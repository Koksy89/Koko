"""Card 2 -- resolution and the call graph.

Turns card 1's inventory into a resolved name environment and a graph of
``Edge`` records over the same IDs, plus an ``Unresolved`` record for every
call site the analysis could not pin down.

The design rule here is precision over recall. An edge is emitted only when a
target can be named; everything else becomes an ``Unresolved`` record carrying
the methods that were attempted and a candidate set with one shared
confidence. A confident wrong edge costs the owner more than an honest
``UNKNOWN``, so the resolver never promotes a guess to a fact.

**Nothing in this module executes, imports, ``exec``s, ``eval``s or unpickles
target code.** Sources are read as text and parsed with :mod:`ast`. Config
files are parsed with :mod:`json`, :mod:`tomllib` and :mod:`configparser`,
none of which execute their input. YAML is an optional adapter: without
``PyYAML`` a ``.yaml`` file becomes an ``Unresolved`` record rather than a
crash or a silent skip.

Approximations, stated once, here:

* ``Element`` carries no body, so this card re-parses each module twice --
  once to summarise definitions, imports and registries, once to resolve call
  sites. Summaries hold unparsed *source text* for base classes, annotations
  and registry members rather than AST nodes, so only one tree is live at a
  time.
* Instance dispatch is resolved through the statically declared MRO and
  reported ``PROBABLE``: a subclass may override. When an override exists in
  the inventory the edge is still emitted *and* an ``AMBIGUOUS`` record lists
  every candidate, so the ambiguity is visible rather than hidden.
* Calls to builtins are resolved but not emitted by default (they are noise at
  116k lines, and they are resolved, not unresolved). The count is reported by
  :meth:`Resolver.statistics`, so the omission is never silent. Pass
  ``include_builtin_calls=True`` to emit them.
"""

from __future__ import annotations

import ast
import builtins
import configparser
import json
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Sequence

from .contracts.interfaces import (
    Confidence,
    Edge,
    EdgeKind,
    Element,
    ElementKind,
    Method,
    Provenance,
    SourceSpan,
    Unresolved,
    UnresolvedReason,
    combine,
    config_key_id,
    make_id,
)
from .parsecache import ParseCache

__all__ = [
    "Resolver",
    "resolve",
    "build_graph",
    "import_cycles",
    "CONFIG_SUFFIXES",
    "WIRING_KEY_WORDS",
]

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

BUILTIN_NAMES: frozenset[str] = frozenset(dir(builtins))

#: Builtins whose *arguments* are the wiring. Handled specially; never emitted
#: as a plain call edge, because the interesting edge is the one they compute.
_WIRING_BUILTINS: frozenset[str] = frozenset(
    {"getattr", "setattr", "hasattr", "delattr", "__import__"}
)

#: Builtins that build code at runtime. Always an unresolved record: this is
#: where card 4 puts a barrier.
_CODE_BUILTINS: frozenset[str] = frozenset({"eval", "exec", "compile"})

CONFIG_SUFFIXES: tuple[str, ...] = (".json", ".yaml", ".yml", ".ini", ".cfg", ".toml")

#: Keys whose *name* suggests the value wires a component. Used only to decide
#: whether a string that matches nothing is worth reporting as dangling; never
#: to raise the confidence of a match.
WIRING_KEY_WORDS: frozenset[str] = frozenset(
    {
        "callable",
        "class",
        "class_name",
        "cls",
        "component",
        "components",
        "entry_point",
        "entrypoint",
        "factory",
        "func",
        "function",
        "handler",
        "handlers",
        "impl",
        "implementation",
        "loader",
        "module",
        "pipeline",
        "plugin",
        "plugins",
        "processor",
        "rule",
        "rules",
        "stage",
        "stages",
        "step",
        "steps",
        "strategy",
        "target",
        "transform",
        "type",
    }
)

_PROPERTY_DECORATORS: frozenset[str] = frozenset({"property", "cached_property", "functools.cached_property"})
_STATIC_DECORATORS: frozenset[str] = frozenset({"staticmethod", "classmethod"})

_CONTAINER_WRITE_METHODS: frozenset[str] = frozenset(
    {"append", "add", "extend", "update", "setdefault", "insert", "register"}
)


# ---------------------------------------------------------------------------
# internal value model
# ---------------------------------------------------------------------------


class _BKind(StrEnum):
    """What a name is bound to. Internal; never emitted."""

    MODULE = "MODULE"
    CLASS = "CLASS"
    CALLABLE = "CALLABLE"
    INSTANCE = "INSTANCE"
    CONST = "CONST"
    REGISTRY = "REGISTRY"
    CANDIDATES = "CANDIDATES"
    BUILTIN = "BUILTIN"
    UNKNOWN = "UNKNOWN"


#: Builtin types a literal can be recognised as. ``dir()`` here inspects the
#: *tool's* interpreter, never the target: no target code is imported.
_BUILTIN_TYPES: dict[str, type] = {
    "bytes": bytes,
    "dict": dict,
    "frozenset": frozenset,
    "int": int,
    "list": list,
    "set": set,
    "str": str,
    "tuple": tuple,
}
_BUILTIN_METHODS: dict[str, frozenset[str]] = {
    name: frozenset(m for m in dir(kind) if not m.startswith("_"))
    for name, kind in _BUILTIN_TYPES.items()
}


@dataclass(frozen=True, slots=True)
class _Binding:
    kind: _BKind = _BKind.UNKNOWN
    target_id: str = ""
    module_name: str = ""
    class_key: tuple[str, str] | None = None
    registry_key: tuple[str, str] | None = None
    const: "_StrVal | None" = None
    candidates: tuple[str, ...] = ()
    method: Method = Method.SCOPE_LOOKUP
    confidence: Confidence = Confidence.UNKNOWN
    note: str = ""
    external: bool = False
    is_property: bool = False
    builtin_type: str = ""
    """Set when the value is plainly a builtin container or scalar, so that a
    method call on it resolves to a builtin rather than joining the unresolved
    stream as a name the analysis failed on."""


_UNKNOWN_BINDING = _Binding()


@dataclass(frozen=True, slots=True)
class _StrVal:
    """Abstract value of a string expression.

    ``literals`` are exact possible values. ``prefixes``/``suffixes`` are the
    known fixed ends of a value whose middle is not traceable. ``open`` means
    at least one possible value is not pinned down, which is what separates
    "resolve it" from "emit a candidate set".
    """

    literals: tuple[str, ...] = ()
    prefixes: tuple[str, ...] = ()
    suffixes: tuple[str, ...] = ()
    open: bool = True

    @property
    def closed(self) -> bool:
        return not self.open and bool(self.literals)

    def matches(self, name: str) -> bool:
        if name in self.literals:
            return True
        if any(name.startswith(p) for p in self.prefixes if p):
            return True
        if any(name.endswith(s) for s in self.suffixes if s):
            return True
        return False

    @property
    def constrained(self) -> bool:
        return bool(self.literals or any(self.prefixes) or any(self.suffixes))


_OPEN_STR = _StrVal()


def _merge_str(left: _StrVal, right: _StrVal) -> _StrVal:
    return _StrVal(
        literals=tuple(sorted(set(left.literals) | set(right.literals))),
        prefixes=tuple(sorted(set(left.prefixes) | set(right.prefixes))),
        suffixes=tuple(sorted(set(left.suffixes) | set(right.suffixes))),
        open=left.open or right.open,
    )


# ---------------------------------------------------------------------------
# pass-A summaries
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ImportSpec:
    """One syntactic import binding, resolved later and lazily."""

    bound_name: str
    module: str
    level: int
    orig_name: str
    is_module_alias: bool
    line: int
    col: int
    conditional: bool
    type_checking: bool
    scope_qualname: str


@dataclass
class _ClassSum:
    module: str
    qualname: str
    element_id: str
    bases: tuple[str, ...] = ()
    metaclass: str = ""
    decorators: tuple[str, ...] = ()
    methods: dict[str, str] = field(default_factory=dict)
    method_ordinal: dict[str, int] = field(default_factory=dict)
    properties: set[str] = field(default_factory=set)
    nested_classes: dict[str, str] = field(default_factory=dict)
    class_attrs: dict[str, str] = field(default_factory=dict)
    self_attrs: dict[str, list[str]] = field(default_factory=dict)
    defines_init_subclass: bool = False
    line: int = 1


@dataclass
class _FuncSum:
    module: str
    qualname: str
    element_id: str
    decorators: tuple[str, ...] = ()
    returns: str = ""
    return_exprs: tuple[str, ...] = ()
    params: tuple[str, ...] = ()
    line: int = 1
    #: set when the body writes one of its (or a nested function's) parameters
    #: into a container -- i.e. this function is a registration decorator.
    registrar_container: str = ""
    registrar_container_is_self: bool = False
    registrar_key: str = ""


@dataclass
class _RegistrySum:
    name: str
    module: str
    element_id: str
    container: str = "dict"
    entries: dict[str, str] = field(default_factory=dict)
    members: list[str] = field(default_factory=list)
    dynamic_keys: bool = False


@dataclass
class _ModuleSum:
    name: str
    path: str
    is_package: bool
    element_id: str
    dunder_all: tuple[str, ...] | None = None
    imports: dict[str, _ImportSpec] = field(default_factory=dict)
    local_imports: list[_ImportSpec] = field(default_factory=list)
    stars: list[_ImportSpec] = field(default_factory=list)
    dotted_imports: set[str] = field(default_factory=set)
    classes: dict[str, _ClassSum] = field(default_factory=dict)
    funcs: dict[str, _FuncSum] = field(default_factory=dict)
    top_defs: dict[str, str] = field(default_factory=dict)
    assigns: dict[str, str] = field(default_factory=dict)
    registries: dict[str, _RegistrySum] = field(default_factory=dict)
    ordinals: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _text(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - unparse is total on valid trees
        return ""


def _dotted(node: ast.expr) -> str:
    """The dotted name of a Name/Attribute chain, or ``""``."""
    parts: list[str] = []
    cur: ast.expr = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return ""
    parts.append(cur.id)
    return ".".join(reversed(parts))


def _json_pointer(parts: Sequence[str]) -> str:
    return "".join("/" + p.replace("~", "~0").replace("/", "~1") for p in parts)


def _span(path: str, node: ast.AST) -> SourceSpan:
    line = int(getattr(node, "lineno", 1) or 1)
    end = getattr(node, "end_lineno", None)
    col = getattr(node, "col_offset", None)
    return SourceSpan(
        path=path,
        line=line,
        end_line=int(end) if end is not None else None,
        col=int(col) if col is not None else None,
    )


def _is_type_checking(test: ast.expr) -> bool:
    name = _dotted(test)
    return name in {"TYPE_CHECKING", "typing.TYPE_CHECKING", "t.TYPE_CHECKING"}


def _strip_subscript(text: str) -> str:
    """``Optional[Foo]`` / ``list[Foo]`` -> the interesting inner name."""
    if "[" not in text:
        return text.strip()
    head, _, rest = text.partition("[")
    head = head.strip()
    inner = rest.rsplit("]", 1)[0]
    if head.split(".")[-1] in {"Optional", "List", "list", "Sequence", "Iterable", "Set", "set", "FrozenSet", "Type", "type"}:
        first = inner.split(",")[0].strip()
        return _strip_subscript(first)
    return head


def _relative_module(current: str, is_package: bool, level: int, mod: str) -> str:
    parts = current.split(".") if current else []
    base = list(parts) if is_package else list(parts[:-1])
    up = max(level - 1, 0)
    if up:
        base = base[:-up] if up <= len(base) else []
    pkg = ".".join(base)
    if mod:
        return f"{pkg}.{mod}" if pkg else mod
    return pkg


# ---------------------------------------------------------------------------
# pass A -- summarise one module
# ---------------------------------------------------------------------------


class _Summariser(ast.NodeVisitor):
    """Collects definitions, imports and registries from a single module.

    Holds no AST nodes past the walk: everything crossing the pass boundary is
    either a string, an int or a source-text fragment.
    """

    def __init__(self, summary: _ModuleSum, id_for: Any) -> None:
        self.s = summary
        self._id_for = id_for
        self._prefix = ""
        self._class_stack: list[_ClassSum] = []
        self._func_stack: list[_FuncSum] = []
        self._cond_depth = 0
        self._tc_depth = 0

    # -- naming ---------------------------------------------------------
    def _qualname(self, name: str) -> str:
        return f"{self._prefix}{name}"


    def _ordinal(self, qualname: str) -> int:
        n = self.s.ordinals.get(qualname, 0) + 1
        self.s.ordinals[qualname] = n
        return n

    # -- imports --------------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.s.dotted_imports.add(alias.name)
            bound = alias.asname or alias.name.split(".")[0]
            target = alias.name if alias.asname else alias.name.split(".")[0]
            spec = _ImportSpec(
                bound_name=bound,
                module=target,
                level=0,
                orig_name="",
                is_module_alias=True,
                line=node.lineno,
                col=node.col_offset,
                conditional=self._cond_depth > 0,
                type_checking=self._tc_depth > 0,
                scope_qualname=self._prefix,
            )
            self._record_import(spec, full_module=alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                spec = _ImportSpec(
                    bound_name="*",
                    module=mod,
                    level=node.level,
                    orig_name="*",
                    is_module_alias=False,
                    line=node.lineno,
                    col=node.col_offset,
                    conditional=self._cond_depth > 0,
                    type_checking=self._tc_depth > 0,
                    scope_qualname=self._prefix,
                )
                self.s.stars.append(spec)
                continue
            spec = _ImportSpec(
                bound_name=alias.asname or alias.name,
                module=mod,
                level=node.level,
                orig_name=alias.name,
                is_module_alias=False,
                line=node.lineno,
                col=node.col_offset,
                conditional=self._cond_depth > 0,
                type_checking=self._tc_depth > 0,
                scope_qualname=self._prefix,
            )
            self._record_import(spec)

    def _record_import(self, spec: _ImportSpec, full_module: str = "") -> None:
        if full_module:
            self.s.dotted_imports.add(full_module)
        if self._func_stack:
            self.s.local_imports.append(spec)
        else:
            self.s.imports.setdefault(spec.bound_name, spec)
            self.s.local_imports.append(spec)

    # -- conditionals ---------------------------------------------------
    def visit_If(self, node: ast.If) -> None:
        tc = _is_type_checking(node.test)
        self._cond_depth += 1
        if tc:
            self._tc_depth += 1
        for child in node.body:
            self.visit(child)
        if tc:
            self._tc_depth -= 1
        for child in node.orelse:
            self.visit(child)
        self._cond_depth -= 1

    def visit_Try(self, node: ast.Try) -> None:
        self._cond_depth += 1
        self.generic_visit(node)
        self._cond_depth -= 1

    # -- definitions ----------------------------------------------------
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualname = self._qualname(node.name)
        ordinal = self._ordinal(qualname)
        metaclass = ""
        for kw in node.keywords:
            if kw.arg == "metaclass":
                metaclass = _text(kw.value)
        info = _ClassSum(
            module=self.s.name,
            qualname=qualname,
            element_id=self._id_for(self.s.name, qualname, ordinal),
            bases=tuple(_text(b) for b in node.bases),
            metaclass=metaclass,
            decorators=tuple(_text(d) for d in node.decorator_list),
            line=node.lineno,
        )
        self.s.classes[qualname] = info
        if not self._class_stack and not self._func_stack:
            self.s.top_defs[node.name] = qualname
        elif self._class_stack:
            self._class_stack[-1].nested_classes[node.name] = qualname
        self._class_stack.append(info)
        outer = self._prefix
        self._prefix = f"{qualname}."
        for child in node.body:
            self.visit(child)
        self._prefix = outer
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node)

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualname = self._qualname(node.name)
        ordinal = self._ordinal(qualname)
        decorators = tuple(_text(d) for d in node.decorator_list)
        args = node.args
        params = tuple(
            a.arg
            for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]
        )
        info = _FuncSum(
            module=self.s.name,
            qualname=qualname,
            element_id=self._id_for(self.s.name, qualname, ordinal),
            decorators=decorators,
            returns=_text(node.returns),
            params=params,
            line=node.lineno,
        )
        self.s.funcs[qualname] = info
        if self._class_stack:
            cls = self._class_stack[-1]
            cls.methods.setdefault(node.name, qualname)
            cls.method_ordinal.setdefault(node.name, ordinal)
            if any(d.split("(")[0] in _PROPERTY_DECORATORS for d in decorators):
                cls.properties.add(node.name)
            if node.name == "__init_subclass__":
                cls.defines_init_subclass = True
        elif not self._func_stack:
            self.s.top_defs[node.name] = qualname

        self._func_stack.append(info)
        outer = self._prefix
        self._prefix = f"{qualname}.<locals>."
        info.return_exprs = tuple(_own_returns(node))
        for child in node.body:
            self.visit(child)
        self._prefix = outer
        self._func_stack.pop()

    # -- assignments ----------------------------------------------------
    def visit_Assign(self, node: ast.Assign) -> None:
        self._assign(node.targets, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._assign([node.target], node.value)
        elif isinstance(node.target, ast.Name) and not self._func_stack and not self._class_stack:
            ann = _text(node.annotation)
            if ann.split("[")[0].split(".")[-1] in {"dict", "Dict", "list", "List", "Mapping", "MutableMapping"}:
                self._new_registry(node.target.id, "dict" if "ict" in ann or "apping" in ann else "list")
        self.generic_visit(node)

    def _new_registry(self, name: str, container: str) -> _RegistrySum:
        reg = self.s.registries.get(name)
        if reg is None:
            reg = _RegistrySum(
                name=name,
                module=self.s.name,
                element_id=self._id_for(self.s.name, name, 1),
                container=container,
            )
            self.s.registries[name] = reg
        return reg

    def _assign(self, targets: Sequence[ast.expr], value: ast.expr) -> None:
        at_module = not self._func_stack and not self._class_stack
        for target in targets:
            if isinstance(target, ast.Name):
                if at_module:
                    self.s.assigns.setdefault(target.id, _text(value))
                    if target.id == "__all__":
                        self.s.dunder_all = _literal_str_seq(value)
                    self._maybe_registry_literal(target.id, value)
            elif isinstance(target, ast.Attribute):
                if self._class_stack and isinstance(target.value, ast.Name) and target.value.id in {"self", "cls"}:
                    cls = self._class_stack[-1]
                    cls.self_attrs.setdefault(target.attr, []).append(_text(value))
            elif isinstance(target, ast.Subscript):
                self._container_write(target.value, _text(value), key_node=target.slice)
        if self._class_stack and not self._func_stack:
            for target in targets:
                if isinstance(target, ast.Name):
                    self._class_stack[-1].class_attrs.setdefault(target.id, _text(value))

    def _maybe_registry_literal(self, name: str, value: ast.expr) -> None:
        if isinstance(value, ast.Dict):
            reg = self._new_registry(name, "dict")
            for key, val in zip(value.keys, value.values):
                text = _text(val)
                if key is not None and isinstance(key, ast.Constant) and isinstance(key.value, str):
                    reg.entries[key.value] = text
                else:
                    reg.dynamic_keys = True
                reg.members.append(text)
        elif isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            container = "list" if isinstance(value, (ast.List, ast.Tuple)) else "set"
            reg = self._new_registry(name, container)
            for item in value.elts:
                reg.members.append(_text(item))

    def _container_write(self, container: ast.expr, value_text: str, key_node: ast.AST | None) -> None:
        """Record ``NAME[key] = value`` / ``self.attr[key] = value``."""
        key = ""
        if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
            key = key_node.value
        if isinstance(container, ast.Name):
            reg = self.s.registries.get(container.id)
            if reg is None and container.id in self.s.assigns:
                reg = self._new_registry(container.id, "dict")
            if reg is None and not self._func_stack and not self._class_stack:
                reg = self._new_registry(container.id, "dict")
            if reg is not None:
                if key:
                    reg.entries.setdefault(key, value_text)
                else:
                    reg.dynamic_keys = True
                if value_text not in reg.members:
                    reg.members.append(value_text)
                self._maybe_registrar(container.id, value_text, key_node, is_self=False)
        elif isinstance(container, ast.Attribute) and isinstance(container.value, ast.Name):
            if container.value.id in {"self", "cls"}:
                self._maybe_registrar(container.attr, value_text, key_node, is_self=True)

    def _maybe_registrar(
        self, container_name: str, value_text: str, key_node: ast.AST | None, *, is_self: bool
    ) -> None:
        """Mark the enclosing function as a registration decorator.

        A function that writes one of its own (or its closure's) parameters
        into a module-level container *is* a registrar; that is what
        ``@register`` and ``@register("name")`` look like from the outside.
        """
        if not self._func_stack:
            return
        params: set[str] = set()
        for fn in self._func_stack:
            params.update(fn.params)
        if value_text not in params:
            return
        key_text = ""
        if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
            key_text = key_node.value
        elif key_node is not None:
            key_text = _text(key_node)
        outer = self._func_stack[0]
        outer.registrar_container = container_name
        outer.registrar_container_is_self = is_self
        outer.registrar_key = key_text

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _CONTAINER_WRITE_METHODS:
            if node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Dict):
                    for key, val in zip(arg.keys, arg.values):
                        self._container_write(func.value, _text(val), key)
                elif isinstance(arg, (ast.List, ast.Tuple, ast.Set)):
                    for item in arg.elts:
                        self._container_write(func.value, _text(item), None)
                elif func.attr == "setdefault" and len(node.args) > 1:
                    self._container_write(func.value, _text(node.args[1]), arg)
                else:
                    self._container_write(func.value, _text(arg), None)
        self.generic_visit(node)


def _own_returns(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Return expressions of *this* function, not of the ones nested in it.

    ``ast.walk`` would fold a wrapper's own `return` into its decorator's, and
    the decorator would then look as if it returned two different things.
    """
    out: list[str] = []

    def walk(body: Sequence[ast.stmt]) -> None:
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(stmt, ast.Return):
                if stmt.value is not None:
                    out.append(_text(stmt.value))
                continue
            for field in ("body", "orelse", "finalbody"):
                inner = getattr(stmt, field, None)
                if isinstance(inner, list):
                    walk([n for n in inner if isinstance(n, ast.stmt)])
            for handler in getattr(stmt, "handlers", []) or []:
                walk(handler.body)

    walk(node.body)
    return out


def _string_constants(node: ast.AST) -> list[str]:
    """Every string literal in a subtree, in source order."""
    out: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            out.append(child.value)
    return out


def _decorator_key_literal(text: str) -> str:
    """The literal string argument of ``@register("scale")``, if there is one."""
    try:
        expr = ast.parse(text, mode="eval").body
    except SyntaxError:
        return ""
    if not isinstance(expr, ast.Call):
        return ""
    for arg in [*expr.args, *[kw.value for kw in expr.keywords]]:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return ""


def _element_name(resolver: "Resolver", element_id: str) -> str:
    element = resolver._elements.get(element_id)
    if element is not None:
        return element.name
    return element_id.rsplit("::", 1)[-1].rsplit(".", 1)[-1]


def _literal_builtin_type(node: ast.expr) -> str:
    """The builtin type a literal expression plainly has, or ``""``."""
    if isinstance(node, (ast.List, ast.ListComp)):
        return "list"
    if isinstance(node, (ast.Dict, ast.DictComp)):
        return "dict"
    if isinstance(node, (ast.Set, ast.SetComp)):
        return "set"
    if isinstance(node, ast.Tuple):
        return "tuple"
    if isinstance(node, ast.JoinedStr):
        return "str"
    if isinstance(node, ast.Constant):
        for name, kind in _BUILTIN_TYPES.items():
            if type(node.value) is kind:
                return name
    return ""


def _literal_str_seq(value: ast.expr) -> tuple[str, ...] | None:
    if not isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        return None
    out: list[str] = []
    for item in value.elts:
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            out.append(item.value)
        else:
            return None
    return tuple(out)


# ---------------------------------------------------------------------------
# the resolver
# ---------------------------------------------------------------------------


class Resolver:
    """Card 2's entry point. Implements ``ResolutionCard``.

    ``root`` is the directory ``Element.span.path`` values are relative to.
    ``config_paths`` are the owner-declared wiring files (``TARGET_PROFILE``
    Q3); a declared file's string matches are ``PROBABLE``, an auto-detected
    one's are ``HEURISTIC``.
    """

    def __init__(
        self,
        root: str | Path = ".",
        *,
        config_paths: Sequence[str] = (),
        include_builtin_calls: bool = False,
        max_traced_targets: int = 4,
        max_candidates: int = 32,
        parse_cache: ParseCache | None = None,
    ) -> None:
        self.root = Path(root)
        #: The run's shared trees. Resolution alone asks for every module
        #: TWICE -- `_summarise` walks it for declarations, `_resolve_module`
        #: walks it again for call sites -- so even a Resolver built on its
        #: own, as every test builds one, halves its own parsing. Passed in by
        #: `cli.analyze` so the CFG and lineage stages get the same trees.
        self._parse_cache = ParseCache() if parse_cache is None else parse_cache
        self.declared_configs = frozenset(str(p) for p in config_paths)
        self.include_builtin_calls = include_builtin_calls
        self.max_traced_targets = max_traced_targets
        self.max_candidates = max_candidates

        self._elements: dict[str, Element] = {}
        self._by_qual: dict[tuple[str, str, int], Element] = {}
        self._by_name: dict[str, list[str]] = defaultdict(list)
        self._modules: dict[str, _ModuleSum] = {}
        self._module_paths: dict[str, str] = {}
        self._edges: list[Edge] = []
        self._unresolved: list[Unresolved] = []
        self._edge_ordinals: dict[str, int] = {}
        self._unres_ordinals: dict[str, int] = {}
        self._export_cache: dict[tuple[str, str], _Binding] = {}
        self._mro_cache: dict[tuple[str, str], tuple[tuple[tuple[str, str], ...], bool]] = {}
        self._scope_cache: dict[str, dict[str, _Binding]] = {}
        self._star_cache: dict[tuple[str, _ImportSpec], dict[str, _Binding]] = {}
        self._subclasses: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
        self._registry_keys: dict[str, list[str]] = defaultdict(list)
        self._decorated_by: dict[str, list[str]] = defaultdict(list)
        self._registry_members: dict[str, list[tuple[str, Method]]] = defaultdict(list)
        self._wrapper_cache: dict[str, str] = {}
        self._stats: dict[str, int] = defaultdict(int)
        self._config_files: list[str] = []

    # -- public ---------------------------------------------------------

    def resolve(
        self, elements: Sequence[Element]
    ) -> tuple[Sequence[Edge], Sequence[Unresolved]]:
        """Resolve *elements* into edges and unresolved records."""
        self._reset()
        self._index(elements)
        self._summarise()
        self._link_subclasses()
        for module in sorted(self._modules):
            self._resolve_module(self._modules[module])
        self._resolve_configs()
        edges = sorted(self._edges, key=lambda e: e.id)
        unresolved = sorted(self._unresolved, key=lambda u: u.id)
        self._stats["edges"] = len(edges)
        self._stats["unresolved"] = len(unresolved)
        return edges, unresolved

    def module_count(self) -> int:
        """How many modules the last :meth:`resolve` walked.

        The unit of work this stage would be split on, if its units were
        independent. Reported so a target that is one module says so with a
        number instead of looking like a stage that had nothing to gain.
        """
        return len(self._modules)

    def statistics(self) -> dict[str, int]:
        """Counts, all integers. Includes the edges deliberately not emitted,
        so the omission of builtin calls is never silent."""
        return {key: int(self._stats[key]) for key in sorted(self._stats)}

    # -- setup ----------------------------------------------------------

    def _reset(self) -> None:
        self._elements.clear()
        self._by_qual.clear()
        self._by_name.clear()
        self._modules.clear()
        self._module_paths.clear()
        self._edges.clear()
        self._unresolved.clear()
        self._edge_ordinals.clear()
        self._unres_ordinals.clear()
        self._export_cache.clear()
        self._mro_cache.clear()
        self._scope_cache.clear()
        self._star_cache.clear()
        self._subclasses.clear()
        self._registry_keys.clear()
        self._decorated_by.clear()
        self._registry_members.clear()
        self._wrapper_cache.clear()
        self._stats.clear()
        self._config_files.clear()

    def _index(self, elements: Sequence[Element]) -> None:
        counts: dict[tuple[str, str], int] = defaultdict(int)
        module_elements: dict[str, Element] = {}
        for element in elements:
            self._elements[element.id] = element
            key = (element.module, element.qualname)
            counts[key] += 1
            self._by_qual[(element.module, element.qualname, counts[key])] = element
            if element.qualname:
                self._by_name[element.name].append(element.id)
            if element.kind in (ElementKind.MODULE, ElementKind.PACKAGE):
                module_elements.setdefault(element.module, element)
            if element.kind in (ElementKind.DATA_FILE, ElementKind.CONFIG_KEY):
                path = element.span.path
                if path.endswith(CONFIG_SUFFIXES) and path not in self._config_files:
                    self._config_files.append(path)
        for path in sorted(self.declared_configs):
            if path not in self._config_files:
                self._config_files.append(path)
        self._config_files.sort()

        for name, element in module_elements.items():
            path = element.span.path
            self._module_paths[name] = path
            self._modules[name] = _ModuleSum(
                name=name,
                path=path,
                is_package=element.kind is ElementKind.PACKAGE
                or Path(path).name == "__init__.py",
                element_id=element.id,
            )
        # Robustness: an element whose module card 1 did not emit as a MODULE
        # element still needs a module entry, or its call sites vanish.
        for element in elements:
            if element.kind in (ElementKind.DATA_FILE, ElementKind.CONFIG_KEY, ElementKind.FEATURE):
                continue
            if element.module and element.module not in self._modules:
                path = element.span.path
                if not path.endswith(".py"):
                    continue
                self._module_paths[element.module] = path
                self._modules[element.module] = _ModuleSum(
                    name=element.module,
                    path=path,
                    is_package=Path(path).name == "__init__.py",
                    element_id=make_id(element.module),
                )

    def _id_for(self, module: str, qualname: str, ordinal: int = 1) -> str:
        element = self._by_qual.get((module, qualname, ordinal))
        if element is not None:
            return element.id
        return make_id(module, qualname, ordinal)

    # -- source access --------------------------------------------------

    def _read(self, path: str) -> str | None:
        full = Path(path) if Path(path).is_absolute() else self.root / path
        try:
            return full.read_text(encoding="utf-8")
        except FileNotFoundError:
            self._record_unresolved(
                owner=make_id(path),
                reason=UnresolvedReason.MISSING_TARGET,
                span=SourceSpan(path=path, line=1),
                description=f"source file not found under root {self.root}",
                attempted=(Method.AST_DIRECT,),
            )
            return None
        except UnicodeDecodeError:
            self._record_unresolved(
                owner=make_id(path),
                reason=UnresolvedReason.DECODE_ERROR,
                span=SourceSpan(path=path, line=1),
                description="file is not valid UTF-8; cannot resolve names in it",
                attempted=(Method.AST_DIRECT,),
            )
            return None
        except OSError as exc:
            self._record_unresolved(
                owner=make_id(path),
                reason=UnresolvedReason.MISSING_TARGET,
                span=SourceSpan(path=path, line=1),
                description=f"could not read source: {type(exc).__name__}",
                attempted=(Method.AST_DIRECT,),
            )
            return None

    def _parse(self, summary: _ModuleSum) -> ast.Module | None:
        """Parse a module to a tree. Never executes it -- ``ast.parse`` only."""
        source = self._read(summary.path)
        if source is None:
            return None
        try:
            return self._parse_cache.parse(summary.path, source)
        except SyntaxError as exc:
            self._record_unresolved(
                owner=summary.element_id,
                reason=UnresolvedReason.SYNTAX_ERROR,
                span=SourceSpan(path=summary.path, line=int(exc.lineno or 1)),
                description=f"cannot parse module: {exc.msg}",
                attempted=(Method.AST_DIRECT,),
            )
            return None
        except (ValueError, RecursionError) as exc:
            self._record_unresolved(
                owner=summary.element_id,
                reason=UnresolvedReason.TOO_LARGE,
                span=SourceSpan(path=summary.path, line=1),
                description=f"cannot parse module: {type(exc).__name__}",
                attempted=(Method.AST_DIRECT,),
            )
            return None

    def _summarise(self) -> None:
        for name in sorted(self._modules):
            summary = self._modules[name]
            tree = self._parse(summary)
            if tree is None:
                continue
            _Summariser(summary, self._id_for).visit(tree)
            self._stats["modules_parsed"] += 1
            del tree

    def _link_subclasses(self) -> None:
        """Cross-element structure both passes need: subclasses, what each
        decorator decorates, and what each registry holds.

        Computed before any body is resolved, so a dispatcher defined above
        the code that fills its registry still sees the whole registry."""
        for module in sorted(self._modules):
            summary = self._modules[module]
            for qualname in sorted(summary.classes):
                info = summary.classes[qualname]
                for base in info.bases:
                    key = self._class_key_from_text(base, module)
                    if key is not None:
                        self._subclasses[key].append((module, qualname))
        for key in self._subclasses:
            self._subclasses[key].sort()

        for module in sorted(self._modules):
            summary = self._modules[module]
            decorated: list[tuple[tuple[str, ...], str]] = [
                (info.decorators, info.element_id) for _, info in sorted(summary.classes.items())
            ]
            decorated += [
                (info.decorators, info.element_id) for _, info in sorted(summary.funcs.items())
            ]
            for decorators, element_id in decorated:
                for text in decorators:
                    head = text.split("(", 1)[0]
                    if head in _PROPERTY_DECORATORS or head in _STATIC_DECORATORS:
                        continue
                    binding = self._binding_for_dotted(head, module)
                    if not binding.target_id:
                        continue
                    # `@trace` applies `trace` itself; `@register("x")` applies
                    # whatever `register` returns. Binding the decorated
                    # element to the wrong one of those two would put the key
                    # argument where the decorated function belongs.
                    applied = binding.target_id
                    if text.strip() != head:
                        applied = self.wrapper_of(binding.target_id) or binding.target_id
                    self._decorated_by[applied].append(element_id)
                    key_literal = _decorator_key_literal(text)
                    registrar = self._registrar_for(binding.target_id)
                    if registrar is None:
                        continue
                    container = self._registrar_container(registrar, head, module)
                    if not container:
                        continue
                    self._registry_members[container].append(
                        (element_id, Method.DECORATOR_REGISTRATION)
                    )
                    self.register_key(key_literal or _element_name(self, element_id), element_id)

        for module in sorted(self._modules):
            summary = self._modules[module]
            for name in sorted(summary.registries):
                reg = summary.registries[name]
                for text in reg.members:
                    binding = self._binding_for_dotted(text, module) if text else _UNKNOWN_BINDING
                    if binding.target_id and binding.kind in (_BKind.CALLABLE, _BKind.CLASS):
                        self._registry_members[reg.element_id].append(
                            (binding.target_id, Method.REGISTRY_MEMBERSHIP)
                        )
                for key in sorted(reg.entries):
                    binding = self._binding_for_dotted(reg.entries[key], module)
                    if binding.target_id:
                        self.register_key(key, binding.target_id)
        for container in self._registry_members:
            self._registry_members[container] = sorted(
                dict.fromkeys(self._registry_members[container])
            )
        for key in self._decorated_by:
            self._decorated_by[key] = sorted(dict.fromkeys(self._decorated_by[key]))

    def _registrar_for(self, element_id: str) -> _FuncSum | None:
        for module in sorted(self._modules):
            for _, func in sorted(self._modules[module].funcs.items()):
                if func.element_id == element_id and func.registrar_container:
                    return func
        return None

    def _registrar_container(self, registrar: _FuncSum, dotted: str, module: str) -> str:
        if registrar.registrar_container_is_self:
            receiver = dotted.rsplit(".", 1)[0] if "." in dotted else ""
            return self._binding_for_dotted(receiver, module).target_id if receiver else ""
        owner = registrar.module
        reg = self._modules[owner].registries.get(registrar.registrar_container)
        return reg.element_id if reg else make_id(owner, registrar.registrar_container)

    def wrapper_of(self, decorator_id: str) -> str:
        """The function a decorator returns, when it plainly returns one.

        ``@trace`` rebinds the decorated name to ``trace``'s inner function, so
        a call to the decorated name reaches that wrapper. A decorator that
        returns its own argument rebinds nothing and yields ``""``.
        """
        cached = self._wrapper_cache.get(decorator_id)
        if cached is not None:
            return cached
        self._wrapper_cache[decorator_id] = ""
        result = ""
        for module in sorted(self._modules):
            summary = self._modules[module]
            for qualname in sorted(summary.funcs):
                func = summary.funcs[qualname]
                if func.element_id != decorator_id:
                    continue
                returns = [r for r in func.return_exprs if r]
                if len(set(returns)) != 1:
                    break
                returned = returns[0]
                if returned in func.params:
                    break  # identity decorator: the name keeps its own target
                nested = f"{qualname}.<locals>.{returned}"
                if nested in summary.funcs:
                    result = summary.funcs[nested].element_id
                break
            if result:
                break
        self._wrapper_cache[decorator_id] = result
        return result

    def decorated_by(self, decorator_id: str) -> tuple[str, ...]:
        return tuple(self._decorated_by.get(decorator_id, ()))

    def decorators_of(self, element_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                decorator
                for decorator, targets in self._decorated_by.items()
                if element_id in targets
            )
        )

    def registry_members(self, container_id: str) -> tuple[tuple[str, Method], ...]:
        return tuple(self._registry_members.get(container_id, ()))

    def registry_binding(self, module: str, name: str) -> _Binding | None:
        """A REGISTRY binding for *name*, or ``None`` if it holds no callables.

        A module-level dict that holds data is a dict, not a registry. Calling
        it one would turn every ``x.get(k)`` in the engine into a dispatch.
        """
        summary = self._modules.get(module)
        reg = summary.registries.get(name) if summary else None
        if reg is None or not self._registry_members.get(reg.element_id):
            return None
        return _Binding(
            kind=_BKind.REGISTRY,
            target_id=reg.element_id,
            registry_key=(module, name),
            method=Method.REGISTRY_MEMBERSHIP,
            confidence=Confidence.RESOLVED,
            builtin_type=reg.container if reg.container in _BUILTIN_TYPES else "",
        )

    # -- emission -------------------------------------------------------

    def _emit(
        self,
        kind: EdgeKind,
        source_id: str,
        target_id: str,
        method: Method,
        confidence: Confidence,
        *,
        call_site: SourceSpan | None = None,
        note: str = "",
    ) -> Edge:
        base = f"edge:{kind.value}:{source_id}=>{target_id}"
        ordinal = self._edge_ordinals.get(base, 0) + 1
        self._edge_ordinals[base] = ordinal
        edge_id = base if ordinal == 1 else f"{base}#{ordinal}"
        edge = Edge(
            id=edge_id,
            kind=kind,
            source_id=source_id,
            target_id=target_id,
            provenance=Provenance(
                method=method, confidence=confidence, span=call_site, note=note
            ),
            call_site=call_site,
        )
        self._edges.append(edge)
        self._stats[f"edge_{kind.value}"] += 1
        self._stats[f"conf_{confidence.value}"] += 1
        return edge

    def _record_unresolved(
        self,
        *,
        owner: str,
        reason: UnresolvedReason,
        span: SourceSpan,
        description: str,
        attempted: tuple[Method, ...] = (),
        candidates: Sequence[str] = (),
        candidate_confidence: Confidence = Confidence.UNKNOWN,
    ) -> Unresolved:
        """*owner* is the record's ID: the element or key the gap belongs to.

        A ``#n`` suffix appears only when the same site produces a second
        record, mirroring :func:`make_id`, so a record keeps its ID across
        reformatting.
        """
        ordinal = self._unres_ordinals.get(owner, 0) + 1
        self._unres_ordinals[owner] = ordinal
        ids = tuple(sorted(dict.fromkeys(candidates)))
        truncated = ""
        if len(ids) > self.max_candidates:
            truncated = f" (candidate set truncated from {len(ids)} to {self.max_candidates})"
            ids = ids[: self.max_candidates]
        record = Unresolved(
            id=owner if ordinal == 1 else f"{owner}#{ordinal}",
            reason=reason,
            span=span,
            description=description + truncated,
            attempted=attempted,
            candidate_ids=ids,
            candidate_confidence=candidate_confidence if ids else Confidence.UNKNOWN,
        )
        self._unresolved.append(record)
        self._stats[f"unresolved_{reason.value}"] += 1
        return record

    # -- module / export resolution -------------------------------------

    def _module_scope(self, module: str) -> dict[str, _Binding]:
        """Module-level bindings: definitions, imports, constants, registries."""
        cached = self._scope_cache.get(module)
        if cached is not None:
            return cached
        scope: dict[str, _Binding] = {}
        self._scope_cache[module] = scope  # placed early: import cycles terminate here
        summary = self._modules.get(module)
        if summary is None:
            return scope
        for name in sorted(summary.top_defs):
            qualname = summary.top_defs[name]
            scope[name] = self._binding_for_def(module, qualname)
        for name in sorted(summary.imports):
            scope[name] = self._binding_for_import(summary, summary.imports[name])
        for spec in summary.stars:
            for exported, binding in self._star_bindings(summary, spec).items():
                scope.setdefault(exported, binding)
        for name in sorted(summary.registries):
            if name in scope:
                continue
            binding = self.registry_binding(module, name)
            if binding is not None:
                scope[name] = binding
        for name in sorted(summary.assigns):
            if name in scope:
                continue
            binding = self._binding_from_assign_text(module, summary.assigns[name])
            if binding.kind is not _BKind.UNKNOWN:
                scope[name] = binding
        return scope

    def _binding_for_def(self, module: str, qualname: str) -> _Binding:
        summary = self._modules[module]
        if qualname in summary.classes:
            info = summary.classes[qualname]
            return _Binding(
                kind=_BKind.CLASS,
                target_id=info.element_id,
                class_key=(module, qualname),
                method=Method.AST_DIRECT,
                confidence=Confidence.CERTAIN,
            )
        if qualname in summary.funcs:
            info = summary.funcs[qualname]
            return _Binding(
                kind=_BKind.CALLABLE,
                target_id=info.element_id,
                method=Method.AST_DIRECT,
                confidence=Confidence.CERTAIN,
            )
        return _UNKNOWN_BINDING

    def _binding_from_assign_text(self, module: str, text: str) -> _Binding:
        if not text:
            return _UNKNOWN_BINDING
        try:
            expr = ast.parse(text, mode="eval").body
        except SyntaxError:
            return _UNKNOWN_BINDING
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            return _Binding(
                kind=_BKind.CONST,
                const=_StrVal(literals=(expr.value,), open=False),
                method=Method.DATAFLOW,
                confidence=Confidence.RESOLVED,
            )
        if isinstance(expr, ast.Call):
            key = self._class_key_from_text(_dotted(expr.func), module)
            if key is not None:
                return _Binding(
                    kind=_BKind.INSTANCE,
                    class_key=key,
                    target_id=self._class_element(key),
                    method=Method.DATAFLOW,
                    confidence=Confidence.PROBABLE,
                )
        if isinstance(expr, (ast.Name, ast.Attribute)):
            dotted = _dotted(expr)
            if dotted:
                key = self._class_key_from_text(dotted, module)
                if key is not None:
                    return _Binding(
                        kind=_BKind.CLASS,
                        class_key=key,
                        target_id=self._class_element(key),
                        method=Method.SCOPE_LOOKUP,
                        confidence=Confidence.RESOLVED,
                    )
        return _UNKNOWN_BINDING

    def _binding_for_import(self, summary: _ModuleSum, spec: _ImportSpec) -> _Binding:
        note_bits: list[str] = []
        if spec.type_checking:
            note_bits.append("under TYPE_CHECKING; not imported at runtime")
        elif spec.conditional:
            note_bits.append("conditional import")
        note = "; ".join(note_bits)
        # A guarded import still names exactly one module: the guard changes
        # *whether* it runs, not *what* it names. The note records the guard;
        # downgrading the confidence would understate a certain resolution.
        ceiling = Confidence.RESOLVED

        if spec.is_module_alias:
            target_module = spec.module
            known = target_module in self._modules
            return _Binding(
                kind=_BKind.MODULE,
                module_name=target_module,
                target_id=self._modules[target_module].element_id if known else "",
                method=Method.IMPORT_ABSOLUTE,
                confidence=ceiling if known else Confidence.UNKNOWN,
                note=note,
                external=not known,
            )

        target_module = (
            _relative_module(summary.name, summary.is_package, spec.level, spec.module)
            if spec.level
            else spec.module
        )
        method = Method.IMPORT_RELATIVE if spec.level else Method.IMPORT_ABSOLUTE
        submodule = f"{target_module}.{spec.orig_name}" if target_module else spec.orig_name
        if submodule in self._modules:
            return _Binding(
                kind=_BKind.MODULE,
                module_name=submodule,
                target_id=self._modules[submodule].element_id,
                method=method,
                confidence=ceiling,
                note=note,
            )
        if target_module in self._modules:
            exported = self._lookup_export(target_module, spec.orig_name, frozenset())
            if exported.kind is not _BKind.UNKNOWN:
                return _Binding(
                    kind=exported.kind,
                    target_id=exported.target_id,
                    module_name=exported.module_name,
                    class_key=exported.class_key,
                    registry_key=exported.registry_key,
                    const=exported.const,
                    method=exported.method if exported.method is Method.REEXPORT else method,
                    confidence=combine(ceiling, exported.confidence),
                    note="; ".join(b for b in [note, exported.note] if b),
                )
            return _Binding(
                kind=_BKind.UNKNOWN,
                module_name=target_module,
                method=method,
                confidence=Confidence.UNKNOWN,
                note="; ".join(b for b in [note, "name not found in target module"] if b),
            )
        if spec.level:
            # A relative import always names something inside the tree. If the
            # tree has no such module the reference is dangling, and claiming
            # an edge to an invented absolute module would be an over-link.
            return _Binding(
                kind=_BKind.UNKNOWN,
                module_name=target_module,
                method=method,
                confidence=Confidence.UNKNOWN,
                note="; ".join(
                    b
                    for b in [
                        note,
                        f"relative import resolves to {target_module!r}, which is not "
                        "in the inventory",
                    ]
                    if b
                ),
            )
        # Outside the tree. There is no element to point at, so there is no
        # edge: an edge to a bare dotted name would fabricate a node. The call
        # site records it as THIRD_PARTY instead.
        return _Binding(
            kind=_BKind.CALLABLE,
            target_id="",
            module_name=target_module,
            method=method,
            confidence=Confidence.UNKNOWN,
            note="; ".join(b for b in [note, "target outside the inventory"] if b),
            external=True,
        )

    def _star_bindings(self, summary: _ModuleSum, spec: _ImportSpec) -> dict[str, _Binding]:
        """Names a ``from x import *`` binds. Memoised, so the residue record
        is written once however many times the scope is rebuilt."""
        cache_key = (summary.name, spec)
        cached = self._star_cache.get(cache_key)
        if cached is not None:
            return cached
        result = self._star_bindings_uncached(summary, spec)
        self._star_cache[cache_key] = result
        return result

    def _star_bindings_uncached(
        self, summary: _ModuleSum, spec: _ImportSpec
    ) -> dict[str, _Binding]:
        target_module = (
            _relative_module(summary.name, summary.is_package, spec.level, spec.module)
            if spec.level
            else spec.module
        )
        out: dict[str, _Binding] = {}
        source = self._modules.get(target_module)
        if source is None:
            self._record_unresolved(
                owner=summary.element_id,
                reason=UnresolvedReason.THIRD_PARTY,
                span=SourceSpan(path=summary.path, line=spec.line, col=spec.col),
                description=(
                    f"star import from {target_module!r}, which is outside the "
                    "inventory: the names it binds cannot be enumerated"
                ),
                attempted=(Method.IMPORT_STAR,),
            )
            return out
        names = source.dunder_all
        residue: list[str] = []
        if names is None:
            names = tuple(sorted(n for n in source.top_defs if not n.startswith("_")))
            residue = sorted(
                n
                for n in {**source.imports}.keys()
                if not n.startswith("_") and n not in source.top_defs
            )
        for name in names:
            binding = self._lookup_export(target_module, name, frozenset())
            if binding.kind is _BKind.UNKNOWN:
                residue.append(name)
                continue
            out[name] = _Binding(
                kind=binding.kind,
                target_id=binding.target_id,
                module_name=binding.module_name,
                class_key=binding.class_key,
                registry_key=binding.registry_key,
                const=binding.const,
                method=Method.IMPORT_STAR,
                confidence=combine(binding.confidence, Confidence.RESOLVED),
            )
        if residue:
            self._record_unresolved(
                owner=summary.element_id,
                reason=UnresolvedReason.AMBIGUOUS,
                span=SourceSpan(path=summary.path, line=spec.line, col=spec.col),
                description=(
                    f"star import from {target_module!r} also rebinds names this "
                    "analysis could not attach to a definition: "
                    + ", ".join(sorted(set(residue)))
                ),
                attempted=(Method.IMPORT_STAR,),
            )
        return out

    def _lookup_export(self, module: str, name: str, seen: frozenset[tuple[str, str]]) -> _Binding:
        key = (module, name)
        if key in seen:
            return _Binding(
                kind=_BKind.UNKNOWN,
                method=Method.REEXPORT,
                note="import cycle: export chain returns to itself",
            )
        cached = self._export_cache.get(key)
        if cached is not None:
            return cached
        summary = self._modules.get(module)
        if summary is None:
            return _UNKNOWN_BINDING
        seen = seen | {key}
        result = _UNKNOWN_BINDING
        if name in summary.top_defs:
            result = self._binding_for_def(module, summary.top_defs[name])
        elif f"{module}.{name}" in self._modules:
            sub = self._modules[f"{module}.{name}"]
            result = _Binding(
                kind=_BKind.MODULE,
                module_name=sub.name,
                target_id=sub.element_id,
                method=Method.IMPORT_ABSOLUTE,
                confidence=Confidence.RESOLVED,
            )
        elif name in summary.imports:
            spec = summary.imports[name]
            inner = self._binding_for_import_seen(summary, spec, seen)
            if inner.kind is not _BKind.UNKNOWN:
                result = _Binding(
                    kind=inner.kind,
                    target_id=inner.target_id,
                    module_name=inner.module_name,
                    class_key=inner.class_key,
                    registry_key=inner.registry_key,
                    const=inner.const,
                    method=Method.REEXPORT,
                    confidence=combine(inner.confidence, Confidence.RESOLVED),
                    external=inner.external,
                    note="re-exported",
                )
        elif self.registry_binding(module, name) is not None:
            found = self.registry_binding(module, name)
            assert found is not None
            result = found
        elif name in summary.assigns:
            result = self._binding_from_assign_text(module, summary.assigns[name])
        else:
            for spec in summary.stars:
                target_module = (
                    _relative_module(summary.name, summary.is_package, spec.level, spec.module)
                    if spec.level
                    else spec.module
                )
                if target_module in self._modules:
                    inner = self._lookup_export(target_module, name, seen)
                    if inner.kind is not _BKind.UNKNOWN:
                        result = _Binding(
                            kind=inner.kind,
                            target_id=inner.target_id,
                            module_name=inner.module_name,
                            class_key=inner.class_key,
                            registry_key=inner.registry_key,
                            method=Method.IMPORT_STAR,
                            confidence=combine(inner.confidence, Confidence.RESOLVED),
                        )
                        break
        if not seen - {key}:
            self._export_cache[key] = result
        return result

    def _binding_for_import_seen(
        self, summary: _ModuleSum, spec: _ImportSpec, seen: frozenset[tuple[str, str]]
    ) -> _Binding:
        if spec.is_module_alias or spec.level == 0 and spec.module not in self._modules:
            return self._binding_for_import(summary, spec)
        target_module = (
            _relative_module(summary.name, summary.is_package, spec.level, spec.module)
            if spec.level
            else spec.module
        )
        submodule = f"{target_module}.{spec.orig_name}" if target_module else spec.orig_name
        if submodule in self._modules:
            return _Binding(
                kind=_BKind.MODULE,
                module_name=submodule,
                target_id=self._modules[submodule].element_id,
                method=Method.IMPORT_RELATIVE if spec.level else Method.IMPORT_ABSOLUTE,
                confidence=Confidence.RESOLVED,
            )
        if target_module in self._modules:
            return self._lookup_export(target_module, spec.orig_name, seen)
        return self._binding_for_import(summary, spec)

    # -- classes and MRO ------------------------------------------------

    def _class_key_from_text(self, text: str, module: str) -> tuple[str, str] | None:
        """Resolve a base-class / annotation source fragment to a class key."""
        text = _strip_subscript(text).strip()
        if not text:
            return None
        binding = self._binding_for_dotted(text, module)
        if binding.kind is _BKind.CLASS and binding.class_key is not None:
            return binding.class_key
        return None

    def _binding_for_dotted(self, dotted: str, module: str) -> _Binding:
        parts = dotted.split(".")
        scope = self._module_scope(module)
        summary = self._modules.get(module)
        binding = scope.get(parts[0], _UNKNOWN_BINDING)
        if binding.kind is _BKind.UNKNOWN and summary is not None and len(parts) == 1:
            if parts[0] in summary.classes:
                return _Binding(
                    kind=_BKind.CLASS,
                    class_key=(module, parts[0]),
                    target_id=summary.classes[parts[0]].element_id,
                    method=Method.SCOPE_LOOKUP,
                    confidence=Confidence.RESOLVED,
                )
            return _UNKNOWN_BINDING
        for part in parts[1:]:
            binding = self._attr_binding(binding, part, module)
        return binding

    def _class_element(self, key: tuple[str, str]) -> str:
        summary = self._modules.get(key[0])
        if summary is None:
            return make_id(key[0], key[1])
        info = summary.classes.get(key[1])
        return info.element_id if info else make_id(key[0], key[1])

    def _mro(self, key: tuple[str, str]) -> tuple[tuple[tuple[str, str], ...], bool]:
        """(linearisation, complete). Incomplete means an unknown base exists."""
        cached = self._mro_cache.get(key)
        if cached is not None:
            return cached
        self._mro_cache[key] = ((key,), False)  # cycle guard
        summary = self._modules.get(key[0])
        info = summary.classes.get(key[1]) if summary else None
        if info is None:
            result: tuple[tuple[tuple[str, str], ...], bool] = ((key,), False)
            self._mro_cache[key] = result
            return result
        order: list[tuple[str, str]] = [key]
        complete = True
        for base in info.bases:
            stripped = _strip_subscript(base).strip()
            if stripped in {"object", "abc.ABC", "ABC", "Protocol", "typing.Protocol"}:
                continue
            base_key = self._class_key_from_text(base, key[0])
            if base_key is None:
                complete = False
                continue
            sub_order, sub_complete = self._mro(base_key)
            complete = complete and sub_complete
            for item in sub_order:
                if item not in order:
                    order.append(item)
        result = (tuple(order), complete)
        self._mro_cache[key] = result
        return result

    def _members(self, key: tuple[str, str]) -> dict[str, tuple[str, tuple[str, str], bool]]:
        """attribute name -> (element id, owning class key, is_property)."""
        out: dict[str, tuple[str, tuple[str, str], bool]] = {}
        order, _ = self._mro(key)
        for cls_key in order:
            summary = self._modules.get(cls_key[0])
            info = summary.classes.get(cls_key[1]) if summary else None
            if info is None:
                continue
            for name in sorted(info.methods):
                if name in out:
                    continue
                qualname = info.methods[name]
                ordinal = info.method_ordinal.get(name, 1)
                out[name] = (
                    self._id_for(cls_key[0], qualname, ordinal),
                    cls_key,
                    name in info.properties,
                )
            for name in sorted(info.nested_classes):
                out.setdefault(
                    name,
                    (self._id_for(cls_key[0], info.nested_classes[name]), cls_key, False),
                )
        return out

    def _lookup_member(
        self, key: tuple[str, str], attr: str
    ) -> tuple[str, tuple[str, str], bool] | None:
        return self._members(key).get(attr)

    def _overriding_subclasses(self, key: tuple[str, str], attr: str) -> list[str]:
        out: list[str] = []
        stack = list(self._subclasses.get(key, ()))
        seen: set[tuple[str, str]] = set()
        while stack:
            sub = stack.pop()
            if sub in seen:
                continue
            seen.add(sub)
            summary = self._modules.get(sub[0])
            info = summary.classes.get(sub[1]) if summary else None
            if info is not None and attr in info.methods:
                out.append(
                    self._id_for(sub[0], info.methods[attr], info.method_ordinal.get(attr, 1))
                )
            stack.extend(self._subclasses.get(sub, ()))
        return sorted(set(out))

    def _attr_binding(self, base: _Binding, attr: str, module: str) -> _Binding:
        """Resolve ``<base>.<attr>`` to a binding."""
        if base.kind is _BKind.MODULE:
            target_module = base.module_name
            sub = f"{target_module}.{attr}"
            if sub in self._modules:
                return _Binding(
                    kind=_BKind.MODULE,
                    module_name=sub,
                    target_id=self._modules[sub].element_id,
                    method=Method.IMPORT_ABSOLUTE,
                    confidence=Confidence.RESOLVED,
                )
            if target_module in self._modules:
                found = self._lookup_export(target_module, attr, frozenset())
                if found.kind is not _BKind.UNKNOWN:
                    return found
                return _Binding(
                    kind=_BKind.UNKNOWN,
                    module_name=target_module,
                    note=f"{target_module!r} defines no attribute {attr!r}",
                )
            summary = self._modules.get(module)
            if summary is not None and sub in summary.dotted_imports:
                return _Binding(
                    kind=_BKind.MODULE,
                    module_name=sub,
                    target_id=make_id(sub),
                    method=Method.IMPORT_ABSOLUTE,
                    confidence=Confidence.RESOLVED,
                    external=True,
                )
            return _Binding(
                kind=_BKind.CALLABLE,
                target_id=make_id(target_module, attr),
                module_name=target_module,
                method=Method.IMPORT_ABSOLUTE,
                confidence=Confidence.RESOLVED,
                external=True,
                note="target outside the inventory",
            )
        if base.kind in (_BKind.CLASS, _BKind.INSTANCE) and base.class_key is not None:
            found = self._lookup_member(base.class_key, attr)
            if found is not None:
                target_id, owner, is_prop = found
                summary = self._modules.get(owner[0])
                info = summary.classes.get(owner[1]) if summary else None
                if info is not None and attr in info.nested_classes:
                    return _Binding(
                        kind=_BKind.CLASS,
                        class_key=(owner[0], info.nested_classes[attr]),
                        target_id=target_id,
                        method=Method.MRO_DISPATCH,
                        confidence=Confidence.RESOLVED,
                    )
                if base.kind is _BKind.CLASS:
                    conf = Confidence.RESOLVED if owner == base.class_key else Confidence.PROBABLE
                else:
                    conf = Confidence.PROBABLE
                return _Binding(
                    kind=_BKind.CALLABLE,
                    target_id=target_id,
                    class_key=base.class_key,
                    method=Method.MRO_DISPATCH,
                    confidence=conf,
                    is_property=is_prop,
                    note="" if owner == base.class_key else f"inherited from {owner[1]}",
                )
            # attribute assigned on the instance in a method body
            attrs = self._self_attr_bindings(base.class_key, attr)
            if len(attrs) == 1:
                return attrs[0]
            if len(attrs) > 1:
                return _Binding(
                    kind=_BKind.CANDIDATES,
                    candidates=tuple(sorted(b.target_id for b in attrs)),
                    method=Method.DATAFLOW,
                    confidence=Confidence.UNKNOWN,
                    note=f"self.{attr} is bound to more than one callable",
                )
        if base.kind is _BKind.REGISTRY and attr in _CONTAINER_WRITE_METHODS | {
            "get",
            "values",
            "keys",
            "items",
            "pop",
        }:
            return _Binding(
                kind=_BKind.REGISTRY,
                registry_key=base.registry_key,
                target_id=base.target_id,
                method=Method.REGISTRY_MEMBERSHIP,
                confidence=base.confidence,
                note=f"registry.{attr}",
            )
        return _UNKNOWN_BINDING

    def _self_attr_bindings(self, key: tuple[str, str], attr: str) -> list[_Binding]:
        out: list[_Binding] = []
        order, _ = self._mro(key)
        for cls_key in order:
            summary = self._modules.get(cls_key[0])
            info = summary.classes.get(cls_key[1]) if summary else None
            if info is None or attr not in info.self_attrs:
                continue
            for text in info.self_attrs[attr]:
                binding = self._binding_from_assign_text(cls_key[0], text)
                if binding.kind in (_BKind.CALLABLE, _BKind.CLASS) and binding.target_id:
                    out.append(
                        _Binding(
                            kind=binding.kind,
                            target_id=binding.target_id,
                            class_key=binding.class_key,
                            method=Method.DATAFLOW,
                            confidence=Confidence.PROBABLE,
                            note=f"self.{attr} assigned in {cls_key[1]}",
                        )
                    )
            if out:
                break
        return out

    # -- per-module resolution ------------------------------------------

    def _resolve_module(self, summary: _ModuleSum) -> None:
        tree = self._parse(summary)
        if tree is None:
            return
        walker = _CallResolver(self, summary)
        walker.run(tree)
        del tree

    # -- config wiring ---------------------------------------------------

    def _resolve_configs(self) -> None:
        for path in self._config_files:
            declared = path in self.declared_configs
            strings = self._config_strings(path)
            lines = self._config_lines(path)
            for pointer, value, key_name in strings:
                self._match_config_string(
                    path, pointer, value, key_name, declared, self._value_span(path, lines, value)
                )

    def _config_lines(self, path: str) -> list[str]:
        full = Path(path) if Path(path).is_absolute() else self.root / path
        try:
            return full.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return []

    def _value_span(self, path: str, lines: Sequence[str], value: str) -> SourceSpan:
        """Point at the line and column the string actually occupies.

        A config finding that names the file but not the line sends the owner
        hunting through it; the exact key is the whole point of
        ``config_key_id``.
        """
        needle = f'"{value}"'
        for index, line in enumerate(lines):
            col = line.find(needle)
            if col < 0:
                col = line.find(f"'{value}'")
            if col < 0 and value in line:
                col = line.find(value)
            if col >= 0:
                return SourceSpan(path=path, line=index + 1, col=col)
        return SourceSpan(path=path, line=1)

    def _config_strings(self, path: str) -> list[tuple[str, str, str]]:
        full = Path(path) if Path(path).is_absolute() else self.root / path
        suffix = Path(path).suffix.lower()
        data: Any = None
        try:
            if suffix == ".json":
                data = json.loads(full.read_text(encoding="utf-8"))
            elif suffix == ".toml":
                data = tomllib.loads(full.read_text(encoding="utf-8"))
            elif suffix in (".ini", ".cfg"):
                parser = configparser.ConfigParser()
                parser.read_string(full.read_text(encoding="utf-8"))
                data = {
                    section: dict(parser.items(section)) for section in parser.sections()
                }
            elif suffix in (".yaml", ".yml"):
                try:
                    import yaml  # type: ignore[import-untyped]
                except ImportError:
                    self._record_unresolved(
                        owner=make_id(path),
                        reason=UnresolvedReason.THIRD_PARTY,
                        span=SourceSpan(path=path, line=1),
                        description=(
                            "YAML config not read: the optional PyYAML adapter is not "
                            "installed, so component names in this file are unresolved"
                        ),
                        attempted=(Method.CONFIG_STRING_MATCH,),
                    )
                    return []
                data = yaml.safe_load(full.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._record_unresolved(
                owner=make_id(path),
                reason=UnresolvedReason.MISSING_TARGET,
                span=SourceSpan(path=path, line=1),
                description="config file listed in the inventory was not found",
                attempted=(Method.CONFIG_STRING_MATCH,),
            )
            return []
        except (UnicodeDecodeError, ValueError, configparser.Error) as exc:
            self._record_unresolved(
                owner=make_id(path),
                reason=UnresolvedReason.DECODE_ERROR,
                span=SourceSpan(path=path, line=1),
                description=f"config file could not be parsed: {type(exc).__name__}",
                attempted=(Method.CONFIG_STRING_MATCH,),
            )
            return []
        out: list[tuple[str, str, str]] = []
        _walk_config(data, [], "", out)
        return out

    def _match_config_string(
        self,
        path: str,
        pointer: str,
        value: str,
        key_name: str,
        declared: bool,
        span: SourceSpan,
    ) -> None:
        text = value.strip()
        if not text or len(text) > 200 or " " in text:
            return
        source_id = config_key_id(path, pointer)
        candidates = self._candidates_for_config(text)
        dotted_shape = all(part.isidentifier() for part in text.split(".")) and "." in text
        colon_shape = ":" in text and all(
            part.isidentifier() for part in text.replace(":", ".").split(".") if part
        )
        looks_like_wiring = (
            dotted_shape or colon_shape or key_name.lower() in WIRING_KEY_WORDS
        )
        if len(candidates) == 1:
            target, how = candidates[0]
            confidence = Confidence.PROBABLE if (declared and how != "name") else Confidence.HEURISTIC
            self._emit(
                EdgeKind.CONFIGURES,
                source_id,
                target,
                Method.CONFIG_STRING_MATCH,
                confidence,
                call_site=span,
                note=(
                    f"config key {pointer} = {text!r} matched by {how}; "
                    + ("owner-declared wiring file" if declared else "auto-detected config file")
                ),
            )
            return
        if len(candidates) > 1:
            self._record_unresolved(
                owner=source_id,
                reason=UnresolvedReason.AMBIGUOUS,
                span=span,
                description=(
                    f"config key {pointer} = {text!r} matches more than one element; "
                    "no edge claimed"
                ),
                attempted=(Method.CONFIG_STRING_MATCH,),
                candidates=[c[0] for c in candidates],
                candidate_confidence=Confidence.HEURISTIC,
            )
            return
        if looks_like_wiring:
            near = self._near_name_candidates(text)
            self._record_unresolved(
                owner=source_id,
                reason=UnresolvedReason.MISSING_TARGET,
                span=span,
                description=(
                    f"config key {pointer} holds {text!r}; no element in the "
                    "target has that name"
                ),
                attempted=(Method.CONFIG_STRING_MATCH, Method.NAME_HEURISTIC),
                candidates=near,
                candidate_confidence=Confidence.HEURISTIC if near else Confidence.UNKNOWN,
            )

    def _candidates_for_config(self, text: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        normalised = text.replace(":", ".")
        if "." in normalised:
            parts = normalised.split(".")
            for split in range(len(parts) - 1, 0, -1):
                module = ".".join(parts[:split])
                qualname = ".".join(parts[split:])
                if module in self._modules:
                    element = self._by_qual.get((module, qualname, 1))
                    if element is not None:
                        out.append((element.id, "dotted path"))
                        return out
                    summary = self._modules[module]
                    if qualname in summary.classes:
                        out.append((summary.classes[qualname].element_id, "dotted path"))
                        return out
                    if qualname in summary.funcs:
                        out.append((summary.funcs[qualname].element_id, "dotted path"))
                        return out
            if normalised in self._modules:
                return [(self._modules[normalised].element_id, "module path")]
            return []
        keyed = sorted(set(self._registry_keys.get(text, ())))
        if len(keyed) == 1:
            return [(keyed[0], "registry key")]
        if len(keyed) > 1:
            return [(k, "registry key") for k in keyed]
        named = [
            element_id
            for element_id in sorted(set(self._by_name.get(text, ())))
            if self._elements[element_id].kind
            in (ElementKind.CLASS, ElementKind.FUNCTION, ElementKind.METHOD)
        ]
        return [(element_id, "name") for element_id in named]

    def _near_name_candidates(self, text: str) -> list[str]:
        bare = text.replace(":", ".").split(".")[-1]
        lowered = bare.lower()
        out: list[str] = []
        for name, ids in self._by_name.items():
            if name.lower() == lowered or name.lower().replace("_", "") == lowered.replace("_", ""):
                out.extend(ids)
        return sorted(set(out))[: self.max_candidates]

    def register_key(self, key: str, element_id: str) -> None:
        """Record that *element_id* is registered under the string *key*."""
        if key and element_id not in self._registry_keys[key]:
            self._registry_keys[key].append(element_id)


def _walk_config(node: Any, parts: list[str], key_name: str, out: list[tuple[str, str, str]]) -> None:
    if isinstance(node, dict):
        for key in sorted(node, key=str):
            _walk_config(node[key], [*parts, str(key)], str(key), out)
    elif isinstance(node, (list, tuple)):
        for index, item in enumerate(node):
            _walk_config(item, [*parts, str(index)], key_name, out)
    elif isinstance(node, str):
        out.append((_json_pointer(parts), node, key_name))


# ---------------------------------------------------------------------------
# pass B -- resolve call sites in one module
# ---------------------------------------------------------------------------


@dataclass
class _Scope:
    kind: str
    names: dict[str, _Binding]
    parent: "_Scope | None" = None
    class_key: tuple[str, str] | None = None


class _CallResolver(ast.NodeVisitor):
    """Walks one module and emits edges and unresolved records."""

    def __init__(self, resolver: Resolver, summary: _ModuleSum) -> None:
        self.r = resolver
        self.s = summary
        self.path = summary.path
        self.module = summary.name
        self.scope = _Scope(kind="module", names=dict(resolver._module_scope(summary.name)))
        self.owner_stack: list[str] = [summary.element_id]
        self.class_stack: list[tuple[str, str]] = []
        self.prefix = ""
        self.ordinals: dict[str, int] = {}
        self.cond_depth = 0
        self.tc_depth = 0
        #: results of dynamic-wiring calls, keyed by node identity, so a node
        #: evaluated both as a statement and as a value emits its edge once.
        self._dynamic_cache: dict[int, _Binding] = {}
        #: nodes whose value is consumed by an enclosing call, so the reference
        #: edge would duplicate that call's edge.
        self._suppress_reference: set[int] = set()
        #: the element a module- or class-level assignment is binding, so a
        #: dynamic value gets the ID of the name that holds it.
        self._assign_name: str = ""

    # -- plumbing -------------------------------------------------------

    @property
    def owner(self) -> str:
        return self.owner_stack[-1]

    def run(self, tree: ast.Module) -> None:
        for node in tree.body:
            self.visit(node)

    def _ordinal(self, qualname: str) -> int:
        n = self.ordinals.get(qualname, 0) + 1
        self.ordinals[qualname] = n
        return n

    def _lookup(self, name: str) -> _Binding:
        scope: _Scope | None = self.scope
        first = True
        while scope is not None:
            if scope.kind != "class" or first:
                binding = scope.names.get(name)
                if binding is not None:
                    return binding
            first = False
            scope = scope.parent
        return _UNKNOWN_BINDING

    def _bind(self, name: str, binding: _Binding) -> None:
        self.scope.names[name] = binding

    def _push(self, kind: str, class_key: tuple[str, str] | None = None) -> None:
        self.scope = _Scope(kind=kind, names={}, parent=self.scope, class_key=class_key)

    def _pop(self) -> None:
        assert self.scope.parent is not None
        self.scope = self.scope.parent

    def _emit(
        self,
        kind: EdgeKind,
        target_id: str,
        method: Method,
        confidence: Confidence,
        node: ast.AST,
        note: str = "",
        source_id: str | None = None,
    ) -> None:
        self.r._emit(
            kind,
            source_id if source_id is not None else self.owner,
            target_id,
            method,
            confidence,
            call_site=_span(self.path, node),
            note=note,
        )

    def _unresolved(
        self,
        node: ast.AST,
        reason: UnresolvedReason,
        description: str,
        attempted: tuple[Method, ...],
        candidates: Sequence[str] = (),
        candidate_confidence: Confidence = Confidence.UNKNOWN,
        site: str = "",
    ) -> None:
        """*site* names the gap inside the enclosing element, so the record ID
        is readable and survives reformatting: ``m::run::getattr@name``."""
        self.r._record_unresolved(
            owner=f"{self.owner}::{site}" if site else self.owner,
            reason=reason,
            span=_span(self.path, node),
            description=description,
            attempted=attempted,
            candidates=candidates,
            candidate_confidence=candidate_confidence,
        )

    # -- definitions ----------------------------------------------------

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualname = f"{self.prefix}{node.name}"
        ordinal = self._ordinal(qualname)
        element_id = self.r._id_for(self.module, qualname, ordinal)
        key = (self.module, qualname)

        for base in node.bases:
            self._inherit_edge(element_id, base)
        for kw in node.keywords:
            if kw.arg == "metaclass":
                self._inherit_edge(element_id, kw.value, note="metaclass")
        self._decorator_edges(node.decorator_list, element_id, node, is_class=True)

        self._bind(
            node.name,
            _Binding(
                kind=_BKind.CLASS,
                target_id=element_id,
                class_key=key,
                method=Method.AST_DIRECT,
                confidence=Confidence.CERTAIN,
            ),
        )
        self._register_subclass_hook(key, element_id, node)

        self.owner_stack.append(element_id)
        self.class_stack.append(key)
        outer = self.prefix
        self.prefix = f"{qualname}."
        self._push("class", class_key=key)
        for child in node.body:
            self.visit(child)
        self._pop()
        self.prefix = outer
        self.class_stack.pop()
        self.owner_stack.pop()

    def _inherit_edge(self, element_id: str, base: ast.expr, note: str = "") -> None:
        dotted = _dotted(base)
        if not dotted:
            self._visit_receiver(base)
            self._unresolved(
                base,
                UnresolvedReason.DYNAMIC_NAME,
                f"base class expression {_text(base)!r} is computed; no inheritance edge claimed",
                (Method.SCOPE_LOOKUP,),
                site="base@computed",
            )
            return
        binding = self._resolve_dotted(dotted)
        if binding.kind is _BKind.CLASS and binding.target_id:
            self._emit(
                EdgeKind.INHERITS,
                binding.target_id,
                Method.AST_DIRECT,
                Confidence.CERTAIN,
                base,
                note=note or "the base class list is read straight off the AST",
                source_id=element_id,
            )
        elif dotted in {"object", "ABC", "abc.ABC", "Protocol", "typing.Protocol"}:
            return
        else:
            self._unresolved(
                base,
                UnresolvedReason.MISSING_TARGET,
                f"base class {dotted!r} could not be resolved to an element",
                (Method.SCOPE_LOOKUP, Method.IMPORT_ABSOLUTE),
                candidates=self._name_candidates(dotted.split(".")[-1], (ElementKind.CLASS,)),
                candidate_confidence=Confidence.HEURISTIC,
                site=f"base@{dotted}",
            )

    def _register_subclass_hook(
        self, key: tuple[str, str], element_id: str, node: ast.ClassDef
    ) -> None:
        """``__init_subclass__``/metaclass registration: the base collects its
        subclasses. Emitted from the base, once per known subclass."""
        summary = self.r._modules.get(key[0])
        info = summary.classes.get(key[1]) if summary else None
        if info is None:
            return
        parents: list[tuple[str, str]] = []
        order, _ = self.r._mro(key)
        for ancestor in order[1:]:
            a_summary = self.r._modules.get(ancestor[0])
            a_info = a_summary.classes.get(ancestor[1]) if a_summary else None
            if a_info is not None and (a_info.defines_init_subclass or a_info.metaclass):
                parents.append(ancestor)
        if info.metaclass:
            meta_key = self.r._class_key_from_text(info.metaclass, key[0])
            if meta_key is not None:
                parents.append(meta_key)
        for parent in parents:
            container = self.r._class_element(parent)
            self._emit(
                EdgeKind.REGISTERS,
                element_id,
                Method.REGISTRY_MEMBERSHIP,
                Confidence.PROBABLE,
                node,
                note="subclass registration via __init_subclass__ or metaclass",
                source_id=container,
            )
            self.r.register_key(node.name, element_id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node)

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualname = f"{self.prefix}{node.name}"
        ordinal = self._ordinal(qualname)
        element_id = self.r._id_for(self.module, qualname, ordinal)

        self._decorator_edges(node.decorator_list, element_id, node, is_class=False)
        binding = _Binding(
            kind=_BKind.CALLABLE,
            target_id=element_id,
            method=Method.AST_DIRECT,
            confidence=Confidence.CERTAIN,
        )
        if not self.class_stack:
            self._bind(node.name, binding)

        for default in [*node.args.defaults, *[d for d in node.args.kw_defaults if d]]:
            self.visit(default)

        self.owner_stack.append(element_id)
        outer = self.prefix
        self.prefix = f"{qualname}.<locals>."
        self._push("function")
        self._bind_parameters(node, element_id)
        for child in node.body:
            self.visit(child)
        self._pop()
        self.prefix = outer
        self.owner_stack.pop()

    def _bind_parameters(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, element_id: str = ""
    ) -> None:
        self._bind_decorated_parameter(node, element_id)
        args = node.args
        all_args = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        decorators = {_text(d).split("(")[0] for d in node.decorator_list}
        if self.class_stack and all_args:
            key = self.class_stack[-1]
            first = all_args[0].arg
            if "staticmethod" not in decorators:
                kind = _BKind.CLASS if "classmethod" in decorators else _BKind.INSTANCE
                self._bind(
                    first,
                    _Binding(
                        kind=kind,
                        class_key=key,
                        target_id=self.r._class_element(key),
                        method=Method.MRO_DISPATCH,
                        confidence=Confidence.RESOLVED,
                        note="bound receiver",
                    ),
                )
                all_args = all_args[1:]
        for arg in all_args:
            if arg.annotation is None:
                continue
            key = self.r._class_key_from_text(_text(arg.annotation), self.module)
            if key is not None:
                self._bind(
                    arg.arg,
                    _Binding(
                        kind=_BKind.INSTANCE,
                        class_key=key,
                        target_id=self.r._class_element(key),
                        method=Method.MRO_DISPATCH,
                        confidence=Confidence.PROBABLE,
                        note="from parameter annotation",
                    ),
                )

    def _bind_decorated_parameter(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, element_id: str
    ) -> None:
        """Inside ``def trace(func)``, ``func`` *is* the decorated function.

        The parameter is not a free variable: the decorator syntax binds it.
        Resolving it turns the body of every wrapper from an unresolved call
        site into the edge the cascade actually needs. PROBABLE, because the
        binding is read off the decorator application rather than a call.
        """
        if not element_id:
            return
        targets = self.r.decorated_by(element_id)
        if not targets:
            return
        args = [*node.args.posonlyargs, *node.args.args]
        if not args:
            return
        first = args[0].arg
        if len(targets) == 1:
            self.scope.names[first] = _Binding(
                kind=_BKind.CALLABLE,
                target_id=targets[0],
                method=Method.DECORATOR_UNWRAP,
                confidence=Confidence.PROBABLE,
                note="`" + first + "` inside the decorator is the decorated function",
            )
        else:
            self.scope.names[first] = _Binding(
                kind=_BKind.CANDIDATES,
                candidates=targets,
                method=Method.DECORATOR_UNWRAP,
                confidence=Confidence.UNKNOWN,
                note="this decorator is applied to more than one element",
            )

    def _decorator_edges(
        self,
        decorators: Sequence[ast.expr],
        element_id: str,
        node: ast.AST,
        *,
        is_class: bool,
    ) -> None:
        for decorator in decorators:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            dotted = _dotted(target)
            if not dotted:
                continue
            binding = self._resolve_dotted(dotted)
            if binding.target_id and binding.kind in (
                _BKind.CALLABLE,
                _BKind.CLASS,
                _BKind.REGISTRY,
            ):
                if dotted in _PROPERTY_DECORATORS or dotted in _STATIC_DECORATORS:
                    continue
                self._emit(
                    EdgeKind.DECORATES,
                    element_id,
                    Method.AST_DIRECT,
                    Confidence.CERTAIN,
                    decorator,
                    note="the decorator list is read straight off the AST",
                    source_id=binding.target_id,
                )
                self._registration_edge(decorator, dotted, binding, element_id, node)
            elif dotted not in _PROPERTY_DECORATORS and dotted not in _STATIC_DECORATORS:
                self._unresolved(
                    decorator,
                    UnresolvedReason.MISSING_TARGET,
                    f"decorator {dotted!r} could not be resolved; the wrapper is unknown",
                    (Method.SCOPE_LOOKUP, Method.DECORATOR_UNWRAP),
                    candidates=self._name_candidates(
                        dotted.split(".")[-1], (ElementKind.FUNCTION, ElementKind.METHOD, ElementKind.CLASS)
                    ),
                    candidate_confidence=Confidence.HEURISTIC,
                    site=f"decorator@{dotted}",
                )
            if isinstance(decorator, ast.Call):
                for arg in [*decorator.args, *[k.value for k in decorator.keywords]]:
                    self.visit(arg)

    def _registration_edge(
        self,
        decorator: ast.expr,
        dotted: str,
        binding: _Binding,
        element_id: str,
        node: ast.AST,
    ) -> None:
        """If the decorator is a registrar, link its container to the element."""
        container_id = ""
        registrar: _FuncSum | None = None
        owner_module = self.module
        for module_name, summary in self.r._modules.items():
            for qualname, func in summary.funcs.items():
                if func.element_id == binding.target_id and func.registrar_container:
                    registrar = func
                    owner_module = module_name
                    break
            if registrar is not None:
                break
        if registrar is None:
            return
        if registrar.registrar_container_is_self:
            receiver = dotted.rsplit(".", 1)[0] if "." in dotted else ""
            if receiver:
                recv_binding = self._resolve_dotted(receiver)
                container_id = recv_binding.target_id
        else:
            reg = self.r._modules[owner_module].registries.get(registrar.registrar_container)
            container_id = (
                reg.element_id
                if reg is not None
                else make_id(owner_module, registrar.registrar_container)
            )
        if not container_id:
            container_id = binding.target_id
        key_literal = ""
        if isinstance(decorator, ast.Call):
            for arg in decorator.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    key_literal = arg.value
                    break
            if not key_literal:
                for kw in decorator.keywords:
                    if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        key_literal = kw.value.value
                        break
        if not key_literal:
            element = self.r._elements.get(element_id)
            key_literal = element.name if element is not None else ""
        self._emit(
            EdgeKind.REGISTERS,
            element_id,
            Method.DECORATOR_REGISTRATION,
            Confidence.PROBABLE,
            decorator,
            note=f"registered by {dotted} under key {key_literal!r}" if key_literal else f"registered by {dotted}",
            source_id=container_id,
        )
        self.r.register_key(key_literal, element_id)

    # -- statements -----------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound = alias.asname or alias.name.split(".")[0]
            spec = _ImportSpec(
                bound_name=bound,
                module=alias.name if alias.asname else alias.name.split(".")[0],
                level=0,
                orig_name="",
                is_module_alias=True,
                line=node.lineno,
                col=node.col_offset,
                conditional=self.cond_depth > 0,
                type_checking=self.tc_depth > 0,
                scope_qualname="",
            )
            binding = self.r._binding_for_import(self.s, spec)
            self._bind_import(bound, binding)
            if binding.external or not binding.target_id:
                self._third_party(node, bound, alias.name, Method.IMPORT_ABSOLUTE)
                continue
            self._emit(
                EdgeKind.IMPORTS,
                binding.target_id,
                Method.IMPORT_ABSOLUTE,
                binding.confidence,
                node,
                note=binding.note,
            )

    def _third_party(
        self, node: ast.AST, bound: str, module: str, method: Method
    ) -> None:
        """A name imported from outside the tree.

        No edge: there is no element to point at and inventing one would put a
        node in the graph that no file backs. A THIRD_PARTY record instead, so
        the dependency is visible and card 4 can put a barrier on it.
        """
        self.r._record_unresolved(
            owner=make_id(self.module, bound),
            reason=UnresolvedReason.THIRD_PARTY,
            span=_span(self.path, node),
            description=f"{module!r} is outside the target tree; no element to point at",
            attempted=(method,),
        )

    def _bind_import(self, name: str, binding: _Binding) -> None:
        """Bind an imported name.

        A name imported twice under different conditions (the
        ``try: from fast import x / except ImportError: from slow import x``
        shape) becomes a candidate set rather than "whichever branch came
        last". Picking the last branch would be a coin flip presented as a
        fact.
        """
        existing = self.scope.names.get(name)
        if (
            self.cond_depth > 0
            and existing is not None
            and existing.target_id
            and binding.target_id
            and existing.target_id != binding.target_id
        ):
            merged = sorted({existing.target_id, binding.target_id} | set(existing.candidates))
            self._bind(
                name,
                _Binding(
                    kind=_BKind.CANDIDATES,
                    candidates=tuple(merged),
                    method=binding.method,
                    confidence=Confidence.UNKNOWN,
                    note="bound by more than one conditional import",
                ),
            )
            return
        self._bind(name, binding)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        target_module = (
            _relative_module(self.module, self.s.is_package, node.level, node.module or "")
            if node.level
            else (node.module or "")
        )
        method = Method.IMPORT_RELATIVE if node.level else Method.IMPORT_ABSOLUTE
        for alias in node.names:
            if alias.name == "*":
                self._star_import(node, target_module)
                continue
            spec = _ImportSpec(
                bound_name=alias.asname or alias.name,
                module=node.module or "",
                level=node.level,
                orig_name=alias.name,
                is_module_alias=False,
                line=node.lineno,
                col=node.col_offset,
                conditional=self.cond_depth > 0,
                type_checking=self.tc_depth > 0,
                scope_qualname="",
            )
            binding = self.r._binding_for_import(self.s, spec)
            self._bind_import(spec.bound_name, binding)
            if binding.external:
                self._third_party(node, spec.bound_name, target_module, method)
                continue
            if binding.kind is _BKind.UNKNOWN or not binding.target_id:
                self.r._record_unresolved(
                    owner=make_id(self.module, spec.bound_name),
                    reason=UnresolvedReason.MISSING_TARGET,
                    span=_span(self.path, node),
                    description=(
                        f"`from {'.' * node.level}{node.module or ''} import "
                        f"{alias.name}` does not resolve to an element"
                        + (
                            f"; {binding.note}"
                            if binding.note
                            else ""
                        )
                    ),
                    attempted=(method,),
                    candidates=self._name_candidates(alias.name, ()),
                    candidate_confidence=Confidence.HEURISTIC,
                )
                continue
            # An absolute `from a.b import c` names the module a.b explicitly,
            # so a.b is imported too and gets its own edge. A relative import
            # names no module the reader can see, so only the bound name does.
            if not node.level and target_module in self.r._modules:
                module_target = self.r._modules[target_module].element_id
                if module_target != binding.target_id:
                    self._emit(
                        EdgeKind.IMPORTS,
                        module_target,
                        method,
                        Confidence.RESOLVED,
                        node,
                        note="dotted name names exactly one module inside the target",
                    )
            self._emit(
                EdgeKind.IMPORTS,
                binding.target_id,
                binding.method if binding.method is not Method.SCOPE_LOOKUP else method,
                binding.confidence,
                node,
                note=binding.note or "the imported name binds to exactly one definition",
            )

    def _star_import(self, node: ast.ImportFrom, target_module: str) -> None:
        spec = _ImportSpec(
            bound_name="*",
            module=node.module or "",
            level=node.level,
            orig_name="*",
            is_module_alias=False,
            line=node.lineno,
            col=node.col_offset,
            conditional=False,
            type_checking=False,
            scope_qualname="",
        )
        bindings = self.r._star_bindings(self.s, spec)
        for name in sorted(bindings):
            self.scope.names.setdefault(name, bindings[name])
        if target_module in self.r._modules:
            self._emit(
                EdgeKind.IMPORTS,
                self.r._modules[target_module].element_id,
                Method.IMPORT_STAR,
                Confidence.RESOLVED,
                node,
                note=f"star import binds {len(bindings)} name(s)",
            )
            for name in sorted(bindings):
                binding = bindings[name]
                if binding.target_id:
                    self._emit(
                        EdgeKind.IMPORTS,
                        binding.target_id,
                        Method.IMPORT_STAR,
                        Confidence.RESOLVED,
                        node,
                        note=f"bound as {name!r} by `import *` via __all__",
                    )
        else:
            self._third_party(node, f"*@{target_module}", target_module, Method.IMPORT_STAR)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        type_checking = _is_type_checking(node.test) and not (
            isinstance(node.test, ast.Name) and node.test.id in self.s.assigns
        )
        self.cond_depth += 1
        if type_checking:
            self.tc_depth += 1
        for child in node.body:
            self.visit(child)
        if type_checking:
            self.tc_depth -= 1
        for child in node.orelse:
            self.visit(child)
        self.cond_depth -= 1

    def visit_Try(self, node: ast.Try) -> None:
        self.cond_depth += 1
        self.generic_visit(node)
        self.cond_depth -= 1

    def _assign_owner(self, fallback: str = "dynamic") -> str:
        """The ID a dynamic value belongs to: the name it is assigned to."""
        if self._assign_name:
            return self.r._id_for(self.module, f"{self.prefix}{self._assign_name}")
        return f"{self.owner}::{fallback}"

    def visit_Assign(self, node: ast.Assign) -> None:
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        outer_name = self._assign_name
        self._assign_name = names[0] if names else ""
        self.visit(node.value)
        binding = self._eval(node.value)
        self._assign_name = outer_name
        for target in node.targets:
            self._assign_target(target, binding, node)
        if self.scope.kind == "module":
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self._registry_literal(target.id, node.value, node)

    def _registry_literal(self, name: str, value: ast.expr, node: ast.AST) -> None:
        """``HANDLERS = {"buy": handle_buy}`` -- membership is literal.

        PROBABLE rather than RESOLVED: the dict is a registry only because it
        holds callables, which is a reading of the code, not a declaration in
        it, and the dict can be mutated later.
        """
        reg = self.s.registries.get(name)
        if reg is None:
            return
        if not isinstance(value, (ast.Dict, ast.List, ast.Tuple, ast.Set)):
            return
        for member, method in self.r.registry_members(reg.element_id):
            if method is not Method.REGISTRY_MEMBERSHIP:
                continue
            self._emit(
                EdgeKind.REGISTERS,
                member,
                Method.REGISTRY_MEMBERSHIP,
                Confidence.PROBABLE,
                node,
                note=f"literal member of {name}",
                source_id=reg.element_id,
            )

    def _assign_target(self, target: ast.expr, binding: _Binding, node: ast.AST) -> None:
        if isinstance(target, ast.Name):
            if binding.kind in (_BKind.UNKNOWN, _BKind.BUILTIN) and self.scope.kind == "module":
                # A container literal evaluates to nothing on its own, but the
                # name still denotes the registry the summariser found. The
                # builtin type rides along, so `HANDLERS.items()` is still a
                # dict method while `HANDLERS[key]` is still a dispatch.
                registry = self.r.registry_binding(self.module, target.id)
                if registry is not None:
                    binding = registry
            self._bind(target.id, binding)
            if self.scope.kind == "module" and binding.kind in (_BKind.CALLABLE, _BKind.CLASS):
                alias_id = self.r._id_for(self.module, target.id, 1)
                if binding.target_id and alias_id != binding.target_id:
                    self.r._emit(
                        EdgeKind.REFERENCES,
                        alias_id,
                        binding.target_id,
                        Method.SCOPE_LOOKUP,
                        Confidence.RESOLVED,
                        call_site=_span(self.path, node),
                        note="module-level alias",
                    )
        elif isinstance(target, ast.Subscript):
            self._registry_write(target.value, target.slice, node)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._assign_target(item, _UNKNOWN_BINDING, node)

    def _registry_write(self, container: ast.expr, key_node: ast.AST, node: ast.AST) -> None:
        binding = self._eval(container)
        if binding.kind is not _BKind.REGISTRY or binding.registry_key is None:
            return
        value = node.value if isinstance(node, ast.Assign) else None
        if value is None:
            return
        member = self._eval(value)
        if member.target_id and member.kind in (_BKind.CALLABLE, _BKind.CLASS):
            self._emit(
                EdgeKind.REGISTERS,
                member.target_id,
                Method.REGISTRY_MEMBERSHIP,
                Confidence.PROBABLE,
                node,
                note="written into the registry",
                source_id=binding.target_id,
            )
            if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                self.r.register_key(key_node.value, member.target_id)

    # -- expressions ----------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        for arg in node.args:
            self.visit(arg)
        for kw in node.keywords:
            self.visit(kw.value)
        if isinstance(node.func, ast.Call):
            # ``getattr(o, "x")()`` -- the inner call's result is invoked here,
            # so the outer CALLS edge says everything; a REFERENCES edge for the
            # same target at the same site would be a duplicate claim.
            self._suppress_reference.add(id(node.func))
        self._visit_receiver(node.func)
        self._resolve_call(node)

    def _visit_receiver(self, func: ast.expr) -> None:
        cur = func
        while isinstance(cur, ast.Attribute):
            cur = cur.value
        if isinstance(cur, (ast.Call, ast.Subscript)):
            self.visit(cur)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load):
            binding = self._eval(node)
            if binding.is_property and binding.target_id:
                self._emit(
                    EdgeKind.CALLS,
                    binding.target_id,
                    Method.MRO_DISPATCH,
                    binding.confidence,
                    node,
                    note="property access invokes the getter",
                )
            elif binding.kind in (_BKind.CALLABLE, _BKind.CLASS) and binding.target_id and not binding.external:
                self._emit(
                    EdgeKind.REFERENCES,
                    binding.target_id,
                    binding.method,
                    binding.confidence,
                    node,
                    note="callable referenced without being called here",
                )
        self._visit_receiver(node)

    def visit_Name(self, node: ast.Name) -> None:
        if not isinstance(node.ctx, ast.Load):
            return
        binding = self._lookup(node.id)
        if binding.kind in (_BKind.CALLABLE, _BKind.CLASS) and binding.target_id and not binding.external:
            self._emit(
                EdgeKind.REFERENCES,
                binding.target_id,
                binding.method,
                binding.confidence,
                node,
                note="callable referenced without being called here",
            )

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        binding = self._eval(node.iter)
        if isinstance(node.target, ast.Name):
            self._bind(node.target.id, binding)
        for child in node.body:
            self.visit(child)
        for child in node.orelse:
            self.visit(child)

    # -- call resolution -------------------------------------------------

    def _resolve_call(self, node: ast.Call) -> None:
        func = node.func
        dotted = _dotted(func)
        tail = dotted.split(".")[-1] if dotted else ""

        if dotted and self._is_builtin(dotted):
            if tail in _CODE_BUILTINS:
                self._unresolved(
                    node,
                    UnresolvedReason.DYNAMIC_NAME,
                    f"{tail}() builds code at runtime; its target cannot be named statically",
                    (Method.AST_DIRECT,),
                    site=f"{tail}@runtime-code",
                )
                return
            if tail == "getattr":
                self._getattr_call(node)
                return
            if tail == "setattr":
                self._setattr_call(node)
                return
            if tail in {"hasattr", "delattr"}:
                return
            if tail == "__import__":
                self._import_module_call(node, arg_index=0)
                return
            self.r._stats["builtin_calls"] += 1
            if self.r.include_builtin_calls:
                self._emit(
                    EdgeKind.CALLS,
                    make_id("builtins", tail),
                    Method.SCOPE_LOOKUP,
                    Confidence.RESOLVED,
                    node,
                    note="builtin",
                )
            return

        if dotted.endswith("importlib.import_module") or tail == "import_module":
            binding = self._resolve_dotted(dotted) if dotted else _UNKNOWN_BINDING
            if binding.module_name in {"importlib"} or dotted.startswith("importlib."):
                self._import_module_call(node, arg_index=0)
                return
            if tail == "import_module" and binding.kind is _BKind.UNKNOWN:
                self._import_module_call(node, arg_index=0)
                return

        if tail in {"iter_modules", "walk_packages"} and (
            "pkgutil" in dotted or tail in {"iter_modules", "walk_packages"}
        ):
            self._pkgutil_call(node, dotted)
            return
        if tail in {"entry_points", "iter_entry_points", "load_entry_point"}:
            self._entry_points_call(node, dotted)
            return

        accessor = self._registry_accessor(node)
        if accessor is not None:
            return

        if isinstance(func, ast.Call):
            inner = self._eval(func)
            self._call_binding(inner, node, _text(func))
            return
        if isinstance(func, ast.Subscript):
            self._subscript_call(node, func)
            return

        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Call):
            base = func.value
            if isinstance(base.func, ast.Name) and base.func.id == "super":
                self._super_call(node, func.attr)
                return

        if not dotted:
            self._unresolved(
                node,
                UnresolvedReason.DYNAMIC_NAME,
                f"call target {_text(func)!r} is a computed expression",
                (Method.SCOPE_LOOKUP,),
                site=f"call@{_text(func)}",
            )
            return

        if isinstance(func, ast.Attribute):
            receiver = self._eval(func.value)
            if receiver.builtin_type and func.attr in _BUILTIN_METHODS.get(
                receiver.builtin_type, frozenset()
            ):
                # ``problems.append(...)`` on a local list is a builtin call,
                # not a name this analysis failed on. Resolved, so it does not
                # belong in the unresolved stream; counted, so it is not silent.
                self.r._stats["builtin_calls"] += 1
                if self.r.include_builtin_calls:
                    self._emit(
                        EdgeKind.CALLS,
                        make_id("builtins", f"{receiver.builtin_type}.{func.attr}"),
                        Method.SCOPE_LOOKUP,
                        Confidence.RESOLVED,
                        node,
                        note=f"builtin {receiver.builtin_type} method",
                    )
                return

        binding = self._resolve_dotted(dotted)
        self._call_binding(binding, node, dotted)

    def _is_builtin(self, dotted: str) -> bool:
        if "." in dotted:
            return False
        return dotted in BUILTIN_NAMES and self._lookup(dotted).kind is _BKind.UNKNOWN

    #: Methods that describe a real inference step and so survive onto the
    #: call edge. Anything else means the name was simply in scope, which is
    #: SCOPE_LOOKUP however the binding got there.
    _DISPATCH_METHODS = frozenset(
        {
            Method.MRO_DISPATCH,
            Method.GETATTR_LITERAL,
            Method.GETATTR_TRACED,
            Method.REGISTRY_MEMBERSHIP,
            Method.DECORATOR_REGISTRATION,
            Method.DECORATOR_UNWRAP,
            Method.IMPORTLIB_LITERAL,
            Method.DATAFLOW,
            Method.REEXPORT,
        }
    )

    def _dispatch_method(self, binding: _Binding) -> Method:
        return binding.method if binding.method in self._DISPATCH_METHODS else Method.SCOPE_LOOKUP

    def _call_binding(self, binding: _Binding, node: ast.Call, label: str) -> None:
        if binding.external:
            # Resolved, but to something no file in the tree backs. The import
            # statement already carries the THIRD_PARTY record; counting it
            # here keeps the omission visible without duplicating that record.
            self.r._stats["third_party_calls"] += 1
            return
        if binding.kind is _BKind.CLASS and binding.target_id:
            self._emit(
                EdgeKind.INSTANTIATES,
                binding.target_id,
                Method.REEXPORT if binding.method is Method.REEXPORT else Method.SCOPE_LOOKUP,
                Confidence.RESOLVED,
                node,
                note=binding.note,
            )
            return
        if binding.kind is _BKind.CALLABLE and binding.target_id:
            confidence = binding.confidence
            if confidence is Confidence.CERTAIN:
                confidence = Confidence.RESOLVED
            self._emit(
                EdgeKind.CALLS,
                binding.target_id,
                self._dispatch_method(binding),
                confidence,
                node,
                note=binding.note,
            )
            self._ambiguity_note(binding, node)
            if binding.method is not Method.DECORATOR_UNWRAP:
                # `fn(...)` inside a wrapper calls the *undecorated* function:
                # the decorator has already been applied at that point, so
                # adding the wrapper here would make the wrapper call itself.
                self._wrapper_edges(binding.target_id, node)
            return
        if binding.kind is _BKind.CANDIDATES and binding.candidates:
            self._unresolved(
                node,
                UnresolvedReason.AMBIGUOUS,
                f"call target {label!r} has more than one possible binding; no edge claimed",
                (Method.SCOPE_LOOKUP, Method.DATAFLOW),
                candidates=binding.candidates,
                candidate_confidence=Confidence.PROBABLE,
                site=f"call@{label}",
            )
            return
        if binding.kind is _BKind.REGISTRY and binding.registry_key is not None:
            self._registry_call(binding, node, label)
            return
        if binding.kind is _BKind.MODULE:
            self._unresolved(
                node,
                UnresolvedReason.DYNAMIC_NAME,
                f"call target {label!r} resolves to a module, not a callable",
                (Method.SCOPE_LOOKUP,),
                site=f"call@{label}",
            )
            return
        self._unresolved_call(node, label)

    def _wrapper_edges(self, target_id: str, node: ast.Call) -> None:
        """A call to a decorated name reaches the wrapper too.

        After ``@trace``, the name ``compute`` is bound to ``trace``'s inner
        function: the wrapper is what actually runs and the wrapped function
        is the element the owner reasons about. Dropping either edge loses one
        of those two truths, so both are emitted -- the wrapper at PROBABLE,
        because whether the decorator really wraps is an inference.
        """
        for decorator_id in self.r.decorators_of(target_id):
            wrapper = self.r.wrapper_of(decorator_id)
            if wrapper and wrapper != target_id:
                self._emit(
                    EdgeKind.CALLS,
                    wrapper,
                    Method.DECORATOR_UNWRAP,
                    Confidence.PROBABLE,
                    node,
                    note="the wrapper: what the name is actually bound to after decoration",
                )

    def _ambiguity_note(self, binding: _Binding, node: ast.Call) -> None:
        """Record subclass overrides that could take this dispatch instead."""
        if binding.method is not Method.MRO_DISPATCH or binding.class_key is None:
            return
        element = self.r._elements.get(binding.target_id)
        attr = element.name if element is not None else ""
        if not attr:
            return
        overrides = [
            o for o in self.r._overriding_subclasses(binding.class_key, attr) if o != binding.target_id
        ]
        if overrides:
            self._unresolved(
                node,
                UnresolvedReason.AMBIGUOUS,
                (
                    f"dispatch of {attr!r} was resolved through the declared MRO, but "
                    "subclasses override it; the edge is PROBABLE and these are the "
                    "other possible receivers"
                ),
                (Method.MRO_DISPATCH,),
                candidates=[binding.target_id, *overrides],
                candidate_confidence=Confidence.PROBABLE,
                site=f"dispatch@{attr}",
            )

    def _unresolved_call(self, node: ast.Call, label: str) -> None:
        bare = label.split(".")[-1]
        kinds = (ElementKind.FUNCTION, ElementKind.METHOD, ElementKind.CLASS)
        candidates = self._name_candidates(bare, kinds)
        attempted: tuple[Method, ...]
        if not label.isidentifier():
            # an attribute chain or a computed expression: not a plain name
            record_id = f"{self.owner}::call@{label}"
            description = f"call target {label!r} could not be bound to an element"
            attempted = (Method.SCOPE_LOOKUP, Method.MRO_DISPATCH)
        else:
            # a bare name nothing in scope binds: the record belongs to the
            # name itself, so a reader lands on the name rather than on a
            # position inside a function.
            record_id = make_id(self.module, bare)
            attempted = (Method.SCOPE_LOOKUP, Method.IMPORT_ABSOLUTE)
            description = f"`{bare}` is used but nothing binds it here"
            if self.s.stars:
                sources = ", ".join(
                    sorted(
                        {
                            spec.module or "." * spec.level
                            for spec in self.s.stars
                        }
                    )
                )
                attempted = (*attempted, Method.IMPORT_STAR)
                description += f"; the star import from {sources} does not export it"
        self.r._record_unresolved(
            owner=record_id,
            reason=UnresolvedReason.MISSING_TARGET if candidates else UnresolvedReason.DYNAMIC_NAME,
            span=_span(self.path, node),
            description=description,
            attempted=attempted,
            candidates=candidates,
            candidate_confidence=Confidence.HEURISTIC if candidates else Confidence.UNKNOWN,
        )

    def _name_candidates(
        self, name: str, kinds: tuple[ElementKind, ...]
    ) -> list[str]:
        ids = self.r._by_name.get(name, [])
        if not kinds:
            return sorted(set(ids))
        return sorted(
            {i for i in ids if self.r._elements[i].kind in kinds}
        )

    # -- dynamic wiring --------------------------------------------------

    def _getattr_call(self, node: ast.Call) -> _Binding:
        """Resolve ``getattr(obj, name)``.

        Emits a REFERENCES edge for what the call *names*; if the result is
        immediately invoked, the enclosing call emits the CALLS edge from the
        binding returned here. Memoised on node identity, because a getattr is
        walked once as a statement and once as the value of its assignment.
        """
        cached = self._dynamic_cache.get(id(node))
        if cached is not None:
            return cached
        result = self._getattr_uncached(node)
        self._dynamic_cache[id(node)] = result
        return result

    def _getattr_uncached(self, node: ast.Call) -> _Binding:
        if len(node.args) < 2:
            return _UNKNOWN_BINDING
        emit = id(node) not in self._suppress_reference
        receiver = self._eval(node.args[0])
        name_val = self._eval_str(node.args[1])
        traced = not (
            isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)
        )
        method = Method.GETATTR_TRACED if traced else Method.GETATTR_LITERAL

        if receiver.kind is _BKind.MODULE and name_val.closed and len(name_val.literals) == 1:
            binding = self.r._attr_binding(receiver, name_val.literals[0], self.module)
            if binding.target_id:
                if emit:
                    self._emit(
                        EdgeKind.REFERENCES,
                        binding.target_id,
                        method,
                        Confidence.PROBABLE,
                        node,
                        note=f"getattr({receiver.module_name}, {name_val.literals[0]!r})",
                    )
                return _Binding(
                    kind=binding.kind,
                    target_id=binding.target_id,
                    class_key=binding.class_key,
                    module_name=binding.module_name,
                    method=method,
                    confidence=Confidence.PROBABLE,
                )
        members: dict[str, tuple[str, tuple[str, str], bool]] = {}
        if receiver.class_key is not None:
            members = self.r._members(receiver.class_key)
        elif receiver.kind is _BKind.MODULE and receiver.module_name in self.r._modules:
            source = self.r._modules[receiver.module_name]
            members = {
                n: (self.r._id_for(receiver.module_name, q), (receiver.module_name, q), False)
                for n, q in sorted(source.top_defs.items())
            }

        if name_val.closed and members:
            targets = [members[n][0] for n in sorted(name_val.literals) if n in members]
            missing = [n for n in sorted(name_val.literals) if n not in members]
            if targets and not missing and len(targets) <= self.r.max_traced_targets:
                for target in targets:
                    if emit:
                        self._emit(
                            EdgeKind.REFERENCES,
                            target,
                            method,
                            Confidence.PROBABLE,
                            node,
                            note=f"getattr name traced to {sorted(name_val.literals)}",
                        )
                if len(targets) == 1:
                    return _Binding(
                        kind=_BKind.CALLABLE,
                        target_id=targets[0],
                        method=method,
                        confidence=Confidence.PROBABLE,
                    )
                return _Binding(
                    kind=_BKind.CANDIDATES,
                    candidates=tuple(sorted(targets)),
                    method=method,
                    confidence=Confidence.UNKNOWN,
                )

        candidates = [
            members[n][0] for n in sorted(members) if name_val.matches(n) or not name_val.constrained
        ]
        candidates = [c for c in candidates if not self._is_dunder(c)]
        self._unresolved(
            node,
            UnresolvedReason.DYNAMIC_NAME,
            (
                f"getattr attribute name is `{_text(node.args[1])}`, which this "
                "analysis could not pin to one attribute"
                + (
                    "; the members matching what is known of the name are listed "
                    "as candidates and none is claimed"
                    if candidates
                    else "; no candidate could be narrowed down"
                )
            ),
            (Method.GETATTR_LITERAL, Method.GETATTR_TRACED),
            candidates=candidates,
            # UNKNOWN even with candidates: the set is right, the choice within
            # it is not being made, and a confidence here would imply one.
            candidate_confidence=Confidence.UNKNOWN,
            site=f"getattr@{_text(node.args[1])}",
        )
        return _Binding(
            kind=_BKind.CANDIDATES,
            candidates=tuple(sorted(candidates)),
            method=method,
            confidence=Confidence.UNKNOWN,
            note="unresolved getattr",
        )

    def _is_dunder(self, element_id: str) -> bool:
        element = self.r._elements.get(element_id)
        name = element.name if element is not None else element_id.rsplit("::", 1)[-1]
        return name.startswith("__") and name.endswith("__")

    def _setattr_call(self, node: ast.Call) -> None:
        if len(node.args) < 3:
            return
        receiver = self._eval(node.args[0])
        name_val = self._eval_str(node.args[1])
        value = self._eval(node.args[2])
        if not value.target_id or value.kind not in (_BKind.CALLABLE, _BKind.CLASS):
            return
        container = receiver.target_id
        if not container:
            self._unresolved(
                node,
                UnresolvedReason.DYNAMIC_NAME,
                "setattr onto a receiver this analysis could not name",
                (Method.GETATTR_LITERAL, Method.DATAFLOW),
                candidates=[value.target_id],
                candidate_confidence=Confidence.HEURISTIC,
                site="setattr@unknown-receiver",
            )
            return
        if name_val.closed and len(name_val.literals) == 1:
            self._emit(
                EdgeKind.REGISTERS,
                value.target_id,
                Method.GETATTR_LITERAL,
                Confidence.PROBABLE,
                node,
                note=f"setattr binds it as {name_val.literals[0]!r}",
                source_id=container,
            )
            self.r.register_key(name_val.literals[0], value.target_id)
        else:
            self._unresolved(
                node,
                UnresolvedReason.DYNAMIC_NAME,
                "setattr with a computed attribute name; the binding name is unknown",
                (Method.GETATTR_LITERAL, Method.GETATTR_TRACED),
                candidates=[value.target_id],
                candidate_confidence=Confidence.PROBABLE,
                site="setattr@computed-name",
            )

    def _import_module_call(self, node: ast.Call, arg_index: int) -> _Binding:
        cached = self._dynamic_cache.get(id(node))
        if cached is not None:
            return cached
        result = self._import_module_uncached(node, arg_index)
        self._dynamic_cache[id(node)] = result
        return result

    def _import_module_uncached(self, node: ast.Call, arg_index: int) -> _Binding:
        if len(node.args) <= arg_index:
            return _UNKNOWN_BINDING
        name_val = self._eval_str(node.args[arg_index])
        package = ""
        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
            value = node.args[1].value
            if isinstance(value, str):
                package = value
        for kw in node.keywords:
            if kw.arg == "package" and isinstance(kw.value, ast.Constant):
                if isinstance(kw.value.value, str):
                    package = kw.value.value
        if name_val.closed and len(name_val.literals) == 1:
            literal = name_val.literals[0]
            method = Method.IMPORTLIB_LITERAL
            if literal.startswith("."):
                base = package or self.module
                level = len(literal) - len(literal.lstrip("."))
                target_module = _relative_module(
                    base, True, level, literal.lstrip(".")
                )
                method = Method.IMPORT_RELATIVE
            else:
                target_module = literal
            known = target_module in self.r._modules
            target_id = (
                self.r._modules[target_module].element_id if known else make_id(target_module)
            )
            if not known:
                self.r._record_unresolved(
                    owner=f"{self._assign_owner()}",
                    reason=UnresolvedReason.THIRD_PARTY,
                    span=_span(self.path, node),
                    description=(
                        f"import_module({literal!r}) names a module outside the "
                        "target tree; no element to point at"
                    ),
                    attempted=(Method.IMPORTLIB_LITERAL,),
                )
                return _Binding(
                    kind=_BKind.MODULE,
                    module_name=target_module,
                    method=method,
                    confidence=Confidence.UNKNOWN,
                    external=True,
                )
            self._emit(
                EdgeKind.IMPORTS,
                target_id,
                method,
                # PROBABLE, not RESOLVED: the string is a literal but the
                # import happens at runtime and nothing static guarantees it
                # is reached.
                Confidence.PROBABLE,
                node,
                note="literal dynamic import",
            )
            return _Binding(
                kind=_BKind.MODULE,
                module_name=target_module,
                target_id=target_id,
                method=method,
                confidence=Confidence.RESOLVED,
                external=not known,
            )
        candidates = [
            self.r._modules[m].element_id
            for m in sorted(self.r._modules)
            if name_val.matches(m) or name_val.matches(m.rsplit(".", 1)[-1])
        ]
        # A literal default buried in the expression -- os.environ.get("X",
        # "pkg.plugin") -- names a real module; offer it, claim nothing.
        for literal in _string_constants(node):
            if literal in self.r._modules:
                candidates.append(self.r._modules[literal].element_id)
        self.r._record_unresolved(
            owner=self._assign_owner("import_module"),
            reason=UnresolvedReason.DYNAMIC_NAME,
            span=_span(self.path, node),
            description=(
                "import_module's argument is not a traceable literal; any string "
                "literal in the expression is offered as a candidate only"
            ),
            attempted=(Method.IMPORTLIB_LITERAL,),
            candidates=candidates,
            candidate_confidence=Confidence.HEURISTIC if candidates else Confidence.UNKNOWN,
        )
        return _Binding(
            kind=_BKind.CANDIDATES,
            candidates=tuple(sorted(candidates)),
            method=Method.IMPORTLIB_LITERAL,
            confidence=Confidence.UNKNOWN,
        )

    def _pkgutil_call(self, node: ast.Call, dotted: str) -> None:
        package = ""
        if node.args:
            arg = node.args[0]
            text = _text(arg)
            if text.endswith(".__path__"):
                package = text[: -len(".__path__")]
                binding = self._resolve_dotted(package)
                package = binding.module_name or package
            else:
                val = self._eval_str(arg)
                if val.closed and len(val.literals) == 1:
                    package = val.literals[0]
        candidates = [
            self.r._modules[m].element_id
            for m in sorted(self.r._modules)
            if package and m.startswith(f"{package}.")
        ]
        self._unresolved(
            node,
            UnresolvedReason.DYNAMIC_NAME,
            (
                f"{dotted}() discovers modules at runtime"
                + (f" under package {package!r}" if package else "")
                + "; the set actually imported cannot be fixed statically"
            ),
            (Method.IMPORTLIB_LITERAL, Method.NAME_HEURISTIC),
            candidates=candidates,
            candidate_confidence=Confidence.HEURISTIC if candidates else Confidence.UNKNOWN,
            site=f"discovery@{dotted}",
        )

    def _entry_points_call(self, node: ast.Call, dotted: str) -> None:
        group = ""
        for kw in node.keywords:
            if kw.arg == "group" and isinstance(kw.value, ast.Constant):
                if isinstance(kw.value.value, str):
                    group = kw.value.value
        if not group and node.args:
            val = self._eval_str(node.args[0])
            if val.closed and len(val.literals) == 1:
                group = val.literals[0]
        self._unresolved(
            node,
            UnresolvedReason.DYNAMIC_NAME,
            (
                f"{dotted}() loads components from installed distribution metadata"
                + (f" in group {group!r}" if group else "")
                + "; the targets are outside the source tree"
            ),
            (Method.REGISTRY_MEMBERSHIP, Method.CONFIG_STRING_MATCH),
            site=f"entry_points@{group or dotted}",
        )

    def _registry_accessor(self, node: ast.Call) -> _Binding | None:
        """``REGISTRY.get("a")`` -- a lookup that *returns* a member.

        Returns ``None`` when the call is not a registry lookup, so the caller
        falls through to ordinary resolution. Memoised on node identity so the
        REFERENCES edge is written once.
        """
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in {"get", "pop", "setdefault"}:
            return None
        if not node.args:
            return None
        base = self._eval(func.value)
        if base.kind is not _BKind.REGISTRY or base.registry_key is None:
            return None
        cached = self._dynamic_cache.get(id(node))
        if cached is not None:
            return cached
        module, name = base.registry_key
        reg = self.r._modules[module].registries.get(name)
        key_val = self._eval_str(node.args[0])
        result: _Binding = _UNKNOWN_BINDING
        if reg is not None and key_val.closed and len(key_val.literals) == 1:
            literal = key_val.literals[0]
            text = reg.entries.get(literal, "")
            member = self.r._binding_for_dotted(text, module) if text else _UNKNOWN_BINDING
            if member.target_id and member.kind in (_BKind.CALLABLE, _BKind.CLASS):
                if id(node) not in self._suppress_reference:
                    self._emit(
                        EdgeKind.REFERENCES,
                        member.target_id,
                        Method.REGISTRY_MEMBERSHIP,
                        Confidence.PROBABLE,
                        node,
                        note=f"{name}.{func.attr}({literal!r})",
                    )
                result = _Binding(
                    kind=member.kind,
                    target_id=member.target_id,
                    class_key=member.class_key,
                    method=Method.REGISTRY_MEMBERSHIP,
                    confidence=Confidence.PROBABLE,
                )
        if result.kind is _BKind.UNKNOWN:
            members = self._registry_member_ids(base.target_id)
            self._unresolved(
                node,
                UnresolvedReason.AMBIGUOUS,
                (
                    f"{name}.{func.attr}(...) with a key that is not a traceable "
                    "literal; no edge claimed"
                ),
                (Method.REGISTRY_MEMBERSHIP, Method.DATAFLOW),
                candidates=members,
                candidate_confidence=Confidence.PROBABLE if members else Confidence.UNKNOWN,
                site=f"registry@{name}.{func.attr}",
            )
            result = _Binding(
                kind=_BKind.CANDIDATES,
                candidates=tuple(members),
                method=Method.REGISTRY_MEMBERSHIP,
                confidence=Confidence.UNKNOWN,
            )
        self._dynamic_cache[id(node)] = result
        return result

    def _registry_call(self, binding: _Binding, node: ast.Call, label: str) -> None:
        assert binding.registry_key is not None
        self._registry_dispatch(binding.target_id, node, label)

    def _registry_dispatch(self, container_id: str, node: ast.Call, label: str) -> None:
        """A lookup whose key is decided at runtime reaches every member.

        One edge per member at HEURISTIC, rather than one unresolved record:
        the *set* of possible targets is known exactly, only the choice within
        it is not, and a cascade that stops at the registry hides the whole
        downstream half of the engine. HEURISTIC is the honesty: at most one
        of these runs on any given call.
        """
        members = self.r.registry_members(container_id)
        if not members:
            self._unresolved(
                node,
                UnresolvedReason.AMBIGUOUS,
                f"lookup through {label!r}, whose members this analysis could not read",
                (Method.REGISTRY_MEMBERSHIP,),
                site=f"registry@{label}",
            )
            return
        for member, method in members:
            self._emit(
                EdgeKind.CALLS,
                member,
                method,
                Confidence.HEURISTIC,
                node,
                note=f"one of {len(members)} members of the registry; the key is runtime data",
            )

    def _registry_member_ids(self, container_id: str) -> list[str]:
        return [member for member, _ in self.r.registry_members(container_id)]

    def _subscript_call(self, node: ast.Call, func: ast.Subscript) -> None:
        binding = self._eval(func.value)
        if binding.kind is not _BKind.REGISTRY or binding.registry_key is None:
            self._unresolved_call(node, _text(func))
            return
        module, name = binding.registry_key
        reg = self.r._modules[module].registries.get(name)
        key_val = self._eval_str(func.slice) if isinstance(func.slice, ast.expr) else _OPEN_STR
        if reg is not None and key_val.closed and len(key_val.literals) == 1:
            literal = key_val.literals[0]
            text = reg.entries.get(literal, "")
            member = self.r._binding_for_dotted(text, module) if text else _UNKNOWN_BINDING
            if member.target_id and member.kind in (_BKind.CALLABLE, _BKind.CLASS):
                kind = EdgeKind.INSTANTIATES if member.kind is _BKind.CLASS else EdgeKind.CALLS
                self._emit(
                    kind,
                    member.target_id,
                    Method.REGISTRY_MEMBERSHIP,
                    Confidence.PROBABLE,
                    node,
                    note=f"registry {name}[{literal!r}]",
                )
                return
        self._registry_dispatch(binding.target_id, node, name)

    def _super_call(self, node: ast.Call, attr: str) -> None:
        if not self.class_stack:
            self._unresolved(
                node,
                UnresolvedReason.DYNAMIC_NAME,
                "super() outside a class body",
                (Method.MRO_DISPATCH,),
                site=f"super@{attr}",
            )
            return
        key = self.class_stack[-1]
        order, complete = self.r._mro(key)
        target: str | None = None
        owner: tuple[str, str] | None = None
        for cls_key in order[1:]:
            found = self.r._lookup_member(cls_key, attr)
            if found is not None:
                target, owner, _ = found
                break
        if target is None:
            self._unresolved(
                node,
                UnresolvedReason.MISSING_TARGET if complete else UnresolvedReason.AMBIGUOUS,
                (
                    f"super().{attr}() -- no ancestor of {key[1]} in the inventory "
                    "defines it" + ("" if complete else "; an unknown base is in the MRO")
                ),
                (Method.MRO_DISPATCH,),
                candidates=self._name_candidates(attr, (ElementKind.METHOD,)),
                candidate_confidence=Confidence.HEURISTIC,
                site=f"super@{attr}",
            )
            return
        single = complete and self._single_inheritance(key)
        self._emit(
            EdgeKind.CALLS,
            target,
            Method.MRO_DISPATCH,
            # PROBABLE even with a complete, linear MRO: super() is resolved
            # against type(self) at runtime, and a subclass loaded elsewhere
            # can sit between these two classes.
            Confidence.PROBABLE,
            node,
            note=f"super() from {key[1]} reaches {owner[1] if owner else '?'}",
        )
        if not single:
            self._unresolved(
                node,
                UnresolvedReason.AMBIGUOUS,
                (
                    f"super().{attr}() was resolved through the declared MRO of "
                    f"{key[1]}, but multiple inheritance or an unknown base can "
                    "reorder it at runtime"
                ),
                (Method.MRO_DISPATCH,),
                candidates=[target],
                candidate_confidence=Confidence.PROBABLE,
                site=f"super@{attr}",
            )

    def _single_inheritance(self, key: tuple[str, str]) -> bool:
        order, complete = self.r._mro(key)
        if not complete:
            return False
        for cls_key in order:
            summary = self.r._modules.get(cls_key[0])
            info = summary.classes.get(cls_key[1]) if summary else None
            if info is None:
                return False
            real = [
                b
                for b in info.bases
                if _strip_subscript(b).strip() not in {"object", "ABC", "abc.ABC", "Protocol", "typing.Protocol"}
            ]
            if len(real) > 1:
                return False
        for sub in self.r._subclasses.get(key, ()):
            summary = self.r._modules.get(sub[0])
            info = summary.classes.get(sub[1]) if summary else None
            if info is not None and len(info.bases) > 1:
                return False
        return True

    # -- expression evaluation -------------------------------------------

    def _resolve_dotted(self, dotted: str) -> _Binding:
        parts = dotted.split(".")
        binding = self._lookup(parts[0])
        if binding.kind is _BKind.UNKNOWN and len(parts) == 1:
            return binding
        for part in parts[1:]:
            binding = self.r._attr_binding(binding, part, self.module)
        return binding

    def _eval(self, node: ast.expr) -> _Binding:
        if isinstance(node, ast.Name):
            return self._lookup(node.id)
        if isinstance(node, ast.Attribute):
            dotted = _dotted(node)
            if dotted:
                return self._resolve_dotted(dotted)
            base = self._eval(node.value)
            return self.r._attr_binding(base, node.attr, self.module)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return _Binding(
                kind=_BKind.CONST,
                const=_StrVal(literals=(node.value,), open=False),
                method=Method.AST_DIRECT,
                confidence=Confidence.CERTAIN,
                builtin_type="str",
            )
        literal_type = _literal_builtin_type(node)
        if literal_type:
            return _Binding(
                kind=_BKind.BUILTIN,
                method=Method.AST_DIRECT,
                confidence=Confidence.CERTAIN,
                builtin_type=literal_type,
                note=f"builtin {literal_type}",
            )
        if isinstance(node, ast.Call):
            func = node.func
            dotted = _dotted(func)
            tail = dotted.split(".")[-1] if dotted else ""
            if dotted in _BUILTIN_TYPES and self._is_builtin(dotted):
                return _Binding(
                    kind=_BKind.BUILTIN,
                    method=Method.SCOPE_LOOKUP,
                    confidence=Confidence.RESOLVED,
                    builtin_type=dotted,
                    note=f"builtin {dotted}",
                )
            if tail == "getattr" and self._is_builtin(dotted):
                return self._getattr_call(node)
            if tail in {"import_module", "__import__"}:
                return self._import_module_call(node, arg_index=0)
            accessor = self._registry_accessor(node)
            if accessor is not None:
                return accessor
            inner = self._resolve_dotted(dotted) if dotted else self._eval(func)
            if inner.kind is _BKind.CLASS and inner.class_key is not None:
                return _Binding(
                    kind=_BKind.INSTANCE,
                    class_key=inner.class_key,
                    target_id=inner.target_id,
                    method=Method.DATAFLOW,
                    confidence=Confidence.RESOLVED,
                )
            if inner.kind is _BKind.CALLABLE and inner.target_id:
                return self._return_binding(inner)
            return _UNKNOWN_BINDING
        if isinstance(node, ast.IfExp):
            left = self._eval(node.body)
            right = self._eval(node.orelse)
            merged = _merge_str(self._eval_str(node.body), self._eval_str(node.orelse))
            if merged.constrained:
                # One branch a literal and the other unknown means the value is
                # *constrained*, never *known*. Collapsing to the literal branch
                # would turn a coin flip into a fact.
                return _Binding(
                    kind=_BKind.CONST,
                    const=merged,
                    method=Method.DATAFLOW,
                    confidence=Confidence.PROBABLE,
                )
            ids = sorted({b.target_id for b in (left, right) if b.target_id})
            if len(ids) > 1:
                return _Binding(
                    kind=_BKind.CANDIDATES,
                    candidates=tuple(ids),
                    method=Method.DATAFLOW,
                    confidence=Confidence.UNKNOWN,
                )
            return left if left.kind is not _BKind.UNKNOWN else right
        if isinstance(node, (ast.BinOp, ast.JoinedStr)):
            value = self._eval_str(node)
            if value.constrained:
                return _Binding(
                    kind=_BKind.CONST,
                    const=value,
                    method=Method.DATAFLOW,
                    confidence=Confidence.PROBABLE,
                )
        if isinstance(node, ast.Await):
            return self._eval(node.value)
        if isinstance(node, ast.Subscript):
            base = self._eval(node.value)
            if base.kind is _BKind.REGISTRY and base.registry_key is not None:
                module, name = base.registry_key
                reg = self.r._modules[module].registries.get(name)
                key_val = self._eval_str(node.slice) if isinstance(node.slice, ast.expr) else _OPEN_STR
                if reg is not None and key_val.closed and len(key_val.literals) == 1:
                    text = reg.entries.get(key_val.literals[0], "")
                    if text:
                        return self.r._binding_for_dotted(text, module)
        return _UNKNOWN_BINDING

    def _return_binding(self, callee: _Binding) -> _Binding:
        """Type of a factory function's result: annotation first, body second."""
        for module in sorted(self.r._modules):
            summary = self.r._modules[module]
            for qualname in sorted(summary.funcs):
                func = summary.funcs[qualname]
                if func.element_id != callee.target_id:
                    continue
                if func.returns:
                    key = self.r._class_key_from_text(func.returns, module)
                    if key is not None:
                        return _Binding(
                            kind=_BKind.INSTANCE,
                            class_key=key,
                            target_id=self.r._class_element(key),
                            method=Method.DATAFLOW,
                            confidence=Confidence.PROBABLE,
                            note="from return annotation",
                        )
                keys = set()
                for text in func.return_exprs:
                    binding = self.r._binding_from_assign_text(module, text)
                    if binding.kind is _BKind.INSTANCE and binding.class_key is not None:
                        keys.add(binding.class_key)
                if len(keys) == 1:
                    key = keys.pop()
                    return _Binding(
                        kind=_BKind.INSTANCE,
                        class_key=key,
                        target_id=self.r._class_element(key),
                        method=Method.DATAFLOW,
                        confidence=Confidence.PROBABLE,
                        note="factory returns a single class",
                    )
                return _UNKNOWN_BINDING
        return _UNKNOWN_BINDING

    def _eval_str(self, node: ast.AST) -> _StrVal:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                return _StrVal(literals=(node.value,), open=False)
            return _OPEN_STR
        if isinstance(node, ast.Name):
            binding = self._lookup(node.id)
            if binding.kind is _BKind.CONST and binding.const is not None:
                return binding.const
            return _OPEN_STR
        if isinstance(node, ast.IfExp):
            return _merge_str(self._eval_str(node.body), self._eval_str(node.orelse))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._eval_str(node.left)
            right = self._eval_str(node.right)
            if left.closed and right.closed:
                return _StrVal(
                    literals=tuple(sorted({a + b for a in left.literals for b in right.literals})),
                    open=False,
                )
            if left.closed:
                return _StrVal(prefixes=left.literals, open=True)
            if right.closed:
                return _StrVal(suffixes=right.literals, open=True)
            return _OPEN_STR
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            left = self._eval_str(node.left)
            if left.closed:
                prefix = left.literals[0].split("%")[0]
                return _StrVal(prefixes=(prefix,), open=True)
            return _OPEN_STR
        if isinstance(node, ast.JoinedStr):
            literal = ""
            prefix = ""
            suffix = ""
            closed = True
            seen_hole = False
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    literal += part.value
                    if seen_hole:
                        suffix = part.value
                    else:
                        prefix += part.value
                    continue
                if isinstance(part, ast.FormattedValue):
                    inner = self._eval_str(part.value)
                    if inner.closed and len(inner.literals) == 1 and not seen_hole:
                        literal += inner.literals[0]
                        prefix += inner.literals[0]
                        continue
                    closed = False
                    seen_hole = True
                    suffix = ""
            if closed:
                return _StrVal(literals=(literal,), open=False)
            return _StrVal(
                prefixes=(prefix,) if prefix else (),
                suffixes=(suffix,) if suffix else (),
                open=True,
            )
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in {"format", "join", "lower", "upper"}:
                base = self._eval_str(func.value)
                if func.attr == "format" and base.closed:
                    template = base.literals[0]
                    return _StrVal(prefixes=(template.split("{")[0],), open=True)
                if func.attr in {"lower", "upper"} and base.closed:
                    method = str.lower if func.attr == "lower" else str.upper
                    return _StrVal(literals=tuple(sorted(method(v) for v in base.literals)), open=False)
            return _OPEN_STR
        return _OPEN_STR


# ---------------------------------------------------------------------------
# module-level convenience
# ---------------------------------------------------------------------------


def resolve(
    elements: Sequence[Element],
    root: str | Path = ".",
    *,
    config_paths: Sequence[str] = (),
    include_builtin_calls: bool = False,
) -> tuple[Sequence[Edge], Sequence[Unresolved]]:
    """Resolve *elements* rooted at *root*. See :class:`Resolver`."""
    return Resolver(
        root,
        config_paths=config_paths,
        include_builtin_calls=include_builtin_calls,
    ).resolve(elements)


def build_graph(elements: Sequence[Element], edges: Sequence[Edge]) -> Any:
    """A ``networkx.DiGraph`` over the same IDs, for cards 3-6.

    ``networkx`` is a core dependency, but this helper is the only place that
    needs it, so it is imported lazily: the resolver itself works without it.
    """
    try:
        import networkx  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - networkx is a core dep
        raise RuntimeError(
            "build_graph needs networkx; resolution itself does not"
        ) from exc
    graph = networkx.DiGraph()
    for element in sorted(elements, key=lambda e: e.id):
        graph.add_node(element.id, kind=str(element.kind), name=element.name)
    for edge in sorted(edges, key=lambda e: e.id):
        for node in (edge.source_id, edge.target_id):
            if node not in graph:
                graph.add_node(node, kind="EXTERNAL", name=node.rsplit("::", 1)[-1])
        graph.add_edge(
            edge.source_id,
            edge.target_id,
            key=edge.id,
            kind=str(edge.kind),
            method=str(edge.provenance.method),
            confidence=str(edge.provenance.confidence),
        )
    return graph


def import_cycles(edges: Sequence[Edge]) -> list[tuple[str, ...]]:
    """Import cycles, reported as cycles rather than as an error.

    Deterministic: each cycle is rotated to start at its smallest ID and the
    list is sorted.
    """
    def module_of(node_id: str) -> str:
        return node_id.split("::", 1)[0].split("#", 1)[0]

    graph: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        if edge.kind is EdgeKind.IMPORTS:
            source = module_of(edge.source_id)
            target = module_of(edge.target_id)
            if source != target:
                graph[source].add(target)
    cycles: set[tuple[str, ...]] = set()
    colour: dict[str, int] = {}
    stack: list[str] = []

    def walk(node: str) -> None:
        colour[node] = 1
        stack.append(node)
        for nxt in sorted(graph.get(node, ())):
            if colour.get(nxt, 0) == 0:
                walk(nxt)
            elif colour.get(nxt) == 1:
                index = stack.index(nxt)
                cycle = tuple(stack[index:])
                pivot = cycle.index(min(cycle))
                cycles.add(cycle[pivot:] + cycle[:pivot])
        stack.pop()
        colour[node] = 2

    for node in sorted(graph):
        if colour.get(node, 0) == 0:
            walk(node)
    return sorted(cycles)
