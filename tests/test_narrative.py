"""Tests for card 14: the execution narrative.

Built against the contract:

    narrate(events, order_nodes, decisions, run) -> Sequence[NarrativeStep]

with hand-constructed values plus the ``nar_*`` cases from
``docs/design/FIXTURES.md``. Card 14 does not execute anything -- it renders
already-captured trace events and the static structures cards 3 and 11 hand
it.
"""

from __future__ import annotations

from cascade_map.contracts.interfaces import (
    BlockedAttempt,
    CaptureStatus,
    Confidence,
    DecisionPoint,
    EventKind,
    Method,
    OrderKind,
    OrderNode,
    Provenance,
    RunRecord,
    TraceEvent,
    ValueCapture,
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


def _run(run_id: str = "run_001", blocked: tuple[BlockedAttempt, ...] = ()) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        target_hashes={},
        graph_hash="graph_1",
        scenario="scn_1",
        interpreter=".venv-target",
        controls_active={"network": True, "filesystem": True},
        blocked=blocked,
    )


def _narrate(
    events: list[TraceEvent],
    order_nodes: tuple[OrderNode, ...] = (),
    decisions: tuple[DecisionPoint, ...] = (),
    run: RunRecord | None = None,
) -> list:
    return Narrator().narrate(events, order_nodes, decisions, run or _run())


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
# nar_anchored -- every step carries element and/or event IDs
# ---------------------------------------------------------------------------


def test_nar_anchored_every_step_has_an_anchor() -> None:
    steps = _narrate(_linear_events())
    assert steps, "narration must not be empty for a non-empty trace"
    for step in steps:
        assert step.element_ids or step.event_ids, f"unanchored step: {step.text!r}"


def test_nar_anchored_event_backed_steps_have_both_ids() -> None:
    steps = _narrate(_linear_events())
    leaves = [s for s in steps if not s.children]
    for step in leaves:
        assert step.element_ids, f"leaf missing element_ids: {step.text!r}"
        assert step.event_ids, f"leaf missing event_ids: {step.text!r}"


def test_nar_anchored_leaf_ids_trace_back_to_the_source_event() -> None:
    steps = _narrate(_linear_events())
    leaves = [s for s in steps if not s.children]
    by_event = {s.event_ids: s for s in leaves}
    assert ("evt_2",) in by_event
    assert by_event[("evt_2",)].element_ids == ("mod::step_one",)


def test_nar_anchored_phase_summary_covers_its_children() -> None:
    steps = _narrate(_linear_events())
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
    steps = _narrate(events)
    leaves = [s for s in steps if not s.children]
    call_texts = [s for s in leaves if s.text.startswith("Loop:")]
    assert len(call_texts) == 1
    loop_step = call_texts[0]
    assert "1000" in loop_step.text
    assert len(loop_step.event_ids) == 2000
    assert "call_0" in loop_step.text
    assert "call_999" in loop_step.text


def test_nar_loop_summary_below_threshold_is_not_collapsed() -> None:
    events = _loop_events(2)
    steps = _narrate(events)
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
    steps = _narrate(events)
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
    steps = _narrate(events)
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
    steps = _narrate(events)
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
        ev("evt_3", EventKind.RETURN, "mod::risky", 3, 0),
    ]
    steps = _narrate(events)
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
    steps = _narrate(events)
    exc = next(s for s in steps if s.text.startswith("Exception"))
    assert "propagated" in exc.text


def test_nar_absence_unmapped_event_reported_not_dropped() -> None:
    events = [ev("evt_1", EventKind.UNMAPPED, "", 1, 0)]
    steps = _narrate(events)
    leaf = next(s for s in steps if not s.children)
    assert "does not map to any static element" in leaf.text
    assert leaf.event_ids == ("evt_1",)


def test_nar_absence_element_never_entered_is_reported() -> None:
    order_nodes = (
        OrderNode(
            id="on:root",
            kind=OrderKind.SEQUENCE,
            element_ids=(),
            children=("on:seg0",),
        ),
        OrderNode(
            id="on:seg0",
            kind=OrderKind.SEQUENCE,
            element_ids=("mod::entered", "mod::skipped"),
        ),
    )
    events = [ev("evt_1", EventKind.CALL, "mod::entered", 1, 0)]
    steps = _narrate(events, order_nodes=order_nodes)
    skipped = [s for s in steps if s.phase == "not entered" and not s.children]
    assert len(skipped) == 1
    assert skipped[0].element_ids == ("mod::skipped",)
    assert skipped[0].event_ids == ()
    assert "never entered" in skipped[0].text


def test_nar_absence_blocked_side_effect_is_reported() -> None:
    blocked = (
        BlockedAttempt(
            id="blk:1",
            kind="NETWORK",
            detail="outbound connect to 10.0.0.1:443 refused",
            element_id="mod::send_order",
            event_id="",
        ),
    )
    steps = _narrate([], run=_run(blocked=blocked))
    leaf = next(s for s in steps if not s.children)
    assert leaf.phase == "blocked side effects"
    assert "NETWORK" in leaf.text
    assert "outbound connect" in leaf.text
    assert leaf.element_ids == ("mod::send_order",)
    assert leaf.event_ids == ("blk:1",)


def test_nar_absence_run_refused_reports_reason_and_narrates_nothing_else() -> None:
    run = RunRecord(
        run_id="run_002",
        target_hashes={},
        graph_hash="graph_1",
        scenario="scn_1",
        interpreter=".venv-target",
        controls_active={"network": False},
        blocked=(),
        refused=True,
        refusal_reason="network isolation could not be guaranteed",
    )
    steps = Narrator().narrate(_linear_events(), (), (), run)
    assert len(steps) == 1
    assert "network isolation could not be guaranteed" in steps[0].text


# ---------------------------------------------------------------------------
# nar_deterministic -- the same trace gives byte-identical text
# ---------------------------------------------------------------------------


def test_nar_deterministic_same_trace_same_output() -> None:
    events = _linear_events()
    steps_a = _narrate(list(events))
    steps_b = _narrate(list(reversed(events)))  # order in input must not matter
    assert steps_a == steps_b


def test_nar_deterministic_repeated_calls_are_stable() -> None:
    events = _loop_events(50)
    a = _narrate(events)
    b = _narrate(events)
    assert a == b


# ---------------------------------------------------------------------------
# Decision points -- condition, values read, branch taken, branches not taken
# ---------------------------------------------------------------------------


def _decision_setup() -> tuple[list[TraceEvent], tuple[DecisionPoint, ...]]:
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
    decisions = (
        DecisionPoint(
            id="dp:gate",
            element_id="mod::gate",
            condition_source="score > 0.5",
            reads_ids=("mod::gate::score",),
            outcomes=(("approve", "mod::approve_path"), ("reject", "mod::reject_path")),
            is_sink=False,
        ),
    )
    return events, decisions


def test_decision_step_names_condition_values_branch_and_not_taken() -> None:
    events, decisions = _decision_setup()
    steps = _narrate(events, decisions=decisions)
    leaf = next(s for s in steps if not s.children)
    assert "score=0.9" in leaf.text
    assert "score > 0.5" in leaf.text
    assert "took branch 'approve'" in leaf.text
    assert "did not take: reject" in leaf.text


def test_sink_decision_is_tagged_final_decision_phase() -> None:
    events = [
        ev("evt_1", EventKind.DECISION, "mod::gate1", 1, 0, branch_taken="continue"),
        ev("evt_2", EventKind.DECISION, "mod::gate2", 2, 0, branch_taken="reject"),
    ]
    decisions = (
        DecisionPoint(
            id="dp:gate1",
            element_id="mod::gate1",
            condition_source="x",
            reads_ids=(),
            outcomes=(("continue", "mod::gate2"),),
            is_sink=False,
        ),
        DecisionPoint(
            id="dp:gate2",
            element_id="mod::gate2",
            condition_source="y",
            reads_ids=(),
            outcomes=(("reject", "mod::sink"), ("accept", "mod::sink")),
            is_sink=True,
        ),
    )
    steps = _narrate(events, decisions=decisions)
    final = [s for s in steps if s.phase == "final decision" and not s.children]
    assert len(final) == 1
    assert final[0].event_ids == ("evt_2",)
    decision_phase = [s for s in steps if s.phase == "decision logic" and not s.children]
    assert len(decision_phase) == 1
    assert decision_phase[0].event_ids == ("evt_1",)


# ---------------------------------------------------------------------------
# Cascade-phase grouping from order_nodes
# ---------------------------------------------------------------------------


def test_phase_grouping_uses_order_node_segments() -> None:
    order_nodes = (
        OrderNode(id="on:root", kind=OrderKind.SEQUENCE, element_ids=(), children=("on:s0", "on:s1")),
        OrderNode(id="on:s0", kind=OrderKind.SEQUENCE, element_ids=("mod::load",)),
        OrderNode(id="on:s1", kind=OrderKind.SEQUENCE, element_ids=("mod::clean",)),
    )
    events = [
        ev("evt_1", EventKind.CALL, "mod::load", 1, 0),
        ev("evt_2", EventKind.CALL, "mod::clean", 2, 0),
    ]
    steps = _narrate(events, order_nodes=order_nodes)
    by_event = {s.event_ids: s for s in steps if not s.children}
    assert by_event[("evt_1",)].phase == "ingestion"
    assert by_event[("evt_2",)].phase == "data engineering"


def test_phase_grouping_unclassified_element_falls_back_honestly() -> None:
    order_nodes = (
        OrderNode(id="on:root", kind=OrderKind.SEQUENCE, element_ids=(), children=("on:s0",)),
        OrderNode(id="on:s0", kind=OrderKind.SEQUENCE, element_ids=("mod::known",)),
    )
    events = [ev("evt_1", EventKind.CALL, "mod::mystery", 1, 0)]
    steps = _narrate(events, order_nodes=order_nodes)
    leaf = next(s for s in steps if not s.children)
    assert leaf.phase == "execution"


# ---------------------------------------------------------------------------
# Empty trace
# ---------------------------------------------------------------------------


def test_empty_trace_produces_empty_narrative() -> None:
    assert _narrate([]) == []
