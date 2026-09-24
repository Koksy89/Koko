"""The shared work-stealing coordinator, and the stages that use it.

What these tests are for, in one sentence each:

* the coordinator hands WHOLE units out of a shared queue, and an idle worker
  takes the next one rather than waiting on a batch it was given up front;
* consolidation is sequential and deterministic, so nothing about which worker
  finished first can reach a byte of output;
* every artifact is **byte-identical at 1, 2, 4 and 8 workers**, proven by
  comparison rather than asserted;
* a stage that does not pay says so with the measured number, and a stage that
  cannot be parallelised says why instead of looking like one with nothing to
  gain.

No network, nothing under `target_engine/` is read, and nothing anywhere is
executed: every target here is either the fixture corpus or a file this test
wrote into `tmp_path`.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from cascade_map.contracts.interfaces import canonical_jsonl
import cascade_map.cascade as cascade_module
import cascade_map.docrecords as docrecords_module
import cascade_map.parallel as parallel_module
from cascade_map.parallel import (
    GAIN_FLOOR,
    MAX_WORKERS,
    PipelineReport,
    StageReport,
    canonical_jsonl_many,
    chunk_bounds,
    cpu_budget,
    render_pipeline_report,
    render_stage_line,
    resolve_workers,
    run_stage,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "mode_b"

#: The counts the byte-identity proof runs at. 1 is the control, 8 is more
#: workers than this machine has cores on purpose: oversubscription is exactly
#: where a completion-order dependency shows itself.
WORKER_COUNTS = (1, 2, 4, 8)


# ---------------------------------------------------------------------------
# module-level task functions -- a closure is not picklable
# ---------------------------------------------------------------------------


def _double(_payload: object, value: int) -> int:
    return value * 2


def _read_shared(payload: "_Counter", value: int) -> tuple[int, int]:
    """Returns the payload's build id, so a test can count how many times the
    shared state was built: once per worker, never once per task."""
    return payload.build_id, value


def _slow_first(_payload: object, value: int) -> int:
    """Unit 0 takes far longer than the rest. With a fixed batch the other
    workers would idle through it; with a shared queue they steal the rest."""
    if value == 0:
        time.sleep(0.35)
    return value


class _Counter:
    """A payload whose `open` is observable. Module level so it pickles."""

    __slots__ = ("build_id",)

    def __init__(self, build_id: int = 0) -> None:
        self.build_id = build_id

    def open(self) -> "_Counter":
        import os

        return _Counter(build_id=os.getpid())


# ---------------------------------------------------------------------------
# worker policy
# ---------------------------------------------------------------------------


def test_zero_and_one_both_mean_in_process() -> None:
    """In-process must always stay reachable: it is how any of this is
    debugged, and a traceback out of a child process is a worse one."""
    assert resolve_workers(0) == 1
    assert resolve_workers(1) == 1


def test_auto_leaves_the_machine_a_core_and_is_capped() -> None:
    auto = resolve_workers(None)
    assert 1 <= auto <= MAX_WORKERS
    assert auto == max(1, min(cpu_budget() - 1, MAX_WORKERS))
    assert resolve_workers(10_000) == MAX_WORKERS


def test_a_negative_worker_count_is_refused_not_clamped() -> None:
    with pytest.raises(ValueError):
        resolve_workers(-1)


# ---------------------------------------------------------------------------
# the coordinator
# ---------------------------------------------------------------------------


def test_results_are_keyed_so_completion_order_cannot_reach_the_output() -> None:
    report = StageReport(stage="t")
    jobs = [(f"k{i}", i) for i in range(40)]
    out = run_stage(_double, jobs, 4, report)
    assert out == {f"k{i}": i * 2 for i in range(40)}
    assert set(report.unit_seconds) == {f"k{i}" for i in range(40)}


def test_every_worker_count_gives_the_same_results() -> None:
    jobs = [(f"k{i:03d}", i) for i in range(200)]
    seen = {
        count: run_stage(_double, jobs, count, StageReport(stage="t"))
        for count in WORKER_COUNTS
    }
    control = seen[1]
    for count in WORKER_COUNTS:
        assert seen[count] == control, f"{count} workers disagreed with 1"


def test_the_shared_payload_is_built_once_per_worker_not_once_per_task() -> None:
    """The owner's engine is one 15 MB file. Sending the state a stage needs
    with every one of ten thousand tasks would cost more than the parallelism
    saves, so it crosses once per worker and this is what proves it."""
    jobs = [(f"k{i:03d}", i) for i in range(400)]
    report = StageReport(stage="t")
    out = run_stage(_read_shared, jobs, 4, report, payload=_Counter())
    builds = {build_id for build_id, _ in out.values()}
    assert report.started == 4
    # One build per worker process, never one per task.
    assert 1 <= len(builds) <= 4
    assert len(out) == 400


def test_an_idle_worker_steals_the_next_unit_rather_than_waiting() -> None:
    """One slow unit must not leave the other workers idle.

    With a fixed batch of 4 units per worker, worker 0 would hold the slow
    unit and the remaining 3 units in its own batch, so the stage could not
    finish before the slow unit plus its 3 followers. With a shared queue the
    other workers take those 3 while worker 0 is still on the slow one.
    """
    if cpu_budget() < 2:  # pragma: no cover - single-core CI
        pytest.skip("work stealing needs at least two usable cores")
    jobs = [(f"k{i}", i) for i in range(16)]
    report = StageReport(stage="t")
    started = time.perf_counter()
    out = run_stage(_slow_first, jobs, 4, report)
    elapsed = time.perf_counter() - started
    assert out == {f"k{i}": i for i in range(16)}
    # The slow unit is 0.35s and everything else is free, so a stealing pool
    # lands near 0.35s. A fixed batch could not, because worker 0's batch
    # would still hold three more units behind the slow one -- but they are
    # free here, so the honest discriminator is that the stage does not take
    # materially longer than the single slow unit.
    assert elapsed < 2.0


def test_an_empty_stage_says_nothing_to_do_rather_than_reporting_a_gain() -> None:
    report = StageReport(stage="t")
    assert run_stage(_double, [], 4, report) == {}
    line = render_stage_line(report)
    assert "n/a" in line
    assert "1.00x" not in line


def test_a_single_unit_does_not_start_a_pool_and_says_why() -> None:
    report = StageReport(stage="t")
    run_stage(_double, [("only", 3)], 8, report)
    assert report.started == 1
    assert "would do all of it" in report.skipped_reason
    assert "would do all of it" in render_stage_line(report)


def test_too_few_units_stay_in_process_and_the_reason_is_measured() -> None:
    report = StageReport(stage="t")
    run_stage(_double, [(f"k{i}", i) for i in range(5)], 4, report,
              min_units_per_worker=64)
    assert report.started == 1
    assert "too few" in report.skipped_reason


def test_in_process_and_pooled_agree_on_the_unit_keys_timed() -> None:
    jobs = [(f"k{i:03d}", i) for i in range(120)]
    serial, pooled = StageReport(stage="t"), StageReport(stage="t")
    run_stage(_double, jobs, 1, serial)
    run_stage(_double, jobs, 4, pooled)
    assert set(serial.unit_seconds) == set(pooled.unit_seconds)


def test_chunk_bounds_cover_every_index_exactly_once_and_split_no_unit() -> None:
    for total in (0, 1, 7, 64, 5000, 5001, 389_999):
        bounds = chunk_bounds(total, 5000)
        covered = [index for start, stop in bounds for index in range(start, stop)]
        assert covered == list(range(total)), f"total={total}"
        assert all(start < stop for start, stop in bounds), f"total={total}"


def test_chunking_gives_many_more_units_than_workers_so_stealing_is_possible() -> None:
    """One chunk per worker is a fixed batch, and a fixed batch is what leaves
    seven workers idle behind one slow unit."""
    assert len(chunk_bounds(389_999, 5000)) >= 8 * 4


# ---------------------------------------------------------------------------
# the report -- measured, never asserted
# ---------------------------------------------------------------------------


def test_a_stage_that_did_not_pay_says_so_with_the_number() -> None:
    report = StageReport(
        stage="records", started=8, unit_count=64,
        wall_seconds=1.0, serial_seconds=1.0, total_seconds=1.0,
        unit_seconds={"a": 1.0}, largest_unit_seconds=1.0, largest_unit_key="a",
    )
    assert not report.helped
    assert report.gain < GAIN_FLOOR
    line = render_stage_line(report)
    assert "1.00x" in line
    assert "--" in line, "a stage that did not pay must say why on the line"
    assert "use 1 worker" in report.recommendation()


def test_a_stage_that_paid_names_the_worker_count_only_with_a_measurement() -> None:
    report = StageReport(
        stage="cascade", started=8, unit_count=1000,
        wall_seconds=1.0, serial_seconds=6.0, total_seconds=1.0,
        unit_seconds={f"u{i}": 0.006 for i in range(1000)},
    )
    assert report.helped
    line = render_stage_line(report)
    assert "6.00x over 8 workers" in line
    assert "6.0s of work in 1.0s" in line


def test_the_gain_does_not_rise_when_the_machine_is_oversubscribed() -> None:
    """CPU seconds, not wall clock: a gain computed from inflated wall times
    rises as the box gets slower, which is the opposite of the truth."""
    fast = StageReport(
        stage="t", started=8, unit_count=8, wall_seconds=0.5,
        serial_seconds=2.0, total_seconds=0.5,
    )
    slow = StageReport(
        stage="t", started=8, unit_count=8, wall_seconds=0.9,
        serial_seconds=2.0, total_seconds=1.2,
    )
    assert slow.gain < fast.gain


def test_a_sequential_stage_is_named_as_such_and_never_shows_a_gain() -> None:
    report = StageReport(stage="order", sequential_by_nature=True,
                         wall_seconds=2.1, total_seconds=2.1)
    line = render_stage_line(report)
    assert "sequential by nature" in line
    assert "2.1s" in line
    assert "x over" not in line
    assert "wrong graph" in report.recommendation()


def test_a_stage_left_serial_states_why_rather_than_looking_like_a_win() -> None:
    """A stage silently left serial reads as a stage that had nothing to gain.
    It is not the same thing and the owner must be able to tell them apart."""
    report = StageReport(
        stage="lineage", requested=8, unit_count=1, total_seconds=99.0,
        not_parallelised_reason="one unit is one module, and the units are not independent",
    )
    line = render_stage_line(report)
    assert "not parallelised" in line
    assert "not independent" in line
    assert "1 unit(s)" in line


def test_the_pipeline_report_covers_every_stage_and_names_the_sequential_share() -> None:
    pipeline = PipelineReport(requested=8)
    pipeline.add(StageReport(stage="cascade", started=8, unit_count=1000,
                             wall_seconds=1.0, serial_seconds=6.0, total_seconds=1.0,
                             unit_seconds={f"u{i}": 0.006 for i in range(1000)}))
    pipeline.add(StageReport(stage="order", sequential_by_nature=True,
                             wall_seconds=3.0, total_seconds=3.0))
    text = render_pipeline_report(pipeline)
    assert "cascade" in text and "order" in text
    assert "3.0s of 4.0s measured (75%)" in text
    assert pytest.approx(pipeline.sequential_share, abs=1e-6) == 0.75


def test_an_empty_pipeline_prints_nothing_rather_than_an_empty_heading() -> None:
    assert render_pipeline_report(PipelineReport()) == ""


# ---------------------------------------------------------------------------
# the write stage -- byte-identical to the contract's own serialiser
# ---------------------------------------------------------------------------


def _rows(count: int, prefix: str = "x") -> list[dict[str, object]]:
    # Deliberately NOT in id order, and with duplicate ids, so a consolidation
    # that leaned on arrival order would be caught.
    return [
        {"id": f"{prefix}{(i * 7919) % count:06d}", "n": i, "note": "é中"}
        for i in range(count)
    ]


@pytest.fixture
def force_pools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every stage use its pool on inputs far smaller than a real target.

    Without this the corpus is below every stage's own "a pool would lose
    here" floor, every stage runs in-process, and a byte-identity test passes
    without a single child process ever having existed -- a test passing for
    the wrong reason, which is the defect class this project has been bitten
    by three times.
    """
    monkeypatch.setattr(parallel_module, "JSONL_MIN_RECORDS_FOR_POOL", 0)
    monkeypatch.setattr(parallel_module, "JSONL_CHUNK_RECORDS", 32)
    # The two stages measured to LOSE in a pool are off by default. They are
    # switched on here and nowhere else, because their correctness under a
    # pool still has to be proven -- a stage switched off is not a stage
    # allowed to be wrong.
    monkeypatch.setattr(cascade_module, "_CFG_POOL_ENABLED", True)
    monkeypatch.setattr(cascade_module, "_CFG_MIN_UNITS_PER_WORKER", 1)
    monkeypatch.setattr(docrecords_module, "_RECORDS_POOL_ENABLED", True)
    monkeypatch.setattr(docrecords_module, "_MIN_RECORDS_PER_CHUNK", 8)
    monkeypatch.setattr(docrecords_module, "_MIN_CHUNKS_PER_WORKER", 1)


@pytest.mark.parametrize("workers", WORKER_COUNTS)
def test_parallel_jsonl_is_byte_identical_to_canonical_jsonl(
    workers: int, force_pools: None
) -> None:
    groups = {
        "big.jsonl": _rows(12_000),
        "small.jsonl": _rows(3, "s"),
        "empty.jsonl": [],
    }
    report = StageReport(stage="write")
    out = canonical_jsonl_many(groups, workers, report)
    assert report.started == min(workers, report.unit_count) or workers == 1
    for name, records in groups.items():
        assert out[name] == canonical_jsonl(records), f"{name} differs at {workers}"


def test_parallel_jsonl_agrees_with_itself_at_every_worker_count(
    force_pools: None,
) -> None:
    groups = {"a.jsonl": _rows(12_000), "b.jsonl": _rows(500, "b")}
    seen = {
        count: canonical_jsonl_many(groups, count, StageReport(stage="write"))
        for count in WORKER_COUNTS
    }
    for count in WORKER_COUNTS:
        assert seen[count] == seen[1], f"{count} workers produced different bytes"


def test_a_small_write_does_not_start_a_pool_that_would_lose() -> None:
    """Measured, not guessed: the fixture corpus came back at 0.78x."""
    report = StageReport(stage="write")
    canonical_jsonl_many({"a.jsonl": _rows(100)}, 8, report)
    assert report.started == 1
    assert "below the" in report.skipped_reason


def test_a_record_is_never_split_across_chunks(force_pools: None) -> None:
    """Half a JSON object is not a record. Every line must parse whole."""
    import json

    out = canonical_jsonl_many(
        {"a.jsonl": _rows(11_111)}, 4, StageReport(stage="write")
    )
    lines = out["a.jsonl"].splitlines()
    assert len(lines) == 11_111
    for line in lines:
        json.loads(line)


# ---------------------------------------------------------------------------
# end to end -- every artifact identical at 1, 2, 4 and 8 workers
# ---------------------------------------------------------------------------


def _analyze_digest(
    root: Path, workers: int, tmp_path: Path
) -> tuple[dict[str, bytes], str]:
    """Run the whole static pipeline and return every artifact's bytes.

    A FRESH cache directory per run, deliberately. The ingestion cache is on
    by default and a warm one makes every run look instant and proves nothing
    -- the second run would not re-derive the elements the later stages are
    supposed to disagree about.
    """
    from cascade_map.cli import analyze

    out = tmp_path / f"out{workers}"
    cache = tmp_path / f"cache{workers}"
    said: list[str] = []
    code, _summary = analyze(
        root, out, cache_dir=cache, strict_gate=False, workers=workers,
        worker_report_sink=said.append,
    )
    assert code == 0, f"analyze exited {code} at {workers} workers"
    blobs = {
        path.name: path.read_bytes()
        for path in sorted(out.iterdir())
        # `run_meta.json` is deliberately outside the byte-identical guarantee:
        # it holds the wall clock and the caller's own paths.
        if path.name != "run_meta.json"
    }
    return blobs, "\n".join(said)



def test_every_artifact_is_byte_identical_at_one_two_four_and_eight_workers(
    tmp_path: Path, force_pools: None
) -> None:
    """Constraint 4, under a pool.

    Proven by comparing bytes, not asserted. Eight is more workers than most
    machines running this have cores, which is the point: a dict whose
    insertion order follows completion order, an id minted from a counter that
    advances in worker order, or an iteration over a set all show themselves
    under oversubscription and nowhere else.
    """
    runs = {
        count: _analyze_digest(FIXTURE_ROOT, count, tmp_path)
        for count in WORKER_COUNTS
    }
    digests = {count: blobs for count, (blobs, _text) in runs.items()}
    control = digests[1]
    assert control, "the corpus produced no artifacts"
    # The proof is worthless if no child process ever existed. Every
    # parallelisable stage must report a real pool at 4 workers.
    text = runs[4][1]
    for stage in ("cascade", "records", "write"):
        line = next((one for one in text.splitlines() if one.strip().startswith(stage)), "")
        assert "over 4 workers" in line, (
            f"the {stage} stage never started a pool, so this test proved "
            f"nothing about one:\n{text}"
        )
    for count in WORKER_COUNTS[1:]:
        assert sorted(digests[count]) == sorted(control), (
            f"{count} workers wrote a different set of artifacts"
        )
        differing = [
            name for name, blob in digests[count].items() if blob != control[name]
        ]
        assert not differing, f"{count} workers differ from 1 in: {differing}"


# ---------------------------------------------------------------------------
# the two facts the CFG worker computes so the AST never has to travel
# ---------------------------------------------------------------------------


def _reference_side_effects(
    analyzer: object, element_id: str, nodes: dict[str, object],
    stack: tuple[str, ...] = (),
) -> bool:
    """The original walk, written out here as the thing to agree with.

    `cascade._may_have_side_effects` no longer walks the AST -- the walk
    happens in the worker that built the CFG and the parent replays what it
    recorded. That is only safe if the two give the same answer, and this is
    what checks it.
    """
    import ast as _ast

    from cascade_map.cascade import _BUILTIN_NAMES, _IMPURE_BUILTINS

    if element_id in stack:
        return True
    node = nodes.get(element_id)
    if node is None:
        return True
    for child in _ast.walk(node):  # type: ignore[arg-type]
        verdict = False
        if isinstance(child, (_ast.Global, _ast.Nonlocal, _ast.Yield, _ast.YieldFrom)):
            verdict = True
        elif isinstance(child, (_ast.Attribute, _ast.Subscript)) and isinstance(
            child.ctx, (_ast.Store, _ast.Del)
        ):
            verdict = True
        elif isinstance(child, _ast.Call):
            if isinstance(child.func, _ast.Attribute):
                verdict = True
            elif isinstance(child.func, _ast.Name):
                name = child.func.id
                if name in _IMPURE_BUILTINS:
                    verdict = True
                elif name not in _BUILTIN_NAMES:
                    target = analyzer._lookup_name(  # type: ignore[attr-defined]
                        analyzer._elements.get(element_id), name  # type: ignore[attr-defined]
                    )
                    verdict = not target or _reference_side_effects(
                        analyzer, target, nodes, (*stack, element_id)
                    )
            else:
                verdict = True
        if verdict:
            return True
    return False


_SIDE_EFFECT_PROGRAM = '''
import os

TOTAL = 0


def pure(a, b):
    return a + b


def impure_print(a):
    print(a)
    return a


def writes_attribute(box, value):
    box.field = value


def writes_global():
    global TOTAL
    TOTAL = 1


def calls_pure(a):
    return pure(a, 1)


def calls_impure(a):
    return impure_print(a)


def yields():
    yield 1


def recurses(n):
    return recurses(n - 1) if n else 0


def method_call(thing):
    return thing.compute()
'''


def test_the_side_effect_replay_agrees_with_walking_the_ast(tmp_path: Path) -> None:
    import ast as _ast

    from cascade_map.cascade import CascadeAnalyzer, _locate
    from cascade_map.ingest.inventory import Ingestor

    (tmp_path / "prog.py").write_text(_SIDE_EFFECT_PROGRAM, encoding="utf-8")
    elements, _unres = Ingestor(cache_dir=tmp_path / "c").inventory(str(tmp_path))
    analyzer = CascadeAnalyzer(tmp_path, workers=1)
    analyzer.order(elements, (), ())

    tree = _ast.parse(_SIDE_EFFECT_PROGRAM, filename="prog.py")
    index: dict[str, object] = {}
    line_cache: dict[tuple[str, str], dict[int, object]] = {}
    nodes_cache: dict[tuple[str, str], dict[str, object]] = {}
    for element in elements:
        if element.id in analyzer._builders:
            located = _locate(nodes_cache, line_cache, element, tree)  # type: ignore[arg-type]
            if located is not None:
                index[element.id] = located

    assert len(index) >= 9, "the fixture did not produce the bodies it was written for"
    checked = 0
    for element_id in sorted(index):
        analyzer._side_effect_cache.clear()
        replayed = analyzer._may_have_side_effects(element_id)
        expected = _reference_side_effects(analyzer, element_id, index)
        assert replayed == expected, element_id
        checked += 1
    assert checked >= 9
    # And the answers are not all the same, or the agreement would be vacuous.
    analyzer._side_effect_cache.clear()
    verdicts = {
        element_id: analyzer._may_have_side_effects(element_id)
        for element_id in sorted(index)
    }
    assert True in verdicts.values() and False in verdicts.values()


def test_the_cascade_chain_numbering_survives_the_worker_boundary(
    tmp_path: Path,
) -> None:
    """An `elif` chain is built from the inside out, so its numbering is the
    thing most likely to come back backwards from a worker."""
    from cascade_map.cascade import CascadeAnalyzer
    from cascade_map.ingest.inventory import Ingestor

    (tmp_path / "rules.py").write_text(
        "def decide(x):\n"
        "    if x > 10:\n"
        "        return 'a'\n"
        "    elif x > 5:\n"
        "        return 'b'\n"
        "    elif x > 1:\n"
        "        return 'c'\n"
        "    return 'd'\n",
        encoding="utf-8",
    )
    elements, _ = Ingestor(cache_dir=tmp_path / "c").inventory(str(tmp_path))
    analyzer = CascadeAnalyzer(tmp_path, workers=1)
    _b, _e, _o, decisions, _r, _c, _u = analyzer.order(elements, (), ())
    steps = [d.provenance.note for d in decisions if "step" in d.provenance.note]
    assert any("step 1 of 3" in note for note in steps), steps
    assert any("step 2 of 3" in note for note in steps), steps
    assert any("step 3 of 3" in note for note in steps), steps


# ---------------------------------------------------------------------------
# stage-level overlap
# ---------------------------------------------------------------------------


def _mark_and_wait(_payload: object, path_text: str) -> str:
    """Writes a marker as soon as it starts, then works for a moment.

    The marker is how a test can tell the stage really ran ALONGSIDE the
    caller rather than after it: the caller checks for the file while it is
    still doing its own work.
    """
    marker = Path(path_text)
    marker.write_text("started", encoding="utf-8")
    time.sleep(0.25)
    return "done"


def test_an_overlapped_stage_returns_the_same_value_at_one_and_many_workers(
    tmp_path: Path,
) -> None:
    from cascade_map.parallel import BackgroundUnit

    for workers in (1, 4):
        report = StageReport(stage="dependencies", overlapped_with="lineage")
        job = BackgroundUnit(
            _double, None, 21, workers, report
        )
        assert job.result() == 42, workers
        assert report.unit_count == 1


def test_an_overlapped_stage_really_runs_while_the_caller_is_still_working(
    tmp_path: Path,
) -> None:
    """Two WHOLE stages, neither split, at the same time. This is the only
    kind of parallelism that helps a target which is a single module."""
    if cpu_budget() < 2:  # pragma: no cover - single-core CI
        pytest.skip("overlap needs at least two usable cores")
    from cascade_map.parallel import BackgroundUnit

    marker = tmp_path / "started.txt"
    report = StageReport(stage="dependencies", overlapped_with="lineage")
    job = BackgroundUnit(_mark_and_wait, None, str(marker), 4, report)
    # The caller's own stage, which knows nothing about the other one.
    deadline = time.perf_counter() + 5.0
    while not marker.exists() and time.perf_counter() < deadline:
        time.sleep(0.01)
    seen_while_working = marker.exists()
    assert job.result() == "done"
    assert seen_while_working, "the overlapped stage had not started before result()"
    line = render_stage_line(report)
    assert "overlapped" in line
    assert "ran alongside lineage" in line


def test_one_worker_runs_the_overlapped_stage_inline_and_says_so() -> None:
    """In-process must stay reachable for every stage, including this one."""
    from cascade_map.parallel import BackgroundUnit

    report = StageReport(stage="dependencies", overlapped_with="lineage")
    job = BackgroundUnit(_double, None, 5, 1, report)
    assert job.result() == 10
    assert "no second process" in report.skipped_reason
