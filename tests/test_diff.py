"""Tests for card 6 -- version diff and impact.

Fixture gap: `tests/fixtures/versions/` is empty. None of the `dif_*` cases
specified in `docs/design/FIXTURES.md` (dif_added_removed, dif_rename,
dif_move, dif_format_only, dif_signature, dif_wiring, dif_impact_rank,
dif_symmetric) exist on disk -- card 8 has not produced them yet. Every test
below is named after the case it stands in for and constructs the smallest
`GraphSnapshot` that exercises the same behaviour the case describes, per the
card-6 prompt's instruction to "test with values you construct" when the real
fixture is missing. These are not a substitute for the real corpus cases and
should be replaced by fixture-driven tests once `tests/fixtures/versions/`
exists.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cascade_map.contracts.interfaces import (
    ChangeKind,
    Confidence,
    Edge,
    EdgeKind,
    Element,
    ElementKind,
    Finding,
    FindingKind,
    Method,
    Provenance,
    Slice,
    SourceSpan,
    VersionChange,
    canonical_dumps,
    canonical_jsonl,
)
from cascade_map.diff import Differ, GraphSnapshot, diff_snapshots, load_snapshot

REPO_ROOT = Path(__file__).resolve().parents[1]

_CERTAIN = Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN)


def elem(
    id: str,
    *,
    kind: ElementKind = ElementKind.FUNCTION,
    name: str | None = None,
    qualname: str | None = None,
    module: str | None = None,
    line: int = 1,
    end_line: int | None = None,
    signature: str = "",
    decorators: tuple[str, ...] = (),
    content_hash: str | None = None,
    docstring: str = "",
) -> Element:
    if content_hash is None:
        content_hash = f"hash::{id}"  # unique per element unless a test needs a collision
    if module is None:
        module = id.split("::", 1)[0]
    if qualname is None:
        qualname = id.split("::", 1)[1] if "::" in id else ""
    if name is None:
        name = qualname.rsplit(".", 1)[-1] if qualname else module
    return Element(
        id=id,
        kind=kind,
        name=name,
        qualname=qualname,
        module=module,
        span=SourceSpan(path=f"{module.replace('.', '/')}.py", line=line, end_line=end_line),
        provenance=_CERTAIN,
        content_hash=content_hash,
        decorators=decorators,
        signature=signature,
        docstring=docstring,
    )


def fwd_slice(root_id: str, members: tuple[str, ...] = (), sinks: tuple[str, ...] = ()) -> Slice:
    return Slice(
        id=f"slice::forward::{root_id}",
        root_id=root_id,
        direction="forward",
        member_ids=members,
        edge_ids=(),
        barrier_ids=(),
        reaches_sink_ids=sinks,
        confidence=Confidence.RESOLVED,
    )


def bwd_slice(root_id: str, members: tuple[str, ...] = ()) -> Slice:
    return Slice(
        id=f"slice::backward::{root_id}",
        root_id=root_id,
        direction="backward",
        member_ids=members,
        edge_ids=(),
        barrier_ids=(),
        reaches_sink_ids=(),
        confidence=Confidence.RESOLVED,
    )


def snap(label: str, elements: tuple[Element, ...], **kwargs) -> GraphSnapshot:
    return GraphSnapshot(label=label, elements=elements, **kwargs)


def kinds_of(changes) -> dict[tuple[str, str], ChangeKind]:
    return {(c.before_id, c.after_id): c.kind for c in changes}


# ---------------------------------------------------------------------------
# dif_added_removed
# ---------------------------------------------------------------------------


def test_dif_added_removed() -> None:
    before = snap("before", (elem("m::a"), elem("m::b")))
    after = snap("after", (elem("m::a"), elem("m::c")))

    changes, _ = diff_snapshots(before, after)
    k = kinds_of(changes)

    assert k[("m::a", "m::a")] == ChangeKind.UNCHANGED
    assert k[("m::b", "")] == ChangeKind.REMOVED
    assert k[("", "m::c")] == ChangeKind.ADDED
    for c in changes:
        # ADDED/REMOVED are CERTAIN (presence/absence is a direct fact);
        # UNCHANGED here falls back to content_hash equality (no source_root
        # was given) which is HEURISTIC -- not formatting-invariant, per the
        # documented fallback chain.
        assert c.provenance.confidence in (Confidence.CERTAIN, Confidence.HEURISTIC)


# ---------------------------------------------------------------------------
# dif_rename
# ---------------------------------------------------------------------------


def test_dif_rename() -> None:
    before = snap("before", (elem("m::old_name", content_hash="SAME", signature="(x)"),))
    after = snap("after", (elem("m::new_name", content_hash="SAME", signature="(x)"),))

    changes, _ = diff_snapshots(before, after)
    assert len(changes) == 1
    vc = changes[0]
    assert vc.kind == ChangeKind.RENAMED
    assert vc.before_id == "m::old_name"
    assert vc.after_id == "m::new_name"
    assert vc.provenance.method == Method.STRUCTURAL_MATCH
    assert vc.provenance.confidence in (Confidence.PROBABLE, Confidence.HEURISTIC)
    assert vc.provenance.note  # evidence is carried, not silent


def test_dif_rename_ambiguous_is_not_resolved_arbitrarily() -> None:
    """Two before-elements and two after-elements, all with identical bodies
    and signatures, so every cross-pair scores equally. Neither side may be
    resolved to a specific match: both become ADDED/REMOVED with UNKNOWN
    confidence and the tied candidates on record."""
    before = snap(
        "before",
        (
            elem("m::helper_a", content_hash="SAME", signature="(x)"),
            elem("m::helper_b", content_hash="SAME", signature="(x)"),
        ),
    )
    after = snap(
        "after",
        (
            elem("m::helper_x", content_hash="SAME", signature="(x)"),
            elem("m::helper_y", content_hash="SAME", signature="(x)"),
        ),
    )

    changes, _ = diff_snapshots(before, after)
    k = kinds_of(changes)
    assert k[("m::helper_a", "")] == ChangeKind.REMOVED
    assert k[("m::helper_b", "")] == ChangeKind.REMOVED
    assert k[("", "m::helper_x")] == ChangeKind.ADDED
    assert k[("", "m::helper_y")] == ChangeKind.ADDED
    for c in changes:
        assert c.provenance.confidence == Confidence.UNKNOWN
        assert "ambiguous" in c.provenance.note


# ---------------------------------------------------------------------------
# dif_move
# ---------------------------------------------------------------------------


def test_dif_move() -> None:
    before = snap(
        "before",
        (elem("pkg.a::helper", module="pkg.a", qualname="helper", name="helper", content_hash="SAME"),),
    )
    after = snap(
        "after",
        (elem("pkg.b::helper", module="pkg.b", qualname="helper", name="helper", content_hash="SAME"),),
    )

    changes, _ = diff_snapshots(before, after)
    assert len(changes) == 1
    vc = changes[0]
    assert vc.kind == ChangeKind.MOVED
    assert vc.before_id == "pkg.a::helper"
    assert vc.after_id == "pkg.b::helper"


# ---------------------------------------------------------------------------
# dif_format_only -- exercises the tokenize normalisation path specifically
# ---------------------------------------------------------------------------


def test_dif_format_only(tmp_path: Path) -> None:
    before_dir = tmp_path / "before"
    after_dir = tmp_path / "after"
    before_dir.mkdir()
    after_dir.mkdir()

    (before_dir / "mod.py").write_text(
        "def compute(x):\n"
        "    # add one\n"
        "    y = x + 1\n"
        "    return y\n",
        encoding="utf-8",
    )
    (after_dir / "mod.py").write_text(
        "def compute(x):\n"
        "\n"
        "    y = x + 1  # add one, reformatted\n"
        "\n"
        "    return y\n",
        encoding="utf-8",
    )

    def make(span_end: int, content_hash: str) -> Element:
        return Element(
            id="mod::compute",
            kind=ElementKind.FUNCTION,
            name="compute",
            qualname="compute",
            module="mod",
            span=SourceSpan(path="mod.py", line=1, end_line=span_end),
            provenance=_CERTAIN,
            content_hash=content_hash,
            signature="(x)",
        )

    # deliberately different content_hash on each side, as raw source hashes
    # of a reformatted file would be -- proves UNCHANGED comes from the
    # token-normalized comparison, not from a hash that happens to match.
    before = snap("before", (make(4, "hash-before"),), source_root=str(before_dir))
    after = snap("after", (make(5, "hash-after"),), source_root=str(after_dir))

    changes, _ = diff_snapshots(before, after)
    assert len(changes) == 1
    vc = changes[0]
    assert vc.kind == ChangeKind.UNCHANGED
    assert "token-normalized" in vc.provenance.note


def test_dif_format_only_real_body_change_is_detected(tmp_path: Path) -> None:
    """Same harness as above, but the logic actually changes -- proves the
    normalizer is not simply always reporting UNCHANGED."""
    before_dir = tmp_path / "before"
    after_dir = tmp_path / "after"
    before_dir.mkdir()
    after_dir.mkdir()
    (before_dir / "mod.py").write_text("def compute(x):\n    return x + 1\n", encoding="utf-8")
    (after_dir / "mod.py").write_text("def compute(x):\n    return x + 2\n", encoding="utf-8")

    def make(root: Path) -> Element:
        return Element(
            id="mod::compute",
            kind=ElementKind.FUNCTION,
            name="compute",
            qualname="compute",
            module="mod",
            span=SourceSpan(path="mod.py", line=1, end_line=2),
            provenance=_CERTAIN,
            content_hash="irrelevant",
            signature="(x)",
        )

    before = snap("before", (make(before_dir),), source_root=str(before_dir))
    after = snap("after", (make(after_dir),), source_root=str(after_dir))

    changes, _ = diff_snapshots(before, after)
    assert changes[0].kind == ChangeKind.BODY_CHANGED
    assert changes[0].provenance.confidence == Confidence.RESOLVED


# ---------------------------------------------------------------------------
# dif_signature
# ---------------------------------------------------------------------------


def test_dif_signature() -> None:
    before = snap("before", (elem("m::f", signature="(x)", content_hash="SAME_BODY"),))
    after = snap("after", (elem("m::f", signature="(x, y=1)", content_hash="SAME_BODY"),))

    changes, _ = diff_snapshots(before, after)
    assert len(changes) == 1
    vc = changes[0]
    assert vc.kind == ChangeKind.SIGNATURE_CHANGED
    assert vc.provenance.confidence == Confidence.CERTAIN


def test_dif_signature_distinguished_from_body_change() -> None:
    """Body differs but signature is identical -> BODY_CHANGED, not
    SIGNATURE_CHANGED; the two must not be conflated."""
    before = snap("before", (elem("m::f", signature="(x)", content_hash="A"),))
    after = snap("after", (elem("m::f", signature="(x)", content_hash="B"),))

    changes, _ = diff_snapshots(before, after)
    assert changes[0].kind == ChangeKind.BODY_CHANGED


# ---------------------------------------------------------------------------
# dif_wiring -- a config key repointed to a different class
# ---------------------------------------------------------------------------


def test_dif_wiring_config_repoint() -> None:
    key_id = "@file:config/wiring.json::/components/0/class"
    old_target = elem("engine.rules::OldRule", module="engine.rules", qualname="OldRule", name="OldRule")
    new_target = elem("engine.rules::NewRule", module="engine.rules", qualname="NewRule", name="NewRule")
    key_before = Element(
        id=key_id,
        kind=ElementKind.CONFIG_KEY,
        name="/components/0/class",
        qualname="/components/0/class",
        module="config/wiring.json",
        span=SourceSpan(path="config/wiring.json", line=1),
        provenance=_CERTAIN,
        content_hash="h",
        signature="engine.rules.OldRule",
    )
    key_after = Element(
        id=key_id,
        kind=ElementKind.CONFIG_KEY,
        name="/components/0/class",
        qualname="/components/0/class",
        module="config/wiring.json",
        span=SourceSpan(path="config/wiring.json", line=1),
        provenance=_CERTAIN,
        content_hash="h",
        signature="engine.rules.NewRule",
    )

    before_edge = Edge(
        id="edge::before",
        kind=EdgeKind.CONFIGURES,
        source_id=key_id,
        target_id=old_target.id,
        provenance=_CERTAIN,
    )
    after_edge = Edge(
        id="edge::after",
        kind=EdgeKind.CONFIGURES,
        source_id=key_id,
        target_id=new_target.id,
        provenance=_CERTAIN,
    )

    before = snap(
        "before",
        (old_target, key_before),
        edges=(before_edge,),
        slices=(fwd_slice(key_id, members=(old_target.id,), sinks=()),),
    )
    after = snap(
        "after",
        (new_target, key_after),
        edges=(after_edge,),
        slices=(fwd_slice(key_id, members=(new_target.id,), sinks=("sink::final",)),),
    )

    changes, impacts = diff_snapshots(before, after)
    k = kinds_of(changes)
    # the config key's own identity is stable (same path/pointer); its
    # repointed value is a SIGNATURE_CHANGED on that element, per the
    # module-docstring's documented mapping for wiring changes.
    assert k[(key_id, key_id)] == ChangeKind.SIGNATURE_CHANGED
    assert k[("", new_target.id)] == ChangeKind.ADDED
    assert k[(old_target.id, "")] == ChangeKind.REMOVED

    key_impact = next(i for i in impacts if i.change_id.endswith(f"{key_id}::{key_id}"))
    assert new_target.id in key_impact.affected_ids
    assert key_impact.decision_paths_changed is True


# ---------------------------------------------------------------------------
# dif_impact_rank -- a one-line decision-condition change outranks a large
# refactor that reaches no sink
# ---------------------------------------------------------------------------


def test_dif_impact_rank() -> None:
    before = snap(
        "before",
        (
            elem("m::decision_input", content_hash="A"),
            elem("m::big_refactor", content_hash="A"),
        ),
        slices=(
            fwd_slice("m::decision_input", members=("m::decision_input", "sink::final"), sinks=()),
            fwd_slice(
                "m::big_refactor",
                members=tuple(f"m::unreachable_{i}" for i in range(50)),
                sinks=(),
            ),
        ),
    )
    after = snap(
        "after",
        (
            elem("m::decision_input", content_hash="B"),  # one-line change
            elem("m::big_refactor", content_hash="B"),  # large rewrite
        ),
        slices=(
            fwd_slice(
                "m::decision_input", members=("m::decision_input", "sink::final"), sinks=("sink::final",)
            ),
            fwd_slice(
                "m::big_refactor",
                members=tuple(f"m::unreachable_{i}" for i in range(50)),
                sinks=(),
            ),
        ),
    )

    changes, impacts = diff_snapshots(before, after)
    by_change = {i.change_id: i for i in impacts}
    small_change = next(c for c in changes if c.before_id == "m::decision_input")
    big_change = next(c for c in changes if c.before_id == "m::big_refactor")

    small_impact = by_change[small_change.id]
    big_impact = by_change[big_change.id]

    assert small_impact.decision_paths_changed is True
    assert big_impact.decision_paths_changed is False
    assert len(big_impact.affected_ids) > len(small_impact.affected_ids)
    assert small_impact.rank < big_impact.rank


# ---------------------------------------------------------------------------
# dif_symmetric -- A->B and B->A agree
# ---------------------------------------------------------------------------


def _symmetric_key(vc: VersionChange) -> tuple:
    family = {
        ChangeKind.ADDED: "ADD_REMOVE",
        ChangeKind.REMOVED: "ADD_REMOVE",
    }.get(vc.kind, vc.kind.value)
    return (family, tuple(sorted({vc.before_id, vc.after_id} - {""})))


def test_dif_symmetric() -> None:
    before = snap(
        "before",
        (
            elem("m::same", content_hash="X"),
            elem("m::gone", content_hash="Y"),
            elem("m::old_name", content_hash="SAME", signature="(x)"),
            elem("m::changed", content_hash="A"),
        ),
    )
    after = snap(
        "after",
        (
            elem("m::same", content_hash="X"),
            elem("m::new_thing", content_hash="Z"),
            elem("m::new_name", content_hash="SAME", signature="(x)"),
            elem("m::changed", content_hash="B"),
        ),
    )

    forward, _ = diff_snapshots(before, after)
    backward, _ = diff_snapshots(after, before)

    forward_keys = sorted(_symmetric_key(c) for c in forward)
    backward_keys = sorted(_symmetric_key(c) for c in backward)
    assert forward_keys == backward_keys


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_two_runs_are_byte_identical() -> None:
    before = snap(
        "before",
        (elem("m::a", content_hash="1"), elem("m::b", content_hash="2")),
        findings=(
            Finding(
                id="f1",
                kind=FindingKind.UNREACHABLE_ELEMENT,
                element_id="m::b",
                span=SourceSpan(path="m.py", line=1),
                summary="unreachable",
                hint="",
                evidence_ids=("m::b",),
                provenance=_CERTAIN,
            ),
        ),
    )
    after = snap("after", (elem("m::a", content_hash="1"),))

    changes1, impacts1 = diff_snapshots(before, after)
    changes2, impacts2 = diff_snapshots(before, after)

    assert canonical_jsonl(changes1) == canonical_jsonl(changes2)
    assert canonical_jsonl(impacts1) == canonical_jsonl(impacts2)


# ---------------------------------------------------------------------------
# load_snapshot round-trip through the canonical artifact format
# ---------------------------------------------------------------------------


def test_load_snapshot_round_trip(tmp_path: Path) -> None:
    out = tmp_path / "out" / "v1"
    out.mkdir(parents=True)
    e = elem("m::f", content_hash="1")
    (out / "elements.jsonl").write_text(canonical_jsonl([e]), encoding="utf-8")

    loaded = load_snapshot(out)
    assert loaded.elements == (e,)
    assert loaded.edges == ()
    assert loaded.findings == ()


def test_load_snapshot_missing_files_degrade_to_empty(tmp_path: Path) -> None:
    out = tmp_path / "out" / "v1"
    out.mkdir(parents=True)
    loaded = load_snapshot(out)
    assert loaded.elements == ()
    assert loaded.edges == ()


def test_differ_implements_diffcard_protocol(tmp_path: Path) -> None:
    before_out = tmp_path / "before"
    after_out = tmp_path / "after"
    before_out.mkdir()
    after_out.mkdir()
    (before_out / "elements.jsonl").write_text(
        canonical_jsonl([elem("m::a", content_hash="1")]), encoding="utf-8"
    )
    (after_out / "elements.jsonl").write_text(
        canonical_jsonl([elem("m::a", content_hash="1")]), encoding="utf-8"
    )

    differ = Differ()
    changes, impacts = differ.diff(str(before_out), str(after_out))
    assert len(changes) == 1
    assert changes[0].kind == ChangeKind.UNCHANGED


# ---------------------------------------------------------------------------
# Sentinel -- proves this card never executes target code
# ---------------------------------------------------------------------------


def test_sentinel_not_executed() -> None:
    marker = Path("/tmp/cascade_map_sentinel_marker.txt")
    marker.unlink(missing_ok=True)

    sentinel_path = REPO_ROOT / "tests" / "fixtures" / "sentinel" / "__init__.py"
    sentinel_elem = Element(
        id="sentinel",
        kind=ElementKind.MODULE,
        name="sentinel",
        qualname="",
        module="sentinel",
        span=SourceSpan(path="tests/fixtures/sentinel/__init__.py", line=1, end_line=20),
        provenance=_CERTAIN,
        content_hash="h",
    )
    before = snap("before", (sentinel_elem,), source_root=str(REPO_ROOT))
    after = snap(
        "after",
        (
            Element(
                id="sentinel",
                kind=ElementKind.MODULE,
                name="sentinel",
                qualname="",
                module="sentinel",
                span=SourceSpan(path="tests/fixtures/sentinel/__init__.py", line=1, end_line=21),
                provenance=_CERTAIN,
                content_hash="h2",
            ),
        ),
        source_root=str(REPO_ROOT),
    )

    assert sentinel_path.exists()
    diff_snapshots(before, after)  # reads and tokenizes the sentinel's own source

    assert not marker.exists(), "diff.py executed the sentinel fixture"
