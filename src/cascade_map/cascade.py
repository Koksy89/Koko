"""Card 3 -- per-function control flow, cascade ordering and decision structure.

What this module answers, in the owner's words: *what runs, in what order, and
what drives the final decision*.

Three artifacts' worth of facts come out of one pass over the target's AST:

``cfg_blocks.jsonl`` / ``cfg_edges.jsonl``
    A basic-block graph per module, class body, function, method and property.
    Branches, loops, ``try``/``except``/``finally``, ``with``, comprehensions,
    ``match``, ``assert``, early ``return``/``raise``/``break``/``continue`` and
    short-circuit ``and``/``or`` all produce real edges. A short circuit is a
    branch, not an expression: rule cascades lean on exactly that.

``order.jsonl``
    A canonical order tree per element (``@order::<element id>``), plus a
    cascade root that names the entry points, the call cycles and the wiring
    whose invocation order the source does not fix. ``SEQUENCE`` appears only
    where control flow fixes an order; everywhere else the node says
    ``BRANCH``, ``MERGE``, ``LOOP``, ``UNORDERED`` or ``CYCLE``. Flattening a
    real branch into a sequence would be a defect, so it is never done -- the
    total-order node is emitted only when the whole reachable cascade is
    branch-free.

``decisions.jsonl``
    Every rule-cascade step, guard clause, short-circuit gate, ``match`` case,
    ternary, assertion and tree-model call, with its condition source, the
    elements the condition reads, and where each outcome leads.

``reachability.jsonl``
    One :class:`~.contracts.interfaces.Reachability` per inventoried element --
    module, class, function, parameter, blob, all of them. The bias is
    deliberate and one-directional: an element reachable only through a
    ``HEURISTIC`` edge is *reachable, at HEURISTIC confidence*, never pruned,
    and anything the analysis cannot settle is ``UNKNOWN`` with a reason rather
    than ``NO_SINK_PATH``. A false "unreachable" sends the owner to delete live
    code, and "I could not tell" must never render as "this reaches nothing".

Constraint 1: nothing here imports, executes, ``exec``s, ``eval``s or unpickles
target code. Files are read as text and parsed with :mod:`ast`. Constraint 4:
every ordinal is derived from AST traversal order, never from a set, a hash or
a clock, so two runs are byte-identical.
"""

from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .contracts.interfaces import (
    CFGBlock,
    CFGEdge,
    BlockKind,
    Confidence,
    DecisionPoint,
    Edge,
    EdgeKind,
    DetectedCandidate,
    Element,
    ElementKind,
    Method,
    OrderKind,
    OrderNode,
    Provenance,
    Reachability,
    ReachabilityState,
    SourceSpan,
    Unresolved,
    UnresolvedReason,
    canonical_jsonl,
    combine,
    feature_id,
    make_id,
)

__all__ = [
    "ROLE_ENTRY_POINT",
    "ROLE_DECISION_SINK",
    "CascadeAnalyzer",
    "CFG_ELEMENT_KINDS",
    "WIRING_EDGE_KINDS",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CFG_ELEMENT_KINDS: frozenset[ElementKind] = frozenset(
    {
        ElementKind.MODULE,
        ElementKind.CLASS,
        ElementKind.FUNCTION,
        ElementKind.METHOD,
        ElementKind.PROPERTY,
    }
)
"""Element kinds that own a body and therefore a control-flow graph."""

WIRING_EDGE_KINDS: frozenset[EdgeKind] = frozenset(
    {
        EdgeKind.CALLS,
        EdgeKind.REGISTERS,
        EdgeKind.INSTANTIATES,
        EdgeKind.CONFIGURES,
        EdgeKind.REFERENCES,
        EdgeKind.DECORATES,
    }
)
"""Edges along which control can travel towards a decision sink.

``IMPORTS`` and ``INHERITS`` are excluded on purpose: importing a module does
not run it, and an inherited method that actually runs arrives as an
``MRO_DISPATCH`` call edge from card 2. Both exclusions are stated as
approximations in this card's report.
"""

_RANK: dict[Confidence, int] = {
    Confidence.CERTAIN: 4,
    Confidence.RESOLVED: 3,
    Confidence.PROBABLE: 2,
    Confidence.HEURISTIC: 1,
    Confidence.UNKNOWN: 0,
}
"""Mirrors the contract's own ordering. ``test_rank_agrees_with_combine`` fails
if the two ever disagree, so this cannot drift from :func:`combine`."""

_BUILTIN_NAMES: frozenset[str] = frozenset(dir(builtins)) | frozenset(
    {"self", "cls", "__name__", "__file__", "__doc__", "None", "True", "False"}
)

_IMPURE_BUILTINS: frozenset[str] = frozenset(
    {"print", "open", "setattr", "delattr", "exec", "eval", "input", "__import__"}
)
"""Builtins that reach outside the call. Named, not guessed: every other
builtin is treated as pure for the side-effect test."""

_SINK_NAME_HINTS: tuple[str, ...] = (
    "final_decision",
    "final_signal",
    "make_decision",
    "take_decision",
    "decide",
    "decision",
    "emit_decision",
    "emit_signal",
    "generate_signal",
    "get_signal",
    "produce_signal",
    "place_order",
    "submit_order",
    "send_order",
    "execute_trade",
)
"""Names that *suggest* a decision sink. Only used when the owner left the sink
blank in TARGET_PROFILE.md, always reported as a candidate with its evidence,
never silently adopted as fact (ARCHITECTURE.md, "Auto-detection")."""

_ENTRY_NAME_HINTS: tuple[str, ...] = ("main", "run", "cli", "entrypoint", "entry_point")

_MODEL_CALL_NAMES: frozenset[str] = frozenset(
    {
        "predict",
        "predict_proba",
        "predict_log_proba",
        "decision_function",
        "classify",
        "infer",
    }
)
"""Tree/ensemble model entry points. A call to one of these is a decision point
whose branching lives inside the model, not in the source."""

_AST_DIRECT_CERTAIN = Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN)


ROLE_ENTRY_POINT = "entry_point"
ROLE_DECISION_SINK = "decision_sink"
"""The two `DetectedCandidate.role` values this card emits."""


# ---------------------------------------------------------------------------
# Flow shapes -- the structure the order tree is built from
# ---------------------------------------------------------------------------


@dataclass
class _Shape:
    """Base for the structural tree recorded while the CFG is built."""

    node_id: str = field(default="", init=False)


@dataclass
class _SeqShape(_Shape):
    children: list[_Shape] = field(default_factory=list)


@dataclass
class _CallShape(_Shape):
    span: SourceSpan
    source: str
    in_loop: bool


@dataclass
class _BranchShape(_Shape):
    span: SourceSpan
    condition: str
    kind: str
    arms: list[tuple[str, _SeqShape]] = field(default_factory=list)
    reads: tuple[str, ...] = ()
    is_guard: bool = False
    cascade_note: str = ""
    block_id: str = ""
    arm_node_ids: list[str] = field(default_factory=list)
    is_decision: bool = True
    rejoins: bool = True
    """False when every arm returns or raises: control rejoins at the element's
    exit, not after the branch. The MERGE node says which."""

    condition_calls: tuple[tuple[int, int], ...] = ()
    """Positions of calls inside the condition. A condition that *calls*
    something reads what it called, and that is often the only element the
    condition names -- `if rule(value)` reads the rule, not the local alias."""

    order_key: int = 0
    """Creation order of the branch's block, so decisions are numbered in
    source order rather than innermost-first (an `elif` chain is built from the
    inside out)."""


@dataclass
class _LoopShape(_Shape):
    span: SourceSpan
    condition: str
    body: _SeqShape
    orelse: _SeqShape | None = None
    comprehension: bool = False
    block_id: str = ""


@dataclass
class _ModelShape(_Shape):
    """A tree-model call: the branching happens inside the model."""

    span: SourceSpan
    source: str
    reads: tuple[str, ...]
    call: _CallShape


# ---------------------------------------------------------------------------
# Small AST helpers
# ---------------------------------------------------------------------------


def _span_of(path: str, node: ast.AST) -> SourceSpan:
    return SourceSpan(
        path=path,
        line=int(getattr(node, "lineno", 1)),
        end_line=getattr(node, "end_lineno", None),
        col=getattr(node, "col_offset", None),
    )


def _src(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - unparse is total for parsed trees
        return type(node).__name__


def _is_terminal(shape_stmt: ast.stmt) -> bool:
    return isinstance(shape_stmt, (ast.Return, ast.Raise, ast.Break, ast.Continue))


def _body_terminates(body: Sequence[ast.stmt]) -> bool:
    return bool(body) and _is_terminal(body[-1])


def _read_targets(node: ast.AST | None) -> tuple[str, ...]:
    """Source-level names an expression reads, in deterministic order.

    Attribute chains stay whole (``self.threshold``) and string-literal
    subscripts become feature names (``row["price"]`` -> ``price``) because the
    owner reasons in features and the contract gives them their own namespace.
    """
    if node is None:
        return ()
    found: list[str] = []

    def add(name: str) -> None:
        if name and name not in found:
            found.append(name)

    def walk(cur: ast.AST) -> None:
        if isinstance(cur, ast.Attribute):
            add(_src(cur))
            walk(cur.value)
            return
        if isinstance(cur, ast.Subscript):
            key = cur.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                add(f"@key:{key.value}")
            walk(cur.value)
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                walk(key)
            return
        if isinstance(cur, ast.Name):
            add(cur.id)
            return
        for child in ast.iter_child_nodes(cur):
            walk(child)

    walk(node)
    return tuple(found)


def _call_positions(node: ast.AST | None) -> tuple[tuple[int, int], ...]:
    """(line, col) of every call inside an expression, in source order."""
    if node is None:
        return ()
    found = [
        (child.lineno, child.col_offset) for child in ast.walk(node) if isinstance(child, ast.Call)
    ]
    return tuple(sorted(set(found)))


def _pattern_source(pattern: ast.pattern) -> str:
    return _src(pattern)


def _is_wildcard_case(case: ast.match_case) -> bool:
    return (
        isinstance(case.pattern, ast.MatchAs)
        and case.pattern.pattern is None
        and case.guard is None
    )


def _has_main_guard(module: ast.Module) -> bool:
    for stmt in module.body:
        if not isinstance(stmt, ast.If):
            continue
        test = stmt.test
        if isinstance(test, ast.Compare) and isinstance(test.left, ast.Name):
            if test.left.id == "__name__":
                for comparator in test.comparators:
                    if isinstance(comparator, ast.Constant) and comparator.value == "__main__":
                        return True
    return False


# ---------------------------------------------------------------------------
# The per-element flow builder
# ---------------------------------------------------------------------------


class _FlowBuilder:
    """Builds the CFG and the structural shape tree for one element body.

    Nested ``def``/``class`` bodies are *not* descended into: they are separate
    elements with their own graphs, joined by card 2's call edges. Their
    decorator and base-class expressions do run here, and are walked here.
    """

    def __init__(self, element: Element, node: ast.AST, path: str) -> None:
        self.element = element
        self.node = node
        self.path = path
        self.blocks: list[CFGBlock] = []
        self.edges: list[CFGEdge] = []
        self.branches: list[_BranchShape] = []
        self.models: list[_ModelShape] = []
        self.deferred: list[_Shape] = []
        self.unresolved: list[Unresolved] = []
        self.shape: _SeqShape = _SeqShape()
        self.entry_id = ""
        self.exit_id = ""
        self._n = 0
        self._edge_seen: dict[str, int] = {}
        self._loops: list[tuple[str, str]] = []
        self._statement: ast.stmt | None = None
        self._handlers: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
        self._finally: list[str] = []
        self._unres_n = 0

    # -- primitives ---------------------------------------------------------

    def _block(self, kind: BlockKind, node: ast.AST, note: str = "") -> str:
        block_id = make_id(self.element.id, f"@block{self._n}")
        self._n += 1
        span = _span_of(self.path, node)
        self.blocks.append(
            CFGBlock(
                id=block_id,
                element_id=self.element.id,
                kind=kind,
                span=span,
                provenance=Provenance(
                    method=Method.AST_DIRECT,
                    confidence=Confidence.CERTAIN,
                    span=span,
                    note=note,
                ),
            )
        )
        return block_id

    def _edge(
        self,
        source_id: str,
        target_id: str,
        condition: str = "",
        taken_when: bool | None = None,
        confidence: Confidence = Confidence.CERTAIN,
        note: str = "",
    ) -> None:
        base = f"{source_id}=>{target_id}"
        seen = self._edge_seen.get(base, 0) + 1
        self._edge_seen[base] = seen
        self.edges.append(
            CFGEdge(
                id=make_id(base, "", seen),
                source_id=source_id,
                target_id=target_id,
                condition=condition,
                taken_when=taken_when,
                provenance=Provenance(
                    method=Method.AST_DIRECT, confidence=confidence, note=note
                ),
            )
        )

    def _expr_span(self, node: ast.AST) -> SourceSpan:
        """The statement a decision-bearing expression belongs to."""
        return _span_of(self.path, self._statement if self._statement is not None else node)

    def _register_branch(self, shape: _BranchShape) -> None:
        """Record a branch, numbered by when its block was created.

        An ``elif`` chain is built from the inside out, so appending in call
        order would number the last `elif` first and make the decision records
        read backwards.
        """
        _, _, ordinal = shape.block_id.rpartition("@block")
        shape.order_key = int(ordinal) if ordinal.isdigit() else len(self.branches)
        self.branches.append(shape)

    def _unresolved(self, node: ast.AST, reason: UnresolvedReason, description: str) -> None:
        record_id = make_id(self.element.id, f"@cfg_unresolved{self._unres_n}")
        self._unres_n += 1
        self.unresolved.append(
            Unresolved(
                id=record_id,
                reason=reason,
                span=_span_of(self.path, node),
                description=description,
                attempted=(Method.AST_DIRECT,),
            )
        )

    # -- driver -------------------------------------------------------------

    def build(self) -> None:
        body = list(getattr(self.node, "body", []))
        self.entry_id = self._block(BlockKind.ENTRY, self.node, note=f"entry of {self.element.id}")
        self.exit_id = self._block(BlockKind.EXIT, self.node, note=f"exit of {self.element.id}")
        cur, shape = self._stmts(body, self.entry_id)
        self.shape = shape
        if cur is not None:
            self._edge(cur, self.exit_id, condition="<fall through>")

    # -- statements ---------------------------------------------------------

    def _stmts(self, stmts: Sequence[ast.stmt], cur: str | None) -> tuple[str | None, _SeqShape]:
        seq = _SeqShape()
        for stmt in stmts:
            if cur is None:
                cur = self._block(
                    BlockKind.NORMAL,
                    stmt,
                    note="unreachable: follows a terminating statement",
                )
            cur, shapes = self._stmt(stmt, cur)
            seq.children.extend(shapes)
        return cur, seq

    def _stmt(self, stmt: ast.stmt, cur: str) -> tuple[str | None, list[_Shape]]:
        # A decision written inside an expression -- a ternary, a short-circuit
        # gate, a comprehension filter -- is reported at the statement that
        # evaluates it, which is where the owner reads it.
        self._statement = stmt
        handler = getattr(self, f"_stmt_{type(stmt).__name__}", None)
        if handler is not None:
            return handler(stmt, cur)  # type: ignore[no-any-return]
        # Default: every expression the statement evaluates, in order.
        shapes: list[_Shape] = []
        for child in ast.iter_child_nodes(stmt):
            if isinstance(child, ast.expr):
                cur, got = self._expr(child, cur)
                shapes.extend(got)
        return cur, shapes

    # definitions bind a name here; their bodies are separate elements
    def _stmt_FunctionDef(self, stmt: ast.stmt, cur: str) -> tuple[str | None, list[_Shape]]:
        shapes: list[_Shape] = []
        for dec in list(getattr(stmt, "decorator_list", [])):
            cur, got = self._expr(dec, cur)
            shapes.extend(got)
        for default in self._defaults(stmt):
            cur, got = self._expr(default, cur)
            shapes.extend(got)
        return cur, shapes

    _stmt_AsyncFunctionDef = _stmt_FunctionDef

    def _defaults(self, stmt: ast.stmt) -> list[ast.expr]:
        args = getattr(stmt, "args", None)
        if args is None:
            return []
        out = [d for d in list(args.defaults) if d is not None]
        out.extend(d for d in list(args.kw_defaults) if d is not None)
        return out

    def _stmt_ClassDef(self, stmt: ast.stmt, cur: str) -> tuple[str | None, list[_Shape]]:
        shapes: list[_Shape] = []
        assert isinstance(stmt, ast.ClassDef)
        for dec in stmt.decorator_list:
            cur, got = self._expr(dec, cur)
            shapes.extend(got)
        for base in stmt.bases:
            cur, got = self._expr(base, cur)
            shapes.extend(got)
        for keyword in stmt.keywords:
            cur, got = self._expr(keyword.value, cur)
            shapes.extend(got)
        return cur, shapes

    def _stmt_Return(self, stmt: ast.stmt, cur: str) -> tuple[str | None, list[_Shape]]:
        assert isinstance(stmt, ast.Return)
        shapes: list[_Shape] = []
        cur, got = self._expr(stmt.value, cur)
        shapes.extend(got)
        block = self._block(BlockKind.RETURN, stmt, note=_src(stmt))
        self._edge(cur, block, condition="<return>")
        if self._finally:
            self._edge(block, self._finally[-1], condition="<enter finally>")
        else:
            self._edge(block, self.exit_id)
        return None, shapes

    def _stmt_Raise(self, stmt: ast.stmt, cur: str) -> tuple[str | None, list[_Shape]]:
        assert isinstance(stmt, ast.Raise)
        shapes: list[_Shape] = []
        cur, got = self._expr(stmt.exc, cur)
        shapes.extend(got)
        block = self._block(BlockKind.RAISE, stmt, note=_src(stmt))
        self._edge(cur, block, condition="<raise>")
        self._exceptional_exit(block, _src(stmt.exc) or "<re-raise>")
        return None, shapes

    def _exceptional_exit(self, block: str, what: str) -> None:
        if self._handlers:
            handler_ids, _ = self._handlers[-1]
            for handler_id in handler_ids:
                self._edge(
                    block,
                    handler_id,
                    condition=what,
                    confidence=Confidence.PROBABLE,
                    note="exception path into an enclosing handler",
                )
            return
        if self._finally:
            self._edge(block, self._finally[-1], condition=what, note="exception path via finally")
            return
        self._edge(block, self.exit_id, condition=what, note="exception leaves this element")

    def _stmt_Break(self, stmt: ast.stmt, cur: str) -> tuple[str | None, list[_Shape]]:
        if not self._loops:
            self._unresolved(stmt, UnresolvedReason.MISSING_TARGET, "`break` outside a loop")
            return None, []
        self._edge(cur, self._loops[-1][1], condition="<break>")
        return None, []

    def _stmt_Continue(self, stmt: ast.stmt, cur: str) -> tuple[str | None, list[_Shape]]:
        if not self._loops:
            self._unresolved(stmt, UnresolvedReason.MISSING_TARGET, "`continue` outside a loop")
            return None, []
        self._edge(cur, self._loops[-1][0], condition="<continue>")
        return None, []

    def _stmt_If(self, stmt: ast.stmt, cur: str) -> tuple[str | None, list[_Shape]]:
        assert isinstance(stmt, ast.If)
        shapes: list[_Shape] = []
        cur, got = self._expr(stmt.test, cur)
        shapes.extend(got)
        condition = _src(stmt.test)
        branch_block = self._block(BlockKind.BRANCH, stmt, note=f"if {condition}")
        self._edge(cur, branch_block)

        then_start = self._block(BlockKind.NORMAL, stmt.body[0] if stmt.body else stmt)
        self._edge(branch_block, then_start, condition=condition, taken_when=True)
        then_end, then_shape = self._stmts(stmt.body, then_start)

        else_end: str | None = None
        else_shape = _SeqShape()
        if stmt.orelse:
            else_start = self._block(BlockKind.NORMAL, stmt.orelse[0])
            self._edge(branch_block, else_start, condition=condition, taken_when=False)
            else_end, else_shape = self._stmts(stmt.orelse, else_start)

        incoming = [b for b in (then_end, else_end) if b is not None]
        merge: str | None = None
        if incoming or not stmt.orelse:
            merge = self._block(BlockKind.NORMAL, stmt, note="merge")
            for block in incoming:
                self._edge(block, merge)
            if not stmt.orelse:
                self._edge(branch_block, merge, condition=condition, taken_when=False)

        is_guard = _body_terminates(stmt.body) and not stmt.orelse
        shape = _BranchShape(
            span=_span_of(self.path, stmt),
            condition=condition,
            kind="GUARD" if is_guard else "IF",
            arms=[("True", then_shape), ("False", else_shape)],
            reads=_read_targets(stmt.test),
            condition_calls=_call_positions(stmt.test),
            is_guard=is_guard,
            block_id=branch_block,
            rejoins=merge is not None,
        )
        self._register_branch(shape)
        shapes.append(shape)
        return merge, shapes

    def _stmt_While(self, stmt: ast.stmt, cur: str) -> tuple[str | None, list[_Shape]]:
        assert isinstance(stmt, ast.While)
        condition = _src(stmt.test)
        head = self._block(BlockKind.LOOP_HEAD, stmt, note=f"while {condition}")
        self._edge(cur, head)
        after = self._block(BlockKind.NORMAL, stmt, note="after loop")
        test_end, test_shapes = self._expr(stmt.test, head)
        body_start = self._block(BlockKind.NORMAL, stmt.body[0] if stmt.body else stmt)
        self._edge(test_end, body_start, condition=condition, taken_when=True)
        self._loops.append((head, after))
        body_end, body_shape = self._stmts(stmt.body, body_start)
        self._loops.pop()
        if body_end is not None:
            self._edge(body_end, head, condition="<loop back>")
        orelse_shape: _SeqShape | None = None
        if stmt.orelse:
            else_start = self._block(BlockKind.NORMAL, stmt.orelse[0], note="loop else")
            self._edge(test_end, else_start, condition=condition, taken_when=False)
            else_end, orelse_shape = self._stmts(stmt.orelse, else_start)
            if else_end is not None:
                self._edge(else_end, after)
        else:
            self._edge(test_end, after, condition=condition, taken_when=False)
        shape = _LoopShape(
            span=_span_of(self.path, stmt),
            condition=f"while {condition}",
            body=body_shape,
            orelse=orelse_shape,
            block_id=head,
        )
        return after, [*test_shapes, shape]

    def _stmt_For(self, stmt: ast.stmt, cur: str) -> tuple[str | None, list[_Shape]]:
        assert isinstance(stmt, (ast.For, ast.AsyncFor))
        shapes: list[_Shape] = []
        cur, got = self._expr(stmt.iter, cur)
        shapes.extend(got)
        condition = f"for {_src(stmt.target)} in {_src(stmt.iter)}"
        head = self._block(BlockKind.LOOP_HEAD, stmt, note=condition)
        self._edge(cur, head)
        after = self._block(BlockKind.NORMAL, stmt, note="after loop")
        body_start = self._block(BlockKind.NORMAL, stmt.body[0] if stmt.body else stmt)
        self._edge(head, body_start, condition="<next item>", taken_when=True)
        self._loops.append((head, after))
        body_end, body_shape = self._stmts(stmt.body, body_start)
        self._loops.pop()
        if body_end is not None:
            self._edge(body_end, head, condition="<loop back>")
        orelse_shape: _SeqShape | None = None
        if stmt.orelse:
            else_start = self._block(BlockKind.NORMAL, stmt.orelse[0], note="loop else")
            self._edge(head, else_start, condition="<iteration complete>", taken_when=False)
            else_end, orelse_shape = self._stmts(stmt.orelse, else_start)
            if else_end is not None:
                self._edge(else_end, after)
        else:
            self._edge(head, after, condition="<iteration complete>", taken_when=False)
        shape = _LoopShape(
            span=_span_of(self.path, stmt),
            condition=condition,
            body=body_shape,
            orelse=orelse_shape,
            block_id=head,
        )
        shapes.append(shape)
        return after, shapes

    _stmt_AsyncFor = _stmt_For

    def _stmt_With(self, stmt: ast.stmt, cur: str) -> tuple[str | None, list[_Shape]]:
        assert isinstance(stmt, (ast.With, ast.AsyncWith))
        shapes: list[_Shape] = []
        for item in stmt.items:
            cur, got = self._expr(item.context_expr, cur)
            shapes.extend(got)
        header = self._block(
            BlockKind.NORMAL,
            stmt,
            note="with " + ", ".join(_src(i.context_expr) for i in stmt.items),
        )
        self._edge(cur, header, condition="<enter context>")
        body_end, body_shape = self._stmts(stmt.body, header)
        shapes.extend(body_shape.children)
        if body_end is None:
            return None, shapes
        exit_block = self._block(BlockKind.NORMAL, stmt, note="exit context")
        self._edge(body_end, exit_block, condition="<exit context>")
        return exit_block, shapes

    _stmt_AsyncWith = _stmt_With

    def _stmt_Try(self, stmt: ast.stmt, cur: str) -> tuple[str | None, list[_Shape]]:
        handlers = list(getattr(stmt, "handlers", []))
        finalbody = list(getattr(stmt, "finalbody", []))
        orelse = list(getattr(stmt, "orelse", []))
        body = list(getattr(stmt, "body", []))

        handler_ids = tuple(
            self._block(BlockKind.HANDLER, h, note=f"except {_src(h.type) or '*'}")
            for h in handlers
        )
        finally_id = self._block(BlockKind.FINALLY, stmt, note="finally") if finalbody else ""

        body_start = self._block(BlockKind.NORMAL, body[0] if body else stmt, note="try body")
        self._edge(cur, body_start, condition="<enter try>")
        for handler, handler_id in zip(handlers, handler_ids):
            self._edge(
                body_start,
                handler_id,
                condition=_src(handler.type) or "<any exception>",
                confidence=Confidence.PROBABLE,
                note="exception raised anywhere in the guarded region",
            )

        if finally_id:
            self._finally.append(finally_id)
        if handler_ids:
            self._handlers.append((handler_ids, ()))
        body_end, body_shape = self._stmts(body, body_start)
        if handler_ids:
            self._handlers.pop()

        if orelse and body_end is not None:
            else_start = self._block(BlockKind.NORMAL, orelse[0], note="try else")
            self._edge(body_end, else_start, condition="<no exception>")
            body_end, orelse_shape = self._stmts(orelse, else_start)
            body_shape.children.extend(orelse_shape.children)

        arms: list[tuple[str, _SeqShape]] = [("try", body_shape)]
        ends: list[str] = [] if body_end is None else [body_end]
        for handler, handler_id in zip(handlers, handler_ids):
            handler_end, handler_shape = self._stmts(list(handler.body), handler_id)
            arms.append((f"except {_src(handler.type) or '*'}", handler_shape))
            if handler_end is not None:
                ends.append(handler_end)

        if finally_id:
            self._finally.pop()
            for end in ends:
                self._edge(end, finally_id, condition="<enter finally>")
            final_end, final_shape = self._stmts(finalbody, finally_id)
            branch = _BranchShape(
                span=_span_of(self.path, stmt),
                condition="try",
                kind="TRY",
                arms=arms,
                reads=(),
                block_id=body_start,
                is_decision=False,
            )
            self._register_branch(branch)
            shapes: list[_Shape] = [branch]
            shapes.extend(final_shape.children)
            return final_end, shapes

        branch = _BranchShape(
            span=_span_of(self.path, stmt),
            condition="try",
            kind="TRY",
            arms=arms,
            reads=(),
            block_id=body_start,
            is_decision=False,
        )
        self._register_branch(branch)
        if not ends:
            return None, [branch]
        merge = self._block(BlockKind.NORMAL, stmt, note="merge")
        for end in ends:
            self._edge(end, merge)
        return merge, [branch]

    _stmt_TryStar = _stmt_Try

    def _stmt_Match(self, stmt: ast.stmt, cur: str) -> tuple[str | None, list[_Shape]]:
        assert isinstance(stmt, ast.Match)
        shapes: list[_Shape] = []
        cur, got = self._expr(stmt.subject, cur)
        shapes.extend(got)
        subject = _src(stmt.subject)
        ends: list[str] = []
        arms: list[tuple[str, _SeqShape]] = []
        fallthrough: str | None = cur
        exhaustive = False
        first_branch = ""
        for case in stmt.cases:
            assert fallthrough is not None
            label = _pattern_source(case.pattern)
            if case.guard is not None:
                label = f"{label} if {_src(case.guard)}"
            branch_block = self._block(BlockKind.BRANCH, case.pattern, note=f"case {label}")
            first_branch = first_branch or branch_block
            self._edge(fallthrough, branch_block)
            case_start = self._block(BlockKind.NORMAL, case.body[0] if case.body else case.pattern)
            self._edge(branch_block, case_start, condition=f"{subject} ~ {label}", taken_when=True)
            case_end, case_shape = self._stmts(list(case.body), case_start)
            arms.append((f"case {label}", case_shape))
            if case_end is not None:
                ends.append(case_end)
            if _is_wildcard_case(case):
                exhaustive = True
                fallthrough = None
                break
            next_block = self._block(BlockKind.NORMAL, case.pattern, note="next case")
            self._edge(branch_block, next_block, condition=f"{subject} ~ {label}", taken_when=False)
            fallthrough = next_block
        if fallthrough is not None and not exhaustive:
            ends.append(fallthrough)
            arms.append(("no case matched", _SeqShape()))
        shape = _BranchShape(
            span=_span_of(self.path, stmt),
            condition=f"match {subject}",
            kind="MATCH",
            arms=arms,
            reads=_read_targets(stmt.subject),
            condition_calls=_call_positions(stmt.subject),
            block_id=first_branch,
            rejoins=bool(ends),
        )
        self._register_branch(shape)
        shapes.append(shape)
        if not ends:
            return None, shapes
        merge = self._block(BlockKind.NORMAL, stmt, note="merge")
        for end in ends:
            self._edge(end, merge)
        return merge, shapes

    def _stmt_Assert(self, stmt: ast.stmt, cur: str) -> tuple[str | None, list[_Shape]]:
        assert isinstance(stmt, ast.Assert)
        shapes: list[_Shape] = []
        cur, got = self._expr(stmt.test, cur)
        shapes.extend(got)
        condition = _src(stmt.test)
        branch_block = self._block(BlockKind.BRANCH, stmt, note=f"assert {condition}")
        self._edge(cur, branch_block)
        fail = self._block(BlockKind.RAISE, stmt, note="AssertionError")
        self._edge(branch_block, fail, condition=condition, taken_when=False)
        self._exceptional_exit(fail, "AssertionError")
        ok = self._block(BlockKind.NORMAL, stmt, note="assertion held")
        self._edge(branch_block, ok, condition=condition, taken_when=True)
        shape = _BranchShape(
            span=_span_of(self.path, stmt),
            condition=condition,
            kind="ASSERT",
            arms=[("True", _SeqShape()), ("False", _SeqShape())],
            reads=_read_targets(stmt.test),
            condition_calls=_call_positions(stmt.test),
            block_id=branch_block,
        )
        self._register_branch(shape)
        shapes.append(shape)
        return ok, shapes

    # -- expressions --------------------------------------------------------

    def _expr(self, node: ast.expr | None, cur: str) -> tuple[str, list[_Shape]]:
        """Walk an expression in evaluation order.

        Returns the block evaluation continues in. Short circuits, ternaries
        and comprehensions create real blocks and edges here, which is the
        whole point: a rule cascade written as ``a and b`` is a branch.
        """
        if node is None:
            return cur, []
        if isinstance(node, ast.BoolOp):
            return self._expr_boolop(node, cur)
        if isinstance(node, ast.IfExp):
            return self._expr_ifexp(node, cur)
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            return self._expr_comprehension(node, cur)
        if isinstance(node, ast.Lambda):
            return self._expr_lambda(node, cur)
        if isinstance(node, ast.Call):
            return self._expr_call(node, cur)
        shapes: list[_Shape] = []
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                cur, got = self._expr(child, cur)
                shapes.extend(got)
        return cur, shapes

    def _expr_boolop(self, node: ast.BoolOp, cur: str) -> tuple[str, list[_Shape]]:
        is_and = isinstance(node.op, ast.And)
        keyword = "and" if is_and else "or"
        cur, shapes = self._expr(node.values[0], cur)
        for index in range(1, len(node.values)):
            left = node.values[index - 1]
            right = node.values[index]
            condition = _src(left)
            branch_block = self._block(
                BlockKind.BRANCH, left, note=f"short-circuit `{keyword}` on {condition}"
            )
            self._edge(cur, branch_block)
            rhs_start = self._block(BlockKind.NORMAL, right, note=f"evaluate {_src(right)}")
            self._edge(branch_block, rhs_start, condition=condition, taken_when=is_and)
            rhs_end, rhs_shapes = self._expr(right, rhs_start)
            merge = self._block(BlockKind.NORMAL, node, note="merge")
            self._edge(
                branch_block,
                merge,
                condition=condition,
                taken_when=not is_and,
                note=f"short circuit: `{_src(right)}` is not evaluated",
            )
            self._edge(rhs_end, merge)
            shape = _BranchShape(
                span=self._expr_span(left),
                condition=condition,
                kind="SHORT_CIRCUIT",
                arms=[
                    ("True", _SeqShape(children=list(rhs_shapes)))
                    if is_and
                    else ("False", _SeqShape(children=list(rhs_shapes))),
                    ("False", _SeqShape()) if is_and else ("True", _SeqShape()),
                ],
                reads=_read_targets(left),
                condition_calls=_call_positions(left),
                cascade_note=f"`{keyword}` gate",
                block_id=branch_block,
            )
            self._register_branch(shape)
            shapes.append(shape)
            cur = merge
        return cur, shapes

    def _expr_ifexp(self, node: ast.IfExp, cur: str) -> tuple[str, list[_Shape]]:
        cur, shapes = self._expr(node.test, cur)
        condition = _src(node.test)
        branch_block = self._block(BlockKind.BRANCH, node, note=f"ternary on {condition}")
        self._edge(cur, branch_block)
        true_start = self._block(BlockKind.NORMAL, node.body)
        self._edge(branch_block, true_start, condition=condition, taken_when=True)
        true_end, true_shapes = self._expr(node.body, true_start)
        false_start = self._block(BlockKind.NORMAL, node.orelse)
        self._edge(branch_block, false_start, condition=condition, taken_when=False)
        false_end, false_shapes = self._expr(node.orelse, false_start)
        merge = self._block(BlockKind.NORMAL, node, note="merge")
        self._edge(true_end, merge)
        self._edge(false_end, merge)
        shape = _BranchShape(
            span=self._expr_span(node),
            condition=condition,
            kind="TERNARY",
            arms=[
                ("True", _SeqShape(children=list(true_shapes))),
                ("False", _SeqShape(children=list(false_shapes))),
            ],
            reads=_read_targets(node.test),
            condition_calls=_call_positions(node.test),
            block_id=branch_block,
        )
        self._register_branch(shape)
        shapes.append(shape)
        return merge, shapes

    def _expr_comprehension(self, node: ast.expr, cur: str) -> tuple[str, list[_Shape]]:
        generators = list(getattr(node, "generators", []))
        elements: list[ast.expr] = []
        if isinstance(node, ast.DictComp):
            elements = [node.key, node.value]
        else:
            elements = [getattr(node, "elt")]
        after = self._block(BlockKind.NORMAL, node, note="after comprehension")

        def build(index: int, block: str) -> tuple[_SeqShape, str]:
            """Returns the body shape and the block the innermost body ends in."""
            if index >= len(generators):
                seq = _SeqShape()
                end = block
                for element in elements:
                    end, shapes = self._expr(element, end)
                    seq.children.extend(shapes)
                return seq, end
            gen = generators[index]
            outer_end, iter_shapes = self._expr(gen.iter, block)
            head = self._block(
                BlockKind.LOOP_HEAD,
                node,
                note=f"comprehension for {_src(gen.target)} in {_src(gen.iter)}",
            )
            self._edge(outer_end, head)
            body_start = self._block(BlockKind.NORMAL, node, note="comprehension body")
            self._edge(head, body_start, condition="<next item>", taken_when=True)
            body_seq = _SeqShape(children=list(iter_shapes))
            filtered = body_start
            filter_shapes: list[_Shape] = []
            for condition_node in gen.ifs:
                filtered, got = self._expr(condition_node, filtered)
                filter_shapes.extend(got)
                condition = _src(condition_node)
                branch_block = self._block(
                    BlockKind.BRANCH, condition_node, note=f"comprehension filter {condition}"
                )
                self._edge(filtered, branch_block)
                kept = self._block(BlockKind.NORMAL, condition_node, note="kept")
                self._edge(branch_block, kept, condition=condition, taken_when=True)
                self._edge(branch_block, head, condition=condition, taken_when=False)
                branch_shape = _BranchShape(
                    span=self._expr_span(condition_node),
                    condition=condition,
                    kind="COMPREHENSION_FILTER",
                    arms=[("True", _SeqShape()), ("False", _SeqShape())],
                    reads=_read_targets(condition_node),
                    condition_calls=_call_positions(condition_node),
                    block_id=branch_block,
                )
                self._register_branch(branch_shape)
                filter_shapes.append(branch_shape)
                filtered = kept
            inner_seq, inner_end = build(index + 1, filtered)
            self._edge(inner_end, head, condition="<loop back>")
            self._edge(head, after, condition="<iteration complete>", taken_when=False)
            loop = _LoopShape(
                span=_span_of(self.path, node),
                condition=f"for {_src(gen.target)} in {_src(gen.iter)}",
                body=_SeqShape(children=[*filter_shapes, *inner_seq.children]),
                comprehension=True,
                block_id=head,
            )
            body_seq.children.append(loop)
            return body_seq, after

        seq, _ = build(0, cur)
        return after, list(seq.children)

    def _expr_lambda(self, node: ast.Lambda, cur: str) -> tuple[str, list[_Shape]]:
        """A lambda binds here; its body runs wherever the lambda is called.

        The body is not stitched into this element's control flow -- that would
        invent an order. Calls inside it are collected as *deferred* shapes so
        nothing is dropped and nothing downstream is falsely unreachable.
        """
        self._unresolved(
            node,
            UnresolvedReason.AMBIGUOUS,
            f"lambda body `{_src(node)}` runs when the lambda is called; its execution "
            "point is not fixed here. Calls inside it are reported as unordered.",
        )
        deferred_start = self._block(
            BlockKind.NORMAL, node, note="lambda body (deferred; no order claimed)"
        )
        _, shapes = self._expr(node.body, deferred_start)
        self.deferred.extend(shapes)
        return cur, []

    def _expr_call(self, node: ast.Call, cur: str) -> tuple[str, list[_Shape]]:
        shapes: list[_Shape] = []
        cur, got = self._expr(node.func, cur)
        shapes.extend(got)
        for arg in node.args:
            cur, got = self._expr(arg, cur)
            shapes.extend(got)
        for keyword in node.keywords:
            cur, got = self._expr(keyword.value, cur)
            shapes.extend(got)
        call = _CallShape(
            span=_span_of(self.path, node),
            source=_src(node),
            in_loop=bool(self._loops),
        )
        shapes.append(call)
        name = node.func.attr if isinstance(node.func, ast.Attribute) else (
            node.func.id if isinstance(node.func, ast.Name) else ""
        )
        if name in _MODEL_CALL_NAMES:
            reads: list[str] = []
            for arg in [*node.args, *(k.value for k in node.keywords)]:
                reads.extend(_read_targets(arg))
            if isinstance(node.func, ast.Attribute):
                reads.extend(_read_targets(node.func.value))
            model = _ModelShape(
                span=_span_of(self.path, node),
                source=_src(node),
                reads=tuple(dict.fromkeys(reads)),
                call=call,
            )
            self.models.append(model)
            shapes.append(model)
        return cur, shapes


# ---------------------------------------------------------------------------
# The card
# ---------------------------------------------------------------------------


class CascadeAnalyzer:
    """Card 3's implementation of :class:`~.contracts.interfaces.CascadeCard`.

    ``root`` is the target root that every :class:`SourceSpan` path is relative
    to. ``sink_ids`` and ``entry_ids`` come from ``TARGET_PROFILE.md``; both are
    auto-detected and *reported* (never silently adopted) when left blank.
    ``unresolved`` is card 2's residue: it is what tells this card that an
    element sits behind a call site nobody could resolve, which is ``UNKNOWN``
    reachability and emphatically not "unreachable".
    """

    def __init__(
        self,
        root: str | Path = ".",
        *,
        sink_ids: Sequence[str] = (),
        unresolved: Sequence[Unresolved] = (),
    ) -> None:
        self.root = Path(root)
        self._declared_sinks: tuple[str, ...] = tuple(sorted(set(sink_ids)))
        self._input_unresolved: tuple[Unresolved, ...] = tuple(unresolved)
        self._reset()

    def _reset(self) -> None:
        self._elements: dict[str, Element] = {}
        self._edges: list[Edge] = []
        self._blocks: list[CFGBlock] = []
        self._cfg_edges: list[CFGEdge] = []
        self._order: list[OrderNode] = []
        self._decisions: list[DecisionPoint] = []
        self._unresolved: list[Unresolved] = []
        self._opaque: set[str] = set()
        self._reachability: list[Reachability] = []
        self._entry_candidates: list[DetectedCandidate] = []
        self._sink_candidates: list[DetectedCandidate] = []
        self._builders: dict[str, _FlowBuilder] = {}
        self._source_cache: dict[str, ast.Module | None] = {}
        self._entry_ids: tuple[str, ...] = ()
        self._sink_ids: tuple[str, ...] = ()
        self._call_index: dict[tuple[str, int], list[Edge]] = {}
        self._edges_by_source: dict[str, list[Edge]] = {}
        self._positioned_edge_ids: set[str] = set()
        self._children: dict[str, list[str]] = {}
        self._by_module: dict[str, list[str]] = {}
        self._discarded_cache: dict[str, set[tuple[int, int]]] = {}
        self._side_effect_cache: dict[str, bool] = {}

    # -- the contract -------------------------------------------------------

    def order(
        self,
        elements: Sequence[Element],
        edges: Sequence[Edge],
        entry_ids: Sequence[str],
    ) -> tuple[
        Sequence[CFGBlock],
        Sequence[CFGEdge],
        Sequence[OrderNode],
        Sequence[DecisionPoint],
        Sequence[Reachability],
    ]:
        self._reset()
        self._elements = {element.id: element for element in elements}
        self._edges = sorted(edges, key=lambda e: e.id)
        for edge in self._edges:
            self._edges_by_source.setdefault(edge.source_id, []).append(edge)
            if edge.call_site is not None:
                key = (edge.call_site.path, edge.call_site.line)
                self._call_index.setdefault(key, []).append(edge)
        for element in sorted(self._elements.values(), key=lambda e: e.id):
            if element.parent_id:
                self._children.setdefault(element.parent_id, []).append(element.id)
            if element.kind is not ElementKind.MODULE:
                self._by_module.setdefault(make_id(element.module), []).append(element.id)

        self._build_cfgs()
        self._entry_ids = self._resolve_entries(entry_ids)
        self._sink_ids = self._resolve_sinks()
        self._build_order()
        self._build_decisions()
        self._build_reachability()

        self._blocks.sort(key=lambda b: b.id)
        self._cfg_edges.sort(key=lambda e: e.id)
        self._order.sort(key=lambda n: n.id)
        self._decisions.sort(key=lambda d: d.id)
        self._unresolved.sort(key=lambda u: u.id)
        self._reachability.sort(key=lambda r: r.id)
        return (
            tuple(self._blocks),
            tuple(self._cfg_edges),
            tuple(self._order),
            tuple(self._decisions),
            tuple(self._reachability),
        )

    # -- accessors for facts the contract has no type for -------------------

    def unresolved(self) -> Sequence[Unresolved]:
        """Everything this card could not settle. Constraint 3.

        ``CascadeCard.order`` has no slot for these; card 3 requests one.
        """
        return tuple(self._unresolved)

    def reachability(self) -> Sequence[Reachability]:
        """One record per inventoried element, sorted by id.

        The same sequence :meth:`order` returns fifth. Cards 5 and 15 read this
        as the canonical answer to "does this drive the final decision"; neither
        derives its own.
        """
        return tuple(self._reachability)

    def entry_point_candidates(self) -> Sequence[DetectedCandidate]:
        return tuple(self._entry_candidates)

    def sink_candidates(self) -> Sequence[DetectedCandidate]:
        return tuple(self._sink_candidates)

    def entry_ids(self) -> Sequence[str]:
        return self._entry_ids

    def sink_ids(self) -> Sequence[str]:
        return self._sink_ids

    def artifacts(self) -> dict[str, str]:
        """The five contracted artifacts, rendered exactly once, sorted."""
        return {
            "cfg_blocks.jsonl": canonical_jsonl(self._blocks),
            "cfg_edges.jsonl": canonical_jsonl(self._cfg_edges),
            "order.jsonl": canonical_jsonl(self._order),
            "decisions.jsonl": canonical_jsonl(self._decisions),
            "reachability.jsonl": canonical_jsonl(self._reachability),
        }

    # -- CFG ----------------------------------------------------------------

    def _parse(self, path: str) -> ast.Module | None:
        if path in self._source_cache:
            return self._source_cache[path]
        tree: ast.Module | None = None
        full = self.root / path
        try:
            text = full.read_text(encoding="utf-8")
        except FileNotFoundError:
            self._record_unresolved(
                make_id("@cascade", f"missing:{path}"),
                UnresolvedReason.MISSING_TARGET,
                SourceSpan(path=path, line=1),
                f"source file not found under {self.root}; no CFG built for it",
            )
        except UnicodeDecodeError as exc:
            self._record_unresolved(
                make_id("@cascade", f"decode:{path}"),
                UnresolvedReason.DECODE_ERROR,
                SourceSpan(path=path, line=1),
                f"not UTF-8 ({exc.reason}); no CFG built for it",
            )
        except OSError as exc:
            self._record_unresolved(
                make_id("@cascade", f"unreadable:{path}"),
                UnresolvedReason.MISSING_TARGET,
                SourceSpan(path=path, line=1),
                f"unreadable: {exc.__class__.__name__}",
            )
        else:
            try:
                tree = ast.parse(text, filename=path)
            except SyntaxError as exc:
                self._record_unresolved(
                    make_id("@cascade", f"syntax:{path}"),
                    UnresolvedReason.SYNTAX_ERROR,
                    SourceSpan(path=path, line=int(exc.lineno or 1)),
                    f"cannot parse: {exc.msg}; no CFG built for it",
                )
        self._source_cache[path] = tree
        return tree

    def _record_unresolved(
        self,
        record_id: str,
        reason: UnresolvedReason,
        span: SourceSpan,
        description: str,
        candidate_ids: tuple[str, ...] = (),
        candidate_confidence: Confidence = Confidence.UNKNOWN,
        opaque: bool = True,
    ) -> None:
        if opaque:
            self._opaque.add(record_id)
        self._unresolved.append(
            Unresolved(
                id=record_id,
                reason=reason,
                span=span,
                description=description,
                attempted=(Method.AST_DIRECT,),
                candidate_ids=candidate_ids,
                candidate_confidence=candidate_confidence,
            )
        )

    def _index_bodies(self, tree: ast.Module, module: str) -> dict[str, ast.AST]:
        """Map element ID -> defining AST node, using the contract's ID rule."""
        index: dict[str, ast.AST] = {make_id(module): tree}
        counts: dict[str, int] = {}

        def walk(body: Iterable[ast.stmt], prefix: str) -> None:
            for stmt in body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    qualname = f"{prefix}{stmt.name}"
                    counts[qualname] = counts.get(qualname, 0) + 1
                    index[make_id(module, qualname, counts[qualname])] = stmt
                    walk(stmt.body, f"{qualname}.")
                    continue
                for name, value in ast.iter_fields(stmt):
                    if name in {"body", "orelse", "finalbody"} and isinstance(value, list):
                        walk([s for s in value if isinstance(s, ast.stmt)], prefix)
                    elif name in {"handlers", "cases"} and isinstance(value, list):
                        for sub in value:
                            walk(list(getattr(sub, "body", [])), prefix)

        walk(tree.body, "")
        return index

    def _locate(self, element: Element, tree: ast.Module) -> ast.AST | None:
        index = self._index_bodies(tree, element.module)
        node = index.get(element.id)
        if node is not None:
            return node
        # Card 1's qualname convention may differ (``<locals>`` and friends).
        # Fall back to the span, which is unambiguous within one file.
        for candidate in index.values():
            if int(getattr(candidate, "lineno", -1)) == element.span.line:
                return candidate
        return None

    def _build_cfgs(self) -> None:
        for element in sorted(self._elements.values(), key=lambda e: e.id):
            if element.kind not in CFG_ELEMENT_KINDS:
                continue
            tree = self._parse(element.span.path)
            if tree is None:
                continue
            node = self._locate(element, tree)
            if node is None:
                self._record_unresolved(
                    make_id(element.id, "@cfg_not_located"),
                    UnresolvedReason.MISSING_TARGET,
                    element.span,
                    "no AST node matches this element's id or span; no CFG built",
                )
                continue
            builder = _FlowBuilder(element, node, element.span.path)
            builder.build()
            self._builders[element.id] = builder
            self._blocks.extend(builder.blocks)
            self._cfg_edges.extend(builder.edges)
            self._unresolved.extend(builder.unresolved)
            # A deferred lambda body hides a control path; `break` outside a
            # loop does not. Only the former blinds reachability.
            self._opaque.update(
                record.id
                for record in builder.unresolved
                if record.reason is UnresolvedReason.AMBIGUOUS
            )

    # -- entries and sinks --------------------------------------------------

    def _resolve_entries(self, entry_ids: Sequence[str]) -> tuple[str, ...]:
        declared = [eid for eid in dict.fromkeys(entry_ids)]
        for entry in declared:
            if entry not in self._elements:
                self._record_unresolved(
                    make_id("@cascade", f"entry:{entry}"),
                    UnresolvedReason.MISSING_TARGET,
                    SourceSpan(path="TARGET_PROFILE.md", line=1),
                    f"declared entry point {entry!r} is not in the inventory",
                )
        known = tuple(e for e in declared if e in self._elements)
        if known:
            for entry in known:
                self._entry_candidates.append(
                    DetectedCandidate(
                        id=make_id("@entry", entry),
                        element_id=entry,
                        role=ROLE_ENTRY_POINT,
                        evidence=("declared by the owner in TARGET_PROFILE.md",)
                        provenance=Provenance(
                            method=Method.AST_DIRECT,
                            confidence=Confidence.CERTAIN,
                            span=self._elements[entry].span,
                            note="declared entry point",
                        ),
                    )
                )
            return known
        return self._detect_entries()

    def _detect_entries(self) -> tuple[str, ...]:
        for element in sorted(self._elements.values(), key=lambda e: e.id):
            evidence = ""
            confidence = Confidence.HEURISTIC
            if element.kind is ElementKind.MODULE:
                tree = self._source_cache.get(element.span.path)
                if tree is not None and _has_main_guard(tree):
                    evidence = "module has an `if __name__ == \"__main__\"` guard"
                    confidence = Confidence.PROBABLE
            elif element.kind is ElementKind.FUNCTION and element.name in _ENTRY_NAME_HINTS:
                stem = Path(element.span.path).stem
                tree = self._source_cache.get(element.span.path)
                guarded = tree is not None and _has_main_guard(tree)
                if guarded:
                    evidence = (
                        f"function named {element.name!r} in a module with a "
                        '`if __name__ == "__main__"` guard'
                    )
                    confidence = Confidence.PROBABLE
                elif stem.startswith("run") or stem in {"main", "__main__"}:
                    evidence = f"function named {element.name!r} in {element.span.path}"
            if not evidence:
                continue
            self._entry_candidates.append(
                DetectedCandidate(
                    id=make_id("@entry", element.id),
                    element_id=element.id,
                    role=ROLE_ENTRY_POINT,
                    evidence=evidence,
                    provenance=Provenance(
                        method=Method.NAME_HEURISTIC,
                        confidence=confidence,
                        span=element.span,
                        note="auto-detected; TARGET_PROFILE.md left the entry point blank",
                    ),
                )
            )
        if not self._entry_candidates:
            self._record_unresolved(
                make_id("@cascade", "entry:none"),
                UnresolvedReason.MISSING_TARGET,
                SourceSpan(path="TARGET_PROFILE.md", line=1),
                "no entry point declared and none detected; the cascade root lists no entries",
            )
        return tuple(c.element_id for c in self._entry_candidates)

    def _resolve_sinks(self) -> tuple[str, ...]:
        known = tuple(s for s in self._declared_sinks if s in self._elements)
        for sink in self._declared_sinks:
            if sink not in self._elements:
                self._record_unresolved(
                    make_id("@cascade", f"sink:{sink}"),
                    UnresolvedReason.MISSING_TARGET,
                    SourceSpan(path="TARGET_PROFILE.md", line=1),
                    f"declared decision sink {sink!r} is not in the inventory",
                )
        if known:
            for sink in known:
                self._sink_candidates.append(
                    DetectedCandidate(
                        id=make_id("@sink", sink),
                        element_id=sink,
                        role=ROLE_DECISION_SINK,
                        evidence=("declared by the owner in TARGET_PROFILE.md",)
                        provenance=Provenance(
                            method=Method.AST_DIRECT,
                            confidence=Confidence.CERTAIN,
                            span=self._elements[sink].span,
                            note="declared decision sink",
                        ),
                    )
                )
            return known
        return self._detect_sinks()

    def _detect_sinks(self) -> tuple[str, ...]:
        for element in sorted(self._elements.values(), key=lambda e: e.id):
            if element.kind not in {
                ElementKind.FUNCTION,
                ElementKind.METHOD,
                ElementKind.ASSIGNMENT,
                ElementKind.PROPERTY,
            }:
                continue
            lowered = element.name.lower()
            hit = next((h for h in _SINK_NAME_HINTS if lowered == h), "")
            if not hit:
                hit = next((h for h in _SINK_NAME_HINTS if h in lowered), "")
                if not hit:
                    continue
            self._sink_candidates.append(
                DetectedCandidate(
                    id=make_id("@sink", element.id),
                    element_id=element.id,
                    role=ROLE_DECISION_SINK,
                    evidence=f"name matches the decision-sink hint {hit!r}",
                    provenance=Provenance(
                        method=Method.NAME_HEURISTIC,
                        confidence=Confidence.HEURISTIC,
                        span=element.span,
                        note=(
                            "auto-detected; TARGET_PROFILE.md left the decision sink blank. "
                            "Reported as a candidate, not adopted as fact."
                        ),
                    ),
                )
            )
        if not self._sink_candidates:
            self._record_unresolved(
                make_id("@cascade", "sink:none"),
                UnresolvedReason.MISSING_TARGET,
                SourceSpan(path="TARGET_PROFILE.md", line=1),
                (
                    "no decision sink declared and none detected by name; decision "
                    "reachability is UNKNOWN for every element rather than false for every "
                    "element (ARCHITECTURE.md, 'Auto-detection')"
                ),
            )
            return ()
        return tuple(c.element_id for c in self._sink_candidates)

    # -- order --------------------------------------------------------------

    def _order_id(self, element_id: str, path: str = "") -> str:
        return make_id("@order", f"{element_id}{path}")

    def _emit(
        self,
        node_id: str,
        kind: OrderKind,
        element_ids: Sequence[str],
        children: Sequence[str],
        method: Method,
        confidence: Confidence,
        note: str,
        span: SourceSpan | None = None,
    ) -> str:
        self._order.append(
            OrderNode(
                id=node_id,
                kind=kind,
                element_ids=tuple(element_ids),
                children=tuple(children),
                provenance=Provenance(
                    method=method, confidence=confidence, span=span, note=note
                ),
            )
        )
        return node_id

    def _targets_at(self, span: SourceSpan, source_element: str) -> list[Edge]:
        """Call edges card 2 placed at this call site."""
        candidates = self._call_index.get((span.path, span.line), [])
        matches = [e for e in candidates if e.source_id == source_element]
        if not matches:
            matches = list(candidates)
        exact = [
            e
            for e in matches
            if e.call_site is not None
            and e.call_site.col is not None
            and span.col is not None
            and e.call_site.col == span.col
        ]
        chosen = exact or matches
        for edge in chosen:
            self._positioned_edge_ids.add(edge.id)
        return sorted(chosen, key=lambda e: e.id)

    def _build_order(self) -> None:
        for element_id in sorted(self._builders):
            self._order_for_element(element_id)
        self._build_cascade_root()
        self._aggregate_element_ids()

    _UNAGGREGATED = frozenset({make_id("@order", "@cascade"), make_id("@order", "@total")})

    def _aggregate_element_ids(self) -> None:
        """Every node names the elements it schedules, in execution order.

        A leaf call node names its callee. A node gathers from a child only
        when the child runs unconditionally and in place -- another SEQUENCE,
        or the MERGE that owns a branch's continuation. It never gathers
        through a BRANCH, LOOP, UNORDERED or CYCLE child, because those say
        something the parent does not: a SEQUENCE naming two exclusive
        alternatives would assert that both run, one after the other, which is
        the flattening the workplan calls a defect. Those nodes name their own
        members and stay the place to read them.

        A node that names elements rests on card 2's call edges, so its method
        becomes CFG_REACHABILITY; a node that names none is pure structure read
        off the AST.
        """
        index = {node.id: node for node in self._order}
        memo: dict[str, tuple[str, ...]] = {}
        transparent = {OrderKind.SEQUENCE, OrderKind.MERGE}

        def gather(node_id: str, seen: frozenset[str]) -> tuple[str, ...]:
            if node_id in memo:
                return memo[node_id]
            node = index.get(node_id)
            if node is None or node_id in seen:
                return ()
            out: list[str] = list(node.element_ids)
            for child_id in node.children:
                child = index.get(child_id)
                if child is None:
                    continue
                if child.kind not in transparent and node.kind not in {
                    OrderKind.BRANCH,
                    OrderKind.LOOP,
                }:
                    continue
                for element_id in gather(child_id, seen | {node_id}):
                    if element_id not in out:
                        out.append(element_id)
            memo[node_id] = tuple(out)
            return memo[node_id]

        rebuilt: list[OrderNode] = []
        for node in self._order:
            if node.id in self._UNAGGREGATED or node.kind is OrderKind.CYCLE:
                rebuilt.append(node)
                continue
            element_ids = gather(node.id, frozenset())
            provenance = node.provenance
            if provenance is not None and element_ids:
                provenance = Provenance(
                    method=Method.CFG_REACHABILITY,
                    confidence=provenance.confidence,
                    span=provenance.span,
                    note=provenance.note,
                )
            rebuilt.append(
                OrderNode(
                    id=node.id,
                    kind=node.kind,
                    element_ids=element_ids,
                    children=node.children,
                    provenance=provenance,
                )
            )
        self._order = rebuilt

    def _order_for_element(self, element_id: str) -> str:
        builder = self._builders[element_id]
        root_id = self._order_id(element_id)
        children, confidence = self._emit_seq(builder.shape, element_id, "")
        if builder.deferred:
            deferred_children, deferred_conf = self._emit_seq(
                _SeqShape(children=list(builder.deferred)), element_id, "/deferred"
            )
            node_id = self._emit(
                self._order_id(element_id, "/deferred"),
                OrderKind.UNORDERED,
                (),
                deferred_children,
                Method.AST_DIRECT,
                deferred_conf,
                "lambda bodies: they run when the lambda is called, so no order is claimed",
                self._elements[element_id].span,
            )
            children = [*children, node_id]
            confidence = combine(confidence, deferred_conf)
        unpositioned = self._unpositioned_children(element_id)
        if unpositioned:
            target_ids = sorted({e.target_id for e in unpositioned})
            node_id = self._emit(
                self._order_id(element_id, "/unpositioned"),
                OrderKind.UNORDERED,
                target_ids,
                (),
                Method.CFG_REACHABILITY,
                combine(*[e.provenance.confidence for e in unpositioned]),
                (
                    "wiring with no call site in this element's body: it runs, but the "
                    "source does not fix when. Never flattened into a SEQUENCE."
                ),
                self._elements[element_id].span,
            )
            children = [*children, node_id]
            confidence = combine(confidence, combine(*[e.provenance.confidence for e in unpositioned]))
        self._emit(
            root_id,
            OrderKind.SEQUENCE,
            (),
            children,
            Method.AST_DIRECT,
            confidence,
            f"body of {element_id} in execution order",
            self._elements[element_id].span,
        )
        return root_id

    def _unpositioned_children(self, element_id: str) -> list[Edge]:
        out = [
            edge
            for edge in self._edges_by_source.get(element_id, [])
            if edge.kind in WIRING_EDGE_KINDS
            and edge.id not in self._positioned_edge_ids
            and (
                edge.call_site is None
                or not self._call_index.get((edge.call_site.path, edge.call_site.line))
            )
        ]
        return sorted(out, key=lambda e: e.id)

    def _emit_seq(
        self, seq: _SeqShape, element_id: str, path: str
    ) -> tuple[list[str], Confidence]:
        """Emit one order node per shape, in execution order.

        A branch is followed by a MERGE node that *owns the continuation*: what
        runs after the arms rejoin hangs off the merge, because that is where
        control actually resumes. The alternative -- listing the branch and the
        continuation as flat siblings -- reads as though the arms and the
        continuation were one sequence, which is the flattening the workplan
        calls a defect.
        """
        children: list[str] = []
        confidences: list[Confidence] = [Confidence.CERTAIN]
        for index, shape in enumerate(seq.children):
            child_path = f"{path}/{index}"
            node_id, confidence = self._emit_shape(shape, element_id, child_path)
            if node_id is None:
                continue
            children.append(node_id)
            confidences.append(confidence)
            if shape.node_id and isinstance(shape, _BranchShape):
                rest = _SeqShape(children=list(seq.children[index + 1 :]))
                rest_children, rest_confidence = self._emit_seq(
                    rest, element_id, f"{child_path}/after"
                )
                merge_id = self._emit(
                    self._order_id(element_id, f"{child_path}/merge"),
                    OrderKind.MERGE,
                    (),
                    rest_children,
                    Method.AST_DIRECT,
                    rest_confidence,
                    (
                        f"control from every arm of {node_id} rejoins here, and what "
                        "follows runs once, whichever arm ran"
                        if shape.rejoins
                        else f"every arm of {node_id} returns or raises; control rejoins "
                        f"at the exit of {element_id}, not here"
                    ),
                    shape.span,
                )
                children.append(merge_id)
                confidences.append(rest_confidence)
                break
        return children, combine(*confidences)

    def _emit_shape(
        self, shape: _Shape, element_id: str, path: str
    ) -> tuple[str | None, Confidence]:
        if isinstance(shape, _CallShape):
            return self._emit_call(shape, element_id, path)
        if isinstance(shape, _ModelShape):
            shape.node_id = self._order_id(element_id, path)
            return (
                self._emit(
                    shape.node_id,
                    OrderKind.BRANCH,
                    (),
                    (),
                    Method.NAME_HEURISTIC,
                    Confidence.HEURISTIC,
                    f"tree-model decision: `{shape.source}` branches inside the model",
                    shape.span,
                ),
                Confidence.HEURISTIC,
            )
        if isinstance(shape, _BranchShape):
            return self._emit_branch(shape, element_id, path)
        if isinstance(shape, _LoopShape):
            return self._emit_loop(shape, element_id, path)
        if isinstance(shape, _SeqShape):
            children, confidence = self._emit_seq(shape, element_id, path)
            if not children:
                return None, Confidence.CERTAIN
            shape.node_id = self._order_id(element_id, path)
            return (
                self._emit(
                    shape.node_id,
                    OrderKind.SEQUENCE,
                    (),
                    children,
                    Method.AST_DIRECT,
                    confidence,
                    "statements in source order",
                ),
                confidence,
            )
        return None, Confidence.CERTAIN

    def _emit_call(
        self, shape: _CallShape, element_id: str, path: str
    ) -> tuple[str | None, Confidence]:
        edges = [e for e in self._targets_at(shape.span, element_id) if e.kind in WIRING_EDGE_KINDS]
        shape.node_id = self._order_id(element_id, path)
        if not edges:
            return (
                self._emit(
                    shape.node_id,
                    OrderKind.SEQUENCE,
                    (),
                    (),
                    Method.AST_DIRECT,
                    Confidence.UNKNOWN,
                    f"call `{shape.source}` -- no resolved target from card 2",
                    shape.span,
                ),
                Confidence.UNKNOWN,
            )
        confidence = combine(*[e.provenance.confidence for e in edges])
        targets = sorted({e.target_id for e in edges})
        if len(targets) == 1:
            return (
                self._emit(
                    shape.node_id,
                    OrderKind.SEQUENCE,
                    targets,
                    (),
                    Method.CFG_REACHABILITY,
                    confidence,
                    f"call `{shape.source}`; the callee's own order is {self._order_id(targets[0])}",
                    shape.span,
                ),
                confidence,
            )
        if shape.in_loop:
            return (
                self._emit(
                    shape.node_id,
                    OrderKind.UNORDERED,
                    targets,
                    (),
                    Method.CFG_REACHABILITY,
                    confidence,
                    (
                        f"`{shape.source}` dispatches from inside a loop: every target may "
                        "run and the source does not fix the order"
                    ),
                    shape.span,
                ),
                confidence,
            )
        return (
            self._emit(
                shape.node_id,
                OrderKind.BRANCH,
                targets,
                (),
                Method.CFG_REACHABILITY,
                confidence,
                (
                    f"`{shape.source}` dispatches to one of {len(targets)} targets; which one "
                    "is not fixed by the source"
                ),
                shape.span,
            ),
            confidence,
        )

    def _emit_branch(
        self, shape: _BranchShape, element_id: str, path: str
    ) -> tuple[str | None, Confidence]:
        shape.node_id = self._order_id(element_id, path)
        arm_ids: list[str] = []
        confidences: list[Confidence] = [Confidence.CERTAIN]
        for index, (label, arm) in enumerate(shape.arms):
            arm_path = f"{path}/arm{index}"
            children, confidence = self._emit_seq(arm, element_id, arm_path)
            arm_id = self._emit(
                self._order_id(element_id, arm_path),
                OrderKind.SEQUENCE,
                (),
                children,
                Method.AST_DIRECT,
                confidence,
                f"arm `{label}` of `{shape.condition}`",
                shape.span,
            )
            arm_ids.append(arm_id)
            shape.arm_node_ids.append(arm_id)
            confidences.append(confidence)
        confidence = combine(*confidences)
        self._emit(
            shape.node_id,
            OrderKind.BRANCH,
            (),
            arm_ids,
            Method.AST_DIRECT,
            confidence,
            f"{shape.kind.lower()} on `{shape.condition}`: exactly one arm runs",
            shape.span,
        )
        return shape.node_id, confidence

    def _emit_loop(
        self, shape: _LoopShape, element_id: str, path: str
    ) -> tuple[str | None, Confidence]:
        shape.node_id = self._order_id(element_id, path)
        body_children, confidence = self._emit_seq(
            shape.body, element_id, f"{path}/body"
        )
        children = [
            self._emit(
                self._order_id(element_id, f"{path}/body"),
                OrderKind.SEQUENCE,
                (),
                body_children,
                Method.AST_DIRECT,
                confidence,
                f"body of `{shape.condition}`",
                shape.span,
            )
        ]
        if shape.orelse is not None:
            else_children, else_conf = self._emit_seq(
                shape.orelse, element_id, f"{path}/else"
            )
            children.append(
                self._emit(
                    self._order_id(element_id, f"{path}/else"),
                    OrderKind.SEQUENCE,
                    (),
                    else_children,
                    Method.AST_DIRECT,
                    else_conf,
                    f"`else` of `{shape.condition}`, run when the loop was not broken out of",
                    shape.span,
                )
            )
            confidence = combine(confidence, else_conf)
        self._emit(
            shape.node_id,
            OrderKind.LOOP,
            (),
            children,
            Method.AST_DIRECT,
            confidence,
            (
                f"`{shape.condition}` repeats its body; the iteration count is not fixed "
                "by the source"
            ),
            shape.span,
        )
        return shape.node_id, confidence

    # -- cascade root, cycles, total order ----------------------------------

    def _wiring_successors(self) -> dict[str, list[Edge]]:
        out: dict[str, list[Edge]] = {}
        for edge in self._edges:
            if edge.kind in WIRING_EDGE_KINDS:
                out.setdefault(edge.source_id, []).append(edge)
        return out

    def _build_cascade_root(self) -> None:
        successors = self._wiring_successors()
        reachable, pre_order = self._walk_from_entries(successors)
        cycles = self._cycles(successors, reachable)
        children: list[str] = []
        confidences: list[Confidence] = [Confidence.CERTAIN]

        entry_roots = [
            self._order_id(entry) for entry in self._entry_ids if entry in self._builders
        ]
        for entry in self._entry_ids:
            if entry not in self._builders:
                self._record_unresolved(
                    make_id("@cascade", f"entry_nobody:{entry}"),
                    UnresolvedReason.MISSING_TARGET,
                    self._elements[entry].span if entry in self._elements else SourceSpan("", 1),
                    f"entry point {entry!r} has no control-flow graph; its order is not expanded",
                )
        by_id = {existing.id: existing for existing in self._order}
        for cycle in cycles:
            children.append(cycle)
            cycle_node = by_id.get(cycle)
            if cycle_node is not None and cycle_node.provenance is not None:
                confidences.append(cycle_node.provenance.confidence)
        if entry_roots:
            children.extend(entry_roots)
            for entry_root in entry_roots:
                root_node = by_id.get(entry_root)
                if root_node is not None and root_node.provenance is not None:
                    confidences.append(root_node.provenance.confidence)
        # The reachable cascade rests on every call edge walked to find it.
        confidences.extend(reachable.values())

        if len(entry_roots) > 1:
            kind = OrderKind.UNORDERED
            note = (
                f"{len(entry_roots)} entry points; nothing in the source fixes an order "
                "between separate launches"
            )
        else:
            kind = OrderKind.SEQUENCE
            note = "the cascade, from its entry point"

        total = self._total_order(pre_order, reachable, successors, bool(cycles))
        if total is not None:
            children.append(total)

        self._emit(
            make_id("@order", "@cascade"),
            kind,
            tuple(self._entry_ids),
            children,
            Method.CFG_REACHABILITY,
            combine(*confidences),
            note,
        )

    def _walk_from_entries(
        self, successors: Mapping[str, list[Edge]]
    ) -> tuple[dict[str, Confidence], list[str]]:
        """Pre-order walk of the call graph from the entry points.

        Pre-order, because "the order elements execute in" is the order they
        *begin* running: ingestion, then data engineering, then features, then
        decision logic.
        """
        reachable: dict[str, Confidence] = {}
        pre_order: list[str] = []
        stack: list[tuple[str, Confidence]] = [
            (entry, Confidence.CERTAIN) for entry in reversed(self._entry_ids)
        ]
        while stack:
            current, confidence = stack.pop()
            if current in reachable:
                if _RANK[confidence] > _RANK[reachable[current]]:
                    reachable[current] = confidence
                continue
            reachable[current] = confidence
            pre_order.append(current)
            for edge in reversed(sorted(successors.get(current, []), key=lambda e: e.id)):
                stack.append((edge.target_id, combine(confidence, edge.provenance.confidence)))
        return reachable, pre_order

    def _cycles(
        self, successors: Mapping[str, list[Edge]], reachable: Mapping[str, Confidence]
    ) -> list[str]:
        """Tarjan SCCs over the reachable call graph; iterative, so deep
        recursion in the target cannot blow this card's stack."""
        nodes = sorted(reachable) if reachable else sorted(self._elements)
        index: dict[str, int] = {}
        low: dict[str, int] = {}
        on_stack: set[str] = set()
        stack: list[str] = []
        counter = 0
        components: list[list[str]] = []

        for root in nodes:
            if root in index:
                continue
            work: list[tuple[str, int]] = [(root, 0)]
            while work:
                node, child_index = work[-1]
                if child_index == 0:
                    index[node] = low[node] = counter
                    counter += 1
                    stack.append(node)
                    on_stack.add(node)
                targets = sorted({e.target_id for e in successors.get(node, [])})
                if child_index < len(targets):
                    work[-1] = (node, child_index + 1)
                    target = targets[child_index]
                    if target not in self._elements and target not in index:
                        continue
                    if target not in index:
                        work.append((target, 0))
                    elif target in on_stack:
                        low[node] = min(low[node], index[target])
                    continue
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    component: list[str] = []
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component.append(member)
                        if member == node:
                            break
                    components.append(sorted(component))

        out: list[str] = []
        for component in sorted(components):
            self_loop = len(component) == 1 and any(
                e.target_id == component[0] for e in successors.get(component[0], [])
            )
            if len(component) == 1 and not self_loop:
                continue
            edges = [
                e
                for member in component
                for e in successors.get(member, [])
                if e.target_id in set(component)
            ]
            confidence = combine(*[e.provenance.confidence for e in edges])
            out.append(
                self._emit(
                    make_id("@order", f"@cycle:{component[0]}"),
                    OrderKind.CYCLE,
                    tuple(component),
                    (),
                    Method.CFG_REACHABILITY,
                    confidence,
                    (
                        "recursion: these elements call each other, so no finite order "
                        f"exists. Members: {', '.join(component)}"
                    ),
                )
            )
        return out

    def _total_order(
        self,
        pre_order: Sequence[str],
        reachable: Mapping[str, Confidence],
        successors: Mapping[str, list[Edge]],
        has_cycles: bool,
    ) -> str | None:
        """A single SEQUENCE over the whole cascade -- only when one exists.

        Emitted only if nothing in the reachable cascade branches, loops,
        cycles or dispatches. Anything else would be a flattened branch, which
        the workplan calls a defect rather than a simplification.
        """
        if not pre_order or has_cycles or len(set(pre_order)) != len(pre_order):
            return None
        for element_id in pre_order:
            builder = self._builders.get(element_id)
            if builder is None:
                return None
            if builder.branches or builder.deferred:
                return None
            if any(block.kind is BlockKind.LOOP_HEAD for block in builder.blocks):
                return None
            if self._unpositioned_children(element_id):
                return None
            targets: set[str] = set()
            for edge in successors.get(element_id, []):
                if edge.target_id in targets:
                    return None
                targets.add(edge.target_id)
        confidence = combine(*[reachable[e] for e in pre_order])
        return self._emit(
            make_id("@order", "@total"),
            OrderKind.SEQUENCE,
            tuple(pre_order),
            (),
            Method.CFG_REACHABILITY,
            confidence,
            (
                "total order: every element reachable from the entry point runs in this "
                "order, listed from the moment each starts. Emitted only because nothing "
                "in this cascade branches, loops or dispatches."
            ),
        )

    # -- decisions ----------------------------------------------------------

    def _build_decisions(self) -> None:
        for element_id in sorted(self._builders):
            builder = self._builders[element_id]
            counter = 0
            cascade_members = self._cascade_chain(builder)
            for shape in sorted(builder.branches, key=lambda b: (b.order_key, b.block_id)):
                if not shape.is_decision:
                    continue
                decision_id = make_id(element_id, f"@decision{counter}")
                counter += 1
                outcomes: list[tuple[str, str]] = []
                for index, (label, _arm) in enumerate(shape.arms):
                    target = (
                        shape.arm_node_ids[index] if index < len(shape.arm_node_ids) else ""
                    )
                    outcomes.append((label, target))
                note = shape.cascade_note
                if shape.block_id in cascade_members:
                    position, length = cascade_members[shape.block_id]
                    note = f"rule cascade: step {position} of {length}"
                if shape.is_guard:
                    note = (note + "; " if note else "") + "guard clause: the true arm leaves"
                called, call_confidences = self._condition_calls(
                    element_id, shape.condition_calls, shape.span
                )
                reads = self._resolve_reads(element_id, shape.reads, shape.span, called)
                self._decisions.append(
                    DecisionPoint(
                        id=decision_id,
                        element_id=element_id,
                        condition_source=shape.condition,
                        reads_ids=reads,
                        outcomes=tuple(outcomes),
                        is_sink=element_id in self._sink_ids,
                        provenance=Provenance(
                            method=Method.AST_DIRECT,
                            confidence=combine(Confidence.CERTAIN, *call_confidences),
                            span=shape.span,
                            note=f"{shape.kind}{'; ' + note if note else ''}",
                        ),
                    )
                )
            for model in builder.models:
                decision_id = make_id(element_id, f"@decision{counter}")
                counter += 1
                self._decisions.append(
                    DecisionPoint(
                        id=decision_id,
                        element_id=element_id,
                        condition_source=model.source,
                        reads_ids=self._resolve_reads(element_id, model.reads, model.span),
                        outcomes=(("model output", model.call.node_id or model.node_id),),
                        is_sink=element_id in self._sink_ids,
                        provenance=Provenance(
                            method=Method.NAME_HEURISTIC,
                            confidence=Confidence.HEURISTIC,
                            span=model.span,
                            note=(
                                "TREE_MODEL; the branching happens inside the model, not in "
                                "the source. Detected from the called name."
                            ),
                        ),
                    )
                )

    def _cascade_chain(self, builder: _FlowBuilder) -> dict[str, tuple[int, int]]:
        """Number the steps of each ``if``/``elif`` chain, for the record."""
        out: dict[str, tuple[int, int]] = {}
        node = builder.node
        for parent in ast.walk(node):
            if not isinstance(parent, ast.If):
                continue
            chain: list[ast.If] = [parent]
            current = parent
            while len(current.orelse) == 1 and isinstance(current.orelse[0], ast.If):
                current = current.orelse[0]
                chain.append(current)
            if len(chain) < 2:
                continue
            spans = {(_span_of(builder.path, c).line, _span_of(builder.path, c).col) for c in chain}
            ordered = sorted(spans)
            for shape in builder.branches:
                key = (shape.span.line, shape.span.col)
                if key in spans and shape.block_id not in out:
                    out[shape.block_id] = (ordered.index(key) + 1, len(ordered))
        return out

    def _condition_calls(
        self, element_id: str, positions: Sequence[tuple[int, int]], span: SourceSpan
    ) -> tuple[tuple[str, ...], tuple[Confidence, ...]]:
        """Elements a condition calls, and how sure card 2 was of each.

        `if rule(value)` reads the rule, and when the only edge to that rule is
        HEURISTIC the decision is a HEURISTIC statement about the program. A
        RESOLVED edge names exactly one element and adds no doubt, so only
        weaker-than-RESOLVED resolutions lower the decision's confidence -- the
        decision point itself is read straight off the AST.
        """
        targets: list[str] = []
        confidences: list[Confidence] = []
        for line, col in positions:
            for edge in self._targets_at(SourceSpan(span.path, line, None, col), element_id):
                if edge.kind not in WIRING_EDGE_KINDS:
                    continue
                if edge.target_id not in targets:
                    targets.append(edge.target_id)
                if _RANK[edge.provenance.confidence] < _RANK[Confidence.RESOLVED]:
                    confidences.append(edge.provenance.confidence)
        return tuple(targets), tuple(confidences)

    def _resolve_reads(
        self,
        element_id: str,
        reads: Sequence[str],
        span: SourceSpan,
        called: Sequence[str] = (),
    ) -> tuple[str, ...]:
        """Map the names a condition reads onto element and feature IDs."""
        out: list[str] = list(called)
        element = self._elements.get(element_id)
        for name in reads:
            if name.startswith("@key:"):
                resolved = feature_id(name[len("@key:") :])
            else:
                resolved = self._lookup_name(element, name)
            if not resolved:
                if name.split(".")[0] in _BUILTIN_NAMES:
                    continue
                self._record_unresolved(
                    make_id(element_id, f"@read:{name}"),
                    UnresolvedReason.MISSING_TARGET,
                    span,
                    f"condition reads {name!r}, which resolves to no inventoried element",
                    opaque=False,
                )
                continue
            if resolved not in out:
                out.append(resolved)
        return tuple(sorted(out))

    def _lookup_name(self, element: Element | None, name: str) -> str:
        if element is None:
            return ""
        head, _, tail = name.partition(".")
        scope: Element | None = element
        while scope is not None:
            for child_id in self._children.get(scope.id, []):
                child = self._elements[child_id]
                if child.name == head and not tail:
                    return child_id
                if tail and child.name == head:
                    nested = f"{child.qualname}.{tail}"
                    hit = make_id(scope.module, nested)
                    if hit in self._elements:
                        return hit
                    return child_id
            scope = self._elements.get(scope.parent_id) if scope.parent_id else None
        if head in {"self", "cls"} and tail:
            owner = element.qualname.split(".")[0] if "." in element.qualname else ""
            for suffix in (f"{owner}.{tail}", f"{owner}.__init__.{tail}"):
                hit = make_id(element.module, suffix)
                if hit in self._elements:
                    return hit
            return ""
        hit = make_id(element.module, name)
        if hit in self._elements:
            return hit
        hit = make_id(element.module, head)
        if hit in self._elements:
            return hit
        return ""

    # -- reachability -------------------------------------------------------

    def _discarded_call_sites(self, path: str) -> set[tuple[int, int]]:
        """Positions of calls whose result is thrown away.

        ``log_metrics(rows)`` as a bare statement hands nothing to anyone. That
        is the only value-free call shape this card can recognise from the AST,
        and it is what separates "runs before the decision" from "feeds the
        decision".
        """
        if path in self._discarded_cache:
            return self._discarded_cache[path]
        found: set[tuple[int, int]] = set()
        tree = self._parse(path)
        if tree is not None:
            for node in ast.walk(tree):
                if not isinstance(node, ast.Expr):
                    continue
                value = node.value
                if isinstance(value, ast.Await):
                    value = value.value
                if isinstance(value, ast.Call):
                    found.add((value.lineno, value.col_offset))
        self._discarded_cache[path] = found
        return found

    def _result_is_used(self, edge: Edge) -> bool:
        if edge.call_site is None:
            return True  # no position: assume the value is used, never the reverse
        position = (edge.call_site.line, edge.call_site.col or 0)
        return position not in self._discarded_call_sites(edge.call_site.path)

    def _may_have_side_effects(self, element_id: str, stack: tuple[str, ...] = ()) -> bool:
        """Could running this element change anything outside itself?

        Used only to decide between NO_SINK_PATH and UNKNOWN for an element
        whose result is discarded. Conservative in the safe direction: anything
        it cannot see -- a body with no CFG, a method call, a call it cannot
        resolve -- counts as a side effect, so the element comes back UNKNOWN
        rather than being called dead.
        """
        if element_id in self._side_effect_cache:
            return self._side_effect_cache[element_id]
        if element_id in stack:
            return True
        builder = self._builders.get(element_id)
        if builder is None:
            return True
        verdict = False
        for node in ast.walk(builder.node):
            if isinstance(node, (ast.Global, ast.Nonlocal, ast.Yield, ast.YieldFrom)):
                verdict = True
            elif isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(
                node.ctx, (ast.Store, ast.Del)
            ):
                verdict = True
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    verdict = True  # a method may mutate its receiver
                elif isinstance(node.func, ast.Name):
                    name = node.func.id
                    if name in _IMPURE_BUILTINS:
                        verdict = True
                    elif name not in _BUILTIN_NAMES:
                        target = self._lookup_name(self._elements.get(element_id), name)
                        verdict = not target or self._may_have_side_effects(
                            target, (*stack, element_id)
                        )
                else:
                    verdict = True
            if verdict:
                break
        self._side_effect_cache[element_id] = verdict
        return verdict

    def _sink_sites(self) -> dict[str, list[tuple[str, int]]]:
        """Sinks that are values rather than callables, keyed by their holder.

        ``FINAL_DECISION = decide(...)`` at module level is a sink nobody calls;
        it is a position inside the element that holds it.
        """
        out: dict[str, list[tuple[str, int]]] = {}
        for sink_id in self._sink_ids:
            sink = self._elements.get(sink_id)
            if sink is None or sink.kind in CFG_ELEMENT_KINDS:
                continue
            owner = sink.parent_id or make_id(sink.module)
            out.setdefault(owner, []).append((sink_id, sink.span.end_line or sink.span.line))
        for bucket in out.values():
            bucket.sort()
        return out

    def _solve_reachability(
        self,
    ) -> tuple[dict[str, Confidence], dict[str, tuple[str, ...]], dict[str, str]]:
        """Which elements drive the final decision?

        Not "which elements run before it" -- that is a different question, and
        it answers yes for a logger called between two cascade stages. An
        element drives the decision when its *work* reaches the sink:

        a. it calls something that reaches the sink;
        b. its result is consumed by something that reaches the sink -- which
           covers both a stage whose output the sink eventually reads, and a
           helper the sink itself calls;
        c. it holds, or contains, the sink.

        (b) is the approximation in this card. Card 3 sees call edges, not
        values, so "consumed by" means the call's result is not discarded. An
        element called only for its side effects therefore has no (b)
        justification -- and rather than call it dead, it comes back UNKNOWN
        whenever it could have a side effect at all, because whether those
        effects feed the decision is a lineage question card 4 owns.

        Returns the confidence per element, a representative path, and the
        reason any element was left UNKNOWN.
        """
        sites: dict[str, list[Edge]] = {}
        callers: dict[str, list[Edge]] = {}
        for edge in self._edges:
            if edge.kind not in WIRING_EDGE_KINDS:
                continue
            sites.setdefault(edge.source_id, []).append(edge)
            callers.setdefault(edge.target_id, []).append(edge)
        for bucket in (*sites.values(), *callers.values()):
            bucket.sort(key=lambda e: e.id)
        sink_sites = self._sink_sites()

        best: dict[str, Confidence] = {}
        via: dict[str, tuple[str, ...]] = {}
        for sink_id in self._sink_ids:
            best[sink_id] = Confidence.CERTAIN
            via[sink_id] = (sink_id,)
        for owner, held in sink_sites.items():
            if owner not in best:
                best[owner] = Confidence.CERTAIN
                via[owner] = (owner, held[0][0])

        def join(head: str, rest: Sequence[str]) -> tuple[str, ...]:
            """Prepend *head*, collapsing any loop back onto it: a
            representative path is a simple path. Recursion is reported as a
            CYCLE order node, not as a path that visits an element twice."""
            rest = tuple(rest)
            if head in rest:
                return (head, *rest[rest.index(head) + 1 :])
            return (head, *rest)

        element_ids = sorted({*self._elements, *sites, *callers})
        changed = True
        while changed:
            changed = False
            for element_id in element_ids:
                winner: tuple[Confidence, tuple[str, ...]] | None = None
                for edge in sites.get(element_id, []):
                    target = edge.target_id
                    if target not in best:
                        continue
                    candidate = (
                        combine(best[target], edge.provenance.confidence),
                        join(element_id, via[target]),
                    )
                    if winner is None or _RANK[candidate[0]] > _RANK[winner[0]]:
                        winner = candidate
                for edge in callers.get(element_id, []):
                    caller = edge.source_id
                    if caller not in best or not self._result_is_used(edge):
                        continue
                    candidate = (
                        combine(best[caller], edge.provenance.confidence),
                        join(element_id, via[caller]),
                    )
                    if winner is None or _RANK[candidate[0]] > _RANK[winner[0]]:
                        winner = candidate
                if winner is None:
                    continue
                known = best.get(element_id)
                if known is None or _RANK[winner[0]] > _RANK[known]:
                    best[element_id] = winner[0]
                    via[element_id] = winner[1]
                    changed = True

        # Side-effect-only callees: not dead, just not traceable from here.
        unknown_reason: dict[str, str] = {}
        for element_id in element_ids:
            if element_id in best:
                continue
            for edge in callers.get(element_id, []):
                if edge.source_id not in best or self._result_is_used(edge):
                    continue
                if self._may_have_side_effects(element_id):
                    unknown_reason[element_id] = (
                        f"called by {edge.source_id}, which reaches a decision sink, but its "
                        "result is discarded. It can still affect the decision through a side "
                        "effect, and whether it does is a lineage question card 4 answers -- "
                        "so this is UNKNOWN, not NO_SINK_PATH."
                    )
                    break

        # An element contained in something that reaches the sink reaches it too:
        # a parameter of a live function is live. Applied once, downwards only,
        # so a module can never launder reachability onto its whole contents.
        direct = dict(best)
        for element in sorted(self._elements.values(), key=lambda e: e.id):
            if element.id in direct:
                continue
            ancestor: Element | None = self._elements.get(element.parent_id)
            hops: list[str] = [element.id]
            while ancestor is not None:
                if ancestor.id in direct and ancestor.kind not in {
                    ElementKind.MODULE,
                    ElementKind.PACKAGE,
                }:
                    best[element.id] = direct[ancestor.id]
                    via[element.id] = (*hops, *via[ancestor.id])
                    break
                hops.append(ancestor.id)
                ancestor = self._elements.get(ancestor.parent_id) if ancestor.parent_id else None

        # A container reaches the sink when anything it contains does.
        for element in sorted(self._elements.values(), key=lambda e: e.id, reverse=True):
            if element.kind not in {ElementKind.MODULE, ElementKind.PACKAGE, ElementKind.CLASS}:
                continue
            if element.id in best:
                continue
            # Card 1 leaves parent_id empty for a module's own top-level
            # members, so a module's contents are found by module name too --
            # otherwise the module holding the sink comes back NO_SINK_PATH,
            # which is the false negative this card exists to avoid.
            held = list(self._children.get(element.id, []))
            if element.kind in {ElementKind.MODULE, ElementKind.PACKAGE}:
                held.extend(self._by_module.get(element.id, []))
            contained = [child for child in sorted(set(held)) if child in best]
            if not contained:
                continue
            winner_id = max(contained, key=lambda c: (_RANK[best[c]], c))
            best[element.id] = best[winner_id]
            via[element.id] = (element.id, *via[winner_id])
        return best, via, unknown_reason


    def _build_reachability(self) -> None:
        """One :class:`Reachability` per inventoried element. No exceptions:
        cards 5 and 15 read this file as the canonical answer, so an element
        missing from it is a hole in both."""
        best, via, side_effect_unknown = self._solve_reachability()
        behind_unknown = self._behind_unresolved()
        sinks = tuple(self._sink_ids)
        for element in sorted(self._elements.values(), key=lambda e: e.id):
            record_id = make_id("@reach", element.id)
            incident: list[Confidence] = []
            if element.id in best:
                confidence = best[element.id]
                path = via[element.id]
                if confidence is Confidence.CERTAIN:
                    reason = "reaches a decision sink along a path of CERTAIN edges"
                else:
                    reason = (
                        "reaches a decision sink along "
                        f"{' -> '.join(path)}, whose weakest link is {confidence}"
                        + ". Biased toward REACHES_SINK and kept at that link's confidence "
                        "rather than pruned: a false 'unreachable' sends the owner to "
                        "delete live code."
                    )
                self._reachability.append(
                    Reachability(
                        id=record_id,
                        element_id=element.id,
                        state=ReachabilityState.REACHES_SINK,
                        provenance=Provenance(
                            method=Method.CFG_REACHABILITY,
                            confidence=confidence,
                            span=element.span,
                            note=reason,
                        ),
                        sink_ids=(path[-1],),
                        path_ids=path,
                        reason=reason,
                    )
                )
                continue
            if not sinks:
                reason = (
                    "no decision sink is declared in TARGET_PROFILE.md and none was detected, "
                    "so nothing can be said about reaching one. UNKNOWN, not NO_SINK_PATH."
                )
                state = ReachabilityState.UNKNOWN
            elif element.id in side_effect_unknown:
                reason = side_effect_unknown[element.id]
                state = ReachabilityState.UNKNOWN
            elif element.id in behind_unknown:
                reason = (
                    "no resolved path to a sink, but an unresolved call site "
                    f"({behind_unknown[element.id]}) lies on the way, so a path may exist. "
                    "UNKNOWN, not NO_SINK_PATH."
                )
                state = ReachabilityState.UNKNOWN
            else:
                reason = (
                    "no wiring edge, and no unresolved call site, connects this element to "
                    "any decision sink"
                )
                state = ReachabilityState.NO_SINK_PATH
                incident = [
                    edge.provenance.confidence
                    for edge in self._edges
                    if edge.kind in WIRING_EDGE_KINDS
                    and element.id in {edge.source_id, edge.target_id}
                ]
            self._reachability.append(
                Reachability(
                    id=record_id,
                    element_id=element.id,
                    state=state,
                    provenance=Provenance(
                        method=Method.CFG_REACHABILITY,
                        # A NO_SINK_PATH verdict is a closed-world claim: it holds
                        # unless card 2 missed an edge, and where card 2 said it
                        # might have, the state above is UNKNOWN instead. It is
                        # therefore only as good as the edges that touch the
                        # element.
                        confidence=Confidence.UNKNOWN
                        if state is ReachabilityState.UNKNOWN
                        else combine(Confidence.RESOLVED, *incident),
                        span=element.span,
                        note=reason,
                    ),
                    sink_ids=sinks,
                    path_ids=(),
                    reason=reason,
                )
            )

    def _behind_unresolved(self) -> dict[str, str]:
        """Elements an unresolved call site could plausibly be calling.

        Only *opaque* residue counts: a call site card 2 could not resolve, a
        file that would not parse, a deferred lambda body. A condition that
        reads a name nobody inventoried opens no control path, so it does not
        turn a NO_SINK_PATH into an UNKNOWN and does not blind card 5.
        """
        by_path: dict[str, list[Element]] = {}
        for element in self._elements.values():
            if element.kind in CFG_ELEMENT_KINDS and element.kind is not ElementKind.MODULE:
                by_path.setdefault(element.span.path, []).append(element)
        for elements in by_path.values():
            elements.sort(key=lambda e: e.id)

        out: dict[str, str] = {}
        records = [
            *self._input_unresolved,
            *[record for record in self._unresolved if record.id in self._opaque],
        ]
        for record in sorted(records, key=lambda r: r.id):
            for candidate in record.candidate_ids:
                if candidate in self._elements:
                    out.setdefault(candidate, record.id)
            for element in by_path.get(record.span.path, []):
                if (
                    element.span.line <= record.span.line
                    and (element.span.end_line or element.span.line) >= record.span.line
                ):
                    out.setdefault(element.id, record.id)
        return out
