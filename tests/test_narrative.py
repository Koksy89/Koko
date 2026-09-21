"""Tests for card 14: the execution narrative.

Built against the contract (``Sequence[TraceEvent]`` in, ``Sequence[NarrativeStep]``
out) with hand-constructed events plus the ``nar_*`` cases from
``docs/design/FIXTURES.md``. Card 14 does not execute anything -- it renders
already-captured trace events.
"""

from __future__ import annotations

from cascade_map.contracts.interfaces import (
    CaptureStatus,
    EventKind,
    Provenance,
    TraceEvent,
    ValueCapture,
    Confidence,
    Method,
)
from cascade_map.narrative import Narrator


def _prov() -> Provenance:
    return Provenance(method=Method.RUNTIME_OBSERVED, confidence=Confidence.CERTAIN)


def ev(
    event_id: str,
    kind: EventKind,
    element_id: str,
    sequence: int,
    depth: int,
    caller_event_id: str = "",
    values: dict[str, ValueCapture] | None = None,
    branch_taken: str = "",
    run_id: str = "run_001",
) -> TraceEvent:
    return TraceEvent(
        event_id=event_id,
        run_id=run_id,
        kind=kind,
        element_id=element_id,
        sequence=sequence,
        depth=depth,
        caller_event_id=caller_event_id,
        values=values or {},
        branch_taken=branch_taken,
        provenance=_prov(),
    )


def _linear_events() -> list[TraceEvent]:
    return [
        ev("evt_1", EventKind.CALL, "mod::main", 1, 0),
        ev("evt_2", EventKind.CALL, "mod::step_one", 2, 1, caller_event_id="evt_1"),
        ev(
            "evt_3",
            EventKind.RETURN,
            "mod::step_one",
            3,
            1,
            caller_event_id="evt_1",
            values={"return_value": ValueCapture(status=CaptureStatus.FULL, repr_text="1", type_name="int")},
        ),
        ev("evt_4", EventKind.CALL, "mod::step_two", 4, 1, caller_event_id="evt_1"),
        ev(
            "evt_5",
            EventKind.RETURN,
            "mod::step_two",
            5,
            1,
            caller_event_id="evt_1",
            values={"return_value": ValueCapture(status=CaptureStatus.FULL, repr_text="3", type_name="int")},
        ),
        ev(
            "evt_6",
            EventKind.RETURN,
            "mod::main",
            6,
            0,
            values={"return_value": ValueCapture(status=CaptureStatus.FULL, repr_text="3", type_name="int")},
        ),
    ]


# ---------------------------------------------------------------------------
# nar_anchored -- every step carries element and event IDs
# ---------------------------------------------------------------------------


def test_nar_anchored_every_step_has_element_and_event_ids() -> None:
    steps = Narrator().narrate(_linear_events())
    assert steps, "narration must not be empty for a non-empty trace"
    for step in steps:
        assert step.element_ids, f"unanchored step (no element_ids): {step.text!r}"
        assert step.event_ids, f"unanchored step (no event_ids): {step.text!r}"


def test_nar_anchored_leaf_ids_trace_back_to_the_source_event() -> None:
    steps = Narrator().narrate(_linear_events())
    leaves = [s for s in steps if not s.children]
    by_event = {s.event_ids: s for s in leaves}
    assert ("evt_2",) in by_event
    assert by_event[("evt_2",)].element_ids == ("mod::step_one",)


def test_nar_anchored_phase_summary_covers_its_children() -> None:
    steps = Narrator().narrate(_linear_events())
    summaries = [s for s in steps if s.children]
    assert summaries
    by_id = {s.id: s for s in steps}
    for summary in summaries:
        child_event_ids = {eid for cid in summary.children for eid in by_id[cid].event_ids}
        assert child_event_ids <= set(summary.event_ids)


# ---------------------------------------------------------------------------
# nar_loop_summary -- a 1000-iteration loop is summarised, not transcribed
# ---------------------------------------------------------------------------


def _loop_events(n: int = 1000) -> list[TraceEvent]:
    events = [ev("evt_0", EventKind.CALL, "mod::runner", 1, 0)]
    seq = 2
    for i in range(n):
        events.append(ev(f"call_{i}", EventKind.CALL, "mod::step", seq, 1, caller_event_id="evt_0"))
        seq += 1
        # last iteration returns a different value -- must be called out.
        repr_text = "99" if i == n - 1 else "0"
        events.append(
            ev(
                f"ret_{i}",
                EventKind.RETURN,
                "mod::step",
                seq,
                1,
                caller_event_id="evt_0",
                values={
                    "return_value": ValueCapture(
                        status=CaptureStatus.FULL, repr_text=repr_text, type_name="int"
                    )
                },
            )
        )
        seq += 1
    events.append(ev("evt_end", EventKind.RETURN, "mod::runner", seq, 0))
    return events


def test_nar_loop_summary_1000_iterations_collapse_to_one_step() -> None:
    events = _loop_events(1000)
    steps = Narrator().narrate(events)
    leaves = [s for s in steps if not s.children]
    # One collapsed loop step, not 1000 CALL steps for mod::step.
    call_texts = [s for s in leaves if s.text.startswith("Loop:")]
    assert len(call_texts) == 1
    loop_step = call_texts[0]
    assert "1000" in loop_step.text
    # All 2000 loop-body events are still anchored, even though not transcribed.
    assert len(loop_step.event_ids) == 2000
    # First and last iterations, and the value-changing last iteration, are named.
    assert "call_0" in loop_step.text
    assert "call_999" in loop_step.text


def test_nar_loop_summary_below_threshold_is_not_collapsed() -> None:
    events = _loop_events(2)
    steps = Narrator().narrate(events)
    leaves = [s for s in steps if not s.children]
    assert not any(s.text.startswith("Loop:") for s in leaves)
    call_steps = [s for s in leaves if s.text == "Called mod::step."]
    assert len(call_steps) == 2


# ---------------------------------------------------------------------------
# nar_absence -- skipped/absent facts appear in the narrative
# ---------------------------------------------------------------------------


def test_nar_absence_summarized_value_is_called_out() -> None:
    events = [
        ev(
            "evt_1",
            EventKind.RETURN,
            "mod::big",
            1,
            0,
            values={
                "return_value": ValueCapture(
                    status=CaptureStatus.SUMMARIZED,
                    repr_text="<DataFrame 1000000 rows>",
                    original_size=50_000_000,
                )
            },
        )
    ]
    steps = Narrator().narrate(events)
    leaf = next(s for s in steps if not s.children)
    assert "SUMMARIZED" in leaf.text
    assert "50000000" in leaf.text


def test_nar_absence_redacted_value_is_called_out() -> None:
    events = [
        ev(
            "evt_1",
            EventKind.FEATURE_WRITE,
            "mod::secret_feature",
            1,
            0,
            values={
                "value": ValueCapture(
                    status=CaptureStatus.REDACTED, repr_text="", reason="marked sensitive"
                )
            },
        )
    ]
    steps = Narrator().narrate(events)
    leaf = next(s for s in steps if not s.children)
    assert "REDACTED" in leaf.text
    assert "marked sensitive" in leaf.text


def test_nar_absence_dropped_value_is_called_out() -> None:
    events = [
        ev(
            "evt_1",
            EventKind.RETURN,
            "mod::f",
            1,
            0,
            values={
                "return_value": ValueCapture(
                    status=CaptureStatus.DROPPED, repr_text="", reason="capture budget exceeded"
                )
            },
        )
    ]
    steps = Narrator().narrate(events)
    leaf = next(s for s in steps if not s.children)
    assert "DROPPED" in leaf.text
    assert "capture budget exceeded" in leaf.text


def test_nar_absence_swallowed_exception_reported() -> None:
    events = [
        ev("evt_1", EventKind.CALL, "mod::risky", 1, 0),
        ev(
            "evt_2",
            EventKind.EXCEPTION,
            "mod::risky",
            2,
            1,
            caller_event_id="evt_1",
            values={"error": ValueCapture(status=CaptureStatus.FULL, repr_text="ValueError('x')")},
        ),
        # execution continues at a shallower-or-equal depth -> swallowed
        ev("evt_3", EventKind.RETURN, "mod::risky", 3, 0),
    ]
    steps = Narrator().narrate(events)
    exc = next(s for s in steps if s.text.startswith("Exception"))
    assert "swallowed" in exc.text


def test_nar_absence_propagated_exception_reported() -> None:
    events = [
        ev("evt_1", EventKind.CALL, "mod::risky", 1, 0),
        ev(
            "evt_2",
            EventKind.EXCEPTION,
            "mod::risky",
            2,
            1,
            caller_event_id="evt_1",
            values={"error": ValueCapture(status=CaptureStatus.FULL, repr_text="ValueError('x')")},
        ),
    ]
    steps = Narrator().narrate(events)
    exc = next(s for s in steps if s.text.startswith("Exception"))
    assert "propagated" in exc.text


def test_nar_absence_unmapped_event_reported_not_dropped() -> None:
    events = [ev("evt_1", EventKind.UNMAPPED, "", 1, 0)]
    steps = Narrator().narrate(events)
    leaf = next(s for s in steps if not s.children)
    assert "does not map to any static element" in leaf.text
    assert leaf.event_ids == ("evt_1",)


# ---------------------------------------------------------------------------
# nar_deterministic -- the same trace gives byte-identical text
# ---------------------------------------------------------------------------


def test_nar_deterministic_same_trace_same_output() -> None:
    events = _linear_events()
    steps_a = Narrator().narrate(list(events))
    steps_b = Narrator().narrate(list(reversed(events)))  # order in input must not matter
    assert steps_a == steps_b


def test_nar_deterministic_repeated_calls_are_stable() -> None:
    events = _loop_events(50)
    a = Narrator().narrate(events)
    b = Narrator().narrate(events)
    assert a == b


# ---------------------------------------------------------------------------
# Decision points -- condition, values read, branch taken
# ---------------------------------------------------------------------------


def test_decision_step_names_condition_values_and_branch() -> None:
    events = [
        ev(
            "evt_1",
            EventKind.DECISION,
            "mod::gate",
            1,
            0,
            values={"score": ValueCapture(status=CaptureStatus.FULL, repr_text="0.9", type_name="float")},
            branch_taken="approve",
        )
    ]
    steps = Narrator().narrate(events)
    leaf = next(s for s in steps if not s.children)
    assert "score=0.9" in leaf.text
    assert "approve" in leaf.text


def test_last_decision_is_tagged_final_decision_phase() -> None:
    events = [
        ev("evt_1", EventKind.DECISION, "mod::gate1", 1, 0, branch_taken="continue"),
        ev("evt_2", EventKind.DECISION, "mod::gate2", 2, 0, branch_taken="reject"),
    ]
    steps = Narrator().narrate(events)
    final = [s for s in steps if s.phase == "final decision" and not s.children]
    assert len(final) == 1
    assert final[0].event_ids == ("evt_2",)
    decision_phase = [s for s in steps if s.phase == "decision logic" and not s.children]
    assert len(decision_phase) == 1
    assert decision_phase[0].event_ids == ("evt_1",)


# ---------------------------------------------------------------------------
# Empty trace
# ---------------------------------------------------------------------------


def test_empty_trace_produces_empty_narrative() -> None:
    assert Narrator().narrate([]) == []
