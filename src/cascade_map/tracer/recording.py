"""The raw observation stream: what the collector wrote down, before mapping.

A recording is the *evidence*; `events.jsonl` is the *reading* of it. Splitting
the two is what makes "replaying a recorded run is byte-identical" a real test
rather than a hope: materialisation is a pure function of a recording plus the
static graph, so it can be run twice, or a year later, and must agree.

Values are already captured -- bounded and redacted -- by the time they reach
this file. A raw object never survives the collector.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable

from cascade_map.contracts.interfaces import CaptureStatus, ValueCapture, canonical_dumps

__all__ = ["ObsKind", "RawObservation", "Recording", "RECORDING_VERSION"]

RECORDING_VERSION = "1"


class ObsKind(StrEnum):
    CALL = "CALL"
    RETURN = "RETURN"
    EXCEPTION = "EXCEPTION"
    BRANCH_COND = "BRANCH_COND"
    BRANCH_NEXT = "BRANCH_NEXT"
    FEATURE = "FEATURE"
    HANDLER = "HANDLER"


@dataclass(frozen=True, slots=True)
class RawObservation:
    """One thing the collector saw, with the location it saw it at."""

    kind: ObsKind
    thread_slot: int
    thread_seq: int
    """Monotonic within a thread. Emission order is (slot, seq), which is
    stable whatever the interleaving was -- the interleaving itself is recorded
    separately as an observed property rather than being smoothed away."""

    arrival: int
    """Global arrival order. Kept because it is the evidence of interleaving;
    never used to order emitted events."""

    frame_key: int
    parent_frame_key: int
    depth: int
    path: str
    line: int
    first_line: int
    qualname: str
    under_root: bool = False
    synthetic: bool = False
    values: dict[str, ValueCapture] = field(default_factory=dict)
    detail: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": str(self.kind),
            "thread_slot": self.thread_slot,
            "thread_seq": self.thread_seq,
            "arrival": self.arrival,
            "frame_key": self.frame_key,
            "parent_frame_key": self.parent_frame_key,
            "depth": self.depth,
            "path": self.path,
            "line": self.line,
            "first_line": self.first_line,
            "qualname": self.qualname,
            "under_root": self.under_root,
            "synthetic": self.synthetic,
            "values": {name: _capture_json(c) for name, c in sorted(self.values.items())},
            "detail": {k: v for k, v in sorted(self.detail.items())},
        }

    @staticmethod
    def from_json(payload: dict[str, Any]) -> "RawObservation":
        return RawObservation(
            kind=ObsKind(payload["kind"]),
            thread_slot=int(payload["thread_slot"]),
            thread_seq=int(payload["thread_seq"]),
            arrival=int(payload["arrival"]),
            frame_key=int(payload["frame_key"]),
            parent_frame_key=int(payload["parent_frame_key"]),
            depth=int(payload["depth"]),
            path=str(payload["path"]),
            line=int(payload["line"]),
            first_line=int(payload["first_line"]),
            qualname=str(payload["qualname"]),
            under_root=bool(payload.get("under_root", False)),
            synthetic=bool(payload.get("synthetic", False)),
            values={
                name: _capture_from_json(value)
                for name, value in dict(payload.get("values", {})).items()
            },
            detail={str(k): str(v) for k, v in dict(payload.get("detail", {})).items()},
        )


def _capture_json(capture: ValueCapture) -> dict[str, Any]:
    return {
        "status": str(capture.status),
        "repr_text": capture.repr_text,
        "type_name": capture.type_name,
        "shape": capture.shape,
        "original_size": capture.original_size,
        "reason": capture.reason,
    }


def _capture_from_json(payload: dict[str, Any]) -> ValueCapture:
    return ValueCapture(
        status=CaptureStatus(payload["status"]),
        repr_text=str(payload.get("repr_text", "")),
        type_name=str(payload.get("type_name", "")),
        shape=str(payload.get("shape", "")),
        original_size=int(payload.get("original_size", 0)),
        reason=str(payload.get("reason", "")),
    )


@dataclass(frozen=True, slots=True)
class Recording:
    """A header plus the observations, in arrival order."""

    header: dict[str, Any]
    observations: tuple[RawObservation, ...] = ()

    def dumps(self) -> str:
        lines = [canonical_dumps({"header": self.header})]
        lines.extend(canonical_dumps(obs.to_json()) for obs in self.observations)
        return "".join(f"{line}\n" for line in lines)

    @staticmethod
    def loads(text: str) -> "Recording":
        header: dict[str, Any] = {}
        observations: list[RawObservation] = []
        for raw in text.splitlines():
            if not raw.strip():
                continue
            payload = json.loads(raw)
            if "header" in payload:
                header = dict(payload["header"])
                continue
            observations.append(RawObservation.from_json(payload))
        return Recording(header=header, observations=tuple(observations))

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.dumps(), encoding="utf-8")
        return target

    @staticmethod
    def read(path: str | Path) -> "Recording":
        return Recording.loads(Path(path).read_text(encoding="utf-8"))

    def ordered(self) -> list[RawObservation]:
        """Observations in the stable emission order: (thread slot, thread seq)."""
        return sorted(self.observations, key=lambda o: (o.thread_slot, o.thread_seq, o.arrival))

    def with_observations(self, observations: Iterable[RawObservation]) -> "Recording":
        return Recording(header=dict(self.header), observations=tuple(observations))

    def with_header(self, **updates: Any) -> "Recording":
        header = dict(self.header)
        header.update(updates)
        return Recording(header=header, observations=self.observations)

