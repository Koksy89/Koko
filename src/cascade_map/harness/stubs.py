"""Declared external clients: what the harness puts in front of a broker.

``RunConfig.client_stubs`` has always accepted a mapping of module name to a
factory returning a module. Nothing could reach it: a scenarios file is JSON
and JSON cannot hold a Python callable, so through the CLI every external
client was simply *undeclared*, and an undeclared client is a hard stop. Safe,
and it meant a Mode 2 run of an engine that talks to a broker or a database
stopped at the first call.

This module is the missing half: a **declaration** the owner writes in the
scenarios file, and a factory built from it.

    "client_stubs": {
      "broker_api":  {"kind": "blocked"},
      "market_db":   {"kind": "record", "returns": {"*": null, "ping": true}},
      "price_feed":  {"kind": "replay", "recording": "feeds/prices.json"}
    }

The hard rule is unchanged and is the reason this is narrow:

* An **undeclared** client is still a hard stop. Nothing here is a blanket
  exemption; every stub names exactly one module, and a module not named here
  reaches the real socket and file layer, where it is blocked like anything
  else.
* Declaring a stub is the owner taking responsibility for **one named
  module**. It is recorded in the run record, folded into the run ID, and
  printed by `preflight` before the run starts.
* Nothing is ever returned by accident. ``record`` refuses to invent a return
  value the owner did not declare, and ``replay`` refuses a call it has no
  recording for. Both refusals are recorded as ``BlockedAttempt``s and abort
  the scenario -- a stub that quietly answered ``None`` is a wrong answer
  wearing the shape of a right one, and the whole point of this tool is not
  to produce those.

Nothing in this module imports, executes or evaluates target code. It builds
module objects out of the owner's JSON and nothing else.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Mapping, Sequence

from cascade_map.contracts.interfaces import canonical_dumps
from cascade_map.harness.errors import BlockedOperation
from cascade_map.harness.sandbox import record_blocked

__all__ = [
    "STUB_KINDS",
    "BLOCKED_KIND",
    "StubDeclarationError",
    "ClientStubDeclaration",
    "parse_declarations",
    "declaration_fingerprint",
    "build_factories",
    "stub_module",
    "describe_declarations",
]


#: The kinds an owner may declare. Anything else is an error naming this list:
#: a misspelled "replay" that silently blocked would be the same defect class
#: as a misspelled setting key.
STUB_KINDS: tuple[str, ...] = ("blocked", "record", "replay")

BLOCKED_KIND = "client_stub"
"""``BlockedAttempt.kind`` for everything this module stops. Distinct from
``network`` and ``process``: this is a client the owner declared, stopped at
the client boundary rather than at the socket."""

#: The catch-all key in a ``record`` stub's ``returns`` map.
ANY = "*"

#: Same normalisation card 12 applies to captured values, duplicated here
#: rather than imported because the harness layer sits *below* the tracer and
#: must not depend on it. See `tracer/capture.py::stable_text` for why both
#: shapes are normalised: an address is not information, and it is different
#: on every run, which would break constraint 4 outright.
_ADDRESS = re.compile(r"(?<= at )(?:0x[0-9a-fA-F]+|[0-9]{6,})")

#: How much of one argument's repr is recorded. A cap, never a silent cut: the
#: recorded value says `...(truncated)` when it bites.
_REPR_LIMIT = 200


class StubDeclarationError(ValueError):
    """A client stub declaration the harness will not guess at.

    Raised in the PARENT process, before anything is executed, so a
    mistyped declaration is a refusal naming the module and the problem --
    never a child that crashes halfway through a run it should not have
    started.
    """


@dataclass(frozen=True, slots=True)
class ClientStubDeclaration:
    """One owner declaration, validated."""

    module: str
    kind: str
    returns: tuple[tuple[str, Any], ...] = ()
    recording: str = ""

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"kind": self.kind}
        if self.returns:
            body["returns"] = {key: value for key, value in self.returns}
        if self.recording:
            body["recording"] = self.recording
        return body


def parse_declarations(raw: Any) -> tuple[ClientStubDeclaration, ...]:
    """Validate the ``client_stubs`` block of a scenarios document.

    Every problem is raised, never repaired. The one thing this must not do is
    fall back to "blocked" on a declaration it cannot read: the owner would
    read the preflight line saying their client is declared and get a run that
    stopped at the first call for a reason nothing named.
    """
    if raw in (None, {}):
        return ()
    if not isinstance(raw, Mapping):
        raise StubDeclarationError(
            f"client_stubs must be a mapping of module name -> declaration, not "
            f"{type(raw).__name__}."
        )
    declarations: list[ClientStubDeclaration] = []
    for module in sorted(raw):
        body = raw[module]
        if not isinstance(module, str) or not module:
            raise StubDeclarationError(
                f"client_stubs key {module!r} is not a module name. The key is the name "
                f"the target imports, for example \"broker_api\"."
            )
        if isinstance(body, str):
            # A bare string is the obvious thing to write, so it is accepted
            # as the kind and nothing else -- never as a path.
            body = {"kind": body}
        if not isinstance(body, Mapping):
            raise StubDeclarationError(
                f"client stub {module!r} must be declared as a mapping with a \"kind\", "
                f"not {type(body).__name__}."
            )
        unknown = sorted(set(body) - {"kind", "returns", "recording"})
        if unknown:
            raise StubDeclarationError(
                f"client stub {module!r} has unknown key(s) {', '.join(unknown)}. "
                f"Valid keys: kind, returns, recording."
            )
        kind = body.get("kind")
        if kind not in STUB_KINDS:
            raise StubDeclarationError(
                f"client stub {module!r} declares kind {kind!r}. Valid kinds: "
                f"{', '.join(STUB_KINDS)} -- \"blocked\" stops every call and records it, "
                f"\"record\" captures every call and returns a declared default, "
                f"\"replay\" returns values from a recording you supply."
            )
        returns_raw = body.get("returns")
        recording = body.get("recording", "")
        if kind == "record":
            if not isinstance(returns_raw, Mapping) or not returns_raw:
                raise StubDeclarationError(
                    f"client stub {module!r} is kind \"record\" and needs a non-empty "
                    f"\"returns\" mapping: what each call should hand back. Use "
                    f"{{\"{ANY}\": null}} to say every call returns None -- saying it is "
                    f"the point, because a stub that invents a return value you did not "
                    f"declare is a wrong answer wearing the shape of a right one."
                )
            for key, value in returns_raw.items():
                if not isinstance(key, str):
                    raise StubDeclarationError(
                        f"client stub {module!r}: every key in \"returns\" is an attribute "
                        f"path such as \"fetch_rows\" or \"Client.connect\", or "
                        f"\"{ANY}\"; got {key!r}."
                    )
                _check_jsonable(module, key, value)
        elif returns_raw is not None:
            raise StubDeclarationError(
                f"client stub {module!r} is kind {kind!r}, which does not take "
                f"\"returns\". Only \"record\" does."
            )
        if kind == "replay":
            if not isinstance(recording, str) or not recording:
                raise StubDeclarationError(
                    f"client stub {module!r} is kind \"replay\" and needs a "
                    f"\"recording\" path: the file holding the values to return."
                )
        elif recording:
            raise StubDeclarationError(
                f"client stub {module!r} is kind {kind!r}, which does not take "
                f"\"recording\". Only \"replay\" does."
            )
        declarations.append(
            ClientStubDeclaration(
                module=module,
                kind=str(kind),
                returns=tuple(sorted((str(k), v) for k, v in (returns_raw or {}).items())),
                recording=str(recording or ""),
            )
        )
    return tuple(declarations)


def _check_jsonable(module: str, key: str, value: Any) -> None:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _check_jsonable(module, key, item)
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _check_jsonable(module, key, item)
        return
    raise StubDeclarationError(
        f"client stub {module!r}: the value for {key!r} must be JSON data "
        f"(null, a number, a string, a list or an object), not "
        f"{type(value).__name__}. A scenarios file cannot hold a Python object, "
        f"and a stub that constructed one would be running code nobody declared."
    )


def declaration_fingerprint(declarations: Sequence[ClientStubDeclaration]) -> str:
    """Canonical text of the declarations, for folding into the run ID.

    Two runs that stub the same module two different ways are two different
    runs and must not share a run ID -- the replay guarantee compares output
    keyed by that ID.
    """
    if not declarations:
        return ""
    return canonical_dumps(
        {item.module: item.to_dict() for item in sorted(declarations, key=lambda d: d.module)}
    )


def describe_declarations(declarations: Sequence[ClientStubDeclaration]) -> tuple[str, ...]:
    """One line per declared client, for `preflight` and the run summary."""
    lines: list[str] = []
    for item in sorted(declarations, key=lambda d: d.module):
        if item.kind == "blocked":
            detail = "every call is stopped and recorded"
        elif item.kind == "record":
            paths = ", ".join(key for key, _ in item.returns)
            detail = f"every call captured; declared returns for {paths}"
        else:
            detail = f"returns replayed from {item.recording}"
        lines.append(f"{item.module} [{item.kind}] -- {detail}")
    return tuple(lines)


# ---------------------------------------------------------------------------
# The stub modules themselves
# ---------------------------------------------------------------------------


def _stable_repr(value: Any) -> str:
    try:
        text = repr(value)
    except Exception as error:  # noqa: BLE001 - a target's __repr__ may raise
        return f"<unreprable {type(value).__name__}: {type(error).__name__}>"
    text = _ADDRESS.sub("0x...", text)
    if len(text) > _REPR_LIMIT:
        return text[:_REPR_LIMIT] + "...(truncated)"
    return text


def _stop(module: str, path: str, reason: str) -> None:
    """Record the attempt on the active sandbox and abort the operation.

    Recorded *before* raising, so a caller that catches ``BlockedOperation``
    loses nothing from the run record -- the same contract the audit hook
    keeps.
    """
    detail = f"{module}.{path}: {reason}"
    record_blocked(BLOCKED_KIND, detail)
    raise BlockedOperation(f"declared client stub stopped this call -- {detail}")


class _Call:
    """One attribute path on a stubbed module.

    Attribute access builds a longer path and never stops the run: a target
    doing ``from broker import Client`` then ``Client().submit(...)`` must get
    as far as the *call*, which is the thing worth recording. Only a call is
    answered, recorded or stopped.
    """

    __slots__ = ("_state", "_path")

    def __init__(self, state: "_State", path: str) -> None:
        self._state = state
        self._path = path

    def __getattr__(self, name: str) -> "_Call":
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _Call(self._state, f"{self._path}.{name}" if self._path else name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._state.call(self._path, args, kwargs)

    def __repr__(self) -> str:
        return f"<client stub {self._state.module}.{self._path} [{self._state.kind}]>"


class _State:
    """What one stubbed module knows and does. Deliberately tiny."""

    def __init__(
        self,
        declaration: ClientStubDeclaration,
        *,
        sandbox_root: Path,
        target_root: Path,
    ) -> None:
        self.module = declaration.module
        self.kind = declaration.kind
        self.returns: dict[str, Any] = {key: value for key, value in declaration.returns}
        self.calls: list[dict[str, Any]] = []
        self._counts: dict[str, int] = {}
        self._sandbox_root = sandbox_root
        self._replay: dict[str, list[Any]] = {}
        self._recording_path = ""
        if declaration.kind == "replay":
            self._recording_path = declaration.recording
            self._replay = _load_recording(
                declaration.module, declaration.recording, target_root
            )

    # -- the one entry point ----------------------------------------------

    def call(self, path: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        index = self._counts.get(path, 0)
        self._counts[path] = index + 1
        if self.kind == "blocked":
            self._append(path, index, args, kwargs, "blocked")
            _stop(
                self.module,
                path,
                'declared kind "blocked": this client is stopped at the client boundary, '
                "by your own declaration",
            )
        if self.kind == "record":
            if path in self.returns:
                value = self.returns[path]
            elif ANY in self.returns:
                value = self.returns[ANY]
            else:
                self._append(path, index, args, kwargs, "no declared return")
                self._flush()
                _stop(
                    self.module,
                    path,
                    f'declared kind "record" but "returns" declares nothing for '
                    f"{path!r} and has no {ANY!r} entry. Add one and re-run; this "
                    f"stub will not invent a return value",
                )
            self._append(path, index, args, kwargs, "recorded")
            self._flush()
            return value
        values = self._replay.get(path)
        if values is None:
            self._append(path, index, args, kwargs, "not in recording")
            _stop(
                self.module,
                path,
                f'declared kind "replay" and the recording {self._recording_path!r} '
                f"has no entry for {path!r}. Known: "
                f"{', '.join(sorted(self._replay)) or '(none)'}",
            )
        if index >= len(values):
            self._append(path, index, args, kwargs, "recording exhausted")
            _stop(
                self.module,
                path,
                f'declared kind "replay" and the recording holds {len(values)} value(s) '
                f"for {path!r}; this is call number {index + 1}. A replay never loops "
                f"and never invents a value",
            )
        self._append(path, index, args, kwargs, "replayed")
        return values[index]

    # -- bookkeeping -------------------------------------------------------

    def _append(
        self,
        path: str,
        index: int,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        outcome: str,
    ) -> None:
        self.calls.append(
            {
                "module": self.module,
                "path": path,
                "call_index": index,
                "args": [_stable_repr(value) for value in args],
                "kwargs": {key: _stable_repr(kwargs[key]) for key in sorted(kwargs)},
                "outcome": outcome,
            }
        )

    def _flush(self) -> None:
        """Write the call log inside the sandbox, which the harness owns.

        Rewritten whole each time rather than appended to, so a scenario that
        is aborted mid-call still leaves a complete, parseable file.
        """
        directory = self._sandbox_root / "client_calls"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"{self.module}.jsonl").write_text(
                "".join(canonical_dumps(row) + "\n" for row in self.calls),
                encoding="utf-8",
                newline="\n",
            )
        except OSError:
            # The in-memory log is what the run record is built from, so a
            # failed write costs the convenience file and nothing else. It is
            # not silent: the run summary prints the call count either way.
            pass


def _load_recording(module: str, recording: str, target_root: Path) -> dict[str, list[Any]]:
    path = Path(recording)
    if not path.is_absolute():
        path = target_root / path
    if not path.is_file():
        raise StubDeclarationError(
            f"client stub {module!r} is kind \"replay\" and its recording "
            f"{recording!r} does not exist. Tried: {path}. Refusing rather than "
            f"running the scenario against a client with nothing to say."
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StubDeclarationError(
            f"client stub {module!r}: cannot read the replay recording {path}: {error}"
        ) from error
    calls = document.get("calls") if isinstance(document, Mapping) else None
    if not isinstance(calls, Mapping) or not calls:
        raise StubDeclarationError(
            f"client stub {module!r}: the replay recording {path} must be "
            f'{{"calls": {{"<attribute path>": [value, ...]}}}} with at least one entry.'
        )
    loaded: dict[str, list[Any]] = {}
    for key, values in calls.items():
        if not isinstance(key, str) or not isinstance(values, list):
            raise StubDeclarationError(
                f"client stub {module!r}: every entry under \"calls\" in {path} maps an "
                f"attribute path to a LIST of return values, one per call in order; "
                f"got {key!r} -> {type(values).__name__}."
            )
        loaded[key] = list(values)
    return loaded


class _StubModule(ModuleType):
    """A module whose every attribute is a stubbed call path."""

    def __init__(self, state: _State) -> None:
        super().__init__(state.module, _module_doc(state))
        object.__setattr__(self, "_cascade_map_state", state)
        self.__cascade_map_stub_kind__ = state.kind
        self.__cascade_map_stub_module__ = state.module

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        state: _State = object.__getattribute__(self, "_cascade_map_state")
        return _Call(state, name)


def _module_doc(state: _State) -> str:
    return (
        f"CASCADE-MAP client stub for {state.module!r}, declared kind "
        f"{state.kind!r}. Not the real module: every call is answered, recorded "
        f"or stopped by the harness, and none of them leaves this process."
    )


def stub_module(
    declaration: ClientStubDeclaration,
    *,
    sandbox_root: Path,
    target_root: Path,
) -> ModuleType:
    """One stub module, ready to be installed into ``sys.modules``."""
    return _StubModule(
        _State(declaration, sandbox_root=sandbox_root, target_root=target_root)
    )


def build_factories(
    declarations: Sequence[ClientStubDeclaration],
    *,
    sandbox_root: Path,
    target_root: Path,
) -> dict[str, Callable[[], ModuleType]]:
    """``RunConfig.client_stubs``, built from validated declarations.

    A ``replay`` recording is read HERE, while the declarations are being
    turned into factories and before the harness has taken the process, so a
    missing or malformed recording is a refusal rather than a stop halfway
    through a run.
    """
    modules = {
        item.module: stub_module(
            item, sandbox_root=sandbox_root, target_root=target_root
        )
        for item in declarations
    }
    return {name: (lambda module=module: module) for name, module in sorted(modules.items())}

