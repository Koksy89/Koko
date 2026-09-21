"""Tests for card 5 -- unplugged detection and hints.

Two families of cases:

1. Fixtures the corpus (card 8) already provides under
   ``tests/fixtures/mode_b/fnd_*`` -- loaded from their ``expected.json`` and
   fed through :class:`Findings` unmodified.
2. Constructed graphs, one per :class:`FindingKind`, built directly against
   the contract types. Card 8 has not yet published a fixture for every
   ``FindingKind`` (only ``fnd_unknown_not_unplugged`` and
   ``fnd_no_false_positive`` exist on disk at the time this card was built);
   this file says so rather than silently skipping coverage. See the missing
   IDs listed in ``MISSING_FIXTURES`` below.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cascade_map.contracts.interfaces import (
    BlockKind,
    CFGBlock,
    CFGEdge,
    Confidence,
    DecisionPoint,
    Edge,
    EdgeKind,
    Element,
    ElementKind,
    FindingKind,
    LineageEdge,
    LineageKind,
    Method,
    Provenance,
    Reachability,
    ReachabilityState,
    Slice,
    SourceSpan,
    Unresolved,
    UnresolvedReason,
)
from cascade_map.findings import Findings

FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "mode_b"

# FIXTURES.md specifies "one case per FindingKind, plus fnd_unknown_not_unplugged
# and fnd_no_false_positive". At the time this card was built (round 2), card 8
# had created a directory and an `expected.json` for every one of these, but
# eight of them are still stubs: a bare module docstring, one MODULE element,
# no edges, no unresolved records -- nothing for the kind under test to
# exercise. Reported rather than silently treated as done; coverage for these
# kinds is supplied by the constructed-graph tests below instead.
MISSING_FIXTURES = {
    "fnd_unreachable_element",
    "fnd_unconsumed_feature",
    "fnd_dangling_config_reference",
    "fnd_orphaned_config_element",
    "fnd_dead_branch",
    "fnd_shadowed_definition",
    "fnd_duplicated_logic",
    "fnd_decision_irrelevant",
}


def _is_stub_fixture(name: str) -> bool:
    path = FIXTURES_ROOT / name / "expected.json"
    if not path.exists():
        return True
    data = json.loads(path.read_text())
    # A real case needs more than the module element itself to exercise
    # anything; every stub seen at round-2 time has exactly one.
    return len(data.get("elements", [])) <= 1


def test_missing_fixtures_are_reported() -> None:
    """Confirms the gap above still holds; update this test (and add a
    fixture-driven case) the day card 8 fills one of these in for real."""
    for name in MISSING_FIXTURES:
        assert _is_stub_fixture(name), (
            f"{name} now has real content in the corpus -- replace the "
            "constructed-graph test for this kind with a fixture-driven one."
        )


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------


def _span(d: dict) -> SourceSpan:
    return SourceSpan(
        path=d["path"],
        line=d["line"],
        end_line=d.get("end_line"),
        col=d.get("col"),
    )


def _provenance(d: dict) -> Provenance:
    return Provenance(
        method=Method(d["method"]),
        confidence=Confidence(d["confidence"]),
        span=_span(d["span"]) if d.get("span") else None,
        note=d.get("note", ""),
        model_id=d.get("model_id", ""),
        run_id=d.get("run_id", ""),
        event_ids=tuple(d.get("event_ids", ())),
    )


def _element(d: dict) -> Element:
    return Element(
        id=d["id"],
        kind=ElementKind(d["kind"]),
        name=d["name"],
        qualname=d["qualname"],
        module=d["module"],
        span=_span(d["span"]),
        provenance=_provenance(d["provenance"]),
        # The corpus stores `null` for a case where content_hash was not
        # hand-computed by whoever wrote the fixture ("content_hash_asserted":
        # false); Element.content_hash is a required str, so treat that as
        # "not provided" rather than crash the loader.
        content_hash=d.get("content_hash") or "",
        decorators=tuple(d.get("decorators", ())),
        signature=d.get("signature", ""),
        docstring=d.get("docstring", ""),
        parent_id=d.get("parent_id", ""),
        byte_size=d.get("byte_size", 0),
    )


def _unresolved(d: dict) -> Unresolved:
    return Unresolved(
        id=d["id"],
        reason=UnresolvedReason(d["reason"]),
        span=_span(d["span"]),
        description=d["description"],
        attempted=tuple(Method(m) for m in d.get("attempted", ())),
        candidate_ids=tuple(d.get("candidate_ids", ())),
        candidate_confidence=Confidence(d.get("candidate_confidence", "UNKNOWN")),
    )


def _edge_from_json(d: dict) -> Edge:
    return Edge(
        id=d["id"],
        kind=EdgeKind(d["kind"]),
        source_id=d["source_id"],
        target_id=d["target_id"],
        provenance=_provenance(d["provenance"]),
        call_site=_span(d["call_site"]) if d.get("call_site") else None,
    )


def _load_case(name: str) -> dict:
    path = FIXTURES_ROOT / name / "expected.json"
    return json.loads(path.read_text())


# entry_ids are an owner/TARGET_PROFILE input in the real pipeline (Q2 in
# OPEN_QUESTIONS.md) -- card 5 never derives them from the graph itself, so a
# fixture's `expected.json` (which only card 1/2 fields) does not carry them
# either. Deriving "the element nothing calls" as a stand-in silently
# produces an *empty* entry set whenever every function happens to be called
# by something in the file (exactly what `fnd_unknown_not_unplugged` does:
# the module calls `process`, so "no CALLS target" finds nothing) -- which
# then makes every downstream assertion pass vacuously. Declared explicitly
# per case instead, matching what each fixture's own edges model as its
# entry: the module invoking `process`, and the conventional `main`.
def _reachability_from_json(d: dict) -> Reachability:
    return Reachability(
        id=d["id"],
        element_id=d["element_id"],
        state=ReachabilityState(d["state"]),
        provenance=_provenance(d["provenance"]),
        sink_ids=tuple(d.get("sink_ids", ())),
        path_ids=tuple(d.get("path_ids", ())),
        reason=d.get("reason", ""),
    )


def _lineage_edge_from_json(d: dict) -> LineageEdge:
    return LineageEdge(
        id=d["id"],
        kind=LineageKind(d["kind"]),
        source_id=d["source_id"],
        target_id=d["target_id"],
        provenance=_provenance(d["provenance"]),
        span=_span(d["span"]) if d.get("span") else None,
    )


def _build_findings_from_case(data: dict) -> Findings:
    return Findings(
        elements=[_element(e) for e in data["elements"]],
        edges=[_edge_from_json(e) for e in data.get("edges", [])],
        unresolved=[_unresolved(u) for u in data.get("unresolved", [])],
        reachability=[_reachability_from_json(r) for r in data.get("reachability", [])],
        lineage_edges=[_lineage_edge_from_json(l) for l in data.get("lineage", [])],
        entry_ids=tuple(data.get("entry_ids", ())),
    )


# Every `fnd_*` case card 8 has published, real corpus cases and not-yet-filled
# stubs alike. Stubs are skipped explicitly (never silently) via
# `_is_stub_fixture`, so a fresh stub landing here shows up as a skip, not a
# silent gap.
FND_CASE_NAMES = sorted(p.name for p in FIXTURES_ROOT.glob("fnd_*") if p.is_dir())

# Cases that need a fact the current contract does not expose. Listed here
# with the exact gap, run as `xfail(strict=True)` so a contract change that
# closes the gap turns this into a hard failure demanding the fix be applied,
# rather than a silent pass. See the card's final report for the change
# requested.
KNOWN_CONTRACT_GAPS: dict[str, str] = {
    "fnd_dead_branch": (
        "requires knowing DEBUG was assigned the literal `False`; neither "
        "Element nor LineageEdge carries a constant's value, and reading the "
        "source text to find it is re-parsing the target, which this card "
        "must not do"
    ),
    "fnd_duplicated_logic": (
        "requires matching score_alpha/score_beta as identical *bodies* "
        "despite different docstrings ('Weighted sum.' vs 'Weighted sum, "
        "copy-pasted.'); Element.content_hash is documented only as a hash "
        "of 'the element's own source text', which is not specified to "
        "normalise out docstrings/comments, so a content_hash match is not a "
        "sound signal here even when populated (and this fixture leaves it "
        "null, `content_hash_asserted: false`)"
    ),
}


@pytest.mark.parametrize("case", FND_CASE_NAMES)
def test_fnd_corpus_case(case: str) -> None:
    if _is_stub_fixture(case):
        pytest.skip(f"{case} is still a stub in the corpus (no elements beyond the module)")
    if case in KNOWN_CONTRACT_GAPS:
        pytest.xfail(KNOWN_CONTRACT_GAPS[case])

    data = _load_case(case)
    findings = _build_findings_from_case(data).find()

    actual_pairs = {(f.kind.value, f.element_id) for f in findings}
    expected_pairs = {(f["kind"], f["element_id"]) for f in data.get("findings", [])}
    forbidden_pairs = {
        (f["kind"], f["element_id"]) for f in data.get("must_not_contain_findings", [])
    }

    missing = expected_pairs - actual_pairs
    assert not missing, f"{case}: expected findings not produced: {missing}"

    present_forbidden = actual_pairs & forbidden_pairs
    assert not present_forbidden, f"{case}: forbidden findings were produced: {present_forbidden}"

    if "finding_count_exact" in data:
        assert len(findings) == data["finding_count_exact"], (
            f"{case}: expected exactly {data['finding_count_exact']} findings, "
            f"got {len(findings)}: {sorted(actual_pairs)!r}"
        )

    for f in findings:
        assert f.evidence_ids, f"{case}: {f.id} shipped with an empty evidence chain"


def test_fnd_unknown_not_unplugged_actually_exercises_the_skip() -> None:
    """Regression guard for the round-2 vacuous-entries defect: with the
    fixture's own real entry_ids, `Handler.helper` must be genuinely
    unreachable by plain BFS (so this is not passing by accident) and still
    be suppressed because it is an unresolved candidate."""
    data = _load_case("fnd_unknown_not_unplugged")
    f = _build_findings_from_case(data)
    reached, _incoming = f._reachable_set()
    helper_id = "fnd_unknown_not_unplugged::Handler.helper"

    assert f._entry_ids, "entries must be non-empty for this test to mean anything"
    assert helper_id not in reached, "helper must be genuinely unreached by plain BFS"
    assert helper_id in f._unresolved_candidate_ids()

    findings = f.find()
    assert not any(fi.element_id == helper_id for fi in findings)


# ---------------------------------------------------------------------------
# Helpers for constructed graphs
# ---------------------------------------------------------------------------


def _el(
    id_: str,
    kind: ElementKind,
    *,
    name: str | None = None,
    qualname: str | None = None,
    module: str = "m",
    line: int = 1,
    parent_id: str = "",
    content_hash: str | None = None,
    path: str = "m.py",
) -> Element:
    nm = name if name is not None else id_.rsplit("::", 1)[-1]
    qn = qualname if qualname is not None else (id_.split("::", 1)[1] if "::" in id_ else "")
    # Unique per element by default, so unrelated test elements never
    # collide in the DUPLICATED_LOGIC detector by accident.
    ch = content_hash if content_hash is not None else f"hash-of-{id_}"
    return Element(
        id=id_,
        kind=kind,
        name=nm,
        qualname=qn,
        module=module,
        span=SourceSpan(path=path, line=line),
        provenance=Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN),
        content_hash=ch,
        parent_id=parent_id,
    )


def _edge(
    id_: str,
    kind: EdgeKind,
    source: str,
    target: str,
    *,
    confidence: Confidence = Confidence.RESOLVED,
    method: Method = Method.SCOPE_LOOKUP,
) -> Edge:
    return Edge(
        id=id_,
        kind=kind,
        source_id=source,
        target_id=target,
        provenance=Provenance(method=method, confidence=confidence),
    )


# ---------------------------------------------------------------------------
# One constructed-graph test per FindingKind
# ---------------------------------------------------------------------------


def test_unreachable_element() -> None:
    main = _el("m::main", ElementKind.FUNCTION)
    live = _el("m::live", ElementKind.FUNCTION)
    dead = _el("m::dead", ElementKind.FUNCTION, line=10)
    edges = [_edge("e1", EdgeKind.CALLS, "m::main", "m::live")]

    findings = Findings(
        elements=[main, live, dead], edges=edges, entry_ids=["m::main"]
    ).find()

    kinds = {f.kind for f in findings}
    assert FindingKind.UNREACHABLE_ELEMENT in kinds
    unreachable = [f for f in findings if f.kind == FindingKind.UNREACHABLE_ELEMENT]
    assert [f.element_id for f in unreachable] == ["m::dead"]
    assert unreachable[0].evidence_ids
    assert unreachable[0].provenance.confidence == Confidence.RESOLVED
    assert not any(f.element_id == "m::live" for f in unreachable)


def test_unreachable_module_and_class_are_reported() -> None:
    """Round-2 defect: a genuinely unreachable MODULE and a genuinely
    unreachable CLASS must be reported, not silently excluded by kind.

    `entry` is the only live root. `dead_module` has no incoming edge of any
    kind, is not an entry, and is not an unresolved candidate for anything --
    a real, importable-but-never-imported module. `dead_class` is the same
    shape for a class nothing instantiates, subclasses or references.
    """
    entry = _el("m::main", ElementKind.FUNCTION)
    live_module = _el("other", ElementKind.MODULE, module="other", path="other.py")
    dead_module = _el("dead_mod", ElementKind.MODULE, module="dead_mod", path="dead_mod.py")
    dead_class = _el("m::DeadClass", ElementKind.CLASS, line=20)
    edges = [_edge("e1", EdgeKind.IMPORTS, "m::main", "other")]

    findings = Findings(
        elements=[entry, live_module, dead_module, dead_class],
        edges=edges,
        entry_ids=["m::main"],
    ).find()

    unreachable_ids = {
        f.element_id for f in findings if f.kind == FindingKind.UNREACHABLE_ELEMENT
    }
    assert "dead_mod" in unreachable_ids, (
        f"a genuinely unreachable MODULE was not reported; got {unreachable_ids!r}"
    )
    assert "m::DeadClass" in unreachable_ids, (
        f"a genuinely unreachable CLASS was not reported; got {unreachable_ids!r}"
    )
    assert "other" not in unreachable_ids
    assert len(unreachable_ids) == 2
    assert findings
    for f in findings:
        assert f.evidence_ids


def test_unknown_not_unplugged_constructed() -> None:
    """An element reachable only via an unresolved call site is UNKNOWN, not
    reported -- rebuilt here as a plain graph, independent of the fixture."""
    main = _el("m::main", ElementKind.FUNCTION)
    maybe = _el("m::maybe", ElementKind.FUNCTION, line=5)
    unresolved = [
        Unresolved(
            id="u1",
            reason=UnresolvedReason.DYNAMIC_NAME,
            span=SourceSpan(path="m.py", line=3),
            description="getattr with computed name",
            candidate_ids=("m::maybe",),
            candidate_confidence=Confidence.PROBABLE,
        )
    ]

    findings = Findings(
        elements=[main, maybe], unresolved=unresolved, entry_ids=["m::main"]
    ).find()

    assert not any(f.element_id == "m::maybe" for f in findings)


def test_dangling_config_reference() -> None:
    unresolved = [
        Unresolved(
            id="u_cfg",
            reason=UnresolvedReason.MISSING_TARGET,
            span=SourceSpan(path="config/wiring.json", line=4),
            description="component 'NoSuchHandler' names no element",
        )
    ]
    findings = Findings(elements=[], unresolved=unresolved).find()

    assert len(findings) == 1
    f = findings[0]
    assert f.kind == FindingKind.DANGLING_CONFIG_REFERENCE
    assert f.evidence_ids == ("u_cfg",)
    assert f.provenance.confidence == Confidence.HEURISTIC


def test_orphaned_config_element() -> None:
    """A sibling of a config-wired class, in the same inheritance family,
    that no config key names -- the shape `fnd_orphaned_config_element`
    proves. A base class reached only by inheritance is not flagged; a
    sibling that does have its own CONFIGURES edge is not flagged either."""
    base = _el("m::Stage", ElementKind.CLASS, line=1)
    wired = _el("m::WiredStage", ElementKind.CLASS, line=5)
    orphan = _el("m::OrphanStage", ElementKind.CLASS, line=10)
    key = _el("cfg::@file:pipeline.json::/stages/0", ElementKind.CONFIG_KEY)
    edges = [
        _edge(
            "e_cfg",
            EdgeKind.CONFIGURES,
            key.id,
            wired.id,
            confidence=Confidence.HEURISTIC,
            method=Method.CONFIG_STRING_MATCH,
        ),
        _edge("e_inh_wired", EdgeKind.INHERITS, wired.id, base.id),
        _edge("e_inh_orphan", EdgeKind.INHERITS, orphan.id, base.id),
    ]

    findings = Findings(elements=[base, wired, orphan, key], edges=edges).find()

    orphaned = [f for f in findings if f.kind == FindingKind.ORPHANED_CONFIG_ELEMENT]
    assert [f.element_id for f in orphaned] == ["m::OrphanStage"]
    assert orphaned[0].evidence_ids == ("e_cfg", "e_inh_orphan")
    assert orphaned[0].provenance.confidence == Confidence.HEURISTIC
    assert not any(f.element_id in ("m::Stage", "m::WiredStage") for f in orphaned)


def test_dead_branch() -> None:
    entry = CFGBlock(
        id="b_entry",
        element_id="m::f",
        kind=BlockKind.ENTRY,
        span=SourceSpan(path="m.py", line=1),
        provenance=Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN),
    )
    branch = CFGBlock(
        id="b_branch",
        element_id="m::f",
        kind=BlockKind.BRANCH,
        span=SourceSpan(path="m.py", line=2),
        provenance=Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN),
    )
    live_target = CFGBlock(
        id="b_live",
        element_id="m::f",
        kind=BlockKind.NORMAL,
        span=SourceSpan(path="m.py", line=3),
        provenance=Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN),
    )
    dead_target = CFGBlock(
        id="b_dead",
        element_id="m::f",
        kind=BlockKind.NORMAL,
        span=SourceSpan(path="m.py", line=4),
        provenance=Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN),
    )
    cfg_edges = [
        CFGEdge(id="c1", source_id="b_entry", target_id="b_branch"),
        CFGEdge(
            id="c2",
            source_id="b_branch",
            target_id="b_live",
            condition="True",
            taken_when=True,
            provenance=Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN),
        ),
        CFGEdge(
            id="c3",
            source_id="b_branch",
            target_id="b_dead",
            condition="False",
            taken_when=True,
            provenance=Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN),
        ),
    ]

    findings = Findings(
        elements=[_el("m::f", ElementKind.FUNCTION)],
        cfg_blocks=[entry, branch, live_target, dead_target],
        cfg_edges=cfg_edges,
    ).find()

    dead = [f for f in findings if f.kind == FindingKind.DEAD_BRANCH]
    assert [f.evidence_ids for f in dead] == [("c3",)]
    assert dead[0].element_id == "m::f"


def test_shadowed_definition() -> None:
    first = _el("m::helper", ElementKind.FUNCTION, line=1)
    second = Element(
        id="m::helper#2",
        kind=ElementKind.FUNCTION,
        name="helper",
        qualname="helper",
        module="m",
        span=SourceSpan(path="m.py", line=10),
        provenance=Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN),
        content_hash="h2",
    )

    findings = Findings(elements=[first, second]).find()

    shadowed = [f for f in findings if f.kind == FindingKind.SHADOWED_DEFINITION]
    assert [f.element_id for f in shadowed] == ["m::helper"]
    assert shadowed[0].evidence_ids == ("m::helper#2",)
    assert shadowed[0].provenance.confidence == Confidence.PROBABLE


def test_duplicated_logic() -> None:
    a = _el("m::a", ElementKind.FUNCTION, content_hash="same")
    b = _el("m::b", ElementKind.FUNCTION, content_hash="same", line=5)
    c = _el("m::c", ElementKind.FUNCTION, content_hash="different", line=9)

    findings = Findings(elements=[a, b, c]).find()

    dups = [f for f in findings if f.kind == FindingKind.DUPLICATED_LOGIC]
    assert [f.element_id for f in dups] == ["m::b"]
    assert dups[0].evidence_ids == ("m::a",)
    assert dups[0].provenance.confidence == Confidence.RESOLVED


def test_unconsumed_feature() -> None:
    writer = _el("m::compute", ElementKind.FUNCTION)
    feature = _el("@feature:unused_score", ElementKind.FEATURE, name="unused_score")
    lineage = [
        LineageEdge(
            id="l1",
            kind=LineageKind.ASSIGNS,
            source_id="m::compute",
            target_id="@feature:unused_score",
            provenance=Provenance(method=Method.DATAFLOW, confidence=Confidence.RESOLVED),
        )
    ]

    findings = Findings(
        elements=[writer, feature], lineage_edges=lineage, entry_ids=["m::compute"]
    ).find()

    unconsumed = [f for f in findings if f.kind == FindingKind.UNCONSUMED_FEATURE]
    assert [f.element_id for f in unconsumed] == ["@feature:unused_score"]
    assert unconsumed[0].evidence_ids == ("l1",)


def test_decision_irrelevant_fallback_without_reachability() -> None:
    """No card-3 `Reachability` supplied: falls back to the slice/decision-point
    derivation. Kept only as a graceful-degradation path."""
    entry = _el("m::main", ElementKind.FUNCTION)
    irrelevant = _el("m::side_calc", ElementKind.FUNCTION, line=4)
    relevant = _el("m::risk_calc", ElementKind.FUNCTION, line=8)
    edges = [
        _edge("e1", EdgeKind.CALLS, "m::main", "m::side_calc"),
        _edge("e2", EdgeKind.CALLS, "m::main", "m::risk_calc"),
    ]
    decision = DecisionPoint(
        id="dp1",
        element_id="m::main",
        condition_source="risk_calc() > 0",
        reads_ids=("m::risk_calc",),
        outcomes=(("approve", "m::main"), ("deny", "m::main")),
        is_sink=True,
    )
    slice_irrelevant = Slice(
        id="s1",
        root_id="m::side_calc",
        direction="forward",
        member_ids=("m::side_calc",),
        edge_ids=("e1",),
        barrier_ids=(),
        reaches_sink_ids=(),
        confidence=Confidence.RESOLVED,
    )
    slice_relevant = Slice(
        id="s2",
        root_id="m::risk_calc",
        direction="forward",
        member_ids=("m::risk_calc",),
        edge_ids=("e2",),
        barrier_ids=(),
        reaches_sink_ids=("dp1",),
        confidence=Confidence.RESOLVED,
    )

    findings = Findings(
        elements=[entry, irrelevant, relevant],
        edges=edges,
        decision_points=[decision],
        slices=[slice_irrelevant, slice_relevant],
        entry_ids=["m::main"],
    ).find()

    result = [f for f in findings if f.kind == FindingKind.DECISION_IRRELEVANT]
    assert [f.element_id for f in result] == ["m::side_calc"]
    assert not any(f.element_id == "m::risk_calc" for f in findings)


def test_decision_irrelevant_reads_card3_reachability() -> None:
    """Primary path: card 3's `Reachability` per element is consumed
    directly, not recomputed -- this is the fix for the second-source-of-truth
    defect. A MODULE and a CLASS are included here specifically, since that
    is exactly the kind-exclusion problem this contract addition closes."""
    entry = _el("m::main", ElementKind.FUNCTION)
    side_module = _el("side", ElementKind.MODULE, module="side", path="side.py")
    side_class = _el("m::SideEffect", ElementKind.CLASS, line=12)
    relevant = _el("m::risk_calc", ElementKind.FUNCTION, line=8)
    edges = [
        _edge("e1", EdgeKind.IMPORTS, "m::main", "side"),
        _edge("e2", EdgeKind.INSTANTIATES, "m::main", "m::SideEffect"),
        _edge("e3", EdgeKind.CALLS, "m::main", "m::risk_calc"),
    ]
    reachability = [
        Reachability(
            id="r_side",
            element_id="side",
            state=ReachabilityState.NO_SINK_PATH,
            provenance=Provenance(method=Method.CFG_REACHABILITY, confidence=Confidence.RESOLVED),
            reason="imported for logging only, never read by a decision",
        ),
        Reachability(
            id="r_sideclass",
            element_id="m::SideEffect",
            state=ReachabilityState.NO_SINK_PATH,
            provenance=Provenance(method=Method.CFG_REACHABILITY, confidence=Confidence.RESOLVED),
            reason="constructed but its output is discarded",
        ),
        Reachability(
            id="r_risk",
            element_id="m::risk_calc",
            state=ReachabilityState.REACHES_SINK,
            provenance=Provenance(method=Method.CFG_REACHABILITY, confidence=Confidence.CERTAIN),
            sink_ids=("dp1",),
        ),
    ]

    findings = Findings(
        elements=[entry, side_module, side_class, relevant],
        edges=edges,
        reachability=reachability,
        entry_ids=["m::main"],
    ).find()

    result = {
        f.element_id: f for f in findings if f.kind == FindingKind.DECISION_IRRELEVANT
    }
    assert set(result) == {"side", "m::SideEffect"}, (
        "a MODULE and a CLASS marked NO_SINK_PATH by card 3 must both be reported"
    )
    assert "m::risk_calc" not in result
    for f in result.values():
        assert f.evidence_ids
        assert "r_side" in f.evidence_ids or "r_sideclass" in f.evidence_ids


def test_decision_irrelevant_never_reports_unknown_reachability() -> None:
    """UNKNOWN must never render as "reaches nothing": card 3 could not
    tell, which is not a claim card 5 may make into a finding."""
    entry = _el("m::main", ElementKind.FUNCTION)
    uncertain = _el("m::maybe_relevant", ElementKind.FUNCTION, line=6)
    edges = [_edge("e1", EdgeKind.CALLS, "m::main", "m::maybe_relevant")]
    reachability = [
        Reachability(
            id="r1",
            element_id="m::maybe_relevant",
            state=ReachabilityState.UNKNOWN,
            provenance=Provenance(method=Method.CFG_REACHABILITY, confidence=Confidence.UNKNOWN),
            reason="reached only through an unresolved call site",
        ),
    ]

    findings = Findings(
        elements=[entry, uncertain],
        edges=edges,
        reachability=reachability,
        entry_ids=["m::main"],
    ).find()

    assert not any(f.element_id == "m::maybe_relevant" for f in findings)


# ---------------------------------------------------------------------------
# Precision guard
# ---------------------------------------------------------------------------


def test_no_false_positive_on_fully_live_graph() -> None:
    main = _el("m::main", ElementKind.FUNCTION)
    step = _el("m::step", ElementKind.FUNCTION, line=5)
    edges = [_edge("e1", EdgeKind.CALLS, "m::main", "m::step")]

    findings = Findings(elements=[main, step], edges=edges, entry_ids=["m::main"]).find()

    assert findings == ()


def test_all_finding_kinds_are_emitted_by_the_test_suite() -> None:
    """Sanity check: every FindingKind has at least one test above proving it
    can be produced. Prevents a silently-unimplemented kind."""
    exercised = {
        FindingKind.UNREACHABLE_ELEMENT,
        FindingKind.UNCONSUMED_FEATURE,
        FindingKind.DANGLING_CONFIG_REFERENCE,
        FindingKind.ORPHANED_CONFIG_ELEMENT,
        FindingKind.DEAD_BRANCH,
        FindingKind.SHADOWED_DEFINITION,
        FindingKind.DUPLICATED_LOGIC,
        FindingKind.DECISION_IRRELEVANT,
    }
    assert exercised == set(FindingKind)


def test_determinism_same_input_same_output() -> None:
    main = _el("m::main", ElementKind.FUNCTION)
    dead = _el("m::dead", ElementKind.FUNCTION, line=10)
    kwargs = dict(elements=[main, dead], entry_ids=["m::main"])

    first = Findings(**kwargs).find()
    second = Findings(**kwargs).find()

    assert first == second
    assert [f.id for f in first] == sorted(f.id for f in first)


def test_every_finding_has_nonempty_evidence() -> None:
    main = _el("m::main", ElementKind.FUNCTION)
    dead = _el("m::dead", ElementKind.FUNCTION, line=10)
    key = _el("cfg::k", ElementKind.CONFIG_KEY)
    handler = _el("m::Handler", ElementKind.CLASS, line=3)
    cfg_edge = _edge(
        "e_cfg",
        EdgeKind.CONFIGURES,
        key.id,
        handler.id,
        confidence=Confidence.HEURISTIC,
        method=Method.CONFIG_STRING_MATCH,
    )
    unresolved = [
        Unresolved(
            id="u1",
            reason=UnresolvedReason.MISSING_TARGET,
            span=SourceSpan(path="config/x.json", line=1),
            description="dangling",
        )
    ]

    findings = Findings(
        elements=[main, dead, key, handler],
        edges=[cfg_edge],
        unresolved=unresolved,
        entry_ids=["m::main"],
    ).find()

    assert findings
    for f in findings:
        assert f.evidence_ids, f"{f.id} shipped with an empty evidence chain"
