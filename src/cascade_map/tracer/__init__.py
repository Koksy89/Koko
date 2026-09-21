"""Card 12 -- the runtime tracer and value capture.

The tracer is a *component of a card 11 run*. It starts no process, and there
is no code path here that could: the harness owns the process, the sandbox and
the network, and installs `TraceCollector` inside the process it already
controls. If the run refused, or the harness cannot confirm every control, the
collector raises `TraceRefused` and records nothing.

What it produces is an **overlay**, never a second graph:

* `TraceEvent`s keyed to the element IDs card 1 minted, each tagged
  RUNTIME_OBSERVED with its run ID and event ID;
* `UNMAPPED` events, with code location and reason, for everything that landed
  on no static element -- those are exactly where the static analysis was
  wrong, and a `MappingReport` says how often it happened;
* `Contradiction` records where the run disagrees with the graph, which is the
  only response to a disagreement: the static graph is never edited;
* `NondeterminismObservation`s for the ways this target will not repeat itself.

Collection and materialisation are separate on purpose. Materialisation is a
pure function of a recording plus the static graph, so replaying a recorded run
is byte-identical by construction rather than by diligence.
"""

from __future__ import annotations

from .capture import MISSING, capture_value, capture_values, dropped
from .collector import (
    DEFAULT_REQUIRED_CONTROLS,
    TraceCollector,
    TraceRefused,
    refusal_reason,
)
from .contradictions import Contradiction, ContradictionKind
from .limits import (
    DEFAULT_LIMITS,
    DEFAULT_SENSITIVE_PATTERNS,
    CaptureLimits,
    RedactionPolicy,
)
from .nondeterminism import NondeterminismKind, NondeterminismObservation
from .recording import ObsKind, RawObservation, Recording, RECORDING_VERSION
from .static_index import BranchOutcome, CodeLocation, EventMapping, StaticIndex
from .tracer import EVENT_ID_WIDTH, MappingReport, TraceResult, Tracer, event_id_for

__all__ = [
    "CaptureLimits",
    "CodeLocation",
    "Contradiction",
    "ContradictionKind",
    "DEFAULT_LIMITS",
    "DEFAULT_REQUIRED_CONTROLS",
    "DEFAULT_SENSITIVE_PATTERNS",
    "EVENT_ID_WIDTH",
    "EventMapping",
    "BranchOutcome",
    "MISSING",
    "MappingReport",
    "NondeterminismKind",
    "NondeterminismObservation",
    "ObsKind",
    "RECORDING_VERSION",
    "RawObservation",
    "Recording",
    "RedactionPolicy",
    "StaticIndex",
    "TraceCollector",
    "TraceRefused",
    "TraceResult",
    "Tracer",
    "capture_value",
    "capture_values",
    "dropped",
    "event_id_for",
    "refusal_reason",
]
