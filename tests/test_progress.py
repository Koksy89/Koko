"""Live progress, timing and the promise that neither can touch the output.

The feature exists because a five-minute run printed a worker report and then
nothing, which is indistinguishable from a hang. The tests that matter are not
"does it print a bar" -- they are the three it must never break:

* stdout is byte-identical with progress on and off;
* the artifacts are byte-identical with progress on and off;
* a non-TTY stream never sees a carriage return.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cascade_map.cli import analyze, main as cli_main  # noqa: E402
from cascade_map.progress import (  # noqa: E402
    ANALYZE_STAGES,
    NullProgress,
    ProgressReporter,
    RunEnvelope,
    Stage,
    format_duration,
    make_envelope,
    make_reporter,
    stage_timing_line,
    utc_stamp,
)

CORPUS = ROOT / "tests" / "fixtures" / "mode_b"

#: The artifacts that carry the byte-identical guarantee. `run_meta.json` is
#: deliberately outside it -- it is where the wall clock legitimately lives.
DETERMINISTIC = (
    "elements.jsonl",
    "edges.jsonl",
    "unresolved.jsonl",
    "lineage.jsonl",
    "findings.jsonl",
    "records.jsonl",
    "manifest.json",
)


class _Clock:
    """A fake monotonic clock. Real sleeps would make these tests slow and
    flaky, and every number below is an assertion about arithmetic, not about
    how fast this machine is."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _reporter(stream: io.StringIO, clock: _Clock, *, tty: bool) -> ProgressReporter:
    return ProgressReporter(
        ANALYZE_STAGES,
        stream=stream,
        tty=tty,
        clock=clock,
        wall_clock=lambda: 1_758_636_131.0,
        live=False,
    )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "seconds,text",
    [(0, "0s"), (0.4, "0s"), (46, "46s"), (59.6, "1m00s"), (75, "1m15s"),
     (315, "5m15s"), (3600, "1h00m"), (4051, "1h07m")],
)
def test_format_duration(seconds: float, text: str) -> None:
    assert format_duration(seconds) == text


def test_utc_stamp_is_utc_and_says_so() -> None:
    assert utc_stamp(1_758_636_131.0) == "2025-09-23 14:02:11 UTC"


def test_stage_timing_line_keeps_execution_order_not_alphabetical() -> None:
    line = stage_timing_line(
        {"inventory": 46_000, "resolve": 69_000, "cascade": 58_000,
         "lineage": 80_000, "records": 7_000, "write": 68_000}
    )
    assert line == (
        "inventory 46s · resolve 1m09s · cascade 58s · lineage 1m20s · "
        "records 7s · write 1m08s"
    )
    assert line.index("inventory") < line.index("cascade")


# ---------------------------------------------------------------------------
# The TTY line
# ---------------------------------------------------------------------------


def test_tty_line_redraws_in_place_and_names_stage_position_and_estimate() -> None:
    stream, clock = io.StringIO(), _Clock()
    bar = _reporter(stream, clock, tty=True)
    bar.start(1_758_636_131.0)
    for name, seconds in (("inventory", 46), ("resolve", 69), ("cascade", 58)):
        clock.advance(seconds)
        bar.complete(name)
    clock.advance(30)
    bar.sub(1, 2)
    text = stream.getvalue()
    assert text.startswith("started   2025-09-23 14:02:11 UTC\n")
    assert "\r" in text
    last = text.split("\r")[-1]
    assert "lineage" in last
    assert "4/8" in last
    assert "elapsed" in last and "left" in last
    assert "~" in last
    # 46+69+58+30 = 203s in, and the bar must be somewhere sensible, never
    # at 0% and never parked at 100% while work continues.
    percent = int(last.split("%")[0].strip(" [").strip())
    assert 40 <= percent <= 70, last


def test_non_tty_emits_one_plain_line_per_stage_and_no_carriage_returns() -> None:
    stream, clock = io.StringIO(), _Clock()
    bar = _reporter(stream, clock, tty=False)
    bar.start(1_758_636_131.0)
    for stage in ANALYZE_STAGES:
        clock.advance(stage.weight)
        bar.complete(stage.name)
    bar.finish(finished_at=1_758_636_446.0, elapsed=341.0,
               stage_millis={"inventory": 46_000})
    text = stream.getvalue()
    assert "\r" not in text, "a log full of carriage returns is worse than silence"
    lines = [line for line in text.splitlines() if line.startswith("[")]
    assert len(lines) == len(ANALYZE_STAGES)
    assert [stage.name for stage in ANALYZE_STAGES] == [
        line.split("]")[1].split()[0] for line in lines
    ]
    assert lines[-1].startswith("[ 100%")
    assert text.rstrip().endswith("finished  2025-09-23 14:07:26 UTC   (5m41s)")


def test_the_estimate_says_estimating_before_anything_has_completed() -> None:
    """Never a precise-looking countdown that nothing supports."""
    stream, clock = io.StringIO(), _Clock()
    bar = _reporter(stream, clock, tty=True)
    bar.start(1_758_636_131.0)
    assert "~ estimating" in stream.getvalue().split("\r")[-1]


def test_the_estimate_recalibrates_from_the_stages_already_measured() -> None:
    """A machine twice as slow as the prior must be estimated as twice as slow."""
    stream, clock = io.StringIO(), _Clock()
    bar = _reporter(stream, clock, tty=True)
    bar.start(1_758_636_131.0)
    total = sum(stage.weight for stage in ANALYZE_STAGES)
    for stage in ANALYZE_STAGES[:4]:
        clock.advance(stage.weight * 2)  # everything takes twice the prior
        bar.complete(stage.name)
    done = sum(stage.weight for stage in ANALYZE_STAGES[:4])
    expected = (total - done) * 2
    line = stream.getvalue().split("\r")[-1]
    shown = line.split("~")[1].split(" left")[0]
    assert shown == format_duration(expected), line


def test_a_stage_with_no_sub_progress_still_moves_the_line() -> None:
    """The owner's complaint in one test: one 14.6 MB module is ONE ingestion
    unit, so no file counter can animate it. Elapsed time must."""
    stream, clock = io.StringIO(), _Clock()
    bar = _reporter(stream, clock, tty=True)
    bar.start(1_758_636_131.0)
    first = stream.getvalue().split("\r")[-1]
    clock.advance(20)
    bar.sub(0, 0)          # nothing real to report
    bar._render()          # what the ticker thread does, without the thread
    second = stream.getvalue().split("\r")[-1]
    assert first != second
    assert int(second.split("%")[0].strip(" [")) > int(first.split("%")[0].strip(" ["))


def test_sub_progress_wins_over_the_time_guess_when_a_stage_has_real_counts() -> None:
    stream, clock = io.StringIO(), _Clock()
    bar = _reporter(stream, clock, tty=True)
    bar.start(1_758_636_131.0)
    bar.sub(9, 10)
    line = stream.getvalue().split("\r")[-1]
    inventory = ANALYZE_STAGES[0].weight
    total = sum(stage.weight for stage in ANALYZE_STAGES)
    # 0.9 of inventory, capped at 0.95 -> a definite, checkable percentage.
    assert int(line.split("%")[0].strip(" [")) == int(100 * 0.9 * inventory / total)


def test_a_finished_run_prints_the_per_stage_split_and_the_true_elapsed() -> None:
    stream, clock = io.StringIO(), _Clock()
    bar = _reporter(stream, clock, tty=True)
    bar.start(1_758_636_131.0)
    bar.finish(
        finished_at=1_758_636_446.0,
        elapsed=315.0,
        stage_millis={"inventory": 46_000, "write": 68_000},
    )
    text = stream.getvalue()
    assert "inventory 46s · write 1m08s" in text
    assert "finished  2025-09-23 14:07:26 UTC   (5m15s)" in text
    assert text.rstrip().endswith("(5m15s)")


def test_the_progress_line_is_cleared_before_anything_else_is_printed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    stream, clock = io.StringIO(), _Clock()
    bar = _reporter(stream, clock, tty=True)
    bar.start(1_758_636_131.0)
    bar.through("the worker report")
    assert capsys.readouterr().out == "the worker report\n"
    # ...and the clearing happened on stderr, not by overwriting stdout.
    assert "\r" in stream.getvalue()


def test_a_stage_the_reporter_was_not_told_about_never_moves_the_bar_backwards() -> None:
    stream, clock = io.StringIO(), _Clock()
    bar = _reporter(stream, clock, tty=False)
    bar.start(1_758_636_131.0)
    clock.advance(46)
    bar.complete("inventory")
    clock.advance(5)
    bar.complete("a stage from the future")
    percents = [
        int(line.split("%")[0].strip(" ["))
        for line in stream.getvalue().splitlines()
        if line.startswith("[")
    ]
    assert percents == sorted(percents)


# ---------------------------------------------------------------------------
# Choosing a reporter
# ---------------------------------------------------------------------------


def test_quiet_and_no_progress_both_give_the_null_reporter() -> None:
    assert isinstance(make_reporter(ANALYZE_STAGES, quiet=True), NullProgress)
    assert isinstance(make_reporter(ANALYZE_STAGES, force=False), NullProgress)
    assert isinstance(make_envelope(quiet=True), NullProgress)
    assert isinstance(make_envelope(force=False), NullProgress)


def test_quiet_beats_an_explicit_progress_flag() -> None:
    assert isinstance(
        make_reporter(ANALYZE_STAGES, quiet=True, force=True), NullProgress
    )


def test_forcing_progress_on_a_pipe_does_not_start_emitting_carriage_returns() -> None:
    stream = io.StringIO()  # a StringIO is not a TTY
    bar = make_reporter(ANALYZE_STAGES, force=True, stream=stream)
    assert isinstance(bar, ProgressReporter)
    bar.start(1_758_636_131.0)
    bar.complete("inventory")
    bar.finish(finished_at=1_758_636_446.0, elapsed=315.0)
    assert "\r" not in stream.getvalue()
    assert "[" in stream.getvalue()


def test_the_null_reporter_writes_nothing_and_still_prints_through(
    capsys: pytest.CaptureFixture[str],
) -> None:
    bar = NullProgress()
    bar.start(1.0)
    bar.complete("inventory")
    bar.sub(1, 2)
    bar.note("ignored")
    bar.finish(finished_at=2.0, elapsed=1.0, stage_millis={"inventory": 1})
    bar.through("this still reaches stdout")
    captured = capsys.readouterr()
    assert captured.out == "this still reaches stdout\n"
    assert captured.err == ""


def test_an_env_var_can_turn_progress_off_without_a_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CASCADE_MAP_NO_PROGRESS", "1")
    assert isinstance(make_reporter(ANALYZE_STAGES), NullProgress)
    # ...and an explicit --progress still wins over it.
    assert isinstance(
        make_reporter(ANALYZE_STAGES, force=True, stream=io.StringIO()),
        ProgressReporter,
    )


def test_a_reporter_with_no_stages_refuses_rather_than_dividing_by_zero() -> None:
    with pytest.raises(ValueError):
        ProgressReporter(())


def test_the_envelope_prints_only_timestamps() -> None:
    stream = io.StringIO()
    env = RunEnvelope(stream=stream, clock=lambda: 1_758_636_131.0)
    env.start()
    env.note("analysing v3")
    env.finish(finished_at=1_758_636_446.0, elapsed=315.0)
    text = stream.getvalue()
    assert text.splitlines() == [
        "started   2025-09-23 14:02:11 UTC",
        "analysing v3",
        "finished  2025-09-23 14:07:26 UTC   (5m15s)",
    ]
    assert "\r" not in text


def test_the_live_ticker_thread_redraws_without_being_asked() -> None:
    """The anti-freeze guarantee itself: nothing calls the reporter for half a
    second and the line still moves."""
    stream = io.StringIO()
    bar = ProgressReporter(
        ANALYZE_STAGES, stream=stream, tty=True, interval=0.01, live=True
    )
    bar.start()
    time.sleep(0.15)
    bar.finish(finished_at=1_758_636_446.0, elapsed=315.0)
    assert stream.getvalue().count("\r") > 3


# ---------------------------------------------------------------------------
# The three promises
# ---------------------------------------------------------------------------


def _stable_stdout(text: str) -> str:
    """Everything a script parses, with the worker report's timings dropped.

    The worker report is wall-clock BY DESIGN -- `cli.py` prints it and never
    writes it into an artifact for exactly that reason -- so it is the one
    part of stdout that two identical runs may legitimately differ on. The
    summary below it is the deterministic part, and it is what these tests
    compare exactly.
    """
    marker = text.find("Wrote ")
    assert marker >= 0, text
    return text[marker:]


def _artifacts(out: Path) -> dict[str, bytes]:
    return {name: (out / name).read_bytes() for name in DETERMINISTIC}


def test_progress_changes_neither_stdout_nor_the_artifacts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole command, both ways round, through `main`.

    A cache dir each so both runs do the same work, and the summary compared
    exactly: it is the part a script reads.
    """
    quiet_out, loud_out = tmp_path / "quiet", tmp_path / "loud"
    assert cli_main([
        "analyze", str(CORPUS), "--out", str(quiet_out), "--no-gate",
        "--cache", str(tmp_path / "cq"), "--quiet",
    ]) == 0
    quiet = capsys.readouterr()
    assert cli_main([
        "analyze", str(CORPUS), "--out", str(loud_out), "--no-gate",
        "--cache", str(tmp_path / "cl"), "--progress",
    ]) == 0
    loud = capsys.readouterr()
    assert _stable_stdout(loud.out) == _stable_stdout(quiet.out).replace(
        str(quiet_out), str(loud_out)
    )
    assert _artifacts(loud_out) == _artifacts(quiet_out)
    assert quiet.err == ""
    assert "started " in loud.err and "finished " in loud.err
    assert "\r" not in loud.err


def test_two_consecutive_runs_with_progress_are_byte_identical(tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    for out in (first, second):
        analyze(
            CORPUS,
            out,
            strict_gate=False,
            progress=make_reporter(ANALYZE_STAGES, force=True, stream=io.StringIO()),
        )
    assert _artifacts(first) == _artifacts(second)


def test_the_printed_footer_and_run_meta_cannot_disagree(tmp_path: Path) -> None:
    """One reading of the clock, used by both -- so the owner's terminal and
    their artifact always report the same run."""
    out = tmp_path / "graph"
    stream = io.StringIO()
    code, summary = analyze(
        CORPUS,
        out,
        strict_gate=False,
        progress=make_reporter(ANALYZE_STAGES, force=True, stream=stream),
    )
    meta = json.loads((out / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["elapsed_seconds"] == summary["elapsed_seconds"]
    assert meta["finished_at"] == time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(summary["finished_at"])
    )
    footer = stream.getvalue().strip().splitlines()[-1]
    assert footer.startswith("finished  " + utc_stamp(summary["finished_at"]))
    assert footer.endswith(f"({format_duration(summary['elapsed_seconds'])})")


def test_every_stage_is_timed_and_named_in_the_footer(tmp_path: Path) -> None:
    stream = io.StringIO()
    _, summary = analyze(
        CORPUS,
        tmp_path / "graph",
        strict_gate=False,
        progress=make_reporter(ANALYZE_STAGES, force=True, stream=stream),
    )
    assert set(summary["stage_millis"]) == {stage.name for stage in ANALYZE_STAGES}
    footer = stream.getvalue()
    for stage in ANALYZE_STAGES:
        assert f"{stage.name} " in footer


# ---------------------------------------------------------------------------
# Sub-progress, where a stage has it cheaply
# ---------------------------------------------------------------------------


def test_ingestion_reports_one_tick_per_parsed_file_and_no_more(
    tmp_path: Path,
) -> None:
    """One call per FILE, never per AST node: the counter must not show up in
    the stage it is measuring."""
    from cascade_map.ingest.inventory import Ingestor

    seen: list[tuple[int, int]] = []
    # A cold cache of its own: a warm cache parses nothing, and a test that
    # passes because there was no work is testing nothing.
    ingestor = Ingestor(cache_dir=tmp_path / "cache", workers=1)
    elements, _ = ingestor.inventory(str(CORPUS), on_unit=lambda d, t: seen.append((d, t)))
    assert elements
    assert seen, "a multi-file target must be able to report real sub-progress"
    assert [done for done, _ in seen] == list(range(1, len(seen) + 1))
    assert {total for _, total in seen} == {len(seen)}


def test_documentation_records_report_at_most_ten_times(tmp_path: Path) -> None:
    from cascade_map.docrecords import _PROGRESS_STEPS, DocumentationBuilder
    from cascade_map.contracts.interfaces import Element
    from cascade_map.cli import _read_jsonl

    out = tmp_path / "graph"
    analyze(CORPUS, out, strict_gate=False)
    elements = _read_jsonl(out, "elements.jsonl", Element)
    seen: list[tuple[int, int]] = []
    builder = DocumentationBuilder(elements=elements)
    records = builder.records(on_progress=lambda d, t: seen.append((d, t)))
    assert len(records) == len(elements)
    assert 0 < len(seen) <= _PROGRESS_STEPS + 1
    assert seen[-1] == (len(elements), len(elements))
    # ...and the callback cannot change the answer.
    assert tuple(records) == tuple(builder.records())


# ---------------------------------------------------------------------------
# End to end, through a real pipe
# ---------------------------------------------------------------------------


def _run(args: list[str], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONHASHSEED": "0",
    }
    return subprocess.run(
        [sys.executable, "-m", "cascade_map.cli", *args],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=900,
    )


def test_a_piped_run_gets_no_carriage_returns_and_stdout_is_unchanged(
    tmp_path: Path,
) -> None:
    """Captured from a real subprocess, where stderr genuinely is not a TTY."""
    # A cache dir each, so both runs are cold and the worker report -- which
    # IS on stdout -- describes the same work in both.
    loud = _run(["analyze", str(CORPUS), "--out", "loud", "--no-gate", "--progress",
                 "--cache", "cache-loud"], tmp_path)
    quiet = _run(["analyze", str(CORPUS), "--out", "quiet", "--no-gate", "--quiet",
                  "--cache", "cache-quiet"], tmp_path)
    assert loud.returncode == 0, loud.stderr
    assert quiet.returncode == 0, quiet.stderr
    assert "\r" not in loud.stderr
    assert _stable_stdout(loud.stdout).replace("Wrote loud", "Wrote <out>") == (
        _stable_stdout(quiet.stdout).replace("Wrote quiet", "Wrote <out>")
    )
    assert quiet.stderr == ""
    assert "started " in loud.stderr and "finished " in loud.stderr
    assert _artifacts(tmp_path / "loud") == _artifacts(tmp_path / "quiet")


def test_progress_is_on_by_default_and_off_with_no_progress(tmp_path: Path) -> None:
    default = _run(["analyze", str(CORPUS), "--out", "d", "--no-gate",
                    "--cache", "cache-d"], tmp_path)
    off = _run(["analyze", str(CORPUS), "--out", "o", "--no-gate", "--no-progress",
                "--cache", "cache-o"], tmp_path)
    assert "finished " in default.stderr
    assert off.stderr == ""
    assert _stable_stdout(default.stdout).replace("Wrote d", "Wrote <o>") == (
        _stable_stdout(off.stdout).replace("Wrote o", "Wrote <o>")
    )


def test_the_progress_flags_exist_on_every_long_command() -> None:
    from cascade_map.cli import _build_parser

    parser = _build_parser()
    for command, required in (
        (["analyze", "."], ()),
        (["track"], ()),
        (["trace", "."], ()),
    ):
        args = parser.parse_args([*command, "--quiet"])
        assert args.quiet is True
        assert parser.parse_args([*command, "--no-progress"]).progress is False
        assert parser.parse_args([*command, "--progress"]).progress is True
        assert parser.parse_args(command).progress is None


def test_analyze_accepts_a_reporter_and_still_returns_the_gate_code(
    tmp_path: Path,
) -> None:
    """Progress must never change an exit code."""
    stream = io.StringIO()
    with_bar, _ = analyze(
        CORPUS, tmp_path / "a", strict_gate=True,
        progress=make_reporter(ANALYZE_STAGES, force=True, stream=stream),
    )
    without, _ = analyze(CORPUS, tmp_path / "b", strict_gate=True)
    assert with_bar == without


def test_cli_main_does_not_crash_when_a_reporter_is_active(tmp_path: Path) -> None:
    assert cli_main(
        ["analyze", str(CORPUS), "--out", str(tmp_path / "g"), "--no-gate", "--quiet"]
    ) == 0
    assert (tmp_path / "g" / "elements.jsonl").is_file()


def test_custom_stage_sets_normalise_their_own_weights() -> None:
    stream, clock = io.StringIO(), _Clock()
    bar = ProgressReporter(
        (Stage("one", 3.0), Stage("two", 1.0)),
        stream=stream, tty=False, clock=clock, live=False,
    )
    bar.start(1_758_636_131.0)
    clock.advance(3)
    bar.complete("one")
    line = [l for l in stream.getvalue().splitlines() if l.startswith("[")][0]
    assert line.startswith("[  75%")
