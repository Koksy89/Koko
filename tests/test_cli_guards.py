"""Read commands refuse a directory that holds no map, and write nothing.

The defect these exist for: `blueprint /no/such/dir` created the directory,
wrote a 95 KB page built from no data, and printed `Wrote ...`. An unmatched
shell glob gave `blueprint ""`, which `Path("")` silently turns into the
working directory, and the owner was handed a finished-looking page.

This is the sixth failure in this project that rendered as success, so the
assertion that matters in every case below is the third one: NOTHING WAS
WRITTEN. An exit code can be ignored and a message can scroll past; a file on
disk is what gets opened and believed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cascade_map import cli  # noqa: E402

#: `(command, how to build argv from a graph directory)`. `history` reads a
#: workspace rather than a graph directory and is exercised separately.
READ_COMMANDS = {
    "blueprint": lambda path: ["blueprint", path],
    "view": lambda path: ["view", path],
    "diff-before": lambda path: ["diff", path, path],
    "trace": lambda path: ["trace", path, "--mode", "2"],
}

#: `trace` is the one command that would otherwise have EXECUTED the owner's
#: engine, so its refusal keeps its own louder exit code. Everything else is a
#: usage error.
EXPECTED_CODE = {
    "blueprint": cli.EXIT_USAGE,
    "view": cli.EXIT_USAGE,
    "diff-before": cli.EXIT_USAGE,
    "trace": cli.EXIT_REFUSED,
}


def _tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


@pytest.fixture()
def sandbox(tmp_path: Path) -> Path:
    """A directory whose contents are compared before and after."""
    box = tmp_path / "box"
    box.mkdir()
    return box


def _case(sandbox: Path, case: str) -> tuple[str, str]:
    """`(argv path, the phrase the refusal must contain)`."""
    if case == "missing":
        return str(sandbox / "no-such-dir"), "the directory does not exist"
    if case == "not-a-directory":
        target = sandbox / "a-file.txt"
        target.write_text("not a map\n", encoding="utf-8")
        return str(target), "that is a file, not a directory"
    if case == "no-elements":
        target = sandbox / "bare"
        target.mkdir()
        return str(target), "elements.jsonl is missing"
    if case == "empty-elements":
        target = sandbox / "hollow"
        target.mkdir()
        (target / "elements.jsonl").write_text("", encoding="utf-8")
        return str(target), "found the directory, found no elements"
    raise AssertionError(case)


@pytest.mark.parametrize("command", sorted(READ_COMMANDS))
@pytest.mark.parametrize(
    "case", ["missing", "not-a-directory", "no-elements", "empty-elements"]
)
def test_a_read_command_refuses_and_writes_nothing(
    command: str, case: str, sandbox: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, phrase = _case(sandbox, case)
    before = _tree(sandbox)

    code = cli.main(READ_COMMANDS[command](path))

    assert code == EXPECTED_CODE[command]
    err = capsys.readouterr().err
    assert path in err, err
    assert phrase in err, err
    # The assertion that would have caught the defect.
    assert _tree(sandbox) == before, "a read command created something"


@pytest.mark.parametrize("command", sorted(READ_COMMANDS))
def test_an_empty_path_is_a_usage_error_not_the_working_directory(
    command: str, sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Path("")` is `Path(".")`. An unmatched glob must not become `cwd`."""
    monkeypatch.chdir(sandbox)
    before = _tree(sandbox)
    with pytest.raises(SystemExit) as exit_info:
        cli.main(READ_COMMANDS[command](""))
    assert exit_info.value.code == cli.EXIT_USAGE
    assert _tree(sandbox) == before


def test_the_empty_path_message_explains_what_produced_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        cli.main(["blueprint", ""])
    err = capsys.readouterr().err
    assert "empty path" in err
    assert "glob that matched nothing" in err


def test_diff_checks_the_second_directory_too(
    sandbox: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A real map against nothing reports every element as deleted, which is a
    dramatic and entirely false answer."""
    good = sandbox / "good"
    good.mkdir()
    (good / "elements.jsonl").write_text('{"id": "x"}\n', encoding="utf-8")
    missing = sandbox / "gone"
    before = _tree(sandbox)

    assert cli.main(["diff", str(good), str(missing), "--out", str(sandbox / "d")]) == (
        cli.EXIT_USAGE
    )
    assert str(missing) in capsys.readouterr().err
    assert _tree(sandbox) == before, "--out was created before the inputs were checked"


def test_blueprint_with_a_custom_html_target_still_refuses_first(
    sandbox: Path,
) -> None:
    """The refusal must not depend on where the page would have been written."""
    before = _tree(sandbox)
    code = cli.main(
        ["blueprint", str(sandbox / "nope"), "--html", str(sandbox / "page.html")]
    )
    assert code == cli.EXIT_USAGE
    assert not (sandbox / "page.html").exists()
    assert _tree(sandbox) == before


def test_history_refuses_a_workspace_that_does_not_exist(
    sandbox: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _tree(sandbox)
    missing = sandbox / "no-workspace"
    code = cli.main(["history", "--workspace", str(missing)])
    assert code == cli.EXIT_USAGE
    err = capsys.readouterr().err
    assert str(missing) in err
    assert "does not exist" in err
    assert _tree(sandbox) == before


def test_history_refuses_a_workspace_that_is_a_file(
    sandbox: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = sandbox / "workspace.txt"
    target.write_text("no\n", encoding="utf-8")
    before = _tree(sandbox)
    assert cli.main(["history", "--workspace", str(target)]) == cli.EXIT_USAGE
    assert str(target) in capsys.readouterr().err
    assert _tree(sandbox) == before


def test_history_refuses_an_empty_workspace_argument(sandbox: Path) -> None:
    before = _tree(sandbox)
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["history", "--workspace", ""])
    assert exit_info.value.code == cli.EXIT_USAGE
    assert _tree(sandbox) == before


def test_the_four_diagnoses_are_distinct(sandbox: Path) -> None:
    """"No such directory" and "directory, no elements" are different problems
    with different fixes, and the owner must not have to work out which."""
    messages = set()
    for case in ("missing", "not-a-directory", "no-elements", "empty-elements"):
        path, _ = _case(sandbox, case)
        messages.add(cli._graph_dir_problem(Path(path)).splitlines()[0])
    assert len(messages) == 4


def test_a_real_map_is_not_refused(tmp_path: Path) -> None:
    """The guard must not have been bought by refusing everything."""
    from cascade_map.cli import analyze

    graph = tmp_path / "graph"
    analyze(ROOT / "tests" / "fixtures" / "mode_b", graph, strict_gate=False)
    assert cli._graph_dir_problem(graph) == ""
    assert cli.main(["view", str(graph)]) == cli.EXIT_OK
    assert (graph / "index.html").is_file()
