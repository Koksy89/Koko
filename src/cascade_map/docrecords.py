"""Card 16 — documentation records and the completeness gate.

Assembles one :class:`~cascade_map.contracts.interfaces.DocRecord` per
inventoried element, entirely from graph objects produced by other cards
(elements, edges, order, lineage, slices, findings, changes, and -- when a
Mode A overlay exists -- trace events, alignment verdicts and narrative
steps). This module never opens a target file: everything it needs arrives
already parsed, as the ``DocsCard`` protocol and the project's constraint 8
require.

The completeness gate (`DocumentationBuilder.completeness_gate`) is not a
lint pass. A non-empty result means the run failed: some element has no
record, or some record has a required field left unfilled. "Unknown" is
accepted only in the explicit, reasoned form produced by :func:`unknown`;
a bare missing key or empty string is always a gate failure. There is no
parameter anywhere in this module that downgrades that to a warning.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Mapping, Sequence

from cascade_map.contracts.interfaces import (
    AlignmentVerdict,
    Confidence,
    DecisionPoint,
    DocRecord,
    Edge,
    EdgeKind,
    Element,
    Finding,
    LineageEdge,
    LineageKind,
    Method,
    NarrativeStep,
    OrderNode,
    Provenance,
    Slice,
    SourceSpan,
    TraceEvent,
    VersionChange,
    combine,
    make_id,
)
from cascade_map.enrichment import EnrichmentClient

# ---------------------------------------------------------------------------
# The explicit-unknown sentinel
# ---------------------------------------------------------------------------


def unknown(reason: str) -> dict[str, str]:
    """The only legitimate way a required field is filled with "nothing."

    A bare ``{}``, ``""`` or missing key is always a gate failure. This
    sentinel is a gate *pass* precisely because it names why the fact is not
    available -- the honest-gap principle applies to documentation records
    the same way it applies to edges and findings.
    """
    if not reason:
        raise ValueError("unknown() requires a non-empty reason")
    return {"status": "UNKNOWN", "reason": reason}


def _is_unknown(value: Any) -> bool:
    return isinstance(value, dict) and value.get("status") == "UNKNOWN"


def _filled(value: Any) -> bool:
    """True if *value* is a legitimately complete field value."""
    if value is None:
        return False
    if isinstance(value, str):
        return value != ""
    if _is_unknown(value):
        return bool(value.get("reason"))
    return True


IDENTITY_REQUIRED: tuple[str, ...] = (
    "id",
    "kind",
    "name",
    "qualname",
    "module",
    "path",
    "line",
    "end_line",
    "signature",
    "parameters",
    "return_type",
    "decorators",
    "docstring",
)

CASCADE_REQUIRED: tuple[str, ...] = ("order", "callers", "callees", "enclosing_scope")

DATA_ROLE_REQUIRED: tuple[str, ...] = (
    "features_read",
    "features_written",
    "backward_slice_summary",
    "forward_slice_summary",
)

DECISION_REQUIRED: tuple[str, ...] = ("reaches_sink", "paths")

RUNTIME_REQUIRED: tuple[str, ...] = (
    "observed_calls",
    "value_summary",
    "alignment_verdict",
    "narrative_fragment",
)


def _dict_complete(payload: Mapping[str, Any], required: Sequence[str]) -> bool:
    return all(key in payload and _filled(payload[key]) for key in required)


# ---------------------------------------------------------------------------
# Signature parsing -- text already produced by card 1's own AST walk. This
# is string-splitting the `signature` field the graph already carries, not
# re-parsing the target: the source of truth remains card 1's AST visit.
# ---------------------------------------------------------------------------

_PARAM_SPLIT = re.compile(r",\s*(?![^\[\]]*\])(?![^()]*\))")


def _split_top_level(inner: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in inner:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def parse_signature(signature: str) -> tuple[list[dict[str, str]], str]:
    """Best-effort split of a ``def``-style signature into parameters and a
    return annotation. Deterministic string parsing only; never touches the
    target file. Returns ``([], "")`` for anything that does not look like a
    parenthesized signature -- callers treat that as an explicit unknown.
    """
    open_at = signature.find("(")
    if open_at == -1:
        return [], ""
    depth = 0
    close_at = -1
    for idx in range(open_at, len(signature)):
        if signature[idx] == "(":
            depth += 1
        elif signature[idx] == ")":
            depth -= 1
            if depth == 0:
                close_at = idx
                break
    if close_at == -1:
        return [], ""
    inner = signature[open_at + 1 : close_at]
    return_type = ""
    arrow = signature.find("->", close_at)
    if arrow != -1:
        return_type = signature[arrow + 2 :].strip().rstrip(":").strip()
    params: list[dict[str, str]] = []
    for raw in _split_top_level(inner):
        if not raw or raw in ("self", "cls"):
            continue
        raw = raw.lstrip("*")
        name = raw
        annotation = ""
        default = ""
        if "=" in name:
            name, default = name.split("=", 1)
            name = name.strip()
            default = default.strip()
        if ":" in name:
            name, annotation = name.split(":", 1)
            name = name.strip()
            annotation = annotation.strip()
        if not name:
            continue
        params.append(
            {
                "name": name,
                "annotation": annotation or "UNANNOTATED",
                "default": default,
            }
        )
    return params, return_type


# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------


class DocumentationBuilder:
    """Implements the ``DocsCard`` protocol.

    Every input is a graph object supplied by another card; this class never
    reads a file. Construct one instance per run with whatever cards 1-6 (and
    optionally 11-14) produced, call :meth:`records`, and run
    :meth:`completeness_gate` on the result before anything is written out.
    """

    def __init__(
        self,
        elements: Sequence[Element],
        edges: Sequence[Edge] = (),
        order_nodes: Sequence[OrderNode] = (),
        decisions: Sequence[DecisionPoint] = (),
        lineage_edges: Sequence[LineageEdge] = (),
        slices: Sequence[Slice] = (),
        findings: Sequence[Finding] = (),
        changes: Sequence[VersionChange] = (),
        decision_sink_ids: Sequence[str] = (),
        trace_events: Sequence[TraceEvent] = (),
        alignment_verdicts: Sequence[AlignmentVerdict] = (),
        narrative_steps: Sequence[NarrativeStep] = (),
        has_runtime_overlay: bool = False,
    ) -> None:
        self._elements = list(elements)
        self._edges = list(edges)
        self._order_nodes = list(order_nodes)
        self._decisions = list(decisions)
        self._lineage_edges = list(lineage_edges)
        self._slices = list(slices)
        self._findings = list(findings)
        self._changes = list(changes)
        self._decision_sink_ids = frozenset(decision_sink_ids)
        self._trace_events = list(trace_events)
        self._alignment_verdicts = list(alignment_verdicts)
        self._narrative_steps = list(narrative_steps)
        self._has_runtime = bool(
            has_runtime_overlay or trace_events or alignment_verdicts or narrative_steps
        )

        self._element_by_id = {el.id: el for el in self._elements}
        self._callers: dict[str, list[str]] = {}
        self._callees: dict[str, list[str]] = {}
        for edge in self._edges:
            if edge.kind is EdgeKind.CALLS:
                self._callees.setdefault(edge.source_id, []).append(edge.target_id)
                self._callers.setdefault(edge.target_id, []).append(edge.source_id)

        self._order_index: dict[str, str] = {}
        for node in self._order_nodes:
            for position, element_id in enumerate(node.element_ids):
                self._order_index[element_id] = f"{node.id}[{position}]:{node.kind}"

        self._findings_by_element: dict[str, list[str]] = {}
        for finding in self._findings:
            self._findings_by_element.setdefault(finding.element_id, []).append(finding.id)

        self._changes_by_element: dict[str, list[str]] = {}
        for change in self._changes:
            for element_id in (change.before_id, change.after_id):
                if element_id:
                    self._changes_by_element.setdefault(element_id, []).append(change.id)

        self._adjacency: dict[str, list[str]] = {}
        for edge in self._edges:
            self._adjacency.setdefault(edge.source_id, []).append(edge.target_id)

    # -- assembly -----------------------------------------------------

    def records(self) -> Sequence[DocRecord]:
        return tuple(self._build_record(el) for el in sorted(self._elements, key=lambda e: e.id))

    def _build_record(self, element: Element) -> DocRecord:
        identity = self._identity(element)
        cascade_position = self._cascade_position(element)
        data_role = self._data_role(element)
        decision_relevance = self._decision_relevance(element)
        runtime = self._runtime(element) if self._has_runtime else {}

        finding_ids = tuple(sorted(self._findings_by_element.get(element.id, ())))
        change_ids = tuple(sorted(self._changes_by_element.get(element.id, ())))

        provenance = Provenance(
            method=Method.STRUCTURAL_MATCH,
            confidence=combine(element.provenance.confidence),
            span=element.span,
            note="assembled from the graph; not re-derived from source",
        )

        return DocRecord(
            id=f"doc::{element.id}",
            element_id=element.id,
            identity=identity,
            cascade_position=cascade_position,
            data_role=data_role,
            decision_relevance=decision_relevance,
            finding_ids=finding_ids,
            change_ids=change_ids,
            provenance=provenance,
            runtime=runtime,
        )

    def _identity(self, element: Element) -> dict[str, Any]:
        params, return_type = parse_signature(element.signature)
        if element.signature:
            parameters: Any = params
            return_val: Any = return_type or unknown(
                "signature has no -> annotation and none could be inferred"
            )
        else:
            parameters = unknown(f"no signature recorded for kind {element.kind}")
            return_val = unknown(f"no signature recorded for kind {element.kind}")
        return {
            "id": element.id,
            "kind": str(element.kind),
            "name": element.name,
            "qualname": element.qualname or element.name,
            "module": element.module,
            "path": element.span.path,
            "line": element.span.line,
            "end_line": element.span.end_line if element.span.end_line is not None else element.span.line,
            "signature": element.signature or unknown("no signature applicable to this kind"),
            "parameters": parameters,
            "return_type": return_val,
            "decorators": list(element.decorators),
            "docstring": element.docstring or unknown("no docstring present in source"),
        }

    def _cascade_position(self, element: Element) -> dict[str, Any]:
        order = self._order_index.get(
            element.id,
            None,
        )
        if order is None:
            order = unknown(f"element of kind {element.kind} is not a member of any order node")
        callers = sorted(set(self._callers.get(element.id, ())))
        callees = sorted(set(self._callees.get(element.id, ())))
        enclosing_scope = element.parent_id or element.module
        return {
            "order": order,
            "callers": callers,
            "callees": callees,
            "enclosing_scope": enclosing_scope,
        }

    def _data_role(self, element: Element) -> dict[str, Any]:
        reads: set[str] = set()
        writes: set[str] = set()
        write_kinds = {
            LineageKind.ASSIGNS,
            LineageKind.COLUMN_WRITE,
            LineageKind.CONTAINER_WRITE,
            LineageKind.ATTRIBUTE_WRITE,
        }
        saw_any_lineage = bool(self._lineage_edges)
        for edge in self._lineage_edges:
            if edge.source_id != element.id:
                continue
            if edge.kind is LineageKind.READS:
                reads.add(edge.target_id)
            elif edge.kind in write_kinds:
                writes.add(edge.target_id)

        features_read: Any = sorted(reads) if saw_any_lineage else unknown(
            "no lineage data supplied to the documentation builder"
        )
        features_written: Any = sorted(writes) if saw_any_lineage else unknown(
            "no lineage data supplied to the documentation builder"
        )

        backward = self._slice_summary(element.id, "backward")
        forward = self._slice_summary(element.id, "forward")
        return {
            "features_read": features_read,
            "features_written": features_written,
            "backward_slice_summary": backward,
            "forward_slice_summary": forward,
        }

    def _slice_summary(self, element_id: str, direction: str) -> Any:
        for sl in self._slices:
            if sl.root_id == element_id and sl.direction == direction:
                return {
                    "member_count": len(sl.member_ids),
                    "barrier_count": len(sl.barrier_ids),
                    "reaches_sink_ids": sorted(sl.reaches_sink_ids),
                    "confidence": str(sl.confidence),
                }
        return unknown(f"no {direction} slice computed rooted at this element")

    def _decision_relevance(self, element: Element) -> dict[str, Any]:
        if not self._decision_sink_ids:
            reason = "no decision sink configured (Q1 unresolved); reachability is UNKNOWN, not false"
            return {"reaches_sink": unknown(reason), "paths": unknown(reason)}

        path = self._bfs_path(element.id, self._decision_sink_ids)
        if path is None:
            return {"reaches_sink": False, "paths": []}
        return {"reaches_sink": True, "paths": [path]}

    def _bfs_path(self, start: str, targets: frozenset[str]) -> list[str] | None:
        if start in targets:
            return [start]
        from collections import deque

        visited = {start}
        queue: deque[list[str]] = deque([[start]])
        while queue:
            path = queue.popleft()
            node = path[-1]
            for neighbor in sorted(set(self._adjacency.get(node, ()))):
                if neighbor in targets:
                    return path + [neighbor]
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(path + [neighbor])
        return None

    def _runtime(self, element: Element) -> dict[str, Any]:
        calls = [
            ev
            for ev in self._trace_events
            if ev.element_id == element.id and str(ev.kind) == "CALL"
        ]
        observed_calls = len(calls)

        value_events = sorted(
            (ev for ev in self._trace_events if ev.element_id == element.id and ev.values),
            key=lambda ev: ev.event_id,
        )
        if value_events:
            value_summary: Any = [
                {
                    "event_id": ev.event_id,
                    "captures": sorted(
                        {
                            f"{name}:{cap.status}:{cap.type_name}"
                            for name, cap in ev.values.items()
                        }
                    ),
                }
                for ev in value_events
            ]
        else:
            value_summary = unknown("no value captures recorded for this element")

        verdicts = sorted(
            (v for v in self._alignment_verdicts if v.element_id == element.id),
            key=lambda v: v.id,
        )
        alignment_verdict: Any
        if verdicts:
            alignment_verdict = str(verdicts[0].verdict)
        else:
            alignment_verdict = unknown("no alignment verdict for this element")

        steps = sorted(
            (s for s in self._narrative_steps if element.id in s.element_ids),
            key=lambda s: s.sequence,
        )
        narrative_fragment: Any
        if steps:
            narrative_fragment = steps[0].text
        else:
            narrative_fragment = unknown("no narrative step references this element")

        return {
            "observed_calls": observed_calls,
            "value_summary": value_summary,
            "alignment_verdict": alignment_verdict,
            "narrative_fragment": narrative_fragment,
        }

    # -- the gate -------------------------------------------------------

    def completeness_gate(self, records: Sequence[DocRecord]) -> Sequence[str]:
        """Return the sorted element IDs of every completeness failure.

        Checks two things: every element this builder was constructed with
        has a record among *records*, and every record present has every
        required field filled (an explicit :func:`unknown` counts as filled;
        a missing key or empty string does not). Non-empty return means the
        run fails -- there is no parameter here to make that a warning.
        """
        offenders: set[str] = set()
        by_element = {r.element_id: r for r in records}

        for element in self._elements:
            record = by_element.get(element.id)
            if record is None:
                offenders.add(element.id)
                continue
            if not self._record_complete(record):
                offenders.add(element.id)

        return tuple(sorted(offenders))

    def _record_complete(self, record: DocRecord) -> bool:
        if not record.id or not record.element_id:
            return False
        if record.provenance is None:
            return False
        if not _dict_complete(record.identity, IDENTITY_REQUIRED):
            return False
        if not _dict_complete(record.cascade_position, CASCADE_REQUIRED):
            return False
        if not _dict_complete(record.data_role, DATA_ROLE_REQUIRED):
            return False
        if not _dict_complete(record.decision_relevance, DECISION_REQUIRED):
            return False
        if self._has_runtime and not _dict_complete(record.runtime, RUNTIME_REQUIRED):
            return False
        return True

    # -- enrichment (optional, prose-only) -------------------------------

    def enrich(
        self, records: Sequence[DocRecord], client: EnrichmentClient
    ) -> Sequence[DocRecord]:
        """Attach model prose to a copy of *records*. Never touches any other
        field, and returns *records* unchanged (same values, new tuple) when
        the client is disabled -- so the deterministic artifact is
        byte-identical whether or not this method is even called.
        """
        if not client.enabled:
            return tuple(records)
        enriched: list[DocRecord] = []
        for record in records:
            result = client.summarize_element(record.identity)
            enriched.append(replace(record, model_prose=result.prose, model_id=result.model_id))
        return tuple(enriched)
