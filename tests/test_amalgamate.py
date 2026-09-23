"""The single-file build must answer identically to the package.

`tools/amalgamate.py` concatenates the package into one `cascade_map.py`. That
is the deliverable, so the only question worth testing is whether it is the
same tool. Twice it was not, and both times the file ran without complaint:

* renaming `__all__` also rewrote the string literal `"__all__"` the resolver
  compares against, so `import *` linked every name in a module -- 110 call
  edges instead of 108, the precision failure `res_import_star` exists to
  forbid;
* stripping lines that begin `from cascade_map` also stripped them from inside
  the string holding the Mode A child's bootstrap, so `trace` shipped a child
  with no imports.

Neither is visible in the source. Both are visible the moment you diff the
output. So these tests diff the output.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
CORPUS = ROOT / "tests" / "fixtures" / "mode_b"
FIXTURES = ROOT / "tests" / "fixtures"

#: Written per run and expected to differ: wall-clock and elapsed time.
VOLATILE = {"run_meta.json"}

def _builder() -> str | None:
    """An interpreter new enough to *build* the single file.

    The amalgamator needs 3.12+, because before 3.12 `tokenize` returns a whole
    f-string as one token and cannot see the names inside it. The file it
    generates supports 3.11+, and both sides of every comparison below run on
    whichever interpreter is running the tests -- so the build interpreter and
    the run interpreter are deliberately allowed to differ. Without that, this
    file would silently skip itself on a 3.11 test run, which is where these
    tests are least dispensable.
    """
    if sys.version_info >= (3, 12):
        return sys.executable
    for name in ("python3.13", "python3.12"):
        found = shutil.which(name)
        if found:
            return found
    return None


needs_312 = pytest.mark.skipif(
    _builder() is None,
    reason="no Python 3.12+ interpreter available to build the single file",
)


@pytest.fixture(scope="module")
def single_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the one-file version into a temp dir.

    Never into the repository: an earlier round of this project spent two
    debugging sessions on `__pycache__` directories written into the fixture
    corpus, which changed content hashes underneath other cards' tests.
    """
    builder = _builder()
    if builder is None:
        pytest.skip("no Python 3.12+ interpreter available to build the single file")
    out = tmp_path_factory.mktemp("amalgam") / "cascade_map.py"
    result = subprocess.run(
        [builder, str(ROOT / "tools" / "amalgamate.py"), "--out", str(out)],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stderr
    assert out.exists()
    return out


def _run_package(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    code = (
        "import sys\n"
        "from cascade_map.cli import main\n"
        f"sys.argv = ['cascade-map', *{args!r}]\n"
        "raise SystemExit(main())\n"
    )
    env = {"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin", "HOME": str(cwd)}
    return subprocess.run(
        [sys.executable, "-c", code], cwd=cwd, env=env,
        capture_output=True, text=True, timeout=900,
    )


def _run_single(single: Path, args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(cwd)}
    return subprocess.run(
        [sys.executable, str(single), *args], cwd=cwd, env=env,
        capture_output=True, text=True, timeout=900,
    )


def _compare_trees(expected: Path, actual: Path, skip: set[str] = VOLATILE) -> None:
    left = {p.name for p in expected.iterdir() if p.is_file()} - skip
    right = {p.name for p in actual.iterdir() if p.is_file()} - skip
    assert left == right, f"different artifacts written: {left ^ right}"
    differing = [
        name
        for name in sorted(left)
        if (expected / name).read_bytes() != (actual / name).read_bytes()
    ]
    assert not differing, f"single-file output differs from the package in: {differing}"


@needs_312
def test_single_file_is_syntactically_whole(single_file: Path) -> None:
    import ast

    ast.parse(single_file.read_text(encoding="utf-8"))


@needs_312
def test_analyze_output_is_byte_identical(single_file: Path, tmp_path: Path) -> None:
    package_out = tmp_path / "pkg"
    single_out = tmp_path / "one"
    assert _run_package(["analyze", str(CORPUS), "--out", str(package_out)],
                        tmp_path).returncode == 0
    assert _run_single(single_file, ["analyze", str(CORPUS), "--out", str(single_out)],
                       tmp_path).returncode == 0
    _compare_trees(package_out, single_out)


@needs_312
def test_analyze_still_refuses_the_over_linked_star_import(
    single_file: Path, tmp_path: Path
) -> None:
    """The specific regression, asserted on its own so a failure names itself.

    `res_import_star/names.py` declares `__all__ = ["alpha", "beta"]` and also
    defines `delta`. An edge to `delta` means the tool matched a bare name and
    called it an import resolution.
    """
    out = tmp_path / "one"
    assert _run_single(single_file, ["analyze", str(CORPUS), "--out", str(out)],
                       tmp_path).returncode == 0
    edges = [json.loads(line) for line in
             (out / "edges.jsonl").read_text(encoding="utf-8").splitlines() if line]
    forbidden = [e for e in edges if e["target_id"].endswith("res_import_star.names::delta")]
    assert not forbidden, (
        "the amalgamated file links a name `__all__` excludes -- the rename pass "
        f"has reached inside a string literal again: {forbidden}"
    )


@needs_312
def test_view_renders_the_same_html(single_file: Path, tmp_path: Path) -> None:
    graph = tmp_path / "graph"
    assert _run_package(["analyze", str(CORPUS), "--out", str(graph)],
                        tmp_path).returncode == 0
    package_html = tmp_path / "pkg.html"
    single_html = tmp_path / "one.html"
    assert _run_package(["view", str(graph), "--html", str(package_html)],
                        tmp_path).returncode == 0
    assert _run_single(single_file, ["view", str(graph), "--html", str(single_html)],
                       tmp_path).returncode == 0
    assert package_html.read_bytes() == single_html.read_bytes()


# --------------------------------------------------------------------------
# Mode A. This executes a fixture, which only harness and tracer tests may do
# -- and that is what this is: it drives `cascade-map trace`, the one
# sanctioned execution path, over a fixture built to be traced.
# --------------------------------------------------------------------------

MODE_A = ROOT / "tests" / "fixtures" / "mode_a"

#: Differs legitimately between the two shapes. `sandbox_dir` is the `--out`
#: path the caller chose. `external_frames` counts frames the run skipped
#: because they are *not* the target -- and the two shapes import themselves
#: differently, so the package child shows importlib's own frames where the
#: single file, already loaded, does not. Neither number describes the target;
#: `total_events`, `mapped_events` and `unmapped_events`, which do, are
#: compared exactly.
RUN_VOLATILE = {"sandbox_dir"}
MAPPING_VOLATILE = {"external_frames", "external_frames_total", "rate_text"}


@needs_312
def test_trace_output_matches_the_package(single_file: Path, tmp_path: Path) -> None:
    graph = tmp_path / "graph"
    assert _run_package(["analyze", str(MODE_A), "--out", str(graph)],
                        tmp_path).returncode == 0

    scenarios = tmp_path / "scenarios.json"
    scenarios.write_text(json.dumps({
        "target_root": str(MODE_A),
        "scenarios": {"linear": {"module": "run_linear", "function": "main"}},
    }), encoding="utf-8")

    common = ["trace", str(graph), "--scenarios", str(scenarios), "--scenario", "linear"]
    package_out = tmp_path / "tr-pkg"
    single_out = tmp_path / "tr-one"
    left = _run_package([*common, "--out", str(package_out)], tmp_path)
    assert left.returncode == 0, left.stderr
    right = _run_single(single_file, [*common, "--out", str(single_out)], tmp_path)
    assert right.returncode == 0, right.stderr

    runs_left = sorted(p.name for p in (package_out / "runtime").iterdir())
    runs_right = sorted(p.name for p in (single_out / "runtime").iterdir())
    assert runs_left == runs_right, "the two shapes disagree on the run id"

    for run_id in runs_left:
        here = package_out / "runtime" / run_id
        there = single_out / "runtime" / run_id
        _compare_trees(here, there, skip={"run.json", "mapping.json"})

        one = json.loads((here / "run.json").read_text(encoding="utf-8"))
        two = json.loads((there / "run.json").read_text(encoding="utf-8"))
        for key in RUN_VOLATILE:
            one.pop(key, None)
            two.pop(key, None)
        assert one == two

        one = json.loads((here / "mapping.json").read_text(encoding="utf-8"))
        two = json.loads((there / "mapping.json").read_text(encoding="utf-8"))
        for key in MAPPING_VOLATILE:
            one.pop(key, None)
            two.pop(key, None)
        assert one == two
        assert one["unmapped_events"] == 0

    # The sandbox is the harness's, not ours; leave nothing behind.
    shutil.rmtree(package_out / "sandbox", ignore_errors=True)
    shutil.rmtree(single_out / "sandbox", ignore_errors=True)


#: Lines of the worker-effectiveness report that carry a wall-clock
#: measurement. They are a timing, so they cannot be byte-identical between
#: two runs and are compared by SHAPE rather than value -- the same disclosed
#: exception this file already makes for `sandbox_dir` and `external_frames`.
#: The lines that do not carry a timing are still compared exactly.
_TIMING_LINE_PREFIXES = ("parallel gain", "why", "recommendation")


def _without_timings(text: str) -> str:
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(_TIMING_LINE_PREFIXES):
            out.append(stripped.split()[0] + " <timing-dependent>")
        else:
            out.append(line)
    return "\n".join(out)


@needs_312



def test_track_builds_the_same_ledger(single_file: Path, tmp_path: Path) -> None:
    """Card 18 through both shapes. The ledger is the artifact an owner keeps
    for the life of the project, so the two builds must write the same bytes --
    everything except the two fields that are wall-clock by definition."""
    versions = FIXTURES / "versions" / "dif_impact_rank"

    def workspace(name: str) -> Path:
        root = tmp_path / name
        (root / "versions").mkdir(parents=True)
        shutil.copytree(versions / "before", root / "versions" / "eng_2026-01-14")
        shutil.copytree(versions / "after", root / "versions" / "eng_2026-02-03")
        return root

    package_root = workspace("pkg")
    single_root = workspace("one")
    args = ["track", "--sink", "engine::decide", "--env", "no-such-env"]
    package_run = _run_package(args, package_root)
    single_run = _run_single(single_file, args, single_root)
    assert package_run.returncode == 0, package_run.stderr
    assert single_run.returncode == 0, single_run.stderr

    def normalized(root: Path) -> str:
        document = json.loads(
            (root / "out" / "metatron_ledger.json").read_text(encoding="utf-8")
        )
        for record in document["versions"]:
            record["discovered_at"] = ""
            record["stage_millis"] = {}
            record["total_millis"] = 0
            record["artifact_dir"] = Path(record["artifact_dir"]).name
        for comparison in document["comparisons"]:
            comparison["analysis_millis_delta"] = {}
        return json.dumps(document, sort_keys=True, indent=1)

    assert normalized(package_root) == normalized(single_root)
    assert "0 unaccounted" in package_run.stdout
    assert _without_timings(package_run.stdout.replace(str(package_root), "X")) == (
        _without_timings(single_run.stdout.replace(str(single_root), "X"))
    )
    # The worker-effectiveness report IS printed by both shapes, and it must
    # be: dropping it here would let it disappear from one of them unnoticed.
    for run in (package_run, single_run):
        assert "parallel gain" in run.stdout
        assert "recommendation" in run.stdout
