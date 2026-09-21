"""Card 4 -- data and feature lineage, and slicing.

Follows values along the cascade (raw input -> engineered data -> features ->
decision inputs) over the IDs cards 1 and 2 mint, and answers two queries:

* **backward slice** of a feature or decision input -- everything that produces it
* **forward slice** of any element -- everything a change to it can reach

Node identity (all through the contract's helpers)

===========================  =================================================
module-level binding         ``make_id(module, name, ordinal)``
class-level binding          ``make_id(module, "Class.name")``
function / method            ``make_id(module, qualname)``
nested function              ``local_id(parent_element_id, name)``
local variable               ``local_id(element_id, name, ordinal)``
parameter                    ``<element_id>.<param>.<name>``
dict key                     ``key_id(container_node, key)``
attribute                    ``attr_id(object_node, attr)``
instance attribute           ``attr_id(class_element_id, attr)``
named feature / column       ``feature_id(name)``
===========================  =================================================

``interfaces.py`` has no helper for a parameter node; ``_param_id`` below mints
``<element_id>.<param>.<name>``, the convention card 1 and card 8 both use. That
gap is reported, not papered over.

The decisions that make a slice worth reading

1. **A named feature is its own node.** A dataframe column, and a dict key whose
   name a config file declares, become ``feature_id(name)`` -- so a column named
   in ``features.json`` and one written in code are one node. Every other dict
   key is ``key_id(container, key)``: still its own node, still never collapsed
   into its container, but scoped to the container that holds it.
2. **A value's return node is the function's own element ID.** ``m::f`` is where
   ``f``'s returns arrive and where its callers read from; no synthetic node.
3. **Flow direction is data direction.** ``source_id`` produces, ``target_id``
   receives.
4. **A barrier is an explicit node, not a missing edge.** ``eval``-built code,
   reflection with a computed name and opaque third-party calls emit a
   ``Barrier``; flow runs *through* it at ``UNKNOWN`` confidence. Nothing is
   stitched across: a slice that crosses one lists it and drops to ``UNKNOWN``.
5. **Reaching definitions.** Each *rebinding* of a name is its own node
   (``#n`` in source order). An augmented assignment is a MUTATES edge from the
   node to itself: ``y += 1`` changes ``y``, it does not create a new ``y``.
6. **An edge's span is the statement where the flow happens** -- not where either
   endpoint was defined, which the endpoints already record.
7. **A literal written into a structure originates at the enclosing element.**
   ``self.mode = "off"`` is an ATTRIBUTE_WRITE from the method that wrote it, so
   the write is never invisible. A literal into a plain local emits nothing: the
   local's own element already says where it is.

Approximations are labelled in the edge provenance note: every edge whose note
starts with ``over-approximate:`` prefers reach to precision, and
:meth:`LineageTracer.over_approximate_edge_ids` lists them. Under-approximation
is exactly the barrier set.

Nothing here imports, executes or evaluates target code. Modules are read as
text and parsed with :mod:`ast`.
"""

from __future__ import annotations

import ast
import builtins as _builtins
import hashlib
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from .contracts.interfaces import (
    Barrier,
    Confidence,
    Edge,
    EdgeKind,
    Element,
    ElementKind,
    LineageEdge,
    LineageKind,
    Method,
    Provenance,
    Slice,
    SourceSpan,
    Unresolved,
    UnresolvedReason,
    attr_id,
    canonical_dumps,
    canonical_jsonl,
    combine,
    feature_id,
    key_id,
    local_id,
    make_id,
)

__all__ = [
    "LineageTracer",
    "BARRIER_BUILTINS",
    "TRANSPARENT_BUILTINS",
    "TRANSPARENT_MODULES",
    "FRAME_METHODS",
    "COLUMN_METHODS",
    "MUTATING_METHODS",
    "PANDAS_MODULES",
]

# --------------------------------------------------------------------------
# Auditable classification tables.
#
# These are the only places where this card assumes anything about a name it
# did not define. Each is deliberately small so the owner can read it.
# --------------------------------------------------------------------------

BUILTIN_NAMES = frozenset(dir(_builtins))

#: Builtins whose result is a function of their arguments. Flow passes through
#: them; they are not barriers.
TRANSPARENT_BUILTINS = frozenset(
    {
        "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes", "chr",
        "dict", "divmod", "enumerate", "filter", "float", "format", "frozenset",
        "hex", "int", "iter", "len", "list", "map", "max", "min", "next", "oct",
        "open", "ord", "pow", "range", "repr", "reversed", "round", "set",
        "sorted", "str", "sum", "tuple", "zip",
    }
)

#: Builtins that construct or reach code at runtime. Flow into one ends in a
#: Barrier -- never stitched across.
BARRIER_BUILTINS = frozenset(
    {"eval", "exec", "compile", "__import__", "globals", "locals", "vars"}
)

#: Modules whose functions are treated as value-transparent (result derives from
#: arguments). Stdlib and pure only; anything else is an opaque call.
TRANSPARENT_MODULES = frozenset(
    {
        "collections", "copy", "datetime", "decimal", "fractions", "functools",
        "itertools", "json", "math", "operator", "os.path", "pathlib", "re",
        "statistics", "string", "textwrap", "time", "typing", "uuid",
    }
)

PANDAS_MODULES = frozenset({"pandas", "pd"})

#: Frame-shaped operations: the result carries the receiver's columns forward.
FRAME_METHODS = frozenset(
    {
        "abs", "agg", "aggregate", "astype", "clip", "copy", "cumsum", "diff",
        "dropna", "ffill", "fillna", "head", "interpolate", "mask", "max",
        "mean", "min", "pct_change", "pipe", "query", "reindex", "replace",
        "reset_index", "rolling", "round", "sample", "set_index", "shift",
        "sort_index", "sort_values", "std", "sum", "tail", "transform", "where",
    }
)

#: Operations that name columns explicitly. Each named column becomes a node.
COLUMN_METHODS = frozenset(
    {"assign", "apply", "drop", "groupby", "join", "merge", "pivot_table", "rename"}
)

#: In-place container mutation.
MUTATING_METHODS = frozenset(
    {"add", "append", "extend", "insert", "setdefault", "update"}
)

_FRAME_NAME_SUFFIXES = ("_df", "_frame")
_FRAME_NAMES = frozenset({"df", "frame", "dataframe", "data_frame"})

_OVER = "over-approximate: "
_INSTANCE = ".@instance"
_PARAM = ".<param>."


def _param_id(element_id: str, name: str) -> str:
    """ID for a parameter node.

    ``interfaces.py`` defines no helper for this and card 1 mints the PARAMETER
    element with exactly this shape, so card 4 matches it rather than inventing a
    second namespace. Reported as a contract gap.
    """
    return f"{element_id}{_PARAM}{name}"


def _stronger(first: Confidence, second: Confidence) -> Confidence:
    """The better-supported of two confidences, using only ``combine``."""
    if first == second:
        return first
    return second if combine(first, second) == first else first


def _short_hash(payload: object) -> str:
    return hashlib.sha256(canonical_dumps(payload).encode("ascii")).hexdigest()[:16]


@dataclass(frozen=True, slots=True, order=True)
class _Src:
    """One value an expression derives from, with how well it is known.

    ``kind`` is set only where the *source* fixes the edge kind regardless of the
    target: a call result is a RETURNS, a column consumed by a frame operation is
    a READS.
    """

    id: str
    confidence: Confidence = Confidence.RESOLVED
    note: str = ""
    kind: LineageKind | None = None


def _merge_srcs(*groups: Iterable[_Src]) -> tuple[_Src, ...]:
    best: dict[str, _Src] = {}
    for group in groups:
        for src in group:
            current = best.get(src.id)
            if current is None:
                best[src.id] = src
            elif _stronger(current.confidence, src.confidence) == src.confidence:
                if src.confidence != current.confidence:
                    best[src.id] = src
    return tuple(sorted(best.values()))


def _retag(srcs: Iterable[_Src], confidence: Confidence, note: str) -> tuple[_Src, ...]:
    return tuple(
        sorted(
            _Src(s.id, combine(s.confidence, confidence), s.note or note, s.kind)
            for s in srcs
        )
    )


# --------------------------------------------------------------------------
# Pre-pass: scopes, binding ordinals, stable node IDs
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Def:
    """One binding occurrence: the node a value flows into."""

    id: str
    name: str
    scope_key: str
    kind: str  # variable | parameter | function | class | import | attribute
    line: int
    external_module: str = ""
    imported_name: str = ""


@dataclass
class _ScopeCtx:
    key: str  # unique key for the scope's symbol table
    kind: str  # module | function | class | lambda | comprehension
    element_id: str  # the element a node in this scope hangs off
    class_id: str = ""
    parent: "_ScopeCtx | None" = None
    global_names: frozenset[str] = frozenset()
    nonlocal_names: frozenset[str] = frozenset()


@dataclass
class _ModuleInfo:
    module: str
    path: str
    tree: ast.Module
    id_of_node: dict[int, str] = field(default_factory=dict)
    all_defs: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    def_meta: dict[str, _Def] = field(default_factory=dict)
    scope_of_node: dict[int, tuple[str, str]] = field(default_factory=dict)
    functions: dict[str, ast.AST] = field(default_factory=dict)
    classes: dict[str, ast.ClassDef] = field(default_factory=dict)
    counter: dict[tuple[str, str], int] = field(default_factory=dict)


def _declared(body: Sequence[ast.stmt], kind: type) -> frozenset[str]:
    """Names declared ``global``/``nonlocal`` anywhere in a scope body."""
    names: set[str] = set()
    stack: list[ast.AST] = list(body)
    while stack:
        node = stack.pop()
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
        ):
            continue
        if isinstance(node, kind):
            names.update(node.names)  # type: ignore[attr-defined]
        stack.extend(ast.iter_child_nodes(node))
    return frozenset(names)


class _PrePass:
    """Mints a stable node ID for every binding, in source order."""

    def __init__(self, info: _ModuleInfo) -> None:
        self.info = info
        self._lambda_n = 0
        self._comp_n = 0

    # -- binding -----------------------------------------------------------

    def _owner(self, ctx: _ScopeCtx, name: str) -> _ScopeCtx:
        if name in ctx.global_names:
            scope: _ScopeCtx | None = ctx
            while scope is not None and scope.parent is not None:
                scope = scope.parent
            return scope or ctx
        if name in ctx.nonlocal_names:
            scope = ctx.parent
            while scope is not None:
                if scope.kind in ("function", "lambda"):
                    return scope
                scope = scope.parent
        return ctx

    def _node_id(self, owner: _ScopeCtx, name: str, ordinal: int, kind: str) -> str:
        if kind == "parameter":
            return _param_id(owner.element_id, name)
        if owner.kind == "module":
            return make_id(self.info.module, name, ordinal)
        if owner.kind == "class":
            return make_id(
                self.info.module, f"{_qual_of(owner.element_id)}.{name}", ordinal
            )
        return local_id(owner.element_id, name, ordinal)

    def bind(
        self,
        ctx: _ScopeCtx,
        name: str,
        node: ast.AST,
        kind: str,
        *,
        owner: _ScopeCtx | None = None,
        external_module: str = "",
        imported_name: str = "",
    ) -> str:
        info = self.info
        scope = owner if owner is not None else self._owner(ctx, name)
        key = (scope.key, name)
        existing = info.all_defs.get(key)
        if kind in ("function", "class") or not existing:
            # `#n` separates a *redefinition* -- two `def`s of one name, which
            # card 1 also inventories separately. A variable rebound in the same
            # scope stays one node: the corpus models `y += 10` and a plain
            # reassignment as changes to the same value, not new values.
            ordinal = info.counter.get(key, 0) + 1
            info.counter[key] = ordinal
            node_id = self._node_id(scope, name, ordinal, kind)
        else:
            node_id = existing[0]
        if node_id not in info.def_meta:
            info.all_defs.setdefault(key, []).append(node_id)
            info.def_meta[node_id] = _Def(
                id=node_id,
                name=name,
                scope_key=scope.key,
                kind=kind,
                line=getattr(node, "lineno", 0),
                external_module=external_module,
                imported_name=imported_name,
            )
        info.id_of_node[id(node)] = node_id
        return node_id

    # -- scopes ------------------------------------------------------------

    def run(self) -> None:
        ctx = _ScopeCtx(key="", kind="module", element_id=self.info.module)
        for stmt in self.info.tree.body:
            self.stmt(stmt, ctx)

    def _child(
        self, ctx: _ScopeCtx, name: str, element_id: str, kind: str,
        body: Sequence[ast.stmt],
    ) -> _ScopeCtx:
        return _ScopeCtx(
            key=f"{ctx.key}.{name}" if ctx.key else name,
            kind=kind,
            element_id=element_id,
            class_id=element_id if kind == "class" else ctx.class_id,
            parent=ctx,
            global_names=_declared(body, ast.Global),
            nonlocal_names=_declared(body, ast.Nonlocal),
        )

    def _element_id_for(self, ctx: _ScopeCtx, name: str, ordinal: int) -> str:
        if ctx.kind == "module":
            return make_id(self.info.module, name, ordinal)
        if ctx.kind == "class":
            return make_id(
                self.info.module, f"{_qual_of(ctx.element_id)}.{name}", ordinal
            )
        return local_id(ctx.element_id, name, ordinal)

    def stmt(self, node: ast.stmt, ctx: _ScopeCtx) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            def_id = self.bind(ctx, node.name, node, "function")
            for dec in node.decorator_list:
                self.expr(dec, ctx)
            args = node.args
            for default in [*args.defaults, *[d for d in args.kw_defaults if d]]:
                self.expr(default, ctx)
            child = self._child(ctx, node.name, def_id, "function", node.body)
            self._bind_args(args, child)
            self.info.functions[def_id] = node
            self.info.scope_of_node[id(node)] = (child.key, def_id)
            for inner in node.body:
                self.stmt(inner, child)
            return
        if isinstance(node, ast.ClassDef):
            def_id = self.bind(ctx, node.name, node, "class")
            for expr in [*node.bases, *node.decorator_list]:
                self.expr(expr, ctx)
            for kw in node.keywords:
                self.expr(kw.value, ctx)
            child = self._child(ctx, node.name, def_id, "class", node.body)
            self.info.classes[def_id] = node
            self.info.scope_of_node[id(node)] = (child.key, def_id)
            for inner in node.body:
                self.stmt(inner, child)
            return
        if isinstance(node, ast.Assign):
            self.expr(node.value, ctx)
            for target in node.targets:
                self.target(target, ctx)
            return
        if isinstance(node, ast.AnnAssign):
            if node.value is not None:
                self.expr(node.value, ctx)
                self.target(node.target, ctx)
            return
        if isinstance(node, ast.AugAssign):
            # An augmented assignment mutates the binding it reads. No new node.
            self.expr(node.value, ctx)
            if isinstance(node.target, (ast.Attribute, ast.Subscript)):
                self.expr(node.target.value, ctx)
            return
        if isinstance(node, (ast.For, ast.AsyncFor)):
            self.expr(node.iter, ctx)
            self.target(node.target, ctx)
            for inner in [*node.body, *node.orelse]:
                self.stmt(inner, ctx)
            return
        if isinstance(node, (ast.While, ast.If)):
            self.expr(node.test, ctx)
            for inner in [*node.body, *node.orelse]:
                self.stmt(inner, ctx)
            return
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                self.expr(item.context_expr, ctx)
                if item.optional_vars is not None:
                    self.target(item.optional_vars, ctx)
            for inner in node.body:
                self.stmt(inner, ctx)
            return
        if isinstance(node, ast.Try) or type(node).__name__ == "TryStar":
            for inner in node.body:  # type: ignore[attr-defined]
                self.stmt(inner, ctx)
            for handler in node.handlers:  # type: ignore[attr-defined]
                if handler.type is not None:
                    self.expr(handler.type, ctx)
                if handler.name:
                    self.bind(ctx, handler.name, handler, "variable")
                for inner in handler.body:
                    self.stmt(inner, ctx)
            for inner in [*node.orelse, *node.finalbody]:  # type: ignore[attr-defined]
                self.stmt(inner, ctx)
            return
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                self.bind(ctx, bound, alias, "import", external_module=alias.name)
            return
        if isinstance(node, ast.ImportFrom):
            source = ("." * (node.level or 0)) + (node.module or "")
            for alias in node.names:
                bound = alias.asname or alias.name
                self.bind(
                    ctx, bound, alias, "import",
                    external_module=source, imported_name=alias.name,
                )
            return
        if isinstance(node, ast.Match):
            self.expr(node.subject, ctx)
            for case in node.cases:
                self._pattern(case.pattern, ctx)
                if case.guard is not None:
                    self.expr(case.guard, ctx)
                for inner in case.body:
                    self.stmt(inner, ctx)
            return
        if isinstance(
            node, (ast.Global, ast.Nonlocal, ast.Pass, ast.Break, ast.Continue)
        ):
            return
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                self.expr(child, ctx)
            elif isinstance(child, ast.stmt):
                self.stmt(child, ctx)

    def _pattern(self, pattern: ast.pattern, ctx: _ScopeCtx) -> None:
        if isinstance(pattern, ast.MatchAs):
            if pattern.pattern is not None:
                self._pattern(pattern.pattern, ctx)
            if pattern.name:
                self.bind(ctx, pattern.name, pattern, "variable")
            return
        if isinstance(pattern, ast.MatchStar):
            if pattern.name:
                self.bind(ctx, pattern.name, pattern, "variable")
            return
        for child in ast.iter_child_nodes(pattern):
            if isinstance(child, ast.pattern):
                self._pattern(child, ctx)
            elif isinstance(child, ast.expr):
                self.expr(child, ctx)

    def _bind_args(self, args: ast.arguments, ctx: _ScopeCtx) -> None:
        for arg in _all_args(args):
            self.bind(ctx, arg.arg, arg, "parameter", owner=ctx)

    def target(self, node: ast.expr, ctx: _ScopeCtx) -> None:
        if isinstance(node, ast.Name):
            self.bind(ctx, node.id, node, "variable")
            return
        if isinstance(node, (ast.Tuple, ast.List)):
            for element in node.elts:
                self.target(element, ctx)
            return
        if isinstance(node, ast.Starred):
            self.target(node.value, ctx)
            return
        if isinstance(node, ast.Attribute):
            self.expr(node.value, ctx)
            return
        if isinstance(node, ast.Subscript):
            self.expr(node.value, ctx)
            self.expr(node.slice, ctx)
            return
        self.expr(node, ctx)

    def expr(self, node: ast.expr, ctx: _ScopeCtx) -> None:
        if isinstance(node, ast.NamedExpr):
            self.expr(node.value, ctx)
            owner = ctx.parent if ctx.kind == "comprehension" and ctx.parent else ctx
            self.bind(owner, node.target.id, node.target, "variable")
            return
        if isinstance(node, ast.Lambda):
            self._lambda_n += 1
            name = f"<lambda{self._lambda_n}>"
            element_id = self._element_id_for(ctx, name, 1)
            for default in [
                *node.args.defaults, *[d for d in node.args.kw_defaults if d]
            ]:
                self.expr(default, ctx)
            child = self._child(ctx, name, element_id, "lambda", [])
            self._bind_args(node.args, child)
            self.info.scope_of_node[id(node)] = (child.key, element_id)
            self.expr(node.body, child)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            self._comp_n += 1
            name = f"<comp{self._comp_n}>"
            element_id = self._element_id_for(ctx, name, 1)
            child = self._child(ctx, name, element_id, "comprehension", [])
            self.info.scope_of_node[id(node)] = (child.key, element_id)
            for index, gen in enumerate(node.generators):
                self.expr(gen.iter, ctx if index == 0 else child)
                self.target(gen.target, child)
                for condition in gen.ifs:
                    self.expr(condition, child)
            if isinstance(node, ast.DictComp):
                self.expr(node.key, child)
                self.expr(node.value, child)
            else:
                self.expr(node.elt, child)  # type: ignore[attr-defined]
            return
        for child_node in ast.iter_child_nodes(node):
            if isinstance(child_node, ast.expr):
                self.expr(child_node, ctx)
            elif isinstance(child_node, ast.keyword):
                self.expr(child_node.value, ctx)
            elif isinstance(child_node, ast.comprehension):
                self.expr(child_node.iter, ctx)
                self.target(child_node.target, ctx)


def _qual_of(element_id: str) -> str:
    return element_id.split("::", 1)[1] if "::" in element_id else ""


# --------------------------------------------------------------------------
# The tracer
# --------------------------------------------------------------------------


class LineageTracer:
    """Implements :class:`~cascade_map.contracts.interfaces.LineageCard`.

    ``root`` is the directory element spans are relative to. ``sink_ids`` are the
    decision sinks a forward slice ends at; with none supplied (owner question
    Q1 unanswered) ``reaches_sink_ids`` is empty everywhere rather than guessed.
    """

    def __init__(
        self,
        root: str | Path = ".",
        sink_ids: Sequence[str] = (),
        transparent_modules: Iterable[str] = TRANSPARENT_MODULES,
    ) -> None:
        self.root = Path(root)
        self.sink_ids: tuple[str, ...] = tuple(sorted(set(sink_ids)))
        self.transparent_modules = frozenset(transparent_modules)
        self._reset()

    # -- lifecycle ---------------------------------------------------------

    def _reset(self) -> None:
        self.lineage_edges: tuple[LineageEdge, ...] = ()
        self.barriers: tuple[Barrier, ...] = ()
        self.unresolved: list[Unresolved] = []
        self._edges: dict[str, LineageEdge] = {}
        self._barriers: dict[str, Barrier] = {}
        self._modules: dict[str, _ModuleInfo] = {}
        self._elements: dict[str, Element] = {}
        self._param_elements: dict[tuple[str, str], str] = {}
        self._config_keys: list[Element] = []
        self._declared_features: set[str] = set()
        self._call_targets: dict[tuple[str, int], list[tuple[str, Confidence]]] = {}
        self._configures: list[Edge] = []
        self._feature_names: set[str] = set()
        self._frame_defs: set[str] = set()
        self._key_nodes: set[str] = set()
        self._established: set[str] = set()
        self._alias_of: dict[str, tuple[str, ...]] = {}
        self._instance_of: dict[str, str] = {}
        self._container_keys: dict[str, set[str]] = {}
        self._mutated_params: set[str] = set()
        self._param_bindings: list[tuple[str, tuple[_Src, ...], SourceSpan, str]] = []
        self._out: dict[str, tuple[tuple[str, str], ...]] = {}
        self._in: dict[str, tuple[tuple[str, str], ...]] = {}
        self._slice_cache: dict[str, Slice] = {}

    # -- LineageCard -------------------------------------------------------

    def trace_values(
        self, elements: Sequence[Element], edges: Sequence[Edge]
    ) -> tuple[Sequence[LineageEdge], Sequence[Barrier]]:
        """Emit every lineage edge and barrier over card 1's and card 2's output."""
        self._reset()
        self._index_elements(elements)
        self._index_edges(edges)
        self._load_modules(elements)
        for module in sorted(
            self._modules, key=lambda name: (self._modules[name].path, name)
        ):
            _ModuleWalker(self, self._modules[module]).run()
        self._emit_mutation_through_parameters()
        self._link_config_keys()
        self._freeze()
        self._link_container_keys()
        self._freeze()
        return self.lineage_edges, self.barriers

    def _freeze(self) -> None:
        self.lineage_edges = tuple(
            sorted(self._edges.values(), key=lambda e: (e.id, e.source_id, e.target_id))
        )
        self.barriers = tuple(sorted(self._barriers.values(), key=lambda b: b.id))
        self._build_adjacency()

    def slice(self, root_id: str, direction: str) -> Slice:
        """Backward or forward slice of ``root_id`` as a reproducible ID set.

        Every hop is evidenced: each ID in ``edge_ids`` resolves to a
        :class:`LineageEdge` carrying its method, confidence and span. A slice
        that crosses a barrier lists it and reports ``UNKNOWN``.

        ``reaches_sink_ids`` answers the same question in both directions: which
        decision sinks the *root* can reach. A backward slice of a feature is
        worth reading precisely because it also says whether that feature
        matters.
        """
        if direction not in ("backward", "forward"):
            raise ValueError(
                f"direction must be 'backward' or 'forward', not {direction!r}"
            )
        key = f"{direction}:{root_id}"
        cached = self._slice_cache.get(key)
        if cached is not None:
            return cached
        known = root_id in self._out or root_id in self._in or root_id in self._barriers
        if not known:
            self.unresolved.append(
                Unresolved(
                    id=f"@slice-root:{root_id}",
                    reason=UnresolvedReason.MISSING_TARGET,
                    span=SourceSpan(path="", line=0),
                    description=(
                        f"slice requested for {root_id!r}, which is not a lineage node"
                    ),
                )
            )
        members, edge_ids, confidences = self._walk(root_id, direction)
        barrier_ids = {member for member in members if member in self._barriers}
        confidence = combine(*confidences) if confidences else Confidence.UNKNOWN
        if barrier_ids:
            confidence = Confidence.UNKNOWN
        result = Slice(
            id=f"@slice:{direction}:{root_id}",
            root_id=root_id,
            direction=direction,
            member_ids=tuple(sorted(members)),
            edge_ids=tuple(sorted(edge_ids)),
            barrier_ids=tuple(sorted(barrier_ids)),
            reaches_sink_ids=self._reaches_sinks(root_id, members, direction),
            confidence=confidence,
        )
        self._slice_cache[key] = result
        return result

    def _walk(
        self, root_id: str, direction: str
    ) -> tuple[set[str], set[str], list[Confidence]]:
        adjacency = self._in if direction == "backward" else self._out
        sinks = set(self.sink_ids)
        members: set[str] = {root_id}
        edge_ids: set[str] = set()
        confidences: list[Confidence] = []
        queue: deque[str] = deque([root_id])
        seen: set[str] = {root_id}
        while queue:
            node = queue.popleft()
            if direction == "forward" and node in sinks and node != root_id:
                continue  # a forward slice ends at a decision sink
            for neighbour, edge_id in adjacency.get(node, ()):  # already sorted
                edge_ids.add(edge_id)
                confidences.append(self._edges[edge_id].provenance.confidence)
                members.add(neighbour)
                if neighbour not in seen:
                    seen.add(neighbour)
                    queue.append(neighbour)
        return members, edge_ids, confidences

    def _reaches_sinks(
        self, root_id: str, members: set[str], direction: str
    ) -> tuple[str, ...]:
        if not self.sink_ids:
            return ()
        if direction == "forward":
            return tuple(sorted(members & set(self.sink_ids)))
        downstream, _, _ = self._walk(root_id, "forward")
        return tuple(sorted(downstream & set(self.sink_ids)))

    # -- reporting helpers -------------------------------------------------

    def hops(self, sliced: Slice) -> tuple[dict[str, object], ...]:
        """Per-hop evidence for a slice: one record per edge, sorted, no prose."""
        records = []
        for edge_id in sliced.edge_ids:
            edge = self._edges[edge_id]
            records.append(
                {
                    "edge_id": edge.id,
                    "kind": str(edge.kind),
                    "source_id": edge.source_id,
                    "target_id": edge.target_id,
                    "method": str(edge.provenance.method),
                    "confidence": str(edge.provenance.confidence),
                    "note": edge.provenance.note,
                    "path": edge.span.path if edge.span else "",
                    "line": edge.span.line if edge.span else 0,
                }
            )
        return tuple(sorted(records, key=lambda row: canonical_dumps(row)))

    def feature_ids(self) -> tuple[str, ...]:
        return tuple(sorted(feature_id(name) for name in self._feature_names))

    def key_node_ids(self) -> tuple[str, ...]:
        """Container-scoped key nodes: a key that no config declared a feature."""
        return tuple(sorted(self._key_nodes))

    def over_approximate_edge_ids(self) -> tuple[str, ...]:
        """Edges where reach was preferred to precision. Stated, never hidden."""
        return tuple(
            sorted(
                edge.id
                for edge in self._edges.values()
                if edge.provenance.note.startswith(_OVER)
            )
        )

    def default_slices(self) -> tuple[Slice, ...]:
        """A backward and a forward slice for every feature, key and sink."""
        roots = [*self.feature_ids(), *self.key_node_ids(), *self.sink_ids]
        out: list[Slice] = []
        for root in sorted(set(roots)):
            out.append(self.slice(root, "backward"))
            out.append(self.slice(root, "forward"))
        return tuple(sorted(out, key=lambda s: s.id))

    def emit(self, slices: Sequence[Slice] | None = None) -> dict[str, str]:
        """The card's three artifacts, byte-identical across runs."""
        chosen = list(self.default_slices()) if slices is None else list(slices)
        return {
            "lineage.jsonl": canonical_jsonl(self.lineage_edges, "id"),
            "barriers.jsonl": canonical_jsonl(self.barriers, "id"),
            "slices.jsonl": canonical_jsonl(sorted(chosen, key=lambda s: s.id), "id"),
        }

    # -- indexing ----------------------------------------------------------

    def _index_elements(self, elements: Sequence[Element]) -> None:
        for element in elements:
            self._elements[element.id] = element
            if element.kind is ElementKind.PARAMETER and element.parent_id:
                self._param_elements[(element.parent_id, element.name)] = element.id
            elif element.kind is ElementKind.CONFIG_KEY:
                self._config_keys.append(element)
                self._declared_features.update(_config_names(element))
            elif element.kind is ElementKind.FEATURE:
                name = (
                    element.id[len("@feature:") :]
                    if element.id.startswith("@feature:")
                    else element.name
                )
                self._declared_features.add(name)
                self._feature_names.add(name)

    def _index_edges(self, edges: Sequence[Edge]) -> None:
        for edge in edges:
            if (
                edge.kind in (EdgeKind.CALLS, EdgeKind.INSTANTIATES)
                and edge.call_site is not None
            ):
                key = (edge.source_id, edge.call_site.line)
                self._call_targets.setdefault(key, []).append(
                    (edge.target_id, edge.provenance.confidence)
                )
            elif edge.kind is EdgeKind.CONFIGURES:
                self._configures.append(edge)
                if edge.target_id.startswith("@feature:"):
                    self._declared_features.add(edge.target_id[len("@feature:") :])
        for targets in self._call_targets.values():
            targets.sort()

    def _load_modules(self, elements: Sequence[Element]) -> None:
        seen: dict[str, str] = {}
        for element in elements:
            if element.kind is not ElementKind.MODULE:
                continue
            seen.setdefault(element.module or element.name, element.span.path)
        for module, rel_path in sorted(seen.items()):
            path = self.root / rel_path
            try:
                text = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                self._unresolved(
                    module, rel_path, UnresolvedReason.MISSING_TARGET,
                    "module file not found",
                )
                continue
            except UnicodeDecodeError:
                self._unresolved(
                    module, rel_path, UnresolvedReason.DECODE_ERROR,
                    "module is not valid UTF-8",
                )
                continue
            except OSError as exc:  # pragma: no cover - environment dependent
                self._unresolved(
                    module, rel_path, UnresolvedReason.MISSING_TARGET,
                    f"unreadable: {exc.strerror}",
                )
                continue
            try:
                tree = ast.parse(text, filename=str(rel_path))
            except SyntaxError as exc:
                self._unresolved(
                    module, rel_path, UnresolvedReason.SYNTAX_ERROR,
                    f"cannot parse for lineage: {exc.msg}", line=exc.lineno or 1,
                )
                continue
            info = _ModuleInfo(module=module, path=rel_path, tree=tree)
            _PrePass(info).run()
            self._modules[module] = info

    def _unresolved(
        self, module: str, path: str, reason: UnresolvedReason, description: str,
        line: int = 1,
    ) -> None:
        self.unresolved.append(
            Unresolved(
                id=f"@lineage-unresolved:{module}:{reason}",
                reason=reason,
                span=SourceSpan(path=path, line=line),
                description=description,
            )
        )

    # -- emission ----------------------------------------------------------

    def add_edge(
        self,
        kind: LineageKind,
        source_id: str,
        target_id: str,
        span: SourceSpan,
        method: Method,
        confidence: Confidence,
        note: str = "",
    ) -> str:
        if not source_id or not target_id:
            return ""
        if source_id == target_id and kind is not LineageKind.MUTATES:
            # A value assigned from an expression containing itself is one node
            # here, so the edge would say only that it equals itself. An
            # in-place change is different: MUTATES self is how `y += 1` is
            # recorded, and dropping it would lose the write.
            return ""
        payload = {
            "kind": str(kind),
            "source": source_id,
            "target": target_id,
            "path": span.path,
            "line": span.line,
            "col": span.col,
            "method": str(method),
            "confidence": str(confidence),
            "note": note,
        }
        edge_id = f"@lin:{kind}:{_short_hash(payload)}"
        if edge_id not in self._edges:
            self._edges[edge_id] = LineageEdge(
                id=edge_id,
                kind=kind,
                source_id=source_id,
                target_id=target_id,
                provenance=Provenance(
                    method=method, confidence=confidence, span=span, note=note
                ),
                span=span,
            )
        return edge_id

    def add_barrier(
        self, element_id: str, span: SourceSpan, reason: UnresolvedReason,
        description: str,
    ) -> str:
        col = span.col if span.col is not None else 0
        base = f"@barrier:{element_id}@{span.line}:{col}"
        barrier_id = base
        suffix = 2
        while (
            barrier_id in self._barriers
            and self._barriers[barrier_id].description != description
        ):
            barrier_id = f"{base}#{suffix}"
            suffix += 1
        if barrier_id not in self._barriers:
            self._barriers[barrier_id] = Barrier(
                id=barrier_id,
                element_id=element_id,
                span=span,
                reason=reason,
                description=description,
            )
        return barrier_id

    def note_feature(self, name: str) -> str:
        self._feature_names.add(name)
        return feature_id(name)

    # -- post-passes -------------------------------------------------------

    def _emit_mutation_through_parameters(self) -> None:
        """A callee that mutates a parameter changes the caller's argument.

        Recorded as MUTATES from the parameter node back onto each argument
        node, PROBABLE: the mutation is certain, the aliasing is the assumption.
        """
        for param_id, arg_srcs, span, element_id in self._param_bindings:
            if param_id not in self._mutated_params:
                continue
            for src in arg_srcs:
                self.add_edge(
                    LineageKind.MUTATES,
                    param_id,
                    src.id,
                    span,
                    Method.DATAFLOW,
                    combine(src.confidence, Confidence.PROBABLE),
                    note=f"mutation through parameter, observed in {element_id}",
                )

    def _link_config_keys(self) -> None:
        """A feature named in config and one written in code are one node."""
        for edge in sorted(self._configures, key=lambda e: e.id):
            if edge.target_id.startswith("@feature:"):
                self._feature_names.add(edge.target_id[len("@feature:") :])
                self.add_edge(
                    LineageKind.ASSIGNS,
                    edge.source_id,
                    edge.target_id,
                    edge.provenance.span or SourceSpan(path="", line=0),
                    Method.CONFIG_STRING_MATCH,
                    combine(edge.provenance.confidence, Confidence.RESOLVED),
                    note="config key names a feature (card 2 CONFIGURES edge)",
                )
        for element in sorted(self._config_keys, key=lambda e: e.id):
            for candidate in sorted(_config_names(element)):
                if candidate not in self._feature_names:
                    continue
                self.add_edge(
                    LineageKind.ASSIGNS,
                    element.id,
                    feature_id(candidate),
                    element.span,
                    Method.CONFIG_STRING_MATCH,
                    Confidence.RESOLVED,
                    note=f"config key names the feature {candidate!r}",
                )

    def _link_container_keys(self) -> None:
        """Join one container's key to the same key of a container it came from.

        `engineer` writes `features["momentum"]` and `decide` reads it from the
        parameter it was passed in. Those are two nodes -- correctly, they are
        two containers -- but the value did travel between them, and a backward
        slice that stops at the parameter answers nothing. The link is only
        drawn where an actual dataflow path already connects the containers, so
        it is never a match on the key's name alone.
        """
        carriers = {
            LineageKind.ASSIGNS,
            LineageKind.RETURNS,
            LineageKind.PARAMETER_BINDING,
            LineageKind.MUTATES,
        }
        keys_by_container: dict[str, dict[str, str]] = {}
        for container, nodes in self._container_keys.items():
            for node_id in nodes:
                if not node_id.startswith(f"{container}["):
                    continue
                keys_by_container.setdefault(container, {})[
                    node_id[len(container) + 1 : -1]
                ] = node_id
        fed = {edge.target_id for edge in self.lineage_edges}
        for container in sorted(keys_by_container):
            orphans = sorted(
                (key, node_id)
                for key, node_id in keys_by_container[container].items()
                if node_id not in fed
            )
            if not orphans:
                continue
            for source in self._ancestor_containers(container, carriers):
                for key, node_id in orphans:
                    origin = keys_by_container.get(source, {}).get(key)
                    if origin is None or origin == node_id:
                        continue
                    self.add_edge(
                        LineageKind.CONTAINER_WRITE,
                        origin,
                        node_id,
                        self._elements_span(container),
                        Method.DATAFLOW,
                        Confidence.PROBABLE,
                        f"the container holding {key!r} reached this scope from "
                        f"{source}",
                    )

    def _ancestor_containers(
        self, container: str, carriers: set[LineageKind]
    ) -> tuple[str, ...]:
        """Containers whose value can reach *container*, nearest first."""
        seen = {container}
        found: list[str] = []
        queue: deque[str] = deque([container])
        while queue:
            node = queue.popleft()
            for source, edge_id in self._in.get(node, ()):
                if self._edges[edge_id].kind not in carriers or source in seen:
                    continue
                seen.add(source)
                queue.append(source)
                if source in self._container_keys:
                    found.append(source)
        return tuple(found)

    def _elements_span(self, node_id: str) -> SourceSpan:
        for edge in self.lineage_edges:
            if edge.target_id == node_id or edge.source_id == node_id:
                if edge.span is not None:
                    return edge.span
        return SourceSpan(path="", line=0)

    # -- cross-module lookups ---------------------------------------------

    def _function_node(self, element_id: str) -> tuple[ast.AST, _ModuleInfo] | None:
        """The parsed def for an element ID, or None if we never parsed it."""
        if not element_id or "::" not in element_id:
            return None
        info = self._modules.get(element_id.split("::")[0])
        if info is None:
            return None
        found = info.functions.get(element_id)
        return None if found is None else (found, info)

    def _is_class(self, element_id: str) -> bool:
        if not element_id or "::" not in element_id:
            return False
        info = self._modules.get(element_id.split("::")[0])
        return info is not None and element_id in info.classes

    def _is_known_callee(self, element_id: str) -> bool:
        return self._function_node(element_id) is not None or self._is_class(element_id)

    def _build_adjacency(self) -> None:
        out: dict[str, list[tuple[str, str]]] = {}
        into: dict[str, list[tuple[str, str]]] = {}
        for edge in self.lineage_edges:
            out.setdefault(edge.source_id, []).append((edge.target_id, edge.id))
            out.setdefault(edge.target_id, [])
            into.setdefault(edge.target_id, []).append((edge.source_id, edge.id))
            into.setdefault(edge.source_id, [])
        self._out = {key: tuple(sorted(value)) for key, value in sorted(out.items())}
        self._in = {key: tuple(sorted(value)) for key, value in sorted(into.items())}


def _config_names(element: Element) -> set[str]:
    """Strings a CONFIG_KEY element could be naming.

    Card 1 may carry the value in ``name`` or ``signature``; the JSON pointer's
    last segment is used too. Only names that also appear as a written key are
    promoted, so a wrong candidate produces no node.
    """
    names = {element.name}
    if element.signature:
        names.add(element.signature.strip().strip("\"'"))
    if "::" in element.id:
        names.add(element.id.rsplit("/", 1)[-1])
    return {name for name in names if name and name.isidentifier()}


# --------------------------------------------------------------------------
# Per-module dataflow walk
# --------------------------------------------------------------------------


@dataclass
class _WalkScope:
    key: str
    kind: str
    element_id: str
    class_id: str = ""


class _ModuleWalker:
    """Reaching-definition walk of one module, emitting lineage edges."""

    def __init__(self, tracer: LineageTracer, info: _ModuleInfo) -> None:
        self.t = tracer
        self.mod = info
        self.env: dict[tuple[str, str], tuple[str, ...]] = {}
        self.scopes: list[_WalkScope] = [
            _WalkScope(key="", kind="module", element_id=info.module)
        ]
        self.guards: list[tuple[_Src, ...]] = []
        self._span: SourceSpan = SourceSpan(path=info.path, line=1)
        self._pending: dict[int, list[tuple[str, tuple[_Src, ...]]]] = {}

    # -- helpers -----------------------------------------------------------

    @property
    def scope(self) -> _WalkScope:
        return self.scopes[-1]

    @property
    def element_id(self) -> str:
        return self.scope.element_id

    def node_span(self, node: ast.AST) -> SourceSpan:
        return SourceSpan(
            path=self.mod.path,
            line=getattr(node, "lineno", 1),
            end_line=getattr(node, "end_lineno", None),
            col=getattr(node, "col_offset", None),
        )

    @property
    def span(self) -> SourceSpan:
        """The statement the flow happens in. Settled convention."""
        return self._span

    def node_id(self, node: ast.AST) -> str:
        return self.mod.id_of_node.get(id(node), "")

    def bind_env(self, node_id: str) -> None:
        meta = self.mod.def_meta.get(node_id)
        if meta is None:
            return
        self.env[(meta.scope_key, meta.name)] = (node_id,)

    def emit(
        self, kind: LineageKind, src: _Src, target_id: str, *,
        method: Method = Method.DATAFLOW, extra: Confidence | None = None,
        note: str = "",
    ) -> None:
        confidence = (
            combine(src.confidence, extra) if extra is not None else src.confidence
        )
        self.t.add_edge(
            kind, src.id, target_id, self.span, method, confidence, src.note or note
        )

    # -- entry -------------------------------------------------------------

    def run(self) -> None:
        self.block(self.mod.tree.body)

    def block(self, stmts: Sequence[ast.stmt]) -> None:
        for stmt in stmts:
            self.stmt(stmt)

    def _merge_env(
        self,
        left: dict[tuple[str, str], tuple[str, ...]],
        right: dict[tuple[str, str], tuple[str, ...]],
    ) -> dict[tuple[str, str], tuple[str, ...]]:
        merged = dict(left)
        for key, value in right.items():
            merged[key] = tuple(sorted(set(merged.get(key, ())) | set(value)))
        return merged

    # -- statements --------------------------------------------------------

    def stmt(self, node: ast.stmt) -> None:
        previous = self._span
        self._span = self.node_span(node)
        try:
            self._stmt(node)
        finally:
            self._span = previous

    def _stmt(self, node: ast.stmt) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._function(node)
            return
        if isinstance(node, ast.ClassDef):
            self._class(node)
            return
        if isinstance(node, ast.Assign):
            srcs = self.sources(node.value)
            for target in node.targets:
                self.assign(target, srcs, node.value)
            return
        if isinstance(node, ast.AnnAssign):
            if node.value is not None:
                self.assign(node.target, self.sources(node.value), node.value)
            return
        if isinstance(node, ast.AugAssign):
            self._aug_assign(node)
            return
        if isinstance(node, (ast.For, ast.AsyncFor)):
            iterable = self.sources(node.iter)
            self.assign(
                node.target,
                _retag(iterable, Confidence.PROBABLE, "element of iterable"),
                node.iter,
            )
            before = dict(self.env)
            self.block(node.body)
            self.block(node.body)  # second pass picks up loop-carried definitions
            self.env = self._merge_env(self.env, before)
            self.block(node.orelse)
            return
        if isinstance(node, ast.While):
            guard = self.sources(node.test)
            self._emit_reads(guard, "loop condition")
            before = dict(self.env)
            self.guards.append(guard)
            self.block(node.body)
            self.block(node.body)
            self.guards.pop()
            self.env = self._merge_env(self.env, before)
            self.block(node.orelse)
            return
        if isinstance(node, ast.If):
            guard = self.sources(node.test)
            self._emit_reads(guard, "branch condition")
            before = dict(self.env)
            self.guards.append(guard)
            self.block(node.body)
            after_body = dict(self.env)
            self.env = dict(before)
            self.block(node.orelse)
            self.guards.pop()
            self.env = self._merge_env(after_body, self.env)
            return
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                srcs = self.sources(item.context_expr)
                if item.optional_vars is not None:
                    self.assign(item.optional_vars, srcs, item.context_expr)
            self.block(node.body)
            return
        if isinstance(node, ast.Try) or type(node).__name__ == "TryStar":
            before = dict(self.env)
            self.block(node.body)  # type: ignore[attr-defined]
            after_body = dict(self.env)
            merged = self._merge_env(before, after_body)
            results = [after_body]
            for handler in node.handlers:  # type: ignore[attr-defined]
                self.env = dict(merged)
                if handler.name:
                    handler_id = self.node_id(handler)
                    if handler_id:
                        self.bind_env(handler_id)
                self.block(handler.body)
                results.append(dict(self.env))
            self.env = results[0]
            for extra in results[1:]:
                self.env = self._merge_env(self.env, extra)
            self.block(node.orelse)  # type: ignore[attr-defined]
            self.block(node.finalbody)  # type: ignore[attr-defined]
            return
        if isinstance(node, ast.Return):
            self._return(node)
            return
        if isinstance(node, ast.Expr):
            self.sources(node.value)
            return
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                alias_id = self.node_id(alias)
                if alias_id:
                    self.bind_env(alias_id)
            return
        if isinstance(node, ast.Delete):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    for key in [k for k in self.env if k[1] == target.id]:
                        self.env.pop(key, None)
            return
        if isinstance(node, (ast.Raise, ast.Assert)):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.expr):
                    self._emit_reads(self.sources(child), "guard")
            return
        if isinstance(node, ast.Match):
            subject = self.sources(node.subject)
            self._emit_reads(subject, "match subject")
            before = dict(self.env)
            results = []
            for case in node.cases:
                self.env = dict(before)
                self._bind_pattern(case.pattern, subject)
                self.block(case.body)
                results.append(dict(self.env))
            self.env = results[0] if results else before
            for extra in results[1:]:
                self.env = self._merge_env(self.env, extra)
            return
        if isinstance(
            node, (ast.Global, ast.Nonlocal, ast.Pass, ast.Break, ast.Continue)
        ):
            return
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt):
                self.stmt(child)
            elif isinstance(child, ast.expr):
                self.sources(child)

    def _return(self, node: ast.Return) -> None:
        target = self.reader_id
        carried: set[str] = set()
        if node.value is not None:
            for src in self.sources(node.value):
                kind = (
                    LineageKind.READS
                    if src.kind is LineageKind.READS or self._is_container_node(src.id)
                    else LineageKind.RETURNS
                )
                carried.add(src.id)
                self.emit(kind, src, target, note="return value")
        # A return under a guard depends on the condition that selected it:
        # this is how a rule cascade decides. Real dependence, not a guess. A
        # value that is already returned here needs no second, weaker edge.
        for guard in self.guards:
            for src in guard:
                if src.id in carried:
                    continue
                self.emit(
                    LineageKind.RETURNS, src, target, extra=Confidence.PROBABLE,
                    note="control dependence: this branch condition selects this return",
                )

    def _aug_assign(self, node: ast.AugAssign) -> None:
        """``y += 1`` mutates ``y``; it does not make a new ``y``."""
        value = self.sources(node.value)
        targets = self._mutation_targets(node.target)
        for target_id in targets:
            self.t.add_edge(
                LineageKind.MUTATES, target_id, target_id, self.span, Method.DATAFLOW,
                Confidence.RESOLVED, "augmented assignment reads and writes this value",
            )
            for src in value:
                self.emit(
                    LineageKind.MUTATES, src, target_id, note="augmented assignment"
                )
            if self._is_parameter(target_id):
                self.t._mutated_params.add(target_id)

    def _mutation_targets(self, target: ast.expr) -> tuple[str, ...]:
        if isinstance(target, ast.Name):
            return tuple(src.id for src in self.read_name(target.id))
        if isinstance(target, ast.Attribute):
            return tuple(src.id for src in self.read_attribute(target))
        if isinstance(target, ast.Subscript):
            return tuple(src.id for src in self.read_subscript(target))
        return ()

    def _bind_pattern(self, pattern: ast.pattern, srcs: tuple[_Src, ...]) -> None:
        if isinstance(pattern, (ast.MatchAs, ast.MatchStar)) and pattern.name:
            target_id = self.node_id(pattern)
            if target_id:
                self.bind_env(target_id)
                for src in _retag(srcs, Confidence.PROBABLE, "match capture"):
                    self.emit(LineageKind.ASSIGNS, src, target_id)
        for child in ast.iter_child_nodes(pattern):
            if isinstance(child, ast.pattern):
                self._bind_pattern(child, srcs)

    # -- definitions -------------------------------------------------------

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        def_id = self.node_id(node)
        if def_id:
            self.bind_env(def_id)
        self._emit_defaults(node.args)
        scope_key, element_id = self.mod.scope_of_node.get(id(node), ("", def_id))
        saved_env = self.env
        self.env = dict(self.env)
        self.scopes.append(
            _WalkScope(
                key=scope_key, kind="function", element_id=element_id,
                class_id=self.scope.class_id,
            )
        )
        self._seed_params(node.args)
        self.block(node.body)
        self.scopes.pop()
        self.env = saved_env

    def _seed_params(self, args: ast.arguments) -> None:
        for arg in _all_args(args):
            param_id = self.node_id(arg)
            if not param_id:
                continue
            self.bind_env(param_id)
            if _looks_like_frame(arg.arg):
                self.t._frame_defs.add(param_id)

    def _emit_defaults(self, args: ast.arguments) -> None:
        positional = [*args.posonlyargs, *args.args]
        paired: list[tuple[ast.arg, ast.expr]] = []
        if args.defaults:
            paired.extend(
                zip(positional[len(positional) - len(args.defaults) :], args.defaults)
            )
        for arg, default in zip(args.kwonlyargs, args.kw_defaults):
            if default is not None:
                paired.append((arg, default))
        for arg, default in paired:
            param_id = self.node_id(arg)
            if not param_id:
                continue
            for src in self.sources(default):
                self.emit(
                    LineageKind.PARAMETER_BINDING, src, param_id, note="default argument"
                )

    def _class(self, node: ast.ClassDef) -> None:
        def_id = self.node_id(node)
        if def_id:
            self.bind_env(def_id)
        scope_key, element_id = self.mod.scope_of_node.get(id(node), ("", def_id))
        self.scopes.append(
            _WalkScope(
                key=scope_key, kind="class", element_id=element_id, class_id=element_id
            )
        )
        self.block(node.body)
        self.scopes.pop()

    # -- assignment --------------------------------------------------------

    def assign(
        self, target: ast.expr, srcs: tuple[_Src, ...], value: ast.expr | None
    ) -> None:
        pending = self._pending.pop(id(value), None) if value is not None else None
        if pending is not None and isinstance(target, (ast.Name, ast.Attribute)):
            self._assign_container_literal(target, pending, value)
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            self._unpack(target, srcs, value)
            return
        if isinstance(target, ast.Starred):
            self.assign(
                target.value,
                _retag(
                    srcs, Confidence.PROBABLE,
                    f"{_OVER}starred unpacking keeps no position",
                ),
                value,
            )
            return
        if isinstance(target, ast.Attribute):
            self._assign_attribute(target, srcs, value)
            return
        if isinstance(target, ast.Subscript):
            self._assign_subscript(target, srcs, value)
            return
        if not isinstance(target, ast.Name):
            return
        target_id = self.node_id(target)
        if not target_id:
            return
        self._classify_target(target_id, target.id, value, srcs)
        self.bind_env(target_id)
        if isinstance(value, ast.Name) and srcs:
            self.t._alias_of[target_id] = tuple(sorted(src.id for src in srcs))
        for src in srcs:
            kind = src.kind or LineageKind.ASSIGNS
            self.emit(kind, src, target_id)

    def _assign_container_literal(
        self, target: ast.Name | ast.Attribute,
        pending: Sequence[tuple[str, tuple[_Src, ...]]], value: ast.expr,
    ) -> None:
        """`d = {"k": v}` writes the key, not a blob called `d`."""
        if isinstance(target, ast.Name):
            target_id = self.node_id(target)
            if not target_id:
                return
            self._classify_target(target_id, target.id, value, ())
            self.bind_env(target_id)
            containers = (target_id,)
        else:
            containers = self._attribute_nodes(target, writing=True)
        for container_id in containers:
            self._write_pending(container_id, pending, "")

    def _unpack(
        self, target: ast.Tuple | ast.List, srcs: tuple[_Src, ...], value: ast.expr | None
    ) -> None:
        elements = list(target.elts)
        starred = any(isinstance(element, ast.Starred) for element in elements)
        if (
            isinstance(value, (ast.Tuple, ast.List))
            and not starred
            and len(value.elts) == len(elements)
        ):
            for element, sub_value in zip(elements, value.elts):
                self.assign(element, self.sources(sub_value), sub_value)
            return
        spread = _retag(
            srcs, Confidence.PROBABLE, f"{_OVER}unpacking does not key by position here"
        )
        for element in elements:
            self.assign(element, spread, None)

    def _assign_attribute(
        self, target: ast.Attribute, srcs: tuple[_Src, ...], value: ast.expr | None
    ) -> None:
        for attr_node in self._attribute_nodes(target, writing=True):
            values = srcs or self._literal_origin(value)
            for src in values:
                self.emit(
                    LineageKind.ATTRIBUTE_WRITE, src, attr_node,
                    note=f"attribute {target.attr!r} written here",
                )

    def _assign_subscript(
        self, target: ast.Subscript, srcs: tuple[_Src, ...], value: ast.expr | None
    ) -> None:
        containers, key = self._subscript_parts(target)
        if key is None:
            for base in containers:
                for src in srcs:
                    self.emit(
                        LineageKind.CONTAINER_WRITE, src, base.id,
                        extra=combine(base.confidence, Confidence.HEURISTIC),
                        note=f"{_OVER}container write with a key that is not a literal",
                    )
            return
        values = srcs or self._literal_origin(value)
        for base in containers:
            frame = self._is_frame(base.id)
            node_id = self._key_node(base.id, key, frame)
            first = node_id not in self.t._established
            self.t._established.add(node_id)
            if frame:
                kind = LineageKind.COLUMN_WRITE
            else:
                kind = LineageKind.CONTAINER_WRITE if first else LineageKind.MUTATES
            label = "column" if frame else "key"
            for src in values:
                self.emit(
                    kind, src, node_id,
                    extra=Confidence.PROBABLE if frame else None,
                    note=f"named {label} {key!r}",
                )

    def _literal_origin(self, value: ast.expr | None) -> tuple[_Src, ...]:
        """A literal written into a structure originates at the enclosing element.

        `self.mode = "off"` has no variable behind it, but the write is real and
        the method is where the value comes from. A literal into a plain local
        emits nothing: that local's own element already says where it is.
        """
        if value is None or not _is_literal_expr(value):
            return ()
        return (
            _Src(self.element_id, Confidence.RESOLVED, "literal written at this site"),
        )

    def _classify_target(
        self, target_id: str, name: str, value: ast.expr | None, srcs: tuple[_Src, ...]
    ) -> None:
        if _looks_like_frame(name) or (
            isinstance(value, ast.Call) and _is_frame_producer(value)
        ):
            self.t._frame_defs.add(target_id)
        for src in srcs:
            if src.id.endswith(_INSTANCE):
                self.t._instance_of[target_id] = src.id[: -len(_INSTANCE)]
            if src.id in self.t._frame_defs:
                self.t._frame_defs.add(target_id)

    # -- container and attribute nodes -------------------------------------

    def _alias_roots(self, node_id: str) -> tuple[str, ...]:
        """Follow ``b = a`` to the binding that actually holds the object.

        Without this, ``alias["count"] = 0`` writes a node nobody reads and the
        closure's counter looks as if it is never reset.
        """
        seen: set[str] = set()
        roots: set[str] = set()
        stack = [node_id]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            parents = self.t._alias_of.get(current)
            if parents:
                stack.extend(parents)
            else:
                roots.add(current)
        return tuple(sorted(roots))

    def _container_sources(self, node: ast.expr) -> tuple[_Src, ...]:
        out: list[_Src] = []
        for src in self.sources(node):
            for root in self._alias_roots(src.id):
                out.append(_Src(root, src.confidence, src.note, src.kind))
        return _merge_srcs(out)

    def _subscript_parts(
        self, node: ast.Subscript
    ) -> tuple[tuple[_Src, ...], str | None]:
        base = node.value
        key = _literal_key(node.slice)
        if key is None and isinstance(base, ast.Attribute) and base.attr in (
            "loc", "iloc", "at", "iat"
        ):
            key = _literal_key_from_index(node.slice)
            base = base.value
        containers = self._container_sources(base)
        held = tuple(src for src in containers if src.kind is not LineageKind.READS)
        return (held or containers), key

    def _key_node(self, container_id: str, key: str, frame: bool) -> str:
        """A column, or a config-declared feature, is global; any other key is
        scoped to its container."""
        if frame or key in self.t._declared_features:
            node_id = self.t.note_feature(key)
        else:
            node_id = key_id(container_id, key)
            self.t._key_nodes.add(node_id)
        self.t._container_keys.setdefault(container_id, set()).add(node_id)
        return node_id

    def _is_frame(self, node_id: str) -> bool:
        return node_id in self.t._frame_defs

    def _is_container_node(self, node_id: str) -> bool:
        return node_id in self.t._key_nodes or node_id.startswith("@feature:")

    def _is_parameter(self, node_id: str) -> bool:
        meta = self.mod.def_meta.get(node_id)
        return meta is not None and meta.kind == "parameter"

    def _attribute_nodes(self, node: ast.Attribute, *, writing: bool) -> tuple[str, ...]:
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            class_id = self.scope.class_id
            if class_id:
                return (attr_id(class_id, node.attr),)
        out: list[str] = []
        for base in self._container_sources(node.value):
            # `settings.threshold` written in a function is not the same node as
            # `Settings.threshold` written in __init__: the corpus keeps them
            # apart, and merging them makes every instance look like every other.
            out.append(attr_id(base.id, node.attr))
        return tuple(sorted(set(out)))

    # -- reads -------------------------------------------------------------

    @property
    def reader_id(self) -> str:
        """The element a read is attributed to.

        A lambda or a comprehension has no element of its own in card 1's
        inventory, so a read inside one is attributed to the function that
        contains it rather than to an ID nothing resolves.
        """
        for scope in reversed(self.scopes):
            if scope.kind not in ("lambda", "comprehension"):
                return scope.element_id
        return self.mod.module

    def _emit_reads(self, srcs: Iterable[_Src], why: str) -> None:
        for src in srcs:
            self.emit(LineageKind.READS, src, self.reader_id, note=why)

    def scope_chain(self) -> list[_WalkScope]:
        chain = list(reversed(self.scopes))
        if len(chain) > 1 and chain[0].kind in ("function", "lambda", "comprehension"):
            chain = [chain[0]] + [s for s in chain[1:] if s.kind != "class"]
        return chain

    def read_name(self, name: str) -> tuple[_Src, ...]:
        chain = self.scope_chain()
        for index, scope in enumerate(chain):
            key = (scope.key, name)
            local = self.env.get(key)
            if local:
                if index == 0:
                    if len(local) == 1:
                        return (_Src(local[0], Confidence.RESOLVED),)
                    merged = f"{_OVER}reaching definitions merged at a branch"
                    return tuple(
                        _Src(node_id, Confidence.PROBABLE, merged)
                        for node_id in sorted(local)
                    )
                every = tuple(sorted(set(local) | set(self.mod.all_defs.get(key, ()))))
                note = (
                    f"{_OVER}closure or global capture: "
                    "every definition in the enclosing scope"
                )
                if len(every) == 1:
                    return (_Src(every[0], Confidence.RESOLVED),)
                return tuple(_Src(node_id, Confidence.PROBABLE, note) for node_id in every)
            defs = self.mod.all_defs.get(key)
            if defs:
                if len(defs) == 1:
                    return (_Src(defs[0], Confidence.RESOLVED),)
                note = f"{_OVER}every definition of {name!r} in scope"
                return tuple(
                    _Src(node_id, Confidence.PROBABLE, note) for node_id in sorted(defs)
                )
        if name in BUILTIN_NAMES:
            return ()
        self.t.unresolved.append(
            Unresolved(
                id=f"@lineage-name:{self.element_id}:{name}",
                reason=UnresolvedReason.MISSING_TARGET,
                span=self.span,
                description=(
                    f"read of {name!r} in {self.element_id}: no binding found in any scope"
                ),
            )
        )
        return ()

    def read_attribute(self, node: ast.Attribute) -> tuple[_Src, ...]:
        if isinstance(node.value, ast.Name):
            imported = self._import_def(node.value.id)
            if imported is not None and node.value.id != "self":
                resolved = self._module_member(imported, node.attr)
                if resolved:
                    return (_Src(resolved, Confidence.RESOLVED, "imported module member"),)
                return ()
        return tuple(
            _Src(node_id, Confidence.PROBABLE, f"attribute {node.attr!r}")
            for node_id in self._attribute_nodes(node, writing=False)
        )

    def read_subscript(self, node: ast.Subscript) -> tuple[_Src, ...]:
        containers, key = self._subscript_parts(node)
        if key is not None:
            through_call = isinstance(node.value, ast.Call) or (
                isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Call)
            )
            out = []
            for base in containers:
                frame = self._is_frame(base.id)
                node_id = self._key_node(base.id, key, frame)
                label = "column" if frame else "key"
                out.append(
                    _Src(
                        node_id,
                        Confidence.PROBABLE if frame else Confidence.RESOLVED,
                        f"named {label} {key!r}",
                        LineageKind.READS if through_call else None,
                    )
                )
            # a key read out of a frame operation carries that operation's own
            # key reads with it: `g.groupby("symbol")["spread"]` reads both
            for src in self._container_sources(node.value):
                if src.kind is LineageKind.READS:
                    out.append(src)
            return _merge_srcs(out)
        self.sources(node.slice)
        out = []
        for base in containers:
            out.append(
                _Src(
                    base.id,
                    combine(base.confidence, Confidence.HEURISTIC),
                    f"{_OVER}container read with a key that is not a literal",
                )
            )
            for member in sorted(self.t._container_keys.get(base.id, ())):
                out.append(
                    _Src(
                        member, Confidence.HEURISTIC,
                        f"{_OVER}any known key of this container",
                    )
                )
        return _merge_srcs(out)

    def _import_def(self, name: str) -> _Def | None:
        for scope in self.scope_chain():
            key = (scope.key, name)
            ids = self.env.get(key) or tuple(self.mod.all_defs.get(key, ()))
            for node_id in ids:
                meta = self.mod.def_meta.get(node_id)
                if meta is not None and meta.kind == "import":
                    return meta
        return None

    def _module_member(self, imported: _Def, name: str) -> str:
        target = self.t._modules.get(imported.external_module.lstrip("."))
        if target is None:
            return ""
        ids = target.all_defs.get(("", name))
        return ids[0] if ids else ""

    # -- expressions -------------------------------------------------------

    def sources(self, node: ast.expr | None) -> tuple[_Src, ...]:
        if node is None or isinstance(node, ast.Constant):
            return ()
        if isinstance(node, ast.Name):
            return self.read_name(node.id)
        if isinstance(node, ast.Attribute):
            return self.read_attribute(node)
        if isinstance(node, ast.Subscript):
            return self.read_subscript(node)
        if isinstance(node, ast.Call):
            return self.call(node)
        if isinstance(node, ast.NamedExpr):
            target_id = self.node_id(node.target)
            srcs = self.sources(node.value)
            if target_id:
                self.bind_env(target_id)
                for src in srcs:
                    self.emit(
                        LineageKind.ASSIGNS, src, target_id, note="walrus assignment"
                    )
                return (_Src(target_id, Confidence.RESOLVED),)
            return srcs
        if isinstance(node, ast.BinOp):
            return _merge_srcs(self.sources(node.left), self.sources(node.right))
        if isinstance(node, ast.UnaryOp):
            return self.sources(node.operand)
        if isinstance(node, ast.BoolOp):
            return _merge_srcs(*[self.sources(value) for value in node.values])
        if isinstance(node, ast.Compare):
            return _merge_srcs(
                self.sources(node.left), *[self.sources(c) for c in node.comparators]
            )
        if isinstance(node, ast.IfExp):
            self._emit_reads(self.sources(node.test), "conditional expression")
            return _merge_srcs(self.sources(node.body), self.sources(node.orelse))
        if isinstance(node, ast.Dict):
            return self._dict_literal(node)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return _merge_srcs(*[self.sources(element) for element in node.elts])
        if isinstance(node, ast.Starred):
            return self.sources(node.value)
        if isinstance(node, ast.JoinedStr):
            return _merge_srcs(*[self.sources(value) for value in node.values])
        if isinstance(node, ast.FormattedValue):
            return self.sources(node.value)
        if isinstance(node, (ast.Await, ast.Yield, ast.YieldFrom)):
            return self.sources(node.value)  # type: ignore[arg-type]
        if isinstance(node, ast.Lambda):
            return self._lambda(node, ())
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            return self._comprehension(node)
        if isinstance(node, ast.Slice):
            return _merge_srcs(
                self.sources(node.lower), self.sources(node.upper), self.sources(node.step)
            )
        collected: list[tuple[_Src, ...]] = []
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                collected.append(self.sources(child))
        return _merge_srcs(*collected)

    def _dict_literal(self, node: ast.Dict) -> tuple[_Src, ...]:
        """A dict literal's keys are nodes, not part of one blob.

        The container they will be bound to is not known until the assignment,
        so the keys are returned as pending writes the assignment completes.
        """
        pending: list[tuple[str, tuple[_Src, ...]]] = []
        loose: list[_Src] = []
        for key, value in zip(node.keys, node.values):
            value_srcs = self.sources(value)
            name = _literal_key(key) if key is not None else None
            if name is None:
                loose.extend(
                    _retag(
                        value_srcs, Confidence.HEURISTIC,
                        f"{_OVER}dict entry with a key that is not a literal",
                    )
                )
                continue
            pending.append((name, value_srcs or self._literal_origin(value)))
            loose.extend(value_srcs)
        self._pending[id(node)] = pending
        return _merge_srcs(loose)

    def _comprehension(self, node: ast.expr) -> tuple[_Src, ...]:
        scope_key, element_id = self.mod.scope_of_node.get(
            id(node), (self.scope.key, self.element_id)
        )
        self.scopes.append(
            _WalkScope(
                key=scope_key, kind="comprehension", element_id=element_id,
                class_id=self.scope.class_id,
            )
        )
        try:
            for generator in node.generators:  # type: ignore[attr-defined]
                iterable = self.sources(generator.iter)
                self.assign(
                    generator.target,
                    _retag(iterable, Confidence.PROBABLE, "element of iterable"),
                    generator.iter,
                )
                for condition in generator.ifs:
                    self._emit_reads(self.sources(condition), "comprehension filter")
            if isinstance(node, ast.DictComp):
                return _merge_srcs(self.sources(node.key), self.sources(node.value))
            return self.sources(node.elt)  # type: ignore[attr-defined]
        finally:
            self.scopes.pop()

    def _lambda(self, node: ast.Lambda, bound: tuple[_Src, ...]) -> tuple[_Src, ...]:
        """Lambdas are inlined: the body's sources are the call's result.

        A lambda has no element of its own in card 1's inventory, so inventing a
        node for it would put an ID in a slice that resolves to nothing.
        """
        scope_key, element_id = self.mod.scope_of_node.get(id(node), ("", ""))
        if not scope_key:
            return ()
        saved = self.env
        self.env = dict(self.env)
        self.scopes.append(
            _WalkScope(
                key=scope_key, kind="lambda", element_id=element_id,
                class_id=self.scope.class_id,
            )
        )
        self._seed_params(node.args)
        params = _all_args(node.args)
        if bound and params:
            param_id = self.node_id(params[0])
            for src in bound:
                self.emit(
                    LineageKind.PARAMETER_BINDING, src, param_id,
                    note="value bound to a lambda parameter",
                )
        result = _retag(self.sources(node.body), Confidence.PROBABLE, "lambda result")
        self.scopes.pop()
        self.env = saved
        return result

    # -- calls -------------------------------------------------------------

    def call(self, node: ast.Call) -> tuple[_Src, ...]:
        func = node.func
        name = _dotted(func)
        base_name = name.split(".")[0] if name else ""
        attr = func.attr if isinstance(func, ast.Attribute) else ""

        # 0. super() is the same instance; which method it reaches is card 2's
        #    MRO dispatch, consumed at step 4.
        if name == "super" and not node.args:
            return _retag(
                self.read_name("self"), Confidence.PROBABLE, "super(): the same instance"
            )

        # 1. reflection and runtime code construction -> explicit barrier
        if name in BARRIER_BUILTINS or base_name in ("importlib", "pickle", "marshal"):
            return self._barrier_call(
                node, UnresolvedReason.DYNAMIC_NAME,
                _barrier_reason(name or "a dynamic call"),
            )
        if name in ("getattr", "setattr"):
            return self._reflective_attr(node, name)

        # 2. dataframe-shaped operations, where columns are first-class nodes
        frame = self._dataframe_call(node)
        if frame is not None:
            return frame

        # 3. in-place container mutation
        if attr in MUTATING_METHODS and isinstance(func, ast.Attribute):
            mutated = self._mutation(node, func)
            if mutated is not None:
                return mutated

        # 4. calls we can resolve to a definition we parsed
        resolved = self._resolve_callee(node)
        if resolved is not None:
            callee_id, confidence, method, note = resolved
            return self._bind_call(node, callee_id, confidence, method, note)

        # 5. transparent builtins and pure stdlib
        if name in TRANSPARENT_BUILTINS and self._import_def(base_name) is None:
            return _retag(
                self._arg_sources(node), Confidence.PROBABLE, f"result of {name}()"
            )
        imported = self._import_def(base_name) if base_name else None
        if imported is not None and self._is_transparent_module(imported):
            return _retag(
                self._arg_sources(node), Confidence.PROBABLE,
                f"result of {name}(), a value-transparent stdlib call",
            )

        # 6. a method on a value whose type we do not know: the result derives
        #    from the receiver, which is true of almost every method -- an
        #    over-approximation, labelled, rather than a barrier on every `.x()`.
        if isinstance(func, ast.Attribute) and imported is None:
            receiver = self.sources(func.value)
            if receiver:
                return _retag(
                    _merge_srcs(receiver, self._arg_sources(node)),
                    Confidence.PROBABLE,
                    f"{_OVER}result of .{attr}() on a value of unknown type",
                )

        # 7. anything else is opaque: an explicit barrier, never stitched across
        reason = (
            UnresolvedReason.THIRD_PARTY
            if imported is not None
            else UnresolvedReason.MISSING_TARGET
        )
        description = (
            f"opaque call to {name or 'a computed callable'}"
            if imported is None
            else f"opaque third-party call to {name} (from {imported.external_module})"
        )
        return self._barrier_call(node, reason, description)

    def _is_transparent_module(self, imported: _Def) -> bool:
        module = imported.external_module.lstrip(".")
        return (
            module in self.t.transparent_modules
            or module.split(".")[0] in self.t.transparent_modules
        )

    def _arg_sources(self, node: ast.Call) -> tuple[_Src, ...]:
        groups = [self.sources(arg) for arg in node.args]
        groups.extend(self.sources(kw.value) for kw in node.keywords)
        return _merge_srcs(*groups)

    def _barrier_call(
        self, node: ast.Call, reason: UnresolvedReason, description: str
    ) -> tuple[_Src, ...]:
        barrier_id = self.t.add_barrier(
            self.element_id, self.node_span(node), reason, description
        )
        for src in self._arg_sources(node):
            self.t.add_edge(
                LineageKind.READS, src.id, barrier_id, self.span, Method.DATAFLOW,
                Confidence.RESOLVED, "value reaches a barrier and is not followed past it",
            )
        if isinstance(node.func, ast.Attribute):
            for base in self.sources(node.func.value):
                self.t.add_edge(
                    LineageKind.READS, base.id, barrier_id, self.span, Method.DATAFLOW,
                    Confidence.RESOLVED,
                    "receiver reaches a barrier and is not followed past it",
                )
        return (
            _Src(
                barrier_id, Confidence.UNKNOWN,
                f"produced past a barrier: {description}",
            ),
        )

    def _reflective_attr(self, node: ast.Call, name: str) -> tuple[_Src, ...]:
        literal = _literal_key(node.args[1]) if len(node.args) > 1 else None
        if literal is None:
            return self._barrier_call(
                node, UnresolvedReason.DYNAMIC_NAME,
                f"{name} with a name that is not a literal",
            )
        bases = self._container_sources(node.args[0]) if node.args else ()
        if name == "setattr":
            values = self.sources(node.args[2]) if len(node.args) > 2 else ()
            for base in bases:
                node_id = attr_id(base.id, literal)
                for src in values:
                    self.t.add_edge(
                        LineageKind.ATTRIBUTE_WRITE, src.id, node_id, self.span,
                        Method.GETATTR_LITERAL,
                        combine(src.confidence, Confidence.PROBABLE),
                        f"setattr with the literal name {literal!r}",
                    )
            return ()
        return tuple(
            sorted(
                _Src(
                    attr_id(base.id, literal),
                    combine(base.confidence, Confidence.PROBABLE),
                    f"getattr with the literal name {literal!r}",
                )
                for base in bases
            )
        )

    def _mutation(self, node: ast.Call, func: ast.Attribute) -> tuple[_Src, ...] | None:
        receivers = self._container_sources(func.value)
        if not receivers:
            return None
        if func.attr == "update" and node.args and isinstance(node.args[0], ast.Dict):
            self.sources(node.args[0])
            pending = self._pending.pop(id(node.args[0]), [])
            for receiver in receivers:
                self._write_pending(receiver.id, pending, "written by update()")
            return ()
        if func.attr == "setdefault" and node.args:
            key = _literal_key(node.args[0])
            if key is not None:
                literal = (
                    self._literal_origin(node.args[1]) if len(node.args) > 1 else ()
                )
                values = (
                    self.sources(node.args[1]) if len(node.args) > 1 else ()
                ) or literal
                out = []
                for receiver in receivers:
                    out.extend(
                        self._write_pending(
                            receiver.id, [(key, values)], "written by setdefault()",
                        )
                    )
                return _merge_srcs(out)
        values = self._arg_sources(node)
        for receiver in receivers:
            for src in values:
                self.emit(
                    LineageKind.MUTATES, src, receiver.id,
                    extra=receiver.confidence, note=f"in-place {func.attr}()",
                )
            if self._is_parameter(receiver.id):
                self.t._mutated_params.add(receiver.id)
        return ()

    def _write_pending(
        self, container_id: str, pending: Sequence[tuple[str, tuple[_Src, ...]]],
        note: str,
    ) -> tuple[_Src, ...]:
        """Complete the writes a dict literal or update() promised."""
        out: list[_Src] = []
        frame = self._is_frame(container_id)
        for key, values in pending:
            node_id = self._key_node(container_id, key, frame)
            first = node_id not in self.t._established
            self.t._established.add(node_id)
            if frame:
                kind = LineageKind.COLUMN_WRITE
            else:
                kind = LineageKind.CONTAINER_WRITE if first else LineageKind.MUTATES
            for src in values:
                self.emit(kind, src, node_id, note=f"named key {key!r} {note}".strip())
            out.append(_Src(node_id, Confidence.RESOLVED, f"named key {key!r}"))
        return tuple(out)

    # -- dataframe-shaped operations ---------------------------------------

    def _dataframe_call(self, node: ast.Call) -> tuple[_Src, ...] | None:
        func = node.func
        name = _dotted(func)
        base = name.split(".")[0] if name else ""

        if isinstance(func, ast.Attribute) and base:
            imported = self._import_def(base)
            if imported is not None and (
                imported.external_module.split(".")[0] in PANDAS_MODULES
                or base in PANDAS_MODULES
            ):
                return self._pandas_module_call(node, func.attr)
        if not isinstance(func, ast.Attribute):
            return None
        attr = func.attr
        if attr not in COLUMN_METHODS and attr not in FRAME_METHODS:
            return None
        receivers = self._container_sources(func.value)
        if not receivers:
            return None
        frame_like = any(
            self._is_frame(receiver.id) or receiver.id.startswith("@feature:")
            for receiver in receivers
        ) or _looks_like_frame(_last_name(func.value))
        confidence = Confidence.PROBABLE
        note_suffix = (
            "" if frame_like else f" ({_OVER}receiver assumed frame-shaped by method name)"
        )

        if attr == "assign":
            out = list(
                _retag(receivers, confidence, f"frame carried through .assign(){note_suffix}")
            )
            for keyword in node.keywords:
                if keyword.arg is None:
                    out.extend(
                        _retag(
                            self.sources(keyword.value), Confidence.HEURISTIC,
                            f"{_OVER}.assign(**mapping) does not name its columns",
                        )
                    )
                    continue
                column = self.t.note_feature(keyword.arg)
                self.t._established.add(column)
                for src in self.sources(keyword.value):
                    self.emit(
                        LineageKind.COLUMN_WRITE, src, column,
                        note=f"column {keyword.arg!r} written by .assign()",
                    )
                out.append(
                    _Src(column, Confidence.RESOLVED, f"column {keyword.arg!r} of the result")
                )
            return _merge_srcs(out)
        if attr in ("merge", "join"):
            other = self._container_sources(node.args[0]) if node.args else ()
            keys = self._named_columns(node, ("on", "left_on", "right_on"))
            out = list(_retag(receivers, confidence, "left frame of a merge"))
            out.extend(_retag(other, confidence, "right frame of a merge"))
            for column in keys:
                out.append(
                    _Src(column, Confidence.PROBABLE, "join key", LineageKind.READS)
                )
            return _merge_srcs(out)
        if attr == "groupby":
            keys = self._named_columns(node, ("by",), positional=0)
            out = list(_retag(receivers, confidence, "frame grouped"))
            for column in keys:
                out.append(
                    _Src(column, Confidence.PROBABLE, "group key", LineageKind.READS)
                )
            return _merge_srcs(out)
        if attr == "rename":
            mapping = self._keyword(node, "columns")
            if isinstance(mapping, ast.Dict):
                out = list(
                    _retag(receivers, confidence, "frame carried through .rename()")
                )
                for key, value in zip(mapping.keys, mapping.values):
                    old = _literal_key(key) if key else None
                    new = _literal_key(value)
                    if old is None or new is None:
                        continue
                    new_id = self.t.note_feature(new)
                    self.t._established.add(new_id)
                    self.emit(
                        LineageKind.COLUMN_WRITE,
                        _Src(self.t.note_feature(old), Confidence.RESOLVED), new_id,
                        note=f"column {old!r} renamed to {new!r}",
                    )
                    out.append(_Src(new_id, Confidence.RESOLVED, "renamed column"))
                return _merge_srcs(out)
            return _retag(
                receivers, Confidence.HEURISTIC,
                f"{_OVER}.rename() mapping is not a literal",
            )
        if attr == "drop":
            for column in self._named_columns(node, ("columns", "labels"), positional=0):
                self.emit(
                    LineageKind.READS, _Src(column, Confidence.PROBABLE), self.element_id,
                    note="column dropped from the frame",
                )
            return _retag(receivers, confidence, "frame carried through .drop()")
        if attr == "apply":
            return self._apply(node, receivers, confidence)
        return _retag(
            receivers, confidence, f"frame carried through .{attr}(){note_suffix}"
        )

    def _pandas_module_call(self, node: ast.Call, attr: str) -> tuple[_Src, ...]:
        if attr in ("DataFrame", "Series"):
            out: list[_Src] = []
            for arg in node.args:
                out.extend(
                    _retag(self.sources(arg), Confidence.PROBABLE, f"pandas {attr}()")
                )
            for keyword in node.keywords:
                out.extend(
                    _retag(
                        self.sources(keyword.value), Confidence.PROBABLE,
                        f"pandas {attr}()",
                    )
                )
            return _merge_srcs(out)
        if attr in ("merge", "concat"):
            out = []
            for arg in node.args:
                if isinstance(arg, (ast.List, ast.Tuple)):
                    for element in arg.elts:
                        out.extend(
                            _retag(
                                self.sources(element), Confidence.PROBABLE,
                                f"pandas {attr}()",
                            )
                        )
                else:
                    out.extend(
                        _retag(self.sources(arg), Confidence.PROBABLE, f"pandas {attr}()")
                    )
            for column in self._named_columns(node, ("on", "left_on", "right_on")):
                out.append(
                    _Src(column, Confidence.PROBABLE, "join key", LineageKind.READS)
                )
            return _merge_srcs(out)
        if attr.startswith("read_"):
            return _retag(
                self._arg_sources(node), Confidence.PROBABLE,
                f"{_OVER}frame loaded by pandas.{attr}() from outside the program",
            )
        return self._barrier_call(
            node, UnresolvedReason.THIRD_PARTY, f"opaque third-party call to pandas.{attr}"
        )

    def _apply(
        self, node: ast.Call, receivers: tuple[_Src, ...], confidence: Confidence
    ) -> tuple[_Src, ...]:
        carried = _retag(receivers, confidence, "value carried through .apply()")
        if not node.args:
            return carried
        applied = node.args[0]
        if isinstance(applied, ast.Lambda):
            return _merge_srcs(carried, self._lambda(applied, receivers))
        resolved = self._resolve_name_to_callee(applied)
        if resolved is not None:
            callee_id, callee_conf = resolved
            found = self.t._function_node(callee_id)
            if found is not None:
                params = _all_args(found[0].args)  # type: ignore[union-attr]
                if params:
                    param_id = found[1].id_of_node.get(id(params[0]), "")
                    for receiver in receivers:
                        self.emit(
                            LineageKind.PARAMETER_BINDING, receiver, param_id,
                            extra=combine(callee_conf, Confidence.PROBABLE),
                            note="value bound by .apply()",
                        )
                return _merge_srcs(
                    carried,
                    (
                        _Src(
                            callee_id, combine(callee_conf, Confidence.PROBABLE),
                            "result of .apply()",
                        ),
                    ),
                )
        return _merge_srcs(
            carried,
            self._barrier_call(
                node, UnresolvedReason.DYNAMIC_NAME,
                "apply() with a callable this analysis cannot resolve",
            ),
        )

    def _keyword(self, node: ast.Call, name: str) -> ast.expr | None:
        for keyword in node.keywords:
            if keyword.arg == name:
                return keyword.value
        return None

    def _named_columns(
        self, node: ast.Call, names: Sequence[str], positional: int | None = None
    ) -> tuple[str, ...]:
        found: list[str] = []
        candidates: list[ast.expr] = []
        for name in names:
            value = self._keyword(node, name)
            if value is not None:
                candidates.append(value)
        if positional is not None and len(node.args) > positional:
            candidates.append(node.args[positional])
        for candidate in candidates:
            literal = _literal_key(candidate)
            if literal is not None:
                found.append(self.t.note_feature(literal))
                continue
            if isinstance(candidate, (ast.List, ast.Tuple)):
                for element in candidate.elts:
                    literal = _literal_key(element)
                    if literal is not None:
                        found.append(self.t.note_feature(literal))
        return tuple(sorted(set(found)))

    # -- callee resolution and parameter binding ---------------------------

    def _resolve_callee(
        self, node: ast.Call
    ) -> tuple[str, Confidence, Method, str] | None:
        """Card 2's edge first, then our own scope lookup. Never a guess."""
        recorded = self.t._call_targets.get(
            (self.reader_id, getattr(node, "lineno", 0))
        )
        if recorded:
            known = [
                (tid, conf) for tid, conf in recorded if self.t._is_known_callee(tid)
            ]
            if len(known) == 1:
                return (known[0][0], known[0][1], Method.DATAFLOW, "call edge from card 2")
            if len(known) > 1:
                return (
                    known[0][0], combine(known[0][1], Confidence.PROBABLE),
                    Method.DATAFLOW,
                    f"{_OVER}card 2 reports {len(known)} possible callees here",
                )
        func = node.func
        if isinstance(func, ast.Name):
            resolved = self._resolve_name_to_callee(func)
            if resolved is not None:
                return (resolved[0], resolved[1], Method.DATAFLOW, "")
            return None
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id == "self" and self.scope.class_id:
                candidate = attr_id(self.scope.class_id, func.attr)
                if self.t._is_known_callee(candidate):
                    return (
                        candidate, Confidence.PROBABLE, Method.DATAFLOW,
                        "method on self",
                    )
                return None
            imported = self._import_def(func.value.id)
            if imported is not None:
                module = imported.external_module.lstrip(".")
                if module in self.t._modules:
                    candidate = make_id(module, func.attr)
                    if self.t._is_known_callee(candidate):
                        return (
                            candidate, Confidence.RESOLVED, Method.DATAFLOW,
                            "call into an imported module",
                        )
                return None
            for src in self._container_sources(func.value):
                class_id = self.t._instance_of.get(src.id, "")
                if not class_id and src.id.endswith(_INSTANCE):
                    class_id = src.id[: -len(_INSTANCE)]
                if class_id:
                    candidate = attr_id(class_id, func.attr)
                    if self.t._is_known_callee(candidate):
                        return (
                            candidate, Confidence.PROBABLE, Method.DATAFLOW,
                            "method on a locally constructed instance",
                        )
        return None

    def _resolve_name_to_callee(self, func: ast.expr) -> tuple[str, Confidence] | None:
        if not isinstance(func, ast.Name):
            return None
        for scope in self.scope_chain():
            key = (scope.key, func.id)
            ids = self.env.get(key) or tuple(self.mod.all_defs.get(key, ()))
            for node_id in ids:
                meta = self.mod.def_meta.get(node_id)
                if meta is None:
                    continue
                if meta.kind in ("function", "class") and self.t._is_known_callee(node_id):
                    return (
                        node_id,
                        Confidence.RESOLVED if len(ids) == 1 else Confidence.PROBABLE,
                    )
                if meta.kind == "import":
                    module = meta.external_module.lstrip(".")
                    if module in self.t._modules:
                        candidate = make_id(module, meta.imported_name or meta.name)
                        if self.t._is_known_callee(candidate):
                            return (candidate, Confidence.RESOLVED)
                    return None
        return None

    def _bind_call(
        self, node: ast.Call, callee_id: str, confidence: Confidence, method: Method,
        note: str,
    ) -> tuple[_Src, ...]:
        found = self.t._function_node(callee_id)
        if found is None:
            if self.t._is_class(callee_id):
                return self._bind_constructor(node, callee_id, confidence, method, note)
            return self._barrier_call(
                node, UnresolvedReason.MISSING_TARGET,
                f"call to {callee_id}, whose definition was not parsed",
            )
        func_node, info = found
        skip = 0
        args = func_node.args  # type: ignore[union-attr]
        leading = [*args.posonlyargs, *args.args][:1]
        if isinstance(node.func, ast.Attribute) and leading and leading[0].arg in (
            "self", "cls"
        ):
            skip = 1
            param_id = info.id_of_node.get(id(leading[0]), "")
            for src in self._container_sources(node.func.value):
                self.emit(
                    LineageKind.PARAMETER_BINDING, src, param_id, method=method,
                    extra=confidence, note=f"receiver bound to {leading[0].arg}",
                )
        self._bind_arguments(node, func_node, info, callee_id, confidence, method, note, skip)
        return (
            _Src(
                callee_id, combine(confidence, Confidence.RESOLVED),
                note or "return value", LineageKind.RETURNS,
            ),
        )

    def _bind_constructor(
        self, node: ast.Call, class_id: str, confidence: Confidence, method: Method,
        note: str,
    ) -> tuple[_Src, ...]:
        """``C(...)`` binds ``__init__``'s parameters and yields an instance node."""
        instance_id = f"{class_id}{_INSTANCE}"
        found = self.t._function_node(attr_id(class_id, "__init__"))
        if found is not None:
            args = found[0].args  # type: ignore[union-attr]
            leading = [*args.posonlyargs, *args.args][:1]
            if leading and leading[0].arg in ("self", "cls"):
                self.t.add_edge(
                    LineageKind.PARAMETER_BINDING, instance_id,
                    found[1].id_of_node.get(id(leading[0]), ""), self.span, method,
                    combine(confidence, Confidence.RESOLVED),
                    f"new instance bound to {leading[0].arg}",
                )
            self._bind_arguments(
                node, found[0], found[1], attr_id(class_id, "__init__"), confidence,
                method, note or "constructor argument", 1,
            )
        else:
            for src in self._arg_sources(node):
                self.emit(
                    LineageKind.PARAMETER_BINDING, src, instance_id, method=method,
                    extra=combine(confidence, Confidence.PROBABLE),
                    note=f"{_OVER}{class_id} defines no __init__ this analysis parsed",
                )
        return (_Src(instance_id, combine(confidence, Confidence.RESOLVED), ""),)

    def _bind_arguments(
        self, node: ast.Call, func_node: ast.AST, info: _ModuleInfo, callee_id: str,
        confidence: Confidence, method: Method, note: str, skip: int = 0,
    ) -> None:
        args = func_node.args  # type: ignore[union-attr]
        positional = [*args.posonlyargs, *args.args][skip:]
        by_name = {arg.arg: arg for arg in _all_args(args)[skip:]}
        bound: set[str] = set()

        def target_of(arg: ast.arg) -> str:
            param_id = info.id_of_node.get(id(arg), "")
            return self.t._param_elements.get((callee_id, arg.arg)) or param_id

        def bind(
            arg: ast.arg, srcs: tuple[_Src, ...], extra: Confidence, why: str
        ) -> None:
            target_id = target_of(arg)
            if not target_id:
                return
            bound.add(arg.arg)
            for src in srcs:
                self.t.add_edge(
                    LineageKind.PARAMETER_BINDING, src.id, target_id, self.span, method,
                    combine(src.confidence, confidence, extra), src.note or why,
                )
            self.t._param_bindings.append((target_id, srcs, self.span, self.element_id))

        index = 0
        for arg_node in node.args:
            if isinstance(arg_node, ast.Starred):
                spread = self.sources(arg_node.value)
                for arg in positional[index:]:
                    bind(
                        arg, spread, Confidence.HEURISTIC,
                        f"{_OVER}*args expansion does not key by position",
                    )
                index = len(positional)
                continue
            values = self.sources(arg_node) or self._literal_origin(arg_node)
            if index < len(positional):
                bind(positional[index], values, Confidence.RESOLVED, note)
            elif args.vararg is not None:
                bind(args.vararg, values, Confidence.RESOLVED, f"*{args.vararg.arg}")
            index += 1
        for keyword in node.keywords:
            if keyword.arg is None:
                self._bind_double_star(keyword.value, args, by_name, bound, bind, info)
                continue
            values = self.sources(keyword.value) or self._literal_origin(keyword.value)
            arg = by_name.get(keyword.arg)
            if arg is not None:
                bind(arg, values, Confidence.RESOLVED, f"keyword {keyword.arg!r}")
            elif args.kwarg is not None:
                # `offset=5` with no `offset` parameter lands inside **options
                # under that key -- the only path from the call site to it.
                kwarg_id = target_of(args.kwarg)
                node_id = self._key_node(kwarg_id, keyword.arg, False)
                self.t._established.add(node_id)
                for src in values:
                    self.t.add_edge(
                        LineageKind.PARAMETER_BINDING, src.id, node_id, self.span,
                        method, combine(src.confidence, confidence),
                        f"keyword {keyword.arg!r} inside **{args.kwarg.arg}",
                    )

    def _bind_double_star(
        self, mapping: ast.expr, args: ast.arguments, by_name: dict[str, ast.arg],
        bound: set[str], bind, info: _ModuleInfo,
    ) -> None:
        if isinstance(mapping, ast.Dict):
            for key, value in zip(mapping.keys, mapping.values):
                literal = _literal_key(key) if key is not None else None
                values = self.sources(value) or self._literal_origin(value)
                if literal is not None and literal in by_name:
                    bind(
                        by_name[literal], values, Confidence.RESOLVED,
                        f"**mapping with the literal key {literal!r}",
                    )
                elif args.kwarg is not None:
                    bind(
                        args.kwarg, values, Confidence.PROBABLE,
                        f"**mapping into **{args.kwarg.arg}",
                    )
            return
        spread = self.sources(mapping)
        if args.kwarg is not None:
            bind(
                args.kwarg, spread, Confidence.PROBABLE,
                f"**kwargs into **{args.kwarg.arg}",
            )
        for arg in _all_args(args):
            if arg.arg in bound or arg is args.kwarg or arg is args.vararg:
                continue
            bind(
                arg, spread, Confidence.HEURISTIC,
                f"{_OVER}**kwargs expansion does not name which parameter it binds",
            )


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _barrier_reason(name: str) -> str:
    if name == "eval":
        return (
            "eval() executes a string assembled at runtime; what it reads and writes "
            "cannot be determined statically, so value flow ends here"
        )
    return f"flow into {name}, which reaches code chosen at runtime"


def _is_literal_expr(node: ast.expr) -> bool:
    """True when the expression is built only from constants."""
    for child in ast.walk(node):
        if isinstance(child, (ast.Name, ast.Call, ast.Attribute, ast.Subscript)):
            return False
    return True


def _all_args(args: ast.arguments) -> list[ast.arg]:
    return [
        *args.posonlyargs,
        *args.args,
        *([args.vararg] if args.vararg else []),
        *args.kwonlyargs,
        *([args.kwarg] if args.kwarg else []),
    ]


def _literal_key(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal_key_from_index(node: ast.expr) -> str | None:
    if isinstance(node, ast.Tuple) and node.elts:
        return _literal_key(node.elts[-1])
    return _literal_key(node)


def _dotted(node: ast.expr) -> str:
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return ""


def _last_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _looks_like_frame(name: str) -> bool:
    if not name:
        return False
    lowered = name.lower()
    return lowered in _FRAME_NAMES or lowered.endswith(_FRAME_NAME_SUFFIXES)


def _is_frame_producer(node: ast.Call) -> bool:
    name = _dotted(node.func)
    if not name:
        return isinstance(node.func, ast.Attribute) and node.func.attr in COLUMN_METHODS
    tail = name.split(".")[-1]
    head = name.split(".")[0]
    constructors = ("DataFrame", "merge", "concat")
    if head in PANDAS_MODULES and (tail.startswith("read_") or tail in constructors):
        return True
    return tail in COLUMN_METHODS
