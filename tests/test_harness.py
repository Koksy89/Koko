"""Tests for card 11, the safe execution harness.

Constraint 7: Mode A must be incapable of real-world side effects. Every test
here either proves a control holds against an adversarial program, or proves
the harness refuses to start rather than run with a guarantee it cannot make.

``tests/fixtures/mode_a/`` currently contains only ``run_linear`` -- the
``adv_*`` cases FIXTURES.md specifies (card 8's territory) do not yet exist on
disk. Rather than reach into ``tests/fixtures/`` (out of this card's
territory) this file builds the same adversarial programs FIXTURES.md
describes as temporary files under ``tmp_path``, one per case, named after the
``adv_*``/``run_*`` case it proves. See the build report for this gap.

Nothing here ever imports, execs or reads ``target_engine/`` or
``target_versions/`` -- every target root used below is a *copy* of a fixture
directory, made under ``tmp_path``, or a ``tmp_path`` the test itself wrote.

The corpus in ``tests/fixtures/`` is the fixed point every card is measured
against, and this card's own purpose is containing side effects -- so no test
here ever points ``RunConfig.target_root`` (or anything else) directly at
``tests/fixtures/``: real fixtures are copied into ``tmp_path`` first via
``_copied_fixture`` before anything executes them, structurally ruling out a
write landing in the corpus regardless of whether the sandbox's own
``sys.dont_write_bytecode`` window is entered correctly. ``_fixtures_untouched``
is the loud, local check that this held: it hashes the whole corpus before
and after this module's tests run and fails if a single byte moved.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import inspect
import json
import multiprocessing
import os
import shutil
import socket
import sys
import threading
from pathlib import Path
from types import ModuleType

import pytest

from cascade_map.contracts import canonical_dumps
from cascade_map.harness import Harness, RunConfig, ScenarioSpec
from cascade_map.harness.hashing import compute_graph_hash, compute_target_hashes
from cascade_map.harness.sandbox import SandboxContext, activate

FIXTURES_ROOT = Path(__file__).parent / "fixtures"
FIXTURES_MODE_A = FIXTURES_ROOT / "mode_a"


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
    (``sys.dont_write_bytecode`` for the whole module, on top of the
    sandbox's own) around the one thing this card exists to prevent.
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
    structurally impossible instead of depending on a flag staying set.
    Returns the copy of *relative* itself, e.g. ``.../tmp_path/copy/run_linear``
    for ``relative="run_linear"`` -- the caller points ``target_root`` at its
    parent so the copied directory still imports under its original name.
    """
    dest = tmp_path / "fixture_copy" / relative
    shutil.copytree(FIXTURES_MODE_A / relative, dest)
    return dest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mode_b_graph(tmp_path: Path, name: str = "out") -> Path:
    """A stand-in completed Mode B graph: just enough for the harness's own
    "does a graph exist" check -- an ``elements.jsonl`` anchor file."""
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


def _config(
    tmp_path: Path,
    target_root: Path,
    scenario: str,
    module: str,
    function: str = "",
    **kwargs: object,
) -> RunConfig:
    out_dir = _mode_b_graph(tmp_path)
    sandbox_root = tmp_path / "sandbox"
    return RunConfig(
        target_root=target_root,
        mode_b_out_dir=out_dir,
        sandbox_root=sandbox_root,
        scenarios={scenario: ScenarioSpec(name=scenario, module=module, function=function)},
        **kwargs,  # type: ignore[arg-type]
    )


def _run(config: RunConfig, scenario: str, graph_hash: str | None = None):
    if graph_hash is None:
        graph_hash = _graph_hash_for(config.target_root)
    return Harness(config).start(scenario, graph_hash)


def _kinds(record) -> list[str]:
    return [b.kind for b in record.blocked]


# ---------------------------------------------------------------------------
# run_linear -- a legitimate scenario completes cleanly, end to end
# ---------------------------------------------------------------------------


def test_run_linear_completes_with_every_control_active(tmp_path: Path) -> None:
    # A copy, never the corpus itself: see _copied_fixture and _fixtures_untouched.
    copied_run_linear = _copied_fixture(tmp_path, "run_linear")
    target_root = copied_run_linear.parent

    out_dir = _mode_b_graph(tmp_path)
    sandbox_root = tmp_path / "sandbox"
    config = RunConfig(
        target_root=target_root,
        mode_b_out_dir=out_dir,
        sandbox_root=sandbox_root,
        scenarios={
            "run_linear": ScenarioSpec(name="run_linear", module="run_linear", function="main")
        },
    )
    record = _run(config, "run_linear")

    assert record.refused is False
    assert record.refusal_reason == ""
    # Only informational reads of stdlib/interpreter internals may appear --
    # nothing the scenario itself did was blocked or written outside it.
    assert _kinds(record) == ["filesystem_read_outside_sandbox"] * len(record.blocked)
    assert record.controls_active == {
        "network": True,
        "filesystem": True,
        "process": True,
        "environment": True,
        "external_clients": True,
    }
    assert record.run_id.startswith("run_")

    # No write landed even in the *copy* -- and the real corpus was never
    # named as a target_root at all, so it could not have been touched.
    assert not (copied_run_linear / "__pycache__").exists()
    assert not (FIXTURES_MODE_A / "run_linear" / "__pycache__").exists()

    run_json = out_dir / "runtime" / record.run_id / "run.json"
    assert run_json.exists()
    on_disk = json.loads(run_json.read_text())
    assert on_disk["run_id"] == record.run_id
    assert canonical_dumps(record) + "\n" == run_json.read_text()


# ---------------------------------------------------------------------------
# Refusals -- constraint 7: refusing to start is the correct outcome
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

    record = _run(config, "s", graph_hash="0" * 64)  # a graph hash that matches nothing

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
    names the guarantee -- simulated here by making hook installation fail,
    since a real broken interpreter is not something a test can construct."""
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
    """There is no override. A regression that adds one is a defect on its own."""
    params = list(inspect.signature(Harness.start).parameters)
    assert params == ["self", "scenario", "graph_hash"]


# ---------------------------------------------------------------------------
# adv_network / adv_dns -- outbound sockets and DNS blocked at the socket layer
# ---------------------------------------------------------------------------


def test_adv_network_outbound_connect_blocked(tmp_path: Path) -> None:
    target_root = _write_target(
        tmp_path,
        "adv_network",
        "import socket\n"
        "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "s.settimeout(0.01)\n"
        "try:\n"
        "    s.connect(('93.184.216.34', 80))\n"
        "finally:\n"
        "    s.close()\n",
    )
    config = _config(tmp_path, target_root, "adv_network", "adv_network")
    record = _run(config, "adv_network")

    assert record.refused is False
    assert "network" in _kinds(record)
    net = [b for b in record.blocked if b.kind == "network"][0]
    assert "socket.connect" in net.detail


def test_adv_dns_lookup_blocked(tmp_path: Path) -> None:
    target_root = _write_target(
        tmp_path,
        "adv_dns",
        "import socket\n"
        "try:\n"
        "    socket.gethostbyname('example.com')\n"
        "except Exception:\n"
        "    pass\n",
    )
    config = _config(tmp_path, target_root, "adv_dns", "adv_dns")
    record = _run(config, "adv_dns")

    assert record.refused is False
    assert "network" in _kinds(record)
    net = [b for b in record.blocked if b.kind == "network"][0]
    assert "gethostbyname" in net.detail


def test_adv_undeclared_client_is_a_hard_stop(tmp_path: Path) -> None:
    """adv_undeclared_client: reaching an external system with no declared
    stub goes through the real socket layer and is blocked like anything
    else undeclared -- not a special-cased pass-through."""
    target_root = _write_target(
        tmp_path,
        "adv_undeclared_client",
        "import socket\n"
        "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "s.settimeout(0.01)\n"
        "try:\n"
        "    s.connect(('203.0.113.5', 5432))  # pretend Postgres, never declared\n"
        "finally:\n"
        "    s.close()\n",
    )
    config = _config(tmp_path, target_root, "adv_undeclared_client", "adv_undeclared_client")
    record = _run(config, "adv_undeclared_client")

    assert record.refused is False
    assert "network" in _kinds(record)


def test_declared_client_stub_bypasses_network_entirely(tmp_path: Path) -> None:
    """A declared client never touches the socket layer at all: the stub
    satisfies the import first."""
    target_root = _write_target(
        tmp_path,
        "uses_broker",
        "import fake_broker\n"
        "result = fake_broker.send('order-1')\n"
        "with open('stub_result.txt', 'w') as f:\n"
        "    f.write(result)\n",
    )

    def _make_stub() -> ModuleType:
        mod = ModuleType("fake_broker")
        mod.send = lambda payload: f"stubbed-ack:{payload}"  # type: ignore[attr-defined]
        return mod

    config = _config(
        tmp_path,
        target_root,
        "uses_broker",
        "uses_broker",
        client_stubs={"fake_broker": _make_stub},
    )
    record = _run(config, "uses_broker")

    assert record.refused is False
    assert "network" not in _kinds(record)
    result_file = config.sandbox_root / "stub_result.txt"
    assert result_file.read_text() == "stubbed-ack:order-1"
    assert "fake_broker" not in sys.modules  # restored, not leaked into the test process


# ---------------------------------------------------------------------------
# adv_write_escape -- absolute path, `..`, and a symlink
# ---------------------------------------------------------------------------


def test_adv_write_escape_absolute_path_blocked(tmp_path: Path) -> None:
    outside = tmp_path / "outside_absolute.txt"
    target_root = _write_target(
        tmp_path,
        "adv_write_absolute",
        f"with open({str(outside)!r}, 'w') as f:\n"
        "    f.write('escaped')\n",
    )
    config = _config(tmp_path, target_root, "adv_write_absolute", "adv_write_absolute")
    record = _run(config, "adv_write_absolute")

    assert "filesystem_write" in _kinds(record)
    assert not outside.exists()


def test_adv_write_escape_dotdot_blocked(tmp_path: Path) -> None:
    target_root = _write_target(
        tmp_path,
        "adv_write_dotdot",
        "with open('../escaped_dotdot.txt', 'w') as f:\n"
        "    f.write('escaped')\n",
    )
    config = _config(tmp_path, target_root, "adv_write_dotdot", "adv_write_dotdot")
    record = _run(config, "adv_write_dotdot")

    assert "filesystem_write" in _kinds(record)
    assert not (config.sandbox_root.parent / "escaped_dotdot.txt").exists()


def test_adv_write_escape_symlink_blocked(tmp_path: Path) -> None:
    outside_dir = tmp_path / "outside_via_symlink"
    outside_dir.mkdir()
    sandbox_root = tmp_path / "sandbox"
    sandbox_root.mkdir()
    (sandbox_root / "escape_link").symlink_to(outside_dir, target_is_directory=True)

    target_root = _write_target(
        tmp_path,
        "adv_write_symlink",
        "with open('escape_link/escaped.txt', 'w') as f:\n"
        "    f.write('escaped')\n",
    )
    out_dir = _mode_b_graph(tmp_path)
    config = RunConfig(
        target_root=target_root,
        mode_b_out_dir=out_dir,
        sandbox_root=sandbox_root,
        scenarios={"s": ScenarioSpec(name="s", module="adv_write_symlink")},
    )
    record = _run(config, "s")

    assert "filesystem_write" in _kinds(record)
    assert not (outside_dir / "escaped.txt").exists()


def test_write_inside_sandbox_succeeds_and_is_not_blocked(tmp_path: Path) -> None:
    target_root = _write_target(
        tmp_path,
        "legit_write",
        "with open('inside.txt', 'w') as f:\n"
        "    f.write('fine')\n",
    )
    config = _config(tmp_path, target_root, "legit_write", "legit_write")
    record = _run(config, "legit_write")

    # No write was blocked (the only permitted kind of entry left is a read
    # of the interpreter's own stdlib/bytecode cache, outside the sandbox by
    # necessity and recorded, not blocked).
    assert "filesystem_write" not in _kinds(record)
    assert (config.sandbox_root / "inside.txt").read_text() == "fine"


# ---------------------------------------------------------------------------
# adv_subprocess -- subprocess, os.system, os.fork
# ---------------------------------------------------------------------------


def test_adv_subprocess_os_system_and_fork_all_blocked(tmp_path: Path) -> None:
    target_root = _write_target(
        tmp_path,
        "adv_subprocess",
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
        "    pass\n",
    )
    config = _config(tmp_path, target_root, "adv_subprocess", "adv_subprocess")
    record = _run(config, "adv_subprocess")

    process_blocks = [b for b in record.blocked if b.kind == "process"]
    events = {b.detail.split()[0] for b in process_blocks}
    assert {"subprocess.Popen", "os.system", "os.fork"} <= events


def test_declared_process_is_allowed_to_spawn(tmp_path: Path) -> None:
    python = os.path.basename(sys.executable)
    target_root = _write_target(
        tmp_path,
        "declared_subprocess",
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, '-c', 'pass'], check=True)\n"
        "with open('ran.txt', 'w') as f:\n"
        "    f.write('ok')\n",
    )
    config = _config(
        tmp_path,
        target_root,
        "declared_subprocess",
        "declared_subprocess",
        declared_process_names=frozenset({python}),
    )
    record = _run(config, "declared_subprocess")

    assert "process" not in _kinds(record)
    assert (config.sandbox_root / "ran.txt").read_text() == "ok"


# ---------------------------------------------------------------------------
# Environment -- secrets are not passed through unless declared
# ---------------------------------------------------------------------------


def test_undeclared_env_var_not_passed_through(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CASCADE_MAP_TEST_SECRET_TOKEN", "super-secret-value")
    target_root = _write_target(
        tmp_path,
        "env_check",
        "import os\n"
        "value = os.environ.get('CASCADE_MAP_TEST_SECRET_TOKEN', '<absent>')\n"
        "with open('env_result.txt', 'w') as f:\n"
        "    f.write(value)\n",
    )
    config = _config(tmp_path, target_root, "env_check", "env_check")
    record = _run(config, "env_check")

    assert record.controls_active["environment"] is True
    assert (config.sandbox_root / "env_result.txt").read_text() == "<absent>"
    # The harness's own process keeps its environment, restored after the run.
    assert os.environ["CASCADE_MAP_TEST_SECRET_TOKEN"] == "super-secret-value"


def test_declared_env_var_is_passed_through(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CASCADE_MAP_TEST_PUBLIC_VAR", "hello")
    target_root = _write_target(
        tmp_path,
        "env_check_declared",
        "import os\n"
        "value = os.environ.get('CASCADE_MAP_TEST_PUBLIC_VAR', '<absent>')\n"
        "with open('env_result.txt', 'w') as f:\n"
        "    f.write(value)\n",
    )
    config = _config(
        tmp_path,
        target_root,
        "env_check_declared",
        "env_check_declared",
        env_passthrough=frozenset({"CASCADE_MAP_TEST_PUBLIC_VAR"}),
    )
    record = _run(config, "env_check_declared")

    assert (config.sandbox_root / "env_result.txt").read_text() == "hello"


# ---------------------------------------------------------------------------
# Reads outside the sandbox: allowed, but recorded
# ---------------------------------------------------------------------------


def test_reads_outside_sandbox_are_allowed_and_recorded(tmp_path: Path) -> None:
    outside_file = tmp_path / "readable_outside.txt"
    outside_file.write_text("read me")
    target_root = _write_target(
        tmp_path,
        "reads_outside",
        f"with open({str(outside_file)!r}) as f:\n"
        "    data = f.read()\n"
        "with open('copy.txt', 'w') as f:\n"
        "    f.write(data)\n",
    )
    config = _config(tmp_path, target_root, "reads_outside", "reads_outside")
    record = _run(config, "reads_outside")

    assert (config.sandbox_root / "copy.txt").read_text() == "read me"
    read_kinds = [b for b in record.blocked if b.kind == "filesystem_read_outside_sandbox"]
    assert any(str(outside_file) in b.detail for b in read_kinds)
    # A read is not a write: it did not block the run or the target's own logic.
    assert "filesystem_write" not in _kinds(record)


# ---------------------------------------------------------------------------
# Determinism -- replaying the same recorded run is byte-identical
# ---------------------------------------------------------------------------


def test_replay_is_byte_identical(tmp_path: Path) -> None:
    target_root = _write_target(
        tmp_path,
        "adv_network_replay",
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
        "    f.write('ran')\n",
    )
    out_dir = _mode_b_graph(tmp_path)
    graph_hash = _graph_hash_for(target_root)

    def _new_config(label: str) -> RunConfig:
        return RunConfig(
            target_root=target_root,
            mode_b_out_dir=out_dir,
            sandbox_root=tmp_path / f"sandbox_{label}",
            scenarios={
                "adv_network_replay": ScenarioSpec(
                    name="adv_network_replay", module="adv_network_replay"
                )
            },
        )

    record_one = Harness(_new_config("one")).start("adv_network_replay", graph_hash)
    record_two = Harness(_new_config("two")).start("adv_network_replay", graph_hash)

    assert record_one.run_id == record_two.run_id
    assert canonical_dumps(record_one) == canonical_dumps(record_two)


def test_run_record_written_to_disk_is_replayable(tmp_path: Path) -> None:
    target_root = _write_target(tmp_path, "trivial_replay", "x = 1\n")
    config = _config(tmp_path, target_root, "trivial_replay", "trivial_replay")
    record = _run(config, "trivial_replay")

    run_json_path = config.mode_b_out_dir / "runtime" / record.run_id / "run.json"
    first_bytes = run_json_path.read_bytes()

    # Re-run the identical scenario against the identical config: the run ID
    # is deterministic, so this overwrites the same path -- with the same
    # bytes, which is exactly the property under test.
    _run(config, "trivial_replay")
    second_bytes = run_json_path.read_bytes()

    assert first_bytes == second_bytes


# ---------------------------------------------------------------------------
# Threads and processes: enforcement must not be scoped to one logical flow
#
# A ``ContextVar``-based earlier version of ``sandbox.activate`` let a plain
# ``threading.Thread`` escape every control (default context, no active
# value) with no exception and no ``BlockedAttempt`` -- found in
# verification, not by this suite. These are the regression tests for that,
# and for the concurrency shapes near it: nested threads, a thread pool, an
# asyncio task (already correctly blocked before the fix -- kept as a
# regression guard, not new coverage), a thread started before the sandbox
# activated but acting during it, and a daemon thread still alive when the
# sandbox's activation block exits.
# ---------------------------------------------------------------------------


def _new_ctx(tmp_path: Path, label: str = "s") -> SandboxContext:
    root = tmp_path / f"sandbox_{label}"
    root.mkdir(parents=True, exist_ok=True)
    return SandboxContext(sandbox_root=str(root), declared_process_names=frozenset())


def _attempt_connect() -> None:
    """A blocking-network attempt swallowed by the caller, not this function
    -- so it works the same whether the audit hook lets it through or not."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.05)
    try:
        s.connect_ex(("127.0.0.1", 9))
    except Exception:
        pass
    finally:
        s.close()


def test_thread_cannot_escape_the_sandbox(tmp_path: Path) -> None:
    """The exact reproduction from verification, as a permanent regression
    test: a plain ``threading.Thread`` started inside ``activate()`` must be
    blocked and recorded, not silently pass through."""
    ctx = _new_ctx(tmp_path)
    with activate(ctx):
        t = threading.Thread(target=_attempt_connect)
        t.start()
        t.join()

    assert len(ctx.blocked) == 1
    assert ctx.blocked[0].kind == "network"


def test_nested_thread_cannot_escape_the_sandbox(tmp_path: Path) -> None:
    """A thread that itself starts another thread: both generations are
    covered, since enforcement is global, not tied to who spawned whom."""
    ctx = _new_ctx(tmp_path)

    def outer() -> None:
        inner = threading.Thread(target=_attempt_connect)
        inner.start()
        inner.join()

    with activate(ctx):
        t = threading.Thread(target=outer)
        t.start()
        t.join()

    assert len(ctx.blocked) == 1
    assert ctx.blocked[0].kind == "network"


def test_threadpool_executor_worker_cannot_escape_the_sandbox(tmp_path: Path) -> None:
    ctx = _new_ctx(tmp_path)
    with activate(ctx):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(_attempt_connect)
            future.result(timeout=5)

    assert len(ctx.blocked) == 1
    assert ctx.blocked[0].kind == "network"


def test_asyncio_task_cannot_escape_the_sandbox(tmp_path: Path) -> None:
    """Regression guard: this case already worked before the fix (asyncio
    tasks copy the creating ``contextvars.Context``) and must keep working
    now that enforcement is a plain global rather than a ``ContextVar``."""
    ctx = _new_ctx(tmp_path)

    async def main() -> None:
        await asyncio.get_event_loop().run_in_executor(None, lambda: None)
        _attempt_connect()

    with activate(ctx):
        asyncio.run(main())

    assert len(ctx.blocked) == 1
    assert ctx.blocked[0].kind == "network"


def test_thread_started_before_activate_is_still_blocked_once_inside(tmp_path: Path) -> None:
    """A thread already running when the sandbox activates is judged the
    same way as one the sandbox itself spawned: enforcement depends on
    whether a run is active *when the operation happens*, not on when or
    where the thread that performs it was created."""
    ctx = _new_ctx(tmp_path)
    started = threading.Event()
    proceed = threading.Event()

    def pre_started_worker() -> None:
        started.set()
        proceed.wait(timeout=5)
        _attempt_connect()

    pre_started = threading.Thread(target=pre_started_worker)
    pre_started.start()
    assert started.wait(timeout=5)

    with activate(ctx):
        proceed.set()
        pre_started.join(timeout=5)

    assert len(ctx.blocked) == 1
    assert ctx.blocked[0].kind == "network"


def test_daemon_thread_outliving_activate_does_not_escape(tmp_path: Path) -> None:
    """adv_thread_daemon_teardown: a daemon thread the sandbox does not (and
    cannot) join is still alive after ``activate()``'s block exits. Its later
    action must still be blocked and recorded -- fail closed, not open --
    rather than passing through the instant enforcement looks "off" from the
    outside. No window: the assertion right after ``activate()`` exits is 0,
    proving the block did not happen before teardown; the second assertion,
    after the daemon has had time to act, is 1.
    """
    ctx = _new_ctx(tmp_path)
    daemon_acted = threading.Event()

    def late_worker() -> None:
        import time

        time.sleep(0.15)
        _attempt_connect()
        daemon_acted.set()

    with activate(ctx):
        t = threading.Thread(target=late_worker, daemon=True)
        t.start()
        # Deliberately does not join: this is what a target's own ingestion
        # or broker client looks like when it does not wait for a worker.

    assert len(ctx.blocked) == 0, "the daemon had not acted yet -- nothing to prove here"
    assert daemon_acted.wait(timeout=5), "the daemon thread never ran its attempt"
    assert len(ctx.blocked) == 1
    assert ctx.blocked[0].kind == "network"


def test_daemon_thread_registered_at_teardown_is_pruned_once_dead(tmp_path: Path) -> None:
    """The lingering-thread registry that makes the previous test pass must
    not grow forever or misattribute a reused thread identity to a stale
    run: a dead lingering thread is forgotten on the next ``activate()``."""
    import cascade_map.harness.sandbox as sandbox_module

    ctx = _new_ctx(tmp_path, "daemon_prune")
    with activate(ctx):
        t = threading.Thread(target=lambda: None, daemon=True)
        t.start()
    t.join(timeout=5)
    assert not t.is_alive()

    # A second, unrelated run: pruning happens at the start of activate().
    ctx2 = _new_ctx(tmp_path, "daemon_prune_2")
    with activate(ctx2):
        pass
    assert t.ident not in sandbox_module._lingering


# ---------------------------------------------------------------------------
# multiprocessing: checked, and one real gap found and closed
#
# ``multiprocessing``'s "fork" start method calls ``os.fork()`` and is
# already covered by ``PROCESS_EVENTS``. Its "spawn" start method does not:
# it calls ``_posixsubprocess.fork_exec`` directly (see
# ``multiprocessing.util.spawnv_passfds``), bypassing the
# ``subprocess.Popen`` audit event along with every other event this module
# otherwise relies on -- verified empirically while building this test, not
# assumed. It is closed by gating the ``import`` of the backend module that
# performs it (``sandbox._MULTIPROCESSING_LAUNCH_MODULES``), since every
# start method loads that module lazily, only once a process is genuinely
# about to be launched.
# ---------------------------------------------------------------------------


def _mp_noop() -> None:
    pass


def test_multiprocessing_fork_is_blocked(tmp_path: Path) -> None:
    ctx = _new_ctx(tmp_path, "mp_fork")
    with activate(ctx):
        try:
            multiprocessing.get_context("fork").Process(target=_mp_noop).start()
        except Exception:
            pass
    assert len(ctx.blocked) == 1
    assert ctx.blocked[0].kind == "process"
    assert "os.fork" in ctx.blocked[0].detail


def test_multiprocessing_spawn_is_blocked(tmp_path: Path) -> None:
    """The gap: reproduced, then closed. Before the import gate, this
    assertion failed with ``len(ctx.blocked) == 0`` and the child process
    genuinely ran, unaudited -- see the harness build report."""
    ctx = _new_ctx(tmp_path, "mp_spawn")
    with activate(ctx):
        try:
            multiprocessing.get_context("spawn").Process(target=_mp_noop).start()
        except Exception:
            pass
    assert len(ctx.blocked) == 1
    assert ctx.blocked[0].kind == "process"
    assert "multiprocessing" in ctx.blocked[0].detail


def test_declared_multiprocessing_is_allowed(tmp_path: Path) -> None:
    root = tmp_path / "sandbox_mp_declared"
    root.mkdir()
    ctx = SandboxContext(
        sandbox_root=str(root), declared_process_names=frozenset({"multiprocessing", "fork"})
    )
    with activate(ctx):
        p = multiprocessing.get_context("spawn").Process(target=_mp_noop)
        p.start()
        p.join(timeout=10)
        exitcode = p.exitcode
    assert ctx.blocked == []
    assert exitcode == 0
