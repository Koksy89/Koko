"""Nondeterminism the target brings, recorded rather than hidden.

The tracer's own output is deterministic: ordering is (thread slot, thread
sequence), IDs are minted from that order, and serialization goes through
`canonical_dumps`. That is the tracer's half of the bargain.

The target's half is different. A run that reads the wall clock, draws random
numbers, iterates a set under hash randomization or interleaves threads is not
reproducible, and pretending otherwise would make every later comparison of two
runs a lie. So each source is emitted as an observed property of the run, with
the events that evidence it.

There is no contract type for this yet; `NondeterminismObservation` is local to
card 12 and is a reported contract-change request.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from cascade_map.contracts.interfaces import Confidence, Method, Provenance

__all__ = [
    "NondeterminismKind",
    "NondeterminismObservation",
    "NONDETERMINISTIC_NAMES",
    "NONDETERMINISTIC_MODULES",
    "names_in",
]


class NondeterminismKind(StrEnum):
    WALL_CLOCK = "WALL_CLOCK"
    RANDOMNESS = "RANDOMNESS"
    IDENTITY = "IDENTITY"
    """uuid, id(), object addresses."""

    HASH_ORDERING = "HASH_ORDERING"
    THREAD_INTERLEAVING = "THREAD_INTERLEAVING"
    ENVIRONMENT = "ENVIRONMENT"
    TRACE_TRUNCATED = "TRACE_TRUNCATED"
    """Not the target's nondeterminism but the tracer's own limit, recorded in
    the same place so a short trace can never be mistaken for a short run."""


#: Names whose appearance in an executed code object's ``co_names`` is evidence
#: that the code reached for a nondeterministic source. Evidence, not proof:
#: these are reported HEURISTIC and name the exact symbol seen.
NONDETERMINISTIC_NAMES: dict[str, NondeterminismKind] = {
    "time": NondeterminismKind.WALL_CLOCK,
    "time_ns": NondeterminismKind.WALL_CLOCK,
    "monotonic": NondeterminismKind.WALL_CLOCK,
    "perf_counter": NondeterminismKind.WALL_CLOCK,
    "perf_counter_ns": NondeterminismKind.WALL_CLOCK,
    "now": NondeterminismKind.WALL_CLOCK,
    "utcnow": NondeterminismKind.WALL_CLOCK,
    "today": NondeterminismKind.WALL_CLOCK,
    "timestamp": NondeterminismKind.WALL_CLOCK,
    "random": NondeterminismKind.RANDOMNESS,
    "randint": NondeterminismKind.RANDOMNESS,
    "randrange": NondeterminismKind.RANDOMNESS,
    "getrandbits": NondeterminismKind.RANDOMNESS,
    "shuffle": NondeterminismKind.RANDOMNESS,
    "choice": NondeterminismKind.RANDOMNESS,
    "choices": NondeterminismKind.RANDOMNESS,
    "sample": NondeterminismKind.RANDOMNESS,
    "gauss": NondeterminismKind.RANDOMNESS,
    "uniform": NondeterminismKind.RANDOMNESS,
    "urandom": NondeterminismKind.RANDOMNESS,
    "token_hex": NondeterminismKind.RANDOMNESS,
    "token_bytes": NondeterminismKind.RANDOMNESS,
    "uuid1": NondeterminismKind.IDENTITY,
    "uuid4": NondeterminismKind.IDENTITY,
}

#: Modules whose executed frames are proof, not evidence.
NONDETERMINISTIC_MODULES: dict[str, NondeterminismKind] = {
    "random": NondeterminismKind.RANDOMNESS,
    "secrets": NondeterminismKind.RANDOMNESS,
    "uuid": NondeterminismKind.IDENTITY,
    "datetime": NondeterminismKind.WALL_CLOCK,
}


def names_in(co_names: tuple[str, ...]) -> tuple[str, ...]:
    """The nondeterministic symbols an executed code object referenced."""
    return tuple(sorted({name for name in co_names if name in NONDETERMINISTIC_NAMES}))


@dataclass(frozen=True, slots=True)
class NondeterminismObservation:
    """One reason two runs of this target may not agree.

    Tagged RUNTIME_OBSERVED with the run and the events that evidence it, like
    every other runtime fact.
    """

    id: str
    run_id: str
    kind: NondeterminismKind
    element_id: str
    detail: str
    event_ids: tuple[str, ...]
    provenance: Provenance

    @staticmethod
    def build(
        run_id: str,
        kind: NondeterminismKind,
        element_id: str,
        detail: str,
        event_ids: tuple[str, ...],
        confidence: Confidence,
        span: Any = None,
    ) -> "NondeterminismObservation":
        anchor = element_id or (event_ids[0] if event_ids else "run")
        return NondeterminismObservation(
            id=f"nondet:{run_id}:{kind}:{anchor}",
            run_id=run_id,
            kind=kind,
            element_id=element_id,
            detail=detail,
            event_ids=event_ids,
            provenance=Provenance(
                method=Method.RUNTIME_OBSERVED,
                confidence=confidence,
                span=span,
                note=detail,
                run_id=run_id,
                event_ids=event_ids,
            ),
        )
