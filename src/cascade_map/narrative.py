"""Card 14 — the execution narrative.

Renders a recorded run (``Sequence[TraceEvent]``, card 12's output) as an
ordered, anchored account of what executed. See ``NarrativeCard`` in
``cascade_map.contracts.interfaces`` for the binding contract.

## What this module can and cannot derive from ``TraceEvent`` alone

``narrate()`` receives only the event stream -- no static graph (no
``Element``, ``OrderNode``, ``DecisionPoint``, ``CFGEdge``) and no
``RunRecord``. That bounds what "structured by cascade phase" and "report
what did not happen" can honestly mean here:

* Phase is assigned from ``EventKind`` alone, because that is the only
  phase-relevant signal ``TraceEvent`` carries. ``CALL``/``RETURN``/``BRANCH``
  land in a single "execution" bucket: the contract gives no way to tell an
  ingestion call from a data-engineering call without the static cascade
  order card 3 computed. See the contract-change note in the card 14 report.
* "What did not happen" is reported only insofar as the trace itself shows
  it: a value arriving ``SUMMARIZED``/``REDACTED``/``DROPPED``, an exception
  that the trace shows execution continuing past (swallowed) versus one nothing
  follows (propagated), and ``UNMAPPED`` events. Branches *not* taken and
  elements *never entered* require ``DecisionPoint``/``OrderNode`` from card 3,
  which ``narrate()`` does not receive; when an event's own ``branch_taken``
  names an outcome, the branch is reported, but the full menu of outcomes not
  taken is not reconstructable from events alone. Likewise, blocked
  side-effect attempts live on ``RunRecord.blocked``, not on ``TraceEvent``,
  and so cannot be reported unless the tracer surfaces them as trace events
  (e.g. an ``EXCEPTION`` event) -- another contract-change note.

Everything this module *does* emit is anchored: every ``NarrativeStep``
carries the element IDs and event IDs it was built from, and nothing here
infers intent or invents causation the trace does not show (that is card 13).

Determinism: given the same event sequence, ``narrate()`` returns
byte-identical ``NarrativeStep`` text and IDs every time. No wall-clock time,
randomness or iteration over unordered containers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from cascade_map.contracts.interfaces import (
    CaptureStatus,
    EventKind,
    NarrativeStep,
    TraceEvent,
    ValueCapture,
)

__all__ = ["Narrator"]


# ---------------------------------------------------------------------------
# Phase assignment -- derivable purely from EventKind, see module docstring.
# ---------------------------------------------------------------------------

_PHASE_EXECUTION = "execution"
_PHASE_FEATURE = "feature engineering"
_PHASE_DECISION = "decision logic"
_PHASE_FINAL = "final decision"
_PHASE_EXCEPTION = "exception"
_PHASE_UNMAPPED = "unmapped"

_PHASE_ORDER = (
    _PHASE_EXECUTION,
    _PHASE_FEATURE,
    _PHASE_DECISION,
    _PHASE_FINAL,
    _PHASE_EXCEPTION,
    _PHASE_UNMAPPED,
)

_LOOP_THRESHOLD = 3
"""Minimum consecutive same-site calls before they are summarised as a loop
rather than narrated one step per iteration."""


def _base_phase(kind: EventKind) -> str:
    if kind is EventKind.FEATURE_WRITE:
        return _PHASE_FEATURE
    if kind is EventKind.DECISION:
        return _PHASE_DECISION
    if kind is EventKind.EXCEPTION:
        return _PHASE_EXCEPTION
    if kind is EventKind.UNMAPPED:
        return _PHASE_UNMAPPED
    return _PHASE_EXECUTION


def _value_clause(name: str, capture: ValueCapture) -> str:
    """Render one captured value, calling out anything short of FULL."""
    base = f"{name}={capture.repr_text}"
    if capture.type_name:
        base += f" ({capture.type_name})"
    if capture.status is CaptureStatus.FULL:
        return base
    if capture.status is CaptureStatus.SUMMARIZED:
        return f"{base} [SUMMARIZED, original_size={capture.original_size}]"
    if capture.status is CaptureStatus.REDACTED:
        reason = capture.reason or "no reason recorded"
        return f"{base} [REDACTED: {reason}]"
    # DROPPED
    reason = capture.reason or "no reason recorded"
    return f"{name}=<dropped> [DROPPED: {reason}]"


def _values_clause(event: TraceEvent) -> str:
    if not event.values:
        return ""
    parts = [_value_clause(name, event.values[name]) for name in sorted(event.values)]
    return "; ".join(parts)


@dataclass(frozen=True, slots=True)
class _Site:
    element_id: str
    caller_event_id: str
    depth: int


def _site_of(event: TraceEvent) -> _Site:
    return _Site(event.element_id, event.caller_event_id, event.depth)


def _subtree_end(events: list[TraceEvent], call_index: int) -> int:
    """Index (exclusive) of the end of the subtree rooted at events[call_index].

    The subtree is every following event with depth > the call's depth, plus
    the matching RETURN at the same depth if present, up to (but not
    including) the next event at depth <= the call's depth.
    """
    call = events[call_index]
    i = call_index + 1
    while i < len(events) and events[i].depth > call.depth:
        i += 1
    if (
        i < len(events)
        and events[i].depth == call.depth
        and events[i].kind is EventKind.RETURN
        and events[i].element_id == call.element_id
        and events[i].caller_event_id == call.caller_event_id
    ):
        i += 1
    return i


class Narrator:
    """Implements ``NarrativeCard``."""

    def narrate(self, events: Sequence[TraceEvent]) -> list[NarrativeStep]:
        ordered = sorted(events, key=lambda e: e.sequence)
        if not ordered:
            return []
        run_id = ordered[0].run_id
        self._all_events = ordered

        last_decision_event_id = self._last_decision_event_id(ordered)

        leaves: list[NarrativeStep] = []
        i = 0
        while i < len(ordered):
            event = ordered[i]
            if event.kind is EventKind.CALL:
                loop_len = self._loop_run_length(ordered, i)
                if loop_len >= _LOOP_THRESHOLD:
                    step, consumed = self._summarise_loop(ordered, i, loop_len, run_id)
                    leaves.append(step)
                    i += consumed
                    continue
            leaves.append(self._leaf_step(event, run_id, last_decision_event_id))
            i += 1

        return self._group_by_phase(leaves, run_id)

    # -- loop detection -----------------------------------------------------

    def _loop_run_length(self, events: list[TraceEvent], start: int) -> int:
        """Number of consecutive sibling-level repeats of the CALL at *start*.

        Siblings share depth, element_id and caller_event_id. Nested events
        inside each call's subtree do not interrupt the run.
        """
        site = _site_of(events[start])
        count = 0
        i = start
        while i < len(events):
            event = events[i]
            if event.kind is not EventKind.CALL or _site_of(event) != site:
                break
            count += 1
            i = _subtree_end(events, i)
        return count

    def _summarise_loop(
        self, events: list[TraceEvent], start: int, loop_len: int, run_id: str
    ) -> tuple[NarrativeStep, int]:
        site = _site_of(events[start])
        iteration_spans: list[tuple[int, int]] = []
        i = start
        for _ in range(loop_len):
            end = _subtree_end(events, i)
            iteration_spans.append((i, end))
            i = end
        consumed = i - start

        def return_repr(span: tuple[int, int]) -> str | None:
            lo, hi = span
            for idx in range(lo, hi):
                ev = events[idx]
                if (
                    ev.kind is EventKind.RETURN
                    and ev.element_id == site.element_id
                    and ev.caller_event_id == site.caller_event_id
                    and "return_value" in ev.values
                ):
                    return ev.values["return_value"].repr_text
            return None

        reprs = [return_repr(span) for span in iteration_spans]

        changed: list[int] = []
        for idx in range(1, len(reprs)):
            if reprs[idx] is not None and reprs[idx] != reprs[idx - 1]:
                changed.append(idx)

        called_out = sorted({0, loop_len - 1, *changed})

        detail_parts: list[str] = []
        for pos in called_out:
            lo, _hi = iteration_spans[pos]
            call_event = events[lo]
            label = "first" if pos == 0 else ("last" if pos == loop_len - 1 else f"iteration {pos + 1}")
            repr_text = reprs[pos]
            if repr_text is not None:
                detail_parts.append(f"{label} (event {call_event.event_id}) returned {repr_text}")
            else:
                detail_parts.append(f"{label} (event {call_event.event_id})")

        text = (
            f"Loop: {site.element_id} was called {loop_len} times in a row "
            f"with the same caller. " + "; ".join(detail_parts) + "."
        )

        all_event_ids: list[str] = []
        all_element_ids: set[str] = set()
        for lo, hi in iteration_spans:
            for idx in range(lo, hi):
                all_event_ids.append(events[idx].event_id)
                all_element_ids.add(events[idx].element_id)

        step = NarrativeStep(
            id=f"nar:{run_id}:loop:{events[start].event_id}",
            run_id=run_id,
            sequence=0,
            phase=_base_phase(events[start].kind),
            text=text,
            element_ids=tuple(sorted(all_element_ids)),
            event_ids=tuple(all_event_ids),
        )
        return step, consumed

    # -- leaf narration -------------------------------------------------------

    def _last_decision_event_id(self, events: list[TraceEvent]) -> str:
        last = ""
        last_seq = -1
        for event in events:
            if event.kind is EventKind.DECISION and event.sequence > last_seq:
                last = event.event_id
                last_seq = event.sequence
        return last

    def _leaf_step(
        self, event: TraceEvent, run_id: str, last_decision_event_id: str
    ) -> NarrativeStep:
        text = self._render_text(event)
        phase = _base_phase(event.kind)
        if event.kind is EventKind.DECISION and event.event_id == last_decision_event_id:
            phase = _PHASE_FINAL
        return NarrativeStep(
            id=f"nar:{run_id}:{event.event_id}",
            run_id=run_id,
            sequence=0,
            phase=phase,
            text=text,
            element_ids=(event.element_id,),
            event_ids=(event.event_id,),
        )

    def _render_text(self, event: TraceEvent) -> str:
        values = _values_clause(event)
        if event.kind is EventKind.CALL:
            return f"Called {event.element_id}."
        if event.kind is EventKind.RETURN:
            if values:
                return f"Returned from {event.element_id} with {values}."
            return f"Returned from {event.element_id}."
        if event.kind is EventKind.BRANCH:
            branch = event.branch_taken or "<unrecorded>"
            reads = f" (read {values})" if values else ""
            return f"Branch at {event.element_id} took '{branch}'{reads}."
        if event.kind is EventKind.DECISION:
            branch = event.branch_taken or "<unrecorded>"
            reads = f" reads {values}" if values else ""
            return f"Decision at {event.element_id}{reads}; took branch '{branch}'."
        if event.kind is EventKind.FEATURE_WRITE:
            if values:
                return f"Feature write at {event.element_id}: {values}."
            return f"Feature write at {event.element_id}."
        if event.kind is EventKind.EXCEPTION:
            swallowed = self._swallowed
            outcome = "swallowed (execution continued)" if swallowed(event) else "propagated"
            detail = f" -- {values}" if values else ""
            return f"Exception at {event.element_id}{detail} ({outcome})."
        # UNMAPPED
        detail = f" -- {values}" if values else ""
        target = event.element_id or "<no static element>"
        return f"Unmapped event at {target}{detail}: does not map to any static element."

    def _swallowed(self, event: TraceEvent) -> bool:
        # Set on the instance in narrate(); default False if unavailable.
        following = getattr(self, "_all_events", None)
        if following is None:
            return False
        for later in following:
            if later.sequence > event.sequence and later.depth <= event.depth:
                return True
        return False

    # -- phase grouping ------------------------------------------------------

    def _group_by_phase(self, leaves: list[NarrativeStep], run_id: str) -> list[NarrativeStep]:
        by_phase: dict[str, list[NarrativeStep]] = {phase: [] for phase in _PHASE_ORDER}
        for leaf in leaves:
            by_phase.setdefault(leaf.phase, []).append(leaf)

        out: list[NarrativeStep] = []
        seq = 1
        phases_present = [p for p in _PHASE_ORDER if by_phase.get(p)]
        for phase in phases_present:
            children = by_phase[phase]
            child_ids = tuple(c.id for c in children)
            element_ids = tuple(sorted({eid for c in children for eid in c.element_ids}))
            event_ids = tuple(eid for c in children for eid in c.event_ids)
            summary = NarrativeStep(
                id=f"nar:{run_id}:phase:{phase.replace(' ', '_')}",
                run_id=run_id,
                sequence=seq,
                phase=phase,
                text=f"Phase '{phase}': {len(children)} step(s).",
                element_ids=element_ids,
                event_ids=event_ids,
                children=child_ids,
            )
            out.append(summary)
            seq += 1
            for child in children:
                out.append(
                    NarrativeStep(
                        id=child.id,
                        run_id=child.run_id,
                        sequence=seq,
                        phase=child.phase,
                        text=child.text,
                        element_ids=child.element_ids,
                        event_ids=child.event_ids,
                        children=child.children,
                        model_prose=child.model_prose,
                        model_id=child.model_id,
                    )
                )
                seq += 1
        return out
