"""Card 4 -- data and feature lineage, and slicing.

Follows values along the cascade (raw input -> engineered data -> features ->
decision inputs) over the IDs cards 1 and 2 mint, and answers two queries:

* **backward slice** of a feature or decision input -- everything that produces it
* **forward slice** of any element -- everything a change to it can reach

Design decisions, all deliberate:

1. **A named feature is its own node.** A dict key or a dataframe column becomes
   ``feature_id(name)``, never the container that holds it. A feature named in a
   config file and a column written in code land on the same node because both
   go through ``feature_id``.
2. **Flow direction is data direction.** ``source_id`` produces, ``target_id``
   receives. A container write is two hops: ``value -> @feature:k -> container``
   (CONTAINER_WRITE then MUTATES), so a backward slice of one key never drags in
   its siblings.
3. **A barrier is an explicit node, not a missing edge.** ``eval``-built code,
   reflection with a computed name and opaque third-party calls emit a
   ``Barrier`` and route flow *through* it (``args -> barrier -> result``) at
   ``UNKNOWN`` confidence. Nothing is stitched across: any slice that crosses one
   reports it in ``barrier_ids`` and drops to ``UNKNOWN``.
4. **Reaching definitions, not one node per name.** Each binding of a name is its
   own node (``m::f.x``, ``m::f.x#2``, ... in source order, via ``make_id``), and
   reads link to the definitions that actually reach them. Ordinals follow the
   contract's ``#n`` scheme so the nodes coincide with card 1's ASSIGNMENT and
   PARAMETER elements.

Approximations are labelled in the edge provenance note: every edge whose note
starts with ``over-approximate:`` is a place where reach was preferred to
precision, and :meth:`LineageTracer.over_approximate_edge_ids` lists them.
Under-approximation is exactly the barrier set.

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
    canonical_dumps,
    canonical_jsonl,
    combine,
    feature_id,
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
        "dropna", "ffill", "fillna", "head", "interpolate", "mask", "mean",
        "pct_change", "pipe", "query", "reindex", "replace", "reset_index",
        "rolling", "round", "sample", "set_index", "shift", "sort_index",
        "sort_values", "std", "sum", "tail", "transform", "where",
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


def _stronger(first: Confidence, second: Confidence) -> Confidence:
    """The better-supported of two confidences, using only ``combine``."""
    if first == second:
        return first
    return second if combine(first, second) == first else first


def _short_hash(payload: object) -> str:
    return hashlib.sha256(canonical_dumps(payload).encode("ascii")).hexdigest()[:16]


@dataclass(frozen=True, slots=True, order=True)
class _Src:
    """One value a expression derives from, with how well it is known."""

    id: str
    confidence: Confidence = Confidence.RESOLVED
    note: str = ""


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
            _Src(s.id, combine(s.confidence, confidence), s.note or note) for s in srcs
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
    scope_qual: str
    kind: str  # variable | parameter | function | class | import | attribute
    line: int
    external_module: str = ""
    imported_name: str = ""


@dataclass
class _ScopeCtx:
    qual: str
    kind: str  # module | function | class | lambda | comprehension
    class_qual: str = ""
    func_qual: str = ""
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
    scope_of_node: dict[int, str] = field(default_factory=dict)
    functions: dict[str, tuple[ast.AST, str]] = field(default_factory=dict)
    classes: dict[str, tuple[ast.ClassDef, str]] = field(default_factory=dict)
    qual_of_def: dict[str, str] = field(default_factory=dict)
    counter: dict[tuple[str, str], int] = field(default_factory=dict)


def _declared(body: Sequence[ast.stmt], kind: type) -> frozenset[str]:
    """Names declared ``global``/``nonlocal`` anywhere in a scope body."""
    names: set[str] = set()
    stack: list[ast.AST] = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
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

    def _owner(self, ctx: _ScopeCtx, name: str) -> str:
        if name in ctx.global_names:
            return ""
        if name in ctx.nonlocal_names:
            scope = ctx.parent
            while scope is not None:
                if scope.kind in ("function", "lambda"):
                    return scope.qual
                scope = scope.parent
            return ""
        return ctx.qual

    def bind(
        self,
        ctx: _ScopeCtx,
        name: str,
        node: ast.AST,
        kind: str,
        *,
        owner_override: str | None = None,
        external_module: str = "",
        imported_name: str = "",
    ) -> str:
        info = self.info
        owner = self._owner(ctx, name) if owner_override is None else owner_override
        key = (owner, name)
        ordinal = info.counter.get(key, 0) + 1
        info.counter[key] = ordinal
        qual = f"{owner}.{name}" if owner else name
        node_id = make_id(info.module, qual, ordinal)
        info.all_defs.setdefault(key, []).append(node_id)
        info.def_meta[node_id] = _Def(
            id=node_id,
            name=name,
            scope_qual=owner,
            kind=kind,
            line=getattr(node, "lineno", 0),
            external_module=external_module,
            imported_name=imported_name,
        )
        info.id_of_node[id(node)] = node_id
        info.qual_of_def[node_id] = qual
        return node_id

    # -- scopes ------------------------------------------------------------

    def run(self) -> None:
        ctx = _ScopeCtx(qual="", kind="module")
        for stmt in self.info.tree.body:
            self.stmt(stmt, ctx)

    def _child_ctx(self, ctx: _ScopeCtx, qual: str, kind: str, body: Sequence[ast.stmt]) -> _ScopeCtx:
        return _ScopeCtx(
            qual=qual,
            kind=kind,
            class_qual=qual if kind == "class" else ctx.class_qual,
            func_qual=qual if kind in ("function", "lambda") else ctx.func_qual,
            parent=ctx,
            global_names=_declared(body, ast.Global),
            nonlocal_names=_declared(body, ast.Nonlocal),
        )

    def stmt(self, node: ast.stmt, ctx: _ScopeCtx) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            def_id = self.bind(ctx, node.name, node, "function")
            for dec in node.decorator_list:
                self.expr(dec, ctx)
            args = node.args
            for default in [*args.defaults, *[d for d in args.kw_defaults if d]]:
                self.expr(default, ctx)
            qual = f"{ctx.qual}.{node.name}" if ctx.qual else node.name
            child = self._child_ctx(ctx, qual, "function", node.body)
            self._bind_args(args, child)
            self.info.functions[def_id] = (node, qual)
            for inner in node.body:
                self.stmt(inner, child)
            return
        if isinstance(node, ast.ClassDef):
            def_id = self.bind(ctx, node.name, node, "class")
            for expr in [*node.bases, *node.decorator_list]:
                self.expr(expr, ctx)
            for kw in node.keywords:
                self.expr(kw.value, ctx)
            qual = f"{ctx.qual}.{node.name}" if ctx.qual else node.name
            child = self._child_ctx(ctx, qual, "class", node.body)
            self.info.classes[def_id] = (node, qual)
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
            self.expr(node.value, ctx)
            self.target(node.target, ctx)
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
        if isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))):
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
        if isinstance(node, (ast.Global, ast.Nonlocal, ast.Pass, ast.Break, ast.Continue)):
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
        every = [
            *args.posonlyargs,
            *args.args,
            *([args.vararg] if args.vararg else []),
            *args.kwonlyargs,
            *([args.kwarg] if args.kwarg else []),
        ]
        for arg in every:
            self.bind(ctx, arg.arg, arg, "parameter")

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
            if isinstance(node.value, ast.Name) and node.value.id == "self" and ctx.class_qual:
                self.bind(ctx, node.attr, node, "attribute", owner_override=ctx.class_qual)
            else:
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
            owner_ctx = ctx.parent if ctx.kind == "comprehension" and ctx.parent else ctx
            self.bind(owner_ctx, node.target.id, node.target, "variable")
            return
        if isinstance(node, ast.Lambda):
            self._lambda_n += 1
            qual = f"{ctx.qual}.<lambda{self._lambda_n}>" if ctx.qual else f"<lambda{self._lambda_n}>"
            for default in [*node.args.defaults, *[d for d in node.args.kw_defaults if d]]:
                self.expr(default, ctx)
            child = self._child_ctx(ctx, qual, "lambda", [])
            self._bind_args(node.args, child)
            self.info.scope_of_node[id(node)] = qual
            self.info.functions[make_id(self.info.module, qual)] = (node, qual)
            self.expr(node.body, child)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            self._comp_n += 1
            qual = f"{ctx.qual}.<comp{self._comp_n}>" if ctx.qual else f"<comp{self._comp_n}>"
            child = self._child_ctx(ctx, qual, "comprehension", [])
            child.parent = ctx
            self.info.scope_of_node[id(node)] = qual
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
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                self.expr(child, ctx)
            elif isinstance(child, ast.keyword):
                self.expr(child.value, ctx)
            elif isinstance(child, ast.comprehension):
                self.expr(child.iter, ctx)
                self.target(child.target, ctx)


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
        self._dict_defs: set[str] = set()
        self._alias_of: dict[str, tuple[str, ...]] = {}
        self._instance_of: dict[str, str] = {}
        self._container_features: dict[str, set[str]] = {}
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
        for module in sorted(self._modules, key=lambda name: (self._modules[name].path, name)):
            _ModuleWalker(self, self._modules[module]).run()
        self._emit_mutation_through_parameters()
        self._link_config_keys()
        self.lineage_edges = tuple(
            sorted(self._edges.values(), key=lambda e: (e.id, e.source_id, e.target_id))
        )
        self.barriers = tuple(sorted(self._barriers.values(), key=lambda b: b.id))
        self._build_adjacency()
        return self.lineage_edges, self.barriers

    def slice(self, root_id: str, direction: str) -> Slice:
        """Backward or forward slice of ``root_id`` as a reproducible ID set.

        Every hop is evidenced: each ID in ``edge_ids`` resolves to a
        :class:`LineageEdge` carrying its method, confidence and span. A slice
        that crosses a barrier lists it and reports ``UNKNOWN``.
        """
        if direction not in ("backward", "forward"):
            raise ValueError(f"direction must be 'backward' or 'forward', not {direction!r}")
        key = f"{direction}:{root_id}"
        cached = self._slice_cache.get(key)
        if cached is not None:
            return cached
        slice_id = f"@slice:{direction}:{root_id}"
        known = root_id in self._out or root_id in self._in or root_id in self._barriers
        if not known:
            self.unresolved.append(
                Unresolved(
                    id=f"@slice-root:{root_id}",
                    reason=UnresolvedReason.MISSING_TARGET,
                    span=SourceSpan(path="", line=0),
                    description=f"slice requested for {root_id!r}, which is not a lineage node",
                )
            )
        adjacency = self._in if direction == "backward" else self._out
        sinks = set(self.sink_ids)
        members: set[str] = {root_id}
        edge_ids: set[str] = set()
        barrier_ids: set[str] = set()
        confidences: list[Confidence] = []
        queue: deque[str] = deque([root_id])
        seen: set[str] = {root_id}
        while queue:
            node = queue.popleft()
            if node in self._barriers:
                barrier_ids.add(node)
            if direction == "forward" and node in sinks and node != root_id:
                continue  # a forward slice ends at a decision sink
            for neighbour, edge_id in adjacency.get(node, ()):  # already sorted
                edge_ids.add(edge_id)
                edge = self._edges[edge_id]
                confidences.append(edge.provenance.confidence)
                members.add(neighbour)
                if neighbour not in seen:
                    seen.add(neighbour)
                    queue.append(neighbour)
        for member in members:
            if member in self._barriers:
                barrier_ids.add(member)
        confidence = combine(*confidences) if confidences else Confidence.UNKNOWN
        if barrier_ids:
            confidence = Confidence.UNKNOWN
        result = Slice(
            id=slice_id,
            root_id=root_id,
            direction=direction,
            member_ids=tuple(sorted(members)),
            edge_ids=tuple(sorted(edge_ids)),
            barrier_ids=tuple(sorted(barrier_ids)),
            reaches_sink_ids=tuple(sorted(members & sinks)),
            confidence=confidence,
        )
        self._slice_cache[key] = result
        return result

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
        """A backward and a forward slice for every feature and every sink."""
        roots = [*self.feature_ids(), *self.sink_ids]
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
            elif element.kind is ElementKind.FEATURE:
                name = element.id[len("@feature:") :] if element.id.startswith("@feature:") else element.name
                self._declared_features.add(name)
                self._feature_names.add(name)

    def _index_edges(self, edges: Sequence[Edge]) -> None:
        for edge in edges:
            if edge.kind in (EdgeKind.CALLS, EdgeKind.INSTANTIATES) and edge.call_site is not None:
                key = (edge.source_id, edge.call_site.line)
                self._call_targets.setdefault(key, []).append(
                    (edge.target_id, edge.provenance.confidence)
                )
            elif edge.kind is EdgeKind.CONFIGURES:
                self._configures.append(edge)
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
                self._unresolved(module, rel_path, UnresolvedReason.MISSING_TARGET, "module file not found")
                continue
            except UnicodeDecodeError:
                self._unresolved(module, rel_path, UnresolvedReason.DECODE_ERROR, "module is not valid UTF-8")
                continue
            except OSError as exc:  # pragma: no cover - environment dependent
                self._unresolved(module, rel_path, UnresolvedReason.MISSING_TARGET, f"unreadable: {exc.strerror}")
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
        self, module: str, path: str, reason: UnresolvedReason, description: str, line: int = 1
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
        if not source_id or not target_id or source_id == target_id:
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
                provenance=Provenance(method=method, confidence=confidence, span=span, note=note),
                span=span,
            )
        return edge_id

    def add_barrier(
        self, element_id: str, span: SourceSpan, reason: UnresolvedReason, description: str
    ) -> str:
        barrier_id = f"@barrier:{element_id}@{span.line}:{span.col if span.col is not None else 0}"
        suffix = 2
        base = barrier_id
        while barrier_id in self._barriers and self._barriers[barrier_id].description != description:
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
                name = edge.target_id[len("@feature:") :]
                self._feature_names.add(name)
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
            candidates = {element.name}
            if "::" in element.id:
                candidates.add(element.id.rsplit("/", 1)[-1])
            for candidate in sorted(candidates):
                if not candidate or candidate not in self._feature_names:
                    continue
                self.add_edge(
                    LineageKind.ASSIGNS,
                    element.id,
                    feature_id(candidate),
                    element.span,
                    Method.CONFIG_STRING_MATCH,
                    Confidence.HEURISTIC,
                    note=f"config key name matches feature {candidate!r}",
                )

    # -- cross-module lookups ---------------------------------------------

    def _function_node(self, element_id: str) -> tuple[ast.AST, _ModuleInfo] | None:
        """The parsed def for an element ID, or None if we never parsed it."""
        if not element_id or "::" not in element_id:
            return None
        module = element_id.split("::")[0]
        info = self._modules.get(module)
        if info is None:
            return None
        found = info.functions.get(element_id)
        return None if found is None else (found[0], info)

    def _is_known_callee(self, element_id: str) -> bool:
        return self._function_node(element_id) is not None or bool(
            self._class_qual_of(element_id)
        )

    def _class_qual_of(self, element_id: str) -> str:
        module = element_id.split("::")[0]
        info = self._modules.get(module)
        if info is None:
            return ""
        found = info.classes.get(element_id)
        return found[1] if found else ""

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


# --------------------------------------------------------------------------
# Per-module dataflow walk
# --------------------------------------------------------------------------


@dataclass
class _WalkScope:
    qual: str
    kind: str
    class_qual: str = ""
    def_id: str = ""  # element id of the enclosing function, "" at module level


class _ModuleWalker:
    """Reaching-definition walk of one module, emitting lineage edges."""

    def __init__(self, tracer: LineageTracer, info: _ModuleInfo) -> None:
        self.t = tracer
        self.mod = info
        self.env: dict[tuple[str, str], tuple[str, ...]] = {}
        self.scopes: list[_WalkScope] = [_WalkScope(qual="", kind="module", def_id=info.module)]
        self.guards: list[tuple[_Src, ...]] = []

    # -- helpers -----------------------------------------------------------

    @property
    def scope(self) -> _WalkScope:
        return self.scopes[-1]

    @property
    def element_id(self) -> str:
        for scope in reversed(self.scopes):
            if scope.def_id:
                return scope.def_id
        return self.mod.module

    def span(self, node: ast.AST) -> SourceSpan:
        return SourceSpan(
            path=self.mod.path,
            line=getattr(node, "lineno", 1),
            end_line=getattr(node, "end_lineno", None),
            col=getattr(node, "col_offset", None),
        )

    def node_id(self, node: ast.AST) -> str:
        return self.mod.id_of_node.get(id(node), "")

    def bind_env(self, node_id: str) -> None:
        meta = self.mod.def_meta.get(node_id)
        if meta is None:
            return
        self.env[(meta.scope_qual, meta.name)] = (node_id,)

    # -- entry -------------------------------------------------------------

    def run(self) -> None:
        self.block(self.mod.tree.body)

    def block(self, stmts: Sequence[ast.stmt]) -> None:
        for stmt in stmts:
            self.stmt(stmt)

    def _merge_env(
        self, left: dict[tuple[str, str], tuple[str, ...]], right: dict[tuple[str, str], tuple[str, ...]]
    ) -> dict[tuple[str, str], tuple[str, ...]]:
        merged = dict(left)
        for key, value in right.items():
            merged[key] = tuple(sorted(set(merged.get(key, ())) | set(value)))
        return merged

    # -- statements --------------------------------------------------------

    def stmt(self, node: ast.stmt) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._function(node)
            return
        if isinstance(node, ast.ClassDef):
            self._class(node)
            return
        if isinstance(node, ast.Assign):
            srcs = self.sources(node.value)
            for target in node.targets:
                self.assign(target, srcs, node.value, node)
            return
        if isinstance(node, ast.AnnAssign):
            if node.value is not None:
                self.assign(node.target, self.sources(node.value), node.value, node)
            else:
                node_id = self.node_id(node.target)
                if node_id:
                    self.bind_env(node_id)
            return
        if isinstance(node, ast.AugAssign):
            previous = self.sources_of_target(node.target)
            srcs = _merge_srcs(previous, self.sources(node.value))
            self.assign(node.target, srcs, node.value, node, note="augmented assignment")
            return
        if isinstance(node, (ast.For, ast.AsyncFor)):
            iterable = self.sources(node.iter)
            self.assign(
                node.target,
                _retag(iterable, Confidence.PROBABLE, "element of iterable"),
                node.iter,
                node,
            )
            before = dict(self.env)
            self.block(node.body)
            self.block(node.body)  # second pass picks up loop-carried definitions
            self.env = self._merge_env(self.env, before)
            self.block(node.orelse)
            return
        if isinstance(node, ast.While):
            guard = self.sources(node.test)
            self._emit_reads(guard, node.test, "loop condition")
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
            self._emit_reads(guard, node.test, "branch condition")
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
                    self.assign(item.optional_vars, srcs, item.context_expr, node)
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
            target = _return_node(self.element_id)
            if node.value is not None:
                for src in self.sources(node.value):
                    self.t.add_edge(
                        LineageKind.RETURNS, src.id, target, self.span(node),
                        Method.DATAFLOW, src.confidence, src.note,
                    )
            # A return under a guard depends on the condition that selected it:
            # this is how a rule cascade decides. Real dependence, not a guess.
            for guard in self.guards:
                for src in guard:
                    self.t.add_edge(
                        LineageKind.RETURNS, src.id, target, self.span(node),
                        Method.DATAFLOW, combine(src.confidence, Confidence.PROBABLE),
                        "control dependence: this branch condition selects this return",
                    )
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
                    self.reads(child, "guard")
            return
        if isinstance(node, ast.Match):
            subject = self.sources(node.subject)
            self.reads(node.subject, "match subject")
            before = dict(self.env)
            results = []
            for case in node.cases:
                self.env = dict(before)
                self._bind_pattern(case.pattern, subject, node)
                self.block(case.body)
                results.append(dict(self.env))
            self.env = results[0] if results else before
            for extra in results[1:]:
                self.env = self._merge_env(self.env, extra)
            return
        if isinstance(node, (ast.Global, ast.Nonlocal, ast.Pass, ast.Break, ast.Continue)):
            return
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt):
                self.stmt(child)
            elif isinstance(child, ast.expr):
                self.sources(child)

    def _bind_pattern(self, pattern: ast.pattern, srcs: tuple[_Src, ...], node: ast.AST) -> None:
        if isinstance(pattern, (ast.MatchAs, ast.MatchStar)) and pattern.name:
            target_id = self.node_id(pattern)
            if target_id:
                self.bind_env(target_id)
                for src in _retag(srcs, Confidence.PROBABLE, "match capture"):
                    self.t.add_edge(
                        LineageKind.ASSIGNS, src.id, target_id, self.span(node),
                        Method.DATAFLOW, src.confidence, src.note,
                    )
        for child in ast.iter_child_nodes(pattern):
            if isinstance(child, ast.pattern):
                self._bind_pattern(child, srcs, node)

    # -- definitions -------------------------------------------------------

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        def_id = self.node_id(node)
        if def_id:
            self.bind_env(def_id)
        qual = self.mod.functions.get(def_id, (None, ""))[1]
        args = node.args
        self._emit_defaults(args, node)
        saved_env = self.env
        self.env = dict(self.env)
        self.scopes.append(
            _WalkScope(qual=qual, kind="function", class_qual=self.scope.class_qual, def_id=def_id)
        )
        self._seed_params(args)
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

    def _emit_defaults(self, args: ast.arguments, node: ast.AST) -> None:
        positional = [*args.posonlyargs, *args.args]
        paired: list[tuple[ast.arg, ast.expr]] = []
        if args.defaults:
            paired.extend(zip(positional[len(positional) - len(args.defaults) :], args.defaults))
        for arg, default in zip(args.kwonlyargs, args.kw_defaults):
            if default is not None:
                paired.append((arg, default))
        for arg, default in paired:
            param_id = self.node_id(arg)
            if not param_id:
                continue
            for src in self.sources(default):
                self.t.add_edge(
                    LineageKind.PARAMETER_BINDING, src.id, param_id, self.span(default),
                    Method.DATAFLOW, src.confidence, src.note or "default argument",
                )

    def _class(self, node: ast.ClassDef) -> None:
        def_id = self.node_id(node)
        if def_id:
            self.bind_env(def_id)
        qual = self.mod.classes.get(def_id, (None, ""))[1]
        self.scopes.append(_WalkScope(qual=qual, kind="class", class_qual=qual, def_id=def_id))
        self.block(node.body)
        self.scopes.pop()

    # -- assignment --------------------------------------------------------

    def assign(
        self,
        target: ast.expr,
        srcs: tuple[_Src, ...],
        value: ast.expr | None,
        stmt: ast.AST,
        note: str = "",
    ) -> None:
        if isinstance(target, (ast.Tuple, ast.List)):
            self._unpack(target, srcs, value, stmt, note)
            return
        if isinstance(target, ast.Starred):
            self.assign(
                target.value,
                _retag(srcs, Confidence.PROBABLE, f"{_OVER}starred unpacking keeps no position"),
                value, stmt, note,
            )
            return
        if isinstance(target, ast.Attribute):
            self._assign_attribute(target, srcs, stmt, note)
            return
        if isinstance(target, ast.Subscript):
            self._assign_subscript(target, srcs, stmt, note)
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
            kind = self._kind_into(src.id, target_id, value)
            self.t.add_edge(
                kind, src.id, target_id, self.span(stmt), Method.DATAFLOW,
                src.confidence, src.note or note,
            )

    def _unpack(
        self, target: ast.Tuple | ast.List, srcs: tuple[_Src, ...], value: ast.expr | None,
        stmt: ast.AST, note: str,
    ) -> None:
        elements = list(target.elts)
        starred = any(isinstance(element, ast.Starred) for element in elements)
        if (
            isinstance(value, (ast.Tuple, ast.List))
            and not starred
            and len(value.elts) == len(elements)
        ):
            for element, sub_value in zip(elements, value.elts):
                self.assign(element, self.sources(sub_value), sub_value, stmt, note)
            return
        spread = _retag(
            srcs, Confidence.PROBABLE, f"{_OVER}unpacking does not key by position here"
        )
        for element in elements:
            self.assign(element, spread, None, stmt, note)

    def _assign_attribute(
        self, target: ast.Attribute, srcs: tuple[_Src, ...], stmt: ast.AST, note: str
    ) -> None:
        direct = self.node_id(target)
        if direct:  # self.attr = ... inside a method
            self.bind_env(direct)
            for src in srcs:
                self.t.add_edge(
                    LineageKind.ATTRIBUTE_WRITE, src.id, direct, self.span(stmt),
                    Method.DATAFLOW, src.confidence, src.note or note,
                )
            return
        for base in self.sources(target.value):
            shared = self._instance_attr_nodes(base.id, target.attr)
            for node_id in shared:
                for src in srcs:
                    self.t.add_edge(
                        LineageKind.ATTRIBUTE_WRITE, src.id, node_id, self.span(stmt),
                        Method.DATAFLOW,
                        combine(src.confidence, base.confidence, Confidence.PROBABLE),
                        f"{_OVER}write to {target.attr!r} on a known class, "
                        "merged with every other write to it",
                    )
            if shared:
                continue
            attr_id = _attr_node(base.id, target.attr)
            for src in srcs:
                self.t.add_edge(
                    LineageKind.ATTRIBUTE_WRITE, src.id, attr_id, self.span(stmt),
                    Method.DATAFLOW, combine(src.confidence, base.confidence),
                    src.note or note or "attribute write",
                )
            self.t.add_edge(
                LineageKind.MUTATES, attr_id, base.id, self.span(stmt), Method.DATAFLOW,
                combine(base.confidence, Confidence.PROBABLE),
                f"attribute {target.attr!r} of this object",
            )

    def _assign_subscript(
        self, target: ast.Subscript, srcs: tuple[_Src, ...], stmt: ast.AST, note: str
    ) -> None:
        container = self.sources(target.value)
        key = _literal_key(target.slice)
        if key is None and isinstance(target.value, ast.Attribute) and target.value.attr in (
            "loc", "iloc", "at", "iat"
        ):
            key = _literal_key_from_index(target.slice)
            container = self.sources(target.value.value)
        if key is None:
            for base in container:
                for src in srcs:
                    self.t.add_edge(
                        LineageKind.CONTAINER_WRITE, src.id, base.id, self.span(stmt),
                        Method.DATAFLOW,
                        combine(src.confidence, base.confidence, Confidence.HEURISTIC),
                        f"{_OVER}container write with a key that is not a literal",
                    )
            return
        node_id = self.t.note_feature(key)
        frame = any(base.id in self.t._frame_defs for base in container)
        kind = LineageKind.COLUMN_WRITE if frame else LineageKind.CONTAINER_WRITE
        for src in srcs:
            self.t.add_edge(
                kind, src.id, node_id, self.span(stmt), Method.DATAFLOW,
                src.confidence, src.note or note or f"named {'column' if frame else 'key'} {key!r}",
            )
        for base in container:
            self.t._container_features.setdefault(base.id, set()).add(node_id)
            self.t.add_edge(
                LineageKind.MUTATES, node_id, base.id, self.span(stmt), Method.DATAFLOW,
                combine(base.confidence, Confidence.RESOLVED),
                f"{'column' if frame else 'key'} {key!r} of this container",
            )

    def _classify_target(
        self, target_id: str, name: str, value: ast.expr | None, srcs: tuple[_Src, ...]
    ) -> None:
        if _looks_like_frame(name):
            self.t._frame_defs.add(target_id)
        if isinstance(value, (ast.Dict, ast.DictComp)):
            self.t._dict_defs.add(target_id)
        if isinstance(value, ast.Call) and _is_frame_producer(value):
            self.t._frame_defs.add(target_id)
        for src in srcs:
            if src.id.endswith(_INSTANCE):
                self.t._instance_of[target_id] = src.id[: -len(_INSTANCE)]
        for src in srcs:
            if src.id in self.t._frame_defs:
                self.t._frame_defs.add(target_id)
            if src.id in self.t._dict_defs:
                self.t._dict_defs.add(target_id)

    def _kind_into(self, source_id: str, target_id: str, value: ast.expr | None) -> LineageKind:
        if source_id.endswith(".@return"):
            return LineageKind.RETURNS
        if source_id.startswith("@feature:") and isinstance(value, (ast.Dict, ast.DictComp, ast.Call)):
            if target_id in self.t._frame_defs:
                return LineageKind.COLUMN_WRITE
            if target_id in self.t._dict_defs:
                return LineageKind.CONTAINER_WRITE
        return LineageKind.ASSIGNS

    # -- reads -------------------------------------------------------------

    def reads(self, node: ast.expr, why: str) -> None:
        self._emit_reads(self.sources(node), node, why)

    def _emit_reads(self, srcs: Iterable[_Src], node: ast.expr, why: str) -> None:
        for src in srcs:
            self.t.add_edge(
                LineageKind.READS, src.id, self.element_id, self.span(node),
                Method.DATAFLOW, src.confidence, src.note or why,
            )

    def sources_of_target(self, target: ast.expr) -> tuple[_Src, ...]:
        if isinstance(target, ast.Name):
            return self.read_name(target.id)
        if isinstance(target, ast.Attribute):
            return self.read_attribute(target)
        if isinstance(target, ast.Subscript):
            return self.read_subscript(target)
        return ()

    def scope_chain(self) -> list[_WalkScope]:
        chain = list(reversed(self.scopes))
        if len(chain) > 1 and chain[0].kind == "function":
            chain = [chain[0]] + [s for s in chain[1:] if s.kind != "class"]
        return chain

    def read_name(self, name: str) -> tuple[_Src, ...]:
        chain = self.scope_chain()
        for index, scope in enumerate(chain):
            key = (scope.qual, name)
            local = self.env.get(key)
            if local:
                if index == 0:
                    # One definition reaches this read in this scope: nothing is
                    # inferred, so CERTAIN. Several reach it (a branch merge or a
                    # loop): PROBABLE, and said so.
                    confidence = Confidence.CERTAIN if len(local) == 1 else Confidence.PROBABLE
                    note = "" if len(local) == 1 else f"{_OVER}reaching definitions merged at a branch"
                    return tuple(_Src(node_id, confidence, note) for node_id in sorted(local))
                every = tuple(sorted(set(local) | set(self.mod.all_defs.get(key, ()))))
                note = f"{_OVER}closure or global capture: every definition in the enclosing scope"
                confidence = Confidence.RESOLVED if len(every) == 1 else Confidence.PROBABLE
                return tuple(
                    _Src(node_id, confidence, "" if len(every) == 1 else note)
                    for node_id in every
                )
            defs = self.mod.all_defs.get(key)
            if defs:
                confidence = Confidence.RESOLVED if len(defs) == 1 else Confidence.PROBABLE
                note = "" if len(defs) == 1 else f"{_OVER}every definition of {name!r} in scope"
                return tuple(_Src(node_id, confidence, note) for node_id in sorted(defs))
        if name in BUILTIN_NAMES:
            return ()
        self.t.unresolved.append(
            Unresolved(
                id=f"@lineage-name:{self.element_id}:{name}",
                reason=UnresolvedReason.MISSING_TARGET,
                span=SourceSpan(path=self.mod.path, line=1),
                description=f"read of {name!r} in {self.element_id}: no binding found in any scope",
            )
        )
        return ()

    def read_attribute(self, node: ast.Attribute) -> tuple[_Src, ...]:
        if isinstance(node.value, ast.Name):
            if node.value.id == "self" and self._class_qual():
                class_qual = self._class_qual()
                key = (class_qual, node.attr)
                local = self.env.get(key) or tuple(self.mod.all_defs.get(key, ()))
                if local:
                    confidence = Confidence.RESOLVED if len(local) == 1 else Confidence.PROBABLE
                    note = "" if len(local) == 1 else f"{_OVER}every write to self.{node.attr}"
                    return tuple(_Src(n, confidence, note) for n in sorted(local))
                return ()
            imported = self._import_def(node.value.id)
            if imported is not None:
                resolved = self._module_member(imported, node.attr)
                if resolved:
                    return (_Src(resolved, Confidence.RESOLVED, "imported module member"),)
                return ()
        bases = self.sources(node.value)
        out: list[_Src] = []
        for base in bases:
            shared = self._instance_attr_nodes(base.id, node.attr)
            if shared:
                note = f"attribute {node.attr!r} of a known class"
                if len(shared) > 1:
                    note = f"{_OVER}every write to {node.attr!r} on this class"
                out.extend(
                    _Src(node_id, combine(base.confidence, Confidence.PROBABLE), note)
                    for node_id in shared
                )
                continue
            out.append(
                _Src(
                    _attr_node(base.id, node.attr),
                    combine(base.confidence, Confidence.PROBABLE),
                    f"attribute {node.attr!r} of this object",
                )
            )
        return _merge_srcs(out)

    def _instance_attr_nodes(self, base_id: str, attr: str) -> tuple[str, ...]:
        """Attribute nodes of the class this value is an instance of, if known.

        Unifies ``obj.attr`` with the ``self.attr`` nodes of the same class, so a
        write in a method and a read through a variable meet on one node.
        """
        class_id = self.t._instance_of.get(base_id, "")
        if not class_id and base_id.endswith(_INSTANCE):
            class_id = base_id[: -len(_INSTANCE)]
        if not class_id:
            return ()
        info = self.t._modules.get(class_id.split("::")[0])
        qual = self.t._class_qual_of(class_id)
        if info is None or not qual:
            return ()
        return tuple(info.all_defs.get((qual, attr), ()))

    def read_subscript(self, node: ast.Subscript) -> tuple[_Src, ...]:
        key = _literal_key(node.slice)
        if key is None and isinstance(node.value, ast.Attribute) and node.value.attr in (
            "loc", "iloc", "at", "iat"
        ):
            key = _literal_key_from_index(node.slice)
            if key is not None:
                self.sources(node.value.value)
                return (_Src(self.t.note_feature(key), Confidence.RESOLVED, f"named column {key!r}"),)
        if key is not None:
            return (_Src(self.t.note_feature(key), Confidence.RESOLVED, f"named key {key!r}"),)
        bases = self.sources(node.value)
        self.sources(node.slice)
        out: list[_Src] = []
        for base in bases:
            out.append(
                _Src(
                    base.id,
                    combine(base.confidence, Confidence.HEURISTIC),
                    f"{_OVER}container read with a key that is not a literal",
                )
            )
            for member in sorted(self.t._container_features.get(base.id, ())):
                out.append(
                    _Src(member, Confidence.HEURISTIC, f"{_OVER}any known key of this container")
                )
        return _merge_srcs(out)

    def _class_qual(self) -> str:
        for scope in reversed(self.scopes):
            if scope.class_qual:
                return scope.class_qual
        return ""

    def _import_def(self, name: str) -> _Def | None:
        for scope in self.scope_chain():
            key = (scope.qual, name)
            ids = self.env.get(key) or tuple(self.mod.all_defs.get(key, ()))
            for node_id in ids:
                meta = self.mod.def_meta.get(node_id)
                if meta is not None and meta.kind == "import":
                    return meta
        return None

    def _module_member(self, imported: _Def, name: str) -> str:
        module = imported.external_module.lstrip(".")
        target = self.t._modules.get(module)
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
                    self.t.add_edge(
                        LineageKind.ASSIGNS, src.id, target_id, self.span(node),
                        Method.DATAFLOW, src.confidence, src.note or "walrus assignment",
                    )
                return (_Src(target_id, Confidence.RESOLVED, "walrus assignment"),)
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
            self.reads(node.test, "conditional expression")
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
            self._lambda_body(node)
            return ()
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
        out: list[_Src] = []
        for key, value in zip(node.keys, node.values):
            value_srcs = self.sources(value)
            name = _literal_key(key) if key is not None else None
            if name is None:
                out.extend(
                    _retag(
                        value_srcs, Confidence.HEURISTIC,
                        f"{_OVER}dict entry with a key that is not a literal",
                    )
                )
                continue
            node_id = self.t.note_feature(name)
            for src in value_srcs:
                self.t.add_edge(
                    LineageKind.CONTAINER_WRITE, src.id, node_id, self.span(value),
                    Method.DATAFLOW, src.confidence, src.note or f"named key {name!r}",
                )
            out.append(_Src(node_id, Confidence.RESOLVED, f"named key {name!r}"))
        return _merge_srcs(out)

    def _comprehension(self, node: ast.expr) -> tuple[_Src, ...]:
        qual = self.mod.scope_of_node.get(id(node), self.scope.qual)
        self.scopes.append(
            _WalkScope(qual=qual, kind="comprehension", class_qual=self.scope.class_qual)
        )
        try:
            for generator in node.generators:  # type: ignore[attr-defined]
                iterable = self.sources(generator.iter)
                self.assign(
                    generator.target,
                    _retag(iterable, Confidence.PROBABLE, "element of iterable"),
                    generator.iter, node,
                )
                for condition in generator.ifs:
                    self.reads(condition, "comprehension filter")
            if isinstance(node, ast.DictComp):
                key_name = _literal_key(node.key)
                value_srcs = self.sources(node.value)
                if key_name is not None:
                    node_id = self.t.note_feature(key_name)
                    for src in value_srcs:
                        self.t.add_edge(
                            LineageKind.CONTAINER_WRITE, src.id, node_id, self.span(node),
                            Method.DATAFLOW, src.confidence, src.note or f"named key {key_name!r}",
                        )
                    return (_Src(node_id, Confidence.RESOLVED, f"named key {key_name!r}"),)
                return _merge_srcs(
                    self.sources(node.key),
                    _retag(
                        value_srcs, Confidence.HEURISTIC,
                        f"{_OVER}comprehension key is computed, so members are not keyed",
                    ),
                )
            return self.sources(node.elt)  # type: ignore[attr-defined]
        finally:
            self.scopes.pop()

    def _lambda_body(self, node: ast.Lambda) -> str:
        qual = self.mod.scope_of_node.get(id(node), "")
        def_id = make_id(self.mod.module, qual) if qual else ""
        saved = self.env
        self.env = dict(self.env)
        self.scopes.append(
            _WalkScope(qual=qual, kind="lambda", class_qual=self.scope.class_qual, def_id=def_id)
        )
        self._seed_params(node.args)
        for src in self.sources(node.body):
            self.t.add_edge(
                LineageKind.RETURNS, src.id, _return_node(def_id), self.span(node),
                Method.DATAFLOW, src.confidence, src.note or "lambda result",
            )
        self.scopes.pop()
        self.env = saved
        return def_id

    # -- calls -------------------------------------------------------------

    def call(self, node: ast.Call) -> tuple[_Src, ...]:
        func = node.func
        name = _dotted(func)
        base_name = name.split(".")[0] if name else ""
        attr = func.attr if isinstance(func, ast.Attribute) else ""

        # 1. reflection and runtime code construction -> explicit barrier
        if name in BARRIER_BUILTINS or base_name in ("importlib", "pickle", "marshal"):
            return self._barrier_call(node, UnresolvedReason.DYNAMIC_NAME, f"flow into {name or 'a dynamic call'}")
        if name == "getattr" or name == "setattr":
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
            return _retag(self._arg_sources(node), Confidence.PROBABLE, f"result of {name}()")
        imported = self._import_def(base_name) if base_name else None
        if imported is not None and self._is_transparent_module(imported):
            return _retag(
                self._arg_sources(node), Confidence.PROBABLE,
                f"result of {name}(), a value-transparent stdlib call",
            )

        # 6. anything else is opaque: an explicit barrier, never stitched across
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
        root = module.split(".")[0]
        return module in self.t.transparent_modules or root in self.t.transparent_modules

    def _arg_sources(self, node: ast.Call) -> tuple[_Src, ...]:
        groups = [self.sources(arg) for arg in node.args]
        groups.extend(self.sources(kw.value) for kw in node.keywords)
        return _merge_srcs(*groups)

    def _barrier_call(
        self, node: ast.Call, reason: UnresolvedReason, description: str
    ) -> tuple[_Src, ...]:
        span = self.span(node)
        barrier_id = self.t.add_barrier(self.element_id, span, reason, description)
        for src in self._arg_sources(node):
            self.t.add_edge(
                LineageKind.READS, src.id, barrier_id, span, Method.DATAFLOW,
                Confidence.UNKNOWN, f"value reaches a barrier: {description}",
            )
        if isinstance(node.func, ast.Attribute):
            for base in self.sources(node.func.value):
                self.t.add_edge(
                    LineageKind.READS, base.id, barrier_id, span, Method.DATAFLOW,
                    Confidence.UNKNOWN, f"receiver reaches a barrier: {description}",
                )
        return (_Src(barrier_id, Confidence.UNKNOWN, f"produced past a barrier: {description}"),)

    def _reflective_attr(self, node: ast.Call, name: str) -> tuple[_Src, ...]:
        literal = _literal_key(node.args[1]) if len(node.args) > 1 else None
        if literal is None:
            return self._barrier_call(
                node, UnresolvedReason.DYNAMIC_NAME,
                f"{name} with a name that is not a literal",
            )
        bases = self.sources(node.args[0]) if node.args else ()
        span = self.span(node)
        if name == "setattr":
            values = self.sources(node.args[2]) if len(node.args) > 2 else ()
            for base in bases:
                attr_id = _attr_node(base.id, literal)
                for src in values:
                    self.t.add_edge(
                        LineageKind.ATTRIBUTE_WRITE, src.id, attr_id, span,
                        Method.GETATTR_LITERAL, combine(src.confidence, Confidence.PROBABLE),
                        f"setattr with the literal name {literal!r}",
                    )
                self.t.add_edge(
                    LineageKind.MUTATES, attr_id, base.id, span, Method.GETATTR_LITERAL,
                    Confidence.PROBABLE, f"attribute {literal!r} of this object",
                )
            return ()
        return tuple(
            sorted(
                _Src(
                    _attr_node(base.id, literal),
                    combine(base.confidence, Confidence.PROBABLE),
                    f"getattr with the literal name {literal!r}",
                )
                for base in bases
            )
        )

    def _mutation(self, node: ast.Call, func: ast.Attribute) -> tuple[_Src, ...] | None:
        receivers = self.sources(func.value)
        if not receivers:
            return None
        span = self.span(node)
        if func.attr == "update" and node.args and isinstance(node.args[0], ast.Dict):
            members = self._dict_literal(node.args[0])
            for receiver in receivers:
                for member in members:
                    self.t._container_features.setdefault(receiver.id, set()).add(member.id)
                    self.t.add_edge(
                        LineageKind.MUTATES, member.id, receiver.id, span, Method.DATAFLOW,
                        combine(receiver.confidence, Confidence.RESOLVED),
                        "key written by update()",
                    )
            return ()
        if func.attr == "setdefault" and node.args:
            key = _literal_key(node.args[0])
            if key is not None:
                node_id = self.t.note_feature(key)
                values = self.sources(node.args[1]) if len(node.args) > 1 else ()
                for src in values:
                    self.t.add_edge(
                        LineageKind.CONTAINER_WRITE, src.id, node_id, span, Method.DATAFLOW,
                        src.confidence, f"named key {key!r} written by setdefault()",
                    )
                for receiver in receivers:
                    self.t._container_features.setdefault(receiver.id, set()).add(node_id)
                    self.t.add_edge(
                        LineageKind.MUTATES, node_id, receiver.id, span, Method.DATAFLOW,
                        combine(receiver.confidence, Confidence.RESOLVED),
                        f"key {key!r} of this container",
                    )
                return (_Src(node_id, Confidence.RESOLVED, f"named key {key!r}"),)
        values = self._arg_sources(node)
        for receiver in receivers:
            targets = [receiver.id, *self.t._alias_of.get(receiver.id, ())]
            for index, target_id in enumerate(dict.fromkeys(targets)):
                note = (
                    f"in-place {func.attr}()"
                    if index == 0
                    else f"in-place {func.attr}() through an alias"
                )
                confidence = Confidence.PROBABLE if index else receiver.confidence
                for src in values:
                    self.t.add_edge(
                        LineageKind.MUTATES, src.id, target_id, span, Method.DATAFLOW,
                        combine(src.confidence, confidence), note,
                    )
                if self.mod.def_meta.get(target_id, _Def("", "", "", "", 0)).kind == "parameter":
                    self.t._mutated_params.add(target_id)
        return ()

    # -- dataframe-shaped operations ---------------------------------------

    def _dataframe_call(self, node: ast.Call) -> tuple[_Src, ...] | None:
        func = node.func
        name = _dotted(func)
        base = name.split(".")[0] if name else ""
        span = self.span(node)

        if isinstance(func, ast.Attribute) and base and self._import_def(base) is not None:
            imported = self._import_def(base)
            assert imported is not None
            if imported.external_module.split(".")[0] in PANDAS_MODULES or base in PANDAS_MODULES:
                return self._pandas_module_call(node, func.attr, span)
        if not isinstance(func, ast.Attribute):
            return None
        attr = func.attr
        if attr not in COLUMN_METHODS and attr not in FRAME_METHODS:
            return None
        receivers = self.sources(func.value)
        if not receivers:
            return None
        frame_like = any(
            receiver.id in self.t._frame_defs or receiver.id.startswith("@feature:")
            for receiver in receivers
        ) or _looks_like_frame(_last_name(func.value))
        if attr in FRAME_METHODS and attr not in COLUMN_METHODS and not frame_like:
            return None
        confidence = Confidence.PROBABLE if frame_like else Confidence.HEURISTIC
        note_suffix = "" if frame_like else f"{_OVER}receiver assumed frame-shaped by method name"

        if attr == "assign":
            out = list(_retag(receivers, confidence, f"frame carried through .assign(){note_suffix}"))
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
                for src in self.sources(keyword.value):
                    self.t.add_edge(
                        LineageKind.COLUMN_WRITE, src.id, column, span, Method.DATAFLOW,
                        src.confidence, src.note or f"column {keyword.arg!r} written by .assign()",
                    )
                out.append(_Src(column, Confidence.RESOLVED, f"column {keyword.arg!r} of the result"))
            return _merge_srcs(out)
        if attr in ("merge", "join"):
            other = self.sources(node.args[0]) if node.args else ()
            keys = self._named_columns(node, ("on", "left_on", "right_on"))
            out = list(_retag(receivers, confidence, "left frame of a merge"))
            out.extend(_retag(other, confidence, "right frame of a merge"))
            for column in keys:
                out.append(_Src(column, Confidence.RESOLVED, "join key"))
            return _merge_srcs(out)
        if attr == "groupby":
            keys = self._named_columns(node, ("by",), positional=0)
            out = list(_retag(receivers, confidence, "frame grouped"))
            for column in keys:
                out.append(_Src(column, Confidence.RESOLVED, "group key"))
            return _merge_srcs(out)
        if attr == "rename":
            mapping = self._keyword(node, "columns")
            if isinstance(mapping, ast.Dict):
                out = list(_retag(receivers, confidence, "frame carried through .rename()"))
                for key, value in zip(mapping.keys, mapping.values):
                    old, new = _literal_key(key) if key else None, _literal_key(value)
                    if old is None or new is None:
                        continue
                    self.t.add_edge(
                        LineageKind.COLUMN_WRITE,
                        self.t.note_feature(old), self.t.note_feature(new), span,
                        Method.DATAFLOW, Confidence.RESOLVED,
                        f"column {old!r} renamed to {new!r}",
                    )
                    out.append(_Src(self.t.note_feature(new), Confidence.RESOLVED, "renamed column"))
                return _merge_srcs(out)
            return _retag(receivers, Confidence.HEURISTIC, f"{_OVER}.rename() mapping is not a literal")
        if attr == "drop":
            for column in self._named_columns(node, ("columns", "labels"), positional=0):
                self.t.add_edge(
                    LineageKind.READS, column, self.element_id, span, Method.DATAFLOW,
                    Confidence.RESOLVED, "column dropped from the frame",
                )
            return _retag(receivers, confidence, "frame carried through .drop()")
        if attr == "apply":
            return self._apply(node, receivers, confidence, span)
        return _retag(
            receivers, confidence, f"frame carried through .{attr}(){note_suffix}"
        )

    def _pandas_module_call(self, node: ast.Call, attr: str, span: SourceSpan) -> tuple[_Src, ...]:
        if attr in ("DataFrame", "Series"):
            out: list[_Src] = []
            for arg in node.args:
                if isinstance(arg, ast.Dict):
                    out.extend(self._dict_literal(arg))
                else:
                    out.extend(_retag(self.sources(arg), Confidence.PROBABLE, f"pandas {attr}()"))
            for keyword in node.keywords:
                out.extend(_retag(self.sources(keyword.value), Confidence.PROBABLE, f"pandas {attr}()"))
            return _merge_srcs(out)
        if attr in ("merge", "concat"):
            out = []
            for arg in node.args:
                if isinstance(arg, (ast.List, ast.Tuple)):
                    for element in arg.elts:
                        out.extend(_retag(self.sources(element), Confidence.PROBABLE, f"pandas {attr}()"))
                else:
                    out.extend(_retag(self.sources(arg), Confidence.PROBABLE, f"pandas {attr}()"))
            for column in self._named_columns(node, ("on", "left_on", "right_on")):
                out.append(_Src(column, Confidence.RESOLVED, "join key"))
            return _merge_srcs(out)
        return self._barrier_call(
            node, UnresolvedReason.THIRD_PARTY,
            f"opaque third-party call to pandas.{attr}",
        )

    def _apply(
        self, node: ast.Call, receivers: tuple[_Src, ...], confidence: Confidence, span: SourceSpan
    ) -> tuple[_Src, ...]:
        if not node.args:
            return _retag(receivers, confidence, "frame carried through .apply()")
        applied = node.args[0]
        if isinstance(applied, ast.Lambda):
            def_id = self._lambda_body_with_binding(applied, receivers, span)
            if def_id:
                return (_Src(_return_node(def_id), Confidence.PROBABLE, "result of .apply(lambda)"),)
        resolved = self._resolve_name_to_callee(applied)
        if resolved is not None:
            callee_id, callee_conf = resolved
            node_info = self.t._function_node(callee_id)
            if node_info is not None:
                params = _all_args(node_info[0].args)  # type: ignore[union-attr]
                if params:
                    param_id = node_info[1].id_of_node.get(id(params[0]), "")
                    for receiver in receivers:
                        if param_id:
                            self.t.add_edge(
                                LineageKind.PARAMETER_BINDING, receiver.id, param_id, span,
                                Method.DATAFLOW, combine(receiver.confidence, callee_conf, Confidence.PROBABLE),
                                "value bound by .apply()",
                            )
                return (
                    _Src(
                        _return_node(callee_id),
                        combine(callee_conf, Confidence.PROBABLE),
                        "result of .apply()",
                    ),
                )
        return self._barrier_call(
            node, UnresolvedReason.DYNAMIC_NAME,
            "apply() with a callable this analysis cannot resolve",
        )

    def _lambda_body_with_binding(
        self, node: ast.Lambda, receivers: tuple[_Src, ...], span: SourceSpan
    ) -> str:
        params = _all_args(node.args)
        def_id = self._lambda_body(node)
        if params and def_id:
            param_id = self.node_id(params[0])
            for receiver in receivers:
                if param_id:
                    self.t.add_edge(
                        LineageKind.PARAMETER_BINDING, receiver.id, param_id, span,
                        Method.DATAFLOW, combine(receiver.confidence, Confidence.PROBABLE),
                        "value bound to a lambda parameter",
                    )
        return def_id

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

    def _resolve_callee(self, node: ast.Call) -> tuple[str, Confidence, Method, str] | None:
        """Card 2's edge first, then our own scope lookup. Never a guess."""
        key = (self.element_id, getattr(node, "lineno", 0))
        recorded = self.t._call_targets.get(key)
        if recorded:
            known = [(tid, conf) for tid, conf in recorded if self.t._is_known_callee(tid)]
            if len(known) == 1:
                return (known[0][0], known[0][1], Method.DATAFLOW, "call edge from card 2")
            if len(known) > 1:
                return (
                    known[0][0], combine(known[0][1], Confidence.PROBABLE), Method.DATAFLOW,
                    f"{_OVER}card 2 reports {len(known)} possible callees here",
                )
        func = node.func
        if isinstance(func, ast.Name):
            resolved = self._resolve_name_to_callee(func)
            if resolved is not None:
                return (resolved[0], resolved[1], Method.SCOPE_LOOKUP, "callee resolved in scope")
            return None
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name):
                if func.value.id == "self":
                    class_qual = self._class_qual()
                    candidate = make_id(self.mod.module, f"{class_qual}.{func.attr}")
                    if self.t._function_node(candidate) is not None:
                        return (candidate, Confidence.PROBABLE, Method.MRO_DISPATCH, "self method call")
                    return None
                imported = self._import_def(func.value.id)
                if imported is not None:
                    module = imported.external_module.lstrip(".")
                    target = self.t._modules.get(module)
                    if target is not None:
                        candidate = make_id(module, func.attr)
                        if self.t._function_node(candidate) is not None:
                            return (
                                candidate, Confidence.RESOLVED, Method.SCOPE_LOOKUP,
                                "call into an imported module",
                            )
                    return None
                for src in self.sources(func.value):
                    class_id = self.t._instance_of.get(src.id)
                    if class_id:
                        class_qual = self.t._class_qual_of(class_id)
                        candidate = make_id(self.mod.module, f"{class_qual}.{func.attr}")
                        if self.t._function_node(candidate) is not None:
                            return (
                                candidate, Confidence.PROBABLE, Method.MRO_DISPATCH,
                                "method on a locally constructed instance",
                            )
            return None
        return None

    def _resolve_name_to_callee(self, func: ast.expr) -> tuple[str, Confidence] | None:
        if not isinstance(func, ast.Name):
            return None
        for scope in self.scope_chain():
            key = (scope.qual, func.id)
            ids = self.env.get(key) or tuple(self.mod.all_defs.get(key, ()))
            for node_id in ids:
                meta = self.mod.def_meta.get(node_id)
                if meta is None:
                    continue
                known = (
                    self.t._function_node(node_id) is not None
                    or bool(self.t._class_qual_of(node_id))
                )
                if meta.kind in ("function", "class") and known:
                    confidence = Confidence.RESOLVED if len(ids) == 1 else Confidence.PROBABLE
                    return (node_id, confidence)
                if meta.kind == "import":
                    module = meta.external_module.lstrip(".")
                    target = self.t._modules.get(module)
                    if target is not None:
                        candidate = make_id(module, meta.imported_name or meta.name)
                        if self.t._function_node(candidate) is not None:
                            return (candidate, Confidence.RESOLVED)
                    return None
        return None

    def _bind_call(
        self, node: ast.Call, callee_id: str, confidence: Confidence, method: Method, note: str
    ) -> tuple[_Src, ...]:
        found = self.t._function_node(callee_id)
        if found is None:
            class_qual = self.t._class_qual_of(callee_id)
            if class_qual:
                return self._bind_constructor(node, callee_id, class_qual, confidence, method, note)
            return self._barrier_call(
                node, UnresolvedReason.MISSING_TARGET,
                f"call to {callee_id}, whose definition was not parsed",
            )
        func_node, info = found
        skip = 0
        leading = [*func_node.args.posonlyargs, *func_node.args.args][:1]  # type: ignore[union-attr]
        if isinstance(node.func, ast.Attribute) and leading and leading[0].arg in ("self", "cls"):
            skip = 1
            param_id = info.id_of_node.get(id(leading[0]), "")
            for src in self.sources(node.func.value):
                self.t.add_edge(
                    LineageKind.PARAMETER_BINDING, src.id, param_id, self.span(node),
                    method, combine(src.confidence, confidence),
                    f"receiver bound to {leading[0].arg}",
                )
        self._bind_arguments(node, func_node, info, callee_id, confidence, method, note, skip=skip)
        return (
            _Src(
                _return_node(callee_id),
                combine(confidence, Confidence.RESOLVED),
                note or "return value",
            ),
        )

    def _bind_constructor(
        self, node: ast.Call, class_id: str, class_qual: str, confidence: Confidence,
        method: Method, note: str,
    ) -> tuple[_Src, ...]:
        """``C(...)`` binds ``__init__``'s parameters and yields an instance node."""
        module = class_id.split("::")[0]
        init_id = make_id(module, f"{class_qual}.__init__")
        instance_id = f"{class_id}{_INSTANCE}"
        found = self.t._function_node(init_id)
        if found is not None:
            leading = [*found[0].args.posonlyargs, *found[0].args.args][:1]  # type: ignore[union-attr]
            if leading and leading[0].arg in ("self", "cls"):
                self.t.add_edge(
                    LineageKind.PARAMETER_BINDING, instance_id,
                    found[1].id_of_node.get(id(leading[0]), ""), self.span(node),
                    method, combine(confidence, Confidence.RESOLVED),
                    f"new instance bound to {leading[0].arg}",
                )
            self._bind_arguments(
                node, found[0], found[1], init_id, confidence, method,
                note or "constructor argument", skip=1,
            )
        else:
            for src in self._arg_sources(node):
                self.t.add_edge(
                    LineageKind.PARAMETER_BINDING, src.id, instance_id,
                    self.span(node), method, combine(src.confidence, confidence, Confidence.PROBABLE),
                    f"{_OVER}{class_qual} defines no __init__ this analysis parsed",
                )
        return (
            _Src(instance_id, combine(confidence, Confidence.RESOLVED), f"instance of {class_qual}"),
        )

    def _bind_arguments(
        self, node: ast.Call, func_node: ast.AST, info: _ModuleInfo, callee_id: str,
        confidence: Confidence, method: Method, note: str, skip: int = 0,
    ) -> None:
        args = func_node.args  # type: ignore[union-attr]
        span = self.span(node)
        positional = [*args.posonlyargs, *args.args][skip:]
        by_name = {arg.arg: arg for arg in _all_args(args)[skip:]}
        bound: set[str] = set()

        def bind(arg: ast.arg, srcs: tuple[_Src, ...], extra: Confidence, why: str) -> None:
            param_id = info.id_of_node.get(id(arg), "")
            if not param_id:
                return
            bound.add(arg.arg)
            element_param = self.t._param_elements.get((callee_id, arg.arg))
            target_id = element_param or param_id
            for src in srcs:
                self.t.add_edge(
                    LineageKind.PARAMETER_BINDING, src.id, target_id, span, method,
                    combine(src.confidence, confidence, extra), src.note or why,
                )
            self.t._param_bindings.append((target_id, srcs, span, self.element_id))

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
            if index < len(positional):
                bind(positional[index], self.sources(arg_node), Confidence.RESOLVED, note)
            elif args.vararg is not None:
                bind(args.vararg, self.sources(arg_node), Confidence.RESOLVED, f"*{args.vararg.arg}")
            index += 1
        for keyword in node.keywords:
            if keyword.arg is None:
                mapping = keyword.value
                if isinstance(mapping, ast.Dict):
                    for key, value in zip(mapping.keys, mapping.values):
                        literal = _literal_key(key) if key is not None else None
                        if literal is not None and literal in by_name:
                            bind(by_name[literal], self.sources(value), Confidence.RESOLVED,
                                 f"**mapping with the literal key {literal!r}")
                        elif args.kwarg is not None:
                            bind(args.kwarg, self.sources(value), Confidence.PROBABLE,
                                 f"**mapping into **{args.kwarg.arg}")
                    continue
                spread = self.sources(mapping)
                if args.kwarg is not None:
                    bind(args.kwarg, spread, Confidence.PROBABLE, f"**kwargs into **{args.kwarg.arg}")
                for arg in _all_args(args):
                    if arg.arg in bound or arg is args.kwarg or arg is args.vararg:
                        continue
                    bind(
                        arg, spread, Confidence.HEURISTIC,
                        f"{_OVER}**kwargs expansion does not name which parameter it binds",
                    )
                continue
            arg = by_name.get(keyword.arg)
            if arg is not None:
                bind(arg, self.sources(keyword.value), Confidence.RESOLVED, f"keyword {keyword.arg!r}")
            elif args.kwarg is not None:
                bind(args.kwarg, self.sources(keyword.value), Confidence.RESOLVED,
                     f"keyword {keyword.arg!r} into **{args.kwarg.arg}")


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _return_node(element_id: str) -> str:
    return f"{element_id}.@return"


def _attr_node(base_id: str, attr: str) -> str:
    return f"{base_id}.@attr.{attr}"


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
        return False
    tail = name.split(".")[-1]
    head = name.split(".")[0]
    if head in PANDAS_MODULES and (tail.startswith("read_") or tail in ("DataFrame", "merge", "concat")):
        return True
    return tail in COLUMN_METHODS or tail in FRAME_METHODS


