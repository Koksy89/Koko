"""The actual isolation mechanism: a process-wide audit hook, default-deny.

``sys.addaudithook`` (PEP 578) fires for network connects and DNS lookups,
file opens and filesystem mutations, and process spawning -- at the C level,
underneath ``socket``, ``os``, ``subprocess`` and everything built on them, no
matter how the target reaches for them. A hook that raises aborts the
operation and propagates the exception to the caller instead of letting it
complete; that is the enforcement, not a side channel to it. Once added, a
hook cannot be removed for the life of the interpreter (deliberately, on
Python's part) -- so a run that can install one has a guarantee that persists
for as long as the process does, and no code path, including a bug in this
module, can later switch it back off.

Enforcement is scoped to "is a run currently active", not to any logical flow
within one -- deliberately **not** a ``ContextVar``. A ``ContextVar`` is
designed to scope a value to one flow of control, and a plain
``threading.Thread`` starts with a fresh default context, so a value set in
the parent is invisible to it: the earlier version of this module used a
``ContextVar`` and a target thread escaped every control as a result (a
``threading.Thread`` that opened a socket saw no active context and was never
blocked, silently -- caught in verification, not by this module's own tests).
``asyncio`` tasks *are* visible to a ``ContextVar`` (they copy the creating
context), which is exactly backwards: the case that already worked is the one
``ContextVar`` was built for, and the case that mattered -- a plain thread,
the ordinary shape of an ingestion or broker client -- is the one it does not
cover.

The fix is a plain module-level flag, guarded by a lock for the transitions
into and out of it. A module global is visible to every thread in the
process without cooperation from whatever created it, which is exactly the
property "a new thread cannot escape enforcement" needs.

Fail-closed at the boundary: tearing down at the end of a run cannot simply
flip the flag back to "inactive" the instant the scenario function returns,
because a daemon thread the scenario spawned and never joined may still be
running and may still act after that instant. ``activate`` snapshots the
threads alive before the run, joins every new *non-daemon* thread (the
process cannot exit until it finishes anyway, so this costs nothing beyond
latency already owed), and registers every new thread still alive after that
-- almost always a daemon -- against this run's context by thread identity,
so its later operations are still judged and still recorded rather than
falling through once the flag clears. Code that was never part of any run
(the harness's own bookkeeping, an unrelated test) is not swept in: only
threads this run is known to have spawned are tracked this way.
"""

from __future__ import annotations

import os
import sys
import threading
from contextlib import contextmanager
from typing import Iterator

from cascade_map.contracts import BlockedAttempt

from .errors import BlockedOperation

# ---------------------------------------------------------------------------
# Event classification
# ---------------------------------------------------------------------------

#: Every audit event that represents reaching off the machine, including DNS.
#: There is no allowlist here: ARCHITECTURE.md specifies none, so none exists.
#: A stub declared for a named external system bypasses this entirely by
#: construction -- it satisfies the import before any of these events fire.
NETWORK_EVENTS: frozenset[str] = frozenset(
    {
        "socket.connect",
        "socket.connect_ex",
        "socket.getaddrinfo",
        "socket.gethostbyname",
        "socket.gethostbyname_ex",
        "socket.gethostbyaddr",
        "socket.getnameinfo",
        "socket.sendmsg_afalg",
        "urllib.Request",
        "ftplib.connect",
        "smtplib.connect",
    }
)

#: Spawning or cloning a new process. Blocked unless the executable's basename
#: (or, for fork, the literal token "fork") is in the run config's declared set.
PROCESS_EVENTS: frozenset[str] = frozenset(
    {
        "os.system",
        "os.posix_spawn",
        "os.exec",
        "os.fork",
        "os.forkpty",
        "subprocess.Popen",
    }
)

#: Filesystem mutation events with no read/write ambiguity: touching one of
#: these is a write, at the path(s) extracted below. "open"/"os.open" are
#: handled separately because they can be a read or a write depending on mode.
FS_MUTATION_EVENTS: frozenset[str] = frozenset(
    {
        "os.mkdir",
        "os.rmdir",
        "os.remove",
        "os.truncate",
        "os.chmod",
        "os.chflags",
        "os.lchflags",
        "os.rename",
        "os.replace",
        "os.link",
        "os.symlink",
        "shutil.copyfile",
        "shutil.copymode",
        "shutil.copystat",
        "shutil.move",
        "shutil.rmtree",
        "shutil.unpack_archive",
        "shutil.make_archive",
    }
)

_OPEN_EVENTS: frozenset[str] = frozenset({"open", "os.open"})

#: Fired only by ``Harness._selftest_audit_hook``. Never a real Python event,
#: so it cannot collide with anything the target might legitimately do; it
#: exists purely to prove the hook is wired to the active context before a
#: single line of the target runs.
SELFTEST_EVENT = "cascade_map.selftest"


def _safe_repr(value: object, limit: int = 200) -> str:
    try:
        text = repr(value)
    except Exception:  # noqa: BLE001 - a hostile __repr__ must not break the guard
        text = "<unrepresentable>"
    return text if len(text) <= limit else text[:limit] + "...(truncated)"


def _stringify(value: object) -> str | None:
    """Best-effort path extraction. ``None`` means "not a path we can judge"."""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "surrogateescape")
    if isinstance(value, os.PathLike):
        try:
            return os.fspath(value)
        except TypeError:
            return None
    return None


def _flags_indicate_write(flags: object) -> bool:
    if not isinstance(flags, int):
        return False
    write_bits = 0
    for name in ("O_WRONLY", "O_RDWR", "O_CREAT", "O_APPEND", "O_TRUNC", "O_EXCL"):
        write_bits |= getattr(os, name, 0)
    return bool(flags & write_bits)


def _classify_open(event: str, args: tuple[object, ...]) -> tuple[str | None, bool]:
    """Return (path, is_write) for an "open"/"os.open" audit event."""
    if event == "open" and len(args) == 3:
        file, mode, flags = args
        path = _stringify(file)
        if path is None:
            return None, False
        is_write = isinstance(mode, str) and any(c in mode for c in "wax+")
        is_write = is_write or _flags_indicate_write(flags)
        return path, is_write
    if event == "os.open" and len(args) == 3:
        path_arg, flags, _mode = args
        path = _stringify(path_arg)
        if path is None:
            return None, False
        return path, _flags_indicate_write(flags)
    return None, False


def _extract_write_paths(event: str, args: tuple[object, ...]) -> list[str]:
    """Paths a filesystem-mutation event touches. Both ends of a rename/link
    matter: either one landing outside the sandbox is a write outside it."""
    if event in (
        "os.mkdir",
        "os.rmdir",
        "os.remove",
        "os.truncate",
        "os.chmod",
        "os.chflags",
        "os.lchflags",
        "shutil.rmtree",
    ):
        path = _stringify(args[0]) if args else None
        return [path] if path else []
    if event in (
        "os.rename",
        "os.replace",
        "os.link",
        "os.symlink",
        "shutil.copyfile",
        "shutil.copymode",
        "shutil.copystat",
        "shutil.move",
    ):
        return [p for a in args[:2] if (p := _stringify(a)) is not None]
    if event in ("shutil.unpack_archive", "shutil.make_archive"):
        return [p for a in args if (p := _stringify(a)) is not None]
    return []


def _extract_executable(event: str, args: tuple[object, ...]) -> str | None:
    """The program name a process-spawn event names, or ``None`` if it names
    none (``os.fork``/``os.forkpty``: there is nothing to declare but "fork")."""
    if event == "os.system":
        cmd = args[0] if args else None
        if isinstance(cmd, str) and cmd.strip():
            return os.path.basename(cmd.strip().split()[0])
        return None
    if event == "subprocess.Popen" and len(args) >= 2:
        executable, exec_args = args[0], args[1]
        if executable:
            return os.path.basename(str(executable))
        if isinstance(exec_args, (list, tuple)) and exec_args:
            return os.path.basename(str(exec_args[0]))
        if isinstance(exec_args, str):
            return os.path.basename(exec_args)
        return None
    if event in ("os.exec", "os.posix_spawn") and args:
        path = args[0]
        return os.path.basename(str(path)) if path else None
    return None


def within_sandbox(path: str, sandbox_root: str) -> bool:
    """True when *path*, fully resolved, lands inside *sandbox_root*.

    ``realpath`` resolves ``..`` segments and symlinks along the way, so an
    absolute-path escape, a ``..`` escape and a symlink escape are all caught
    by the same check: whatever the path claims to be, this is where it
    actually points.
    """
    try:
        real = os.path.realpath(path)
        root = os.path.realpath(sandbox_root)
    except (OSError, ValueError):
        return False
    return real == root or real.startswith(root + os.sep)


# ---------------------------------------------------------------------------
# The sandbox context: one per run, holds everything the hook needs to judge
# ---------------------------------------------------------------------------


class SandboxContext:
    """State for one harness run: what is allowed, and what has been blocked."""

    def __init__(self, sandbox_root: str, declared_process_names: frozenset[str]) -> None:
        self.sandbox_root = sandbox_root
        self.declared_process_names = declared_process_names
        self.blocked: list[BlockedAttempt] = []
        self.reads_outside_sandbox: list[BlockedAttempt] = []
        self.selftest_token: str = ""
        self.selftest_seen: bool = False
        self._counter = 0

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}#{self._counter}"

    def _record_blocked(self, kind: str, detail: str) -> BlockedAttempt:
        attempt = BlockedAttempt(id=self._next_id("blocked"), kind=kind, detail=detail)
        self.blocked.append(attempt)
        return attempt

    def _record_read_outside(self, detail: str) -> BlockedAttempt:
        attempt = BlockedAttempt(
            id=self._next_id("read_outside_sandbox"),
            kind="filesystem_read_outside_sandbox",
            detail=detail,
        )
        self.reads_outside_sandbox.append(attempt)
        return attempt

    # -- the dispatch entry point, called for every audited event ----------

    def handle_event(self, event: str, args: tuple[object, ...]) -> None:
        if event == SELFTEST_EVENT:
            if args and args[0] == self.selftest_token:
                self.selftest_seen = True
            return
        if event in NETWORK_EVENTS:
            self._handle_network(event, args)
            return
        if event in PROCESS_EVENTS:
            self._handle_process(event, args)
            return
        if event in _OPEN_EVENTS:
            self._handle_open(event, args)
            return
        if event in FS_MUTATION_EVENTS:
            self._handle_mutation(event, args)
            return

    def _handle_network(self, event: str, args: tuple[object, ...]) -> None:
        detail = f"{event} args={_safe_repr(args)}"
        self._record_blocked("network", detail)
        raise BlockedOperation(f"network blocked at the socket layer: {event}")

    def _handle_process(self, event: str, args: tuple[object, ...]) -> None:
        if event in ("os.fork", "os.forkpty"):
            allowed = "fork" in self.declared_process_names or event in self.declared_process_names
        else:
            exe = _extract_executable(event, args)
            allowed = exe is not None and exe in self.declared_process_names
        if allowed:
            return
        detail = f"{event} args={_safe_repr(args)}"
        self._record_blocked("process", detail)
        raise BlockedOperation(f"process spawn blocked (not declared in run config): {event}")

    def _handle_open(self, event: str, args: tuple[object, ...]) -> None:
        path, is_write = _classify_open(event, args)
        if path is None:
            return
        if is_write:
            if not within_sandbox(path, self.sandbox_root):
                detail = f"{event} write to {path!r}, outside the sandbox"
                self._record_blocked("filesystem_write", detail)
                raise BlockedOperation(f"filesystem write blocked, outside sandbox: {path!r}")
        elif not within_sandbox(path, self.sandbox_root):
            self._record_read_outside(f"{event} read {path!r}, outside the sandbox")

    def _handle_mutation(self, event: str, args: tuple[object, ...]) -> None:
        for path in _extract_write_paths(event, args):
            if not within_sandbox(path, self.sandbox_root):
                detail = f"{event} on {path!r}, outside the sandbox"
                self._record_blocked("filesystem_write", detail)
                raise BlockedOperation(f"filesystem mutation blocked, outside sandbox: {path!r}")


# ---------------------------------------------------------------------------
# Process-wide installation, run-scoped activation
# ---------------------------------------------------------------------------

#: The currently active run, or ``None``. A plain module global, not a
#: ``ContextVar``: every thread in the process reads the same object with no
#: cooperation required from whatever created the thread. Reads in the hot
#: path (``_dispatch``) are lock-free -- a bare reference read/write is
#: atomic under the GIL -- and are correct under concurrent mutation because
#: every transition below only ever narrows *which* context a given event is
#: judged against, never removes judgement entirely for a thread this run
#: spawned. See ``_lingering`` for the one case that needs more care.
_active_ctx: SandboxContext | None = None
_state_lock = threading.Lock()

#: Thread identities a run's teardown found still alive after joining every
#: non-daemon one -- almost always a daemon thread. Judged against that run's
#: context even after ``_active_ctx`` has moved on, so a late action from a
#: thread a run spawned is still blocked and still recorded instead of
#: silently passing through once enforcement "ends" for the run that spawned
#: it. Idents are pruned once the thread is no longer alive, since Python (and
#: the OS underneath it) reuses thread identities and an unpruned entry could
#: misattribute a later, unrelated thread's actions to a long-finished run --
#: itself a fail-*closed* mistake (an extra block), never a fail-open one.
_lingering: dict[int, SandboxContext] = {}

_hook_installed = False


def _dispatch(event: str, args: tuple[object, ...]) -> None:
    ctx = _active_ctx
    if ctx is None:
        ctx = _lingering.get(threading.get_ident())
        if ctx is None:
            return
    ctx.handle_event(event, args)


def install_hook() -> None:
    """Install the process-wide audit hook, once. Idempotent and permanent:
    Python does not offer a way to remove an audit hook, by design."""
    global _hook_installed
    if _hook_installed:
        return
    sys.addaudithook(_dispatch)
    _hook_installed = True


def _prune_lingering() -> None:
    alive = {t.ident for t in threading.enumerate() if t.ident is not None}
    for ident in [i for i in _lingering if i not in alive]:
        _lingering.pop(ident, None)


@contextmanager
def activate(ctx: SandboxContext) -> Iterator[SandboxContext]:
    """Make *ctx* the context the process-wide hook enforces, for this block
    -- and, fail-closed, for any thread the block spawns and does not clean
    up after itself, for as long as that thread remains alive.
    """
    global _active_ctx
    install_hook()
    _prune_lingering()
    before = {t.ident for t in threading.enumerate() if t.ident is not None}
    with _state_lock:
        previous = _active_ctx
        _active_ctx = ctx
    try:
        yield ctx
    finally:
        current = threading.current_thread()
        spawned = [
            t
            for t in threading.enumerate()
            if t.ident is not None and t.ident not in before and t is not current
        ]
        # The process cannot exit while a non-daemon thread is alive, so
        # waiting for one here adds no latency the caller was not already
        # going to pay -- it only moves that wait inside the enforced window
        # instead of after it, which is the point.
        for t in spawned:
            if not t.daemon:
                t.join(timeout=5.0)
        with _state_lock:
            for t in spawned:
                if t.is_alive():
                    _lingering[t.ident] = ctx  # type: ignore[index]
            _active_ctx = previous
