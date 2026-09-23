"""Worker policy, the process pool, and the worker-effectiveness report.

Three things live here, and the third is the one that matters most to an
owner:

1. `resolve_workers` — how many child processes to ask for. Auto is derived
   from the machine, with headroom, never a fixed number written in a config
   file on a different machine.
2. `run_units` — run a list of independent units, in-process or across a
   pool. Results come back keyed by unit id; the caller re-orders them, so
   output can never depend on which worker finished first.
3. `WorkerReport` — what the workers actually bought **on this run**,
   measured, not asserted. A worker count printed on its own reads as a
   benefit; it is not one. Where the gain is below `GAIN_FLOOR` the report
   says plainly that the workers did not help, and why.

Why the gain is measurable without running the whole thing twice: every unit
is timed individually in CPU seconds, so the sum of the per-unit times is
what one process would have spent on the same work; adding the serial
remainder that no worker count can remove gives a single-process control for
the whole stage. `gain = that control / this run's wall clock`. It is
computed from data already held, and it is checked against a genuine
`--workers 1` run in the tests -- on `src/cascade_map` the estimate said
2.4x and the real control said 2.35x.

A LIMIT THE OWNER MUST KNOW, measured on a 14.6 MB single-file target:
parallelism here is ACROSS FILES. One file is one unit and a unit is never
split, because half a function is not parseable and the facts derived from
it would be wrong rather than merely untidy. A target that is one large file
therefore has exactly one unit: one worker takes it and the rest idle. See
`explain_no_gain` -- that case is detected and stated rather than hidden
behind a worker count.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Any

#: Below this, the workers are not earning their overhead and the report
#: says so in words rather than printing a count that implies a benefit.
GAIN_FLOOR = 1.2

#: Asking for more processes than this has never paid on any shape measured;
#: beyond the core count they contend rather than help.
MAX_WORKERS = 32


def cpu_budget() -> int:
    """Usable cores. `sched_getaffinity` rather than `cpu_count` because a
    container pinned to 2 of 64 cores reports 64, and spawning 63 workers on
    2 cores is slower than staying in-process."""
    try:
        return len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except (AttributeError, OSError):  # pragma: no cover - non-Linux
        return os.cpu_count() or 1


def resolve_workers(requested: int | None) -> int:
    """`None` = auto: the machine's usable cores minus one, so an `analyze`
    does not take the whole box, capped at `MAX_WORKERS`.

    `0` and `1` both mean in-process. In-process must always remain
    reachable: it is how anything here is debugged, and a traceback from a
    child process is a worse one.
    """
    if requested is None:
        return max(1, min(cpu_budget() - 1, MAX_WORKERS))
    if requested < 0:
        raise ValueError(f"--workers must be >= 0, got {requested}")
    if requested <= 1:
        return 1
    return min(requested, MAX_WORKERS)


@dataclass(frozen=True)
class UnitResult:
    """One unit's outcome plus the CPU seconds it cost inside its worker."""

    key: str
    value: Any
    seconds: float


@dataclass
class WorkerReport:
    """What the workers bought on this run. Never written into an analysis
    artifact -- it is a timing, and timings are not reproducible, and
    constraint 4 says identical input gives identical bytes."""

    requested: int = 1
    started: int = 1
    unit_count: int = 0
    wall_seconds: float = 0.0
    serial_seconds: float = 0.0
    largest_unit_key: str = ""
    largest_unit_seconds: float = 0.0

    #: Wall clock for the WHOLE ingestion, not just the parallel section.
    #: Set by the caller once it has finished; the gain the owner feels is
    #: the one over the whole stage, and the serial parts (walking, reading,
    #: hashing, writing the cache) do not shrink with more workers.
    total_seconds: float = 0.0

    #: Set when the caller skipped the pool entirely and says why.
    skipped_reason: str = ""

    unit_seconds: dict[str, float] = field(default_factory=dict, repr=False)

    @property
    def serial_control_seconds(self) -> float:
        """What this same ingestion would have cost in one process: the CPU
        the units actually consumed, plus the serial remainder that no
        number of workers can remove.

        A control computed from data already held rather than by running the
        whole thing twice. `test_estimated_gain_matches_a_real_single_process
        _control` checks it against a genuine `--workers 1` run.
        """
        total = self.total_seconds or self.wall_seconds
        non_parallel = max(0.0, total - self.wall_seconds)
        return self.serial_seconds + non_parallel

    @property
    def gain(self) -> float:
        """Measured speed-up of the whole ingestion stage. 1.00x means the
        workers bought nothing."""
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
        if self.unit_count == 0:
            return "nothing to do -- no files needed parsing (cache was warm)"
        if self.unit_count == 1:
            return (
                "1 file holds 100% of the work; parallelism is across files "
                "and cannot split a single file's parse"
            )
        share = self.largest_unit_share
        if share >= 0.5:
            return (
                f"1 file ({self.largest_unit_key}) holds {share * 100:.0f}% of the "
                "work; parallelism is across files and cannot split a file's parse"
            )
        if self.started <= 1:
            return "running in-process; no child workers were started"
        if self.unit_count < self.started:
            return (
                f"{self.unit_count} unit(s) for {self.started} worker(s); "
                f"{self.started - self.unit_count} idled"
            )
        return (
            "per-file work is small enough that process startup and sending "
            "results back cost about as much as they save"
        )

    def recommendation(self) -> str:
        if self.helped:
            return f"--workers {self.started} is earning its overhead on this target"
        if self.unit_count <= 1 or self.largest_unit_share >= 0.5:
            return (
                "use --workers 1 for this target; more workers help when files "
                "are many and evenly sized"
            )
        return "use --workers 1 for this target; the gain here does not cover the overhead"


def render_worker_report(report: WorkerReport) -> str:
    """Four lines, in the shape the owner asked for. `parallel gain` is
    always present and is always a measurement of this run."""
    lines = [
        f"workers        {report.requested} requested, {report.started} started",
    ]
    if report.unit_count == 0:
        reason = report.skipped_reason or "no files needed parsing"
        lines.append(f"parallel gain  n/a ({reason})")
        lines.append(f"why            {report.explain_no_gain()}")
        lines.append(f"recommendation {report.recommendation()}")
        return "\n".join(lines)

    total = report.total_seconds or report.wall_seconds
    lines.append(
        f"parallel gain  {report.gain:.2f}x vs single process   "
        f"<- measured on this target ({report.unit_count} file(s), "
        f"{report.serial_control_seconds:.1f}s of work done in {total:.1f}s)"
    )
    if report.helped:
        lines.append(
            f"why            work spread over {report.started} workers; "
            f"largest single file is {report.largest_unit_share * 100:.0f}% of it"
        )
    else:
        lines.append(f"why            {report.explain_no_gain()}")
    lines.append(f"recommendation {report.recommendation()}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------


def _timed(fn: Callable[[Any], Any], job: tuple[str, Any]) -> UnitResult:
    """Times the unit with `process_time`, i.e. CPU seconds, NOT wall clock.

    This matters and is not a detail. Under a pool with more workers than
    cores, every unit's *wall* time inflates because the units are competing
    for the same cores. Summing inflated wall times and dividing by the
    section's wall clock produces a gain that RISES as the machine gets more
    oversubscribed and slower -- it reported 5.06x for a run that was
    measurably slower than the 3.02x one. The work here is CPU-bound, so CPU
    seconds are what a single process would have spent, and they do not move
    when the box is contended.
    """
    key, payload = job
    start = time.process_time()
    value = fn(payload)
    return UnitResult(key=key, value=value, seconds=time.process_time() - start)


class _Call:
    """A picklable `functools.partial`. `ProcessPoolExecutor` pickles the
    callable, and a closure is not picklable, so the bound function travels
    as a module-level class instance instead."""

    __slots__ = ("fn",)

    def __init__(self, fn: Callable[[Any], Any]) -> None:
        self.fn = fn

    def __call__(self, job: tuple[str, Any]) -> UnitResult:
        return _timed(self.fn, job)


def run_units(
    fn: Callable[[Any], Any],
    jobs: Sequence[tuple[str, Any]],
    workers: int,
    report: WorkerReport,
) -> dict[str, Any]:
    """Run every `(key, payload)` job through `fn` and return `{key: value}`.

    The caller must re-derive its own order from the keys; nothing about the
    returned mapping's construction order is allowed to reach the output.

    Falls back to in-process, recording why, whenever a pool cannot help or
    cannot start. A failed pool must never become a failed analysis: the work
    is still done, just serially, and the report says so.
    """
    report.requested = workers
    report.unit_count = len(jobs)

    if not jobs:
        report.started = 1
        report.skipped_reason = "no files needed parsing"
        return {}

    if workers <= 1 or len(jobs) == 1:
        if workers > 1 and len(jobs) == 1:
            report.skipped_reason = (
                "1 unit of work: a pool was not started because one worker "
                "would do all of it and the rest would idle"
            )
        report.started = 1
        started_at = time.perf_counter()
        out: dict[str, Any] = {}
        for job in jobs:
            r = _timed(fn, job)
            out[r.key] = r.value
            report.unit_seconds[r.key] = r.seconds
        report.wall_seconds = time.perf_counter() - started_at
        report.serial_seconds = sum(report.unit_seconds.values())
        _record_largest(report)
        return out

    effective = min(workers, len(jobs))
    started_at = time.perf_counter()
    try:
        with ProcessPoolExecutor(max_workers=effective) as pool:
            results = list(pool.map(_Call(fn), jobs, chunksize=1))
    except Exception as exc:  # pragma: no cover - platform dependent
        report.started = 1
        report.skipped_reason = f"pool unavailable ({type(exc).__name__}); ran in-process"
        report.wall_seconds = 0.0
        report.unit_seconds.clear()
        return run_units(fn, jobs, 1, report)

    report.started = effective
    out = {}
    for r in results:
        out[r.key] = r.value
        report.unit_seconds[r.key] = r.seconds
    report.wall_seconds = time.perf_counter() - started_at
    report.serial_seconds = sum(report.unit_seconds.values())
    _record_largest(report)
    return out


def _record_largest(report: WorkerReport) -> None:
    if not report.unit_seconds:
        return
    # Sorted by (-seconds, key) so ties break on the name, never on dict
    # insertion order -- the report is printed and an owner comparing two
    # runs must not see a different file named for the same timings.
    key = sorted(report.unit_seconds.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    report.largest_unit_key = key
    report.largest_unit_seconds = report.unit_seconds[key]
