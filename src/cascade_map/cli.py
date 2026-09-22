"""Card 10 — the command line, and the only place the cards meet.

Every other card was built against the contracts rather than against its
neighbours. That is what let thirteen of them be built at once, and it means
this module is the first place they are asked to agree. Expect integration to
find disagreements rather than confirm their absence.

Four commands:

* ``analyze``  static map of a target tree. Never executes it.
* ``diff``     compare two analysed versions.
* ``view``     render an analysed tree as one offline HTML page.
* ``trace``    Mode A. Not built yet, and refuses rather than pretending.

**Nothing here executes, imports, execs, evals or unpickles the target.** The
static path reads source as text and parses it with ``ast``. The one command
that would run target code is ``trace``, which belongs to the harness and is
the only command that ever needs the owner's explicit approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from cascade_map import __version__
from cascade_map.cascade import CascadeAnalyzer
from cascade_map.contracts.interfaces import (
    SCHEMA_VERSION,
    Confidence,
    DetectedCandidate,
    canonical_dumps,
    canonical_jsonl,
)
from cascade_map.dependencies import Dependencies
from cascade_map.diff import diff_snapshots, load_snapshot
from cascade_map.docrecords import DocumentationBuilder
from cascade_map.findings import Findings
from cascade_map.ingest import inventory
from cascade_map.lineage import LineageTracer
from cascade_map.resolve import Resolver
from cascade_map.ledger import (
    SETTING_DEFAULTS,
    LedgerError,
    Settings,
    SettingsError,
    render_history_report,
    render_track_report,
    track,
)

# ---------------------------------------------------------------------------
# METATRON_SETTINGS — edit this dict, run `track`, read the ledger.
# ---------------------------------------------------------------------------
#
# These are DATA. Nothing here is ever exec'd, and an unknown key is an error
# naming the typo rather than a silent no-op — a misspelled "SINK" that does
# nothing is how an owner ends up trusting a map built without the setting they
# thought they had applied. Every key can still be overridden by a flag, so
# scripting stays possible.
#
# Deliberately absent: worker/core count. Ingestion parallelises across files
# and the right degree is what the machine knows, not what a config file
# written on a different machine guessed.

METATRON_SETTINGS = {
    # 1 = static only (never executes your engine).
    # 2 = static + runtime tracing inside the harness. 2 always includes 1:
    #     Mode A refuses to start without a completed static map.
    "MODE": 1,

    # The folder holding your versions. One sub-folder per version.
    #   versions/
    #     amun_2026-01-14/        <- a whole engine tree
    #     amun_2026-02-03/
    #     amun_v3/
    "VERSIONS_DIR": "versions",

    # Where maps are written. One sub-directory per version, named by its id.
    "OUT_DIR": "out",

    # The history file. Created on first run, appended to for ever after.
    "LEDGER": "out/metatron_ledger.json",

    # Your final-decision element(s). The single highest-value setting here:
    # everything the tool says about "what drives the decision" is measured
    # against these. Left empty, it guesses by name and labels the guess.
    "SINKS": [],

    # Entry point(s). Detected when empty.
    "ENTRIES": [],

    # Config files that wire components by name, relative to each version root.
    "CONFIGS": [],

    # Card 17: the interpreter whose installed packages to read, as TEXT.
    # Nothing here is ever imported or executed.
    "ENV": ".venv-target",

    # Explicit ordering, for when the tool cannot establish it from the files.
    # Folder names, oldest first. Empty = work it out and report how.
    "ORDER": [],

    # MODE 2 only.
    "SCENARIOS": "scenarios.json",
    "SCENARIO": "baseline",
}


EXIT_OK = 0
EXIT_USAGE = 2
EXIT_GATE_FAILED = 3
EXIT_REFUSED = 4


# ---------------------------------------------------------------------------
# Writing artifacts
# ---------------------------------------------------------------------------


def _write(out_dir: Path, name: str, text: str) -> str:
    """Write one artifact and return its content hash."""
    path = out_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _target_hashes(root: Path) -> dict[str, str]:
    """Content hash per target file, by POSIX path relative to the root.

    Read as bytes. Nothing here decodes or parses, so an unreadable or binary
    file hashes like any other rather than breaking the run.
    """
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        hashes[path.relative_to(root).as_posix()] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    return hashes


def _confidence_census(records: Sequence[Any]) -> dict[str, int]:
    """How many facts sit at each confidence level.

    The single most useful number in the report. A map that is 90% HEURISTIC
    is a different object from one that is 90% CERTAIN, and the owner should
    not have to open the artifacts to find out which they have.
    """
    census = {level.value: 0 for level in Confidence}
    for record in records:
        provenance = getattr(record, "provenance", None)
        if provenance is not None:
            census[str(provenance.confidence)] += 1
    return census


# ---------------------------------------------------------------------------
# Reading a Mode B graph back
# ---------------------------------------------------------------------------


def _rebuild(cls: type, payload: dict[str, Any]) -> Any:
    """Reconstruct one contract dataclass from a parsed artifact row.

    Generic on purpose. A hand-written reader per type would be a second
    description of the schema, free to drift from `canonical_dumps` the moment
    a field is added -- and the drift would be silent, because a missing field
    just reads as a default. This walks the dataclass's own fields instead, so
    the contract stays the single description.
    """
    import dataclasses
    import typing

    hints = typing.get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for field in dataclasses.fields(cls):
        if field.name not in payload:
            continue
        value = payload[field.name]
        hint = hints[field.name]
        origin = typing.get_origin(hint)
        args = typing.get_args(hint)
        if value is None:
            kwargs[field.name] = None
        elif origin is tuple and args and dataclasses.is_dataclass(args[0]):
            kwargs[field.name] = tuple(_rebuild(args[0], v) for v in value)
        elif origin is tuple:
            kwargs[field.name] = tuple(value)
        elif dataclasses.is_dataclass(hint) and isinstance(value, dict):
            kwargs[field.name] = _rebuild(hint, value)
        elif args and any(dataclasses.is_dataclass(a) for a in args) and isinstance(value, dict):
            inner = next(a for a in args if dataclasses.is_dataclass(a))
            kwargs[field.name] = _rebuild(inner, value)
        else:
            kwargs[field.name] = value
    return cls(**kwargs)


def _read_jsonl(out_dir: Path, name: str, cls: type) -> list[Any]:
    path = out_dir / name
    if not path.exists():
        return []
    return [
        _rebuild(cls, json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def build_index(graph_dir: Path, target_root: Path) -> Any:
    """The `StaticIndex` for a target. Used by this process **and the child**.

    Shared deliberately. The first Mode A run mapped 0 of 524 events because
    the child collected against an empty index while this process materialised
    against a full one — the collector keys events to elements as it observes
    them, so an index the child does not have is an index the recording never
    saw. Two call sites building "the same" index independently is how that
    happens.
    """
    from cascade_map.contracts.interfaces import (
        CFGBlock,
        CFGEdge,
        DecisionPoint,
        Edge,
        Element,
        LineageEdge,
        OrderNode,
    )
    from cascade_map.tracer import StaticIndex

    manifest_path = graph_dir / "manifest.json"
    sinks = (
        tuple(json.loads(manifest_path.read_text(encoding="utf-8")).get("sink_ids", []))
        if manifest_path.exists()
        else ()
    )
    return StaticIndex(
        root=str(target_root),
        elements=_read_jsonl(graph_dir, "elements.jsonl", Element),
        edges=_read_jsonl(graph_dir, "edges.jsonl", Edge),
        decisions=_read_jsonl(graph_dir, "decisions.jsonl", DecisionPoint),
        cfg_blocks=_read_jsonl(graph_dir, "cfg_blocks.jsonl", CFGBlock),
        cfg_edges=_read_jsonl(graph_dir, "cfg_edges.jsonl", CFGEdge),
        lineage=_read_jsonl(graph_dir, "lineage.jsonl", LineageEdge),
        order_nodes=_read_jsonl(graph_dir, "order.jsonl", OrderNode),
        sink_element_ids=sinks,
    )


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------


def analyze(
    root: Path,
    out_dir: Path,
    *,
    entry_ids: Sequence[str] = (),
    sink_ids: Sequence[str] = (),
    config_paths: Sequence[str] = (),
    cache_dir: Path | None = None,
    strict_gate: bool = True,
    env_root: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    """Run the static pipeline over *root* and write artifacts to *out_dir*.

    Returns an exit code and a summary. The gate failing is a non-zero exit:
    an incomplete map that reports success is worse than one that refuses,
    because the owner acts on it either way.
    """
    started = time.time()
    summary: dict[str, Any] = {}
    artifacts: dict[str, str] = {}
    stage_millis: dict[str, int] = {}
    _stage_started = started

    def _stage(name: str) -> None:
        """Record how long the stage that just finished took, in whole
        milliseconds -- an int, because `canonical_dumps` rejects floats and
        the project keeps one serialiser.

        This measures **this tool**, not the owner's engine. A static map
        cannot time code it refuses to execute; card 18 stores these per
        version because a version that doubles the analysis time has grown or
        tangled, which is a fact about the target's size and shape and nothing
        at all about its speed.
        """
        nonlocal _stage_started
        now = time.time()
        stage_millis[name] = int(round((now - _stage_started) * 1000))
        _stage_started = now

    # Card 1 — inventory.
    elements, unresolved = inventory(str(root), cache_dir=cache_dir)
    summary["elements"] = len(elements)
    _stage("inventory")

    # Card 2 — resolution.
    resolver = Resolver(root, config_paths=tuple(config_paths))
    edges, resolve_unresolved = resolver.resolve(elements)
    unresolved = list(unresolved) + list(resolve_unresolved)
    summary["edges"] = len(edges)
    _stage("resolve")

    # Card 3 — CFG, ordering, decisions, reachability, detected candidates.
    analyzer = CascadeAnalyzer(root, sink_ids=tuple(sink_ids), unresolved=unresolved)
    (
        blocks,
        cfg_edges,
        order_nodes,
        decisions,
        reachability,
        candidates,
        cascade_unresolved,
    ) = analyzer.order(elements, edges, tuple(entry_ids))
    unresolved = list(unresolved) + list(cascade_unresolved)
    summary["decisions"] = len(decisions)
    _stage("cascade")

    # Card 4 — lineage and slices.
    tracer = LineageTracer(root, sink_ids=tuple(sink_ids))
    lineage_edges, barriers = tracer.trace_values(elements, edges)
    slices = tracer.default_slices()
    summary["lineage_edges"] = len(lineage_edges)
    summary["barriers"] = len(barriers)
    _stage("lineage")

    # Card 17 — dependency and version applicability. Declared, installed and
    # used are gathered separately and joined; with no --env the installed
    # half is absent and every artifact and the summary say so.
    dependencies = Dependencies(
        root,
        environment_root=env_root,
        elements=elements,
        edges=edges,
        reachability=reachability,
    )
    package_requirements = dependencies.requirements()
    installed_packages = dependencies.installed()
    package_usage = dependencies.usage()
    interpreter_requirements = dependencies.interpreter_requirements()
    dependency_findings = dependencies.findings()
    unresolved = list(unresolved) + list(dependencies.unresolved())
    summary["dependencies"] = dependencies.summary()
    _stage("dependencies")

    # Card 5 — findings.
    findings = Findings(
        elements=elements,
        edges=edges,
        unresolved=unresolved,
        cfg_blocks=blocks,
        cfg_edges=cfg_edges,
        decision_points=decisions,
        lineage_edges=lineage_edges,
        barriers=barriers,
        slices=slices,
        reachability=reachability,
        entry_ids=tuple(entry_ids),
    ).find()
    findings = tuple(sorted([*findings, *dependency_findings], key=lambda f: f.id))
    summary["findings"] = len(findings)
    _stage("findings")

    # Card 16 — documentation records, then the gate.
    builder = DocumentationBuilder(
        elements=elements,
        edges=edges,
        order_nodes=order_nodes,
        decisions=decisions,
        lineage_edges=lineage_edges,
        slices=slices,
        findings=findings,
        decision_sink_ids=tuple(sink_ids),
        dependencies=dependencies.doc_dependencies(),
    )
    records = builder.records()
    offenders = builder.completeness_gate(records)
    summary["incomplete_records"] = len(offenders)
    _stage("records")

    for name, payload in (
        ("elements.jsonl", canonical_jsonl(elements)),
        ("edges.jsonl", canonical_jsonl(edges)),
        ("unresolved.jsonl", canonical_jsonl(unresolved)),
        ("cfg_blocks.jsonl", canonical_jsonl(blocks)),
        ("cfg_edges.jsonl", canonical_jsonl(cfg_edges)),
        ("order.jsonl", canonical_jsonl(order_nodes)),
        ("decisions.jsonl", canonical_jsonl(decisions)),
        ("reachability.jsonl", canonical_jsonl(reachability)),
        ("candidates.jsonl", canonical_jsonl(candidates)),
        ("lineage.jsonl", canonical_jsonl(lineage_edges)),
        ("barriers.jsonl", canonical_jsonl(barriers)),
        ("slices.jsonl", canonical_jsonl(slices)),
        ("findings.jsonl", canonical_jsonl(findings)),
        ("records.jsonl", canonical_jsonl(records)),
        ("requirements.jsonl", canonical_jsonl(package_requirements)),
        ("installed.jsonl", canonical_jsonl(installed_packages)),
        ("package_usage.jsonl", canonical_jsonl(package_usage)),
        ("interpreter.jsonl", canonical_jsonl(interpreter_requirements)),
    ):
        artifacts[name] = _write(out_dir, name, payload)

    _stage("write")
    summary["unresolved"] = len(unresolved)
    summary["stage_millis"] = stage_millis
    summary["total_millis"] = int(round((time.time() - started) * 1000))
    summary["confidence"] = _confidence_census(list(edges) + list(lineage_edges))
    summary["detected"] = [
        {"role": c.role, "element_id": c.element_id,
         "confidence": str(c.provenance.confidence)}
        for c in candidates
    ]

    # manifest.json is inside the byte-identical guarantee; run_meta.json is
    # deliberately outside it, and holds everything that legitimately varies.
    _write(
        out_dir,
        "manifest.json",
        canonical_dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "artifacts": artifacts,
                "target_hashes": _target_hashes(root),
                "entry_ids": sorted(entry_ids),
                "sink_ids": sorted(sink_ids),
            }
        )
        + "\n",
    )
    (out_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "tool_version": __version__,
                "python": platform.python_version(),
                "target_root": str(root.resolve()),
                "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "elapsed_seconds": round(time.time() - started, 1),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    if offenders and strict_gate:
        return EXIT_GATE_FAILED, summary
    return EXIT_OK, summary


# ---------------------------------------------------------------------------
# trace — Mode A
# ---------------------------------------------------------------------------


def trace(
    graph_dir: Path,
    scenario_file: Path,
    scenario: str,
    out_root: Path,
) -> tuple[int, str]:
    """Run the target under the harness and write the runtime overlay.

    The harness runs in a **child process**, and that is not an optimisation.

    Card 11's sandbox never auto-clears its enforcement once a run starts:
    clearing it at block exit left a window in which a thread outliving the
    run escaped every control, so it now ends only at process exit. That is
    the right call for containment, and it means the harness owns its process
    for good — the first attempt at this command died trying to `mkdir` its own
    output directory afterwards, blocked by its own sandbox.

    So the child runs the target and writes its recording and run record
    *inside* the sandbox, which it legitimately owns. This parent stays clean
    and does the materialising and writing. Nothing is widened to make room for
    the tool: the alternative was declaring our output directory a permitted
    write root, which would have let the target write there too.
    """
    import subprocess

    from cascade_map.contracts.interfaces import (
        CFGBlock,
        CFGEdge,
        DecisionPoint,
        Edge,
        Element,
        LineageEdge,
        OrderNode,
        RunRecord,
    )
    from cascade_map.harness.hashing import compute_graph_hash, compute_target_hashes
    from cascade_map.narrative import Narrator
    from cascade_map.tracer import StaticIndex, Tracer

    spec_doc = json.loads(scenario_file.read_text(encoding="utf-8"))
    target_root = Path(spec_doc["target_root"]).resolve()
    if scenario not in spec_doc.get("scenarios", {}):
        known = ", ".join(sorted(spec_doc.get("scenarios", {}))) or "none"
        return EXIT_USAGE, f"no scenario named {scenario!r}. Declared: {known}"

    elements = _read_jsonl(graph_dir, "elements.jsonl", Element)
    if not elements:
        return EXIT_USAGE, (
            f"no Mode B graph in {graph_dir}. Mode A always builds on a completed "
            f"static graph — run `cascade-map analyze` first."
        )

    sandbox_root = (out_root / "sandbox").resolve()
    sandbox_root.mkdir(parents=True, exist_ok=True)
    recordings = sandbox_root / "recordings"
    recordings.mkdir(parents=True, exist_ok=True)
    record_out = sandbox_root / "run_record.json"

    child = _CHILD_SOURCE.format(
        spec=json.dumps(spec_doc),
        scenario=json.dumps(scenario),
        graph_dir=json.dumps(str(graph_dir.resolve())),
        sandbox=json.dumps(str(sandbox_root)),
        recordings=json.dumps(str(recordings)),
        record_out=json.dumps(str(record_out)),
        src=json.dumps(str(Path(__file__).resolve().parents[1])),
        self_file=json.dumps(str(Path(__file__).resolve())),
    )
    completed = subprocess.run(
        [sys.executable, "-c", child], capture_output=True, text=True, timeout=1800
    )
    if not record_out.exists():
        detail = (completed.stderr or completed.stdout or "").strip()[-2000:]
        return EXIT_REFUSED, f"the harness produced no run record.\n\n{detail}"

    run = _rebuild(RunRecord, json.loads(record_out.read_text(encoding="utf-8")))
    if run.refused:
        return EXIT_REFUSED, (
            f"REFUSED: {run.refusal_reason}\n\n"
            f"A refusal is a correct outcome, not a warning to work around. "
            f"Nothing was executed."
        )

    index = build_index(graph_dir, target_root)
    tracer = Tracer(index, recordings_dir=recordings)
    result = tracer.result(run)
    order_nodes = _read_jsonl(graph_dir, "order.jsonl", OrderNode)
    decisions = _read_jsonl(graph_dir, "decisions.jsonl", DecisionPoint)
    narrative = Narrator().narrate(result.events, order_nodes, decisions, run)

    run_dir = out_root / "runtime" / run.run_id
    tracer.emit(result, run_dir)
    _write(run_dir, "run.json", canonical_dumps(run) + "\n")
    _write(run_dir, "narrative.jsonl", canonical_jsonl(narrative))

    failure = run.scenario_failure
    total, mapped = result.mapping.total_events, result.mapping.mapped_events
    rate = f"{100 * mapped // total}%" if total else "nothing was observed"
    lines = [
        f"Wrote {run_dir}",
        "",
        f"  run id         {run.run_id}",
        f"  events         {total:,}",
        f"  mapped         {mapped:,} ({rate})",
        f"  unmapped       {total - mapped:,}   <- where the static map was wrong",
        f"  contradictions {len(result.contradictions):,}   <- observation vs static claim",
        f"  nondeterminism {len(result.nondeterminism):,}",
        f"  blocked        {len(run.blocked):,}   <- side effects the harness stopped",
        "",
    ]
    if failure is not None:
        # Before anything else. A scenario that never ran produces a report
        # that reads exactly like a run whose analysis was wrong, and an owner
        # would go hunting the wrong bug.
        if failure.stage == "import":
            headline = (
                "YOUR SCENARIO DID NOT RUN — the module could not be imported."
            )
            hint = (
                "Either `target_root` in your scenario file points at the wrong "
                "directory, or the target cannot import itself. The traceback "
                "below says which."
            )
        elif failure.exception_type == "AttributeError":
            headline = (
                "YOUR SCENARIO DID NOT RUN — the module imported, but the named "
                "function does not exist."
            )
            hint = "Check the `function` field in your scenario file."
        else:
            headline = "YOUR TARGET RAISED. The run completed; the scenario did not."
            hint = "This is your engine's own exception, not a tool failure."
        lines = [
            headline,
            "",
            f"  {failure.exception_type}: {failure.message}",
            "",
            f"  {hint}",
            "",
            "Everything below describes a run in which that happened. Read the "
            "event counts with that in mind.",
            "",
        ] + lines

    if run.unguaranteed:
        lines.append("WHAT THIS RUN COULD NOT GUARANTEE:")
        lines += [f"  - {item}" for item in run.unguaranteed]
        lines.append("")
    return EXIT_OK, "\n".join(lines)


#: How the child gets this tool's own code into scope. A named seam, not an
#: inlined block: `tools/amalgamate.py` replaces this one assignment, because
#: the single-file build has no `cascade_map` package for the child to import —
#: only the one file, which it loads by path. Anything that needs the two
#: shapes to differ belongs here and nowhere else.
_CHILD_PROLOGUE = """
import json, sys
sys.path.insert(0, {src})
from pathlib import Path
from cascade_map.contracts.interfaces import canonical_dumps
from cascade_map.harness import Harness, HarnessRefusal, RunConfig, ScenarioSpec
from cascade_map.harness.hashing import compute_graph_hash, compute_target_hashes
from cascade_map.tracer import StaticIndex, Tracer
from cascade_map.cli import build_index
"""

#: Runs in a child interpreter. Writes its record inside the sandbox, which is
#: the only place it is allowed to write once the harness has taken the process.
_CHILD_SOURCE = _CHILD_PROLOGUE + """
spec_doc = json.loads({spec!r}) if isinstance({spec!r}, str) else {spec}
target_root = Path(spec_doc["target_root"]).resolve()
config = RunConfig(
    target_root=target_root,
    mode_b_out_dir=Path({graph_dir}),
    sandbox_root=Path({sandbox}),
    scenarios={{
        name: ScenarioSpec(name=name, module=body["module"],
                           function=body.get("function", ""),
                           args=tuple(body.get("args", ())))
        for name, body in spec_doc["scenarios"].items()
    }},
    declared_process_names=frozenset(spec_doc.get("declared_process_names", ())),
    env_passthrough=frozenset(spec_doc.get("env_passthrough", ())),
)
index = build_index(Path({graph_dir}), target_root)
tracer = Tracer(index, recordings_dir=Path({recordings}))
graph_hash = compute_graph_hash(compute_target_hashes(target_root))
try:
    run = Harness(config).start({scenario}, graph_hash, tracer)
except HarnessRefusal as exc:
    print("refusal:", exc, file=sys.stderr)
    raise SystemExit(4)
Path({record_out}).write_text(canonical_dumps(run) + "\\n", encoding="utf-8")
"""


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _report(summary: dict[str, Any], out_dir: Path, exit_code: int) -> str:
    """The owner-facing summary. Honest about what it could not work out."""
    lines = [
        f"Wrote {out_dir}",
        "",
        f"  elements      {summary['elements']:>8,}",
        f"  call edges    {summary['edges']:>8,}",
        f"  lineage edges {summary['lineage_edges']:>8,}",
        f"  decisions     {summary['decisions']:>8,}",
        f"  findings      {summary['findings']:>8,}",
        f"  unresolved    {summary['unresolved']:>8,}   <- reported, never dropped",
        f"  barriers      {summary['barriers']:>8,}   <- value flow stops being traceable",
        "",
        "Confidence of the edges the map is built from:",
    ]
    census = summary["confidence"]
    total = sum(census.values()) or 1
    for level in ("CERTAIN", "RESOLVED", "PROBABLE", "HEURISTIC", "UNKNOWN"):
        count = census.get(level, 0)
        lines.append(f"  {level:<10} {count:>8,}  {100 * count // total:>3}%")

    lines += _dependency_lines(summary)

    detected = summary.get("detected") or []
    if detected:
        lines += ["", "Detected, NOT confirmed — these are proposals for you:"]
        for item in detected:
            lines.append(
                f"  {item['role']:<14} {item['element_id']}  ({item['confidence']})"
            )
        lines.append("  Set them in docs/design/TARGET_PROFILE.md to make them facts.")

    if exit_code == EXIT_GATE_FAILED:
        lines += [
            "",
            f"GATE FAILED: {summary['incomplete_records']:,} elements have an "
            "incomplete documentation record.",
            "The artifacts were still written so you can see what is missing.",
        ]
    return "\n".join(lines)


def _dependency_lines(summary: dict[str, Any]) -> list[str]:
    """The dependency section of the report.

    Silence when ``--env`` was not given would be a lie by omission: a run
    that checked nothing must not read like a run that found nothing. The
    absent case names the flag.
    """
    payload = summary.get("dependencies")
    if not payload:
        return []
    counts = payload["findings"]
    lines = [
        "",
        "Dependencies — declared, installed and used are three separate answers:",
        f"  declared      {payload['requirements']:>8,}   "
        f"<- from {len(payload['manifests'])} manifest(s)",
    ]
    if payload["environment_located"]:
        lines.append(
            f"  installed     {payload['installed']:>8,}   "
            f"<- read as text from {payload['environment']}"
        )
    else:
        lines += [
            "  installed            -   <- NOT CHECKED: no environment was read",
            "     Pass --env PATH (.venv-target in production) to read it. Until "
            "then nothing",
            "     here says your installed versions are right; it says they were "
            "not looked at.",
        ]
    lines.append(f"  used          {payload['distributions_used']:>8,}   "
                 "<- distributions the code actually imports")
    if payload["reaching_sink"]:
        lines.append(
            "  on a path to the decision: " + ", ".join(payload["reaching_sink"])
        )
    disagreements = [(kind, n) for kind, n in sorted(counts.items()) if n]
    if disagreements:
        lines.append("  Disagreements:")
        for kind, number in disagreements:
            lines.append(f"    {kind:<24} {number:>6,}")
    elif payload["environment_located"]:
        lines.append("  No disagreement between what is declared, installed and used.")
    if not payload["manifests"]:
        lines.append(
            "  No manifest was found, so nothing is reported as undeclared — there "
            "is nothing to have declared it in."
        )
    return lines


# ---------------------------------------------------------------------------
# Cross-linking the tabular report and the blueprint canvas
# ---------------------------------------------------------------------------


def _crosslink_report_to_blueprint(report_path: Path) -> None:
    """Best-effort, additive cross-link from the phase B report page to a
    sibling ``blueprint.html``, when one already exists next to it.

    Card 15 phase C's blueprint page links back to the report on its own
    (:func:`cascade_map.viewer.render_blueprint_to_file` checks for
    ``index.html`` next to its own output). The reverse direction is done
    **here**, not by editing ``viewer/html_export.py``, which phase C was
    told not to touch: a single, deterministic string insertion right after
    the report page's one ``<nav>`` opening tag. If that anchor is not
    found -- e.g. a future template change -- the file is left untouched
    rather than partially spliced.
    """
    blueprint_candidate = report_path.parent / "blueprint.html"
    if not blueprint_candidate.exists():
        return
    html = report_path.read_text(encoding="utf-8")
    anchor = "<nav>\n"
    if html.count(anchor) != 1:
        return
    link = f'<a href="{blueprint_candidate.name}">Blueprint canvas</a>\n'
    report_path.write_text(html.replace(anchor, anchor + link, 1), encoding="utf-8")


# ---------------------------------------------------------------------------
# track — settings, flags, and the Mode A seam
# ---------------------------------------------------------------------------


def _settings_from_args(args: Any) -> Settings:
    """METATRON_SETTINGS, with any flag the caller passed overriding it.

    The merged dict is validated as a whole, so a typo in the shipped dict is
    an error even on a fully flag-driven run. `Settings.from_mapping` names the
    offending key and the nearest real one.
    """
    merged = dict(METATRON_SETTINGS)
    overrides = {
        "MODE": args.mode,
        "VERSIONS_DIR": str(args.versions) if args.versions else None,
        "OUT_DIR": str(args.out) if args.out else None,
        "LEDGER": str(args.ledger) if args.ledger else None,
        "SINKS": args.sink,
        "ENTRIES": args.entry,
        "CONFIGS": args.config,
        "ENV": str(args.env) if args.env else None,
        "ORDER": args.order,
        "SCENARIOS": str(args.scenarios) if args.scenarios else None,
        "SCENARIO": args.scenario,
    }
    for key, value in overrides.items():
        if value is not None:
            merged[key] = value
    return Settings.from_mapping(merged)


def _track_trace(
    graph_dir: Path, scenarios: Path, scenario: str, out_root: Path
) -> tuple[int, str]:
    """The one seam through which `track` can reach Mode A.

    Named rather than inlined so that a test can prove the wiring without a
    single line of the target ever executing: MODE 2 is the only path in this
    tool that runs owner code, and it runs it through `trace`, which is the
    harness's own entry point and refuses when it cannot guarantee isolation.
    """
    return trace(graph_dir, scenarios, scenario, out_root)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cascade-map",
        description="Map a Python decision engine. The static commands never "
        "execute the target.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("analyze", help="static map of a target tree")
    run.add_argument("root", type=Path)
    run.add_argument("--out", type=Path, default=Path("out/latest"))
    run.add_argument("--entry", action="append", default=[], metavar="ID",
                     help="entry point element id; repeatable. Detected if omitted.")
    run.add_argument("--sink", action="append", default=[], metavar="ID",
                     help="decision sink element id; repeatable. Detected if omitted.")
    run.add_argument("--config", action="append", default=[], metavar="PATH",
                     help="config file that wires components by name; repeatable")
    run.add_argument("--cache", type=Path, default=None)
    run.add_argument("--env", type=Path, default=None, metavar="PATH",
                     help="interpreter tree whose installed package metadata to "
                          "read (.venv-target in production). Read as text; nothing "
                          "under it is imported. Without it the installed half of "
                          "the dependency map is absent and the report says so.")
    run.add_argument("--no-gate", action="store_true",
                     help="write artifacts even if the completeness gate fails, "
                          "and exit 0. The gate still reports.")

    cmp_ = sub.add_parser("diff", help="compare two analysed output directories")
    cmp_.add_argument("before", type=Path)
    cmp_.add_argument("after", type=Path)
    cmp_.add_argument("--out", type=Path, default=Path("out/diff"))

    show = sub.add_parser("view", help="render an analysed tree as offline HTML")
    show.add_argument("out_dir", type=Path)
    show.add_argument("--html", type=Path, default=None)

    blue = sub.add_parser(
        "blueprint",
        help="render an analysed tree as an interactive, UE5-Blueprint-styled node canvas",
    )
    blue.add_argument("graph_dir", type=Path, help="an analysed Mode B output directory")
    blue.add_argument("--html", type=Path, default=None)
    blue.add_argument("--diff", type=Path, default=None,
                      help="a `metatron diff` output directory (changes.jsonl/impacts.jsonl); "
                           "without it, the Diff tab states that it has nothing loaded")
    blue.add_argument("--run", default=None, metavar="RUN_ID",
                      help="a run id under graph_dir/runtime/; layers the runtime overlay on")

    hist = sub.add_parser(
        "track",
        help="analyse every version in VERSIONS_DIR that has not been analysed "
             "already, compare consecutive versions, and update the ledger",
    )
    hist.add_argument("--versions", type=Path, default=None, metavar="DIR",
                      help="overrides VERSIONS_DIR")
    hist.add_argument("--out", type=Path, default=None, help="overrides OUT_DIR")
    hist.add_argument("--ledger", type=Path, default=None, help="overrides LEDGER")
    hist.add_argument("--mode", type=int, choices=(1, 2), default=None,
                      help="overrides MODE. 2 executes your engine inside the "
                           "harness; 1 never executes anything.")
    hist.add_argument("--sink", action="append", default=None, metavar="ID",
                      help="overrides SINKS; repeatable")
    hist.add_argument("--entry", action="append", default=None, metavar="ID",
                      help="overrides ENTRIES; repeatable")
    hist.add_argument("--config", action="append", default=None, metavar="PATH",
                      help="overrides CONFIGS; repeatable")
    hist.add_argument("--env", type=Path, default=None, metavar="PATH",
                      help="overrides ENV")
    hist.add_argument("--order", action="append", default=None, metavar="LABEL",
                      help="overrides ORDER, oldest first; repeatable")
    hist.add_argument("--scenarios", type=Path, default=None,
                      help="overrides SCENARIOS (MODE 2 only)")
    hist.add_argument("--scenario", default=None,
                      help="overrides SCENARIO (MODE 2 only)")
    hist.add_argument("--report", action="store_true",
                      help="print the whole history, not only what this run did")

    run_a = sub.add_parser("trace", help="Mode A — run the target under the harness")
    run_a.add_argument("graph_dir", type=Path, help="an analysed Mode B output directory")
    run_a.add_argument("--scenarios", type=Path, required=True,
                       help="JSON file declaring target_root and named scenarios")
    run_a.add_argument("--scenario", required=True)
    run_a.add_argument("--out", type=Path, default=Path("out/latest"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "analyze":
        if not args.root.is_dir():
            print(f"not a directory: {args.root}", file=sys.stderr)
            return EXIT_USAGE
        code, summary = analyze(
            args.root,
            args.out,
            entry_ids=tuple(args.entry),
            sink_ids=tuple(args.sink),
            config_paths=tuple(args.config),
            cache_dir=args.cache,
            strict_gate=not args.no_gate,
            env_root=args.env,
        )
        print(_report(summary, args.out, code))
        return EXIT_OK if args.no_gate else code

    if args.command == "diff":
        changes, impacts = diff_snapshots(
            load_snapshot(args.before), load_snapshot(args.after)
        )
        args.out.mkdir(parents=True, exist_ok=True)
        _write(args.out, "changes.jsonl", canonical_jsonl(changes))
        _write(args.out, "impacts.jsonl", canonical_jsonl(impacts))
        moved = sum(1 for i in impacts if i.decision_paths_changed)
        print(f"Wrote {args.out}\n  changes {len(changes):,}\n  impacts {len(impacts):,}")
        print(f"  {moved:,} of them change a path to a decision")
        return EXIT_OK

    if args.command == "track":
        try:
            settings = _settings_from_args(args)
            result, ledger = track(
                settings,
                root=Path.cwd(),
                trace=_track_trace if settings.mode == 2 else None,
            )
        except (SettingsError, LedgerError) as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_USAGE
        print(render_track_report(result))
        if args.report:
            print()
            print(render_history_report(ledger))
        incomplete = [c for c in result.comparisons if c.unaccounted_element_ids]
        if incomplete:
            # Every element of both versions must land in exactly one
            # classification. One that does not is named in the ledger, and a
            # run that produced one does not report success.
            return EXIT_GATE_FAILED
        return EXIT_OK

    if args.command == "view":
        # Imported by name, not as a module object: `viewer.render_to_file`
        # needs a `viewer` namespace to exist, and in the single-file build
        # there are no module namespaces -- only globals.
        from cascade_map.viewer import render_to_file

        target = args.html or (args.out_dir / "index.html")
        render_to_file(args.out_dir, target)
        _crosslink_report_to_blueprint(target)
        print(f"Wrote {target}\nOpen it in a browser. It needs no network.")
        return EXIT_OK

    if args.command == "blueprint":
        from cascade_map.viewer import render_blueprint_to_file

        target = args.html or (args.graph_dir / "blueprint.html")
        render_blueprint_to_file(args.graph_dir, target, run_id=args.run, diff_root=args.diff)
        print(f"Wrote {target}\nOpen it in a browser. It needs no network.")
        return EXIT_OK

    code, message = trace(args.graph_dir, args.scenarios, args.scenario, args.out)
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
