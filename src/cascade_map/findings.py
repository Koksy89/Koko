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
    Reachability,
    ReachabilityState,
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

# Element kinds worth reporting as unreachable / decision-irrelevant. A dead
# MODULE (nothing imports it and it is not itself an entry) and a dead CLASS
# (nothing instantiates, subclasses or references it) are real, reportable
# findings -- excluding whole kinds here would silently hide them from the
# owner, which is worse than an honest evidence-bearing finding. Leaf facts
# like PARAMETER, IMPORT or ASSIGNMENT stay out: too fine-grained to report
# on their own, and their containing element already carries the finding.
_REPORTABLE_KINDS = {
    ElementKind.MODULE,
    ElementKind.CLASS,
    ElementKind.FUNCTION,
    ElementKind.METHOD,
    ElementKind.PROPERTY,
}

# Element kinds that bind a name in a Python scope, and so can meaningfully
# "shadow" one another. CONFIG_KEY, DATA_FILE, FEATURE, IMPORT etc. are not
# name bindings in this sense.
_SHADOWABLE_KINDS = {
    ElementKind.FUNCTION,
    ElementKind.METHOD,
    ElementKind.CLASS,
    ElementKind.PROPERTY,
    ElementKind.ASSIGNMENT,
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
        reachability: Sequence[Reachability] = (),
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
        # Card 3's canonical answer to "does this reach a decision sink".
        # DECISION_IRRELEVANT reads this instead of deriving its own verdict
        # from slices, per the Reachability contract added to close exactly
        # this second-source-of-truth gap. One element may appear at most
        # once; a duplicate is last-write-wins, which cannot happen from a
        # well-formed card 3 output (one record per element).
        self._reachability_by_element: Mapping[str, Reachability] = {
            r.element_id: r for r in reachability
        }
        # Only entries that name a real element are usable as BFS roots. An
        # entry_id naming nothing in the graph is silently useless for
        # reachability, not a crash.
        self._entry_ids = tuple(sorted(i for i in set(entry_ids) if i in self._by_id))

    # -- public API ---------------------------------------------------

    def find(self) -> Sequence[Finding]:
        shadowed = self._shadowed_definitions()
        # An element with an earlier, live definition that shadows it has a
        # more specific, correct explanation already: reporting it a second
        # time as UNREACHABLE_ELEMENT sends the owner to look for a missing
        # call site, when the actual cause is the redefinition.
        shadowed_ids = {f.element_id for f in shadowed}

        findings: list[Finding] = []
        findings.extend(self._unreachable_elements(exclude=shadowed_ids))
        findings.extend(self._dangling_config_references())
        findings.extend(self._orphaned_config_elements())
        findings.extend(self._dead_branches())
        findings.extend(shadowed)
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
        # A module that defines an entry element has necessarily already run
        # its top-level code -- that is how the entry function came to exist
        # -- even though the import that pulled it in lives outside this
        # graph, at the interpreter boundary. Without this, every entry
        # module's own MODULE element reads as unreachable, which is wrong
        # in the same way for every single-module fixture in the corpus.
        entry_modules = {
            el.module
            for el_id in self._entry_ids
            if (el := self._by_id.get(el_id)) is not None and el.module
        }
        if entry_modules:
            for el in self._elements:
                if el.kind == ElementKind.MODULE and el.module in entry_modules:
                    reached.add(el.id)

        queue: deque[str] = deque(reached)
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

    def _unreachable_elements(self, *, exclude: set[str] = frozenset()) -> list[Finding]:
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
            if el.id in exclude:
                # A more specific finding (e.g. SHADOWED_DEFINITION) already
                # explains why this element is never used; naming it here
                # too sends the owner looking for the wrong cause.
                continue
            unreachable[el.id] = el

        out: list[Finding] = []
        for el_id, el in sorted(unreachable.items()):
            # Report only the outermost unreachable ancestor: a method
            # inside an already-unreachable class is not a second finding.
            if el.parent_id in unreachable:
                continue

            touching = incoming.get(el_id, [])
            reachability = self._reachability_by_element.get(el_id)
            evidence_parts = {e.id for e in touching}
            if reachability is not None:
                # Card 3's own record for this element is the strongest, most
                # specific citation available -- prefer it over the coarser
                # entry_ids fallback.
                evidence_parts.add(reachability.id)
            if not evidence_parts:
                evidence_parts = set(self._entry_ids)
            evidence = tuple(sorted(evidence_parts))
            if not evidence:
                # No edge chain to cite: contract says a finding with an
                # empty evidence chain does not ship.
                continue

            confidences = [e.provenance.confidence for e in touching]
            if reachability is not None:
                confidences.append(reachability.provenance.confidence)
            if confidences:
                conf = combine(*confidences)
                note = (
                    reachability.reason
                    if reachability is not None and reachability.reason
                    else "all incoming edges originate from other unreachable elements"
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
        """A component-shaped element in a family config demonstrably wires,
        that no config key names.

        "Demonstrably wires" is load-bearing: absence of a CONFIGURES edge
        means nothing on its own (most classes are never named by config at
        all). It only means something once at least one sibling under the
        same base class *is* named by a config key -- that is what makes the
        family a config-driven registry rather than an ordinary class
        hierarchy. `Stage` itself (the base) is never flagged: it is reached
        by inheritance, not by wiring. `WiredStage` (has its own CONFIGURES
        edge) is never flagged either.
        """
        inherits_by_base: dict[str, list[Edge]] = defaultdict(list)
        for e in self._edges:
            if e.kind == EdgeKind.INHERITS:
                inherits_by_base[e.target_id].append(e)

        configures_by_target: dict[str, list[Edge]] = defaultdict(list)
        for e in self._edges:
            if e.kind == EdgeKind.CONFIGURES:
                configures_by_target[e.target_id].append(e)

        out: dict[str, Finding] = {}
        for family_edges in inherits_by_base.values():
            wired_siblings = [
                e for e in family_edges if configures_by_target.get(e.source_id)
            ]
            if not wired_siblings:
                # This hierarchy shows no sign of being config-driven at
                # all: nothing to compare an absence against.
                continue
            wiring_edge = configures_by_target[wired_siblings[0].source_id][0]

            for child_edge in family_edges:
                child_id = child_edge.source_id
                if configures_by_target.get(child_id):
                    continue  # this sibling is itself named by config
                child = self._by_id.get(child_id)
                if child is None or child.kind not in _REPORTABLE_KINDS:
                    continue

                evidence = tuple(sorted({wiring_edge.id, child_edge.id}))
                conf = combine(
                    wiring_edge.provenance.confidence,
                    child_edge.provenance.confidence,
                )
                out[child_id] = Finding(
                    id=self._fid("ORPHANED_CONFIG_ELEMENT", child_id),
                    kind=FindingKind.ORPHANED_CONFIG_ELEMENT,
                    element_id=child_id,
                    span=child.span,
                    summary=(
                        f"{child.qualname or child.name} is in a family a config "
                        "file wires, and no config key names it."
                    ),
                    hint=(
                        "Either add it to the wiring config or delete it; check "
                        "other deployments' configs before removing."
                    ),
                    evidence_ids=evidence,
                    provenance=Provenance(
                        method=Method.CONFIG_STRING_MATCH,
                        confidence=conf,
                        span=child.span,
                    ),
                )
        return list(out.values())

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
            if el.kind not in _SHADOWABLE_KINDS:
                # Only elements that bind a name in a Python scope can
                # "shadow" one another. CONFIG_KEY/DATA_FILE/FEATURE ids are
                # not name bindings: a config file's `/rules/0` and `/rules/1`
                # share a blank qualname and the same parent (the file) but
                # are two different keys, not one name redefined.
                continue
            if not el.qualname:
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

        reads_by_feature: dict[str, list[LineageEdge]] = defaultdict(list)
        writes_by_feature: dict[str, list[LineageEdge]] = defaultdict(list)
        for le in self._lineage_edges:
            if le.kind == LineageKind.READS:
                reads_by_feature[le.source_id].append(le)
            elif le.kind in _WRITE_LINEAGE_KINDS:
                writes_by_feature[le.target_id].append(le)

        # A feature with any READS edge at all is consumed by something: at
        # minimum that rules out "no lineage edge leaves it", which is the
        # actual claim this finding makes. DecisionPoint.reads_ids and a
        # sink-reaching backward Slice are a stronger, sink-specific version
        # of the same fact when card 3/4 supply them; either is sufficient.
        consumed: set[str] = set(reads_by_feature.keys())
        for dp in self._decision_points:
            consumed.update(dp.reads_ids)
        for s in self._slices:
            if s.direction == "backward" and s.reaches_sink_ids:
                consumed.update(s.member_ids)

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

        if self._reachability_by_element:
            return self._decision_irrelevant_from_reachability(reached, incoming)
        return self._decision_irrelevant_from_slices(reached, incoming)

    def _decision_irrelevant_from_reachability(
        self, reached: set[str], incoming: dict[str, list[Edge]]
    ) -> list[Finding]:
        """Card 3's canonical `Reachability` record per element, not a
        recomputed answer -- this is the one place the card contract exists
        specifically to prevent a second source of truth.

        `UNKNOWN` is never treated as "irrelevant": the contract is explicit
        that "I could not tell" must never render the same as "this reaches
        nothing". Only an explicit `NO_SINK_PATH` verdict is reported.
        """
        out: list[Finding] = []
        for el in self._elements:
            if el.kind not in _REPORTABLE_KINDS:
                continue
            if el.id not in reached:
                continue
            r = self._reachability_by_element.get(el.id)
            if r is None or r.state != ReachabilityState.NO_SINK_PATH:
                continue

            touching = incoming.get(el.id, [])
            evidence = tuple(sorted({r.id} | {e.id for e in touching}))
            if not evidence:
                continue
            conf = combine(
                r.provenance.confidence,
                *(e.provenance.confidence for e in touching),
            )
            out.append(
                Finding(
                    id=self._fid("DECISION_IRRELEVANT", el.id),
                    kind=FindingKind.DECISION_IRRELEVANT,
                    element_id=el.id,
                    span=el.span,
                    summary=(
                        f"{el.qualname or el.name} runs but card 3 marks it "
                        f"NO_SINK_PATH: {r.reason or 'no path to a decision sink'}."
                    ),
                    hint=(
                        "Check whether this cluster should feed a decision; if not, "
                        "it may be safe to drop."
                    ),
                    evidence_ids=evidence,
                    provenance=Provenance(
                        method=Method.CFG_REACHABILITY,
                        confidence=conf,
                        span=el.span,
                        note=f"from card 3 Reachability {r.id}",
                    ),
                )
            )
        return out

    def _decision_irrelevant_from_slices(
        self, reached: set[str], incoming: dict[str, list[Edge]]
    ) -> list[Finding]:
        """Fallback used only when no `Reachability` records were supplied
        (e.g. card 3 output not wired in yet). Derives sink-relevance from
        `DecisionPoint.reads_ids` and sink-reaching `Slice`s instead -- a
        strictly weaker, locally-derived substitute for the same answer.
        """
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
                        note="no card 3 Reachability supplied; derived from slices/decision points",
                    ),
                )
            )
        return out
