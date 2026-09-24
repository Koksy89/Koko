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
from dataclasses import dataclass
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from cascade_map import __version__
from cascade_map.cascade import CascadeAnalyzer
from cascade_map.contracts.interfaces import (
    SCHEMA_VERSION,
    Confidence,
    DetectedCandidate,
    ElementKind,
    FindingKind,
    SliceScope,
    canonical_dumps,
    canonical_jsonl,
)
from cascade_map.dependencies import MAX_SOURCE_BYTES, Dependencies
from cascade_map.diff import diff_snapshots, load_snapshot
from cascade_map.docrecords import DocumentationBuilder
from cascade_map.findings import Findings
from cascade_map.ingest import inventory
from cascade_map.ingest.inventory import Ingestor
from cascade_map.ingest.parallel import render_worker_report
from cascade_map.parallel import (
    BackgroundUnit,
    Payload,
    PipelineReport,
    StageReport,
    canonical_jsonl_many,
    render_pipeline_report,
    resolve_workers,
)
from cascade_map.lineage import LineageTracer
from cascade_map.parsecache import DEFAULT_BUDGET_BYTES, ParseCache
from cascade_map.progress import (
    ANALYZE_STAGES,
    NullProgress,
    TRACE_STAGES,
    make_envelope,
    make_reporter,
)
from cascade_map.resolve import Resolver
from cascade_map.harness.hashing import (
    compute_graph_hash,
    compute_target_hashes,
    is_target_content,
)
from cascade_map.harness.scenarios import (
    ScenarioDerivationError,
    derive_scenario_document,
    harness_warnings,
    select_sports,
)
from cascade_map.harness.stubs import (
    StubDeclarationError,
    describe_declarations,
    parse_declarations,
)
from cascade_map.alignment import (
    AlignmentEngine,
    coverage_json,
    intents_jsonl,
    load_registry,
    propose_intents,
    registry_text,
    verdicts_jsonl,
)
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
# WORKERS is here but defaults to 0 = auto, because the right degree is what
# the machine knows, not what a config file written on a different machine
# guessed. Every run prints what the workers actually bought.

METATRON_SETTINGS = {
    # 1 = static only (never executes your engine).
    # 2 = static + runtime tracing inside the harness. 2 always includes 1:
    #     Mode A refuses to start without a completed static map.
    "MODE": 1,

    # THE ONE PATH. Everything else lives under it and is derived, so naming
    # your script is the whole instruction:
    #
    #   Metatron_Engine/                     <- WORKSPACE
    #       AmunEV_Engine_V2/                <- PROJECT, from ENGINE below
    #           history/
    #               AmunEV_Engine_V2_history.json        <- open this one
    #               AmunEV_Engine_V2_fingerprints.jsonl
    #               AmunEV_Engine_V2_comparisons.jsonl
    #               runtime/basketball_history.json      <- one per sport
    #           sources/<content hash>/       <- stored ONCE per distinct content
    #           io/runs/<UTC timestamp>/      <- the map from that run
    #           io/reports/
    "WORKSPACE": "workspace",

    # The folder under WORKSPACE holding this script's whole history. Empty
    # means "derive it from ENGINE", which is the point: name the script and
    # there is nothing left to point at and nothing to mis-point.
    "PROJECT": "",

    # Keep one copy of each DISTINCT version's tree under sources/. A re-run
    # of unchanged code stores nothing, so this costs one snapshot per real
    # change and nothing at all otherwise -- and the history file prints the
    # number, so it never grows in silence. false is `--no-sources`: the
    # content hash is still recorded, the copy is not kept.
    "SOURCES": True,

    # ---- the three keys WORKSPACE replaced ----------------------------
    #
    # Left EMPTY, WORKSPACE resolves all three and you can ignore them. Set to
    # a path, each one still does exactly what it always did, and `track`
    # prints one line saying what it now means. Nothing you already have on
    # disk stops working because you upgraded mid-project.
    #
    #   VERSIONS_DIR  the drop folder versions are read from
    #   OUT_DIR       the artifact root, one directory per version id
    #   LEDGER        a second, flat copy of the history at that path
    "VERSIONS_DIR": "",
    "OUT_DIR": "",
    "LEDGER": "",

    # Your final-decision element(s). The single highest-value setting here:
    # everything the tool says about "what drives the decision" is measured
    # against these. Left empty, it guesses by name and labels the guess.
    "SINKS": [],

    # Entry point(s). Detected when empty.
    "ENTRIES": [],

    # Config files that wire components by name, relative to each version root.
    "CONFIGS": [],

    # Which roots get a PRECOMPUTED slice in slices.jsonl. This decides HOW
    # MANY slices are stored, never how complete any one of them is: every
    # slice emitted is exact and whole, and none is ever truncated or sampled.
    #
    #   "DECISION"  default. The roots that bear on a decision: your SINKS
    #               and what they read. Bounded by your declaration of what
    #               the decision IS -- so with no SINKS declared it emits NO
    #               slices, because there is then no principled set of roots
    #               and lineage.jsonl answers any of them on demand.
    #   "ALL"       every root. Exhaustive, correct, and quadratic in OUTPUT --
    #               measured 211 MB of slices for 1.4 MB of source, 1.6 GB for
    #               5.6 MB, and gigabytes for a 14.8 MB engine. The run says so
    #               before it writes them.
    #   "NONE"      none at all. lineage.jsonl still holds every edge, so any
    #               slice remains answerable on demand.
    "SLICES": "DECISION",

    # Roots to precompute WHATEVER SLICES says, by element or feature id.
    # Chasing one feature should never mean switching to the exhaustive mode.
    "SLICE_ROOTS": [],

    # Write slices.jsonl even when its estimated size exceeds the guard.
    # The guard refuses above 1 GB and prints the number; this is how you
    # say you meant it.
    "FORCE_SLICES": False,

    # Child processes for ingestion. 0 = auto (usable cores less one, so an
    # analysis does not take the whole box); 1 = in-process, which is how
    # anything here is debugged and always stays available.
    #
    # Parallelism is ACROSS FILES: one file is one unit, and a unit is never
    # split because half a function is not parseable. If your target is ONE
    # large file, workers cannot help it -- the run will say so, with the
    # measured gain, rather than printing a worker count as if it were a
    # benefit.
    "WORKERS": 0,

    # Card 17: the interpreter whose installed packages to read, as TEXT.
    # Nothing here is ever imported or executed.
    "ENV": ".venv-target",

    # Explicit ordering, for when the tool cannot establish it from the files.
    # Folder names, oldest first. Empty = work it out and report how.
    "ORDER": [],

    # The owner-confirmed intents file: what each element is MEANT to do, in
    # checkable terms. The one setting that answers "which of the things I
    # built are not plugged in?" against YOUR OWN DECLARATION rather than
    # against a guess from structure or naming.
    #
    # Empty means no spec, and every element is reported NO_INTENT -- a state,
    # not a pass and not a failure. A file that IS named and cannot be read is
    # never silently ignored: every parse problem lands in unresolved.jsonl
    # with its line number and its reason, and the run says so.
    #
    # Start one with:  metatron analyze <target> --propose-intents intents.yaml
    # Everything it writes is PROPOSED. You confirm; the tool proposes.
    "INTENTS": "",

    # MODE 2 only.
    "SCENARIOS": "scenarios.json",
    "SCENARIO": "baseline",

    # MODE 2 / `trace`: sports as first-class scenarios.
    #
    # The engine file is a LIBRARY -- running it does nothing. The launcher is
    # what runs, and it is argv-driven, so a scenario is
    #   RUNNER --engine ENGINE --sports <sport> [RUN_ARGS...]
    # one per sport, each its own scenario with its own run id. Different
    # sports are never compared against each other.
    #
    # You do not have to edit any of this to change which sport runs:
    #   metatron_engine.py trace out/amun --sport basketball
    #   metatron_engine.py track --mode 2 --all-sports
    "SPORTS": [
        "etennis",
        "esport",
        "basketball",
        "tabletennis",
        "football",
        "efootball",
        "ebasketball",
    ],

    # "" = all of SPORTS. Overridden by --sport / --all-sports.
    "SPORT": "",

    # The engine file, relative to the version root. Passed as --engine.
    "ENGINE": "AmunEV_Engine_V2.py",

    # The launcher, relative to the version root. Resolved to an importable
    # module rooted at the target; an unresolvable path is a refusal naming
    # the path tried, never a guess at another module.
    "RUNNER": "bin/go_live.py",

    # Extra flags passed through to the runner, after the sport. Appended to
    # by --run-arg. Note: --workers N asks for N child processes, which the
    # harness blocks unless declared, and does not supervise when declared.
    "RUN_ARGS": [],
}


EXIT_OK = 0
EXIT_USAGE = 2
EXIT_GATE_FAILED = 3
EXIT_REFUSED = 4

#: Above this, `slices.jsonl` is refused rather than written. One gigabyte is
#: the point at which an artifact stops being something an owner can open, and
#: the number is a starting threshold, not a law: --force-slices writes it
#: anyway and `slice_size_limit` moves the line.
#:
#: Only `slices.jsonl` is guarded. It is the one artifact quadratic in OUTPUT
#: -- a slice per root, each holding O(graph) ids -- so it is the only one
#: whose size is out of proportion to the target. `lineage.jsonl` and
#: `order.jsonl` are linear in the target (one record per lineage edge, one per
#: ordered node) and are what every unwritten slice is recomputed from:
#: refusing to write them would save less and cost everything.
SLICE_SIZE_LIMIT_BYTES = 1 << 30

#: An artifact at or above this is NAMED with its size when it is written --
#: not refused. Measured on the owner's engine, `lineage.jsonl` was 205 MB and
#: `order.jsonl` 105 MB for a 14.8 MB target: roughly 14x and 7x the source and
#: linear in it, which is proportionate for one record per lineage edge and one
#: per ordered node. `slices.jsonl` at the same scope was 6.4 GB, 432x, because
#: it alone is quadratic. Proportion is the difference between a sentence and a
#: refusal.
LARGE_ARTIFACT_BYTES = 64 << 20


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
    from enum import StrEnum

    def _enum_of(hint: Any) -> type[StrEnum] | None:
        """The `StrEnum` this field is typed as, through an optional if need be."""
        if isinstance(hint, type) and issubclass(hint, StrEnum):
            return hint
        for arg in typing.get_args(hint):
            if isinstance(arg, type) and issubclass(arg, StrEnum):
                return arg
        return None

    hints = typing.get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for field in dataclasses.fields(cls):
        if field.name not in payload:
            continue
        value = payload[field.name]
        hint = hints[field.name]
        origin = typing.get_origin(hint)
        args = typing.get_args(hint)
        enum_type = _enum_of(hint)
        if value is None:
            kwargs[field.name] = None
        elif enum_type is not None:
            # A `StrEnum` left as a bare string compares equal but is never
            # `is` the member, and this project's own code asks `edge.kind is
            # EdgeKind.CALLS`. Read back as a string, every such test is
            # silently False: card 13 saw a re-read `Reachability.state` of
            # "REACHES_SINK" fail `is ReachabilityState.REACHES_SINK` and
            # report a live element as unreachable. A value the enum does not
            # name is left alone rather than raising -- an artifact from a
            # newer schema must degrade, not crash.
            try:
                kwargs[field.name] = enum_type(value)
            except ValueError:
                kwargs[field.name] = value
        elif origin is tuple and args and dataclasses.is_dataclass(args[0]):
            kwargs[field.name] = tuple(_rebuild(args[0], v) for v in value)
        elif origin is tuple and args and isinstance(args[0], type) and issubclass(args[0], StrEnum):
            kwargs[field.name] = tuple(
                _coerce_enum_member(args[0], item) for item in value
            )
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


def _coerce_enum_member(enum_type: type, value: Any) -> Any:
    try:
        return enum_type(value)
    except ValueError:
        return value


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


# ---------------------------------------------------------------------------
# card 17 as an overlapped whole stage
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _DependencyResults:
    """Everything card 17 answers, collected in one value.

    A bundle rather than the `Dependencies` object itself: the object is built
    inside the worker and only its ANSWERS travel, so nothing here depends on
    a stage's internal state surviving a process boundary.
    """

    requirements: tuple[Any, ...]
    installed: tuple[Any, ...]
    usage: tuple[Any, ...]
    interpreter_requirements: tuple[Any, ...]
    findings: tuple[Any, ...]
    unresolved: tuple[Any, ...]
    summary: dict[str, Any]
    doc_dependencies: dict[str, dict[str, Any]]


class _DependenciesPayload(Payload):
    """Card 17's whole input, sent ONCE to the one worker that runs it.

    Under the `fork` start method the child inherits it and nothing is pickled
    at all; under `spawn` it crosses once, which for one unit of work is also
    once in total.
    """

    __slots__ = (
        "root", "environment_root", "elements", "edges", "reachability",
        "max_source_bytes",
    )

    def __init__(
        self,
        *,
        root: str,
        environment_root: str | None,
        elements: tuple[Any, ...],
        edges: tuple[Any, ...],
        reachability: tuple[Any, ...],
        max_source_bytes: int,
    ) -> None:
        self.root = root
        self.environment_root = environment_root
        self.elements = elements
        self.edges = edges
        self.reachability = reachability
        self.max_source_bytes = max_source_bytes

    def open(self) -> "_DependenciesPayload":
        return self


def _dependencies_unit(payload: "_DependenciesPayload", _arg: Any) -> _DependencyResults:
    """The WHOLE of card 17, run exactly as it runs in one process.

    Nothing inside the stage is split. It reads the target as text and never
    imports, executes or unpickles any of it -- constraint 1 holds in a worker
    exactly as it holds here.
    """
    dependencies = Dependencies(
        Path(payload.root),
        environment_root=(
            None if payload.environment_root is None else Path(payload.environment_root)
        ),
        elements=payload.elements,
        edges=payload.edges,
        reachability=payload.reachability,
        max_source_bytes=payload.max_source_bytes,
    )
    return _DependencyResults(
        requirements=tuple(dependencies.requirements()),
        installed=tuple(dependencies.installed()),
        usage=tuple(dependencies.usage()),
        interpreter_requirements=tuple(dependencies.interpreter_requirements()),
        findings=tuple(dependencies.findings()),
        unresolved=tuple(dependencies.unresolved()),
        summary=dependencies.summary(),
        doc_dependencies=dependencies.doc_dependencies(),
    )


def analyze(
    root: Path,
    out_dir: Path,
    *,
    entry_ids: Sequence[str] = (),
    sink_ids: Sequence[str] = (),
    config_paths: Sequence[str] = (),
    slice_scope: SliceScope = SliceScope.DECISION,
    slice_roots: Sequence[str] = (),
    force_slices: bool = False,
    slice_size_limit: int = SLICE_SIZE_LIMIT_BYTES,
    cache_dir: Path | None = None,
    strict_gate: bool = True,
    env_root: Path | None = None,
    workers: int | None = None,
    worker_report_sink: Callable[[str], None] | None = None,
    progress: Any = None,
    max_source_bytes: int = MAX_SOURCE_BYTES,
    parse_cache_bytes: int = DEFAULT_BUDGET_BYTES,
    intents_path: Path | None = None,
    propose_intents_path: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    """Run the static pipeline over *root* and write artifacts to *out_dir*.

    Returns an exit code and a summary. The gate failing is a non-zero exit:
    an incomplete map that reports success is worse than one that refuses,
    because the owner acts on it either way.
    """
    started = time.time()
    summary: dict[str, Any] = {}
    artifacts: dict[str, str] = {}
    artifact_bytes: dict[str, int] = {}
    stage_millis: dict[str, int] = {}
    _stage_started = started
    # A null object when the caller passed none, so every `progress.` call
    # below is unconditional and a forgotten guard cannot crash the quiet path.
    bar = progress if progress is not None else NullProgress()
    bar.start(started)

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
        # The same call that records the timing announces it. One hook, so a
        # stage can never be timed and not shown, or shown and not timed.
        bar.complete(name)

    # Card 1 — inventory.
    #
    # `Ingestor` rather than the `inventory()` wrapper so the worker report
    # can be read back. That report is PRINTED and never written into an
    # artifact: it is wall-clock, and constraint 4 says identical input gives
    # identical bytes.
    # Every stage that can take workers reports what they actually bought, per
    # stage, measured on this run. `worker_count` is resolved once so every
    # stage asks for the same thing and the report can name one number.
    worker_count = resolve_workers(workers)
    pipeline = PipelineReport(requested=worker_count)

    # ONE parse per file per run, shared by every stage that runs in THIS
    # process. Resolution asks for each module twice, the CFG stage once and
    # lineage once; before this they were four `ast.parse` calls on the same
    # bytes, and on the owner's 14.8 MB single-module engine one of those is
    # 20.8s and 895,811 nodes.
    #
    # NOT given to inventory and NOT given to dependencies, and neither is an
    # oversight. Inventory is the first stage, so it can only ever seed the
    # cache, never hit it -- and its file units run in a POOL as soon as a
    # target has more than one file, where a fork would copy every held tree
    # into every worker. Dependencies travels to its own process inside a
    # pickled payload; a ParseCache holding live trees must never enter one
    # (the same trap `cascade._PreOpened` documents). Both parse for
    # themselves, exactly as they did.
    parse_cache = ParseCache(parse_cache_bytes)

    ingestor = Ingestor(cache_dir=cache_dir, workers=workers)
    elements, unresolved = ingestor.inventory(str(root), on_unit=bar.sub)
    summary["elements"] = len(elements)
    _stage("inventory")
    (worker_report_sink or bar.through)(render_worker_report(ingestor.worker_report))

    # Card 2 — resolution.
    #
    # NOT handed to the pool, and the report says so with the number rather
    # than leaving it looking like a stage with nothing to gain. Resolution's
    # unit is a whole MODULE -- a `_CallResolver` carries a scope chain down
    # one module's tree and binds names as it goes, so there is no boundary
    # inside a module to cut on, and cutting anyway would resolve names
    # against a scope that does not exist. Across modules the walks share
    # caches that one module's pass fills for the next. On a target that is
    # one 15 MB module, as the owner's is, there is exactly one unit either
    # way.
    resolve_started = time.time()
    resolver = Resolver(root, config_paths=tuple(config_paths), parse_cache=parse_cache)
    edges, resolve_unresolved = resolver.resolve(elements)
    unresolved = list(unresolved) + list(resolve_unresolved)
    summary["edges"] = len(edges)
    pipeline.add(
        StageReport(
            stage="resolve",
            requested=worker_count,
            unit_count=resolver.module_count(),
            total_seconds=time.time() - resolve_started,
            not_parallelised_reason=(
                "one unit is one module, and a module's name resolution "
                "carries a scope chain and caches that the next module's "
                "pass reads; the units are not independent"
            ),
        )
    )
    _stage("resolve")

    # Card 3 — CFG, ordering, decisions, reachability, detected candidates.
    analyzer = CascadeAnalyzer(
        root,
        sink_ids=tuple(sink_ids),
        unresolved=unresolved,
        workers=worker_count,
        on_unit=bar.sub,
        parse_cache=parse_cache,
    )
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
    analyzer.cfg_report.total_seconds = analyzer.cfg_report.wall_seconds
    pipeline.add(analyzer.cfg_report)
    pipeline.add(analyzer.order_report)
    _stage("cascade")

    # Card 4 — lineage and slices.
    #
    # `slice_scope` decides HOW MANY slices are precomputed and written. It
    # never decides how complete one is: every slice emitted here is exact and
    # whole, because a half-slice answering "what produces this feature" is a
    # wrong answer wearing the shape of a right one. `lineage.jsonl` is always
    # written in full, so any root not precomputed is still answerable.
    # Card 17 STARTS HERE, in a second process, and is collected after card 4.
    #
    # This is the one kind of parallelism that helps a target which is a
    # single module. Dependencies reads elements, edges and reachability --
    # all of which exist by now -- and reads nothing card 4 produces; card 4
    # reads nothing of card 17's. Two whole stages, neither split, running at
    # the same time, consolidated in the same fixed order as before, so the
    # bytes cannot move. On the owner's engine card 17 is 37.4s that card 4's
    # 238.6s completely hides.
    dependencies_report = pipeline.add(
        StageReport(stage="dependencies", overlapped_with="lineage")
    )
    dependencies_job = BackgroundUnit(
        _dependencies_unit,
        _DependenciesPayload(
            root=str(root),
            environment_root=None if env_root is None else str(env_root),
            elements=tuple(elements),
            edges=tuple(edges),
            reachability=tuple(reachability),
            max_source_bytes=max_source_bytes,
        ),
        None,
        worker_count,
        dependencies_report,
    )

    lineage_started = time.time()
    tracer = LineageTracer(root, sink_ids=tuple(sink_ids), parse_cache=parse_cache)
    lineage_edges, barriers = tracer.trace_values(elements, edges)

    say = worker_report_sink or bar.through
    available_roots = tracer.all_slice_roots()
    if slice_scope is SliceScope.ALL and available_roots:
        say(_all_scope_warning(len(available_roots), len(lineage_edges)))

    # The basis card 5 reasons from is the SAME SET at every scope, with and
    # without a declared sink: sinks, what they read, and every feature, plus
    # any explicit `--slice-root`. The scope is a storage decision and a
    # finding is a fact; a storage decision that silently changed a finding
    # would be a defect, so it cannot reach one.
    #
    # Since DECISION no longer writes the feature roots, most of these slices
    # are now computed for card 5 and never written. That is the intended
    # trade: the cost was the bytes, not the computation, and `lineage.jsonl`
    # still lets anyone recompute any of them exactly.
    slices = tracer.default_slices(slice_scope, slice_roots)
    findings_slices = tracer.default_slices(
        SliceScope.NONE,
        (*tracer.findings_basis_roots(), *slice_roots),
        emitted_as=SliceScope.DECISION,
    )
    summary["lineage_edges"] = len(lineage_edges)
    summary["barriers"] = len(barriers)
    # NOT handed to the pool either, for a harder reason than resolution's: a
    # reaching-definition walk is a fold over the module, and its walkers
    # write shared state -- aliases, container keys, declared features,
    # established definitions -- that a LATER module's walk reads. Splitting
    # the modules across processes would give each worker a different half of
    # that state and a different set of lineage edges. A wrong graph is
    # infinitely worse than a slow one, so this stays in one process and the
    # cost is printed rather than disguised.
    pipeline.add(
        StageReport(
            stage="lineage",
            requested=worker_count,
            unit_count=tracer.module_count(),
            total_seconds=time.time() - lineage_started,
            not_parallelised_reason=(
                "one unit is one module, and a module's dataflow walk writes "
                "alias, container-key and feature state that later modules' "
                "walks read; the units are not independent"
            ),
        )
    )
    _stage("lineage")

    # Card 17 — collected. Declared, installed and used are gathered
    # separately and joined; with no --env the installed half is absent and
    # every artifact and the summary say so.
    #
    # Consolidated HERE, at the point the serial pipeline always consolidated
    # it, and in the same order: `unresolved` is extended after card 4 and
    # before card 5, exactly as before, so the artifact's contents and their
    # order are untouched by the stage having run early.
    deps = dependencies_job.result()
    package_requirements = deps.requirements
    installed_packages = deps.installed
    package_usage = deps.usage
    interpreter_requirements = deps.interpreter_requirements
    dependency_findings = deps.findings
    unresolved = list(unresolved) + list(deps.unresolved)
    summary["dependencies"] = deps.summary
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
        slices=findings_slices,
        reachability=reachability,
        entry_ids=tuple(entry_ids),
    ).find()
    findings = tuple(sorted([*findings, *dependency_findings], key=lambda f: f.id))
    summary["findings"] = len(findings)

    # The root of a finding is NO LONGER an automatic DECISION root: on the
    # 14.6 MB single-module target card 5 reports 19,227 findings, and a slice
    # per finding root was 38,454 slices -- more than `ALL` emits, and exactly
    # the quadratic DECISION exists to avoid. It was one half of the leak that
    # produced a measured 6.4 GB `slices.jsonl` from a 15 MB input. A finding
    # the owner wants a slice of is one `--slice-root <id>` away, and
    # `lineage.jsonl` answers it exactly either way.
    #
    # ALL is unchanged and still tops up, because ALL is the scope that asks
    # for everything and says what it will cost before paying it. Only the two
    # kinds whose EVIDENCE IS A LINEAGE PATH are topped up: an
    # UNREACHABLE_ELEMENT or a VERSION_CONFLICT is not answered by a data-flow
    # slice; an UNCONSUMED_FEATURE and a DECISION_IRRELEVANT are.
    if slice_scope is SliceScope.ALL:
        precomputed = {sliced.root_id for sliced in slices}
        top_up = tuple(
            sorted(
                {
                    finding.element_id
                    for finding in findings
                    if finding.kind in _SLICE_EVIDENCED_FINDINGS
                    and finding.element_id not in precomputed
                    and tracer.has_lineage_node(finding.element_id)
                }
            )
        )
        if top_up:
            merged = {sliced.id: sliced for sliced in slices}
            for sliced in tracer.default_slices(
                SliceScope.NONE, top_up, emitted_as=slice_scope
            ):
                merged[sliced.id] = sliced
            slices = tuple(sorted(merged.values(), key=lambda one: one.id))
    _stage("findings")

    # Card 13 — the intent registry, and the verdicts the STATIC map supports.
    #
    # After card 5 because it needs cards 2, 3 and 4 whole; before anything is
    # written because a registry issue is an `Unresolved` like any other and
    # belongs in the same `unresolved.jsonl` as everything else this run could
    # not resolve.
    #
    # An intents file the owner wrote that silently did nothing is the worst
    # outcome available here, so every problem in it carries its line number
    # and its reason, none of them is dropped, and the printed report names
    # the count.
    registry = load_registry(intents_path, (element.id for element in elements))
    unresolved = list(unresolved) + list(registry.issues)

    # The universe judged is the REGISTRY'S OWN: the elements the owner wrote
    # an intent for, plus the ones whose entry was too malformed to load.
    # Judging every element would emit one NO_INTENT verdict per element --
    # true, and nothing an owner can act on; on a 116k-line engine it is tens
    # of thousands of lines saying "you did not write an intent for this".
    # The narrowing is stated in the manifest and the report, not only here.
    named = {intent.element_id for intent in registry.intents}
    named.update(registry.rejected_element_ids)
    aligner = AlignmentEngine(
        registry=registry,
        elements=tuple(element for element in elements if element.id in named),
        edges=edges,
        lineage_edges=lineage_edges,
        reachability=reachability,
    )
    static_verdicts = aligner.judge_static(registry.intents)
    intent_coverage = aligner.coverage()
    summary["intents"] = _intent_summary(
        registry, intent_coverage, static_verdicts, intents_path
    )

    # --propose-intents: a STARTER registry, every entry PROPOSED. Written
    # even when it would overwrite, because the owner named the path; never
    # written into the artifact directory, because it is an input they edit,
    # not an output they read.
    if propose_intents_path is not None:
        # Only the kinds an intent can sensibly be ABOUT. A parameter, an
        # import or an embedded blob gets a proposal of the shape "Named
        # 'amount', suggesting it amount", and on a 116k-line engine that is
        # tens of thousands of lines of noise between the owner and the
        # entries worth confirming. The number left out is printed and stored,
        # so the narrowing is visible rather than assumed.
        candidates_for_proposal = [
            element for element in elements if element.kind in _PROPOSABLE_KINDS
        ]
        skipped = len(elements) - len(candidates_for_proposal)
        proposals = propose_intents(candidates_for_proposal)
        propose_intents_path.parent.mkdir(parents=True, exist_ok=True)
        propose_intents_path.write_text(
            registry_text(proposals), encoding="utf-8", newline="\n"
        )
        summary["intents"]["proposed_written"] = {
            "path": str(propose_intents_path),
            "entries": len(proposals),
            "status": "PROPOSED",
            "elements_skipped": skipped,
            "kinds_proposed": sorted(str(kind) for kind in _PROPOSABLE_KINDS),
        }
    _stage("intents")

    # The size guard, BEFORE anything is written and before card 16 links a
    # record to a slice, so a refusal can never leave a record pointing at a
    # slice the artifact does not hold.
    #
    # The owner of a 14.8 MB engine discovered a 6.4 GB slices.jsonl after the
    # fact. An estimate costs a sum over ids already in memory; discovering the
    # size afterwards costs a disk. A refusal the owner can override with
    # --force-slices is honest, and silently writing 6.4 GB is not.
    slice_bytes = tracer.estimated_slice_bytes(slices)
    slice_members = sum(len(one.member_ids) for one in slices)
    slice_refusal: str | None = None
    if slices and slice_bytes > slice_size_limit and not force_slices:
        slice_refusal = _slice_size_refusal(
            estimated=slice_bytes,
            limit=slice_size_limit,
            roots=len({sliced.root_id for sliced in slices}),
            members=slice_members,
            has_sink=bool(sink_ids),
        )
        say(slice_refusal)
        # Dropped WHOLE, never shortened. The principle the scope rests on is
        # "emit fewer slices, never smaller ones", and a truncated slices.jsonl
        # would be the wrong answer wearing the shape of a right one.
        slices = ()

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
        dependencies=deps.doc_dependencies,
    )
    records_report = pipeline.add(StageReport(stage="records"))
    records = builder.records(
        on_progress=bar.sub, workers=worker_count, report=records_report
    )
    records_report.total_seconds = records_report.wall_seconds
    offenders = builder.completeness_gate(records)
    summary["incomplete_records"] = len(offenders)
    _stage("records")

    precomputed_roots = {sliced.root_id for sliced in slices}
    root_universe = {*available_roots, *precomputed_roots}
    slice_disclosure = {
        "scope": str(slice_scope),
        "slices_written": len(slices),
        "roots_precomputed": len(precomputed_roots),
        "roots_total": len(root_universe),
        "roots_not_precomputed": len(root_universe) - len(precomputed_roots),
        "note": (
            "Scope decides how many slices are PRECOMPUTED, never how complete "
            "one is: every slice here is exact and whole. Any root not "
            "precomputed is still answerable from lineage.jsonl, which is "
            "always written in full."
        ),
    }
    # Why the count is what it is, in the artifact and not only on a terminal
    # that has scrolled. A count of 0 with no explanation reads as "this target
    # has no lineage", which is the opposite of true.
    reason = _slice_count_reason(
        scope=slice_scope,
        has_sink=bool(sink_ids),
        written=len(slices),
        refusal=slice_refusal,
    )
    if reason:
        slice_disclosure["reason"] = reason
    slice_disclosure["estimated_bytes"] = slice_bytes
    slice_disclosure["member_ids_total"] = slice_members
    slice_disclosure["size_limit_bytes"] = slice_size_limit
    slice_disclosure["refused"] = slice_refusal is not None
    summary["slices"] = slice_disclosure
    if reason and slice_refusal is None:
        say("  " + reason.replace("\n", "\n  "))
    if slice_scope is SliceScope.ALL and slices:
        say(
            f"  slices.jsonl will be about "
            f"{_human_bytes(tracer.estimated_slice_bytes(slices))} "
            f"({len(slices):,} slices over {len(precomputed_roots):,} roots). "
            f"`--slices decision` writes only the roots that bear on a "
            f"decision; the rest stay recomputable from lineage.jsonl."
        )

    # Serialisation is the largest stage on a large target -- 437 MB of JSON
    # on the owner's engine -- and it is per RECORD, so it parallelises. The
    # unit is a whole record; the global sort that fixes the order stays in
    # this process, so the bytes cannot depend on which worker rendered what.
    # `canonical_jsonl_many` is byte-identical to `canonical_jsonl` per group
    # and there is a test that fails if it ever stops being.
    write_report = pipeline.add(StageReport(stage="write"))
    payloads = canonical_jsonl_many(
        {
            "elements.jsonl": elements,
            "edges.jsonl": edges,
            "unresolved.jsonl": unresolved,
            "cfg_blocks.jsonl": blocks,
            "cfg_edges.jsonl": cfg_edges,
            "order.jsonl": order_nodes,
            "decisions.jsonl": decisions,
            "reachability.jsonl": reachability,
            "candidates.jsonl": candidates,
            "lineage.jsonl": lineage_edges,
            "barriers.jsonl": barriers,
            "slices.jsonl": slices,
            "findings.jsonl": findings,
            "records.jsonl": records,
            "requirements.jsonl": package_requirements,
            "installed.jsonl": installed_packages,
            "package_usage.jsonl": package_usage,
            "interpreter.jsonl": interpreter_requirements,
            # Card 13. Always written, even empty: an absent intents.jsonl and
            # an intents.jsonl holding nothing are different facts, and the
            # viewer reads this file to show an element's declared purpose.
            "intents.jsonl": registry.intents,
            "verdicts.jsonl": static_verdicts,
        },
        worker_count,
        write_report,
        on_unit=bar.sub,
    )
    for name in sorted(payloads):
        payload = payloads.pop(name)
        artifacts[name] = _write(out_dir, name, payload)
        artifact_bytes[name] = len(payload.encode("utf-8"))
        del payload

    # Only `slices.jsonl` is REFUSED on size, because it is the only artifact
    # quadratic in OUTPUT. The rest are linear in the target -- one record per
    # element, per edge, per ordered node -- and they are what every unwritten
    # slice is recomputed from, so refusing to write them would save less and
    # cost everything. What they get instead is a sentence, so a large one is
    # never a surprise discovered on disk afterwards.
    large = sorted(
        (name for name, size in artifact_bytes.items() if size >= LARGE_ARTIFACT_BYTES),
        key=lambda name: (-artifact_bytes[name], name),
    )
    if large:
        say(
            "  large artifacts: "
            + ", ".join(f"{name} {_human_bytes(artifact_bytes[name])}" for name in large)
            + ". Linear in the target and written in full: these are what any "
            "slice is recomputed from, so none of them is ever withheld."
        )
    summary["artifact_bytes"] = dict(sorted(artifact_bytes.items()))

    write_report.total_seconds = write_report.wall_seconds
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
                # An owner must never mistake a scoped artifact for an
                # exhaustive one, so the scope travels with the artifacts
                # rather than only appearing on a terminal that has scrolled.
                "slice_scope": slice_disclosure,
                # Which elements `verdicts.jsonl` covers, and which it does
                # not. A verdict file that covered only the registry's own
                # elements while reading as if it covered the target would be
                # the same mistake the slice scope exists to prevent.
                "intents": {
                    "path": str(intents_path) if intents_path is not None else "",
                    "present": bool(registry.present),
                    "intents": len(registry.intents),
                    "issues": len(registry.issues),
                    "elements_judged": len(static_verdicts),
                    "note": (
                        "verdicts.jsonl covers the elements the intents file "
                        "NAMES, not every element: an element with no intent is "
                        "NO_INTENT by construction and is not enumerated. "
                        "Runtime verdicts, which need a run, live in "
                        "runtime/<run_id>/verdicts.jsonl."
                    ),
                },
            }
        )
        + "\n",
    )
    # ONE reading of the clock, used by the printed footer and by
    # run_meta.json alike. Two readings would let the owner's terminal and
    # their artifact disagree about how long their own run took.
    finished = time.time()
    elapsed_seconds = round(finished - started, 1)
    summary["started_at"] = started
    summary["finished_at"] = finished
    summary["elapsed_seconds"] = elapsed_seconds
    (out_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "tool_version": __version__,
                "python": platform.python_version(),
                "target_root": str(root.resolve()),
                "finished_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(finished)
                ),
                "elapsed_seconds": elapsed_seconds,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    # What the workers bought, per stage, measured on this run. Printed and
    # never written into an artifact: these are wall-clock timings and
    # constraint 4 says identical input gives identical bytes.
    (worker_report_sink or bar.through)(render_pipeline_report(pipeline))
    # What the shared trees bought. A run-shaped measurement like the ones
    # above, so it is printed and never written into an artifact.
    (worker_report_sink or bar.through)(parse_cache.stats.render())

    bar.finish(
        finished_at=finished, elapsed=elapsed_seconds, stage_millis=stage_millis
    )
    # A refusal outranks the gate: the owner asked for slices, did not get
    # them, and must not read an exit code that says the run did what it was
    # asked. Everything else was written, so the workspace is usable and the
    # analysis does not have to be paid for twice.
    if slice_refusal is not None:
        return EXIT_REFUSED, summary
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
    progress: Any = None,
    intents_path: Path | None = None,
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

    # The caller owns start and finish, because a refusal returns from a dozen
    # places in this function and every one of them must still print a footer.
    bar = progress if progress is not None else NullProgress()

    from cascade_map.contracts.interfaces import (
        CFGBlock,
        CFGEdge,
        DecisionPoint,
        Edge,
        Element,
        LineageEdge,
        OrderNode,
        Reachability,
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

    # Client stubs, validated HERE -- in the parent, before a child exists and
    # long before a line of the target runs. A declaration the harness cannot
    # read must be a refusal naming the module and the problem, never a child
    # that dies halfway through a run it should not have started.
    #
    # The hard rule is untouched: this validates modules the owner NAMED.
    # Anything not named here is still undeclared, and an undeclared client is
    # still a hard stop at the socket layer.
    try:
        stub_declarations = parse_declarations(spec_doc.get("client_stubs"))
    except StubDeclarationError as exc:
        return EXIT_REFUSED, (
            f"REFUSED: {exc}\n\n"
            f"`client_stubs` in {scenario_file} could not be read, so this run "
            f"cannot say which external systems are stubbed. Nothing was executed."
        )
    # A `replay` recording is named RELATIVE TO THE SCENARIOS FILE, which is
    # where the owner keeps it -- not relative to the target, which would
    # require putting tool inputs inside the code under analysis. Resolved
    # here so there is exactly one rule and the child never has to guess.
    for declaration in stub_declarations:
        if not declaration.recording:
            continue
        resolved = Path(declaration.recording)
        if not resolved.is_absolute():
            resolved = (scenario_file.parent / resolved).resolve()
        if not resolved.is_file():
            return EXIT_REFUSED, (
                f"REFUSED: client stub {declaration.module!r} is kind \"replay\" and "
                f"its recording {declaration.recording!r} does not exist. Tried: "
                f"{resolved}. A replay with nothing to replay would answer every "
                f"call with a stop, which is not what you declared. Nothing was "
                f"executed."
            )
        spec_doc["client_stubs"][declaration.module]["recording"] = str(resolved)

    # The hash the harness checks the target against is the one the GRAPH was
    # built from, read out of manifest.json. Letting the child recompute it
    # from the target it is about to run makes the staleness check compare a
    # number against itself, which can never fail -- a control that always
    # passes is not a control.
    expected_graph_hash = _graph_hash_of(graph_dir)
    if not expected_graph_hash:
        return EXIT_REFUSED, (
            f"REFUSED: {graph_dir} carries no manifest.json with target hashes, "
            f"so this run cannot prove the Mode B graph matches the target it "
            f"is about to execute. Re-run `analyze`. Nothing was executed."
        )

    elements = _read_jsonl(graph_dir, "elements.jsonl", Element)
    if not elements:
        return EXIT_USAGE, (
            f"no Mode B graph in {graph_dir}. Mode A always builds on a completed "
            f"static graph — run `{_PROG} analyze` first."
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
        graph_hash=json.dumps(expected_graph_hash),
        src=json.dumps(str(Path(__file__).resolve().parents[1])),
        self_file=json.dumps(str(Path(__file__).resolve())),
    )
    bar.complete("preflight")
    completed = subprocess.run(
        [sys.executable, "-c", child], capture_output=True, text=True, timeout=1800
    )
    bar.complete("harness")
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
    bar.complete("map")
    order_nodes = _read_jsonl(graph_dir, "order.jsonl", OrderNode)
    decisions = _read_jsonl(graph_dir, "decisions.jsonl", DecisionPoint)
    narrative = Narrator().narrate(result.events, order_nodes, decisions, run)
    bar.complete("narrate")

    # Card 13 — the runtime half of alignment. The same registry `analyze`
    # loaded, now judged against what was actually observed, keyed by the same
    # stable IDs. One graph, two evidence sources: this is an overlay, not a
    # second answer.
    registry = load_registry(intents_path, (element.id for element in elements))
    edges = _read_jsonl(graph_dir, "edges.jsonl", Edge)
    lineage_edges = _read_jsonl(graph_dir, "lineage.jsonl", LineageEdge)
    reachability = _read_jsonl(graph_dir, "reachability.jsonl", Reachability)
    named = {intent.element_id for intent in registry.intents}
    named.update(registry.rejected_element_ids)
    aligner = AlignmentEngine(
        registry=registry,
        elements=[element for element in elements if element.id in named],
        edges=edges,
        lineage_edges=lineage_edges,
        reachability=reachability,
        run=run,
    )
    verdicts = aligner.judge(registry.intents, result.events)
    intent_coverage = aligner.coverage()

    run_dir = out_root / "runtime" / run.run_id
    tracer.emit(result, run_dir)
    _write(run_dir, "run.json", canonical_dumps(run) + "\n")
    _write(run_dir, "narrative.jsonl", canonical_jsonl(narrative))
    _write(run_dir, "verdicts.jsonl", verdicts_jsonl(verdicts))
    _write(run_dir, "coverage.json", coverage_json(intent_coverage))
    if registry.present:
        # The viewer reads `intents.jsonl` from the ARTIFACT ROOT, so a
        # `trace` whose --out is not the graph directory still has the intent
        # text next to the verdicts that judged it. Written only when a spec
        # was actually loaded: an empty file here would overwrite the one
        # `analyze` wrote into the same directory.
        _write(out_root, "intents.jsonl", intents_jsonl(registry.intents))
    bar.complete("write")

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
    lines += _verdict_lines(registry, verdicts, intent_coverage, intents_path, run_dir)
    if stub_declarations:
        lines.append(
            f"DECLARED EXTERNAL CLIENTS — you took responsibility for "
            f"{len(stub_declarations)} named module(s). Nothing else is exempt:"
        )
        lines += [f"  - {item}" for item in describe_declarations(stub_declarations)]
        lines.append("")
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

    stopped = [
        item for item in run.blocked
        if item.kind != "filesystem_read_outside_sandbox"
    ]
    if stopped:
        # Loudly, with what was attempted. A blocked connect or a refused
        # spawn reported only as a count next to four other counts is how a
        # run that never reached its data reads as a clean run.
        lines.append(
            f"THE HARNESS STOPPED {len(stopped):,} REAL SIDE EFFECT(S). These are "
            f"findings, not noise — your engine tried to do each of these:"
        )
        shown = stopped[:_BLOCKED_SHOWN]
        lines += [f"  - [{item.kind}] {item.detail}" for item in shown]
        if len(stopped) > len(shown):
            lines.append(
                f"  ... and {len(stopped) - len(shown):,} more; all of them are in "
                f"{run_dir / 'run.json'} under `blocked`."
            )
        lines.append("")
    lines.append("WHAT THE HARNESS DID TO THIS RUN — read before you trust the output:")
    lines += [
        f"  - {item}"
        for item in harness_warnings(
            str(sandbox_root),
            spec_doc.get("declared_process_names", ()),
            spec_doc["scenarios"][scenario].get("argv", ()),
        )
    ]
    lines.append("")
    if run.unguaranteed:
        lines.append("WHAT THIS RUN COULD NOT GUARANTEE:")
        lines += [f"  - {item}" for item in run.unguaranteed]
        lines.append("")
    return EXIT_OK, "\n".join(lines)


def _verdict_lines(
    registry: Any,
    verdicts: Sequence[Any],
    coverage: Any,
    intents_path: Path | None,
    run_dir: Path,
) -> list[str]:
    """The alignment section of a Mode A run summary.

    `NOT_EXERCISED` gets its own line because it is the one an owner will
    otherwise read as a pass. It is not: an element no scenario ran is
    unverified, and the whole point of this card is to say so out loud.
    """
    if intents_path is None and not registry.present:
        return [
            "ALIGNMENT: no intents file, so nothing was judged. Every element "
            "is NO_INTENT.",
            "  --intents PATH (or INTENTS in METATRON_SETTINGS) is what turns "
            "this run into an answer to",
            "  \"does this element do what I meant it to do\".",
            "",
        ]
    counts: dict[str, int] = {}
    for verdict in verdicts:
        counts[str(verdict.verdict)] = counts.get(str(verdict.verdict), 0) + 1
    lines = [
        f"ALIGNMENT against {intents_path or registry.source_path}:",
        "  "
        + (
            ", ".join(f"{count:,} {name}" for name, count in sorted(counts.items()))
            or "nothing judged"
        ),
    ]
    not_exercised = [item for item in verdicts if str(item.verdict) == "NOT_EXERCISED"]
    if not_exercised:
        lines.append(
            f"  {len(not_exercised):,} element(s) WITH A CONFIRMED INTENT WERE NEVER "
            f"RUN by this scenario. NOT_EXERCISED is not ALIGNED:"
        )
        for item in sorted(not_exercised, key=lambda one: one.element_id)[
            :_NOT_EXERCISED_SHOWN
        ]:
            lines.append(f"    - {item.element_id}")
        if len(not_exercised) > _NOT_EXERCISED_SHOWN:
            lines.append(
                f"    ... and {len(not_exercised) - _NOT_EXERCISED_SHOWN:,} more, all "
                f"of them in {run_dir / 'verdicts.jsonl'}."
            )
    misaligned = [item for item in verdicts if str(item.verdict) == "MISALIGNED"]
    for item in sorted(misaligned, key=lambda one: one.element_id)[:_MISALIGNED_SHOWN]:
        lines.append(f"  MISALIGNED  {item.element_id}")
        lines.append(f"    expected    {item.expectation}")
        lines.append(f"    observed    {item.observation}")
    if len(misaligned) > _MISALIGNED_SHOWN:
        lines.append(
            f"  ... and {len(misaligned) - _MISALIGNED_SHOWN:,} more MISALIGNED, all "
            f"of them in {run_dir / 'verdicts.jsonl'}."
        )
    lines.append(
        f"  coverage: {coverage.checks_passed:,} expectation(s) held, "
        f"{coverage.checks_failed:,} contradicted, "
        f"{coverage.checks_unverifiable:,} could not be checked "
        f"(of {coverage.checks_total:,})."
    )
    if registry.issues:
        lines.append(
            f"  {len(registry.issues):,} PROBLEM(S) IN YOUR INTENTS FILE — each is an "
            f"intent doing nothing:"
        )
        for issue in registry.issues[:_INTENT_ISSUES_SHOWN]:
            line_no = issue.span.line if issue.span else 0
            lines.append(f"    {registry.source_path}:{line_no}  {issue.description}")
    lines.append("")
    return lines


#: Caps on the two verdict kinds the run summary prints in full. Explicit,
#: never a silent cut: the rest are counted and pointed at verdicts.jsonl.
_NOT_EXERCISED_SHOWN = 20
_MISALIGNED_SHOWN = 10


#: How many blocked attempts the run summary prints in full. The rest are
#: counted and pointed at run.json -- an explicit cap, never a silent cut.
_BLOCKED_SHOWN = 20


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
from cascade_map.harness.stubs import (build_factories, declaration_fingerprint,
                                       parse_declarations, StubDeclarationError)
from cascade_map.tracer import StaticIndex, Tracer
from cascade_map.cli import build_index
"""

#: Runs in a child interpreter. Writes its record inside the sandbox, which is
#: the only place it is allowed to write once the harness has taken the process.
_CHILD_SOURCE = _CHILD_PROLOGUE + """
spec_doc = json.loads({spec!r}) if isinstance({spec!r}, str) else {spec}
target_root = Path(spec_doc["target_root"]).resolve()
# Already validated in the parent; parsed again here because the child gets
# the document, not the parse. A `replay` recording is READ here, so a
# missing one refuses before the harness takes the process.
_stub_declarations = parse_declarations(spec_doc.get("client_stubs"))
try:
    _stub_factories = build_factories(
        _stub_declarations, sandbox_root=Path({sandbox}), target_root=target_root
    )
except StubDeclarationError as exc:
    print("refusal:", exc, file=sys.stderr)
    raise SystemExit(4)
config = RunConfig(
    target_root=target_root,
    mode_b_out_dir=Path({graph_dir}),
    sandbox_root=Path({sandbox}),
    scenarios={{
        name: ScenarioSpec(name=name, module=body["module"],
                           function=body.get("function", ""),
                           args=tuple(body.get("args", ())),
                           argv=tuple(body.get("argv", ())))
        for name, body in spec_doc["scenarios"].items()
    }},
    declared_process_names=frozenset(spec_doc.get("declared_process_names", ())),
    client_stubs=_stub_factories,
    client_declarations=declaration_fingerprint(_stub_declarations),
    env_passthrough=frozenset(spec_doc.get("env_passthrough", ())),
)
index = build_index(Path({graph_dir}), target_root)
tracer = Tracer(index, recordings_dir=Path({recordings}))
graph_hash = {graph_hash}
try:
    run = Harness(config).start({scenario}, graph_hash, tracer)
except (HarnessRefusal, StubDeclarationError) as exc:
    print("refusal:", exc, file=sys.stderr)
    raise SystemExit(4)
Path({record_out}).write_text(canonical_dumps(run) + "\\n", encoding="utf-8")
"""


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def target_root_of(graph_dir: Path) -> Path:
    """The tree the Mode B graph was built from, read back from its own output.

    `analyze` records it in `run_meta.json`. Reading it back is what lets
    `trace out/amun --sport basketball` work with no scenarios file and no
    second copy of the path: the graph already knows what it is a map of.
    Raises `ScenarioDerivationError` naming the file tried rather than
    guessing a directory to execute code from.
    """
    meta = graph_dir / "run_meta.json"
    if not meta.is_file():
        raise ScenarioDerivationError(
            f"cannot tell which tree {graph_dir} is a map of: no run_meta.json. "
            f"Tried: {meta}. Re-run `analyze`, or declare target_root in a "
            f"scenarios file."
        )
    try:
        payload = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScenarioDerivationError(f"cannot read {meta}: {exc}") from exc
    root = str(payload.get("target_root", ""))
    if not root:
        raise ScenarioDerivationError(
            f"{meta} records no target_root, so there is nothing to run. "
            f"Re-run `analyze`."
        )
    return Path(root)


def _version_tuple(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in text.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _required_python(graph_dir: Path) -> tuple[str, str]:
    """The highest `minimum_python` card 17 read off the target's own syntax.

    A measured fact -- `match` is 3.10, `except*` is 3.11 -- not a guess at
    what the engine needs, and not a number this tool invented. Returns
    ``("", "")`` when the graph carries no interpreter requirements, which is
    reported as "not measured", never as "any version will do".
    """
    path = graph_dir / "interpreter.jsonl"
    if not path.is_file():
        return "", ""
    best, best_element = "", ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        version = str(row.get("minimum_python", ""))
        if version and _version_tuple(version) > _version_tuple(best):
            best, best_element = version, str(row.get("element_id", ""))
    return best, best_element


def preflight(
    graph_dir: Path,
    out_root: Path,
    settings: Settings,
    sports: Sequence[str],
    scenario_file: Path | None,
) -> tuple[int, str]:
    """Can this run start? Answered WITHOUT executing anything.

    Every line is a check that either passed or did not, and the verdict at
    the end is the same decision `Harness.start` would make on the inputs
    this can see from outside the harness. It deliberately does not claim to
    be the harness's own verdict: the audit-hook self-test happens inside the
    run, and a preflight that promised it would be promising something it
    never tried.
    """
    checks: list[tuple[str, str, bool]] = []

    anchor = graph_dir / "elements.jsonl"
    graph_ok = anchor.is_file()
    element_count = (
        sum(1 for line in anchor.read_text(encoding="utf-8").splitlines() if line.strip())
        if graph_ok
        else 0
    )
    checks.append((
        "Mode B graph",
        f"{anchor} ({element_count:,} elements)" if graph_ok else f"missing: {anchor}",
        graph_ok,
    ))

    target_root: Path | None = None
    document: dict[str, Any] | None = None
    derivation_error = ""
    if scenario_file is not None:
        if scenario_file.is_file():
            try:
                document = json.loads(scenario_file.read_text(encoding="utf-8"))
                target_root = Path(str(document["target_root"]))
            except (OSError, json.JSONDecodeError, KeyError) as exc:
                derivation_error = f"cannot read {scenario_file}: {exc}"
        else:
            derivation_error = f"no scenarios file at {scenario_file}"
        checks.append((
            "scenarios file", str(scenario_file), not derivation_error,
        ))
    else:
        try:
            target_root = target_root_of(graph_dir)
            document = derive_scenario_document(
                target_root,
                sports=sports,
                runner=settings.runner,
                engine=settings.engine,
                run_args=settings.run_args,
            )
        except ScenarioDerivationError as exc:
            derivation_error = str(exc)
        checks.append((
            "runner",
            f"{settings.runner} -> {document['scenarios'][sports[0]]['module']}"
            if document and sports
            else derivation_error or "not resolved",
            document is not None,
        ))
        checks.append((
            "engine file",
            str(target_root / settings.engine) if target_root else settings.engine,
            bool(target_root and (target_root / settings.engine).is_file()),
        ))

    if target_root is not None:
        checks.append(("target root", str(target_root), target_root.is_dir()))
        hashes = compute_target_hashes(target_root)
        current = compute_graph_hash(hashes)
        recorded = _graph_hash_of(graph_dir)
        checks.append((
            "graph is current",
            f"target hashes {current[:12]}, graph built from "
            f"{recorded[:12] if recorded else 'unrecorded'}"
            + ("" if recorded == current else "  <- STALE: re-run analyze"),
            bool(recorded) and recorded == current,
        ))

    needed, needed_by = _required_python(graph_dir)
    running = platform.python_version()
    if needed:
        ok = _version_tuple(running) >= _version_tuple(needed)
        detail = (
            f"this interpreter is {running}; the target's own syntax needs "
            f"{needed} or newer (first seen in {needed_by})"
        )
    else:
        ok = True
        detail = (
            f"this interpreter is {running}; the graph carries no interpreter "
            f"requirements, so the minimum was NOT MEASURED"
        )
    checks.append(("python", detail, ok))

    checks.append((
        "sports",
        ", ".join(sports) if sports else "none selected",
        bool(sports) or scenario_file is not None,
    ))

    lines = ["PREFLIGHT — nothing was executed.", ""]
    width = max(len(name) for name, _, _ in checks)
    for name, detail, ok in checks:
        lines.append(f"  {'OK  ' if ok else 'FAIL'}  {name.ljust(width)}  {detail}")
    lines.append("")

    sandbox_root = (out_root / "sandbox").resolve()
    declared = sorted(document.get("declared_process_names", ())) if document else []
    passthrough = sorted(document.get("env_passthrough", ())) if document else []
    stub_lines: list[str] = []
    stub_problem = ""
    if document:
        try:
            stub_lines = list(describe_declarations(parse_declarations(document.get("client_stubs"))))
        except StubDeclarationError as exc:
            stub_problem = str(exc)
            checks.append(("client stubs", stub_problem, False))
    lines += [
        "CONTROLS THAT WILL BE ACTIVE:",
        "  network           blocked at the socket layer, including DNS. No allowlist exists.",
        f"  filesystem        every write redirected under {sandbox_root}",
        "  process           blocked unless declared: "
        + (", ".join(declared) if declared else "(none declared)"),
        "  environment       passed through: "
        + (", ".join(passthrough) if passthrough else "(nothing, including secrets)"),
        "  external clients  "
        + (
            "declared, and NOTHING ELSE is exempt:"
            if stub_lines
            else "(none declared; an undeclared client is a hard stop)"
        ),
    ]
    lines += [f"                      {item}" for item in stub_lines]
    if stub_problem:
        lines.append(f"                      NOT READABLE: {stub_problem}")
    lines.append("")
    if document:
        lines.append("SCENARIOS THAT WOULD RUN:")
        for name in sorted(document.get("scenarios", {})):
            body = document["scenarios"][name]
            lines.append(f"  {name}")
            lines.append(f"    module  {body.get('module', '')}")
            argv = body.get("argv", ())
            lines.append(
                f"    argv    {' '.join(argv) if argv else '(sys.argv left alone)'}"
            )
        lines.append("")

    lines.append("WHAT THE HARNESS WILL DO TO THIS RUN:")
    lines += [
        f"  - {item}"
        for item in harness_warnings(str(sandbox_root), declared, settings.run_args)
    ]
    lines.append("")

    failed = [name for name, _, ok in checks if not ok]
    if failed or derivation_error:
        if derivation_error:
            lines.append(f"  {derivation_error}")
            lines.append("")
        lines.append(
            "VERDICT: this run WOULD REFUSE TO START — "
            + ", ".join(failed or ["scenario could not be derived"])
            + ". Nothing was executed."
        )
        return EXIT_REFUSED, "\n".join(lines)
    lines.append(
        "VERDICT: every check this can make from outside the harness passes. "
        "The harness re-verifies its own controls when the run starts, and "
        "refuses there if any of them cannot be proved active."
    )
    return EXIT_OK, "\n".join(lines)


def _graph_hash_of(graph_dir: Path) -> str:
    """The graph hash recorded by `analyze`, or "" when there is none.

    Derived from `manifest.json:target_hashes`, which is the same input
    `compute_graph_hash` takes inside the harness, so "current" here means
    exactly what "current" means there.
    """
    manifest = graph_dir / "manifest.json"
    if not manifest.is_file():
        return ""
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    hashes = payload.get("target_hashes")
    if not isinstance(hashes, dict):
        return ""
    # `analyze` skips only __pycache__; the harness also skips VCS and tool
    # cache directories. Filtering the manifest through the harness's own set
    # makes the two key sets identical, so this hash is comparable with the
    # one the harness computes -- and a mismatch means the target changed,
    # never that the two sides disagree on what a target file is.
    return compute_graph_hash(
        {
            str(key): str(value)
            for key, value in hashes.items()
            if is_target_content(str(key))
        }
    )


#: Findings whose evidence is a data-flow path, and therefore the findings a
#: precomputed slice actually answers. `SliceScope.DECISION` tops up the roots
#: of these; the roots of the others stay recomputable from `lineage.jsonl`
#: like every other root the scope did not precompute.
_SLICE_EVIDENCED_FINDINGS = frozenset(
    {FindingKind.UNCONSUMED_FEATURE, FindingKind.DECISION_IRRELEVANT}
)


def _human_count(count: int) -> str:
    """A count an owner can hold in their head. 41,231,904 reads as 41M."""
    if count >= 1_000_000:
        return f"{count // 1_000_000}M"
    return f"{count:,}"


def _slice_size_refusal(
    *, estimated: int, limit: int, roots: int, members: int, has_sink: bool
) -> str:
    """Said INSTEAD of writing a slices.jsonl larger than the limit.

    Every number is measured from the slices already computed for this run, not
    from a rule of thumb about another one. The alternatives are named in the
    same breath, including the one that writes it anyway: a refusal the owner
    cannot override is an obstruction rather than a warning.
    """
    alternatives = (
        "  Pass --slices none, or --force-slices to write it anyway."
        if has_sink
        else "  Declare a sink with --sink <id>, or pass --slices none, or "
        "--force-slices to write it anyway."
    )
    return (
        f"  slices would be ~{_human_bytes(estimated)} "
        f"({roots:,} roots, {_human_count(members)} member ids), over the "
        f"{_human_bytes(limit)} limit. Refusing to write it.\n"
        f"{alternatives}\n"
        f"  Nothing was truncated and nothing is lost: lineage.jsonl is written "
        f"in full and answers any slice exactly, on demand."
    )


#: The DECISION scope with no declared sink. Not an error -- a statement that
#: the question has no principled answer yet, and how to give it one.
NO_SINK_SLICE_REASON = (
    "scope DECISION; no decision sink declared, so there is no principled set "
    "of roots. Declare one with --sink <id> to get slices, or use --slices all "
    "to precompute every root and pay for it. A feature or any other root you "
    "care about is one --slice-root <id> away."
)


def _slice_count_reason(
    *, scope: SliceScope, has_sink: bool, written: int, refusal: str | None
) -> str:
    """Why `slices.jsonl` holds what it holds, carried into the artifact.

    An owner who finds a count of zero with no explanation concludes the target
    has no lineage, which is the opposite of true. The two counts that need
    explaining are the ones this tool chose: the unrooted DECISION scope, and a
    refusal on size.
    """
    if refusal is not None:
        return "\n".join(part.strip() for part in refusal.splitlines())
    if scope is SliceScope.DECISION and not has_sink and written == 0:
        return NO_SINK_SLICE_REASON
    return ""


def _human_bytes(count: int) -> str:
    """A size an owner can act on. Never rounded up into a smaller unit."""
    for unit, size in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if count >= size:
            return f"{count / size:.1f} {unit}"
    return f"{count} bytes"


def _all_scope_warning(roots: int, lineage_edges: int) -> str:
    """Said BEFORE the exhaustive scope is paid for, never after.

    ALL is correct and it is available on purpose. What it must never be is a
    surprise: the numbers below are measured, and the scope that avoids them is
    named in the same breath.
    """
    return (
        f"  --slices all: precomputing a backward and a forward slice for each "
        f"of {roots:,} roots over {lineage_edges:,} lineage edges.\n"
        f"  slices.jsonl is quadratic in OUTPUT -- measured 211 MB for 1.4 MB "
        f"of source, 1.6 GB for 5.6 MB, gigabytes for a 14.8 MB engine.\n"
        f"  Correct, and expensive. `--slices decision` precomputes only the "
        f"roots that bear on a decision; every other slice stays exactly "
        f"recomputable from lineage.jsonl."
    )


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
    ]
    lines += _slice_lines(summary)
    lines += ["", "Confidence of the edges the map is built from:"]
    census = summary["confidence"]
    total = sum(census.values()) or 1
    for level in ("CERTAIN", "RESOLVED", "PROBABLE", "HEURISTIC", "UNKNOWN"):
        count = census.get(level, 0)
        lines.append(f"  {level:<10} {count:>8,}  {100 * count // total:>3}%")

    lines += _dependency_lines(summary)
    lines += _intent_lines(summary)

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


#: The element kinds `--propose-intents` writes an entry for. An intent is a
#: statement about something that DOES something; a PARAMETER, an IMPORT, an
#: ASSIGNMENT or a BLOB gets a proposal built from its own name, which is
#: noise the owner then has to delete by hand.
_PROPOSABLE_KINDS = frozenset(
    {
        ElementKind.MODULE,
        ElementKind.PACKAGE,
        ElementKind.CLASS,
        ElementKind.FUNCTION,
        ElementKind.METHOD,
        ElementKind.PROPERTY,
    }
)


#: `UnresolvedReason` values a registry issue can carry, in the order the
#: report lists them, with what each one means to the owner reading it.
_INTENT_ISSUE_LABELS: dict[str, str] = {
    "SYNTAX_ERROR": "could not be parsed",
    "MISSING_TARGET": "names an element that does not exist",
    "AMBIGUOUS": "two intents claim the same element",
    "ID_COLLISION": "two entries share one intent id",
    "DECODE_ERROR": "the file is not valid UTF-8",
    "UNSUPPORTED_SYNTAX": "uses something this parser refuses to guess at",
}


def _intent_summary(
    registry: Any,
    coverage: Any,
    verdicts: Sequence[Any],
    intents_path: Path | None,
) -> dict[str, Any]:
    """What `analyze` says about the owner's intents, as data.

    Every count here is a fact about the owner's own file. `issues` is a list,
    not a count, because a parse problem the report summarises as "1 issue"
    is a parse problem the owner cannot fix.
    """
    counts: dict[str, int] = {}
    for verdict in verdicts:
        counts[str(verdict.verdict)] = counts.get(str(verdict.verdict), 0) + 1
    return {
        "path": str(intents_path) if intents_path is not None else "",
        "present": bool(registry.present),
        "total": len(registry.intents),
        "confirmed": coverage.intents_confirmed,
        "proposed": coverage.intents_proposed,
        "elements_judged": coverage.elements_judged,
        "checks_total": coverage.checks_total,
        "checks_passed": coverage.checks_passed,
        "checks_failed": coverage.checks_failed,
        "checks_unverifiable": coverage.checks_unverifiable,
        "checks_unparseable": coverage.checks_unparseable,
        "verdicts": dict(sorted(counts.items())),
        "coverage": coverage.to_dict(),
        "issues": [
            {
                "line": issue.span.line if issue.span else 0,
                "reason": str(issue.reason),
                "description": issue.description,
            }
            for issue in registry.issues
        ],
    }


def _intent_lines(summary: dict[str, Any]) -> list[str]:
    """The intents section of the report.

    Loud on purpose when something is wrong. An intents file the owner wrote
    and that quietly did nothing is the worst outcome this feature has, so a
    file that was named but could not be read, an intent that names no
    element, and an element the owner declared live that nothing reaches all
    get their own line with the line number that fixes them.
    """
    payload = summary.get("intents")
    if not payload:
        return []
    if not payload["path"]:
        # Nothing was asked for. One line, so the feature is discoverable
        # rather than invisible -- and no more than one, because most runs
        # will not use it.
        lines = [
            "",
            "Intents: no spec declared (--intents PATH / INTENTS), so every "
            "element is NO_INTENT.",
            "  NO_INTENT is a reported state, not a pass and not a failure. "
            "Start one with --propose-intents PATH.",
        ]
        return lines + _proposed_lines(payload)
    lines = ["", f"Intents: {payload['path']}"]
    if not payload["present"]:
        lines.append(
            "  THE FILE YOU NAMED WAS NOT READ. Nothing in it was applied to "
            "this map."
        )
    lines.append(
        f"  {payload['total']:,} intent(s): {payload['confirmed']:,} CONFIRMED, "
        f"{payload['proposed']:,} PROPOSED (a PROPOSED intent can never ground "
        f"ALIGNED or MISALIGNED)."
    )
    verdicts = payload["verdicts"]
    if verdicts:
        lines.append(
            "  static verdicts: "
            + ", ".join(f"{count:,} {name}" for name, count in sorted(verdicts.items()))
        )
    lines.append(
        f"  expectations: {payload['checks_passed']:,} held, "
        f"{payload['checks_failed']:,} contradicted, "
        f"{payload['checks_unverifiable']:,} unchecked here "
        f"(of {payload['checks_total']:,}). An unchecked expectation is never "
        f"reported as met."
    )
    if payload["checks_unparseable"]:
        lines.append(
            f"  {payload['checks_unparseable']:,} invariant(s) are not in the "
            f"checkable language and were NOT checked."
        )
    issues = payload["issues"]
    if issues:
        lines.append(
            f"  {len(issues):,} PROBLEM(S) IN YOUR INTENTS FILE — each one is an "
            f"intent that is doing nothing:"
        )
        for issue in issues[:_INTENT_ISSUES_SHOWN]:
            label = _INTENT_ISSUE_LABELS.get(issue["reason"], issue["reason"])
            lines.append(f"    {payload['path']}:{issue['line']}  {label}")
            lines.append(f"      {issue['description']}")
        if len(issues) > _INTENT_ISSUES_SHOWN:
            lines.append(
                f"    ... and {len(issues) - _INTENT_ISSUES_SHOWN:,} more, all of "
                f"them in unresolved.jsonl."
            )
    return lines + _proposed_lines(payload)


def _proposed_lines(payload: dict[str, Any]) -> list[str]:
    """What `--propose-intents` wrote, and what it deliberately left out."""
    written = payload.get("proposed_written")
    if not written:
        return []
    lines = [
        f"  wrote {written['entries']:,} PROPOSED intent(s) to {written['path']}.",
        "    None of them is confirmed and none of them can produce an ALIGNED "
        "or a MISALIGNED verdict.",
        "    Edit it, set `status: CONFIRMED` on the ones you stand behind, then "
        f"re-run with --intents {written['path']}.",
    ]
    if written.get("elements_skipped"):
        lines.append(
            f"    {written['elements_skipped']:,} element(s) got no proposal: only "
            + ", ".join(written["kinds_proposed"])
            + " are proposed for. Every element is still in elements.jsonl, and "
            "you can write an intent for any of them by hand."
        )
    return lines


#: How many intents-file problems the report prints in full. The rest are
#: counted and pointed at `unresolved.jsonl` -- an explicit cap, never a
#: silent cut.
_INTENT_ISSUES_SHOWN = 10


def _slice_lines(summary: dict[str, Any]) -> list[str]:
    """One line for slices, and it always names the scope.

    A scoped artifact that reports only its own count reads exactly like an
    exhaustive one, and an owner who mistakes the first for the second
    concludes that a root with no slice has no lineage. The count of roots NOT
    precomputed is therefore printed next to the count of slices, with where
    to get them.
    """
    payload = summary.get("slices")
    if not payload:
        return []
    line = f"  slices        {payload['slices_written']:>8,}   <- scope {payload['scope']}"
    missing = payload["roots_not_precomputed"]
    if missing:
        line += (
            f"; {missing:,} of {payload['roots_total']:,} roots not "
            f"precomputed, each still exactly recomputable from lineage.jsonl"
        )
    else:
        line += "; every root precomputed"
    lines = [line]
    # A count of zero with a reason is an answer. A count of zero without one
    # reads as "this target has no lineage", which is never what happened.
    reason = payload.get("reason")
    if reason:
        lines += [f"                {part}" for part in str(reason).splitlines()]
    return lines


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
    def flag(name: str) -> Any:
        """`trace` and `track` share the sports flags but not the rest, so a
        namespace missing a key means "not overridden", never an
        AttributeError deep inside a run."""
        return getattr(args, name, None)

    merged = dict(METATRON_SETTINGS)
    overrides = {
        "MODE": flag("mode"),
        "WORKSPACE": str(flag("workspace")) if flag("workspace") else None,
        "PROJECT": flag("project"),
        "SOURCES": False if flag("no_sources") else None,
        "VERSIONS_DIR": str(flag("versions")) if flag("versions") else None,
        "OUT_DIR": str(flag("out")) if flag("out") else None,
        "LEDGER": str(flag("ledger")) if flag("ledger") else None,
        "SINKS": flag("sink"),
        "ENTRIES": flag("entry"),
        "CONFIGS": flag("config"),
        "SLICES": flag("slices"),
        "SLICE_ROOTS": flag("slice_root"),
        "FORCE_SLICES": True if flag("force_slices") else None,
        "ENV": str(flag("env")) if flag("env") else None,
        "INTENTS": str(flag("intents")) if flag("intents") else None,
        "ORDER": flag("order"),
        "SCENARIOS": str(flag("scenarios")) if flag("scenarios") else None,
        "SCENARIO": flag("scenario"),
        "WORKERS": flag("workers"),
    }
    for key, value in overrides.items():
        if value is not None:
            merged[key] = value

    # Sports. `--sport` is validated against SPORTS *before* the override, so
    # the error lists the sports the owner's own settings declare, and then
    # narrows SPORTS to the selection: everything downstream reads
    # `Settings.selected_sports()` and there is one answer, not two.
    requested = list(flag("sport") or ())
    all_sports = bool(flag("all_sports"))
    try:
        selected = select_sports(
            tuple(merged["SPORTS"]), str(merged["SPORT"]), requested, all_sports
        )
    except ValueError as exc:
        raise SettingsError(str(exc)) from exc
    if requested or all_sports:
        merged["SPORTS"] = list(selected)
        merged["SPORT"] = selected[0] if len(selected) == 1 else ""
    extra_args = list(flag("run_arg") or ())
    if extra_args:
        merged["RUN_ARGS"] = list(merged["RUN_ARGS"]) + extra_args
    return Settings.from_mapping(merged)


def _track_trace(
    graph_dir: Path,
    scenarios: Path,
    scenario: str,
    out_root: Path,
    intents_path: Path | None = None,
) -> tuple[int, str]:
    """The one seam through which `track` can reach Mode A.

    Named rather than inlined so that a test can prove the wiring without a
    single line of the target ever executing: MODE 2 is the only path in this
    tool that runs owner code, and it runs it through `trace`, which is the
    harness's own entry point and refuses when it cannot guarantee isolation.
    """
    return trace(graph_dir, scenarios, scenario, out_root, intents_path=intents_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


#: The name the owner types. Messages that tell them what to run next must
#: name the command they actually have.
_PROG = "metatron"


def _graph_path(text: str) -> Path:
    """An argparse type for a path that must NAME something.

    `Path("")` is `Path(".")`, silently, so an unmatched shell glob arrives as
    the working directory and every downstream check passes on the wrong tree.
    Rejecting it here means the refusal happens before any command body runs,
    and nothing can be created on the way.
    """
    if not text.strip():
        raise argparse.ArgumentTypeError(
            "empty path. An empty argument is a usage error, not the working "
            "directory -- a shell glob that matched nothing produces exactly "
            f"this. Pass the directory `{_PROG} analyze --out` wrote."
        )
    return Path(text)


def _graph_dir_problem(path: Path) -> str:
    """`""` if *path* already holds a Mode B map; otherwise why it does not.

    Read-only, always: it stats and it reads a size, and it creates nothing.
    A read command that makes the directory it was supposed to find is how an
    empty answer gets a filename and a 95 KB page gets believed.

    The four diagnoses are kept distinct on purpose. "No such directory" and
    "the directory is there and holds no elements" are different problems with
    different fixes, and the owner should not have to work out which one they
    have from a single generic sentence.
    """
    run_this = f"Run `{_PROG} analyze <target> --out {path}` first."
    if not path.exists():
        return f"no map at {path} — the directory does not exist.\n{run_this}"
    if not path.is_dir():
        return f"no map at {path} — that is a file, not a directory.\n{run_this}"
    elements = path / "elements.jsonl"
    if not elements.is_file():
        return f"no map at {path} — elements.jsonl is missing.\n{run_this}"
    if elements.stat().st_size == 0:
        return (
            f"no map at {path} — found the directory, found no elements: "
            f"elements.jsonl is empty.\n"
            f"That is a map of nothing, not an empty target. Re-run "
            f"`{_PROG} analyze <target> --out {path}` and read its summary."
        )
    return ""


def _require_graph_dir(path: Path) -> bool:
    """Print the refusal and say whether the caller may continue."""
    problem = _graph_dir_problem(path)
    if problem:
        print(problem, file=sys.stderr)
        return False
    return True


def _add_progress_flags(sub_parser: argparse.ArgumentParser) -> None:
    """Progress flags, identical on `analyze`, `track` and `trace`.

    Progress is on by default and writes ONLY to stderr, so a run that is
    piped, redirected or parsed sees exactly the bytes it always saw. On a
    terminal the line is redrawn in place; anywhere else it is one plain line
    per completed stage, because a log full of carriage returns is worse than
    silence.
    """
    group = sub_parser.add_mutually_exclusive_group()
    group.add_argument(
        "--progress", dest="progress", action="store_true", default=None,
        help="show live progress even when stderr is not a terminal "
             "(one line per stage, no carriage returns)",
    )
    group.add_argument(
        "--no-progress", dest="progress", action="store_false",
        help="no progress line and no timing footer",
    )
    sub_parser.add_argument(
        "-q", "--quiet", action="store_true",
        help="suppress progress and timing entirely. Never affects stdout.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=_PROG,
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
    run.add_argument("--slices", choices=("decision", "all", "none"), default=None,
                     help='which roots get a PRECOMPUTED slice. Never how complete one is -- every slice written is exact and whole. decision (default): the roots that bear on a decision. all: every root, exhaustive and quadratic in output. none: no precomputed slices; lineage.jsonl still answers any of them.')
    run.add_argument("--slice-root", action="append", default=[], metavar="ID",
                     help='precompute the slice rooted at this id whatever --slices says; repeatable')
    run.add_argument("--force-slices", action="store_true",
                     help='write slices.jsonl even when the estimate exceeds the 1 GB size guard. The guard exists because an owner found a 6.4 GB slices.jsonl after the fact; this is how you say you meant it.')
    run.add_argument("--cache", type=Path, default=None)
    run.add_argument("--workers", type=int, default=None, metavar="N",
                     help="child processes for ingestion. Omitted or 0 = auto "
                          "(usable cores less one); 1 = in-process, which is how "
                          "this is debugged and always stays available. "
                          "Parallelism is ACROSS FILES and never splits one file, "
                          "so a single-large-file target gains nothing -- the run "
                          "measures and prints what the workers actually bought.")
    run.add_argument("--env", type=Path, default=None, metavar="PATH",
                     help="interpreter tree whose installed package metadata to "
                          "read (.venv-target in production). Read as text; nothing "
                          "under it is imported. Without it the installed half of "
                          "the dependency map is absent and the report says so.")
    run.add_argument("--mode", type=int, choices=(1, 2), default=None,
                     help="overrides MODE. 1 never executes anything. 2 needs "
                          "a completed static map, which is what this command "
                          "builds — it will tell you the exact `trace` to run "
                          "next rather than executing your engine from here.")
    run.add_argument("--max-source-mb", type=int, default=None, metavar="MB",
                     help="the dependency scanner's per-file limit: a source file "
                          "larger than this has its imports and interpreter "
                          "requirements skipped, and the skip is recorded against "
                          f"that one file (default {MAX_SOURCE_BYTES // 1_000_000}). "
                          "Every other analysis has its own limits and is unaffected.")
    run.add_argument("--parse-cache-mb", type=int, default=None, metavar="MB",
                     help="how much SOURCE the run may hold parsed at once so "
                          "that resolution, the CFG stage and lineage share one "
                          "tree per file instead of parsing it four times "
                          f"(default {DEFAULT_BUDGET_BYTES // 1_000_000}; 0 "
                          "disables sharing and every stage parses for itself). "
                          "A tree costs roughly forty times its source in "
                          "memory, so raising this trades memory for parses; "
                          "the run reports if the cache thrashed.")
    run.add_argument("--intents", type=Path, default=None, metavar="PATH",
                     help="owner-confirmed intents YAML: what each element is "
                          "MEANT to do. Overrides INTENTS. Every problem in "
                          "the file is reported with its line; none is skipped.")
    run.add_argument("--propose-intents", type=Path, default=None, metavar="PATH",
                     help="write a STARTER intents file derived from docstrings "
                          "and names. Every entry is PROPOSED and binding on "
                          "nothing until you set status: CONFIRMED.")
    run.add_argument("--no-gate", action="store_true",
                     help="write artifacts even if the completeness gate fails, "
                          "and exit 0. The gate still reports.")

    _add_progress_flags(run)

    doc = sub.add_parser(
        "doctor",
        help="measure this machine on this target and write one file you can send",
    )
    doc.add_argument("--out", type=Path, default=Path("out/doctor"),
                     help="where to write metatron_doctor_<UTC>.log and .json")
    doc.add_argument("--target", type=Path, default=None, metavar="PATH",
                     help="the tree to measure. Omitted, metatron measures its "
                          "OWN source. Nothing under PATH is ever executed.")
    doc.add_argument("--workers", type=int, action="append", default=None, metavar="N",
                     help="a worker count to measure; repeatable. "
                          "Default 1, 4 and 8.")

    cmp_ = sub.add_parser("diff", help="compare two analysed output directories")
    cmp_.add_argument("before", type=_graph_path)
    cmp_.add_argument("after", type=_graph_path)
    cmp_.add_argument("--out", type=Path, default=Path("out/diff"))

    show = sub.add_parser("view", help="render an analysed tree as offline HTML")
    show.add_argument("out_dir", type=_graph_path)
    show.add_argument("--html", type=Path, default=None)

    blue = sub.add_parser(
        "blueprint",
        help="render an analysed tree as an interactive, UE5-Blueprint-styled node canvas",
    )
    blue.add_argument("graph_dir", type=_graph_path,
                      help="an analysed Mode B output directory")
    blue.add_argument("--html", type=Path, default=None)
    blue.add_argument("--diff", type=Path, default=None,
                      help="a `metatron diff` output directory (changes.jsonl/impacts.jsonl); "
                           "without it, the Diff tab states that it has nothing loaded")
    blue.add_argument("--run", default=None, metavar="RUN_ID",
                      help="a run id under graph_dir/runtime/; layers the runtime overlay on")
    blue.add_argument("--scope", choices=("cascade", "full"), default="cascade",
                      help="cascade (default): structure only -- modules and stages as "
                           "cards, decision points, declared and detected sinks, entry "
                           "points, and the edges between them. full: every element, "
                           "every call edge and every lineage edge, which on a large "
                           "engine will be refused unless it fits or you pass --force.")
    blue.add_argument("--max-nodes", type=int, default=None, metavar="N",
                      help="keep at most N nodes, the most decision-relevant first "
                           "(sinks and entry points, then elements on a path to a sink, "
                           "then decision points, then the rest). The page and the summary "
                           "both state how many were left out. Default 2000 in cascade "
                           "scope, no cap in full scope; 0 means no cap.")
    blue.add_argument("--focus", default="", metavar="ELEMENT_ID",
                      help="draw only this element and its neighbourhood. This is also "
                           "the only way to populate the Lineage tab in cascade scope.")
    blue.add_argument("--hops", type=int, default=2, metavar="N",
                      help="how many hops --focus reaches (default 2)")
    blue.add_argument("--force", action="store_true",
                      help="write the page even when the size guard says a browser "
                           "cannot open it")
    blue.add_argument("--size-limit-mb", type=int, default=50, metavar="MB",
                      help="the size guard's threshold on the data island as the "
                           "browser carries it -- compressed, unless --no-compress "
                           "(default 50). 0 disables the guard, exactly as --force does.")
    blue.add_argument("--no-compress", action="store_true",
                      help="write the data island as plain JSON instead of gzip+base64. "
                           "Lossless either way and the same page either way; this is "
                           "only for a browser with no DecompressionStream, or for "
                           "reading the island by hand. Measured on a 116k-line engine "
                           "at --scope full: 226 MB raw, 76 MB with --no-compress, "
                           "16 MB compressed.")

    hist = sub.add_parser(
        "track",
        help="analyse every version in VERSIONS_DIR that has not been analysed "
             "already, compare consecutive versions, and update the ledger",
    )
    hist.add_argument("--workspace", type=_graph_path, default=None, metavar="DIR",
                      help="overrides WORKSPACE, the one path everything else "
                           "is derived from")
    hist.add_argument("--project", default=None, metavar="NAME",
                      help="overrides PROJECT, the folder under WORKSPACE "
                           "holding this script's whole history. Derived from "
                           "ENGINE when omitted.")
    hist.add_argument("--no-sources", action="store_true",
                      help="record each version's content hash without keeping "
                           "a copy of its tree under the workspace")
    hist.add_argument("--versions", type=Path, default=None, metavar="DIR",
                      help="overrides VERSIONS_DIR (legacy; WORKSPACE derives it)")
    hist.add_argument("--out", type=Path, default=None, help="overrides OUT_DIR")
    hist.add_argument("--ledger", type=Path, default=None, help="overrides LEDGER")
    hist.add_argument("--mode", type=int, choices=(1, 2), default=None,
                      help="overrides MODE. 2 executes your engine inside the "
                           "harness; 1 never executes anything.")
    hist.add_argument("--sink", action="append", default=None, metavar="ID",
                      help="overrides SINKS; repeatable")
    hist.add_argument("--entry", action="append", default=None, metavar="ID",
                      help="overrides ENTRIES; repeatable")
    hist.add_argument("--slices", choices=("decision", "all", "none"), default=None,
                      help='which roots get a PRECOMPUTED slice. Never how complete one is -- every slice written is exact and whole. decision (default): the roots that bear on a decision. all: every root, exhaustive and quadratic in output. none: no precomputed slices; lineage.jsonl still answers any of them.')
    hist.add_argument("--slice-root", action="append", default=None, metavar="ID",
                      help='precompute the slice rooted at this id whatever --slices says; repeatable')
    hist.add_argument("--config", action="append", default=None, metavar="PATH",
                      help="overrides CONFIGS; repeatable")
    hist.add_argument("--intents", type=Path, default=None, metavar="PATH",
                      help="owner-confirmed intents YAML. Overrides INTENTS.")
    hist.add_argument("--env", type=Path, default=None, metavar="PATH",
                      help="overrides ENV")
    hist.add_argument("--order", action="append", default=None, metavar="LABEL",
                      help="overrides ORDER, oldest first; repeatable")
    hist.add_argument("--scenarios", type=Path, default=None,
                      help="overrides SCENARIOS (MODE 2 only)")
    hist.add_argument("--scenario", default=None,
                      help="overrides SCENARIO (MODE 2 only)")
    hist.add_argument("--workers", type=int, default=None, metavar="N",
                      help="overrides WORKERS. 0 = auto, 1 = in-process.")
    hist.add_argument("--report", action="store_true",
                      help="print the whole history, not only what this run did")
    _add_sport_flags(hist)

    _add_progress_flags(hist)

    story = sub.add_parser(
        "history",
        help="read one script's whole development story back out of the workspace",
    )
    story.add_argument("project", nargs="?", default=None,
                       help="omit to list every project in the workspace, newest first")
    story.add_argument("--workspace", type=_graph_path, default=None, metavar="DIR",
                       help="overrides WORKSPACE")
    # dest is not "sport": `track` and `trace` take `--sport` REPEATABLY and
    # `_settings_from_args` reads that list. A bare string arriving there would
    # be iterated into its characters and validated as seven unknown sports.
    story.add_argument("--sport", dest="sport_name", default=None, metavar="NAME",
                       help="that sport's file only. It declares its own scope, "
                            "and a scope of UNION ACROSS ALL SPORTS means the "
                            "data under it is NOT sport-specific.")
    story.add_argument("--element", default=None, metavar="ID",
                       help="one element's life across every version: when it "
                            "appeared, every body change, every time it started "
                            "or stopped reaching a decision, and which run first "
                            "observed it executing")

    move = sub.add_parser(
        "migrate",
        help="move an existing flat layout into the workspace shape. Idempotent: "
             "a second run does nothing and says so.",
    )
    move.add_argument("--workspace", type=Path, default=None, metavar="DIR",
                      help="overrides WORKSPACE")
    move.add_argument("--project", default=None, metavar="NAME",
                      help="overrides PROJECT")
    move.add_argument("--ledger", type=Path, default=None, metavar="PATH",
                      help="the flat ledger to migrate. Defaults to "
                           "out/metatron_ledger.json, the old default.")
    move.add_argument("--versions", type=Path, default=None, metavar="DIR",
                      help="the old flat VERSIONS_DIR. Defaults to versions/.")
    move.add_argument("--out", type=Path, default=None, metavar="DIR",
                      help="the old flat OUT_DIR. Defaults to out/.")
    move.add_argument("--no-sources", action="store_true",
                      help="reuse the version ids without copying any tree")

    run_a = sub.add_parser("trace", help="Mode A — run the target under the harness")
    run_a.add_argument("graph_dir", type=_graph_path,
                       help="an analysed Mode B output directory")
    run_a.add_argument("--scenarios", type=Path, default=None,
                       help="JSON file declaring target_root and named scenarios. "
                            "Omitted, scenarios are derived from METATRON_SETTINGS: "
                            "one per selected sport, driving RUNNER by argv.")
    run_a.add_argument("--scenario", default=None,
                       help="a scenario name from --scenarios. Ignored when "
                            "scenarios are derived -- the sport is the name.")
    run_a.add_argument("--out", type=Path, default=Path("out/latest"))
    run_a.add_argument("--mode", type=int, choices=(1, 2), default=None,
                       help="overrides MODE. `--mode 1` REFUSES: mode 1 never "
                            "executes your engine. `--mode 2` runs it, and "
                            "refuses if no completed static map exists.")
    run_a.add_argument("--intents", type=Path, default=None, metavar="PATH",
                       help="owner-confirmed intents YAML. Overrides INTENTS. "
                            "Verdicts for this run land in "
                            "runtime/<run_id>/verdicts.jsonl.")
    run_a.add_argument("--preflight", action="store_true",
                       help="say whether this run could start, and what would be "
                            "active, WITHOUT executing anything")
    _add_sport_flags(run_a)
    _add_progress_flags(run_a)
    return parser


def _add_sport_flags(sub_parser: argparse.ArgumentParser) -> None:
    """The sports flags, identical on `trace` and `track`.

    The owner should never have to open a Python file to change which sport
    runs, and the two commands that run sports must not diverge on how they
    are named.
    """
    sub_parser.add_argument(
        "--sport", action="append", default=None, metavar="NAME",
        help="a sport to run; repeatable. Overrides SPORT. An unknown name is "
             "an error listing the valid ones.",
    )
    sub_parser.add_argument(
        "--all-sports", action="store_true",
        help="every sport in SPORTS, each as its own scenario",
    )
    sub_parser.add_argument(
        "--run-arg", action="append", default=None, metavar="ARG",
        help="an extra flag passed through to the runner; repeatable. Appended "
             "to RUN_ARGS. Use --run-arg=--workers --run-arg=8 for flags, so "
             "argparse does not read them as this tool's own options.",
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "analyze":
        if not args.root.is_dir():
            print(f"not a directory: {args.root}", file=sys.stderr)
            return EXIT_USAGE
        # A named intents file that does not exist is a USAGE error, decided
        # before any analysis runs. Discovering it in the report, after a
        # twenty-minute run, is how an owner ends up with a map they believe
        # their intents were applied to.
        if args.intents is not None and not args.intents.is_file():
            print(
                f"no intents file at {args.intents}. --intents names the "
                f"owner-confirmed intents YAML; `--propose-intents {args.intents}` "
                f"writes a starter one. Nothing was analysed.",
                file=sys.stderr,
            )
            return EXIT_USAGE
        intents_path = args.intents
        if intents_path is None and METATRON_SETTINGS.get("INTENTS"):
            candidate = Path(str(METATRON_SETTINGS["INTENTS"]))
            # A settings path that does not exist is still LOADED, not
            # ignored: `load_registry` turns it into a MISSING_TARGET issue
            # with the path, which is what makes the report say so.
            intents_path = candidate
        code, summary = analyze(
            args.root,
            args.out,
            intents_path=intents_path,
            propose_intents_path=args.propose_intents,
            progress=make_reporter(
                ANALYZE_STAGES, quiet=args.quiet, force=args.progress
            ),
            entry_ids=tuple(args.entry),
            sink_ids=tuple(args.sink),
            config_paths=tuple(args.config),
            slice_scope=_slice_scope_of(args),
            slice_roots=tuple(args.slice_root),
            force_slices=bool(getattr(args, "force_slices", False)),
            cache_dir=args.cache,
            strict_gate=not args.no_gate,
            env_root=args.env,
            workers=args.workers,
            max_source_bytes=(
                args.max_source_mb * 1_000_000 if args.max_source_mb
                else MAX_SOURCE_BYTES
            ),
            parse_cache_bytes=(
                args.parse_cache_mb * 1_000_000
                if args.parse_cache_mb is not None
                else DEFAULT_BUDGET_BYTES
            ),
        )
        print(_report(summary, args.out, code))
        if _mode_of(args) == 2:
            # Not a silent no-op and not an execution either. `analyze` IS the
            # mode 1 half; mode 2 is a second, explicit command that runs the
            # owner's engine, and it is never started as a side effect of a
            # flag on a command that promised not to.
            print()
            print(
                f"MODE 2 requested. `analyze` builds the static map and never "
                f"executes anything, which is the half that just finished. "
                f"Runtime observation is a separate command, so that executing "
                f"your engine is always something you asked for by name:\n"
                f"    {_PROG} trace {args.out} --mode 2"
            )
        return EXIT_OK if args.no_gate else code

    if args.command == "doctor":
        from cascade_map.doctor import SCALING_POINTS, run_doctor, write_report

        if args.target is not None and not args.target.is_dir():
            print(f"not a directory: {args.target}", file=sys.stderr)
            return EXIT_USAGE
        points = tuple(args.workers) if args.workers else SCALING_POINTS
        result = run_doctor(args.target, scaling_points=points)
        path = write_report(result, args.out)
        # The full path last, on its own line, so it can be copied straight
        # out of the terminal.
        print(f"Measured {result.target}")
        print(f"  {result.elements:,} elements, {result.file_count:,} file(s)")
        if result.errors:
            print(f"  {len(result.errors)} error(s) recorded in the report")
        print("Send this file:")
        print(path.resolve())
        return EXIT_OK

    if args.command == "diff":
        # Both sides, before either is read and before --out is created: a
        # diff of a map against nothing reports every element as deleted,
        # which is a dramatic and entirely false answer.
        if not _require_graph_dir(args.before) or not _require_graph_dir(args.after):
            return EXIT_USAGE
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
        envelope = make_envelope(quiet=args.quiet, force=args.progress)
        started = time.time()
        envelope.start(started)

        def _version_progress(label: str) -> Any:
            reporter = make_reporter(
                ANALYZE_STAGES, quiet=args.quiet, force=args.progress
            )
            reporter.note(f"analysing {label}")
            return reporter

        try:
            settings = _settings_from_args(args)
            result, ledger = track(
                settings,
                root=Path.cwd(),
                trace=_track_trace if settings.mode == 2 else None,
                progress_factory=_version_progress,
            )
        except (SettingsError, LedgerError) as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_USAGE
        finished = time.time()
        print(render_track_report(result))
        if args.report:
            print()
            print(render_history_report(ledger))
        # Last, so the timing footer sits under everything the run printed and
        # an owner scrolling to the bottom finds it where `analyze` puts it.
        envelope.finish(finished_at=finished, elapsed=finished - started)
        incomplete = [c for c in result.comparisons if c.unaccounted_element_ids]
        if incomplete:
            # Every element of both versions must land in exactly one
            # classification. One that does not is named in the ledger, and a
            # run that produced one does not report success.
            return EXIT_GATE_FAILED
        return EXIT_OK

    if args.command == "history":
        return _history_command(args)

    if args.command == "migrate":
        return _migrate_command(args)

    if args.command == "view":
        # Imported by name, not as a module object: `viewer.render_to_file`
        # needs a `viewer` namespace to exist, and in the single-file build
        # there are no module namespaces -- only globals.
        from cascade_map.viewer import render_to_file

        if not _require_graph_dir(args.out_dir):
            return EXIT_USAGE
        target = args.html or (args.out_dir / "index.html")
        render_to_file(args.out_dir, target)
        _crosslink_report_to_blueprint(target)
        print(f"Wrote {target}\nOpen it in a browser. It needs no network.")
        return EXIT_OK

    if args.command == "blueprint":
        from cascade_map.viewer.blueprint import (
            BlueprintTooLarge,
            BlueprintView,
            render_blueprint_to_file,
        )

        if not _require_graph_dir(args.graph_dir):
            return EXIT_USAGE
        target = args.html or (args.graph_dir / "blueprint.html")
        view = BlueprintView(
            scope=args.scope,
            max_nodes=args.max_nodes,
            focus=args.focus,
            hops=args.hops,
            force=args.force,
            size_limit_bytes=max(0, args.size_limit_mb) * 1024 * 1024,
            compress=not args.no_compress,
        )
        try:
            _store, selection, estimate = render_blueprint_to_file(
                args.graph_dir, target, run_id=args.run, diff_root=args.diff, view=view,
            )
        except BlueprintTooLarge as too_large:
            # Nothing was written. The owner gets the number and the ways
            # out, never a file their browser cannot open.
            print(too_large.estimate.refusal_text(), file=sys.stderr)
            return EXIT_REFUSED
        written = target.stat().st_size
        lines = [
            f"Wrote {target}  ({_human_bytes(written)}, scope `{selection.view.scope}`)",
            f"  {selection.headline()}",
        ]
        if estimate.measured:
            # Every field of every node and edge is in that island; the two
            # numbers are the same data, losslessly, in two encodings.
            form = "interned + gzip" if estimate.compressed else "interned"
            lines.append(
                f"  data island {_human_bytes(estimate.actual_bytes)} {form}, "
                f"from {_human_bytes(estimate.total_bytes)} of JSON -- nothing dropped, "
                "the browser rebuilds it exactly"
            )
        for note in selection.notes:
            lines.append(f"  - {note}")
        lines.append("Open it in a browser. It needs no network.")
        print("\n".join(lines))
        return EXIT_OK

    return _trace_command(args)


def _slice_scope_of(args: Any) -> SliceScope:
    """The slice scope this invocation runs under: the flag, else SLICES.

    One function, exactly like `_mode_of`, so a flag and a setting can never
    disagree about how much of `slices.jsonl` an owner is looking at.
    """
    flag = getattr(args, "slices", None)
    if flag:
        return SliceScope(str(flag).upper())
    return Settings.from_mapping({"SLICES": METATRON_SETTINGS.get("SLICES", "DECISION")}).slices


def _mode_of(args: Any) -> int:
    """The mode this invocation runs in: the flag, else MODE in the settings.

    One function, so a command cannot read the flag and another read the
    setting and the two disagree about whether the engine is allowed to run.
    """
    flag = getattr(args, "mode", None)
    if flag in (1, 2):
        return int(flag)
    value = METATRON_SETTINGS.get("MODE", 1)
    return int(value) if value in (1, 2) else 1


def _workspace_for(args: Any, project: str | None = None) -> Any:
    """The workspace a read-only command is pointed at.

    Built from METATRON_SETTINGS with the flags on top, exactly like every
    other command, so `history` and `track` can never disagree about where the
    history lives.
    """
    from cascade_map.ledger import layout_for
    from cascade_map.workspace import Workspace

    settings = _settings_from_args(args)
    layout = layout_for(settings, Path.cwd())
    if project:
        return Workspace(root=layout.workspace.root, project=project)
    return layout.workspace


def _history_command(args: Any) -> int:
    """`history`, in its four forms.

    The fourth -- one element across every version -- is the point of the
    whole layout, and it is answerable only because every version's
    fingerprints were kept as lines rather than rewritten as one document.
    """
    from cascade_map.workspace import (
        WorkspaceError,
        element_life,
        list_projects,
        read_history,
        render_element_life,
        render_history,
    )
    from cascade_map.ledger import Ledger, LedgerError, layout_for, observed_runs_for

    try:
        settings = _settings_from_args(args)
    except SettingsError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    layout = layout_for(settings, Path.cwd())
    root = layout.workspace.root

    # `history` READS, and it never creates the workspace it was asked to
    # read. Two different absences, kept apart:
    #
    # * the DEFAULT workspace has simply never been written -- that is an
    #   absence, said as one, exit 0, which is card 6's decision and stands;
    # * a path the owner TYPED is not there -- that is a usage error, because
    #   the owner believes they named something and they did not. Rendering an
    #   empty history for it would answer a question about the wrong tree.
    named = getattr(args, "workspace", None) is not None
    if named and not root.exists():
        print(
            f"no workspace at {root} — the directory does not exist.\n"
            f"Run `{_PROG} track` to create one, or `{_PROG} migrate` if you "
            f"have a flat layout from before the workspace existed.",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if root.exists() and not root.is_dir():
        print(
            f"no workspace at {root} — that is a file, not a directory.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    if not args.project:
        projects = list_projects(root)
        if not projects:
            # An absence, said as an absence. "No projects" printed over a
            # workspace that was never written would read as "nothing changed".
            print(
                f"No project in {root} carries a history file yet. That is an "
                f"absence, not an empty history: run `metatron track` to start "
                f"one, or `metatron migrate` if you have a flat layout from "
                f"before the workspace existed."
            )
            return EXIT_OK
        print(f"{root} — {len(projects):,} project(s)")
        for name in projects:
            space = _workspace_for(args, name)
            document = read_history(space)
            print(
                f"  {name}: {len(document.get('versions', [])):,} version(s), "
                f"{len(document.get('comparisons', [])):,} comparison(s), "
                f"{int(document.get('disk', {}).get('total_bytes', 0)):,} B on disk"
            )
        return EXIT_OK

    space = _workspace_for(args, args.project)
    try:
        if args.element:
            observed = {}
            try:
                ledger = Ledger(settings, root=Path.cwd(), layout=layout).load(
                    space.history_file
                )
                observed = observed_runs_for(ledger, args.element)
            except LedgerError as exc:
                # Said out loud. A silent empty `observed` would render as
                # "never observed executing", which is a different claim.
                print(f"NOTE  Mode A runs could not be scanned: {exc}", file=sys.stderr)
            document = read_history(space)
            document = {**document, "observed_elements": observed}
            life = element_life(space, args.element, history=document)
            print(render_element_life(life, project=args.project))
            return EXIT_OK
        document = read_history(space)
        print(render_history(document, workspace=space, sport=args.sport_name or ""))
        return EXIT_OK
    except WorkspaceError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE


def _migrate_command(args: Any) -> int:
    """`migrate`. Copies, never moves, and reuses every version id."""
    from cascade_map.ledger import layout_for
    from cascade_map.workspace import WorkspaceError, migrate, render_migration

    try:
        settings = _settings_from_args(args)
    except SettingsError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    root = Path.cwd()
    layout = layout_for(settings, root)
    ledger_path = args.ledger or Path("out/metatron_ledger.json")
    if not ledger_path.is_absolute():
        ledger_path = root / ledger_path
    try:
        result = migrate(
            root=root,
            workspace=layout.workspace,
            ledger_path=ledger_path,
            versions_dir=root / (args.versions or Path("versions")),
            out_dir=root / (args.out or Path("out")),
            keep_sources=not args.no_sources,
        )
    except WorkspaceError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    print(render_migration(result))
    return EXIT_OK


def _trace_command(args: Any) -> int:
    """`trace`. The only command that executes owner code, and the only one
    that needs the owner to have asked for it by name.

    Two ways in, and the file wins when it is given:

    * ``--scenarios FILE`` -- the declared path, unchanged.
    * nothing -- scenarios are DERIVED from METATRON_SETTINGS, one per
      selected sport, each driving RUNNER through ``argv``. This is what
      makes `trace out/amun --sport basketball` work with no scenarios file
      and no edit to any Python file.

    Derivation never guesses. A runner it cannot resolve is a refusal naming
    the path it tried.
    """
    try:
        settings = _settings_from_args(args)
    except SettingsError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE

    # `trace` is mode 2 BY NAME. Typing it is the explicit command Mode A
    # requires, so the MODE setting does not gate it -- gating it would mean an
    # owner who ran `trace` on purpose was refused by a line in a dict. An
    # explicit `--mode 1` is different: that is the owner saying "not this
    # time" out loud, and mode 1's whole definition is that it never executes
    # anything, so it refuses rather than running anyway.
    intents_path = Path(settings.intents) if settings.intents else None
    if intents_path is not None and not intents_path.is_file():
        print(
            f"no intents file at {intents_path}. --intents (or INTENTS) names the "
            f"owner-confirmed intents YAML. Nothing was executed.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    if getattr(args, "mode", None) == 1:
        print(
            "REFUSED: MODE 1 never executes your engine, and `trace` is the only "
            f"command that does. Nothing was executed. Run `{_PROG} analyze` "
            "for the static map, or drop `--mode 1` to trace.",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    problem = _graph_dir_problem(args.graph_dir)
    if problem:
        # Keyed to nothing is worse than not run: a runtime overlay with no
        # graph to attach to cannot be read, compared or trusted. The
        # diagnosis is the shared one, so `trace` distinguishes "no such
        # directory" from "directory, no elements" exactly as the read-only
        # commands do -- and REFUSED rather than USAGE, because this is the
        # one command that would otherwise have executed the owner's engine.
        print(
            f"REFUSED: MODE 2 always builds on a completed static map.\n"
            f"{problem}\n"
            f"Run mode 1 first:\n"
            f"    {_PROG} analyze <your target> --out {args.graph_dir}\n"
            f"Nothing was executed.",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    if args.scenarios is not None:
        if args.preflight:
            code, message = preflight(
                args.graph_dir, args.out, settings, (), args.scenarios
            )
            print(message, file=sys.stderr if code else sys.stdout)
            return code
        if not args.scenario:
            print(
                "--scenario is required with --scenarios: naming the file is not "
                "naming which of its scenarios to run.",
                file=sys.stderr,
            )
            return EXIT_USAGE
        if not args.scenarios.is_file():
            print(f"no scenarios file at {args.scenarios}", file=sys.stderr)
            return EXIT_USAGE
        return _timed_trace(
            args, args.scenarios, args.scenario, intents_path=intents_path
        )

    sports = settings.selected_sports()
    if args.preflight:
        code, message = preflight(args.graph_dir, args.out, settings, sports, None)
        print(message, file=sys.stderr if code else sys.stdout)
        return code

    try:
        document = derive_scenario_document(
            target_root_of(args.graph_dir),
            sports=sports,
            runner=settings.runner,
            engine=settings.engine,
            run_args=settings.run_args,
        )
    except ScenarioDerivationError as exc:
        print(
            f"REFUSED: {exc}\n\n"
            f"No scenarios file was given, so scenarios are derived from "
            f"METATRON_SETTINGS. A refusal is the correct outcome here — nothing "
            f"was executed. Give --scenarios FILE to declare them explicitly.",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    # Written out, not kept in memory, for two reasons: the owner can read
    # exactly what was derived and hand-edit it into a scenarios file, and
    # `trace()` keeps one code path for both ways in.
    derived_path = args.out / "derived_scenarios.json"
    derived_path.parent.mkdir(parents=True, exist_ok=True)
    derived_path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    if args.scenario and args.scenario not in sports:
        print(
            f"--scenario {args.scenario!r} names no derived scenario. Derived "
            f"scenarios are named for their sport: "
            f"{', '.join(sports) if sports else 'none selected'}. Use --sport.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    chosen = (args.scenario,) if args.scenario else sports
    print(
        f"Derived {len(chosen)} scenario(s) from METATRON_SETTINGS "
        f"({', '.join(chosen)}), written to {derived_path}.\n"
    )
    worst = EXIT_OK
    for sport in chosen:
        worst = max(
            worst,
            _timed_trace(
                args, derived_path, sport, banner=sport, intents_path=intents_path
            ),
        )
    return worst


def _timed_trace(
    args: Any,
    scenario_file: Path,
    scenario: str,
    *,
    banner: str = "",
    intents_path: Path | None = None,
) -> int:
    """One traced scenario, with its progress line and its timing footer.

    The reporter is started and finished HERE rather than inside `trace()`,
    because `trace()` returns a refusal from a dozen places and a run that
    printed `started` and never printed `finished` reads exactly like a hang --
    which is the complaint this whole feature answers.
    """
    bar = make_reporter(TRACE_STAGES, quiet=args.quiet, force=args.progress)
    started = time.time()
    bar.start(started)
    try:
        code, message = trace(
            args.graph_dir,
            scenario_file,
            scenario,
            args.out,
            progress=bar,
            intents_path=intents_path,
        )
    finally:
        finished = time.time()
        bar.finish(finished_at=finished, elapsed=finished - started)
    if banner:
        print(f"=== {banner} ===")
    print(message, file=sys.stderr if code else sys.stdout)
    if banner:
        print()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
