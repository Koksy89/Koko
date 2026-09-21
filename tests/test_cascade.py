"""Card 3 -- CFG, cascade ordering, decisions and decision reachability.

Graded against the ten FIXTURES.md cases for this card: `cfg_shapes`,
`cfg_shortcircuit`, `ord_linear`, `ord_branching`, `ord_unordered`, `ord_cycle`,
`dec_rule_cascade`, `dec_guard_clause`, `dec_sink` and `dec_uncertain_edge`.

The corpus expectations are the grading instrument, so the tests are driven by
them rather than restating them: elements, edges, entry points and declared
sinks all come out of each `expected.json`, and every card-3 section it
declares -- `cfg_requirements`, `order`, `decisions`, `reachability`,
`forbidden_order_kinds_over_elements`, `min_branch_blocks_per_element`,
`call_must_be_conditional`, `cycle_count_exact`, `decision_count_exact` -- is
asserted generically. A case that gains a section starts being graded on it
without a test change.

Two things the expectations state cannot be asserted literally, and are checked
structurally instead, with the divergence named in this card's report:

* Record **ids** in the expectations (`ord_linear:o1`, `dec_rule_cascade:d1`)
  are fixture-local labels. Real ids come from `make_id`, so order nodes are
  matched by kind and element set, decisions by element and condition.
* Decision **outcome targets** in the expectations are the returned string
  (`"forced"`) or the next decision's label. The contract says the target is
  "target order-node or element id", so the card emits the arm's order-node id.
  Labels are compared; targets are checked for being resolvable nodes.

Supplementary programs in `tmp_path` cover shapes the corpus does not contain
(`finally` inside a function with returns, lambdas, tree-model calls, wiring
with no call site, multiple entry points).

Nothing here imports or executes a fixture. Sources are read as text and parsed
with `ast`, exactly as the card does. `test_sentinel_is_never_executed` proves
it.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Iterable, Sequence

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
MODE_B = REPO_ROOT / "tests" / "fixtures" / "mode_b"
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
# Loading a corpus case
# ---------------------------------------------------------------------------


def expectation(case: str) -> dict[str, Any]:
    return json.loads((MODE_B / case / "expected.json").read_text())


def _span(payload: dict[str, Any] | None) -> SourceSpan | None:
    if not payload:
        return None
    return SourceSpan(
        path=payload["path"],
        line=payload["line"],
        end_line=payload.get("end_line"),
        col=payload.get("col"),
    )


def _provenance(payload: dict[str, Any]) -> Provenance:
    return Provenance(
        method=Method(payload["method"]),
        confidence=Confidence(payload["confidence"]),
        span=_span(payload.get("span")),
        note=payload.get("note", ""),
    )


def case_elements(case: str) -> list[Element]:
    out: list[Element] = []
    for record in expectation(case)["elements"]:
        span = _span(record["span"])
        assert span is not None
        out.append(
            Element(
                id=record["id"],
                kind=ElementKind(record["kind"]),
                name=record["name"],
                qualname=record["qualname"],
                module=record["module"],
                span=span,
                provenance=_provenance(record["provenance"]),
                content_hash=record.get("content_hash") or "",
                decorators=tuple(record.get("decorators", ())),
                signature=record.get("signature", ""),
                docstring=record.get("docstring", ""),
                parent_id=record.get("parent_id", ""),
                byte_size=record.get("byte_size", 0),
            )
        )
    return out


def case_edges(case: str) -> list[Edge]:
    return [
        Edge(
            id=record["id"],
            kind=EdgeKind(record["kind"]),
            source_id=record["source_id"],
            target_id=record["target_id"],
            provenance=_provenance(record["provenance"]),
            call_site=_span(record.get("call_site")),
        )
        for record in expectation(case).get("edges", [])
    ]


class Result:
    """Everything one case run produced, kept together for readability."""

    def __init__(self, case: str, **overrides: Any) -> None:
        payload = expectation(case)
        self.case = case
        self.expected = payload
        self.elements = case_elements(case)
        self.edges = case_edges(case)
        self.entry_ids = list(overrides.get("entry", payload.get("entry_ids", [])))
        self.sink_ids = list(overrides.get("sinks", payload.get("declared_sink_ids", [])))
        self.analyzer = C.CascadeAnalyzer(
            REPO_ROOT,
            sink_ids=self.sink_ids,
            unresolved=overrides.get("unresolved", ()),
        )
        (
            self.blocks,
            self.cfg_edges,
            self.order,
            self.decisions,
            self.reach,
        ) = (list(part) for part in self.analyzer.order(self.elements, self.edges, self.entry_ids))

    # -- lookups ------------------------------------------------------------

    def blocks_of(self, element_id: str) -> list[CFGBlock]:
        return [b for b in self.blocks if b.element_id == element_id]

    def kinds_of(self, element_id: str) -> set[BlockKind]:
        return {b.kind for b in self.blocks_of(element_id)}

    def only_block(self, element_id: str, kind: BlockKind) -> CFGBlock:
        found = [b for b in self.blocks_of(element_id) if b.kind is kind]
        assert len(found) == 1, f"expected one {kind} in {element_id}, got {len(found)}"
        return found[0]

    def out_edges(self, block_id: str) -> list[CFGEdge]:
        return [e for e in self.cfg_edges if e.source_id == block_id]

    def node(self, node_id: str) -> OrderNode:
        for candidate in self.order:
            if candidate.id == node_id:
                return candidate
        raise AssertionError(f"no order node {node_id!r}")

    def parents(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for candidate in self.order:
            for child in candidate.children:
                out.setdefault(child, []).append(candidate.id)
        return out

    def ancestors(self, node_id: str) -> list[OrderNode]:
        parents = self.parents()
        index = {n.id: n for n in self.order}
        seen: list[OrderNode] = []
        queue = list(parents.get(node_id, []))
        while queue:
            current = queue.pop()
            if current not in index or index[current] in seen:
                continue
            seen.append(index[current])
            queue.extend(parents.get(current, []))
        return seen

    def descendants(self, root_id: str) -> list[OrderNode]:
        index = {n.id: n for n in self.order}
        seen: list[OrderNode] = []
        stack = [root_id]
        while stack:
            current = stack.pop()
            if current not in index or index[current] in seen:
                continue
            seen.append(index[current])
            stack.extend(index[current].children)
        return seen

    def elements_under(self, root_id: str) -> set[str]:
        return {e for n in self.descendants(root_id) for e in n.element_ids}

    def reach_of(self, element_id: str) -> Reachability:
        for record in self.reach:
            if record.element_id == element_id:
                return record
        raise AssertionError(f"no reachability record for {element_id!r}")

    def decisions_of(self, element_id: str) -> list[DecisionPoint]:
        return [d for d in self.decisions if d.element_id == element_id]


CASES: dict[str, Result] = {}


def result(case: str) -> Result:
    if case not in CASES:
        CASES[case] = Result(case)
    return CASES[case]


# ---------------------------------------------------------------------------
# Supplementary programs, for shapes the corpus does not contain
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, name: str, source: str) -> str:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return name


def _prov(confidence: Confidence = Confidence.CERTAIN) -> Provenance:
    return Provenance(method=Method.AST_DIRECT, confidence=confidence)


def inventory(root: Path, rel: str, module: str | None = None) -> list[Element]:
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
                own = add(
                    stmt,
                    ElementKind.METHOD if in_class else ElementKind.FUNCTION,
                    stmt.name,
                    qualname,
                    parent,
                )
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
    confidence: Confidence = Confidence.RESOLVED,
) -> list[Edge]:
    module = rel[: -len(".py")].replace("/", ".") if rel.endswith(".py") else rel
    by_name = {
        element.qualname.rsplit(".", 1)[-1]: element.id
        for element in elements
        if element.kind in {ElementKind.FUNCTION, ElementKind.METHOD, ElementKind.CLASS}
    }
    by_line = {
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
                            kind=EdgeKind.CALLS,
                            source_id=owner,
                            target_id=target,
                            provenance=Provenance(
                                method=Method.SCOPE_LOOKUP, confidence=confidence
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


class Program:
    """A supplementary program analysed the same way a corpus case is."""

    def __init__(
        self,
        tmp_path: Path,
        source: str,
        *,
        name: str = "m.py",
        entry: Sequence[str] = (),
        sinks: Sequence[str] = (),
        edges: Sequence[Edge] | None = None,
        unresolved: Sequence[Unresolved] = (),
        edge_confidence: Confidence = Confidence.RESOLVED,
    ) -> None:
        self.rel = _write(tmp_path, name, source)
        self.elements = inventory(tmp_path, self.rel)
        self.edges = (
            list(edges)
            if edges is not None
            else call_edges(tmp_path, self.rel, self.elements, confidence=edge_confidence)
        )
        self.analyzer = C.CascadeAnalyzer(tmp_path, sink_ids=sinks, unresolved=unresolved)
        (
            self.blocks,
            self.cfg_edges,
            self.order,
            self.decisions,
            self.reach,
        ) = (list(part) for part in self.analyzer.order(self.elements, self.edges, entry))

    blocks_of = Result.blocks_of
    kinds_of = Result.kinds_of
    only_block = Result.only_block
    out_edges = Result.out_edges
    node = Result.node
    parents = Result.parents
    ancestors = Result.ancestors
    descendants = Result.descendants
    elements_under = Result.elements_under
    reach_of = Result.reach_of
    decisions_of = Result.decisions_of


# ---------------------------------------------------------------------------
# The corpus exists and is loadable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CARD3_CASES)
def test_case_exists_and_analyses(case: str) -> None:
    assert (MODE_B / case / "__init__.py").is_file(), f"no fixture for {case}"
    run = result(case)
    assert run.blocks, "every case has at least one control-flow block"
    assert len(run.reach) == len(run.elements)


# ---------------------------------------------------------------------------
# cfg_requirements -- cfg_shapes, cfg_shortcircuit, dec_guard_clause
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CARD3_CASES)
def test_cfg_requirements(case: str) -> None:
    run = result(case)
    requirements = run.expected.get("cfg_requirements")
    if not requirements:
        pytest.skip(f"{case} declares no cfg_requirements")
    for requirement in requirements:
        element_id = requirement["element_id"]
        kinds = run.kinds_of(element_id)
        assert kinds, f"{case}: no CFG for {element_id}"
        required = {BlockKind(k) for k in requirement.get("required_block_kinds", [])}
        forbidden = {BlockKind(k) for k in requirement.get("forbidden_block_kinds", [])}
        assert required <= kinds, (
            f"{case}/{requirement.get('shape')}: missing {sorted(required - kinds)}"
        )
        assert not (forbidden & kinds), (
            f"{case}/{requirement.get('shape')}: forbidden {sorted(forbidden & kinds)}"
        )
        for condition in requirement.get("branch_conditions_both_outcomes", []):
            taken = {
                edge.taken_when
                for edge in run.cfg_edges
                if edge.condition == condition
                and edge.source_id.startswith(f"{element_id}::@block")
            }
            assert taken == {True, False}, (
                f"{case}: `{condition}` must have a true and a false outcome, got {taken}"
            )


@pytest.mark.parametrize("case", CARD3_CASES)
def test_min_branch_blocks_per_element(case: str) -> None:
    run = result(case)
    minimums = run.expected.get("min_branch_blocks_per_element")
    if not minimums:
        pytest.skip(f"{case} declares no branch-block minimums")
    for element_id, minimum in sorted(minimums.items()):
        found = [b for b in run.blocks_of(element_id) if b.kind is BlockKind.BRANCH]
        assert len(found) >= minimum, f"{case}: {element_id} has {len(found)} branch blocks"


@pytest.mark.parametrize("case", CARD3_CASES)
def test_call_must_be_conditional(case: str) -> None:
    """A call gated by a short circuit sits under a branch arm, not beside it."""
    run = result(case)
    claims = run.expected.get("call_must_be_conditional")
    if not claims:
        pytest.skip(f"{case} declares no conditional calls")
    edges = {edge.id: edge for edge in run.edges}
    for claim in claims:
        edge = edges[claim["edge_id"]]
        holders = [n for n in run.order if n.element_ids == (edge.target_id,)]
        assert holders, f"{case}: no order node schedules {edge.target_id}"
        for holder in holders:
            kinds = {ancestor.kind for ancestor in run.ancestors(holder.id)}
            assert OrderKind.BRANCH in kinds, (
                f"{case}: {edge.target_id} is not under a branch -- {claim['why']}"
            )


def test_short_circuit_taken_when_is_the_gate_direction() -> None:
    """`and` skips on a false left operand; `or` skips on a true one."""
    run = result("cfg_shortcircuit")
    branches = [b for b in run.blocks_of("cfg_shortcircuit::check") if b.kind is BlockKind.BRANCH]
    assert len(branches) == 2
    by_note = {b.provenance.note: b for b in branches}
    assert set(by_note) == {
        "short-circuit `and` on a",
        "short-circuit `or` on a and b",
    }
    for note, block in sorted(by_note.items()):
        short = [
            e
            for e in run.out_edges(block.id)
            if e.provenance and "short circuit" in e.provenance.note
        ]
        assert len(short) == 1, note
        assert short[0].taken_when is ("`or`" in note)


def test_short_circuit_decisions_name_the_gate() -> None:
    run = result("cfg_shortcircuit")
    conditions = [d.condition_source for d in run.decisions_of("cfg_shortcircuit::check")]
    assert conditions == ["a", "a and b"]
    for decision in run.decisions_of("cfg_shortcircuit::check"):
        assert decision.provenance is not None
        assert decision.provenance.note.startswith("SHORT_CIRCUIT")
        assert [label for label, _ in decision.outcomes] == ["True", "False"] or [
            label for label, _ in decision.outcomes
        ] == ["False", "True"]
        for _label, target in decision.outcomes:
            assert run.node(target).kind is OrderKind.SEQUENCE


# ---------------------------------------------------------------------------
# order -- ord_linear, ord_branching, ord_unordered, ord_cycle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CARD3_CASES)
def test_expected_order_nodes(case: str) -> None:
    """Every order node the case declares exists, by kind and element set.

    Matched on kind plus the elements the node schedules, because the ids in
    the expectations are fixture-local labels and real ids come from `make_id`.
    """
    run = result(case)
    expected_nodes = run.expected.get("order")
    if not expected_nodes:
        pytest.skip(f"{case} declares no order nodes")
    for expected_node in expected_nodes:
        kind = OrderKind(expected_node["kind"])
        element_ids = tuple(expected_node["element_ids"])
        matches = [
            candidate
            for candidate in run.order
            if candidate.kind is kind and candidate.element_ids == element_ids
        ]
        assert matches, (
            f"{case}: no {kind} node over {list(element_ids)}; "
            f"have {[(n.kind, n.element_ids) for n in run.order if n.element_ids]}"
        )
        provenance = expected_node.get("provenance", {})
        for match in matches:
            assert match.provenance is not None
            if "confidence" in provenance:
                assert match.provenance.confidence is Confidence(provenance["confidence"]), (
                    f"{case}: {kind} over {list(element_ids)}"
                )
            if "method" in provenance:
                assert match.provenance.method is Method(provenance["method"])


@pytest.mark.parametrize("case", CARD3_CASES)
def test_forbidden_order_kinds_over_elements(case: str) -> None:
    """Flattening a real branch, or inventing an order, is a defect."""
    run = result(case)
    forbidden = run.expected.get("forbidden_order_kinds_over_elements")
    if not forbidden:
        pytest.skip(f"{case} forbids no order kinds")
    for claim in forbidden:
        kind = OrderKind(claim["kind"])
        members = set(claim["element_ids"])
        offenders = [
            candidate
            for candidate in run.order
            if candidate.kind is kind and members <= set(candidate.element_ids)
        ]
        assert not offenders, f"{case}: {[n.id for n in offenders]} -- {claim['why']}"


@pytest.mark.parametrize("case", CARD3_CASES)
def test_cycle_count_exact(case: str) -> None:
    run = result(case)
    expected_count = run.expected.get("cycle_count_exact")
    if expected_count is None:
        pytest.skip(f"{case} declares no cycle count")
    cycles = [n for n in run.order if n.kind is OrderKind.CYCLE]
    assert len(cycles) == expected_count, [n.element_ids for n in cycles]
    for cycle in cycles:
        assert cycle.provenance is not None and "recursion" in cycle.provenance.note


def test_ord_linear_elements_are_in_execution_order() -> None:
    run = result("ord_linear")
    assert run.expected.get("order_element_ids_are_ordered") is True
    node = run.node(make_id("@order", "ord_linear::main"))
    assert node.kind is OrderKind.SEQUENCE
    assert list(node.element_ids) == [
        "ord_linear::ingest",
        "ord_linear::clean",
        "ord_linear::featurize",
        "ord_linear::decide",
    ]
    assert node.provenance is not None
    assert node.provenance.confidence is Confidence.RESOLVED, (
        "the order rests on four SCOPE_LOOKUP/RESOLVED edges, and combine takes the weakest"
    )
    assert not [n for n in run.order if n.kind in {OrderKind.BRANCH} and n.element_ids]


def test_ord_branching_merge_owns_the_continuation() -> None:
    run = result("ord_branching")
    branch = [n for n in run.order if n.kind is OrderKind.BRANCH and n.element_ids][0]
    merge = [n for n in run.order if n.kind is OrderKind.MERGE and n.element_ids][0]
    assert branch.element_ids == ("ord_branching::aggressive", "ord_branching::conservative")
    assert merge.element_ids == ("ord_branching::report",)
    main_root = run.node(make_id("@order", "ord_branching::main"))
    assert branch.id in main_root.children and merge.id in main_root.children
    assert main_root.children.index(branch.id) < main_root.children.index(merge.id)
    arms = [run.elements_under(child) for child in branch.children]
    assert any("ord_branching::aggressive" in arm for arm in arms)
    assert any("ord_branching::conservative" in arm for arm in arms)
    assert not any({"ord_branching::aggressive", "ord_branching::conservative"} <= a for a in arms)


def test_ord_unordered_is_a_loop_over_an_unordered_set() -> None:
    run = result("ord_unordered")
    unordered = [n for n in run.order if n.kind is OrderKind.UNORDERED]
    assert len(unordered) == 1
    assert unordered[0].element_ids == ("ord_unordered::audit", "ord_unordered::notify")
    assert unordered[0].provenance is not None
    assert unordered[0].provenance.confidence is Confidence.HEURISTIC
    assert "does not fix the order" in unordered[0].provenance.note
    loops = [n for n in run.order if n.kind is OrderKind.LOOP]
    assert loops and unordered[0].id in run.descendants(loops[0].id)[0].children + tuple(
        child for n in run.descendants(loops[0].id) for child in n.children
    )


def test_ord_cycle_members_are_named() -> None:
    run = result("ord_cycle")
    cycles = sorted(
        (n for n in run.order if n.kind is OrderKind.CYCLE), key=lambda n: n.element_ids
    )
    assert [n.element_ids for n in cycles] == [
        ("ord_cycle::countdown",),
        ("ord_cycle::ping", "ord_cycle::pong"),
    ]
    cascade = run.node(make_id("@order", "@cascade"))
    assert all(cycle.id in cascade.children for cycle in cycles)
    assert not [n for n in run.order if n.id == make_id("@order", "@total")]


# ---------------------------------------------------------------------------
# decisions -- dec_rule_cascade, dec_guard_clause, dec_sink, dec_uncertain_edge
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CARD3_CASES)
def test_expected_decisions(case: str) -> None:
    run = result(case)
    expected_decisions = run.expected.get("decisions")
    if not expected_decisions:
        pytest.skip(f"{case} declares no decisions")
    for expected_decision in expected_decisions:
        element_id = expected_decision["element_id"]
        matches = [
            decision
            for decision in run.decisions_of(element_id)
            if decision.condition_source == expected_decision["condition_source"]
        ]
        assert len(matches) == 1, (
            f"{case}: `{expected_decision['condition_source']}` in {element_id}; "
            f"have {[d.condition_source for d in run.decisions_of(element_id)]}"
        )
        decision = matches[0]
        assert decision.is_sink is expected_decision["is_sink"]
        assert set(expected_decision["reads_ids"]) <= set(decision.reads_ids), (
            f"{case}: {decision.condition_source} reads {decision.reads_ids}"
        )
        assert [label for label, _ in decision.outcomes] == [
            label for label, _ in expected_decision["outcomes"]
        ]
        for _label, target in decision.outcomes:
            assert run.node(target) is not None
        provenance = expected_decision.get("provenance", {})
        assert decision.provenance is not None
        assert decision.provenance.method is Method(provenance["method"])
        assert decision.provenance.confidence is Confidence(provenance["confidence"]), (
            f"{case}: {decision.condition_source}"
        )
        expected_span = provenance.get("span")
        if expected_span:
            assert decision.provenance.span is not None
            actual = decision.provenance.span
            assert actual.path == expected_span["path"]
            assert actual.line == expected_span["line"]
            if "col" in expected_span:
                assert actual.col == expected_span["col"]
            if "end_line" in expected_span:
                assert actual.end_line == expected_span["end_line"]


@pytest.mark.parametrize("case", CARD3_CASES)
def test_decision_count_exact(case: str) -> None:
    run = result(case)
    expected_count = run.expected.get("decision_count_exact")
    if expected_count is None:
        pytest.skip(f"{case} declares no decision count")
    assert len(run.decisions) == expected_count, [d.condition_source for d in run.decisions]


def test_dec_rule_cascade_is_ordered_not_independent() -> None:
    """`score < 50` is reached only when the earlier conditions were false."""
    run = result("dec_rule_cascade")
    decisions = run.decisions_of("dec_rule_cascade::classify")
    assert [d.condition_source for d in decisions] == ["override", "score < 0", "score < 50"]
    for step, decision in enumerate(decisions, start=1):
        assert decision.provenance is not None
        assert f"rule cascade: step {step} of 3" in decision.provenance.note
    # The later conditions live inside the false arm of the earlier ones.
    first, second = decisions[0], decisions[1]
    false_arm = first.outcomes[1][1]
    assert second.outcomes[0][1] in {n.id for n in run.descendants(false_arm)}


def test_dec_guard_clause_guards_are_marked_and_leave() -> None:
    run = result("dec_guard_clause")
    decisions = run.decisions_of("dec_guard_clause::validate")
    assert [d.condition_source for d in decisions] == [
        "payload is None",
        "not payload",
        "len(payload) > limit",
    ]
    for decision in decisions:
        assert decision.provenance is not None
        assert decision.provenance.note.startswith("GUARD")
        assert "guard clause: the true arm leaves" in decision.provenance.note
    exit_block = run.only_block("dec_guard_clause::validate", BlockKind.EXIT)
    returns = [b for b in run.blocks_of("dec_guard_clause::validate") if b.kind is BlockKind.RETURN]
    assert len(returns) == 4
    for ret in returns:
        assert [e.target_id for e in run.out_edges(ret.id)] == [exit_block.id]


# ---------------------------------------------------------------------------
# reachability -- dec_sink, dec_uncertain_edge
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CARD3_CASES)
def test_expected_reachability(case: str) -> None:
    run = result(case)
    expected_records = run.expected.get("reachability")
    if not expected_records:
        pytest.skip(f"{case} declares no reachability")
    for expected_record in expected_records:
        record = run.reach_of(expected_record["element_id"])
        assert record.state is ReachabilityState(expected_record["state"]), (
            f"{case}: {record.element_id} -- {record.reason}"
        )
        if "path_ids" in expected_record:
            assert list(record.path_ids) == expected_record["path_ids"]
        if "sink_ids" in expected_record:
            assert list(record.sink_ids) == expected_record["sink_ids"]
        provenance = expected_record.get("provenance", {})
        if "confidence" in provenance:
            assert record.provenance.confidence is Confidence(provenance["confidence"])
        if "method" in provenance:
            assert record.provenance.method is Method(provenance["method"])


@pytest.mark.parametrize("case", CARD3_CASES)
def test_forbidden_reachability_states(case: str) -> None:
    run = result(case)
    forbidden = run.expected.get("forbidden_reachability_states")
    if not forbidden:
        pytest.skip(f"{case} forbids no reachability states")
    for element_id, states in sorted(forbidden.items()):
        record = run.reach_of(element_id)
        assert record.state.value not in states, f"{case}: {element_id} -- {record.reason}"


@pytest.mark.parametrize("case", CARD3_CASES)
def test_every_element_is_marked_for_reachability(case: str) -> None:
    run = result(case)
    assert sorted(r.element_id for r in run.reach) == sorted(e.id for e in run.elements)
    assert len({r.id for r in run.reach}) == len(run.reach)
    assert all(record.reason for record in run.reach), "an unexplained verdict is a gate failure"
    assert list(run.reach) == list(run.analyzer.reachability())
    kinds = {e.kind for e in run.elements}
    assert ElementKind.MODULE in kinds


def test_dec_uncertain_edge_is_reachable_at_the_weakest_link() -> None:
    run = result("dec_uncertain_edge")
    record = run.reach_of("dec_uncertain_edge::momentum_rule")
    assert record.state is ReachabilityState.REACHES_SINK, record.reason
    assert record.provenance.confidence is Confidence.HEURISTIC
    assert "delete live code" in record.reason
    assert record.reason == record.provenance.note
    assert record.path_ids[-1] == "dec_uncertain_edge::decide"


def test_dec_sink_separates_no_path_from_unknown() -> None:
    run = result("dec_sink")
    assert run.reach_of("dec_sink::log_metrics").state is ReachabilityState.NO_SINK_PATH
    assert run.reach_of("dec_sink::load").state is ReachabilityState.REACHES_SINK
    states = {r.element_id: r.state for r in run.reach}
    assert ReachabilityState.UNKNOWN not in states.values(), (
        "with a declared sink and fully resolved edges, nothing here is unknown"
    )


def test_a_discarded_call_that_could_mutate_is_unknown_not_dead(tmp_path: Path) -> None:
    """The limit of this card: side effects are card 4's question, not card 3's.

    `stamp` is called for its effect and its result is thrown away, so no value
    reaches the decision through it -- but it writes through its argument, so it
    may still drive the decision. UNKNOWN, with the reason, never NO_SINK_PATH.
    """
    source = '''
def main(rows):
    stamp(rows)
    measure(rows)
    return final_decision(rows)


def stamp(rows):
    rows["seen"] = True


def measure(rows):
    return len(rows)


def final_decision(rows):
    return "BUY"
'''
    run = Program(tmp_path, source, entry=["m::main"], sinks=["m::final_decision"])
    stamped = run.reach_of("m::stamp")
    assert stamped.state is ReachabilityState.UNKNOWN
    assert "side effect" in stamped.reason and "card 4" in stamped.reason
    assert run.reach_of("m::measure").state is ReachabilityState.NO_SINK_PATH


def test_a_stage_whose_output_feeds_the_decision_reaches_it(tmp_path: Path) -> None:
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
    run = Program(tmp_path, source, entry=["m::main"], sinks=["m::final_decision"])
    for element_id in ("m::main", "m::ingest", "m::engineer", "m::final_decision"):
        record = run.reach_of(element_id)
        assert record.state is ReachabilityState.REACHES_SINK, element_id
        assert record.path_ids[0] == element_id
        assert record.path_ids[-1] == "m::final_decision"
    assert run.reach_of("m::main.raw").state is ReachabilityState.REACHES_SINK


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
    rel = "m.py"
    residue = Unresolved(
        id=make_id("@unresolved", "m::main::dynamic"),
        reason=UnresolvedReason.DYNAMIC_NAME,
        span=SourceSpan(path=rel, line=4, col=11),
        description="call through a computed name",
        attempted=(Method.GETATTR_TRACED,),
        candidate_ids=("m::maybe_called",),
        candidate_confidence=Confidence.UNKNOWN,
    )
    run = Program(
        tmp_path,
        source,
        entry=["m::main"],
        sinks=["m::final_decision"],
        edges=[],
        unresolved=[residue],
    )
    candidate = run.reach_of("m::maybe_called")
    assert candidate.state is ReachabilityState.UNKNOWN
    assert residue.id in candidate.reason
    assert candidate.provenance.confidence is Confidence.UNKNOWN
    assert run.reach_of("m::main").state is ReachabilityState.UNKNOWN


def test_no_sink_means_unknown_everywhere_never_unreachable(tmp_path: Path) -> None:
    run = Program(tmp_path, "def alpha():\n    return 1\n")
    assert run.analyzer.sink_ids() == ()
    assert {r.state for r in run.reach} == {ReachabilityState.UNKNOWN}
    assert all("no decision sink" in r.reason for r in run.reach)
    assert any(
        u.reason is UnresolvedReason.MISSING_TARGET and "decision sink" in u.description
        for u in run.analyzer.unresolved()
    )


def test_a_container_reaches_the_sink_when_its_contents_do(tmp_path: Path) -> None:
    source = '''
class Strategy:
    def evaluate(self):
        return 1


def final_decision():
    return "BUY"
'''
    run = Program(tmp_path, source, sinks=["m::final_decision"])
    assert run.reach_of("m").state is ReachabilityState.REACHES_SINK
    assert run.reach_of("m::Strategy").state is ReachabilityState.NO_SINK_PATH
    assert run.reach_of("m::Strategy.evaluate").state is ReachabilityState.NO_SINK_PATH
    assert run.reach_of("m::Strategy").reason


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
    run = Program(tmp_path, source, sinks=["m::final_decision"])
    assert any(u.id.endswith("@read:numpy.isnan") for u in run.analyzer.unresolved())
    assert run.reach_of("m::orphan").state is ReachabilityState.NO_SINK_PATH


# ---------------------------------------------------------------------------
# Shapes the corpus does not contain
# ---------------------------------------------------------------------------


def test_finally_is_its_own_block_on_the_return_path(tmp_path: Path) -> None:
    source = '''
def risky(data):
    try:
        return int(data)
    except ValueError:
        return 0
    finally:
        cleanup()


def cleanup():
    return None
'''
    run = Program(tmp_path, source)
    finally_block = run.only_block("m::risky", BlockKind.FINALLY)
    assert any(
        e.target_id == finally_block.id and e.condition == "<enter finally>"
        for e in run.cfg_edges
    ), "a return inside try must pass through finally"
    assert BlockKind.HANDLER in run.kinds_of("m::risky")


def test_break_and_continue_leave_the_loop_correctly(tmp_path: Path) -> None:
    source = '''
def scan(rows):
    for row in rows:
        if row < 0:
            continue
        if row > 100:
            break
    return rows
'''
    run = Program(tmp_path, source)
    head = run.only_block("m::scan", BlockKind.LOOP_HEAD)
    conditions = {(e.source_id, e.condition, e.target_id) for e in run.cfg_edges}
    assert any(c == "<continue>" and target == head.id for _s, c, target in conditions)
    assert any(c == "<break>" for _s, c, _t in conditions)


def test_tree_model_call_is_a_decision_point(tmp_path: Path) -> None:
    source = '''
def score(model, features):
    return model.predict(features)
'''
    run = Program(tmp_path, source)
    assert len(run.decisions) == 1
    decision = run.decisions[0]
    assert decision.condition_source == "model.predict(features)"
    assert decision.provenance is not None
    assert decision.provenance.method is Method.NAME_HEURISTIC
    assert decision.provenance.confidence is Confidence.HEURISTIC
    assert "inside the model" in decision.provenance.note
    assert set(decision.reads_ids) == {"m::score.features", "m::score.model"}


def test_a_named_feature_read_in_a_condition_is_its_own_node(tmp_path: Path) -> None:
    source = '''
def classify(row):
    if row["price"] > 0:
        return "up"
    return "down"
'''
    run = Program(tmp_path, source)
    assert run.decisions[0].reads_ids == ("@feature:price", "m::classify.row")


def test_wiring_with_no_call_site_is_unordered_not_dropped(tmp_path: Path) -> None:
    source = '''
def main():
    return 1


def plugin():
    return 2
'''
    configured = Edge(
        id=make_id("@edge", "config->plugin"),
        kind=EdgeKind.CONFIGURES,
        source_id="m::main",
        target_id="m::plugin",
        provenance=Provenance(method=Method.CONFIG_STRING_MATCH, confidence=Confidence.HEURISTIC),
    )
    run = Program(tmp_path, source, entry=["m::main"], edges=[configured])
    unordered = [n for n in run.order if n.kind is OrderKind.UNORDERED]
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
    run = Program(tmp_path, source, entry=["m::main_a", "m::main_b"])
    root = run.node(make_id("@order", "@cascade"))
    assert root.kind is OrderKind.UNORDERED
    assert root.element_ids == ("m::main_a", "m::main_b")


def test_a_lambda_body_is_unordered_and_reported_not_stitched_in(tmp_path: Path) -> None:
    source = '''
def main():
    fn = lambda: helper()
    return fn


def helper():
    return 1
'''
    run = Program(tmp_path, source, entry=["m::main"])
    ambiguous = [u for u in run.analyzer.unresolved() if u.reason is UnresolvedReason.AMBIGUOUS]
    assert len(ambiguous) == 1 and "lambda" in ambiguous[0].description
    deferred = run.node(make_id("@order", "m::main/deferred"))
    assert deferred.kind is OrderKind.UNORDERED
    assert "m::helper" in run.elements_under(deferred.id)


def test_a_total_order_is_emitted_only_when_one_exists(tmp_path: Path) -> None:
    linear = '''
def main():
    return step()


def step():
    return leaf()


def leaf():
    return 1
'''
    run = Program(tmp_path, linear, entry=["m::main"])
    total = run.node(make_id("@order", "@total"))
    assert list(total.element_ids) == ["m::main", "m::step", "m::leaf"]

    branching = '''
def main(flag):
    if flag:
        return left()
    return right()


def left():
    return 1


def right():
    return 2
'''
    branched = Program(tmp_path, branching, name="n.py", entry=["n::main"])
    assert not [n for n in branched.order if n.id == make_id("@order", "@total")]


# ---------------------------------------------------------------------------
# Auto-detection, unresolved residue, provenance
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
    run = Program(tmp_path, source, name="run_m5.py")
    candidates = run.analyzer.entry_point_candidates()
    assert [c.element_id for c in candidates] == ["run_m5", "run_m5::main"]
    for candidate in candidates:
        assert candidate.role == "ENTRY_POINT"
        assert candidate.evidence
        assert candidate.provenance.confidence is Confidence.PROBABLE
        assert "auto-detected" in candidate.provenance.note
    assert run.analyzer.entry_ids() == ("run_m5", "run_m5::main")


def test_sink_is_auto_detected_and_reported_not_adopted_silently() -> None:
    run = Result("dec_sink", sinks=[])
    candidates = run.analyzer.sink_candidates()
    assert [c.element_id for c in candidates] == ["dec_sink::final_decision"]
    for candidate in candidates:
        assert candidate.role == "DECISION_SINK"
        assert candidate.provenance.method is Method.NAME_HEURISTIC
        assert candidate.provenance.confidence is Confidence.HEURISTIC
        assert "not adopted as fact" in candidate.provenance.note
        assert "hint" in candidate.evidence


def test_declared_entry_that_is_not_inventoried_is_reported(tmp_path: Path) -> None:
    run = Program(tmp_path, "def main():\n    return 1\n", entry=["m::nope"])
    residue = [u for u in run.analyzer.unresolved() if "nope" in u.description]
    assert len(residue) == 1 and residue[0].reason is UnresolvedReason.MISSING_TARGET


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


def test_an_unresolved_call_site_makes_its_order_node_unknown(tmp_path: Path) -> None:
    run = Program(tmp_path, "def main():\n    return mystery()\n", entry=["m::main"])
    unknown = [
        n
        for n in run.order
        if n.provenance is not None and n.provenance.confidence is Confidence.UNKNOWN
    ]
    assert unknown, "a call nobody resolved must not be silently dropped from the order"
    assert any("no resolved target" in n.provenance.note for n in unknown if n.provenance)


@pytest.mark.parametrize("case", CARD3_CASES)
def test_every_emitted_fact_carries_provenance(case: str) -> None:
    run = result(case)
    for block in run.blocks:
        assert block.provenance.method is Method.AST_DIRECT
        assert block.provenance.confidence is Confidence.CERTAIN
        assert block.provenance.span is not None
    for edge in run.cfg_edges:
        assert edge.provenance is not None and edge.provenance.method is Method.AST_DIRECT
    for order_node in run.order:
        assert order_node.provenance is not None and order_node.provenance.note
    for decision in run.decisions:
        assert decision.provenance is not None and decision.provenance.span is not None
    for record in run.reach:
        assert record.provenance.method is Method.CFG_REACHABILITY


def test_order_confidence_is_combined_from_the_edges_it_rests_on(tmp_path: Path) -> None:
    source = '''
def main():
    return step()


def step():
    return 1
'''
    for confidence in (Confidence.RESOLVED, Confidence.PROBABLE, Confidence.HEURISTIC):
        run = Program(tmp_path, source, entry=["m::main"], edge_confidence=confidence)
        root = run.node(make_id("@order", "m::main"))
        total = run.node(make_id("@order", "@total"))
        assert root.provenance is not None and root.provenance.confidence is confidence
        assert total.provenance is not None and total.provenance.confidence is confidence


def test_rank_agrees_with_combine() -> None:
    """`_RANK` mirrors the contract's ordering; this fails if it ever drifts."""
    for left in Confidence:
        for right in Confidence:
            weaker = left if C._RANK[left] <= C._RANK[right] else right
            assert combine(left, right) is weaker


# ---------------------------------------------------------------------------
# Determinism and safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CARD3_CASES)
def test_two_runs_are_byte_identical(case: str) -> None:
    payload = expectation(case)
    elements, edges = case_elements(case), case_edges(case)
    entry = payload.get("entry_ids", [])
    sinks = payload.get("declared_sink_ids", [])
    first = C.CascadeAnalyzer(REPO_ROOT, sink_ids=sinks)
    first.order(elements, edges, entry)
    second = C.CascadeAnalyzer(REPO_ROOT, sink_ids=sinks)
    second.order(list(reversed(elements)), list(reversed(edges)), entry)
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


@pytest.mark.parametrize("case", CARD3_CASES)
def test_every_artifact_is_sorted_by_id(case: str) -> None:
    run = result(case)
    for records in (run.blocks, run.cfg_edges, run.order, run.decisions, run.reach):
        ids = [r.id for r in records]
        assert ids == sorted(ids), case
    for name, text in run.analyzer.artifacts().items():
        rows = [json.loads(line)["id"] for line in text.splitlines()]
        assert rows == sorted(rows), f"{case}/{name}"


def test_reachability_jsonl_round_trips_deterministically() -> None:
    run = result("dec_sink")
    rendered = canonical_jsonl(run.reach)
    assert rendered == canonical_jsonl(list(reversed(run.reach)))
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
    assert run.analyzer.artifacts()["reachability.jsonl"] == rendered


def test_the_card_satisfies_the_cascade_card_protocol() -> None:
    card: CascadeCard = C.CascadeAnalyzer(REPO_ROOT)
    outcome = card.order(case_elements("ord_linear"), case_edges("ord_linear"), ["ord_linear::main"])
    assert len(outcome) == 5
    blocks, cfg_edges, order, decisions, reach = outcome
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
    """Coverage beyond this card's ten cases: every corpus module."""
    cases = sorted(p.name for p in MODE_B.iterdir() if (p / "__init__.py").is_file())
    assert len(cases) >= len(CARD3_CASES)
    analysed = 0
    for case in cases:
        rel = f"tests/fixtures/mode_b/{case}/__init__.py"
        try:
            elements = inventory(REPO_ROOT, rel, module=case)
        except (SyntaxError, UnicodeDecodeError):
            continue  # inv_syntax_error and inv_non_utf8 are meant to be unreadable
        analyzer = C.CascadeAnalyzer(REPO_ROOT)
        blocks, edges, order, decisions, reach = analyzer.order(elements, [], [])
        assert len(reach) == len(elements), case
        assert {e.source_id for e in edges} <= {b.id for b in blocks}, case
        assert all(n.provenance is not None for n in order), case
        assert all(d.element_id in {e.id for e in elements} for d in decisions), case
        analysed += 1
    assert analysed >= len(CARD3_CASES)
    assert not SENTINEL_MARKER.exists()
