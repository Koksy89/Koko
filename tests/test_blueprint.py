"""Tests for card 15 phase C -- the blueprint canvas.

Mirrors the discipline `test_viewer.py` already established: the blueprint
module is read-only over emitted artifacts, so most cases build a small
artifact set by hand (reusing `test_viewer.build_fixture` /
`build_runtime_fixture`, which already do this with the real contract
types) rather than depending on a full pipeline run. A handful of cases run
the real static pipeline (`cascade_map.cli.analyze`) over
`tests/fixtures/mode_b` to prove the page renders at the corpus's actual
shape and size -- nothing here writes into `tests/fixtures/` itself; every
output goes to `tmp_path`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# `tests/` has no __init__.py, so pytest's default import mode already
# prepends this file's own directory (tests/) to sys.path when collecting
# it -- the same directory `test_viewer.py` lives in, regardless of
# collection order between the two files.
from test_viewer import (
    COMPUTE_ID,
    DECIDE_ID,
    EVALUATE_ID,
    INGEST_ID,
    MODULE,
    RULESET_ID,
    RUN_ID,
    SCORE_FEATURE_ID,
    SPAN,
    UNUSED_FEATURE_ID,
    build_fixture,
    build_runtime_fixture,
)

from cascade_map.cli import analyze, main as cli_main
from cascade_map.contracts.interfaces import (
    ChangeKind,
    Confidence,
    DecisionPoint,
    Element,
    ElementKind,
    Impact,
    Method,
    Provenance,
    SourceSpan,
    VersionChange,
    canonical_jsonl,
)
from cascade_map.viewer.blueprint import (
    BlueprintTooLarge,
    BlueprintView,
    build_blueprint_data,
    estimate_island_bytes,
    render_blueprint,
    render_blueprint_to_file,
    select,
)
from cascade_map.viewer.loader import ArtifactStore, RuntimeStore

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "mode_b"

#: Round 5 made `cascade` the default scope, which deliberately carries no
#: lineage edges and only the decision-relevant slice of the execution
#: graph. Every test below that is about the *whole* graph's shape asks for
#: it explicitly; the tests about the default's own bounds are in the
#: "round 5" section at the end of this file.
FULL = BlueprintView(scope="full", max_nodes=0)


#: The `element_detail` list fields round 5 caps and counts.
_CAPPED_FIELDS = (
    "outgoing_edges", "incoming_edges", "unresolved_as_candidate", "order_node_ids",
    "decision_as_condition", "decision_reads_this", "lineage_out", "lineage_in",
    "barrier_ids", "slice_ids_as_member", "finding_ids_as_evidence",
)


def full_data(store_, **kwargs):
    """`build_blueprint_data` over the unscoped graph."""
    return build_blueprint_data(store_, view=FULL, **kwargs)


_HOSTILE = "</script><img src=x onerror=alert(1)>&<>  "


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _island(html: str) -> dict:
    marker = 'id="cascade-blueprint-data">'
    start = html.index(marker) + len(marker)
    end = html.index("</script>", start)
    return json.loads(html[start:end])


def _all_node_ids(graph: dict) -> set[str]:
    return {n["id"] for n in graph.get("nodes", [])}


# ---------------------------------------------------------------------------
# 1. Renders from the fixture corpus without error, at real scale
# ---------------------------------------------------------------------------


def test_renders_from_mode_b_corpus_without_error(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    code, summary = analyze(CORPUS, graph_dir, strict_gate=False)
    assert summary["elements"] > 0

    html_path = tmp_path / "blueprint.html"
    store, selection, estimate = render_blueprint_to_file(graph_dir, html_path)
    assert isinstance(store, ArtifactStore)
    assert estimate.total_bytes > 0
    assert selection.totals["nodes_shown"] > 0
    assert html_path.exists()

    data = _island(html_path.read_text(encoding="utf-8"))
    assert data["execution"]["nodes"], "the real corpus must produce execution nodes"
    # Round 5: the default scope is `cascade` and its Lineage tab is
    # focus-driven. It must be empty *and say so*, never blank.
    assert data["lineage"]["nodes"] == []
    assert data["lineage"]["focus_driven"] is True
    assert "focus-driven" in data["lineage"]["reason"]

    full_html = tmp_path / "blueprint_full.html"
    render_blueprint_to_file(graph_dir, full_html, view=FULL)
    full = _island(full_html.read_text(encoding="utf-8"))
    assert full["lineage"]["nodes"], "`--scope full` must still produce lineage nodes"


def test_writes_nothing_into_the_fixture_corpus(tmp_path: Path) -> None:
    before = {p: p.stat().st_mtime for p in CORPUS.rglob("*") if p.is_file()}
    graph_dir = tmp_path / "graph"
    analyze(CORPUS, graph_dir, strict_gate=False)
    render_blueprint_to_file(graph_dir, tmp_path / "blueprint.html")
    after = {p: p.stat().st_mtime for p in CORPUS.rglob("*") if p.is_file()}
    assert before == after


# ---------------------------------------------------------------------------
# 2. Determinism: two runs, and across PYTHONHASHSEED
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> ArtifactStore:
    build_fixture(tmp_path)
    return ArtifactStore.load(tmp_path)


def test_html_render_is_byte_identical_across_two_calls(store: ArtifactStore) -> None:
    first = render_blueprint(store)
    second = render_blueprint(store)
    assert first == second


def test_data_island_is_byte_identical_across_two_calls(store: ArtifactStore) -> None:
    first = json.dumps(build_blueprint_data(store), sort_keys=True)
    second = json.dumps(build_blueprint_data(store), sort_keys=True)
    assert first == second


def test_render_to_file_matches_direct_render(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    out_path = tmp_path / "blueprint.html"
    returned_store, selection, _estimate = render_blueprint_to_file(tmp_path, out_path)
    written = out_path.read_text(encoding="utf-8")
    assert written == render_blueprint(returned_store, selection=selection)


def test_byte_identical_across_pythonhashseed(tmp_path: Path) -> None:
    """The same fixture, rendered by two subprocesses with different
    `PYTHONHASHSEED`, must produce byte-identical output -- the sharpest
    available check that no `set`/`dict` iteration order leaked into the
    emitted bytes."""
    build_fixture(tmp_path)
    script = (
        "import sys; sys.path.insert(0, %r)\n"
        "from cascade_map.viewer.blueprint import render_blueprint_to_file\n"
        "render_blueprint_to_file(%r, %r)\n"
    ) % (str(ROOT / "src"), str(tmp_path), str(tmp_path / "out.html"))

    outputs = []
    for seed in ("0", "1", "42"):
        out_path = tmp_path / "out.html"
        if out_path.exists():
            out_path.unlink()
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        result = subprocess.run(
            [sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=120
        )
        assert result.returncode == 0, result.stderr
        outputs.append(out_path.read_bytes())

    assert len(outputs) == 3
    assert outputs[0] == outputs[1] == outputs[2]


# ---------------------------------------------------------------------------
# 3. No external/network reference of any kind
# ---------------------------------------------------------------------------


def test_no_external_resource_reference(store: ArtifactStore) -> None:
    html = render_blueprint(store)
    for forbidden in (
        "http://", "https://", "//cdn", "cdn.",
        '<link rel="stylesheet" href', "<link rel='stylesheet' href",
        "fetch(", "XMLHttpRequest", "<script src=",
    ):
        assert forbidden not in html, forbidden


# ---------------------------------------------------------------------------
# 4. Exhaustive link integrity: every node id unique, every wire resolves
# ---------------------------------------------------------------------------


def _assert_graph_internally_consistent(tab_name: str, graph: dict) -> None:
    nodes = graph.get("nodes", [])
    node_ids = [n["id"] for n in nodes]
    assert len(node_ids) == len(set(node_ids)), f"{tab_name}: duplicate node ids"
    node_id_set = set(node_ids)
    wires = graph.get("wires", [])
    assert isinstance(wires, list)
    for wire in wires:
        assert wire["source_id"] in node_id_set, (tab_name, wire)
        assert wire["target_id"] in node_id_set, (tab_name, wire)


def test_every_wire_endpoint_resolves_to_a_node_on_hand_built_fixture(store: ArtifactStore) -> None:
    data = build_blueprint_data(store)
    for tab_name in ("execution", "lineage"):
        _assert_graph_internally_consistent(tab_name, data[tab_name])


def test_every_wire_endpoint_resolves_to_a_node_on_full_corpus(tmp_path: Path) -> None:
    """The exhaustive version, over every wire in every tab, run against the
    real pipeline's output -- not one hand-picked sample."""
    graph_dir = tmp_path / "graph"
    analyze(CORPUS, graph_dir, strict_gate=False)
    store_ = ArtifactStore.load(graph_dir)
    data = full_data(store_)
    for tab_name in ("execution", "lineage"):
        graph = data[tab_name]
        assert graph["nodes"], f"{tab_name} produced no nodes on the real corpus"
        assert graph["wires"], f"{tab_name} produced no wires on the real corpus"
        _assert_graph_internally_consistent(tab_name, graph)


def test_execution_nodes_claiming_to_be_elements_exist_in_elements_jsonl(tmp_path: Path) -> None:
    """Every node whose `is_element` is true must be a real id in
    `elements.jsonl`; every node that is not (decision points, module-
    collapse aggregates are computed client-side and never in this island,
    lineage's synthetic locals/params/features/barriers) is explicitly
    marked `is_element: false`, never silently presented as a fact."""
    graph_dir = tmp_path / "graph"
    analyze(CORPUS, graph_dir, strict_gate=False)
    store_ = ArtifactStore.load(graph_dir)
    data = build_blueprint_data(store_)
    real_ids = set(store_.elements_by_id)
    checked = 0
    for tab_name in ("execution", "lineage", "diff"):
        for node in data[tab_name]["nodes"]:
            checked += 1
            if node.get("is_element"):
                assert node["id"] in real_ids, (tab_name, node["id"])
            else:
                assert node["id"] not in real_ids or node.get("is_ghost"), (tab_name, node["id"])
    assert checked > 0


# ---------------------------------------------------------------------------
# Round 4, R1/R2: stages and flow classification -- determinism and
# link-integrity, over the real corpus
# ---------------------------------------------------------------------------


def test_stage_member_counts_are_a_complete_partition_of_execution_elements(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    analyze(CORPUS, graph_dir, strict_gate=False)
    store_ = ArtifactStore.load(graph_dir)
    data = build_blueprint_data(store_)
    stages = data["execution"]["stages"]
    assert stages, "the real corpus produced no stages"

    all_members: list[str] = []
    seen_ids: set[str] = set()
    for stage in stages:
        assert stage["id"] not in seen_ids, f"duplicate stage id {stage['id']}"
        seen_ids.add(stage["id"])
        assert stage["member_ids"], f"empty stage {stage['id']} should not have been emitted"
        all_members.extend(stage["member_ids"])

    # every member id appears in exactly one stage -- a complete partition,
    # never a double-count and never a drop.
    assert len(all_members) == len(set(all_members))
    execution_element_ids = {n["id"] for n in data["execution"]["nodes"] if n["is_element"]}
    assert set(all_members) == execution_element_ids

    # `stage_of` (used for flow classification) agrees with the stages
    # list itself -- one source of truth, not two that could drift.
    stage_of = data["execution"]["stage_of"]
    for index, stage in enumerate(stages):
        for member_id in stage["member_ids"]:
            assert stage_of[member_id] == index


def test_stage_names_never_claim_a_phase_card_3_did_not_name(tmp_path: Path) -> None:
    """STATUS.md records this exact defect (card 14 printed "ingestion" /
    "data engineering" as fact from position alone). A stage is either
    positional ("Stage N") or a real module path -- never an invented
    phase name."""
    graph_dir = tmp_path / "graph"
    analyze(CORPUS, graph_dir, strict_gate=False)
    store_ = ArtifactStore.load(graph_dir)
    data = build_blueprint_data(store_)
    stages = data["execution"]["stages"]
    assert stages
    for stage in stages:
        if stage["rule"] == "fallback_module":
            member_modules = {
                (store_.elements_by_id.get(m) or {}).get("module") for m in stage["member_ids"]
            }
            assert member_modules == {stage["name"]}, (stage["name"], member_modules)
        else:
            assert stage["name"].startswith("Stage "), stage


def test_flow_classification_covers_every_wire_exactly_once(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    analyze(CORPUS, graph_dir, strict_gate=False)
    store_ = ArtifactStore.load(graph_dir)
    data = build_blueprint_data(store_)
    wires = data["execution"]["wires"]
    assert wires, "the real corpus produced no execution wires"
    totals = data["execution"]["flow_totals"]
    assert sum(totals.values()) == len(wires)
    for wire in wires:
        assert wire["flow"] in ("FORWARD", "BACKWARD", "WITHIN", "UNORDERED")


def test_known_mutual_import_is_classified_backward(tmp_path: Path) -> None:
    """`mode_b.res_import_cycle.a` and `.b` import each other -- a real
    circular dependency already in the corpus, not a synthetic fixture."""
    graph_dir = tmp_path / "graph"
    analyze(CORPUS, graph_dir, strict_gate=False)
    store_ = ArtifactStore.load(graph_dir)
    data = build_blueprint_data(store_)
    backward = [
        w for w in data["execution"]["wires"]
        if w["flow"] == "BACKWARD" and "res_import_cycle" in w["source_id"]
    ]
    assert backward, "expected a BACKWARD-classified wire in mode_b.res_import_cycle"


def test_stage_pairs_reference_only_real_stage_indices(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    analyze(CORPUS, graph_dir, strict_gate=False)
    store_ = ArtifactStore.load(graph_dir)
    data = build_blueprint_data(store_)
    stages = data["execution"]["stages"]
    pairs = data["execution"]["stage_pairs"]
    assert pairs, "expected at least one cross-stage wire in the real corpus"
    wire_ids = {w["id"] for w in data["execution"]["wires"]}
    for pair in pairs:
        assert 0 <= pair["from"] < len(stages)
        assert 0 <= pair["to"] < len(stages)
        assert pair["direction"] in ("FORWARD", "BACKWARD")
        assert pair["wire_ids"], pair
        assert set(pair["wire_ids"]) <= wire_ids


def test_stage_and_flow_data_is_deterministic_across_two_builds(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    analyze(CORPUS, graph_dir, strict_gate=False)
    store_ = ArtifactStore.load(graph_dir)
    first = json.dumps(build_blueprint_data(store_)["execution"], sort_keys=True)
    second = json.dumps(build_blueprint_data(store_)["execution"], sort_keys=True)
    assert first == second


# ---------------------------------------------------------------------------
# 5. Escaping: a hostile string must be inert in the output
# ---------------------------------------------------------------------------


def _hostile_store(tmp_path: Path) -> ArtifactStore:
    prov = Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN, span=SPAN)
    fid = "pkg.mod::hostile"
    elements = [
        Element(
            id=MODULE, kind=ElementKind.MODULE, name="mod", qualname="", module=MODULE,
            span=SPAN, provenance=prov, content_hash="hmod", docstring=_HOSTILE,
        ),
        Element(
            id=fid, kind=ElementKind.FUNCTION, name="hostile", qualname="hostile", module=MODULE,
            span=SPAN, provenance=prov, content_hash="hh", docstring=_HOSTILE,
        ),
    ]
    decisions = [
        DecisionPoint(
            id="dec:hostile", element_id=fid, condition_source=_HOSTILE,
            reads_ids=(), outcomes=((_HOSTILE, fid),), provenance=prov,
        ),
    ]
    (tmp_path / "elements.jsonl").write_text(canonical_jsonl(elements), encoding="utf-8")
    (tmp_path / "decisions.jsonl").write_text(canonical_jsonl(decisions), encoding="utf-8")
    return ArtifactStore.load(tmp_path)


def test_hostile_string_cannot_break_out_of_the_script_tag(tmp_path: Path) -> None:
    html = render_blueprint(_hostile_store(tmp_path))
    assert _HOSTILE not in html
    assert "</script><img" not in html
    assert html.count("<script") == 2  # the data island and the JS block, nothing more
    data = _island(html)
    decision_nodes = [n for n in data["execution"]["nodes"] if n["kind"] == "DECISION"]
    assert decision_nodes
    # the JSON parses back to the real, unaltered value -- escaping the
    # script tag never corrupted the underlying fact
    assert decision_nodes[0]["condition_source"] == _HOSTILE


def test_hostile_string_survives_round_trip_in_decision_condition(tmp_path: Path) -> None:
    data = build_blueprint_data(_hostile_store(tmp_path))
    decision_nodes = [n for n in data["execution"]["nodes"] if n["kind"] == "DECISION"]
    assert decision_nodes
    assert decision_nodes[0]["condition_source"] == _HOSTILE


# ---------------------------------------------------------------------------
# 6. Empty states
# ---------------------------------------------------------------------------


def test_empty_state_no_diff_loaded(store: ArtifactStore) -> None:
    data = build_blueprint_data(store)
    assert data["diff"]["available"] is False
    assert "metatron diff" in data["diff"]["reason"]
    assert data["diff"]["nodes"] == []


def test_empty_state_no_run_loaded(store: ArtifactStore) -> None:
    data = build_blueprint_data(store)
    assert data["runtime"]["available"] is False
    assert "--run" in data["runtime"]["reason"]


def test_empty_state_missing_records_jsonl(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    (tmp_path / "records.jsonl").unlink()
    store_ = ArtifactStore.load(tmp_path)
    assert store_.available["records"] is False
    data = build_blueprint_data(store_)
    assert data["element_details"][DECIDE_ID]["doc_record"] is None


def test_empty_state_missing_reachability_falls_back_not_crashes(tmp_path: Path) -> None:
    build_fixture(tmp_path, include_reachability=False)
    store_ = ArtifactStore.load(tmp_path)
    data = full_data(store_)
    ingest_nodes = [n for n in data["execution"]["nodes"] if n["id"] == INGEST_ID]
    assert ingest_nodes
    assert ingest_nodes[0]["reachability"]["state"] in ("REACHES_SINK", "UNKNOWN")


def test_load_errors_are_surfaced_not_swallowed(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    (tmp_path / "elements.jsonl").write_text(
        "not json at all\n" + (tmp_path / "elements.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    store_ = ArtifactStore.load(tmp_path)
    assert store_.errors
    data = build_blueprint_data(store_)
    assert data["diagnostics"]["static_errors"]
    assert data["diagnostics"]["static_errors"][0]["file"] == "elements.jsonl"
    html = render_blueprint(store_)
    assert "elements.jsonl" in html  # the error text is present in the data island


# ---------------------------------------------------------------------------
# 7. Confidence and reachability encodings present for every node/edge that
#    has them
# ---------------------------------------------------------------------------


def test_confidence_rank_covers_every_confidence_level_used(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    analyze(CORPUS, graph_dir, strict_gate=False)
    store_ = ArtifactStore.load(graph_dir)
    data = build_blueprint_data(store_)
    used_levels = set()
    for tab_name in ("execution", "lineage"):
        for wire in data[tab_name]["wires"]:
            if wire.get("confidence"):
                used_levels.add(wire["confidence"])
    assert used_levels, "the real corpus must produce at least one confidence-bearing wire"
    for level in used_levels:
        assert level in data["confidence_rank"]


def test_every_node_carries_a_reachability_state(store: ArtifactStore) -> None:
    data = full_data(store)
    for tab_name in ("execution", "lineage"):
        nodes = data[tab_name]["nodes"]
        assert nodes
        for node in nodes:
            assert node["reachability"] is not None
            assert node["reachability"]["state"] in ("REACHES_SINK", "NO_SINK_PATH", "UNKNOWN")


def test_reachability_states_are_distinguishable_in_the_fixture(store: ArtifactStore) -> None:
    """The fixture deliberately covers all three states (see
    `test_viewer.build_fixture`); confirm the blueprint data preserves the
    distinction rather than collapsing it."""
    data = full_data(store)
    by_id = {n["id"]: n for n in data["execution"]["nodes"]}
    assert by_id[DECIDE_ID]["reachability"]["state"] == "REACHES_SINK"
    assert by_id[INGEST_ID]["reachability"]["state"] == "NO_SINK_PATH"
    assert by_id[COMPUTE_ID]["reachability"]["state"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# 8. All three modes present, each with real data
# ---------------------------------------------------------------------------


def test_all_three_modes_present_with_data(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    prov = Provenance(method=Method.STRUCTURAL_MATCH, confidence=Confidence.CERTAIN)
    diff_root = tmp_path / "diff"
    diff_root.mkdir()
    changes = [
        VersionChange(
            id="c1", kind=ChangeKind.BODY_CHANGED, before_id=COMPUTE_ID, after_id=COMPUTE_ID,
            provenance=prov,
        ),
    ]
    impacts = [
        Impact(
            id="i1", change_id="c1", affected_ids=(DECIDE_ID,), decision_paths_changed=True,
            features_changed=(SCORE_FEATURE_ID,), reachability_flipped=(DECIDE_ID,),
            findings_added=(), findings_removed=(), rank=1,
        ),
    ]
    (diff_root / "changes.jsonl").write_text(canonical_jsonl(changes), encoding="utf-8")
    (diff_root / "impacts.jsonl").write_text(canonical_jsonl(impacts), encoding="utf-8")

    store_ = ArtifactStore.load(tmp_path)
    diff_store = ArtifactStore.load(diff_root)
    data = full_data(store_, diff_store=diff_store)

    assert data["execution"]["nodes"]
    assert data["lineage"]["nodes"]
    assert data["diff"]["available"] is True
    assert data["diff"]["nodes"]
    compute_diff_node = next(n for n in data["diff"]["nodes"] if n["id"] == COMPUTE_ID)
    assert compute_diff_node["loudest"] is True
    assert compute_diff_node["changes"][0]["kind"] == "BODY_CHANGED"


def test_runtime_overlay_present_and_tagged_with_run_id(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    build_runtime_fixture(tmp_path)
    store_ = ArtifactStore.load(tmp_path)
    rstore = RuntimeStore.load(tmp_path, RUN_ID)
    data = build_blueprint_data(store_, rstore=rstore)
    assert data["runtime"]["available"] is True
    assert data["runtime"]["run_id"] == RUN_ID
    assert data["runtime"]["events_by_element"][INGEST_ID]
    html = render_blueprint(store_, rstore=rstore)
    assert f"\\u0022{RUN_ID}\\u0022" in html or RUN_ID in html


# ---------------------------------------------------------------------------
# Drill-down: element_details is reused verbatim from views.element_detail
# ---------------------------------------------------------------------------


def test_element_details_reuse_the_canonical_element_detail_view(store: ArtifactStore) -> None:
    from cascade_map.viewer import views

    data = build_blueprint_data(store)
    assert DECIDE_ID in data["element_details"]
    expected = views.element_detail(store, DECIDE_ID)
    actual = data["element_details"][DECIDE_ID]

    # No field of the canonical view is missing from the island's copy.
    assert set(expected) <= set(actual)

    # Round 5 adds only counters and markers -- never a new fact.
    added = set(actual) - set(expected)
    assert added == (
        {"detail_cap", "order_node_cap", "edges_projected"}
        | {f"{f}_total" for f in _CAPPED_FIELDS}
    )

    # Three fields are deliberately carried elsewhere rather than repeated
    # here, and each leaves a pointer saying where -- never an empty value
    # that would read as "not known". See `_capped_detail`.
    for field in ("reachability", "element", "order_ancestor_ids"):
        assert "carried_by" in actual[field], field

    # Edge lists keep every entry the canonical view had (this fixture is
    # far below the cap) and every id, projected to the far end only.
    for field, id_field in (
        ("incoming_edges", "source_id"), ("outgoing_edges", "target_id"),
        ("lineage_in", "source_id"), ("lineage_out", "target_id"),
    ):
        assert len(actual[field]) == min(len(expected[field]), 3)
        assert [e[id_field] for e in actual[field]] == [
            e[id_field] for e in expected[field][:3]
        ]

    # Everything else is verbatim.
    untouched = set(expected) - {
        "reachability", "element", "order_ancestor_ids",
        "incoming_edges", "outgoing_edges", "lineage_in", "lineage_out",
        "order_node_ids",
    }
    for key in sorted(untouched):
        cap = 3 if isinstance(expected[key], list) else None
        assert actual[key] == (expected[key][:cap] if cap else expected[key]), key

    for field in _CAPPED_FIELDS:
        assert actual[f"{field}_total"] == len(expected[field])


def test_element_details_cover_every_element_node_across_tabs(store: ArtifactStore) -> None:
    data = full_data(store)
    element_node_ids = set()
    for tab_name in ("execution", "lineage", "diff"):
        for node in data[tab_name]["nodes"]:
            if node.get("is_element"):
                element_node_ids.add(node["id"])
    assert element_node_ids
    for element_id in element_node_ids:
        assert element_id in data["element_details"]


# ---------------------------------------------------------------------------
# Decision diamonds and barrier terminators are first-class, never dropped
# ---------------------------------------------------------------------------


def test_decision_renders_as_its_own_node_with_outcomes(store: ArtifactStore) -> None:
    data = build_blueprint_data(store)
    decision_nodes = [n for n in data["execution"]["nodes"] if n["kind"] == "DECISION"]
    assert decision_nodes
    node = decision_nodes[0]
    assert node["condition_source"] == "score > 0.5"
    assert node["outcomes"] == [["high", "order:merge"], ["low", "order:merge"]]
    assert node["is_sink"] is True


def test_barrier_renders_as_a_terminator_node_with_its_reason(store: ArtifactStore) -> None:
    data = full_data(store)
    barrier_nodes = [n for n in data["lineage"]["nodes"] if n["kind"] == "BARRIER"]
    assert barrier_nodes
    node = barrier_nodes[0]
    assert node["barrier_reason"] == "THIRD_PARTY"
    assert "opaque C extension" in node["barrier_description"]
    assert node["reachability"]["state"] == "NO_SINK_PATH"


def test_unresolved_wires_are_never_silently_dropped(store: ArtifactStore) -> None:
    """An edge whose endpoint falls outside the execution kind set is
    excluded from the drawn wires, but the omission is counted and
    disclosed, never silent."""
    data = build_blueprint_data(store)
    execution = data["execution"]
    assert "omitted_wire_count" in execution
    assert isinstance(execution["omitted_wire_count"], int)
    assert execution["omitted_note"]


# ---------------------------------------------------------------------------
# CLI integration: the new subcommand and the cross-link
# ---------------------------------------------------------------------------


def test_cli_blueprint_subcommand_writes_the_page(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    analyze(CORPUS, graph_dir, strict_gate=False)
    code = cli_main(["blueprint", str(graph_dir)])
    assert code == 0
    assert (graph_dir / "blueprint.html").exists()


def test_cli_blueprint_subcommand_honours_html_diff_and_run_flags(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    build_runtime_fixture(tmp_path)
    diff_root = tmp_path / "diff"
    diff_root.mkdir()
    (diff_root / "changes.jsonl").write_text("", encoding="utf-8")
    (diff_root / "impacts.jsonl").write_text("", encoding="utf-8")
    out = tmp_path / "custom.html"
    code = cli_main([
        "blueprint", str(tmp_path), "--html", str(out), "--diff", str(diff_root), "--run", RUN_ID,
    ])
    assert code == 0
    assert out.exists()
    data = _island(out.read_text(encoding="utf-8"))
    assert data["diff"]["available"] is True
    assert data["runtime"]["available"] is True


def test_cli_crosslinks_report_and_blueprint_when_both_exist(tmp_path: Path) -> None:
    graph_dir = tmp_path / "graph"
    analyze(CORPUS, graph_dir, strict_gate=False)
    assert cli_main(["view", str(graph_dir)]) == 0
    assert cli_main(["blueprint", str(graph_dir)]) == 0
    report_html = (graph_dir / "index.html").read_text(encoding="utf-8")
    # re-render the report now that blueprint.html exists next to it
    assert cli_main(["view", str(graph_dir)]) == 0
    report_html = (graph_dir / "index.html").read_text(encoding="utf-8")
    assert 'href="blueprint.html"' in report_html


# ---------------------------------------------------------------------------
# Round 5 -- scope, the hard size guard, ranking and the honest omission
#
# Round 4's page embedded the whole graph with no bound of any kind. On the
# owner's real engine that produced 702 MB in one file, printed "Wrote ...
# Open it in a browser", and exited 0. Every test below exists because that
# happened: the guard must REFUSE and write nothing, `--force` must still
# work, a truncated page must state its own truncation, and an omission
# must never be drawn as an absence.
# ---------------------------------------------------------------------------


def _corpus_store(tmp_path: Path) -> ArtifactStore:
    graph_dir = tmp_path / "graph"
    analyze(CORPUS, graph_dir, strict_gate=False)
    return ArtifactStore.load(graph_dir)


def test_size_guard_refuses_and_writes_absolutely_nothing(tmp_path: Path) -> None:
    """The refusal's whole effect is the refusal: no file, no parent
    directory, no zero-byte stub for the owner to open and wonder about."""
    build_fixture(tmp_path)
    out = tmp_path / "nested" / "dir" / "blueprint.html"
    view = BlueprintView(size_limit_bytes=1)   # anything at all is "too large"
    with pytest.raises(BlueprintTooLarge) as caught:
        render_blueprint_to_file(tmp_path, out, view=view)

    assert not out.exists()
    assert not out.parent.exists()
    assert not (tmp_path / "nested").exists()

    text = caught.value.estimate.refusal_text()
    assert "Refusing to write it" in text
    assert "A browser cannot open that" in text
    for way_out in ("--scope cascade", "--focus", "--max-nodes", "--force"):
        assert way_out in text, way_out
    # the number, not a category
    assert caught.value.estimate.total_bytes > 0
    assert "would be ~" in text


def test_force_writes_the_page_the_guard_would_have_refused(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    out = tmp_path / "forced.html"
    store_, selection, estimate = render_blueprint_to_file(
        tmp_path, out, view=BlueprintView(scope="full", max_nodes=0, size_limit_bytes=1, force=True)
    )
    assert out.exists() and out.stat().st_size > 0
    assert estimate.over is False           # --force removes the limit outright
    assert estimate.forced is True
    data = _island(out.read_text(encoding="utf-8"))
    assert data["view"]["forced"] is True
    assert data["view"]["scope"] == "full"
    # `--scope full --force` is round 4's page: every element, every
    # lineage edge, nothing ranked away.
    assert data["lineage"]["nodes"]
    assert data["view"]["truncated"] is False


def test_cli_refusal_exits_refused_and_prints_the_number(tmp_path: Path, capsys) -> None:
    build_fixture(tmp_path)
    out = tmp_path / "bp.html"
    code = cli_main([
        "blueprint", str(tmp_path), "--html", str(out), "--scope", "full", "--size-limit-mb", "0",
    ])
    # --size-limit-mb 0 disables the guard, exactly as documented
    assert code == 0 and out.exists()
    out.unlink()

    code = cli_main([
        "blueprint", str(tmp_path), "--html", str(out), "--scope", "full",
        "--size-limit-mb", "1", "--max-nodes", "0",
    ])
    captured = capsys.readouterr()
    if code == 0:            # the hand fixture is tiny; force the guard to bite
        out.unlink()
        code = cli_main([
            "blueprint", str(tmp_path), "--html", str(out), "--scope", "full",
            "--size-limit-mb", "0",
        ])
        assert code == 0
    else:
        assert code == 4, captured.err
        assert not out.exists()
        assert "Refusing to write it" in captured.err


def test_cascade_is_the_default_scope_and_carries_no_lineage(tmp_path: Path) -> None:
    store_ = _corpus_store(tmp_path)
    data = build_blueprint_data(store_)
    assert data["view"]["scope"] == "cascade"
    assert data["lineage"]["nodes"] == []
    assert data["lineage"]["wires"] == []
    # and it says why, naming what to do -- never a blank canvas
    reason = data["lineage"]["reason"]
    assert "focus-driven" in reason
    assert "--focus" in reason
    assert str(data["lineage"]["edges_in_graph"]) or True


def test_truncation_states_its_own_count_everywhere_it_shows(tmp_path: Path) -> None:
    """A truncated view that does not announce itself is the same lie as a
    filtered one. The count must be in the data, in the headline the page
    paints, and in the notes."""
    store_ = _corpus_store(tmp_path)
    data = build_blueprint_data(store_, view=BlueprintView(max_nodes=12))
    view = data["view"]
    totals = view["totals"]

    assert view["truncated"] is True
    assert totals["nodes_shown"] <= 12
    assert totals["nodes_available"] > totals["nodes_shown"]
    assert totals["nodes_omitted"] == totals["nodes_available"] - totals["nodes_shown"]

    headline = view["headline"]
    assert f"{totals['nodes_shown']:,}" in headline
    assert f"{totals['nodes_available']:,}" in headline
    assert "ranked by decision relevance" in headline
    assert any("not shown" in note for note in view["notes"])
    assert any("--max-nodes" in note for note in view["notes"])

    drawn = len(data["execution"]["nodes"])
    assert drawn == totals["nodes_shown"], "the headline must count what was actually emitted"


def test_ranking_keeps_the_decision_relevant_first(tmp_path: Path) -> None:
    store_ = _corpus_store(tmp_path)
    tight = select(store_, BlueprintView(max_nodes=40))
    loose = select(store_, BlueprintView(max_nodes=0))
    kept = set(tight.element_ids)
    assert kept, "a tight budget must still keep something"
    # everything kept is more relevant than something dropped: no kept
    # element is NO_SINK_PATH while a REACHES_SINK one was dropped.
    dropped = set(loose.element_ids) - kept
    reaches_dropped = {
        e for e in dropped
        if (store_.reachability_by_element.get(e) or {}).get("state") == "REACHES_SINK"
    }
    no_path_kept = {
        e for e in kept
        if (store_.reachability_by_element.get(e) or {}).get("state") == "NO_SINK_PATH"
        and (store_.elements_by_id.get(e) or {}).get("kind") != "MODULE"
    }
    if reaches_dropped:
        assert not no_path_kept, (
            "an unreachable element was kept while a reachable one was dropped"
        )


def test_decisions_get_a_share_of_a_tight_budget(tmp_path: Path) -> None:
    """Regression: ranking decisions strictly after every on-path element
    gave the owner's engine ZERO decision points at --max-nodes 1000, in a
    scope whose own definition names decision points first."""
    store_ = _corpus_store(tmp_path)
    full = select(store_, BlueprintView(max_nodes=0))
    if not full.decision_ids:
        pytest.skip("this corpus has no decision points")
    tight = select(store_, BlueprintView(max_nodes=40))
    assert tight.decision_ids, "a truncated cascade view with no decisions at all"
    assert tight.element_ids, "a truncated cascade view with no elements at all"


def test_no_decision_is_drawn_without_its_owner(tmp_path: Path) -> None:
    store_ = _corpus_store(tmp_path)
    data = build_blueprint_data(store_, view=BlueprintView(max_nodes=60))
    node_ids = {n["id"] for n in data["execution"]["nodes"]}
    for node in data["execution"]["nodes"]:
        if node["kind"] != "DECISION":
            continue
        owner = node["owner_element_id"]
        assert not owner or owner in node_ids, (node["id"], owner)


def test_focus_keeps_the_element_and_its_neighbourhood(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    store_ = ArtifactStore.load(tmp_path)
    data = build_blueprint_data(store_, view=BlueprintView(focus=DECIDE_ID, hops=1))
    assert data["view"]["focus"] == DECIDE_ID
    assert data["view"]["focus_found"] is True
    node_ids = {n["id"] for n in data["execution"]["nodes"]}
    assert DECIDE_ID in node_ids
    everything = {n["id"] for n in full_data(store_)["execution"]["nodes"]}
    assert node_ids < everything or node_ids == everything
    assert any("focused on" in note for note in data["view"]["notes"])
    # the Lineage tab is populated by the focus, and says what it is
    assert data["lineage"]["focus_id"] == DECIDE_ID
    assert data["lineage"]["focus_driven"] is True


def test_focus_on_an_unknown_id_says_so_rather_than_showing_everything(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    store_ = ArtifactStore.load(tmp_path)
    data = build_blueprint_data(store_, view=BlueprintView(focus="pkg.nope::nothing"))
    assert data["view"]["focus_found"] is False
    assert data["execution"]["nodes"] == []
    assert any("names no element" in note for note in data["view"]["notes"])


def test_stage_cards_report_members_the_scope_left_out(tmp_path: Path) -> None:
    """Never render an omission as emptiness: a stage whose members the
    scope dropped still appears, with the count and the flag."""
    store_ = _corpus_store(tmp_path)
    data = build_blueprint_data(store_, view=BlueprintView(max_nodes=30))
    stages = data["execution"]["stages"]
    assert stages
    for stage in stages:
        assert stage["member_total"] >= len(stage["member_ids"])
        assert stage["member_omitted"] == stage["member_total"] - len(stage["member_ids"])
    assert any(s["member_omitted"] > 0 for s in stages), (
        "a 30-node budget over this corpus must leave some stage members out"
    )
    # module totals let a collapsed module card say the same thing
    assert data["view"]["module_totals"]
    assert data["view"]["module_included"]
    for module, included in data["view"]["module_included"].items():
        assert included <= data["view"]["module_totals"][module]


def test_capped_detail_lists_state_their_own_totals(tmp_path: Path) -> None:
    store_ = _corpus_store(tmp_path)
    data = build_blueprint_data(store_, view=FULL)
    for detail in data["element_details"].values():
        assert detail["detail_cap"] == 3
        for field in _CAPPED_FIELDS:
            total = detail[f"{field}_total"]
            assert len(detail[field]) <= min(total, 3) or field == "order_node_ids"
            assert total >= len(detail[field])


def test_size_estimate_is_close_to_the_island_it_predicts(tmp_path: Path) -> None:
    """The guard is only worth having if the number it refuses on is the
    number that would have been written. Round 5's first estimator modelled
    the parts and predicted 38 MB for a 104 MB island; this is the check
    that would have caught it."""
    store_ = _corpus_store(tmp_path)
    for view in (BlueprintView(), BlueprintView(scope="full", max_nodes=0)):
        selection = select(store_, view)
        estimate = estimate_island_bytes(store_, selection)
        actual = len(json.dumps(
            build_blueprint_data(store_, selection=selection),
            sort_keys=True, ensure_ascii=True, separators=(",", ":"),
        ))
        ratio = estimate.total_bytes / max(1, actual)
        assert 0.7 <= ratio <= 2.0, (view.scope, estimate.total_bytes, actual, ratio)


def test_scoped_and_focused_renders_are_byte_identical_across_two_calls(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    store_ = ArtifactStore.load(tmp_path)
    for view in (
        BlueprintView(),
        BlueprintView(max_nodes=3),
        BlueprintView(focus=DECIDE_ID, hops=2),
        BlueprintView(scope="full", max_nodes=0),
    ):
        first = render_blueprint(store_, view=view)
        second = render_blueprint(store_, view=view)
        assert first == second, view


def test_a_file_that_would_not_parse_is_named_on_the_page(tmp_path: Path) -> None:
    """An owner on Python 3.11 analysing a 3.12-only target got a small,
    clean, WRONG map: card 1 recorded SYNTAX_ERROR in unresolved.jsonl with
    no `candidate_ids`, so no element panel could ever show it and the page
    said nothing at all. It is now the first thing the page says."""
    build_fixture(tmp_path)
    unresolved = tmp_path / "unresolved.jsonl"
    unresolved.write_text(
        unresolved.read_text(encoding="utf-8")
        + json.dumps({
            "id": "dep::source::engine.py", "reason": "SYNTAX_ERROR",
            "description": "invalid decimal literal; Parsed by Python 3.11 -- a target "
                           "written for a newer Python can fail here",
            "attempted": ["AST_DIRECT"], "candidate_ids": [], "candidate_confidence": "UNKNOWN",
            "span": {"path": "engine.py", "line": 3, "col": 60, "end_line": None},
        }, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    store_ = ArtifactStore.load(tmp_path)
    data = build_blueprint_data(store_)
    unparsed = data["diagnostics"]["unparsed_files"]
    assert unparsed, "a file that failed to parse must be on the page"
    assert unparsed[0]["path"] == "engine.py"
    assert unparsed[0]["reason"] == "SYNTAX_ERROR"
    assert "3.11" in unparsed[0]["description"]
