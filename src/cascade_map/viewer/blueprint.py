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

import base64
import gzip
import heapq
import json
from dataclasses import dataclass, replace
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
# Round 6: lossless compression of the data island
#
# Two independent, purely mechanical transforms, both exactly reversible:
#
#   1. `_pack_data` -- string/scalar interning plus a shape table. Every
#      scalar in the document (string, number, bool, null) is stored ONCE in
#      a sorted pool and referred to by integer index; every object's key
#      list is stored ONCE in a sorted shape table and referred to by index.
#      On this tool's own output the ids dominate the bytes: the owner's
#      lineage artifacts carry 80,001 rows over 37,500 distinct ids of mean
#      length 59, so each id is written once instead of tens of thousands of
#      times.
#
#   2. gzip + base64 (`_compress_island`). Applied to the packed form, in
#      the page, inflated by the browser's own `DecompressionStream` or, if
#      the browser has none, by the inflater embedded in the page itself.
#
# NOTHING IS DROPPED BY EITHER. No node, edge, record or field is removed,
# shortened, rounded or sampled; `_unpack_data(_pack_data(x)) == x` for
# every x the page can carry, and the JS decoder is the same inverse. The
# bounds that DO drop things -- `--scope`, `--max-nodes`, `--focus`, the
# detail caps -- are round 5's and are unchanged; this is orthogonal to
# them and can be applied to any of them.
# ---------------------------------------------------------------------------

#: Marker written into every packed island. A decoder that does not know
#: this exact string must refuse rather than guess at the layout.
PACK_FORMAT = "cascade-blueprint-pack/1"

#: The array tag. A packed array is ``[-1, item, ...]``; a packed object is
#: ``[shape_index, value, ...]`` with ``shape_index >= 0``; a packed scalar
#: is a bare integer index into the pool. The three are distinguishable in
#: JavaScript by `typeof`/`Array.isArray` alone, with no per-value tag.
PACK_ARRAY_TAG = -1

#: Sort rank per scalar type, so the pool's order is a total order over a
#: heterogeneous set and therefore identical on two runs. Within a rank the
#: values are of one type and compare directly.
_POOL_TYPE_RANK = {"null": 0, "bool": 1, "int": 2, "float": 3, "str": 4}


def _scalar_key(value: Any) -> tuple[str, Any]:
    """A pool key that never conflates values JSON keeps apart.

    Python hashes ``True == 1 == 1.0`` equal, so a plain set would merge a
    boolean, an integer and a float into one pool entry and hand the wrong
    type back on decode. The type tag keeps them apart, which is the whole
    difference between "smaller" and "lossless".
    """
    if value is None:
        return ("null", "")
    if value is True or value is False:
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, float):
        return ("float", value)
    if isinstance(value, str):
        return ("str", value)
    raise TypeError(f"not a JSON scalar: {type(value).__name__}")


def _pack_data(data: Any) -> dict[str, Any]:
    """*data*, interned. Same information, far fewer bytes.

    Two passes. The first surveys every scalar and every object key-set;
    both are then sorted, which is what makes the tables -- and so the
    whole island -- byte-identical on two runs. The second pass rewrites
    the document against those tables.
    """
    scalars: set[tuple[str, Any]] = set()
    shapes: set[tuple[str, ...]] = set()

    def survey(node: Any) -> None:
        if isinstance(node, dict):
            keys = tuple(sorted(node))
            shapes.add(keys)
            for key in keys:
                scalars.add(("str", key))
                survey(node[key])
        elif isinstance(node, (list, tuple)):
            for item in node:
                survey(item)
        else:
            scalars.add(_scalar_key(node))

    survey(data)
    pool_keys = sorted(scalars, key=lambda kv: (_POOL_TYPE_RANK[kv[0]], kv[1]))
    pool_index = {key: position for position, key in enumerate(pool_keys)}
    pool = [None if key[0] == "null" else key[1] for key in pool_keys]
    shape_keys = sorted(shapes)
    shape_index = {keys: position for position, keys in enumerate(shape_keys)}

    def encode(node: Any) -> Any:
        if isinstance(node, dict):
            keys = tuple(sorted(node))
            packed: list[Any] = [shape_index[keys]]
            packed.extend(encode(node[key]) for key in keys)
            return packed
        if isinstance(node, (list, tuple)):
            items: list[Any] = [PACK_ARRAY_TAG]
            items.extend(encode(item) for item in node)
            return items
        return pool_index[_scalar_key(node)]

    return {
        "format": PACK_FORMAT,
        "pool": pool,
        "shapes": [[pool_index[("str", key)] for key in keys] for keys in shape_keys],
        "root": encode(data),
    }


def _unpack_data(packed: dict[str, Any]) -> Any:
    """The exact inverse of :func:`_pack_data`, and the JS decoder's twin.

    Present so that losslessness is something the suite can *check* rather
    than something this module asserts: `test_blueprint_compression.py`
    round-trips the whole island, field by field, in Python, and
    `test_blueprint_render.py` does the same comparison in a real browser
    against a page rendered without compression.
    """
    if packed.get("format") != PACK_FORMAT:
        raise ValueError(f"not a {PACK_FORMAT} island: {packed.get('format')!r}")
    pool = packed["pool"]
    shapes = [[pool[index] for index in shape] for shape in packed["shapes"]]

    def decode(node: Any) -> Any:
        if isinstance(node, list):
            head = node[0]
            if head == PACK_ARRAY_TAG:
                return [decode(item) for item in node[1:]]
            keys = shapes[head]
            return {key: decode(node[position + 1]) for position, key in enumerate(keys)}
        return pool[node]

    return decode(packed["root"])


def _island_json(packed: dict[str, Any]) -> str:
    """The packed island as compact JSON, for gzipping.

    ``ensure_ascii`` is off here and only here: this text is never embedded
    in HTML, it is gzipped and base64-ed, and the browser decodes it as
    UTF-8. Dropping the ``\\uXXXX`` escaping is both smaller and exactly as
    deterministic -- UTF-8 encoding of a fixed string is a fixed byte
    sequence.
    """
    return json.dumps(packed, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _compress_island(packed: dict[str, Any]) -> str:
    """gzip + base64 of the packed island, as ASCII safe for an HTML script.

    ``mtime=0``: gzip writes the wall clock into its header by default,
    which would make two runs differ by four bytes and break constraint 4.
    The base64 alphabet contains none of ``<``, ``>`` or ``&``, so the
    result needs no further escaping to sit inside a ``<script>`` element.
    """
    raw = _island_json(packed).encode("utf-8")
    blob = gzip.compress(raw, compresslevel=9, mtime=0)
    return base64.b64encode(blob).decode("ascii")


# ---------------------------------------------------------------------------
# Node views shared across tabs
# ---------------------------------------------------------------------------


def _capped_reachability(reach: dict[str, Any]) -> dict[str, Any]:
    """`element_reachability`, with its representative path capped and counted.

    Card 3's `path_ids` is one representative path to a sink; on a deep
    cascade it is hundreds of ids long and it rides on every node AND in
    every detail record. The cap is stated in `path_ids_total`, and the
    panel says "first N of M" rather than presenting N as the whole path.
    """
    reach = dict(reach)
    path_ids = reach.get("path_ids") or []
    reach["path_ids_total"] = len(path_ids)
    if len(path_ids) > PATH_IDS_CAP:
        reach["path_ids"] = list(path_ids[:PATH_IDS_CAP])
    reason = reach.get("reason") or ""
    # Card 3's `reason` spells the same representative path out in prose,
    # so on a deep cascade it is ~620 bytes of text carried on every node.
    # Cut with the cut declared, never silently: `reason_total_chars` is
    # the real length and reachability.jsonl holds the whole sentence.
    if len(reason) > REASON_CAP:
        reach["reason"] = reason[:REASON_CAP] + " ..."
        reach["reason_total_chars"] = len(reason)
    return reach


#: How a node's single alignment badge is chosen when an element carries
#: more than one verdict. Worst first: a contradiction must never be hidden
#: behind an agreement, and NOT_EXERCISED must never be hidden behind
#: anything -- it is the one an owner is most likely to misread as a pass.
_VERDICT_RANK = (
    "MISALIGNED",
    "NOT_EXERCISED",
    "UNVERIFIABLE",
    "ALIGNED",
    "NO_INTENT",
)


def _static_verdict_badge(store: ArtifactStore, element_id: str) -> str | None:
    """The one verdict a node shows, or ``None`` when nothing judged it."""
    seen = {
        str(v.get("verdict"))
        for v in store.static_verdicts_by_element.get(element_id, [])
        if v.get("verdict")
    }
    for name in _VERDICT_RANK:
        if name in seen:
            return name
    return next(iter(sorted(seen)), None)


def _element_node(store: ArtifactStore, element_id: str) -> dict[str, Any]:
    """A node view for one element id, real or unresolved -- never a crash."""
    element = store.elements_by_id.get(element_id)
    reach = _capped_reachability(views.element_reachability(store, element_id))
    finding_count = len(store.findings_by_element.get(element_id, []))
    has_record = element_id in store.records_by_element
    # Card 13. `None` means "no intent names this element", which the panel
    # renders as nothing at all -- it is not NO_INTENT wearing a badge, and
    # it is certainly not a pass.
    verdict = _static_verdict_badge(store, element_id)
    intent_count = len(store.intents_by_element.get(element_id, []))
    if element is None:
        return {
            "id": element_id, "is_element": False, "kind": "UNKNOWN",
            "name": element_id, "qualname": element_id, "module": "",
            "confidence": None, "method": None, "span": None,
            "reachability": reach, "finding_count": finding_count,
            "has_record": has_record, "static_verdict": verdict,
            "intent_count": intent_count,
        }
    summary = views.element_summary(element)
    return {
        "id": element_id, "is_element": True, "kind": summary["kind"],
        "name": summary["name"], "qualname": summary["qualname"] or summary["name"],
        "module": summary["module"] or "", "confidence": summary["confidence"],
        "method": summary["method"], "span": summary["span"],
        "reachability": reach, "finding_count": finding_count, "has_record": has_record,
        "static_verdict": verdict, "intent_count": intent_count,
    }


def _decision_node(store: ArtifactStore, decision: dict[str, Any]) -> dict[str, Any]:
    prov = decision.get("provenance") or {}
    owner = decision.get("element_id", "") or ""
    owner_el = store.elements_by_id.get(owner) or {}
    outcomes = [list(o) for o in decision.get("outcomes") or ()]
    # The owner element's node carries the whole Reachability record; a
    # decision diamond needs only the state, for its badge and the
    # "reaches a decision" filter. Repeating the reason and the path here
    # cost 0.6 MB of pure duplication on the owner's engine.
    owner_reach = views.element_reachability(store, owner) if owner else {}
    reach = (
        {
            "state": owner_reach.get("state"), "source": "owner",
            "reason": "carried by this decision's owning element",
            "sink_ids": [], "path_ids": [], "confidence": owner_reach.get("confidence"),
        }
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
        "reachability": _capped_reachability(views.element_reachability(store, id_)),
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


#: card 3's own id for "the cascade, from its entry point" (see cascade.py,
#: `make_id("@order", "@cascade")`). Its children are the engine's phases in
#: execution order -- the one grouping round 4 was told to use, because it
#: is card 3's fact, not a guess made here.
CASCADE_ROOT_ID = "@order::@cascade"


def _stage_member_ids(
    store: ArtifactStore, node_id: str, execution_ids: set[str], seen: set[str]
) -> list[str]:
    """Every execution-kind element under *node_id* in the order tree, in
    the order card 3 already gave them -- own `element_ids` first, then
    each child's subtree, in child order. `seen` guards the same cycles
    :func:`_order_node_view`/cascade_view already have to guard."""
    if node_id in seen:
        return []
    seen.add(node_id)
    node = store.order_by_id.get(node_id)
    if node is None:
        return []
    out = [eid for eid in node.get("element_ids") or () if eid in execution_ids]
    for child in node.get("children") or ():
        out.extend(_stage_member_ids(store, child, execution_ids, seen))
    return out


def _build_stages(
    store: ArtifactStore, element_ids: list[str]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Stages for the Execution tab's grouped default view (round 4, R1).

    The top-level children of :data:`CASCADE_ROOT_ID` are the engine's
    phases, already ordered by card 3 -- used verbatim, never re-derived.
    An element the order tree never placed (common in this fixture corpus,
    where most programs are standalone and wired to no detected entry
    point) falls back to a group named after its own module, marked
    ``rule: "fallback_module"`` so the card never claims to be a cascade
    phase it is not. Nothing here invents a phase name: every stage is
    either "Stage N" (positional -- card 3 does not name phases; see
    STATUS.md's card 14 lesson) or a real module path.
    """
    execution_ids = set(element_ids)
    cascade_node = store.order_by_id.get(CASCADE_ROOT_ID)
    if cascade_node and cascade_node.get("children"):
        stage_node_ids = list(cascade_node["children"])
        rule = "order:@cascade"
    else:
        stage_node_ids = sorted(store.order_roots)
        rule = "order:roots"

    stages: list[dict[str, Any]] = []
    covered: set[str] = set()
    for node_id in stage_node_ids:
        seen: set[str] = set()
        raw_members = _stage_member_ids(store, node_id, execution_ids, seen)
        ordered_unique: list[str] = []
        seen_members: set[str] = set()
        for eid in raw_members:
            if eid in covered or eid in seen_members:
                continue
            seen_members.add(eid)
            ordered_unique.append(eid)
        if not ordered_unique:
            continue
        covered.update(ordered_unique)
        node = store.order_by_id.get(node_id) or {}
        prov = node.get("provenance") or {}
        stages.append({
            "id": node_id, "name": f"Stage {len(stages) + 1}", "rule": rule,
            "order_node_id": node_id, "order_kind": node.get("kind"),
            "member_ids": ordered_unique, "confidence": prov.get("confidence"),
        })

    remaining = [eid for eid in element_ids if eid not in covered]
    by_module: dict[str, list[str]] = {}
    for eid in remaining:
        module = (store.elements_by_id.get(eid) or {}).get("module") or "(no module)"
        by_module.setdefault(module, []).append(eid)
    for module in sorted(by_module):
        stages.append({
            "id": f"@stage:fallback:{module}", "name": module, "rule": "fallback_module",
            "order_node_id": None, "order_kind": None,
            "member_ids": sorted(by_module[module]), "confidence": None,
        })

    stage_of: dict[str, int] = {}
    for index, stage in enumerate(stages):
        for eid in stage["member_ids"]:
            stage_of[eid] = index
    return stages, stage_of


def _scope_stages(stages: list[dict[str, Any]], included: set[str]) -> None:
    """Restrict every stage card's member list to what this page carries.

    Stage structure and numbering are always derived from the *whole*
    execution element set, so a scoped or truncated page never renumbers
    the owner's stages under them. What changes is the membership list,
    and each card records how many of its own members are missing so that
    an omission can never be drawn as an empty stage -- round 5's whole
    point.
    """
    for stage in stages:
        members = stage["member_ids"]
        kept = [eid for eid in members if eid in included]
        stage["member_total"] = len(members)
        stage["member_ids"] = kept
        stage["member_omitted"] = len(members) - len(kept)


#: `Confidence` order, weakest first is highest index -- reused to combine
#: several wires' confidence into one stage-pair's "weakest link" figure,
#: the same rule `combine()` in contracts/interfaces.py applies everywhere
#: else. Built from the enum, not hand-copied, so it cannot drift from it.
_FLOW_CONFIDENCE_RANK = {level.value: index for index, level in enumerate(Confidence)}


def _classify_and_summarize_flow(
    wires: list[dict[str, Any]], stage_of: dict[str, int]
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Classify every wire against the stage order (round 4, R2): FORWARD
    (into a later stage), BACKWARD (into an earlier one), WITHIN (same
    stage) or UNORDERED (either end has no stage -- a decision node, or an
    element the order tree never placed and fallback grouping still could
    not date -- never silently folded into one of the other three).

    This is classification of edges and stage indices card 3 and this
    module's own :func:`_build_stages` already produced -- no new edge, no
    new fact, no guessed direction.
    """
    pair_buckets: dict[tuple[int, int], dict[str, Any]] = {}
    totals = {"FORWARD": 0, "BACKWARD": 0, "WITHIN": 0, "UNORDERED": 0}
    for wire in wires:
        source_stage = stage_of.get(wire["source_id"])
        target_stage = stage_of.get(wire["target_id"])
        if source_stage is None or target_stage is None:
            flow = "UNORDERED"
        elif source_stage == target_stage:
            flow = "WITHIN"
        elif source_stage < target_stage:
            flow = "FORWARD"
        else:
            flow = "BACKWARD"
        wire["flow"] = flow
        wire["source_stage"] = source_stage
        wire["target_stage"] = target_stage
        totals[flow] += 1
        if flow in ("FORWARD", "BACKWARD"):
            key = (source_stage, target_stage)
            bucket = pair_buckets.setdefault(key, {
                "from": source_stage, "to": target_stage, "direction": flow,
                "count": 0, "wire_ids": [], "confidence_rank": len(_FLOW_CONFIDENCE_RANK),
            })
            bucket["count"] += 1
            bucket["wire_ids"].append(wire["id"])
            rank = _FLOW_CONFIDENCE_RANK.get(wire.get("confidence") or "", len(_FLOW_CONFIDENCE_RANK))
            if rank < bucket["confidence_rank"]:
                bucket["confidence_rank"] = rank

    rank_to_level = {index: level for level, index in _FLOW_CONFIDENCE_RANK.items()}
    pairs = []
    for bucket in pair_buckets.values():
        bucket["wire_ids"] = sorted(bucket["wire_ids"])
        bucket["confidence"] = rank_to_level.get(bucket.pop("confidence_rank"))
        pairs.append(bucket)
    pairs.sort(key=lambda p: (p["from"], p["to"]))
    return totals, pairs


# ---------------------------------------------------------------------------
# Round 5 -- scope, the hard size guard, and decision-relevance ranking.
#
# Round 4 embedded the whole graph as one unbounded JSON island. On the
# owner's real engine that produced 702 MB in a single file: every scale
# mechanism the page has (module auto-collapse, viewport culling) runs
# AFTER the browser has parsed the island, which it never survives to do.
# The file reported success -- "Wrote ... Open it in a browser" -- and was
# unopenable. Nothing below is a tuning constant on that failure; the
# default view is bounded *by construction*, and anything that would still
# be too large is refused before a byte is written, with the flags that
# make it smaller printed next to the number.
# ---------------------------------------------------------------------------

#: The two scopes. `cascade` (the default) is structure only: modules and
#: stages as cards, decision points, declared and detected sinks, entry
#: points and the edges between them. `full` is round 4's everything.
SCOPE_CASCADE = "cascade"
SCOPE_FULL = "full"
SCOPES = (SCOPE_CASCADE, SCOPE_FULL)

#: Default node budget in `cascade` scope. `full` scope defaults to no cap
#: -- it is the explicit "give me everything" scope, and the size guard,
#: not a silent cap, is what stands between it and an unopenable file.
DEFAULT_MAX_NODES = 2000

#: Default radius for `--focus`.
DEFAULT_HOPS = 2

#: The share of the node budget reserved for decision points. See the
#: comment in :func:`select`: without a reserved share, a real engine's
#: on-path elements fill the whole budget and the cascade scope draws none
#: of the decision points its own definition names first.
DECISION_BUDGET_SHARE = 0.5

#: Above this estimated data-island size, `render_blueprint_to_file`
#: refuses and writes nothing. 50 MB is already far past comfortable; it is
#: the point past which a refusal is certainly right, not a guess at where
#: the page gets slow.
SIZE_GUARD_BYTES = 50 * 1024 * 1024

#: How many entries of an unbounded per-element list (lineage edges in and
#: out, call edges, slice memberships) a detail record carries before it
#: states its own truncation. On the owner's engine one element's
#: `lineage_in` alone runs to thousands of full edge records, and every one
#: of them was being embedded, for every element, on top of the lineage
#: tab's own copy.
DETAIL_LIST_CAP = 3

#: Order-tree membership is capped separately and much harder. Card 3's
#: order tree on the owner's engine holds 137,414 nodes, and an element can
#: be a member of hundreds of them; each membership then drags its whole
#: ancestor chain into `order_ancestor_ids`. Measured: at a cap of 40 that
#: one field alone was 66.7 MB of a 103.7 MB island -- more than everything
#: else on the page put together.
ORDER_NODE_CAP = 2
ORDER_ANCESTOR_CAP = 1

#: `Reachability.path_ids` is card 3's one representative path to a sink.
#: On a deep cascade that path is hundreds of ids long, and it is carried
#: on every node AND in every detail record.
PATH_IDS_CAP = 4

#: How many characters of `Reachability.reason` a node carries.
REASON_CAP = 160

#: Floors under the compression ratio, used only by the *pre-build* check
#: (see :meth:`SizeEstimate.effective_limit`). Measured on the owner's real
#: engine at `--scope full`: interning alone 2.98x, interning + gzip +
#: base64 14.4x. These are the conservative numbers, not the measured ones,
#: because a pre-check that refuses a page which would have fitted is a
#: worse failure than one that lets a page through to the real check.
PACKED_RATIO_FLOOR = 2
COMPRESSED_RATIO_FLOOR = 8

#: How many records each class is sampled for when estimating island size.
#: A stride sample, not a head sample -- see :func:`_stride_sample`.
_SAMPLE_SIZE = 64


class BlueprintTooLarge(Exception):
    """The island would exceed the size guard and `--force` was not given.

    Carries the estimate and the counts behind it so the caller can print
    the number rather than a category.
    """

    def __init__(self, estimate: "SizeEstimate") -> None:
        super().__init__(estimate.refusal_text())
        self.estimate = estimate


def _human_bytes(count: int) -> str:
    """A size a person reads, with the unit the number deserves."""
    if count < 1024:
        return f"{count} B"
    for unit, scale in (("KB", 1024), ("MB", 1024 ** 2), ("GB", 1024 ** 3)):
        if count < scale * 1024 or unit == "GB":
            value = count / scale
            return f"{value:.0f} {unit}" if value >= 10 else f"{value:.1f} {unit}"
    return f"{count} B"  # pragma: no cover - unreachable, GB is terminal


@dataclass(frozen=True)
class BlueprintView:
    """What the caller asked to see. Presentation only; no analysis."""

    scope: str = SCOPE_CASCADE
    max_nodes: int | None = None
    focus: str = ""
    hops: int = DEFAULT_HOPS
    force: bool = False
    size_limit_bytes: int = SIZE_GUARD_BYTES
    #: Round 6. The island is always interned; this adds gzip+base64 on
    #: top. Both are lossless -- see `_pack_data` -- so turning it off
    #: changes the file's size and nothing else about what the page shows.
    compress: bool = True

    @property
    def node_budget(self) -> int:
        """The node cap in force. 0 means uncapped."""
        if self.max_nodes is not None:
            return max(0, int(self.max_nodes))
        return DEFAULT_MAX_NODES if self.scope == SCOPE_CASCADE else 0


@dataclass(frozen=True)
class SizeEstimate:
    """A predicted island size, and the counts it was predicted from.

    Predicted by sampling: every count below is exact, and each one is
    multiplied by the mean serialized size of a deterministic sample of
    that record class from this very graph. No hard-coded bytes-per-record
    constant, so the estimate cannot drift when a record gains a field.
    """

    total_bytes: int
    limit_bytes: int
    parts: dict[str, int]
    counts: dict[str, int]
    scope: str
    forced: bool
    #: Whether the island this estimate describes will be gzipped.
    compressed: bool = True
    #: The island's **measured** size in the form the page will carry it.
    #: Zero until the page has actually been built.
    actual_bytes: int = 0
    #: True once `actual_bytes` is a measurement rather than a placeholder.
    measured: bool = False

    @property
    def guard_bytes(self) -> int:
        """The number the guard judges: what the browser actually carries."""
        return self.actual_bytes if self.measured else self.total_bytes

    @property
    def effective_limit(self) -> int:
        """The limit `guard_bytes` is compared against.

        Before the page is built the only number available is round 5's
        estimate of the island *with no compression at all*, and comparing
        that to the limit would refuse pages that in fact fit easily:
        measured on the owner's real engine, `--scope full` is 236,582,738
        bytes uncompressed, 79,435,487 interned, and 16,390,780 interned +
        gzip + base64 -- 14.4x. The factors below are deliberate floors
        well under the measured ratios (2x for interning alone, 8x for
        interning + gzip), so this pre-check can only ever stop a graph so
        large that *building* it would exhaust memory; the real refusal is
        made on `actual_bytes` once the island exists.
        """
        if self.limit_bytes <= 0:
            return 0
        if self.measured:
            return self.limit_bytes
        return self.limit_bytes * (
            COMPRESSED_RATIO_FLOOR if self.compressed else PACKED_RATIO_FLOOR
        )

    @property
    def over(self) -> bool:
        limit = self.effective_limit
        return limit > 0 and self.guard_bytes > limit

    def refusal_text(self) -> str:
        counts = self.counts
        shape = ", ".join(
            f"{counts[key]:,} {label}"
            for key, label in (
                ("elements", "elements"),
                ("call_edges", "call edges"),
                ("lineage_edges", "lineage edges"),
                ("decisions", "decisions"),
            )
            if counts.get(key)
        )
        if self.measured:
            head = (
                f"blueprint data island is {_human_bytes(self.actual_bytes)} "
                f"{'compressed' if self.compressed else 'interned'} "
                f"({_human_bytes(self.total_bytes)} before it was, {shape}), over the "
                f"{_human_bytes(self.limit_bytes)} limit.\n"
                "Nothing was written."
            )
        else:
            head = (
                f"blueprint would be ~{_human_bytes(self.total_bytes)} before "
                f"compression ({shape}), too large to build.\n"
                "Refusing to write it."
            )
        return (
            head + "\n"
            "Try:  --scope cascade       structure only: modules, stages, decisions, sinks\n"
            "      --focus <element-id>  that element and N hops around it\n"
            "      --max-nodes 2000      the most decision-relevant N\n"
            "      --size-limit-mb N     raise the limit\n"
            "      --force               write it anyway"
        )


@dataclass(frozen=True)
class Selection:
    """Exactly which ids each tab will hold, and what was left out.

    Built once, then used *both* by the size estimator and by the graph
    builders, so the number the guard refuses on and the number the page
    renders can never come from two different rules.
    """

    view: BlueprintView
    element_ids: tuple[str, ...]
    decision_ids: tuple[str, ...]
    lineage_ids: tuple[str, ...]
    detail_ids: tuple[str, ...]
    lineage_enabled: bool
    lineage_note: str
    focus_id: str
    focus_found: bool
    totals: dict[str, int]
    module_totals: dict[str, int]
    module_included: dict[str, int]
    notes: tuple[str, ...]

    @property
    def truncated(self) -> bool:
        return bool(self.totals.get("nodes_omitted", 0))

    def headline(self) -> str:
        """The one sentence the page and the terminal both print."""
        t = self.totals
        if not self.truncated:
            return (
                f"showing all {t['nodes_shown']:,} nodes of the {self.view.scope} scope "
                f"({t['elements_shown']:,} elements, {t['decisions_shown']:,} decisions)"
            )
        return (
            f"showing {t['nodes_shown']:,} of {t['nodes_available']:,} nodes, "
            f"ranked by decision relevance; {t['nodes_omitted']:,} not shown "
            f"({t['elements_shown']:,} of {t['elements_available']:,} elements, "
            f"{t['decisions_shown']:,} of {t['decisions_available']:,} decisions)"
        )


def _anchor_ids(store: ArtifactStore) -> tuple[frozenset[str], frozenset[str]]:
    """(sink element ids, entry element ids) -- declared, then detected.

    Read only from artifacts already loaded: `manifest.json`'s declared
    `sink_ids`/`entry_ids`, the sinks card 3's `Reachability` records
    already name, decisions card 3 already marked `is_sink`, and the
    children of card 3's own cascade root. Nothing is detected here.
    """
    elements = store.elements_by_id
    sinks = {i for i in (store.manifest.get("sink_ids") or ()) if i in elements}
    for record in store.raw["reachability"]:
        for sink_id in record.get("sink_ids") or ():
            if sink_id in elements:
                sinks.add(sink_id)
    for decision in store.decisions_by_id.values():
        owner = decision.get("element_id")
        if decision.get("is_sink") and owner in elements:
            sinks.add(owner)

    entries = {i for i in (store.manifest.get("entry_ids") or ()) if i in elements}
    cascade_node = store.order_by_id.get(CASCADE_ROOT_ID) or {}
    for child in cascade_node.get("children") or ():
        bare = child[len("@order::"):] if child.startswith("@order::") else child
        if bare in elements:
            entries.add(bare)
    return frozenset(sinks), frozenset(entries)


def _execution_neighbourhood(store: ArtifactStore, seed: str, hops: int) -> set[str]:
    """Ids within *hops* undirected call/ownership hops of *seed*.

    Walks `edges.jsonl` in both directions plus the decision-ownership
    link, so focusing on a function reaches its callers, its callees and
    its decision points alike. Deterministic: the frontier is sorted at
    every level, and the result is a set the caller sorts.
    """
    seen = {seed}
    frontier = [seed]
    for _ in range(max(0, hops)):
        nxt: set[str] = set()
        for node_id in sorted(frontier):
            for edge in store.edges_out.get(node_id, ()):
                target = edge.get("target_id")
                if isinstance(target, str) and target:
                    nxt.add(target)
            for edge in store.edges_in.get(node_id, ()):
                source = edge.get("source_id")
                if isinstance(source, str) and source:
                    nxt.add(source)
            for decision in store.decisions_by_element.get(node_id, ()):
                if "id" in decision:
                    nxt.add(decision["id"])
            decision = store.decisions_by_id.get(node_id)
            if decision and decision.get("element_id"):
                nxt.add(decision["element_id"])
        frontier = sorted(nxt - seen)
        seen.update(nxt)
        if not frontier:
            break
    return seen


def _lineage_neighbourhood(store: ArtifactStore, seed: str, hops: int) -> set[str]:
    """Ids within *hops* undirected lineage hops of *seed*.

    Uses `lineage_out`/`lineage_in`, the indices the loader already built
    -- never a second adjacency structure over 400,000 edges.
    """
    seen = {seed}
    frontier = [seed]
    for _ in range(max(0, hops)):
        nxt: set[str] = set()
        for node_id in sorted(frontier):
            for edge in store.lineage_out.get(node_id, ()):
                target = edge.get("target_id")
                if isinstance(target, str) and target:
                    nxt.add(target)
            for edge in store.lineage_in.get(node_id, ()):
                source = edge.get("source_id")
                if isinstance(source, str) and source:
                    nxt.add(source)
        frontier = sorted(nxt - seen)
        seen.update(nxt)
        if not frontier:
            break
    return seen


#: Relevance tiers, most decision-relevant first. The brief's own order:
#: elements on a path to a sink first, then decisions, then the rest. A
#: MODULE shares tier 0 with the sinks and entry points because it is the
#: cascade scope's skeleton -- there is at most one per source file, and a
#: module card that vanished would take its whole drill-down with it.
_TIER_ANCHOR = 0
_TIER_ON_PATH = 1
_TIER_DECISION_RELEVANT = 2
_TIER_UNKNOWN = 3
_TIER_REST = 4
_TIER_DECISION_REST = 5


def _rank_nodes(
    store: ArtifactStore,
    element_ids: list[str],
    decision_ids: list[str],
    sinks: frozenset[str],
    entries: frozenset[str],
) -> list[tuple[tuple[int, int, str], str, bool]]:
    """Every candidate node as (sort key, id, is_decision), most relevant first.

    The state read for each element is card 3's `Reachability` record, via
    the loader's index -- the canonical carrier. Nothing is re-derived.
    """
    elements = store.elements_by_id
    ranked: list[tuple[tuple[int, int, str], str, bool]] = []
    element_tier: dict[str, int] = {}
    for element_id in element_ids:
        kind = (elements.get(element_id) or {}).get("kind")
        state = (store.reachability_by_element.get(element_id) or {}).get("state") or "UNKNOWN"
        if element_id in sinks or element_id in entries or kind == "MODULE":
            tier = _TIER_ANCHOR
        elif state == "REACHES_SINK":
            tier = _TIER_ON_PATH
        elif state == "UNKNOWN":
            tier = _TIER_UNKNOWN
        else:
            tier = _TIER_REST
        element_tier[element_id] = tier
        sub = 0 if element_id in sinks else 1 if element_id in entries else 2
        ranked.append(((tier, sub, element_id), element_id, False))

    for decision_id in decision_ids:
        decision = store.decisions_by_id.get(decision_id) or {}
        owner = decision.get("element_id") or ""
        owner_tier = element_tier.get(owner, _TIER_REST)
        tier = _TIER_DECISION_RELEVANT if owner_tier <= _TIER_ON_PATH else _TIER_DECISION_REST
        sub = 0 if decision.get("is_sink") else 1
        ranked.append(((tier, sub, decision_id), decision_id, True))

    ranked.sort(key=lambda item: item[0])
    return ranked


def select(store: ArtifactStore, view: BlueprintView) -> Selection:
    """Which ids this view holds, and an honest account of what it drops.

    The one place scope, focus and the node budget are applied. Both the
    size estimator and the graph builders consume its output, so the size
    the guard refuses on is the size of the page that would be written.
    """
    elements = store.elements_by_id
    all_execution = sorted(
        eid for eid, element in elements.items() if element.get("kind") in EXECUTION_KINDS
    )
    execution_set = set(all_execution)
    all_decisions = sorted(store.decisions_by_id)
    sinks, entries = _anchor_ids(store)
    notes: list[str] = []

    focus_id = view.focus or ""
    focus_found = bool(focus_id) and (
        focus_id in elements or focus_id in store.decisions_by_id
        or bool(store.lineage_out.get(focus_id)) or bool(store.lineage_in.get(focus_id))
    )
    if focus_id and not focus_found:
        notes.append(
            f"--focus {focus_id} names no element, decision or lineage node in this graph; "
            "the focused view is empty rather than silently showing everything"
        )

    # -- which elements and decisions are candidates at all ---------------
    if focus_id:
        near = _execution_neighbourhood(store, focus_id, view.hops) if focus_found else set()
        if focus_found:
            near.add(focus_id)
        # A focused page shows the neighbourhood whatever KIND it is made
        # of -- an owner focusing on the assignment that holds the final
        # decision must not be handed a blank page because the element is
        # not a FUNCTION. The scope's kind filter is a default, not a
        # property of `--focus`.
        candidate_elements = sorted(near & set(elements))
        candidate_decisions = sorted(near & set(all_decisions))
        notes.append(
            f"focused on {focus_id} within {view.hops} call hop(s): "
            f"{len(candidate_elements)} element(s) and {len(candidate_decisions)} decision(s). "
            "Everything outside that neighbourhood is not in this page; raise `--hops N`, "
            "or drop `--focus` for the whole cascade scope"
        )
        if focus_found and len(candidate_elements) <= 1 and not candidate_decisions:
            notes.append(
                f"{focus_id} has no call-graph neighbours within {view.hops} hop(s). If it is "
                "a value rather than a function, its neighbours are lineage edges -- the "
                "Lineage tab of this same page is focused on it and shows them"
            )
    elif view.scope == SCOPE_FULL:
        candidate_elements = list(all_execution)
        candidate_decisions = list(all_decisions)
    else:
        picked = {eid for eid in all_execution if (elements[eid] or {}).get("kind") == "MODULE"}
        picked |= (sinks | entries) & execution_set
        for decision in store.decisions_by_id.values():
            owner = decision.get("element_id")
            if owner in execution_set:
                picked.add(owner)
        for record in store.raw["reachability"]:
            for path_id in record.get("path_ids") or ():
                if path_id in execution_set:
                    picked.add(path_id)
        candidate_elements = sorted(picked)
        candidate_decisions = list(all_decisions)
        notes.append(
            "scope `cascade`: modules and stages as cards, decision points, declared and "
            "detected sinks, entry points, and elements card 3 placed on a path to a sink. "
            "Assignments, parameters, imports, config keys and lineage edges are NOT in this "
            "page -- re-run with `--scope full` for those"
        )

    # -- the budget -------------------------------------------------------
    # Decision points get a guaranteed share of the budget. Ranking them
    # strictly after every on-path element looked right and was not: on the
    # owner's engine 1,642 elements are on a path to a sink, so at
    # --max-nodes 1000 the page held ZERO decision points -- in a scope
    # whose definition names decision points first. Whichever side does not
    # use its share gives it to the other, so nothing is wasted.
    ranked = _rank_nodes(store, candidate_elements, candidate_decisions, sinks, entries)
    budget = view.node_budget
    ranked_elements = [(key, nid) for key, nid, is_d in ranked if not is_d]
    ranked_decisions = [(key, nid) for key, nid, is_d in ranked if is_d]
    decision_quota = int(budget * DECISION_BUDGET_SHARE) if budget else len(ranked_decisions)
    element_quota = (budget - decision_quota) if budget else len(ranked_elements)

    kept_elements: list[str] = []
    kept_decisions: list[str] = []
    kept: set[str] = set()
    for _key, node_id in ranked_elements[:element_quota]:
        kept_elements.append(node_id)
        kept.add(node_id)
    for _key, node_id in ranked_decisions:
        if len(kept_decisions) >= decision_quota:
            break
        owner = (store.decisions_by_id.get(node_id) or {}).get("element_id") or ""
        # A decision diamond with no owner card on the page is an orphan,
        # not a fact -- it is skipped, never drawn dangling.
        if owner and owner not in kept:
            continue
        kept_decisions.append(node_id)
        kept.add(node_id)
    # Whatever neither side used, in one deterministic pass over both
    # ranked lists merged by their own keys.
    if budget and len(kept) < budget:
        leftovers = sorted(
            [(key, nid, False) for key, nid in ranked_elements if nid not in kept]
            + [(key, nid, True) for key, nid in ranked_decisions if nid not in kept]
        )
        for _key, node_id, is_decision in leftovers:
            if len(kept) >= budget:
                break
            if is_decision:
                owner = (store.decisions_by_id.get(node_id) or {}).get("element_id") or ""
                if owner and owner not in kept:
                    continue
                kept_decisions.append(node_id)
            else:
                kept_elements.append(node_id)
            kept.add(node_id)

    nodes_available = len(candidate_elements) + len(candidate_decisions)
    nodes_shown = len(kept_elements) + len(kept_decisions)
    if nodes_shown < nodes_available:
        notes.append(
            f"showing {nodes_shown:,} of {nodes_available:,} nodes, ranked by decision "
            f"relevance (sinks and entry points first, then elements on a path to a sink, "
            f"then decision points, then the rest); {nodes_available - nodes_shown:,} not "
            "shown. Raise or remove the cap with `--max-nodes N` (`--max-nodes 0` = no cap)"
        )

    # -- the lineage tab --------------------------------------------------
    lineage_total = len(store.raw["lineage"])
    if focus_id and focus_found:
        lineage_ids = sorted(_lineage_neighbourhood(store, focus_id, view.hops))
        lineage_enabled = True
        lineage_note = (
            f"lineage within {view.hops} hop(s) of {focus_id}: {len(lineage_ids):,} nodes "
            f"of this graph's {lineage_total:,} lineage edges"
        )
    elif focus_id:
        lineage_ids = []
        lineage_enabled = True
        lineage_note = f"--focus {focus_id} matches nothing in this graph"
    elif view.scope == SCOPE_FULL:
        lineage_ids = []            # every id; the builder takes the whole file
        lineage_enabled = True
        lineage_note = f"scope `full`: all {lineage_total:,} lineage edges"
    else:
        lineage_ids = []
        lineage_enabled = False
        lineage_note = (
            f"This graph holds {lineage_total:,} lineage edges. Drawing them all is what "
            "made this page unopenable, so lineage is focus-driven: pick an element on the "
            "Execution tab and use its 'Show lineage' link, or re-render with "
            "`--focus <element-id> [--hops N]`. `--scope full` draws every edge (and will "
            "be refused unless it fits, or you pass --force)."
        )

    # -- what gets a detail record ---------------------------------------
    detail_ids = sorted(
        {node_id for node_id in kept_elements if node_id in elements}
        | ({focus_id} if focus_found and focus_id in elements else set())
        | ({i for i in lineage_ids if i in elements} if lineage_enabled and lineage_ids else set())
    )

    module_totals: dict[str, int] = {}
    for element_id in all_execution:
        module = (elements.get(element_id) or {}).get("module") or ""
        module_totals[module] = module_totals.get(module, 0) + 1
    module_included: dict[str, int] = {module: 0 for module in module_totals}
    for element_id in kept_elements:
        module = (elements.get(element_id) or {}).get("module") or ""
        module_included[module] = module_included.get(module, 0) + 1

    totals = {
        "elements_in_graph": len(elements),
        "elements_available": len(candidate_elements),
        "elements_shown": len(kept_elements),
        "decisions_available": len(candidate_decisions),
        "decisions_shown": len(kept_decisions),
        "nodes_available": nodes_available,
        "nodes_shown": nodes_shown,
        "nodes_omitted": nodes_available - nodes_shown,
        "lineage_edges_in_graph": lineage_total,
        "call_edges_in_graph": len(store.raw["edges"]),
        "decisions_in_graph": len(all_decisions),
        "execution_elements_in_graph": len(all_execution),
        "node_budget": budget,
    }
    return Selection(
        view=view,
        element_ids=tuple(sorted(kept_elements)),
        decision_ids=tuple(sorted(kept_decisions)),
        lineage_ids=tuple(lineage_ids),
        detail_ids=tuple(detail_ids),
        lineage_enabled=lineage_enabled,
        lineage_note=lineage_note,
        focus_id=focus_id,
        focus_found=focus_found,
        totals=totals,
        module_totals=dict(sorted(module_totals.items())),
        module_included=dict(sorted(module_included.items())),
        notes=tuple(notes),
    )


def _json_bytes(obj: Any) -> int:
    """Serialized size of *obj*, in the island's own encoding."""
    return len(json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":")))


def _stride_sample(items: "list[str] | tuple[str, ...]", size: int) -> list[str]:
    """Up to *size* entries spread evenly across *items*, deterministically.

    A stride, not the first N: on a real engine the first few ids by sort
    order are not representative of the rest, and taking the head made the
    round-5 estimator wrong by 2.7x on the owner's own graph. No `random`,
    no clock, no set iteration -- two runs sample the same entries.
    """
    count = len(items)
    if count == 0 or size <= 0:
        return []
    if count <= size:
        return list(items)
    step = count / size
    return [items[min(count - 1, int(index * step))] for index in range(size)]


def _mean_bytes(objects: list[Any]) -> float:
    if not objects:
        return 0.0
    return sum(_json_bytes(obj) for obj in objects) / len(objects)


def estimate_island_bytes(
    store: ArtifactStore,
    selection: Selection,
    diff_store: ArtifactStore | None = None,
    rstore: RuntimeStore | None = None,
) -> SizeEstimate:
    """How large the data island would be, before any of it is built.

    Every count below is exact. Every unit cost is measured by building a
    stride sample of the very records the page would carry -- the real
    :func:`_element_node`, :func:`_decision_node`, :func:`_edge_wire` and
    :func:`_capped_detail`, not a model of them. Round 5's first estimator
    modelled the parts instead and missed `order_ancestor_ids` entirely,
    predicting 38 MB for an island that came out at 104 MB. An estimator
    that can be wrong in that direction is worse than none: it is the
    guard's whole job to be right about the number it refuses on.
    """
    view = selection.view
    totals = selection.totals

    # -- execution tab ----------------------------------------------------
    element_sample = _stride_sample(selection.element_ids, _SAMPLE_SIZE)
    decision_sample = _stride_sample(selection.decision_ids, _SAMPLE_SIZE)
    node_cost = _mean_bytes([_element_node(store, i) for i in element_sample]) + 120
    decision_cost = _mean_bytes(
        [_decision_node(store, store.decisions_by_id[i]) for i in decision_sample]
    ) + 120
    edge_cost = _mean_bytes([_edge_wire(e) for e in store.raw["edges"][:_SAMPLE_SIZE]]) + 4

    kept = set(selection.element_ids) | set(selection.decision_ids)
    if totals["nodes_omitted"]:
        execution_edges = sum(
            1 for edge in store.raw["edges"]
            if edge.get("source_id") in kept and edge.get("target_id") in kept
        )
    else:
        execution_edges = totals["call_edges_in_graph"]
    decision_outcomes = sum(
        1 + len(store.decisions_by_id[i].get("outcomes") or ())
        for i in selection.decision_ids
    )

    parts = {
        "execution_nodes": int(
            len(selection.element_ids) * node_cost + len(selection.decision_ids) * decision_cost
        ),
        "execution_wires": int((execution_edges + decision_outcomes) * edge_cost),
    }

    # -- lineage tab ------------------------------------------------------
    lineage_wire_cost = _mean_bytes([
        {"id": e.get("id"), "kind": e.get("kind"), "source_id": e.get("source_id"),
         "target_id": e.get("target_id"), "confidence": None, "method": None,
         "span": e.get("span"), "outcome_label": ""}
        for e in store.raw["lineage"][:_SAMPLE_SIZE]
    ]) + 4
    if not selection.lineage_enabled:
        parts["lineage"] = 0
    elif selection.focus_id:
        focus_ids = list(selection.lineage_ids)
        focus_edges = sum(len(store.lineage_out.get(i, ())) for i in focus_ids)
        lineage_node_cost = _mean_bytes(
            [_lineage_node(store, i) for i in _stride_sample(focus_ids, _SAMPLE_SIZE)]
        ) + 120
        parts["lineage"] = int(len(focus_ids) * lineage_node_cost + focus_edges * lineage_wire_cost)
    else:
        endpoints: set[str] = set()
        for edge in store.raw["lineage"]:
            endpoints.add(edge.get("source_id") or "")
            endpoints.add(edge.get("target_id") or "")
        endpoint_list = sorted(endpoints)
        lineage_node_cost = _mean_bytes(
            [_lineage_node(store, i) for i in _stride_sample(endpoint_list, _SAMPLE_SIZE)]
        ) + 120
        parts["lineage"] = int(
            len(endpoint_list) * lineage_node_cost
            + totals["lineage_edges_in_graph"] * lineage_wire_cost
            + len(store.raw["barriers"]) * (lineage_node_cost + lineage_wire_cost)
        )

    # -- element details: round 4's real bulk ------------------------------
    detail_ids = selection.detail_ids
    detail_cost = _mean_bytes([
        _capped_detail(store, i, DETAIL_LIST_CAP)
        for i in _stride_sample(detail_ids, _SAMPLE_SIZE)
    ]) + 120
    parts["element_details"] = int(len(detail_ids) * detail_cost)

    # -- diff and runtime -------------------------------------------------
    if diff_store is not None:
        parts["diff"] = int(
            _mean_bytes(diff_store.raw["changes"][:_SAMPLE_SIZE]) * len(diff_store.raw["changes"])
            + _mean_bytes(diff_store.raw["impacts"][:_SAMPLE_SIZE]) * len(diff_store.raw["impacts"])
            + len(selection.element_ids) * node_cost
        )
    else:
        parts["diff"] = 0
    if rstore is not None:
        events = rstore.raw.get("events", [])
        narrative = rstore.raw.get("narrative", [])
        parts["runtime"] = int(
            _mean_bytes(events[:_SAMPLE_SIZE]) * len(events)
            + _mean_bytes(narrative[:_SAMPLE_SIZE]) * len(narrative)
        )
    else:
        parts["runtime"] = 0

    total = sum(parts.values()) + 4096
    counts = {
        "elements": totals["elements_in_graph"],
        "call_edges": totals["call_edges_in_graph"],
        "lineage_edges": totals["lineage_edges_in_graph"],
        "decisions": totals["decisions_in_graph"],
    }
    return SizeEstimate(
        total_bytes=total,
        limit_bytes=0 if view.force else view.size_limit_bytes,
        parts=dict(sorted(parts.items())),
        counts=counts,
        scope=view.scope,
        forced=view.force,
        compressed=view.compress,
    )


def _build_execution(store: ArtifactStore, selection: Selection) -> dict[str, Any]:
    all_execution_ids = sorted(
        eid for eid, element in store.elements_by_id.items()
        if element.get("kind") in EXECUTION_KINDS
    )
    element_ids = list(selection.element_ids)
    element_id_set = set(element_ids)
    sink_ids, entry_ids = _anchor_ids(store)

    element_nodes = []
    for element_id in element_ids:
        node = _element_node(store, element_id)
        node["is_sink"] = element_id in sink_ids
        node["is_entry"] = element_id in entry_ids
        # An element can sit in thousands of order nodes on a real engine;
        # the tail is not silently cut, it states its own length.
        # Only the count here: the ids themselves are in this element's
        # detail record, which is the one place the page reads them from.
        node["order_node_total"] = len(store.order_containing_element.get(element_id, []))
        element_nodes.append(node)

    decision_ids = list(selection.decision_ids)
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

    sorted_wires = sorted(wires, key=lambda wire: wire["id"] or "")
    # Stage structure comes from the WHOLE execution element set, then is
    # restricted to what this page carries -- see :func:`_scope_stages`.
    # `--focus` can keep elements outside the execution kinds; every kept
    # node must land in some stage card or stage mode would simply not
    # draw it, which is an omission with no notice attached.
    stages, stage_of = _build_stages(store, sorted(set(all_execution_ids) | element_id_set))
    _scope_stages(stages, element_id_set)
    flow_totals, stage_pairs = _classify_and_summarize_flow(sorted_wires, stage_of)

    return {
        "nodes": nodes,
        "wires": sorted_wires,
        "omitted_wire_count": omitted,
        "omitted_note": (
            "a wire is not drawn when an endpoint is outside "
            "MODULE/CLASS/FUNCTION/METHOD/DECISION, or when a decision outcome names "
            "an order-tree node this canvas does not resolve to a single element -- "
            "the raw target is still visible in that node's or decision's own detail panel"
        ),
        "scope_note": selection.headline(),
        "stages": stages,
        "stage_of": stage_of,
        "flow_totals": flow_totals,
        "stage_pairs": stage_pairs,
    }


# ---------------------------------------------------------------------------
# LINEAGE tab
# ---------------------------------------------------------------------------


def _build_lineage(store: ArtifactStore, selection: Selection) -> dict[str, Any]:
    """The lineage canvas, focus-driven by default.

    Round 5: this tab was the worst offender -- on the owner's engine it
    tried to draw 390,000 edges, which is most of the 702 MB the page used
    to weigh. It now draws nothing at all unless the caller asked for a
    neighbourhood (`--focus`) or for everything (`--scope full`), and the
    "nothing" is a readable instruction, not a blank canvas.
    """
    lineage_edges = store.raw["lineage"]
    if not selection.lineage_enabled:
        return {
            "nodes": [], "wires": [], "focus_driven": True, "focus_id": "",
            "reason": selection.lineage_note,
            "edges_in_graph": len(lineage_edges),
        }

    focused = bool(selection.focus_id)
    ids: set[str] = set(selection.lineage_ids) if focused else set()
    if not focused:
        for edge in lineage_edges:
            source_id, target_id = edge.get("source_id"), edge.get("target_id")
            if source_id:
                ids.add(source_id)
            if target_id:
                ids.add(target_id)
        for element_id, element in store.elements_by_id.items():
            if element.get("kind") == "FEATURE":
                ids.add(element_id)
    barriers = [
        b for b in sorted(store.raw["barriers"], key=lambda b: b.get("id") or "")
        if not focused or b.get("element_id") in ids
    ]
    if not focused:
        for barrier in barriers:
            if barrier.get("element_id"):
                ids.add(barrier["element_id"])

    node_ids = sorted(ids)
    nodes_by_id: dict[str, dict[str, Any]] = {
        node_id: _lineage_node(store, node_id) for node_id in node_ids
    }

    # A focused page must never pay for a pass over every lineage edge in
    # the graph: `lineage_out` is the index the loader already built.
    if focused:
        relevant: list[dict[str, Any]] = []
        seen_edge_ids: set[str] = set()
        for node_id in node_ids:
            for edge in store.lineage_out.get(node_id, ()):
                edge_id = edge.get("id") or ""
                if edge_id in seen_edge_ids:
                    continue
                seen_edge_ids.add(edge_id)
                relevant.append(edge)
    else:
        relevant = lineage_edges

    edge_pairs = sorted({
        (edge.get("source_id"), edge.get("target_id"))
        for edge in relevant
        if edge.get("source_id") in ids and edge.get("target_id") in ids
    })
    layers = _longest_path_layers(node_ids, edge_pairs, {})

    wires: list[dict[str, Any]] = []
    for edge in relevant:
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

    return {
        "nodes": nodes,
        "wires": sorted(wires, key=lambda wire: wire["id"] or ""),
        "focus_driven": focused,
        "focus_id": selection.focus_id,
        "reason": selection.lineage_note,
        "edges_in_graph": len(lineage_edges),
    }


# ---------------------------------------------------------------------------
# DIFF tab
# ---------------------------------------------------------------------------


def _build_diff(
    store: ArtifactStore, diff_store: ArtifactStore | None, selection: Selection
) -> dict[str, Any]:
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

    # Round 5: the Diff tab draws this page's scoped element set, PLUS every
    # element the diff actually touches, whatever the scope -- a change that
    # fell outside the scope would otherwise be a change the owner cannot
    # see, which is the one thing this tab exists to prevent.
    execution_ids = set(selection.element_ids)
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


#: Detail fields that are unbounded on a real engine. Each is capped at
#: :data:`DETAIL_LIST_CAP` and gains a `<field>_total` sibling, so the panel
#: can say "4 of 3,182 shown" rather than quietly presenting 4 as all of
#: them. On the owner's engine these lists alone were most of the 702 MB:
#: `lineage_in`/`lineage_out` embed a whole lineage edge record per entry,
#: which is two further copies of lineage.jsonl spread across the details.
_CAPPED_DETAIL_FIELDS = (
    "outgoing_edges", "incoming_edges", "unresolved_as_candidate", "order_node_ids",
    "decision_as_condition", "decision_reads_this", "lineage_out", "lineage_in",
    "barrier_ids", "slice_ids_as_member", "finding_ids_as_evidence",
)

#: Detail fields that hold whole EDGE records where the panel only ever
#: renders the id at the other end. The edge's own provenance is not lost:
#: it rides on the drawn wire (execution tab) or on the focused lineage
#: edge, both of which are clickable and show method and confidence. Carrying
#: it a second time inside every element's detail cost 6.2 MB on the owner's
#: engine and put nothing new on the screen.
_EDGE_PROJECTIONS = {
    "incoming_edges": "source_id",
    "outgoing_edges": "target_id",
    "lineage_in": "source_id",
    "lineage_out": "target_id",
}


def _capped_detail(store: ArtifactStore, element_id: str, cap: int) -> dict[str, Any]:
    """`views.element_detail`, with its unbounded lists capped and counted.

    No fact is altered: every entry kept is the entry :mod:`.views`
    produced, in the order it produced it, and every entry dropped is
    accounted for by an exact total the panel prints. Nothing is silently
    dropped -- the element's own artifacts remain the whole answer.
    """
    detail = views.element_detail(store, element_id)
    if cap <= 0:
        return detail
    detail["reachability"] = _capped_reachability(detail.get("reachability") or {})
    for field_name in _CAPPED_DETAIL_FIELDS:
        value = detail.get(field_name)
        if not isinstance(value, list):
            continue
        detail[f"{field_name}_total"] = len(value)
        if len(value) > cap:
            detail[field_name] = value[:cap]
    # Order membership is capped harder than everything else, and its
    # ancestor chains harder still: measured at 66.7 MB of a 103.7 MB
    # island on the owner's engine, more than every other field combined.
    order_all = detail.get("order_node_ids") or []
    detail["order_node_ids"] = order_all[:ORDER_NODE_CAP]
    ancestors = detail.get("order_ancestor_ids")
    if isinstance(ancestors, dict):
        # The blueprint renders the order node ids themselves, never their
        # ancestor chains, and each chain repeats its own key. Nothing is
        # lost: the tabular report shows the chain, and order.jsonl holds
        # every parent link. The pointer says so rather than leaving a
        # field that reads as "this element has no ancestors".
        detail["order_ancestor_ids"] = {
            "carried_by": "order.jsonl and the tabular report",
            "nodes": len(ancestors),
        }
    detail["order_node_cap"] = ORDER_NODE_CAP
    detail["detail_cap"] = cap
    # The same fact stored twice is not information, it is weight. Every id
    # with a detail record also has a node on a canvas, and that node
    # already carries the canonical `Reachability` verbatim (see
    # `_element_node`/`_lineage_node`), which the detail panel is what
    # actually reads. Measured on the owner's engine: 1.43 MB of pure
    # duplication. The pointer stays, so a reader of the island can never
    # mistake "carried elsewhere" for "not known".
    detail["reachability"] = {"carried_by": "node"}
    detail["element"] = {"carried_by": "node"}
    # Edge lists become id lists; the edges themselves stay one click away
    # on the canvas, with their method and confidence intact.
    for field_name, id_field in _EDGE_PROJECTIONS.items():
        value = detail.get(field_name)
        if not isinstance(value, list):
            continue
        detail[field_name] = [
            {"id": edge.get("id"), "kind": edge.get("kind"), id_field: edge.get(id_field)}
            for edge in value
        ]
    detail["edges_projected"] = True   # the note itself lives once, in DATA.view
    return detail


#: Unresolved reasons that can mean a source file was not read. Which
#: *analysis* did not read it, and therefore what is actually missing from
#: the map, is a separate question answered per record below.
#: MISSING_TARGET is deliberately NOT here: card 2 emits it per unresolved
#: call site, tens of thousands of times on a real engine, and it says
#: nothing about whether the file was read.
_FILE_LEVEL_UNRESOLVED = ("SYNTAX_ERROR", "DECODE_ERROR", "TOO_LARGE")

#: Which analysis emitted a file-level `Unresolved`, by the prefix each card
#: puts on its ids. Card 17 (dependencies) namespaces every id `dep::...`;
#: card 1 (inventory) uses the module's dotted name, which has no `::`.
#: A prefix this table does not know yields "" and the record's own words
#: are shown without a name attached -- never a guessed one.
_UNRESOLVED_SOURCE = {
    "dep": "the dependency scanner (card 17)",
}


def _analysis_for(unresolved_id: str) -> str:
    """The named analysis that emitted this record, or "" if unknown."""
    if "::" not in unresolved_id:
        return ""
    return _UNRESOLVED_SOURCE.get(unresolved_id.split("::", 1)[0], "")


def _elements_per_path(store: ArtifactStore) -> dict[str, int]:
    """How many inventory elements each source path contributed.

    A count read straight out of `elements.jsonl`. It is the fact that
    decides whether "this file was not read" is true of the *map* or only
    of one analysis, and it is why round 6 stopped saying the former when
    only the latter happened.
    """
    counts: dict[str, int] = {}
    for element in store.raw["elements"]:
        span = element.get("span") or {}
        path = span.get("path")
        if isinstance(path, str) and path:
            counts[path] = counts.get(path, 0) + 1
    return counts


def _unparsed_files(store: ArtifactStore) -> list[dict[str, Any]]:
    """Every source file some analysis could not read, from `unresolved.jsonl`.

    Read verbatim: the reason, the path, the line and the emitting card's
    own description. Nothing is judged or re-derived here.

    Round 6 adds the one fact that makes the difference between a true
    statement and a false one: **how many elements of this map came from
    that file**. Card 17 skipped the owner's 14.8 MB engine for dependency
    analysis only, and the page turned that into "1 source file was NOT
    read -- this map does not describe it", in red, at the top of a map
    built from 10,164 elements of that very file. The record was right;
    the sentence built from it was not. Overstating a limitation costs
    trust exactly as fast as hiding one, so the page now states what was
    covered and what was not, per file, and names the analysis that
    skipped it.
    """
    per_path = _elements_per_path(store)
    rows: dict[str, dict[str, Any]] = {}
    for record in store.raw["unresolved"]:
        reason = record.get("reason")
        if reason not in _FILE_LEVEL_UNRESOLVED:
            continue
        span = record.get("span") or {}
        path = span.get("path") or ""
        if not isinstance(path, str) or not path.endswith(".py"):
            continue
        key = f"{reason}::{path}::{span.get('line')}"
        if key in rows:
            continue
        element_count = per_path.get(path, 0)
        rows[key] = {
            "reason": reason,
            "path": path,
            "line": span.get("line"),
            "description": record.get("description", ""),
            "id": record.get("id", ""),
            "analysis": _analysis_for(str(record.get("id", ""))),
            "elements_from_file": element_count,
            # True when this map holds no element at all from that path --
            # the only case in which "this map does not describe it" is a
            # true sentence.
            "absent_from_map": element_count == 0,
        }
    return [rows[key] for key in sorted(rows)]


def build_blueprint_data(
    store: ArtifactStore,
    rstore: RuntimeStore | None = None,
    diff_store: ArtifactStore | None = None,
    *,
    report_link: str = "",
    view: BlueprintView | None = None,
    selection: Selection | None = None,
) -> dict[str, Any]:
    """Everything the page needs, as one JSON-serializable, sorted structure.

    Every field here traces to an artifact record read by :mod:`.loader` or
    a view already defined in :mod:`.views` -- nothing is derived here that
    is not either a direct field copy or the presentation-only layer number
    computed above.

    *view* / *selection* bound what goes in (round 5). Omitting both keeps
    the round-4 shape for callers that already knew this graph was small
    enough -- the default `BlueprintView` is `--scope cascade`, so the
    bound is on by default everywhere it matters.
    """
    manifest = store.manifest or {}
    confidence_rank = {level.value: index for index, level in enumerate(Confidence)}
    if selection is None:
        selection = select(store, view if view is not None else BlueprintView())

    execution = _build_execution(store, selection)
    lineage = _build_lineage(store, selection)
    diff = _build_diff(store, diff_store, selection)

    element_ids: set[str] = set()
    for graph in (execution, lineage, diff):
        for node in graph["nodes"]:
            if node.get("is_element"):
                element_ids.add(node["id"])
    element_details = {
        element_id: _capped_detail(store, element_id, DETAIL_LIST_CAP)
        for element_id in sorted(element_ids)
    }

    return {
        "view": {
            "scope": selection.view.scope,
            "max_nodes": selection.view.node_budget,
            "focus": selection.focus_id,
            "focus_found": selection.focus_found,
            "hops": selection.view.hops,
            "forced": selection.view.force,
            "truncated": selection.truncated,
            "headline": selection.headline(),
            "notes": list(selection.notes),
            "totals": dict(sorted(selection.totals.items())),
            "module_totals": selection.module_totals,
            "module_included": selection.module_included,
            "detail_cap": DETAIL_LIST_CAP,
            "order_node_cap": ORDER_NODE_CAP,
            "order_ancestor_cap": ORDER_ANCESTOR_CAP,
            "path_ids_cap": PATH_IDS_CAP,
            "edges_projected_note": (
                "in the detail panel, caller/callee and lineage lists carry the far "
                "end's id only. The edge record, with its method and confidence, is on "
                "the wire itself -- click it -- and in edges.jsonl / lineage.jsonl."
            ),
        },
        "schema_version": manifest.get("schema_version", ""),
        "tool_version": manifest.get("tool_version", ""),
        "generator": "cascade-map blueprint",
        "report_link": report_link,
        "module_collapse_threshold": MODULE_COLLAPSE_THRESHOLD,
        "confidence_rank": confidence_rank,
        "legend": LEGEND,
        "available": dict(sorted(store.available.items())),
        "diagnostics": {
            # A source file the parser could not read at all is the loudest
            # fact in the whole graph and had nowhere to be seen: card 1
            # emits it as an `Unresolved` with reason SYNTAX_ERROR /
            # DECODE_ERROR and no `candidate_ids`, so no element's panel
            # could ever show it. A map missing a whole file must say so on
            # the page, not only in unresolved.jsonl -- an owner on Python
            # 3.11 analysing a 3.12-only target otherwise gets a small,
            # clean, WRONG map that reads as success.
            "unparsed_files": _unparsed_files(store),
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
        # Card 13. Every intent on the page by id, so a verdict -- static or
        # runtime -- can show the SENTENCE it was judged against without the
        # reader going to look it up. Only the intents that name an element on
        # this page: the file is the whole answer and is always written.
        "intents_by_id": {
            intent_id: store.intents_by_id[intent_id]
            for element_id in sorted(element_ids)
            for intent in store.intents_by_element.get(element_id, [])
            for intent_id in (intent.get("id"),)
            if intent_id in store.intents_by_id
        },
        "element_details": element_details,
    }


# ---------------------------------------------------------------------------
# Presentation: CSS and JS, inlined. No CDN, no external fonts, no network.
# ---------------------------------------------------------------------------

_STYLE = """<style>
/* round 4, R3: four named palettes, picked by `data-palette` on <html>.
   Every rule elsewhere in this stylesheet reads only these custom
   properties -- never a literal colour -- so a palette changes hues and
   nothing else: confidence keeps its dash patterns and relative stroke
   widths, reachability keeps its badges and border styles, diff keeps its
   marks, in every palette. --amber/--red/--green/--purple are the
   *semantic* colours (confidence, reachability, diff, findings) and are
   deliberately kept close to the same hue family across palettes, so a
   colour never means something different in one palette than another;
   --accent/--sink/--bg/--panel/--node-* are the *identity* colours that
   actually change per palette.
*/
:root[data-palette="blueprint-dark"] {
  --bg:#0a0c11; --grid-minor:#171d28; --grid-major:#232b3a; --panel:#12161f; --panel-border:#262e3b;
  --text:#e3e8f0; --text-dim:#8b95a5; --accent:#5aa9ff; --accent-dark:#2e6fc2; --sink:#ffb648;
  --node-bg:#1c2330; --node-bg-2:#161c28; --node-border:#3a4763; --node-header:#26314a;
  --amber:#e0a326; --red:#e5484d; --green:#3fce7c; --purple:#a970ff;
}
:root[data-palette="ai-blue"] {
  --bg:#050b16; --grid-minor:#0d1b2e; --grid-major:#173a5c; --panel:#081729; --panel-border:#1c4468;
  --text:#e5f6ff; --text-dim:#86b3d1; --accent:#2dd4ff; --accent-dark:#0f7fa8; --sink:#ffb648;
  --node-bg:#0c2138; --node-bg-2:#081729; --node-border:#20517a; --node-header:#123a5c;
  --amber:#e6ab2e; --red:#ff5470; --green:#3fce7c; --purple:#a970ff;
}
:root[data-palette="silver-milk"] {
  /* D12: the first pass read as cream/tan, not silver and milk white --
     every one of these was a *warm* neutral (e.g. bg #f7f5f0 has more red
     than blue). Every neutral below is now cool (blue channel >= green
     channel >= red channel), so the surface reads as milk white and the
     structure as silver/graphite, with exactly one saturated accent. */
  --bg:#f6f8fa; --grid-minor:#e5e9ee; --grid-major:#ccd3dc; --panel:#ffffff; --panel-border:#ccd3dc;
  --text:#20242b; --text-dim:#5c6472; --accent:#0063d1; --accent-dark:#00468f; --sink:#8a5708;
  --node-bg:#ffffff; --node-bg-2:#eef1f5; --node-border:#b7bfc9; --node-header:#e4e8ee;
  --amber:#7a5405; --red:#c2262b; --green:#187a44; --purple:#6b3fc9;
}
:root[data-palette="high-contrast"] {
  --bg:#000000; --grid-minor:#1c1c1c; --grid-major:#3a3a3a; --panel:#000000; --panel-border:#ffffff;
  --text:#ffffff; --text-dim:#d8d8d8; --accent:#20e0ff; --accent-dark:#0aa8c9; --sink:#ff9d00;
  --node-bg:#000000; --node-bg-2:#161616; --node-border:#ffffff; --node-header:#1c1c1c;
  --amber:#ffcf33; --red:#ff4136; --green:#2ecc40; --purple:#c58aff;
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
#palette-picker, #report-link { background:linear-gradient(180deg, var(--node-bg), var(--node-bg-2));
  border:1px solid var(--panel-border); color:var(--text); border-radius:6px; padding:.35rem .7rem;
  cursor:pointer; font-size:.75rem; text-decoration:none; transition:border-color .12s, color .12s; }
#palette-picker:hover, #report-link:hover { border-color:var(--accent); color:var(--accent); }
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
#empty-state { position:absolute; inset:0; display:flex; flex-direction:column; align-items:center;
  justify-content:center; text-align:center; padding:2rem; font-size:1rem; color:var(--text-dim);
  background:var(--bg); z-index:20; }
/* The empty state's own children are inert to hit-testing, so
   `document.elementFromPoint` at the canvas centre keeps returning
   `#empty-state` itself -- the exact property
   test_blueprint_render.py's D1 guard asserts. They carry no behaviour,
   so nothing is lost by making them transparent to the pointer. */
#empty-state > * { pointer-events:none; }
#empty-state .empty-title { font-size:1.15rem; font-weight:700; color:var(--text); margin-bottom:.6rem;
  max-width:60rem; }
#empty-state .empty-body { max-width:52rem; line-height:1.55; }
#empty-state code { color:var(--accent); background:var(--node-bg); border:1px solid var(--panel-border);
  border-radius:4px; padding:0 .25rem; }
/* Round 5: the scope banner. The page must say what it is NOT showing, at
   all times and without a click -- a truncated view that does not announce
   itself is the same lie as a filtered one. */
#scope-banner { padding:.35rem 1rem; background:var(--panel); border-bottom:1px solid var(--panel-border);
  font-size:.72rem; color:var(--text-dim); z-index:54; display:flex; flex-wrap:wrap;
  align-items:baseline; gap:.5rem; }
#scope-banner .scope-tag { font-weight:700; color:var(--accent); text-transform:uppercase;
  letter-spacing:.04em; }
#scope-banner.scope-truncated { border-bottom-color:var(--amber); }
#scope-banner.scope-truncated .scope-headline { color:var(--amber); font-weight:600; }
#scope-banner details { display:inline; }
#scope-banner summary { cursor:pointer; color:var(--accent); }
#scope-banner .scope-note { display:block; margin:.25rem 0 0 .8rem; max-width:80rem; }
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
.verdict-head { font-weight:700; letter-spacing:0.03em; }
.verdict-intent { color:var(--text-dim); margin:2px 0 4px; }
.verdict-warn { color:var(--amber); margin-top:4px; }
.intent-row { border-left:3px solid var(--accent); padding:4px 8px; margin:6px 0;
              background:var(--node-bg-2); }
.intent-row.intent-PROPOSED { border-left-color:var(--amber); border-left-style:dashed; }
.intent-status { font-weight:700; font-size:0.85em; letter-spacing:0.03em; }
.intent-invariant { color:var(--text-dim); font-family:ui-monospace,monospace;
                    font-size:0.9em; }
.verdict-ALIGNED { border-left-color:var(--green); }
.verdict-MISALIGNED { border-left-color:var(--red); }
.verdict-NOT_EXERCISED { border-left-color:var(--amber); border-left-style:dashed; }
.verdict-UNVERIFIABLE { border-left-color:var(--text-dim); }
.verdict-NO_INTENT { border-left-color:var(--panel-border); }
.action-btn { margin:.4rem 0; background:var(--accent); color:#fff; border:none; border-radius:6px;
  padding:.3rem .6rem; cursor:pointer; font-size:.73rem; }
.condition-source { background:var(--node-bg); padding:.4rem; border-radius:4px; white-space:pre-wrap;
  word-break:break-word; font-size:.73rem; }
.change-box { border:1px solid var(--panel-border); border-radius:6px; padding:.3rem; margin:.3rem 0; }
/* round 4, R1/R2: stage cards and the flow readout. */
.kind-STAGE { border-radius:14px; display:flex; flex-direction:column; align-items:stretch; }
.kind-STAGE .node-header { text-align:center; }
.stage-rule-note { padding:0 .5rem; font-size:.6rem; color:var(--text-dim); font-style:italic; }
.stage-list { list-style:decimal; margin:.2rem 0 0; padding:0 .6rem 0 1.5rem; overflow-y:auto; flex:1;
  font-size:.68rem; line-height:1.5; }
.stage-list-item { cursor:pointer; overflow-wrap:anywhere; }
.stage-list-item:hover { color:var(--accent); }
.stage-list-more { color:var(--text-dim); font-style:italic; list-style:none; margin-left:-1.5rem; }
/* Round 5: an omission is drawn in the attention colour, never in the
   dimmed "and some more of the same" grey -- "not included" and "there are
   further members" are different facts and must not look alike. */
.stage-list-omitted { color:var(--amber); list-style:none; margin-left:-1.5rem; font-size:.66rem;
  line-height:1.3; overflow-wrap:anywhere; word-break:normal; }
.detail-truncated { color:var(--amber); font-size:.7rem; margin:.15rem 0 .4rem 0; }
.wire-flow-BACKWARD { stroke:var(--red) !important; stroke-width:2.6px !important; stroke-dasharray:6 4 !important; }
.wire-flow-BACKWARD.wire-conf-CERTAIN, .wire-flow-BACKWARD.wire-conf-RESOLVED { filter:drop-shadow(0 0 3px rgba(229,72,77,.6)); }
#flow-readout { position:absolute; right:.6rem; top:.6rem; width:22rem; max-width:calc(100% - 1.2rem);
  background:var(--panel); border:1px solid var(--red); border-radius:10px;
  box-shadow:0 10px 28px rgba(0,0,0,.45); font-size:.68rem; z-index:35; max-height:min(60vh, calc(100vh - 8rem));
  display:flex; flex-direction:column; overflow:hidden; }
/* A plain <button>, not a <details>: its collapsed state is remembered in
   localStorage the same way the palette is, which `<details open>` cannot
   drive from JS as cleanly. Always visible, `flex-shrink:0`, so at a short
   viewport the heading can never scroll out of view the way it did before
   -- the actual D11-adjacent bug was `#flow-pairs` missing `min-height:0`,
   which let its content inflate the whole flex column instead of scrolling
   internally. */
#flow-readout-header { flex-shrink:0; width:100%; display:flex; align-items:center; justify-content:space-between;
  background:linear-gradient(180deg, rgba(229,72,77,.25), transparent); border:none; cursor:pointer;
  padding:.4rem .7rem; font-weight:700; font-size:.68rem; letter-spacing:.03em; text-transform:uppercase;
  color:var(--text); border-bottom:1px solid var(--panel-border); font-family:inherit; }
#flow-readout-body { display:flex; flex-direction:column; min-height:0; overflow:hidden; }
#flow-totals { flex-shrink:0; display:flex; gap:.5rem; padding:.5rem .7rem; flex-wrap:wrap; }
.flow-total-chip { border:1px solid var(--panel-border); border-radius:6px; padding:.15rem .5rem; font-size:.66rem; }
.flow-total-chip.flow-total-BACKWARD { border-color:var(--red); color:var(--red); font-weight:700; }
#flow-pairs { flex:1 1 auto; min-height:0; overflow-y:auto; padding:0 .5rem .5rem; }
.flow-pair-row { display:flex; justify-content:space-between; gap:.4rem; padding:.25rem .3rem; border-radius:4px;
  cursor:pointer; font-size:.66rem; }
.flow-pair-row:hover { background:var(--node-header); }
.flow-pair-row.flow-pair-BACKWARD { color:var(--red); font-weight:600; }
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
/* Bounded by construction: a banner that can grow without limit takes the
   canvas with it. Measured: an unbounded list of unreadable files on the
   fixture corpus left #canvas-wrap 312px tall and Fit at 0.087. */
#diagnostics { background:rgba(229,72,77,.12); border-bottom:2px solid var(--red); padding:.5rem 1rem;
  font-size:.73rem; max-height:5.5rem; overflow:auto; flex:0 0 auto; }
/* A narrow skip is not a red-alert fact about the whole map; it reads as
   information, not as failure. */
#diagnostics .partial-skip { color:var(--text); margin:.15rem 0; }
#diagnostics.diag-info { background:var(--panel); border-bottom:1px solid var(--panel-border); }
#diagnostics summary { cursor:pointer; color:var(--accent); }
#diagnostics ul { margin:.3rem 0 0; padding-left:1.2rem; }
/* Round 6: the decoder's own line. Visible from the first paint, replaced
   by the canvas when the island is decoded, and left showing an explicit
   error if it never is -- a blank page is the one thing it must not be. */
#boot-status { background:var(--panel); border-bottom:1px solid var(--panel-border);
  padding:.4rem 1rem; font-size:.73rem; color:var(--text-dim); flex:0 0 auto; }
#boot-status.boot-error { background:rgba(229,72,77,.12); border-bottom:2px solid var(--red);
  color:var(--text); }
#boot-status.boot-note { background:rgba(255,182,72,.12); border-bottom:2px solid var(--amber);
  color:var(--text); }
.lod-hide-labels .node-body, .lod-hide-labels .node-badges { display:none; }
.lod-hide-badges .node-badges { display:none; }
</style>"""

_BODY = """<div id="app">
<div id="diagnostics" hidden></div>
<div id="boot-status" role="status">Decompressing map data&hellip;</div>
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
<select id="palette-picker" title="colour palette"></select>
</div>
</header>
<div id="toolbar">
<button type="button" id="btn-fit">Fit (F)</button>
<button type="button" id="btn-reset">Reset camera</button>
<button type="button" id="btn-zoom-in">+</button>
<button type="button" id="btn-zoom-out">-</button>
<button type="button" id="btn-expand-all">Expand all modules</button>
<button type="button" id="btn-collapse-all">Collapse all modules</button>
<span id="stage-controls" hidden>
<button type="button" id="btn-toggle-stage-mode">Show full graph</button>
<button type="button" id="btn-expand-all-stages">Expand all stages</button>
<button type="button" id="btn-collapse-all-stages">Collapse all stages</button>
</span>
<details id="filters-panel"><summary>Filters</summary>
<div id="filter-kinds"></div>
<div><label>min confidence <select id="filter-min-conf"></select></label></div>
<label class="filter-chip"><input type="checkbox" id="filter-decision-reach"> only elements that reach a decision</label>
<label class="filter-chip"><input type="checkbox" id="filter-findings"> only elements with findings</label>
<label class="filter-chip" id="filter-backward-chip" hidden><input type="checkbox" id="filter-backward"> only backward edges</label>
</details>
<button type="button" id="btn-clear-filters">Clear filters</button>
<span id="filter-summary"></span>
</div>
<div id="scope-banner"></div>
<div id="canvas-wrap">
<div id="viewport">
<div id="grid-bg"></div>
<div id="world">
<svg id="wires-svg"><g id="wires-g"></g></svg>
<div id="nodes-layer"></div>
</div>
</div>
<div id="empty-state" hidden></div>
<!-- Every overlay below is `position:absolute` and anchors to *this*
     container -- `#canvas-wrap` is the region below the header/toolbar,
     so `top:0`/`top:.6rem` here means "just under the toolbar", not "the
     very top of the page". These used to be siblings of `#canvas-wrap`
     instead of children of it, so their containing block was `#app`
     (full page height): `#detail-panel`'s `top:0` rendered it starting
     at the page's own top edge, covering the search box and palette
     picker in the header, and `#flow-readout`'s `top:.6rem` landed it
     behind the header entirely -- found while checking why its own
     button was unclickable. -->
<aside id="detail-panel" hidden>
<button type="button" id="detail-close">&times;</button>
<div id="detail-content"></div>
</aside>
<div id="flow-readout" hidden>
<button type="button" id="flow-readout-header">
<span>Flow vs. cascade order</span><span id="flow-readout-caret">&#9662;</span>
</button>
<div id="flow-readout-body">
<div id="flow-totals"></div>
<div id="flow-pairs"></div>
</div>
</div>
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
</div>
</div>"""

_SCRIPT_BODY = """
function main(DATA) {
//: Round 6: the page's data arrives as an argument rather than being read
//: from the DOM here, because decompression is asynchronous. Everything
//: below is unchanged and still closes over DATA exactly as before.
window.CASCADE_BLUEPRINT_DATA = DATA;
//: Every execution-tab node, by id, regardless of current collapse state
//: -- built once so stage cards can label their member lines without an
//: O(n) scan per line per render.
var EXECUTION_NODE_BY_ID = {};
(DATA.execution.nodes || []).forEach(function (n) { EXECUTION_NODE_BY_ID[n.id] = n; });

var NODE_W = 220, NODE_H = 72, DECISION_SIZE = 158, STAGE_W = 260, STAGE_H = 300;
//: How many member lines a collapsed stage card shows before "+N more" --
//: the deck's own reference layout shows 5 per container; this affords a
//: little more since the card is taller than the deck's, and caps
//: regardless of how many hundreds of elements a fallback module group
//: might hold.
var STAGE_VISIBLE_MEMBERS = 10;
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

// ---- palette (round 4, R3) ----
var PALETTES = ['blueprint-dark', 'ai-blue', 'silver-milk', 'high-contrast'];
var PALETTE_LABELS = {
  'blueprint-dark': 'Blueprint Dark', 'ai-blue': 'AI Blue',
  'silver-milk': 'Silver & Milk', 'high-contrast': 'High Contrast'
};
function loadPalette() {
  // The owner asked for "a highly visually aesthetic flow chart similar to
  // that from Unreal Engine 5.0" -- Blueprint Dark is the default
  // unconditionally, not from `prefers-color-scheme`. The picker below and
  // the localStorage remembered choice both still work either direction;
  // only the default, on a machine that has never opened this page
  // before, is fixed.
  try {
    var saved = window.localStorage.getItem('cascade_blueprint_palette');
    if (PALETTES.indexOf(saved) !== -1) return saved;
  } catch (e) {}
  return 'blueprint-dark';
}
function savePalette(p) { try { window.localStorage.setItem('cascade_blueprint_palette', p); } catch (e) {} }
var palette = loadPalette();
root.setAttribute('data-palette', palette);
var paletteSelect = document.getElementById('palette-picker');
PALETTES.forEach(function (p) {
  var opt = document.createElement('option');
  opt.value = p; opt.textContent = PALETTE_LABELS[p];
  if (p === palette) opt.selected = true;
  paletteSelect.appendChild(opt);
});
paletteSelect.addEventListener('change', function () {
  palette = paletteSelect.value;
  root.setAttribute('data-palette', palette);
  savePalette(palette);
});

// ---- state ----
function defaultFilters() {
  return { kinds:{}, minConfidenceIndex:null, onlyDecisionReach:false, onlyFindings:false, onlyBackward:false };
}
var state = {
  tab: 'execution',
  camera: { x:40, y:40, scale:1 },
  selectedId: null,
  selectedWireId: null,
  positions: { execution:{}, lineage:{}, diff:{} },
  collapsed: { execution:{}, lineage:{}, diff:{} },
  filters: defaultFilters(),
  search: '',
  // round 4, R1: the Execution tab's default is grouped stage cards, not
  // the per-element graph. `false` here means "ungrouped" and reveals
  // exactly the graph phase C originally shipped.
  stageMode: true,
  collapsedStages: {},
  stageDefaultsSet: false
};
['execution', 'lineage', 'diff'].forEach(function (tab) {
  var g = DATA[tab];
  if (!g || !g.nodes || g.nodes.length <= MODULE_COLLAPSE_THRESHOLD) return;
  var mods = {};
  g.nodes.forEach(function (n) { if (n.module) mods[n.module] = true; });
  state.collapsed[tab] = mods;
});

function getGraph(tab) { return DATA[tab] || { nodes: [], wires: [] }; }

//: The single flag string every "not included" note names, so the page can
//: never suggest a flag that would not actually widen THIS view.
function widenFlag() {
  var v = DATA.view || {};
  if (v.scope !== 'full') return '--scope full';
  if (v.truncated) return '--max-nodes 0';
  return '--scope full';
}

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

//: The default grouping for the Execution tab (round 4, R1): stage cards
//: from `DATA.execution.stages`, all collapsed until the user expands one.
//: `false` reverts to today's per-element/module-collapsible graph --
//: "Expand all stages" sets this, and it stays reachable exactly as
//: before, per the round 4 brief's own requirement.
function ensureStageDefaults() {
  if (state.stageDefaultsSet) return;
  state.stageDefaultsSet = true;
  var stages = (DATA.execution && DATA.execution.stages) || [];
  stages.forEach(function (s, i) { state.collapsedStages[i] = true; });
}

function computeModuleGrouping(tab, kept, collapsedMods) {
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
  var moduleTotals = (DATA.view && DATA.view.module_totals) || {};
  var moduleIncluded = (DATA.view && DATA.view.module_included) || {};
  var aggList = Object.keys(moduleAgg).sort().map(function (k) {
    var agg = moduleAgg[k];
    // Round 5: a collapsed module card must distinguish "this module has
    // 3 members" from "this page carries 3 of this module's 1,712
    // members". Both numbers come from Python, counted over the unscoped
    // graph and over the island respectively -- never from `agg.count`,
    // which the user's own filters also move, so a filtered card can never
    // blame the scope for what a filter did.
    if (tab === 'execution') {
      var total = moduleTotals[k];
      agg.member_total = (total === undefined) ? agg.count : total;
      agg.not_included = Math.max(0, agg.member_total - (moduleIncluded[k] || 0));
    }
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
  return { displayNodes: displayNodes, displayIdOf: displayIdOf };
}

//: The Execution tab's grouped default (round 4, R1): a STAGE node per
//: `DATA.execution.stages` entry that is still collapsed, holding its
//: member ids in card 3's own order (never re-sorted, unlike module
//: aggregates, which have no inherent order to preserve). An expanded
//: stage's members pass through individually, at that stage's column --
//: not their own call-graph layer, which would scatter them across
//: whichever columns the ungrouped view uses and defeat the point of
//: grouping by stage. A decision node is only shown once its owning
//: element's stage is expanded; collapsed, it is part of what the stage
//: card summarises, not drawn on its own.
function computeStageGrouping(kept, keptIds) {
  var graph = getGraph('execution');
  var stages = graph.stages || [];
  var stageOf = graph.stage_of || {};
  var byId = {};
  kept.forEach(function (n) { byId[n.id] = n; });
  var displayNodes = [];
  var displayIdOf = {};

  stages.forEach(function (stage, index) {
    var members = stage.member_ids.filter(function (id) { return keptIds[id]; });
    // Round 5: a stage whose members are all outside this page's SCOPE is
    // still drawn, and says so. Dropping the card would render an
    // omission as an absence -- the exact failure this round exists to
    // fix. `member_omitted` is the scope's doing, computed in Python; a
    // stage emptied by the user's own filters still disappears, exactly as
    // before, because the filter panel already says a filter is on.
    var notIncluded = stage.member_omitted || 0;
    if (!members.length && !notIncluded) return;
    if (state.collapsedStages[index] !== false || !members.length) {
      displayNodes.push({
        id: stage.id, kind: 'STAGE', is_stage: true, name: stage.name, rule: stage.rule,
        layer: index, ordered_member_ids: members, count: members.length,
        member_total: stage.member_total, not_included: notIncluded,
        confidence: stage.confidence, module: '', is_element: false,
        reachability: { state: 'UNKNOWN', source: 'stage',
          reason: 'expand the stage to see per-element reachability', sink_ids: [], path_ids: [] },
        finding_count: 0, is_sink: false
      });
      members.forEach(function (id) { displayIdOf[id] = stage.id; });
    } else {
      members.forEach(function (id) {
        var n = byId[id];
        if (!n) return;
        var clone = {};
        for (var k in n) { if (Object.prototype.hasOwnProperty.call(n, k)) clone[k] = n[k]; }
        clone.layer = index;
        displayNodes.push(clone);
        displayIdOf[id] = id;
      });
    }
  });
  kept.forEach(function (n) {
    if (n.kind !== 'DECISION') return;
    var ownerStage = stageOf[n.owner_element_id];
    if (ownerStage === undefined || state.collapsedStages[ownerStage] !== false) return;
    var clone = {};
    for (var k in n) { if (Object.prototype.hasOwnProperty.call(n, k)) clone[k] = n[k]; }
    clone.layer = ownerStage;
    displayNodes.push(clone);
    displayIdOf[n.id] = n.id;
  });
  return { displayNodes: displayNodes, displayIdOf: displayIdOf };
}

function computeLayout(tab) {
  var graph = getGraph(tab);
  var allNodes = graph.nodes || [];
  var wires = graph.wires || [];
  var kept = allNodes.filter(passesFilters);
  var keptIds = {};
  kept.forEach(function (n) { keptIds[n.id] = true; });

  var grouping;
  if (tab === 'execution' && state.stageMode) {
    ensureStageDefaults();
    grouping = computeStageGrouping(kept, keptIds);
  } else {
    grouping = computeModuleGrouping(tab, kept, state.collapsed[tab] || {});
  }
  var displayNodes = grouping.displayNodes;
  var displayIdOf = grouping.displayIdOf;

  var wireBuckets = {}, wireOrder = [];
  wires.forEach(function (w) {
    if (tab === 'execution' && state.filters.onlyBackward && w.flow !== 'BACKWARD') return;
    if (!keptIds[w.source_id] || !keptIds[w.target_id]) return;
    var ds = displayIdOf[w.source_id], dt = displayIdOf[w.target_id];
    if (ds === undefined || dt === undefined || ds === dt) return;
    var key = ds + '=>' + dt + '::' + w.kind;
    var bucket = wireBuckets[key];
    if (!bucket) {
      bucket = wireBuckets[key] = {
        id: key, kind: w.kind, source_id: ds, target_id: dt, count: 0, bestRank: 999,
        representative: w, flow: w.flow || ''
      };
      wireOrder.push(key);
    }
    bucket.count += 1;
    var r = (w.confidence !== null && w.confidence !== undefined) ? DATA.confidence_rank[w.confidence] : 999;
    if (r < bucket.bestRank) { bucket.bestRank = r; bucket.representative = w; }
    // Grouping (by module or by stage) can fold several original wires
    // with different individual flow classifications into one drawn
    // wire -- e.g. two members of the same collapsed module sit in
    // different stages. A backward relationship must never be hidden
    // behind an aggregate that happens to also contain a forward one, so
    // BACKWARD wins the bucket's displayed flow regardless of arrival
    // order.
    if (w.flow === 'BACKWARD') bucket.flow = 'BACKWARD';
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
  var rowsPerSubcol = Math.max(3, Math.ceil(Math.sqrt(Math.max(1, displayNodes.length))));
  var ROW_GUTTER = 24, BAND_GUTTER = 60;

  // Pass 1: each layer's *actual* content height, from the real size of
  // whatever is in it (a STAGE card is ~4x a plain element row) -- never a
  // flat ROW_GAP-based guess. D11 found the guess wasted ~900px per band
  // (a 1250px band pitch for ~320px-tall content), landing Fit at scale
  // 0.088 -- a grey speck, not a readable canvas.
  var layerInfo = {};
  var totalCols = 0, heightSum = 0;
  orderedLayers.forEach(function (l) {
    var members = byLayer[l];
    var itemH = NODE_H;
    members.forEach(function (n) { itemH = Math.max(itemH, sizeOf(n).h); });
    var pitch = itemH + ROW_GUTTER;
    var subcols = Math.max(1, Math.ceil(members.length / rowsPerSubcol));
    var rowsUsed = Math.max(1, Math.min(members.length, rowsPerSubcol));
    var height = rowsUsed * pitch;
    layerInfo[l] = { subcols: subcols, pitch: pitch, height: height };
    totalCols += subcols;
    heightSum += height;
  });

  // The mirror-image problem: stage mode (round 4, R1) can produce dozens
  // of stages -- each its own layer with exactly one card, so the
  // sub-column packing above never triggers -- and a fixture corpus with
  // no single wired entry point turns every independent program into its
  // own stage. Left as one row, that is the same "Fit shrinks everything
  // to illegibility" failure D2 already named, just along the other axis.
  // Past a threshold, wrap columns into bands, choosing the band width so
  // the whole canvas approaches the ~16:9 a real viewport presents --
  // using each axis's real pixel pitch (a column is ~300px, a row is
  // however tall its content actually is), not raw counts.
  var TARGET_ASPECT = 16 / 9;
  var avgLayerHeight = heightSum / Math.max(1, orderedLayers.length);
  var numBands = orderedLayers.length > 1 ? Math.max(1, Math.round(
    Math.sqrt(totalCols * COL_GAP * (1 / TARGET_ASPECT) / Math.max(1, avgLayerHeight))
  )) : 1;
  var wrapCols = Math.max(1, Math.ceil(totalCols / numBands));

  // Pass 2: assign each layer to a band, then give every band exactly the
  // height its tallest layer actually needs, plus one gutter -- not a
  // guess repeated per band.
  var layerBand = {};
  var bandMaxHeight = [];
  var colsInBand = 0, band = 0;
  orderedLayers.forEach(function (l) {
    layerBand[l] = band;
    bandMaxHeight[band] = Math.max(bandMaxHeight[band] || 0, layerInfo[l].height);
    colsInBand += layerInfo[l].subcols;
    if (colsInBand >= wrapCols) { colsInBand = 0; band += 1; }
  });
  var bandYOffset = [0];
  for (var bandIndex = 1; bandIndex < bandMaxHeight.length; bandIndex++) {
    bandYOffset[bandIndex] = bandYOffset[bandIndex - 1] + bandMaxHeight[bandIndex - 1] + BAND_GUTTER;
  }

  var pos = {};
  var xCursor = 0, curBand = 0;
  orderedLayers.forEach(function (l) {
    if (layerBand[l] !== curBand) { xCursor = 0; curBand = layerBand[l]; }
    var members = byLayer[l];
    var info = layerInfo[l];
    members.forEach(function (n, i) {
      var saved = state.positions[tab][n.id];
      if (saved) { pos[n.id] = { x: saved.x, y: saved.y }; return; }
      var subcol = Math.floor(i / rowsPerSubcol);
      var row = i % rowsPerSubcol;
      pos[n.id] = { x: xCursor + subcol * COL_GAP, y: row * info.pitch + bandYOffset[curBand] };
    });
    xCursor += info.subcols * COL_GAP;
  });

  var byId = {};
  displayNodes.forEach(function (n) { byId[n.id] = n; });

  return { nodes: displayNodes, wires: displayWires, pos: pos, byId: byId, displayIdOf: displayIdOf };
}

function sizeOf(n) {
  if (n.kind === 'DECISION') return { w: DECISION_SIZE, h: DECISION_SIZE };
  if (n.kind === 'STAGE') return { w: STAGE_W, h: STAGE_H };
  return { w: NODE_W, h: NODE_H };
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

  wrap.appendChild(el('div', 'node-header', n.kind === 'STAGE' ? 'STAGE' : n.kind));
  wrap.appendChild(el('div', 'node-body', n.name || n.id));

  if (n.kind === 'STAGE') {
    var ruleNote = n.rule === 'fallback_module'
      ? 'module (not in the cascade order)'
      : (n.count + ' step' + (n.count === 1 ? '' : 's') + ', cascade order');
    wrap.appendChild(el('div', 'stage-rule-note', ruleNote));
    var list = el('ol', 'stage-list');
    var shown = n.ordered_member_ids.slice(0, STAGE_VISIBLE_MEMBERS);
    shown.forEach(function (memberId) {
      var memberNode = EXECUTION_NODE_BY_ID[memberId];
      var li = elWithBreaks('li', 'stage-list-item', (memberNode && memberNode.name) || memberId);
      li.addEventListener('click', function (ev) {
        ev.stopPropagation();
        selectElementById(memberId, 'execution');
      });
      list.appendChild(li);
    });
    if (n.ordered_member_ids.length > shown.length) {
      list.appendChild(el('li', 'stage-list-more', '+ ' + (n.ordered_member_ids.length - shown.length) + ' more'));
    }
    if (n.not_included) {
      // Never an empty list: what is missing is named, with the flag that
      // brings it back.
      list.appendChild(el('li', 'stage-list-omitted',
        '+ ' + n.not_included + ' member' + (n.not_included === 1 ? '' : 's')
        + ' not included in this page \\u2014 re-run with ' + widenFlag()));
    }
    wrap.appendChild(list);
  }
  if (n.is_module_agg && n.not_included) {
    wrap.appendChild(el('div', 'stage-list-omitted',
      n.not_included + ' more element' + (n.not_included === 1 ? '' : 's')
      + ' in this module are not included in this page \\u2014 re-run with ' + widenFlag()));
  }

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
  if (w.flow === 'BACKWARD') {
    // Round 4, R2: a return wire must read as going back, not as a longer
    // forward one -- routed clear below both lanes rather than the usual
    // S-curve leaving each pin horizontally.
    var dip = 90 + Math.min(240, Math.abs(x1 - x2) * 0.12);
    var midY = Math.max(y1, y2) + dip;
    return 'M ' + x1 + ' ' + y1 + ' C ' + x1 + ' ' + midY + ', ' + x2 + ' ' + midY + ', ' + x2 + ' ' + y2;
  }
  var dx = Math.max(40, Math.abs(x2 - x1) * 0.5);
  return 'M ' + x1 + ' ' + y1 + ' C ' + (x1 + dx) + ' ' + y1 + ', ' + (x2 - dx) + ' ' + y2 + ', ' + x2 + ' ' + y2;
}

function buildWireEl(w, layout, tab) {
  var SVG_NS = 'http:' + '//www.w3.org/2000/svg';
  var path = document.createElementNS(SVG_NS, 'path');
  path.setAttribute('d', wirePathD(w, layout));
  var conf = w.representative ? w.representative.confidence : w.confidence;
  var secondary = (w.kind !== 'CALLS' && w.kind !== 'OUTCOME') ? ' wire-secondary' : '';
  var flowClass = w.flow ? (' wire-flow-' + cssSafe(w.flow)) : '';
  path.setAttribute('class', 'wire wire-conf-' + cssSafe(conf) + ' wire-kind-' + cssSafe(w.kind) + secondary + flowClass);
  if (w.flow === 'BACKWARD' && w.source_stage !== undefined && w.target_stage !== undefined
      && layout.byId[w.source_id] && layout.byId[w.source_id].kind === 'STAGE') {
    path.dataset.stagePair = w.source_stage + '>' + w.target_stage;
  }
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

//: An empty canvas must always say WHY it is empty and what to do about
//: it -- round 5's rule, after the lineage tab's 402,596 edges had to
//: become opt-in: never render an omission as emptiness.
function setEmptyState(title, body) {
  var es = document.getElementById('empty-state');
  es.innerHTML = '';
  es.appendChild(el('div', 'empty-title', title));
  var bodyEl = el('div', 'empty-body');
  String(body || '').split('`').forEach(function (part, i) {
    if (part === '') return;
    bodyEl.appendChild(i % 2 ? el('code', null, part) : document.createTextNode(part));
  });
  es.appendChild(bodyEl);
  es.hidden = false;
}

function updateEmptyState(tab) {
  var es = document.getElementById('empty-state');
  var g = getGraph(tab);
  if (tab === 'diff' && !DATA.diff.available) {
    setEmptyState('No version diff loaded', DATA.diff.reason); return;
  }
  if (!g.nodes || !g.nodes.length) {
    if (tab === 'lineage') {
      var count = (g.edges_in_graph || 0).toLocaleString();
      setEmptyState(
        g.focus_driven && g.focus_id
          ? 'Nothing in lineage within ' + (DATA.view.hops) + ' hop(s) of ' + g.focus_id
          : 'Lineage is focus-driven \\u2014 pick an element',
        g.reason || ('no lineage data \\u2014 lineage.jsonl is empty or missing (' + count + ' edges)')
      );
      return;
    }
    setEmptyState(
      tab === 'execution' ? 'Nothing to draw on the Execution tab' : 'No changes in this diff',
      tab === 'execution'
        ? 'no MODULE/CLASS/FUNCTION/METHOD elements are in this page \\u2014 run `metatron analyze` '
          + 'first, or widen the view with `--scope full` / `--max-nodes 0`'
        : 'the loaded diff names no element in this graph'
    );
    return;
  }
  es.hidden = true;
}

//: The scope banner: what this page holds, what it does not, and the flag
//: that changes it. Painted once, from `DATA.view`, and never hidden.
function renderScopeBanner() {
  var banner = document.getElementById('scope-banner');
  var v = DATA.view || {};
  banner.innerHTML = '';
  banner.classList.toggle('scope-truncated', !!v.truncated);
  banner.appendChild(el('span', 'scope-tag', 'scope ' + (v.scope || 'cascade')));
  if (v.focus) {
    banner.appendChild(el('span', 'scope-tag', 'focus ' + v.focus + ' (' + v.hops + ' hops)'));
  }
  if (v.forced) banner.appendChild(el('span', 'scope-tag', '--force'));
  banner.appendChild(elWithBreaks('span', 'scope-headline', v.headline || ''));
  var notes = v.notes || [];
  if (notes.length) {
    var det = document.createElement('details');
    det.appendChild(el('summary', null, 'what is not on this page (' + notes.length + ')'));
    notes.forEach(function (n) { det.appendChild(elWithBreaks('span', 'scope-note', n)); });
    banner.appendChild(det);
  }
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
//: `total`, when given, is the number of entries the artifacts hold before
//: the detail record's own cap (round 5, DETAIL_LIST_CAP). The row states
//: the shortfall: "40 of 3,182 shown" is a fact; showing 40 silently is not.
function idListRow(label, ids, total) {
  var row = el('div', 'detail-row');
  var shown = ids ? ids.length : 0;
  var labelText = (total !== undefined && total !== null && total > shown)
    ? label + ' (' + shown + ' of ' + total + ')' : label;
  row.appendChild(el('span', 'detail-label', labelText));
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
    panel.appendChild(idListRow('callers', (detail.incoming_edges || []).map(function (e) { return e.source_id; }), detail.incoming_edges_total));
    panel.appendChild(idListRow('callees', (detail.outgoing_edges || []).map(function (e) { return e.target_id; }), detail.outgoing_edges_total));
    if (detail.edges_projected && DATA.view && DATA.view.edges_projected_note) {
      panel.appendChild(el('p', 'detail-truncated', DATA.view.edges_projected_note));
    }
    if (detail.detail_cap) {
      var capped = [];
      ['incoming_edges', 'outgoing_edges', 'lineage_in', 'lineage_out', 'order_node_ids',
       'slice_ids_as_member', 'barrier_ids'].forEach(function (f) {
        var t = detail[f + '_total'];
        if (t !== undefined && t > (detail[f] || []).length) capped.push(f + ': ' + (detail[f] || []).length + ' of ' + t);
      });
      if (capped.length) {
        panel.appendChild(el('p', 'detail-truncated',
          'This panel caps long lists at ' + detail.detail_cap + ' entries \\u2014 ' + capped.join(', ')
          + '. The artifacts still hold every one; the tabular report and the JSONL files are exhaustive.'));
      }
    }
    var featOut = (detail.lineage_out || []).filter(function (e) { return e.target_id && e.target_id.indexOf('@feature:') === 0; }).map(function (e) { return e.target_id; });
    var featIn = (detail.lineage_in || []).filter(function (e) { return e.source_id && e.source_id.indexOf('@feature:') === 0; }).map(function (e) { return e.source_id; });
    if (featOut.length || featIn.length) {
      panel.appendChild(el('h3', null, 'features'));
      panel.appendChild(idListRow('writes', featOut));
      panel.appendChild(idListRow('reads', featIn));
    }
  }
}

// Card 13. The intent an element was judged against, and the verdicts on it.
// The statement sits NEXT TO the verdict, because "MISALIGNED" on its own is
// a word, not an answer -- the owner has to see the sentence it contradicts.
function intentById(intentId) {
  var index = DATA.intents_by_id || {};
  return index[intentId] || null;
}

function appendVerdict(panel, v, source) {
  var box = el('div', 'runtime-evidence verdict-' + cssSafe(v.verdict));
  box.appendChild(el('div', 'verdict-head', v.verdict + '  (' + source + ')'));
  var intent = intentById(v.intent_id);
  if (intent) {
    box.appendChild(el('div', 'verdict-intent',
      'intent (' + intent.status + '): ' + intent.statement));
  } else if (v.intent_id) {
    box.appendChild(el('div', 'verdict-intent', 'intent: ' + v.intent_id));
  }
  if (v.expectation) box.appendChild(el('div', null, 'expected: ' + v.expectation));
  if (v.observation) box.appendChild(el('div', null, 'observed: ' + v.observation));
  if (v.verdict === 'NOT_EXERCISED') {
    box.appendChild(el('div', 'verdict-warn',
      'NOT_EXERCISED is not ALIGNED. No scenario in this run entered this '
      + 'element, so its intent is unverified.'));
  }
  panel.appendChild(box);
}

function renderIntentSection(panel, detail, elementId) {
  var intents = (detail && detail.intents) || [];
  var verdicts = (detail && detail.static_verdicts) || [];
  if (!intents.length && !verdicts.length) return;
  panel.appendChild(el('h3', null, 'intent'));
  intents.forEach(function (i) {
    var box = el('div', 'intent-row intent-' + cssSafe(i.status));
    box.appendChild(el('div', 'intent-status', i.status + (i.status === 'PROPOSED'
      ? '  -- derived by this tool, NOT owner-confirmed, and binding on nothing'
      : '  -- owner-confirmed')));
    box.appendChild(elWithBreaks('div', null, i.statement || '(no statement)'));
    (i.invariants || []).forEach(function (text) {
      box.appendChild(el('div', 'intent-invariant', 'invariant: ' + text));
    });
    (i.expected_reads || []).forEach(function (text) {
      box.appendChild(el('div', 'intent-invariant', 'expected read: ' + text));
    });
    (i.expected_writes || []).forEach(function (text) {
      box.appendChild(el('div', 'intent-invariant', 'expected write: ' + text));
    });
    panel.appendChild(box);
  });
  verdicts.forEach(function (v) { appendVerdict(panel, v, 'static evidence'); });
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
    appendVerdict(panel, v, 'observed in run ' + (v.run_id || rt.run_id));
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
  // `#detail-close` is a persistent sibling of this panel, never moved
  // into it: an earlier version re-parented it here on every render,
  // which worked once and then made the second render's `innerHTML = ''`
  // delete the button outright, crashing the next `appendChild` with "not
  // a Node" -- found by selecting two elements in the same page load, a
  // path none of this card's single-selection tests had exercised.
  panel.innerHTML = '';
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
  if (n.is_stage) {
    panel.appendChild(fieldRow('grouping rule', n.rule === 'fallback_module'
      ? 'module (this element is not in the cascade order -- see order.jsonl)'
      : 'cascade order (order.jsonl, card 3)'));
    panel.appendChild(el('h3', null, 'steps (' + n.ordered_member_ids.length
      + (n.member_total && n.member_total !== n.ordered_member_ids.length
         ? ' of ' + n.member_total : '') + '), in cascade order'));
    if (n.not_included) {
      panel.appendChild(el('p', 'detail-truncated',
        n.not_included + ' of this stage\\u2019s ' + n.member_total
        + ' members are not in this page. This is an omission by scope, not an '
        + 'empty stage \\u2014 re-render with ' + widenFlag() + ' to include them.'));
    }
    var stageMembersUl = el('ol');
    n.ordered_member_ids.forEach(function (m) {
      var li = el('li');
      li.appendChild(idLink(m, tab, (EXECUTION_NODE_BY_ID[m] && EXECUTION_NODE_BY_ID[m].name) || m));
      stageMembersUl.appendChild(li);
    });
    panel.appendChild(stageMembersUl);
    var expandStageBtn = el('button', 'action-btn', 'Expand this stage');
    expandStageBtn.type = 'button';
    expandStageBtn.addEventListener('click', function () {
      state.collapsedStages[n.layer] = false;
      renderTab(tab);
    });
    panel.appendChild(expandStageBtn);
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
    panel.appendChild(el('h3', null, 'members (' + n.count
      + (n.member_total && n.member_total !== n.count ? ' of ' + n.member_total : '') + ')'));
    if (n.not_included) {
      panel.appendChild(el('p', 'detail-truncated',
        n.not_included + ' of this module\\u2019s ' + n.member_total
        + ' elements are not in this page \\u2014 re-render with ' + widenFlag() + '.'));
    }
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
    renderIntentSection(panel, DATA.element_details[n.id], n.id);
    renderRuntimeSection(panel, n.id);
  }
}
function renderDetailForWire(w, tab) {
  document.getElementById('detail-panel').hidden = false;
  var panel = document.getElementById('detail-content');
  panel.innerHTML = '';
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
//: Selecting a stage's member line: the element may not be painted as its
//: own node right now (its stage is still collapsed), so this looks the
//: element up by id directly rather than requiring it to already be on
//: canvas -- `updateSelectionHighlight` degrades to "nothing matches" if
//: it is not currently painted, which is correct, not a bug.
function selectElementById(id, tab) {
  var node = EXECUTION_NODE_BY_ID[id];
  if (!node) return;
  state.selectedId = id; state.selectedWireId = null;
  updateSelectionHighlight();
  renderDetailForNode(node, tab);
}
function selectWire(w, tab) {
  state.selectedWireId = w.id; state.selectedId = null;
  updateSelectionHighlight(); renderDetailForWire(w, tab);
}

// ---- tabs ----
function switchTab(tab) {
  state.tab = tab;
  document.querySelectorAll('.tab-btn').forEach(function (b) { b.classList.toggle('active', b.dataset.tab === tab); });
  var isExecution = tab === 'execution';
  document.getElementById('stage-controls').hidden = !isExecution;
  document.getElementById('filter-backward-chip').hidden = !isExecution;
  document.getElementById('btn-expand-all').hidden = isExecution && state.stageMode;
  document.getElementById('btn-collapse-all').hidden = isExecution && state.stageMode;
  document.getElementById('flow-readout').hidden = !isExecution;
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
  document.getElementById('filter-backward').checked = false;
}
function updateFilterSummary() {
  var active = [];
  var excludedKinds = Object.keys(state.filters.kinds).filter(function (k) { return state.filters.kinds[k]; });
  if (excludedKinds.length) active.push('kinds excluded: ' + excludedKinds.join(','));
  if (state.filters.minConfidenceIndex !== null) active.push('confidence floor active');
  if (state.filters.onlyDecisionReach) active.push('only reaches-decision');
  if (state.filters.onlyFindings) active.push('only with findings');
  if (state.filters.onlyBackward) active.push('only backward edges');
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

// ---- round 4, R1: stage mode (the Execution tab's default) ----
document.getElementById('btn-toggle-stage-mode').addEventListener('click', function () {
  state.stageMode = !state.stageMode;
  this.textContent = state.stageMode ? 'Show full graph' : 'Show grouped stages';
  document.getElementById('btn-expand-all').hidden = state.stageMode;
  document.getElementById('btn-collapse-all').hidden = state.stageMode;
  if (state.tab === 'execution') renderTab('execution');
});
document.getElementById('btn-expand-all-stages').addEventListener('click', function () {
  var stages = (DATA.execution && DATA.execution.stages) || [];
  stages.forEach(function (s, i) { state.collapsedStages[i] = false; });
  if (state.tab === 'execution') renderTab('execution');
});
document.getElementById('btn-collapse-all-stages').addEventListener('click', function () {
  var stages = (DATA.execution && DATA.execution.stages) || [];
  stages.forEach(function (s, i) { state.collapsedStages[i] = true; });
  if (state.tab === 'execution') renderTab('execution');
});
document.getElementById('filter-backward').addEventListener('change', function (ev) {
  state.filters.onlyBackward = ev.target.checked;
  renderTab(state.tab);
  updateFilterSummary();
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

// ---- round 4, R2: the permanent flow readout ----
function loadFlowReadoutCollapsed() {
  try {
    return window.localStorage.getItem('cascade_blueprint_flow_collapsed') === '1';
  } catch (e) { return false; }
}
function saveFlowReadoutCollapsed(collapsed) {
  try { window.localStorage.setItem('cascade_blueprint_flow_collapsed', collapsed ? '1' : '0'); } catch (e) {}
}
function applyFlowReadoutCollapsed(collapsed) {
  document.getElementById('flow-readout-body').hidden = collapsed;
  document.getElementById('flow-readout-caret').textContent = collapsed ? '\\u25b8' : '\\u25be';
}
document.getElementById('flow-readout-header').addEventListener('click', function () {
  var collapsed = !document.getElementById('flow-readout-body').hidden;
  applyFlowReadoutCollapsed(collapsed);
  saveFlowReadoutCollapsed(collapsed);
});
applyFlowReadoutCollapsed(loadFlowReadoutCollapsed());

// ---- legend & diagnostics ----
function renderFlowReadout() {
  var ex = DATA.execution;
  var totalsWrap = document.getElementById('flow-totals');
  totalsWrap.innerHTML = '';
  ['FORWARD', 'WITHIN', 'BACKWARD', 'UNORDERED'].forEach(function (kind) {
    var count = (ex.flow_totals && ex.flow_totals[kind]) || 0;
    totalsWrap.appendChild(el('span', 'flow-total-chip flow-total-' + kind, kind + ': ' + count));
  });
  var pairsWrap = document.getElementById('flow-pairs');
  pairsWrap.innerHTML = '';
  var stages = ex.stages || [];
  (ex.stage_pairs || []).forEach(function (pair) {
    var fromName = (stages[pair.from] && stages[pair.from].name) || ('stage ' + pair.from);
    var toName = (stages[pair.to] && stages[pair.to].name) || ('stage ' + pair.to);
    var arrow = pair.direction === 'BACKWARD' ? ' \\u2190 ' : ' \\u2192 ';
    var label = pair.direction === 'BACKWARD'
      ? (toName + arrow + fromName + ': ' + pair.count + ' call' + (pair.count === 1 ? '' : 's') + ' back')
      : (fromName + arrow + toName + ': ' + pair.count);
    var row = el('div', 'flow-pair-row flow-pair-' + cssSafe(pair.direction), label);
    row.addEventListener('click', function () { frameStagePair(pair); });
    pairsWrap.appendChild(row);
  });
  if (!(ex.stage_pairs || []).length) {
    pairsWrap.appendChild(el('div', 'missing-note', 'no forward/backward cross-stage wires.'));
  }
}
//: "Clicking a backward count selects and frames those edges" -- switches
//: to the execution tab, expands whichever stages own the pair's wires
//: (a collapsed stage's wires are aggregated and would not individually
//: highlight), then highlights exactly those wire ids.
function frameStagePair(pair) {
  switchTab('execution');
  state.collapsedStages[pair.from] = false;
  state.collapsedStages[pair.to] = false;
  renderTab('execution');
  var layout = layoutCache.execution;
  var wireIdSet = {};
  (pair.wire_ids || []).forEach(function (id) { wireIdSet[id] = true; });
  var touchedNodes = {};
  layout.wires.forEach(function (w) {
    if (w.representative && wireIdSet[w.representative.id]) {
      touchedNodes[w.source_id] = true;
      touchedNodes[w.target_id] = true;
    }
  });
  Array.prototype.forEach.call(nodesLayer.children, function (e) {
    var on = !!touchedNodes[e.dataset.id];
    e.classList.toggle('dim', !on);
    e.classList.toggle('path-highlight', on);
  });
  Array.prototype.forEach.call(wiresG.children, function (e) {
    var on = wireIdSet[e.dataset.id] || false;
    if (!on) {
      var w = null;
      for (var i = 0; i < layout.wires.length; i++) { if (layout.wires[i].id === e.dataset.id) { w = layout.wires[i]; break; } }
      on = w && w.representative && wireIdSet[w.representative.id];
    }
    e.classList.toggle('dim', !on);
    e.classList.toggle('path-highlight', !!on);
  });
  var ids = Object.keys(touchedNodes);
  if (ids.length) {
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    ids.forEach(function (id) {
      var n = layout.byId[id], p = layout.pos[id];
      if (!n || !p) return;
      var size = sizeOf(n);
      minX = Math.min(minX, p.x); minY = Math.min(minY, p.y);
      maxX = Math.max(maxX, p.x + size.w); maxY = Math.max(maxY, p.y + size.h);
    });
    if (isFinite(minX)) {
      var rect = viewport.getBoundingClientRect(), pad = 80;
      var scale = Math.min(2, Math.max(0.15, Math.min(
        (rect.width - pad * 2) / Math.max(1, maxX - minX),
        (rect.height - pad * 2) / Math.max(1, maxY - minY))));
      state.camera.scale = scale;
      state.camera.x = (rect.width - (maxX - minX) * scale) / 2 - minX * scale;
      state.camera.y = (rect.height - (maxY - minY) * scale) / 2 - minY * scale;
      applyCameraTransform();
    }
  }
}

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
  var unparsed = diag.unparsed_files || [];
  var hasAny = (diag.static_errors && diag.static_errors.length) ||
    (diag.diff_errors && diag.diff_errors.length) || (diag.runtime_errors && diag.runtime_errors.length)
    || unparsed.length;
  if (!hasAny) return;
  var box = document.getElementById('diagnostics');
  box.hidden = false;
  var anyError = (diag.static_errors && diag.static_errors.length) ||
    (diag.diff_errors && diag.diff_errors.length) ||
    (diag.runtime_errors && diag.runtime_errors.length) ||
    unparsed.some(function (u) { return u.absent_from_map; });
  // Red is reserved for "part of this map is missing or wrong". A narrow,
  // named skip in one analysis is information, and is styled as such.
  if (!anyError) { box.className = 'diag-info'; }
  if (unparsed.length) {
    // Round 6, D-owner-3: this headline used to read "N source files were
    // NOT read -- this map does not describe them" for ANY file-level
    // skip. On the owner's engine that named the one file the map is
    // almost entirely built from: card 17 had skipped it for dependency
    // analysis only. The distinction is `absent_from_map`, which is the
    // count of elements this map actually holds from that path -- so the
    // banner now separates "not in this map at all" from "one analysis
    // did not read it", states which analysis and what is missing, and
    // never makes a claim about the whole map from a narrow skip.
    var absent = unparsed.filter(function (u) { return u.absent_from_map; });
    var partial = unparsed.filter(function (u) { return !u.absent_from_map; });
    if (absent.length) {
      box.appendChild(elWithBreaks('strong', null,
        absent.length + ' source file' + (absent.length === 1 ? ' was' : 's were')
        + ' NOT read \u2014 this map does not describe '
        + (absent.length === 1 ? 'it' : 'them') + ': ' + absent[0].path
        + (absent.length > 1 ? ' and ' + (absent.length - 1) + ' more' : '')));
    }
    partial.forEach(function (u) {
      // One line per file, naming the analysis that skipped it and what is
      // therefore missing -- and, in the same sentence, what IS covered.
      box.appendChild(elWithBreaks('div', 'partial-skip',
        'Partly covered: ' + u.description
        + (u.analysis ? ' (' + u.analysis + ')' : '')
        + '. Everything else in this map covers ' + u.path + ' normally \u2014 '
        + u.elements_from_file.toLocaleString() + ' element'
        + (u.elements_from_file === 1 ? '' : 's') + ' in this map come from it.'));
    });
    var udet = document.createElement('details');
    udet.appendChild(el('summary', null, 'every file some analysis did not read, and what it means'));
    var uul = el('ul');
    unparsed.forEach(function (u) {
      uul.appendChild(elWithBreaks('li', null,
        u.reason + '  ' + u.path + ':' + (u.line === null || u.line === undefined ? '?' : u.line)
        + '  \u2014 ' + u.description
        + (u.analysis ? '  [' + u.analysis + ']' : '')
        + '  \u2014 ' + (u.absent_from_map
            ? 'no element of this map comes from this file'
            : u.elements_from_file.toLocaleString() + ' elements of this map come from this file')));
    });
    udet.appendChild(uul);
    box.appendChild(udet);
  }
  if (!((diag.static_errors && diag.static_errors.length) ||
        (diag.diff_errors && diag.diff_errors.length) ||
        (diag.runtime_errors && diag.runtime_errors.length))) return;
  var det = document.createElement('details');
  det.appendChild(el('summary', null, 'Artifact load errors (nothing was silently dropped)'));
  box.appendChild(det);
  var ul = el('ul');
  (diag.static_errors || []).forEach(function (e) { ul.appendChild(el('li', null, e.file + ':' + e.line + ': ' + e.reason)); });
  (diag.diff_errors || []).forEach(function (e) { ul.appendChild(el('li', null, '[diff] ' + e.file + ':' + e.line + ': ' + e.reason)); });
  (diag.runtime_errors || []).forEach(function (e) { ul.appendChild(el('li', null, '[runtime] ' + e.file + ':' + e.line + ': ' + e.reason)); });
  det.appendChild(ul);
}

// ---- init ----
function init() {
  buildLegend();
  buildFilterUI();
  showDiagnostics();
  renderScopeBanner();
  renderFlowReadout();
  if (DATA.report_link) {
    var link = document.getElementById('report-link');
    link.hidden = false;
    link.href = DATA.report_link;
  }
  window.addEventListener('resize', function () { cullNodes(); });
  switchTab('execution');
}
init();
var status = document.getElementById('boot-status');
if (status) { status.hidden = true; }
document.documentElement.setAttribute('data-cascade-ready', '1');
}
"""


#: Round 6: the island decoder, and -- when the browser has no native gzip
#: -- the inflater itself. No library, no CDN, no network: the page is
#: still one file that works from `file://`.
#:
#: `unpackIsland` is the exact twin of `_unpack_data` above. It rebuilds
#: every object and every array with every field; nothing is lazy,
#: approximate or omitted, so from `main(DATA)` onward the page is
#: byte-for-byte the page round 5 rendered.
_DECODER = """
var PACK_FORMAT = 'cascade-blueprint-pack/1';

function bootStatus(text, cls) {
  var box = document.getElementById('boot-status');
  if (!box) { return; }
  box.hidden = false;
  box.className = cls || '';
  box.textContent = text;
}

function bootFailed(err) {
  // A page that cannot decode its own data says so, with the error and the
  // way out. It never renders an empty canvas that reads as "nothing here".
  bootStatus(
    'This map could not be decoded in this browser: ' + (err && err.message ? err.message : err)
    + '  \u2014 nothing is wrong with the data; re-render with  metatron blueprint '
    + '<graph-dir> --no-compress  for a page that needs no decompression.',
    'boot-error');
  if (window.console && console.error) { console.error(err); }
}

function unpackIsland(packed) {
  if (!packed || packed.format !== PACK_FORMAT) {
    throw new Error('unknown data island format: ' + (packed && packed.format));
  }
  var pool = packed.pool, rawShapes = packed.shapes;
  var shapes = new Array(rawShapes.length), s, k, ids, keys;
  for (s = 0; s < rawShapes.length; s++) {
    ids = rawShapes[s];
    keys = new Array(ids.length);
    for (k = 0; k < ids.length; k++) { keys[k] = pool[ids[k]]; }
    shapes[s] = keys;
  }
  function decode(node) {
    if (typeof node === 'number') { return pool[node]; }
    var head = node[0], i, n = node.length;
    if (head === -1) {
      var arr = new Array(n - 1);
      for (i = 1; i < n; i++) { arr[i - 1] = decode(node[i]); }
      return arr;
    }
    var objKeys = shapes[head], out = {};
    for (i = 0; i < objKeys.length; i++) { out[objKeys[i]] = decode(node[i + 1]); }
    return out;
  }
  return decode(packed.root);
}

function base64Bytes(text) {
  var binary = atob(text);
  var n = binary.length, bytes = new Uint8Array(n), i;
  for (i = 0; i < n; i++) { bytes[i] = binary.charCodeAt(i) & 0xff; }
  return bytes;
}

function gunzipNative(bytes) {
  var stream = new DecompressionStream('gzip');
  var writer = stream.writable.getWriter();
  writer.write(bytes);
  writer.close();
  return new Response(stream.readable).arrayBuffer().then(function (buffer) {
    return new TextDecoder('utf-8').decode(new Uint8Array(buffer));
  });
}

// ---- RFC 1951 / 1952, for browsers without DecompressionStream ----------
// Decompression only. The algorithm is zlib's own `puff` reference shape:
// canonical Huffman decoded one bit at a time. Slower than the native
// path and only ever reached when there is no native path, which is why
// the page says which one it took.
var LENGTH_BASE = [3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258];
var LENGTH_EXTRA = [0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0];
var DIST_BASE = [1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,2049,3073,4097,6145,8193,12289,16385,24577];
var DIST_EXTRA = [0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13];
var CODE_LENGTH_ORDER = [16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15];

function huffmanTable(lengths, count) {
  var counts = new Int32Array(16), offsets = new Int32Array(16), i, total = 0;
  for (i = 0; i < count; i++) { counts[lengths[i]]++; }
  counts[0] = 0;
  for (i = 1; i < 16; i++) { offsets[i] = total; total += counts[i]; }
  var symbols = new Int32Array(total);
  for (i = 0; i < count; i++) { if (lengths[i]) { symbols[offsets[lengths[i]]++] = i; } }
  return { counts: counts, symbols: symbols };
}

var FIXED_LIT = null, FIXED_DIST = null;
function fixedTables() {
  if (FIXED_LIT) { return; }
  var lengths = new Int32Array(288), i;
  for (i = 0; i < 144; i++) { lengths[i] = 8; }
  for (i = 144; i < 256; i++) { lengths[i] = 9; }
  for (i = 256; i < 280; i++) { lengths[i] = 7; }
  for (i = 280; i < 288; i++) { lengths[i] = 8; }
  FIXED_LIT = huffmanTable(lengths, 288);
  var dists = new Int32Array(30);
  for (i = 0; i < 30; i++) { dists[i] = 5; }
  FIXED_DIST = huffmanTable(dists, 30);
}

function inflateRaw(input, start, sizeHint) {
  var out = new Uint8Array(sizeHint > 0 ? sizeHint : 65536), outLen = 0;
  var pos = start, bitbuf = 0, bitcnt = 0;

  function grow(extra) {
    if (outLen + extra <= out.length) { return; }
    var cap = out.length || 1024;
    while (cap < outLen + extra) { cap *= 2; }
    var bigger = new Uint8Array(cap);
    bigger.set(out.subarray(0, outLen));
    out = bigger;
  }
  function getBits(need) {
    while (bitcnt < need) {
      if (pos >= input.length) { throw new Error('truncated deflate stream'); }
      bitbuf |= input[pos++] << bitcnt;
      bitcnt += 8;
    }
    var value = bitbuf & ((1 << need) - 1);
    bitbuf >>>= need;
    bitcnt -= need;
    return value;
  }
  function decodeSymbol(table) {
    var code = 0, first = 0, index = 0, len, cnt;
    for (len = 1; len < 16; len++) {
      code |= getBits(1);
      cnt = table.counts[len];
      if (code - first < cnt) { return table.symbols[index + (code - first)]; }
      index += cnt;
      first = (first + cnt) << 1;
      code <<= 1;
    }
    throw new Error('invalid Huffman code');
  }
  function inflateBlock(litTable, distTable) {
    var symbol, extra, length, distance, from, i;
    do {
      symbol = decodeSymbol(litTable);
      if (symbol < 256) {
        grow(1);
        out[outLen++] = symbol;
      } else if (symbol > 256) {
        extra = symbol - 257;
        if (extra >= 29) { throw new Error('invalid length code'); }
        length = LENGTH_BASE[extra] + getBits(LENGTH_EXTRA[extra]);
        var dsym = decodeSymbol(distTable);
        if (dsym >= 30) { throw new Error('invalid distance code'); }
        distance = DIST_BASE[dsym] + getBits(DIST_EXTRA[dsym]);
        from = outLen - distance;
        if (from < 0) { throw new Error('distance too far back'); }
        grow(length);
        for (i = 0; i < length; i++) { out[outLen++] = out[from++]; }
      }
    } while (symbol !== 256);
  }

  fixedTables();
  var last;
  do {
    last = getBits(1);
    var type = getBits(2);
    if (type === 0) {
      bitbuf = 0; bitcnt = 0;
      var stored = input[pos] | (input[pos + 1] << 8);
      pos += 4;
      grow(stored);
      out.set(input.subarray(pos, pos + stored), outLen);
      outLen += stored;
      pos += stored;
    } else if (type === 1) {
      inflateBlock(FIXED_LIT, FIXED_DIST);
    } else if (type === 2) {
      var nlen = getBits(5) + 257, ndist = getBits(5) + 1, ncode = getBits(4) + 4, i;
      var clen = new Int32Array(19);
      for (i = 0; i < ncode; i++) { clen[CODE_LENGTH_ORDER[i]] = getBits(3); }
      var clenTable = huffmanTable(clen, 19);
      var lengths = new Int32Array(nlen + ndist), index = 0;
      while (index < nlen + ndist) {
        var symbol = decodeSymbol(clenTable), repeat, value;
        if (symbol < 16) {
          lengths[index++] = symbol;
        } else {
          if (symbol === 16) {
            if (index === 0) { throw new Error('repeat with no previous length'); }
            value = lengths[index - 1];
            repeat = 3 + getBits(2);
          } else if (symbol === 17) {
            value = 0; repeat = 3 + getBits(3);
          } else {
            value = 0; repeat = 11 + getBits(7);
          }
          if (index + repeat > nlen + ndist) { throw new Error('too many lengths'); }
          while (repeat--) { lengths[index++] = value; }
        }
      }
      var litLengths = lengths.subarray(0, nlen);
      var distLengths = lengths.subarray(nlen, nlen + ndist);
      inflateBlock(huffmanTable(litLengths, nlen), huffmanTable(distLengths, ndist));
    } else {
      throw new Error('invalid deflate block type');
    }
  } while (!last);
  return out.subarray(0, outLen);
}

function gunzipJS(input) {
  if (input.length < 18 || input[0] !== 31 || input[1] !== 139 || input[2] !== 8) {
    throw new Error('not a gzip stream');
  }
  var flags = input[3], pos = 10;
  if (flags & 4) { pos += 2 + (input[pos] | (input[pos + 1] << 8)); }
  if (flags & 8) { while (input[pos] !== 0) { pos++; } pos++; }
  if (flags & 16) { while (input[pos] !== 0) { pos++; } pos++; }
  if (flags & 2) { pos += 2; }
  var n = input.length;
  var isize = ((input[n - 4]) | (input[n - 3] << 8) | (input[n - 2] << 16) | (input[n - 1] << 24)) >>> 0;
  var out = inflateRaw(input, pos, isize);
  if (out.length !== isize) {
    throw new Error('gzip length mismatch: ' + out.length + ' vs ' + isize);
  }
  return out;
}

function boot() {
  var plainEl = document.getElementById('cascade-blueprint-data');
  var gzEl = document.getElementById('cascade-blueprint-data-gz');
  if (plainEl) {
    // `--no-compress`: the island is the packed JSON, in the clear.
    try {
      main(unpackIsland(JSON.parse(plainEl.textContent)));
    } catch (err) { bootFailed(err); }
    return;
  }
  if (!gzEl) { bootFailed(new Error('this page carries no data island')); return; }
  var native = (typeof DecompressionStream === 'function');
  if (!native) {
    bootStatus('This browser has no native gzip (DecompressionStream), so the map is '
      + 'being inflated in JavaScript instead. Nothing is missing; it is only slower. '
      + 'Chrome 80+, Edge 80+, Firefox 113+ and Safari 16.4+ take the fast path.',
      'boot-note');
  }
  var bytes;
  try { bytes = base64Bytes(gzEl.textContent); } catch (err) { bootFailed(err); return; }
  var pending = native
    ? gunzipNative(bytes)
    : new Promise(function (resolve) {
        // One turn of the event loop first, so the note above is painted
        // before the inflater blocks the main thread.
        setTimeout(function () {
          resolve(new TextDecoder('utf-8').decode(gunzipJS(bytes)));
        }, 0);
      });
  pending.then(function (text) {
    main(unpackIsland(JSON.parse(text)));
  }).catch(bootFailed);
}
"""

_SCRIPT = "(function () {\n'use strict';\n" + _SCRIPT_BODY + _DECODER + "\nboot();\n})();\n"


def render_blueprint(
    store: ArtifactStore,
    rstore: RuntimeStore | None = None,
    diff_store: ArtifactStore | None = None,
    *,
    report_link: str = "",
    view: BlueprintView | None = None,
    selection: Selection | None = None,
    compress: bool = True,
) -> str:
    """Render the whole offline blueprint page for one loaded artifact root.

    Pure string assembly -- no ``str.format``/f-string is applied to the CSS
    or JS blocks, so neither can be corrupted by their own ``{``/``}``
    characters. The only interpolated value is the JSON data island, and it
    goes through :func:`_safe_json`.
    """
    data = build_blueprint_data(
        store, rstore=rstore, diff_store=diff_store, report_link=report_link,
        view=view, selection=selection,
    )
    html, _island_bytes = _render_page(data, compress=compress)
    return html


def _render_page(data: dict[str, Any], *, compress: bool) -> tuple[str, int]:
    """``(html, island_bytes)`` -- the page, and what the browser must carry.

    The island is always interned (:func:`_pack_data`); *compress* adds
    gzip+base64 on top. Both are exactly reversible, so the two forms
    render the identical page from the identical object -- which is what
    `test_blueprint_compression.py` and the browser round-trip test check
    rather than assume.

    The second element is the **measured** island size, not a prediction:
    it is the number the size guard judges, because it is the number the
    browser pays.
    """
    packed = _pack_data(data)
    if compress:
        island_tag = '<script type="application/gzip-base64" id="cascade-blueprint-data-gz">'
        island_text = _compress_island(packed)
    else:
        island_tag = '<script type="application/json" id="cascade-blueprint-data">'
        island_text = _safe_json(packed)
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<title>CASCADE-MAP &mdash; blueprint canvas</title>",
        _STYLE,
        "</head><body>",
        _BODY,
        island_tag,
        island_text,
        "</script>",
        "<script>",
        _SCRIPT,
        "</script>",
        "</body></html>",
    ]
    return "".join(parts), len(island_text)


def render_blueprint_to_file(
    root: str | Path,
    out_path: str | Path,
    run_id: str | None = None,
    diff_root: str | Path | None = None,
    view: BlueprintView | None = None,
) -> tuple[ArtifactStore, Selection, SizeEstimate]:
    """Load the artifacts at *root* and write the blueprint canvas to *out_path*.

    Returns ``(store, selection, estimate)``: the loaded
    :class:`ArtifactStore` (as round 4 did), plus what this page actually
    carries and how large the estimator said it would be, so the caller can
    print the same numbers the page prints.

    **Raises** :class:`BlueprintTooLarge` -- before opening *out_path*, and
    therefore without writing, truncating or creating anything -- when the
    estimated data island exceeds ``view.size_limit_bytes`` and
    ``view.force`` is not set. Round 4 wrote 702 MB and told the owner to
    open it in a browser; a refusal they can override is honest, and a file
    that kills their browser is not.

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

    view = view if view is not None else BlueprintView()
    selection = select(store, view)
    estimate = estimate_island_bytes(store, selection, diff_store=diff_store, rstore=rstore)
    if estimate.over:
        # Nothing below this line has run: no mkdir, no open, no partial
        # file. The refusal is the whole effect.
        raise BlueprintTooLarge(estimate)

    report_candidate = out_path.parent / "index.html"
    report_link = report_candidate.name if report_candidate.exists() else ""

    data = build_blueprint_data(
        store, rstore=rstore, diff_store=diff_store, report_link=report_link,
        view=view, selection=selection,
    )
    html, island_bytes = _render_page(data, compress=view.compress)
    # Round 6: the guard's real decision, made on the measured island rather
    # than on a prediction of it. Still before anything is opened, so a
    # refusal still writes, truncates and creates nothing.
    estimate = replace(estimate, actual_bytes=island_bytes, measured=True)
    if estimate.over:
        raise BlueprintTooLarge(estimate)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return store, selection, estimate
