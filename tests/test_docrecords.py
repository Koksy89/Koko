"""Tests for card 16: documentation records, the completeness gate, and the
enrichment client.

Built against the `interfaces.py` contract with hand-constructed graph
values -- cards 1-6 are being built in parallel and are not integrated here.
No network is used anywhere; the enrichment client is always given a stub
transport.
"""

from __future__ import annotations

import dataclasses

import pytest

from cascade_map.contracts.interfaces import (
    Confidence,
    Edge,
    EdgeKind,
    Element,
    ElementKind,
    EventKind,
    Finding,
    FindingKind,
    LineageEdge,
    LineageKind,
    Method,
    Provenance,
    Slice,
    SourceSpan,
    TraceEvent,
    ValueCapture,
    CaptureStatus,
    VersionChange,
    ChangeKind,
    canonical_jsonl,
    make_id,
)
from cascade_map.docrecords import (
    DocumentationBuilder,
    parse_signature,
    unknown,
)
from cascade_map.enrichment import (
    API_KEY_ENV,
    EnrichmentClient,
    HAIKU_MODEL,
    EnrichmentDisabled,
)


def _span(line: int = 1, end: int | None = None) -> SourceSpan:
    return SourceSpan(path="pkg/mod.py", line=line, end_line=end or line)


def _prov(confidence: Confidence = Confidence.CERTAIN) -> Provenance:
    return Provenance(method=Method.AST_DIRECT, confidence=confidence, span=_span())


def _func_element(
    name: str = "compute_score",
    module: str = "pkg.mod",
    signature: str = "(self, a: int, b: str = 'x') -> bool",
    docstring: str = "Computes a score.",
    parent_id: str = "",
) -> Element:
    eid = make_id(module, name)
    return Element(
        id=eid,
        kind=ElementKind.FUNCTION,
        name=name,
        qualname=name,
        module=module,
        span=_span(10, 20),
        provenance=_prov(),
        content_hash="deadbeef",
        decorators=("staticmethod",),
        signature=signature,
        docstring=docstring,
        parent_id=parent_id,
    )


# ---------------------------------------------------------------------------
# Signature parsing
# ---------------------------------------------------------------------------


def test_parse_signature_extracts_params_and_return() -> None:
    params, ret = parse_signature("(self, a: int, b: str = 'x') -> bool")
    assert params == [
        {"name": "a", "annotation": "int", "default": ""},
        {"name": "b", "annotation": "str", "default": "'x'"},
    ]
    assert ret == "bool"


def test_parse_signature_no_parens_is_empty() -> None:
    params, ret = parse_signature("not a signature")
    assert params == []
    assert ret == ""


def test_parse_signature_handles_nested_brackets() -> None:
    params, ret = parse_signature("(items: list[dict[str, int]] = []) -> None")
    assert params[0]["name"] == "items"
    assert params[0]["annotation"] == "list[dict[str, int]]"
    assert ret == "None"


# ---------------------------------------------------------------------------
# unknown() sentinel
# ---------------------------------------------------------------------------


def test_unknown_requires_a_reason() -> None:
    with pytest.raises(ValueError):
        unknown("")
    assert unknown("no data") == {"status": "UNKNOWN", "reason": "no data"}


# ---------------------------------------------------------------------------
# Record assembly -- minimal graph
# ---------------------------------------------------------------------------


def test_records_one_per_element_with_required_fields_filled() -> None:
    el = _func_element()
    builder = DocumentationBuilder(elements=[el])
    records = builder.records()
    assert len(records) == 1
    rec = records[0]
    assert rec.element_id == el.id
    assert rec.identity["name"] == "compute_score"
    assert rec.identity["parameters"] == [
        {"name": "a", "annotation": "int", "default": ""},
        {"name": "b", "annotation": "str", "default": "'x'"},
    ]
    assert rec.identity["return_type"] == "bool"
    # No decision sink configured: relevance is an explicit, reasoned unknown.
    assert rec.decision_relevance["reaches_sink"]["status"] == "UNKNOWN"
    assert rec.decision_relevance["reaches_sink"]["reason"]
    # No runtime overlay: runtime stays empty, not a gate requirement.
    assert rec.runtime == {}
    # Model fields empty: enrichment never ran.
    assert rec.model_prose == ""
    assert rec.model_id == ""


def test_gate_passes_on_a_complete_minimal_record_set() -> None:
    el = _func_element()
    builder = DocumentationBuilder(elements=[el])
    records = builder.records()
    assert builder.completeness_gate(records) == ()


def test_gate_fails_when_an_element_has_no_record() -> None:
    el = _func_element()
    builder = DocumentationBuilder(elements=[el])
    assert builder.completeness_gate([]) == (el.id,)


def test_gate_fails_when_a_required_identity_field_is_empty() -> None:
    el = _func_element()
    builder = DocumentationBuilder(elements=[el])
    [rec] = builder.records()
    broken_identity = dict(rec.identity)
    broken_identity["docstring"] = ""  # silently empty -- not unknown(), a failure
    broken = dataclasses.replace(rec, identity=broken_identity)
    offenders = builder.completeness_gate([broken])
    assert offenders == (el.id,)


def test_gate_accepts_explicit_unknown_as_complete() -> None:
    el = _func_element(signature="", docstring="")
    builder = DocumentationBuilder(elements=[el])
    [rec] = builder.records()
    assert rec.identity["signature"]["status"] == "UNKNOWN"
    assert rec.identity["docstring"]["status"] == "UNKNOWN"
    assert builder.completeness_gate([rec]) == ()


# ---------------------------------------------------------------------------
# Cascade position, calls, decision relevance
# ---------------------------------------------------------------------------


def test_callers_and_callees_from_call_edges() -> None:
    caller = _func_element(name="run", module="pkg.mod")
    callee = _func_element(name="helper", module="pkg.mod")
    edge = Edge(
        id="e1",
        kind=EdgeKind.CALLS,
        source_id=caller.id,
        target_id=callee.id,
        provenance=_prov(),
    )
    builder = DocumentationBuilder(elements=[caller, callee], edges=[edge])
    records = {r.element_id: r for r in builder.records()}
    assert records[caller.id].cascade_position["callees"] == [callee.id]
    assert records[callee.id].cascade_position["callers"] == [caller.id]


def test_decision_relevance_true_when_sink_is_reachable() -> None:
    a = _func_element(name="a", module="pkg.mod")
    sink = _func_element(name="sink", module="pkg.mod")
    edge = Edge(
        id="e1", kind=EdgeKind.CALLS, source_id=a.id, target_id=sink.id, provenance=_prov()
    )
    builder = DocumentationBuilder(elements=[a, sink], edges=[edge], decision_sink_ids=[sink.id])
    records = {r.element_id: r for r in builder.records()}
    assert records[a.id].decision_relevance["reaches_sink"] is True
    assert records[a.id].decision_relevance["paths"] == [[a.id, sink.id]]
    assert builder.completeness_gate(builder.records()) == ()


def test_decision_relevance_false_when_sink_unreachable() -> None:
    a = _func_element(name="a", module="pkg.mod")
    sink = _func_element(name="sink", module="pkg.mod")
    builder = DocumentationBuilder(elements=[a, sink], decision_sink_ids=[sink.id])
    records = {r.element_id: r for r in builder.records()}
    assert records[a.id].decision_relevance["reaches_sink"] is False
    assert records[a.id].decision_relevance["paths"] == []


# ---------------------------------------------------------------------------
# Data role from lineage and slices
# ---------------------------------------------------------------------------


def test_data_role_reads_and_writes_from_lineage() -> None:
    el = _func_element()
    lineage = [
        LineageEdge(
            id="l1",
            kind=LineageKind.READS,
            source_id=el.id,
            target_id="@feature:price",
            provenance=_prov(),
        ),
        LineageEdge(
            id="l2",
            kind=LineageKind.COLUMN_WRITE,
            source_id=el.id,
            target_id="@feature:score",
            provenance=_prov(),
        ),
    ]
    builder = DocumentationBuilder(elements=[el], lineage_edges=lineage)
    [rec] = builder.records()
    assert rec.data_role["features_read"] == ["@feature:price"]
    assert rec.data_role["features_written"] == ["@feature:score"]


def test_data_role_unknown_when_no_lineage_supplied() -> None:
    el = _func_element()
    builder = DocumentationBuilder(elements=[el])
    [rec] = builder.records()
    assert rec.data_role["features_read"]["status"] == "UNKNOWN"


def test_slice_summary_present_when_a_slice_roots_here() -> None:
    el = _func_element()
    sl = Slice(
        id="s1",
        root_id=el.id,
        direction="backward",
        member_ids=(el.id,),
        edge_ids=(),
        barrier_ids=(),
        reaches_sink_ids=(),
        confidence=Confidence.CERTAIN,
    )
    builder = DocumentationBuilder(elements=[el], slices=[sl])
    [rec] = builder.records()
    assert rec.data_role["backward_slice_summary"]["member_count"] == 1
    assert rec.data_role["forward_slice_summary"]["status"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# Findings and changes attach by element id
# ---------------------------------------------------------------------------


def test_finding_and_change_ids_attach_to_the_right_element() -> None:
    el = _func_element()
    finding = Finding(
        id="f1",
        kind=FindingKind.UNREACHABLE_ELEMENT,
        element_id=el.id,
        span=_span(),
        summary="unreachable",
        hint="check wiring",
        evidence_ids=("e1",),
        provenance=_prov(),
    )
    change = VersionChange(
        id="c1", kind=ChangeKind.BODY_CHANGED, before_id=el.id, after_id=el.id, provenance=_prov()
    )
    builder = DocumentationBuilder(elements=[el], findings=[finding], changes=[change])
    [rec] = builder.records()
    assert rec.finding_ids == ("f1",)
    assert rec.change_ids == ("c1",)


# ---------------------------------------------------------------------------
# Runtime overlay
# ---------------------------------------------------------------------------


def test_runtime_overlay_required_fields_filled_when_present() -> None:
    el = _func_element()
    event = TraceEvent(
        event_id="ev1",
        run_id="run1",
        kind=EventKind.CALL,
        element_id=el.id,
        sequence=0,
        depth=0,
        values={"a": ValueCapture(status=CaptureStatus.FULL, repr_text="1", type_name="int")},
        provenance=Provenance(
            method=Method.RUNTIME_OBSERVED,
            confidence=Confidence.CERTAIN,
            run_id="run1",
            event_ids=("ev1",),
        ),
    )
    builder = DocumentationBuilder(elements=[el], trace_events=[event])
    [rec] = builder.records()
    assert rec.runtime["observed_calls"] == 1
    assert rec.runtime["alignment_verdict"]["status"] == "UNKNOWN"
    assert builder.completeness_gate([rec]) == ()


def test_gate_fails_when_runtime_overlay_present_but_record_lacks_it() -> None:
    el = _func_element()
    event = TraceEvent(
        event_id="ev1",
        run_id="run1",
        kind=EventKind.CALL,
        element_id=el.id,
        sequence=0,
        depth=0,
    )
    builder = DocumentationBuilder(elements=[el], trace_events=[event])
    [rec] = builder.records()
    broken = dataclasses.replace(rec, runtime={})
    assert builder.completeness_gate([broken]) == (el.id,)


# ---------------------------------------------------------------------------
# Every ElementKind gets a complete record -- fields that don't apply to a
# kind must be an explicit unknown(), never a silently blank string or list.
# ---------------------------------------------------------------------------


def _element_of_kind(kind: ElementKind) -> Element:
    """One representative element per kind, deliberately leaving fields blank
    (no signature, no decorators, no module, no parent) the way card 1 would
    for a kind that structurally cannot have them -- e.g. a DATA_FILE has no
    Python module and no signature."""
    return Element(
        id=make_id("pkg.mod", f"thing_{kind.value.lower()}"),
        kind=kind,
        name=f"thing_{kind.value.lower()}",
        qualname=f"thing_{kind.value.lower()}",
        module="pkg.mod" if kind not in {ElementKind.DATA_FILE, ElementKind.CONFIG_KEY, ElementKind.FEATURE} else "",
        span=_span(1, 1),
        provenance=_prov(),
        content_hash="abc123",
    )


ALL_ELEMENT_KINDS = tuple(ElementKind)


def test_every_element_kind_gets_a_complete_record() -> None:
    assert len(ALL_ELEMENT_KINDS) == 13, "update this test if ElementKind grows"
    elements = [_element_of_kind(k) for k in ALL_ELEMENT_KINDS]
    builder = DocumentationBuilder(elements=elements)
    records = builder.records()
    assert len(records) == len(elements)
    offenders = builder.completeness_gate(records)
    assert offenders == (), f"gate offenders for kinds it should have handled: {offenders}"


def test_non_callable_kinds_get_explicit_unknown_not_blank_fields() -> None:
    for kind in (ElementKind.DATA_FILE, ElementKind.CONFIG_KEY, ElementKind.FEATURE, ElementKind.BLOB):
        el = _element_of_kind(kind)
        builder = DocumentationBuilder(elements=[el])
        [rec] = builder.records()
        assert rec.identity["parameters"]["status"] == "UNKNOWN", kind
        assert rec.identity["decorators"]["status"] == "UNKNOWN", kind
        assert rec.cascade_position["callers"]["status"] == "UNKNOWN", kind
        assert rec.cascade_position["callees"]["status"] == "UNKNOWN", kind
        assert rec.data_role["features_read"]["status"] == "UNKNOWN", kind
        assert builder.completeness_gate(builder.records()) == ()


def test_no_python_module_kinds_get_explicit_unknown_module() -> None:
    for kind in (ElementKind.DATA_FILE, ElementKind.CONFIG_KEY, ElementKind.FEATURE):
        el = _element_of_kind(kind)
        builder = DocumentationBuilder(elements=[el])
        [rec] = builder.records()
        assert rec.identity["module"]["status"] == "UNKNOWN", kind
        assert rec.identity["module"]["reason"]


def test_root_of_hierarchy_kinds_have_no_enclosing_scope() -> None:
    for kind in (ElementKind.PACKAGE, ElementKind.MODULE):
        el = _element_of_kind(kind)
        builder = DocumentationBuilder(elements=[el])
        [rec] = builder.records()
        assert rec.cascade_position["enclosing_scope"]["status"] == "UNKNOWN", kind


def test_config_key_gets_enclosing_scope_from_parent_data_file() -> None:
    data_file = _element_of_kind(ElementKind.DATA_FILE)
    key = dataclasses.replace(
        _element_of_kind(ElementKind.CONFIG_KEY),
        parent_id=data_file.id,
    )
    builder = DocumentationBuilder(elements=[data_file, key])
    records = {r.element_id: r for r in builder.records()}
    assert records[key.id].cascade_position["enclosing_scope"] == data_file.id
    assert builder.completeness_gate(builder.records()) == ()


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_two_runs_produce_byte_identical_jsonl() -> None:
    elements = [_func_element(name=f"f{i}") for i in range(5)]
    builder1 = DocumentationBuilder(elements=elements)
    builder2 = DocumentationBuilder(elements=list(reversed(elements)))
    out1 = canonical_jsonl(builder1.records(), sort_key="element_id")
    out2 = canonical_jsonl(builder2.records(), sort_key="element_id")
    assert out1 == out2
    assert out1  # non-empty


# ---------------------------------------------------------------------------
# Enrichment client
# ---------------------------------------------------------------------------


def test_enrichment_disabled_with_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = EnrichmentClient()
    assert client.enabled is False
    result = client.summarize_element({"name": "x"})
    assert result.prose == ""
    assert result.model_id == ""


def test_enrichment_never_reads_anthropic_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-be-used")
    client = EnrichmentClient()
    assert client.enabled is False


def test_enrichment_enabled_with_key_uses_stub_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, "test-key")

    def stub_transport(payload: dict, model: str) -> str:
        assert model == HAIKU_MODEL
        assert "name" in payload
        assert "signature" not in payload or len(payload["signature"]) <= 2000
        return f"summary of {payload.get('name', '?')}"

    client = EnrichmentClient(transport=stub_transport)
    assert client.enabled is True
    result = client.summarize_element({"name": "compute_score", "docstring": "does a thing"})
    assert result.prose == "summary of compute_score"
    assert result.model_id == HAIKU_MODEL
    assert result.source == "MODEL_PROPOSED"


def test_enabled_client_without_transport_raises_rather_than_network() -> None:
    client = EnrichmentClient(api_key="k")
    with pytest.raises(EnrichmentDisabled):
        client.summarize_element({"name": "x"})


def test_enrich_records_labels_prose_separately_and_never_touches_facts() -> None:
    el = _func_element()
    builder = DocumentationBuilder(elements=[el])
    plain_records = builder.records()

    def stub_transport(payload: dict, model: str) -> str:
        return "a model-written summary"

    client = EnrichmentClient(api_key="k", transport=stub_transport)
    enriched_records = builder.enrich(plain_records, client)

    assert enriched_records[0].model_prose == "a model-written summary"
    assert enriched_records[0].model_id == HAIKU_MODEL

    # Every fact field is untouched -- strip model_prose/model_id and compare.
    plain = dataclasses.replace(enriched_records[0], model_prose="", model_id="")
    assert plain == plain_records[0]


def test_deterministic_artifact_identical_with_and_without_enrichment() -> None:
    el = _func_element()
    builder = DocumentationBuilder(elements=[el])
    records = builder.records()

    disabled_client = EnrichmentClient(api_key="")
    still_plain = builder.enrich(records, disabled_client)
    assert still_plain == records

    def stub_transport(payload: dict, model: str) -> str:
        return "prose"

    enabled_client = EnrichmentClient(api_key="k", transport=stub_transport)
    enriched = builder.enrich(records, enabled_client)

    def strip(recs):
        return canonical_jsonl(
            [dataclasses.replace(r, model_prose="", model_id="") for r in recs],
            sort_key="element_id",
        )

    assert strip(records) == strip(enriched)


def test_enrichment_payload_never_carries_full_docstring_source_beyond_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Guards against accidentally widening the payload to raw source bodies.
    monkeypatch.setenv(API_KEY_ENV, "k")
    seen = {}

    def stub_transport(payload: dict, model: str) -> str:
        seen.update(payload)
        return "ok"

    client = EnrichmentClient(transport=stub_transport)
    client.summarize_element(
        {
            "name": "f",
            "qualname": "f",
            "kind": "FUNCTION",
            "module": "pkg.mod",
            "signature": "(a) -> None",
            "docstring": "doc",
            "body_source": "print('should never be sent')",
        }
    )
    assert "body_source" not in seen
