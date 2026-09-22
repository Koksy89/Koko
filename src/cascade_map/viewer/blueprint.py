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

import heapq
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
        {"state": "NO_SINK_PATH", "style": "dashed border + no-path badge -- full brightness, NOT the same as deleted"},
        {"state": "UNKNOWN", "style": "dotted border + ? badge -- could not tell, not a verdict"},
    ],
    "diff": [
        {"kind": "ADDED", "style": "green border, glowing"},
        {"kind": "REMOVED", "style": "faded, dashed grey border -- present in the graph you loaded, gone in the other"},
        {"kind": "RENAMED", "style": "accent-coloured border"},
        {"kind": "MOVED", "style": "purple border"},
        {"kind": "SIGNATURE_CHANGED / BODY_CHANGED", "style": "amber border"},
        {"kind": "DECORATORS_CHANGED", "style": "purple border"},
        {"kind": "AMBIGUOUS", "style": "dotted red border -- several equally-good matches, none chosen"},
        {"kind": "decision path changed", "style": "red glowing outline + ⚠ badge -- the loudest mark on the page"},
        {"kind": "collapsed module", "style": "shows a count per ChangeKind among its members, plus its own "
                                              "border in the strongest kind present, plus the ⚠ mark if any "
                                              "member changes a decision path -- a rollup of the same facts, "
                                              "never a new one"},
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


def _strongly_connected_components(
    node_ids: list[str], adjacency: dict[str, list[str]]
) -> dict[str, int]:
    """Tarjan's algorithm, iterative (no recursion-depth risk), assigning
    every id in *node_ids* an integer SCC index.

    *node_ids* must already be sorted by the caller -- the order components
    are discovered in, and therefore the SCC indices themselves, are a
    direct function of that seed order, which is what keeps this
    deterministic across runs and ``PYTHONHASHSEED`` values.
    """
    index_of: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    scc_of: dict[str, int] = {}
    counters = {"index": 0, "scc": 0}

    for root in node_ids:
        if root in index_of:
            continue
        # (node, iterator-over-its-successors) per stack frame.
        work: list[tuple[str, Any]] = [(root, iter(adjacency.get(root, ())))]
        index_of[root] = lowlink[root] = counters["index"]
        counters["index"] += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, successors = work[-1]
            descended = False
            for successor in successors:
                if successor not in index_of:
                    index_of[successor] = lowlink[successor] = counters["index"]
                    counters["index"] += 1
                    stack.append(successor)
                    on_stack.add(successor)
                    work.append((successor, iter(adjacency.get(successor, ()))))
                    descended = True
                    break
                if successor in on_stack:
                    lowlink[node] = min(lowlink[node], index_of[successor])
            if descended:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
            if lowlink[node] == index_of[node]:
                scc_id = counters["scc"]
                counters["scc"] += 1
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    scc_of[member] = scc_id
                    if member == node:
                        break
    return scc_of


def _condensed_layers(
    scc_ids: list[int], condensed_edges: set[tuple[int, int]], scc_floor: dict[int, int]
) -> dict[int, int]:
    """Longest-path layer per SCC, over the condensation DAG (guaranteed
    acyclic by construction). Kahn's algorithm with a min-heap frontier, so
    the processing order -- and therefore every layer value -- is a pure
    function of the SCC ids and edges, never of dict/set iteration order.
    """
    successors: dict[int, list[int]] = {scc_id: [] for scc_id in scc_ids}
    in_degree: dict[int, int] = {scc_id: 0 for scc_id in scc_ids}
    for source, target in condensed_edges:
        successors[source].append(target)
        in_degree[target] += 1

    pending: dict[int, int] = {scc_id: 0 for scc_id in scc_ids}
    ready = [scc_id for scc_id in scc_ids if in_degree[scc_id] == 0]
    heapq.heapify(ready)
    condensed_layer: dict[int, int] = {}
    while ready:
        node = heapq.heappop(ready)
        condensed_layer[node] = max(scc_floor.get(node, 0), pending[node])
        for successor in sorted(successors[node]):
            pending[successor] = max(pending[successor], condensed_layer[node] + 1)
            in_degree[successor] -= 1
            if in_degree[successor] == 0:
                heapq.heappush(ready, successor)
    return condensed_layer


def _longest_path_layers(
    node_ids: list[str], edges: list[tuple[str, str]], base: dict[str, int]
) -> dict[str, int]:
    """Fallback layering for nodes ``_layers_from_order`` did not place.

    Longest path from a root, computed on the **condensation** of *edges*
    into strongly-connected components, not on the raw graph. Recursion and
    mutual recursion are real in the target and produce real cycles; a
    naive relaxation loop over a cyclic graph has no fixed point, and even
    capped at N iterations to terminate at all, it still inflates every
    node reachable from the cycle to roughly N layers -- verification
    measured a ~260-node graph laid out across ~460 columns from exactly
    this, which does not read as an approximation, it reads as a blank
    canvas. Collapsing each SCC to one layer before taking the longest path
    is exact for a DAG and bounded for a cyclic one (every node in one
    recursive cluster shares a single column), and it stays presentation,
    not analysis: no confidence is claimed and no edge is added or removed,
    only a column index is chosen for nodes that already exist.

    Pure stdlib (Tarjan's SCC algorithm + Kahn's topological sort) rather
    than ``networkx``: this module is card 15's only code path so far that
    would have exercised that dependency, and it was not actually
    installed in the environment this fix was verified in despite being
    declared in ``pyproject.toml`` -- see this builder's report.
    """
    adjacency: dict[str, list[str]] = {}
    for source, target in edges:
        adjacency.setdefault(source, []).append(target)

    scc_of = _strongly_connected_components(node_ids, adjacency)

    condensed_edges: set[tuple[int, int]] = set()
    for source, target in edges:
        source_scc, target_scc = scc_of.get(source), scc_of.get(target)
        if source_scc is not None and target_scc is not None and source_scc != target_scc:
            condensed_edges.add((source_scc, target_scc))

    scc_floor: dict[int, int] = {}
    for node_id in node_ids:
        floor = base.get(node_id, 0)
        scc_id = scc_of[node_id]
        if floor > scc_floor.get(scc_id, 0):
            scc_floor[scc_id] = floor

    condensed_layer = _condensed_layers(sorted(set(scc_of.values())), condensed_edges, scc_floor)

    layers = dict(base)
    for node_id in node_ids:
        layers[node_id] = condensed_layer[scc_of[node_id]]
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
  --bg:#0a0c11; --grid-minor:#171d28; --grid-major:#232b3a; --panel:#12161f; --panel-border:#262e3b;
  --text:#e3e8f0; --text-dim:#8b95a5; --accent:#5aa9ff; --accent-dark:#2e6fc2; --sink:#ffb648;
  --node-bg:#1c2330; --node-bg-2:#161c28; --node-border:#3a4763; --node-header:#26314a;
  --amber:#e0a326; --red:#e5484d; --green:#3fce7c; --purple:#a970ff;
}
:root[data-theme="light"] {
  --bg:#eef1f7; --grid-minor:#dde3ee; --grid-major:#c7cfdf; --panel:#ffffff; --panel-border:#d2d9e5;
  --text:#1a2130; --text-dim:#5c6675; --accent:#1c6fd9; --accent-dark:#144e9e; --sink:#c9761a;
  --node-bg:#ffffff; --node-bg-2:#f3f6fb; --node-border:#c7cfdc; --node-header:#eaf0fb;
  --amber:#9a6a06; --red:#c2262b; --green:#187a44; --purple:#6b3fc9;
}
* { box-sizing:border-box; }
/* Structural fix for a real defect found in verification: an ID-selector
   rule that also sets `display` (e.g. `#empty-state { display:flex; }`)
   is an *author* rule and beats the UA stylesheet's `[hidden]{display:none}`
   regardless of specificity, because author origin always outranks UA
   origin in the cascade. `#empty-state` painted at full opacity over the
   whole canvas while `hidden` was set and elementFromPoint() never reached
   the graph underneath -- a blank-looking page with no console error. This
   one rule, with `!important`, makes every element on this page obey
   `hidden` unconditionally, so no other `display:` rule -- present or
   added later -- can repeat the same failure. */
[hidden] { display:none !important; }
html,body { margin:0; height:100%; }
body { font-family:-apple-system,'Segoe UI',sans-serif; background:var(--bg); color:var(--text); }
#app { display:flex; flex-direction:column; height:100vh; }
header#topbar { display:flex; align-items:center; gap:1rem; padding:.5rem 1rem;
  background:var(--panel); border-bottom:1px solid var(--panel-border); z-index:60; }
header#topbar h1 { font-size:.95rem; margin:0; white-space:nowrap; }
#tabs { display:flex; gap:.25rem; }
.tab-btn { background:linear-gradient(180deg, var(--node-bg), var(--node-bg-2)); border:1px solid var(--panel-border);
  color:var(--text-dim); padding:.35rem .8rem; border-radius:6px; cursor:pointer; font-size:.8rem;
  font-weight:600; transition:border-color .12s, color .12s, box-shadow .12s; }
.tab-btn:hover { color:var(--text); border-color:var(--accent); }
.tab-btn.active { background:linear-gradient(180deg, var(--accent), var(--accent-dark));
  color:#fff; border-color:var(--accent); box-shadow:0 0 10px rgba(90,169,255,.45); }
#topbar-right { margin-left:auto; display:flex; align-items:center; gap:.5rem; position:relative; }
#search-box { background:var(--node-bg); color:var(--text); border:1px solid var(--panel-border);
  border-radius:6px; padding:.35rem .6rem; width:16rem; font-size:.8rem; transition:border-color .12s, box-shadow .12s; }
#search-box:focus { outline:none; border-color:var(--accent); box-shadow:0 0 0 2px rgba(90,169,255,.25); }
#search-results { position:absolute; top:2.3rem; right:6.5rem; width:22rem; max-height:18rem;
  overflow:auto; background:var(--panel); border:1px solid var(--panel-border); border-radius:6px; z-index:80;
  box-shadow:0 8px 24px rgba(0,0,0,.4); }
.search-result { padding:.35rem .5rem; cursor:pointer; font-size:.75rem; border-bottom:1px solid var(--panel-border); }
.search-result:hover { background:var(--node-header); }
.search-empty { padding:.35rem .5rem; font-size:.75rem; color:var(--text-dim); }
#theme-toggle, #report-link { background:linear-gradient(180deg, var(--node-bg), var(--node-bg-2));
  border:1px solid var(--panel-border); color:var(--text); border-radius:6px; padding:.35rem .7rem;
  cursor:pointer; font-size:.75rem; text-decoration:none; transition:border-color .12s, color .12s; }
#theme-toggle:hover, #report-link:hover { border-color:var(--accent); color:var(--accent); }
#toolbar { display:flex; align-items:center; gap:.4rem; padding:.4rem 1rem; background:var(--panel);
  border-bottom:1px solid var(--panel-border); flex-wrap:wrap; font-size:.75rem; z-index:55; }
#toolbar button { background:linear-gradient(180deg, var(--node-bg), var(--node-bg-2)); border:1px solid var(--panel-border);
  color:var(--text); border-radius:6px; padding:.3rem .6rem; cursor:pointer; font-size:.75rem;
  transition:border-color .12s, color .12s, box-shadow .12s; }
#toolbar button:hover { border-color:var(--accent); color:var(--accent); box-shadow:0 0 6px rgba(90,169,255,.25); }
#filter-summary { margin-left:.4rem; color:var(--text-dim); }
#filter-summary.filters-active { color:var(--amber); font-weight:600; }
#filters-panel { background:var(--node-bg); border:1px solid var(--panel-border); border-radius:6px;
  padding:.3rem .5rem; }
#filters-panel summary { cursor:pointer; }
.filter-chip { display:inline-flex; align-items:center; gap:.2rem; margin:.15rem .6rem .15rem 0; font-size:.72rem; }
#canvas-wrap { position:relative; flex:1; overflow:hidden; }
#viewport { position:absolute; inset:0; overflow:hidden; cursor:grab; touch-action:none; }
#grid-bg { position:absolute; inset:-3000px; background-image:
  linear-gradient(var(--grid-minor) 1px, transparent 1px),
  linear-gradient(90deg, var(--grid-minor) 1px, transparent 1px),
  linear-gradient(var(--grid-major) 1.5px, transparent 1.5px),
  linear-gradient(90deg, var(--grid-major) 1.5px, transparent 1.5px); }
#world { position:absolute; left:0; top:0; transform-origin:0 0; }
#wires-svg { position:absolute; left:0; top:0; overflow:visible; pointer-events:none; }
.wire { fill:none; pointer-events:stroke; stroke-width:2.2px; cursor:pointer; stroke-linecap:round; }
.wire-secondary { stroke-width:1.5px; }
.wire.dim { opacity:.1; }
.wire.selected, .wire.path-highlight { opacity:1; filter:drop-shadow(0 0 4px var(--accent)) drop-shadow(0 0 1px var(--accent)); }
/* Confidence is this tool's whole thesis, so it is never colour alone:
   solid vs. dashed vs. dotted is the shape encoding, stroke-width and glow
   intensity fall with confidence, and CERTAIN is the only level that glows
   by default (visible without hovering or selecting). */
.wire-conf-CERTAIN { stroke:var(--green); stroke-width:3.4px;
  filter:drop-shadow(0 0 3px rgba(63,206,124,.65)); }
.wire-conf-RESOLVED { stroke:var(--accent); stroke-width:2.6px; }
.wire-conf-PROBABLE { stroke:var(--amber); stroke-width:2.1px; stroke-dasharray:10 6; }
.wire-conf-HEURISTIC { stroke:var(--amber); stroke-width:1.8px; stroke-dasharray:2 5; opacity:.92; }
.wire-conf-UNKNOWN { stroke:var(--red); stroke-width:1.8px; stroke-dasharray:2 5; opacity:.92; }
.node { position:absolute; background:linear-gradient(165deg, var(--node-bg) 0%, var(--node-bg-2) 100%);
  border:1px solid var(--node-border); border-radius:10px;
  box-shadow:0 3px 10px rgba(0,0,0,.45), inset 0 1px 0 rgba(255,255,255,.04);
  overflow:hidden; user-select:none; cursor:grab; transition:box-shadow .12s, border-color .12s; }
.node:hover { border-color:var(--accent); box-shadow:0 4px 14px rgba(0,0,0,.5), 0 0 0 1px var(--accent); }
/* !important: dimming is an interaction state (highlighting a selection's
   neighbours), not a fact about the element, and it must always win over
   every other opacity rule on this page (reach-NO_SINK_PATH, ghost,
   REMOVED, ...) regardless of which of those also applies to this node --
   the same reasoning as the `[hidden]` rule above, for the same reason:
   verification already found one opacity/specificity fight on this page
   that silently produced the wrong visible state. */
.node.dim { opacity:.1 !important; }
.node.selected { outline:2px solid var(--accent); outline-offset:2px; box-shadow:0 0 0 5px rgba(90,169,255,.2); }
.node.path-highlight { outline:2px solid var(--sink); outline-offset:2px; box-shadow:0 0 0 5px rgba(255,182,72,.22); }
.node-header { background:var(--kind-color, var(--node-header));
  background-image:linear-gradient(180deg, rgba(255,255,255,.16), rgba(255,255,255,0));
  color:#fff; font-size:.62rem; font-weight:700; text-transform:uppercase; letter-spacing:.05em;
  padding:.22rem .45rem; border-bottom:1px solid rgba(0,0,0,.25); text-shadow:0 1px 1px rgba(0,0,0,.35); }
.node-body { padding:.3rem .45rem; font-size:.78rem; font-weight:600; white-space:nowrap;
  overflow:hidden; text-overflow:ellipsis; }
.node-badges { display:flex; gap:.2rem; flex-wrap:wrap; padding:0 .45rem .35rem; }
.badge { font-size:.58rem; padding:.05rem .35rem; border-radius:3px; border:1px solid var(--panel-border);
  white-space:nowrap; background:rgba(127,127,127,.08); }
.reach-badge.reach-REACHES_SINK { background:rgba(63,206,124,.2); border-color:var(--green); color:var(--green); }
.reach-badge.reach-NO_SINK_PATH { border-style:dashed; background:rgba(139,149,165,.14); }
.reach-badge.reach-UNKNOWN { border-style:dotted; border-color:var(--red); color:var(--red); }
/* D8: NO_SINK_PATH and UNKNOWN must be distinguishable from REACHES_SINK
   at a glance without reading as "deleted" -- ghosts and REMOVED changes
   already own that visual language (see .node-ghost / .change-REMOVED,
   opacity .45-.5). Verification measured .8 opacity as unreadable at fit
   zoom on a dark canvas; the badge text and the border style now carry
   the distinction, so opacity barely moves and can never be confused
   with "gone". */
.node.reach-NO_SINK_PATH { opacity:.97; border-style:dashed; }
.node.reach-UNKNOWN { opacity:.97; border-style:dotted; }
/* D7: a true 4-point diamond only has usable width at its vertical
   centre -- label text and badges drawn with the old
   `polygon(50% 0,100% 50%,50% 100%,0 50%)` overflowed the shape at any
   zoom. A chamfered octagon keeps the same "not a rectangle, it is a
   branch" read (still diamond-ish, per the original brief) while giving a
   real rectangular safe area in the middle for content -- see the
   percentage padding below, which is sized to that safe area, not to the
   full bounding box. */
.kind-DECISION { clip-path:polygon(26% 0,74% 0,100% 26%,100% 74%,74% 100%,26% 100%,0 74%,0 26%);
  display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center;
  /* Fixed px, not a percentage: this node is `position:absolute` and its
     containing block is `#world`, which is thousands of px wide -- a
     percentage padding resolves against *that* width, not this node's own
     158px, and inflated this box to ~1250px before this fix. Values below
     are chosen to match what 17%/15% of DECISION_SIZE (158px) meant. */
  padding:27px 24px; gap:.15rem;
  background:linear-gradient(165deg, var(--kind-color, var(--node-header)) 0%, var(--node-bg-2) 130%); }
.kind-DECISION .node-header { background:transparent; color:var(--text); border-bottom:none;
  text-shadow:none; padding:0; font-size:.56rem; }
.kind-DECISION .node-body { white-space:normal; overflow-wrap:anywhere; font-size:.64rem; line-height:1.15;
  max-height:2.3em; overflow:hidden; padding:0; }
.kind-DECISION .node-badges { justify-content:center; padding:.1rem 0 0; max-width:100%; }
.kind-DECISION .badge { font-size:.5rem; padding:0 .25rem; max-width:4.6rem; overflow:hidden;
  text-overflow:ellipsis; white-space:nowrap; }
.node-sink { box-shadow:0 0 0 3px var(--sink), 0 0 14px rgba(255,182,72,.5), 0 3px 10px rgba(0,0,0,.45); }
.node-sink::after { content:'SINK'; position:absolute; top:1px; right:3px; font-size:.52rem;
  color:var(--sink); font-weight:800; text-shadow:0 0 4px rgba(255,182,72,.6); }
.node-module-agg { border-style:dashed; }
/* `.node.node-ghost` / `.node.change-REMOVED`, not the bare class: a
   ghost or removed node's `reachability.state` is UNKNOWN (see
   `_ghost_node` in blueprint.py), and `.node.reach-UNKNOWN` is itself a
   two-class selector. A single-class `.node-ghost` would lose that
   specificity fight and render at full brightness -- "gone" must win over
   "reachability unknown" for opacity, always. */
.node.node-ghost { opacity:.5; border-style:dashed; }
.node-loudest { box-shadow:0 0 0 3px var(--red); }
.change-ADDED { border-color:var(--green); box-shadow:0 0 8px var(--green); }
.node.change-REMOVED { opacity:.45; border-style:dashed; }
.change-RENAMED { border-color:var(--accent); }
.change-MOVED { border-color:var(--purple); }
.change-SIGNATURE_CHANGED { border-color:var(--amber); }
.change-BODY_CHANGED { border-color:var(--amber); }
.change-DECORATORS_CHANGED { border-color:var(--purple); }
.change-AMBIGUOUS { border-style:dotted; border-color:var(--red); }
/* D5/D6: a module aggregate's change-count badges reuse the exact same
   colours as the per-element `.change-*` border treatments above, so the
   legend entry for "wire confidence + node reachability + version diff"
   covers both the expanded and the collapsed reading with one key. */
.change-badge.change-ADDED { border-color:var(--green); color:var(--green); }
.change-badge.change-REMOVED { border-color:var(--text-dim); color:var(--text-dim); border-style:dashed; }
.change-badge.change-RENAMED { border-color:var(--accent); color:var(--accent); }
.change-badge.change-MOVED { border-color:var(--purple); color:var(--purple); }
.change-badge.change-SIGNATURE_CHANGED, .change-badge.change-BODY_CHANGED { border-color:var(--amber); color:var(--amber); }
.change-badge.change-DECORATORS_CHANGED { border-color:var(--purple); color:var(--purple); }
.change-badge.change-AMBIGUOUS { border-style:dotted; border-color:var(--red); color:var(--red); }
.change-loudest-badge { border-color:var(--red); color:#fff; background:var(--red); font-weight:700; }
/* Pins are where wires actually attach (bezier control points read these
   same coordinates), so they need to read as connectors, not decoration:
   an input pin is a hollow ring the wire runs into; an output pin is a
   filled, glowing dot the wire leaves from -- the same shape language a
   node-based editor like this is modelled on uses. */
.pin { position:absolute; width:11px; height:11px; border-radius:50%; top:50%;
  transform:translateY(-50%); z-index:2; box-shadow:0 0 0 2px var(--node-bg); }
.pin-in { left:-6px; background:var(--node-bg); border:2px solid var(--text-dim); }
.pin-out { right:-6px; background:var(--accent); border:2px solid var(--node-bg);
  box-shadow:0 0 0 2px var(--node-bg), 0 0 6px rgba(90,169,255,.7); }
.pin-outcome { right:-6px; background:var(--sink); border:2px solid var(--node-bg);
  box-shadow:0 0 0 2px var(--node-bg), 0 0 6px rgba(255,182,72,.7); }
#empty-state { position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
  text-align:center; padding:2rem; font-size:1rem; color:var(--text-dim); background:var(--bg); z-index:20; }
/* D10: every long-id context in this panel uses `overflow-wrap:anywhere`
   with `word-break:normal`, never the legacy `word-break:break-word` --
   `break-word` breaks *anywhere* it needs to, including mid-word, which is
   what produced "...@d / ecision0". `overflow-wrap:anywhere` only breaks
   as a last resort and prefers the `<wbr>` opportunities `elWithBreaks()`
   inserts at `::`/`.`/`_` boundaries, so a long id wraps at a natural seam
   whenever one exists and only falls back to an arbitrary break when it
   truly must. */
#detail-panel { position:absolute; right:0; top:0; bottom:0; width:23rem; max-width:92vw; background:var(--panel);
  border-left:1px solid var(--panel-border); box-shadow:-8px 0 24px rgba(0,0,0,.35);
  overflow-y:auto; overflow-x:hidden; padding:.8rem; z-index:70; overflow-wrap:anywhere; word-break:normal; }
#detail-panel h2 { font-size:1rem; overflow-wrap:anywhere; word-break:normal; margin:.2rem 3.5rem .3rem 0; }
#detail-panel .detail-id { overflow-wrap:anywhere; word-break:normal; color:var(--text-dim); font-size:.72rem; margin-bottom:.4rem; }
#detail-close { float:right; background:var(--node-bg); border:1px solid var(--panel-border);
  color:var(--text); border-radius:6px; cursor:pointer; }
.detail-row { display:flex; gap:.4rem; font-size:.76rem; padding:.18rem 0; border-bottom:1px solid var(--panel-border); }
.detail-label { color:var(--text-dim); width:8.5rem; flex-shrink:0; }
.detail-value { overflow-wrap:anywhere; word-break:normal; min-width:0; }
#detail-content ul { margin:.3rem 0; padding-left:1.1rem; }
#detail-content li { overflow-wrap:anywhere; word-break:normal; margin:.15rem 0; }
.id-link { background:none; border:none; color:var(--accent); cursor:pointer; padding:0; font:inherit;
  text-decoration:underline; white-space:normal; overflow-wrap:anywhere; word-break:normal; text-align:left; }
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
#legend { position:absolute; left:.6rem; bottom:.6rem; width:27rem; max-width:calc(100% - 1.2rem);
  background:var(--panel); border:1px solid var(--panel-border); border-radius:10px;
  box-shadow:0 10px 28px rgba(0,0,0,.45); font-size:.68rem; z-index:30; overflow:hidden; }
#legend-header { background:linear-gradient(180deg, var(--node-header), transparent);
  padding:.4rem .7rem; font-weight:700; font-size:.68rem; letter-spacing:.04em; text-transform:uppercase;
  color:var(--text-dim); border-bottom:1px solid var(--panel-border); }
#legend-body { display:grid; grid-template-columns:1fr 1fr; gap:0 1rem; padding:.55rem .7rem .65rem; }
.legend-col h4 { margin:0 0 .3rem; font-size:.64rem; text-transform:uppercase; letter-spacing:.03em; color:var(--accent); }
#legend-diff-details { border-top:1px solid var(--panel-border); }
#legend-diff-details summary { cursor:pointer; padding:.4rem .7rem; font-size:.66rem; font-weight:700;
  text-transform:uppercase; letter-spacing:.03em; color:var(--accent); }
#legend-diff { padding:0 .7rem .6rem; max-height:14rem; overflow-y:auto; }
.legend-row { display:flex; align-items:flex-start; gap:.4rem; margin:.22rem 0; line-height:1.3; }
.legend-swatch { width:1.5rem; height:0; display:inline-block; margin-top:.4rem; flex-shrink:0; }
.legend-swatch.wire-conf-CERTAIN { border-top:3px solid var(--green); filter:drop-shadow(0 0 2px rgba(63,206,124,.6)); }
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
<div id="legend-header">Legend &mdash; what this canvas encodes</div>
<div id="legend-body">
<div class="legend-col"><h4>Wire confidence</h4><div id="legend-confidence"></div></div>
<div class="legend-col"><h4>Node reachability</h4><div id="legend-reachability"></div></div>
</div>
<details id="legend-diff-details"><summary>Version diff encodings</summary>
<div id="legend-diff"></div>
</details>
</div>
<div id="shortcuts-help">/ search &middot; Esc clear search &middot; F fit &middot; 1/2/3 tabs</div>
</div>"""

_SCRIPT = """
(function () {
'use strict';
var dataEl = document.getElementById('cascade-blueprint-data');
var DATA = JSON.parse(dataEl.textContent);

var NODE_W = 220, NODE_H = 72, DECISION_SIZE = 158;
var COL_GAP = 300, ROW_GAP = 110;
var MODULE_COLLAPSE_THRESHOLD = DATA.module_collapse_threshold || 150;
var KIND_COLORS = {
  MODULE:'#4c8bf5', CLASS:'#a970ff', FUNCTION:'#35c470', METHOD:'#2fb6c4',
  DECISION:'#ffb648', MODULE_GROUP:'#7d8aa3', UNKNOWN:'#8993a4', FEATURE:'#e07be0',
  LOCAL:'#9aa6b8', PARAMETER:'#c9b458', CONTAINER_KEY:'#c98458', ATTRIBUTE:'#67c9a4',
  FILE:'#8993a4', CONFIG_KEY:'#c98458', BARRIER:'#e5484d'
};
//: Which ChangeKind, if several are present among a collapsed module's
//: members, gets the aggregate card's own border/glow treatment. This
//: picks ONE visual accent for the card; it never replaces the per-kind
//: counts shown as badges, which are the actual rollup of facts -- see
//: `renderTab`'s module aggregation and the D5 note in this file's history.
var CHANGE_KIND_PRIORITY = [
  'AMBIGUOUS', 'REMOVED', 'ADDED', 'SIGNATURE_CHANGED', 'BODY_CHANGED',
  'DECORATORS_CHANGED', 'RENAMED', 'MOVED'
];

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
// D10: element/decision ids are one long token with no spaces
// ("mode_b.cfg_shapes::branching::@decision0"), so the browser's own line
// breaking has no natural opportunity and falls back to breaking
// mid-character wherever a line happens to end. This splits on the id
// grammar's own delimiters (`::`, `.`, `_`) and inserts a real `<wbr>`
// (a browser-native, zero-width break *opportunity*, not a forced break)
// after each one -- via DOM nodes, never innerHTML, so a hostile id
// string is exactly as inert here as everywhere else on this page.
function elWithBreaks(tag, cls, text) {
  var e = document.createElement(tag);
  if (cls) e.className = cls;
  var parts = String(text === undefined || text === null ? '' : text).split(/(::|[._])/);
  parts.forEach(function (part) {
    if (part === '') return;
    e.appendChild(document.createTextNode(part));
    if (part === '::' || part === '.' || part === '_') {
      e.appendChild(document.createElement('wbr'));
    }
  });
  return e;
}

// ---- theme ----
function loadTheme() {
  // The owner asked for "a highly visually aesthetic flow chart similar to
  // that from Unreal Engine 5.0" -- Blueprint is dark, so this page opens
  // dark unconditionally, not from `prefers-color-scheme`. The toggle
  // below and the localStorage remembered choice still both work either
  // direction; only the *default*, on a machine that has never opened this
  // page before, is fixed.
  try {
    var saved = window.localStorage.getItem('cascade_blueprint_theme');
    if (saved === 'dark' || saved === 'light') return saved;
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
          finding_count: 0, is_sink: false,
          // D5: a module aggregate must still show that its collapsed
          // members changed. change_counts is a rollup of *facts* (how many
          // members earned each ChangeKind on the member's own card); it
          // is never turned into an invented single status like "MODIFIED".
          change_counts: {}, changed_member_count: 0, loudest: false
        };
      }
      agg.count += 1;
      agg.name = mod + ' (' + agg.count + ')';
      agg.members.push(n.id);
      agg.layer = Math.min(agg.layer, n.layer);
      agg.finding_count += (n.finding_count || 0);
      (n.changes || []).forEach(function (c) {
        agg.change_counts[c.kind] = (agg.change_counts[c.kind] || 0) + 1;
        agg.changed_member_count += 1;
        if (c.decision_paths_changed || (c.reachability_flipped && c.reachability_flipped.length)) {
          agg.loudest = true;
        }
      });
    } else {
      visible.push(n);
    }
  });
  var aggList = Object.keys(moduleAgg).sort().map(function (k) {
    var agg = moduleAgg[k];
    for (var i = 0; i < CHANGE_KIND_PRIORITY.length; i++) {
      if (agg.change_counts[CHANGE_KIND_PRIORITY[i]]) { agg.strongest_change_kind = CHANGE_KIND_PRIORITY[i]; break; }
    }
    return agg;
  });
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
  var orderedLayers = Object.keys(byLayer).map(Number).sort(function (a, b) { return a - b; });

  // A layer is a column of unbounded height by construction (every node
  // that could not be placed more precisely lands at layer 0), and a real
  // target collapses many small, mutually independent modules onto that
  // one column. Left uncapped, "Fit" has to shrink the whole canvas to fit
  // one 18,000px-tall column and every node becomes illegible. Wrapping
  // each layer into a roughly-square block of sub-columns bounds both
  // width and height to about sqrt(node count) -- the same reasoning as
  // the layer computation itself: this is packing, not analysis, and it
  // never changes which layer (hence which drawn wires) a node has.
  var rowsPerSubcol = Math.max(6, Math.ceil(Math.sqrt(Math.max(1, displayNodes.length))));
  var pos = {};
  var xCursor = 0;
  orderedLayers.forEach(function (l) {
    var members = byLayer[l];
    var subcols = Math.max(1, Math.ceil(members.length / rowsPerSubcol));
    members.forEach(function (n, i) {
      var saved = state.positions[tab][n.id];
      if (saved) { pos[n.id] = { x: saved.x, y: saved.y }; return; }
      var subcol = Math.floor(i / rowsPerSubcol);
      var row = i % rowsPerSubcol;
      pos[n.id] = { x: xCursor + subcol * COL_GAP, y: row * ROW_GAP };
    });
    xCursor += subcols * COL_GAP;
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
  // D5: a collapsed module aggregate carries no `.changes` of its own --
  // its members' changes were rolled up into `change_counts` and
  // `strongest_change_kind` when the module was collapsed (see
  // computeLayout). The aggregate's card gets the same `change-<kind>`
  // treatment its loudest member earned, so collapse never reads as "no
  // changes here" when the data says otherwise.
  if (n.strongest_change_kind) wrap.classList.add('change-' + cssSafe(n.strongest_change_kind));
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
  if (n.change_counts) {
    // One badge per ChangeKind actually present among the collapsed
    // module's members -- "3 ADDED, 1 BODY_CHANGED" is a count of facts;
    // it is deliberately never collapsed into one invented word.
    Object.keys(n.change_counts).sort().forEach(function (kind) {
      badges.appendChild(el('span', 'badge change-badge change-' + cssSafe(kind), n.change_counts[kind] + ' ' + kind));
    });
    if (n.loudest) {
      badges.appendChild(el('span', 'badge change-badge change-loudest-badge', '\\u26a0 decision path changed'));
    }
  }
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

  updateSelectionHighlight();
  updateEmptyState(tab);
  // Every call site that changes what is laid out (initial paint, a tab
  // switch, expand/collapse, a filter change) calls renderTab only for the
  // active tab -- see the comment on `nodesLayer`/`wiresG` being shared,
  // single DOM containers, further down. Node dragging and search do not
  // call renderTab at all, so fitting here cannot fight either of those.
  // A stale camera pointed at empty space above-left of the content was
  // verification's D3.
  fitToContent();
}

// ---- camera: pan, zoom, fit ----
function applyCameraTransform() {
  world.style.transform = 'translate(' + state.camera.x + 'px,' + state.camera.y + 'px) scale(' + state.camera.scale + ')';
  var bg = document.getElementById('grid-bg');
  var minor = Math.max(4, 40 * state.camera.scale);
  var major = minor * 5;
  var minorSize = minor + 'px ' + minor + 'px';
  var majorSize = major + 'px ' + major + 'px';
  bg.style.backgroundSize = minorSize + ', ' + minorSize + ', ' + majorSize + ', ' + majorSize;
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
  // D9: `pad - min*scale` pins the content's top-left corner at a fixed
  // (pad, pad) regardless of which axis the scale came from, so the
  // *other* axis -- whichever was not the limiting one -- is left with
  // unused space pushed entirely to the right/bottom instead of split
  // evenly. Centre both axes: place the fitted content's midpoint at the
  // viewport's midpoint.
  var contentW = (maxX - minX) * scale, contentH = (maxY - minY) * scale;
  state.camera.x = (rect.width - contentW) / 2 - minX * scale;
  state.camera.y = (rect.height - contentH) / 2 - minY * scale;
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
  var display = value === null || value === undefined || value === '' ? '\\u2014' : value;
  row.appendChild(elWithBreaks('span', 'detail-value', display));
  return row;
}
function idLink(id, tab, labelText) {
  var btn = elWithBreaks('button', 'id-link', labelText || id);
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
  panel.appendChild(elWithBreaks('h2', null, n.name || n.id));
  panel.appendChild(elWithBreaks('div', 'detail-id', n.id));
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
    if (n.change_counts && Object.keys(n.change_counts).length) {
      panel.appendChild(el('h3', null, 'version diff (rolled up from ' + n.changed_member_count + ' changed member' +
        (n.changed_member_count === 1 ? '' : 's') + ')'));
      var rollupParts = Object.keys(n.change_counts).sort().map(function (kind) {
        return n.change_counts[kind] + ' ' + kind;
      });
      panel.appendChild(el('p', null, rollupParts.join(', ') + '.'));
      if (n.loudest) {
        panel.appendChild(el('p', 'missing-note', '\\u26a0 at least one member changes a path to a decision.'));
      }
      panel.appendChild(el('p', 'missing-note', 'expand the module for the per-element detail below.'));
    }
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
      renderTab(state.tab); // filters/collapse are global state; only the visible tab needs (re)painting
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
    renderTab(state.tab); // filters/collapse are global state; only the visible tab needs (re)painting
    updateFilterSummary();
  });
  document.getElementById('filter-decision-reach').addEventListener('change', function (ev) {
    state.filters.onlyDecisionReach = ev.target.checked;
    renderTab(state.tab); // filters/collapse are global state; only the visible tab needs (re)painting
    updateFilterSummary();
  });
  document.getElementById('filter-findings').addEventListener('change', function (ev) {
    state.filters.onlyFindings = ev.target.checked;
    renderTab(state.tab); // filters/collapse are global state; only the visible tab needs (re)painting
    updateFilterSummary();
  });
  document.getElementById('btn-clear-filters').addEventListener('click', function () {
    state.filters = defaultFilters();
    syncFilterUI();
    renderTab(state.tab); // filters/collapse are global state; only the visible tab needs (re)painting
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
  var diffWrap = document.getElementById('legend-diff');
  (DATA.legend.diff || []).forEach(function (item) {
    var row = el('div', 'legend-row');
    row.appendChild(el('span', 'badge change-badge', item.kind));
    row.appendChild(document.createTextNode(' -- ' + item.style));
    diffWrap.appendChild(row);
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
