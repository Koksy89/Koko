"""Tests for card 11, the safe execution harness.

Constraint 7: Mode A must be incapable of real-world side effects. Every test
here either proves a control holds against an adversarial program, or proves
the harness refuses to start rather than run with a guarantee it cannot make.

**Why most of these tests run the harness in a subprocess.** Enforcement in
``cascade_map.harness.sandbox`` is a plain module-level flag that, once a run
activates it, deliberately never clears itself (see that module's docstring
for the two escapes that happened when it tried to). That is correct for a
real ``cascade-map trace`` invocation -- one process, exiting once its run
record is written -- and is exactly the wrong thing for a single pytest
process that runs the harness dozens of times to prove those escapes stay
closed: an in-process test would leave its sandbox active for the rest of the
session, including for pytest's own housekeeping. So every test that actually
activates the sandbox (``activate()`` directly, or a ``Harness.start()`` call
that reaches the point of constructing one) runs the relevant code in a
genuinely separate interpreter via ``_probe`` or ``_run_harness``, and reads
back what happened from that subprocess's stdout or its ``run.json``. Only
the tests that provably never touch ``activate()`` at all -- the refusal
paths that return before a sandbox is built, and the pure signature check --
run in-process.

``tests/fixtures/mode_a/adv_*`` exist on disk now (card 8's rebuild) but are
still docstring-only placeholders with no adversarial code, so the adversarial
programs below are still built as source text here rather than read from the
corpus. See the build report.

Nothing here ever imports, execs or reads ``target_engine/`` or
``target_versions/`` -- every target root used below is a *copy* of a fixture
directory, made under ``tmp_path``, or a ``tmp_path`` the test itself wrote.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cascade_map.contracts import canonical_dumps
from cascade_map.harness import Harness, RunConfig, ScenarioSpec
from cascade_map.harness.hashing import compute_graph_hash, compute_target_hashes

FIXTURES_ROOT = Path(__file__).parent / "fixtures"
FIXTURES_MODE_A = FIXTURES_ROOT / "mode_a"
SRC_PATH = str(Path(__file__).resolve().parent.parent / "src")


# ---------------------------------------------------------------------------
# The corpus is never a target. This module's own proof that held.
# ---------------------------------------------------------------------------


def _snapshot_fixtures_tree() -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(FIXTURES_ROOT.rglob("*")):
        if path.is_file():
            snapshot[path.relative_to(FIXTURES_ROOT).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return snapshot


@pytest.fixture(scope="module", autouse=True)
def _fixtures_untouched():
    """No test in this module may write anything into ``tests/fixtures/`` --
    not output, not a temp file, and not a bytecode cache from executing a
    fixture directly. Belt (this snapshot) and suspenders
    (``sys.dont_write_bytecode`` for the whole module) around the one thing
    this card exists to prevent.
    """
    original_dont_write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    before = _snapshot_fixtures_tree()
    yield
    sys.dont_write_bytecode = original_dont_write_bytecode
    after = _snapshot_fixtures_tree()
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(k for k in (set(after) & set(before)) if after[k] != before[k])
    assert not added and not removed and not changed, (
        "tests/fixtures/ changed while this module's tests ran -- "
        f"added={added} removed={removed} changed={changed}. Every other "
        "card is measured against this corpus; nothing here may write to it."
    )


def _copied_fixture(tmp_path: Path, relative: str) -> Path:
    """A read-only-by-construction copy of a real fixture under ``tmp_path``.

    Executing the corpus directly risks writing into it (a bytecode cache is
    exactly how this went wrong once already); copying first makes that
    structurally impossible. Returns the copy of *relative* itself, e.g.
    ``.../tmp_path/fixture_copy/run_linear`` for ``relative="run_linear"`` --
    the caller points ``target_root`` at its parent so the copy still imports
    under its original name.
    """
    dest = tmp_path / "fixture_copy" / relative
    shutil.copytree(FIXTURES_MODE_A / relative, dest)
    return dest


# ---------------------------------------------------------------------------
# Subprocess helpers -- see the module docstring for why these exist.
# ---------------------------------------------------------------------------


def _run_python(script: str, timeout: float = 30.0, env: dict[str, str] | None = None):
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _probe(
    tmp_path: Path,
    body: str,
    declared_process_names: frozenset[str] = frozenset(),
    label: str = "probe",
    timeout: float = 30.0,
) -> dict:
    """Run *body* against a fresh ``SandboxContext``/``activate`` in a
    genuinely separate interpreter and return what it recorded.

    *body* is Python source; ``ctx`` is already bound when it runs.
    """
    sandbox_root = tmp_path / f"sandbox_{label}"
    sandbox_root.mkdir(parents=True, exist_ok=True)
    script = (
        "import sys, json\n"
        f"sys.path.insert(0, {SRC_PATH!r})\n"
        "from cascade_map.harness.sandbox import SandboxContext, activate\n"
        f"ctx = SandboxContext(sandbox_root={str(sandbox_root)!r}, "
        f"declared_process_names=frozenset({sorted(declared_process_names)!r}))\n"
        + body
        + "\nprint(json.dumps({"
        "'blocked': [{'kind': b.kind, 'detail': b.detail} for b in ctx.blocked],"
        "'reads_outside': len(ctx.reads_outside_sandbox),"
        "}))\n"
    )
    result = _run_python(script, timeout=timeout)
    assert result.returncode == 0, (
        f"probe subprocess failed (exit {result.returncode}):\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines, f"probe produced no JSON output:\nSTDERR:\n{result.stderr}"
    return json.loads(lines[-1])


def _blocked_kinds(result: dict) -> list[str]:
    return [b["kind"] for b in result["blocked"]]


def _run_harness(
    tmp_path: Path,
    *,
    source: str,
    module: str = "target_mod",
    scenario: str = "s",
    function: str = "",
    declared_process_names: frozenset[str] = frozenset(),
    env_passthrough: frozenset[str] = frozenset(),
    client_stub_source: str = "",
    observer_source: str = "",
    target_root: Path | None = None,
    sandbox_root: Path | None = None,
    graph_hash: str | None = None,
    env: dict[str, str] | None = None,
    label: str = "run",
    timeout: float = 30.0,
) -> tuple[dict, Path, Path]:
    """Run ``Harness.start()`` against a target module built from *source*,
    in a genuinely separate interpreter, and return
    ``(run_record_dict, sandbox_root, mode_b_out_dir)``.
    """
    if target_root is None:
        target_root = tmp_path / f"target_{label}"
        target_root.mkdir(parents=True, exist_ok=True)
    if source:
        (target_root / f"{module}.py").write_text(source)

    out_dir = tmp_path / f"out_{label}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "elements.jsonl").write_text("")
    if sandbox_root is None:
        sandbox_root = tmp_path / f"sandbox_{label}"

    graph_hash_line = (
        f"graph_hash = {graph_hash!r}\n"
        if graph_hash is not None
        else "graph_hash = compute_graph_hash(compute_target_hashes(target_root))\n"
    )

    script = (
        "import sys\n"
        f"sys.path.insert(0, {SRC_PATH!r})\n"
        "from pathlib import Path\n"
        "from cascade_map.harness import Harness, RunConfig, ScenarioSpec\n"
        "from cascade_map.harness.hashing import compute_graph_hash, compute_target_hashes\n"
        f"target_root = Path({str(target_root)!r})\n"
        f"out_dir = Path({str(out_dir)!r})\n"
        f"sandbox_root = Path({str(sandbox_root)!r})\n"
        + client_stub_source
        + observer_source
        + "\nconfig = RunConfig(\n"
        "    target_root=target_root,\n"
        "    mode_b_out_dir=out_dir,\n"
        "    sandbox_root=sandbox_root,\n"
        f"    scenarios={{{scenario!r}: ScenarioSpec(name={scenario!r}, "
        f"module={module!r}, function={function!r})}},\n"
        f"    declared_process_names=frozenset({sorted(declared_process_names)!r}),\n"
        f"    env_passthrough=frozenset({sorted(env_passthrough)!r}),\n"
        + ("    client_stubs=CLIENT_STUBS,\n" if client_stub_source else "")
        + ")\n"
        + graph_hash_line
        + (
            f"Harness(config).start({scenario!r}, graph_hash, observer=OBSERVER)\n"
            if observer_source
            else f"Harness(config).start({scenario!r}, graph_hash)\n"
        )
    )
    run_env = dict(os.environ) if env is None else env
    result = _run_python(script, timeout=timeout, env=run_env)
    assert result.returncode == 0, (
        f"harness subprocess failed (exit {result.returncode}):\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    run_json_files = list((out_dir / "runtime").glob("*/run.json"))
    assert len(run_json_files) == 1, f"expected exactly one run.json, found {run_json_files}"
    record = json.loads(run_json_files[0].read_text())
    return record, sandbox_root, out_dir


# ---------------------------------------------------------------------------
# In-process helpers -- only for tests that provably never call activate().
# ---------------------------------------------------------------------------


def _mode_b_graph(tmp_path: Path, name: str = "out") -> Path:
    out_dir = tmp_path / name
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "elements.jsonl").write_text("")
    return out_dir


def _write_target(tmp_path: Path, module_name: str, source: str) -> Path:
    target_root = tmp_path / f"target_{module_name}"
    target_root.mkdir(parents=True, exist_ok=True)
    (target_root / f"{module_name}.py").write_text(source)
    return target_root


def _graph_hash_for(target_root: Path) -> str:
    return compute_graph_hash(compute_target_hashes(target_root))


def _config(tmp_path: Path, target_root: Path, scenario: str, module: str) -> RunConfig:
    out_dir = _mode_b_graph(tmp_path)
    return RunConfig(
        target_root=target_root,
        mode_b_out_dir=out_dir,
        sandbox_root=tmp_path / "sandbox",
        scenarios={scenario: ScenarioSpec(name=scenario, module=module)},
    )


def _run(config: RunConfig, scenario: str, graph_hash: str | None = None):
    if graph_hash is None:
        graph_hash = _graph_hash_for(config.target_root)
    return Harness(config).start(scenario, graph_hash)


# ---------------------------------------------------------------------------
# run_linear -- a legitimate scenario completes cleanly, end to end
# ---------------------------------------------------------------------------


def test_run_linear_completes_with_every_control_active(tmp_path: Path) -> None:
    copied_run_linear = _copied_fixture(tmp_path, "run_linear")
    target_root = copied_run_linear.parent

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "elements.jsonl").write_text("")
    sandbox_root = tmp_path / "sandbox"
    graph_hash = _graph_hash_for(target_root)

    script = (
        "import sys\n"
        f"sys.path.insert(0, {SRC_PATH!r})\n"
        "from pathlib import Path\n"
        "from cascade_map.harness import Harness, RunConfig, ScenarioSpec\n"
        f"config = RunConfig(\n"
        f"    target_root=Path({str(target_root)!r}),\n"
        f"    mode_b_out_dir=Path({str(out_dir)!r}),\n"
        f"    sandbox_root=Path({str(sandbox_root)!r}),\n"
        "    scenarios={'run_linear': ScenarioSpec(name='run_linear', "
        "module='run_linear', function='main')},\n"
        ")\n"
        f"Harness(config).start('run_linear', {graph_hash!r})\n"
    )
    result = _run_python(script)
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"

    run_json_files = list((out_dir / "runtime").glob("*/run.json"))
    assert len(run_json_files) == 1
    record = json.loads(run_json_files[0].read_text())

    assert record["refused"] is False
    assert record["refusal_reason"] == ""
    # Only informational reads of stdlib/interpreter internals may appear --
    # nothing the scenario itself did was blocked or written outside it.
    assert _blocked_kinds(record) == ["filesystem_read_outside_sandbox"] * len(
        record["blocked"]
    )
    assert record["controls_active"] == {
        "network": True,
        "filesystem": True,
        "process": True,
        "environment": True,
        "external_clients": True,
    }
    assert record["run_id"].startswith("run_")
    assert record["sandbox_dir"] == str(sandbox_root)

    # No write landed even in the *copy* -- and the real corpus was never
    # named as a target_root at all, so it could not have been touched.
    assert not (copied_run_linear / "__pycache__").exists()
    assert not (FIXTURES_MODE_A / "run_linear" / "__pycache__").exists()


# ---------------------------------------------------------------------------
# Refusals -- constraint 7: refusing to start is the correct outcome.
# None of these reach the point of constructing a sandbox, so they run
# in-process: verified case by case in the harness build report.
# ---------------------------------------------------------------------------


def test_refuses_when_no_mode_b_graph_exists(tmp_path: Path) -> None:
    target_root = _write_target(tmp_path, "trivial", "x = 1\n")
    out_dir = tmp_path / "out_without_a_graph"
    out_dir.mkdir()
    config = RunConfig(
        target_root=target_root,
        mode_b_out_dir=out_dir,
        sandbox_root=tmp_path / "sandbox",
        scenarios={"s": ScenarioSpec(name="s", module="trivial")},
    )
    record = _run(config, "s")

    assert record.refused is True
    assert "no completed Mode B graph" in record.refusal_reason
    assert record.blocked == ()
    assert all(v is False for v in record.controls_active.values())


def test_refuses_when_graph_is_stale(tmp_path: Path) -> None:
    """adv_stale_graph: a Mode B graph not matching current target hashes."""
    target_root = _write_target(tmp_path, "trivial", "x = 1\n")
    config = _config(tmp_path, target_root, "s", "trivial")

    record = _run(config, "s", graph_hash="0" * 64)  # matches nothing

    assert record.refused is True
    assert "stale graph" in record.refusal_reason
    assert record.blocked == ()


def test_refuses_when_scenario_not_declared(tmp_path: Path) -> None:
    target_root = _write_target(tmp_path, "trivial", "x = 1\n")
    config = _config(tmp_path, target_root, "declared_scenario", "trivial")

    record = _run(config, "not_the_declared_one")

    assert record.refused is True
    assert "not declared" in record.refusal_reason


def test_refuses_when_audit_hook_cannot_be_verified(tmp_path: Path, monkeypatch) -> None:
    """adv_refuse_start: with a control unavailable, the run refuses and
    names the guarantee -- simulated by making hook installation fail, since
    a genuinely broken interpreter is not something a test can construct.
    ``install_hook`` raises before ``activate`` ever touches the sandbox
    pointer, so this stays safe to run in-process.
    """
    target_root = _write_target(tmp_path, "trivial", "x = 1\n")
    config = _config(tmp_path, target_root, "s", "trivial")

    import cascade_map.harness.sandbox as sandbox_module

    def _broken_install_hook() -> None:
        raise RuntimeError("simulated: audit hook installation failed")

    monkeypatch.setattr(sandbox_module, "install_hook", _broken_install_hook)

    record = _run(config, "s")

    assert record.refused is True
    assert "audit hook" in record.refusal_reason
    assert record.controls_active["network"] is False
    assert record.controls_active["filesystem"] is False
    assert record.controls_active["process"] is False


def test_no_force_flag_exists_on_start() -> None:
    """There is no override. ``observer`` is the one contract-mandated
    addition (card 12's seam, ``RunObserver``); anything beyond these four
    parameters is a regression worth catching on its own."""
    params = list(inspect.signature(Harness.start).parameters)
    assert params == ["self", "scenario", "graph_hash", "observer"]
    observer_param = inspect.signature(Harness.start).parameters["observer"]
    assert observer_param.default is None


# ---------------------------------------------------------------------------
# unguaranteed -- constraint 7's honest disclosure of what cannot be closed
#
# An empty ``unguaranteed`` is a claim of complete coverage. This harness
# cannot make that claim: the direct ``_posixsubprocess.fork_exec`` escape
# and "a declared process runs unaudited once started" are both demonstrated
# elsewhere in this file (see the escapes section below), and both must
# travel with *every* run record the owner actually reads -- proven once in
# an isolated probe is not the same as disclosed in the artifact.
# ---------------------------------------------------------------------------


def test_unguaranteed_lists_both_known_gaps_on_a_successful_run(tmp_path: Path) -> None:
    record, _, _ = _run_harness(
        tmp_path,
        source="x = 1\n",
        module="trivial_unguaranteed",
        label="unguaranteed_success",
    )
    assert record["refused"] is False
    assert len(record["unguaranteed"]) == 2
    assert any(
        "low-level process-spawn primitive" in u for u in record["unguaranteed"]
    ), record["unguaranteed"]
    assert any(
        "explicitly declared and permitted to spawn" in u for u in record["unguaranteed"]
    ), record["unguaranteed"]


def test_unguaranteed_lists_both_known_gaps_on_a_refused_run(tmp_path: Path) -> None:
    """A refused run never executes anything, but the owner still needs to
    know what this harness could not have guaranteed had it proceeded --
    refusing is not an excuse to omit the disclosure."""
    target_root = _write_target(tmp_path, "trivial", "x = 1\n")
    out_dir = tmp_path / "out_without_a_graph"
    out_dir.mkdir()
    config = RunConfig(
        target_root=target_root,
        mode_b_out_dir=out_dir,
        sandbox_root=tmp_path / "sandbox",
        scenarios={"s": ScenarioSpec(name="s", module="trivial")},
    )
    record = _run(config, "s")

    assert record.refused is True
    assert len(record.unguaranteed) == 2
    assert any("low-level process-spawn primitive" in u for u in record.unguaranteed)
    assert any("explicitly declared and permitted to spawn" in u for u in record.unguaranteed)


# ---------------------------------------------------------------------------
# adv_network / adv_dns -- outbound sockets and DNS blocked at the socket layer
# ---------------------------------------------------------------------------


def test_adv_network_outbound_connect_blocked(tmp_path: Path) -> None:
    result = _probe(
        tmp_path,
        "import socket\n"
        "with activate(ctx):\n"
        "    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "    s.settimeout(0.01)\n"
        "    try:\n"
        "        s.connect(('93.184.216.34', 80))\n"
        "    except Exception:\n"
        "        pass\n"
        "    finally:\n"
        "        s.close()\n",
    )
    assert _blocked_kinds(result) == ["network"]
    assert "socket.connect" in result["blocked"][0]["detail"]


def test_adv_dns_lookup_blocked(tmp_path: Path) -> None:
    result = _probe(
        tmp_path,
        "import socket\n"
        "with activate(ctx):\n"
        "    try:\n"
        "        socket.gethostbyname('example.com')\n"
        "    except Exception:\n"
        "        pass\n",
    )
    assert _blocked_kinds(result) == ["network"]
    assert "gethostbyname" in result["blocked"][0]["detail"]


def test_adv_undeclared_client_is_a_hard_stop(tmp_path: Path) -> None:
    """adv_undeclared_client: reaching an external system with no declared
    stub goes through the real socket layer and is blocked like anything
    else undeclared -- not a special-cased pass-through."""
    result = _probe(
        tmp_path,
        "import socket\n"
        "with activate(ctx):\n"
        "    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "    s.settimeout(0.01)\n"
        "    try:\n"
        "        s.connect(('203.0.113.5', 5432))  # pretend Postgres, never declared\n"
        "    except Exception:\n"
        "        pass\n"
        "    finally:\n"
        "        s.close()\n",
    )
    assert _blocked_kinds(result) == ["network"]


def test_declared_client_stub_bypasses_network_entirely(tmp_path: Path) -> None:
    """A declared client never touches the socket layer at all: the stub
    satisfies the import first."""
    client_stub_source = (
        "from types import ModuleType\n"
        "def _make_fake_broker():\n"
        "    mod = ModuleType('fake_broker')\n"
        "    mod.send = lambda payload: f'stubbed-ack:{payload}'\n"
        "    return mod\n"
        "CLIENT_STUBS = {'fake_broker': _make_fake_broker}\n"
    )
    record, sandbox_root, _ = _run_harness(
        tmp_path,
        source=(
            "import fake_broker\n"
            "result = fake_broker.send('order-1')\n"
            "with open('stub_result.txt', 'w') as f:\n"
            "    f.write(result)\n"
        ),
        module="uses_broker",
        function="",
        client_stub_source=client_stub_source,
        label="stub",
    )
    assert record["refused"] is False
    assert "network" not in _blocked_kinds(record)
    assert (sandbox_root / "stub_result.txt").read_text() == "stubbed-ack:order-1"


# ---------------------------------------------------------------------------
# adv_write_escape -- absolute path, `..`, and a symlink
# ---------------------------------------------------------------------------


def test_adv_write_escape_absolute_path_blocked(tmp_path: Path) -> None:
    outside = tmp_path / "outside_absolute.txt"
    record, _, _ = _run_harness(
        tmp_path,
        source=f"with open({str(outside)!r}, 'w') as f:\n    f.write('escaped')\n",
        module="adv_write_absolute",
        label="abs",
    )
    assert "filesystem_write" in _blocked_kinds(record)
    assert not outside.exists()


def test_adv_write_escape_dotdot_blocked(tmp_path: Path) -> None:
    record, sandbox_root, _ = _run_harness(
        tmp_path,
        source="with open('../escaped_dotdot.txt', 'w') as f:\n    f.write('escaped')\n",
        module="adv_write_dotdot",
        label="dotdot",
    )
    assert "filesystem_write" in _blocked_kinds(record)
    assert not (sandbox_root.parent / "escaped_dotdot.txt").exists()


def test_adv_write_escape_symlink_blocked(tmp_path: Path) -> None:
    outside_dir = tmp_path / "outside_via_symlink"
    outside_dir.mkdir()
    sandbox_root = tmp_path / "sandbox_symlink"
    sandbox_root.mkdir()
    (sandbox_root / "escape_link").symlink_to(outside_dir, target_is_directory=True)

    record, _, _ = _run_harness(
        tmp_path,
        source="with open('escape_link/escaped.txt', 'w') as f:\n    f.write('escaped')\n",
        module="adv_write_symlink",
        sandbox_root=sandbox_root,
        label="symlink",
    )
    assert "filesystem_write" in _blocked_kinds(record)
    assert not (outside_dir / "escaped.txt").exists()


def test_write_inside_sandbox_succeeds_and_is_not_blocked(tmp_path: Path) -> None:
    record, sandbox_root, _ = _run_harness(
        tmp_path,
        source="with open('inside.txt', 'w') as f:\n    f.write('fine')\n",
        module="legit_write",
        label="legit",
    )
    # No write was blocked (the only permitted entries left are reads of the
    # interpreter's own stdlib/bytecode cache, outside the sandbox by
    # necessity and recorded, not blocked).
    assert "filesystem_write" not in _blocked_kinds(record)
    assert (sandbox_root / "inside.txt").read_text() == "fine"


def test_extra_write_roots_do_not_leak_across_contexts(tmp_path: Path) -> None:
    """``extra_write_roots`` is the one deliberate widening of the write
    control (a run's own Mode B output directory, alongside its sandbox),
    and it is scoped per context, not global -- see sandbox.py's module
    docstring. Activating context A must never make context B's roots (its
    sandbox *or* its extra roots) reachable, and vice versa.
    """
    root_a = tmp_path / "sandbox_a"
    root_a.mkdir()
    extra_a = tmp_path / "extra_a"
    extra_a.mkdir()
    root_b = tmp_path / "sandbox_b"
    root_b.mkdir()
    extra_b = tmp_path / "extra_b"
    extra_b.mkdir()

    def _try_write(path: Path) -> str:
        return (
            "    try:\n"
            f"        open({str(path)!r}, 'w').close()\n"
            "    except Exception:\n"
            "        pass\n"
        )

    script = (
        "import sys, json\n"
        f"sys.path.insert(0, {SRC_PATH!r})\n"
        "from cascade_map.harness.sandbox import SandboxContext, activate\n"
        f"ctx_a = SandboxContext(sandbox_root={str(root_a)!r}, "
        f"declared_process_names=frozenset(), extra_write_roots=({str(extra_a)!r},))\n"
        f"ctx_b = SandboxContext(sandbox_root={str(root_b)!r}, "
        f"declared_process_names=frozenset(), extra_write_roots=({str(extra_b)!r},))\n"
        "with activate(ctx_a):\n"
        + _try_write(root_b / "leak.txt")
        + _try_write(extra_b / "leak.txt")
        + "with activate(ctx_b):\n"
        + _try_write(root_a / "leak.txt")
        + _try_write(extra_a / "leak.txt")
        + "print(json.dumps({'a_blocked': len(ctx_a.blocked), 'b_blocked': len(ctx_b.blocked)}))\n"
    )
    result = _run_python(script)
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    payload = json.loads(lines[-1])

    assert payload["a_blocked"] == 2, "A's writes into B's roots must both be blocked"
    assert payload["b_blocked"] == 2, "B's writes into A's roots must both be blocked"
    assert not (root_b / "leak.txt").exists()
    assert not (extra_b / "leak.txt").exists()
    assert not (root_a / "leak.txt").exists()
    assert not (extra_a / "leak.txt").exists()


# ---------------------------------------------------------------------------
# adv_subprocess -- subprocess, os.system, os.fork
# ---------------------------------------------------------------------------


def test_adv_subprocess_os_system_and_fork_all_blocked(tmp_path: Path) -> None:
    record, _, _ = _run_harness(
        tmp_path,
        source=(
            "import os, subprocess\n"
            "try:\n"
            "    subprocess.run(['echo', 'hi'])\n"
            "except Exception:\n"
            "    pass\n"
            "try:\n"
            "    os.system('echo hi')\n"
            "except Exception:\n"
            "    pass\n"
            "try:\n"
            "    os.fork()\n"
            "except Exception:\n"
            "    pass\n"
        ),
        module="adv_subprocess",
        label="subproc",
    )
    process_blocks = [b for b in record["blocked"] if b["kind"] == "process"]
    events = {b["detail"].split()[0] for b in process_blocks}
    assert {"subprocess.Popen", "os.system", "os.fork"} <= events


def test_declared_process_is_allowed_to_spawn(tmp_path: Path) -> None:
    python = os.path.basename(sys.executable)
    record, sandbox_root, _ = _run_harness(
        tmp_path,
        source=(
            "import subprocess, sys\n"
            "subprocess.run([sys.executable, '-c', 'pass'], check=True)\n"
            "with open('ran.txt', 'w') as f:\n"
            "    f.write('ok')\n"
        ),
        module="declared_subprocess",
        declared_process_names=frozenset({python}),
        label="declared_proc",
    )
    assert "process" not in _blocked_kinds(record)
    assert (sandbox_root / "ran.txt").read_text() == "ok"


# ---------------------------------------------------------------------------
# Environment -- secrets are not passed through unless declared
# ---------------------------------------------------------------------------


def test_undeclared_env_var_not_passed_through(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CASCADE_MAP_TEST_SECRET_TOKEN", "super-secret-value")
    record, sandbox_root, _ = _run_harness(
        tmp_path,
        source=(
            "import os\n"
            "value = os.environ.get('CASCADE_MAP_TEST_SECRET_TOKEN', '<absent>')\n"
            "with open('env_result.txt', 'w') as f:\n"
            "    f.write(value)\n"
        ),
        module="env_check",
        label="env_secret",
    )
    assert record["controls_active"]["environment"] is True
    assert (sandbox_root / "env_result.txt").read_text() == "<absent>"
    # The parent test process keeps its own environment untouched.
    assert os.environ["CASCADE_MAP_TEST_SECRET_TOKEN"] == "super-secret-value"


def test_declared_env_var_is_passed_through(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CASCADE_MAP_TEST_PUBLIC_VAR", "hello")
    record, sandbox_root, _ = _run_harness(
        tmp_path,
        source=(
            "import os\n"
            "value = os.environ.get('CASCADE_MAP_TEST_PUBLIC_VAR', '<absent>')\n"
            "with open('env_result.txt', 'w') as f:\n"
            "    f.write(value)\n"
        ),
        module="env_check_declared",
        env_passthrough=frozenset({"CASCADE_MAP_TEST_PUBLIC_VAR"}),
        label="env_public",
    )
    assert (sandbox_root / "env_result.txt").read_text() == "hello"


# ---------------------------------------------------------------------------
# Reads outside the sandbox: allowed, but recorded
# ---------------------------------------------------------------------------


def test_reads_outside_sandbox_are_allowed_and_recorded(tmp_path: Path) -> None:
    outside_file = tmp_path / "readable_outside.txt"
    outside_file.write_text("read me")
    record, sandbox_root, _ = _run_harness(
        tmp_path,
        source=(
            f"with open({str(outside_file)!r}) as f:\n"
            "    data = f.read()\n"
            "with open('copy.txt', 'w') as f:\n"
            "    f.write(data)\n"
        ),
        module="reads_outside",
        label="reads_outside",
    )
    assert (sandbox_root / "copy.txt").read_text() == "read me"
    read_kinds = [b for b in record["blocked"] if b["kind"] == "filesystem_read_outside_sandbox"]
    assert any(str(outside_file) in b["detail"] for b in read_kinds)
    assert "filesystem_write" not in _blocked_kinds(record)


# ---------------------------------------------------------------------------
# Determinism -- replaying the same recorded run is byte-identical
# ---------------------------------------------------------------------------


def test_replay_is_byte_identical(tmp_path: Path) -> None:
    source = (
        "import socket\n"
        "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "s.settimeout(0.01)\n"
        "try:\n"
        "    s.connect(('93.184.216.34', 80))\n"
        "except Exception:\n"
        "    pass\n"
        "finally:\n"
        "    s.close()\n"
        "with open('marker.txt', 'w') as f:\n"
        "    f.write('ran')\n"
    )
    target_root = tmp_path / "target_shared"
    target_root.mkdir()
    (target_root / "adv_network_replay.py").write_text(source)
    graph_hash = _graph_hash_for(target_root)

    record_one, _, out_one = _run_harness(
        tmp_path,
        source="",
        module="adv_network_replay",
        target_root=target_root,
        graph_hash=graph_hash,
        label="replay_one",
    )
    record_two, _, out_two = _run_harness(
        tmp_path,
        source="",
        module="adv_network_replay",
        target_root=target_root,
        graph_hash=graph_hash,
        label="replay_two",
    )

    assert record_one["run_id"] == record_two["run_id"]
    # Both records include their own (different) sandbox_dir, which is
    # expected -- exclude it and compare everything else byte for byte.
    one = {k: v for k, v in record_one.items() if k != "sandbox_dir"}
    two = {k: v for k, v in record_two.items() if k != "sandbox_dir"}
    assert canonical_dumps(one) == canonical_dumps(two)


def test_run_record_written_to_disk_is_replayable(tmp_path: Path) -> None:
    target_root = tmp_path / "target_shared"
    target_root.mkdir()
    (target_root / "trivial_replay.py").write_text("x = 1\n")
    sandbox_root = tmp_path / "sandbox_fixed"
    graph_hash = _graph_hash_for(target_root)

    record_one, _, out_dir = _run_harness(
        tmp_path,
        source="",
        module="trivial_replay",
        target_root=target_root,
        sandbox_root=sandbox_root,
        graph_hash=graph_hash,
        label="disk_replay",
    )
    run_json_path = out_dir / "runtime" / record_one["run_id"] / "run.json"
    first_bytes = run_json_path.read_bytes()

    # Re-run the identical scenario against the identical config: the run ID
    # is deterministic, so this overwrites the same path -- with the same
    # bytes, which is exactly the property under test.
    _run_harness(
        tmp_path,
        source="",
        module="trivial_replay",
        target_root=target_root,
        sandbox_root=sandbox_root,
        graph_hash=graph_hash,
        label="disk_replay",
    )
    second_bytes = run_json_path.read_bytes()

    assert first_bytes == second_bytes


# ---------------------------------------------------------------------------
# Threads and processes: enforcement must not be scoped to one logical flow
#
# Round one used a ``ContextVar``, which a plain ``threading.Thread`` does
# not inherit -- a thread that opened a socket was never blocked, silently.
# Round two kept a global but tried to track "which threads exist" to decide
# when to clear it; a ``Thread`` *constructed* inside the active block but
# *started* after it returns, and a raw ``_thread.start_new_thread``, both
# escaped that registry. The fix (see sandbox.py) is to stop tracking threads
# at all: the pointer is set on activation and never cleared automatically.
# These are the regression tests for all of that, plus the shapes near it.
# ---------------------------------------------------------------------------

_ATTEMPT_CONNECT = (
    "import socket\n"
    "def _attempt():\n"
    "    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
    "    s.settimeout(0.05)\n"
    "    try:\n"
    "        s.connect_ex(('127.0.0.1', 9))\n"
    "    except Exception:\n"
    "        pass\n"
    "    finally:\n"
    "        s.close()\n"
)


def test_thread_cannot_escape_the_sandbox(tmp_path: Path) -> None:
    """The exact reproduction from verification: a plain ``threading.Thread``
    started inside ``activate()`` must be blocked and recorded."""
    result = _probe(
        tmp_path,
        "import threading\n"
        + _ATTEMPT_CONNECT
        + "with activate(ctx):\n"
        "    t = threading.Thread(target=_attempt)\n"
        "    t.start()\n"
        "    t.join()\n",
        label="thread",
    )
    assert _blocked_kinds(result) == ["network"]


def test_thread_constructed_inside_started_after_cannot_escape(tmp_path: Path) -> None:
    """The escape that broke round two: a ``Thread`` built while the sandbox
    is active but only ``.start()``-ed after the block exits. Enforcement
    must still be in force when it actually runs."""
    result = _probe(
        tmp_path,
        "import threading\n"
        + _ATTEMPT_CONNECT
        + "with activate(ctx):\n"
        "    t = threading.Thread(target=_attempt)\n"
        "t.start()\n"
        "t.join()\n",
        label="thread_late_start",
    )
    assert _blocked_kinds(result) == ["network"]


def test_thread_start_new_thread_cannot_escape(tmp_path: Path) -> None:
    """The other escape that broke round two: ``_thread.start_new_thread``
    bypasses ``threading``'s own bookkeeping entirely and never appears in
    ``threading.enumerate()``. Blocked and recorded regardless, during and
    after the active block, since enforcement here never depended on
    knowing this thread existed."""
    result = _probe(
        tmp_path,
        "import _thread, threading\n"
        + _ATTEMPT_CONNECT
        + "done = threading.Event()\n"
        "def _run():\n"
        "    _attempt()\n"
        "    done.set()\n"
        "with activate(ctx):\n"
        "    _thread.start_new_thread(_run, ())\n"
        "done.wait(timeout=5)\n",
        label="start_new_thread",
    )
    assert _blocked_kinds(result) == ["network"]


def test_nested_thread_cannot_escape_the_sandbox(tmp_path: Path) -> None:
    """A thread that itself starts another thread: both generations are
    covered, since enforcement is global, not tied to who spawned whom."""
    result = _probe(
        tmp_path,
        "import threading\n"
        + _ATTEMPT_CONNECT
        + "def outer():\n"
        "    inner = threading.Thread(target=_attempt)\n"
        "    inner.start()\n"
        "    inner.join()\n"
        "with activate(ctx):\n"
        "    t = threading.Thread(target=outer)\n"
        "    t.start()\n"
        "    t.join()\n",
        label="nested_thread",
    )
    assert _blocked_kinds(result) == ["network"]


def test_threadpool_executor_worker_cannot_escape_the_sandbox(tmp_path: Path) -> None:
    result = _probe(
        tmp_path,
        "import concurrent.futures\n"
        + _ATTEMPT_CONNECT
        + "with activate(ctx):\n"
        "    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:\n"
        "        future = pool.submit(_attempt)\n"
        "        future.result(timeout=5)\n",
        label="threadpool",
    )
    assert _blocked_kinds(result) == ["network"]


def test_process_pool_executor_worker_cannot_escape_the_sandbox(tmp_path: Path) -> None:
    """A ``ProcessPoolExecutor`` worker opens a socket in a genuinely
    separate OS process. Its own ``.submit`` first has to launch that
    process, which is blocked the same way any other process spawn is; the
    worker's socket call is therefore never reached at all."""
    result = _probe(
        tmp_path,
        "import concurrent.futures\n"
        + _ATTEMPT_CONNECT
        + "with activate(ctx):\n"
        "    try:\n"
        "        with concurrent.futures.ProcessPoolExecutor(max_workers=1) as pool:\n"
        "            future = pool.submit(_attempt)\n"
        "            future.result(timeout=10)\n"
        "    except Exception:\n"
        "        pass\n",
        label="processpool",
    )
    assert "process" in _blocked_kinds(result)


def test_asyncio_task_cannot_escape_the_sandbox(tmp_path: Path) -> None:
    """Regression guard: this case already worked under the ``ContextVar``
    design (asyncio tasks copy the creating context) and must keep working
    now that enforcement is a plain global instead."""
    result = _probe(
        tmp_path,
        "import asyncio\n"
        + _ATTEMPT_CONNECT
        + "async def main():\n"
        "    await asyncio.get_event_loop().run_in_executor(None, lambda: None)\n"
        "    _attempt()\n"
        "with activate(ctx):\n"
        "    asyncio.run(main())\n",
        label="asyncio",
    )
    assert _blocked_kinds(result) == ["network"]


def test_thread_started_before_activate_is_still_blocked_once_inside(tmp_path: Path) -> None:
    """A thread already running when the sandbox activates is judged the
    same way as one the sandbox itself spawned: enforcement depends on
    whether a run is active *when the operation happens*, not on when or
    where the thread performing it was created."""
    result = _probe(
        tmp_path,
        "import threading\n"
        + _ATTEMPT_CONNECT
        + "started = threading.Event()\n"
        "proceed = threading.Event()\n"
        "def pre_started_worker():\n"
        "    started.set()\n"
        "    proceed.wait(timeout=5)\n"
        "    _attempt()\n"
        "pre_started = threading.Thread(target=pre_started_worker)\n"
        "pre_started.start()\n"
        "started.wait(timeout=5)\n"
        "with activate(ctx):\n"
        "    proceed.set()\n"
        "    pre_started.join(timeout=5)\n",
        label="pre_started",
    )
    assert _blocked_kinds(result) == ["network"]


def test_daemon_thread_outliving_activate_does_not_escape(tmp_path: Path) -> None:
    """A daemon thread the sandbox does not (and cannot) join is still alive
    after ``activate()``'s block exits. Its later action must still be
    blocked and recorded -- fail closed, not open. The probe asserts zero
    blocked attempts immediately after ``activate()`` exits (proving the
    block did not already happen), then waits for the daemon and asserts
    one -- no window where enforcement looked "off" to it.
    """
    result = _probe(
        tmp_path,
        "import threading, time, json, sys\n"
        + _ATTEMPT_CONNECT
        + "daemon_acted = threading.Event()\n"
        "def late_worker():\n"
        "    time.sleep(0.15)\n"
        "    _attempt()\n"
        "    daemon_acted.set()\n"
        "with activate(ctx):\n"
        "    t = threading.Thread(target=late_worker, daemon=True)\n"
        "    t.start()\n"
        "assert len(ctx.blocked) == 0, 'blocked before the daemon acted'\n"
        "assert daemon_acted.wait(timeout=5), 'daemon thread never ran'\n",
        label="daemon_teardown",
    )
    assert _blocked_kinds(result) == ["network"]


# ---------------------------------------------------------------------------
# multiprocessing: checked, and one real gap found and closed
#
# ``multiprocessing``'s "fork" start method calls ``os.fork()`` and is
# already covered by ``PROCESS_EVENTS``. Its "spawn" start method does not:
# it calls ``_posixsubprocess.fork_exec`` directly (see
# ``multiprocessing.util.spawnv_passfds``), bypassing the
# ``subprocess.Popen`` audit event along with every other event this module
# otherwise relies on -- verified empirically while building this test, not
# assumed (see the build report for the raw trace). It is closed by gating
# the ``import`` of the backend module that performs it, since every start
# method loads that module lazily, only once a process is genuinely about
# to be launched.
# ---------------------------------------------------------------------------

_MP_NOOP_DEF = "def _mp_noop():\n    pass\n"


def test_multiprocessing_fork_is_blocked(tmp_path: Path) -> None:
    result = _probe(
        tmp_path,
        "import multiprocessing\n"
        + _MP_NOOP_DEF
        + "with activate(ctx):\n"
        "    try:\n"
        "        multiprocessing.get_context('fork').Process(target=_mp_noop).start()\n"
        "    except Exception:\n"
        "        pass\n",
        label="mp_fork",
    )
    assert _blocked_kinds(result) == ["process"]
    assert "os.fork" in result["blocked"][0]["detail"]


def test_multiprocessing_spawn_is_blocked(tmp_path: Path) -> None:
    """The gap: reproduced, then closed. Before the import gate this probe's
    only assertion failed with zero blocked attempts, and the child process
    genuinely ran, unaudited."""
    result = _probe(
        tmp_path,
        "import multiprocessing\n"
        + _MP_NOOP_DEF
        + "with activate(ctx):\n"
        "    try:\n"
        "        multiprocessing.get_context('spawn').Process(target=_mp_noop).start()\n"
        "    except Exception:\n"
        "        pass\n",
        label="mp_spawn",
    )
    assert _blocked_kinds(result) == ["process"]
    assert "multiprocessing" in result["blocked"][0]["detail"]


def test_declared_multiprocessing_is_allowed(tmp_path: Path) -> None:
    # multiprocessing's "spawn" method pickles the target by module-qualified
    # name and re-imports it in the child, so it needs a real module on disk
    # -- a `python -c` script has no importable `__main__` for the child to
    # find it in.
    mp_target_dir = tmp_path / "mp_target_module"
    mp_target_dir.mkdir(parents=True, exist_ok=True)
    (mp_target_dir / "mp_target.py").write_text("def mp_noop():\n    pass\n")

    result = _probe(
        tmp_path,
        "import multiprocessing, sys\n"
        f"sys.path.insert(0, {str(mp_target_dir)!r})\n"
        "import mp_target\n"
        "with activate(ctx):\n"
        "    p = multiprocessing.get_context('spawn').Process(target=mp_target.mp_noop)\n"
        "    p.start()\n"
        "    p.join(timeout=10)\n"
        "    assert p.exitcode == 0, p.exitcode\n",
        declared_process_names=frozenset({"multiprocessing", "fork"}),
        label="mp_declared",
    )
    assert result["blocked"] == []


def test_direct_posixsubprocess_fork_exec_is_a_verified_known_gap(tmp_path: Path) -> None:
    """Not merely unverified: constructed and reproduced. ``subprocess.Popen``
    fires the "subprocess.Popen" audit event from its own Python-level
    ``__init__`` *before* calling ``_posixsubprocess.fork_exec`` -- that call
    itself raises no audit event of any kind (confirmed by installing a hook
    that logs every event PEP 578 fires and finding zero for this call, see
    the build report). Code that imports ``_posixsubprocess`` directly and
    calls ``fork_exec`` itself, bypassing ``subprocess.Popen`` entirely, is
    therefore invisible to every control in this module: this probe launches
    a real child process (``/bin/true``) with zero blocked attempts.

    This is not closed. Gating the ``import`` of ``_posixsubprocess`` the way
    ``multiprocessing``'s spawn backend is gated does not work here:
    ``subprocess.py`` imports ``_posixsubprocess`` unconditionally at
    ``import subprocess`` time, not at ``Popen()`` call time, so the same
    gate would block importing ``subprocess`` at all -- including the
    already-working, already-tested "declared executable" path -- rather
    than just this bypass. No fix was found that closes this without
    breaking that. It is a real, disclosed limit of an audit-hook-based
    harness: PEP 578 only fires where CPython's own C code calls
    ``PySys_Audit``, and there is no way to intercept a call that does not.
    Closing it requires OS-level sandboxing (seccomp-bpf, a container, a
    namespace) underneath this harness, not a change to this module. See the
    build report.
    """
    result = _probe(
        tmp_path,
        "import os, _posixsubprocess\n"
        "executable = b'/bin/true'\n"
        "errpipe_read, errpipe_write = os.pipe()\n"
        "with activate(ctx):\n"
        "    pid = _posixsubprocess.fork_exec(\n"
        "        [executable], (executable,), True, (), None, None,\n"
        "        -1, -1, -1, -1, -1, -1, errpipe_read, errpipe_write,\n"
        "        False, False, 0, -1, None, -1, -1, None, False,\n"
        "    )\n"
        "    os.waitpid(pid, 0)\n"
        "os.close(errpipe_read)\n"
        "os.close(errpipe_write)\n",
        label="direct_fork_exec",
    )
    # Documents the gap rather than hiding it: this is what "not closed"
    # looks like. A future fix that closes it should change this assertion,
    # not delete the test.
    assert result["blocked"] == []


def test_declared_child_process_runs_unaudited_once_permitted(tmp_path: Path) -> None:
    """Declaring a token in ``declared_process_names`` permits *spawning*
    that process -- it extends no control into it. Once running, a child is
    a genuinely separate interpreter with no audit hook installed at all, so
    its own operations are neither blocked nor recorded, in either
    direction. Proven, not assumed: the child here connects a real socket
    back to a listener the parent set up before ``activate``, and that
    connection completes -- if the child's own network use were somehow
    still being enforced, this would hang or raise instead.
    """
    python = os.path.basename(sys.executable)
    result = _probe(
        tmp_path,
        "import socket, subprocess, sys, threading\n"
        "server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "server.bind(('127.0.0.1', 0))\n"
        "server.listen(1)\n"
        "port = server.getsockname()[1]\n"
        "received = {}\n"
        "def accept_one():\n"
        "    conn, _ = server.accept()\n"
        "    received['data'] = conn.recv(1024)\n"
        "    conn.close()\n"
        "acceptor = threading.Thread(target=accept_one)\n"
        "acceptor.start()\n"
        "child_code = (\n"
        "    'import socket\\n'\n"
        "    's = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\\n'\n"
        "    's.connect((\\'127.0.0.1\\', ' + str(port) + '))\\n'\n"
        "    's.sendall(b\\'unaudited-child\\')\\n'\n"
        "    's.close()\\n'\n"
        ")\n"
        "with activate(ctx):\n"
        "    subprocess.run([sys.executable, '-c', child_code], check=True, timeout=20)\n"
        "acceptor.join(timeout=20)\n"
        "server.close()\n"
        "assert received.get('data') == b'unaudited-child', received\n",
        declared_process_names=frozenset({python}),
        label="declared_child_unaudited",
        timeout=45.0,
    )
    # Neither the parent's spawn nor the child's own socket use is recorded:
    # declaring a process token permits only the spawn, nothing beyond it.
    assert result["blocked"] == []


# ---------------------------------------------------------------------------
# RunObserver -- card 12's seam (contracts.RunObserver / HarnessCard.start)
#
# The contract left the handoff between card 11 and card 12 unspecified;
# `Tracer.collector(run)` (card 12) had nowhere to install, since
# `Harness.start` took no parameter to install it through. `RunObserver` is a
# `start()`/`stop()` protocol so this module never imports card 12 -- the
# harness must keep working with no observer at all, and a tracing run is
# the same run with something watching, not a different code path.
# ---------------------------------------------------------------------------

_OBSERVER_SOURCE = (
    "import json\n"
    "class _RecordingObserver:\n"
    "    def __init__(self):\n"
    "        self.calls = []\n"
    "    def start(self, run):\n"
    "        self.calls.append('start')\n"
    "        with open('observer_events.log', 'a') as f:\n"
    "            f.write(\n"
    "                'start:' + run.run_id + ':'\n"
    "                + json.dumps(run.controls_active, sort_keys=True) + '\\n'\n"
    "            )\n"
    "    def stop(self):\n"
    "        self.calls.append('stop')\n"
    "        with open('observer_events.log', 'a') as f:\n"
    "            f.write('stop\\n')\n"
    "OBSERVER = _RecordingObserver()\n"
)


def test_observer_started_and_stopped_exactly_once_around_a_successful_run(
    tmp_path: Path,
) -> None:
    record, sandbox_root, _ = _run_harness(
        tmp_path,
        source="with open('ran.txt', 'w') as f:\n    f.write('ok')\n",
        module="observed_success",
        observer_source=_OBSERVER_SOURCE,
        label="observer_success",
    )
    assert record["refused"] is False
    lines = (sandbox_root / "observer_events.log").read_text().splitlines()
    assert len(lines) == 2 and lines[1] == "stop"
    # start() received the real, verified record -- not one fabricated
    # before controls were checked: the same run_id and controls_active
    # the harness itself produced for this run.
    expected = f"start:{record['run_id']}:{json.dumps(record['controls_active'], sort_keys=True)}"
    assert lines[0] == expected
    # The observed call itself still ran normally, inside the same window.
    assert (sandbox_root / "ran.txt").read_text() == "ok"


def test_observer_is_stopped_when_the_target_raises(tmp_path: Path) -> None:
    record, sandbox_root, _ = _run_harness(
        tmp_path,
        source="raise RuntimeError('the target blew up')\n",
        module="observed_crash",
        observer_source=_OBSERVER_SOURCE,
        label="observer_crash",
    )
    # The scenario crashing is still a completed run, not a refusal.
    assert record["refused"] is False
    lines = (sandbox_root / "observer_events.log").read_text().splitlines()
    assert len(lines) == 2 and lines[1] == "stop"
    assert lines[0].startswith(f"start:{record['run_id']}:")


def test_observer_is_started_inside_the_sandbox_window(tmp_path: Path) -> None:
    """observer.start() itself runs inside the activated sandbox -- proven
    by having it perform a network attempt from within start(), which must
    be blocked and recorded exactly like anything the target itself does."""
    observer_source = (
        "class _NetworkAttemptingObserver:\n"
        "    def start(self, run):\n"
        "        import socket\n"
        "        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "        s.settimeout(0.01)\n"
        "        try:\n"
        "            s.connect(('93.184.216.34', 80))\n"
        "        except Exception:\n"
        "            pass\n"
        "        finally:\n"
        "            s.close()\n"
        "    def stop(self):\n"
        "        pass\n"
        "OBSERVER = _NetworkAttemptingObserver()\n"
    )
    record, _, _ = _run_harness(
        tmp_path,
        source="x = 1\n",
        module="observer_network_probe",
        observer_source=observer_source,
        label="observer_window",
    )
    assert record["refused"] is False
    assert "network" in _blocked_kinds(record)


class _CountingObserver:
    def __init__(self) -> None:
        self.start_calls = 0
        self.stop_calls = 0
        self.last_run: object | None = None

    def start(self, run: object) -> None:
        self.start_calls += 1
        self.last_run = run

    def stop(self) -> None:
        self.stop_calls += 1


def test_observer_none_is_the_default_and_behaves_identically(tmp_path: Path) -> None:
    """A run with ``observer=None`` (explicit or omitted) behaves exactly as
    it did before the parameter existed. Runs both through ``_run_harness``
    (subprocess) rather than in-process: this is a real, successful
    ``activate()``-touching run, and an in-process call here would leave
    ``_active_ctx`` set for the rest of this module's own tests -- exactly
    the contamination the module docstring explains why to avoid.
    """
    target_root = tmp_path / "target_observer_none"
    target_root.mkdir()
    (target_root / "trivial_observer_none.py").write_text("x = 1\n")
    graph_hash = _graph_hash_for(target_root)

    record_omitted, _, _ = _run_harness(
        tmp_path,
        source="",
        module="trivial_observer_none",
        target_root=target_root,
        graph_hash=graph_hash,
        label="observer_omitted",
    )
    record_explicit_none, _, _ = _run_harness(
        tmp_path,
        source="",
        module="trivial_observer_none",
        target_root=target_root,
        graph_hash=graph_hash,
        observer_source="OBSERVER = None\n",
        label="observer_explicit_none",
    )

    for record in (record_omitted, record_explicit_none):
        assert record["refused"] is False
        # Only informational reads of stdlib/interpreter internals may
        # appear -- nothing was blocked (same pattern as run_linear's test).
        assert set(_blocked_kinds(record)) <= {"filesystem_read_outside_sandbox"}
        assert record["controls_active"] == {
            "network": True,
            "filesystem": True,
            "process": True,
            "environment": True,
            "external_clients": True,
        }
    # Same target, same graph hash: the omitted and explicit-None runs
    # blocked (and read outside the sandbox) exactly the same things.
    assert _blocked_kinds(record_omitted) == _blocked_kinds(record_explicit_none)


def test_observer_never_started_when_no_mode_b_graph_exists(tmp_path: Path) -> None:
    observer = _CountingObserver()
    target_root = _write_target(tmp_path, "trivial", "x = 1\n")
    out_dir = tmp_path / "out_without_a_graph"
    out_dir.mkdir()
    config = RunConfig(
        target_root=target_root,
        mode_b_out_dir=out_dir,
        sandbox_root=tmp_path / "sandbox",
        scenarios={"s": ScenarioSpec(name="s", module="trivial")},
    )
    record = Harness(config).start("s", _graph_hash_for(target_root), observer=observer)

    assert record.refused is True
    assert observer.start_calls == 0
    assert observer.stop_calls == 0


def test_observer_never_started_when_graph_is_stale(tmp_path: Path) -> None:
    observer = _CountingObserver()
    target_root = _write_target(tmp_path, "trivial", "x = 1\n")
    config = _config(tmp_path, target_root, "s", "trivial")

    record = Harness(config).start("s", "0" * 64, observer=observer)

    assert record.refused is True
    assert observer.start_calls == 0
    assert observer.stop_calls == 0


def test_observer_never_started_when_scenario_not_declared(tmp_path: Path) -> None:
    observer = _CountingObserver()
    target_root = _write_target(tmp_path, "trivial", "x = 1\n")
    config = _config(tmp_path, target_root, "declared_scenario", "trivial")

    record = Harness(config).start(
        "not_the_declared_one", _graph_hash_for(target_root), observer=observer
    )

    assert record.refused is True
    assert observer.start_calls == 0
    assert observer.stop_calls == 0


def test_observer_never_started_when_audit_hook_cannot_be_verified(
    tmp_path: Path, monkeypatch
) -> None:
    observer = _CountingObserver()
    target_root = _write_target(tmp_path, "trivial", "x = 1\n")
    config = _config(tmp_path, target_root, "s", "trivial")

    import cascade_map.harness.sandbox as sandbox_module

    def _broken_install_hook() -> None:
        raise RuntimeError("simulated: audit hook installation failed")

    monkeypatch.setattr(sandbox_module, "install_hook", _broken_install_hook)

    record = Harness(config).start("s", _graph_hash_for(target_root), observer=observer)

    assert record.refused is True
    assert observer.start_calls == 0
    assert observer.stop_calls == 0
