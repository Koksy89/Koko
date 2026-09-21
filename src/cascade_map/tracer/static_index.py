"""The static graph, indexed for keying runtime events onto it.

This module is the whole reason the runtime evidence is an *overlay* rather
than a second graph: every event is looked up here and comes back with a static
element ID, or comes back with a refusal that names the code location and the
reason. Nothing in this module writes to the static graph.

Mapping keys on ``(file, qualname)`` and is confirmed by line containment.
It deliberately does not key on line number alone: element IDs are structural
(`make_id`), so a reformat must not strand every event.
"""

from __future__ import annotations

import linecache
import os
import posixpath
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from cascade_map.contracts.interfaces import (
    CFGBlock,
    CFGEdge,
    BlockKind,
    Confidence,
    DecisionPoint,
    Edge,
    EdgeKind,
    Element,
    ElementKind,
    LineageEdge,
    Method,
    OrderKind,
    OrderNode,
    SourceSpan,
)

__all__ = ["CodeLocation", "EventMapping", "BranchOutcome", "StaticIndex"]

FEATURE_PREFIX = "@feature:"


@dataclass(frozen=True, slots=True)
class CodeLocation:
    """Where a runtime event happened, as the interpreter reported it."""

    path: str
    """Relative to the analysed root when the file is under it; otherwise a
    machine-independent stand-in such as ``<stdlib>/random.py``. Absolute paths
    never reach an artifact -- ARCHITECTURE keeps them in run_meta.json."""

    qualname: str
    line: int
    first_line: int = 0
    under_root: bool = False
    synthetic: bool = False
    """True for code objects with no file: ``<string>``, ``<stdin>``, an
    ``exec`` of generated source. These are the run_unmapped shape."""


@dataclass(frozen=True, slots=True)
class EventMapping:
    """The result of keying one code location onto the static graph."""

    element_id: str
    method: Method
    confidence: Confidence
    reason: str = ""
    """Empty when mapped. When ``element_id`` is empty this says why, and the
    event is emitted as UNMAPPED rather than dropped."""


@dataclass(frozen=True, slots=True)
class BranchOutcome:
    label: str
    target_id: str
    confidence: Confidence
    reason: str = ""


def _norm(path: str) -> str:
    return posixpath.normpath(path.replace(os.sep, "/"))


class StaticIndex:
    """Read-only index over the Mode B artifacts card 12 overlays."""

    def __init__(
        self,
        root: str,
        elements: Sequence[Element] = (),
        edges: Sequence[Edge] = (),
        decisions: Sequence[DecisionPoint] = (),
        cfg_blocks: Sequence[CFGBlock] = (),
        cfg_edges: Sequence[CFGEdge] = (),
        lineage: Sequence[LineageEdge] = (),
        order_nodes: Sequence[OrderNode] = (),
        sink_element_ids: Iterable[str] = (),
    ) -> None:
        self.root = os.path.abspath(root)
        self.elements = tuple(elements)
        self.edges = tuple(edges)
        self.decisions = tuple(decisions)
        self.cfg_blocks = tuple(cfg_blocks)
        self.cfg_edges = tuple(cfg_edges)
        self.lineage = tuple(lineage)
        self.order_nodes = tuple(order_nodes)

        self._by_key: dict[tuple[str, str], list[Element]] = {}
        self._by_id: dict[str, Element] = {}
        for element in self.elements:
            self._by_id[element.id] = element
            key = (_norm(element.span.path), _qualkey(element))
            self._by_key.setdefault(key, []).append(element)
        for bucket in self._by_key.values():
            bucket.sort(key=lambda e: (e.span.line, e.id))

        self._blocks_by_element: dict[str, list[CFGBlock]] = {}
        for block in self.cfg_blocks:
            self._blocks_by_element.setdefault(block.element_id, []).append(block)
        self._block_by_id = {block.id: block for block in self.cfg_blocks}
        self._succ: dict[str, list[CFGEdge]] = {}
        for edge in self.cfg_edges:
            self._succ.setdefault(edge.source_id, []).append(edge)
        for bucket in self._succ.values():
            bucket.sort(key=lambda e: e.id)

        self._decisions_by_element: dict[str, list[DecisionPoint]] = {}
        for decision in self.decisions:
            self._decisions_by_element.setdefault(decision.element_id, []).append(decision)
        for bucket in self._decisions_by_element.values():
            bucket.sort(key=lambda d: d.id)

        declared = {d.element_id for d in self.decisions if d.is_sink}
        self.sink_element_ids: frozenset[str] = frozenset(declared | set(sink_element_ids))
        self.sink_decision_ids: frozenset[str] = frozenset(
            d.id for d in self.decisions if d.is_sink
        )

        self._branch_lines: dict[str, dict[int, str]] = {}
        self._handler_lines: dict[str, set[int]] = {}
        self._feature_lines: dict[str, dict[int, tuple[str, ...]]] = {}
        self._build_line_plans()

    # -- paths -------------------------------------------------------------

    def relocate(self, filename: str) -> CodeLocation:
        """Turn an interpreter filename into a machine-independent location."""
        if not filename or filename.startswith("<"):
            return CodeLocation(path=filename or "<unknown>", qualname="", line=0, synthetic=True)
        absolute = os.path.abspath(filename)
        if absolute == self.root or absolute.startswith(self.root + os.sep):
            return CodeLocation(
                path=_norm(os.path.relpath(absolute, self.root)),
                qualname="",
                line=0,
                under_root=True,
            )
        return CodeLocation(path=_external(absolute), qualname="", line=0)

    # -- mapping -----------------------------------------------------------

    def map_code(self, location: CodeLocation) -> EventMapping:
        """Key a code location onto a static element, or say why it did not."""
        if location.synthetic:
            return EventMapping(
                "",
                Method.RUNTIME_OBSERVED,
                Confidence.UNKNOWN,
                f"dynamically created code object with no source file "
                f"({location.path!r}, qualname {location.qualname!r}): the static analysis "
                "never saw this code",
            )
        if not location.under_root:
            return EventMapping(
                "",
                Method.RUNTIME_OBSERVED,
                Confidence.UNKNOWN,
                f"code at {location.path} lies outside the analysed root, so no static "
                "element exists for it",
            )
        key = (_norm(location.path), _normalise_qualname(location.qualname))
        candidates = self._by_key.get(key, [])
        if not candidates:
            return EventMapping(
                "",
                Method.RUNTIME_OBSERVED,
                Confidence.UNKNOWN,
                f"no static element at {location.path} with qualname "
                f"{_normalise_qualname(location.qualname)!r} (first line "
                f"{location.first_line}); the static analysis missed this element",
            )
        if len(candidates) == 1:
            element = candidates[0]
            if _covers(element.span, location.first_line):
                return EventMapping(element.id, Method.RUNTIME_OBSERVED, Confidence.RESOLVED)
            return EventMapping(
                element.id,
                Method.RUNTIME_OBSERVED,
                Confidence.PROBABLE,
                f"only candidate for the qualname, but its span "
                f"({element.span.line}-{element.span.end_line}) does not cover the running "
                f"code's first line {location.first_line}",
            )
        covering = [e for e in candidates if _covers(e.span, location.first_line)]
        if len(covering) == 1:
            return EventMapping(covering[0].id, Method.RUNTIME_OBSERVED, Confidence.PROBABLE)
        return EventMapping(
            "",
            Method.RUNTIME_OBSERVED,
            Confidence.UNKNOWN,
            f"{len(candidates)} static elements share qualname "
            f"{_normalise_qualname(location.qualname)!r} in {location.path} and "
            f"{len(covering)} of them cover line {location.first_line}: ambiguous, so no "
            "element is claimed. Candidates: "
            + ",".join(sorted(e.id for e in candidates)),
        )

    def element(self, element_id: str) -> Element | None:
        return self._by_id.get(element_id)

    # -- line plans --------------------------------------------------------

    def _build_line_plans(self) -> None:
        for block in self.cfg_blocks:
            path = _norm(block.span.path)
            if block.kind in (BlockKind.BRANCH, BlockKind.LOOP_HEAD):
                self._branch_lines.setdefault(path, {})[block.span.line] = block.id
            if block.kind == BlockKind.HANDLER:
                for line in _lines(block.span):
                    self._handler_lines.setdefault(path, set()).add(line)
        for edge in self.lineage:
            if not edge.target_id.startswith(FEATURE_PREFIX) or edge.span is None:
                continue
            path = _norm(edge.span.path)
            bucket = self._feature_lines.setdefault(path, {})
            existing = bucket.get(edge.span.line, ())
            if edge.target_id not in existing:
                bucket[edge.span.line] = tuple(sorted(existing + (edge.target_id,)))

    def branch_lines(self, path: str) -> dict[int, str]:
        return dict(self._branch_lines.get(_norm(path), {}))

    def handler_lines(self, path: str) -> set[int]:
        return set(self._handler_lines.get(_norm(path), set()))

    def feature_lines(self, path: str) -> dict[int, tuple[str, ...]]:
        return dict(self._feature_lines.get(_norm(path), {}))

    def traces_lines(self, path: str) -> bool:
        """Whether line events are worth paying for in this file."""
        key = _norm(path)
        return bool(
            self._branch_lines.get(key)
            or self._feature_lines.get(key)
            or self._handler_lines.get(key)
        )

    # -- decisions and branches -------------------------------------------

    def decision_at(self, element_id: str, line: int, path: str) -> DecisionPoint | None:
        """Which decision point sits on this branch line, if any."""
        points = self._decisions_by_element.get(element_id, [])
        if not points:
            return None
        text = linecache.getline(os.path.join(self.root, _norm(path)), line).strip()
        if text:
            matches = [p for p in points if p.condition_source and p.condition_source in text]
            if len(matches) == 1:
                return matches[0]
        if len(points) == 1:
            return points[0]
        return None

    def reads_names(self, decision: DecisionPoint | None) -> tuple[str, ...]:
        """Plain names a decision reads, for lookup in the running frame."""
        if decision is None:
            return ()
        names = []
        for read_id in decision.reads_ids:
            names.append(_leaf_name(read_id))
        return tuple(sorted({n for n in names if n}))

    def branch_outcome(
        self, block_id: str, next_line: int, decision: DecisionPoint | None
    ) -> BranchOutcome:
        """Which way a branch actually went, given the next line that ran."""
        successors = self._succ.get(block_id, [])
        matched = [
            edge
            for edge in successors
            if (block := self._block_by_id.get(edge.target_id)) is not None
            and _covers(block.span, next_line)
        ]
        if len(matched) == 1:
            edge = matched[0]
            target_block = self._block_by_id[edge.target_id]
            label = _edge_label(edge)
            if decision is not None:
                for outcome_label, target_id in decision.outcomes:
                    if target_id in (edge.target_id, target_block.element_id):
                        return BranchOutcome(outcome_label, target_id, Confidence.RESOLVED)
            return BranchOutcome(label, edge.target_id, Confidence.PROBABLE)
        if not successors:
            return BranchOutcome(
                f"line:{next_line}",
                "",
                Confidence.UNKNOWN,
                f"branch block {block_id} has no CFG successors in the static graph; "
                f"the run continued at line {next_line}",
            )
        return BranchOutcome(
            f"line:{next_line}",
            "",
            Confidence.UNKNOWN,
            f"{len(matched)} of {len(successors)} CFG successors of {block_id} cover line "
            f"{next_line}: the branch taken cannot be named from the static graph",
        )

    # -- contradiction inputs ---------------------------------------------

    def call_pairs(self) -> dict[tuple[str, str], str]:
        """Static CALLS edges as ``(source, target) -> edge id``."""
        pairs: dict[tuple[str, str], str] = {}
        for edge in self.edges:
            if edge.kind is EdgeKind.CALLS:
                pairs.setdefault((edge.source_id, edge.target_id), edge.id)
        return pairs

    def sequence_pairs(self) -> list[tuple[str, str, str]]:
        """Ordered pairs the static graph claims: ``(before, after, order node)``."""
        pairs: list[tuple[str, str, str]] = []
        for node in sorted(self.order_nodes, key=lambda n: n.id):
            if node.kind is not OrderKind.SEQUENCE:
                continue
            members = list(node.element_ids)
            for index, before in enumerate(members):
                for after in members[index + 1 :]:
                    pairs.append((before, after, node.id))
        return pairs

    def callable_element_ids(self) -> frozenset[str]:
        return frozenset(
            element.id
            for element in self.elements
            if element.kind in (ElementKind.FUNCTION, ElementKind.METHOD, ElementKind.PROPERTY)
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _external(absolute: str) -> str:
    """A stand-in path for code outside the root, stable across machines."""
    parts = absolute.replace(os.sep, "/").split("/")
    for marker in ("site-packages", "dist-packages"):
        if marker in parts:
            return "<" + marker + ">/" + "/".join(parts[parts.index(marker) + 1 :])
    for index, part in enumerate(parts):
        if part.startswith("python3.") and index + 1 < len(parts):
            return "<stdlib>/" + "/".join(parts[index + 1 :])
    return "<external>/" + parts[-1]


def _qualkey(element: Element) -> str:
    if element.kind in (ElementKind.MODULE, ElementKind.PACKAGE):
        return "<module>"
    return _normalise_qualname(element.qualname or element.name)


def _normalise_qualname(qualname: str) -> str:
    if not qualname:
        return "<module>"
    return ".".join(part for part in qualname.split(".") if part != "<locals>")


def _leaf_name(element_id: str) -> str:
    if element_id.startswith(FEATURE_PREFIX):
        return element_id[len(FEATURE_PREFIX) :]
    tail = element_id.split("::")[-1]
    tail = tail.split("#")[0]
    return tail.split(".")[-1]


def _covers(span: SourceSpan, line: int) -> bool:
    if line <= 0:
        return False
    end = span.end_line if span.end_line is not None else span.line
    return span.line <= line <= max(end, span.line)


def _lines(span: SourceSpan) -> range:
    end = span.end_line if span.end_line is not None else span.line
    return range(span.line, max(end, span.line) + 1)


def _edge_label(edge: CFGEdge) -> str:
    if edge.taken_when is True:
        return "true"
    if edge.taken_when is False:
        return "false"
    if edge.condition:
        return edge.condition
    return edge.target_id
