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
    AlignmentVerdict,
    Barrier,
    BlockedAttempt,
    CaptureStatus,
    ChangeKind,
    Confidence,
    Contradiction,
    DecisionPoint,
    DocRecord,
    Edge,
    EdgeKind,
    Element,
    ElementKind,
    EventKind,
    Finding,
    FindingKind,
    Impact,
    Intent,
    IntentStatus,
    LineageEdge,
    LineageKind,
    MappingReport,
    Method,
    NarrativeStep,
    NondeterminismObservation,
    OrderKind,
    OrderNode,
    Provenance,
    Reachability,
    ReachabilityState,
    RunRecord,
    SCHEMA_VERSION,
    ScenarioFailure,
    Slice,
    SourceSpan,
    TraceEvent,
    Unresolved,
    UnresolvedReason,
    ValueCapture,
    Verdict,
    VersionChange,
    canonical_dumps,
    canonical_jsonl,
    feature_id,
    make_id,
)
from cascade_map.viewer import (
    ArtifactStore,
    RuntimeStore,
    browser_view,
    cascade_view,
    callgraph_view,
    contradictions_view,
    decision_branch_view,
    diff_view,
    doc_record_view,
    element_detail,
    element_reachability,
    event_detail_view,
    findings_view,
    lineage_view,
    list_runs,
    mapping_view,
    narrative_view,
    nondeterminism_view,
    observed_order_view,
    render_site,
    render_to_file,
    render_to_file_with_runtime,
    runtime_overview_view,
    unmapped_events_view,
    verdicts_view,
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


# ---------------------------------------------------------------------------
# Phase A: the runtime overlay
#
# Same discipline as build_fixture above -- a hand-authored artifact set
# using the real contract types, since cards 11-14 exist independently of
# this one. Nothing here is read from an actual harness or tracer run.
# ---------------------------------------------------------------------------

RUN_ID = "run_0001"

_RUNTIME_PROV = Provenance(
    method=Method.RUNTIME_OBSERVED, confidence=Confidence.CERTAIN, run_id=RUN_ID,
)


def _runtime_prov(*event_ids: str) -> Provenance:
    return Provenance(
        method=Method.RUNTIME_OBSERVED, confidence=Confidence.CERTAIN,
        run_id=RUN_ID, event_ids=event_ids,
    )


def build_runtime_fixture(
    root: Path, run_id: str = RUN_ID, *,
    scenario_failure: ScenarioFailure | None = None,
    observer_failure: ScenarioFailure | None = None,
) -> None:
    """Write a small, hand-authored runtime overlay to
    ``root/runtime/<run_id>/``, keyed onto the same element IDs
    ``build_fixture`` already wrote to *root*. *scenario_failure* /
    *observer_failure* let a test build a run whose scenario raised, or
    whose observer itself broke, without duplicating the whole fixture."""
    rundir = root / "runtime" / run_id
    rundir.mkdir(parents=True, exist_ok=True)

    events = [
        TraceEvent(
            event_id="evt_00000001", run_id=run_id, kind=EventKind.CALL,
            element_id=INGEST_ID, sequence=1, depth=0,
            values={"raw": ValueCapture(status=CaptureStatus.FULL, repr_text="{'n': 3}", type_name="dict")},
            provenance=_runtime_prov("evt_00000001"),
        ),
        TraceEvent(
            event_id="evt_00000002", run_id=run_id, kind=EventKind.RETURN,
            element_id=INGEST_ID, sequence=2, depth=0, caller_event_id="evt_00000001",
            values={
                "result": ValueCapture(
                    status=CaptureStatus.SUMMARIZED, repr_text="[list of 500 items] (truncated)",
                    type_name="list", original_size=500, reason="exceeds per-value cap",
                ),
            },
            provenance=_runtime_prov("evt_00000002"),
        ),
        TraceEvent(
            event_id="evt_00000003", run_id=run_id, kind=EventKind.CALL,
            element_id=COMPUTE_ID, sequence=3, depth=0,
            provenance=_runtime_prov("evt_00000003"),
        ),
        TraceEvent(
            event_id="evt_00000004", run_id=run_id, kind=EventKind.FEATURE_WRITE,
            element_id=COMPUTE_ID, sequence=4, depth=0,
            values={
                "score": ValueCapture(
                    status=CaptureStatus.REDACTED, repr_text="<redacted>",
                    reason="declared sensitive by the intent registry",
                ),
                "raw_frame": ValueCapture(
                    status=CaptureStatus.DROPPED, repr_text="",
                    original_size=2_000_000, reason="exceeds the absolute per-value cap",
                ),
            },
            provenance=_runtime_prov("evt_00000004"),
        ),
        TraceEvent(
            event_id="evt_00000005", run_id=run_id, kind=EventKind.RETURN,
            element_id=COMPUTE_ID, sequence=5, depth=0, caller_event_id="evt_00000003",
            provenance=_runtime_prov("evt_00000005"),
        ),
        TraceEvent(
            event_id="evt_00000006", run_id=run_id, kind=EventKind.CALL,
            element_id=DECIDE_ID, sequence=6, depth=0,
            provenance=_runtime_prov("evt_00000006"),
        ),
        TraceEvent(
            event_id="evt_00000007", run_id=run_id, kind=EventKind.DECISION,
            element_id=DECIDE_ID, sequence=7, depth=0, branch_taken="high",
            provenance=_runtime_prov("evt_00000007"),
        ),
        TraceEvent(
            event_id="evt_00000008", run_id=run_id, kind=EventKind.RETURN,
            element_id=DECIDE_ID, sequence=8, depth=0, caller_event_id="evt_00000006",
            provenance=_runtime_prov("evt_00000008"),
        ),
        TraceEvent(
            event_id="evt_00000009", run_id=run_id, kind=EventKind.UNMAPPED,
            element_id="", sequence=9, depth=0,
            provenance=Provenance(
                method=Method.RUNTIME_OBSERVED, confidence=Confidence.CERTAIN, run_id=run_id,
                event_ids=("evt_00000009",),
                span=SourceSpan(path="pkg/mod.py", line=42),
                note="dynamic getattr dispatch resolved to no static candidate",
            ),
        ),
    ]

    run_record = RunRecord(
        run_id=run_id,
        target_hashes={"pkg/mod.py": "hashmod"},
        graph_hash="graphhash1",
        scenario="scenario_smoke",
        interpreter="python3.11",
        controls_active={"network": True, "filesystem_writes": True, "process_spawn": True},
        blocked=(
            BlockedAttempt(
                id="blocked:1", kind="network",
                detail="outbound HTTPS attempt to api.example.com",
                element_id=COMPUTE_ID, event_id="evt_00000003",
            ),
        ),
        unguaranteed=(
            "a direct call to the interpreter's low-level process-spawn primitive "
            "bypasses sys.audit",
            "a child process permitted by declared_process_names is unaudited once running",
        ),
        sandbox_dir="sandbox/run_0001",
        scenario_failure=scenario_failure,
        observer_failure=observer_failure,
    )

    mapping = MappingReport(
        run_id=run_id, total_events=9, mapped_events=8, unmapped_events=1,
        unmapped_by_reason={"dynamic_dispatch": 1},
    )

    contradictions = [
        Contradiction(
            id="contra:1", element_id=COMPUTE_ID,
            claim="edge:compute->decide predicts compute_score calls decide directly",
            observation="compute_score returned to its caller; decide was invoked from a "
                         "different call site than the static edge records",
            provenance=_runtime_prov("evt_00000005", "evt_00000006"),
            static_evidence_ids=("edge:compute->decide",),
        ),
    ]

    nondeterminism = [
        NondeterminismObservation(
            id="nondet:1", element_id=COMPUTE_ID, kind="clock",
            detail="compute_score reads time.time() to seed a jitter factor",
            provenance=_runtime_prov("evt_00000004"),
        ),
    ]

    verdicts = [
        AlignmentVerdict(
            id="verdict:1", element_id=DECIDE_ID, intent_id="intent:decide",
            verdict=Verdict.ALIGNED,
            expectation="decide the final outcome from the computed score",
            observation="decide read score and branched 'high' as expected",
            evidence_ids=("evt_00000007",),
            provenance=_runtime_prov("evt_00000007"),
        ),
        AlignmentVerdict(
            id="verdict:2", element_id=EVALUATE_ID, intent_id="intent:evaluate-not-exercised",
            verdict=Verdict.NOT_EXERCISED,
            expectation="RuleSet.evaluate should apply the configured rules",
            observation="no event in this run entered RuleSet.evaluate",
            evidence_ids=(),
            provenance=_RUNTIME_PROV,
        ),
    ]

    narrative = [
        NarrativeStep(
            id="narr:1", run_id=run_id, sequence=1, phase="segment 1",
            text="ingest ran once and produced a raw payload.",
            element_ids=(INGEST_ID,), event_ids=("evt_00000001", "evt_00000002"),
        ),
        NarrativeStep(
            id="narr:2", run_id=run_id, sequence=2, phase="segment 2",
            text="compute_score derived a score from the payload.",
            element_ids=(COMPUTE_ID,), event_ids=("evt_00000003", "evt_00000004", "evt_00000005"),
            model_prose="This step turns raw signals into a single confidence number.",
            model_id="claude-haiku-4-5-20251001",
        ),
        NarrativeStep(
            id="narr:3", run_id=run_id, sequence=3, phase="segment 3",
            text="decide branched 'high' and returned the final decision.",
            element_ids=(DECIDE_ID,), event_ids=("evt_00000006", "evt_00000007", "evt_00000008"),
        ),
    ]

    (rundir / "run.json").write_text(canonical_dumps(run_record), encoding="utf-8")
    (rundir / "mapping.json").write_text(canonical_dumps(mapping), encoding="utf-8")
    (rundir / "events.jsonl").write_text(canonical_jsonl(events, sort_key="event_id"), encoding="utf-8")
    (rundir / "contradictions.jsonl").write_text(canonical_jsonl(contradictions), encoding="utf-8")
    (rundir / "nondeterminism.jsonl").write_text(canonical_jsonl(nondeterminism), encoding="utf-8")
    (rundir / "verdicts.jsonl").write_text(canonical_jsonl(verdicts), encoding="utf-8")
    (rundir / "narrative.jsonl").write_text(canonical_jsonl(narrative), encoding="utf-8")


@pytest.fixture()
def runtime_store(tmp_path: Path) -> tuple[ArtifactStore, RuntimeStore]:
    build_fixture(tmp_path)
    build_runtime_fixture(tmp_path)
    return ArtifactStore.load(tmp_path), RuntimeStore.load(tmp_path, RUN_ID)


# -- loading ------------------------------------------------------------


def test_runtime_store_loads_and_indexes_everything(runtime_store) -> None:
    _store, rstore = runtime_store
    assert all(rstore.available.values())
    assert len(rstore.events_ordered) == 9
    assert [e["event_id"] for e in rstore.events_ordered][:3] == [
        "evt_00000001", "evt_00000002", "evt_00000003",
    ]
    assert {e["event_id"] for e in rstore.events_by_element[COMPUTE_ID]} == {
        "evt_00000003", "evt_00000004", "evt_00000005",
    }
    assert len(rstore.unmapped_events) == 1
    assert rstore.unmapped_events[0]["event_id"] == "evt_00000009"


def test_list_runs_finds_run_directories(tmp_path: Path) -> None:
    assert list_runs(tmp_path) == []
    build_fixture(tmp_path)
    build_runtime_fixture(tmp_path, run_id="run_a")
    build_runtime_fixture(tmp_path, run_id="run_b")
    assert list_runs(tmp_path) == ["run_a", "run_b"]


def test_runtime_store_missing_run_degrades_gracefully(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    empty = RuntimeStore.load(tmp_path, "no-such-run")
    assert all(v is False for v in empty.available.values())
    assert empty.events_ordered == []
    assert runtime_overview_view(empty) == {"available": False, "run_id": "no-such-run"}
    assert mapping_view(empty) == {"available": False, "run_id": "no-such-run"}
    assert observed_order_view(empty) == []
    assert unmapped_events_view(empty) == []
    assert contradictions_view(empty) == []
    assert nondeterminism_view(empty) == []
    assert verdicts_view(empty) == []
    assert narrative_view(empty) == []
    # rendering with this empty overlay must not crash
    store = ArtifactStore.load(tmp_path)
    html = render_site(store, empty)
    assert "<table" in html


def test_runtime_store_malformed_jsonl_line_is_reported(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    build_runtime_fixture(tmp_path)
    events_path = tmp_path / "runtime" / RUN_ID / "events.jsonl"
    events_path.write_text("not json\n" + events_path.read_text(encoding="utf-8"), encoding="utf-8")
    rstore = RuntimeStore.load(tmp_path, RUN_ID)
    assert any(e.line_number == 1 and "events.jsonl" in e.file for e in rstore.errors)
    assert len(rstore.events_ordered) == 9  # the valid lines still loaded


def test_runtime_store_malformed_run_json_is_reported(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    build_runtime_fixture(tmp_path)
    (tmp_path / "runtime" / RUN_ID / "run.json").write_text("{not valid json", encoding="utf-8")
    rstore = RuntimeStore.load(tmp_path, RUN_ID)
    assert rstore.run_record is None
    assert rstore.available["run"] is True  # file exists; content failed to parse
    assert any(e.line_number == 0 and "run.json" in e.file for e in rstore.errors)


# -- run overview: unguaranteed and blocked -----------------------------


def test_runtime_overview_surfaces_unguaranteed_and_blocked(runtime_store) -> None:
    _store, rstore = runtime_store
    overview = runtime_overview_view(rstore)
    assert overview["available"] is True
    assert len(overview["unguaranteed"]) == 2
    assert "sys.audit" in overview["unguaranteed"][0]
    assert overview["blocked"][0]["kind"] == "network"
    assert overview["refused"] is False


def test_html_render_unguaranteed_appears_before_events(runtime_store) -> None:
    """The owner needs to see what a run could not guarantee before what it
    observed -- not buried in a footer."""
    store, rstore = runtime_store
    html = render_site(store, rstore)
    unguaranteed_pos = html.index("could not close")
    events_pos = html.index("Events and captured values")
    assert unguaranteed_pos < events_pos
    blocked_pos = html.index("Blocked attempts")
    assert blocked_pos < events_pos


# -- scenario_failure: the run happened, but the scenario did not ------------
#
# Reproduces the exact shape of lie the CLI told once: a scenario pointed at
# the wrong target_root reported "524 events, 0 mapped, exit 0" with no
# visual distinction from a normal completed run. RunRecord.scenario_failure
# exists precisely so this page never repeats that.

_LONG_TRACEBACK_NOTE = "...[traceback truncated: showing 8000 of 15000 characters]"


def test_runtime_overview_normal_run_has_no_scenario_failure(runtime_store) -> None:
    _store, rstore = runtime_store
    assert runtime_overview_view(rstore)["scenario_failure"] is None


def test_runtime_overview_import_failure_explains_target_root(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    build_runtime_fixture(
        tmp_path,
        scenario_failure=ScenarioFailure(
            stage="import", exception_type="ModuleNotFoundError",
            message="No module named 'metatron_engine'", traceback="Traceback...\n",
        ),
    )
    rstore = RuntimeStore.load(tmp_path, RUN_ID)
    sf = runtime_overview_view(rstore)["scenario_failure"]
    assert sf["stage"] == "import"
    assert "target_root" in sf["explanation"]
    assert "wrong directory" in sf["explanation"] or "cannot import itself" in sf["explanation"]


def test_runtime_overview_call_attributeerror_is_a_scenario_typo(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    build_runtime_fixture(
        tmp_path,
        scenario_failure=ScenarioFailure(
            stage="call", exception_type="AttributeError",
            message="module 'pkg.mod' has no attribute 'run_m5'", traceback="Traceback...\n",
        ),
    )
    rstore = RuntimeStore.load(tmp_path, RUN_ID)
    sf = runtime_overview_view(rstore)["scenario_failure"]
    assert "typo" in sf["explanation"]
    assert "not a fact about the target" in sf["explanation"]


def test_runtime_overview_call_other_exception_is_the_targets_own(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    build_runtime_fixture(
        tmp_path,
        scenario_failure=ScenarioFailure(
            stage="call", exception_type="ZeroDivisionError",
            message="division by zero", traceback="Traceback...\n" + _LONG_TRACEBACK_NOTE,
        ),
    )
    rstore = RuntimeStore.load(tmp_path, RUN_ID)
    sf = runtime_overview_view(rstore)["scenario_failure"]
    assert "target's own exception" in sf["explanation"] or "target engine itself raised" in sf["explanation"]
    assert sf["traceback"].endswith(_LONG_TRACEBACK_NOTE)


def test_html_render_scenario_failure_appears_before_unguaranteed_and_events(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    build_runtime_fixture(
        tmp_path,
        scenario_failure=ScenarioFailure(
            stage="import", exception_type="ModuleNotFoundError",
            message="No module named 'metatron_engine'",
            traceback="Traceback (most recent call last):\n  ...\n" + _LONG_TRACEBACK_NOTE,
        ),
    )
    store = ArtifactStore.load(tmp_path)
    rstore = RuntimeStore.load(tmp_path, RUN_ID)
    html = render_site(store, rstore)
    failure_pos = html.index("scenario did not complete")
    unguaranteed_pos = html.index("could not close")
    blocked_pos = html.index("Blocked attempts")
    events_pos = html.index("Events and captured values")
    assert failure_pos < unguaranteed_pos < blocked_pos < events_pos
    # the traceback's own truncation note is rendered verbatim, not hidden
    assert _LONG_TRACEBACK_NOTE in html
    # the event counts are not suppressed -- still fully present below
    assert "Mapping rate" in html
    assert "not suppressed" in html


def test_html_render_normal_run_has_no_scenario_failure_block(runtime_store) -> None:
    """A run whose scenario completed must never render the failure block --
    that would be its own kind of misleading."""
    store, rstore = runtime_store
    html = render_site(store, rstore)
    assert 'class="scenario-failure"' not in html
    assert "did not complete" not in html


# -- observer_failure: the tool may have missed the run, not the target's fault --
#
# Verification ran a deliberately broken observer through a real
# Harness.start() and got refused=False, scenario_failure=None, blocked=(),
# every control True -- indistinguishable from a clean run. observer_failure
# exists so a bug in card 12 never gets attributed to the target.


def test_runtime_overview_normal_run_has_no_observer_failure(runtime_store) -> None:
    _store, rstore = runtime_store
    assert runtime_overview_view(rstore)["observer_failure"] is None


def test_runtime_overview_observer_start_failure_means_nothing_was_watched(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    build_runtime_fixture(
        tmp_path,
        observer_failure=ScenarioFailure(
            stage="start", exception_type="RuntimeError",
            message="collector could not attach", traceback="Traceback...\n",
        ),
    )
    rstore = RuntimeStore.load(tmp_path, RUN_ID)
    of = runtime_overview_view(rstore)["observer_failure"]
    assert of["stage"] == "start"
    assert "not watched" in of["explanation"] or "nothing was watched" in of["explanation"]
    assert "bug in the tool" in of["explanation"]


def test_runtime_overview_observer_stop_failure_means_tail_may_be_missing(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    build_runtime_fixture(
        tmp_path,
        observer_failure=ScenarioFailure(
            stage="stop", exception_type="RuntimeError",
            message="collector flush failed", traceback="Traceback...\n",
        ),
    )
    rstore = RuntimeStore.load(tmp_path, RUN_ID)
    of = runtime_overview_view(rstore)["observer_failure"]
    assert of["stage"] == "stop"
    assert "tail may be missing" in of["explanation"]
    assert "bug in the tool" in of["explanation"]


def test_observer_failure_and_scenario_failure_are_independently_rendered(tmp_path: Path) -> None:
    """A target crash and a broken observer in the same run is worse than
    either alone, and must not collapse into a single message."""
    build_fixture(tmp_path)
    build_runtime_fixture(
        tmp_path,
        scenario_failure=ScenarioFailure(
            stage="call", exception_type="ZeroDivisionError", message="division by zero",
        ),
        observer_failure=ScenarioFailure(
            stage="stop", exception_type="RuntimeError", message="collector flush failed",
        ),
    )
    store = ArtifactStore.load(tmp_path)
    rstore = RuntimeStore.load(tmp_path, RUN_ID)
    html = render_site(store, rstore)
    assert "class='observer-failure'" in html
    assert "class='scenario-failure'" in html
    assert "division by zero" in html
    assert "collector flush failed" in html
    # distinct wording -- neither block borrows the other's framing
    assert "tool bug" in html
    assert "target's own exception" in html or "target engine itself raised" in html


def test_html_render_observer_failure_is_distinct_from_scenario_failure(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    build_runtime_fixture(
        tmp_path,
        observer_failure=ScenarioFailure(
            stage="start", exception_type="RuntimeError", message="collector could not attach",
        ),
    )
    store = ArtifactStore.load(tmp_path)
    rstore = RuntimeStore.load(tmp_path, RUN_ID)
    html = render_site(store, rstore)
    assert "class='observer-failure'" in html
    assert 'class="scenario-failure"' not in html
    assert "not a finding about the target" in html


def test_html_render_observer_failure_appears_before_unguaranteed_and_events(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    build_runtime_fixture(
        tmp_path,
        observer_failure=ScenarioFailure(
            stage="start", exception_type="RuntimeError", message="collector could not attach",
        ),
    )
    store = ArtifactStore.load(tmp_path)
    rstore = RuntimeStore.load(tmp_path, RUN_ID)
    html = render_site(store, rstore)
    observer_pos = html.index("The observer itself failed")
    unguaranteed_pos = html.index("could not close")
    events_pos = html.index("Events and captured values")
    assert observer_pos < unguaranteed_pos < events_pos


def test_html_render_normal_run_has_no_observer_failure_block(runtime_store) -> None:
    store, rstore = runtime_store
    html = render_site(store, rstore)
    assert "class='observer-failure'" not in html
    assert "observer itself failed" not in html


# -- mapping rate ---------------------------------------------------------


def test_mapping_view_computes_rate_from_reported_counts(runtime_store) -> None:
    _store, rstore = runtime_store
    m = mapping_view(rstore)
    assert m["total_events"] == 9
    assert m["mapped_events"] == 8
    assert abs(m["mapping_rate"] - (8 / 9)) < 1e-9
    assert m["unmapped_by_reason"] == {"dynamic_dispatch": 1}


def test_mapping_view_zero_events_has_no_rate(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    rundir = tmp_path / "runtime" / RUN_ID
    rundir.mkdir(parents=True)
    mapping = MappingReport(run_id=RUN_ID, total_events=0, mapped_events=0, unmapped_events=0, unmapped_by_reason={})
    (rundir / "mapping.json").write_text(canonical_dumps(mapping), encoding="utf-8")
    rstore = RuntimeStore.load(tmp_path, RUN_ID)
    assert mapping_view(rstore)["mapping_rate"] is None


# -- captured values and CaptureStatus ------------------------------------


def test_event_detail_view_exposes_capture_status_and_original_size(runtime_store) -> None:
    _store, rstore = runtime_store
    detail = event_detail_view(rstore, "evt_00000002")
    assert detail["values"]["result"]["status"] == "SUMMARIZED"
    assert detail["values"]["result"]["original_size"] == 500

    dropped = event_detail_view(rstore, "evt_00000004")
    assert dropped["values"]["raw_frame"]["status"] == "DROPPED"
    assert dropped["values"]["raw_frame"]["original_size"] == 2_000_000
    assert dropped["values"]["score"]["status"] == "REDACTED"

    full = event_detail_view(rstore, "evt_00000001")
    assert full["values"]["raw"]["status"] == "FULL"


def test_event_detail_view_unknown_id_is_none(runtime_store) -> None:
    _store, rstore = runtime_store
    assert event_detail_view(rstore, "evt_no_such_event") is None


def test_html_render_non_full_capture_never_reads_as_complete(runtime_store) -> None:
    store, rstore = runtime_store
    html = render_site(store, rstore)
    # every non-FULL status badge carries the disclosure text
    assert html.count("not the complete value") >= 3  # SUMMARIZED, REDACTED, DROPPED
    assert "original size 500" in html
    assert "original size 2000000" in html


# -- unmapped events --------------------------------------------------------


def test_unmapped_events_view_carries_location_and_reason(runtime_store) -> None:
    _store, rstore = runtime_store
    unmapped = unmapped_events_view(rstore)
    assert len(unmapped) == 1
    assert unmapped[0]["event_id"] == "evt_00000009"
    assert unmapped[0]["location"] == {"path": "pkg/mod.py", "line": 42, "end_line": None, "col": None}
    assert "no static candidate" in unmapped[0]["reason"]


# -- decision branches: observed vs. declared -------------------------------


def test_decision_branch_view_separates_observed_from_not_observed(runtime_store) -> None:
    store, rstore = runtime_store
    dv = decision_branch_view(store, rstore, "decision:decide")
    assert dv["declared_outcomes"] == [("high", "order:merge"), ("low", "order:merge")]
    assert [o["branch_taken"] for o in dv["observed"]] == ["high"]
    assert dv["not_observed_labels"] == ["low"]


def test_decision_branch_view_unknown_decision_is_none(runtime_store) -> None:
    store, rstore = runtime_store
    assert decision_branch_view(store, rstore, "decision:does-not-exist") is None


# -- contradictions: never merged, both readings shown ----------------------


def test_contradictions_view_keeps_both_readings_distinct(runtime_store) -> None:
    _store, rstore = runtime_store
    contradictions = contradictions_view(rstore)
    assert len(contradictions) == 1
    c = contradictions[0]
    assert "compute_score calls decide directly" in c["claim"]
    assert "different call site" in c["observation"]
    assert c["claim"] != c["observation"]
    assert c["static_evidence_ids"] == ["edge:compute->decide"]
    assert c["run_id"] == RUN_ID


def test_html_render_contradiction_shows_both_readings_in_one_row(runtime_store) -> None:
    store, rstore = runtime_store
    html = render_site(store, rstore)
    assert "contradiction-row" in html
    assert "compute_score calls decide directly" in html
    assert "different call site" in html


# -- nondeterminism -----------------------------------------------------


def test_nondeterminism_view_reads_observed_property(runtime_store) -> None:
    _store, rstore = runtime_store
    nd = nondeterminism_view(rstore)
    assert len(nd) == 1
    assert nd[0]["kind"] == "clock"
    assert nd[0]["element_id"] == COMPUTE_ID


# -- alignment verdicts: NOT_EXERCISED distinct from ALIGNED -----------------


def test_verdicts_view_reports_not_exercised_distinctly(runtime_store) -> None:
    _store, rstore = runtime_store
    verdicts = verdicts_view(rstore)
    by_id = {v["id"]: v for v in verdicts}
    assert by_id["verdict:1"]["verdict"] == "ALIGNED"
    assert by_id["verdict:2"]["verdict"] == "NOT_EXERCISED"
    assert by_id["verdict:1"]["verdict"] != by_id["verdict:2"]["verdict"]


def test_html_render_not_exercised_is_visually_distinct_from_aligned(runtime_store) -> None:
    store, rstore = runtime_store
    html = render_site(store, rstore)
    assert "verdict-ALIGNED" in html
    assert "verdict-NOT_EXERCISED" in html
    assert "verdict-ALIGNED" != "verdict-NOT_EXERCISED"


# -- narrative: anchored to elements and events ------------------------------


def test_narrative_view_is_anchored_and_separates_model_prose(runtime_store) -> None:
    _store, rstore = runtime_store
    steps = narrative_view(rstore)
    assert len(steps) == 3
    assert steps[0]["element_ids"] == [INGEST_ID]
    assert steps[0]["event_ids"] == ["evt_00000001", "evt_00000002"]
    assert steps[1]["model_prose"]
    assert steps[1]["model_id"] == "claude-haiku-4-5-20251001"
    assert steps[0]["model_prose"] == ""  # no model prose for this step: shown as absent, not empty-but-present


def test_html_render_narrative_labels_model_prose(runtime_store) -> None:
    store, rstore = runtime_store
    html = render_site(store, rstore)
    assert "MODEL-WRITTEN" in html
    assert "confidence number" in html  # the model prose text itself


# -- observed order beside static order, both tagged with run id -----------


def test_observed_order_view_is_sorted_by_sequence(runtime_store) -> None:
    _store, rstore = runtime_store
    order = observed_order_view(rstore)
    assert [e["sequence"] for e in order] == list(range(1, 10))
    assert all(e["run_id"] == RUN_ID for e in order)


def test_html_render_runtime_evidence_is_visually_distinct_and_tagged(runtime_store) -> None:
    store, rstore = runtime_store
    html = render_site(store, rstore)
    assert "runtime-evidence" in html
    assert "runtime-section" in html
    assert f"run: {RUN_ID}" in html
    # the phase B page (no rstore) never mentions a run id, and never
    # renders the runtime section itself (the CSS rule for it stays defined
    # in the shared stylesheet either way, so check the section marker, not
    # the class name).
    baseline = render_site(store)
    assert RUN_ID not in baseline
    assert 'id="runtime"' not in baseline
    assert 'class="runtime-section"' not in baseline


# -- determinism and offline discipline, extended to the overlay ------------


def test_html_render_with_runtime_is_deterministic(runtime_store) -> None:
    store, rstore = runtime_store
    first = render_site(store, rstore)
    second = render_site(store, rstore)
    assert first == second


def test_html_render_with_runtime_has_no_network_reference(runtime_store) -> None:
    store, rstore = runtime_store
    html = render_site(store, rstore)
    for forbidden in ("http://", "https://", "cdn.", "<script src=", "fetch(", "XMLHttpRequest"):
        assert forbidden not in html


_EVT_HREF_RE = re.compile(r'href="#evt-([^"]*)"')
_EVT_ANCHOR_RE = re.compile(r'id="evt-([^"]*)"')


def test_html_render_event_links_resolve_to_anchors(runtime_store) -> None:
    store, rstore = runtime_store
    html = render_site(store, rstore)
    hrefs = set(_EVT_HREF_RE.findall(html))
    anchors = set(_EVT_ANCHOR_RE.findall(html))
    assert anchors, "no event anchors rendered"
    dangling = hrefs - anchors
    assert not dangling, f"event links with no matching anchor: {sorted(dangling)}"
    assert anchors == set(rstore.events_by_id)

    # element links inside the runtime overlay still resolve to the same
    # #el-<id> anchors the static page already renders -- no second linking
    # scheme for the same kind of ID.
    el_hrefs = set(_HREF_RE.findall(html))
    el_anchors = set(_ANCHOR_RE.findall(html))
    assert not (el_hrefs - el_anchors)


def test_render_to_file_with_runtime_writes_overlay(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    build_runtime_fixture(tmp_path)
    out_path = tmp_path / "view.html"
    store, rstore = render_to_file_with_runtime(tmp_path, out_path, RUN_ID)
    written = out_path.read_text(encoding="utf-8")
    assert written == render_site(store, rstore)
    assert rstore is not None
    assert f"run: {RUN_ID}" in written


def test_render_to_file_without_run_id_is_unchanged_from_phase_b(tmp_path: Path) -> None:
    """render_to_file's phase B return shape (a bare ArtifactStore) and
    output must be unaffected by phase A existing."""
    build_fixture(tmp_path)
    build_runtime_fixture(tmp_path)  # present on disk, but not requested
    out_path = tmp_path / "view.html"
    returned_store = render_to_file(tmp_path, out_path)
    assert isinstance(returned_store, ArtifactStore)
    written = out_path.read_text(encoding="utf-8")
    assert written == render_site(returned_store)
    assert RUN_ID not in written


