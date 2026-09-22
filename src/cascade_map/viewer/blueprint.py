"""Card 15, phase C -- the blueprint canvas.

A single, self-contained, offline HTML page rendering three interactive,
UE5-Blueprint-styled node canvases (execution, lineage, diff) sharing one
camera and one selection, switched by tabs.

This module contains **no analysis**. Every node, wire, confidence,
reachability state, change and runtime fact rendered here is read verbatim
from an artifact already loaded by :mod:`.loader` and, wherever possible,
already shaped by :mod:`.views` -- the same read-only views phase B uses.
The only computation this module performs that is not a direct field lookup
is *layout*: which layer (column) a node is drawn in. That is presentation,
not analysis -- it carries no confidence, asserts no new fact, and has no
effect on any other view. Final x/y pixel placement happens in the browser,
in JavaScript, from the embedded (fixed) layer numbers; nothing about pixel
position is baked into the emitted bytes, so it cannot affect the
byte-identical guarantee.

Determinism: every list is built from ``sorted(...)`` before it is emitted;
nothing here reads the clock, an environment variable, or iterates a
``set``/``dict`` into output without sorting first. The JSON data island is
serialized with sorted keys and ASCII escaping, then further escaped so that
no ``<``, ``>`` or ``&`` character can appear literally inside the
``<script>`` tag that carries it -- see :func:`_safe_json`.

Never reads ``target_engine/`` or ``target_versions/`` and never imports,
execs, evals or unpickles anything.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cascade_map.contracts.interfaces import Confidence

from . import views
from .loader import ArtifactStore, RuntimeStore, list_runs

# ---------------------------------------------------------------------------
# Constants shared between the Python data model and the embedded JS.
# ---------------------------------------------------------------------------

#: The node kinds the EXECUTION and DIFF canvases draw as ordinary elements.
#: Decision points (card 3's DecisionPoint) are drawn separately, as diamonds.
EXECUTION_KINDS = ("MODULE", "CLASS", "FUNCTION", "METHOD")

#: card 4's id-minting prefixes (see contracts/interfaces.py: feature_id,
#: file_id, config_key_id, local_id, param_id, key_id, attr_id). Recognising
#: them here only decodes an already-documented ID grammar for a node label
#: -- it asserts no new fact and carries no confidence.
_FEATURE_PREFIX = "@feature:"
_FILE_PREFIX = "@file:"

#: Above this many nodes in a tab, every module starts collapsed by default.
#: Chosen so the fixture corpus (tens of elements) renders expanded, and a
#: target the size of the real engine (~thousands of elements) opens in a
#: navigable state rather than a wall of cards. The owner can always expand.
MODULE_COLLAPSE_THRESHOLD = 150

#: What the wire and node encodings mean, in words -- rendered as an
#: always-visible legend, because a viewer that renders a HEURISTIC guess
#: identically to a CERTAIN fact defeats the one thing this tool is for.
LEGEND = {
    "confidence": [
        {"level": "CERTAIN", "style": "solid, thickest, brightest -- read directly off the AST"},
        {"level": "RESOLVED", "style": "solid -- one deterministic outcome"},
        {"level": "PROBABLE", "style": "dashed -- resolution rests on an assumption"},
        {"level": "HEURISTIC", "style": "dotted, amber -- pattern/name/config match, may be wrong"},
        {"level": "UNKNOWN", "style": "dotted, red -- not resolved"},
    ],
    "reachability": [
        {"state": "REACHES_SINK", "style": "accented border + → sink badge"},
        {"state": "NO_SINK_PATH", "style": "dimmed + no-path badge -- NOT the same as deleted"},
        {"state": "UNKNOWN", "style": "dotted border + ? badge -- could not tell, not a verdict"},
    ],
}


# ---------------------------------------------------------------------------
# Safe embedding of the JSON data island
# ---------------------------------------------------------------------------


def _safe_json(obj: Any) -> str:
    """Serialize *obj* for embedding inside a ``<script type="application/json">``.

    ``json.dumps`` already escapes quotes, backslashes and control
    characters; ``sort_keys``/``ensure_ascii`` give determinism. The extra
    pass below neutralises ``<``, ``>`` and ``&`` -- none of which are ever
    JSON structural characters, only ever content -- so a docstring or
    condition string containing ``</script><img src=x onerror=alert(1)>``
    can never break out of the surrounding tag or inject markup. The
    browser's ``JSON.parse`` reads ``\\u003c`` back as the literal ``<``
    character; the HTML parser never sees a literal ``<`` in the script
    body at all.
    """
    text = json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return text.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


# ---------------------------------------------------------------------------
# Node views shared across tabs
# ---------------------------------------------------------------------------


def _element_node(store: ArtifactStore, element_id: str) -> dict[str, Any]:
    """A node view for one element id, real or unresolved -- never a crash."""
    element = store.elements_by_id.get(element_id)
    reach = views.element_reachability(store, element_id)
    finding_count = len(store.findings_by_element.get(element_id, []))
    has_record = element_id in store.records_by_element
    if element is None:
        return {
            "id": element_id, "is_element": False, "kind": "UNKNOWN",
            "name": element_id, "qualname": element_id, "module": "",
            "confidence": None, "method": None, "span": None,
            "reachability": reach, "finding_count": finding_count,
            "has_record": has_record,
        }
    summary = views.element_summary(element)
    return {
        "id": element_id, "is_element": True, "kind": summary["kind"],
        "name": summary["name"], "qualname": summary["qualname"] or summary["name"],
        "module": summary["module"] or "", "confidence": summary["confidence"],
        "method": summary["method"], "span": summary["span"],
        "reachability": reach, "finding_count": finding_count, "has_record": has_record,
    }


def _decision_node(store: ArtifactStore, decision: dict[str, Any]) -> dict[str, Any]:
    prov = decision.get("provenance") or {}
    owner = decision.get("element_id", "") or ""
    owner_el = store.elements_by_id.get(owner) or {}
    outcomes = [list(o) for o in decision.get("outcomes") or ()]
    reach = (
        views.element_reachability(store, owner)
        if owner
        else {
            "state": "UNKNOWN", "source": "no_owner",
            "reason": "this DecisionPoint has no element_id", "sink_ids": [], "path_ids": [],
            "confidence": None,
        }
    )
    return {
        "id": decision["id"], "is_element": False, "kind": "DECISION",
        "name": decision["id"], "qualname": decision["id"], "module": owner_el.get("module", ""),
        "confidence": prov.get("confidence"), "method": prov.get("method"), "span": None,
        "reachability": reach, "finding_count": 0, "has_record": False,
        "condition_source": decision.get("condition_source", ""),
        "outcomes": outcomes, "is_sink": bool(decision.get("is_sink", False)),
        "owner_element_id": owner,
    }


def _classify_synthetic(id_: str) -> str:
    """Decode card 4's id-minting grammar for a display kind label.

    Reads only the shape of the id string against the conventions
    ``make_id``/``local_id``/``param_id``/``key_id``/``attr_id``/
    ``feature_id``/``file_id``/``config_key_id`` document in
    ``contracts/interfaces.py`` -- never a new fact, no confidence claimed.
    """
    if id_.startswith(_FEATURE_PREFIX):
        return "FEATURE"
    if id_.startswith(_FILE_PREFIX):
        return "CONFIG_KEY" if "::" in id_[len(_FILE_PREFIX):] else "FILE"
    if ".<locals>." in id_:
        return "LOCAL"
    if ".<param>." in id_:
        return "PARAMETER"
    if id_.endswith("]") and "[" in id_:
        return "CONTAINER_KEY"
    tail = id_.rsplit("::", 1)[-1]
    if "." in tail:
        return "ATTRIBUTE"
    return "UNKNOWN"


def _synthetic_label(id_: str, kind: str) -> str:
    if kind == "FEATURE":
        return id_[len(_FEATURE_PREFIX):]
    if kind in ("FILE", "CONFIG_KEY"):
        return id_[len(_FILE_PREFIX):]
    if kind in ("LOCAL", "PARAMETER", "ATTRIBUTE"):
        return id_.rsplit(".", 1)[-1]
    if kind == "CONTAINER_KEY":
        base, sep, rest = id_.partition("[")
        return base.rsplit("::", 1)[-1] + sep + rest
    return id_


def _lineage_node(store: ArtifactStore, id_: str) -> dict[str, Any]:
    if id_ in store.elements_by_id:
        node = _element_node(store, id_)
        node["synthetic_kind"] = ""
        return node
    kind = _classify_synthetic(id_)
    return {
        "id": id_, "is_element": False, "kind": kind,
        "name": _synthetic_label(id_, kind), "qualname": id_, "module": "",
        "confidence": None, "method": None, "span": None,
        "reachability": views.element_reachability(store, id_),
        "finding_count": 0, "has_record": False, "synthetic_kind": kind,
    }


def _barrier_node(barrier: dict[str, Any]) -> dict[str, Any]:
    reason = barrier.get("reason", "") or ""
    description = barrier.get("description", "") or ""
    return {
        "id": barrier["id"], "is_element": False, "kind": "BARRIER",
        "name": reason or "BARRIER", "qualname": description, "module": "",
        "confidence": None, "method": None, "span": barrier.get("span"),
        "reachability": {
            "state": "NO_SINK_PATH", "source": "barrier",
            "reason": "value flow stops here: " + description, "sink_ids": [], "path_ids": [],
            "confidence": None,
        },
        "finding_count": 0, "has_record": False, "synthetic_kind": "BARRIER",
        "barrier_reason": reason, "barrier_description": description,
    }


def _ghost_node(id_: str) -> dict[str, Any]:
    """A change-referenced id with no `Element` in the loaded graph.

    The label is decoded from ``make_id``'s own ``module::qualname`` shape
    -- the only information a bare id string carries -- not a new fact
    about the element's kind, which this graph cannot know.
    """
    if "::" in id_:
        module, _, qual = id_.partition("::")
    else:
        module, qual = id_, ""
    return {
        "id": id_, "is_element": False, "kind": "UNKNOWN", "name": qual or id_,
        "qualname": qual or id_, "module": module, "confidence": None, "method": None,
        "span": None,
        "reachability": {
            "state": "UNKNOWN", "source": "not_in_this_graph",
            "reason": "not present in the loaded graph -- removed, or this graph is "
                      "the 'before' side of the diff",
            "sink_ids": [], "path_ids": [], "confidence": None,
        },
        "finding_count": 0, "has_record": False, "is_ghost": True,
    }


def _edge_wire(edge: dict[str, Any]) -> dict[str, Any]:
    prov = edge.get("provenance") or {}
    return {
        "id": edge.get("id"), "kind": edge.get("kind"), "source_id": edge.get("source_id"),
        "target_id": edge.get("target_id"), "confidence": prov.get("confidence"),
        "method": prov.get("method"), "call_site": edge.get("call_site"), "outcome_label": "",
    }


# ---------------------------------------------------------------------------
# Layout: which column (layer) a node sits in. Presentation only -- see the
# module docstring. Never a pixel position; that is computed in the browser.
# ---------------------------------------------------------------------------


def _order_depth(store: ArtifactStore, node_id: str) -> int:
    depth = 0
    seen = {node_id}
    current = store.order_parent.get(node_id)
    while current is not None and current not in seen:
        depth += 1
        seen.add(current)
        current = store.order_parent.get(current)
    return depth


def _layers_from_order(store: ArtifactStore) -> dict[str, int]:
    """Element id -> order-tree depth, from ``order.jsonl`` (card 3).

    An element that is a member of more than one order node takes the
    deepest depth seen -- a deterministic reduction over an already-fixed
    set, independent of dict/set iteration order.
    """
    layers: dict[str, int] = {}
    for node_id in sorted(store.order_by_id):
        depth = _order_depth(store, node_id)
        node = store.order_by_id[node_id]
        for eid in node.get("element_ids") or ():
            if depth > layers.get(eid, -1):
                layers[eid] = depth
    return layers


def _longest_path_layers(
    node_ids: list[str], edges: list[tuple[str, str]], base: dict[str, int]
) -> dict[str, int]:
    """Fallback layering for nodes ``_layers_from_order`` did not place.

    A capped, deterministic longest-path relaxation (Bellman-Ford shape)
    over *edges*, restricted to *node_ids*. The cap makes it safe on a
    cyclic call graph -- recursion is real in the target -- without an
    unbounded loop; it simply stops improving once the cap is reached,
    which is an approximation disclosed in the builder's report, not a
    silent wrong answer (a layer number carries no confidence and is never
    read as one).
    """
    layers = dict(base)
    for node_id in node_ids:
        layers.setdefault(node_id, 0)
    cap = min(max(len(node_ids), 1), 500)
    changed = True
    iterations = 0
    while changed and iterations < cap:
        changed = False
        iterations += 1
        for source, target in edges:
            if layers[source] + 1 > layers[target]:
                layers[target] = layers[source] + 1
                changed = True
    return layers


# ---------------------------------------------------------------------------
# EXECUTION tab
# ---------------------------------------------------------------------------


def _build_execution(store: ArtifactStore) -> dict[str, Any]:
    element_ids = sorted(
        eid for eid, element in store.elements_by_id.items()
        if element.get("kind") in EXECUTION_KINDS
    )
    element_id_set = set(element_ids)
    sink_ids = set(store.manifest.get("sink_ids") or ())

    element_nodes = []
    for element_id in element_ids:
        node = _element_node(store, element_id)
        node["is_sink"] = element_id in sink_ids
        node["order_node_ids"] = sorted(store.order_containing_element.get(element_id, []))
        element_nodes.append(node)

    decision_ids = sorted(store.decisions_by_id)
    decision_nodes = [_decision_node(store, store.decisions_by_id[did]) for did in decision_ids]

    nodes = element_nodes + decision_nodes
    node_id_set = {node["id"] for node in nodes}

    order_layers = _layers_from_order(store)
    call_pairs = sorted({
        (edge.get("source_id"), edge.get("target_id"))
        for edge in store.raw["edges"]
        if edge.get("kind") == "CALLS"
        and edge.get("source_id") in element_id_set
        and edge.get("target_id") in element_id_set
    })
    layers = _longest_path_layers(element_ids, call_pairs, order_layers)
    for node in element_nodes:
        node["layer"] = layers.get(node["id"], 0)
    for node in decision_nodes:
        node["layer"] = layers.get(node["owner_element_id"], 0) + 1

    wires: list[dict[str, Any]] = []
    omitted = 0
    for edge in store.raw["edges"]:
        source_id, target_id = edge.get("source_id"), edge.get("target_id")
        if source_id in node_id_set and target_id in node_id_set:
            wires.append(_edge_wire(edge))
        else:
            omitted += 1

    for node in decision_nodes:
        owner = node["owner_element_id"]
        if owner in node_id_set:
            wires.append({
                "id": f"{node['id']}::owner", "kind": "HAS_DECISION", "source_id": owner,
                "target_id": node["id"], "confidence": node["confidence"],
                "method": node["method"], "call_site": None, "outcome_label": "",
            })
        else:
            omitted += 1
        for index, (label, target) in enumerate(node["outcomes"]):
            if target in node_id_set:
                wires.append({
                    "id": f"{node['id']}::outcome::{index}", "kind": "OUTCOME",
                    "source_id": node["id"], "target_id": target,
                    "confidence": node["confidence"], "method": node["method"],
                    "call_site": None, "outcome_label": label,
                })
            else:
                omitted += 1

    return {
        "nodes": nodes,
        "wires": sorted(wires, key=lambda wire: wire["id"] or ""),
        "omitted_wire_count": omitted,
        "omitted_note": (
            "a wire is not drawn when an endpoint is outside "
            "MODULE/CLASS/FUNCTION/METHOD/DECISION, or when a decision outcome names "
            "an order-tree node this canvas does not resolve to a single element -- "
            "the raw target is still visible in that node's or decision's own detail panel"
        ),
    }


# ---------------------------------------------------------------------------
# LINEAGE tab
# ---------------------------------------------------------------------------


def _build_lineage(store: ArtifactStore) -> dict[str, Any]:
    lineage_edges = store.raw["lineage"]
    ids: set[str] = set()
    for edge in lineage_edges:
        source_id, target_id = edge.get("source_id"), edge.get("target_id")
        if source_id:
            ids.add(source_id)
        if target_id:
            ids.add(target_id)
    for element_id, element in store.elements_by_id.items():
        if element.get("kind") == "FEATURE":
            ids.add(element_id)
    barriers = sorted(store.raw["barriers"], key=lambda b: b.get("id") or "")
    for barrier in barriers:
        if barrier.get("element_id"):
            ids.add(barrier["element_id"])

    node_ids = sorted(ids)
    nodes_by_id: dict[str, dict[str, Any]] = {
        node_id: _lineage_node(store, node_id) for node_id in node_ids
    }

    edge_pairs = sorted({
        (edge.get("source_id"), edge.get("target_id"))
        for edge in lineage_edges
        if edge.get("source_id") in ids and edge.get("target_id") in ids
    })
    layers = _longest_path_layers(node_ids, edge_pairs, {})

    wires: list[dict[str, Any]] = []
    for edge in lineage_edges:
        source_id, target_id = edge.get("source_id"), edge.get("target_id")
        if source_id not in ids or target_id not in ids:
            continue
        prov = edge.get("provenance") or {}
        wires.append({
            "id": edge.get("id"), "kind": edge.get("kind"), "source_id": source_id,
            "target_id": target_id, "confidence": prov.get("confidence"),
            "method": prov.get("method"), "span": edge.get("span"), "outcome_label": "",
        })

    for barrier in barriers:
        node = _barrier_node(barrier)
        owner = barrier.get("element_id", "") or ""
        layers[node["id"]] = layers.get(owner, 0) + 1
        # A barrier's own id can coincide with a lineage-edge endpoint id
        # already discovered above (card 4 may use the same local binding as
        # both a lineage edge's target and the barrier record for it). The
        # Barrier record is the more specific fact for that id, so it
        # replaces rather than duplicates -- one node per id, never two.
        nodes_by_id[node["id"]] = node
        if owner in ids:
            wires.append({
                "id": f"{barrier['id']}::wire", "kind": "BARRIER", "source_id": owner,
                "target_id": node["id"], "confidence": None, "method": None,
                "span": barrier.get("span"), "outcome_label": "",
            })

    nodes = sorted(nodes_by_id.values(), key=lambda n: n["id"])
    for node in nodes:
        node["layer"] = layers.get(node["id"], 0)

    return {"nodes": nodes, "wires": sorted(wires, key=lambda wire: wire["id"] or "")}


# ---------------------------------------------------------------------------
# DIFF tab
# ---------------------------------------------------------------------------


def _build_diff(store: ArtifactStore, diff_store: ArtifactStore | None) -> dict[str, Any]:
    if diff_store is None:
        return {
            "available": False,
            "reason": "no diff loaded: run `metatron diff BEFORE_DIR AFTER_DIR --out DIFF_DIR`, "
                      "then re-render with `metatron blueprint GRAPH_DIR --diff DIFF_DIR`",
            "nodes": [], "wires": [], "ranked_changes": [],
        }

    rows = views.diff_view(diff_store)
    changes_by_id = {c.get("id"): c for c in diff_store.raw.get("changes", []) if c.get("id")}

    changed_ids: set[str] = set()
    for row in rows:
        if row.get("before_id"):
            changed_ids.add(row["before_id"])
        if row.get("after_id"):
            changed_ids.add(row["after_id"])
        for affected_id in row.get("affected_ids") or ():
            changed_ids.add(affected_id)

    execution_ids = {
        eid for eid, element in store.elements_by_id.items() if element.get("kind") in EXECUTION_KINDS
    }
    resolvable_changed = changed_ids & set(store.elements_by_id)
    ghost_ids = sorted(changed_ids - set(store.elements_by_id))

    node_ids = sorted(execution_ids | resolvable_changed)
    nodes = [_element_node(store, eid) for eid in node_ids]
    for ghost_id in ghost_ids:
        nodes.append(_ghost_node(ghost_id))

    full_ids = sorted(node["id"] for node in nodes)
    full_id_set = set(full_ids)

    order_layers = _layers_from_order(store)
    call_pairs = sorted({
        (edge.get("source_id"), edge.get("target_id"))
        for edge in store.raw["edges"]
        if edge.get("kind") == "CALLS"
        and edge.get("source_id") in full_id_set
        and edge.get("target_id") in full_id_set
    })
    layers = _longest_path_layers(full_ids, call_pairs, order_layers)
    for node in nodes:
        node["layer"] = layers.get(node["id"], 0)

    wires = []
    for edge in store.raw["edges"]:
        source_id, target_id = edge.get("source_id"), edge.get("target_id")
        if source_id in full_id_set and target_id in full_id_set:
            wires.append(_edge_wire(edge))

    change_by_node: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        entry = {
            "change_id": row.get("change_id"), "kind": row.get("change_kind"), "rank": row.get("rank"),
            "before_id": row.get("before_id"), "after_id": row.get("after_id"),
            "decision_paths_changed": bool(row.get("decision_paths_changed")),
            "reachability_flipped": list(row.get("reachability_flipped") or ()),
            "features_changed": list(row.get("features_changed") or ()),
            "findings_added": list(row.get("findings_added") or ()),
            "findings_removed": list(row.get("findings_removed") or ()),
            "candidate_ids": sorted(
                changes_by_id.get(row.get("change_id"), {}).get("candidate_ids") or ()
            ),
            "note": row.get("note", ""),
        }
        for side_id in (row.get("before_id"), row.get("after_id")):
            if side_id:
                change_by_node.setdefault(side_id, []).append(entry)

    for node in nodes:
        entries = sorted(change_by_node.get(node["id"], []), key=lambda c: c["change_id"] or "")
        node["changes"] = entries
        node["loudest"] = any(
            entry["decision_paths_changed"] or entry["reachability_flipped"] for entry in entries
        )

    return {
        "available": True,
        "nodes": nodes,
        "wires": sorted(wires, key=lambda wire: wire["id"] or ""),
        "ranked_changes": [
            {
                "change_id": row.get("change_id"), "rank": row.get("rank"), "kind": row.get("change_kind"),
                "before_id": row.get("before_id"), "after_id": row.get("after_id"),
                "decision_paths_changed": bool(row.get("decision_paths_changed")),
            }
            for row in rows
        ],
    }


# ---------------------------------------------------------------------------
# Runtime overlay -- reused verbatim from views.py, grouped by element id.
# ---------------------------------------------------------------------------


def _build_runtime(store: ArtifactStore, rstore: RuntimeStore | None) -> dict[str, Any]:
    if rstore is None:
        return {
            "available": False,
            "reason": "no run loaded: pass --run RUN_ID (a run is produced by `metatron trace`)",
        }

    events_by_element: dict[str, list[dict[str, Any]]] = {}
    for event in views.observed_order_view(rstore):
        events_by_element.setdefault(event["element_id"], []).append(event)

    narrative_by_element: dict[str, list[dict[str, Any]]] = {}
    for step in views.narrative_view(rstore):
        for element_id in step["element_ids"]:
            narrative_by_element.setdefault(element_id, []).append(step)

    verdicts_by_element: dict[str, list[dict[str, Any]]] = {}
    for verdict in views.verdicts_view(rstore):
        verdicts_by_element.setdefault(verdict["element_id"], []).append(verdict)

    contradictions_by_element: dict[str, list[dict[str, Any]]] = {}
    for contradiction in views.contradictions_view(rstore):
        contradictions_by_element.setdefault(contradiction["element_id"], []).append(contradiction)

    nondeterminism_by_element: dict[str, list[dict[str, Any]]] = {}
    for observation in views.nondeterminism_view(rstore):
        nondeterminism_by_element.setdefault(observation["element_id"], []).append(observation)

    return {
        "available": True,
        "run_id": rstore.run_id,
        "overview": views.runtime_overview_view(rstore),
        "mapping": views.mapping_view(rstore),
        "events_by_element": events_by_element,
        "narrative_by_element": narrative_by_element,
        "verdicts_by_element": verdicts_by_element,
        "contradictions_by_element": contradictions_by_element,
        "nondeterminism_by_element": nondeterminism_by_element,
        "other_runs": sorted(run_id for run_id in list_runs(store.root) if run_id != rstore.run_id),
    }


# ---------------------------------------------------------------------------
# Assembling the data island
# ---------------------------------------------------------------------------


def build_blueprint_data(
    store: ArtifactStore,
    rstore: RuntimeStore | None = None,
    diff_store: ArtifactStore | None = None,
    *,
    report_link: str = "",
) -> dict[str, Any]:
    """Everything the page needs, as one JSON-serializable, sorted structure.

    Every field here traces to an artifact record read by :mod:`.loader` or
    a view already defined in :mod:`.views` -- nothing is derived here that
    is not either a direct field copy or the presentation-only layer number
    computed above.
    """
    manifest = store.manifest or {}
    confidence_rank = {level.value: index for index, level in enumerate(Confidence)}

    execution = _build_execution(store)
    lineage = _build_lineage(store)
    diff = _build_diff(store, diff_store)

    element_ids: set[str] = set()
    for graph in (execution, lineage, diff):
        for node in graph["nodes"]:
            if node.get("is_element"):
                element_ids.add(node["id"])
    element_details = {
        element_id: views.element_detail(store, element_id) for element_id in sorted(element_ids)
    }

    return {
        "schema_version": manifest.get("schema_version", ""),
        "tool_version": manifest.get("tool_version", ""),
        "generator": "cascade-map blueprint",
        "report_link": report_link,
        "module_collapse_threshold": MODULE_COLLAPSE_THRESHOLD,
        "confidence_rank": confidence_rank,
        "legend": LEGEND,
        "available": dict(sorted(store.available.items())),
        "diagnostics": {
            "static_errors": [
                {"file": e.file, "line": e.line_number, "reason": e.reason} for e in store.errors
            ],
            "diff_errors": [
                {"file": e.file, "line": e.line_number, "reason": e.reason} for e in diff_store.errors
            ] if diff_store is not None else [],
            "runtime_errors": [
                {"file": e.file, "line": e.line_number, "reason": e.reason} for e in rstore.errors
            ] if rstore is not None else [],
        },
        "execution": execution,
        "lineage": lineage,
        "diff": diff,
        "runtime": _build_runtime(store, rstore),
        "element_details": element_details,
    }


# ---------------------------------------------------------------------------
# Presentation: CSS and JS, inlined. No CDN, no external fonts, no network.
# ---------------------------------------------------------------------------

_STYLE = """<style>
:root[data-theme="dark"] {
  --bg:#0d1017; --grid:#1a2028; --panel:#141922; --panel-border:#262e3b;
  --text:#d7dde5; --text-dim:#8b95a5; --accent:#5aa9ff; --sink:#ffb648;
  --node-bg:#1a212d; --node-border:#333f57; --node-header:#26314a;
  --amber:#e0a326; --red:#e5484d; --green:#3fce7c; --purple:#a970ff;
}
:root[data-theme="light"] {
  --bg:#f3f5f9; --grid:#e2e7f0; --panel:#ffffff; --panel-border:#d2d9e5;
  --text:#1a2130; --text-dim:#5c6675; --accent:#1c6fd9; --sink:#c9761a;
  --node-bg:#ffffff; --node-border:#c7cfdc; --node-header:#eaf0fb;
  --amber:#9a6a06; --red:#c2262b; --green:#187a44; --purple:#6b3fc9;
}
* { box-sizing:border-box; }
html,body { margin:0; height:100%; }
body { font-family:-apple-system,'Segoe UI',sans-serif; background:var(--bg); color:var(--text); }
#app { display:flex; flex-direction:column; height:100vh; }
header#topbar { display:flex; align-items:center; gap:1rem; padding:.5rem 1rem;
  background:var(--panel); border-bottom:1px solid var(--panel-border); z-index:60; }
header#topbar h1 { font-size:.95rem; margin:0; white-space:nowrap; }
#tabs { display:flex; gap:.25rem; }
.tab-btn { background:transparent; border:1px solid var(--panel-border); color:var(--text);
  padding:.3rem .7rem; border-radius:6px; cursor:pointer; font-size:.8rem; }
.tab-btn.active { background:var(--accent); color:#fff; border-color:var(--accent); }
#topbar-right { margin-left:auto; display:flex; align-items:center; gap:.5rem; position:relative; }
#search-box { background:var(--node-bg); color:var(--text); border:1px solid var(--panel-border);
  border-radius:6px; padding:.3rem .5rem; width:16rem; font-size:.8rem; }
#search-results { position:absolute; top:2.3rem; right:6.5rem; width:22rem; max-height:18rem;
  overflow:auto; background:var(--panel); border:1px solid var(--panel-border); border-radius:6px; z-index:80; }
.search-result { padding:.35rem .5rem; cursor:pointer; font-size:.75rem; border-bottom:1px solid var(--panel-border); }
.search-result:hover { background:var(--node-header); }
.search-empty { padding:.35rem .5rem; font-size:.75rem; color:var(--text-dim); }
#theme-toggle, #report-link { background:var(--node-bg); border:1px solid var(--panel-border);
  color:var(--text); border-radius:6px; padding:.3rem .6rem; cursor:pointer; font-size:.75rem; text-decoration:none; }
#toolbar { display:flex; align-items:center; gap:.4rem; padding:.35rem 1rem; background:var(--panel);
  border-bottom:1px solid var(--panel-border); flex-wrap:wrap; font-size:.75rem; z-index:55; }
#toolbar button { background:var(--node-bg); border:1px solid var(--panel-border); color:var(--text);
  border-radius:6px; padding:.25rem .55rem; cursor:pointer; font-size:.75rem; }
#filter-summary { margin-left:.4rem; color:var(--text-dim); }
#filter-summary.filters-active { color:var(--amber); font-weight:600; }
#filters-panel { background:var(--node-bg); border:1px solid var(--panel-border); border-radius:6px;
  padding:.3rem .5rem; }
#filters-panel summary { cursor:pointer; }
.filter-chip { display:inline-flex; align-items:center; gap:.2rem; margin:.15rem .6rem .15rem 0; font-size:.72rem; }
#canvas-wrap { position:relative; flex:1; overflow:hidden; }
#viewport { position:absolute; inset:0; overflow:hidden; cursor:grab; touch-action:none; }
#grid-bg { position:absolute; inset:-3000px; background-image:
  linear-gradient(var(--grid) 1px, transparent 1px), linear-gradient(90deg, var(--grid) 1px, transparent 1px); }
#world { position:absolute; left:0; top:0; transform-origin:0 0; }
#wires-svg { position:absolute; left:0; top:0; overflow:visible; pointer-events:none; }
.wire { fill:none; pointer-events:stroke; stroke-width:2px; cursor:pointer; }
.wire-secondary { stroke-width:1.3px; }
.wire.dim { opacity:.12; }
.wire.selected, .wire.path-highlight { opacity:1; filter:drop-shadow(0 0 3px var(--accent)); }
.wire-conf-CERTAIN { stroke:var(--green); stroke-width:3px; }
.wire-conf-RESOLVED { stroke:var(--accent); }
.wire-conf-PROBABLE { stroke:var(--amber); stroke-dasharray:9 5; }
.wire-conf-HEURISTIC { stroke:var(--amber); stroke-dasharray:2 4; opacity:.9; }
.wire-conf-UNKNOWN { stroke:var(--red); stroke-dasharray:2 4; }
.node { position:absolute; background:var(--node-bg); border:1px solid var(--node-border);
  border-radius:10px; box-shadow:0 2px 6px rgba(0,0,0,.35); overflow:hidden; user-select:none; cursor:grab; }
.node.dim { opacity:.18; }
.node.selected { outline:2px solid var(--accent); outline-offset:2px; }
.node.path-highlight { outline:2px solid var(--sink); outline-offset:2px; }
.node-header { background:var(--kind-color, var(--node-header)); color:#fff; font-size:.62rem;
  font-weight:700; text-transform:uppercase; letter-spacing:.03em; padding:.2rem .4rem; }
.node-body { padding:.28rem .4rem; font-size:.76rem; font-weight:600; white-space:nowrap;
  overflow:hidden; text-overflow:ellipsis; }
.node-badges { display:flex; gap:.2rem; flex-wrap:wrap; padding:0 .4rem .3rem; }
.badge { font-size:.58rem; padding:0 .3rem; border-radius:3px; border:1px solid var(--panel-border); white-space:nowrap; }
.reach-badge.reach-REACHES_SINK { background:rgba(63,206,124,.18); border-color:var(--green); }
.reach-badge.reach-NO_SINK_PATH { border-style:dashed; }
.reach-badge.reach-UNKNOWN { border-style:dotted; border-color:var(--red); }
.node.reach-NO_SINK_PATH { opacity:.82; }
.node.reach-UNKNOWN { border-style:dotted; }
.kind-DECISION { clip-path:polygon(50% 0,100% 50%,50% 100%,0 50%); display:flex; flex-direction:column;
  align-items:center; justify-content:center; text-align:center; }
.kind-DECISION .node-header { background:transparent; color:var(--text); }
.node-sink { box-shadow:0 0 0 3px var(--sink), 0 2px 6px rgba(0,0,0,.35); }
.node-sink::after { content:'SINK'; position:absolute; top:1px; right:3px; font-size:.52rem;
  color:var(--sink); font-weight:800; }
.node-module-agg { border-style:dashed; }
.node-ghost { opacity:.5; border-style:dashed; }
.node-loudest { box-shadow:0 0 0 3px var(--red); }
.change-ADDED { border-color:var(--green); box-shadow:0 0 8px var(--green); }
.change-REMOVED { opacity:.45; border-style:dashed; }
.change-RENAMED { border-color:var(--accent); }
.change-MOVED { border-color:var(--purple); }
.change-SIGNATURE_CHANGED { border-color:var(--amber); }
.change-BODY_CHANGED { border-color:var(--amber); }
.change-DECORATORS_CHANGED { border-color:var(--purple); }
.change-AMBIGUOUS { border-style:dotted; border-color:var(--red); }
.pin { position:absolute; width:8px; height:8px; border-radius:50%; background:var(--text-dim); top:50%; transform:translateY(-50%); }
.pin-in { left:-4px; }
.pin-out { right:-4px; }
.pin-outcome { right:-4px; background:var(--sink); }
#empty-state { position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
  text-align:center; padding:2rem; font-size:1rem; color:var(--text-dim); background:var(--bg); z-index:20; }
#detail-panel { position:absolute; right:0; top:0; bottom:0; width:23rem; background:var(--panel);
  border-left:1px solid var(--panel-border); overflow:auto; padding:.8rem; z-index:70; }
#detail-close { float:right; background:var(--node-bg); border:1px solid var(--panel-border);
  color:var(--text); border-radius:6px; cursor:pointer; }
.detail-row { display:flex; gap:.4rem; font-size:.76rem; padding:.18rem 0; border-bottom:1px solid var(--panel-border); }
.detail-label { color:var(--text-dim); width:8.5rem; flex-shrink:0; }
.detail-value { word-break:break-word; }
.id-link { background:none; border:none; color:var(--accent); cursor:pointer; padding:0; font:inherit; text-decoration:underline; }
.id-link-unresolved { color:var(--text-dim); text-decoration:none; cursor:default; }
.model-prose { background:rgba(224,163,38,.12); border-left:3px solid var(--amber); padding:.4rem; margin:.4rem 0; font-size:.74rem; }
.model-prose-label { font-weight:700; margin-bottom:.2rem; }
.missing-note { color:var(--text-dim); font-style:italic; font-size:.76rem; }
.fact-box { margin:.3rem 0; }
.fact-title { font-weight:700; font-size:.68rem; text-transform:uppercase; color:var(--text-dim); }
.fact-json { font-size:.68rem; background:var(--node-bg); padding:.3rem; border-radius:4px; white-space:pre-wrap; word-break:break-word; }
.run-tag { display:inline-block; background:rgba(90,169,255,.15); border:1px solid var(--accent);
  color:var(--accent); font-size:.62rem; padding:0 .3rem; border-radius:3px; margin:.2rem 0; }
.runtime-evidence { border-left:3px solid var(--accent); padding:.2rem .4rem; margin:.25rem 0; font-size:.7rem; }
.contradiction-row { border-left-color:var(--red); }
.verdict-ALIGNED { border-left-color:var(--green); }
.verdict-MISALIGNED { border-left-color:var(--red); }
.verdict-NOT_EXERCISED { border-left-color:var(--accent); border-left-style:dashed; }
.action-btn { margin:.4rem 0; background:var(--accent); color:#fff; border:none; border-radius:6px;
  padding:.3rem .6rem; cursor:pointer; font-size:.73rem; }
.condition-source { background:var(--node-bg); padding:.4rem; border-radius:4px; white-space:pre-wrap;
  word-break:break-word; font-size:.73rem; }
.change-box { border:1px solid var(--panel-border); border-radius:6px; padding:.3rem; margin:.3rem 0; }
#legend { position:absolute; left:.5rem; bottom:.5rem; background:var(--panel); border:1px solid var(--panel-border);
  border-radius:8px; padding:.5rem .7rem; font-size:.66rem; max-width:19rem; z-index:30; }
#legend h3 { margin:.1rem 0 .3rem; font-size:.7rem; }
.legend-row { display:flex; align-items:center; gap:.35rem; margin:.15rem 0; }
.legend-swatch { width:1.5rem; height:0; display:inline-block; border-top-width:3px; }
.legend-swatch.wire-conf-CERTAIN { border-top:3px solid var(--green); }
.legend-swatch.wire-conf-RESOLVED { border-top:2px solid var(--accent); }
.legend-swatch.wire-conf-PROBABLE { border-top:2px dashed var(--amber); }
.legend-swatch.wire-conf-HEURISTIC { border-top:2px dotted var(--amber); }
.legend-swatch.wire-conf-UNKNOWN { border-top:2px dotted var(--red); }
#shortcuts-help { position:absolute; right:.5rem; bottom:.5rem; background:var(--panel);
  border:1px solid var(--panel-border); border-radius:8px; padding:.3rem .6rem; font-size:.62rem;
  color:var(--text-dim); z-index:30; }
#diagnostics { background:rgba(229,72,77,.12); border-bottom:2px solid var(--red); padding:.5rem 1rem; font-size:.73rem; }
#diagnostics ul { margin:.3rem 0 0; padding-left:1.2rem; }
.lod-hide-labels .node-body, .lod-hide-labels .node-badges { display:none; }
.lod-hide-badges .node-badges { display:none; }
</style>"""

_BODY = """<div id="app">
<div id="diagnostics" hidden></div>
<header id="topbar">
<h1>CASCADE-MAP &mdash; blueprint canvas</h1>
<div id="tabs">
<button type="button" class="tab-btn active" data-tab="execution">1 Execution</button>
<button type="button" class="tab-btn" data-tab="lineage">2 Lineage</button>
<button type="button" class="tab-btn" data-tab="diff">3 Diff</button>
</div>
<div id="topbar-right">
<input id="search-box" type="text" placeholder="search id / name / module ( / )" autocomplete="off">
<div id="search-results" hidden></div>
<a id="report-link" href="#" hidden>Tabular report &rarr;</a>
<button type="button" id="theme-toggle">theme</button>
</div>
</header>
<div id="toolbar">
<button type="button" id="btn-fit">Fit (F)</button>
<button type="button" id="btn-reset">Reset camera</button>
<button type="button" id="btn-zoom-in">+</button>
<button type="button" id="btn-zoom-out">-</button>
<button type="button" id="btn-expand-all">Expand all modules</button>
<button type="button" id="btn-collapse-all">Collapse all modules</button>
<details id="filters-panel"><summary>Filters</summary>
<div id="filter-kinds"></div>
<div><label>min confidence <select id="filter-min-conf"></select></label></div>
<label class="filter-chip"><input type="checkbox" id="filter-decision-reach"> only elements that reach a decision</label>
<label class="filter-chip"><input type="checkbox" id="filter-findings"> only elements with findings</label>
</details>
<button type="button" id="btn-clear-filters">Clear filters</button>
<span id="filter-summary"></span>
</div>
<div id="canvas-wrap">
<div id="viewport">
<div id="grid-bg"></div>
<div id="world">
<svg id="wires-svg"><g id="wires-g"></g></svg>
<div id="nodes-layer"></div>
</div>
</div>
<div id="empty-state" hidden></div>
</div>
<aside id="detail-panel" hidden>
<button type="button" id="detail-close">&times;</button>
<div id="detail-content"></div>
</aside>
<div id="legend">
<h3>Legend</h3>
<div id="legend-confidence"></div>
<div id="legend-reachability"></div>
</div>
<div id="shortcuts-help">/ search &middot; Esc clear search &middot; F fit &middot; 1/2/3 tabs</div>
</div>"""

_SCRIPT = """
(function () {
'use strict';
var dataEl = document.getElementById('cascade-blueprint-data');
var DATA = JSON.parse(dataEl.textContent);

var NODE_W = 220, NODE_H = 72, DECISION_SIZE = 130;
var COL_GAP = 300, ROW_GAP = 110;
var MODULE_COLLAPSE_THRESHOLD = DATA.module_collapse_threshold || 150;
var KIND_COLORS = {
  MODULE:'#4c8bf5', CLASS:'#a970ff', FUNCTION:'#35c470', METHOD:'#2fb6c4',
  DECISION:'#ffb648', MODULE_GROUP:'#7d8aa3', UNKNOWN:'#8993a4', FEATURE:'#e07be0',
  LOCAL:'#9aa6b8', PARAMETER:'#c9b458', CONTAINER_KEY:'#c98458', ATTRIBUTE:'#67c9a4',
  FILE:'#8993a4', CONFIG_KEY:'#c98458', BARRIER:'#e5484d'
};

var root = document.documentElement;
var viewport = document.getElementById('viewport');
var world = document.getElementById('world');
var nodesLayer = document.getElementById('nodes-layer');
var wiresG = document.getElementById('wires-g');
var searchBox = document.getElementById('search-box');
var searchResults = document.getElementById('search-results');

function el(tag, cls, text) {
  var e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}
function cssSafe(s) { return String(s || 'UNKNOWN').replace(/[^A-Za-z0-9_-]/g, '_'); }

// ---- theme ----
function loadTheme() {
  try {
    var saved = window.localStorage.getItem('cascade_blueprint_theme');
    if (saved === 'dark' || saved === 'light') return saved;
  } catch (e) {}
  try {
    if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) return 'light';
  } catch (e) {}
  return 'dark';
}
function saveTheme(t) { try { window.localStorage.setItem('cascade_blueprint_theme', t); } catch (e) {} }
var theme = loadTheme();
root.setAttribute('data-theme', theme);
document.getElementById('theme-toggle').addEventListener('click', function () {
  theme = theme === 'dark' ? 'light' : 'dark';
  root.setAttribute('data-theme', theme);
  saveTheme(theme);
});

// ---- state ----
function defaultFilters() {
  return { kinds:{}, minConfidenceIndex:null, onlyDecisionReach:false, onlyFindings:false };
}
var state = {
  tab: 'execution',
  camera: { x:40, y:40, scale:1 },
  selectedId: null,
  selectedWireId: null,
  positions: { execution:{}, lineage:{}, diff:{} },
  collapsed: { execution:{}, lineage:{}, diff:{} },
  filters: defaultFilters(),
  search: ''
};
['execution', 'lineage', 'diff'].forEach(function (tab) {
  var g = DATA[tab];
  if (!g || !g.nodes || g.nodes.length <= MODULE_COLLAPSE_THRESHOLD) return;
  var mods = {};
  g.nodes.forEach(function (n) { if (n.module) mods[n.module] = true; });
  state.collapsed[tab] = mods;
});

function getGraph(tab) { return DATA[tab] || { nodes: [], wires: [] }; }

// ---- layout ----
var layoutCache = {};

function passesFilters(n) {
  var f = state.filters;
  if (f.kinds[n.kind]) return false;
  if (f.minConfidenceIndex !== null) {
    var rank = (n.confidence !== null && n.confidence !== undefined) ? DATA.confidence_rank[n.confidence] : null;
    if (rank === null || rank === undefined || rank > f.minConfidenceIndex) return false;
  }
  if (f.onlyDecisionReach) {
    var st = n.reachability && n.reachability.state;
    if (st !== 'REACHES_SINK') return false;
  }
  if (f.onlyFindings && !n.finding_count) return false;
  return true;
}

function computeLayout(tab) {
  var graph = getGraph(tab);
  var allNodes = graph.nodes || [];
  var wires = graph.wires || [];
  var kept = allNodes.filter(passesFilters);
  var keptIds = {};
  kept.forEach(function (n) { keptIds[n.id] = true; });

  var collapsedMods = state.collapsed[tab] || {};
  var visible = [];
  var moduleAgg = {};
  kept.forEach(function (n) {
    var mod = n.module || '';
    var collapsible = n.kind !== 'DECISION' && n.kind !== 'BARRIER' && mod && collapsedMods[mod];
    if (collapsible) {
      var agg = moduleAgg[mod];
      if (!agg) {
        agg = moduleAgg[mod] = {
          id: '@module:' + tab + ':' + mod, is_module_agg: true, module: mod,
          name: mod + ' (' + '0' + ')', qualname: mod, kind: 'MODULE_GROUP', count: 0,
          layer: n.layer, members: [], confidence: null,
          reachability: { state:'UNKNOWN', source:'collapsed',
            reason:'module collapsed -- expand to see per-element reachability', sink_ids:[], path_ids:[] },
          finding_count: 0, is_sink: false
        };
      }
      agg.count += 1;
      agg.name = mod + ' (' + agg.count + ')';
      agg.members.push(n.id);
      agg.layer = Math.min(agg.layer, n.layer);
      agg.finding_count += (n.finding_count || 0);
    } else {
      visible.push(n);
    }
  });
  var aggList = Object.keys(moduleAgg).sort().map(function (k) { return moduleAgg[k]; });
  var displayNodes = visible.concat(aggList);

  var displayIdOf = {};
  displayNodes.forEach(function (n) {
    if (n.is_module_agg) { n.members.forEach(function (m) { displayIdOf[m] = n.id; }); }
    else { displayIdOf[n.id] = n.id; }
  });

  var wireBuckets = {}, wireOrder = [];
  wires.forEach(function (w) {
    if (!keptIds[w.source_id] || !keptIds[w.target_id]) return;
    var ds = displayIdOf[w.source_id], dt = displayIdOf[w.target_id];
    if (ds === undefined || dt === undefined) return;
    var key = ds + '=>' + dt + '::' + w.kind;
    var bucket = wireBuckets[key];
    if (!bucket) {
      bucket = wireBuckets[key] = { id:key, kind:w.kind, source_id:ds, target_id:dt, count:0, bestRank:999, representative:w };
      wireOrder.push(key);
    }
    bucket.count += 1;
    var r = (w.confidence !== null && w.confidence !== undefined) ? DATA.confidence_rank[w.confidence] : 999;
    if (r < bucket.bestRank) { bucket.bestRank = r; bucket.representative = w; }
  });
  wireOrder.sort();
  var displayWires = wireOrder.map(function (k) { return wireBuckets[k]; });

  var byLayer = {};
  displayNodes.forEach(function (n) {
    var l = n.layer || 0;
    (byLayer[l] = byLayer[l] || []).push(n);
  });
  Object.keys(byLayer).forEach(function (l) {
    byLayer[l].sort(function (a, b) { return a.id < b.id ? -1 : a.id > b.id ? 1 : 0; });
  });

  var pos = {};
  Object.keys(byLayer).map(Number).sort(function (a, b) { return a - b; }).forEach(function (l) {
    byLayer[l].forEach(function (n, i) {
      var saved = state.positions[tab][n.id];
      pos[n.id] = saved ? { x: saved.x, y: saved.y } : { x: l * COL_GAP, y: i * ROW_GAP };
    });
  });

  var byId = {};
  displayNodes.forEach(function (n) { byId[n.id] = n; });

  return { nodes: displayNodes, wires: displayWires, pos: pos, byId: byId, displayIdOf: displayIdOf };
}

function sizeOf(n) {
  return n.kind === 'DECISION' ? { w: DECISION_SIZE, h: DECISION_SIZE } : { w: NODE_W, h: NODE_H };
}

// ---- rendering ----
function reachBadge(stateName) {
  var span = el('span', 'badge reach-badge reach-' + cssSafe(stateName));
  var label = stateName === 'REACHES_SINK' ? '\\u2192 sink' : stateName === 'NO_SINK_PATH' ? '\\u2298 no path' : '? unknown';
  span.textContent = label;
  return span;
}

function buildNodeEl(n, p, tab) {
  var size = sizeOf(n);
  var wrap = el('div', 'node kind-' + cssSafe(n.kind));
  if (n.is_module_agg) wrap.classList.add('node-module-agg');
  if (n.is_ghost) wrap.classList.add('node-ghost');
  if (n.is_sink) wrap.classList.add('node-sink');
  if (n.loudest) wrap.classList.add('node-loudest');
  var reachState = n.reachability ? n.reachability.state : 'UNKNOWN';
  wrap.classList.add('reach-' + cssSafe(reachState));
  (n.changes || []).forEach(function (c) { wrap.classList.add('change-' + cssSafe(c.kind)); });
  wrap.style.left = p.x + 'px';
  wrap.style.top = p.y + 'px';
  wrap.style.width = size.w + 'px';
  wrap.style.height = size.h + 'px';
  wrap.style.setProperty('--kind-color', KIND_COLORS[n.kind] || '#8993a4');
  wrap.dataset.id = n.id;

  wrap.appendChild(el('div', 'node-header', n.kind));
  wrap.appendChild(el('div', 'node-body', n.name || n.id));

  var badges = el('div', 'node-badges');
  if (n.confidence) badges.appendChild(el('span', 'badge wire-conf-' + cssSafe(n.confidence), n.confidence));
  badges.appendChild(reachBadge(reachState));
  if (n.finding_count) badges.appendChild(el('span', 'badge', n.finding_count + ' finding' + (n.finding_count > 1 ? 's' : '')));
  wrap.appendChild(badges);

  wrap.appendChild(el('div', 'pin pin-in'));
  if (n.kind === 'DECISION' && n.outcomes && n.outcomes.length) {
    n.outcomes.forEach(function (o, idx) {
      var pin = el('div', 'pin pin-out pin-outcome');
      pin.style.top = ((idx + 1) * (100 / (n.outcomes.length + 1))) + '%';
      pin.title = o[0];
      wrap.appendChild(pin);
    });
  } else {
    wrap.appendChild(el('div', 'pin pin-out'));
  }

  wrap.addEventListener('click', function (ev) { ev.stopPropagation(); selectNode(n, tab); });
  makeDraggable(wrap, n, tab);
  return wrap;
}

function wirePathD(w, layout) {
  var srcNode = layout.byId[w.source_id], tgtNode = layout.byId[w.target_id];
  var sp = layout.pos[w.source_id], tp = layout.pos[w.target_id];
  if (!sp || !tp) return '';
  var sSize = sizeOf(srcNode || {}), tSize = sizeOf(tgtNode || {});
  var x1 = sp.x + sSize.w, y1 = sp.y + sSize.h / 2;
  var x2 = tp.x, y2 = tp.y + tSize.h / 2;
  var dx = Math.max(40, Math.abs(x2 - x1) * 0.5);
  return 'M ' + x1 + ' ' + y1 + ' C ' + (x1 + dx) + ' ' + y1 + ', ' + (x2 - dx) + ' ' + y2 + ', ' + x2 + ' ' + y2;
}

function buildWireEl(w, layout, tab) {
  var SVG_NS = 'http:' + '//www.w3.org/2000/svg';
  var path = document.createElementNS(SVG_NS, 'path');
  path.setAttribute('d', wirePathD(w, layout));
  var conf = w.representative ? w.representative.confidence : w.confidence;
  var secondary = (w.kind !== 'CALLS' && w.kind !== 'OUTCOME') ? ' wire-secondary' : '';
  path.setAttribute('class', 'wire wire-conf-' + cssSafe(conf) + ' wire-kind-' + cssSafe(w.kind) + secondary);
  path.dataset.id = w.id;
  path.addEventListener('click', function (ev) { ev.stopPropagation(); selectWire(w, tab); });
  return path;
}

function redrawWires(tab) {
  var layout = layoutCache[tab];
  if (!layout) return;
  Array.prototype.forEach.call(wiresG.children, function (p) {
    var w = null;
    for (var i = 0; i < layout.wires.length; i++) { if (layout.wires[i].id === p.dataset.id) { w = layout.wires[i]; break; } }
    if (w) p.setAttribute('d', wirePathD(w, layout));
  });
}

function updateEmptyState(tab) {
  var es = document.getElementById('empty-state');
  var g = getGraph(tab);
  if (tab === 'diff' && !DATA.diff.available) {
    es.hidden = false; es.textContent = DATA.diff.reason; return;
  }
  if (!g.nodes || !g.nodes.length) {
    es.hidden = false;
    es.textContent = tab === 'execution'
      ? 'no MODULE/CLASS/FUNCTION/METHOD elements found -- run `metatron analyze` first'
      : tab === 'lineage'
        ? 'no lineage data -- lineage.jsonl is empty or missing'
        : 'no changes in this diff';
    return;
  }
  es.hidden = true;
}

function renderTab(tab) {
  var layout = computeLayout(tab);
  layoutCache[tab] = layout;
  nodesLayer.innerHTML = '';
  while (wiresG.firstChild) wiresG.removeChild(wiresG.firstChild);

  var maxX = 0, maxY = 0;
  layout.nodes.forEach(function (n) {
    var p = layout.pos[n.id], size = sizeOf(n);
    maxX = Math.max(maxX, p.x + size.w);
    maxY = Math.max(maxY, p.y + size.h);
    nodesLayer.appendChild(buildNodeEl(n, p, tab));
  });
  world.style.width = (maxX + 400) + 'px';
  world.style.height = (maxY + 400) + 'px';
  layout.wires.forEach(function (w) { wiresG.appendChild(buildWireEl(w, layout, tab)); });

  applyCameraTransform();
  updateSelectionHighlight();
  updateEmptyState(tab);
}

// ---- camera: pan, zoom, fit ----
function applyCameraTransform() {
  world.style.transform = 'translate(' + state.camera.x + 'px,' + state.camera.y + 'px) scale(' + state.camera.scale + ')';
  var bg = document.getElementById('grid-bg');
  var size = Math.max(4, 40 * state.camera.scale);
  bg.style.backgroundSize = size + 'px ' + size + 'px';
  bg.style.backgroundPosition = state.camera.x + 'px ' + state.camera.y + 'px';
  updateLOD();
  cullNodes();
}
function updateLOD() {
  var s = state.camera.scale;
  world.classList.toggle('lod-hide-labels', s < 0.32);
  world.classList.toggle('lod-hide-badges', s < 0.55);
}
function cullNodes() {
  var layout = layoutCache[state.tab];
  if (!layout) return;
  var rect = viewport.getBoundingClientRect();
  var pad = 300;
  Array.prototype.forEach.call(nodesLayer.children, function (elNode) {
    var n = layout.byId[elNode.dataset.id];
    if (!n) return;
    var p = layout.pos[n.id];
    var sx = p.x * state.camera.scale + state.camera.x;
    var sy = p.y * state.camera.scale + state.camera.y;
    var visible = sx > -pad && sx < rect.width + pad && sy > -pad && sy < rect.height + pad;
    elNode.style.display = visible ? '' : 'none';
  });
}

var isPanning = false, panStart = null, cameraStart = null;
viewport.addEventListener('pointerdown', function (ev) {
  if (ev.target.closest && ev.target.closest('.node')) return;
  isPanning = true;
  panStart = { x: ev.clientX, y: ev.clientY };
  cameraStart = { x: state.camera.x, y: state.camera.y };
  try { viewport.setPointerCapture(ev.pointerId); } catch (e) {}
});
viewport.addEventListener('pointermove', function (ev) {
  if (!isPanning) return;
  state.camera.x = cameraStart.x + (ev.clientX - panStart.x);
  state.camera.y = cameraStart.y + (ev.clientY - panStart.y);
  applyCameraTransform();
});
['pointerup', 'pointercancel'].forEach(function (evt) {
  viewport.addEventListener(evt, function () { isPanning = false; });
});
viewport.addEventListener('click', function (ev) {
  if (ev.target === viewport || ev.target === world || ev.target.id === 'grid-bg') {
    state.selectedId = null; state.selectedWireId = null;
    updateSelectionHighlight(); closeDetail();
  }
});
function zoomAt(factor, clientX, clientY) {
  var rect = viewport.getBoundingClientRect();
  var localX = clientX - rect.left, localY = clientY - rect.top;
  var worldX = (localX - state.camera.x) / state.camera.scale;
  var worldY = (localY - state.camera.y) / state.camera.scale;
  var newScale = Math.min(2.5, Math.max(0.06, state.camera.scale * factor));
  state.camera.x = localX - worldX * newScale;
  state.camera.y = localY - worldY * newScale;
  state.camera.scale = newScale;
  applyCameraTransform();
}
viewport.addEventListener('wheel', function (ev) {
  ev.preventDefault();
  zoomAt(ev.deltaY < 0 ? 1.1 : 1 / 1.1, ev.clientX, ev.clientY);
}, { passive: false });
document.getElementById('btn-zoom-in').addEventListener('click', function () {
  var r = viewport.getBoundingClientRect(); zoomAt(1.2, r.left + r.width / 2, r.top + r.height / 2);
});
document.getElementById('btn-zoom-out').addEventListener('click', function () {
  var r = viewport.getBoundingClientRect(); zoomAt(1 / 1.2, r.left + r.width / 2, r.top + r.height / 2);
});
document.getElementById('btn-reset').addEventListener('click', function () {
  state.camera = { x: 40, y: 40, scale: 1 }; applyCameraTransform();
});
document.getElementById('btn-fit').addEventListener('click', fitToContent);
function fitToContent() {
  var layout = layoutCache[state.tab];
  if (!layout || !layout.nodes.length) return;
  var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  layout.nodes.forEach(function (n) {
    var p = layout.pos[n.id], size = sizeOf(n);
    minX = Math.min(minX, p.x); minY = Math.min(minY, p.y);
    maxX = Math.max(maxX, p.x + size.w); maxY = Math.max(maxY, p.y + size.h);
  });
  var rect = viewport.getBoundingClientRect(), pad = 60;
  var scale = Math.min(2.5, Math.max(0.06, Math.min(
    (rect.width - pad * 2) / Math.max(1, maxX - minX),
    (rect.height - pad * 2) / Math.max(1, maxY - minY))));
  state.camera.scale = scale;
  state.camera.x = pad - minX * scale;
  state.camera.y = pad - minY * scale;
  applyCameraTransform();
}

// ---- node dragging ----
function makeDraggable(elNode, n, tab) {
  var dragging = false, start = null, origin = null;
  elNode.addEventListener('pointerdown', function (ev) {
    ev.stopPropagation();
    dragging = true;
    start = { x: ev.clientX, y: ev.clientY };
    origin = layoutCache[tab].pos[n.id];
    try { elNode.setPointerCapture(ev.pointerId); } catch (e) {}
  });
  elNode.addEventListener('pointermove', function (ev) {
    if (!dragging) return;
    var dx = (ev.clientX - start.x) / state.camera.scale;
    var dy = (ev.clientY - start.y) / state.camera.scale;
    var np = { x: origin.x + dx, y: origin.y + dy };
    layoutCache[tab].pos[n.id] = np;
    state.positions[tab][n.id] = np;
    elNode.style.left = np.x + 'px';
    elNode.style.top = np.y + 'px';
    redrawWires(tab);
  });
  ['pointerup', 'pointercancel'].forEach(function (evt) {
    elNode.addEventListener(evt, function () { dragging = false; });
  });
}

// ---- selection, neighbours, path highlight ----
function neighboursOf(tab, id) {
  var layout = layoutCache[tab], set = {};
  set[id] = true;
  layout.wires.forEach(function (w) {
    if (w.source_id === id) set[w.target_id] = true;
    if (w.target_id === id) set[w.source_id] = true;
  });
  return set;
}
function updateSelectionHighlight() {
  var layout = layoutCache[state.tab];
  if (!layout) return;
  if (!state.selectedId && !state.selectedWireId) {
    Array.prototype.forEach.call(nodesLayer.children, function (e) { e.classList.remove('dim', 'selected', 'path-highlight'); });
    Array.prototype.forEach.call(wiresG.children, function (e) { e.classList.remove('dim', 'selected', 'path-highlight'); });
    return;
  }
  if (state.selectedId) {
    var nb = neighboursOf(state.tab, state.selectedId);
    Array.prototype.forEach.call(nodesLayer.children, function (e) {
      var inNb = !!nb[e.dataset.id];
      e.classList.toggle('dim', !inNb);
      e.classList.toggle('selected', e.dataset.id === state.selectedId);
    });
    Array.prototype.forEach.call(wiresG.children, function (e) {
      var w = null;
      for (var i = 0; i < layout.wires.length; i++) { if (layout.wires[i].id === e.dataset.id) { w = layout.wires[i]; break; } }
      var touches = w && (w.source_id === state.selectedId || w.target_id === state.selectedId);
      e.classList.toggle('dim', !touches);
      e.classList.toggle('selected', !!touches);
    });
  } else {
    Array.prototype.forEach.call(wiresG.children, function (e) {
      e.classList.toggle('selected', e.dataset.id === state.selectedWireId);
      e.classList.toggle('dim', e.dataset.id !== state.selectedWireId);
    });
    Array.prototype.forEach.call(nodesLayer.children, function (e) { e.classList.remove('dim', 'selected'); });
  }
}
function highlightPath(pathIds, tab) {
  switchTab(tab);
  var layout = layoutCache[tab];
  var mappedSet = {};
  (pathIds || []).forEach(function (id) { mappedSet[layout.displayIdOf[id] || id] = true; });
  Array.prototype.forEach.call(nodesLayer.children, function (e) {
    var on = !!mappedSet[e.dataset.id];
    e.classList.toggle('dim', !on);
    e.classList.toggle('path-highlight', on);
  });
  Array.prototype.forEach.call(wiresG.children, function (e) {
    var w = null;
    for (var i = 0; i < layout.wires.length; i++) { if (layout.wires[i].id === e.dataset.id) { w = layout.wires[i]; break; } }
    var on = w && mappedSet[w.source_id] && mappedSet[w.target_id];
    e.classList.toggle('dim', !on);
    e.classList.toggle('path-highlight', !!on);
  });
}

// ---- detail panel ----
function closeDetail() { document.getElementById('detail-panel').hidden = true; }
document.getElementById('detail-close').addEventListener('click', closeDetail);

function fieldRow(label, value) {
  var row = el('div', 'detail-row');
  row.appendChild(el('span', 'detail-label', label));
  row.appendChild(el('span', 'detail-value', value === null || value === undefined || value === '' ? '\\u2014' : value));
  return row;
}
function idLink(id, tab, labelText) {
  var btn = el('button', 'id-link', labelText || id);
  btn.type = 'button';
  var g = getGraph(tab);
  var present = false;
  for (var i = 0; i < (g.nodes || []).length; i++) { if (g.nodes[i].id === id) { present = true; break; } }
  if (!present) { btn.disabled = true; btn.classList.add('id-link-unresolved'); btn.title = 'not present in this view'; return btn; }
  btn.addEventListener('click', function () { switchTab(tab); flyTo(id, tab); });
  return btn;
}
function idListRow(label, ids) {
  var row = el('div', 'detail-row');
  row.appendChild(el('span', 'detail-label', label));
  var wrap = el('span', 'detail-value');
  var uniq = Array.prototype.slice.call(new Set(ids)).sort();
  if (!uniq.length) { wrap.textContent = '\\u2014'; }
  else {
    uniq.forEach(function (id, i) {
      if (i > 0) wrap.appendChild(document.createTextNode(', '));
      wrap.appendChild(idLink(id, state.tab));
    });
  }
  row.appendChild(wrap);
  return row;
}

function renderRecordSection(panel, detail) {
  panel.appendChild(el('h3', null, 'documentation record'));
  var dr = detail ? detail.doc_record : null;
  if (!dr) {
    panel.appendChild(el('p', 'missing-note', 'no documentation record for this element (records.jsonl not available, or the completeness gate has not run)'));
  } else {
    Object.keys(dr.facts).sort().forEach(function (k) {
      var v = dr.facts[k];
      if (v && Object.keys(v).length) {
        var box = el('div', 'fact-box');
        box.appendChild(el('div', 'fact-title', k));
        box.appendChild(el('pre', 'fact-json', JSON.stringify(v, null, 1)));
        panel.appendChild(box);
      }
    });
    if (dr.model_authored && dr.model_authored.is_present) {
      var mp = el('div', 'model-prose');
      mp.appendChild(el('div', 'model-prose-label', 'MODEL-WRITTEN, not a fact (model: ' + (dr.model_authored.model_id || 'unknown') + ')'));
      mp.appendChild(el('div', null, dr.model_authored.prose));
      panel.appendChild(mp);
    } else {
      panel.appendChild(el('p', 'missing-note', 'no model-written prose (enrichment did not run for this element)'));
    }
  }
  if (detail && detail.finding_ids && detail.finding_ids.length) {
    panel.appendChild(el('h3', null, 'findings'));
    var ul = el('ul');
    detail.finding_ids.forEach(function (fid) { ul.appendChild(el('li', null, fid)); });
    panel.appendChild(ul);
  }
  if (detail) {
    panel.appendChild(el('h3', null, 'callers / callees'));
    panel.appendChild(idListRow('callers', (detail.incoming_edges || []).map(function (e) { return e.source_id; })));
    panel.appendChild(idListRow('callees', (detail.outgoing_edges || []).map(function (e) { return e.target_id; })));
    var featOut = (detail.lineage_out || []).filter(function (e) { return e.target_id && e.target_id.indexOf('@feature:') === 0; }).map(function (e) { return e.target_id; });
    var featIn = (detail.lineage_in || []).filter(function (e) { return e.source_id && e.source_id.indexOf('@feature:') === 0; }).map(function (e) { return e.source_id; });
    if (featOut.length || featIn.length) {
      panel.appendChild(el('h3', null, 'features'));
      panel.appendChild(idListRow('writes', featOut));
      panel.appendChild(idListRow('reads', featIn));
    }
  }
}

function renderRuntimeSection(panel, elementId) {
  var rt = DATA.runtime;
  panel.appendChild(el('h3', null, 'runtime'));
  if (!rt || !rt.available) {
    panel.appendChild(el('p', 'missing-note', (rt && rt.reason) || 'no run loaded'));
    return;
  }
  panel.appendChild(el('span', 'run-tag', 'run: ' + rt.run_id));
  var events = (rt.events_by_element && rt.events_by_element[elementId]) || [];
  if (events.length) {
    var ul = el('ul');
    events.forEach(function (ev) {
      ul.appendChild(el('li', 'runtime-evidence', ev.sequence + ' ' + ev.kind + (ev.branch_taken ? (' -> ' + ev.branch_taken) : '')));
    });
    panel.appendChild(ul);
  } else {
    panel.appendChild(el('p', 'missing-note', 'no observed events for this element in this run'));
  }
  ((rt.narrative_by_element && rt.narrative_by_element[elementId]) || []).forEach(function (s) {
    var box = el('div', 'runtime-evidence');
    box.appendChild(el('div', null, '[' + s.phase + '] ' + s.text));
    if (s.model_prose) {
      var mp = el('div', 'model-prose');
      mp.appendChild(el('div', 'model-prose-label', 'MODEL-WRITTEN (model: ' + (s.model_id || 'unknown') + ')'));
      mp.appendChild(el('div', null, s.model_prose));
      box.appendChild(mp);
    }
    panel.appendChild(box);
  });
  ((rt.verdicts_by_element && rt.verdicts_by_element[elementId]) || []).forEach(function (v) {
    panel.appendChild(el('div', 'runtime-evidence verdict-' + cssSafe(v.verdict), 'verdict: ' + v.verdict + ' -- ' + v.observation));
  });
  ((rt.contradictions_by_element && rt.contradictions_by_element[elementId]) || []).forEach(function (c) {
    var box = el('div', 'runtime-evidence contradiction-row');
    box.appendChild(el('div', null, 'claim: ' + c.claim));
    box.appendChild(el('div', null, 'observed: ' + c.observation));
    panel.appendChild(box);
  });
}

function renderDetailForNode(n, tab) {
  document.getElementById('detail-panel').hidden = false;
  var panel = document.getElementById('detail-content');
  panel.innerHTML = '';
  panel.appendChild(document.getElementById('detail-close'));
  panel.appendChild(el('h2', null, n.name || n.id));
  panel.appendChild(el('div', 'detail-id', n.id));
  panel.appendChild(fieldRow('kind', n.kind));
  panel.appendChild(fieldRow('module', n.module));
  if (n.confidence) panel.appendChild(fieldRow('confidence', n.confidence + (n.method ? ' (' + n.method + ')' : '')));
  if (n.span) panel.appendChild(fieldRow('location', (n.span.path || '') + ':' + (n.span.line || '')));
  if (n.reachability) {
    panel.appendChild(fieldRow('reachability', n.reachability.state + (n.reachability.reason ? ' -- ' + n.reachability.reason : '')));
    if (n.reachability.path_ids && n.reachability.path_ids.length) {
      var btn = el('button', 'action-btn', 'Trace path to decision');
      btn.type = 'button';
      btn.addEventListener('click', function () { highlightPath(n.reachability.path_ids, tab); });
      panel.appendChild(btn);
    }
  }
  if (n.kind === 'DECISION') {
    panel.appendChild(el('h3', null, 'condition'));
    panel.appendChild(el('pre', 'condition-source', n.condition_source || ''));
    panel.appendChild(el('h3', null, 'outcomes'));
    var ul = el('ul');
    (n.outcomes || []).forEach(function (o) {
      var li = el('li');
      li.appendChild(document.createTextNode(o[0] + ' \\u2192 '));
      li.appendChild(idLink(o[1], tab));
      ul.appendChild(li);
    });
    panel.appendChild(ul);
  }
  if (n.is_module_agg) {
    panel.appendChild(el('h3', null, 'members (' + n.count + ')'));
    var mul = el('ul');
    n.members.slice().sort().forEach(function (m) { var li = el('li'); li.appendChild(idLink(m, tab)); mul.appendChild(li); });
    panel.appendChild(mul);
    var expandBtn = el('button', 'action-btn', 'Expand this module');
    expandBtn.type = 'button';
    expandBtn.addEventListener('click', function () { state.collapsed[tab][n.module] = false; renderTab(tab); });
    panel.appendChild(expandBtn);
  }
  if (n.changes && n.changes.length) {
    panel.appendChild(el('h3', null, 'version diff'));
    n.changes.forEach(function (c) {
      var box = el('div', 'change-box change-' + cssSafe(c.kind));
      box.appendChild(fieldRow('kind', c.kind + (c.rank !== null && c.rank !== undefined ? (' (rank ' + c.rank + ')') : ' (unranked)')));
      box.appendChild(fieldRow('before', c.before_id));
      box.appendChild(fieldRow('after', c.after_id));
      box.appendChild(fieldRow('decision paths changed', c.decision_paths_changed ? 'YES' : 'no'));
      if (c.reachability_flipped.length) box.appendChild(fieldRow('reachability flipped', c.reachability_flipped.join(', ')));
      if (c.candidate_ids.length) box.appendChild(fieldRow('AMBIGUOUS candidates', c.candidate_ids.join(', ')));
      panel.appendChild(box);
    });
  }
  if (n.is_element) {
    renderRecordSection(panel, DATA.element_details[n.id]);
    renderRuntimeSection(panel, n.id);
  }
}
function renderDetailForWire(w, tab) {
  document.getElementById('detail-panel').hidden = false;
  var panel = document.getElementById('detail-content');
  panel.innerHTML = '';
  panel.appendChild(document.getElementById('detail-close'));
  panel.appendChild(el('h2', null, w.kind + ' wire'));
  panel.appendChild(fieldRow('source', w.source_id));
  panel.appendChild(fieldRow('target', w.target_id));
  var rep = w.representative || w;
  panel.appendChild(fieldRow('method', rep.method));
  panel.appendChild(fieldRow('confidence', rep.confidence));
  if (rep.outcome_label) panel.appendChild(fieldRow('outcome label', rep.outcome_label));
  if (rep.call_site) panel.appendChild(fieldRow('call site', (rep.call_site.path || '') + ':' + (rep.call_site.line || '')));
  if (rep.span) panel.appendChild(fieldRow('span', (rep.span.path || '') + ':' + (rep.span.line || '')));
  if (w.count && w.count > 1) panel.appendChild(fieldRow('aggregated', w.count + ' underlying wires (a module endpoint is collapsed)'));
}
function selectNode(n, tab) {
  state.selectedId = n.id; state.selectedWireId = null;
  updateSelectionHighlight(); renderDetailForNode(n, tab);
}
function selectWire(w, tab) {
  state.selectedWireId = w.id; state.selectedId = null;
  updateSelectionHighlight(); renderDetailForWire(w, tab);
}

// ---- tabs ----
function switchTab(tab) {
  state.tab = tab;
  document.querySelectorAll('.tab-btn').forEach(function (b) { b.classList.toggle('active', b.dataset.tab === tab); });
  renderTab(tab);
}
document.querySelectorAll('.tab-btn').forEach(function (b) {
  b.addEventListener('click', function () { switchTab(b.dataset.tab); });
});

// ---- filters ----
function buildFilterUI() {
  var kindSet = {};
  ['execution', 'lineage', 'diff'].forEach(function (tab) {
    (getGraph(tab).nodes || []).forEach(function (n) { kindSet[n.kind] = true; });
  });
  var kindsWrap = document.getElementById('filter-kinds');
  Object.keys(kindSet).sort().forEach(function (k) {
    var label = el('label', 'filter-chip');
    var cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = true;
    cb.addEventListener('change', function () {
      state.filters.kinds[k] = !cb.checked;
      ['execution', 'lineage', 'diff'].forEach(renderTab);
      updateFilterSummary();
    });
    label.appendChild(cb);
    label.appendChild(document.createTextNode(' ' + k));
    kindsWrap.appendChild(label);
  });
  var confSel = document.getElementById('filter-min-conf');
  var ranks = DATA.confidence_rank;
  var levels = Object.keys(ranks).sort(function (a, b) { return ranks[a] - ranks[b]; });
  var noneOpt = document.createElement('option'); noneOpt.value = ''; noneOpt.textContent = '(no floor)';
  confSel.appendChild(noneOpt);
  levels.forEach(function (lv) {
    var o = document.createElement('option'); o.value = ranks[lv]; o.textContent = lv;
    confSel.appendChild(o);
  });
  confSel.addEventListener('change', function () {
    state.filters.minConfidenceIndex = confSel.value === '' ? null : Number(confSel.value);
    ['execution', 'lineage', 'diff'].forEach(renderTab);
    updateFilterSummary();
  });
  document.getElementById('filter-decision-reach').addEventListener('change', function (ev) {
    state.filters.onlyDecisionReach = ev.target.checked;
    ['execution', 'lineage', 'diff'].forEach(renderTab);
    updateFilterSummary();
  });
  document.getElementById('filter-findings').addEventListener('change', function (ev) {
    state.filters.onlyFindings = ev.target.checked;
    ['execution', 'lineage', 'diff'].forEach(renderTab);
    updateFilterSummary();
  });
  document.getElementById('btn-clear-filters').addEventListener('click', function () {
    state.filters = defaultFilters();
    syncFilterUI();
    ['execution', 'lineage', 'diff'].forEach(renderTab);
    updateFilterSummary();
  });
}
function syncFilterUI() {
  document.querySelectorAll('#filter-kinds input').forEach(function (cb) { cb.checked = true; });
  document.getElementById('filter-min-conf').value = '';
  document.getElementById('filter-decision-reach').checked = false;
  document.getElementById('filter-findings').checked = false;
}
function updateFilterSummary() {
  var active = [];
  var excludedKinds = Object.keys(state.filters.kinds).filter(function (k) { return state.filters.kinds[k]; });
  if (excludedKinds.length) active.push('kinds excluded: ' + excludedKinds.join(','));
  if (state.filters.minConfidenceIndex !== null) active.push('confidence floor active');
  if (state.filters.onlyDecisionReach) active.push('only reaches-decision');
  if (state.filters.onlyFindings) active.push('only with findings');
  var summary = document.getElementById('filter-summary');
  summary.textContent = active.length ? ('FILTERED: ' + active.join('; ')) : 'no filters active (showing everything)';
  summary.classList.toggle('filters-active', active.length > 0);
}
document.getElementById('btn-expand-all').addEventListener('click', function () {
  state.collapsed[state.tab] = {}; renderTab(state.tab);
});
document.getElementById('btn-collapse-all').addEventListener('click', function () {
  var mods = {};
  (getGraph(state.tab).nodes || []).forEach(function (n) { if (n.module) mods[n.module] = true; });
  state.collapsed[state.tab] = mods;
  renderTab(state.tab);
});

// ---- search ----
function flyTo(id, tab) {
  var layout = layoutCache[tab];
  if (!layout) return;
  var n = layout.byId[id] || layout.byId[layout.displayIdOf[id]];
  if (!n) return;
  var p = layout.pos[n.id], size = sizeOf(n);
  var rect = viewport.getBoundingClientRect();
  state.camera.scale = Math.max(state.camera.scale, 0.8);
  state.camera.x = rect.width / 2 - (p.x + size.w / 2) * state.camera.scale;
  state.camera.y = rect.height / 2 - (p.y + size.h / 2) * state.camera.scale;
  applyCameraTransform();
  state.selectedId = n.id; state.selectedWireId = null;
  updateSelectionHighlight();
  renderDetailForNode(n, tab);
}
searchBox.addEventListener('input', function () {
  state.search = searchBox.value.trim().toLowerCase();
  renderSearchResults();
});
searchBox.addEventListener('keydown', function (ev) {
  if (ev.key === 'Enter') {
    var first = searchResults.querySelector('.search-result');
    if (first) first.click();
  } else if (ev.key === 'Escape') {
    searchBox.value = ''; state.search = ''; searchResults.hidden = true; searchBox.blur();
  }
});
function renderSearchResults() {
  searchResults.innerHTML = '';
  if (!state.search) { searchResults.hidden = true; return; }
  var matches = [];
  ['execution', 'lineage', 'diff'].forEach(function (tab) {
    (getGraph(tab).nodes || []).forEach(function (n) {
      var hay = (n.id + ' ' + (n.name || '') + ' ' + (n.module || '')).toLowerCase();
      if (hay.indexOf(state.search) !== -1) matches.push({ n: n, tab: tab });
    });
  });
  matches = matches.slice(0, 40);
  searchResults.hidden = false;
  if (!matches.length) { searchResults.appendChild(el('div', 'search-empty', 'no matches')); return; }
  matches.forEach(function (m) {
    var item = el('div', 'search-result', '[' + m.tab + '] ' + (m.n.name || m.n.id) + ' -- ' + m.n.id);
    item.addEventListener('click', function () {
      switchTab(m.tab); flyTo(m.n.id, m.tab);
      searchResults.hidden = true; searchBox.value = m.n.name || m.n.id;
    });
    searchResults.appendChild(item);
  });
}

// ---- keyboard shortcuts ----
document.addEventListener('keydown', function (ev) {
  if (ev.target === searchBox) return;
  if (ev.key === '/') { ev.preventDefault(); searchBox.focus(); return; }
  if (ev.key === 'Escape') {
    searchBox.value = ''; state.search = ''; searchResults.hidden = true;
    state.selectedId = null; state.selectedWireId = null;
    updateSelectionHighlight(); closeDetail();
    return;
  }
  if (ev.key === 'f' || ev.key === 'F') { fitToContent(); return; }
  if (ev.key === '1') { switchTab('execution'); return; }
  if (ev.key === '2') { switchTab('lineage'); return; }
  if (ev.key === '3') { switchTab('diff'); return; }
});

// ---- legend & diagnostics ----
function buildLegend() {
  var confWrap = document.getElementById('legend-confidence');
  DATA.legend.confidence.forEach(function (item) {
    var row = el('div', 'legend-row');
    row.appendChild(el('span', 'legend-swatch wire-conf-' + cssSafe(item.level)));
    row.appendChild(document.createTextNode(' ' + item.level + ' -- ' + item.style));
    confWrap.appendChild(row);
  });
  var reachWrap = document.getElementById('legend-reachability');
  DATA.legend.reachability.forEach(function (item) {
    var row = el('div', 'legend-row');
    row.appendChild(reachBadge(item.state));
    row.appendChild(document.createTextNode(' -- ' + item.style));
    reachWrap.appendChild(row);
  });
}
function showDiagnostics() {
  var diag = DATA.diagnostics;
  var hasAny = (diag.static_errors && diag.static_errors.length) ||
    (diag.diff_errors && diag.diff_errors.length) || (diag.runtime_errors && diag.runtime_errors.length);
  if (!hasAny) return;
  var box = document.getElementById('diagnostics');
  box.hidden = false;
  box.appendChild(el('strong', null, 'Artifact load errors (nothing was silently dropped):'));
  var ul = el('ul');
  (diag.static_errors || []).forEach(function (e) { ul.appendChild(el('li', null, e.file + ':' + e.line + ': ' + e.reason)); });
  (diag.diff_errors || []).forEach(function (e) { ul.appendChild(el('li', null, '[diff] ' + e.file + ':' + e.line + ': ' + e.reason)); });
  (diag.runtime_errors || []).forEach(function (e) { ul.appendChild(el('li', null, '[runtime] ' + e.file + ':' + e.line + ': ' + e.reason)); });
  box.appendChild(ul);
}

// ---- init ----
function init() {
  buildLegend();
  buildFilterUI();
  showDiagnostics();
  if (DATA.report_link) {
    var link = document.getElementById('report-link');
    link.hidden = false;
    link.href = DATA.report_link;
  }
  window.addEventListener('resize', function () { cullNodes(); });
  switchTab('execution');
}
init();
})();
"""


def render_blueprint(
    store: ArtifactStore,
    rstore: RuntimeStore | None = None,
    diff_store: ArtifactStore | None = None,
    *,
    report_link: str = "",
) -> str:
    """Render the whole offline blueprint page for one loaded artifact root.

    Pure string assembly -- no ``str.format``/f-string is applied to the CSS
    or JS blocks, so neither can be corrupted by their own ``{``/``}``
    characters. The only interpolated value is the JSON data island, and it
    goes through :func:`_safe_json`.
    """
    data = build_blueprint_data(store, rstore=rstore, diff_store=diff_store, report_link=report_link)
    data_json = _safe_json(data)
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<title>CASCADE-MAP &mdash; blueprint canvas</title>",
        _STYLE,
        "</head><body>",
        _BODY,
        '<script type="application/json" id="cascade-blueprint-data">',
        data_json,
        "</script>",
        "<script>",
        _SCRIPT,
        "</script>",
        "</body></html>",
    ]
    return "".join(parts)


def render_blueprint_to_file(
    root: str | Path,
    out_path: str | Path,
    run_id: str | None = None,
    diff_root: str | Path | None = None,
) -> ArtifactStore:
    """Load the artifacts at *root* and write the blueprint canvas to *out_path*.

    Returns the loaded :class:`ArtifactStore`, mirroring
    :func:`cascade_map.viewer.render_to_file`'s return shape.

    *run_id*, when given, loads ``runtime/<run_id>/*`` for the runtime
    overlay. *diff_root*, when given, loads a second, independent
    :class:`ArtifactStore` from a `metatron diff` output directory
    (``changes.jsonl`` / ``impacts.jsonl``) for the Diff tab; without it the
    Diff tab renders its stated empty state rather than a blank canvas.

    If ``index.html`` (the phase B tabular report's default filename)
    already exists next to *out_path*, the page links to it -- a
    one-directional cross-link, from blueprint to report; see this card's
    builder report for why the reverse link is not made here.
    """
    root = Path(root)
    out_path = Path(out_path)
    store = ArtifactStore.load(root)
    rstore = RuntimeStore.load(root, run_id) if run_id is not None else None
    diff_store = ArtifactStore.load(diff_root) if diff_root is not None else None

    report_candidate = out_path.parent / "index.html"
    report_link = report_candidate.name if report_candidate.exists() else ""

    html = render_blueprint(store, rstore=rstore, diff_store=diff_store, report_link=report_link)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return store
