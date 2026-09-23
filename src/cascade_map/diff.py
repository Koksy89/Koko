"""Card 6 -- version diff and impact.

Compares two Mode B graphs (`GraphSnapshot`) built from `target_versions/<label>/`
or `target_engine/`, and answers "what does this change actually affect?"

Scope and design, read before changing anything
-------------------------------------------------
Cards 1-4 are being built in parallel with this one. `Differ.diff()` implements
the `DiffCard` protocol exactly (`diff(before_root, after_root) -> (changes,
impacts)`), loading each side's artifacts from an `out/<label>/` directory per
`ARCHITECTURE.md`'s output layout. The real matching and classification logic
lives in `diff_snapshots(before, after)`, which takes two `GraphSnapshot`
values directly -- that is what tests exercise, since cards 1-4's real output
does not exist yet. `Differ.diff` is a thin loader wrapper around it.

Normalisation (formatting-only / comment-only -> UNCHANGED)
-------------------------------------------------------------
Body comparison never uses `ast.parse` or `compile` on target text (both can
raise on isolated fragments and, more importantly, this module must stay
obviously inert under constraint 1). It uses `tokenize.generate_tokens`, which
is purely lexical and executes nothing. COMMENT, NL, NEWLINE, INDENT, DEDENT,
ENCODING and ENDMARKER tokens are dropped; every remaining token's exact
string (identifiers, keywords, operators, string and number literals) is kept
and joined with single spaces. Two spans that tokenize to the same sequence
are UNCHANGED regardless of whitespace, indentation style, blank lines or
comments. If a span does not tokenize (e.g. an unavailable source file), the
comparison falls back to `Element.content_hash` equality (HEURISTIC -- not
formatting-invariant) and, failing that, is reported as changed rather than
silently claimed unchanged (UNKNOWN confidence) -- an honest gap outranks a
confident wrong answer, per the project's own stated principle.

Ambiguous matches (`ChangeKind.AMBIGUOUS`)
---------------------------------------------
Rename/move candidates are grouped into connected components by tied top
score (see `_match_renames_moves`): an edge joins a removed element to an
added one whenever the pair is the *best* available match for either side.
A component containing exactly one removed and one added element is a clean
match (RENAMED/MOVED). Any other component -- one element tied against
several, or several tied against several -- emits one `VersionChange` per
member with `kind=AMBIGUOUS`, and **every member of the component carries the
same `candidate_ids`: the full sorted set of every ID in the component,
including its own**. This is deliberate, not an oversight: an earlier version
that gave only the many-candidates side an UNKNOWN-confidence ADDED/REMOVED
pair let the *other* side of the same tie render as a plain CERTAIN ADDED
with no back-reference -- half the ambiguity was invisible from that end.
Attaching the identical candidate set to every member means the doubt reads
the same regardless of which element the owner looked up first.

Known contract gap (reported, not worked around -- see the final report)
---------------------------------------------------------------------------
There is no `ChangeKind` for wiring-only changes (a CONFIGURES edge
repointed, a call edge gained/lost with no accompanying element change). A
config key repoint is reported via the existing element-level kinds on the
affected CONFIG_KEY element (SIGNATURE_CHANGED when its target value
changes); edge-level gains/losses feed `Impact` instead of a `VersionChange`
of their own. Unlike the ambiguity gap above, this is not a false claim --
the config key's value did change, and interpreting any `VersionChange`
already requires looking up the element's `kind` -- so it is reported as a
weaker request in the card's report, not re-asserted as a blocker.
"""

from __future__ import annotations

import io
import json
import tokenize
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from cascade_map.contracts.interfaces import (
    Confidence,
    Edge,
    EdgeKind,
    Element,
    ElementKind,
    ChangeKind,
    Finding,
    Impact,
    LineageEdge,
    Method,
    Provenance,
    Slice,
    SourceSpan,
    VersionChange,
    combine,
)

__all__ = [
    "GraphSnapshot",
    "load_snapshot",
    "diff_snapshots",
    "Differ",
]


# ---------------------------------------------------------------------------
# Snapshot: one version's Mode B graph, the input to the diff
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GraphSnapshot:
    """One version's Mode B graph. Card 6 does not produce this -- cards 1-4
    do. `load_snapshot` reads it from an `out/<label>/` artifact directory;
    tests construct it directly."""

    label: str
    elements: tuple[Element, ...] = ()
    edges: tuple[Edge, ...] = ()
    lineage_edges: tuple[LineageEdge, ...] = ()
    slices: tuple[Slice, ...] = ()
    findings: tuple[Finding, ...] = ()
    source_root: str = ""
    """Directory that `Element.span.path` is relative to, for reading source
    text for normalisation. Empty means source text is unavailable and body
    comparison falls back per the module docstring."""

    def elements_by_id(self) -> dict[str, Element]:
        return {e.id: e for e in self.elements}

    def forward_slice(self, root_id: str) -> Slice | None:
        for s in self.slices:
            if s.root_id == root_id and s.direction == "forward":
                return s
        return None

    def backward_slice(self, root_id: str) -> Slice | None:
        for s in self.slices:
            if s.root_id == root_id and s.direction == "backward":
                return s
        return None

    def read_span(self, element: Element) -> str | None:
        """The element's own source text, read-only, never executed. Returns
        None when unavailable rather than raising -- callers degrade the
        confidence of whatever comparison they were about to make."""
        if not self.source_root:
            return None
        path = Path(self.source_root) / element.span.path
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return None
        start = max(element.span.line - 1, 0)
        end = element.span.end_line if element.span.end_line else element.span.line
        end = min(end, len(lines))
        if start >= end:
            return None
        return "\n".join(lines[start:end])


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _span_from_dict(d: dict | None) -> SourceSpan | None:
    if d is None:
        return None
    return SourceSpan(path=d["path"], line=d["line"], end_line=d.get("end_line"), col=d.get("col"))


def _prov_from_dict(d: dict | None) -> Provenance | None:
    if d is None:
        return None
    return Provenance(
        method=Method(d["method"]),
        confidence=Confidence(d["confidence"]),
        span=_span_from_dict(d.get("span")),
        note=d.get("note", ""),
        model_id=d.get("model_id", ""),
        run_id=d.get("run_id", ""),
        event_ids=tuple(d.get("event_ids", ())),
    )


def _element_from_dict(d: dict) -> Element:
    return Element(
        id=d["id"],
        kind=ElementKind(d["kind"]),
        name=d["name"],
        qualname=d["qualname"],
        module=d["module"],
        span=_span_from_dict(d["span"]),
        provenance=_prov_from_dict(d["provenance"]),
        content_hash=d.get("content_hash") or "",
        decorators=tuple(d.get("decorators", ())),
        signature=d.get("signature", ""),
        docstring=d.get("docstring", ""),
        parent_id=d.get("parent_id", ""),
        byte_size=d.get("byte_size", 0),
    )


def _edge_from_dict(d: dict) -> Edge:
    return Edge(
        id=d["id"],
        kind=EdgeKind(d["kind"]),
        source_id=d["source_id"],
        target_id=d["target_id"],
        provenance=_prov_from_dict(d["provenance"]),
        call_site=_span_from_dict(d.get("call_site")),
    )


def _lineage_from_dict(d: dict) -> LineageEdge:
    from cascade_map.contracts.interfaces import LineageKind

    return LineageEdge(
        id=d["id"],
        kind=LineageKind(d["kind"]),
        source_id=d["source_id"],
        target_id=d["target_id"],
        provenance=_prov_from_dict(d["provenance"]),
        span=_span_from_dict(d.get("span")),
    )


def _slice_from_dict(d: dict) -> Slice:
    return Slice(
        id=d["id"],
        root_id=d["root_id"],
        direction=d["direction"],
        member_ids=tuple(d.get("member_ids", ())),
        edge_ids=tuple(d.get("edge_ids", ())),
        barrier_ids=tuple(d.get("barrier_ids", ())),
        reaches_sink_ids=tuple(d.get("reaches_sink_ids", ())),
        confidence=Confidence(d["confidence"]),
        scope=str(d.get("scope", "DECISION")),
    )


def _finding_from_dict(d: dict) -> Finding:
    from cascade_map.contracts.interfaces import FindingKind

    return Finding(
        id=d["id"],
        kind=FindingKind(d["kind"]),
        element_id=d["element_id"],
        span=_span_from_dict(d["span"]),
        summary=d.get("summary", ""),
        hint=d.get("hint", ""),
        evidence_ids=tuple(d.get("evidence_ids", ())),
        provenance=_prov_from_dict(d["provenance"]),
    )


def load_snapshot(out_dir: str | Path, source_root: str | Path | None = None) -> GraphSnapshot:
    """Read one version's artifacts from `out_dir` (`elements.jsonl`,
    `edges.jsonl`, `lineage.jsonl`, `slices.jsonl`, `findings.jsonl`, per
    `ARCHITECTURE.md`). Missing files degrade to empty, not an error: cards
    1-4 may not have produced every artifact yet, and diffing should still do
    what it can with what exists. `source_root` defaults to `out_dir`."""
    out = Path(out_dir)
    elements = tuple(_element_from_dict(d) for d in _read_jsonl(out / "elements.jsonl"))
    edges = tuple(_edge_from_dict(d) for d in _read_jsonl(out / "edges.jsonl"))
    lineage_edges = tuple(_lineage_from_dict(d) for d in _read_jsonl(out / "lineage.jsonl"))
    slices = tuple(_slice_from_dict(d) for d in _read_jsonl(out / "slices.jsonl"))
    findings = tuple(_finding_from_dict(d) for d in _read_jsonl(out / "findings.jsonl"))
    root = str(source_root) if source_root is not None else str(out)
    return GraphSnapshot(
        label=out.name,
        elements=elements,
        edges=edges,
        lineage_edges=lineage_edges,
        slices=slices,
        findings=findings,
        source_root=root,
    )


# ---------------------------------------------------------------------------
# Body normalisation -- see module docstring
# ---------------------------------------------------------------------------

_SKIP_TOKEN_TYPES = {
    tokenize.COMMENT,
    tokenize.NL,
    tokenize.NEWLINE,
    tokenize.INDENT,
    tokenize.DEDENT,
    tokenize.ENCODING,
    tokenize.ENDMARKER,
}


def _normalized_body(source: str | None) -> str | None:
    """Comment- and whitespace-insensitive token sequence of `source`. Purely
    lexical (`tokenize`, never `ast.parse`/`compile`/`exec`): nothing here
    executes target code. Returns None if `source` is None or does not
    tokenize, so the caller can fall back rather than assert a wrong answer.

    The caught name is `tokenize.TokenError`. It was `tokenize.TokenizeError`,
    which does not exist, so evaluating the `except` clause raised
    `AttributeError` and the documented fallback never ran -- found by card 18
    the first time a real fragment failed to tokenize: a module element whose
    span is the first line of a triple-quoted docstring, and a JSON data file
    whose span text is `{`."""
    if source is None:
        return None
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return None
    kept = [t.string for t in tokens if t.type not in _SKIP_TOKEN_TYPES]
    return " ".join(kept)


def _body_comparison(b: Element, a: Element, before: GraphSnapshot, after: GraphSnapshot) -> tuple[bool, Confidence, str]:
    """(bodies_equal, confidence, note) per the fallback chain in the module
    docstring."""
    norm_b = _normalized_body(before.read_span(b))
    norm_a = _normalized_body(after.read_span(a))
    if norm_b is not None and norm_a is not None:
        return (
            norm_b == norm_a,
            Confidence.RESOLVED,
            "body compared via token-normalized source (comments and whitespace stripped)",
        )
    if b.content_hash and a.content_hash:
        return (
            b.content_hash == a.content_hash,
            Confidence.HEURISTIC,
            "source text unavailable; fell back to content_hash, which is not "
            "formatting-invariant",
        )
    return (
        False,
        Confidence.UNKNOWN,
        "no source text or content_hash available; body equality could not be "
        "checked, reported as changed rather than silently unchanged",
    )


# ---------------------------------------------------------------------------
# Change id
# ---------------------------------------------------------------------------


def _change_id(kind: ChangeKind, before_id: str, after_id: str) -> str:
    return f"diff::{kind.value}::{before_id or '-'}::{after_id or '-'}"


# ---------------------------------------------------------------------------
# Same-ID classification
# ---------------------------------------------------------------------------


def _classify_same_id(b: Element, a: Element, before: GraphSnapshot, after: GraphSnapshot) -> VersionChange:
    decorators_equal = b.decorators == a.decorators
    signature_equal = b.signature == a.signature
    body_equal, body_confidence, body_note = _body_comparison(b, a, before, after)

    if not signature_equal:
        kind = ChangeKind.SIGNATURE_CHANGED
        confidence = Confidence.CERTAIN
        note = "signature field differs"
    elif not decorators_equal:
        kind = ChangeKind.DECORATORS_CHANGED
        confidence = Confidence.CERTAIN
        note = "decorators field differs"
    elif not body_equal:
        kind = ChangeKind.BODY_CHANGED
        confidence = body_confidence
        note = body_note
    else:
        kind = ChangeKind.UNCHANGED
        confidence = combine(Confidence.CERTAIN, Confidence.CERTAIN, body_confidence)
        note = body_note

    return VersionChange(
        id=_change_id(kind, b.id, a.id),
        kind=kind,
        before_id=b.id,
        after_id=a.id,
        provenance=Provenance(method=Method.AST_DIRECT, confidence=confidence, note=note),
    )


# ---------------------------------------------------------------------------
# Rename / move detection for elements whose ID did not survive
# ---------------------------------------------------------------------------

_RENAME_KINDS = {ElementKind.FUNCTION, ElementKind.METHOD, ElementKind.CLASS, ElementKind.PROPERTY}


def _callee_ids(snap: GraphSnapshot, element_id: str) -> frozenset[str]:
    return frozenset(
        e.target_id for e in snap.edges if e.source_id == element_id and e.kind == EdgeKind.CALLS
    )


def _score_pair(
    b: Element, a: Element, before: GraphSnapshot, after: GraphSnapshot
) -> tuple[int, ChangeKind, Confidence, str] | None:
    """Score a candidate (removed, added) pair on body structure, signature
    and call-neighbourhood. Returns None when there is not enough evidence to
    propose a match at all (element becomes a plain ADDED/REMOVED). All
    arithmetic here is integer -- the score is internal ranking only, never
    serialized, so no float ever reaches an emitted record."""
    body_equal, _, _ = _body_comparison(b, a, before, after)
    signature_equal = b.signature == a.signature and b.signature != ""
    name_equal = b.name == a.name
    callees_b = _callee_ids(before, b.id)
    callees_a = _callee_ids(after, a.id)
    union = callees_b | callees_a
    overlap = len(callees_b & callees_a)
    denom = len(union)
    half_or_more = denom > 0 and overlap * 2 >= denom

    if not body_equal and not (signature_equal and half_or_more):
        return None

    score = (
        (3 if body_equal else 0)
        + (1 if signature_equal else 0)
        + (1 if name_equal else 0)
        + (overlap * 100 // denom if denom else 0)
    )
    moved = name_equal and b.module != a.module
    kind = ChangeKind.MOVED if moved else ChangeKind.RENAMED

    if body_equal:
        confidence = Confidence.PROBABLE
        note = (
            f"structural match: body identical after normalization; "
            f"name_equal={name_equal} module_before={b.module} module_after={a.module} "
            f"call_overlap={overlap}/{denom}"
        )
    else:
        confidence = Confidence.HEURISTIC
        note = (
            f"structural match: signature identical, body differs; "
            f"call_overlap={overlap}/{denom}"
        )
    return score, kind, confidence, note


def _find(parent: dict[str, str], x: str) -> str:
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def _union(parent: dict[str, str], x: str, y: str) -> None:
    rx, ry = _find(parent, x), _find(parent, y)
    if rx != ry:
        # union by the smaller root ID -- deterministic and symmetric under
        # relabeling before/after, since it depends only on the IDs involved.
        if rx < ry:
            parent[ry] = rx
        else:
            parent[rx] = ry


def _match_renames_moves(
    before_ids: Sequence[str], after_ids: Sequence[str], before: GraphSnapshot, after: GraphSnapshot
) -> tuple[list[VersionChange], set[str], set[str], set[str]]:
    """Match removed elements to added ones on body structure, signature and
    call-neighbourhood, grouped into connected components by tied top score:
    an edge joins a removed element to an added one whenever the pair is the
    best available match for *either* side. Deterministic and symmetric --
    the score of a pair and the union-find tie-break both depend only on the
    IDs involved, never on which snapshot is called "before" -- so diffing
    A->B and B->A produce the same components with roles swapped.

    A component with exactly one removed and one added element is a clean
    match (RENAMED/MOVED). Any other component is ambiguous: every member
    gets its own AMBIGUOUS `VersionChange`, and all of them carry the same
    `candidate_ids` -- the full sorted component -- so the doubt is visible
    from every element in the tie, not just the many-candidates side. See the
    module docstring.

    Returns (changes, matched_before, matched_after, ambiguous_ids).
    """
    before_elems = before.elements_by_id()
    after_elems = after.elements_by_id()

    candidates: list[tuple[str, str, int, ChangeKind, Confidence, str]] = []
    for b_id in before_ids:
        b = before_elems[b_id]
        if b.kind not in _RENAME_KINDS:
            continue
        for a_id in after_ids:
            a = after_elems[a_id]
            if a.kind != b.kind:
                continue
            scored = _score_pair(b, a, before, after)
            if scored is None:
                continue
            score, kind, confidence, note = scored
            candidates.append((b_id, a_id, score, kind, confidence, note))

    changes: list[VersionChange] = []
    matched_before: set[str] = set()
    matched_after: set[str] = set()
    ambiguous_ids: set[str] = set()

    if not candidates:
        return changes, matched_before, matched_after, ambiguous_ids

    best_for_before: dict[str, int] = {}
    best_for_after: dict[str, int] = {}
    for b_id, a_id, score, *_ in candidates:
        best_for_before[b_id] = max(best_for_before.get(b_id, score), score)
        best_for_after[a_id] = max(best_for_after.get(a_id, score), score)

    parent: dict[str, str] = {}
    for b_id, a_id, *_ in candidates:
        parent.setdefault(b_id, b_id)
        parent.setdefault(a_id, a_id)

    top_edges: list[tuple[str, str, int, ChangeKind, Confidence, str]] = []
    for cand in candidates:
        b_id, a_id, score, *_ = cand
        if score == best_for_before[b_id] or score == best_for_after[a_id]:
            _union(parent, b_id, a_id)
            top_edges.append(cand)

    components: dict[str, set[str]] = defaultdict(set)
    for b_id, a_id, *_ in top_edges:
        components[_find(parent, b_id)].add(b_id)
        components[_find(parent, a_id)].add(a_id)

    for root in sorted(components):
        members = components[root]
        b_members = sorted(m for m in members if m in before_elems)
        a_members = sorted(m for m in members if m in after_elems)

        if len(b_members) == 1 and len(a_members) == 1:
            b_id, a_id = b_members[0], a_members[0]
            match = next(c for c in top_edges if c[0] == b_id and c[1] == a_id)
            _, _, _score, kind, confidence, note = match
            changes.append(
                VersionChange(
                    id=_change_id(kind, b_id, a_id),
                    kind=kind,
                    before_id=b_id,
                    after_id=a_id,
                    provenance=Provenance(method=Method.STRUCTURAL_MATCH, confidence=confidence, note=note),
                )
            )
            matched_before.add(b_id)
            matched_after.add(a_id)
            continue

        candidate_ids = tuple(sorted(members))
        note = f"ambiguous structural match: tied candidates {list(candidate_ids)}"
        for m in candidate_ids:
            ambiguous_ids.add(m)
            is_before = m in before_elems
            changes.append(
                VersionChange(
                    id=_change_id(ChangeKind.AMBIGUOUS, m if is_before else "", "" if is_before else m),
                    kind=ChangeKind.AMBIGUOUS,
                    before_id=m if is_before else "",
                    after_id="" if is_before else m,
                    provenance=Provenance(method=Method.STRUCTURAL_MATCH, confidence=Confidence.UNKNOWN, note=note),
                    candidate_ids=candidate_ids,
                )
            )

    return changes, matched_before, matched_after, ambiguous_ids


# ---------------------------------------------------------------------------
# Top-level element diff
# ---------------------------------------------------------------------------


def _diff_elements(
    before: GraphSnapshot, after: GraphSnapshot
) -> tuple[list[VersionChange], dict[str, str]]:
    """Returns (changes, match_map) where match_map carries before_id ->
    after_id for every element whose identity survived (same ID, or matched
    as a rename/move)."""
    before_by_id = before.elements_by_id()
    after_by_id = after.elements_by_id()

    common = sorted(before_by_id.keys() & after_by_id.keys())
    only_before = sorted(before_by_id.keys() - after_by_id.keys())
    only_after = sorted(after_by_id.keys() - before_by_id.keys())

    changes: list[VersionChange] = []
    match_map: dict[str, str] = {}

    for eid in common:
        vc = _classify_same_id(before_by_id[eid], after_by_id[eid], before, after)
        changes.append(vc)
        match_map[eid] = eid

    renamed_moved, matched_before, matched_after, ambiguous_ids = _match_renames_moves(
        only_before, only_after, before, after
    )
    changes.extend(renamed_moved)
    for vc in renamed_moved:
        if vc.kind in (ChangeKind.RENAMED, ChangeKind.MOVED):
            match_map[vc.before_id] = vc.after_id

    for eid in only_before:
        if eid in matched_before or eid in ambiguous_ids:
            continue
        changes.append(
            VersionChange(
                id=_change_id(ChangeKind.REMOVED, eid, ""),
                kind=ChangeKind.REMOVED,
                before_id=eid,
                after_id="",
                provenance=Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN),
            )
        )

    for eid in only_after:
        if eid in matched_after or eid in ambiguous_ids:
            continue
        changes.append(
            VersionChange(
                id=_change_id(ChangeKind.ADDED, "", eid),
                kind=ChangeKind.ADDED,
                before_id="",
                after_id=eid,
                provenance=Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN),
            )
        )

    changes.sort(key=lambda c: c.id)
    return changes, match_map


# ---------------------------------------------------------------------------
# Impact
# ---------------------------------------------------------------------------


def _features_changed(vc: VersionChange, before: GraphSnapshot, after: GraphSnapshot) -> tuple[str, ...]:
    changed_id = vc.after_id or vc.before_id
    out: set[str] = set()
    feature_ids = {e.id for e in before.elements if e.kind == ElementKind.FEATURE} | {
        e.id for e in after.elements if e.kind == ElementKind.FEATURE
    }
    for fid in feature_ids:
        sb = before.backward_slice(fid)
        sa = after.backward_slice(fid)
        members_b = set(sb.member_ids) if sb else set()
        members_a = set(sa.member_ids) if sa else set()
        if members_b == members_a:
            continue
        if changed_id in members_b or changed_id in members_a or vc.before_id in members_b or vc.after_id in members_a:
            out.add(fid)
    return tuple(sorted(out))


def _reachability_flip_set(before: GraphSnapshot, after: GraphSnapshot, match_map: dict[str, str]) -> set[str]:
    """Element IDs (in after-version identity where matched) whose forward
    slice reaching a decision sink flipped between versions."""
    flips: set[str] = set()
    reverse_map = {v: k for k, v in match_map.items()}
    after_slice_roots = {s.root_id for s in after.slices if s.direction == "forward"}
    for after_root in after_slice_roots:
        before_root = reverse_map.get(after_root, after_root)
        sb = before.forward_slice(before_root)
        sa = after.forward_slice(after_root)
        reaches_before = bool(sb.reaches_sink_ids) if sb else False
        reaches_after = bool(sa.reaches_sink_ids) if sa else False
        if sb is not None and reaches_before != reaches_after:
            flips.add(after_root)
    return flips


def _findings_key_maps(
    before: GraphSnapshot, after: GraphSnapshot, match_map: dict[str, str]
) -> tuple[dict[tuple, str], dict[tuple, str]]:
    before_keys = {(f.kind, match_map.get(f.element_id, f.element_id)): f.id for f in before.findings}
    after_keys = {(f.kind, f.element_id): f.id for f in after.findings}
    return before_keys, after_keys


def _build_impacts(
    changes: Sequence[VersionChange], before: GraphSnapshot, after: GraphSnapshot, match_map: dict[str, str]
) -> list[Impact]:
    global_flips = _reachability_flip_set(before, after, match_map)
    before_finding_keys, after_finding_keys = _findings_key_maps(before, after, match_map)
    added_finding_keys = set(after_finding_keys) - set(before_finding_keys)
    removed_finding_keys = set(before_finding_keys) - set(after_finding_keys)

    raw: list[Impact] = []
    for vc in changes:
        if vc.kind == ChangeKind.UNCHANGED:
            continue

        slice_after = after.forward_slice(vc.after_id) if vc.after_id else None
        slice_before = before.forward_slice(vc.before_id) if vc.before_id else None
        affected = tuple(
            sorted(
                set(slice_after.member_ids if slice_after else ())
                | set(slice_before.member_ids if slice_before else ())
            )
        )

        reaches_before = bool(slice_before.reaches_sink_ids) if slice_before else False
        reaches_after = bool(slice_after.reaches_sink_ids) if slice_after else False
        sink_set_before = set(slice_before.reaches_sink_ids) if slice_before else set()
        sink_set_after = set(slice_after.reaches_sink_ids) if slice_after else set()
        decision_paths_changed = (reaches_before != reaches_after) or (sink_set_before != sink_set_after)

        features_changed = _features_changed(vc, before, after)

        changed_id = vc.after_id or vc.before_id
        reachability_flipped = tuple(
            sorted(global_flips & (set(affected) | {changed_id}))
        )

        changed_key_id = vc.after_id or match_map.get(vc.before_id, vc.before_id)
        findings_added = tuple(
            sorted(
                fid
                for key, fid in after_finding_keys.items()
                if key in added_finding_keys and key[1] == changed_key_id
            )
        )
        findings_removed = tuple(
            sorted(
                fid
                for key, fid in before_finding_keys.items()
                if key in removed_finding_keys and key[1] == changed_key_id
            )
        )

        raw.append(
            Impact(
                id=f"impact::{vc.id}",
                change_id=vc.id,
                affected_ids=affected,
                decision_paths_changed=bool(decision_paths_changed),
                features_changed=features_changed,
                reachability_flipped=reachability_flipped,
                findings_added=findings_added,
                findings_removed=findings_removed,
                rank=0,
            )
        )

    def rank_key(impact: Impact) -> tuple:
        return (
            0 if impact.decision_paths_changed else 1,
            0 if impact.reachability_flipped else 1,
            0 if (impact.findings_added or impact.findings_removed) else 1,
            -len(impact.affected_ids),
            impact.change_id,
        )

    ordered = sorted(raw, key=rank_key)
    ranked = [
        Impact(
            id=impact.id,
            change_id=impact.change_id,
            affected_ids=impact.affected_ids,
            decision_paths_changed=impact.decision_paths_changed,
            features_changed=impact.features_changed,
            reachability_flipped=impact.reachability_flipped,
            findings_added=impact.findings_added,
            findings_removed=impact.findings_removed,
            rank=i + 1,
        )
        for i, impact in enumerate(ordered)
    ]
    ranked.sort(key=lambda imp: imp.id)
    return ranked


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def diff_snapshots(before: GraphSnapshot, after: GraphSnapshot) -> tuple[list[VersionChange], list[Impact]]:
    changes, match_map = _diff_elements(before, after)
    impacts = _build_impacts(changes, before, after, match_map)
    return changes, impacts


class Differ:
    """Implements `DiffCard`. `diff()` loads each version's Mode B graph from
    its `out/<label>/` artifact directory and delegates to `diff_snapshots`."""

    def diff(self, before_root: str, after_root: str) -> tuple[Sequence[VersionChange], Sequence[Impact]]:
        before = load_snapshot(before_root)
        after = load_snapshot(after_root)
        return diff_snapshots(before, after)
