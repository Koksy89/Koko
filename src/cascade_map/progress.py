"""Live progress and honest timing for runs that would otherwise print nothing.

A five-minute run that prints one worker report and then goes silent is
indistinguishable from a hung one. This module is the only thing in the tool
that writes while work is in flight, and it obeys three rules absolutely:

1. **Everything goes to stderr.** Never stdout. The summary, the artifacts and
   anything a script parses are unchanged whether progress ran or not, so
   constraint 4 -- identical input, byte-identical output -- is untouched.
2. **Carriage returns only on a TTY.** Piped into a log, `\\r` spam is worse
   than silence, so a non-TTY gets exactly one plain line per completed stage.
3. **An estimate is stated as an estimate.** The remaining time carries `~`,
   and where no stage has finished yet there is nothing to calibrate against,
   so it says `estimating` rather than inventing a countdown.

The estimate starts from measured stage weights (see `ANALYZE_STAGES`) and is
recalibrated after every stage completes: the stages already done say how fast
this machine and this target actually are, which is a far better prior than any
constant. Within a stage the fraction is real sub-progress when a stage can
report it cheaply (ingestion knows its file count, records its element count),
and otherwise a time-based guess capped below 1.0 so the bar never sits at
100% waiting.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Sequence, TextIO

__all__ = [
    "ANALYZE_STAGES",
    "NullProgress",
    "ProgressReporter",
    "RunEnvelope",
    "Stage",
    "TRACE_STAGES",
    "format_duration",
    "make_envelope",
    "make_reporter",
    "stage_timing_line",
    "utc_stamp",
]


@dataclass(frozen=True)
class Stage:
    """One pipeline stage and its share of a typical run.

    `weight` is relative, not a percentage: the reporter normalises by the sum,
    so a caller adding a stage never has to rebalance the others.
    """

    name: str
    weight: float


#: Measured stage weights, in seconds, from a real `analyze` over the owner's
#: 14.6 MB single-module target:
#:
#:     inventory 46s · resolve 69s · cascade 58s · lineage 80s · records 7s
#:     · write 68s
#:
#: `dependencies` and `findings` are not in that measurement -- it predates
#: them being timed separately -- so they carry a small measured-here prior
#: instead. They are the two cheapest stages on every corpus run, and being
#: wrong about them moves the estimate by single-digit percent, which is
#: inside what a `~` is claiming anyway.
ANALYZE_STAGES: tuple[Stage, ...] = (
    Stage("inventory", 46.0),
    Stage("resolve", 69.0),
    Stage("cascade", 58.0),
    Stage("lineage", 80.0),
    Stage("dependencies", 4.0),
    Stage("findings", 9.0),
    #: Card 13. Loading a hand-written intents file and checking it against
    #: cards 2-4 is a few thousand comparisons at most, so this is the
    #: cheapest stage here -- and it only does anything at all when the owner
    #: named an intents file.
    Stage("intents", 1.0),
    Stage("records", 7.0),
    Stage("write", 68.0),
)

#: Mode A. The child process owns most of the wall clock and cannot be asked
#: how far through it is without instrumenting the target, which mode A will
#: not do, so `harness` is deliberately one long opaque stage.
TRACE_STAGES: tuple[Stage, ...] = (
    Stage("preflight", 2.0),
    Stage("harness", 70.0),
    Stage("map", 15.0),
    Stage("narrate", 5.0),
    Stage("write", 8.0),
)


def format_duration(seconds: float) -> str:
    """`5m15s`, `46s`, `1h07m`. Short enough to sit on a redrawn line."""
    if seconds < 0:
        seconds = 0.0
    whole = int(round(seconds))
    if whole < 60:
        return f"{whole}s"
    if whole < 3600:
        return f"{whole // 60}m{whole % 60:02d}s"
    return f"{whole // 3600}h{(whole % 3600) // 60:02d}m"


def utc_stamp(epoch: float) -> str:
    """`2026-09-23 14:02:11 UTC` -- printed, never written into an artifact."""
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(epoch))


def stage_timing_line(stage_millis: dict[str, int]) -> str:
    """`inventory 46s · resolve 69s · ...`, in the order the stages ran.

    `dict` preserves insertion order and the pipeline inserts in execution
    order, so this needs no sort -- and must not have one, because alphabetical
    stage names would tell the owner nothing about where the time went.
    """
    if not stage_millis:
        return ""
    return " · ".join(
        f"{name} {format_duration(millis / 1000.0)}"
        for name, millis in stage_millis.items()
    )


class NullProgress:
    """The disabled reporter. Every method exists and does nothing.

    A null object rather than `if self._progress:` at eight call sites: a
    forgotten guard is a crash in the one configuration -- `--quiet`, or no
    terminal -- that exists precisely so nothing extra happens.
    """

    enabled = False

    def start(self, started_at: float | None = None) -> None: ...

    def complete(self, name: str) -> None: ...

    def sub(self, done: int, total: int) -> None: ...

    def note(self, text: str) -> None: ...

    def through(self, text: str, *, stream: TextIO | None = None) -> None:
        print(text, file=stream if stream is not None else sys.stdout)

    def finish(
        self,
        *,
        finished_at: float | None = None,
        elapsed: float | None = None,
        stage_millis: dict[str, int] | None = None,
    ) -> None: ...


class ProgressReporter:
    """Redraws one line on a TTY; emits one line per stage otherwise.

    The reporter owns a daemon ticker thread on a TTY so the line keeps moving
    inside a stage that reports no sub-progress -- which is the owner's actual
    complaint, since a single 14.6 MB module is *one* ingestion unit and no
    file counter can animate it. The thread does nothing but re-render a
    string and write it; it holds a lock the foreground also takes, so a stage
    boundary and a tick can never interleave mid-line.
    """

    def __init__(
        self,
        stages: Sequence[Stage],
        *,
        stream: TextIO | None = None,
        tty: bool | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        interval: float = 0.25,
        live: bool = True,
    ) -> None:
        if not stages:
            raise ValueError("a reporter needs at least one stage")
        self.enabled = True
        self._stages = tuple(stages)
        self._index = 0
        self._stream = stream if stream is not None else sys.stderr
        self._tty = self._detect_tty() if tty is None else bool(tty)
        self._clock = clock
        self._wall = wall_clock
        self._interval = interval
        self._live = live and self._tty
        self._total_weight = sum(s.weight for s in self._stages) or 1.0
        self._done_weight = 0.0
        self._t0 = 0.0
        self._stage_t0 = 0.0
        self._sub = (0, 0)
        self._durations: dict[str, float] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._painted = 0
        self._started = False

    # -- lifecycle ---------------------------------------------------------

    @staticmethod
    def _detect_tty() -> bool:
        try:
            return bool(sys.stderr.isatty())
        except Exception:  # pragma: no cover - a closed or exotic stream
            return False

    def start(self, started_at: float | None = None) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._t0 = self._clock()
            self._stage_t0 = self._t0
            stamp = utc_stamp(started_at if started_at is not None else self._wall())
            self._write(f"started   {stamp}\n")
            self._render()
        if self._live:
            self._thread = threading.Thread(
                target=self._tick, name="metatron-progress", daemon=True
            )
            self._thread.start()

    def complete(self, name: str) -> None:
        """The stage called *name* has just finished."""
        with self._lock:
            if not self._started:
                return
            now = self._clock()
            self._durations[name] = now - self._stage_t0
            current = self._stages[self._index] if self._index < len(self._stages) else None
            if current is not None and current.name == name:
                self._done_weight += current.weight
                self._index += 1
            else:
                # A stage the reporter was not told about, or one out of
                # order. It is still timed and still announced; it simply does
                # not move the bar, because a bar that jumps backwards is
                # worse than one that is briefly conservative.
                found = next(
                    (i for i, s in enumerate(self._stages) if s.name == name), None
                )
                if found is not None and found >= self._index:
                    self._done_weight = sum(
                        s.weight for s in self._stages[: found + 1]
                    )
                    self._index = found + 1
            self._stage_t0 = now
            self._sub = (0, 0)
            if self._tty:
                self._render()
            else:
                self._clear()
                self._write(self._line(done_stage=name) + "\n")

    def sub(self, done: int, total: int) -> None:
        """Real sub-progress inside the current stage, if it has any cheaply."""
        with self._lock:
            if not self._started or total <= 0:
                return
            self._sub = (max(0, min(done, total)), total)
            if self._tty:
                self._render()

    def note(self, text: str) -> None:
        """A one-off line on stderr, without disturbing the progress line."""
        with self._lock:
            self._clear()
            self._write(text.rstrip("\n") + "\n")
            if self._tty:
                self._render()

    def through(self, text: str, *, stream: TextIO | None = None) -> None:
        """Print *text* (stdout by default) with the progress line out of the way."""
        with self._lock:
            self._clear()
            print(text, file=stream if stream is not None else sys.stdout)
            if self._tty:
                self._render()

    def finish(
        self,
        *,
        finished_at: float | None = None,
        elapsed: float | None = None,
        stage_millis: dict[str, int] | None = None,
    ) -> None:
        """Stop, and print the closing timestamp, true elapsed and stage split.

        `finished_at` and `elapsed` come from the caller, not from this
        object's own clock, so the printed numbers and the ones in
        `run_meta.json` are the same numbers and cannot drift apart.
        """
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
            self._thread = None
        with self._lock:
            self._clear()
            wall = finished_at if finished_at is not None else self._wall()
            took = elapsed if elapsed is not None else (self._clock() - self._t0)
            line = stage_timing_line(stage_millis or {})
            if line:
                self._write(line + "\n")
            self._write(f"finished  {utc_stamp(wall)}   ({format_duration(took)})\n")
            self._flush()

    # -- rendering ---------------------------------------------------------

    def _tick(self) -> None:
        while not self._stop.wait(self._interval):
            with self._lock:
                if self._stop.is_set():
                    return
                self._render()

    def _render(self) -> None:
        if not self._tty:
            return
        text = self._line()
        pad = max(0, self._painted - len(text))
        self._write("\r" + text + " " * pad)
        self._painted = len(text)
        self._flush()

    def _clear(self) -> None:
        if self._tty and self._painted:
            self._write("\r" + " " * self._painted + "\r")
            self._painted = 0
            self._flush()

    def _line(self, *, done_stage: str | None = None) -> str:
        elapsed = self._clock() - self._t0
        fraction = self._stage_fraction()
        if self._index < len(self._stages):
            current = self._stages[self._index]
            weighted = self._done_weight + fraction * current.weight
            name = done_stage or current.name
            position = f"{self._index + 1}/{len(self._stages)}"
        else:
            current = None
            weighted = self._total_weight
            name = done_stage or "done"
            position = f"{len(self._stages)}/{len(self._stages)}"
        if done_stage is not None:
            # A completion line names the stage that finished, at the point
            # the bar reached when it finished.
            weighted = self._done_weight
            position = f"{self._index}/{len(self._stages)}"
        pct = int(100 * weighted / self._total_weight)
        pct = max(0, min(99 if weighted < self._total_weight else 100, pct))
        remaining = self._remaining(weighted, elapsed)
        left = f"~{format_duration(remaining)} left" if remaining is not None else "~ estimating"
        return (
            f"[ {pct:>3d}% ] {name:<14} {position:<6} ·  "
            f"{format_duration(elapsed)} elapsed  ·  {left}"
        )

    def _stage_fraction(self) -> float:
        """How far through the current stage we are, in [0, 0.95].

        Real counts win. With none, elapsed against the stage's measured
        weight scaled by how wrong the weights have been so far -- capped
        below 1.0, because a stage sitting at 100% for a minute is exactly the
        false precision this module exists to avoid.
        """
        done, total = self._sub
        if total > 0:
            return min(0.95, done / total)
        if self._index >= len(self._stages):
            return 0.0
        expected = self._stages[self._index].weight * self._scale()
        if expected <= 0:
            return 0.0
        return min(0.95, (self._clock() - self._stage_t0) / expected)

    def _scale(self) -> float:
        """Seconds per unit of weight, measured from the stages already done.

        Before anything has finished, 1.0 -- the weights are in seconds from a
        real run, so that is the honest prior rather than a neutral one.
        """
        if not self._durations or self._done_weight <= 0:
            return 1.0
        measured = sum(
            self._durations.get(s.name, 0.0) for s in self._stages[: self._index]
        )
        if measured <= 0:
            return 1.0
        return measured / self._done_weight

    def _remaining(self, weighted: float, elapsed: float) -> float | None:
        """`None` means unknown, and the line says so rather than guessing."""
        left_weight = self._total_weight - weighted
        if left_weight <= 0:
            return 0.0
        if self._durations:
            return left_weight * self._scale()
        # NOTHING has finished yet. The stage weights are seconds from one
        # machine and one target, and turning them into "6m47s left" two
        # hundred milliseconds into a run is exactly the precise-looking
        # countdown this module must not print. `~ estimating` until the first
        # stage lands and there is something real to scale by.
        return None

    # -- stream ------------------------------------------------------------

    def _write(self, text: str) -> None:
        try:
            self._stream.write(text)
        except Exception:  # pragma: no cover - a closed stream must not fail a run
            pass

    def _flush(self) -> None:
        try:
            self._stream.flush()
        except Exception:  # pragma: no cover
            pass


class RunEnvelope:
    """Timestamps only: `started` and `finished` on stderr, no live line.

    For commands whose work is a sequence of sub-runs that each draw their own
    progress (`track` analyses one version at a time). Two objects redrawing
    the same terminal line would garble each other, so the outer one does not
    draw.
    """

    enabled = True

    def __init__(
        self,
        *,
        stream: TextIO | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self._clock = clock
        self._t0: float | None = None

    def start(self, started_at: float | None = None) -> None:
        self._t0 = started_at if started_at is not None else self._clock()
        self._emit(f"started   {utc_stamp(self._t0)}")

    def note(self, text: str) -> None:
        self._emit(text.rstrip("\n"))

    def through(self, text: str, *, stream: TextIO | None = None) -> None:
        print(text, file=stream if stream is not None else sys.stdout)

    def complete(self, name: str) -> None: ...

    def sub(self, done: int, total: int) -> None: ...

    def finish(
        self,
        *,
        finished_at: float | None = None,
        elapsed: float | None = None,
        stage_millis: dict[str, int] | None = None,
    ) -> None:
        wall = finished_at if finished_at is not None else self._clock()
        took = elapsed if elapsed is not None else (wall - (self._t0 or wall))
        line = stage_timing_line(stage_millis or {})
        if line:
            self._emit(line)
        self._emit(f"finished  {utc_stamp(wall)}   ({format_duration(took)})")

    def _emit(self, text: str) -> None:
        try:
            self._stream.write(text + "\n")
            self._stream.flush()
        except Exception:  # pragma: no cover - a closed stream must not fail a run
            pass


def make_envelope(
    *,
    quiet: bool = False,
    force: bool | None = None,
    stream: TextIO | None = None,
) -> RunEnvelope | NullProgress:
    """`make_reporter`'s rules, for the commands that only want timestamps."""
    if quiet or force is False:
        return NullProgress()
    if force is None and os.environ.get("CASCADE_MAP_NO_PROGRESS"):
        return NullProgress()
    return RunEnvelope(stream=stream)


def make_reporter(
    stages: Sequence[Stage],
    *,
    quiet: bool = False,
    force: bool | None = None,
    stream: TextIO | None = None,
) -> ProgressReporter | NullProgress:
    """The one place that decides whether a run shows progress.

    `--quiet` always wins. `--progress/--no-progress` (`force`) overrides the
    TTY test either way. `CASCADE_MAP_NO_PROGRESS` is honoured for the same
    reason `NO_COLOR` exists: a CI job should be able to turn it off without
    every call site growing a flag.
    """
    if quiet or force is False:
        return NullProgress()
    if force is None and os.environ.get("CASCADE_MAP_NO_PROGRESS"):
        return NullProgress()
    target = stream if stream is not None else sys.stderr
    # Forced on, a pipe is still a pipe: `--progress` must not start emitting
    # carriage returns into the owner's log file. The flag decides WHETHER,
    # the TTY test decides HOW.
    return ProgressReporter(stages, stream=target, tty=_isatty(target))


def _isatty(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except Exception:  # pragma: no cover
        return False
