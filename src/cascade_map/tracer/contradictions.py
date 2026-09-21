"""Where the run disagrees with the static graph.

The static graph is never edited to match what a run happened to do. One run is
one scenario: an edge that was not taken here may be taken tomorrow, and a call
the graph did not predict may be a resolution card 2 honestly could not make.
So a disagreement is emitted as its own record, tagged RUNTIME_OBSERVED with
the run and the events that evidence it, and the graph is left alone.

There is no contract type for this yet. `Unresolved` has no provenance, so it
cannot carry the run ID and event IDs constraint 2 requires; `Finding` has no
kind for a contradiction. `Contradiction` is therefore local to card 12 and is
a reported contract-change request.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from cascade_map.contracts.interfaces import Confidence, Method, Provenance, SourceSpan

__all__ = ["ContradictionKind", "Contradiction"]

MAX_EVIDENCE_EVENTS = 5


class ContradictionKind(StrEnum):
    EDGE_NOT_TAKEN = "EDGE_NOT_TAKEN"
    """The static graph predicts a call the run never made, from a caller the
    run did enter. Scenario-scoped: stated as an observation, not a verdict."""

    CALL_NOT_PREDICTED = "CALL_NOT_PREDICTED"
    """The run made a call the static graph has no edge for."""

    ORDER_DIFFERS = "ORDER_DIFFERS"
    """A SEQUENCE the static graph fixed ran the other way round."""

    ELEMENT_NOT_PREDICTED = "ELEMENT_NOT_PREDICTED"
    """An element ran that the static inventory does not contain at all."""


@dataclass(frozen=True, slots=True)
class Contradiction:
    id: str
    kind: ContradictionKind
    run_id: str
    static_ids: tuple[str, ...]
    """The static facts contradicted: element, edge or order-node IDs."""

    observed_ids: tuple[str, ...]
    """The elements actually involved in the observation."""

    summary: str
    occurrences: int
    provenance: Provenance

    @staticmethod
    def build(
        run_id: str,
        kind: ContradictionKind,
        static_ids: tuple[str, ...],
        observed_ids: tuple[str, ...],
        summary: str,
        event_ids: tuple[str, ...],
        confidence: Confidence,
        span: SourceSpan | None = None,
        occurrences: int = 1,
    ) -> "Contradiction":
        anchor = "|".join(static_ids or observed_ids)
        evidence = tuple(sorted(event_ids)[:MAX_EVIDENCE_EVENTS])
        return Contradiction(
            id=f"contradiction:{run_id}:{kind}:{anchor}",
            kind=kind,
            run_id=run_id,
            static_ids=static_ids,
            observed_ids=observed_ids,
            summary=summary,
            occurrences=occurrences,
            provenance=Provenance(
                method=Method.RUNTIME_OBSERVED,
                confidence=confidence,
                span=span,
                note=summary,
                run_id=run_id,
                event_ids=evidence,
            ),
        )
