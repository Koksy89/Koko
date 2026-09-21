"""Card 14 — the execution narrative.

Renders a recorded run as an ordered, anchored account of what executed. See
``NarrativeCard`` in ``cascade_map.contracts.interfaces`` for the binding
contract; its docstring explains why the static structures (``OrderNode``,
``DecisionPoint``, ``RunRecord``) are parameters here rather than fields
denormalised onto ``TraceEvent`` -- one source of truth per fact, the same
reason runtime evidence is an overlay rather than a second graph.

## What each parameter buys this module

* ``events`` (card 12): what ran, in what order, with what values, at each
  depth.
* ``order_nodes`` (card 3): the cascade's *structure*, not its names.
  ``OrderNode`` carries no label field -- nothing in the data says the
  root's first child is "ingestion" rather than "data engineering". This
  module reads the single tree root's ``children``, in order, as the run's
  top-level segments and assigns each element the segment that contains it,
  but names each segment only by its position -- ``"segment 1"``,
  ``"segment 2"``, ... -- never by a guessed cascade-stage name. A confident
  wrong label is worse than an honest position; see ``_phase_name_for_segment``.
  ``EventKind`` still wins for the phases it *does* let us name honestly: a
  feature write is "feature engineering" outright, a decision or an
  exception is its own phase -- the segment lookup only classifies plain
  calls and returns, which carry no such signal.
* ``decisions`` (card 3): ``DecisionPoint.condition_source``, ``reads_ids``
  and ``outcomes`` turn a bare "branch_taken" into the condition as written,
  the branches not taken, and (via ``is_sink``) which decision is the run's
  final one.
* ``run`` (card 11): ``RunRecord.blocked`` is the only place a blocked
  side-effect attempt lives, and ``RunRecord.refused``/``refusal_reason``
  say plainly that nothing ran at all.

Nothing here infers intent or invents causation the trace does not show
(that is card 13).

## The anchoring rule, exactly

* Every step carries at least one anchor (non-empty ``element_ids`` or
  non-empty ``event_ids``), always.
* A step backed by a ``TraceEvent`` carries ``event_ids``, always.
* It also carries ``element_ids`` -- **unless** its event is
  ``EventKind.UNMAPPED`` with an empty ``element_id``. That is the only
  exemption: forcing an element ID onto an event that maps to no static
  element would invent the very mapping card 12 could not make.
* Steps not backed by any ``TraceEvent`` -- "not entered", "blocked",
  "refused" -- carry ``element_ids`` and/or a checkable identifier (a
  ``BlockedAttempt.id`` or the run ID) in place of ``event_ids``, because no
  event exists for them to cite.

Determinism: given the same events, order_nodes, decisions and run,
``narrate()`` returns byte-identical ``NarrativeStep``s every time,
regardless of the input sequences' own ordering.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from cascade_map.contracts.interfaces import (
    CaptureStatus,
    DecisionPoint,
    EventKind,
    NarrativeStep,
    OrderNode,
    RunRecord,
    TraceEvent,
    ValueCapture,
)

__all__ = ["Narrator"]


# ---------------------------------------------------------------------------
# Phase names.
# ---------------------------------------------------------------------------

_PHASE_FEATURE = "feature engineering"
_PHASE_DECISION = "decision logic"
_PHASE_FINAL = "final decision"
_PHASE_EXCEPTION = "exception"
_PHASE_UNMAPPED = "unmapped"
_PHASE_BLOCKED = "blocked side effects"
_PHASE_NOT_ENTERED = "not entered"
_PHASE_UNCLASSIFIED = "execution"
"""A call/return on an element no order-node segment claims. Honest fallback,
not a guess: card 3 simply did not place this element in the cascade order."""

_SEGMENT_RE = re.compile(r"^segment (\d+)$")
"""Matches the positional segment phase names ``_phase_name_for_segment``
produces, so ``_group_by_phase`` can order them numerically without knowing
in advance how many segments a given order-node tree has."""

# Fixed phases, in the order they are presented after the positional
# segments. Segments always come first because they are the cascade's own
# structural order; these five are ordered by how confidently each can be
# named at all (justified names before honest fallbacks before anomalies).
_FIXED_PHASE_ORDER = (
    _PHASE_FEATURE,
    _PHASE_DECISION,
    _PHASE_FINAL,
    _PHASE_UNCLASSIFIED,
    _PHASE_EXCEPTION,
    _PHASE_UNMAPPED,
    _PHASE_BLOCKED,
    _PHASE_NOT_ENTERED,
)

_LOOP_THRESHOLD = 3
"""Minimum consecutive same-site calls before they are summarised as a loop
rather than narrated one step per iteration."""


def _phase_name_for_segment(index: int) -> str:
    """Name a positional cascade segment honestly: by position, not by a
    guessed cascade-stage label. ``OrderNode`` carries no name field, so
    "segment 1" is the only claim this module can make and defend."""
    return f"segment {index + 1}"


def _segment_index_by_element(order_nodes: Sequence[OrderNode]) -> dict[str, int]:
    """Map each element ID to the index of its top-level cascade segment.

    The order-node tree's single root -- a node no other node lists as a
    child -- is read as a SEQUENCE whose ``children``, in order, are the
    cascade's top-level segments. Each segment's element IDs (collected by
    walking its own children recursively) map to that segment's index.

    If the graph has no single unambiguous root (empty, disconnected, or
    more than one root-less node), each root-less node is its own segment,
    taken in the order the caller supplied -- still deterministic, just
    unable to claim a single sequence the way one root's ``children`` can.
    """
    if not order_nodes:
        return {}
    by_id = {node.id: node for node in order_nodes}
    is_child: set[str] = set()
    for node in order_nodes:
        is_child.update(node.children)
    roots = [node for node in order_nodes if node.id not in is_child]
    if not roots:
        roots = list(order_nodes)

    if len(roots) == 1 and roots[0].children:
        segment_roots = [by_id[cid] for cid in roots[0].children if cid in by_id]
    else:
        segment_roots = roots

    mapping: dict[str, int] = {}
    for index, seg_root in enumerate(segment_roots):
        stack = [seg_root]
        seen_nodes: set[str] = set()
        while stack:
            node = stack.pop()
            if node.id in seen_nodes:
                continue
            seen_nodes.add(node.id)
            for element_id in node.element_ids:
                mapping.setdefault(element_id, index)
            for child_id in node.children:
                child = by_id.get(child_id)
                if child is not None:
                    stack.append(child)
    return mapping


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

    def narrate(
        self,
        events: Sequence[TraceEvent],
        order_nodes: Sequence[OrderNode],
        decisions: Sequence[DecisionPoint],
        run: RunRecord,
    ) -> list[NarrativeStep]:
        if run.refused:
            return [self._refusal_step(run)]

        ordered = sorted(events, key=lambda e: e.sequence)
        run_id = run.run_id or (ordered[0].run_id if ordered else "")
        self._all_events = ordered

        segment_by_element = _segment_index_by_element(order_nodes)
        decision_by_element = {d.element_id: d for d in decisions}

        leaves: list[NarrativeStep] = []
        i = 0
        while i < len(ordered):
            event = ordered[i]
            if event.kind is EventKind.CALL:
                loop_len = self._loop_run_length(ordered, i)
                if loop_len >= _LOOP_THRESHOLD:
                    step, consumed = self._summarise_loop(
                        ordered, i, loop_len, run_id, segment_by_element
                    )
                    leaves.append(step)
                    i += consumed
                    continue
            leaves.append(self._leaf_step(event, run_id, segment_by_element, decision_by_element))
            i += 1

        leaves.extend(self._not_entered_steps(order_nodes, ordered, run_id))
        leaves.extend(self._blocked_steps(run, run_id))

        return self._group_by_phase(leaves, run_id)

    # -- refusal --------------------------------------------------------------

    def _refusal_step(self, run: RunRecord) -> NarrativeStep:
        reason = run.refusal_reason or "<no reason recorded>"
        return NarrativeStep(
            id=f"nar:{run.run_id}:refused",
            run_id=run.run_id,
            sequence=1,
            phase="refused",
            text=f"Run refused to start: {reason}.",
            element_ids=(),
            event_ids=(run.run_id,) if run.run_id else (),
        )

    # -- loop detection -------------------------------------------------------

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
        self,
        events: list[TraceEvent],
        start: int,
        loop_len: int,
        run_id: str,
        segment_by_element: dict[str, int],
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
            label = (
                "first"
                if pos == 0
                else ("last" if pos == loop_len - 1 else f"iteration {pos + 1}")
            )
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

        index = segment_by_element.get(site.element_id)
        phase = _PHASE_UNCLASSIFIED if index is None else _phase_name_for_segment(index)

        step = NarrativeStep(
            id=f"nar:{run_id}:loop:{events[start].event_id}",
            run_id=run_id,
            sequence=0,
            phase=phase,
            text=text,
            element_ids=tuple(sorted(all_element_ids)),
            event_ids=tuple(all_event_ids),
        )
        return step, consumed

    # -- leaf narration ---------------------------------------------------------

    def _leaf_step(
        self,
        event: TraceEvent,
        run_id: str,
        segment_by_element: dict[str, int],
        decision_by_element: dict[str, DecisionPoint],
    ) -> NarrativeStep:
        phase = self._phase_for_event(event, segment_by_element, decision_by_element)
        text = self._render_text(event, decision_by_element)
        if event.element_id:
            element_ids: tuple[str, ...] = (event.element_id,)
        elif event.kind is EventKind.UNMAPPED:
            # The only exemption: an UNMAPPED event maps to no static
            # element, so claiming one would invent the mapping card 12
            # could not make. event_ids below is still a real anchor.
            element_ids = ()
        else:
            # The contract does not produce this for any other EventKind;
            # if it ever does, do not silently drop the anchor requirement.
            raise ValueError(
                f"event {event.event_id!r} of kind {event.kind!r} has no element_id "
                "and is not UNMAPPED -- refusing to emit an unanchored step"
            )
        return NarrativeStep(
            id=f"nar:{run_id}:{event.event_id}",
            run_id=run_id,
            sequence=0,
            phase=phase,
            text=text,
            element_ids=element_ids,
            event_ids=(event.event_id,),
        )

    def _phase_for_event(
        self,
        event: TraceEvent,
        segment_by_element: dict[str, int],
        decision_by_element: dict[str, DecisionPoint],
    ) -> str:
        if event.kind is EventKind.FEATURE_WRITE:
            return _PHASE_FEATURE
        if event.kind is EventKind.EXCEPTION:
            return _PHASE_EXCEPTION
        if event.kind is EventKind.UNMAPPED:
            return _PHASE_UNMAPPED
        if event.kind is EventKind.DECISION:
            decision = decision_by_element.get(event.element_id)
            if decision is not None and decision.is_sink:
                return _PHASE_FINAL
            return _PHASE_DECISION
        index = segment_by_element.get(event.element_id)
        if index is None:
            return _PHASE_UNCLASSIFIED
        return _phase_name_for_segment(index)

    def _render_text(self, event: TraceEvent, decision_by_element: dict[str, DecisionPoint]) -> str:
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
            return self._render_decision(event, decision_by_element)
        if event.kind is EventKind.FEATURE_WRITE:
            if values:
                return f"Feature write at {event.element_id}: {values}."
            return f"Feature write at {event.element_id}."
        if event.kind is EventKind.EXCEPTION:
            outcome = "swallowed (execution continued)" if self._swallowed(event) else "propagated"
            detail = f" -- {values}" if values else ""
            return f"Exception at {event.element_id}{detail} ({outcome})."
        # UNMAPPED
        detail = f" -- {values}" if values else ""
        target = event.element_id or "<no static element>"
        return f"Unmapped event at {target}{detail}: does not map to any static element."

    def _render_decision(
        self, event: TraceEvent, decision_by_element: dict[str, DecisionPoint]
    ) -> str:
        values = _values_clause(event)
        branch = event.branch_taken or "<unrecorded>"
        decision = decision_by_element.get(event.element_id)
        if decision is None:
            reads = f" reads {values}" if values else ""
            return f"Decision at {event.element_id}{reads}; took branch '{branch}'."
        condition = decision.condition_source or "<condition not recorded>"
        if values:
            reads = f" reads {values}"
        elif decision.reads_ids:
            reads = f" reads {', '.join(decision.reads_ids)}"
        else:
            reads = ""
        labels = [label for label, _target in decision.outcomes]
        not_taken = [label for label in labels if label != branch]
        not_taken_clause = f"; did not take: {', '.join(not_taken)}" if not_taken else ""
        return (
            f"Decision at {event.element_id}: condition `{condition}`{reads}; "
            f"took branch '{branch}'{not_taken_clause}."
        )

    def _swallowed(self, event: TraceEvent) -> bool:
        following = getattr(self, "_all_events", None)
        if following is None:
            return False
        for later in following:
            if later.sequence > event.sequence and later.depth <= event.depth:
                return True
        return False

    # -- what did not happen ---------------------------------------------------

    def _not_entered_steps(
        self, order_nodes: Sequence[OrderNode], events: list[TraceEvent], run_id: str
    ) -> list[NarrativeStep]:
        all_elements: set[str] = set()
        for node in order_nodes:
            all_elements.update(node.element_ids)
        seen = {e.element_id for e in events if e.element_id}
        never_entered = sorted(all_elements - seen)
        return [
            NarrativeStep(
                id=f"nar:{run_id}:not_entered:{element_id}",
                run_id=run_id,
                sequence=0,
                phase=_PHASE_NOT_ENTERED,
                text=f"{element_id} was part of the cascade order but no event in this run "
                f"observed it: it was never entered.",
                element_ids=(element_id,),
                event_ids=(),
            )
            for element_id in never_entered
        ]

    def _blocked_steps(self, run: RunRecord, run_id: str) -> list[NarrativeStep]:
        steps = []
        for attempt in sorted(run.blocked, key=lambda a: a.id):
            element_ids = (attempt.element_id,) if attempt.element_id else ()
            event_ids = (attempt.event_id,) if attempt.event_id else (attempt.id,)
            where = f" at {attempt.element_id}" if attempt.element_id else ""
            steps.append(
                NarrativeStep(
                    id=f"nar:{run_id}:blocked:{attempt.id}",
                    run_id=run_id,
                    sequence=0,
                    phase=_PHASE_BLOCKED,
                    text=f"Blocked side effect ({attempt.kind}){where}: {attempt.detail}.",
                    element_ids=element_ids,
                    event_ids=event_ids,
                )
            )
        return steps

    # -- phase grouping ---------------------------------------------------------

    def _group_by_phase(self, leaves: list[NarrativeStep], run_id: str) -> list[NarrativeStep]:
        by_phase: dict[str, list[NarrativeStep]] = {}
        for leaf in leaves:
            by_phase.setdefault(leaf.phase, []).append(leaf)

        # Positional segments first, in numeric order -- that is the
        # cascade's own structural order. Then the fixed phases, in the
        # order they can be named with justification.
        segment_phases = sorted(
            (p for p in by_phase if _SEGMENT_RE.match(p)),
            key=lambda p: int(_SEGMENT_RE.match(p).group(1)),  # type: ignore[union-attr]
        )
        fixed_phases = [p for p in _FIXED_PHASE_ORDER if p in by_phase]
        phases_present = segment_phases + fixed_phases

        out: list[NarrativeStep] = []
        seq = 1
        for phase in phases_present:
            children = by_phase[phase]
            child_ids = tuple(c.id for c in children)
            element_ids = tuple(sorted({eid for c in children for eid in c.element_ids}))
            event_ids = tuple(eid for c in children for eid in c.event_ids)
            if _SEGMENT_RE.match(phase):
                text = (
                    f"Phase '{phase}' (a positional segment of the cascade order; "
                    f"card 3 does not name it): {len(children)} step(s)."
                )
            else:
                text = f"Phase '{phase}': {len(children)} step(s)."
            summary = NarrativeStep(
                id=f"nar:{run_id}:phase:{phase.replace(' ', '_')}",
                run_id=run_id,
                sequence=seq,
                phase=phase,
                text=text,
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
