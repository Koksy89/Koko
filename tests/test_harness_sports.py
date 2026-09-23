"""Card 11, second round: ``argv`` scenarios and sports as first-class runs.

The first real target is a **library** in one 14.8 MB file that never calls
itself, plus an argv-driven launcher. Nothing shaped like "import this module
and call that function" can drive it, and requiring the owner to add a shim
file to their own tree is a change to the code under analysis -- the one
thing this tool must never require. So a scenario can carry ``argv``, and
scenarios can be derived from settings so a sport is a command-line argument.

**Nothing here executes anything under ``tests/fixtures/``, and nothing here
writes there.** Every program these tests run is written into ``tmp_path`` by
the test itself, which is the same rule the rest of card 11's suite follows
for programs it needs to be adversarial about. `tests/fixtures/` is hashed
before and after, exactly as `test_harness.py` does, because a corpus that
moves while it is being read cannot grade anything.

Tests that reach ``Harness.start`` run it in a **separate interpreter**: the
sandbox's enforcement flag deliberately never clears, so an in-process run
would leave the rest of the pytest session sandboxed. See
``tests/test_harness.py``'s module docstring.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cascade_map import cli
from cascade_map.harness.scenarios import (
    ScenarioDerivationError,
    derive_scenario_document,
    derive_scenarios,
    harness_warnings,
    runner_module_name,
    select_sports,
)
from cascade_map.ledger import SETTING_DEFAULTS, Settings, SettingsError

FIXTURES_ROOT = Path(__file__).parent / "fixtures"
SRC_PATH = str(Path(__file__).resolve().parent.parent / "src")

SPORTS = tuple(SETTING_DEFAULTS["SPORTS"])


# ---------------------------------------------------------------------------
# The corpus is never touched by this module.
# ---------------------------------------------------------------------------


def _snapshot_fixtures_tree() -> dict[str, str]:
    return {
        path.relative_to(FIXTURES_ROOT).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(FIXTURES_ROOT.rglob("*"))
        if path.is_file()
    }


@pytest.fixture(scope="module", autouse=True)
def _fixtures_untouched():
    original = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    before = _snapshot_fixtures_tree()
    yield
    sys.dont_write_bytecode = original
    after = _snapshot_fixtures_tree()
    assert after == before, "tests/fixtures/ changed while this module ran"


# ---------------------------------------------------------------------------
# An argv-driven target, written into tmp_path. This is the shape the real
# launcher has: argparse at module top level, no callable entry point.
# ---------------------------------------------------------------------------

#: Reads --engine/--sports/--workers with argparse at import time, exactly as
#: a real `python bin/go_live.py --sports basketball` launcher does, and
#: writes what it saw into the current directory -- which the harness has
#: already redirected into the sandbox.
RUNNER_SOURCE = '''\
import argparse
import json
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--engine", required=True)
parser.add_argument("--sports", required=True)
parser.add_argument("--workers", type=int, default=1)
parser.add_argument("--target-new", type=int, default=0)
args = parser.parse_args()

Path("argv_seen.json").write_text(
    json.dumps({"argv": list(sys.argv), "parsed": vars(args)}, sort_keys=True),
    encoding="utf-8",
)


def main(*extra):
    Path("called.json").write_text(
        json.dumps({"argv": list(sys.argv), "extra": list(extra)}, sort_keys=True),
        encoding="utf-8",
    )
'''

RAISING_RUNNER_SOURCE = '''\
import argparse
import json
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--engine", required=True)
parser.add_argument("--sports", required=True)
parser.add_argument("--workers", type=int, default=1)
args = parser.parse_args()
Path("argv_seen.json").write_text(
    json.dumps({"argv": list(sys.argv)}, sort_keys=True), encoding="utf-8"
)
raise RuntimeError("the launcher exploded after reading argv")
'''

ENGINE_SOURCE = '''\
"""A library. Running it does nothing; the launcher is what runs."""


def decide(value):
    return value > 0
'''


def _target_tree(tmp_path: Path, runner_source: str = RUNNER_SOURCE) -> Path:
    """A target shaped like the real one: an engine library plus bin/<runner>."""
    root = tmp_path / "target"
    (root / "bin").mkdir(parents=True)
    (root / "AmunEV_Engine_V2.py").write_text(ENGINE_SOURCE, encoding="utf-8")
    (root / "bin" / "go_live.py").write_text(runner_source, encoding="utf-8")
    return root


def _analyse(root: Path, graph: Path) -> None:
    cli.analyze(root, graph, strict_gate=False)


def _child_script(target_root: Path, graph: Path, sandbox: Path, spec: str) -> str:
    """A script that runs one scenario through the real harness and then
    prints the interpreter's argv, so "restored afterwards" is measured in
    the process that was actually changed."""
    return (
        "import json, sys\n"
        f"sys.path.insert(0, {SRC_PATH!r})\n"
        "from pathlib import Path\n"
        "from cascade_map.harness import Harness, RunConfig, ScenarioSpec\n"
        "from cascade_map.harness.hashing import compute_graph_hash, compute_target_hashes\n"
        f"target_root = Path({str(target_root)!r})\n"
        "before = list(sys.argv)\n"
        "config = RunConfig(\n"
        "    target_root=target_root,\n"
        f"    mode_b_out_dir=Path({str(graph)!r}),\n"
        f"    sandbox_root=Path({str(sandbox)!r}),\n"
        f"    scenarios={{'basketball': {spec}}},\n"
        ")\n"
        "graph_hash = compute_graph_hash(compute_target_hashes(target_root))\n"
        "record = Harness(config).start('basketball', graph_hash)\n"
        "print(json.dumps({'before': before, 'after': list(sys.argv),\n"
        "                  'refused': record.refused,\n"
        "                  'reason': record.refusal_reason,\n"
        "                  'failure': None if record.scenario_failure is None\n"
        "                             else record.scenario_failure.exception_type}))\n"
    )


def _run_child(script: str, extra_env: dict[str, str] | None = None):
    env = dict(os.environ)
    env.pop("PYTHONHASHSEED", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120, env=env
    )


def _graph(tmp_path: Path) -> Path:
    out = tmp_path / "graph"
    out.mkdir(parents=True, exist_ok=True)
    (out / "elements.jsonl").write_text("", encoding="utf-8")
    return out


ARGV = (
    "bin/go_live.py",
    "--engine",
    "AmunEV_Engine_V2.py",
    "--sports",
    "basketball",
    "--workers",
    "8",
)


# ---------------------------------------------------------------------------
# argv reaches the target, and is restored -- including when it raises.
# ---------------------------------------------------------------------------


def test_argv_reaches_an_import_only_scenario(tmp_path: Path) -> None:
    """The shape the real runner has: no function, argparse at module level."""
    target_root = _target_tree(tmp_path)
    sandbox = tmp_path / "sandbox"
    script = _child_script(
        target_root,
        _graph(tmp_path),
        sandbox,
        f"ScenarioSpec(name='basketball', module='bin.go_live', function='', argv={ARGV!r})",
    )
    result = _run_child(script)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["refused"] is False, report["reason"]
    assert report["failure"] is None, "the scenario raised; argparse did not get its flags"

    seen = json.loads((sandbox / "argv_seen.json").read_text(encoding="utf-8"))
    assert seen["argv"] == list(ARGV)
    assert seen["parsed"]["sports"] == "basketball"
    assert seen["parsed"]["engine"] == "AmunEV_Engine_V2.py"
    assert seen["parsed"]["workers"] == 8


def test_argv_is_restored_after_the_scenario(tmp_path: Path) -> None:
    target_root = _target_tree(tmp_path)
    script = _child_script(
        target_root,
        _graph(tmp_path),
        tmp_path / "sandbox",
        f"ScenarioSpec(name='basketball', module='bin.go_live', function='', argv={ARGV!r})",
    )
    result = _run_child(script)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["after"] == report["before"], (
        "sys.argv was left rewritten after the scenario; every later scenario "
        "and this tool itself would see the target's flags"
    )


def test_argv_is_restored_when_the_scenario_raises(tmp_path: Path) -> None:
    """The case a `try`/`finally` written the other way round would miss."""
    target_root = _target_tree(tmp_path, RAISING_RUNNER_SOURCE)
    sandbox = tmp_path / "sandbox"
    script = _child_script(
        target_root,
        _graph(tmp_path),
        sandbox,
        f"ScenarioSpec(name='basketball', module='bin.go_live', function='', argv={ARGV!r})",
    )
    result = _run_child(script)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["failure"] == "RuntimeError", "the scenario was supposed to raise"
    assert report["after"] == report["before"]
    # And the target really did see the flags before it blew up.
    seen = json.loads((sandbox / "argv_seen.json").read_text(encoding="utf-8"))
    assert seen["argv"] == list(ARGV)


def test_argv_also_reaches_a_function_scenario(tmp_path: Path) -> None:
    target_root = _target_tree(tmp_path)
    sandbox = tmp_path / "sandbox"
    script = _child_script(
        target_root,
        _graph(tmp_path),
        sandbox,
        f"ScenarioSpec(name='basketball', module='bin.go_live', function='main', "
        f"args=('extra',), argv={ARGV!r})",
    )
    result = _run_child(script)
    assert result.returncode == 0, result.stderr
    called = json.loads((sandbox / "called.json").read_text(encoding="utf-8"))
    assert called["argv"] == list(ARGV)
    assert called["extra"] == ["extra"]


def test_an_empty_argv_leaves_sys_argv_alone(tmp_path: Path) -> None:
    """Default-off. A scenario that declares no argv must not have the
    harness invent one for it."""
    target_root = tmp_path / "plain"
    target_root.mkdir()
    (target_root / "probe.py").write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "Path('argv_seen.json').write_text(json.dumps({'argv': list(sys.argv)}))\n",
        encoding="utf-8",
    )
    sandbox = tmp_path / "sandbox"
    script = _child_script(
        target_root,
        _graph(tmp_path),
        sandbox,
        "ScenarioSpec(name='basketball', module='probe', function='')",
    )
    result = _run_child(script)
    assert result.returncode == 0, result.stderr
    seen = json.loads((sandbox / "argv_seen.json").read_text(encoding="utf-8"))
    assert seen["argv"] == ["-c"], "the harness rewrote argv for a scenario that declared none"


# ---------------------------------------------------------------------------
# Sport selection
# ---------------------------------------------------------------------------


def test_no_selection_means_every_sport() -> None:
    assert select_sports(SPORTS) == SPORTS


def test_sport_setting_selects_one() -> None:
    assert select_sports(SPORTS, "basketball") == ("basketball",)


def test_requested_sports_beat_the_setting_and_keep_sports_order() -> None:
    assert select_sports(SPORTS, "football", ["basketball", "etennis"]) == (
        "etennis",
        "basketball",
    )


def test_a_repeated_sport_is_selected_once() -> None:
    """Order and duplicates decide scenario names, and scenario names decide
    run ids. Two identical runs may not differ over how the flags were typed."""
    assert select_sports(SPORTS, "", ["basketball", "basketball"]) == ("basketball",)


def test_all_sports_beats_the_setting() -> None:
    assert select_sports(SPORTS, "basketball", (), all_sports=True) == SPORTS


def test_all_sports_with_sport_is_an_error() -> None:
    with pytest.raises(ValueError, match="cannot both be given"):
        select_sports(SPORTS, "", ["basketball"], all_sports=True)


def test_unknown_sport_lists_the_valid_ones() -> None:
    with pytest.raises(ValueError) as excinfo:
        select_sports(SPORTS, "", ["basketbal"])
    message = str(excinfo.value)
    assert "basketbal" in message
    assert len(SPORTS) == 7
    for sport in SPORTS:
        assert sport in message


# ---------------------------------------------------------------------------
# Settings -- still data, and SPORT still has to name a real sport
# ---------------------------------------------------------------------------


def test_the_shipped_settings_carry_the_sports_keys() -> None:
    settings = Settings.from_mapping(cli.METATRON_SETTINGS)
    assert settings.sports == SPORTS
    assert settings.sport == ""
    assert settings.engine == "AmunEV_Engine_V2.py"
    assert settings.runner == "bin/go_live.py"
    assert settings.run_args == ()
    assert settings.selected_sports() == SPORTS


def test_sport_naming_no_declared_sport_is_an_error_listing_them() -> None:
    with pytest.raises(SettingsError) as excinfo:
        Settings.from_mapping({**SETTING_DEFAULTS, "SPORT": "cricket"})
    message = str(excinfo.value)
    assert "cricket" in message
    assert "basketball" in message


def test_sports_must_be_a_list_of_strings() -> None:
    with pytest.raises(SettingsError, match="list of strings"):
        Settings.from_mapping({**SETTING_DEFAULTS, "SPORTS": "basketball"})


def test_run_args_must_be_a_list_of_strings() -> None:
    with pytest.raises(SettingsError, match="list of strings"):
        Settings.from_mapping({**SETTING_DEFAULTS, "RUN_ARGS": "--workers 8"})


def test_a_typo_in_a_sports_key_names_the_typo() -> None:
    with pytest.raises(SettingsError) as excinfo:
        Settings.from_mapping({**SETTING_DEFAULTS, "SPORTZ": []})
    message = str(excinfo.value)
    assert 'Did you mean "SPORT' in message, message
    assert "SPORTS" in message


# ---------------------------------------------------------------------------
# Deriving scenarios -- and refusing rather than guessing a module
# ---------------------------------------------------------------------------


def test_runner_path_resolves_to_a_module_rooted_at_the_target(tmp_path: Path) -> None:
    root = _target_tree(tmp_path)
    assert runner_module_name(root, "bin/go_live.py") == "bin.go_live"


def test_a_missing_runner_refuses_and_names_the_path_tried(tmp_path: Path) -> None:
    root = _target_tree(tmp_path)
    with pytest.raises(ScenarioDerivationError) as excinfo:
        runner_module_name(root, "bin/launch.py")
    message = str(excinfo.value)
    assert str(root / "bin" / "launch.py") in message
    assert "guessing" in message


def test_a_runner_that_is_not_python_refuses(tmp_path: Path) -> None:
    root = _target_tree(tmp_path)
    (root / "bin" / "go_live.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    with pytest.raises(ScenarioDerivationError, match="not a .py file"):
        runner_module_name(root, "bin/go_live.sh")


def test_a_runner_that_is_not_a_legal_module_name_refuses(tmp_path: Path) -> None:
    root = _target_tree(tmp_path)
    (root / "bin" / "go-live.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(ScenarioDerivationError, match="importable module name"):
        runner_module_name(root, "bin/go-live.py")


def test_a_runner_escaping_the_target_root_refuses(tmp_path: Path) -> None:
    root = _target_tree(tmp_path)
    with pytest.raises(ScenarioDerivationError, match=r"must not climb out"):
        runner_module_name(root, "../outside.py")


def test_a_missing_engine_file_refuses_before_anything_runs(tmp_path: Path) -> None:
    root = _target_tree(tmp_path)
    (root / "AmunEV_Engine_V2.py").unlink()
    with pytest.raises(ScenarioDerivationError) as excinfo:
        derive_scenarios(
            root, sports=["basketball"], runner="bin/go_live.py",
            engine="AmunEV_Engine_V2.py",
        )
    assert str(root / "AmunEV_Engine_V2.py") in str(excinfo.value)


def test_one_scenario_per_sport_named_for_the_sport(tmp_path: Path) -> None:
    root = _target_tree(tmp_path)
    scenarios = derive_scenarios(
        root, sports=SPORTS, runner="bin/go_live.py", engine="AmunEV_Engine_V2.py",
    )
    assert sorted(scenarios) == sorted(SPORTS)
    for sport, body in scenarios.items():
        assert body["module"] == "bin.go_live"
        assert body["function"] == ""
        assert body["argv"] == [
            "bin/go_live.py", "--engine", "AmunEV_Engine_V2.py", "--sports", sport,
        ]


def test_run_args_are_passed_through_after_the_sport(tmp_path: Path) -> None:
    root = _target_tree(tmp_path)
    scenarios = derive_scenarios(
        root, sports=["basketball"], runner="bin/go_live.py",
        engine="AmunEV_Engine_V2.py", run_args=["--workers", "8", "--target-new", "200"],
    )
    assert scenarios["basketball"]["argv"] == [
        "bin/go_live.py", "--engine", "AmunEV_Engine_V2.py", "--sports", "basketball",
        "--workers", "8", "--target-new", "200",
    ]


def test_a_derived_document_declares_no_process_and_no_environment(tmp_path: Path) -> None:
    """Default deny is a property of these defaults. A derived run may not
    quietly hand the target a subprocess or a secret."""
    root = _target_tree(tmp_path)
    document = derive_scenario_document(
        root, sports=["basketball"], runner="bin/go_live.py",
        engine="AmunEV_Engine_V2.py",
    )
    assert document["declared_process_names"] == []
    assert document["env_passthrough"] == []


# ---------------------------------------------------------------------------
# The warnings the owner reads. These bite on this target specifically.
# ---------------------------------------------------------------------------


def test_the_three_warnings_name_network_sandbox_and_processes() -> None:
    warnings = harness_warnings("/tmp/sbx")
    joined = "\n".join(warnings)
    assert "NETWORK IS BLOCKED" in joined and "DNS" in joined
    assert "/tmp/sbx" in joined and "Workbooks/" in joined
    assert "PROCESS SPAWNING IS BLOCKED" in joined


def test_a_declared_child_process_is_called_unsupervised_and_unrecorded() -> None:
    warnings = harness_warnings("/tmp/sbx", declared_process_names=["python3"])
    joined = "\n".join(warnings)
    assert "UNSUPERVISED" in joined and "UNRECORDED" in joined
    assert "python3" in joined


def test_a_workers_flag_is_called_out_by_name() -> None:
    joined = "\n".join(harness_warnings("/tmp/sbx", run_args=["--workers", "8"]))
    assert "--workers" in joined


# ---------------------------------------------------------------------------
# The command line: a sport, from the terminal, with no scenarios file
# ---------------------------------------------------------------------------


def _prepared(tmp_path: Path, runner_source: str = RUNNER_SOURCE) -> tuple[Path, Path]:
    root = _target_tree(tmp_path, runner_source)
    graph = tmp_path / "graph"
    _analyse(root, graph)
    return root, graph


def test_trace_runs_a_named_sport_with_no_scenarios_file(tmp_path: Path, capsys) -> None:
    """The whole point of this card: a sport is a command-line argument."""
    _root, graph = _prepared(tmp_path)
    out = tmp_path / "out"
    code = cli.main(["trace", str(graph), "--out", str(out), "--sport", "basketball"])
    printed = capsys.readouterr()
    assert code == cli.EXIT_OK, printed.err

    derived = json.loads((out / "derived_scenarios.json").read_text(encoding="utf-8"))
    assert sorted(derived["scenarios"]) == ["basketball"]

    seen = json.loads((out / "sandbox" / "argv_seen.json").read_text(encoding="utf-8"))
    assert seen["parsed"]["sports"] == "basketball"

    runs = sorted((out / "runtime").iterdir())
    assert len(runs) == 1
    record = json.loads((runs[0] / "run.json").read_text(encoding="utf-8"))
    assert record["refused"] is False
    assert record["scenario"] == "basketball"
    assert record["scenario_failure"] is None


def test_trace_prints_the_three_warnings_as_part_of_the_run(tmp_path: Path, capsys) -> None:
    _root, graph = _prepared(tmp_path)
    out = tmp_path / "out"
    assert cli.main(
        ["trace", str(graph), "--out", str(out), "--sport", "basketball",
         "--run-arg=--workers", "--run-arg=8"]
    ) == cli.EXIT_OK
    printed = capsys.readouterr().out
    assert "NETWORK IS BLOCKED" in printed
    assert "EVERY FILE WRITE IS REDIRECTED" in printed
    assert "PROCESS SPAWNING IS BLOCKED" in printed
    assert "--workers" in printed


def test_all_sports_runs_each_sport_as_its_own_scenario(tmp_path: Path, capsys) -> None:
    _root, graph = _prepared(tmp_path)
    out = tmp_path / "out"
    assert cli.main(
        ["trace", str(graph), "--out", str(out), "--all-sports"]
    ) == cli.EXIT_OK
    capsys.readouterr()
    records = [
        json.loads((run / "run.json").read_text(encoding="utf-8"))
        for run in sorted((out / "runtime").iterdir())
    ]
    assert sorted(r["scenario"] for r in records) == sorted(SPORTS)
    assert len({r["run_id"] for r in records}) == len(SPORTS), (
        "two sports shared a run id; they are different scenarios"
    )


def test_an_unknown_sport_on_the_command_line_lists_the_valid_ones(
    tmp_path: Path, capsys
) -> None:
    _root, graph = _prepared(tmp_path)
    code = cli.main(["trace", str(graph), "--out", str(tmp_path / "o"),
                     "--sport", "cricket"])
    message = capsys.readouterr().err
    assert code == cli.EXIT_USAGE
    assert "cricket" in message
    assert len(SPORTS) == 7
    for sport in SPORTS:
        assert sport in message


def test_an_unknown_sport_on_track_lists_the_valid_ones(tmp_path: Path, capsys) -> None:
    code = cli.main(["track", "--sport", "cricket"])
    assert code == cli.EXIT_USAGE
    assert "cricket" in capsys.readouterr().err


def test_an_unresolvable_runner_refuses_rather_than_guessing(
    tmp_path: Path, capsys
) -> None:
    root, graph = _prepared(tmp_path)
    (root / "bin" / "go_live.py").unlink()
    code = cli.main(["trace", str(graph), "--out", str(tmp_path / "o"),
                     "--sport", "basketball"])
    message = capsys.readouterr().err
    assert code == cli.EXIT_REFUSED
    assert "REFUSED" in message
    assert str(root / "bin" / "go_live.py") in message
    assert not (tmp_path / "o" / "runtime").exists(), "something ran anyway"


def test_a_scenarios_file_still_wins(tmp_path: Path, capsys) -> None:
    """The declared path is unchanged: naming a file means running that file."""
    root, graph = _prepared(tmp_path)
    scenarios = tmp_path / "scenarios.json"
    scenarios.write_text(
        json.dumps({
            "target_root": str(root),
            "scenarios": {
                "declared": {
                    "module": "bin.go_live",
                    "function": "",
                    "argv": ["bin/go_live.py", "--engine", "AmunEV_Engine_V2.py",
                             "--sports", "etennis"],
                }
            },
        }),
        encoding="utf-8",
    )
    out = tmp_path / "out"
    code = cli.main(["trace", str(graph), "--out", str(out),
                     "--scenarios", str(scenarios), "--scenario", "declared",
                     "--sport", "basketball"])
    assert code == cli.EXIT_OK, capsys.readouterr().err
    capsys.readouterr()
    assert not (out / "derived_scenarios.json").exists()
    seen = json.loads((out / "sandbox" / "argv_seen.json").read_text(encoding="utf-8"))
    assert seen["parsed"]["sports"] == "etennis", "the --sport flag overrode the file"


# ---------------------------------------------------------------------------
# --preflight: an answer without executing anything
# ---------------------------------------------------------------------------

#: Writes a marker the instant it is imported. If preflight ever executes the
#: target, this file appears and the test fails -- the same sentinel technique
#: the static cards use, applied to the one command that could run something.
SENTINEL_RUNNER = '''\
from pathlib import Path

Path(__file__).parent.parent.joinpath("SENTINEL_RAN").write_text("ran", encoding="utf-8")
'''


def test_preflight_executes_nothing(tmp_path: Path, capsys) -> None:
    root, graph = _prepared(tmp_path, SENTINEL_RUNNER)
    code = cli.main(["trace", str(graph), "--out", str(tmp_path / "o"),
                     "--sport", "basketball", "--preflight"])
    printed = capsys.readouterr()
    assert code == cli.EXIT_OK, printed.err
    assert "nothing was executed" in printed.out.lower()
    assert not (root / "SENTINEL_RAN").exists(), "preflight executed the target"
    assert not (tmp_path / "o" / "runtime").exists()


def test_preflight_reports_the_controls_and_the_scenarios(tmp_path: Path, capsys) -> None:
    _root, graph = _prepared(tmp_path)
    assert cli.main(["trace", str(graph), "--out", str(tmp_path / "o"),
                     "--sport", "basketball", "--preflight"]) == cli.EXIT_OK
    printed = capsys.readouterr().out
    assert "bin/go_live.py -> bin.go_live" in printed
    assert "blocked at the socket layer" in printed
    assert "--engine AmunEV_Engine_V2.py --sports basketball" in printed
    assert "VERDICT" in printed


def test_preflight_refuses_when_the_runner_cannot_be_resolved(
    tmp_path: Path, capsys
) -> None:
    root, graph = _prepared(tmp_path)
    (root / "bin" / "go_live.py").unlink()
    code = cli.main(["trace", str(graph), "--out", str(tmp_path / "o"),
                     "--sport", "basketball", "--preflight"])
    printed = capsys.readouterr().err
    assert code == cli.EXIT_REFUSED
    assert "WOULD REFUSE TO START" in printed
    assert str(root / "bin" / "go_live.py") in printed


def test_preflight_reports_a_stale_graph_without_running(tmp_path: Path, capsys) -> None:
    root, graph = _prepared(tmp_path)
    (root / "AmunEV_Engine_V2.py").write_text(ENGINE_SOURCE + "\n# edited\n",
                                              encoding="utf-8")
    code = cli.main(["trace", str(graph), "--out", str(tmp_path / "o"),
                     "--sport", "basketball", "--preflight"])
    printed = capsys.readouterr().err
    assert code == cli.EXIT_REFUSED
    assert "STALE" in printed


def test_preflight_reports_the_python_the_targets_syntax_needs(
    tmp_path: Path, capsys
) -> None:
    _root, graph = _prepared(tmp_path)
    cli.main(["trace", str(graph), "--out", str(tmp_path / "o"),
              "--sport", "basketball", "--preflight"])
    printed = capsys.readouterr().out
    assert "this interpreter is" in printed


# ---------------------------------------------------------------------------
# Refusal: the harness will not start on a stale graph, through the CLI
# ---------------------------------------------------------------------------


def test_trace_refuses_on_a_stale_graph(tmp_path: Path, capsys) -> None:
    """The graph hash comes from the GRAPH, not from a fresh read of the
    target. Recomputing it from the target it is about to run would compare a
    number against itself and could never fail."""
    root, graph = _prepared(tmp_path)
    (root / "AmunEV_Engine_V2.py").write_text(ENGINE_SOURCE + "\n# edited\n",
                                              encoding="utf-8")
    out = tmp_path / "out"
    code = cli.main(["trace", str(graph), "--out", str(out), "--sport", "basketball"])
    message = capsys.readouterr()
    assert code == cli.EXIT_REFUSED
    assert "stale graph" in (message.err + message.out)
    assert not (out / "sandbox" / "argv_seen.json").exists(), "the target ran anyway"


def test_trace_refuses_when_the_graph_has_no_manifest(tmp_path: Path, capsys) -> None:
    root, graph = _prepared(tmp_path)
    (graph / "manifest.json").unlink()
    code = cli.main(["trace", str(graph), "--out", str(tmp_path / "o"),
                     "--sport", "basketball"])
    message = capsys.readouterr().err
    assert code == cli.EXIT_REFUSED
    assert "manifest.json" in message
    assert root.is_dir()


# ---------------------------------------------------------------------------
# Determinism: the same run twice, including across PYTHONHASHSEED
# ---------------------------------------------------------------------------


def test_two_runs_of_the_same_sport_are_byte_identical(tmp_path: Path) -> None:
    root, graph = _prepared(tmp_path)

    def once(seed: str, out: Path) -> tuple[str, str]:
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        env["PYTHONPATH"] = SRC_PATH
        result = subprocess.run(
            [sys.executable, "-m", "cascade_map.cli", "trace", str(graph),
             "--out", str(out), "--sport", "basketball"],
            capture_output=True, text=True, timeout=300, env=env, cwd=str(tmp_path),
        )
        assert result.returncode == 0, result.stderr
        runs = sorted((out / "runtime").iterdir())
        assert len(runs) == 1
        record = json.loads((runs[0] / "run.json").read_text(encoding="utf-8"))
        record.pop("sandbox_dir")
        return (
            json.dumps(record, sort_keys=True),
            (out / "derived_scenarios.json").read_text(encoding="utf-8"),
        )

    first = once("0", tmp_path / "a")
    second = once("12345", tmp_path / "b")
    assert first[0] == second[0], "two runs of the same sport disagreed"
    assert first[1].replace(str(tmp_path / "a"), "") == second[1].replace(
        str(tmp_path / "b"), ""
    )
    assert root.is_dir()


def test_the_run_id_is_the_same_for_the_same_sport_and_differs_between_sports(
    tmp_path: Path, capsys
) -> None:
    _root, graph = _prepared(tmp_path)
    out = tmp_path / "out"
    assert cli.main(["trace", str(graph), "--out", str(out),
                     "--sport", "basketball", "--sport", "etennis"]) == cli.EXIT_OK
    capsys.readouterr()
    by_scenario = {
        json.loads((run / "run.json").read_text(encoding="utf-8"))["scenario"]: run.name
        for run in sorted((out / "runtime").iterdir())
    }
    assert set(by_scenario) == {"basketball", "etennis"}
    assert by_scenario["basketball"] != by_scenario["etennis"]

    again = tmp_path / "again"
    assert cli.main(["trace", str(graph), "--out", str(again),
                     "--sport", "basketball"]) == cli.EXIT_OK
    capsys.readouterr()
    repeated = sorted((again / "runtime").iterdir())[0].name
    assert repeated == by_scenario["basketball"]


# ---------------------------------------------------------------------------
# `track` MODE 2: each sport is its own scenario, recorded per version
#
# Nothing below executes a line of any version tree. The Mode A seam is a
# stub that records how it was called, which is the only way to prove the
# wiring without running owner code -- the same technique card 18 uses.
# ---------------------------------------------------------------------------


def _version_tree(tmp_path: Path, label: str, body: str) -> Path:
    root = tmp_path / "versions" / label
    (root / "bin").mkdir(parents=True)
    (root / "AmunEV_Engine_V2.py").write_text(
        ENGINE_SOURCE + body, encoding="utf-8"
    )
    (root / "bin" / "go_live.py").write_text(RUNNER_SOURCE, encoding="utf-8")
    return root


def _version_pair(tmp_path: Path) -> None:
    _version_tree(tmp_path, "amun_2026-01-14", "\n\ndef old_helper():\n    return 1\n")
    _version_tree(tmp_path, "amun_2026-02-03", "\n\ndef new_helper():\n    return 2\n")


def _sports_settings(tmp_path: Path, **overrides) -> Settings:
    from cascade_map.ledger import Settings as _Settings

    base = {
        **SETTING_DEFAULTS,
        "VERSIONS_DIR": "versions",
        "OUT_DIR": "out",
        "LEDGER": "out/metatron_ledger.json",
        "ENV": "",
        "MODE": 2,
        "SCENARIOS": "no-such-scenarios.json",
    }
    base.update(overrides)
    return _Settings.from_mapping(base)


def test_track_mode_two_runs_each_sport_as_its_own_scenario(tmp_path: Path) -> None:
    from cascade_map.ledger import track as ledger_track

    _version_pair(tmp_path)
    calls: list[tuple[str, str]] = []

    def fake_trace(graph_dir, scenarios, scenario, out_root):
        calls.append((Path(graph_dir).name, scenario))
        return 0, f"ran {scenario}"

    settings = _sports_settings(tmp_path, SPORT="basketball")
    result, ledger = ledger_track(settings, root=tmp_path, trace=fake_trace)

    version_ids = {record.id for record in ledger.versions}
    assert len(version_ids) == 2
    assert sorted(calls) == sorted((vid, "basketball") for vid in version_ids)
    assert all("[basketball]" in note for note in result.trace_notes)

    for version_id in version_ids:
        document = json.loads(
            (tmp_path / "out" / version_id / "derived_scenarios.json").read_text(
                encoding="utf-8"
            )
        )
        assert sorted(document["scenarios"]) == ["basketball"]
        assert document["scenarios"]["basketball"]["argv"] == [
            "bin/go_live.py", "--engine", "AmunEV_Engine_V2.py",
            "--sports", "basketball",
        ]


def test_track_all_sports_is_one_scenario_per_sport_per_version(tmp_path: Path) -> None:
    from cascade_map.ledger import track as ledger_track

    _version_pair(tmp_path)
    calls: list[tuple[str, str]] = []
    settings = _sports_settings(tmp_path)
    _result, ledger = ledger_track(
        settings,
        root=tmp_path,
        trace=lambda g, s, name, o: (calls.append((Path(g).name, name)), (0, ""))[1],
    )
    version_ids = sorted({record.id for record in ledger.versions})
    assert sorted(calls) == sorted(
        (vid, sport) for vid in version_ids for sport in SPORTS
    )


def test_the_same_sport_is_compared_across_versions(tmp_path: Path) -> None:
    from cascade_map.ledger import render_track_report, track as ledger_track

    _version_pair(tmp_path)
    settings = _sports_settings(tmp_path, SPORT="basketball")
    _first, ledger = ledger_track(settings, root=tmp_path, trace=lambda *a: (0, ""))
    before_id, after_id = ledger.consecutive_pairs()[0]
    _write_run(tmp_path / "out" / before_id, "run_a", "basketball", {"api::call": 2})
    _write_run(tmp_path / "out" / after_id, "run_b", "basketball",
               {"api::call": 5, "api::extra": 1})

    result, ledger = ledger_track(settings, root=tmp_path, trace=lambda *a: (0, ""))
    delta = ledger.compare(before_id, after_id).runtime_delta
    assert delta["scenario"] == "basketball"
    assert delta["events_before"] == 2
    assert delta["events_after"] == 6
    assert "basketball" in render_track_report(result)


def test_two_different_sports_are_never_compared_against_each_other(
    tmp_path: Path,
) -> None:
    """Different sports are different programs' worth of execution. A
    basketball run compared with a football run would measure the sport."""
    from cascade_map.ledger import render_track_report, track as ledger_track

    _version_pair(tmp_path)
    settings = _sports_settings(tmp_path, SPORT="basketball")
    _first, ledger = ledger_track(settings, root=tmp_path, trace=lambda *a: (0, ""))
    before_id, after_id = ledger.consecutive_pairs()[0]
    _write_run(tmp_path / "out" / before_id, "run_a", "basketball", {"api::call": 2})
    _write_run(tmp_path / "out" / after_id, "run_b", "football", {"api::call": 99})

    result, ledger = ledger_track(settings, root=tmp_path, trace=lambda *a: (0, ""))
    assert ledger.compare(before_id, after_id).runtime_delta == {}
    report = render_track_report(result)
    assert "not measured for basketball" in report
    assert "no change" not in report.lower()


def test_a_version_with_no_run_for_the_sport_says_not_measured_for_it(
    tmp_path: Path,
) -> None:
    from cascade_map.ledger import render_track_report, track as ledger_track

    _version_pair(tmp_path)
    settings = _sports_settings(tmp_path, SPORT="basketball")
    _first, ledger = ledger_track(settings, root=tmp_path, trace=lambda *a: (0, ""))
    before_id, after_id = ledger.consecutive_pairs()[0]
    _write_run(tmp_path / "out" / before_id, "run_a", "basketball", {"api::call": 2})

    result, ledger = ledger_track(settings, root=tmp_path, trace=lambda *a: (0, ""))
    assert ledger.compare(before_id, after_id).runtime_delta == {}
    report = render_track_report(result)
    assert "not measured for basketball" in report
    labels = {record.label for record in ledger.versions}
    assert any(label in report for label in labels)


def test_track_refuses_to_derive_when_the_runner_is_absent_and_runs_nothing(
    tmp_path: Path,
) -> None:
    from cascade_map.ledger import render_track_report, track as ledger_track

    _version_pair(tmp_path)
    for label in ("amun_2026-01-14", "amun_2026-02-03"):
        (tmp_path / "versions" / label / "bin" / "go_live.py").unlink()
    calls: list[tuple] = []
    settings = _sports_settings(tmp_path)
    result, _ledger = ledger_track(
        settings,
        root=tmp_path,
        trace=lambda *a: (calls.append(a), (0, ""))[1],
    )
    assert calls == [], "something was executed despite an unresolvable runner"
    assert any("no scenario could be derived" in note for note in result.trace_notes)
    assert any("go_live.py" in note for note in result.trace_notes)
    assert "MODE 2:" in render_track_report(result)


def _write_run(artifact_dir: Path, run_id: str, scenario: str,
               events: dict[str, int]) -> None:
    """The artifacts a Mode A run leaves behind, hand-built. Nothing runs."""
    run_dir = artifact_dir / "runtime" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(
        json.dumps({"run_id": run_id, "scenario": scenario, "refused": False}),
        encoding="utf-8",
    )
    lines = []
    sequence = 0
    for element_id, count in sorted(events.items()):
        for _ in range(count):
            sequence += 1
            lines.append(json.dumps({
                "event_id": f"evt_{sequence:08d}", "run_id": run_id,
                "kind": "CALL", "element_id": element_id,
                "sequence": sequence, "depth": 1,
            }))
    (run_dir / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
