"""The work-stealing coordinator every parallelisable stage shares.

One place, used by every stage that can take it. The shape is the owner's:
a task manager hands whole units out of a shared queue, an idle worker
immediately takes the next one rather than waiting on a fixed batch it was
assigned up front, and the results come back to the manager which
consolidates them **sequentially and deterministically**.

Four rules this module exists to enforce
----------------------------------------

**1. A task is a WHOLE UNIT.** A whole element, a whole function, a whole
module. Never a fragment: half a function is not parseable and the facts
derived from it would be *wrong*, not merely worse.

**2. Consolidation is sequential.** :func:`run_stage` returns a plain
``{key: value}`` mapping and the caller re-derives its own order from the
keys. Nothing about which worker finished first is allowed to reach an
artifact. Where a stage mints sequential ids, it must mint them in the
consolidation step, never in a worker -- a counter that advances in worker
completion order is a determinism bug that only shows up under load.

**3. Large shared state travels ONCE PER WORKER, never per task.** The
owner's engine is one 15 MB file: sending its source, or its element index,
with every one of ten thousand tasks would cost more than the parallelism
saves. :class:`Payload` is built once inside each worker (``initializer``)
and every task in that worker reads it. Under the ``fork`` start method it
is not even pickled -- the child inherits it.

**4. Every number is measured on this run.** :class:`StageReport` reports
what the workers actually bought, per stage. Where a stage does not pay, it
says so with the number and the reason, and the recommendation is one
worker. A worker count printed as a benefit without evidence is worse than
none.

What is NOT parallelisable, and is not faked
--------------------------------------------

    resolve      per module against a shared index      parallel
    cascade      one control-flow graph per function    parallel
    lineage      per module dataflow, then merge        parallel
    records      per element                            parallel
    write        one artifact per file                  parallel
    order        topological closure over the graph     SEQUENTIAL
    reachability closure over the reversed graph        SEQUENTIAL

The last two are closures over the whole graph: every node's answer depends
on every other node's. They stay in one process and :class:`PipelineReport`
prints their cost rather than hiding it, because a wrong graph is infinitely
worse than a slow one.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from .contracts.interfaces import canonical_dumps

__all__ = [
    "GAIN_FLOOR",
    "MAX_WORKERS",
    "Payload",
    "PipelineReport",
    "StageReport",
    "UnitResult",
    "canonical_jsonl_many",
    "chunk_bounds",
    "cpu_budget",
    "render_pipeline_report",
    "render_stage_line",
    "resolve_workers",
    "run_stage",
]

#: Below this, the workers are not earning their overhead and the report says
#: so in words rather than printing a count that implies a benefit.
GAIN_FLOOR = 1.2

#: Asking for more processes than this has never paid on any shape measured;
#: beyond the core count they contend rather than help.
MAX_WORKERS = 32


def cpu_budget() -> int:
    """Usable cores. ``sched_getaffinity`` rather than ``cpu_count`` because a
    container pinned to 2 of 64 cores reports 64, and spawning 63 workers on 2
    cores is slower than staying in-process."""
    try:
        return len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except (AttributeError, OSError):  # pragma: no cover - non-Linux
        return os.cpu_count() or 1


def resolve_workers(requested: int | None) -> int:
    """``None`` = auto: the machine's usable cores minus one, so an ``analyze``
    does not take the whole box, capped at :data:`MAX_WORKERS`.

    ``0`` and ``1`` both mean in-process. In-process must always remain
    reachable: it is how anything here is debugged, and a traceback out of a
    child process is a worse one.
    """
    if requested is None:
        return max(1, min(cpu_budget() - 1, MAX_WORKERS))
    if requested < 0:
        raise ValueError(f"--workers must be >= 0, got {requested}")
    if requested <= 1:
        return 1
    return min(requested, MAX_WORKERS)


# ---------------------------------------------------------------------------
# the report -- measured, never asserted
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UnitResult:
    """One unit's outcome plus the CPU seconds it cost inside its worker."""

    key: str
    value: Any
    seconds: float


@dataclass
class StageReport:
    """What the workers bought **in one stage, on this run**.

    Never written into an analysis artifact: these are wall-clock timings and
    constraint 4 says identical input gives identical bytes.
    """

    stage: str = ""
    requested: int = 1
    started: int = 1
    unit_count: int = 0

    #: Wall clock of the parallel section alone.
    wall_seconds: float = 0.0
    #: Sum of the per-unit CPU seconds -- what one process would have spent.
    serial_seconds: float = 0.0
    #: Wall clock of the WHOLE stage, parallel section included. Set by the
    #: caller. The gain an owner feels is the one over the whole stage; the
    #: serial remainder does not shrink with more workers.
    total_seconds: float = 0.0
    #: The part of the stage that is sequential by nature and can never be
    #: spread: a topological closure, a merge, a sort.
    sequential_by_nature: bool = False

    #: Why this stage was not handed to the pool at all. Set when the units
    #: exist but are NOT INDEPENDENT -- a walk that carries state from one
    #: unit into the next has no unit boundary to cut on, and cutting anyway
    #: would give a different graph, not a faster one. Stated, never hidden:
    #: a stage silently left serial reads as a stage that had nothing to gain.
    not_parallelised_reason: str = ""

    largest_unit_key: str = ""
    largest_unit_seconds: float = 0.0
    skipped_reason: str = ""
    unit_seconds: dict[str, float] = field(default_factory=dict, repr=False)

    @property
    def serial_control_seconds(self) -> float:
        """What this same stage would have cost in one process: the CPU the
        units actually consumed, plus the serial remainder no worker count can
        remove. Computed from data already held rather than by running the
        whole thing twice, and checked against a genuine ``--workers 1`` run in
        the tests."""
        total = self.total_seconds or self.wall_seconds
        non_parallel = max(0.0, total - self.wall_seconds)
        return self.serial_seconds + non_parallel

    @property
    def gain(self) -> float:
        total = self.total_seconds or self.wall_seconds
        if total <= 0.0:
            return 1.0
        return self.serial_control_seconds / total

    @property
    def largest_unit_share(self) -> float:
        if self.serial_seconds <= 0.0:
            return 0.0
        return self.largest_unit_seconds / self.serial_seconds

    @property
    def helped(self) -> bool:
        return self.started > 1 and self.gain >= GAIN_FLOOR

    def explain_no_gain(self) -> str:
        """Why the workers did not help. Always a measured reason."""
        if self.sequential_by_nature:
            return (
                "sequential by nature: a closure over the whole graph, where "
                "every node's answer depends on every other node's"
            )
        if self.not_parallelised_reason:
            return self.not_parallelised_reason
        if self.unit_count == 0:
            return self.skipped_reason or "nothing to do -- no units of work"
        # A stage that declined the pool said exactly why when it declined;
        # the generic guesses below are for a pool that ran and did not pay.
        if self.skipped_reason:
            return self.skipped_reason
        if self.unit_count == 1:
            return (
                "1 unit holds 100% of the work; a unit is never split, because "
                "half of one is not analysable"
            )
        share = self.largest_unit_share
        if share >= 0.5:
            return (
                f"1 unit ({self.largest_unit_key}) holds {share * 100:.0f}% of "
                "the work; a unit is never split"
            )
        if self.started <= 1:
            return "running in-process; no child workers were started"
        if self.unit_count < self.started:
            return (
                f"{self.unit_count} unit(s) for {self.started} worker(s); "
                f"{self.started - self.unit_count} idled"
            )
        return (
            "per-unit work is small enough that sending the unit out and its "
            "result back cost about as much as they save"
        )

    def recommendation(self) -> str:
        if self.not_parallelised_reason:
            return "one worker; the units here are not independent of each other"
        if self.sequential_by_nature:
            return "one worker; parallelising this would give a wrong graph, not a fast one"
        if self.helped:
            return f"--workers {self.started} earns its overhead on this stage"
        if self.unit_count <= 1 or self.largest_unit_share >= 0.5:
            return "use 1 worker for this stage on this target"
        return "use 1 worker for this stage; the gain does not cover the overhead"


def render_stage_line(report: StageReport) -> str:
    """One aligned line per stage, in the shape the owner asked for::

        resolve    3.40x over 8 workers    (78.2s of work in 23.0s, 412 units)
        order      sequential by nature    (2.1s)
    """
    name = f"  {report.stage:<12}"
    total = report.total_seconds or report.wall_seconds
    if report.sequential_by_nature:
        return f"{name}{'sequential by nature':<24}({total:.1f}s)"
    if report.not_parallelised_reason:
        units = f", {report.unit_count:,} unit(s)" if report.unit_count else ""
        return (
            f"{name}{'1.00x, not parallelised':<24}({total:.1f}s{units}) "
            f"-- {report.not_parallelised_reason}"
        )
    if report.unit_count == 0:
        reason = report.skipped_reason or "no units of work"
        return f"{name}{'n/a':<24}({reason})"
    if report.started <= 1:
        return (
            f"{name}{'1.00x over 1 worker':<24}"
            f"({total:.1f}s, {report.unit_count:,} unit(s)) "
            f"-- {report.explain_no_gain()}"
        )
    body = (
        f"{report.gain:.2f}x over {report.started} workers".ljust(24)
        + f"({report.serial_control_seconds:.1f}s of work in {total:.1f}s, "
        f"{report.unit_count:,} unit(s))"
    )
    if not report.helped:
        body += f" -- {report.explain_no_gain()}"
    return name + body


@dataclass
class PipelineReport:
    """Every parallelised stage, measured per stage, plus the sequential ones.

    The owner has been told twice that a worker count printed as a benefit
    without evidence is worse than none. Every line here is a measurement of
    the run that just happened.
    """

    requested: int = 1
    stages: list[StageReport] = field(default_factory=list)

    def add(self, report: StageReport) -> StageReport:
        self.stages.append(report)
        return report

    def stage(self, name: str) -> StageReport | None:
        for report in self.stages:
            if report.stage == name:
                return report
        return None

    @staticmethod
    def _spread(stage: StageReport) -> bool:
        """Whether a worker count can touch this stage at all."""
        return not stage.sequential_by_nature and not stage.not_parallelised_reason

    @property
    def parallel_seconds(self) -> float:
        return sum(
            (s.total_seconds or s.wall_seconds)
            for s in self.stages
            if self._spread(s)
        )

    @property
    def sequential_seconds(self) -> float:
        return sum(
            (s.total_seconds or s.wall_seconds)
            for s in self.stages
            if not self._spread(s)
        )

    @property
    def measured_seconds(self) -> float:
        return self.parallel_seconds + self.sequential_seconds

    @property
    def sequential_share(self) -> float:
        total = self.measured_seconds
        return 0.0 if total <= 0.0 else self.sequential_seconds / total


def render_pipeline_report(report: PipelineReport) -> str:
    """The worker-effectiveness report, one block covering every stage."""
    if not report.stages:
        return ""
    lines = [f"worker effectiveness, measured on this run ({report.requested} requested)"]
    lines.extend(render_stage_line(stage) for stage in report.stages)
    total = report.measured_seconds
    if total > 0.0:
        lines.append(
            f"  {'sequential':<12}{report.sequential_seconds:.1f}s of "
            f"{total:.1f}s measured ({report.sequential_share * 100:.0f}%) no "
            "worker count removes -- see the reason on each line above"
        )
    unpaid = [
        s.stage
        for s in report.stages
        if not s.sequential_by_nature
        and not s.not_parallelised_reason
        and s.started > 1
        and not s.helped
    ]
    if unpaid:
        lines.append(
            "  "
            + f"{'note':<12}"
            + f"{', '.join(unpaid)} did not pay for the workers on this target; "
            "the line above each says why"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------


class Payload:
    """Base for the large state a stage's workers share.

    Built ONCE per worker. The instance that travels to a worker must hold
    only what is cheap to send -- a root path, a list of file paths, the
    already-parsed inputs a stage genuinely needs -- and :meth:`open` does the
    expensive part (reading, parsing, indexing) inside the worker.

    Subclasses must be picklable, which means module-level classes holding
    plain data: a closure is not picklable and neither is an open file.
    """

    def open(self) -> Any:
        """Return the object every task in this worker will be handed."""
        return self


#: Set by :func:`_init_worker` inside each child, read by every task that child
#: runs. A module-level global rather than an argument because the whole point
#: is that it crosses the process boundary once and not once per task.
_OPENED: Any = None


def _init_worker(payload: Payload | None) -> None:
    global _OPENED
    _OPENED = None if payload is None else payload.open()


class _Task:
    """A picklable bound work function.

    ``ProcessPoolExecutor`` pickles the callable, and a closure is not
    picklable, so the stage's function travels as a module-level class
    instance instead.
    """

    __slots__ = ("fn",)

    def __init__(self, fn: Callable[[Any, Any], Any]) -> None:
        self.fn = fn

    def __call__(self, job: tuple[str, Any]) -> UnitResult:
        key, arg = job
        # `process_time`, i.e. CPU seconds, NOT wall clock. Under a pool with
        # more workers than cores every unit's *wall* time inflates because the
        # units compete for the same cores; summing inflated wall times and
        # dividing by the section's wall clock produces a gain that RISES as
        # the machine gets slower. The work here is CPU-bound, so CPU seconds
        # are what a single process would have spent.
        start = time.process_time()
        value = self.fn(_OPENED, arg)
        return UnitResult(key=key, value=value, seconds=time.process_time() - start)


def run_stage(
    fn: Callable[[Any, Any], Any],
    jobs: Sequence[tuple[str, Any]],
    workers: int,
    report: StageReport,
    *,
    payload: Payload | None = None,
    on_unit: Callable[[int, int], None] | None = None,
    min_units_per_worker: int = 1,
) -> dict[str, Any]:
    """Run every ``(key, arg)`` job through ``fn(opened_payload, arg)``.

    Returns ``{key: value}``. **The caller must re-derive its own order from
    the keys.** Nothing about the returned mapping's construction order is
    allowed to reach an artifact.

    Falls back to in-process, recording why, whenever a pool cannot help or
    cannot start. A failed pool must never become a failed analysis: the work
    is still done, just serially, and the report says so.
    """
    report.requested = workers
    report.unit_count = len(jobs)
    report.unit_seconds.clear()

    if not jobs:
        report.started = 1
        if not report.skipped_reason:
            report.skipped_reason = "no units of work"
        return {}

    too_few = len(jobs) < workers * min_units_per_worker
    if workers <= 1 or len(jobs) == 1 or too_few:
        if workers > 1 and len(jobs) == 1:
            report.skipped_reason = (
                "1 unit of work: a pool was not started because one worker "
                "would do all of it and the rest would idle"
            )
        elif workers > 1 and too_few:
            report.skipped_reason = (
                f"{len(jobs)} unit(s) is too few for {workers} worker(s) to pay "
                "for their own startup; ran in-process"
            )
        return _run_in_process(fn, jobs, report, payload=payload, on_unit=on_unit)

    effective = min(workers, len(jobs))
    started_at = time.perf_counter()
    try:
        with ProcessPoolExecutor(
            max_workers=effective,
            initializer=_init_worker,
            initargs=(payload,),
        ) as pool:
            results: list[UnitResult] = []
            # `chunksize=1` is what makes this work-STEALING rather than a
            # fixed batch: every unit goes into one shared queue and an idle
            # worker takes the next one the instant it finishes, so one slow
            # unit cannot leave seven workers idle. Iterated rather than
            # `list(...)` so progress is reported as results arrive; `map`
            # still yields in SUBMISSION order, so only when we hear about a
            # result changes, never the order of anything emitted.
            for done, item in enumerate(pool.map(_Task(fn), jobs, chunksize=1), start=1):
                results.append(item)
                if on_unit is not None:
                    on_unit(done, len(jobs))
    except Exception as exc:  # pragma: no cover - platform dependent
        report.skipped_reason = (
            f"pool unavailable ({type(exc).__name__}); ran in-process"
        )
        report.wall_seconds = 0.0
        report.unit_seconds.clear()
        return _run_in_process(fn, jobs, report, payload=payload, on_unit=on_unit)

    report.started = effective
    out: dict[str, Any] = {}
    for result in results:
        out[result.key] = result.value
        report.unit_seconds[result.key] = result.seconds
    report.wall_seconds = time.perf_counter() - started_at
    report.serial_seconds = sum(report.unit_seconds.values())
    _record_largest(report)
    return out


def _run_in_process(
    fn: Callable[[Any, Any], Any],
    jobs: Sequence[tuple[str, Any]],
    report: StageReport,
    *,
    payload: Payload | None,
    on_unit: Callable[[int, int], None] | None,
) -> dict[str, Any]:
    """The path everything is debugged on. Must always stay reachable."""
    global _OPENED
    previous = _OPENED
    _OPENED = None if payload is None else payload.open()
    report.started = 1
    started_at = time.perf_counter()
    task = _Task(fn)
    out: dict[str, Any] = {}
    try:
        for done, job in enumerate(jobs, start=1):
            result = task(job)
            out[result.key] = result.value
            report.unit_seconds[result.key] = result.seconds
            if on_unit is not None:
                on_unit(done, len(jobs))
    finally:
        _OPENED = previous
    report.wall_seconds = time.perf_counter() - started_at
    report.serial_seconds = sum(report.unit_seconds.values())
    _record_largest(report)
    return out


def _record_largest(report: StageReport) -> None:
    if not report.unit_seconds:
        return
    # Sorted by (-seconds, key) so ties break on the name, never on dict
    # insertion order -- the report is printed, and an owner comparing two runs
    # must not see a different unit named for the same timings.
    key = sorted(report.unit_seconds.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    report.largest_unit_key = key
    report.largest_unit_seconds = report.unit_seconds[key]


# ---------------------------------------------------------------------------
# chunking -- whole units, many more chunks than workers
# ---------------------------------------------------------------------------


def chunk_bounds(total: int, per_chunk: int) -> list[tuple[int, int]]:
    """Contiguous half-open ranges over an already-ordered sequence.

    Many more chunks than workers, deliberately: that is what lets the shared
    queue *steal*. One chunk per worker is a fixed batch, and a fixed batch is
    what leaves seven workers idle behind one slow unit.
    """
    if total <= 0:
        return []
    step = max(1, per_chunk)
    return [(start, min(total, start + step)) for start in range(0, total, step)]


# ---------------------------------------------------------------------------
# the write stage -- one artifact's records, in whole-record chunks
# ---------------------------------------------------------------------------

#: Records per chunk when serialising artifacts. Small enough that 390,000
#: lineage edges become ~80 stealable units; large enough that the round trip
#: is a rounding error against the work in the chunk.
JSONL_CHUNK_RECORDS = 5_000

#: Total records below which the write stage stays in one process. Measured,
#: not guessed: the fixture corpus writes ~1,600 records across 18 artifacts
#: and a four-worker pool came back at 0.78x -- slower than doing it here. A
#: pool that loses is worse than no pool, so it is not started.
JSONL_MIN_RECORDS_FOR_POOL = 20_000


class _JsonlPayload(Payload):
    """Every artifact's records, sent ONCE per worker.

    Under the ``fork`` start method the child inherits them and nothing is
    pickled at all; under ``spawn`` they cross once per worker rather than
    once per chunk, which on a 437 MB artifact set is the difference between
    a win and a loss.
    """

    __slots__ = ("groups", "sort_key")

    def __init__(self, groups: Mapping[str, Sequence[Any]], sort_key: str) -> None:
        self.groups = {name: tuple(records) for name, records in groups.items()}
        self.sort_key = sort_key

    def open(self) -> "_JsonlPayload":
        return self


def _jsonl_unit(
    payload: "_JsonlPayload", arg: tuple[str, int, int]
) -> list[tuple[Any, str]]:
    """Serialise one contiguous run of WHOLE records.

    A record is never split across chunks: half a JSON object is not a record,
    and an artifact holding one would be wrong rather than merely untidy.

    Returns ``(sort key, row)`` pairs. The sort key is read back out of the
    rendered row exactly as `canonical_jsonl` reads it, so the ordering cannot
    drift from the contract's serialiser by taking a shortcut through the
    record object.
    """
    name, start, stop = arg
    sort_key = payload.sort_key
    out: list[tuple[Any, str]] = []
    for record in payload.groups[name][start:stop]:
        row = canonical_dumps(record)
        out.append((json.loads(row).get(sort_key, ""), row))
    return out


def canonical_jsonl_many(
    groups: Mapping[str, Sequence[Any]],
    workers: int,
    report: StageReport,
    *,
    sort_key: str = "id",
    on_unit: Callable[[int, int], None] | None = None,
) -> dict[str, str]:
    """`{name: canonical_jsonl(records)}` for several artifacts at once.

    **Byte-identical to calling `canonical_jsonl` on each group**, and
    `test_parallel_jsonl_is_byte_identical_at_every_worker_count` is what
    keeps it that way. The rendering is per record and parallel; the ORDERING
    is a single global sort in this process, over ``(sort key, row)`` -- the
    very tuple `canonical_jsonl` sorts by -- so which worker rendered which
    row cannot reach a byte of the output.

    One pool for the whole write stage rather than one per artifact: the
    artifacts differ in size by three orders of magnitude, and a pool per
    artifact would start eight processes to serialise an empty file.
    """
    names = sorted(groups)
    total_records = sum(len(groups[name]) for name in names)
    if total_records < JSONL_MIN_RECORDS_FOR_POOL:
        workers = 1
        report.skipped_reason = (
            f"{total_records:,} record(s) is below the {JSONL_MIN_RECORDS_FOR_POOL:,} "
            "at which a pool starts paying for itself here; ran in-process"
        )
    jobs: list[tuple[str, tuple[str, int, int]]] = []
    chunk_counts: dict[str, int] = {}
    for name in names:
        bounds = chunk_bounds(len(groups[name]), JSONL_CHUNK_RECORDS)
        chunk_counts[name] = len(bounds)
        for index, (start, stop) in enumerate(bounds):
            jobs.append((f"{name}#{index:08d}", (name, start, stop)))

    results = run_stage(
        _jsonl_unit,
        jobs,
        workers,
        report,
        payload=_JsonlPayload(groups, sort_key),
        on_unit=on_unit,
        # A handful of chunks is not worth eight processes; the report says so
        # with the number rather than printing a worker count that bought
        # nothing.
        min_units_per_worker=2,
    )

    out: dict[str, str] = {}
    for name in names:
        pairs: list[tuple[Any, str]] = []
        for index in range(chunk_counts[name]):
            # `pop`, not `get`: the parent holds every artifact's rows at once
            # and a 437 MB artifact set is not something to keep two copies of.
            pairs.extend(results.pop(f"{name}#{index:08d}", ()))
        pairs.sort()
        out[name] = "".join(f"{row}\n" for _, row in pairs)
        del pairs
    return out
