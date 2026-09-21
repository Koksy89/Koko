"""Card 3 -- CFG, cascade ordering, decisions and decision reachability.

Graded against the ten FIXTURES.md cases for this card: `cfg_shapes`,
`cfg_shortcircuit`, `ord_linear`, `ord_branching`, `ord_unordered`, `ord_cycle`,
`dec_rule_cascade`, `dec_guard_clause`, `dec_sink` and `dec_uncertain_edge`.

Every one of those directories now exists, and each test below reads the real
fixture source. What the expectation files do *not* yet contain is any card-3
record: nine of the ten carry only `elements`/`unresolved`/`edges`/`findings`,
`cfg_shapes` carries `"cfg_blocks": []` for a module that plainly has blocks,
and `ord_linear` carries one order node whose id (`order_linear`) follows no
rule in the contract. So each case is asserted against the property FIXTURES.md
says it proves, read off the fixture's own source, and
`test_card3_expectation_sections_are_still_placeholders` records the gap so it
cannot be mistaken for a passing expectation file.

Cards 1 and 2 are being built in parallel, so inputs are minted here from the
contract (`Element`, `Edge`) rather than taken from their current output;
`test_fixture_element_ids_match_the_corpus` checks the ids agree with card 1's
committed expectations.

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
MODE_B = FIXTURES / "mode_b"
SENTINEL_MARKER = Path("/tmp/cascade_map_sentinel_marker.txt")

CARD3_CASES = (
    "cfg_shapes",
    "cfg_shortcircuit",
    "ord_linear",
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
# These mint exactly the `Element` and `Edge` records the `CascadeCard` protocol
# promises, so this card is tested against the contract rather than against
# cards 1 and 2 as they happen to stand today.
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, name: str, source: str) -> str:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return name


def _prov(confidence: Confidence = Confidence.CERTAIN) -> Provenance:
    return Provenance(method=Method.AST_DIRECT, confidence=confidence)


def inventory(root: Path, rel: str, module: str | None = None) -> list[Element]:
    """Elements for one module: module, classes, functions, params, assigns."""
    if module is None:
        module = rel[: -len(".py")].replace("/", ".") if rel.endswith(".py") else rel
    tree = ast.parse((root / rel).read_text(encoding="utf-8"), filename=rel)
    elements: list[Element] = [
        Element(
            id=make_id(module),
            kind=ElementKind.MODULE,
            name=module.rsplit(".", 1)[-1],
            qualname="",
            module=module,
            span=SourceSpan(
                path=rel, line=1, end_line=tree.body[-1].end_lineno if tree.body else 1
            ),
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
                    add(arg, ElementKind.PARAMETER, arg.arg, f"{qualname}.{arg.arg}", own)
                walk(stmt.body, f"{qualname}.", own, False)
            elif isinstance(stmt, ast.ClassDef):
                qualname = f"{prefix}{stmt.name}"
                own = add(stmt, ElementKind.CLASS, stmt.name, qualname, parent)
                walk(stmt.body, f"{qualname}.", own, True)
            elif isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        add(stmt, ElementKind.ASSIGNMENT, target.id, f"{prefix}{target.id}", parent)
            else:
                for name, value in ast.iter_fields(stmt):
                    if name in {"body", "orelse", "finalbody"} and isinstance(value, list):
                        walk(
                            [s for s in value if isinstance(s, ast.stmt)], prefix, parent, in_class
                        )

    walk(tree.body, "", make_id(module), False)
    return elements


def call_edges(
    root: Path,
    rel: str,
    elements: Sequence[Element],
    *,
    module: str | None = None,
    confidence: Confidence = Confidence.RESOLVED,
    kind: EdgeKind = EdgeKind.CALLS,
    overrides: dict[str, Confidence] | None = None,
) -> list[Edge]:
    """CALLS edges resolved by name inside one module, with call sites."""
    if module is None:
        module = rel[: -len(".py")].replace("/", ".") if rel.endswith(".py") else rel
    by_name: dict[str, str] = {
        element.qualname.rsplit(".", 1)[-1]: element.id
        for element in elements
        if element.kind in {ElementKind.FUNCTION, ElementKind.METHOD, ElementKind.CLASS}
    }
    by_line: dict[int, str] = {
        element.span.line: element.id
        for element in elements
        if element.kind in {ElementKind.FUNCTION, ElementKind.METHOD}
    }
    tree = ast.parse((root / rel).read_text(encoding="utf-8"), filename=rel)
    out: list[Edge] = []
    counter = 0

    def visit(node: ast.AST, owner: str) -> None:
        nonlocal counter
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, by_line.get(child.lineno, owner))
                continue
            if isinstance(child, ast.Call):
                name = (
                    child.func.id
                    if isinstance(child.func, ast.Name)
                    else child.func.attr if isinstance(child.func, ast.Attribute) else ""
                )
                target = by_name.get(name, "")
                if target:
                    counter += 1
                    out.append(
                        Edge(
                            id=make_id("@edge", f"{owner}->{target}", counter),
                            kind=kind,
                            source_id=owner,
                            target_id=target,
                            provenance=Provenance(
                                method=Method.SCOPE_LOOKUP,
                                confidence=(overrides or {}).get(target, confidence),
                            ),
                            call_site=SourceSpan(
                                path=rel,
                                line=child.lineno,
                                end_line=child.end_lineno,
                                col=child.col_offset,
                            ),
                        )
                    )
            visit(child, owner)

    visit(tree, make_id(module))
    return sorted(out, key=lambda e: e.id)


def case_source(case: str) -> tuple[str, str]:
    """(module name, path relative to the repo root) for a corpus case."""
    return case, f"tests/fixtures/mode_b/{case}/__init__.py"


def case_inputs(
    case: str,
    *,
    confidence: Confidence = Confidence.RESOLVED,
    overrides: dict[str, Confidence] | None = None,
) -> tuple[list[Element], list[Edge]]:
    module, rel = case_source(case)
    elements = inventory(REPO_ROOT, rel, module=module)
    edges = call_edges(
        REPO_ROOT, rel, elements, module=module, confidence=confidence, overrides=overrides
    )
    return elements, edges


def run_case(
    case: str,
    *,
    entry: Sequence[str] = (),
    sinks: Sequence[str] = (),
    confidence: Confidence = Confidence.RESOLVED,
    overrides: dict[str, Confidence] | None = None,
    unresolved: Sequence[Unresolved] = (),
) -> tuple[
    C.CascadeAnalyzer,
    list[CFGBlock],
    list[CFGEdge],
    list[OrderNode],
    list[DecisionPoint],
    list[Reachability],
]:
    elements, edges = case_inputs(case, confidence=confidence, overrides=overrides)
    analyzer = C.CascadeAnalyzer(REPO_ROOT, sink_ids=sinks, unresolved=unresolved)
    blocks, cfg_edges, order, decisions, reach = analyzer.order(elements, edges, entry)
    return analyzer, list(blocks), list(cfg_edges), list(order), list(decisions), list(reach)


def analyze(
    tmp_path: Path,
    source: str,
    *,
    name: str = "m.py",
    entry: Sequence[str] = (),
    sinks: Sequence[str] = (),
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
    """Supplementary programs, for shapes the corpus does not contain."""
    rel = _write(tmp_path, name, source)
    elements = inventory(tmp_path, rel)
    edges = call_edges(tmp_path, rel, elements, confidence=edge_confidence)
    analyzer = C.CascadeAnalyzer(tmp_path, sink_ids=sinks, unresolved=unresolved)
    blocks, cfg_edges, order, decisions, reach = analyzer.order(elements, edges, entry)
    return analyzer, list(blocks), list(cfg_edges), list(order), list(decisions), list(reach)


def blocks_of(blocks: Sequence[CFGBlock], element_id: str) -> list[CFGBlock]:
    return [b for b in blocks if b.element_id == element_id]


def kinds_of(blocks: Sequence[CFGBlock], element_id: str) -> set[BlockKind]:
    return {b.kind for b in blocks_of(blocks, element_id)}


def only_block(blocks: Sequence[CFGBlock], element_id: str, kind: BlockKind) -> CFGBlock:
    found = [b for b in blocks_of(blocks, element_id) if b.kind is kind]
    assert len(found) == 1, f"expected one {kind} block in {element_id}, got {len(found)}"
    return found[0]


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
        if current not in index or index[current] in seen:
            continue
        seen.append(index[current])
        stack.extend(index[current].children)
    return seen


def elements_under(order: Sequence[OrderNode], root_id: str) -> set[str]:
    return {e for child in descendants(order, root_id) for e in child.element_ids}


def reach_of(records: Sequence[Reachability], element_id: str) -> Reachability:
    for record in records:
        if record.element_id == element_id:
            return record
    raise AssertionError(f"no reachability record for {element_id!r}")


# ---------------------------------------------------------------------------
# The corpus itself
# ---------------------------------------------------------------------------


def test_every_card3_case_exists() -> None:
    missing = [case for case in CARD3_CASES if not (MODE_B / case / "__init__.py").is_file()]
    assert missing == [], f"FIXTURES.md card-3 cases with no fixture: {missing}"


def test_card3_expectation_sections_are_still_placeholders() -> None:
    """No expectation file yet states a card-3 record, so none can be asserted.

    Nine of the ten carry only card 1/2/5 sections. `cfg_shapes` declares
    `"cfg_blocks": []` for a module with four functions, which is not a
    hand-written expectation of this card's output. `ord_linear` declares one
    order node with the id `order_linear`, which no rule in the contract
    produces -- ids come from `make_id`, and the card emits `@order::...`.

    Recorded here rather than worked around, so the divergence is visible and
    this test starts failing the day the expectations are filled in.
    """
    for case in CARD3_CASES:
        payload = json.loads((MODE_B / case / "expected.json").read_text())
        card3 = {
            key: payload.get(key)
            for key in ("cfg_blocks", "cfg_edges", "order", "decisions", "reachability")
            if key in payload
        }
        assert not any(
            records for key, records in card3.items() if key != "order"
        ), f"{case} now has card-3 expectations; assert against them"
        if "order" in card3:
            assert [n["id"] for n in card3["order"] or []] == ["order_linear"], case


def test_fixture_element_ids_match_the_corpus() -> None:
    """The test inventory mints the same ids card 1's expectations declare."""
    for case in CARD3_CASES:
        expected = {
            record["id"] for record in json.loads((MODE_B / case / "expected.json").read_text())["elements"]
        }
        minted = {element.id for element in case_inputs(case)[0]}
        assert expected <= minted, f"{case}: {sorted(expected - minted)}"


# ---------------------------------------------------------------------------
# cfg_shapes -- branches, loops, try/except, with, early return
# ---------------------------------------------------------------------------


def test_cfg_shapes_branch_loop_handler_and_with() -> None:
    _a, blocks, edges, _order, _dec, _reach = run_case("cfg_shapes")

    branch = only_block(blocks, "cfg_shapes::branching_code", BlockKind.BRANCH)
    arms = out_edges(edges, branch.id)
    assert sorted(e.taken_when for e in arms) == [False, True]
    assert {e.condition for e in arms} == {"x > 0"}
    assert kinds_of(blocks, "cfg_shapes::branching_code") >= {
        BlockKind.ENTRY,
        BlockKind.BRANCH,
        BlockKind.RETURN,
        BlockKind.EXIT,
    }

    head = only_block(blocks, "cfg_shapes::loop_code", BlockKind.LOOP_HEAD)
    assert any(e.target_id == head.id and e.condition == "<loop back>" for e in edges)
    assert {e.taken_when for e in out_edges(edges, head.id)} == {True, False}

    handler = only_block(blocks, "cfg_shapes::exception_handling", BlockKind.HANDLER)
    assert any(
        e.target_id == handler.id and e.condition == "ValueError" for e in edges
    ), "the guarded region must have an exception edge into its handler"

    with_edges = [e for e in edges if e.source_id.startswith("cfg_shapes::with_statement")]
    assert "<enter context>" in {e.condition for e in with_edges}

    block_ids = {b.id for b in blocks}
    assert len(block_ids) == len(blocks), "block ids must be unique"
    for edge in edges:
        assert edge.source_id in block_ids and edge.target_id in block_ids
        assert edge.provenance is not None


def test_cfg_covers_try_finally_match_comprehension_and_early_exit(tmp_path: Path) -> None:
    """Supplementary: `cfg_shapes` omits finally, match and comprehensions."""
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
    assert kinds_of(blocks, "m::shapes") >= {
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
    assert {"<break>", "<continue>", "<enter finally>", "<return>"} <= {
        e.condition for e in edges
    }
    finally_block = only_block(blocks, "m::shapes", BlockKind.FINALLY)
    assert any(e.target_id == finally_block.id for e in edges)


def test_early_return_goes_to_exit_not_to_the_next_statement(tmp_path: Path) -> None:
    source = '''
def f(x):
    if x:
        return 1
    return 2
'''
    _a, blocks, edges, _order, _dec, _reach = analyze(tmp_path, source)
    returns = [b for b in blocks_of(blocks, "m::f") if b.kind is BlockKind.RETURN]
    exit_block = only_block(blocks, "m::f", BlockKind.EXIT)
    assert len(returns) == 2
    for ret in returns:
        assert [e.target_id for e in out_edges(edges, ret.id)] == [exit_block.id]


# ---------------------------------------------------------------------------
# cfg_shortcircuit -- `and`/`or` are branches, not expressions
# ---------------------------------------------------------------------------


def test_cfg_shortcircuit_produces_branch_edges() -> None:
    """`return a and b or c`: two gates, each with a short-circuit edge."""
    _a, blocks, edges, order, decisions, _reach = run_case("cfg_shortcircuit")
    branches = [b for b in blocks_of(blocks, "cfg_shortcircuit::check") if b.kind is BlockKind.BRANCH]
    assert len(branches) == 2, "`a and b or c` is two branches, not a flat expression"

    conditions = {b.provenance.note for b in branches}
    assert conditions == {
        "short-circuit `and` on a",
        "short-circuit `or` on a and b",
    }
    for branch in branches:
        arms = out_edges(edges, branch.id)
        assert sorted(e.taken_when for e in arms) == [False, True]
        short = [e for e in arms if e.provenance and "short circuit" in e.provenance.note]
        assert len(short) == 1, "one arm must skip the right-hand side"

    and_branch = [b for b in branches if b.provenance.note.endswith("on a")][0]
    or_branch = [b for b in branches if b.provenance.note.endswith("on a and b")][0]
    and_short = [
        e
        for e in out_edges(edges, and_branch.id)
        if e.provenance and "short circuit" in e.provenance.note
    ][0]
    or_short = [
        e
        for e in out_edges(edges, or_branch.id)
        if e.provenance and "short circuit" in e.provenance.note
    ][0]
    assert and_short.taken_when is False, "`and` short-circuits on a false left side"
    assert or_short.taken_when is True, "`or` short-circuits on a true left side"

    assert [d.condition_source for d in decisions] == ["a", "a and b"]
    for decision in decisions:
        assert decision.provenance is not None
        assert decision.provenance.note.startswith("SHORT_CIRCUIT")
        assert [label for label, _ in decision.outcomes][1] == "short-circuit"
        assert all(node(order, target) for _label, target in decision.outcomes)
    assert decisions[0].reads_ids == ("cfg_shortcircuit::check.a",)
    assert decisions[1].reads_ids == (
        "cfg_shortcircuit::check.a",
        "cfg_shortcircuit::check.b",
    )


def test_short_circuit_right_hand_call_lives_inside_its_arm(tmp_path: Path) -> None:
    """The gated call must not become a sibling of the gate in one sequence."""
    source = '''
def gate(a, b):
    return cheap(a) and expensive(b)


def cheap(a):
    return a


def expensive(b):
    return b
'''
    _a, _blocks, _edges, order, _dec, _reach = analyze(tmp_path, source, entry=["m::gate"])
    arm = [n for n in order if n.provenance and n.provenance.note.startswith("arm `evaluate ")]
    assert len(arm) == 1
    inside = elements_under(order, arm[0].id)
    assert "m::expensive" in inside and "m::cheap" not in inside


# ---------------------------------------------------------------------------
# ord_linear -- a fixed order is a SEQUENCE
# ---------------------------------------------------------------------------


def test_ord_linear_is_a_total_sequence() -> None:
    _a, _b, _e, order, _dec, reach = run_case("ord_linear", entry=["ord_linear::main"])

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
    assert "ord_linear::third" in elements_under(order, main_root.id)
    assert all(r.state is ReachabilityState.UNKNOWN for r in reach), "no sink is declared"


# ---------------------------------------------------------------------------
# ord_branching -- a branch is never flattened
# ---------------------------------------------------------------------------


def test_ord_branching_yields_branch_and_merge_never_a_sequence() -> None:
    _a, _b, _e, order, _dec, _reach = run_case("ord_branching", entry=["ord_branching::main"])

    branches = [n for n in order if n.kind is OrderKind.BRANCH]
    merges = [n for n in order if n.kind is OrderKind.MERGE]
    assert len(branches) == 1 and len(merges) == 1

    main_root = node(order, make_id("@order", "ord_branching::main"))
    assert branches[0].id in main_root.children and merges[0].id in main_root.children
    assert main_root.children.index(branches[0].id) < main_root.children.index(merges[0].id)
    assert merges[0].provenance is not None
    assert "returns or raises" in merges[0].provenance.note, (
        "both arms of this branch return, and the merge node must say so rather "
        "than claim control rejoins after the branch"
    )

    for candidate in order:
        if candidate.kind is OrderKind.SEQUENCE:
            assert not {"ord_branching::path_a", "ord_branching::path_b"} <= set(
                candidate.element_ids
            ), "flattening a real branch into a sequence is a defect"

    arm_sets = [elements_under(order, child) for child in branches[0].children]
    assert any("ord_branching::path_a" in group for group in arm_sets)
    assert any("ord_branching::path_b" in group for group in arm_sets)
    assert not any({"ord_branching::path_a", "ord_branching::path_b"} <= g for g in arm_sets)

    assert not [
        n for n in order if n.id == make_id("@order", "@total")
    ], "a branching cascade has no total order and must not claim one"


# ---------------------------------------------------------------------------
# ord_unordered
# ---------------------------------------------------------------------------


def test_ord_unordered_two_sequential_calls_are_a_sequence_not_unordered() -> None:
    """`main()` calls `op1()` then `op2()`: control flow fixes that order.

    FIXTURES.md calls this case "two independent calls yield UNORDERED", but
    WORKPLAN card 3 and `OrderKind.UNORDERED` define UNORDERED as elements
    "that run, in no order the analysis can fix", and Python fixes this one:
    `op1` always precedes `op2`. Reporting UNORDERED here would be a false
    statement about the program, so the card reports SEQUENCE and the conflict
    is raised in this card's report rather than resolved unilaterally.

    What the fixture is *about* -- that nothing forces `op1` before `op2`, so
    the owner may reorder them -- is a data-independence fact, and card 4 owns
    lineage. Card 3 cannot know it.
    """
    _a, _b, _e, order, _dec, _reach = run_case("ord_unordered", entry=["ord_unordered::main"])
    main_root = node(order, make_id("@order", "ord_unordered::main"))
    called = [
        element_id
        for child_id in main_root.children
        for element_id in node(order, child_id).element_ids
    ]
    assert called == ["ord_unordered::op1", "ord_unordered::op2"]
    assert main_root.kind is OrderKind.SEQUENCE
    assert not [n for n in order if n.kind is OrderKind.UNORDERED]


def test_registry_dispatch_from_a_loop_is_unordered(tmp_path: Path) -> None:
    """Genuine UNORDERED: every target runs, in an order the source leaves open."""
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
    assert "does not fix the order" in unordered[0].provenance.note
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
        provenance=Provenance(method=Method.CONFIG_STRING_MATCH, confidence=Confidence.HEURISTIC),
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
# ord_cycle -- recursion is a cycle with its members
# ---------------------------------------------------------------------------


def test_ord_cycle_reports_recursion_with_its_member_ids() -> None:
    _a, _b, _e, order, _dec, _reach = run_case("ord_cycle", entry=["ord_cycle::recursive"])
    cycles = [n for n in order if n.kind is OrderKind.CYCLE]
    assert len(cycles) == 1
    assert cycles[0].element_ids == ("ord_cycle::recursive",)
    assert cycles[0].id in node(order, make_id("@order", "@cascade")).children
    assert cycles[0].provenance is not None
    assert "recursion" in cycles[0].provenance.note
    assert not [n for n in order if n.id == make_id("@order", "@total")]


def test_mutual_recursion_is_one_cycle_with_both_members(tmp_path: Path) -> None:
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
    assert not [n for n in order if n.id == make_id("@order", "@total")]


# ---------------------------------------------------------------------------
# dec_rule_cascade / dec_guard_clause
# ---------------------------------------------------------------------------


def test_dec_rule_cascade_records_conditions_reads_and_outcomes() -> None:
    _a, _b, _e, order, decisions, _reach = run_case("dec_rule_cascade")
    in_decide = [d for d in decisions if d.element_id == "dec_rule_cascade::decide"]
    assert [d.condition_source for d in in_decide] == [
        "value < 0",
        "value == 0",
        "value < 10",
    ], "the chain is recorded in source order, not innermost-first"
    assert [d.id for d in in_decide] == [
        "dec_rule_cascade::decide::@decision0",
        "dec_rule_cascade::decide::@decision1",
        "dec_rule_cascade::decide::@decision2",
    ]
    for step, decision in enumerate(in_decide, start=1):
        assert decision.reads_ids == ("dec_rule_cascade::decide.value",)
        assert [label for label, _ in decision.outcomes] == ["true", "false"]
        for _label, target in decision.outcomes:
            assert node(order, target).kind is OrderKind.SEQUENCE
        assert decision.provenance is not None
        assert f"rule cascade: step {step} of 3" in decision.provenance.note
        assert decision.provenance.span is not None
        assert decision.provenance.confidence is Confidence.CERTAIN
    # Each arm is its own node: the four outcomes are four distinct targets.
    targets = {target for d in in_decide for _label, target in d.outcomes}
    assert len(targets) == 6


def test_dec_guard_clause_marks_guards_and_their_arms_leave() -> None:
    _a, blocks, edges, _order, decisions, _reach = run_case("dec_guard_clause")
    assert [d.condition_source for d in decisions] == ["not data", "data < 0"]
    for decision in decisions:
        assert decision.provenance is not None
        assert decision.provenance.note.startswith("GUARD")
        assert "guard clause: the true arm leaves" in decision.provenance.note
        assert decision.reads_ids == ("dec_guard_clause::validate.data",)
    returns = [b for b in blocks_of(blocks, "dec_guard_clause::validate") if b.kind is BlockKind.RETURN]
    exit_block = only_block(blocks, "dec_guard_clause::validate", BlockKind.EXIT)
    assert len(returns) == 3
    for ret in returns:
        assert [e.target_id for e in out_edges(edges, ret.id)] == [exit_block.id]


def test_a_raise_guard_leaves_through_the_raise_block(tmp_path: Path) -> None:
    source = '''
def handle(book):
    if not book:
        raise ValueError("empty")
    return "ok"
'''
    _a, blocks, edges, _order, decisions, _r = analyze(tmp_path, source)
    assert len(decisions) == 1 and decisions[0].provenance is not None
    assert "guard clause" in decisions[0].provenance.note
    raise_block = only_block(blocks, "m::handle", BlockKind.RAISE)
    exit_block = only_block(blocks, "m::handle", BlockKind.EXIT)
    assert [e.target_id for e in out_edges(edges, raise_block.id)] == [exit_block.id]


def test_match_ternary_and_assert_are_decision_points(tmp_path: Path) -> None:
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
    assert [d.provenance.note.split(";")[0] for d in decisions if d.provenance] == [
        "TERNARY",
        "ASSERT",
        "MATCH",
    ]
    match_decision = decisions[-1]
    assert match_decision.condition_source == "match mode"
    assert [label for label, _ in match_decision.outcomes] == ["case 'a'", "case _"]


def test_a_named_feature_read_in_a_condition_is_its_own_node(tmp_path: Path) -> None:
    source = '''
def classify(row):
    if row["price"] > 0:
        return "up"
    return "down"
'''
    _a, _b, _e, _order, decisions, _r = analyze(tmp_path, source)
    assert decisions[0].reads_ids == ("@feature:price", "m::classify.row")


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
    assert "inside the model" in decision.provenance.note
    assert set(decision.reads_ids) == {"m::score.features", "m::score.model"}


# ---------------------------------------------------------------------------
# dec_sink and reachability
# ---------------------------------------------------------------------------


def test_dec_sink_declared_marks_the_chain_and_the_sink_decision() -> None:
    analyzer, _b, _e, _order, decisions, reach = run_case(
        "dec_sink", sinks=["dec_sink::FINAL_DECISION"]
    )
    assert analyzer.sink_ids() == ("dec_sink::FINAL_DECISION",)

    producer = reach_of(reach, "dec_sink::get_decision")
    assert producer.state is ReachabilityState.REACHES_SINK
    assert producer.sink_ids == ("dec_sink::FINAL_DECISION",)
    assert producer.path_ids == (
        "dec_sink::get_decision",
        "dec_sink",
        "dec_sink::FINAL_DECISION",
    ), "the decision is assigned after this call returns to module level"
    assert producer.provenance.method is Method.CFG_REACHABILITY
    assert producer.provenance.confidence is Confidence.RESOLVED

    assert reach_of(reach, "dec_sink").state is ReachabilityState.REACHES_SINK
    assert reach_of(reach, "dec_sink::FINAL_DECISION").state is ReachabilityState.REACHES_SINK
    assert (
        reach_of(reach, "dec_sink::get_decision.input_val").state
        is ReachabilityState.REACHES_SINK
    ), "a parameter of a live function is live"

    assert [d.condition_source for d in decisions] == ["input_val > 100"]
    assert not decisions[0].is_sink, "the condition lives in get_decision, not in the sink"


def test_dec_sink_is_auto_detected_and_reported_when_not_declared() -> None:
    analyzer, _b, _e, _order, _dec, _reach = run_case("dec_sink")
    candidates = analyzer.sink_candidates()
    assert [c.element_id for c in candidates] == [
        "dec_sink::FINAL_DECISION",
        "dec_sink::get_decision",
    ]
    for candidate in candidates:
        assert candidate.role == "DECISION_SINK"
        assert candidate.provenance.method is Method.NAME_HEURISTIC
        assert candidate.provenance.confidence is Confidence.HEURISTIC
        assert "not adopted as fact" in candidate.provenance.note
        assert "hint" in candidate.evidence


def test_dec_uncertain_edge_stays_reachable_with_its_reason() -> None:
    """Reachable only through a HEURISTIC edge: reachable, and said so."""
    analyzer, _b, _e, _order, _dec, reach = run_case(
        "dec_uncertain_edge",
        entry=["dec_uncertain_edge::make_choice"],
        sinks=["dec_uncertain_edge::possible_path"],
        overrides={"dec_uncertain_edge::possible_path": Confidence.HEURISTIC},
    )
    chooser = reach_of(reach, "dec_uncertain_edge::make_choice")
    assert chooser.state is ReachabilityState.REACHES_SINK, "never pruned for being uncertain"
    assert chooser.provenance.confidence is Confidence.HEURISTIC, "weakest link on the path"
    assert chooser.path_ids == (
        "dec_uncertain_edge::make_choice",
        "dec_uncertain_edge::possible_path",
    )
    assert "HEURISTIC" in chooser.reason and "delete live code" in chooser.reason
    assert chooser.reason == chooser.provenance.note
    assert analyzer.sink_ids() == ("dec_uncertain_edge::possible_path",)


def test_order_confidence_carries_the_uncertain_edge() -> None:
    _a, _b, _e, order, _dec, _reach = run_case(
        "dec_uncertain_edge",
        entry=["dec_uncertain_edge::make_choice"],
        overrides={"dec_uncertain_edge::possible_path": Confidence.HEURISTIC},
    )
    root = node(order, make_id("@order", "dec_uncertain_edge::make_choice"))
    assert root.provenance is not None
    assert root.provenance.confidence is Confidence.HEURISTIC, (
        "an order resting on a HEURISTIC call edge is a HEURISTIC order"
    )


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
    analyzer = C.CascadeAnalyzer(tmp_path, sink_ids=["m::final_decision"], unresolved=[residue])
    *_rest, reach = analyzer.order(elements, [], ["m::main"])

    candidate = reach_of(reach, "m::maybe_called")
    assert candidate.state is ReachabilityState.UNKNOWN
    assert candidate.state is not ReachabilityState.NO_SINK_PATH
    assert residue.id in candidate.reason
    assert candidate.provenance.confidence is Confidence.UNKNOWN

    caller = reach_of(reach, "m::main")
    assert caller.state is ReachabilityState.UNKNOWN, "the unresolved site is inside main"


def test_no_sink_means_unknown_everywhere_never_unreachable(tmp_path: Path) -> None:
    source = '''
def alpha():
    return 1
'''
    analyzer, _b, _e, _order, _dec, reach = analyze(tmp_path, source)
    assert analyzer.sink_ids() == ()
    assert {r.state for r in reach} == {ReachabilityState.UNKNOWN}
    assert all("no decision sink" in r.reason for r in reach)
    assert any(
        u.reason is UnresolvedReason.MISSING_TARGET and "decision sink" in u.description
        for u in analyzer.unresolved()
    )


def test_an_element_nobody_wires_is_no_sink_path(tmp_path: Path) -> None:
    source = '''
def main():
    return final_decision()


def final_decision():
    return "BUY"


def orphan():
    return "nobody calls me"
'''
    _a, _b, _e, _order, _dec, reach = analyze(
        tmp_path, source, entry=["m::main"], sinks=["m::final_decision"]
    )
    orphan = reach_of(reach, "m::orphan")
    assert orphan.state is ReachabilityState.NO_SINK_PATH
    assert orphan.reason and orphan.provenance.confidence is Confidence.PROBABLE
    assert reach_of(reach, "m::main").state is ReachabilityState.REACHES_SINK


def test_an_element_that_runs_after_the_decision_does_not_reach_it(tmp_path: Path) -> None:
    """Precision: reaching the sink is not the same as being reachable at all."""
    source = '''
def main():
    decision = final_decision()
    log(decision)
    return decision


def final_decision():
    return "BUY"


def log(value):
    return value
'''
    _a, _b, _e, _order, _dec, reach = analyze(
        tmp_path, source, entry=["m::main"], sinks=["m::final_decision"]
    )
    assert reach_of(reach, "m::log").state is ReachabilityState.NO_SINK_PATH
    assert reach_of(reach, "m::main").state is ReachabilityState.REACHES_SINK


def test_a_stage_before_the_decision_reaches_it(tmp_path: Path) -> None:
    """The cascade case: `ingest` never calls the sink, yet the sink follows it."""
    source = '''
def main(raw):
    frame = ingest(raw)
    features = engineer(frame)
    return final_decision(features)


def ingest(raw):
    return raw


def engineer(frame):
    return frame


def final_decision(features):
    return "BUY" if features else "HOLD"
'''
    _a, _b, _e, _order, _dec, reach = analyze(
        tmp_path, source, entry=["m::main"], sinks=["m::final_decision"]
    )
    for element_id in ("m::main", "m::ingest", "m::engineer", "m::final_decision"):
        record = reach_of(reach, element_id)
        assert record.state is ReachabilityState.REACHES_SINK, element_id
        assert record.path_ids[0] == element_id
        assert record.path_ids[-1] == "m::final_decision"
    assert reach_of(reach, "m::ingest").path_ids == (
        "m::ingest",
        "m::main",
        "m::final_decision",
    )


def test_every_element_gets_exactly_one_reachability_record() -> None:
    for case in CARD3_CASES:
        elements, edges = case_inputs(case)
        analyzer = C.CascadeAnalyzer(REPO_ROOT)
        *_rest, reach = analyzer.order(elements, edges, [])
        assert sorted(r.element_id for r in reach) == sorted(e.id for e in elements), case
        assert len({r.id for r in reach}) == len(reach), case
        assert all(r.reason for r in reach), case
        assert list(reach) == list(analyzer.reachability()), case


def test_class_and_module_get_records_even_without_call_edges(tmp_path: Path) -> None:
    source = '''
class Strategy:
    def evaluate(self):
        return 1


def final_decision():
    return "BUY"
'''
    _a, _b, _e, _order, _d, reach = analyze(tmp_path, source, sinks=["m::final_decision"])
    assert reach_of(reach, "m").state is ReachabilityState.REACHES_SINK  # it holds the sink
    assert reach_of(reach, "m::Strategy").state is ReachabilityState.NO_SINK_PATH
    assert reach_of(reach, "m::Strategy.evaluate").state is ReachabilityState.NO_SINK_PATH
    assert reach_of(reach, "m::Strategy").reason


def test_unresolvable_condition_read_does_not_blind_reachability(tmp_path: Path) -> None:
    """A condition naming something uninventoried opens no control path.

    Only opaque residue -- an unresolved call site, an unparsable file, a
    deferred lambda -- turns NO_SINK_PATH into UNKNOWN. Otherwise one unknown
    name would make the whole map UNKNOWN and card 5 would see nothing at all.
    """
    source = '''
def orphan(x):
    if numpy.isnan(x):
        return 1
    return 2


def final_decision():
    return "BUY"
'''
    analyzer, _b, _e, _order, _d, reach = analyze(tmp_path, source, sinks=["m::final_decision"])
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
    assert analyzer.entry_ids() == ("run_m5", "run_m5::main")


def test_declared_entry_that_is_not_inventoried_is_reported(tmp_path: Path) -> None:
    analyzer, _b, _e, _order, _d, _r = analyze(
        tmp_path, "def main():\n    return 1\n", entry=["m::nope"]
    )
    residue = [u for u in analyzer.unresolved() if "nope" in u.description]
    assert len(residue) == 1 and residue[0].reason is UnresolvedReason.MISSING_TARGET


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
    assert "m::helper" in elements_under(order, deferred.id)


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


def test_every_emitted_fact_carries_provenance() -> None:
    _a, blocks, edges, order, decisions, reach = run_case(
        "dec_rule_cascade",
        entry=["dec_rule_cascade::decide"],
        sinks=["dec_rule_cascade::decide"],
    )
    for block in blocks:
        assert block.provenance.method is Method.AST_DIRECT
        assert block.provenance.confidence is Confidence.CERTAIN
        assert block.provenance.span is not None
    for edge in edges:
        assert edge.provenance is not None and edge.provenance.method is Method.AST_DIRECT
    for order_node in order:
        assert order_node.provenance is not None and order_node.provenance.note
    for decision in decisions:
        assert decision.provenance is not None and decision.provenance.span is not None
    for record in reach:
        assert record.provenance.method is Method.CFG_REACHABILITY
    assert all(d.is_sink for d in decisions), "every decision here is inside the sink"


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
        total = node(order, make_id("@order", "@total"))
        assert root.provenance is not None and root.provenance.confidence is confidence
        assert total.provenance is not None and total.provenance.confidence is confidence


def test_an_unresolved_call_site_makes_its_order_node_unknown(tmp_path: Path) -> None:
    source = '''
def main():
    return mystery()
'''
    _a, _b, _e, order, _d, _r = analyze(tmp_path, source, entry=["m::main"])
    unknown = [
        n for n in order if n.provenance is not None and n.provenance.confidence is Confidence.UNKNOWN
    ]
    assert unknown, "a call nobody resolved must not be silently dropped from the order"
    assert any("no resolved target" in n.provenance.note for n in unknown if n.provenance)


def test_rank_agrees_with_combine() -> None:
    """`_RANK` mirrors the contract's ordering; this fails if it ever drifts."""
    for left in Confidence:
        for right in Confidence:
            weaker = left if C._RANK[left] <= C._RANK[right] else right
            assert combine(left, right) is weaker


def test_two_runs_are_byte_identical() -> None:
    for case in CARD3_CASES:
        elements, edges = case_inputs(case)
        first = C.CascadeAnalyzer(REPO_ROOT, sink_ids=[elements[-1].id])
        first.order(elements, edges, [elements[1].id])
        second = C.CascadeAnalyzer(REPO_ROOT, sink_ids=[elements[-1].id])
        second.order(list(reversed(elements)), list(reversed(edges)), [elements[1].id])
        assert first.artifacts() == second.artifacts(), case
        assert set(first.artifacts()) == {
            "cfg_blocks.jsonl",
            "cfg_edges.jsonl",
            "order.jsonl",
            "decisions.jsonl",
            "reachability.jsonl",
        }
        for text in first.artifacts().values():
            assert text == "" or text.endswith("\n")


def test_every_artifact_is_sorted_by_id() -> None:
    for case in CARD3_CASES:
        elements, edges = case_inputs(case)
        analyzer = C.CascadeAnalyzer(REPO_ROOT)
        for records in analyzer.order(elements, edges, []):
            ids = [r.id for r in records]
            assert ids == sorted(ids), case
        for name, text in analyzer.artifacts().items():
            rows = [json.loads(line)["id"] for line in text.splitlines()]
            assert rows == sorted(rows), f"{case}/{name}"


def test_reachability_jsonl_round_trips_deterministically() -> None:
    _a, _b, _e, _o, _d, reach = run_case(
        "dec_sink", entry=["dec_sink"], sinks=["dec_sink::FINAL_DECISION"]
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
                span=SourceSpan(**row["provenance"]["span"]) if row["provenance"]["span"] else None,
                note=row["provenance"]["note"],
            ),
            sink_ids=tuple(row["sink_ids"]),
            path_ids=tuple(row["path_ids"]),
            reason=row["reason"],
        )
        for row in rows
    ]
    assert canonical_jsonl(revived) == rendered


def test_the_card_satisfies_the_cascade_card_protocol(tmp_path: Path) -> None:
    card: CascadeCard = C.CascadeAnalyzer(REPO_ROOT)
    elements, edges = case_inputs("ord_linear")
    result = card.order(elements, edges, ["ord_linear::main"])
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
        pytest.fail(f"{SENTINEL_MARKER} exists before the run; something executed the sentinel")
    rel = "tests/fixtures/sentinel/__init__.py"
    assert (REPO_ROOT / rel).is_file()
    elements = inventory(REPO_ROOT, rel, module="sentinel")
    analyzer = C.CascadeAnalyzer(REPO_ROOT)
    blocks, _e, _o, _d, reach = analyzer.order(elements, [], [])
    assert blocks, "the sentinel was parsed as text"
    assert reach
    assert not SENTINEL_MARKER.exists(), "the sentinel ran: constraint 1 is broken"


def test_the_whole_mode_b_corpus_analyses_without_raising() -> None:
    """Coverage check: every corpus module, not only this card's ten cases."""
    cases = sorted(p.name for p in MODE_B.iterdir() if (p / "__init__.py").is_file())
    assert len(cases) >= len(CARD3_CASES)
    for case in cases:
        rel = f"tests/fixtures/mode_b/{case}/__init__.py"
        try:
            elements = inventory(REPO_ROOT, rel, module=case)
        except SyntaxError:
            continue  # inv_syntax_error is meant to be unparsable
        except UnicodeDecodeError:
            continue  # inv_non_utf8
        analyzer = C.CascadeAnalyzer(REPO_ROOT)
        blocks, edges, order, decisions, reach = analyzer.order(elements, [], [])
        assert len(reach) == len(elements), case
        assert {e.source_id for e in edges} <= {b.id for b in blocks}, case
        assert all(n.provenance is not None for n in order), case
        assert all(d.element_id in {e.id for e in elements} for d in decisions), case
    assert not SENTINEL_MARKER.exists()
