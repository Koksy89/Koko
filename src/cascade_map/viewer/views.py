"""Read-only views over a loaded :class:`ArtifactStore`.

Every function here renders facts that already exist in the artifacts. None
of them compute a new edge, slice, confidence or verdict -- that would be
analysis logic living in the viewer, which is a defect: it creates a second
source of truth that will drift from cards 1-16.

Every view returns a plain, JSON-serializable structure. Every element,
order node, decision, finding, change and doc record it names carries its
own ``id`` (or ``element_id``), so any caller can pass that id to
:func:`element_detail` and land on the same hub every other view would land
on -- that reachability is the point of the viewer.
"""

from __future__ import annotations

from typing import Any

from cascade_map.contracts.interfaces import Confidence

from .loader import ArtifactStore

# Confidence declares CERTAIN first, UNKNOWN last. Rank 0 is the strongest,
# read directly off the enum so this cannot drift from the contract.
_CONFIDENCE_RANK: dict[str, int] = {c.value: i for i, c in enumerate(Confidence)}
_WORST_RANK = len(_CONFIDENCE_RANK)


def _rank(confidence: str | None) -> int:
    return _CONFIDENCE_RANK.get(confidence or "", _WORST_RANK)


def _meets_min_confidence(confidence: str | None, minimum: str | None) -> bool:
    if minimum is None:
        return True
    return _rank(confidence) <= _rank(minimum)


def _confidence_of(record: dict[str, Any]) -> str | None:
    prov = record.get("provenance") or {}
    return prov.get("confidence")


def _method_of(record: dict[str, Any]) -> str | None:
    prov = record.get("provenance") or {}
    return prov.get("method")


# ---------------------------------------------------------------------------
# Element browser
# ---------------------------------------------------------------------------

#: Named, precomputed signals a caller may filter the browser by. Each is a
#: direct membership check against a fact some other card already emitted --
#: never a synthesized verdict. There is no single "is this element decision
#: relevant" field in the contract (Element carries no such flag); these are
#: the closest existing facts, exposed as-is. See the card's final report.
DECISION_SIGNALS = (
    "reads_in_decision",  # id appears in some DecisionPoint.reads_ids
    "reaches_sink",  # a forward Slice rooted here has reaches_sink_ids
    "decision_irrelevant_finding",  # a DECISION_IRRELEVANT finding names it
    "unreachable_finding",  # an UNREACHABLE_ELEMENT finding names it
)


def _has_decision_signal(store: ArtifactStore, element_id: str, signal: str) -> bool:
    if signal == "reads_in_decision":
        return element_id in store.decisions_reading
    if signal == "reaches_sink":
        for s in store.slices_by_root.get((element_id, "forward"), ()):
            if s.get("reaches_sink_ids"):
                return True
        return False
    if signal == "decision_irrelevant_finding":
        return any(
            f.get("kind") == "DECISION_IRRELEVANT"
            for f in store.findings_by_element.get(element_id, ())
        )
    if signal == "unreachable_finding":
        return any(
            f.get("kind") == "UNREACHABLE_ELEMENT"
            for f in store.findings_by_element.get(element_id, ())
        )
    raise ValueError(f"unknown decision signal: {signal!r}")


def element_summary(element: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": element.get("id"),
        "kind": element.get("kind"),
        "name": element.get("name"),
        "qualname": element.get("qualname"),
        "module": element.get("module"),
        "confidence": _confidence_of(element),
        "method": _method_of(element),
        "span": element.get("span"),
    }


def browser_view(
    store: ArtifactStore,
    *,
    kind: str | None = None,
    module: str | None = None,
    min_confidence: str | None = None,
    query: str | None = None,
    decision_signal: str | None = None,
) -> list[dict[str, Any]]:
    """The element browser: searchable and filterable over ``elements.jsonl``."""
    query_lower = query.lower() if query else None
    results: list[dict[str, Any]] = []
    for element in store.raw["elements"]:
        if kind is not None and element.get("kind") != kind:
            continue
        if module is not None:
            m = element.get("module") or ""
            if m != module and not m.startswith(module + "."):
                continue
        if not _meets_min_confidence(_confidence_of(element), min_confidence):
            continue
        if query_lower is not None:
            haystack = " ".join(
                str(element.get(f, "")) for f in ("id", "name", "qualname", "module", "docstring")
            ).lower()
            if query_lower not in haystack:
                continue
        if decision_signal is not None:
            if not _has_decision_signal(store, element.get("id", ""), decision_signal):
                continue
        results.append(element_summary(element))
    results.sort(key=lambda r: r["id"] or "")
    return results


# ---------------------------------------------------------------------------
# Cascade order -- branches, merges, loops and unordered sets, never flattened
# ---------------------------------------------------------------------------


def _order_node_view(store: ArtifactStore, node_id: str, seen: set[str]) -> dict[str, Any]:
    node = store.order_by_id.get(node_id)
    if node is None:
        return {"id": node_id, "missing": True}
    if node_id in seen:
        # A CYCLE node may legitimately name itself in its own children chain
        # via a back edge; stop recursing rather than looping forever, and
        # say so instead of silently truncating.
        return {"id": node_id, "kind": node.get("kind"), "cycle_back_reference": True}
    seen = seen | {node_id}
    decisions = [
        d for eid in node.get("element_ids") or () for d in store.decisions_by_element.get(eid, ())
    ]
    return {
        "id": node_id,
        "kind": node.get("kind"),
        "element_ids": list(node.get("element_ids") or ()),
        "confidence": _confidence_of(node) if node.get("provenance") else None,
        "decision_ids": sorted({d["id"] for d in decisions if "id" in d}),
        "children": [_order_node_view(store, c, seen) for c in node.get("children") or ()],
    }


def cascade_view(store: ArtifactStore) -> list[dict[str, Any]]:
    """The cascade in execution order, as the tree ``order.jsonl`` describes.

    ``OrderKind`` is rendered verbatim -- BRANCH, MERGE, LOOP, UNORDERED and
    CYCLE nodes keep their shape. Flattening any of them into a SEQUENCE
    here would be exactly the defect the contract calls out.
    """
    return [_order_node_view(store, root_id, set()) for root_id in store.order_roots]


def decision_point_view(store: ArtifactStore, decision_id: str) -> dict[str, Any] | None:
    d = store.decisions_by_id.get(decision_id)
    if d is None:
        return None
    return {
        "id": d["id"],
        "element_id": d.get("element_id"),
        "condition_source": d.get("condition_source"),
        "reads_ids": list(d.get("reads_ids") or ()),
        "outcomes": [list(o) for o in d.get("outcomes") or ()],
        "is_sink": d.get("is_sink", False),
        "confidence": _confidence_of(d) if d.get("provenance") else None,
    }


# ---------------------------------------------------------------------------
# Call graph navigation
# ---------------------------------------------------------------------------


def _edge_view(edge: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": edge.get("id"),
        "kind": edge.get("kind"),
        "source_id": edge.get("source_id"),
        "target_id": edge.get("target_id"),
        "method": _method_of(edge),
        "confidence": _confidence_of(edge),
        "call_site": edge.get("call_site"),
    }


def _unresolved_view(u: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": u.get("id"),
        "reason": u.get("reason"),
        "span": u.get("span"),
        "description": u.get("description"),
        "attempted": list(u.get("attempted") or ()),
        "candidate_ids": list(u.get("candidate_ids") or ()),
        "candidate_confidence": u.get("candidate_confidence"),
    }


def callgraph_view(store: ArtifactStore, *, root_id: str | None = None) -> dict[str, Any]:
    """Edges with method and confidence, and unresolved call sites with their
    candidate sets. Filtered to one element's neighbourhood when *root_id* is
    given; otherwise the whole graph."""
    if root_id is None:
        edges = store.raw["edges"]
        unresolved = store.raw["unresolved"]
    else:
        edges = store.edges_out.get(root_id, []) + store.edges_in.get(root_id, [])
        element = store.elements_by_id.get(root_id) or {}
        path = ((element.get("span") or {}).get("path"))
        unresolved = list(store.unresolved_by_candidate.get(root_id, []))
        if path:
            for u in store.unresolved_by_path.get(path, []):
                if u not in unresolved:
                    unresolved.append(u)
    edges_sorted = sorted((_edge_view(e) for e in edges), key=lambda e: e["id"] or "")
    unresolved_sorted = sorted((_unresolved_view(u) for u in unresolved), key=lambda u: u["id"] or "")
    return {"root_id": root_id, "edges": edges_sorted, "unresolved": unresolved_sorted}


# ---------------------------------------------------------------------------
# Lineage: backward / forward slices
# ---------------------------------------------------------------------------


def lineage_view(store: ArtifactStore, root_id: str, direction: str) -> dict[str, Any]:
    """A precomputed :class:`Slice` for *root_id* / *direction*, verbatim.

    The viewer does not walk lineage edges itself -- card 4 already computed
    the slice with its own evidence and confidence. If no slice was emitted
    for this root and direction, that is reported as a gap, not silently
    filled in by a BFS here.
    """
    matches = store.slices_by_root.get((root_id, direction), [])
    if not matches:
        return {
            "root_id": root_id,
            "direction": direction,
            "found": False,
            "note": "no precomputed slice for this root/direction in slices.jsonl",
        }
    if len(matches) > 1:
        note = f"{len(matches)} slices found for this root/direction; all are shown"
    else:
        note = ""
    return {
        "root_id": root_id,
        "direction": direction,
        "found": True,
        "note": note,
        "slices": [
            {
                "id": s["id"],
                "member_ids": list(s.get("member_ids") or ()),
                "edge_ids": list(s.get("edge_ids") or ()),
                "barrier_ids": list(s.get("barrier_ids") or ()),
                "reaches_sink_ids": list(s.get("reaches_sink_ids") or ()),
                "confidence": s.get("confidence"),
            }
            for s in matches
        ],
    }


# ---------------------------------------------------------------------------
# Findings with evidence chains
# ---------------------------------------------------------------------------


def findings_view(store: ArtifactStore, *, kind: str | None = None) -> list[dict[str, Any]]:
    findings = store.raw["findings"]
    if kind is not None:
        findings = [f for f in findings if f.get("kind") == kind]
    out = []
    for f in findings:
        out.append(
            {
                "id": f.get("id"),
                "kind": f.get("kind"),
                "element_id": f.get("element_id"),
                "span": f.get("span"),
                "summary": f.get("summary"),
                "hint": f.get("hint"),
                "evidence_ids": list(f.get("evidence_ids") or ()),
                "confidence": _confidence_of(f),
                "method": _method_of(f),
            }
        )
    out.sort(key=lambda f: f["id"] or "")
    return out


# ---------------------------------------------------------------------------
# Version diff, ranked by decision impact
# ---------------------------------------------------------------------------


def diff_view(store: ArtifactStore) -> list[dict[str, Any]]:
    """``changes.jsonl`` joined to ``impacts.jsonl``, ordered by rank.

    ``Impact.rank`` is card 6's own ranking by decision impact; the viewer
    sorts by that field verbatim rather than recomputing an order.
    """
    changes_by_id = {c["id"]: c for c in store.raw["changes"] if "id" in c}
    rows: list[dict[str, Any]] = []
    for impact in store.raw["impacts"]:
        change = changes_by_id.get(impact.get("change_id", ""))
        rows.append(
            {
                "change_id": impact.get("change_id"),
                "rank": impact.get("rank"),
                "change_kind": change.get("kind") if change else None,
                "before_id": change.get("before_id") if change else None,
                "after_id": change.get("after_id") if change else None,
                "decision_paths_changed": impact.get("decision_paths_changed"),
                "affected_ids": list(impact.get("affected_ids") or ()),
                "features_changed": list(impact.get("features_changed") or ()),
                "reachability_flipped": list(impact.get("reachability_flipped") or ()),
                "findings_added": list(impact.get("findings_added") or ()),
                "findings_removed": list(impact.get("findings_removed") or ()),
            }
        )
    changed_ids_with_impact = {r["change_id"] for r in rows}
    for change in store.raw["changes"]:
        if change.get("id") not in changed_ids_with_impact:
            rows.append(
                {
                    "change_id": change.get("id"),
                    "rank": None,
                    "change_kind": change.get("kind"),
                    "before_id": change.get("before_id"),
                    "after_id": change.get("after_id"),
                    "decision_paths_changed": None,
                    "affected_ids": [],
                    "features_changed": [],
                    "reachability_flipped": [],
                    "findings_added": [],
                    "findings_removed": [],
                    "note": "no impact record emitted for this change",
                }
            )
    # Unranked (no impact record) sorts after ranked changes; ranked changes
    # sort by rank ascending -- rank 1 is the highest decision impact.
    rows.sort(key=lambda r: (r["rank"] is None, r["rank"] if r["rank"] is not None else 0, r["change_id"] or ""))
    return rows


# ---------------------------------------------------------------------------
# Documentation record: facts vs model-written prose, kept visibly separate
# ---------------------------------------------------------------------------


def doc_record_view(store: ArtifactStore, element_id: str) -> dict[str, Any] | None:
    record = store.records_by_element.get(element_id)
    if record is None:
        return None
    return {
        "id": record.get("id"),
        "element_id": record.get("element_id"),
        "facts": {
            "identity": record.get("identity") or {},
            "cascade_position": record.get("cascade_position") or {},
            "data_role": record.get("data_role") or {},
            "decision_relevance": record.get("decision_relevance") or {},
            "runtime": record.get("runtime") or {},
        },
        "finding_ids": list(record.get("finding_ids") or ()),
        "change_ids": list(record.get("change_ids") or ()),
        "confidence": _confidence_of(record),
        "method": _method_of(record),
        "model_authored": {
            "prose": record.get("model_prose") or "",
            "model_id": record.get("model_id") or "",
            "is_present": bool(record.get("model_prose")),
        },
    }


# ---------------------------------------------------------------------------
# Element detail: the drill-down hub every view lands on
# ---------------------------------------------------------------------------


def _order_ancestors(store: ArtifactStore, node_id: str) -> list[str]:
    chain: list[str] = []
    current = store.order_parent.get(node_id)
    seen = {node_id}
    while current is not None and current not in seen:
        chain.append(current)
        seen.add(current)
        current = store.order_parent.get(current)
    return chain


def element_detail(store: ArtifactStore, element_id: str) -> dict[str, Any]:
    """Everything the artifacts say about one element ID.

    Every other view names element IDs; this is where each of them resolves,
    so drill-down is total: any ID mentioned anywhere is reachable here.
    """
    element = store.elements_by_id.get(element_id)
    order_node_ids = store.order_containing_element.get(element_id, [])
    slices_rooted = {
        direction: [
            s["id"] for s in store.slices_by_root.get((element_id, direction), ())
        ]
        for direction in ("backward", "forward")
    }
    return {
        "id": element_id,
        "found": element is not None,
        "element": element_summary(element) if element else None,
        "outgoing_edges": sorted(
            (_edge_view(e) for e in store.edges_out.get(element_id, [])),
            key=lambda e: e["id"] or "",
        ),
        "incoming_edges": sorted(
            (_edge_view(e) for e in store.edges_in.get(element_id, [])),
            key=lambda e: e["id"] or "",
        ),
        "unresolved_as_candidate": sorted(
            (_unresolved_view(u) for u in store.unresolved_by_candidate.get(element_id, [])),
            key=lambda u: u["id"] or "",
        ),
        "order_node_ids": order_node_ids,
        "order_ancestor_ids": {
            nid: _order_ancestors(store, nid) for nid in order_node_ids
        },
        "decision_as_condition": sorted(
            d["id"] for d in store.decisions_by_element.get(element_id, []) if "id" in d
        ),
        "decision_reads_this": sorted(
            d["id"] for d in store.decisions_reading.get(element_id, []) if "id" in d
        ),
        "lineage_out": sorted(
            store.lineage_out.get(element_id, []), key=lambda e: e.get("id") or ""
        ),
        "lineage_in": sorted(
            store.lineage_in.get(element_id, []), key=lambda e: e.get("id") or ""
        ),
        "barrier_ids": sorted(
            b["id"] for b in store.barriers_by_element.get(element_id, []) if "id" in b
        ),
        "slice_ids_rooted_here": slices_rooted,
        "slice_ids_as_member": sorted(
            {s["id"] for s in store.slices_by_member.get(element_id, []) if "id" in s}
        ),
        "finding_ids": sorted(
            f["id"] for f in store.findings_by_element.get(element_id, []) if "id" in f
        ),
        "finding_ids_as_evidence": sorted(
            {f["id"] for f in store.findings_by_evidence.get(element_id, []) if "id" in f}
        ),
        "change_ids_before": sorted(
            c["id"] for c in store.changes_by_before.get(element_id, []) if "id" in c
        ),
        "change_ids_after": sorted(
            c["id"] for c in store.changes_by_after.get(element_id, []) if "id" in c
        ),
        "doc_record": doc_record_view(store, element_id),
        "intent_ids": sorted(
            i["id"] for i in store.intents_by_element.get(element_id, []) if "id" in i
        ),
    }
