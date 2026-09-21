"""Incremental cache, keyed on file content hash.

Rebuilt fresh from the files present on this run: an entry only survives via
`reuse()`/`put()`, both driven by files the walker actually found this time.
A file removed from the target simply has nothing call `reuse`/`put` for it,
so its stale record cannot survive into the new cache -- the key *is* the
content, not the path (ARCHITECTURE.md, Incrementality).
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from cascade_map.contracts.interfaces import (
    Confidence,
    Element,
    ElementKind,
    Method,
    Provenance,
    SourceSpan,
    Unresolved,
    UnresolvedReason,
)


def _prov_from_dict(d: dict) -> Provenance:
    d = dict(d)
    d["method"] = Method(d["method"])
    d["confidence"] = Confidence(d["confidence"])
    if d.get("span"):
        d["span"] = SourceSpan(**d["span"])
    d["event_ids"] = tuple(d.get("event_ids", ()))
    return Provenance(**d)


def element_to_dict(el: Element) -> dict:
    return dataclasses.asdict(el)


def element_from_dict(d: dict) -> Element:
    d = dict(d)
    d["kind"] = ElementKind(d["kind"])
    d["span"] = SourceSpan(**d["span"])
    d["provenance"] = _prov_from_dict(d["provenance"])
    d["decorators"] = tuple(d.get("decorators", ()))
    return Element(**d)


def unresolved_to_dict(u: Unresolved) -> dict:
    return dataclasses.asdict(u)


def unresolved_from_dict(d: dict) -> Unresolved:
    d = dict(d)
    d["reason"] = UnresolvedReason(d["reason"])
    d["span"] = SourceSpan(**d["span"])
    d["attempted"] = tuple(Method(m) for m in d.get("attempted", ()))
    d["candidate_ids"] = tuple(d.get("candidate_ids", ()))
    d["candidate_confidence"] = Confidence(d.get("candidate_confidence", "UNKNOWN"))
    return Unresolved(**d)


class Cache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, dict] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}
        self._next: dict[str, dict] = {}

    def get(self, relkey: str, content_hash: str) -> tuple[list[Element], list[Unresolved]] | None:
        entry = self._data.get(relkey)
        if entry is None or entry.get("hash") != content_hash:
            return None
        elements = [element_from_dict(e) for e in entry.get("elements", [])]
        unresolved = [unresolved_from_dict(u) for u in entry.get("unresolved", [])]
        return elements, unresolved

    def put(
        self, relkey: str, content_hash: str, elements: list[Element], unresolved: list[Unresolved]
    ) -> None:
        self._next[relkey] = {
            "hash": content_hash,
            "elements": [element_to_dict(e) for e in elements],
            "unresolved": [unresolved_to_dict(u) for u in unresolved],
        }

    def reuse(self, relkey: str) -> None:
        """Carry forward an entry already validated via `get()` this run."""
        if relkey in self._data:
            self._next[relkey] = self._data[relkey]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._next, sort_keys=True), encoding="utf-8")
