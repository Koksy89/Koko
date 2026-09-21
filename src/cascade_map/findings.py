"""Card 5 -- unplugged detection and hints.

Derives :class:`Finding` records entirely from the graph cards 1-4 already
built: elements, edges, unresolved records, CFG blocks/edges, decision
points, lineage edges and slices. This module never re-parses the target --
doing so would be a second source of truth and a defect per the card's
acceptance criteria.

Precision over recall throughout: every detector here would rather emit
nothing than emit a wrong finding, and every finding carries a non-empty
``evidence_ids`` chain of edge, slice or element IDs the owner can jump to.

Confidence is never invented. It is always ``combine()`` of the provenance
the finding rests on, per the contract in
``src/cascade_map/contracts/interfaces.py``.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from typing import Mapping, Sequence

from cascade_map.contracts.interfaces import (
    Barrier,
    BlockKind,
    CFGBlock,
    CFGEdge,
    Confidence,
    DecisionPoint,
    Edge,
    EdgeKind,
    Element,
    ElementKind,
    Finding,
    FindingKind,
    LineageEdge,
    LineageKind,
    Method,
    Provenance,
    Slice,
    SourceSpan,
    Unresolved,
    UnresolvedReason,
    combine,
)

__all__ = ["Findings"]

# Edge kinds that constitute "wiring" for reachability purposes: if one of
# these connects A to B, running A can reach B.
_STRUCTURAL_EDGE_KINDS = {
    EdgeKind.CALLS,
    EdgeKind.INSTANTIATES,
    EdgeKind.REGISTERS,
    EdgeKind.DECORATES,
    EdgeKind.INHERITS,
    EdgeKind.CONFIGURES,
    EdgeKind.REFERENCES,
    EdgeKind.IMPORTS,
}

# Element kinds worth reporting as unreachable / decision-irrelevant. Leaf
# facts like PARAMETER, IMPORT or ASSIGNMENT are too fine-grained to report
# on their own -- their containing FUNCTION/METHOD/CLASS carries the finding.
_REPORTABLE_KINDS = {
    ElementKind.MODULE,
    ElementKind.CLASS,
    ElementKind.FUNCTION,
    ElementKind.METHOD,
    ElementKind.PROPERTY,
}

_CONFIG_EXTENSIONS = (".json", ".yaml", ".yml", ".ini", ".cfg", ".toml")

_WRITE_LINEAGE_KINDS = {
    LineageKind.ASSIGNS,
    LineageKind.COLUMN_WRITE,
    LineageKind.CONTAINER_WRITE,
    LineageKind.ATTRIBUTE_WRITE,
    LineageKind.RETURNS,
}

_ORDINAL_RE = re.compile(r"#(\d+)$")

_EMPTY_SPAN = SourceSpan(path="", line=0)


def _ordinal_of(element_id: str) -> int:
    match = _ORDINAL_RE.search(element_id)
    return int(match.group(1)) if match else 1


class Findings:
    """Implements ``FindingsCard``.

    Constructed directly from the sequences cards 1-4 emit. There is no
    ``root`` parameter and no filesystem access: everything comes from the
    graph passed in.
    """

    def __init__(
        self,
        *,
        elements: Sequence[Element],
        edges: Sequence[Edge] = (),
        unresolved: Sequence[Unresolved] = (),
        cfg_blocks: Sequence[CFGBlock] = (),
        cfg_edges: Sequence[CFGEdge] = (),
        decision_points: Sequence[DecisionPoint] = (),
        lineage_edges: Sequence[LineageEdge] = (),
        barriers: Sequence[Barrier] = (),
        slices: Sequence[Slice] = (),
        entry_ids: Sequence[str] = (),
    ) -> None:
        self._elements = list(elements)
        self._by_id: Mapping[str, Element] = {e.id: e for e in self._elements}
        self._edges = list(edges)
        self._unresolved = list(unresolved)
        self._cfg_blocks = list(cfg_blocks)
        self._cfg_edges = list(cfg_edges)
        self._decision_points = list(decision_points)
        self._lineage_edges = list(lineage_edges)
        self._barriers = list(barriers)
        self._slices = list(slices)
        # Only entries that name a real element are usable as BFS roots. An
        # entry_id naming nothing in the graph is silently useless for
        # reachability, not a crash.
        self._entry_ids = tuple(sorted(i for i in set(entry_ids) if i in self._by_id))

    # -- public API ---------------------------------------------------

    def find(self) -> Sequence[Finding]:
        findings: list[Finding] = []
        findings.extend(self._unreachable_elements())
        findings.extend(self._dangling_config_references())
        findings.extend(self._orphaned_config_elements())
        findings.extend(self._dead_branches())
        findings.extend(self._shadowed_definitions())
        findings.extend(self._duplicated_logic())
        findings.extend(self._unconsumed_features())
        findings.extend(self._decision_irrelevant())
        return tuple(sorted(findings, key=lambda f: f.id))

    # -- shared machinery ----------------------------------------------

    def _reachable_set(self) -> tuple[set[str], dict[str, list[Edge]]]:
        """BFS from entry_ids over structural edges.

        Returns the reached element IDs and an incoming-edge index (built
        regardless of whether entries exist, so callers can still ask "does
        anything point at this element" even with no configured entry).
        """
        incoming: dict[str, list[Edge]] = defaultdict(list)
        outgoing: dict[str, list[Edge]] = defaultdict(list)
        for e in self._edges:
            if e.kind in _STRUCTURAL_EDGE_KINDS:
                outgoing[e.source_id].append(e)
                incoming[e.target_id].append(e)

        reached: set[str] = set(self._entry_ids)
        queue: deque[str] = deque(self._entry_ids)
        while queue:
            current = queue.popleft()
            for e in outgoing.get(current, ()):
                if e.target_id not in reached:
                    reached.add(e.target_id)
                    queue.append(e.target_id)
        return reached, incoming

    def _unresolved_candidate_ids(self) -> set[str]:
        """Elements any unresolved call site *might* reach.

        These are UNKNOWN, not unplugged, per the card's defining rule.
        """
        ids: set[str] = set()
        for u in self._unresolved:
            ids.update(u.candidate_ids)
        return ids

    def _fid(self, kind: str, *parts: str) -> str:
        return f"finding::{kind}::{'|'.join(parts)}"

    # -- UNREACHABLE_ELEMENT --------------------------------------------

    def _unreachable_elements(self) -> list[Finding]:
        if not self._entry_ids:
            # Without a known entry point, reachability is UNKNOWN
            # everywhere, not wrong everywhere. Report nothing rather than
            # guess.
            return []

        reached, incoming = self._reachable_set()
        unknown_candidates = self._unresolved_candidate_ids()

        unreachable: dict[str, Element] = {}
        for el in self._elements:
            if el.kind not in _REPORTABLE_KINDS:
                continue
            if el.id in reached:
                continue
            if el.id in unknown_candidates:
                # Reachable only through an unresolved call site: UNKNOWN,
                # not unplugged. This is the distinction the card exists to
                # preserve.
                continue
            unreachable[el.id] = el

        out: list[Finding] = []
        for el_id, el in sorted(unreachable.items()):
            # Report only the outermost unreachable ancestor: a method
            # inside an already-unreachable class is not a second finding.
            if el.parent_id in unreachable:
                continue

            touching = incoming.get(el_id, [])
            evidence = tuple(sorted({e.id for e in touching} | set(self._entry_ids)))
            if not evidence:
                # No edge chain to cite: contract says a finding with an
                # empty evidence chain does not ship.
                continue
            if touching:
                conf = combine(*(e.provenance.confidence for e in touching))
                note = (
                    "all incoming edges originate from other unreachable "
                    "elements"
                )
            else:
                # Absence of any edge at all is itself a fact resting on
                # card 2's resolution being complete, so it is RESOLVED
                # rather than CERTAIN.
                conf = Confidence.RESOLVED
                note = "no incoming call/reference/import edge exists in the graph"

            out.append(
                Finding(
                    id=self._fid("UNREACHABLE_ELEMENT", el_id),
                    kind=FindingKind.UNREACHABLE_ELEMENT,
                    element_id=el_id,
                    span=el.span,
                    summary=(
                        f"{el.qualname or el.name} is not reachable from any "
                        "known entry point."
                    ),
                    hint=(
                        "Confirm no dynamic caller reaches this before removing "
                        "it -- check unresolved.jsonl for candidates first."
                    ),
                    evidence_ids=evidence,
                    provenance=Provenance(
                        method=Method.CFG_REACHABILITY,
                        confidence=conf,
                        span=el.span,
                        note=note,
                    ),
                )
            )
        return out

    # -- DANGLING_CONFIG_REFERENCE ---------------------------------------

    def _dangling_config_references(self) -> list[Finding]:
        out: list[Finding] = []
        for u in self._unresolved:
            if u.reason != UnresolvedReason.MISSING_TARGET:
                continue
            if not u.span.path.endswith(_CONFIG_EXTENSIONS):
                continue
            element_id = self._config_element_id_for_span(u.span) or u.id
            out.append(
                Finding(
                    id=self._fid("DANGLING_CONFIG_REFERENCE", u.id),
                    kind=FindingKind.DANGLING_CONFIG_REFERENCE,
                    element_id=element_id,
                    span=u.span,
                    summary=(
                        f"Config reference resolves to no element: {u.description}"
                    ),
                    hint="Fix the name in the config file, or remove the stale key.",
                    evidence_ids=(u.id,),
                    provenance=Provenance(
                        method=Method.CONFIG_STRING_MATCH,
                        confidence=Confidence.HEURISTIC,
                        span=u.span,
                        note="derived from an unresolved MISSING_TARGET record",
                    ),
                )
            )
        return out

    def _config_element_id_for_span(self, span: SourceSpan) -> str:
        for el in self._elements:
            if el.kind == ElementKind.CONFIG_KEY and el.span.path == span.path and el.span.line == span.line:
                return el.id
        return ""

    # -- ORPHANED_CONFIG_ELEMENT ------------------------------------------

    def _orphaned_config_elements(self) -> list[Finding]:
        if not self._entry_ids:
            return []
        reached, incoming = self._reachable_set()

        by_config_target: dict[str, list[Edge]] = defaultdict(list)
        by_other_target: dict[str, list[Edge]] = defaultdict(list)
        for e in self._edges:
            if e.kind == EdgeKind.CONFIGURES:
                by_config_target[e.target_id].append(e)
            elif e.kind in _STRUCTURAL_EDGE_KINDS:
                by_other_target[e.target_id].append(e)

        out: list[Finding] = []
        for el in self._elements:
            if el.kind not in _REPORTABLE_KINDS:
                continue
            config_in = by_config_target.get(el.id, [])
            if not config_in:
                continue
            if by_other_target.get(el.id):
                # Reached some other way too: not orphaned.
                continue

            live = any(
                edge.source_id in reached or bool(incoming.get(edge.source_id))
                for edge in config_in
            )
            if live:
                continue

            evidence = tuple(sorted(e.id for e in config_in))
            conf = combine(*(e.provenance.confidence for e in config_in))
            out.append(
                Finding(
                    id=self._fid("ORPHANED_CONFIG_ELEMENT", el.id),
                    kind=FindingKind.ORPHANED_CONFIG_ELEMENT,
                    element_id=el.id,
                    span=el.span,
                    summary=(
                        f"{el.qualname or el.name} is reachable only through a "
                        "config key that no live config appears to set."
                    ),
                    hint=(
                        "Check whether any loaded config actually sets this key; "
                        "if none does, this element never runs."
                    ),
                    evidence_ids=evidence,
                    provenance=Provenance(
                        method=Method.CONFIG_STRING_MATCH,
                        confidence=conf,
                        span=el.span,
                    ),
                )
            )
        return out

    # -- DEAD_BRANCH -------------------------------------------------------

    def _dead_branches(self) -> list[Finding]:
        block_owner: dict[str, str] = {b.id: b.element_id for b in self._cfg_blocks}
        block_span: dict[str, SourceSpan] = {b.id: b.span for b in self._cfg_blocks}
        blocks_by_element: dict[str, list[CFGBlock]] = defaultdict(list)
        for b in self._cfg_blocks:
            blocks_by_element[b.element_id].append(b)

        edges_by_element: dict[str, list[CFGEdge]] = defaultdict(list)
        for e in self._cfg_edges:
            owner = block_owner.get(e.source_id) or block_owner.get(e.target_id)
            if owner:
                edges_by_element[owner].append(e)

        out: list[Finding] = []
        for element_id, cfg_edges in edges_by_element.items():
            blocks = blocks_by_element.get(element_id, [])
            entry_blocks = [b.id for b in blocks if b.kind == BlockKind.ENTRY]
            adj: dict[str, list[CFGEdge]] = defaultdict(list)
            for e in cfg_edges:
                adj[e.source_id].append(e)

            reached_blocks: set[str] = set(entry_blocks)
            queue: deque[str] = deque(entry_blocks)
            while queue:
                current = queue.popleft()
                for e in adj.get(current, ()):
                    if e.target_id not in reached_blocks:
                        reached_blocks.add(e.target_id)
                        queue.append(e.target_id)

            for e in cfg_edges:
                dead_reason = ""
                if entry_blocks and e.source_id in reached_blocks and e.target_id not in reached_blocks:
                    dead_reason = "target block is unreachable from the element's entry block"
                elif self._is_contradictory(e):
                    dead_reason = "condition is a literal constant contradicting the branch taken"
                if not dead_reason:
                    continue

                span = block_span.get(e.target_id) or block_span.get(e.source_id) or _EMPTY_SPAN
                conf = e.provenance.confidence if e.provenance is not None else Confidence.HEURISTIC
                out.append(
                    Finding(
                        id=self._fid("DEAD_BRANCH", e.id),
                        kind=FindingKind.DEAD_BRANCH,
                        element_id=element_id,
                        span=span,
                        summary=f"CFG edge {e.id} is never taken: {dead_reason}.",
                        hint="Verify the guard; if it can never hold, remove the branch.",
                        evidence_ids=(e.id,),
                        provenance=Provenance(
                            method=Method.CFG_REACHABILITY,
                            confidence=conf,
                            span=span,
                            note=dead_reason,
                        ),
                    )
                )
        return out

    @staticmethod
    def _is_contradictory(edge: CFGEdge) -> bool:
        condition = (edge.condition or "").strip().lower()
        if edge.taken_when is True and condition in {"false", "0", "none"}:
            return True
        if edge.taken_when is False and condition in {"true", "1"}:
            return True
        return False

    # -- SHADOWED_DEFINITION -----------------------------------------------

    def _shadowed_definitions(self) -> list[Finding]:
        groups: dict[tuple[str, str, str], list[Element]] = defaultdict(list)
        for el in self._elements:
            if el.kind in (ElementKind.MODULE, ElementKind.PACKAGE):
                continue
            groups[(el.module, el.parent_id, el.qualname)].append(el)

        out: list[Finding] = []
        for group in groups.values():
            if len(group) < 2:
                continue
            ordered = sorted(group, key=lambda e: _ordinal_of(e.id))
            for shadowed, shadowing in zip(ordered, ordered[1:]):
                out.append(
                    Finding(
                        id=self._fid("SHADOWED_DEFINITION", shadowed.id, shadowing.id),
                        kind=FindingKind.SHADOWED_DEFINITION,
                        element_id=shadowed.id,
                        span=shadowed.span,
                        summary=(
                            f"{shadowed.qualname or shadowed.name} is redefined at "
                            f"{shadowing.span.path}:{shadowing.span.line}; the earlier "
                            "definition can never be used if the redefinition is "
                            "unconditional."
                        ),
                        hint=(
                            "If both definitions coexist on separate branches this is "
                            "fine; otherwise remove the earlier definition."
                        ),
                        evidence_ids=(shadowing.id,),
                        provenance=Provenance(
                            method=Method.AST_DIRECT,
                            confidence=Confidence.PROBABLE,
                            span=shadowed.span,
                            note=(
                                "two definitions of the same name exist structurally; "
                                "whether the redefinition is unconditional was not "
                                "verified against the CFG"
                            ),
                        ),
                    )
                )
        return out

    # -- DUPLICATED_LOGIC ----------------------------------------------------

    def _duplicated_logic(self) -> list[Finding]:
        by_hash: dict[str, list[Element]] = defaultdict(list)
        for el in self._elements:
            if el.kind not in (ElementKind.FUNCTION, ElementKind.METHOD):
                continue
            if not el.content_hash:
                continue
            by_hash[el.content_hash].append(el)

        out: list[Finding] = []
        for content_hash, group in by_hash.items():
            if len(group) < 2:
                continue
            ordered = sorted(group, key=lambda e: e.id)
            primary, *rest = ordered
            for dup in rest:
                out.append(
                    Finding(
                        id=self._fid("DUPLICATED_LOGIC", primary.id, dup.id),
                        kind=FindingKind.DUPLICATED_LOGIC,
                        element_id=dup.id,
                        span=dup.span,
                        summary=(
                            f"{dup.qualname or dup.name} has a body identical to "
                            f"{primary.qualname or primary.name}."
                        ),
                        hint="Consider consolidating into one element the others call.",
                        evidence_ids=(primary.id,),
                        provenance=Provenance(
                            method=Method.STRUCTURAL_MATCH,
                            confidence=Confidence.RESOLVED,
                            span=dup.span,
                            note=f"identical content_hash {content_hash}",
                        ),
                    )
                )
        return out

    # -- UNCONSUMED_FEATURE ---------------------------------------------------

    def _unconsumed_features(self) -> list[Finding]:
        feature_elements = [e for e in self._elements if e.kind == ElementKind.FEATURE]
        if not feature_elements:
            return []

        consumed: set[str] = set()
        for dp in self._decision_points:
            consumed.update(dp.reads_ids)
        for s in self._slices:
            if s.direction == "backward" and s.reaches_sink_ids:
                consumed.update(s.member_ids)

        reads_by_feature: dict[str, list[LineageEdge]] = defaultdict(list)
        writes_by_feature: dict[str, list[LineageEdge]] = defaultdict(list)
        for le in self._lineage_edges:
            if le.kind == LineageKind.READS:
                reads_by_feature[le.source_id].append(le)
            elif le.kind in _WRITE_LINEAGE_KINDS:
                writes_by_feature[le.target_id].append(le)

        out: list[Finding] = []
        for feat in feature_elements:
            if feat.id in consumed:
                continue
            writes = writes_by_feature.get(feat.id, [])
            if not writes:
                # Nothing in the graph shows this feature being computed;
                # without that, "unconsumed" is not a claim we can support
                # with evidence.
                continue
            reads = reads_by_feature.get(feat.id, [])
            evidence = tuple(sorted({w.id for w in writes} | {r.id for r in reads}))
            conf = combine(
                *(w.provenance.confidence for w in writes),
                *(r.provenance.confidence for r in reads),
            )
            span = writes[0].span or feat.span
            out.append(
                Finding(
                    id=self._fid("UNCONSUMED_FEATURE", feat.id),
                    kind=FindingKind.UNCONSUMED_FEATURE,
                    element_id=feat.id,
                    span=span,
                    summary=(
                        f"Feature {feat.name} is computed but no path to a decision "
                        "sink reads it."
                    ),
                    hint="Confirm the feature is truly unused before removing its computation.",
                    evidence_ids=evidence,
                    provenance=Provenance(
                        method=Method.DATAFLOW,
                        confidence=conf,
                        span=span,
                        note="no decision point or sink-reaching backward slice reads this feature",
                    ),
                )
            )
        return out

    # -- DECISION_IRRELEVANT ---------------------------------------------------

    def _decision_irrelevant(self) -> list[Finding]:
        if not self._entry_ids:
            return []
        reached, incoming = self._reachable_set()

        sink_relevant: set[str] = set()
        for dp in self._decision_points:
            sink_relevant.add(dp.element_id)
            sink_relevant.update(dp.reads_ids)
        for s in self._slices:
            if s.reaches_sink_ids:
                sink_relevant.update(s.member_ids)
        if not sink_relevant:
            # No decision/sink information available at all: reachability to
            # a sink is UNKNOWN, not "irrelevant". Report nothing.
            return []

        slice_members: set[str] = set()
        for s in self._slices:
            slice_members.update(s.member_ids)

        out: list[Finding] = []
        for el in self._elements:
            if el.id not in slice_members:
                # Scope this finding to elements a lineage slice already
                # covers, so a plain reachable-but-irrelevant helper (a
                # logger, a validator with no data role) is not flagged just
                # because it is not a "decision" itself.
                continue
            if el.id not in reached:
                continue
            if el.id in sink_relevant:
                continue

            touching = incoming.get(el.id, [])
            evidence = tuple(sorted({e.id for e in touching} | set(self._entry_ids)))
            if not evidence:
                continue
            conf = (
                combine(*(e.provenance.confidence for e in touching))
                if touching
                else Confidence.HEURISTIC
            )
            out.append(
                Finding(
                    id=self._fid("DECISION_IRRELEVANT", el.id),
                    kind=FindingKind.DECISION_IRRELEVANT,
                    element_id=el.id,
                    span=el.span,
                    summary=(
                        f"{el.qualname or el.name} runs but no slice or decision "
                        "point shows it reaching a decision sink."
                    ),
                    hint=(
                        "Check whether this cluster should feed a decision; if not, "
                        "it may be safe to drop."
                    ),
                    evidence_ids=evidence,
                    provenance=Provenance(
                        method=Method.DATAFLOW,
                        confidence=conf,
                        span=el.span,
                    ),
                )
            )
        return out
