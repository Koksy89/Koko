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
# runtime/<run_id>/ (run.json, events.jsonl, verdicts.jsonl, narrative.jsonl):
# those need a run_id and belong to phase A of this card, not phase B.
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
                      "barriers", "slices", "findings", "changes", "records", "intents"):
            for record in self.raw.get(name, ()):
                rid = record.get("id")
                if isinstance(rid, str):
                    ids.add(rid)
        return ids
