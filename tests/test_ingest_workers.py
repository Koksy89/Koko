"""Workers must buy speed without costing a single byte of difference.

The whole risk of a worker pool in this project is constraint 4: identical
input gives byte-identical output. A pool introduces two ways to break it —
results arriving in completion order, and concurrent writers to the cache —
so both are asserted directly rather than reasoned about.

The second thing asserted here is honesty. A worker count printed on its own
reads as a benefit. These tests require the run to MEASURE the benefit and to
say plainly when there is none, including the case the owner actually has:
one very large file, where parallelism across files can do nothing at all.
"""

from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path

import pytest

from cascade_map.contracts.interfaces import canonical_jsonl
from cascade_map.ingest.inventory import Ingestor
from cascade_map.ingest.parallel import (
    GAIN_FLOOR,
    MAX_WORKERS,
    WorkerReport,
    cpu_budget,
    render_worker_report,
    resolve_workers,
    run_units,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "mode_b"


def _digest(root: Path, workers: int, cache_dir: Path) -> tuple[str, Ingestor]:
    ing = Ingestor(cache_dir=cache_dir, workers=workers)
    elements, unresolved = ing.inventory(str(root))
    blob = (canonical_jsonl(elements) + canonical_jsonl(unresolved)).encode()
    return hashlib.sha256(blob).hexdigest(), ing


def _make_tree(base: Path, n_files: int, lines_each: int = 40) -> Path:
    """A synthetic package. Never `tests/fixtures/` -- a corpus that moves
    while it is read cannot grade anything (STATUS.md), and these tests
    write."""
    base.mkdir(parents=True, exist_ok=True)
    (base / "__init__.py").write_text("", encoding="utf-8")
    for i in range(n_files):
        body = "\n".join(
            textwrap.dedent(
                f'''
                def mod{i}_fn{j}(a, b):
                    """Docstring {j}."""
                    total = a + b + {j}
                    if total > {j}:
                        total = total * 2
                    return total
                '''
            )
            for j in range(lines_each)
        )
        (base / f"mod{i}.py").write_text(f"CONST_{i} = {i}\n{body}\n", encoding="utf-8")
    return base


# ---------------------------------------------------------------------------
# worker policy
# ---------------------------------------------------------------------------


def test_zero_and_one_both_mean_in_process() -> None:
    """In-process must stay reachable: it is how this is debugged, and a
    traceback out of a child process is a worse one."""
    assert resolve_workers(0) == 1
    assert resolve_workers(1) == 1


def test_auto_derives_from_the_machine_with_headroom() -> None:
    auto = resolve_workers(None)
    assert 1 <= auto <= MAX_WORKERS
    assert auto <= max(1, cpu_budget() - 1), "auto must leave the machine headroom"


def test_a_request_above_the_cap_is_capped_not_obeyed() -> None:
    assert resolve_workers(10_000) == MAX_WORKERS


def test_a_negative_request_is_an_error_naming_the_value() -> None:
    with pytest.raises(ValueError, match="-3"):
        resolve_workers(-3)


# ---------------------------------------------------------------------------
# determinism -- the non-negotiable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workers", [1, 2, 8])
def test_output_is_byte_identical_at_every_worker_count(
    tmp_path: Path, workers: int
) -> None:
    """Worker counts 1, 2 and 8 must all give the bytes the in-process run
    gives. Every run gets its OWN cold cache: a warm cache would make the
    comparison instant and meaningless."""
    root = _make_tree(tmp_path / "pkg", n_files=12)
    control, _ = _digest(root, 1, tmp_path / "cache-control")
    got, _ = _digest(root, workers, tmp_path / f"cache-{workers}")
    assert got == control


def test_output_is_byte_identical_on_the_real_corpus(tmp_path: Path) -> None:
    """The synthetic tree is uniform; the corpus is not. It holds blobs,
    syntax errors, non-UTF-8 files and data files -- i.e. every branch of
    pass 1 that decides a record's place before any worker starts."""
    if not FIXTURE_ROOT.is_dir():  # pragma: no cover
        pytest.skip("fixture corpus absent")
    control, _ = _digest(FIXTURE_ROOT, 1, tmp_path / "c1")
    parallel, _ = _digest(FIXTURE_ROOT, 4, tmp_path / "c4")
    assert parallel == control


def test_a_warm_cache_written_in_parallel_reproduces_the_cold_run(
    tmp_path: Path,
) -> None:
    """The parent owns every cache write, so a parallel run's cache must be
    as good as a serial one's. Cold-then-warm, both parallel, must agree with
    the single-process cold run."""
    root = _make_tree(tmp_path / "pkg", n_files=8)
    control, _ = _digest(root, 1, tmp_path / "control")
    shared = tmp_path / "shared"
    cold, _ = _digest(root, 4, shared)
    warm, ing = _digest(root, 4, shared)
    assert cold == control
    assert warm == control
    assert ing.worker_report.unit_count == 0, "warm run should re-parse nothing"


def test_a_changed_file_is_re_parsed_under_workers(tmp_path: Path) -> None:
    """The cache must never let a stale record survive a change, and running
    the parse in another process must not weaken that."""
    root = _make_tree(tmp_path / "pkg", n_files=4)
    shared = tmp_path / "shared"
    first, _ = _digest(root, 4, shared)
    (root / "mod1.py").write_text("def brand_new_name():\n    return 99\n", encoding="utf-8")
    second, ing = _digest(root, 4, shared)
    assert second != first
    assert ing.worker_report.unit_count == 1, "only the changed file should re-parse"
    fresh, _ = _digest(root, 1, tmp_path / "fresh")
    assert second == fresh, "the incrementally updated run must match a cold one"


# ---------------------------------------------------------------------------
# the report must measure, not assert
# ---------------------------------------------------------------------------


def test_one_big_file_reports_that_workers_cannot_help(tmp_path: Path) -> None:
    """The owner's actual shape. Asking for 8 workers on a single-file target
    must NOT print "8 workers" as if that were a benefit: one worker takes the
    file and the rest idle, and the run has to say so."""
    root = tmp_path / "single"
    root.mkdir()
    (root / "engine.py").write_text(
        "\n".join(f"def f{i}(a):\n    return a + {i}\n" for i in range(2000)),
        encoding="utf-8",
    )
    _, ing = _digest(root, 8, tmp_path / "cache")
    report = ing.worker_report

    assert report.requested == 8
    assert report.started == 1, "a pool for one unit is pure overhead"
    assert report.unit_count == 1
    assert not report.helped
    text = render_worker_report(report)
    assert "cannot split" in text
    assert "--workers 1" in text
    assert "8 requested, 1 started" in text


def test_a_dominant_file_is_named_with_its_share(tmp_path: Path) -> None:
    root = _make_tree(tmp_path / "pkg", n_files=4, lines_each=4)
    (root / "huge.py").write_text(
        "\n".join(f"def g{i}(a):\n    return a * {i}\n" for i in range(4000)),
        encoding="utf-8",
    )
    _, ing = _digest(root, 4, tmp_path / "cache")
    report = ing.worker_report
    assert report.largest_unit_key == "huge.py"
    assert report.largest_unit_share > 0.5
    assert "huge.py" in render_worker_report(report)


def test_the_report_never_claims_a_benefit_it_did_not_measure() -> None:
    """A gain below the floor must read as "did not help", with a reason and
    a recommendation of --workers 1 -- never as a bare worker count."""
    report = WorkerReport(
        requested=8,
        started=8,
        unit_count=40,
        wall_seconds=1.0,
        serial_seconds=1.05,
        total_seconds=1.0,
        largest_unit_key="a.py",
        largest_unit_seconds=0.05,
        unit_seconds={"a.py": 0.05},
    )
    assert report.gain < GAIN_FLOOR
    assert not report.helped
    text = render_worker_report(report)
    assert "recommendation use --workers 1" in text
    assert "1.05x" in text


def test_the_gain_estimate_matches_a_real_single_process_control(
    tmp_path: Path,
) -> None:
    """The report's gain is derived from per-unit CPU times plus the serial
    remainder, not from running the analysis twice. That derivation is only
    trustworthy if it agrees with a genuine control, so here it is measured
    against one.

    Generous slack: this is a timing on shared CI hardware. The assertion is
    that the derived number is in the same neighbourhood as the measured one
    and, critically, never wildly optimistic.
    """
    root = _make_tree(tmp_path / "pkg", n_files=24, lines_each=30)

    control_ing = Ingestor(cache_dir=tmp_path / "c1", workers=1)
    control_ing.inventory(str(root))
    control_seconds = control_ing.worker_report.total_seconds

    par = Ingestor(cache_dir=tmp_path / "c4", workers=4)
    par.inventory(str(root))
    report = par.worker_report

    if report.started < 2:  # pragma: no cover - single-core machine
        pytest.skip("pool did not start; nothing to compare")
    if control_seconds < 0.2:  # pragma: no cover
        pytest.skip("too fast to time reliably")

    measured_gain = control_seconds / report.total_seconds
    assert report.gain <= measured_gain * 2.0, (
        f"derived gain {report.gain:.2f}x is far above the measured "
        f"{measured_gain:.2f}x -- the report would be flattering itself"
    )
    assert report.gain >= measured_gain * 0.4


def test_gain_does_not_rise_when_the_machine_is_oversubscribed() -> None:
    """The defect this guards: an earlier version summed per-unit WALL times,
    which inflate under contention, so it reported 5.06x for a run that was
    measurably slower than the 3.02x one. Per-unit times are CPU seconds now,
    so the same CPU work in more wall time must report a LOWER gain."""
    fast = WorkerReport(
        started=4, unit_count=8, wall_seconds=0.5, serial_seconds=2.0,
        total_seconds=0.8, unit_seconds={"a": 2.0}, largest_unit_seconds=2.0,
    )
    slow = WorkerReport(
        started=8, unit_count=8, wall_seconds=0.9, serial_seconds=2.0,
        total_seconds=1.2, unit_seconds={"a": 2.0}, largest_unit_seconds=2.0,
    )
    assert slow.gain < fast.gain


def test_an_empty_run_says_nothing_to_do_rather_than_reporting_a_gain() -> None:
    report = WorkerReport()
    assert run_units(lambda p: p, [], 4, report) == {}
    text = render_worker_report(report)
    assert "n/a" in text
    assert "1.00x" not in text


def test_results_are_keyed_not_ordered(tmp_path: Path) -> None:
    """`run_units` returns a mapping precisely so nothing downstream can
    accidentally depend on completion order."""
    report = WorkerReport()
    jobs = [(f"k{i}", i) for i in range(6)]
    out = run_units(_double, jobs, 2, report)
    assert out == {f"k{i}": i * 2 for i in range(6)}
    assert set(report.unit_seconds) == {f"k{i}" for i in range(6)}


def _double(payload: int) -> int:
    """Module level so the pool can pickle it."""
    return payload * 2
