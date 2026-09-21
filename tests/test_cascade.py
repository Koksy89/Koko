"""Card 3 -- CFG, cascade ordering, decisions and decision reachability.

The FIXTURES.md cases that grade this card are `cfg_shapes`, `cfg_shortcircuit`,
`ord_linear`, `ord_branching`, `ord_unordered`, `ord_cycle`, `dec_rule_cascade`,
`dec_guard_clause`, `dec_sink` and `dec_uncertain_edge`.

Only `cfg_shapes` and `ord_linear` exist in `tests/fixtures/` at the time of
writing, and the card-3 sections of both expectation files are placeholders
(`"cfg_blocks": []` for a module that plainly has blocks; an order node whose id
`"order_linear"` follows no ID rule in the contract). Those two cases are
therefore graded here against their *source*, which is real, and against the
properties FIXTURES.md states the case proves. The eight missing cases are
covered by equivalent programs built in this file and are named in this card's
report, not quietly dropped.

Nothing here imports or executes a fixture. Sources are read as text and parsed
with `ast`, exactly as the card does. `test_sentinel_is_never_executed` proves
it.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Iterable, Sequence

import pytest

from cascade_map import cascade as C
from cascade_map.contracts.interfaces import (
    BlockKind,
    CascadeCard,
    CFGBlock,
    CFGEdge,
    Confidence,
    DecisionPoint,
    Edge,
    EdgeKind,
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
    make_id,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SENTINEL_MARKER = Path("/tmp/cascade_map_sentinel_marker.txt")

MISSING_FIXTURE_CASES = (
    "cfg_shortcircuit",
    "ord_branching",
    "ord_unordered",
    "ord_cycle",
    "dec_rule_cascade",
    "dec_guard_clause",
    "dec_sink",
    "dec_uncertain_edge",
)


# ---------------------------------------------------------------------------
# A minimal, test-only inventory and call graph.
#
# Cards 1 and 2 are being built in parallel, so this card is tested against the
# contract rather than against their current output: these helpers mint exactly
# the `Element` and `Edge` records the `CascadeCard` protocol promises.
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, name: str, source: str) -> str:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return name


def _prov(confidence: Confidence = Confidence.CERTAIN) -> Provenance:
    return Provenance(method=Method.AST_DIRECT, confidence=confidence)


def inventory(root: Path, rel: str) -> list[Element]:
    """Elements for one module: module, classes, functions, params, assigns."""
    module = rel[: -len(".py")].replace("/", ".") if rel.endswith(".py") else rel
    tree = ast.parse((root / rel).read_text(encoding="utf-8"), filename=rel)
    elements: list[Element] = [
        Element(
            id=make_id(module),
            kind=ElementKind.MODULE,
            name=module.rsplit(".", 1)[-1],
            qualname="",
            module=module,
            span=SourceSpan(path=rel, line=1, end_line=len(tree.body) and tree.body[-1].end_lineno),
            provenance=_prov(),
            content_hash="h",
        )
    ]
    counts: dict[str, int] = {}

    def add(node: ast.AST, kind: ElementKind, name: str, qualname: str, parent: str) -> str:
        counts[qualname] = counts.get(qualname, 0) + 1
        element_id = make_id(module, qualname, counts[qualname])
        elements.append(
            Element(
                id=element_id,
                kind=kind,
                name=name,
                qualname=qualname,
                module=module,
                span=SourceSpan(
                    path=rel,
                    line=int(getattr(node, "lineno", 1)),
                    end_line=getattr(node, "end_lineno", None),
                    col=getattr(node, "col_offset", None),
                ),
                provenance=_prov(),
                content_hash="h",
                parent_id=parent,
            )
        )
        return element_id

    def walk(body: Iterable[ast.stmt], prefix: str, parent: str, in_class: bool) -> None:
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}{stmt.name}"
                kind = ElementKind.METHOD if in_class else ElementKind.FUNCTION
                own = add(stmt, kind, stmt.name, qualname, parent)
                for arg in [*stmt.args.posonlyargs, *stmt.args.args, *stmt.args.kwonlyargs]:
                    if arg.arg in {"self", "cls"}:
                        continue
                    add(
                        arg,
                        ElementKind.PARAMETER,
                        arg.arg,
                        f"{qualname}.{arg.arg}",
                        own,
                    )
                walk(stmt.body, f"{qualname}.", own, False)
            elif isinstance(stmt, ast.ClassDef):
                qualname = f"{prefix}{stmt.name}"
                own = add(stmt, ElementKind.CLASS, stmt.name, qualname, parent)
                walk(stmt.body, f"{qualname}.", own, True)
            elif isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        add(
                            stmt,
                            ElementKind.ASSIGNMENT,
                            target.id,
                            f"{prefix}{target.id}",
                            parent,
                        )
            else:
                for name, value in ast.iter_fields(stmt):
                    if name in {"body", "orelse", "finalbody"} and isinstance(value, list):
                        walk([s for s in value if isinstance(s, ast.stmt)], prefix, parent, in_class)

    walk(tree.body, "", make_id(module), False)
    return elements


def call_edges(
    root: Path,
    rel: str,
    elements: Sequence[Element],
    *,
    confidence: Confidence = Confidence.RESOLVED,
    kind: EdgeKind = EdgeKind.CALLS,
    only: Sequence[str] = (),
) -> list[Edge]:
    """CALLS edges resolved by name inside one module, with call sites."""
    module = rel[: -len(".py")].replace("/", ".") if rel.endswith(".py") else rel
    by_name: dict[str, str] = {
        element.qualname.rsplit(".", 1)[-1]: element.id
        for element in elements
        if element.kind in {ElementKind.FUNCTION, ElementKind.METHOD, ElementKind.CLASS}
    }
    owners = sorted(
        (e for e in elements if e.kind in {ElementKind.FUNCTION, ElementKind.METHOD}),
        key=lambda e: (e.span.line, e.id),
    )

    def owner_of(line: int) -> str:
        best = make_id(module)
        for element in owners:
            if element.span.line <= line <= (element.span.end_line or element.span.line):
                best = element.id
        return best

    tree = ast.parse((root / rel).read_text(encoding="utf-8"), filename=rel)
    out: list[Edge] = []
    seen = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr if isinstance(node.func, ast.Attribute) else ""
        )
        target = by_name.get(name, "")
        if not target or (only and name not in only):
            continue
        source = owner_of(node.lineno)
        seen += 1
        out.append(
            Edge(
                id=make_id("@edge", f"{source}->{target}", seen),
                kind=kind,
                source_id=source,
                target_id=target,
                provenance=Provenance(method=Method.SCOPE_LOOKUP, confidence=confidence),
                call_site=SourceSpan(
                    path=rel, line=node.lineno, end_line=node.end_lineno, col=node.col_offset
                ),
            )
        )
    return sorted(out, key=lambda e: e.id)


def analyze(
    tmp_path: Path,
    source: str,
    *,
    name: str = "m.py",
    entry: Sequence[str] = (),
    sinks: Sequence[str] = (),
    extra_edges: Sequence[Edge] = (),
    unresolved: Sequence[Unresolved] = (),
    edge_confidence: Confidence = Confidence.RESOLVED,
) -> tuple[
    C.CascadeAnalyzer,
    list[CFGBlock],
    list[CFGEdge],
    list[OrderNode],
    list[DecisionPoint],
    list[Reachability],
]:
    rel = _write(tmp_path, name, source)
    elements = inventory(tmp_path, rel)
    edges = [*call_edges(tmp_path, rel, elements, confidence=edge_confidence), *extra_edges]
    analyzer = C.CascadeAnalyzer(tmp_path, sink_ids=sinks, unresolved=unresolved)
    blocks, cfg_edges, order, decisions, reach = analyzer.order(elements, edges, entry)
    return (
        analyzer,
        list(blocks),
        list(cfg_edges),
        list(order),
        list(decisions),
        list(reach),
    )


def fixture_elements(case: str) -> list[Element]:
    """Elements for a corpus case, taken from its own expectation file."""
    expected = json.loads((FIXTURES / "mode_b" / case / "expected.json").read_text())
    out: list[Element] = []
    for record in expected["elements"]:
        out.append(
            Element(
                id=record["id"],
                kind=ElementKind(record["kind"]),
                name=record["name"],
                qualname=record["qualname"],
                module=record["module"],
                span=SourceSpan(path=record["span"]["path"], line=record["span"]["line"]),
                provenance=_prov(),
                content_hash="h",
                parent_id=record.get("parent_id", ""),
            )
        )
    return out


def blocks_of(blocks: Sequence[CFGBlock], element_id: str) -> list[CFGBlock]:
    return [b for b in blocks if b.element_id == element_id]


def kinds_of(blocks: Sequence[CFGBlock], element_id: str) -> set[BlockKind]:
    return {b.kind for b in blocks_of(blocks, element_id)}


def out_edges(edges: Sequence[CFGEdge], block_id: str) -> list[CFGEdge]:
    return [e for e in edges if e.source_id == block_id]


def node(order: Sequence[OrderNode], node_id: str) -> OrderNode:
    for candidate in order:
        if candidate.id == node_id:
            return candidate
    raise AssertionError(f"no order node {node_id!r} in {[n.id for n in order]}")


def descendants(order: Sequence[OrderNode], root_id: str) -> list[OrderNode]:
    index = {n.id: n for n in order}
    seen: list[OrderNode] = []
    stack = [root_id]
    while stack:
        current = stack.pop()
        if current not in index:
            continue
        current_node = index[current]
        if current_node in seen:
            continue
        seen.append(current_node)
        stack.extend(current_node.children)
    return seen


def reach_of(records: Sequence[Reachability], element_id: str) -> Reachability:
    for record in records:
        if record.element_id == element_id:
            return record
    raise AssertionError(f"no reachability record for {element_id!r}")


# ---------------------------------------------------------------------------
# Missing corpus cases are reported, never fabricated
# ---------------------------------------------------------------------------


def test_missing_fixture_cases_are_named_not_invented() -> None:
    """FIXTURES.md names ten card-3 cases; eight have no directory yet.

    This test documents the gap and fails the day card 8 adds one, so the
    equivalent programs in this file get replaced by the real corpus case
    rather than living on as a private second corpus.
    """
    still_missing = sorted(
        case for case in MISSING_FIXTURE_CASES if not (FIXTURES / "mode_b" / case).is_dir()
    )
    assert still_missing == sorted(MISSING_FIXTURE_CASES), (
        "a card-3 fixture case has appeared; grade against it instead of the "
        f"equivalent program in this file: {sorted(set(MISSING_FIXTURE_CASES) - set(still_missing))}"
    )


def test_existing_card3_expectations_are_placeholders() -> None:
    """`cfg_shapes` and `ord_linear` ship empty/derived card-3 expectations.

    `cfg_shapes` declares `"cfg_blocks": []` for a module with four functions,
    and `ord_linear` declares an order node id (`order_linear`) that no rule in
    the contract produces. Neither can be asserted against, so the two cases are
    graded from their source below. Recording it here keeps the divergence
    visible instead of silent.
    """
    cfg = json.loads((FIXTURES / "mode_b" / "cfg_shapes" / "expected.json").read_text())
    assert cfg["cfg_blocks"] == [] and cfg["cfg_edges"] == []
    order = json.loads((FIXTURES / "mode_b" / "ord_linear" / "expected.json").read_text())
    assert [n["id"] for n in order["order"]] == ["order_linear"]


# ---------------------------------------------------------------------------
# cfg_shapes -- branches, loops, try/except, with, comprehension, match, exits
# ---------------------------------------------------------------------------


def test_cfg_shapes_fixture_branch_loop_handler_and_with() -> None:
    elements = fixture_elements("cfg_shapes")
    analyzer = C.CascadeAnalyzer(REPO_ROOT)
    blocks, edges, _order, _decisions, _reach = analyzer.order(elements, [], [])

    branch_blocks = [
        b for b in blocks_of(blocks, "cfg_shapes::branching_code") if b.kind is BlockKind.BRANCH
    ]
    assert len(branch_blocks) == 1
    arms = out_edges(edges, branch_blocks[0].id)
    assert sorted(e.taken_when for e in arms) == [False, True]
    assert {e.condition for e in arms} == {"x > 0"}
    assert kinds_of(blocks, "cfg_shapes::branching_code") >= {
        BlockKind.ENTRY,
        BlockKind.BRANCH,
        BlockKind.RETURN,
        BlockKind.EXIT,
    }

    heads = [b for b in blocks_of(blocks, "cfg_shapes::loop_code") if b.kind is BlockKind.LOOP_HEAD]
    assert len(heads) == 1
    head = heads[0].id
    assert any(e.target_id == head and e.condition == "<loop back>" for e in edges)
    assert {e.taken_when for e in out_edges(edges, head)} == {True, False}

    handlers = [
        b for b in blocks_of(blocks, "cfg_shapes::exception_handling") if b.kind is BlockKind.HANDLER
    ]
    assert len(handlers) == 1
    assert any(
        e.target_id == handlers[0].id and e.condition == "ValueError" for e in edges
    ), "the try body must have an exception edge into its handler"

    with_conditions = {
        e.condition
        for e in edges
        if e.source_id.startswith("cfg_shapes::with_statement")
        or e.target_id.startswith("cfg_shapes::with_statement")
    }
    assert "<enter context>" in with_conditions

    block_ids = {b.id for b in blocks}
    assert len(block_ids) == len(blocks), "block ids must be unique"
    for edge in edges:
        assert edge.source_id in block_ids and edge.target_id in block_ids
        assert edge.provenance is not None


def test_cfg_covers_try_finally_match_comprehension_and_early_exit(tmp_path: Path) -> None:
    source = '''
def shapes(rows, mode):
    picked = [r for r in rows if r > 0]
    if not picked:
        return None
    try:
        total = sum(picked)
    except ValueError:
        total = 0
    finally:
        seen = True
    match mode:
        case "sum":
            return total
        case _:
            pass
    for row in picked:
        if row < 0:
            continue
        if row > 100:
            break
    else:
        total = 0
    assert total >= 0
    raise SystemExit(total)
'''
    _a, blocks, edges, _order, _dec, _reach = analyze(tmp_path, source)
    kinds = kinds_of(blocks, "m::shapes")
    assert kinds >= {
        BlockKind.ENTRY,
        BlockKind.NORMAL,
        BlockKind.BRANCH,
        BlockKind.LOOP_HEAD,
        BlockKind.HANDLER,
        BlockKind.FINALLY,
        BlockKind.RETURN,
        BlockKind.RAISE,
        BlockKind.EXIT,
    }
    notes = {b.provenance.note for b in blocks}
    assert any(n.startswith("comprehension for") for n in notes)
    assert any(n.startswith("case ") for n in notes)
    conditions = {e.condition for e in edges}
    assert {"<break>", "<continue>", "<enter finally>", "<return>"} <= conditions
    finally_blocks = [b for b in blocks if b.kind is BlockKind.FINALLY]
    assert finally_blocks
    assert any(e.target_id == finally_blocks[0].id for e in edges)


def test_early_return_goes_to_exit_not_to_the_next_statement(tmp_path: Path) -> None:
    source = '''
def f(x):
    if x:
        return 1
    return 2
'''
    _a, blocks, edges, _order, _dec, _reach = analyze(tmp_path, source)
    returns = [b for b in blocks if b.kind is BlockKind.RETURN]
    exits = [b for b in blocks if b.kind is BlockKind.EXIT]
    assert len(returns) == 2 and len(exits) == 1
    for ret in returns:
        assert [e.target_id for e in out_edges(edges, ret.id)] == [exits[0].id]


# ---------------------------------------------------------------------------
# cfg_shortcircuit -- `and`/`or` are branches, not expressions
# (FIXTURES.md case `cfg_shortcircuit` does not exist yet)
# ---------------------------------------------------------------------------


def test_short_circuit_and_produces_branch_edges(tmp_path: Path) -> None:
    source = '''
def gate(a, b):
    return cheap(a) and expensive(b)


def cheap(a):
    return a


def expensive(b):
    return b
'''
    _a, blocks, edges, order, decisions, _reach = analyze(tmp_path, source)
    branches = [b for b in blocks_of(blocks, "m::gate") if b.kind is BlockKind.BRANCH]
    assert len(branches) == 1, "`and` must produce exactly one branch block"
    arms = out_edges(edges, branches[0].id)
    assert sorted(e.taken_when for e in arms) == [False, True]
    assert {e.condition for e in arms} == {"cheap(a)"}
    short = [e for e in arms if e.taken_when is False][0]
    assert "short circuit" in (short.provenance.note if short.provenance else "")

    decision = [d for d in decisions if d.condition_source == "cheap(a)"]
    assert len(decision) == 1
    assert "SHORT_CIRCUIT" in (decision[0].provenance.note if decision[0].provenance else "")

    # The right-hand call belongs inside the arm that evaluates it, never as a
    # sibling of the left-hand call in one flat sequence.
    evaluate_arm = [
        n for n in order if n.provenance and n.provenance.note.startswith("arm `evaluate ")
    ]
    assert len(evaluate_arm) == 1
    inside = {
        element_id
        for child in descendants(order, evaluate_arm[0].id)
        for element_id in child.element_ids
    }
    assert "m::expensive" in inside
    assert "m::cheap" not in inside


def test_short_circuit_or_inverts_the_taken_when(tmp_path: Path) -> None:
    source = '''
def gate(a, b):
    return cheap(a) or expensive(b)


def cheap(a):
    return a


def expensive(b):
    return b
'''
    _a, blocks, edges, _order, _dec, _reach = analyze(tmp_path, source)
    branch = [b for b in blocks_of(blocks, "m::gate") if b.kind is BlockKind.BRANCH][0]
    arms = out_edges(edges, branch.id)
    short = [e for e in arms if e.provenance and "short circuit" in e.provenance.note]
    assert len(short) == 1
    assert short[0].taken_when is True, "`or` short-circuits when the left side is true"


# ---------------------------------------------------------------------------
# ord_linear -- a fixed order is a SEQUENCE
# ---------------------------------------------------------------------------


def test_ord_linear_fixture_is_a_total_sequence() -> None:
    elements = fixture_elements("ord_linear")
    rel = "tests/fixtures/mode_b/ord_linear/__init__.py"
    edges = call_edges(REPO_ROOT, rel, elements)
    analyzer = C.CascadeAnalyzer(REPO_ROOT)
    _b, _e, order, _d, reach = analyzer.order(elements, edges, ["ord_linear::main"])

    total = node(order, make_id("@order", "@total"))
    assert total.kind is OrderKind.SEQUENCE
    assert list(total.element_ids) == [
        "ord_linear::main",
        "ord_linear::third",
        "ord_linear::second",
        "ord_linear::first",
    ], "elements are listed from the moment each starts running"
    assert total.provenance is not None
    assert total.provenance.confidence is Confidence.RESOLVED

    root = node(order, make_id("@order", "@cascade"))
    assert root.kind is OrderKind.SEQUENCE
    assert root.element_ids == ("ord_linear::main",)
    assert not [n for n in order if n.kind in {OrderKind.BRANCH, OrderKind.LOOP, OrderKind.CYCLE}]

    main_root = node(order, make_id("@order", "ord_linear::main"))
    called = {
        element_id
        for child in descendants(order, main_root.id)
        for element_id in child.element_ids
    }
    assert "ord_linear::third" in called

    for element in elements:
        assert reach_of(reach, element.id).state is ReachabilityState.UNKNOWN


# ---------------------------------------------------------------------------
# ord_branching -- a branch is never flattened  (case does not exist yet)
# ---------------------------------------------------------------------------


def test_ord_branching_yields_branch_and_merge_never_a_sequence(tmp_path: Path) -> None:
    source = '''
def main(flag):
    prepare()
    if flag:
        left()
    else:
        right()
    finish()


def prepare():
    return 1


def left():
    return 2


def right():
    return 3


def finish():
    return 4
'''
    _a, _blocks, _edges, order, _dec, _reach = analyze(tmp_path, source, entry=["m::main"])
    branch = [n for n in order if n.kind is OrderKind.BRANCH]
    assert len(branch) == 1
    merges = [n for n in order if n.kind is OrderKind.MERGE]
    assert len(merges) == 1

    main_root = node(order, make_id("@order", "m::main"))
    assert branch[0].id in main_root.children and merges[0].id in main_root.children
    assert main_root.children.index(branch[0].id) < main_root.children.index(merges[0].id)

    # The two arms must never appear in one sequence together.
    for candidate in order:
        if candidate.kind is OrderKind.SEQUENCE:
            assert not {"m::left", "m::right"} <= set(candidate.element_ids)

    arms = [node(order, child) for child in branch[0].children]
    arm_elements = [
        {e for child in descendants(order, arm.id) for e in child.element_ids} for arm in arms
    ]
    assert any("m::left" in group for group in arm_elements)
    assert any("m::right" in group for group in arm_elements)

    assert not [
        n for n in order if n.id == make_id("@order", "@total")
    ], "a branching cascade has no total order and must not claim one"


# ---------------------------------------------------------------------------
# ord_unordered -- elements that run in no fixed order  (case does not exist yet)
# ---------------------------------------------------------------------------


def test_ord_unordered_registry_dispatch_is_not_a_sequence(tmp_path: Path) -> None:
    source = '''
HANDLERS = [alpha, beta]


def main():
    for handler in HANDLERS:
        handler()


def alpha():
    return 1


def beta():
    return 2
'''
    rel = _write(tmp_path, "m.py", source)
    elements = inventory(tmp_path, rel)
    call_line = source.splitlines().index("        handler()") + 1
    dispatch = [
        Edge(
            id=make_id("@edge", f"dispatch->{target}"),
            kind=EdgeKind.REGISTERS,
            source_id="m::main",
            target_id=target,
            provenance=Provenance(
                method=Method.REGISTRY_MEMBERSHIP, confidence=Confidence.PROBABLE
            ),
            call_site=SourceSpan(path=rel, line=call_line, col=8),
        )
        for target in ("m::alpha", "m::beta")
    ]
    analyzer = C.CascadeAnalyzer(tmp_path)
    _b, _e, order, _d, _r = analyzer.order(elements, dispatch, ["m::main"])

    unordered = [n for n in order if n.kind is OrderKind.UNORDERED]
    assert len(unordered) == 1
    assert unordered[0].element_ids == ("m::alpha", "m::beta")
    assert unordered[0].provenance is not None
    assert unordered[0].provenance.confidence is Confidence.PROBABLE
    assert "order" in unordered[0].provenance.note
    for candidate in order:
        if candidate.kind is OrderKind.SEQUENCE:
            assert not {"m::alpha", "m::beta"} <= set(candidate.element_ids)


def test_wiring_with_no_call_site_is_unordered_not_dropped(tmp_path: Path) -> None:
    source = '''
def main():
    return 1


def plugin():
    return 2
'''
    rel = _write(tmp_path, "m.py", source)
    elements = inventory(tmp_path, rel)
    configured = Edge(
        id=make_id("@edge", "config->plugin"),
        kind=EdgeKind.CONFIGURES,
        source_id="m::main",
        target_id="m::plugin",
        provenance=Provenance(
            method=Method.CONFIG_STRING_MATCH, confidence=Confidence.HEURISTIC
        ),
    )
    analyzer = C.CascadeAnalyzer(tmp_path)
    _b, _e, order, _d, _r = analyzer.order(elements, [configured], ["m::main"])
    unordered = [n for n in order if n.kind is OrderKind.UNORDERED]
    assert len(unordered) == 1
    assert unordered[0].element_ids == ("m::plugin",)
    assert unordered[0].provenance is not None
    assert unordered[0].provenance.confidence is Confidence.HEURISTIC


def test_multiple_entry_points_are_unordered_at_the_root(tmp_path: Path) -> None:
    source = '''
def main_a():
    return 1


def main_b():
    return 2
'''
    _a, _b, _e, order, _d, _r = analyze(tmp_path, source, entry=["m::main_a", "m::main_b"])
    root = node(order, make_id("@order", "@cascade"))
    assert root.kind is OrderKind.UNORDERED
    assert root.element_ids == ("m::main_a", "m::main_b")


# ---------------------------------------------------------------------------
# ord_cycle -- recursion is a cycle with its members  (case does not exist yet)
# ---------------------------------------------------------------------------


def test_ord_cycle_reports_mutual_recursion_with_member_ids(tmp_path: Path) -> None:
    source = '''
def main():
    return ping(3)


def ping(n):
    return pong(n - 1)


def pong(n):
    return ping(n - 1)
'''
    _a, _b, _e, order, _d, _r = analyze(tmp_path, source, entry=["m::main"])
    cycles = [n for n in order if n.kind is OrderKind.CYCLE]
    assert len(cycles) == 1
    assert cycles[0].element_ids == ("m::ping", "m::pong")
    assert cycles[0].id in node(order, make_id("@order", "@cascade")).children
    assert not [n for n in order if n.id == make_id("@order", "@total")]


def test_direct_recursion_is_a_cycle(tmp_path: Path) -> None:
    source = '''
def main():
    return fact(3)


def fact(n):
    return fact(n - 1)
'''
    _a, _b, _e, order, _d, _r = analyze(tmp_path, source, entry=["m::main"])
    cycles = [n for n in order if n.kind is OrderKind.CYCLE]
    assert len(cycles) == 1 and cycles[0].element_ids == ("m::fact",)


# ---------------------------------------------------------------------------
# dec_rule_cascade / dec_guard_clause  (cases do not exist yet)
# ---------------------------------------------------------------------------


RULES_SOURCE = '''
THRESHOLD = 10


def classify(row, score):
    if score > THRESHOLD:
        return "strong"
    elif row["price"] > 0:
        return "weak"
    elif score < 0:
        return "negative"
    else:
        return "flat"
'''


def test_dec_rule_cascade_records_condition_reads_and_outcomes(tmp_path: Path) -> None:
    _a, _b, _e, order, decisions, _r = analyze(tmp_path, RULES_SOURCE)
    in_classify = [d for d in decisions if d.element_id == "m::classify"]
    assert [d.condition_source for d in in_classify] == [
        "score > THRESHOLD",
        "row['price'] > 0",
        "score < 0",
    ]
    assert [d.id for d in in_classify] == [
        "m::classify::@decision0",
        "m::classify::@decision1",
        "m::classify::@decision2",
    ]

    first = in_classify[0]
    assert first.reads_ids == ("m::THRESHOLD", "m::classify.score")
    assert [label for label, _ in first.outcomes] == ["true", "false"]
    for _label, target in first.outcomes:
        assert node(order, target).kind is OrderKind.SEQUENCE
    assert first.provenance is not None
    assert "rule cascade: step 1 of 3" in first.provenance.note

    second = in_classify[1]
    assert "@feature:price" in second.reads_ids, "a named feature is its own node"
    assert "rule cascade: step 2 of 3" in (second.provenance.note if second.provenance else "")


def test_dec_guard_clause_is_marked_and_its_arm_leaves(tmp_path: Path) -> None:
    source = '''
def handle(order_book):
    if order_book is None:
        return "no book"
    if not order_book:
        raise ValueError("empty")
    return "ok"
'''
    _a, blocks, edges, _order, decisions, _r = analyze(tmp_path, source)
    assert [d.condition_source for d in decisions] == [
        "order_book is None",
        "not order_book",
    ]
    for decision in decisions:
        assert decision.provenance is not None
        assert "guard clause" in decision.provenance.note
        assert decision.provenance.note.startswith("GUARD")
        assert decision.reads_ids == ("m::handle.order_book",)
    raises = [b for b in blocks if b.kind is BlockKind.RAISE]
    assert len(raises) == 1
    exits = [b for b in blocks if b.kind is BlockKind.EXIT]
    assert [e.target_id for e in out_edges(edges, raises[0].id)] == [exits[0].id]


def test_match_and_ternary_and_assert_are_decision_points(tmp_path: Path) -> None:
    source = '''
def pick(mode, score):
    label = "high" if score > 5 else "low"
    assert label
    match mode:
        case "a":
            return label
        case _:
            return "none"
'''
    _a, _b, _e, _order, decisions, _r = analyze(tmp_path, source)
    notes = [d.provenance.note.split(";")[0] for d in decisions if d.provenance]
    assert notes == ["TERNARY", "ASSERT", "MATCH"]
    match_decision = decisions[-1]
    assert match_decision.condition_source == "match mode"
    assert [label for label, _ in match_decision.outcomes] == ["case 'a'", "case _"]


def test_tree_model_call_is_a_decision_point(tmp_path: Path) -> None:
    source = '''
def score(model, features):
    return model.predict(features)
'''
    _a, _b, _e, _order, decisions, _r = analyze(tmp_path, source)
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.condition_source == "model.predict(features)"
    assert decision.provenance is not None
    assert decision.provenance.method is Method.NAME_HEURISTIC
    assert decision.provenance.confidence is Confidence.HEURISTIC
    assert set(decision.reads_ids) == {"m::score.features", "m::score.model"}


# ---------------------------------------------------------------------------
# dec_sink and reachability  (cases do not exist yet)
# ---------------------------------------------------------------------------


SINK_SOURCE = '''
def main(raw):
    frame = ingest(raw)
    features = engineer(frame)
    return final_decision(features)


def ingest(raw):
    return raw


def engineer(frame):
    return frame


def final_decision(features):
    if features:
        return "BUY"
    return "HOLD"


def orphan():
    return "nobody calls me"
'''


def test_dec_sink_declared_marks_the_chain_reachable(tmp_path: Path) -> None:
    analyzer, _b, _e, _order, decisions, reach = analyze(
        tmp_path, SINK_SOURCE, entry=["m::main"], sinks=["m::final_decision"]
    )
    assert analyzer.sink_ids() == ("m::final_decision",)
    for element_id in ("m::main", "m::ingest", "m::engineer", "m::final_decision"):
        record = reach_of(reach, element_id)
        assert record.state is ReachabilityState.REACHES_SINK, element_id
        assert record.sink_ids == ("m::final_decision",)
        assert record.path_ids[0] == element_id
        assert record.path_ids[-1] == "m::final_decision"
        assert record.provenance.method is Method.CFG_REACHABILITY

    orphan = reach_of(reach, "m::orphan")
    assert orphan.state is ReachabilityState.NO_SINK_PATH
    assert orphan.reason

    sink_decisions = [d for d in decisions if d.element_id == "m::final_decision"]
    assert sink_decisions and all(d.is_sink for d in sink_decisions)
    assert not any(d.is_sink for d in decisions if d.element_id != "m::final_decision")


def test_a_parameter_of_a_live_function_is_live(tmp_path: Path) -> None:
    _a, _b, _e, _order, _d, reach = analyze(
        tmp_path, SINK_SOURCE, entry=["m::main"], sinks=["m::final_decision"]
    )
    assert reach_of(reach, "m::main.raw").state is ReachabilityState.REACHES_SINK
    assert reach_of(reach, "m::orphan").state is ReachabilityState.NO_SINK_PATH


def test_every_element_gets_exactly_one_reachability_record(tmp_path: Path) -> None:
    rel = _write(tmp_path, "m.py", SINK_SOURCE)
    elements = inventory(tmp_path, rel)
    edges = call_edges(tmp_path, rel, elements)
    analyzer = C.CascadeAnalyzer(tmp_path, sink_ids=["m::final_decision"])
    *_rest, reach = analyzer.order(elements, edges, ["m::main"])
    assert sorted(r.element_id for r in reach) == sorted(e.id for e in elements)
    assert len({r.id for r in reach}) == len(reach)
    kinds = {e.kind for e in elements}
    assert ElementKind.MODULE in kinds and ElementKind.PARAMETER in kinds
    assert all(r.reason for r in reach)
    assert list(reach) == list(analyzer.reachability())


def test_class_and_module_get_records_even_without_call_edges(tmp_path: Path) -> None:
    source = '''
class Strategy:
    def evaluate(self):
        return 1


def final_decision():
    return "BUY"
'''
    _a, _b, _e, _order, _d, reach = analyze(tmp_path, source, sinks=["m::final_decision"])
    module = reach_of(reach, "m")
    klass = reach_of(reach, "m::Strategy")
    method = reach_of(reach, "m::Strategy.evaluate")
    assert module.state is ReachabilityState.REACHES_SINK  # it contains the sink
    assert klass.state is ReachabilityState.NO_SINK_PATH
    assert method.state is ReachabilityState.NO_SINK_PATH
    assert klass.reason and method.reason


def test_dec_uncertain_edge_stays_reachable_with_its_reason(tmp_path: Path) -> None:
    """FIXTURES.md `dec_uncertain_edge`: reachable only via a HEURISTIC edge."""
    source = '''
def main():
    return 1


def hidden():
    return final_decision()


def final_decision():
    return "BUY"
'''
    rel = _write(tmp_path, "m.py", source)
    elements = inventory(tmp_path, rel)
    edges = [
        *call_edges(tmp_path, rel, elements),
        Edge(
            id=make_id("@edge", "guess->hidden"),
            kind=EdgeKind.CALLS,
            source_id="m::main",
            target_id="m::hidden",
            provenance=Provenance(method=Method.NAME_HEURISTIC, confidence=Confidence.HEURISTIC),
            call_site=SourceSpan(path=rel, line=3, col=11),
        ),
    ]
    analyzer = C.CascadeAnalyzer(tmp_path, sink_ids=["m::final_decision"])
    *_rest, reach = analyzer.order(elements, edges, ["m::main"])

    main = reach_of(reach, "m::main")
    assert main.state is ReachabilityState.REACHES_SINK, "never pruned for being uncertain"
    assert main.provenance.confidence is Confidence.HEURISTIC, "weakest edge on the path"
    assert "HEURISTIC" in main.reason and "delete live code" in main.reason
    assert main.path_ids == ("m::main", "m::hidden", "m::final_decision")
    assert reach_of(reach, "m::hidden").provenance.confidence is Confidence.RESOLVED


def test_element_behind_an_unresolved_call_site_is_unknown_not_no_path(tmp_path: Path) -> None:
    source = '''
def main(name):
    fn = globals()[name]
    return fn()


def maybe_called():
    return 1


def final_decision():
    return "BUY"
'''
    rel = _write(tmp_path, "m.py", source)
    elements = inventory(tmp_path, rel)
    residue = Unresolved(
        id=make_id("@unresolved", "m::main::dynamic"),
        reason=UnresolvedReason.DYNAMIC_NAME,
        span=SourceSpan(path=rel, line=4, col=11),
        description="call through a computed name",
        attempted=(Method.GETATTR_TRACED,),
        candidate_ids=("m::maybe_called",),
        candidate_confidence=Confidence.UNKNOWN,
    )
    analyzer = C.CascadeAnalyzer(
        tmp_path, sink_ids=["m::final_decision"], unresolved=[residue]
    )
    *_rest, reach = analyzer.order(elements, [], ["m::main"])

    candidate = reach_of(reach, "m::maybe_called")
    assert candidate.state is ReachabilityState.UNKNOWN
    assert candidate.state is not ReachabilityState.NO_SINK_PATH
    assert residue.id in candidate.reason
    assert candidate.provenance.confidence is Confidence.UNKNOWN

    caller = reach_of(reach, "m::main")
    assert caller.state is ReachabilityState.UNKNOWN, "the unresolved site is inside main"


def test_no_sink_means_unknown_everywhere_never_unreachable(tmp_path: Path) -> None:
    analyzer, _b, _e, _order, _d, reach = analyze(tmp_path, SINK_SOURCE, entry=["m::main"])
    assert analyzer.sink_ids() == ("m::final_decision",), "auto-detected by name"
    assert [c.provenance.confidence for c in analyzer.sink_candidates()] == [
        Confidence.HEURISTIC
    ]

    source = '''
def alpha():
    return 1
'''
    analyzer2, _b2, _e2, _o2, _d2, reach2 = analyze(tmp_path, source, name="n.py")
    assert analyzer2.sink_ids() == ()
    assert {r.state for r in reach2} == {ReachabilityState.UNKNOWN}
    assert all("no decision sink" in r.reason for r in reach2)
    assert any(u.reason is UnresolvedReason.MISSING_TARGET for u in analyzer2.unresolved())


def test_unresolvable_condition_read_does_not_blind_reachability(tmp_path: Path) -> None:
    """A condition naming something uninventoried opens no control path.

    Only opaque residue -- an unresolved call site, an unparsable file, a
    deferred lambda -- may turn NO_SINK_PATH into UNKNOWN. Otherwise a single
    unknown name would make the whole map UNKNOWN and card 5 would see nothing.
    """
    source = '''
def orphan(x):
    if numpy.isnan(x):
        return 1
    return 2


def final_decision():
    return "BUY"
'''
    analyzer, _b, _e, _order, _d, reach = analyze(
        tmp_path, source, sinks=["m::final_decision"]
    )
    assert any(u.id.endswith("@read:numpy.isnan") for u in analyzer.unresolved())
    assert reach_of(reach, "m::orphan").state is ReachabilityState.NO_SINK_PATH


# ---------------------------------------------------------------------------
# Auto-detection is reported, never silently adopted
# ---------------------------------------------------------------------------


def test_entry_point_is_auto_detected_and_reported(tmp_path: Path) -> None:
    source = '''
def main():
    return work()


def work():
    return 1


if __name__ == "__main__":
    main()
'''
    analyzer, _b, _e, _order, _d, _r = analyze(tmp_path, source, name="run_m5.py")
    candidates = analyzer.entry_point_candidates()
    assert [c.element_id for c in candidates] == ["run_m5", "run_m5::main"]
    for candidate in candidates:
        assert candidate.role == "ENTRY_POINT"
        assert candidate.evidence
        assert candidate.provenance.confidence is Confidence.PROBABLE
        assert "auto-detected" in candidate.provenance.note


def test_declared_entry_that_is_not_inventoried_is_reported(tmp_path: Path) -> None:
    analyzer, _b, _e, _order, _d, _r = analyze(
        tmp_path, "def main():\n    return 1\n", entry=["m::nope"]
    )
    residue = [u for u in analyzer.unresolved() if "nope" in u.description]
    assert len(residue) == 1
    assert residue[0].reason is UnresolvedReason.MISSING_TARGET


def test_a_lambda_body_is_unordered_and_reported_not_stitched_in(tmp_path: Path) -> None:
    source = '''
def main():
    fn = lambda: helper()
    return fn


def helper():
    return 1
'''
    analyzer, _b, _e, order, _d, _r = analyze(tmp_path, source, entry=["m::main"])
    ambiguous = [u for u in analyzer.unresolved() if u.reason is UnresolvedReason.AMBIGUOUS]
    assert len(ambiguous) == 1 and "lambda" in ambiguous[0].description
    deferred = node(order, make_id("@order", "m::main/deferred"))
    assert deferred.kind is OrderKind.UNORDERED
    inside = {e for child in descendants(order, deferred.id) for e in child.element_ids}
    assert "m::helper" in inside


# ---------------------------------------------------------------------------
# Unresolved, provenance, determinism, safety
# ---------------------------------------------------------------------------


def test_an_unparsable_file_is_reported_and_the_run_continues(tmp_path: Path) -> None:
    rel = _write(tmp_path, "bad.py", "def broken(:\n    pass\n")
    elements = [
        Element(
            id="bad",
            kind=ElementKind.MODULE,
            name="bad",
            qualname="",
            module="bad",
            span=SourceSpan(path=rel, line=1),
            provenance=_prov(),
            content_hash="h",
        )
    ]
    analyzer = C.CascadeAnalyzer(tmp_path)
    blocks, _e, _o, _d, reach = analyzer.order(elements, [], [])
    assert blocks == ()
    assert any(u.reason is UnresolvedReason.SYNTAX_ERROR for u in analyzer.unresolved())
    assert len(reach) == 1, "an unparsable module still gets a reachability record"


def test_a_missing_source_file_is_reported(tmp_path: Path) -> None:
    elements = [
        Element(
            id="gone",
            kind=ElementKind.MODULE,
            name="gone",
            qualname="",
            module="gone",
            span=SourceSpan(path="gone.py", line=1),
            provenance=_prov(),
            content_hash="h",
        )
    ]
    analyzer = C.CascadeAnalyzer(tmp_path)
    analyzer.order(elements, [], [])
    assert [u.reason for u in analyzer.unresolved()].count(UnresolvedReason.MISSING_TARGET) >= 1


def test_every_emitted_fact_carries_provenance(tmp_path: Path) -> None:
    _a, blocks, edges, order, decisions, reach = analyze(
        tmp_path, RULES_SOURCE, entry=["m::classify"], sinks=["m::classify"]
    )
    for block in blocks:
        assert block.provenance.method is Method.AST_DIRECT
        assert block.provenance.confidence is Confidence.CERTAIN
    for edge in edges:
        assert edge.provenance is not None and edge.provenance.method is Method.AST_DIRECT
    for order_node in order:
        assert order_node.provenance is not None and order_node.provenance.note
    for decision in decisions:
        assert decision.provenance is not None and decision.provenance.span is not None
    for record in reach:
        assert record.provenance.method is Method.CFG_REACHABILITY


def test_order_confidence_is_combined_from_the_edges_it_rests_on(tmp_path: Path) -> None:
    source = '''
def main():
    return step()


def step():
    return 1
'''
    for confidence in (Confidence.RESOLVED, Confidence.PROBABLE, Confidence.HEURISTIC):
        _a, _b, _e, order, _d, _r = analyze(
            tmp_path, source, entry=["m::main"], edge_confidence=confidence
        )
        root = node(order, make_id("@order", "m::main"))
        assert root.provenance is not None
        assert root.provenance.confidence is confidence, confidence
        total = node(order, make_id("@order", "@total"))
        assert total.provenance is not None and total.provenance.confidence is confidence


def test_an_unresolved_call_site_makes_its_order_node_unknown(tmp_path: Path) -> None:
    source = '''
def main():
    return mystery()
'''
    _a, _b, _e, order, _d, _r = analyze(tmp_path, source, entry=["m::main"])
    unknown = [
        n
        for n in order
        if n.provenance is not None and n.provenance.confidence is Confidence.UNKNOWN
    ]
    assert unknown, "a call nobody resolved must not be silently dropped from the order"
    assert any("no resolved target" in n.provenance.note for n in unknown if n.provenance)


def test_rank_agrees_with_combine() -> None:
    """`_RANK` mirrors the contract's ordering; this fails if it ever drifts."""
    levels = list(Confidence)
    for left in levels:
        for right in levels:
            weaker = left if C._RANK[left] <= C._RANK[right] else right
            assert combine(left, right) is weaker


def test_two_runs_are_byte_identical(tmp_path: Path) -> None:
    rel = _write(tmp_path, "m.py", SINK_SOURCE)
    elements = inventory(tmp_path, rel)
    edges = call_edges(tmp_path, rel, elements)
    first = C.CascadeAnalyzer(tmp_path, sink_ids=["m::final_decision"])
    first.order(elements, edges, ["m::main"])
    second = C.CascadeAnalyzer(tmp_path, sink_ids=["m::final_decision"])
    second.order(list(reversed(elements)), list(reversed(edges)), ["m::main"])
    assert first.artifacts() == second.artifacts()
    assert set(first.artifacts()) == {
        "cfg_blocks.jsonl",
        "cfg_edges.jsonl",
        "order.jsonl",
        "decisions.jsonl",
        "reachability.jsonl",
    }
    for text in first.artifacts().values():
        assert text == "" or text.endswith("\n")


def test_reachability_jsonl_round_trips_deterministically(tmp_path: Path) -> None:
    _a, _b, _e, _o, _d, reach = analyze(
        tmp_path, SINK_SOURCE, entry=["m::main"], sinks=["m::final_decision"]
    )
    rendered = canonical_jsonl(reach)
    assert rendered == canonical_jsonl(list(reversed(reach)))
    rows = [json.loads(line) for line in rendered.splitlines()]
    assert [r["id"] for r in rows] == sorted(r["id"] for r in rows)
    assert {r["state"] for r in rows} <= {s.value for s in ReachabilityState}
    revived = [
        Reachability(
            id=row["id"],
            element_id=row["element_id"],
            state=ReachabilityState(row["state"]),
            provenance=Provenance(
                method=Method(row["provenance"]["method"]),
                confidence=Confidence(row["provenance"]["confidence"]),
                span=SourceSpan(**row["provenance"]["span"])
                if row["provenance"]["span"]
                else None,
                note=row["provenance"]["note"],
            ),
            sink_ids=tuple(row["sink_ids"]),
            path_ids=tuple(row["path_ids"]),
            reason=row["reason"],
        )
        for row in rows
    ]
    assert canonical_jsonl(revived) == rendered


def test_whole_corpus_is_deterministic_and_all_records_sorted() -> None:
    cases = sorted(p.name for p in (FIXTURES / "mode_b").iterdir() if p.is_dir())
    assert cases, "the mode_b corpus is empty"
    for case in cases:
        expected = FIXTURES / "mode_b" / case / "expected.json"
        if not expected.is_file():
            continue
        payload = json.loads(expected.read_text())
        if not payload.get("elements"):
            continue
        elements = fixture_elements(case)
        analyzer_a = C.CascadeAnalyzer(REPO_ROOT)
        results_a = analyzer_a.order(elements, [], [])
        analyzer_b = C.CascadeAnalyzer(REPO_ROOT)
        results_b = analyzer_b.order(list(reversed(elements)), [], [])
        assert analyzer_a.artifacts() == analyzer_b.artifacts(), case
        for records in results_a:
            ids = [r.id for r in records]
            assert ids == sorted(ids), case


def test_the_card_satisfies_the_cascade_card_protocol(tmp_path: Path) -> None:
    card: CascadeCard = C.CascadeAnalyzer(tmp_path)
    rel = _write(tmp_path, "m.py", SINK_SOURCE)
    elements = inventory(tmp_path, rel)
    result = card.order(elements, call_edges(tmp_path, rel, elements), ["m::main"])
    assert len(result) == 5
    blocks, cfg_edges, order, decisions, reach = result
    assert all(isinstance(b, CFGBlock) for b in blocks)
    assert all(isinstance(e, CFGEdge) for e in cfg_edges)
    assert all(isinstance(n, OrderNode) for n in order)
    assert all(isinstance(d, DecisionPoint) for d in decisions)
    assert all(isinstance(r, Reachability) for r in reach)


def test_sentinel_is_never_executed() -> None:
    """Constraint 1, proved empirically: analysing the sentinel does not run it."""
    if SENTINEL_MARKER.exists():
        pytest.fail(
            f"{SENTINEL_MARKER} exists before the run; something executed the sentinel"
        )
    rel = "tests/fixtures/sentinel/__init__.py"
    assert (REPO_ROOT / rel).is_file()
    elements = inventory(REPO_ROOT, rel.replace("/__init__.py", "").replace("/", ".") and rel)
    analyzer = C.CascadeAnalyzer(REPO_ROOT)
    blocks, _e, _o, _d, reach = analyzer.order(elements, [], [])
    assert blocks, "the sentinel was parsed as text"
    assert reach
    assert not SENTINEL_MARKER.exists(), "the sentinel ran: constraint 1 is broken"
