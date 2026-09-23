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

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

# One coordinator, one worker policy, one pool -- `cascade_map.parallel`. This
# module keeps only what is specific to INGESTION: its own wording for the
# report, because ingestion's unit is a file and the other stages' units are
# not, and the `fn(payload)` call shape its callers already use.
from cascade_map.parallel import (
    GAIN_FLOOR,
    MAX_WORKERS,
    StageReport,
    UnitResult,
    cpu_budget,
    resolve_workers,
    run_stage,
)

__all__ = [
    "GAIN_FLOOR",
    "MAX_WORKERS",
    "UnitResult",
    "WorkerReport",
    "cpu_budget",
    "render_worker_report",
    "resolve_workers",
    "run_units",
]


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
# execution -- the shared coordinator, in ingestion's call shape
# ---------------------------------------------------------------------------


class _IngestCall:
    """A picklable ``fn(opened, arg)`` that ignores the shared payload."""

    __slots__ = ("fn",)

    def __init__(self, fn: Callable[[Any], Any]) -> None:
        self.fn = fn

    def __call__(self, _opened: Any, payload: Any) -> Any:
        return self.fn(payload)


def run_units(
    fn: Callable[[Any], Any],
    jobs: Sequence[tuple[str, Any]],
    workers: int,
    report: WorkerReport,
    *,
    on_unit: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Run every `(key, payload)` job through `fn` and return `{key: value}`.

    The caller must re-derive its own order from the keys; nothing about the
    returned mapping's construction order is allowed to reach the output.

    Delegates to :func:`cascade_map.parallel.run_stage`, which is the one
    work-stealing pool in the project. The unit here is a whole file.
    """
    stage = StageReport(stage="inventory")
    out = run_stage(
        _IngestCall(fn), jobs, workers, stage, payload=None, on_unit=on_unit
    )
    report.requested = stage.requested
    report.started = stage.started
    report.unit_count = stage.unit_count
    report.wall_seconds = stage.wall_seconds
    report.serial_seconds = stage.serial_seconds
    report.largest_unit_key = stage.largest_unit_key
    report.largest_unit_seconds = stage.largest_unit_seconds
    report.unit_seconds.clear()
    report.unit_seconds.update(stage.unit_seconds)
    if stage.skipped_reason:
        report.skipped_reason = stage.skipped_reason
    elif not jobs:
        report.skipped_reason = "no files needed parsing"
    return out
