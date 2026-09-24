"""Read-only loader for CASCADE-MAP artifacts.

The viewer never derives a fact. Every view is built from a direct parse of
the JSONL/JSON files an analysis card emitted, indexed here for lookup. A
missing file degrades the views that need it -- reported through
:attr:`ArtifactStore.available`, never silently rendered as "nothing here".

This module never reads ``target_engine/`` or ``target_versions/`` and never
imports, execs or unpickles anything. It parses JSON text with the stdlib
``json`` module only.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# name -> filename, relative to the artifact root. Matches every top-level
# file in ARCHITECTURE.md's output layout except the files that live under
# runtime/<run_id>/ (run.json, events.jsonl, contradictions.jsonl,
# nondeterminism.jsonl, mapping.json, verdicts.jsonl, narrative.jsonl):
# those need a run_id and are loaded by RuntimeStore, phase A of this card.
ARTIFACT_FILES: dict[str, str] = {
    "elements": "elements.jsonl",
    "unresolved": "unresolved.jsonl",
    "edges": "edges.jsonl",
    "cfg_blocks": "cfg_blocks.jsonl",
    "cfg_edges": "cfg_edges.jsonl",
    "order": "order.jsonl",
    "decisions": "decisions.jsonl",
    "reachability": "reachability.jsonl",
    "lineage": "lineage.jsonl",
    "barriers": "barriers.jsonl",
    "slices": "slices.jsonl",
    "findings": "findings.jsonl",
    "changes": "changes.jsonl",
    "impacts": "impacts.jsonl",
    "records": "records.jsonl",
    "intents": "intents.jsonl",
    # Card 13's STATIC verdicts -- what the map can say about an intent with
    # nothing executed. The runtime verdicts for one run live under
    # runtime/<run_id>/ and are loaded by `RuntimeStore`; these two are
    # different evidence about the same intents and are never merged into one
    # list, because "the call graph says so" and "the run showed it" are not
    # the same claim.
    "static_verdicts": "verdicts.jsonl",
}


@dataclass(frozen=True, slots=True)
class LoadError:
    """A line the loader could not parse. Reported, never dropped silently."""

    file: str
    line_number: int
    reason: str


def _read_jsonl(path: Path, file_label: str, errors: list[LoadError]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    text = path.read_text(encoding="utf-8")
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(LoadError(file=file_label, line_number=line_number, reason=str(exc)))
            continue
        if not isinstance(record, dict):
            errors.append(
                LoadError(
                    file=file_label,
                    line_number=line_number,
                    reason=f"expected a JSON object, got {type(record).__name__}",
                )
            )
            continue
        records.append(record)
    return records


def _multi_index(records: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        value = record.get(key)
        if isinstance(value, str) and value:
            index[value].append(record)
    return dict(index)


def _multi_index_many(records: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    """Index by a field that holds a list of ids (e.g. evidence_ids)."""
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        for value in record.get(key) or ():
            if isinstance(value, str) and value:
                index[value].append(record)
    return dict(index)


def _read_json_object(
    path: Path, file_label: str, errors: list[LoadError]
) -> dict[str, Any] | None:
    """Parse a single-object JSON artifact (``run.json``, ``mapping.json``).

    Line number 0 marks a whole-file error, matching how ``manifest.json``
    (not a per-line artifact either) already reports a parse failure.
    """
    text = path.read_text(encoding="utf-8")
    try:
        record = json.loads(text)
    except json.JSONDecodeError as exc:
        errors.append(LoadError(file=file_label, line_number=0, reason=str(exc)))
        return None
    if not isinstance(record, dict):
        errors.append(
            LoadError(
                file=file_label,
                line_number=0,
                reason=f"expected a JSON object, got {type(record).__name__}",
            )
        )
        return None
    return record


@dataclass
class ArtifactStore:
    """All artifacts for one ``out/<label>/`` run, loaded and indexed.

    Every field is read directly off disk. Nothing here is computed by
    analysis -- indices are lookups (by id, by path, by membership in an
    already-emitted list), not new facts.
    """

    root: Path
    available: dict[str, bool] = field(default_factory=dict)
    errors: list[LoadError] = field(default_factory=list)
    raw: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)

    # indices, populated by _build_indices()
    elements_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges_out: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    edges_in: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    unresolved_by_path: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    unresolved_by_candidate: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    order_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    order_containing_element: dict[str, list[str]] = field(default_factory=dict)
    order_parent: dict[str, str] = field(default_factory=dict)
    order_roots: list[str] = field(default_factory=list)
    decisions_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    decisions_by_element: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    decisions_reading: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    reachability_by_element: dict[str, dict[str, Any]] = field(default_factory=dict)
    lineage_out: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    lineage_in: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    barriers_by_element: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    slices_by_root: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    slices_by_member: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    findings_by_element: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    findings_by_evidence: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    changes_by_before: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    changes_by_after: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    impacts_by_change: dict[str, dict[str, Any]] = field(default_factory=dict)
    records_by_element: dict[str, dict[str, Any]] = field(default_factory=dict)
    intents_by_element: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    intents_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    static_verdicts_by_element: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    @classmethod
    def load(cls, root: str | Path) -> "ArtifactStore":
        root = Path(root)
        store = cls(root=root)
        errors: list[LoadError] = []

        manifest_path = root / "manifest.json"
        if manifest_path.exists():
            try:
                store.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                errors.append(LoadError(file="manifest.json", line_number=0, reason=str(exc)))

        for name, filename in ARTIFACT_FILES.items():
            path = root / filename
            if path.exists():
                store.available[name] = True
                store.raw[name] = _read_jsonl(path, filename, errors)
            else:
                store.available[name] = False
                store.raw[name] = []

        store.errors = errors
        store._build_indices()
        return store

    # -- indexing -----------------------------------------------------

    def _build_indices(self) -> None:
        self.elements_by_id = {e["id"]: e for e in self.raw["elements"] if "id" in e}

        edges = self.raw["edges"]
        self.edges_out = _multi_index(edges, "source_id")
        self.edges_in = _multi_index(edges, "target_id")

        for u in self.raw["unresolved"]:
            span = u.get("span") or {}
            p = span.get("path")
            if isinstance(p, str) and p:
                self.unresolved_by_path.setdefault(p, []).append(u)
            for cid in u.get("candidate_ids") or ():
                self.unresolved_by_candidate.setdefault(cid, []).append(u)

        order_nodes = self.raw["order"]
        self.order_by_id = {n["id"]: n for n in order_nodes if "id" in n}
        for n in order_nodes:
            node_id = n.get("id")
            for eid in n.get("element_ids") or ():
                self.order_containing_element.setdefault(eid, []).append(node_id)
            for child in n.get("children") or ():
                self.order_parent[child] = node_id
        all_children = set(self.order_parent.keys())
        self.order_roots = sorted(
            nid for nid in self.order_by_id if nid not in all_children
        )

        decisions = self.raw["decisions"]
        self.decisions_by_id = {d["id"]: d for d in decisions if "id" in d}
        self.decisions_by_element = _multi_index(decisions, "element_id")
        self.decisions_reading = _multi_index_many(decisions, "reads_ids")

        # One Reachability record per element (card 3). If card 3 ever emits
        # more than one for the same element, the first in id order wins,
        # deterministically -- the viewer does not adjudicate between them.
        for r in self.raw["reachability"]:
            eid = r.get("element_id")
            if isinstance(eid, str) and eid and eid not in self.reachability_by_element:
                self.reachability_by_element[eid] = r

        lineage = self.raw["lineage"]
        self.lineage_out = _multi_index(lineage, "source_id")
        self.lineage_in = _multi_index(lineage, "target_id")

        self.barriers_by_element = _multi_index(self.raw["barriers"], "element_id")

        for s in self.raw["slices"]:
            root_id = s.get("root_id")
            direction = s.get("direction")
            if root_id and direction:
                self.slices_by_root.setdefault((root_id, direction), []).append(s)
            for member in s.get("member_ids") or ():
                self.slices_by_member.setdefault(member, []).append(s)

        findings = self.raw["findings"]
        self.findings_by_element = _multi_index(findings, "element_id")
        self.findings_by_evidence = _multi_index_many(findings, "evidence_ids")

        changes = self.raw["changes"]
        self.changes_by_before = _multi_index(changes, "before_id")
        self.changes_by_after = _multi_index(changes, "after_id")

        self.impacts_by_change = {
            i["change_id"]: i for i in self.raw["impacts"] if i.get("change_id")
        }

        self.records_by_element = {
            r["element_id"]: r for r in self.raw["records"] if r.get("element_id")
        }

        self.intents_by_element = _multi_index(self.raw["intents"], "element_id")
        self.intents_by_id = {i["id"]: i for i in self.raw["intents"] if "id" in i}
        self.static_verdicts_by_element = _multi_index(
            self.raw["static_verdicts"], "element_id"
        )

    # -- convenience ----------------------------------------------------

    def element(self, element_id: str) -> dict[str, Any] | None:
        return self.elements_by_id.get(element_id)

    def known_ids(self) -> set[str]:
        """Every ID this store has ever seen, from any artifact -- used to
        check that drill-down targets actually resolve somewhere."""
        ids: set[str] = set(self.elements_by_id)
        ids.update(self.order_by_id)
        ids.update(self.decisions_by_id)
        ids.update(self.impacts_by_change)
        for name in ("unresolved", "cfg_blocks", "cfg_edges", "reachability", "lineage",
                      "barriers", "slices", "findings", "changes", "records", "intents",
                      "static_verdicts"):
            for record in self.raw.get(name, ()):
                rid = record.get("id")
                if isinstance(rid, str):
                    ids.add(rid)
        return ids


# ---------------------------------------------------------------------------
# Phase A -- the runtime overlay, keyed by run_id
# ---------------------------------------------------------------------------

# Single-object JSON artifacts under runtime/<run_id>/.
RUNTIME_JSON_FILES: dict[str, str] = {
    "run": "run.json",
    "mapping": "mapping.json",
}

# JSONL artifacts under runtime/<run_id>/.
RUNTIME_JSONL_FILES: dict[str, str] = {
    "events": "events.jsonl",
    "contradictions": "contradictions.jsonl",
    "nondeterminism": "nondeterminism.jsonl",
    "verdicts": "verdicts.jsonl",
    "narrative": "narrative.jsonl",
}


def list_runs(root: str | Path) -> list[str]:
    """Every run_id with a directory under ``root/runtime/``, sorted.

    A directory listing only -- it does not validate contents or pick a
    "latest" run (the contract carries no reliable, non-clock signal for
    that; ``run_meta.json`` is explicitly not byte-compared). The reader --
    a human or card 10 -- picks which run to load with
    :meth:`RuntimeStore.load`.
    """
    runtime_dir = Path(root) / "runtime"
    if not runtime_dir.is_dir():
        return []
    return sorted(p.name for p in runtime_dir.iterdir() if p.is_dir())


@dataclass
class RuntimeStore:
    """One run's overlay -- ``runtime/<run_id>/*``, loaded and indexed.

    Mirrors :class:`ArtifactStore` exactly: every field is read directly
    off disk, every index is a lookup over records already emitted by
    cards 11-14 (never a new fact), a missing file degrades
    (``available[name] = False``) rather than raising, and a malformed
    line is reported as a :class:`LoadError`, never silently dropped.
    """

    root: Path
    run_id: str
    available: dict[str, bool] = field(default_factory=dict)
    errors: list[LoadError] = field(default_factory=list)
    raw: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    run_record: dict[str, Any] | None = None
    mapping_report: dict[str, Any] | None = None

    events_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    events_by_element: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    events_ordered: list[dict[str, Any]] = field(default_factory=list)
    unmapped_events: list[dict[str, Any]] = field(default_factory=list)
    contradictions_by_element: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    nondeterminism_by_element: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    verdicts_by_element: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    verdicts_by_intent: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    narrative_steps: list[dict[str, Any]] = field(default_factory=list)
    narrative_by_element: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    narrative_by_event: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    @classmethod
    def load(cls, root: str | Path, run_id: str) -> "RuntimeStore":
        root = Path(root)
        rstore = cls(root=root, run_id=run_id)
        rundir = root / "runtime" / run_id
        errors: list[LoadError] = []

        for name, filename in RUNTIME_JSON_FILES.items():
            path = rundir / filename
            label = f"runtime/{run_id}/{filename}"
            if path.exists():
                rstore.available[name] = True
                obj = _read_json_object(path, label, errors)
                if name == "run":
                    rstore.run_record = obj
                else:
                    rstore.mapping_report = obj
            else:
                rstore.available[name] = False

        for name, filename in RUNTIME_JSONL_FILES.items():
            path = rundir / filename
            label = f"runtime/{run_id}/{filename}"
            if path.exists():
                rstore.available[name] = True
                rstore.raw[name] = _read_jsonl(path, label, errors)
            else:
                rstore.available[name] = False
                rstore.raw[name] = []

        rstore.errors = errors
        rstore._build_indices()
        return rstore

    def _build_indices(self) -> None:
        events = self.raw.get("events", [])
        self.events_by_id = {e["event_id"]: e for e in events if "event_id" in e}
        self.events_by_element = _multi_index(events, "element_id")
        # Observed execution order is sequence, not event_id -- the two are
        # expected to agree (card 12 mints event_id in trace order) but
        # sequence is the field the contract defines as the order.
        # Filtered to records that have an event_id, matching events_by_id --
        # a record missing its required id cannot be looked up by one, so it
        # is excluded here the same way a nameless element is excluded from
        # ArtifactStore.elements_by_id (never from .raw, which keeps it).
        self.events_ordered = sorted(
            (e for e in events if "event_id" in e),
            key=lambda e: (e.get("sequence", 0), e.get("event_id", "")),
        )
        self.unmapped_events = [e for e in events if e.get("kind") == "UNMAPPED"]

        self.contradictions_by_element = _multi_index(
            self.raw.get("contradictions", []), "element_id"
        )
        self.nondeterminism_by_element = _multi_index(
            self.raw.get("nondeterminism", []), "element_id"
        )

        verdicts = self.raw.get("verdicts", [])
        self.verdicts_by_element = _multi_index(verdicts, "element_id")
        self.verdicts_by_intent = _multi_index(verdicts, "intent_id")

        narrative = self.raw.get("narrative", [])
        self.narrative_steps = sorted(
            narrative, key=lambda s: (s.get("sequence", 0), s.get("id", ""))
        )
        self.narrative_by_element = _multi_index_many(narrative, "element_ids")
        self.narrative_by_event = _multi_index_many(narrative, "event_ids")

    def known_ids(self) -> set[str]:
        """Every ID this run's overlay has ever seen -- used the same way
        :meth:`ArtifactStore.known_ids` is, to check drill-down targets."""
        ids: set[str] = set(self.events_by_id)
        for name in ("contradictions", "nondeterminism", "verdicts", "narrative"):
            for record in self.raw.get(name, ()):
                rid = record.get("id")
                if isinstance(rid, str):
                    ids.add(rid)
        return ids
