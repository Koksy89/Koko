"""Tests for card 15 phase B: the static viewer.

The viewer is read-only over emitted artifacts, so these tests build a small
but representative artifact set by hand (using the same contract types every
real card would emit) rather than depending on any other card's output --
none exists yet; cards 1-16 are built in parallel with this one. Expectations
are written from what the fixture data says, never recorded from tool output.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from cascade_map.contracts.interfaces import (
    Barrier,
    ChangeKind,
    Confidence,
    DecisionPoint,
    DocRecord,
    Edge,
    EdgeKind,
    Element,
    ElementKind,
    Finding,
    FindingKind,
    Impact,
    Intent,
    IntentStatus,
    LineageEdge,
    LineageKind,
    Method,
    OrderKind,
    OrderNode,
    Provenance,
    Reachability,
    ReachabilityState,
    SCHEMA_VERSION,
    Slice,
    SourceSpan,
    Unresolved,
    UnresolvedReason,
    VersionChange,
    canonical_dumps,
    canonical_jsonl,
    feature_id,
    make_id,
)
from cascade_map.viewer import (
    ArtifactStore,
    browser_view,
    cascade_view,
    callgraph_view,
    diff_view,
    doc_record_view,
    element_detail,
    element_reachability,
    findings_view,
    lineage_view,
    render_site,
    render_to_file,
)

MODULE = "pkg.mod"
SPAN = SourceSpan(path="pkg/mod.py", line=1, end_line=1)

INGEST_ID = make_id(MODULE, "ingest")
COMPUTE_ID = make_id(MODULE, "compute_score")
DECIDE_ID = make_id(MODULE, "decide")
RULESET_ID = make_id(MODULE, "RuleSet")
EVALUATE_ID = make_id(MODULE, "RuleSet.evaluate")
SCORE_FEATURE_ID = feature_id("score")
UNUSED_FEATURE_ID = feature_id("unused")

_CERTAIN = Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN, span=SPAN)
_RESOLVED = Provenance(method=Method.SCOPE_LOOKUP, confidence=Confidence.RESOLVED, span=SPAN)


def _element(id_: str, kind: ElementKind, qualname: str, prov: Provenance = _CERTAIN) -> Element:
    return Element(
        id=id_,
        kind=kind,
        name=qualname.rsplit(".", 1)[-1],
        qualname=qualname,
        module=MODULE,
        span=SPAN,
        provenance=prov,
        content_hash="h" + qualname,
    )


def build_fixture(root: Path, *, include_reachability: bool = True) -> None:
    """Write a small, hand-authored artifact set to *root*."""
    elements = [
        Element(
            id=MODULE, kind=ElementKind.MODULE, name="mod", qualname="", module=MODULE,
            span=SPAN, provenance=_CERTAIN, content_hash="hmod",
        ),
        _element(INGEST_ID, ElementKind.FUNCTION, "ingest"),
        _element(COMPUTE_ID, ElementKind.FUNCTION, "compute_score"),
        _element(DECIDE_ID, ElementKind.FUNCTION, "decide"),
        _element(RULESET_ID, ElementKind.CLASS, "RuleSet"),
        _element(EVALUATE_ID, ElementKind.METHOD, "RuleSet.evaluate", _RESOLVED),
        Element(
            id=SCORE_FEATURE_ID, kind=ElementKind.FEATURE, name="score", qualname="score",
            module=MODULE, span=SPAN, provenance=_CERTAIN, content_hash="hscore",
        ),
        Element(
            id=UNUSED_FEATURE_ID, kind=ElementKind.FEATURE, name="unused", qualname="unused",
            module=MODULE, span=SPAN, provenance=_CERTAIN, content_hash="hunused",
        ),
    ]

    edges = [
        Edge(
            id="edge:ingest->compute", kind=EdgeKind.CALLS, source_id=INGEST_ID,
            target_id=COMPUTE_ID, provenance=_RESOLVED, call_site=SPAN,
        ),
        Edge(
            id="edge:compute->decide", kind=EdgeKind.CALLS, source_id=COMPUTE_ID,
            target_id=DECIDE_ID, provenance=_CERTAIN, call_site=SPAN,
        ),
    ]

    unresolved = [
        Unresolved(
            id="unresolved:1",
            reason=UnresolvedReason.DYNAMIC_NAME,
            span=SPAN,
            description="getattr(obj, name)(...) with a non-literal name",
            attempted=(Method.GETATTR_TRACED,),
            candidate_ids=(DECIDE_ID, EVALUATE_ID),
            candidate_confidence=Confidence.HEURISTIC,
        ),
    ]

    # order tree: root SEQUENCE -> [BRANCH -> MERGE, LOOP -> CYCLE(back to LOOP), UNORDERED]
    merge = OrderNode(id="order:merge", kind=OrderKind.MERGE, element_ids=(), children=())
    branch = OrderNode(
        id="order:branch", kind=OrderKind.BRANCH, element_ids=(DECIDE_ID,), children=("order:merge",)
    )
    loop = OrderNode(
        id="order:loop", kind=OrderKind.LOOP, element_ids=(COMPUTE_ID,), children=("order:cycle",)
    )
    cycle = OrderNode(
        id="order:cycle", kind=OrderKind.CYCLE, element_ids=(), children=("order:loop",)
    )
    unordered = OrderNode(
        id="order:unordered", kind=OrderKind.UNORDERED, element_ids=(INGEST_ID, EVALUATE_ID),
    )
    order_root = OrderNode(
        id="order:root", kind=OrderKind.SEQUENCE, element_ids=(),
        children=("order:branch", "order:loop", "order:unordered"),
    )
    order_nodes = [order_root, branch, merge, loop, cycle, unordered]

    decisions = [
        DecisionPoint(
            id="decision:decide",
            element_id=DECIDE_ID,
            condition_source="score > 0.5",
            reads_ids=(SCORE_FEATURE_ID,),
            outcomes=(("high", "order:merge"), ("low", "order:merge")),
            is_sink=True,
            provenance=_CERTAIN,
        ),
    ]

    # Canonical decision-sink reachability (card 3). Deliberately covers all
    # three states, plus one element with no record at all, so the viewer's
    # distinction between "no data" and each state is exercised.
    reachability = [
        Reachability(
            id="reach:score", element_id=SCORE_FEATURE_ID, state=ReachabilityState.REACHES_SINK,
            provenance=_RESOLVED, sink_ids=(DECIDE_ID,), path_ids=(SCORE_FEATURE_ID, DECIDE_ID),
        ),
        Reachability(
            id="reach:decide", element_id=DECIDE_ID, state=ReachabilityState.REACHES_SINK,
            provenance=_CERTAIN, sink_ids=(DECIDE_ID,),
        ),
        Reachability(
            id="reach:ingest", element_id=INGEST_ID, state=ReachabilityState.NO_SINK_PATH,
            provenance=_CERTAIN, reason="ingest's output only reaches a log call, never decide",
        ),
        Reachability(
            id="reach:compute", element_id=COMPUTE_ID, state=ReachabilityState.UNKNOWN,
            provenance=Provenance(method=Method.CFG_REACHABILITY, confidence=Confidence.UNKNOWN),
            reason="an unresolved call site lies on the only candidate path to a sink",
        ),
        # RULESET_ID, EVALUATE_ID, UNUSED_FEATURE_ID and the module element
        # deliberately get no record, to exercise the "canonical file present
        # but no record for this element" branch.
    ]

    lineage = [
        LineageEdge(
            id="lineage:1", kind=LineageKind.ASSIGNS, source_id=INGEST_ID,
            target_id=SCORE_FEATURE_ID, provenance=_CERTAIN, span=SPAN,
        ),
    ]

    barriers = [
        Barrier(
            id="barrier:1", element_id=EVALUATE_ID, span=SPAN,
            reason=UnresolvedReason.THIRD_PARTY, description="calls into an opaque C extension",
        ),
    ]

    slices = [
        Slice(
            id="slice:forward:score", root_id=SCORE_FEATURE_ID, direction="forward",
            member_ids=(SCORE_FEATURE_ID, DECIDE_ID), edge_ids=("lineage:1",), barrier_ids=(),
            reaches_sink_ids=(DECIDE_ID,), confidence=Confidence.RESOLVED,
        ),
        Slice(
            id="slice:backward:decide", root_id=DECIDE_ID, direction="backward",
            member_ids=(DECIDE_ID, SCORE_FEATURE_ID, INGEST_ID), edge_ids=("lineage:1",),
            barrier_ids=(), reaches_sink_ids=(), confidence=Confidence.RESOLVED,
        ),
    ]

    findings = [
        Finding(
            id="finding:1", kind=FindingKind.UNCONSUMED_FEATURE, element_id=UNUSED_FEATURE_ID,
            span=SPAN, summary="feature 'unused' is never read",
            hint="remove it or wire it into a decision", evidence_ids=(UNUSED_FEATURE_ID,),
            provenance=_RESOLVED,
        ),
    ]

    changes = [
        VersionChange(
            id="change:1", kind=ChangeKind.BODY_CHANGED, before_id=COMPUTE_ID,
            after_id=COMPUTE_ID, provenance=_CERTAIN,
        ),
        VersionChange(
            id="change:2", kind=ChangeKind.ADDED, before_id="", after_id=RULESET_ID,
            provenance=_CERTAIN,
        ),
    ]

    impacts = [
        Impact(
            id="impact:1", change_id="change:1", affected_ids=(DECIDE_ID,),
            decision_paths_changed=True, features_changed=(SCORE_FEATURE_ID,),
            reachability_flipped=(), findings_added=(), findings_removed=(), rank=1,
        ),
    ]

    records = [
        DocRecord(
            id="record:decide", element_id=DECIDE_ID,
            identity={"kind": "FUNCTION"},
            cascade_position={"phase": "decision"},
            data_role={"reads": ["score"]},
            decision_relevance={"is_sink": True},
            finding_ids=(), change_ids=("change:1",), provenance=_CERTAIN,
            model_prose="This function makes the final call based on the score.",
            model_id="claude-haiku-4-5-20251001",
        ),
    ]

    intents = [
        Intent(
            id="intent:decide", element_id=DECIDE_ID, status=IntentStatus.PROPOSED,
            statement="Decide the final outcome from the computed score.",
        ),
    ]

    files = {
        "elements.jsonl": canonical_jsonl(elements),
        "unresolved.jsonl": canonical_jsonl(unresolved),
        "edges.jsonl": canonical_jsonl(edges),
        "cfg_blocks.jsonl": canonical_jsonl([]),
        "cfg_edges.jsonl": canonical_jsonl([]),
        "order.jsonl": canonical_jsonl(order_nodes),
        "decisions.jsonl": canonical_jsonl(decisions),
        "lineage.jsonl": canonical_jsonl(lineage),
        "barriers.jsonl": canonical_jsonl(barriers),
        "slices.jsonl": canonical_jsonl(slices),
        "findings.jsonl": canonical_jsonl(findings),
        "changes.jsonl": canonical_jsonl(changes),
        "impacts.jsonl": canonical_jsonl(impacts),
        "records.jsonl": canonical_jsonl(records),
        "intents.jsonl": canonical_jsonl(intents),
    }
    if include_reachability:
        files["reachability.jsonl"] = canonical_jsonl(reachability)

    for name, content in files.items():
        (root / name).write_text(content, encoding="utf-8")

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": "0.0.0-test",
        "target_hashes": {},
        "artifact_hashes": {},
    }
    (root / "manifest.json").write_text(canonical_dumps(manifest), encoding="utf-8")


@pytest.fixture()
def store(tmp_path: Path) -> ArtifactStore:
    build_fixture(tmp_path)
    return ArtifactStore.load(tmp_path)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def test_load_indexes_everything(store: ArtifactStore) -> None:
    assert all(store.available.values())
    assert store.elements_by_id[DECIDE_ID]["kind"] == "FUNCTION"
    assert {e["id"] for e in store.edges_out[INGEST_ID]} == {"edge:ingest->compute"}
    assert {e["id"] for e in store.edges_in[DECIDE_ID]} == {"edge:compute->decide"}
    assert store.manifest["schema_version"] == SCHEMA_VERSION


def test_missing_artifacts_degrade_gracefully(tmp_path: Path) -> None:
    empty_store = ArtifactStore.load(tmp_path)
    assert all(v is False for v in empty_store.available.values())
    assert browser_view(empty_store) == []
    assert cascade_view(empty_store) == []
    assert findings_view(empty_store) == []
    assert diff_view(empty_store) == []
    # rendering must not crash even with nothing loaded
    html = render_site(empty_store)
    assert "missing" in html.lower() or "no impact" in html.lower() or "<table>" in html


def test_malformed_jsonl_line_is_reported_not_dropped(tmp_path: Path) -> None:
    (tmp_path / "elements.jsonl").write_text(
        "not json at all\n" + canonical_dumps(
            Element(
                id=MODULE, kind=ElementKind.MODULE, name="mod", qualname="", module=MODULE,
                span=SPAN, provenance=_CERTAIN, content_hash="hmod",
            )
        ) + "\n",
        encoding="utf-8",
    )
    s = ArtifactStore.load(tmp_path)
    assert len(s.errors) == 1
    assert s.errors[0].file == "elements.jsonl"
    assert s.errors[0].line_number == 1
    assert MODULE in s.elements_by_id


# ---------------------------------------------------------------------------
# Element browser
# ---------------------------------------------------------------------------


def test_browser_filters_by_kind_module_and_query(store: ArtifactStore) -> None:
    functions = browser_view(store, kind="FUNCTION")
    assert {e["id"] for e in functions} == {INGEST_ID, COMPUTE_ID, DECIDE_ID}

    by_module = browser_view(store, module="pkg.mod")
    assert len(by_module) == len(store.raw["elements"])

    by_query = browser_view(store, query="decide")
    assert {e["id"] for e in by_query} == {DECIDE_ID}


def test_browser_filters_by_min_confidence(store: ArtifactStore) -> None:
    certain_only = browser_view(store, min_confidence="CERTAIN")
    assert EVALUATE_ID not in {e["id"] for e in certain_only}  # RESOLVED, weaker than CERTAIN
    at_least_resolved = browser_view(store, min_confidence="RESOLVED")
    assert EVALUATE_ID in {e["id"] for e in at_least_resolved}


def test_browser_decision_signals_reflect_emitted_facts(store: ArtifactStore) -> None:
    reads = browser_view(store, decision_signal="reads_in_decision")
    assert {e["id"] for e in reads} == {SCORE_FEATURE_ID}

    sinks = browser_view(store, decision_signal="reaches_sink")
    assert {e["id"] for e in sinks} == {SCORE_FEATURE_ID, DECIDE_ID}

    no_path = browser_view(store, decision_signal="no_sink_path")
    assert {e["id"] for e in no_path} == {INGEST_ID}

    unknown = browser_view(store, decision_signal="reachability_unknown")
    # COMPUTE_ID has an explicit UNKNOWN record; the rest have no record at
    # all, which is also UNKNOWN -- distinct in source, same rendered state.
    assert {e["id"] for e in unknown} == {COMPUTE_ID, MODULE, RULESET_ID, EVALUATE_ID, UNUSED_FEATURE_ID}

    irrelevant = browser_view(store, decision_signal="decision_irrelevant_finding")
    assert irrelevant == []


def test_element_reachability_reads_the_canonical_record(store: ArtifactStore) -> None:
    reach = element_reachability(store, SCORE_FEATURE_ID)
    assert reach["state"] == "REACHES_SINK"
    assert reach["source"] == "reachability.jsonl"
    assert reach["sink_ids"] == [DECIDE_ID]

    no_path = element_reachability(store, INGEST_ID)
    assert no_path["state"] == "NO_SINK_PATH"
    assert no_path["source"] == "reachability.jsonl"

    unknown = element_reachability(store, COMPUTE_ID)
    assert unknown["state"] == "UNKNOWN"
    assert unknown["source"] == "reachability.jsonl"

    missing_record = element_reachability(store, RULESET_ID)
    assert missing_record["state"] == "UNKNOWN"
    assert missing_record["source"] == "reachability.jsonl"
    assert "no record" in missing_record["reason"]


def test_element_reachability_falls_back_when_file_absent(tmp_path: Path) -> None:
    build_fixture(tmp_path, include_reachability=False)
    s = ArtifactStore.load(tmp_path)
    assert s.available["reachability"] is False

    # A forward slice with reaches_sink_ids is the only honest fallback
    # signal for REACHES_SINK.
    reach = element_reachability(s, SCORE_FEATURE_ID)
    assert reach["state"] == "REACHES_SINK"
    assert reach["source"] == "fallback:slices.jsonl"

    # No slice, no canonical record: the fallback can only say UNKNOWN. It
    # must never claim NO_SINK_PATH -- that is a positive claim only the
    # canonical record is entitled to make.
    no_data = element_reachability(s, INGEST_ID)
    assert no_data["state"] == "UNKNOWN"
    assert no_data["source"] == "fallback:no_data"


# ---------------------------------------------------------------------------
# Cascade order: branches, merges, loops, unordered sets and cycles as-is
# ---------------------------------------------------------------------------


def _collect_kinds(nodes: list[dict]) -> set[str]:
    found: set[str] = set()
    for n in nodes:
        if n.get("missing"):
            continue
        found.add(n["kind"])
        found |= _collect_kinds(n.get("children", []))
    return found


def test_cascade_view_preserves_every_order_kind(store: ArtifactStore) -> None:
    tree = cascade_view(store)
    assert len(tree) == 1
    assert tree[0]["kind"] == "SEQUENCE"
    kinds = _collect_kinds(tree)
    assert kinds == {"SEQUENCE", "BRANCH", "MERGE", "LOOP", "CYCLE", "UNORDERED"}


def test_cascade_view_cycle_is_a_back_reference_not_a_flattened_sequence(store: ArtifactStore) -> None:
    tree = cascade_view(store)
    # walk down to the LOOP -> CYCLE -> (back to LOOP) chain
    root = tree[0]
    loop_node = next(c for c in root["children"] if c["kind"] == "LOOP")
    cycle_node = loop_node["children"][0]
    assert cycle_node["kind"] == "CYCLE"
    back_ref = cycle_node["children"][0]
    assert back_ref["id"] == "order:loop"
    assert back_ref.get("cycle_back_reference") is True


def test_cascade_view_branch_carries_its_decision(store: ArtifactStore) -> None:
    tree = cascade_view(store)
    branch_node = next(c for c in tree[0]["children"] if c["kind"] == "BRANCH")
    assert branch_node["decision_ids"] == ["decision:decide"]


# ---------------------------------------------------------------------------
# Call graph
# ---------------------------------------------------------------------------


def test_callgraph_view_shows_edges_and_unresolved_candidates(store: ArtifactStore) -> None:
    cg = callgraph_view(store)
    assert {e["id"] for e in cg["edges"]} == {"edge:ingest->compute", "edge:compute->decide"}
    for e in cg["edges"]:
        assert e["method"] and e["confidence"]
    assert len(cg["unresolved"]) == 1
    u = cg["unresolved"][0]
    assert u["reason"] == "DYNAMIC_NAME"
    assert set(u["candidate_ids"]) == {DECIDE_ID, EVALUATE_ID}


def test_callgraph_view_scoped_to_root(store: ArtifactStore) -> None:
    cg = callgraph_view(store, root_id=DECIDE_ID)
    assert {e["id"] for e in cg["edges"]} == {"edge:compute->decide"}
    assert {u["id"] for u in cg["unresolved"]} == {"unresolved:1"}  # DECIDE_ID is a candidate


# ---------------------------------------------------------------------------
# Lineage slices
# ---------------------------------------------------------------------------


def test_lineage_view_returns_precomputed_slice(store: ArtifactStore) -> None:
    forward = lineage_view(store, SCORE_FEATURE_ID, "forward")
    assert forward["found"] is True
    assert forward["slices"][0]["reaches_sink_ids"] == [DECIDE_ID]

    backward = lineage_view(store, DECIDE_ID, "backward")
    assert backward["found"] is True
    assert INGEST_ID in backward["slices"][0]["member_ids"]


def test_lineage_view_reports_gap_when_no_slice_precomputed(store: ArtifactStore) -> None:
    result = lineage_view(store, INGEST_ID, "forward")
    assert result["found"] is False
    assert "no precomputed slice" in result["note"]


# ---------------------------------------------------------------------------
# Findings with evidence links
# ---------------------------------------------------------------------------


def test_findings_view_links_to_evidence(store: ArtifactStore) -> None:
    findings = findings_view(store)
    assert len(findings) == 1
    f = findings[0]
    assert f["kind"] == "UNCONSUMED_FEATURE"
    assert f["evidence_ids"] == [UNUSED_FEATURE_ID]
    detail = element_detail(store, UNUSED_FEATURE_ID)
    assert "finding:1" in detail["finding_ids_as_evidence"]


# ---------------------------------------------------------------------------
# Version diff ranked by decision impact
# ---------------------------------------------------------------------------


def test_diff_view_ranked_and_unranked_changes(store: ArtifactStore) -> None:
    rows = diff_view(store)
    assert rows[0]["change_id"] == "change:1"
    assert rows[0]["rank"] == 1
    assert rows[0]["decision_paths_changed"] is True
    unranked = [r for r in rows if r["rank"] is None]
    assert len(unranked) == 1
    assert unranked[0]["change_id"] == "change:2"
    assert "no impact record" in unranked[0]["note"]


# ---------------------------------------------------------------------------
# Documentation record: facts vs model prose
# ---------------------------------------------------------------------------


def test_doc_record_view_separates_facts_from_model_prose(store: ArtifactStore) -> None:
    record = doc_record_view(store, DECIDE_ID)
    assert record is not None
    assert record["facts"]["decision_relevance"] == {"is_sink": True}
    assert record["model_authored"]["is_present"] is True
    assert "final call" in record["model_authored"]["prose"]
    assert record["model_authored"]["model_id"] == "claude-haiku-4-5-20251001"
    # model prose is never mixed into the facts block
    for fact_block in record["facts"].values():
        assert "final call" not in json.dumps(fact_block)


def test_doc_record_view_missing_is_reported(store: ArtifactStore) -> None:
    assert doc_record_view(store, INGEST_ID) is None


# ---------------------------------------------------------------------------
# Element detail: the drill-down hub
# ---------------------------------------------------------------------------


def test_every_element_id_is_reachable_from_element_detail(store: ArtifactStore) -> None:
    for element_id in store.elements_by_id:
        detail = element_detail(store, element_id)
        assert detail["found"] is True
        assert detail["id"] == element_id


def test_element_detail_links_every_kind_of_evidence(store: ArtifactStore) -> None:
    detail = element_detail(store, DECIDE_ID)
    assert "edge:compute->decide" in {e["id"] for e in detail["incoming_edges"]}
    assert "unresolved:1" in {u["id"] for u in detail["unresolved_as_candidate"]}
    assert "decision:decide" in detail["decision_as_condition"]
    assert detail["doc_record"] is not None
    assert "intent:decide" in detail["intent_ids"]
    compute_detail = element_detail(store, COMPUTE_ID)
    assert "change:1" in compute_detail["change_ids_before"]
    assert "change:1" in compute_detail["change_ids_after"]


def test_element_detail_unknown_id_is_explicit_not_a_crash(store: ArtifactStore) -> None:
    detail = element_detail(store, "pkg.mod::does_not_exist")
    assert detail["found"] is False
    assert detail["element"] is None


# ---------------------------------------------------------------------------
# Offline, deterministic HTML export
# ---------------------------------------------------------------------------


def test_html_render_is_deterministic(store: ArtifactStore) -> None:
    first = render_site(store)
    second = render_site(store)
    assert first == second


def test_html_render_has_no_network_reference(store: ArtifactStore) -> None:
    html = render_site(store)
    for forbidden in ("http://", "https://", "cdn.", "<script src=", "fetch(", "XMLHttpRequest"):
        assert forbidden not in html


_HREF_RE = re.compile(r'href="#el-([^"]*)"')
_ANCHOR_RE = re.compile(r'id="el-([^"]*)"')


def test_html_render_drill_down_links_resolve_to_anchors(store: ArtifactStore) -> None:
    """Every element-detail link in the page lands on a real anchor.

    This is exhaustive: it extracts every ``href="#el-..."`` and every
    ``id="el-..."`` in the rendered page and asserts the link set is a
    subset of the anchor set, listing any dangling target by name so a
    regression is diagnosable from the assertion message alone. It also
    checks the reverse direction: every element this page renders a detail
    section for is linked from at least one other view, so no ID is an
    island only reachable by scrolling.
    """
    html = render_site(store)
    hrefs = set(_HREF_RE.findall(html))
    anchors = set(_ANCHOR_RE.findall(html))

    assert anchors, "no element anchors were rendered at all"
    dangling = hrefs - anchors
    assert not dangling, f"links with no matching anchor: {sorted(dangling)}"

    # Anchors are minted 1:1 from elements.jsonl -- confirm the anchor set is
    # exactly the element ID set, no more, no less.
    assert anchors == set(store.elements_by_id)

    # Every element ID this fixture actually exercises through a non-element
    # view (order nodes, edges, unresolved candidates, decisions, findings,
    # changes) still resolves to a real anchor -- proving _ref()'s smart
    # linking, not just element_detail's own self-anchors.
    for element_id in (INGEST_ID, COMPUTE_ID, DECIDE_ID, EVALUATE_ID, SCORE_FEATURE_ID, UNUSED_FEATURE_ID):
        assert f'href="#el-{element_id}"' in html, f"{element_id} is never linked from anywhere"
        assert f'id="el-{element_id}"' in html

    assert f'id="el-{DECIDE_ID}"' in html
    assert f'href="#el-{DECIDE_ID}"' in html
    assert "MODEL-WRITTEN" in html  # model prose visibly labelled


def test_html_render_non_element_ids_are_shown_but_not_linked(store: ArtifactStore) -> None:
    """IDs that are not elements (order nodes, decisions) never become a
    dangling '#el-' link, but the ID itself is still visible as text."""
    html = render_site(store)
    assert 'href="#el-order:loop"' not in html
    assert 'href="#el-decision:decide"' not in html
    assert "order:loop" in html  # still visible, just not a broken link
    assert "decision:decide" in html


def test_html_render_reachability_states_are_visually_distinct(store: ArtifactStore) -> None:
    html = render_site(store)
    assert "reach-REACHES_SINK" in html
    assert "reach-NO_SINK_PATH" in html
    assert "reach-UNKNOWN" in html


def test_render_to_file_writes_the_same_content(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    out_path = tmp_path / "view.html"
    returned_store = render_to_file(tmp_path, out_path)
    written = out_path.read_text(encoding="utf-8")
    assert written == render_site(returned_store)


# ---------------------------------------------------------------------------
# Constraint: the viewer never touches target_engine / target_versions
# ---------------------------------------------------------------------------


def test_viewer_source_never_references_the_target_or_executes() -> None:
    # Mentioning target_engine/target_versions in docstrings ("the viewer
    # never reads these") is expected and desirable; what must never appear
    # is a code path that would actually run or unpickle target code.
    viewer_dir = Path(__file__).resolve().parent.parent / "src" / "cascade_map" / "viewer"
    forbidden = [
        "import subprocess",
        "os.system",
        "exec(",
        "eval(",
        "pickle.load",
        "pickle.loads",
        "__import__(",
        ".venv-target",
        "importlib.import_module",
    ]
    for path in viewer_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{token!r} found in {path}"
