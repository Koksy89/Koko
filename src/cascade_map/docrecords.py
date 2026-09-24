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
from typing import Any, Callable, Mapping, Sequence

from cascade_map.contracts.interfaces import (
    AlignmentVerdict,
    DecisionPoint,
    DocRecord,
    Edge,
    EdgeKind,
    Element,
    ElementKind,
    Finding,
    LineageEdge,
    LineageKind,
    Method,
    NarrativeStep,
    OrderNode,
    Provenance,
    Slice,
    TraceEvent,
    VersionChange,
    combine,
)
from cascade_map.enrichment import EnrichmentClient
from cascade_map.parallel import Payload, StageReport, resolve_workers, run_stage

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
# Per-kind field applicability.
#
# Every ElementKind gets a record, and every required field in that record is
# either a real value or an explicit unknown() naming why the field does not
# apply to that kind. A PACKAGE has no signature; a CONFIG_KEY has no callers;
# a DATA_FILE has no enclosing Python module. None of that is a gap in the
# analysis -- it is a fact about the kind -- so it is never left as a raw
# empty string or an unexplained empty list. Going through every ElementKind
# here (13 total) is deliberate: a kind absent from today's fixture corpus
# must still get a correct record when the owner's engine has one.
# ---------------------------------------------------------------------------

# Kinds that are themselves the root of a Python module namespace, or above
# it. Their enclosing scope -- if any -- comes only from parent_id (the
# containing package); falling back to `element.module` would be circular.
_ROOT_OF_HIERARCHY_KINDS = frozenset({ElementKind.PACKAGE, ElementKind.MODULE})

# Kinds that live outside the Python module namespace entirely: a JSON config
# file, a key inside it, and a named feature that may be written from more
# than one module. None of these has a single enclosing Python module.
_NO_PYTHON_MODULE_KINDS = frozenset(
    {ElementKind.DATA_FILE, ElementKind.CONFIG_KEY, ElementKind.FEATURE}
)

# Only these can carry Python decorators.
_DECORATABLE_KINDS = frozenset(
    {ElementKind.CLASS, ElementKind.FUNCTION, ElementKind.METHOD, ElementKind.PROPERTY}
)

# Only these can appear as a source or target of a CALLS edge.
_CALL_GRAPH_KINDS = frozenset(
    {ElementKind.FUNCTION, ElementKind.METHOD, ElementKind.PROPERTY}
)

# Only these execute and so can read or write a feature. A FEATURE element is
# the data itself, not something that reads or writes; a CLASS is a template,
# not a running frame; PACKAGE/MODULE/IMPORT/PARAMETER/DATA_FILE/CONFIG_KEY/
# BLOB never appear as the source of a lineage edge.
_DATA_ROLE_KINDS = frozenset(
    {
        ElementKind.FUNCTION,
        ElementKind.METHOD,
        ElementKind.PROPERTY,
        ElementKind.MODULE,
        ElementKind.ASSIGNMENT,
    }
)


# ---------------------------------------------------------------------------
# Per-kind constant strings.
#
# Every reason string below is a function of the ElementKind alone, so on a
# 14.6 MB single-module target the old code formatted the same ten f-strings
# once per element -- hundreds of thousands of identical strings built and
# thrown away. Interning them per kind changes no byte of output (the strings
# are equal, and `unknown()` still allocates a fresh dict per record so no two
# records share a mutable value) and removes the formatting from the inner
# loop.
# ---------------------------------------------------------------------------


class _KindStrings:
    """The kind-dependent literals a record needs, computed once per kind."""

    __slots__ = (
        "kind_str",
        "no_signature",
        "outside_module_namespace",
        "no_module_recorded",
        "not_decoratable",
        "no_docstring",
        "not_ordered",
        "not_in_call_graph",
        "root_of_hierarchy",
        "no_enclosing_scope",
        "no_data_role",
    )

    def __init__(self, kind: ElementKind) -> None:
        self.kind_str = str(kind)
        self.no_signature = f"{kind} elements have no call signature"
        self.outside_module_namespace = (
            f"{kind} elements live outside the Python module namespace"
        )
        self.no_module_recorded = (
            f"no module recorded for this {kind}; expected one for this kind"
        )
        self.not_decoratable = f"{kind} elements cannot carry decorators"
        self.no_docstring = f"no docstring present for this {kind}"
        self.not_ordered = f"{kind} is not a member of any order node"
        self.not_in_call_graph = (
            f"{kind} elements do not appear in the CALLS call graph"
        )
        self.root_of_hierarchy = (
            f"{kind} is at the root of the hierarchy; it has no enclosing scope"
        )
        self.no_enclosing_scope = f"no enclosing scope recorded for this {kind}"
        self.no_data_role = (
            f"{kind} elements do not read or write features directly"
        )


_KIND_STRINGS: dict[ElementKind, _KindStrings] = {
    kind: _KindStrings(kind) for kind in ElementKind
}

#: Below this a chunk is not worth sending: the record build is microseconds
#: and the round trip is not.
_MIN_RECORDS_PER_CHUNK = 64

#: Chunks per worker. Above one, so an idle worker has something left to steal.
_CHUNKS_PER_WORKER = 8

#: Fewer chunks than this per worker and the pool costs more than it saves.
#: Measured on the fixture corpus, where 491 elements came back at 0.20x.
_MIN_CHUNKS_PER_WORKER = 4

#: WHETHER THE RECORDS STAGE USES THE POOL AT ALL. `False`, measured:
#:
#:   owner's engine, 10,165 elements
#:       1 worker   records stage 0.79s
#:       4 workers  records stage 1.38s   (0.3s of work, 0.8s in the pool)
#:
#: This stage is embarrassingly parallel and the pool below is correct -- the
#: byte-identity test drives it at 1, 2, 4 and 8 workers. It loses anyway,
#: because card 16 already made the build itself nearly free (117x, measured)
#: while the RESULT is 25 MB of documentation records that must cross back out
#: of the worker. When the work is 0.3s and the answer is 25 MB, no worker
#: count helps.
#:
#: The honest consequence: records is 0.2% of a 393s run on the owner's
#: engine. There is nothing here for workers to win.
_RECORDS_POOL_ENABLED = False

_NO_LINEAGE_REASON = "no lineage data supplied to the documentation builder"
_NO_RETURN_REASON = (
    "signature has no -> annotation and none could be inferred"
)
_NO_SLICE_REASON = {
    direction: f"no {direction} slice computed rooted at this element"
    for direction in ("backward", "forward")
}
_LINEAGE_WRITE_KINDS = frozenset(
    {
        LineageKind.ASSIGNS,
        LineageKind.COLUMN_WRITE,
        LineageKind.CONTAINER_WRITE,
        LineageKind.ATTRIBUTE_WRITE,
    }
)


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


#: How many times `records()` reports sub-progress over a whole build. Ten
#: calls on any corpus size, so the cost of reporting is constant and cannot
#: grow into the stage it is measuring.
_PROGRESS_STEPS = 10


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
        dependencies: Mapping[str, dict[str, Any]] | None = None,
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

        # Card 17's per-element dependency answer, passed in rather than
        # derived: this builder never reads a file. Optional exactly as the
        # runtime overlay is optional -- a run with no manifests and no
        # environment leaves it empty and the completeness gate must not fail
        # for that.
        self._dependencies: Mapping[str, dict[str, Any]] = dict(dependencies or {})

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

        self._changes_by_element: dict[str, set[str]] = {}
        for change in self._changes:
            for element_id in {change.before_id, change.after_id}:
                if element_id:
                    self._changes_by_element.setdefault(element_id, set()).add(change.id)

        self._adjacency: dict[str, list[str]] = {}
        for edge in self._edges:
            self._adjacency.setdefault(edge.source_id, []).append(edge.target_id)

        # ------------------------------------------------------------------
        # Prepared indexes.
        #
        # Every structure below answers a per-element question in O(1). Each
        # was previously a scan of a whole collection inside a loop that
        # already runs once per element -- the shape that made `records()`
        # quadratic on a single-module target. Building them here costs one
        # linear pass each and changes no value: the same first-match and the
        # same sort order are preserved deliberately below.
        # ------------------------------------------------------------------

        # Lineage: reads and writes per SOURCE element. The old code walked
        # every lineage edge for every element.
        self._saw_any_lineage = bool(self._lineage_edges)
        self._lineage_reads: dict[str, set[str]] = {}
        self._lineage_writes: dict[str, set[str]] = {}
        for lineage_edge in self._lineage_edges:
            if lineage_edge.kind is LineageKind.READS:
                bucket = self._lineage_reads
            elif lineage_edge.kind in _LINEAGE_WRITE_KINDS:
                bucket = self._lineage_writes
            else:
                continue
            target = bucket.get(lineage_edge.source_id)
            if target is None:
                bucket[lineage_edge.source_id] = {lineage_edge.target_id}
            else:
                target.add(lineage_edge.target_id)

        # Slices, keyed by (root, direction). FIRST wins, exactly as the old
        # linear search returned the first match in list order.
        self._slice_by_root: dict[tuple[str, str], Slice] = {}
        for sliced in self._slices:
            self._slice_by_root.setdefault((sliced.root_id, sliced.direction), sliced)

        # Reachability to a decision sink. `_sorted_adjacency` is the
        # `sorted(set(...))` the BFS used to recompute at every visit of every
        # node of every search; `_reaches_sink` is the reverse-reachable set,
        # which lets an element that cannot reach a sink answer without a
        # search at all. Neither changes a returned path: every node on a path
        # from a start to a sink is by definition in `_reaches_sink`, so
        # pruning the rest removes only branches that could never have won.
        self._sorted_adjacency: dict[str, tuple[str, ...]] = {}
        self._reaches_sink: frozenset[str] = frozenset()
        if self._decision_sink_ids:
            self._sorted_adjacency = {
                source: tuple(sorted(set(targets)))
                for source, targets in self._adjacency.items()
            }
            self._reaches_sink = self._reverse_reachable(self._decision_sink_ids)

        # Runtime overlay indexes, built only when there is an overlay.
        self._calls_by_element: dict[str, int] = {}
        self._value_events_by_element: dict[str, list[TraceEvent]] = {}
        self._verdict_by_element: dict[str, AlignmentVerdict] = {}
        self._step_by_element: dict[str, NarrativeStep] = {}
        if self._has_runtime:
            for event in self._trace_events:
                if str(event.kind) == "CALL":
                    self._calls_by_element[event.element_id] = (
                        self._calls_by_element.get(event.element_id, 0) + 1
                    )
                if event.values:
                    self._value_events_by_element.setdefault(
                        event.element_id, []
                    ).append(event)
            for events in self._value_events_by_element.values():
                events.sort(key=lambda ev: ev.event_id)
            # `min` keeps the first element with the smallest key, which is
            # what `sorted(...)[0]` returned.
            for verdict in self._alignment_verdicts:
                held = self._verdict_by_element.get(verdict.element_id)
                if held is None or verdict.id < held.id:
                    self._verdict_by_element[verdict.element_id] = verdict
            for step in self._narrative_steps:
                for element_id in step.element_ids:
                    held_step = self._step_by_element.get(element_id)
                    if held_step is None or step.sequence < held_step.sequence:
                        self._step_by_element[element_id] = step

    def _reverse_reachable(self, targets: frozenset[str]) -> frozenset[str]:
        """Every node with a path along `_adjacency` to some node in *targets*.

        A one-off reverse BFS. Anything outside this set provably has no path
        to a sink, so `_bfs_path` can answer it without searching.
        """
        incoming: dict[str, list[str]] = {}
        for source, outgoing in self._adjacency.items():
            for target in outgoing:
                incoming.setdefault(target, []).append(source)
        seen: set[str] = set(targets)
        stack: list[str] = list(targets)
        while stack:
            node = stack.pop()
            for predecessor in incoming.get(node, ()):
                if predecessor not in seen:
                    seen.add(predecessor)
                    stack.append(predecessor)
        return frozenset(seen)

    # -- assembly -----------------------------------------------------

    def records(
        self,
        *,
        on_progress: Callable[[int, int], None] | None = None,
        workers: int | None = None,
        report: StageReport | None = None,
    ) -> Sequence[DocRecord]:
        """One record per element, built over the work-stealing pool.

        A record is embarrassingly parallel: it depends on this builder's
        indexes and on nothing any other record produces. The unit is a whole
        element, the builder crosses to each worker ONCE (never once per
        element -- it holds the whole graph), and the consolidation below puts
        the results back in element-id order, which is the order the serial
        loop produced and is independent of which worker finished first.

        `on_progress(done, total)` is called at most `_PROGRESS_STEPS` times
        over the whole build, not once per element: the callback exists so a
        long stage looks alive, and firing it per element on a 400,000-element
        target would cost more than the reporting is worth.
        """
        ordered = sorted(self._elements, key=lambda e: e.id)
        stage = report if report is not None else StageReport(stage="records")
        asked = resolve_workers(workers)
        count = asked if _RECORDS_POOL_ENABLED else 1
        if not _RECORDS_POOL_ENABLED and asked > 1:
            stage.not_parallelised_reason = (
                "measured slower in a pool: the build is 0.3s of work and the "
                "answer is 25 MB of records that must cross back out of the "
                "worker (0.79s in one process, 1.38s over four). See "
                "_RECORDS_POOL_ENABLED"
            )
        total = len(ordered)
        chunks = _chunk_bounds(total, count)

        def tick(done: int, of: int) -> None:
            # Chunks, not elements: a chunk coming back is the only moment the
            # pool has news, and the callback exists so a long stage looks
            # alive rather than to count anything.
            assert on_progress is not None
            on_progress(min(total, round(total * done / max(1, of))), total)

        results: dict[str, list[DocRecord]] = run_stage(
            _records_unit,
            [(f"{index:08d}", bounds) for index, bounds in enumerate(chunks)],
            count,
            stage,
            payload=_RecordsPayload(self, ordered),
            on_unit=tick if on_progress is not None else None,
            # A few hundred records cost less to build than a pool costs to
            # start. Below this many chunks per worker the stage stays
            # in-process and the report says why, with the number.
            min_units_per_worker=_MIN_CHUNKS_PER_WORKER,
        )
        # SEQUENTIAL CONSOLIDATION, by chunk index, which is element-id order.
        # Nothing here depends on which worker finished first.
        built: list[DocRecord] = []
        for index in range(len(chunks)):
            built.extend(results.get(f"{index:08d}", ()))
        return tuple(built)

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
            dependencies=dict(self._dependencies.get(element.id, {})),
        )

    def _identity(self, element: Element) -> dict[str, Any]:
        kind = element.kind
        words = _KIND_STRINGS[kind]

        if element.signature:
            params, return_type = parse_signature(element.signature)
            parameters: Any = params
            return_val: Any = return_type or unknown(_NO_RETURN_REASON)
            signature_val: Any = element.signature
        else:
            reason = words.no_signature
            parameters = unknown(reason)
            return_val = unknown(reason)
            signature_val = unknown(reason)

        if kind in _NO_PYTHON_MODULE_KINDS:
            module_val: Any = unknown(words.outside_module_namespace)
        else:
            module_val = element.module or unknown(words.no_module_recorded)

        if kind in _DECORATABLE_KINDS:
            decorators_val: Any = list(element.decorators)
        else:
            decorators_val = unknown(words.not_decoratable)

        return {
            "id": element.id,
            "kind": words.kind_str,
            "name": element.name,
            "qualname": element.qualname or element.name,
            "module": module_val,
            "path": element.span.path,
            "line": element.span.line,
            "end_line": element.span.end_line if element.span.end_line is not None else element.span.line,
            "signature": signature_val,
            "parameters": parameters,
            "return_type": return_val,
            "decorators": decorators_val,
            "docstring": element.docstring or unknown(words.no_docstring),
        }

    def _cascade_position(self, element: Element) -> dict[str, Any]:
        kind = element.kind
        words = _KIND_STRINGS[kind]
        order = self._order_index.get(element.id)
        if order is None:
            order = unknown(words.not_ordered)

        if kind in _CALL_GRAPH_KINDS:
            callers: Any = sorted(set(self._callers.get(element.id, ())))
            callees: Any = sorted(set(self._callees.get(element.id, ())))
        else:
            reason = words.not_in_call_graph
            callers = unknown(reason)
            callees = unknown(reason)

        if element.parent_id:
            enclosing_scope: Any = element.parent_id
        elif kind in _ROOT_OF_HIERARCHY_KINDS:
            enclosing_scope = unknown(words.root_of_hierarchy)
        elif element.module and kind not in _NO_PYTHON_MODULE_KINDS:
            enclosing_scope = element.module
        else:
            enclosing_scope = unknown(words.no_enclosing_scope)

        return {
            "order": order,
            "callers": callers,
            "callees": callees,
            "enclosing_scope": enclosing_scope,
        }

    def _data_role(self, element: Element) -> dict[str, Any]:
        kind = element.kind
        if kind not in _DATA_ROLE_KINDS:
            reason = _KIND_STRINGS[kind].no_data_role
            features_read: Any = unknown(reason)
            features_written: Any = unknown(reason)
        elif self._saw_any_lineage:
            features_read = sorted(self._lineage_reads.get(element.id, ()))
            features_written = sorted(self._lineage_writes.get(element.id, ()))
        else:
            features_read = unknown(_NO_LINEAGE_REASON)
            features_written = unknown(_NO_LINEAGE_REASON)

        backward = self._slice_summary(element.id, "backward")
        forward = self._slice_summary(element.id, "forward")
        return {
            "features_read": features_read,
            "features_written": features_written,
            "backward_slice_summary": backward,
            "forward_slice_summary": forward,
        }

    def _slice_summary(self, element_id: str, direction: str) -> Any:
        sl = self._slice_by_root.get((element_id, direction))
        if sl is None:
            return unknown(_NO_SLICE_REASON[direction])
        return {
            "member_count": len(sl.member_ids),
            "barrier_count": len(sl.barrier_ids),
            "reaches_sink_ids": sorted(sl.reaches_sink_ids),
            "confidence": str(sl.confidence),
        }

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
        if start not in self._reaches_sink:
            # Provably no path, established once by the reverse BFS in
            # `__init__` instead of by exploring this element's whole
            # reachable component here.
            return None
        from collections import deque

        adjacency = self._sorted_adjacency
        reaches = self._reaches_sink
        visited = {start}
        queue: deque[list[str]] = deque([[start]])
        while queue:
            path = queue.popleft()
            node = path[-1]
            for neighbor in adjacency.get(node, ()):
                if neighbor in targets:
                    return path + [neighbor]
                if neighbor not in visited and neighbor in reaches:
                    visited.add(neighbor)
                    queue.append(path + [neighbor])
        return None

    def _runtime(self, element: Element) -> dict[str, Any]:
        observed_calls = self._calls_by_element.get(element.id, 0)

        value_events = self._value_events_by_element.get(element.id, ())
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

        verdict = self._verdict_by_element.get(element.id)
        alignment_verdict: Any
        if verdict is not None:
            alignment_verdict = str(verdict.verdict)
        else:
            alignment_verdict = unknown("no alignment verdict for this element")

        step = self._step_by_element.get(element.id)
        narrative_fragment: Any
        if step is not None:
            narrative_fragment = step.text
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


# ---------------------------------------------------------------------------
# The records unit of work -- whole elements, never a fragment of one
# ---------------------------------------------------------------------------


def _chunk_bounds(total: int, workers: int) -> list[tuple[int, int]]:
    """Contiguous half-open ranges over an already-sorted element list.

    Many more chunks than workers, deliberately: that is what keeps the shared
    queue able to *steal*. With one chunk per worker a single slow chunk
    leaves the rest idle, which is the fixed-batch failure this pool exists to
    avoid. Bounded below so a chunk is never so small that sending it costs
    more than building it.
    """
    if total <= 0:
        return []
    if workers <= 1:
        return [(0, total)]
    target = max(_MIN_RECORDS_PER_CHUNK, -(-total // (workers * _CHUNKS_PER_WORKER)))
    bounds: list[tuple[int, int]] = []
    start = 0
    while start < total:
        stop = min(total, start + target)
        bounds.append((start, stop))
        start = stop
    return bounds


class _RecordsWorker:
    """One worker's view: the builder plus the element order, sorted once."""

    __slots__ = ("builder", "ordered")

    def __init__(
        self, builder: "DocumentationBuilder", ordered: Sequence[Element]
    ) -> None:
        self.builder = builder
        self.ordered = ordered


class _RecordsPayload(Payload):
    """The whole builder, sent ONCE per worker.

    It holds the entire graph -- elements, edges, order, decisions, lineage,
    slices, findings -- so sending it with every task would cost orders of
    magnitude more than the records cost to build. Under the ``fork`` start
    method it is not even pickled: the child inherits it.
    """

    __slots__ = ("builder", "ordered")

    def __init__(
        self, builder: "DocumentationBuilder", ordered: Sequence[Element]
    ) -> None:
        self.builder = builder
        # Sorted ONCE, by the caller, and carried rather than re-derived: a
        # sort per worker on a 400,000-element target is a cost for nothing.
        self.ordered = tuple(ordered)

    def open(self) -> "_RecordsWorker":
        return _RecordsWorker(self.builder, self.ordered)


def _records_unit(
    worker: "_RecordsWorker", bounds: tuple[int, int]
) -> list[DocRecord]:
    """Whole records for one contiguous range of the sorted element list.

    Whole elements: a record is never split, because half a record would fail
    the completeness gate and a record that fails the gate is a failed run.
    """
    start, stop = bounds
    build = worker.builder._build_record
    return [build(element) for element in worker.ordered[start:stop]]
