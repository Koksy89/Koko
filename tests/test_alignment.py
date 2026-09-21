"""Tests for card 13 — the intent registry and alignment verdicts.

The FIXTURES.md Mode A alignment cases (`ali_aligned`, `ali_misaligned`,
`ali_not_exercised`, `ali_no_intent`, `ali_proposed_not_binding`) each have a test
named after them below. They run against the `run_linear` fixture's recorded events
where the corpus provides them and against constructed :class:`TraceEvent` values
otherwise: card 13 takes `Sequence[Intent]` and `Sequence[TraceEvent]` per the
`AlignmentCard` protocol, so the contract is what is under test, not card 12's
internals.

Nothing here executes target code. The one fixture touched is read as JSON.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

import pytest

from cascade_map import alignment as alignment_module
from cascade_map.alignment import (
    API_KEY_ENV,
    MODEL_ID,
    AlignmentEngine,
    AnthropicAlignmentModel,
    CheckKind,
    Coverage,
    default_model,
    intents_jsonl,
    issues_jsonl,
    load_registry,
    model_available,
    parse_check,
    parse_registry_text,
    propose_intents,
    verdicts_jsonl,
)
from cascade_map.contracts.interfaces import (
    AlignmentCard,
    AlignmentVerdict,
    BlockedAttempt,
    CaptureStatus,
    Confidence,
    Edge,
    EdgeKind,
    Element,
    ElementKind,
    EventKind,
    Intent,
    IntentStatus,
    LineageEdge,
    LineageKind,
    Method,
    Provenance,
    RunRecord,
    SourceSpan,
    TraceEvent,
    UnresolvedReason,
    ValueCapture,
    Verdict,
    canonical_dumps,
    feature_id,
)

FIXTURES = Path(__file__).parent / "fixtures"
RUN = "run_001"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def make_element(element_id: str, *, name: str = "", docstring: str = "") -> Element:
    return Element(
        id=element_id,
        kind=ElementKind.FUNCTION,
        name=name or element_id.split("::")[-1],
        qualname=element_id.split("::")[-1],
        module=element_id.split("::")[0],
        span=SourceSpan(path="fixture.py", line=1),
        provenance=Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN),
        content_hash="hash",
        docstring=docstring,
    )


def make_event(
    event_id: str,
    element_id: str,
    kind: EventKind,
    sequence: int,
    *,
    run_id: str = RUN,
    caller: str = "",
    values: dict[str, ValueCapture] | None = None,
    depth: int = 0,
) -> TraceEvent:
    return TraceEvent(
        event_id=event_id,
        run_id=run_id,
        kind=kind,
        element_id=element_id,
        sequence=sequence,
        depth=depth,
        caller_event_id=caller,
        values=values or {},
        provenance=Provenance(
            method=Method.RUNTIME_OBSERVED,
            confidence=Confidence.CERTAIN,
            run_id=run_id,
            event_ids=(event_id,),
        ),
    )


def full(repr_text: str, type_name: str) -> ValueCapture:
    return ValueCapture(status=CaptureStatus.FULL, repr_text=repr_text, type_name=type_name)


def intent(
    element_id: str,
    *,
    statement: str = "does the thing",
    status: IntentStatus = IntentStatus.CONFIRMED,
    invariants: tuple[str, ...] = (),
    reads: tuple[str, ...] = (),
    writes: tuple[str, ...] = (),
    intent_id: str = "",
) -> Intent:
    return Intent(
        id=intent_id or f"@intent:{element_id}",
        element_id=element_id,
        status=status,
        statement=statement,
        invariants=invariants,
        expected_reads=reads,
        expected_writes=writes,
        provenance=Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN),
    )


def by_element(verdicts: Sequence[AlignmentVerdict]) -> dict[str, AlignmentVerdict]:
    return {verdict.element_id: verdict for verdict in verdicts}


def event_id_of(
    events: Sequence[TraceEvent], element_id: str, kind: EventKind, occurrence: int = 0
) -> str:
    """The ID of a fixture event, found by what it *is*, never written as a literal.

    Event ID formatting belongs to cards 8 and 12 (`evt_` plus a zero-padded ordinal,
    so events.jsonl sorts into execution order). Asserting the relation -- "the
    verdict cites the RETURN of this element" -- is both the property under test and
    immune to their formatting.
    """
    ordered = sorted(events, key=lambda event: event.sequence)
    matches = [
        event for event in ordered if event.element_id == element_id and event.kind is kind
    ]
    assert len(matches) > occurrence, f"fixture has no {kind.value} #{occurrence} for {element_id}"
    return matches[occurrence].event_id


def run_id_of(events: Sequence[TraceEvent]) -> str:
    """The run ID the fixture declares. Also not ours to hardcode."""
    run_ids = {event.run_id for event in events}
    assert len(run_ids) == 1, f"fixture spans several runs: {sorted(run_ids)}"
    return run_ids.pop()


def load_run_linear() -> tuple[tuple[Element, ...], tuple[TraceEvent, ...]]:
    """Read the `run_linear` corpus case. Read as data; never executed."""
    path = FIXTURES / "mode_a" / "run_linear" / "expected.json"
    if not path.is_file():  # pragma: no cover - corpus case is card 8's to provide
        pytest.skip("fixture tests/fixtures/mode_a/run_linear/expected.json is not present")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    elements = tuple(
        Element(
            id=item["id"],
            kind=ElementKind(item["kind"]),
            name=item["name"],
            qualname=item.get("qualname", ""),
            module=item["module"],
            span=SourceSpan(path=item["span"]["path"], line=item["span"]["line"]),
            provenance=Provenance(
                method=Method(item["provenance"]["method"]),
                confidence=Confidence(item["provenance"]["confidence"]),
            ),
            content_hash=item.get("content_hash", ""),
        )
        for item in data["elements"]
    )
    events = tuple(
        make_event(
            item["event_id"],
            item["element_id"],
            EventKind(item["kind"]),
            item["sequence"],
            run_id=item["run_id"],
            caller=item.get("caller_event_id", ""),
            depth=item.get("depth", 0),
            values={
                key: ValueCapture(
                    status=CaptureStatus(value["status"]),
                    repr_text=value["repr_text"],
                    type_name=value.get("type_name", ""),
                )
                for key, value in item.get("values", {}).items()
            },
        )
        for item in data["events"]
    )
    return elements, events


# ---------------------------------------------------------------------------
# The registry: loading, validation, locations
# ---------------------------------------------------------------------------


SPEC = """\
# owner-confirmed intents
version: 1
intents:
  - element_id: pkg.mod::score
    status: CONFIRMED
    statement: "Returns an integer score in 0..100."
    invariants:
      - returns.type == int
      - returns.value >= 0
      - returns.value <= 100
    expected_writes:
      - "@feature:score"
  - element_id: pkg.mod::gate
    status: PROPOSED
    statement: Gates the cascade.
    expected_reads: ["@feature:score"]
"""


def test_registry_loads_owner_confirmed_intents(tmp_path: Path) -> None:
    path = tmp_path / "intents.yaml"
    path.write_text(SPEC, encoding="utf-8")
    registry = load_registry(path)

    assert registry.present is True
    assert [item.element_id for item in registry.intents] == ["pkg.mod::gate", "pkg.mod::score"]
    score = registry.for_element("pkg.mod::score")[0]
    assert score.status is IntentStatus.CONFIRMED
    assert score.invariants == ("returns.type == int", "returns.value >= 0", "returns.value <= 100")
    assert score.expected_writes == ("@feature:score",)
    assert registry.for_element("pkg.mod::gate")[0].status is IntentStatus.PROPOSED
    assert registry.for_element("pkg.mod::gate")[0].expected_reads == ("@feature:score",)
    assert registry.issues == ()
    # Every intent records where in the spec it came from.
    assert score.provenance is not None and score.provenance.span is not None
    assert score.provenance.span.path == str(path)
    assert score.provenance.span.line == 4


def test_parse_registry_text_takes_a_document_already_in_memory() -> None:
    registry = parse_registry_text(SPEC, "docs/intents.yaml")
    assert registry.present is True
    assert len(registry.intents) == 2
    assert registry.issues == ()
    assert registry.unknown_check_performed is False
    assert registry.intents[1].provenance is not None
    assert registry.intents[1].provenance.span == SourceSpan(path="docs/intents.yaml", line=4)


def test_absent_spec_is_a_reported_state_not_an_error() -> None:
    for value in (None, "", "none", "None"):
        registry = load_registry(value)
        assert registry.present is False
        assert registry.intents == ()
        assert registry.issues == ()


def test_missing_spec_file_is_reported_with_location(tmp_path: Path) -> None:
    registry = load_registry(tmp_path / "nope.yaml")
    assert registry.present is False
    assert len(registry.issues) == 1
    issue = registry.issues[0]
    assert issue.reason is UnresolvedReason.MISSING_TARGET
    assert issue.span.path.endswith("nope.yaml")


def test_unknown_element_id_is_reported_never_skipped(tmp_path: Path) -> None:
    path = tmp_path / "intents.yaml"
    path.write_text(SPEC, encoding="utf-8")
    registry = load_registry(path, known_element_ids=["pkg.mod::score"])

    issues = [issue for issue in registry.issues if issue.reason is UnresolvedReason.MISSING_TARGET]
    assert len(issues) == 1
    assert "pkg.mod::gate" in issues[0].description
    assert issues[0].span.line == 13
    assert issues[0].candidate_ids == ("pkg.mod::gate",)
    assert registry.unknown_check_performed is True
    # The intent is still loaded: the element may exist and the static pass missed it.
    assert registry.for_element("pkg.mod::gate")


def test_duplicate_intent_for_one_element_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "intents.yaml"
    path.write_text(
        "intents:\n"
        "  - element_id: a::b\n"
        "    status: CONFIRMED\n"
        "    statement: one\n"
        "  - element_id: a::b\n"
        "    status: CONFIRMED\n"
        "    statement: two\n",
        encoding="utf-8",
    )
    registry = load_registry(path)
    reasons = {issue.reason for issue in registry.issues}
    assert UnresolvedReason.AMBIGUOUS in reasons
    assert UnresolvedReason.ID_COLLISION in reasons
    assert registry.ambiguous_element_ids == ("a::b",)
    ambiguous = [issue for issue in registry.issues if issue.reason is UnresolvedReason.AMBIGUOUS][0]
    assert ambiguous.span.line == 5


@pytest.mark.parametrize(
    ("body", "line", "needle"),
    [
        ("intents:\n  - status: CONFIRMED\n    statement: x\n", 2, "element_id"),
        ("intents:\n  - element_id: a::b\n    statement: x\n", 2, "status"),
        ("intents:\n  - element_id: a::b\n    status: CONFIRMED\n", 2, "statement"),
        (
            "intents:\n  - element_id: a::b\n    status: MAYBE\n    statement: x\n",
            3,
            "CONFIRMED",
        ),
        (
            "intents:\n  - element_id: a::b\n    status: CONFIRMED\n    statement: x\n"
            "    invariant:\n      - returns.type == int\n",
            5,
            "unknown key",
        ),
        (
            "intents:\n  - element_id: a::b\n    status: CONFIRMED\n    statement: x\n"
            "    invariants: notalist\n",
            5,
            "list of strings",
        ),
    ],
)
def test_malformed_entry_is_reported_with_location(
    tmp_path: Path, body: str, line: int, needle: str
) -> None:
    path = tmp_path / "intents.yaml"
    path.write_text(body, encoding="utf-8")
    registry = load_registry(path)

    assert registry.intents == (), "a malformed entry must not be loaded as if it were valid"
    assert registry.issues, "a malformed entry must be reported, never skipped"
    issue = registry.issues[0]
    assert issue.reason is UnresolvedReason.SYNTAX_ERROR
    assert issue.span.path == str(path)
    assert issue.span.line == line
    assert needle in issue.description


@pytest.mark.parametrize(
    ("body", "line"),
    [
        ("intents: &anchor\n  - element_id: a::b\n", 1),
        ("intents:\n  - element_id: a::b\n    statement: *alias\n    status: CONFIRMED\n", 3),
        ("intents:\n  - element_id: a::b\n    statement: !!str x\n    status: CONFIRMED\n", 3),
        ("intents:\n  - element_id: a::b\n    statement: |\n      two\n      lines\n", 3),
        ("intents:\n  - element_id: a::b\n    statement: >\n      folded\n", 3),
        ("intents:\n  - element_id: a::b\n    statement: {a: 1}\n", 3),
        ("intents:\n  - element_id: a::b\n    invariants: [a, [b]]\n", 3),
        ("---\nintents: []\n", 1),
    ],
)
def test_parser_refuses_unsupported_yaml_constructs(tmp_path: Path, body: str, line: int) -> None:
    """An anchor, alias, tag, block scalar or flow mapping is a located refusal.

    Reading `statement: *common` as the literal text `*common` would silently
    misrepresent what the owner wrote, which is worse than refusing the file.
    """
    path = tmp_path / "intents.yaml"
    path.write_text(body, encoding="utf-8")
    registry = load_registry(path)
    assert registry.intents == ()
    assert registry.issues[0].reason is UnresolvedReason.SYNTAX_ERROR
    assert registry.issues[0].span.line == line
    assert "supported" in registry.issues[0].description


def test_duplicate_key_inside_an_entry_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "intents.yaml"
    path.write_text(
        "intents:\n"
        "  - element_id: a::b\n"
        "    status: CONFIRMED\n"
        "    statement: one\n"
        "    statement: two\n",
        encoding="utf-8",
    )
    registry = load_registry(path)
    assert registry.intents == ()
    assert registry.rejected_element_ids == ("a::b",)
    assert any("duplicate key" in issue.description for issue in registry.issues)


def test_missing_status_is_never_read_as_owner_confirmation(tmp_path: Path) -> None:
    path = tmp_path / "intents.yaml"
    path.write_text("intents:\n  - element_id: a::b\n    statement: x\n", encoding="utf-8")
    registry = load_registry(path)
    assert registry.intents == ()
    assert registry.rejected_element_ids == ("a::b",)
    assert "never be read as owner confirmation" in registry.issues[0].description


def test_syntax_error_reports_its_line(tmp_path: Path) -> None:
    path = tmp_path / "intents.yaml"
    path.write_text("intents:\n  - element_id: a::b\n\tstatus: CONFIRMED\n", encoding="utf-8")
    registry = load_registry(path)
    assert registry.issues[0].reason is UnresolvedReason.SYNTAX_ERROR
    assert registry.issues[0].span.line == 3


def test_undecodable_spec_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "intents.yaml"
    path.write_bytes(b"intents:\n  - element_id: \xff\xfe\n")
    registry = load_registry(path)
    assert registry.issues[0].reason is UnresolvedReason.DECODE_ERROR


def test_issue_ids_are_unique_and_sorted(tmp_path: Path) -> None:
    path = tmp_path / "intents.yaml"
    path.write_text(
        "intents:\n"
        "  - element_id: a::b\n"
        "    status: NOPE\n"
        "    statement: \n"
        "  - element_id: a::c\n"
        "    status: NOPE\n"
        "    statement: \n",
        encoding="utf-8",
    )
    registry = load_registry(path)
    ids = [issue.id for issue in registry.issues]
    assert len(ids) == len(set(ids))
    assert ids == sorted(ids)
    assert issues_jsonl(registry.issues) == issues_jsonl(registry.issues)


def test_registry_parse_is_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "intents.yaml"
    path.write_text(SPEC, encoding="utf-8")
    first = load_registry(path)
    second = load_registry(path)
    assert intents_jsonl(first.intents) == intents_jsonl(second.intents)
    assert issues_jsonl(first.issues) == issues_jsonl(second.issues)


# ---------------------------------------------------------------------------
# Proposed intents
# ---------------------------------------------------------------------------


def test_proposed_intents_are_never_confirmed_and_carry_no_invariants() -> None:
    elements = (
        make_element("m::compute_score", docstring="Compute the score.\n\nMore prose."),
        make_element("m::gate"),
    )
    proposals = propose_intents(elements)
    assert [item.element_id for item in proposals] == ["m::compute_score", "m::gate"]
    for proposal in proposals:
        assert proposal.status is IntentStatus.PROPOSED
        assert proposal.invariants == ()
        assert proposal.provenance is not None
        assert proposal.provenance.method is Method.NAME_HEURISTIC
        assert proposal.provenance.confidence is Confidence.HEURISTIC
        assert "not owner-confirmed" in proposal.provenance.note
    assert proposals[0].statement == "Compute the score."


# ---------------------------------------------------------------------------
# FIXTURES.md: the ali_* cases
# ---------------------------------------------------------------------------


def test_ali_aligned() -> None:
    """A confirmed intent met by observation."""
    elements, events = load_run_linear()
    confirmed = intent(
        "run_linear::step_two",
        statement="Adds two to its argument.",
        invariants=("returns.type == int", "returns.value == 3", "runs after run_linear::step_one"),
    )
    engine = AlignmentEngine(elements=elements)
    verdicts = by_element(engine.judge([confirmed], events))

    verdict = verdicts["run_linear::step_two"]
    assert verdict.verdict is Verdict.ALIGNED
    assert verdict.intent_id == confirmed.id
    assert verdict.provenance.method is Method.RUNTIME_OBSERVED
    assert verdict.provenance.run_id == run_id_of(events)
    returned = event_id_of(events, "run_linear::step_two", EventKind.RETURN)
    assert returned in verdict.evidence_ids, "the verdict cites the RETURN it judged"
    assert verdict.provenance.event_ids
    assert verdict.provenance.confidence is Confidence.CERTAIN


def test_ali_misaligned_names_expectation_and_contradiction() -> None:
    """MISALIGNED names the specific expectation and the specific observation."""
    elements, events = load_run_linear()
    confirmed = intent(
        "run_linear::step_one",
        statement="Returns the constant two.",
        invariants=("returns.value == 2",),
    )
    engine = AlignmentEngine(elements=elements)
    verdict = by_element(engine.judge([confirmed], events))["run_linear::step_one"]

    assert verdict.verdict is Verdict.MISALIGNED
    assert verdict.expectation == "invariant 'returns.value == 2'"
    assert "observed value 1" in verdict.observation
    contradicting = event_id_of(events, "run_linear::step_one", EventKind.RETURN)
    assert contradicting in verdict.observation, "the contradiction names its event"
    assert verdict.evidence_ids == (contradicting,)
    assert verdict.provenance.method is Method.RUNTIME_OBSERVED
    assert verdict.provenance.event_ids == (contradicting,)
    assert verdict.provenance.model_id == ""


def test_ali_not_exercised_is_not_aligned() -> None:
    """An element no scenario ran is NOT_EXERCISED, not ALIGNED."""
    elements, events = load_run_linear()
    elements = elements + (make_element("run_linear::never_called"),)
    confirmed = intent(
        "run_linear::never_called",
        statement="Returns 1.",
        invariants=("returns.value == 1",),
    )
    engine = AlignmentEngine(elements=elements)
    verdict = by_element(engine.judge([confirmed], events))["run_linear::never_called"]

    assert verdict.verdict is Verdict.NOT_EXERCISED
    assert verdict.verdict is not Verdict.ALIGNED
    assert verdict.evidence_ids == ()
    assert f"no event in run {run_id_of(events)}" in verdict.observation
    assert "NOT_EXERCISED is not ALIGNED" in verdict.observation
    assert verdict.intent_id == confirmed.id


def test_ali_not_exercised_when_nothing_ran_at_all() -> None:
    confirmed = intent("m::f", invariants=("returns.value == 1",))
    verdicts = AlignmentEngine().judge([confirmed], [])
    assert [item.verdict for item in verdicts] == [Verdict.NOT_EXERCISED]


def test_ali_no_intent() -> None:
    """An element with no intent is NO_INTENT: not a pass and not a failure."""
    elements, events = load_run_linear()
    confirmed = intent("run_linear::step_one", invariants=("returns.value == 1",))
    engine = AlignmentEngine(elements=elements)
    verdicts = by_element(engine.judge([confirmed], events))

    assert verdicts["run_linear::main"].verdict is Verdict.NO_INTENT
    assert verdicts["run_linear::main"].intent_id == ""
    assert "no intent is registered" in verdicts["run_linear::main"].observation
    assert verdicts["run_linear::step_one"].verdict is Verdict.ALIGNED
    assert {verdict.verdict for verdict in verdicts.values()} == {
        Verdict.ALIGNED,
        Verdict.NO_INTENT,
    }


def test_ali_proposed_not_binding() -> None:
    """A PROPOSED intent cannot ground ALIGNED or MISALIGNED."""
    elements, events = load_run_linear()
    # Identical expectations, one confirmed and one proposed: only the status differs.
    checkable = ("returns.type == int", "returns.value == 1")
    proposed = intent(
        "run_linear::step_one", status=IntentStatus.PROPOSED, invariants=checkable
    )
    contradicted = intent(
        "run_linear::step_two", status=IntentStatus.PROPOSED, invariants=("returns.value == 999",)
    )
    engine = AlignmentEngine(elements=elements)
    verdicts = by_element(engine.judge([proposed, contradicted], events))

    for element_id in ("run_linear::step_one", "run_linear::step_two"):
        verdict = verdicts[element_id]
        assert verdict.verdict is Verdict.UNVERIFIABLE
        assert verdict.verdict not in (Verdict.ALIGNED, Verdict.MISALIGNED)
        assert "PROPOSED" in verdict.observation
        assert "never ground ALIGNED or MISALIGNED" in verdict.observation


def test_proposed_intents_from_docstrings_never_produce_a_judgement() -> None:
    elements, events = load_run_linear()
    proposals = propose_intents(elements)
    verdicts = AlignmentEngine(elements=elements).judge(proposals, events)
    assert {verdict.verdict for verdict in verdicts} == {Verdict.UNVERIFIABLE}


# ---------------------------------------------------------------------------
# "Unchecked is not aligned"
# ---------------------------------------------------------------------------


def test_prose_only_intent_is_unverifiable_not_aligned() -> None:
    elements, events = load_run_linear()
    confirmed = intent("run_linear::step_one", statement="Kicks off the cascade sensibly.")
    verdict = by_element(AlignmentEngine(elements=elements).judge([confirmed], events))[
        "run_linear::step_one"
    ]
    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert "nothing to check it against" in verdict.observation
    assert verdict.evidence_ids


def test_one_unparseable_invariant_blocks_aligned() -> None:
    elements, events = load_run_linear()
    confirmed = intent(
        "run_linear::step_one",
        invariants=("returns.value == 1", "is basically sensible"),
    )
    verdict = by_element(AlignmentEngine(elements=elements).judge([confirmed], events))[
        "run_linear::step_one"
    ]
    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert "is basically sensible" in verdict.expectation
    assert "1 of 2 expectations held" in verdict.observation


def test_a_contradiction_outranks_an_unverifiable_expectation() -> None:
    elements, events = load_run_linear()
    confirmed = intent(
        "run_linear::step_one",
        invariants=("nonsense expectation", "returns.value == 7"),
    )
    verdict = by_element(AlignmentEngine(elements=elements).judge([confirmed], events))[
        "run_linear::step_one"
    ]
    assert verdict.verdict is Verdict.MISALIGNED
    assert verdict.expectation == "invariant 'returns.value == 7'"
    # The unrecognised invariant is still named: a contradiction outranking it must
    # not bury the fact that one expectation went unchecked.
    assert "UNVERIFIABLE: invariant 'nonsense expectation'" in verdict.provenance.note


def test_missing_captured_value_is_unverifiable() -> None:
    events = (make_event("e1", "m::f", EventKind.CALL, 1),)
    confirmed = intent("m::f", invariants=("returns.value == 1",))
    verdict = AlignmentEngine().judge([confirmed], events)[0]
    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert "no captured value named" in verdict.observation


@pytest.mark.parametrize(
    "status", [CaptureStatus.SUMMARIZED, CaptureStatus.REDACTED, CaptureStatus.DROPPED]
)
def test_a_partial_capture_never_grounds_a_value_verdict(status: CaptureStatus) -> None:
    capture = ValueCapture(
        status=status, repr_text="<frame of 3 rows>", type_name="DataFrame", original_size=10_000
    )
    events = (
        make_event(
            "e1", "m::f", EventKind.RETURN, 1, values={"return_value": capture}
        ),
    )
    for expectation in ("returns.value == 1", "returns.value == 99999"):
        verdict = AlignmentEngine().judge([intent("m::f", invariants=(expectation,))], events)[0]
        assert verdict.verdict is Verdict.UNVERIFIABLE
        assert status.value in verdict.observation
        assert "cannot confirm or contradict" in verdict.observation


def test_type_check_still_works_on_a_summarized_capture() -> None:
    capture = ValueCapture(
        status=CaptureStatus.SUMMARIZED, repr_text="<big>", type_name="DataFrame", original_size=99
    )
    events = (make_event("e1", "m::f", EventKind.RETURN, 1, values={"return_value": capture}),)
    verdict = AlignmentEngine().judge(
        [intent("m::f", invariants=("returns.type == DataFrame",))], events
    )[0]
    assert verdict.verdict is Verdict.ALIGNED
    verdict = AlignmentEngine().judge(
        [intent("m::f", invariants=("returns.type == Series",))], events
    )[0]
    assert verdict.verdict is Verdict.MISALIGNED
    assert "observed type 'DataFrame'" in verdict.observation


def test_an_invariant_must_hold_on_every_observation() -> None:
    events = (
        make_event("e1", "m::f", EventKind.RETURN, 1, values={"return_value": full("5", "int")}),
        make_event("e2", "m::f", EventKind.RETURN, 2, values={"return_value": full("-1", "int")}),
    )
    verdict = AlignmentEngine().judge([intent("m::f", invariants=("returns.value >= 0",))], events)[0]
    assert verdict.verdict is Verdict.MISALIGNED
    assert "e2" in verdict.observation
    assert verdict.evidence_ids == ("e2",)


# ---------------------------------------------------------------------------
# The expectation language
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("returns.type == int", CheckKind.TYPE),
        ("returns.value <= 100", CheckKind.VALUE),
        ("returns.status == FULL", CheckKind.CAPTURE_STATUS),
        ("arg.x.value > 0", CheckKind.VALUE),
        ("values.score.type in (int, float)", CheckKind.TYPE),
        ("returns.value in (1, 2)", CheckKind.VALUE),
        ("calls m::g", CheckKind.CALLS),
        ("not calls m::g", CheckKind.NOT_CALLS),
        ("does not call m::g", CheckKind.NOT_CALLS),
        ("runs before m::g", CheckKind.RUNS_BEFORE),
        ("runs after m::g", CheckKind.RUNS_AFTER),
    ],
)
def test_parse_check_accepts_the_language(text: str, kind: CheckKind) -> None:
    assert parse_check(text).kind is kind


@pytest.mark.parametrize(
    "text",
    [
        "",
        "the score should be sensible",
        "returns.value",
        "returns.magnitude == 3",
        "__import__('os').system('x')",
        "returns.value == 1.5",
        "returns.value == (1, 2)",
    ],
)
def test_parse_check_refuses_what_it_cannot_check(text: str) -> None:
    check = parse_check(text)
    assert check.kind is CheckKind.UNPARSEABLE
    assert check.reason


def test_argument_invariants_read_call_events() -> None:
    events = (
        make_event("e1", "m::f", EventKind.CALL, 1, values={"x": full("4", "int")}),
        make_event("e2", "m::f", EventKind.RETURN, 2, values={"return_value": full("6", "int")}),
    )
    verdict = AlignmentEngine().judge([intent("m::f", invariants=("arg.x.value > 0",))], events)[0]
    assert verdict.verdict is Verdict.ALIGNED
    assert verdict.evidence_ids == ("e1",)


def test_string_values_compare_against_the_captured_repr() -> None:
    events = (
        make_event(
            "e1", "m::f", EventKind.RETURN, 1, values={"return_value": full("'BUY'", "str")}
        ),
    )
    aligned = AlignmentEngine().judge(
        [intent("m::f", invariants=('returns.value == "BUY"',))], events
    )[0]
    assert aligned.verdict is Verdict.ALIGNED
    misaligned = AlignmentEngine().judge(
        [intent("m::f", invariants=('returns.value == "SELL"',))], events
    )[0]
    assert misaligned.verdict is Verdict.MISALIGNED
    assert "observed value 'BUY'" in misaligned.observation


def test_ordered_comparison_on_a_non_integer_is_unverifiable() -> None:
    events = (
        make_event("e1", "m::f", EventKind.RETURN, 1, values={"return_value": full("'BUY'", "str")}),
    )
    verdict = AlignmentEngine().judge([intent("m::f", invariants=("returns.value >= 0",))], events)[0]
    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert "not an integer" in verdict.observation


# ---------------------------------------------------------------------------
# Structural expectations, and the conservative ladder
# ---------------------------------------------------------------------------


CALL_EVENTS = (
    make_event("e1", "m::f", EventKind.CALL, 1),
    make_event("e2", "m::g", EventKind.CALL, 2, caller="e1", depth=1),
)


def test_calls_invariant_confirmed_by_runtime() -> None:
    verdict = AlignmentEngine().judge([intent("m::f", invariants=("calls m::g",))], CALL_EVENTS)
    assert by_element(verdict)["m::f"].verdict is Verdict.ALIGNED
    assert by_element(verdict)["m::f"].evidence_ids == ("e2",)


def test_calls_invariant_without_a_static_graph_is_unverifiable_not_misaligned() -> None:
    events = (make_event("e1", "m::f", EventKind.CALL, 1),)
    verdict = AlignmentEngine().judge([intent("m::f", invariants=("calls m::h",))], events)[0]
    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert "absence in one run is not a contradiction" in verdict.observation


def test_calls_invariant_present_statically_but_unobserved_is_unverifiable() -> None:
    edge = Edge(
        id="edge1",
        kind=EdgeKind.CALLS,
        source_id="m::f",
        target_id="m::h",
        provenance=Provenance(method=Method.SCOPE_LOOKUP, confidence=Confidence.RESOLVED),
    )
    events = (make_event("e1", "m::f", EventKind.CALL, 1),)
    engine = AlignmentEngine(edges=[edge])
    verdict = engine.judge([intent("m::f", invariants=("calls m::h",))], events)[0]
    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert "edge1" in verdict.evidence_ids


def test_calls_invariant_contradicted_by_both_evidence_sources() -> None:
    edge = Edge(
        id="edge1",
        kind=EdgeKind.CALLS,
        source_id="m::f",
        target_id="m::other",
        provenance=Provenance(method=Method.SCOPE_LOOKUP, confidence=Confidence.RESOLVED),
    )
    events = (make_event("e1", "m::f", EventKind.CALL, 1),)
    engine = AlignmentEngine(edges=[edge])
    verdict = engine.judge([intent("m::f", invariants=("calls m::h",))], events)[0]
    assert verdict.verdict is Verdict.MISALIGNED
    assert "no CALLS edge" in verdict.observation
    assert verdict.evidence_ids == ("e1",)
    assert verdict.provenance.confidence is Confidence.PROBABLE


def test_not_calls_invariant_names_the_offending_event() -> None:
    verdict = by_element(
        AlignmentEngine().judge([intent("m::f", invariants=("not calls m::g",))], CALL_EVENTS)
    )["m::f"]
    assert verdict.verdict is Verdict.MISALIGNED
    assert "e2" in verdict.observation
    assert verdict.provenance.confidence is Confidence.CERTAIN


def test_absence_in_one_run_is_only_probable() -> None:
    events = (make_event("e1", "m::f", EventKind.CALL, 1),)
    verdict = AlignmentEngine().judge([intent("m::f", invariants=("not calls m::g",))], events)[0]
    assert verdict.verdict is Verdict.ALIGNED
    assert verdict.provenance.confidence is Confidence.PROBABLE
    assert "one run cannot prove absence" in verdict.observation


def test_order_invariants() -> None:
    aligned = by_element(
        AlignmentEngine().judge([intent("m::f", invariants=("runs before m::g",))], CALL_EVENTS)
    )["m::f"]
    assert aligned.verdict is Verdict.ALIGNED
    misaligned = by_element(
        AlignmentEngine().judge([intent("m::f", invariants=("runs after m::g",))], CALL_EVENTS)
    )["m::f"]
    assert misaligned.verdict is Verdict.MISALIGNED
    assert "the opposite order" in misaligned.observation
    assert set(misaligned.evidence_ids) == {"e1", "e2"}


def test_order_invariant_against_an_unexercised_element_is_unverifiable() -> None:
    events = (make_event("e1", "m::f", EventKind.CALL, 1),)
    verdict = AlignmentEngine().judge([intent("m::f", invariants=("runs before m::z",))], events)[0]
    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert "not exercised" in verdict.observation


def test_expected_write_observed_as_a_feature_event() -> None:
    events = (
        make_event("e1", "m::f", EventKind.CALL, 1),
        make_event(
            "e2",
            feature_id("score"),
            EventKind.FEATURE_WRITE,
            2,
            caller="e1",
            values={"value": full("7", "int")},
        ),
    )
    verdict = by_element(
        AlignmentEngine().judge([intent("m::f", writes=("@feature:score",))], events)
    )["m::f"]
    assert verdict.verdict is Verdict.ALIGNED
    assert verdict.evidence_ids == ("e2",)


def test_expected_write_observed_as_a_value_key_on_the_writer() -> None:
    events = (
        make_event(
            "e1",
            "m::f",
            EventKind.FEATURE_WRITE,
            1,
            values={"score": full("7", "int")},
        ),
    )
    verdict = AlignmentEngine().judge([intent("m::f", writes=("@feature:score",))], events)[0]
    assert verdict.verdict is Verdict.ALIGNED


def test_expected_write_without_lineage_is_unverifiable() -> None:
    events = (make_event("e1", "m::f", EventKind.CALL, 1),)
    verdict = AlignmentEngine().judge([intent("m::f", writes=("@feature:score",))], events)[0]
    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert "absence of evidence is not a contradiction" in verdict.observation


def test_expected_write_contradicted_when_lineage_exists_and_disagrees() -> None:
    lineage = LineageEdge(
        id="lin1",
        kind=LineageKind.COLUMN_WRITE,
        source_id="m::f",
        target_id=feature_id("other"),
        provenance=Provenance(method=Method.DATAFLOW, confidence=Confidence.RESOLVED),
    )
    events = (make_event("e1", "m::f", EventKind.CALL, 1),)
    engine = AlignmentEngine(lineage_edges=[lineage])
    verdict = engine.judge([intent("m::f", writes=("@feature:score",))], events)[0]
    assert verdict.verdict is Verdict.MISALIGNED
    assert "no runtime event and no lineage edge" in verdict.observation


def test_expected_read_observed_in_captured_arguments() -> None:
    events = (
        make_event("e1", "m::f", EventKind.CALL, 1, values={"score": full("7", "int")}),
    )
    verdict = AlignmentEngine().judge([intent("m::f", reads=("@feature:score",))], events)[0]
    assert verdict.verdict is Verdict.ALIGNED


# ---------------------------------------------------------------------------
# Refusals and unusable evidence
# ---------------------------------------------------------------------------


def refused_run() -> RunRecord:
    return RunRecord(
        run_id="run_bad",
        target_hashes={},
        graph_hash="h",
        scenario="s",
        interpreter=".venv-target",
        controls_active={"network_blocked": False},
        blocked=(BlockedAttempt(id="b1", kind="network", detail="socket"),),
        refused=True,
        refusal_reason="outbound network could not be blocked",
    )


def test_a_refused_run_grounds_no_verdict() -> None:
    """Isolation that cannot be guaranteed is a refusal, and a refusal judges nothing.

    Card 11 owns the refusal itself; card 13 must never turn a refused run into an
    ALIGNED verdict, so the whole judgement degrades to UNVERIFIABLE with the
    guarantee that failed quoted in the observation.
    """
    engine = AlignmentEngine(run=refused_run())
    verdicts = engine.judge([intent("m::f", invariants=("returns.value == 1",))], [])
    assert [verdict.verdict for verdict in verdicts] == [Verdict.UNVERIFIABLE]
    assert "refused to start" in verdicts[0].observation
    assert "outbound network could not be blocked" in verdicts[0].observation
    assert "no verdict can rest on this evidence" in verdicts[0].observation


def test_events_spanning_two_runs_ground_no_verdict() -> None:
    events = (
        make_event("e1", "m::f", EventKind.RETURN, 1, values={"return_value": full("1", "int")}),
        make_event(
            "e2",
            "m::f",
            EventKind.RETURN,
            1,
            run_id="run_002",
            values={"return_value": full("1", "int")},
        ),
    )
    verdicts = AlignmentEngine().judge([intent("m::f", invariants=("returns.value == 1",))], events)
    assert verdicts[0].verdict is Verdict.UNVERIFIABLE
    assert "span multiple runs" in verdicts[0].observation


def test_events_from_another_run_than_the_run_record_ground_no_verdict() -> None:
    run = RunRecord(
        run_id="run_999",
        target_hashes={},
        graph_hash="h",
        scenario="s",
        interpreter=".venv-target",
        controls_active={},
        blocked=(),
    )
    events = (
        make_event("e1", "m::f", EventKind.RETURN, 1, values={"return_value": full("1", "int")}),
    )
    verdict = AlignmentEngine(run=run).judge(
        [intent("m::f", invariants=("returns.value == 1",))], events
    )[0]
    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert "does not key onto this run" in verdict.observation


def test_a_rejected_registry_entry_is_unverifiable_not_no_intent(tmp_path: Path) -> None:
    path = tmp_path / "intents.yaml"
    path.write_text("intents:\n  - element_id: m::f\n    statement: x\n", encoding="utf-8")
    registry = load_registry(path)
    events = (make_event("e1", "m::f", EventKind.CALL, 1),)
    verdict = AlignmentEngine(registry=registry).judge(registry.intents, events)[0]
    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert "This is not NO_INTENT" in verdict.observation


def test_duplicate_intents_make_the_element_unverifiable() -> None:
    first = intent("m::f", invariants=("returns.value == 1",), intent_id="@intent:a")
    second = intent("m::f", invariants=("returns.value == 2",), intent_id="@intent:b")
    events = (
        make_event("e1", "m::f", EventKind.RETURN, 1, values={"return_value": full("1", "int")}),
    )
    verdicts = AlignmentEngine().judge([first, second], events)
    assert len(verdicts) == 1
    assert verdicts[0].verdict is Verdict.UNVERIFIABLE
    assert "2 intents claim m::f" in verdicts[0].observation
    assert "would be a guess" in verdicts[0].observation


# ---------------------------------------------------------------------------
# Model escalation: bounded, labelled, never authoritative
# ---------------------------------------------------------------------------


class StubModel:
    """Stands in for `claude-sonnet-5`. No network is touched in any test."""

    def __init__(self, reply: str = "This looks MISALIGNED to me, verdict: MISALIGNED.") -> None:
        self.reply = reply
        self.calls: list[str] = []

    def propose(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self.reply


class ExplodingModel:
    def propose(self, prompt: str) -> str:
        raise AssertionError("the model must not be consulted here")


def test_no_key_means_no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-never-be-read")
    assert model_available() is False
    assert default_model() is None
    with pytest.raises(RuntimeError) as error:
        AnthropicAlignmentModel()
    assert API_KEY_ENV in str(error.value)
    assert "ANTHROPIC_API_KEY is never read" in str(error.value)


def test_everything_works_with_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    elements, events = load_run_linear()
    confirmed = intent("run_linear::step_one", invariants=("returns.value == 1",))
    verdicts = by_element(AlignmentEngine(elements=elements).judge([confirmed], events))
    assert verdicts["run_linear::step_one"].verdict is Verdict.ALIGNED
    assert all(verdict.provenance.model_id == "" for verdict in verdicts.values())


def test_model_may_only_propose_never_decide() -> None:
    model = StubModel()
    events = (make_event("e1", "m::f", EventKind.CALL, 1, values={"x": full("1", "int")}),)
    confirmed = intent("m::f", statement="Should be sensible.")
    verdict = AlignmentEngine(model=model).judge([confirmed], events)[0]

    assert model.calls, "the model is consulted when nothing is checkable"
    assert verdict.verdict is Verdict.UNVERIFIABLE, "a model never produces a verdict"
    assert "MODEL PROPOSAL" in verdict.observation
    assert MODEL_ID in verdict.observation
    assert "not a verdict" in verdict.observation
    assert verdict.provenance.method is Method.MODEL_PROPOSED
    assert verdict.provenance.model_id == MODEL_ID
    assert verdict.provenance.confidence is Confidence.UNKNOWN, "a model never sets a confidence"
    assert verdict.evidence_ids == ("e1",), "a proposal is tied to checkable evidence"
    assert "prompt=" in verdict.provenance.note


def test_the_model_is_not_consulted_when_an_expectation_is_checkable() -> None:
    events = (
        make_event("e1", "m::f", EventKind.RETURN, 1, values={"return_value": full("1", "int")}),
    )
    engine = AlignmentEngine(model=ExplodingModel())
    for expectation in ("returns.value == 1", "returns.value == 2", "not a real invariant"):
        verdict = engine.judge([intent("m::f", invariants=(expectation,))], events)[0]
        assert verdict.provenance.method is Method.RUNTIME_OBSERVED
        assert verdict.provenance.model_id == ""


def test_the_model_is_not_consulted_for_a_proposed_or_unexercised_intent() -> None:
    engine = AlignmentEngine(model=ExplodingModel())
    engine.judge([intent("m::f", status=IntentStatus.PROPOSED)], [])
    engine.judge([intent("m::f")], [])


def test_a_model_failure_never_fails_the_run() -> None:
    class Broken:
        def propose(self, prompt: str) -> str:
            raise TimeoutError("no answer")

    events = (make_event("e1", "m::f", EventKind.CALL, 1),)
    verdict = AlignmentEngine(model=Broken()).judge([intent("m::f")], events)[0]
    assert verdict.verdict is Verdict.UNVERIFIABLE
    assert "escalation failed: TimeoutError" in verdict.observation


def test_model_prose_is_flattened_and_bounded() -> None:
    model = StubModel(reply="line one\n" + "x" * 900)
    events = (make_event("e1", "m::f", EventKind.CALL, 1),)
    verdict = AlignmentEngine(model=model).judge([intent("m::f")], events)[0]
    assert "\n" not in verdict.observation
    assert len(verdict.observation) < 1200


# ---------------------------------------------------------------------------
# Shape, determinism and coverage
# ---------------------------------------------------------------------------


def test_exactly_one_verdict_per_element_with_unique_ids() -> None:
    elements, events = load_run_linear()
    intents = [
        intent("run_linear::step_one", invariants=("returns.value == 1",)),
        intent("run_linear::step_two", status=IntentStatus.PROPOSED),
    ]
    verdicts = AlignmentEngine(elements=elements).judge(intents, events)
    ids = [verdict.id for verdict in verdicts]
    assert len(ids) == len(set(ids)) == len(elements)
    assert ids == sorted(ids)
    prefix = f"@verdict:{run_id_of(events)}:"
    assert all(verdict.id.startswith(prefix) for verdict in verdicts)


def test_every_verdict_carries_intent_evidence_method_and_confidence() -> None:
    elements, events = load_run_linear()
    intents = [
        intent("run_linear::step_one", invariants=("returns.value == 1",)),
        intent("run_linear::step_two", invariants=("returns.value == 9",)),
        intent("run_linear::main", status=IntentStatus.PROPOSED),
    ]
    for verdict in AlignmentEngine(elements=elements).judge(intents, events):
        assert verdict.provenance.method in set(Method)
        assert verdict.provenance.confidence in set(Confidence)
        assert verdict.observation, "a verdict always says what was observed"
        if verdict.verdict in (Verdict.ALIGNED, Verdict.MISALIGNED):
            assert verdict.intent_id
            assert verdict.evidence_ids
            assert verdict.expectation
            assert verdict.provenance.method is Method.RUNTIME_OBSERVED
            assert verdict.provenance.run_id == run_id_of(events)


def test_replaying_the_same_run_twice_is_byte_identical() -> None:
    elements, events = load_run_linear()
    intents = [
        intent("run_linear::step_one", invariants=("returns.value == 1",)),
        intent("run_linear::step_two", invariants=("returns.value == 9", "calls m::z")),
        intent("run_linear::main", status=IntentStatus.PROPOSED),
    ]
    first = AlignmentEngine(elements=elements).judge(intents, events)
    second = AlignmentEngine(elements=list(reversed(elements))).judge(
        list(reversed(intents)), list(reversed(events))
    )
    assert verdicts_jsonl(first) == verdicts_jsonl(second)
    assert verdicts_jsonl(first).endswith("\n")


_DETERMINISM_DRIVER = '''\
"""Emits every card-13 artifact from one fixed input. Run under two hash seeds."""

import sys

sys.path.insert(0, sys.argv[1])

from cascade_map.alignment import (
    AlignmentEngine,
    intents_jsonl,
    issues_jsonl,
    parse_registry_text,
    verdicts_jsonl,
)
from cascade_map.contracts.interfaces import (
    CaptureStatus,
    Confidence,
    Edge,
    EdgeKind,
    Element,
    ElementKind,
    EventKind,
    LineageEdge,
    LineageKind,
    Method,
    Provenance,
    SourceSpan,
    TraceEvent,
    ValueCapture,
    canonical_dumps,
    feature_id,
)

SPEC = """\\
intents:
  - element_id: m::f
    status: CONFIRMED
    statement: Writes the score and calls the gate.
    invariants:
      - returns.type == int
      - returns.value >= 0
      - calls m::g
      - runs before m::g
      - not a real invariant
    expected_writes:
      - "@feature:score"
    expected_reads:
      - "@feature:raw"
  - element_id: m::g
    status: CONFIRMED
    statement: Runs before f.
    invariants:
      - runs before m::f
  - element_id: m::y
    status: CONFIRMED
    statement: Returns one.
    invariants:
      - returns.value == 1
  - element_id: m::h
    status: PROPOSED
    statement: Unknown.
  - element_id: m::dup
    status: CONFIRMED
    statement: one
  - element_id: m::dup
    status: CONFIRMED
    statement: two
  - element_id: m::bad
    statement: no status
"""


def element(element_id):
    return Element(
        id=element_id,
        kind=ElementKind.FUNCTION,
        name=element_id.split("::")[-1],
        qualname=element_id.split("::")[-1],
        module="m",
        span=SourceSpan(path="m.py", line=1),
        provenance=Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN),
        content_hash="h",
    )


def event(event_id, element_id, kind, sequence, caller="", values=None):
    return TraceEvent(
        event_id=event_id,
        run_id="run_001",
        kind=kind,
        element_id=element_id,
        sequence=sequence,
        depth=0,
        caller_event_id=caller,
        values=values or {},
    )


def full(text, type_name):
    return ValueCapture(status=CaptureStatus.FULL, repr_text=text, type_name=type_name)


registry = parse_registry_text(SPEC, "intents.yaml", known_element_ids=["m::f", "m::g"])
elements = [element(name) for name in ("m::f", "m::g", "m::x", "m::y", "m::z")]
events = [
    event("e1", "m::f", EventKind.CALL, 1, values={"raw": full("2", "int")}),
    event("e2", "m::g", EventKind.CALL, 2, caller="e1"),
    event(
        "e3",
        feature_id("score"),
        EventKind.FEATURE_WRITE,
        3,
        caller="e1",
        values={"v": full("7", "int")},
    ),
    event("e4", "m::f", EventKind.RETURN, 4, values={"return_value": full("7", "int")}),
    event("e5", "m::x", EventKind.CALL, 5),
]
edges = [
    Edge(
        id="edge1",
        kind=EdgeKind.CALLS,
        source_id="m::f",
        target_id="m::g",
        provenance=Provenance(method=Method.SCOPE_LOOKUP, confidence=Confidence.RESOLVED),
    )
]
lineage = [
    LineageEdge(
        id="lin1",
        kind=LineageKind.COLUMN_WRITE,
        source_id="m::f",
        target_id=feature_id("score"),
        provenance=Provenance(method=Method.DATAFLOW, confidence=Confidence.RESOLVED),
    )
]

engine = AlignmentEngine(
    registry=registry, elements=elements, edges=edges, lineage_edges=lineage
)
verdicts = engine.judge(registry.intents, events)
sys.stdout.write(verdicts_jsonl(verdicts))
sys.stdout.write(intents_jsonl(registry.intents))
sys.stdout.write(issues_jsonl(registry.issues))
sys.stdout.write(canonical_dumps(engine.coverage().to_dict()))
'''


def test_output_is_byte_identical_across_processes_with_different_hash_seeds(
    tmp_path: Path,
) -> None:
    """Determinism across processes, not merely across two calls in one process.

    Set iteration order is stable within a process and varies between processes, so
    an in-process comparison cannot see the one mistake most likely to break
    constraint 4 here: emitting a set without sorting it. Three subprocesses, one
    with hashing disabled and two with different random seeds, must agree byte for
    byte on every artifact this card emits.
    """
    script = tmp_path / "driver.py"
    script.write_text(_DETERMINISM_DRIVER, encoding="utf-8")
    src_root = str(Path(alignment_module.__file__).resolve().parents[1])

    outputs: list[bytes] = []
    for seed in ("0", "1", "524287"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        env["PYTHONDONTWRITEBYTECODE"] = "1"  # never write into the source tree
        env.pop(API_KEY_ENV, None)
        result = subprocess.run(
            [sys.executable, str(script), src_root],
            env=env,
            capture_output=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
        outputs.append(result.stdout)

    assert outputs[0] == outputs[1] == outputs[2]
    # The run must actually exercise the set-bearing paths, or it proves nothing.
    payload = outputs[0]
    for marker in (
        b'"verdict":"MISALIGNED"',
        b'"verdict":"UNVERIFIABLE"',
        b'"verdict":"NO_INTENT"',
        b'"verdict":"NOT_EXERCISED"',
        b"AMBIGUOUS",
        b"MISSING_TARGET",
        b'"evidence_ids":["e1"',
    ):
        assert marker in payload, marker
    # And it must agree with what this process computes from the same input.
    assert payload.decode("utf-8").count("\n") > 10


def test_artifacts_contain_no_floats() -> None:
    elements, events = load_run_linear()
    engine = AlignmentEngine(elements=elements)
    verdicts = engine.judge([intent("run_linear::step_one", invariants=("returns.value == 1",))], events)
    # canonical_dumps raises on any float; serializing at all is the proof.
    assert verdicts_jsonl(verdicts)
    assert canonical_dumps(engine.coverage().to_dict())


def test_coverage_reports_what_was_checkable_and_against_what() -> None:
    elements, events = load_run_linear()
    intents = [
        intent("run_linear::step_one", invariants=("returns.value == 1",)),
        intent("run_linear::step_two", invariants=("returns.value == 9",)),
        intent("run_linear::main", status=IntentStatus.PROPOSED),
        intent("run_linear::ghost", invariants=("returns.value == 1",)),
    ]
    engine = AlignmentEngine(elements=elements)
    engine.judge(intents, events)
    coverage: Coverage = engine.coverage()

    assert coverage.run_id == run_id_of(events)
    assert coverage.intents_total == 4
    assert coverage.intents_confirmed == 3
    assert coverage.intents_proposed == 1
    assert coverage.intents_checked == 2, "only the exercised, confirmed, checkable ones"
    assert coverage.checks_total == 2
    assert coverage.checks_passed == 1
    assert coverage.checks_failed == 1
    assert coverage.verdicts[Verdict.ALIGNED.value] == 1
    assert coverage.verdicts[Verdict.MISALIGNED.value] == 1
    assert coverage.verdicts[Verdict.UNVERIFIABLE.value] == 1
    assert coverage.verdicts[Verdict.NOT_EXERCISED.value] == 1
    assert coverage.evidence["runtime_events"] == len(events)
    assert coverage.evidence["static_edges"] == 0
    assert any("no static call graph" in note for note in coverage.notes)
    assert canonical_dumps(coverage.to_dict())


def test_coverage_counts_unparseable_invariants() -> None:
    events = (make_event("e1", "m::f", EventKind.CALL, 1),)
    engine = AlignmentEngine()
    engine.judge([intent("m::f", invariants=("nonsense",))], events)
    assert engine.coverage().checks_unparseable == 1


def test_coverage_notes_an_absent_spec(tmp_path: Path) -> None:
    engine = AlignmentEngine(registry=load_registry(None), elements=[make_element("m::f")])
    verdicts = engine.judge([], [])
    assert [verdict.verdict for verdict in verdicts] == [Verdict.NO_INTENT]
    assert any("no intent spec" in note for note in engine.coverage().notes)


def test_engine_satisfies_the_alignment_card_protocol() -> None:
    card: AlignmentCard = AlignmentEngine()
    assert card.judge([], []) == ()


def test_end_to_end_from_spec_file(tmp_path: Path) -> None:
    elements, events = load_run_linear()
    path = tmp_path / "intents.yaml"
    path.write_text(
        "version: 1\n"
        "intents:\n"
        "  - element_id: run_linear::step_one\n"
        "    status: CONFIRMED\n"
        "    statement: Returns the constant one.\n"
        "    invariants:\n"
        "      - returns.type == int\n"
        "      - returns.value == 1\n"
        "  - element_id: run_linear::step_two\n"
        "    status: CONFIRMED\n"
        "    statement: Adds two.\n"
        "    invariants:\n"
        "      - returns.value == 99\n"
        "      - runs after run_linear::step_one\n",
        encoding="utf-8",
    )
    registry = load_registry(path, known_element_ids=[element.id for element in elements])
    assert registry.issues == ()

    engine = AlignmentEngine(registry=registry, elements=elements)
    verdicts = by_element(engine.judge(registry.intents, events))
    assert verdicts["run_linear::step_one"].verdict is Verdict.ALIGNED
    assert verdicts["run_linear::step_two"].verdict is Verdict.MISALIGNED
    assert verdicts["run_linear::main"].verdict is Verdict.NO_INTENT
    assert intents_jsonl(registry.intents).count("\n") == 2
