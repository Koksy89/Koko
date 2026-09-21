"""Materialisation: turning a recording into the overlay.

`Tracer.trace` is a pure function of a recording plus the static graph. It
starts no process, imports nothing from the target and reads no target source
except the single line `linecache` needs to tell two decision points apart.
Run it twice on the same recording and the bytes are the same; that is the
whole of the replay guarantee.

Everything it produces attaches to IDs card 1 minted. Nothing it produces
edits them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from cascade_map.contracts.interfaces import (
    Confidence,
    DecisionPoint,
    EventKind,
    Method,
    Provenance,
    RunRecord,
    SourceSpan,
    TraceEvent,
    canonical_dumps,
    canonical_jsonl,
    combine,
)

from .capture import capture_value
from .collector import DEFAULT_REQUIRED_CONTROLS, TraceCollector, TraceRefused, refusal_reason
from .contradictions import Contradiction, ContradictionKind
from .limits import DEFAULT_LIMITS, CaptureLimits, RedactionPolicy
from .nondeterminism import (
    NONDETERMINISTIC_MODULES,
    NONDETERMINISTIC_NAMES,
    NondeterminismKind,
    NondeterminismObservation,
)
from .recording import ObsKind, RawObservation, Recording
from .static_index import CodeLocation, StaticIndex

__all__ = ["MappingReport", "TraceResult", "Tracer", "event_id_for"]

EVENT_ID_WIDTH = 8


def event_id_for(sequence: int) -> str:
    """Deterministic within a run, and sorted by it.

    `schema.json` sorts events.jsonl by ``event_id``, so the ID is zero-padded:
    unpadded counters would sort evt_10 before evt_2 and scramble the one file
    whose natural reading order is execution order.
    """
    return f"evt_{sequence:0{EVENT_ID_WIDTH}d}"


@dataclass(frozen=True, slots=True)
class MappingReport:
    """How much of the run landed on the static graph. Reported, always."""

    run_id: str
    total_events: int
    mapped_events: int
    unmapped_events: int
    rate_permille: int
    """Mapping rate in parts per thousand. An int because artifacts carry no
    floats; ``rate_text`` is the human form."""

    rate_text: str
    unmapped_reasons: dict[str, int]
    external_frames: dict[str, int]
    observations: int
    dropped_observations: int
    elements_entered: int

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "total_events": self.total_events,
            "mapped_events": self.mapped_events,
            "unmapped_events": self.unmapped_events,
            "rate_permille": self.rate_permille,
            "rate_text": self.rate_text,
            "unmapped_reasons": dict(sorted(self.unmapped_reasons.items())),
            "external_frames": dict(sorted(self.external_frames.items())),
            "observations": self.observations,
            "dropped_observations": self.dropped_observations,
            "elements_entered": self.elements_entered,
        }


@dataclass(frozen=True, slots=True)
class TraceResult:
    run_id: str
    events: tuple[TraceEvent, ...]
    contradictions: tuple[Contradiction, ...]
    nondeterminism: tuple[NondeterminismObservation, ...]
    mapping: MappingReport


class Tracer:
    """Card 12's entry point. Implements `TracerCard`."""

    def __init__(
        self,
        index: StaticIndex,
        *,
        recordings_dir: str | Path | None = None,
        limits: CaptureLimits = DEFAULT_LIMITS,
        policy: RedactionPolicy | None = None,
        required_controls: Sequence[str] = DEFAULT_REQUIRED_CONTROLS,
        max_observations: int = 200_000,
    ) -> None:
        self.index = index
        self.recordings_dir = Path(recordings_dir) if recordings_dir is not None else None
        self.limits = limits
        self.policy = policy if policy is not None else RedactionPolicy()
        self.required_controls = tuple(required_controls)
        self.max_observations = max_observations
        self._recordings: dict[str, Recording] = {}

    # -- collection (called by the harness, inside its own process) --------

    def collector(self, run: RunRecord) -> TraceCollector:
        """The hook the harness installs around the target call."""
        return TraceCollector(
            run,
            self.index,
            limits=self.limits,
            policy=self.policy,
            required_controls=self.required_controls,
            max_observations=self.max_observations,
        )

    def hold(self, run: RunRecord, recording: Recording) -> None:
        """Keep a freshly collected recording for materialisation in-process."""
        self._recordings[run.run_id] = recording

    def recording_path(self, run_id: str) -> Path:
        if self.recordings_dir is None:
            raise TraceRefused(
                f"no recordings directory is configured, so run {run_id} cannot be "
                "replayed; the tracer never starts a process of its own"
            )
        return self.recordings_dir / run_id / "recording.jsonl"

    def load_recording(self, run: RunRecord) -> Recording:
        held = self._recordings.get(run.run_id)
        if held is not None:
            return held
        path = self.recording_path(run.run_id)
        if not path.exists():
            raise TraceRefused(
                f"no recording for run {run.run_id} at {path.name}; a run must be "
                "collected inside the card 11 harness before it can be traced"
            )
        return Recording.read(path)

    # -- TracerCard --------------------------------------------------------

    def trace(self, run: RunRecord) -> Sequence[TraceEvent]:
        return self.result(run).events

    def result(self, run: RunRecord) -> TraceResult:
        reason = refusal_reason(run, self.required_controls)
        if reason:
            raise TraceRefused(reason)
        return self.materialise(run, self.load_recording(run))

    # -- materialisation ---------------------------------------------------

    def materialise(self, run: RunRecord, recording: Recording) -> TraceResult:
        run_id = run.run_id
        ordered = recording.ordered()
        dispositions = _dispositions(ordered)
        events: list[TraceEvent] = []
        frame_event: dict[int, str] = {}
        pending_cond: dict[int, RawObservation] = {}
        entered: dict[str, list[str]] = {}
        observed_calls: dict[tuple[str, str], list[str]] = {}
        first_call: dict[str, int] = {}
        unmapped_reasons: dict[str, int] = {}
        nd_names_seen: dict[tuple[str, str], list[str]] = {}
        sequence = 0

        def emit(
            kind: EventKind,
            obs: RawObservation,
            element_id: str,
            confidence: Confidence,
            note: str,
            values: dict[str, Any] | None = None,
            branch_taken: str = "",
        ) -> TraceEvent:
            nonlocal sequence
            sequence += 1
            eid = event_id_for(sequence)
            event = TraceEvent(
                event_id=eid,
                run_id=run_id,
                kind=kind,
                element_id=element_id,
                sequence=sequence,
                depth=obs.depth,
                caller_event_id=frame_event.get(obs.parent_frame_key, ""),
                values=dict(values if values is not None else obs.values),
                branch_taken=branch_taken,
                provenance=Provenance(
                    method=Method.RUNTIME_OBSERVED,
                    confidence=confidence,
                    span=SourceSpan(path=obs.path, line=obs.line),
                    note=note,
                    run_id=run_id,
                    event_ids=(eid,),
                ),
            )
            events.append(event)
            return event

        for obs in ordered:
            mapping = self.index.map_code(
                CodeLocation(
                    path=obs.path,
                    qualname=obs.qualname,
                    line=obs.line,
                    first_line=obs.first_line,
                    under_root=obs.under_root,
                    synthetic=obs.synthetic,
                )
            )
            element_id = mapping.element_id
            if not element_id:
                unmapped_reasons[_category(mapping.reason)] = (
                    unmapped_reasons.get(_category(mapping.reason), 0) + 1
                )
                note = (
                    f"UNMAPPED {obs.kind} at {obs.path}:{obs.line} "
                    f"(qualname {obs.qualname!r}): {mapping.reason}"
                )
                if obs.kind is ObsKind.BRANCH_COND:
                    pending_cond[obs.frame_key] = obs
                    continue
                if obs.kind is ObsKind.HANDLER:
                    continue
                event = emit(EventKind.UNMAPPED, obs, "", Confidence.UNKNOWN, note)
                if obs.kind is ObsKind.CALL:
                    frame_event[obs.frame_key] = event.event_id
                continue

            if obs.kind is ObsKind.CALL:
                note = _note(mapping.reason, f"entered {element_id}")
                event = emit(EventKind.CALL, obs, element_id, mapping.confidence, note)
                frame_event[obs.frame_key] = event.event_id
                entered.setdefault(element_id, []).append(event.event_id)
                first_call.setdefault(element_id, event.sequence)
                caller_event = frame_event.get(obs.parent_frame_key, "")
                caller_element = _element_of(events, caller_event)
                if caller_element:
                    observed_calls.setdefault((caller_element, element_id), []).append(
                        event.event_id
                    )
                for name in _nd_names(obs):
                    nd_names_seen.setdefault(
                        (element_id, str(NONDETERMINISTIC_NAMES[name])), []
                    ).append(f"{event.event_id}:{name}")
            elif obs.kind is ObsKind.RETURN:
                note = _note(mapping.reason, f"left {element_id}")
                if obs.detail.get("unwinding") == "1":
                    note += "; the frame left via an exception, not a return"
                emit(EventKind.RETURN, obs, element_id, mapping.confidence, note)
                if element_id in self.index.sink_element_ids:
                    emit(
                        EventKind.DECISION,
                        obs,
                        element_id,
                        mapping.confidence,
                        f"final decision produced at sink {element_id}",
                        values={"decision": obs.values.get("return_value")}
                        if "return_value" in obs.values
                        else {},
                    )
            elif obs.kind is ObsKind.EXCEPTION:
                disposition, confidence, why = dispositions.get(
                    (obs.frame_key, obs.thread_slot, obs.thread_seq),
                    ("UNKNOWN", Confidence.UNKNOWN, "disposition not determined"),
                )
                note = _note(
                    mapping.reason,
                    f"{obs.detail.get('exception_type', 'exception')} raised in "
                    f"{element_id}; disposition={disposition} ({why})",
                )
                emit(
                    EventKind.EXCEPTION,
                    obs,
                    element_id,
                    combine(mapping.confidence, confidence),
                    note,
                )
            elif obs.kind is ObsKind.FEATURE:
                feature_id = obs.detail.get("feature_id", "")
                note = _note(
                    mapping.reason,
                    f"feature {feature_id} written by {element_id} at "
                    f"{obs.path}:{obs.line}",
                )
                emit(
                    EventKind.FEATURE_WRITE,
                    obs,
                    feature_id or element_id,
                    mapping.confidence,
                    note,
                )
            elif obs.kind is ObsKind.BRANCH_COND:
                pending_cond[obs.frame_key] = obs
            elif obs.kind is ObsKind.BRANCH_NEXT:
                cond = pending_cond.pop(obs.frame_key, None)
                if cond is None:
                    continue
                decision = self.index.decision(cond.detail.get("decision_id", ""))
                outcome = self.index.branch_outcome(
                    cond.detail.get("block_id", ""), obs.line, decision
                )
                note = _note(
                    mapping.reason,
                    f"condition {cond.detail.get('condition', '') or '<unknown>'!r} at "
                    f"{cond.path}:{cond.line} continued at line {obs.line}"
                    + (f"; {outcome.reason}" if outcome.reason else ""),
                )
                branch_event = emit(
                    EventKind.BRANCH,
                    cond,
                    element_id,
                    combine(mapping.confidence, outcome.confidence),
                    note,
                    values=dict(cond.values),
                    branch_taken=outcome.label,
                )
                if cond.detail.get("is_sink") == "1":
                    emit(
                        EventKind.DECISION,
                        cond,
                        element_id,
                        branch_event.provenance.confidence
                        if branch_event.provenance
                        else Confidence.UNKNOWN,
                        f"final decision at sink decision "
                        f"{cond.detail.get('decision_id', '')}: {outcome.label}",
                        values=dict(cond.values),
                        branch_taken=outcome.label,
                    )

        for frame_key, cond in sorted(pending_cond.items()):
            mapping = self.index.map_code(
                CodeLocation(
                    path=cond.path,
                    qualname=cond.qualname,
                    line=cond.line,
                    first_line=cond.first_line,
                    under_root=cond.under_root,
                    synthetic=cond.synthetic,
                )
            )
            emit(
                EventKind.BRANCH if mapping.element_id else EventKind.UNMAPPED,
                cond,
                mapping.element_id,
                Confidence.UNKNOWN,
                "the branch never resolved: the trace ended before the next line ran, "
                "so which way this condition went is not known",
                values=dict(cond.values),
            )

        dropped = int(recording.header.get("dropped_observations", 0) or 0)
        if dropped:
            last = ordered[-1] if ordered else _origin()
            emit(
                EventKind.UNMAPPED,
                last,
                "",
                Confidence.UNKNOWN,
                f"tracing stopped at max_observations="
                f"{recording.header.get('max_observations')}: {dropped} further "
                "observations were not recorded. This trace is shorter than the run.",
            )

        mapped = sum(1 for event in events if event.element_id)
        total = len(events)
        permille = (mapped * 1000) // total if total else 1000
        report = MappingReport(
            run_id=run_id,
            total_events=total,
            mapped_events=mapped,
            unmapped_events=total - mapped,
            rate_permille=permille,
            rate_text=f"{mapped}/{total} events mapped "
            f"({permille // 10}.{permille % 10}%)",
            unmapped_reasons=unmapped_reasons,
            external_frames={
                str(key): int(value)
                for key, value in dict(recording.header.get("external_frames", {})).items()
            },
            observations=len(ordered),
            dropped_observations=dropped,
            elements_entered=len(entered),
        )

        return TraceResult(
            run_id=run_id,
            events=tuple(events),
            contradictions=self._contradictions(run_id, entered, observed_calls, first_call),
            nondeterminism=self._nondeterminism(run_id, recording, nd_names_seen, dropped),
            mapping=report,
        )

    # -- overlay records ---------------------------------------------------

    def _contradictions(
        self,
        run_id: str,
        entered: dict[str, list[str]],
        observed_calls: dict[tuple[str, str], list[str]],
        first_call: dict[str, int],
    ) -> tuple[Contradiction, ...]:
        out: list[Contradiction] = []
        static_pairs = self.index.call_pairs()
        for (source, target), edge_id in sorted(static_pairs.items()):
            if source not in entered or (source, target) in observed_calls:
                continue
            out.append(
                Contradiction.build(
                    run_id,
                    ContradictionKind.EDGE_NOT_TAKEN,
                    (edge_id,),
                    (source, target),
                    f"the static graph has a CALLS edge {source} -> {target}, and "
                    f"{source} ran {len(entered[source])} time(s) in this run, but the "
                    "call was never made. The edge may still be right under another "
                    "scenario: this is an observation of one run, not a verdict on the "
                    "graph.",
                    tuple(entered[source]),
                    Confidence.RESOLVED,
                    occurrences=len(entered[source]),
                )
            )
        for (source, target), event_ids in sorted(observed_calls.items()):
            if (source, target) in static_pairs:
                continue
            out.append(
                Contradiction.build(
                    run_id,
                    ContradictionKind.CALL_NOT_PREDICTED,
                    (),
                    (source, target),
                    f"{source} called {target} {len(event_ids)} time(s), and the static "
                    "graph has no CALLS edge for it. Either card 2 could not resolve the "
                    "call or the call is dynamic.",
                    tuple(event_ids),
                    Confidence.RESOLVED,
                    occurrences=len(event_ids),
                )
            )
        for before, after, node_id in self.index.sequence_pairs():
            if before not in first_call or after not in first_call:
                continue
            if first_call[after] < first_call[before]:
                out.append(
                    Contradiction.build(
                        run_id,
                        ContradictionKind.ORDER_DIFFERS,
                        (node_id,),
                        (before, after),
                        f"order node {node_id} is a SEQUENCE placing {before} before "
                        f"{after}, but {after} ran first in this run.",
                        tuple(
                            event_id_for(first_call[after]) for _ in (0,)
                        )
                        + (event_id_for(first_call[before]),),
                        Confidence.RESOLVED,
                    )
                )
        return tuple(sorted(out, key=lambda c: c.id))

    def _nondeterminism(
        self,
        run_id: str,
        recording: Recording,
        nd_names_seen: dict[tuple[str, str], list[str]],
        dropped: int,
    ) -> tuple[NondeterminismObservation, ...]:
        out: list[NondeterminismObservation] = []
        for (element_id, kind), hits in sorted(nd_names_seen.items()):
            names = sorted({hit.split(":", 1)[1] for hit in hits})
            event_ids = tuple(sorted({hit.split(":", 1)[0] for hit in hits}))
            out.append(
                NondeterminismObservation.build(
                    run_id,
                    NondeterminismKind(kind),
                    element_id,
                    f"{element_id} referenced {', '.join(names)} while running: this "
                    "element's result is not reproducible from its inputs alone",
                    event_ids,
                    Confidence.HEURISTIC,
                )
            )
        for path, count in sorted(
            dict(recording.header.get("external_frames", {})).items()
        ):
            module = str(path).rsplit("/", 1)[-1].removesuffix(".py")
            kind = NONDETERMINISTIC_MODULES.get(module)
            if kind is None:
                continue
            out.append(
                NondeterminismObservation.build(
                    run_id,
                    kind,
                    "",
                    f"the run executed {count} frame(s) in {path}, a nondeterministic "
                    "source outside the analysed root",
                    (),
                    Confidence.RESOLVED,
                )
            )
        if recording.header.get("hash_randomization"):
            out.append(
                NondeterminismObservation.build(
                    run_id,
                    NondeterminismKind.HASH_ORDERING,
                    "",
                    "hash randomization was active in the traced process, so iteration "
                    "order over sets and over dicts keyed by str or bytes can differ "
                    "between runs of the target",
                    (),
                    Confidence.RESOLVED,
                )
            )
        if int(recording.header.get("thread_count", 1) or 1) > 1:
            out.append(
                NondeterminismObservation.build(
                    run_id,
                    NondeterminismKind.THREAD_INTERLEAVING,
                    "",
                    f"the run used {recording.header.get('thread_count')} threads. Events "
                    "are emitted grouped by thread and ordered within each thread, which "
                    "is stable; the interleaving that actually happened is kept in the "
                    "recording's arrival order and is not reproducible",
                    (),
                    Confidence.RESOLVED,
                )
            )
        if dropped:
            out.append(
                NondeterminismObservation.build(
                    run_id,
                    NondeterminismKind.TRACE_TRUNCATED,
                    "",
                    f"{dropped} observations were not recorded once the collector reached "
                    f"max_observations={recording.header.get('max_observations')}; the "
                    "trace is shorter than the run",
                    (),
                    Confidence.RESOLVED,
                )
            )
        return tuple(sorted(out, key=lambda o: o.id))

    # -- artifacts ---------------------------------------------------------

    def events_jsonl(self, events: Sequence[TraceEvent]) -> str:
        return canonical_jsonl(events, "event_id")

    def emit(self, result: TraceResult, out_dir: str | Path) -> dict[str, str]:
        """Write the overlay. `events.jsonl` is the contract artifact; the other
        three are card 12's own records and are reported as contract requests."""
        directory = Path(out_dir)
        directory.mkdir(parents=True, exist_ok=True)
        files = {
            "events.jsonl": self.events_jsonl(result.events),
            "contradictions.jsonl": canonical_jsonl(result.contradictions, "id"),
            "nondeterminism.jsonl": canonical_jsonl(result.nondeterminism, "id"),
            "mapping.json": canonical_dumps(result.mapping.to_json()) + "\n",
        }
        for name, text in files.items():
            (directory / name).write_text(text, encoding="utf-8")
        return files


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _origin() -> RawObservation:
    return RawObservation(
        kind=ObsKind.CALL,
        thread_slot=0,
        thread_seq=0,
        arrival=0,
        frame_key=0,
        parent_frame_key=0,
        depth=0,
        path="<run>",
        line=0,
        first_line=0,
        qualname="",
    )


def _note(reason: str, text: str) -> str:
    return f"{text}; {reason}" if reason else text


def _nd_names(obs: RawObservation) -> tuple[str, ...]:
    raw = obs.detail.get("nd_names", "")
    return tuple(name for name in raw.split(",") if name in NONDETERMINISTIC_NAMES)


def _element_of(events: Sequence[TraceEvent], event_id: str) -> str:
    if not event_id:
        return ""
    for event in reversed(events):
        if event.event_id == event_id:
            return event.element_id
    return ""


def _category(reason: str) -> str:
    if reason.startswith("dynamically created"):
        return "DYNAMIC_CODE"
    if "outside the analysed root" in reason:
        return "EXTERNAL_CODE"
    if reason.startswith("no static element"):
        return "MISSING_FROM_INVENTORY"
    if "ambiguous" in reason:
        return "AMBIGUOUS"
    return "OTHER"


def _dispositions(
    ordered: Sequence[RawObservation],
) -> dict[tuple[int, int, int], tuple[str, Confidence, str]]:
    """Was each exception caught in its own frame, or did it leave?"""
    by_frame: dict[int, list[RawObservation]] = {}
    for obs in ordered:
        by_frame.setdefault(obs.frame_key, []).append(obs)
    out: dict[tuple[int, int, int], tuple[str, Confidence, str]] = {}
    for frame_key, observations in by_frame.items():
        for index, obs in enumerate(observations):
            if obs.kind is not ObsKind.EXCEPTION:
                continue
            verdict = ("PROPAGATED", Confidence.HEURISTIC, "no later event in this frame")
            for later in observations[index + 1 :]:
                if later.kind is ObsKind.HANDLER:
                    verdict = (
                        "CAUGHT",
                        Confidence.RESOLVED,
                        "an except handler block of this element ran afterwards",
                    )
                    break
                if later.kind is ObsKind.RETURN:
                    if later.detail.get("unwinding") == "1":
                        verdict = (
                            "PROPAGATED",
                            Confidence.PROBABLE,
                            "the frame left without returning a value",
                        )
                    else:
                        verdict = (
                            "CAUGHT",
                            Confidence.PROBABLE,
                            "the frame returned normally afterwards",
                        )
                    break
            out[(frame_key, obs.thread_slot, obs.thread_seq)] = verdict
    return out
