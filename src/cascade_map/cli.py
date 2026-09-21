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
from cascade_map.diff import diff_snapshots, load_snapshot
from cascade_map.docrecords import DocumentationBuilder
from cascade_map.findings import Findings
from cascade_map.ingest import inventory
from cascade_map.lineage import LineageTracer
from cascade_map.resolve import Resolver

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
) -> tuple[int, dict[str, Any]]:
    """Run the static pipeline over *root* and write artifacts to *out_dir*.

    Returns an exit code and a summary. The gate failing is a non-zero exit:
    an incomplete map that reports success is worse than one that refuses,
    because the owner acts on it either way.
    """
    started = time.time()
    summary: dict[str, Any] = {}
    artifacts: dict[str, str] = {}

    # Card 1 — inventory.
    elements, unresolved = inventory(str(root), cache_dir=cache_dir)
    summary["elements"] = len(elements)

    # Card 2 — resolution.
    resolver = Resolver(root, config_paths=tuple(config_paths))
    edges, resolve_unresolved = resolver.resolve(elements)
    unresolved = list(unresolved) + list(resolve_unresolved)
    summary["edges"] = len(edges)

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

    # Card 4 — lineage and slices.
    tracer = LineageTracer(root, sink_ids=tuple(sink_ids))
    lineage_edges, barriers = tracer.trace_values(elements, edges)
    slices = tracer.default_slices()
    summary["lineage_edges"] = len(lineage_edges)
    summary["barriers"] = len(barriers)

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
    summary["findings"] = len(findings)

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
    )
    records = builder.records()
    offenders = builder.completeness_gate(records)
    summary["incomplete_records"] = len(offenders)

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
    ):
        artifacts[name] = _write(out_dir, name, payload)

    summary["unresolved"] = len(unresolved)
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


#: Runs in a child interpreter. Writes its record inside the sandbox, which is
#: the only place it is allowed to write once the harness has taken the process.
_CHILD_SOURCE = """
import json, sys
sys.path.insert(0, {src})
from pathlib import Path
from cascade_map.contracts.interfaces import canonical_dumps
from cascade_map.harness import Harness, HarnessRefusal, RunConfig, ScenarioSpec
from cascade_map.harness.hashing import compute_graph_hash, compute_target_hashes
from cascade_map.tracer import StaticIndex, Tracer

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
from cascade_map.cli import build_index
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

    if args.command == "view":
        from cascade_map import viewer

        target = args.html or (args.out_dir / "index.html")
        viewer.render_to_file(args.out_dir, target)
        print(f"Wrote {target}\nOpen it in a browser. It needs no network.")
        return EXIT_OK

    code, message = trace(args.graph_dir, args.scenarios, args.scenario, args.out)
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
