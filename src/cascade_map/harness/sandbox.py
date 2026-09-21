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

Enforcement answers exactly one question, for every audited event, on every
thread: **is a run currently active?** Two designs for answering it have
already failed, and the failures are the reason this one looks the way it
does.

Round one used a ``ContextVar``. A ``ContextVar`` scopes a value to one
logical flow of control, and a plain ``threading.Thread`` starts with a
fresh default context -- a value set in the parent is invisible to it. A
target thread that opened a socket saw no active context and was never
blocked. Fixed by moving to a plain module-level global, visible to every
thread with no cooperation required.

Round two kept the global but tried to answer "is anything still running?"
at block-exit time by *enumerating* threads: snapshot before, diff after,
join what's new, register what's still alive by thread identity so its later
actions stay judged after the flag itself cleared. Two more escapes followed
the same shape: a ``threading.Thread`` *constructed* inside the active block
but *started* after it returns is invisible to ``threading.enumerate()``
until it starts, by which point the snapshot has already been taken and the
flag has already cleared; and ``_thread.start_new_thread`` bypasses
``threading``'s bookkeeping entirely and never appears in
``threading.enumerate()`` at all. Both escapes were the same root cause as
round one wearing a different disguise: enforcement depended on *knowing
which threads exist*, and there is always another way to create one the
registry does not see.

**The fix is to stop trying to know.** There is no registry, no snapshot, no
thread-identity tracking anywhere in this module. ``activate`` sets one
module-level pointer on entry. On exit it makes a best-effort, bounded
attempt to join non-daemon threads it can currently see -- pure hygiene, not
a correctness mechanism, and not treated as proof of anything -- and then
**does not clear the pointer**. A ``Thread`` object can be constructed and
started from arbitrary later code with no observable trace at the moment
``activate`` tears down, so there is no sound moment to declare "nothing from
this run can still be running." Failing closed means treating that as true
indefinitely rather than guessing it is false: enforcement for a run ends
when a later run's ``activate`` call replaces it, or at process exit,
whichever comes first -- never at block exit. An over-long window costs a
spurious block (a refusal, the safe direction); a short one costs the
real-world side effect this card exists to prevent.

This has one real consequence worth naming: the harness's own bookkeeping
(creating the sandbox directory, writing ``run.json`` after the scenario
returns) happens *after* a window that may never close, and would otherwise
be judged against a stale run's sandbox the moment a second run starts in
the same process. It is not exempted by tracking *who* is asking (that is
the same mistake in a new place) -- it is exempted by directory, via
``register_trusted_root``: the harness's own sandbox root and its Mode B
output directory are always-writable regardless of which run (if any) is
currently active, because they are tool-owned locations the scenario is
never told the path to, not real-world side-effect targets. In production
this whole question is close to moot: one ``cascade-map trace`` invocation
is one process that exits once its run record is written, so "ends at
process exit" costs nothing there. It only bites inside a single process
that runs the harness many times in a row, which is exactly what this
module's own test suite does -- deliberately, to prove the escapes are
closed.
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

#: ``multiprocessing``'s "spawn" start method launches the child by calling
#: ``_posixsubprocess.fork_exec`` directly (see ``multiprocessing.util.
#: spawnv_passfds``), bypassing ``subprocess.Popen.__init__`` entirely --
#: which is the only place the "subprocess.Popen" audit event is actually
#: raised. Nothing in ``PROCESS_EVENTS`` fires for it: verified empirically,
#: not assumed (see the harness build report). The one place this path is
#: reliably observable is the ``import`` of the backend module that performs
#: it, which every start method loads lazily, only once a process is about
#: to actually be launched -- so that import is where this control gates
#: multiprocessing, declared through the same mechanism as everything else
#: in ``declared_process_names``, via the literal token ``"multiprocessing"``.
_MULTIPROCESSING_LAUNCH_MODULES: frozenset[str] = frozenset(
    {
        "multiprocessing.popen_spawn_posix",
        "multiprocessing.popen_spawn_win32",
        "multiprocessing.popen_forkserver",
        "multiprocessing.forkserver",
    }
)

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
    """True when *path*, fully resolved, lands inside *sandbox_root* -- or
    inside a directory ``register_trusted_root`` has marked as the harness's
    own (see there for why that check belongs here too: it is the same
    "is this write somewhere we control" question, just answered by path
    instead of by which run happens to be current).

    ``realpath`` resolves ``..`` segments and symlinks along the way, so an
    absolute-path escape, a ``..`` escape and a symlink escape are all caught
    by the same check: whatever the path claims to be, this is where it
    actually points.
    """
    if _is_trusted(path):
        return True
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
        if event == "import":
            self._handle_import(event, args)
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

    def _handle_import(self, event: str, args: tuple[object, ...]) -> None:
        module = args[0] if args else None
        if not isinstance(module, str) or module not in _MULTIPROCESSING_LAUNCH_MODULES:
            return
        if "multiprocessing" in self.declared_process_names:
            return
        detail = f"import {module!r} (multiprocessing process-launch backend)"
        self._record_blocked("process", detail)
        raise BlockedOperation(
            f"process spawn blocked (not declared in run config): import {module}"
        )

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

#: The currently active run, or ``None`` if no run has ever started in this
#: process. A plain module global: every thread reads the same object with no
#: cooperation required from whatever created the thread, and no registry of
#: "which threads exist" is consulted anywhere below -- see the module
#: docstring for why that registry is exactly what round two's escapes broke.
#: Reads in the hot path (``_dispatch``) are lock-free -- a bare reference
#: read is atomic under the GIL.
_active_ctx: SandboxContext | None = None
_state_lock = threading.Lock()

_hook_installed = False

#: Directories the harness's own code may always write to, regardless of
#: which run (if any) is currently active, or whether enforcement has ever
#: cleared at all. Populated by ``register_trusted_root`` -- a run's sandbox
#: root and its Mode B output directory (where ``run.json`` lands). These are
#: exempted *by path*, not by tracking who is asking (the caller's identity
#: is exactly what this module stopped trying to know): the scenario is never
#: told either path, so widening trust for them does not widen what the
#: scenario itself can reach.
_trusted_roots: set[str] = set()
_trusted_roots_lock = threading.Lock()


def register_trusted_root(path: str) -> None:
    """Mark *path* as always-writable by the harness's own bookkeeping.

    Call this once per directory, before anything might write to it. Safe to
    call before the directory exists -- resolution is lexical, matching
    ``within_sandbox``.
    """
    with _trusted_roots_lock:
        _trusted_roots.add(os.path.realpath(path))


def _is_trusted(path: str) -> bool:
    try:
        real = os.path.realpath(path)
    except (OSError, ValueError):
        return False
    return any(real == root or real.startswith(root + os.sep) for root in _trusted_roots)


def _reset_for_tests() -> None:
    """Return to the same "no run has ever started in this process" state a
    fresh interpreter has.

    Not called anywhere in ``Harness`` or ``__init__.py``'s public surface --
    this is not a force flag, and no production code path reaches it. It
    exists because enforcement deliberately never clears itself (see the
    module docstring), which is correct for a real ``cascade-map trace``
    invocation, exiting as one process per run, and is friction for this
    module's own test suite, which runs many runs in one process on purpose
    to prove the escapes stay closed. Test files call this between tests --
    see ``tests/test_harness.py`` -- so each test's own setup code gets the
    same clean slate a fresh process would have, instead of being judged
    against whatever a previous, unrelated test left active.
    """
    global _active_ctx
    with _state_lock:
        _active_ctx = None
    with _trusted_roots_lock:
        _trusted_roots.clear()


def _dispatch(event: str, args: tuple[object, ...]) -> None:
    ctx = _active_ctx
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


@contextmanager
def activate(ctx: SandboxContext) -> Iterator[SandboxContext]:
    """Make *ctx* the context the process-wide hook enforces.

    Sets the pointer on entry; deliberately does **not** clear it on exit.
    See the module docstring: there is no sound way to prove a thread
    constructed during this block, or started via ``_thread.start_new_thread``
    (invisible to every ``threading`` API), cannot still run or still start
    later. A best-effort, bounded join of currently-visible non-daemon
    threads happens on the way out as hygiene -- it does not gate whether the
    pointer clears, because treating its success as proof is the exact
    mistake that let two different escapes through this module already.
    Enforcement for this run ends when a later ``activate`` call replaces the
    pointer, or at process exit.
    """
    global _active_ctx
    install_hook()
    with _state_lock:
        _active_ctx = ctx
    try:
        yield ctx
    finally:
        current = threading.current_thread()
        for t in threading.enumerate():
            if t is not current and not t.daemon and t.is_alive():
                t.join(timeout=5.0)
