"""Tests for the engine guard hook.

Two failure modes matter equally here. A guard that misses a real execution is
useless; a guard that blocks `grep -rn ... target_engine/` makes every static
card impossible, because reading the target as text is how they all work. Both
directions are tested.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK_PATH = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "guard_engine.py"
PROJECT = "/home/user/project"


def _load_hook():
    spec = importlib.util.spec_from_file_location("guard_engine", HOOK_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load_hook()


# --------------------------------------------------------------------------
# Execution of target code must be blocked.
# --------------------------------------------------------------------------

BLOCKED_COMMANDS = [
    "python target_engine/run_m5.py",
    "python3 target_engine/run_m5.py --scenario a",
    "python3.12 target_engine/run_m5.py",
    "./target_engine/run.sh",
    "target_engine/bin/launch",
    "cd target_engine && python run_m5.py",
    "cd target_engine; python run_m5.py",
    "cd /home/user/project/target_versions/v3 && pytest",
    "python -c 'import sys; sys.path.insert(0, \"target_engine\"); import engine'",
    "python -m pytest target_engine/tests",
    "PYTHONPATH=target_engine python -c 'import engine'",
    "pytest target_engine/",
    "ipython target_engine/notebook.py",
    "bash target_engine/start.sh",
    "sh -c 'python target_engine/run_m5.py'",
    "source target_engine/env.sh",
    "env PYTHONPATH=target_engine python run.py",
    "nohup python target_engine/run_m5.py &",
    "timeout 30 python target_engine/run_m5.py",
    "find target_engine -name '*.py' -exec python {} ;",
    "pip install -e target_engine/",
    "make -C target_engine run",
    "echo hi && python target_engine/run_m5.py",
    "python target_versions/v2/run_m5.py",
    "uv run python target_engine/run_m5.py",
]


@pytest.mark.parametrize("command", BLOCKED_COMMANDS)
def test_execution_is_blocked(command: str) -> None:
    assert guard.check_bash(command, PROJECT) is not None, command


# --------------------------------------------------------------------------
# The target virtualenv is blocked everywhere outside the harness.
# --------------------------------------------------------------------------

VENV_COMMANDS = [
    ".venv-target/bin/python run.py",
    "source .venv-target/bin/activate",
    ". .venv-target/bin/activate",
    ".venv-target/bin/pip install pandas",
    "/home/user/project/.venv-target/bin/python -c 'print(1)'",
]


@pytest.mark.parametrize("command", VENV_COMMANDS)
def test_target_venv_is_blocked(command: str) -> None:
    assert guard.check_bash(command, PROJECT) is not None, command


# --------------------------------------------------------------------------
# Writing to the target must be blocked.
# --------------------------------------------------------------------------

MUTATING_COMMANDS = [
    "rm -rf target_engine/cache",
    "mv target_engine/a.py target_engine/b.py",
    "cp fix.py target_engine/patch.py",
    "chmod +x target_engine/run.sh",
    "touch target_engine/__init__.py",
    "sed -i 's/a/b/' target_engine/foo.py",
    "echo broken > target_engine/foo.py",
    "cat notes.txt >> target_versions/v1/README",
    "tee target_engine/out.log",
]


@pytest.mark.parametrize("command", MUTATING_COMMANDS)
def test_mutation_is_blocked(command: str) -> None:
    assert guard.check_bash(command, PROJECT) is not None, command


@pytest.mark.parametrize(
    "tool,key",
    [
        ("Write", "file_path"),
        ("Edit", "file_path"),
        ("MultiEdit", "file_path"),
        ("NotebookEdit", "notebook_path"),
    ],
)
def test_write_tools_blocked_on_target(tool: str, key: str) -> None:
    payload = {
        "tool_name": tool,
        "tool_input": {key: "target_engine/strategy/core.py"},
        "cwd": PROJECT,
    }
    assert guard.evaluate(payload) is not None


def test_write_tools_allowed_elsewhere() -> None:
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": "src/cascade_map/ingest.py"},
        "cwd": PROJECT,
    }
    assert guard.evaluate(payload) is None


# --------------------------------------------------------------------------
# Reading the target must keep working. These are how every static card runs.
# --------------------------------------------------------------------------

ALLOWED_COMMANDS = [
    "grep -rn 'def main' target_engine/",
    "grep -rn 'python' target_engine/",
    "rg --files target_engine",
    "sed -n '1,80p' target_engine/run_m5.py",
    "cat target_engine/config/wiring.json",
    "head -c 2000 target_engine/blobs.py",
    "find target_engine -name '*.py' | wc -l",
    "ls -la target_engine",
    "wc -l target_engine/run_m5.py",
    "sha256sum target_engine/run_m5.py",
    "diff target_versions/v1/a.py target_versions/v2/a.py",
    "stat target_engine",
    "file target_engine/blobs.py",
    "git status",
    "python -m pytest tests/",
    "python -c 'print(1)'",
    "pytest tests/test_ingest.py -q",
    "pip install networkx",
    "python src/cascade_map/cli.py analyze",
    "echo 'target_engine is read-only' >> docs/STATUS.md",
    "grep -rn target_engine docs/",
    "mkdir -p out/graph",
]


@pytest.mark.parametrize("command", ALLOWED_COMMANDS)
def test_reads_and_normal_work_are_allowed(command: str) -> None:
    assert guard.check_bash(command, PROJECT) is None, command


# --------------------------------------------------------------------------
# The one sanctioned execution path stays open.
# --------------------------------------------------------------------------

HARNESS_COMMANDS = [
    "cascade-map trace --scenario baseline",
    "cascade-map trace",
    "python -m cascade_map trace --scenario baseline",
]


@pytest.mark.parametrize("command", HARNESS_COMMANDS)
def test_harness_command_is_allowed(command: str) -> None:
    assert guard.check_bash(command, PROJECT) is None, command


def test_cascade_map_is_not_a_blanket_exemption() -> None:
    """Only `trace` runs the target. Other subcommands get no free pass."""
    assert guard.check_bash("cascade-map python target_engine/run.py", PROJECT) is not None


# --------------------------------------------------------------------------
# The hook binary itself: contract with Claude Code.
# --------------------------------------------------------------------------


def _run_hook(payload: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_hook_emits_deny_json() -> None:
    result = _run_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "python target_engine/run_m5.py"},
            "cwd": PROJECT,
        }
    )
    assert result.returncode == 0
    decision = json.loads(result.stdout)["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "deny"
    assert "guard_engine.py" in decision["permissionDecisionReason"]


def test_hook_stays_silent_when_allowed() -> None:
    result = _run_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "grep -rn 'def main' target_engine/"},
            "cwd": PROJECT,
        }
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_hook_ignores_unrelated_tools() -> None:
    result = _run_hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "target_engine/run_m5.py"},
            "cwd": PROJECT,
        }
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_malformed_payload_fails_closed_when_target_mentioned() -> None:
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input="{not json at all, target_engine",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


# --------------------------------------------------------------------------
# Heredoc bodies are data, not commands.
#
# Regression: writing documentation that merely *names* the target venv or the
# target directories was denied, because every line of the heredoc body was
# being parsed as a shell segment. This project's own docs trip that constantly.
# --------------------------------------------------------------------------

HEREDOC_ALLOWED = [
    "cat > docs/design/TARGET_PROFILE.md <<'EOF'\n"
    "| Mode A interpreter | `.venv-target` |\n"
    "| Target engine | `target_engine/` |\n"
    "EOF",
    "cat > docs/STATUS.md <<'EOF'\n"
    "Never run python target_engine/run_m5.py outside the harness.\n"
    "EOF",
    "cat <<EOF > notes.txt\ntarget_versions/v1 is read-only\nEOF",
]


@pytest.mark.parametrize("command", HEREDOC_ALLOWED)
def test_heredoc_body_is_treated_as_data(command: str) -> None:
    assert guard.check_bash(command, PROJECT) is None, command


HEREDOC_BLOCKED = [
    # The redirect target still matters, even with a heredoc.
    "cat > target_engine/patch.py <<'EOF'\nprint(1)\nEOF",
    # A real command after the heredoc terminator is still a real command.
    "cat > notes.txt <<'EOF'\nharmless\nEOF\npython target_engine/run_m5.py",
]


@pytest.mark.parametrize("command", HEREDOC_BLOCKED)
def test_heredoc_does_not_hide_real_commands(command: str) -> None:
    assert guard.check_bash(command, PROJECT) is not None, command


def test_left_shift_is_not_mistaken_for_a_heredoc() -> None:
    """`<<` inside a quoted string is arithmetic, not a heredoc delimiter."""
    assert guard.check_bash("python -c 'x = 1 << shift; print(x)'", PROJECT) is None


def test_malformed_payload_fails_open_otherwise() -> None:
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input="{not json at all",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


# -- the harness path survives a rename ---------------------------------------
# HARNESS_HEADS is an allowlist, so a name missing from it fails SAFE: the
# command is judged by the ordinary rules and blocked if it touches the target.
# Safe, but it takes Mode A away from the owner entirely -- which is what
# happened when the console script became `metatron` while the guard still
# said `cascade-map`. These pin every shipped spelling of the sanctioned path.


@pytest.mark.parametrize(
    "command",
    [
        "metatron trace out --mode 2",
        "cascade-map trace out --mode 2",
        "python3 Metatron_Engine_Prototype_v1.py trace out --mode 2",
        "python3 dist/Metatron_Engine_Prototype_v1.py trace out",
        "python3 -m cascade_map trace out",
    ],
)
def test_every_shipped_spelling_of_the_harness_path_is_allowed(command: str) -> None:
    assert guard.check_bash(command, PROJECT) is None, (
        f"{command!r} is the sanctioned Mode A path and must not be blocked; "
        f"blocking it leaves the owner no way to run Mode A at all."
    )


@pytest.mark.parametrize(
    "command",
    [
        # A tool-shaped name is not a licence to run the target.
        "python3 target_engine/run_m5.py",
        "python3 metatron.py target_engine/run_m5.py",
        # `trace` appearing somewhere is not enough on its own.
        "python3 target_engine/trace_helper.py",
        "source .venv-target/bin/activate && python run.py",
    ],
)
def test_the_rename_opened_no_hole(command: str) -> None:
    assert guard.check_bash(command, PROJECT) is not None, (
        f"{command!r} executes target code and must stay blocked."
    )
