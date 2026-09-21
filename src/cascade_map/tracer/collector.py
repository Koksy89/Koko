"""In-process collection. A component of a card 11 run -- never its own process.

There is no ``subprocess``, ``os.system``, ``fork`` or ``exec`` anywhere in
this package, and a test asserts it. The harness owns the process, its
filesystem and its network; the tracer is installed *inside* that process and
writes down what it sees.

Before it writes anything down it checks the harness's own controls. If the
run refused, or a required guarantee is missing or off, collection raises
`TraceRefused` and no tracing is installed at all. A refusal is the correct
outcome, never a warning to proceed past.
"""

from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from types import CodeType, FrameType
from typing import Any, Callable, Sequence

from cascade_map.contracts.interfaces import RunRecord, ValueCapture

from .capture import MISSING, capture_values, dropped
from .limits import DEFAULT_LIMITS, CaptureLimits, RedactionPolicy
from .nondeterminism import names_in
from .recording import RECORDING_VERSION, ObsKind, RawObservation, Recording
from .static_index import CodeLocation, StaticIndex

__all__ = [
    "TraceRefused",
    "TraceCollector",
    "DEFAULT_REQUIRED_CONTROLS",
    "refusal_reason",
]

DEFAULT_REQUIRED_CONTROLS: tuple[str, ...] = ("network", "filesystem", "subprocess")

_OWN_PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
"""CASCADE-MAP's own frames are not observations of the target. They are
skipped and not counted: counting the tracer's own ``__exit__`` as external
target code would put noise in the mapping report.""" 

_CONTROL_NOISE = frozenset(
    {
        "block",
        "blocked",
        "blocking",
        "deny",
        "denied",
        "default",
        "is",
        "active",
        "enabled",
        "on",
        "sandbox",
        "sandboxed",
        "redirect",
        "redirected",
        "isolation",
        "isolated",
        "control",
        "controls",
        "guard",
        "guarded",
        "outbound",
        "mode",
        "a",
    }
)

_CONTROL_ALIASES = {
    "net": "network",
    "network": "network",
    "networking": "network",
    "fs": "filesystem",
    "file": "filesystem",
    "files": "filesystem",
    "filesystem": "filesystem",
    "write": "filesystem",
    "writes": "filesystem",
    "process": "subprocess",
    "processes": "subprocess",
    "spawn": "subprocess",
    "spawning": "subprocess",
    "fork": "subprocess",
    "subprocess": "subprocess",
}


class TraceRefused(RuntimeError):
    """Collection refused. Carries the guarantee that could not be made."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _tokens(key: str) -> set[str]:
    parts: list[str] = []
    current = ""
    for char in key.lower():
        if char.isalnum():
            current += char
        else:
            parts.append(current)
            current = ""
    parts.append(current)
    out: set[str] = set()
    for part in parts:
        if not part or part in _CONTROL_NOISE:
            continue
        out.add(_CONTROL_ALIASES.get(part, part))
    return out


def refusal_reason(
    run: RunRecord, required: Sequence[str] = DEFAULT_REQUIRED_CONTROLS
) -> str:
    """Why this run may not be traced, or "" when it may.

    The tracer does not enforce isolation -- card 11 does -- but it refuses to
    produce evidence from a run whose isolation was never established. Trusting
    the flag would make a trace of an unsandboxed run indistinguishable from a
    trace of a sandboxed one.
    """
    if run.refused:
        return (
            f"run {run.run_id} refused to start"
            + (f": {run.refusal_reason}" if run.refusal_reason else "")
            + "; there is nothing to trace"
        )
    controls = dict(run.controls_active or {})
    for name in required:
        matching = {key: value for key, value in controls.items() if name in _tokens(key)}
        if not matching:
            return (
                f"run {run.run_id} does not report a {name!r} control "
                f"(controls reported: {sorted(controls) or 'none'}); the tracer cannot "
                "confirm the run is incapable of real-world side effects"
            )
        off = sorted(key for key, value in matching.items() if not value)
        if off:
            return (
                f"run {run.run_id} reports the {name!r} control inactive "
                f"({', '.join(off)}); tracing would record a run that could reach the "
                "real world"
            )
    return ""


@dataclass(slots=True)
class _CodeMeta:
    path: str
    own: bool
    qualname: str
    first_line: int
    under_root: bool
    synthetic: bool
    traced: bool
    element_id: str
    trace_lines: bool
    branch_lines: dict[int, str]
    feature_lines: dict[int, tuple[str, ...]]
    handler_lines: set[int]
    nd_names: tuple[str, ...]


@dataclass(slots=True)
class _FrameState:
    meta: _CodeMeta
    frame_key: int
    parent_key: int
    depth: int
    thread_slot: int
    pending_branch: str = ""
    pending_branch_line: int = 0
    pending_features: tuple[str, ...] = ()
    pending_feature_line: int = 0
    exception_seen: bool = False
    handler_seen: bool = False


class TraceCollector:
    """Installs the trace hook, writes observations, and gets out of the way."""

    def __init__(
        self,
        run: RunRecord,
        index: StaticIndex,
        *,
        limits: CaptureLimits = DEFAULT_LIMITS,
        policy: RedactionPolicy | None = None,
        max_observations: int = 200_000,
        required_controls: Sequence[str] = DEFAULT_REQUIRED_CONTROLS,
        trace_dynamic: bool = True,
    ) -> None:
        reason = refusal_reason(run, required_controls)
        if reason:
            raise TraceRefused(reason)
        self.run = run
        self.index = index
        self.limits = limits
        self.policy = policy if policy is not None else RedactionPolicy()
        self.max_observations = max_observations
        self.trace_dynamic = trace_dynamic
        self.required_controls = tuple(required_controls)

        self._lock = threading.Lock()
        self._observations: list[RawObservation] = []
        self._meta: dict[CodeType, _CodeMeta] = {}
        self._frames: dict[FrameType, _FrameState] = {}
        self._stacks: dict[int, list[_FrameState]] = {}
        self._slots: dict[int, int] = {}
        self._thread_seq: dict[int, int] = {}
        self._external: dict[str, int] = {}
        self._seen_frames: set[int] = set()
        self._arrival = 0
        self._frame_keys = 0
        self._truncated = 0
        self._active = False

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> "TraceCollector":
        reason = refusal_reason(self.run, self.required_controls)
        if reason:
            raise TraceRefused(reason)
        if self._active:
            raise RuntimeError("collector already started")
        self._active = True
        threading.settrace(self._dispatch)
        sys.settrace(self._dispatch)
        return self

    def stop(self) -> None:
        if not self._active:
            return
        sys.settrace(None)
        threading.settrace(None)  # type: ignore[arg-type]
        self._active = False
        self._frames.clear()
        self._stacks.clear()

    def __enter__(self) -> "TraceCollector":
        return self.start()

    def __exit__(self, *exc: object) -> bool:
        self.stop()
        return False

    # -- output ------------------------------------------------------------

    def recording(self) -> Recording:
        header: dict[str, Any] = {
            "recording_version": RECORDING_VERSION,
            "run_id": self.run.run_id,
            "observation_count": len(self._observations),
            "dropped_observations": self._truncated,
            "max_observations": self.max_observations,
            "hash_randomization": bool(sys.flags.hash_randomization),
            "unguaranteed": list(getattr(self.run, "unguaranteed", ()) or ()),
            "thread_count": len(self._slots),
            "thread_slots": sorted(self._slots.values()),
            "external_frames": {key: self._external[key] for key in sorted(self._external)},
            "redaction_from_profile": self.policy.from_profile,
            "limits": {
                "max_repr_chars": self.limits.max_repr_chars,
                "max_event_chars": self.limits.max_event_chars,
                "max_items": self.limits.max_items,
                "sample_items": self.limits.sample_items,
                "max_string_sample": self.limits.max_string_sample,
                "max_columns": self.limits.max_columns,
                "count_nulls": self.limits.count_nulls,
                "capture_self": self.limits.capture_self,
            },
        }
        return Recording(header=header, observations=tuple(self._observations))

    # -- the hook ----------------------------------------------------------

    def _dispatch(self, frame: FrameType, event: str, arg: Any) -> Callable[..., Any] | None:
        if event != "call" or not self._active:
            return None
        meta = self._meta_for(frame.f_code)
        if not meta.traced:
            if meta.own:
                return None
            with self._lock:
                self._external[meta.path] = self._external.get(meta.path, 0) + 1
            return None
        ident = threading.get_ident()
        slot = self._slot(ident)
        stack = self._stacks.setdefault(ident, [])
        parent = stack[-1].frame_key if stack else 0
        state = _FrameState(
            meta=meta,
            frame_key=self._next_frame_key(),
            parent_key=parent,
            depth=len(stack),
            thread_slot=slot,
        )
        stack.append(state)
        self._frames[frame] = state
        detail: dict[str, str] = {}
        if meta.nd_names:
            detail["nd_names"] = ",".join(meta.nd_names)
        if id(frame) in self._seen_frames:
            detail["resumed"] = "1"
        else:
            self._seen_frames.add(id(frame))
        self._record(ObsKind.CALL, state, frame.f_lineno, self._call_values(frame, meta), detail)
        frame.f_trace_lines = meta.trace_lines
        return self._local

    def _local(self, frame: FrameType, event: str, arg: Any) -> Callable[..., Any] | None:
        state = self._frames.get(frame)
        if state is None or not self._active:
            return None
        if event == "line":
            self._on_line(frame, state)
        elif event == "return":
            self._on_return(frame, state, arg)
        elif event == "exception":
            self._on_exception(frame, state, arg)
        return self._local

    # -- per-event handling -------------------------------------------------

    def _on_line(self, frame: FrameType, state: _FrameState) -> None:
        line = frame.f_lineno
        self._flush_features(frame, state)
        if state.pending_branch:
            self._record(
                ObsKind.BRANCH_NEXT,
                state,
                line,
                {},
                {
                    "block_id": state.pending_branch,
                    "cond_line": str(state.pending_branch_line),
                    "via": "line",
                },
            )
            state.pending_branch = ""
        meta = state.meta
        if state.exception_seen and line in meta.handler_lines:
            state.handler_seen = True
            self._record(ObsKind.HANDLER, state, line, {}, {})
        block = meta.branch_lines.get(line)
        if block:
            decision = (
                self.index.decision_at(meta.element_id, line, meta.path)
                if meta.element_id
                else None
            )
            names = self.index.reads_names(decision)
            raw = {name: frame.f_locals.get(name, MISSING) for name in names}
            self._record(
                ObsKind.BRANCH_COND,
                state,
                line,
                capture_values(
                    raw, limits=self.limits, policy=self.policy, element_id=meta.element_id
                ),
                {
                    "block_id": block,
                    "decision_id": decision.id if decision is not None else "",
                    "condition": decision.condition_source if decision is not None else "",
                    "is_sink": "1" if decision is not None and decision.is_sink else "0",
                },
            )
            state.pending_branch = block
            state.pending_branch_line = line
        features = meta.feature_lines.get(line)
        if features:
            state.pending_features = features
            state.pending_feature_line = line

    def _on_return(self, frame: FrameType, state: _FrameState, arg: Any) -> None:
        self._flush_features(frame, state)
        if state.pending_branch:
            self._record(
                ObsKind.BRANCH_NEXT,
                state,
                frame.f_lineno,
                {},
                {
                    "block_id": state.pending_branch,
                    "cond_line": str(state.pending_branch_line),
                    "via": "return",
                },
            )
            state.pending_branch = ""
        unwinding = state.exception_seen and not state.handler_seen and arg is None
        if unwinding:
            values = {
                "return_value": dropped(
                    "the frame left via an exception, so there is no return value",
                    "",
                )
            }
        else:
            values = capture_values(
                {"return_value": arg},
                limits=self.limits,
                policy=self.policy,
                element_id=state.meta.element_id,
            )
        detail = {
            "exception_seen": "1" if state.exception_seen else "0",
            "handler_seen": "1" if state.handler_seen else "0",
            "unwinding": "1" if unwinding else "0",
        }
        self._record(ObsKind.RETURN, state, frame.f_lineno, values, detail)
        stack = self._stacks.get(threading.get_ident())
        if stack and stack[-1] is state:
            stack.pop()
        elif stack and state in stack:
            stack.remove(state)
        self._frames.pop(frame, None)

    def _on_exception(self, frame: FrameType, state: _FrameState, arg: Any) -> None:
        state.exception_seen = True
        exc_type = arg[0] if isinstance(arg, tuple) and arg else None
        exc_value = arg[1] if isinstance(arg, tuple) and len(arg) > 1 else None
        type_name = getattr(exc_type, "__name__", "<unknown>")
        values = capture_values(
            {"exception_type": type_name, "exception_message": exc_value},
            limits=self.limits,
            policy=self.policy,
            element_id=state.meta.element_id,
        )
        self._record(
            ObsKind.EXCEPTION,
            state,
            frame.f_lineno,
            values,
            {"exception_type": type_name},
        )

    def _flush_features(self, frame: FrameType, state: _FrameState) -> None:
        if not state.pending_features:
            return
        pending = state.pending_features
        line = state.pending_feature_line
        state.pending_features = ()
        for feature_id in pending:
            name = feature_id.split(":", 1)[1] if ":" in feature_id else feature_id
            value = _feature_value(frame, name)
            self._record(
                ObsKind.FEATURE,
                state,
                line,
                capture_values(
                    {name: value},
                    limits=self.limits,
                    policy=self.policy,
                    element_id=feature_id,
                ),
                {"feature_id": feature_id, "feature_name": name},
            )

    # -- plumbing ----------------------------------------------------------

    def _meta_for(self, code: CodeType) -> _CodeMeta:
        cached = self._meta.get(code)
        if cached is not None:
            return cached
        own = os.path.abspath(code.co_filename).startswith(_OWN_PACKAGE + os.sep)
        location = self.index.relocate(code.co_filename)
        qualname = getattr(code, "co_qualname", code.co_name)
        full = CodeLocation(
            path=location.path,
            qualname=qualname,
            line=code.co_firstlineno,
            first_line=code.co_firstlineno,
            under_root=location.under_root,
            synthetic=location.synthetic,
        )
        traced = (not own) and (
            location.under_root or (location.synthetic and self.trace_dynamic)
        )
        mapping = self.index.map_code(full) if traced else None
        meta = _CodeMeta(
            path=location.path,
            own=own,
            qualname=qualname,
            first_line=code.co_firstlineno,
            under_root=location.under_root,
            synthetic=location.synthetic,
            traced=traced,
            element_id=mapping.element_id if mapping is not None else "",
            trace_lines=traced and self.index.traces_lines(location.path),
            branch_lines=self.index.branch_lines(location.path) if traced else {},
            feature_lines=self.index.feature_lines(location.path) if traced else {},
            handler_lines=self.index.handler_lines(location.path) if traced else set(),
            nd_names=names_in(code.co_names) if traced else (),
        )
        self._meta[code] = meta
        return meta

    def _call_values(self, frame: FrameType, meta: _CodeMeta) -> dict[str, ValueCapture]:
        code = frame.f_code
        count = code.co_argcount + code.co_kwonlyargcount
        names = list(code.co_varnames[:count])
        index = count
        if code.co_flags & 0x04 and index < len(code.co_varnames):
            names.append(code.co_varnames[index])
            index += 1
        if code.co_flags & 0x08 and index < len(code.co_varnames):
            names.append(code.co_varnames[index])
        locals_ = frame.f_locals
        raw: dict[str, Any] = {}
        skipped: dict[str, ValueCapture] = {}
        for name in names:
            if name in ("self", "cls") and not self.limits.capture_self:
                skipped[name] = dropped(
                    "receiver not captured: capture_self is off, which keeps method calls "
                    "cheap; set CaptureLimits(capture_self=True) to capture it",
                    type(locals_.get(name)).__qualname__ if name in locals_ else "",
                )
                continue
            raw[name] = locals_.get(name, MISSING)
        captured = capture_values(
            raw, limits=self.limits, policy=self.policy, element_id=meta.element_id
        )
        captured.update(skipped)
        return captured

    def _slot(self, ident: int) -> int:
        with self._lock:
            slot = self._slots.get(ident)
            if slot is None:
                slot = len(self._slots)
                self._slots[ident] = slot
            return slot

    def _next_frame_key(self) -> int:
        with self._lock:
            self._frame_keys += 1
            return self._frame_keys

    def _record(
        self,
        kind: ObsKind,
        state: _FrameState,
        line: int,
        values: dict[str, ValueCapture],
        detail: dict[str, str],
    ) -> None:
        with self._lock:
            if self._arrival >= self.max_observations:
                self._truncated += 1
                return
            self._arrival += 1
            arrival = self._arrival
            seq = self._thread_seq.get(state.thread_slot, 0) + 1
            self._thread_seq[state.thread_slot] = seq
            self._observations.append(
                RawObservation(
                    kind=kind,
                    thread_slot=state.thread_slot,
                    thread_seq=seq,
                    arrival=arrival,
                    frame_key=state.frame_key,
                    parent_frame_key=state.parent_key,
                    depth=state.depth,
                    path=state.meta.path,
                    line=line,
                    first_line=state.meta.first_line,
                    qualname=state.meta.qualname,
                    under_root=state.meta.under_root,
                    synthetic=state.meta.synthetic,
                    values=values,
                    detail=detail,
                )
            )


def _feature_value(frame: FrameType, name: str) -> Any:
    """Read a tracked feature out of the running frame, or report it missing.

    Local name first, then any mapping or frame-like local that holds the key
    -- scanned in sorted order so the answer does not depend on dict ordering.
    A feature that cannot be reached becomes an explicit DROPPED capture.
    """
    locals_ = frame.f_locals
    if name in locals_:
        return locals_[name]
    for key in sorted(locals_):
        container = locals_[key]
        try:
            if isinstance(container, dict):
                if name in container:
                    return container[name]
                continue
            columns = getattr(container, "columns", None)
            if columns is not None and name in list(columns):
                return container[name]
        except BaseException:  # noqa: BLE001
            continue
    if name in frame.f_globals:
        return frame.f_globals[name]
    return MISSING
