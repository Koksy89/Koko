"""`SliceScope` — how many slices are stored, never how complete one is.

`slices.jsonl` is the one artifact that is quadratic in OUTPUT: a slice per
root, each holding O(graph) ids. Measured on a single-module target it reaches
211 MB for 1.4 MB of source and gigabytes for the 14.8 MB engine this tool was
built for, which is why `SliceScope` exists.

The two properties that make scoping safe rather than lossy are asserted here:

1. **No slice is ever truncated.** A slice that is emitted is byte-identical to
   the slice computed on demand for the same root. Scoping removes whole
   slices; it never shortens one. A half-slice answering "what produces this
   feature" would be a wrong answer wearing the shape of a right one.
2. **The scope cannot change a finding.** Findings are facts and the scope is a
   storage decision. `analyze` therefore hands card 5 the same basis at every
   scope, and `findings.jsonl` is compared byte for byte across all three on
   the whole corpus and on every fixture that declares a decision sink -- the
   only fixtures where a slice can reach a finding at all.

And the disclosure, because a scoped artifact that reports only its own count
reads exactly like an exhaustive one: the scope and the number of roots NOT
precomputed appear in the summary and in `manifest.json`.

Nothing here executes fixture code; `analyze` is Mode B throughout.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cascade_map.cli import analyze
from cascade_map.contracts.interfaces import SliceScope, canonical_jsonl
from cascade_map.ingest.inventory import Ingestor
from cascade_map.lineage import LineageTracer
from cascade_map.resolve import Resolver

CORPUS = Path(__file__).parent / "fixtures" / "mode_b"
SCOPES = (SliceScope.DECISION, SliceScope.ALL, SliceScope.NONE)


def _sink_fixtures() -> list[Path]:
    """Fixtures that declare a decision sink.

    Only these can exercise the path where a slice reaches a finding:
    `Slice.reaches_sink_ids` is empty everywhere when no sink is declared, and
    the two places card 5 reads a slice both gate on it.
    """
    out: list[Path] = []
    for case in sorted(CORPUS.iterdir()):
        spec = case / "expected.json"
        if not spec.is_file():
            continue
        payload = json.loads(spec.read_text(encoding="utf-8"))
        if payload.get("declared_sink_ids"):
            out.append(case)
    return out


#: The corpus declares no sink of its own, so every run that needs DECISION to
#: have a principled root set passes this one explicitly. That is the whole
#: point of the scope: it is bounded by the owner's declaration.
CORPUS_SINK = ("mode_b.dec_sink::final_decision",)


def _run(
    root: Path,
    out: Path,
    scope: SliceScope,
    sinks: tuple[str, ...] = (),
    entries: tuple[str, ...] = (),
    **kwargs: object,
) -> Path:
    _code, _summary = _run_full(root, out, scope, sinks, entries, **kwargs)
    return out


def _run_full(
    root: Path,
    out: Path,
    scope: SliceScope,
    sinks: tuple[str, ...] = (),
    entries: tuple[str, ...] = (),
    say: list[str] | None = None,
    **kwargs: object,
) -> tuple[int, dict]:
    return analyze(
        root,
        out,
        sink_ids=sinks,
        entry_ids=entries,
        slice_scope=scope,
        cache_dir=out / "cache",
        strict_gate=False,
        worker_report_sink=(say.append if say is not None else (lambda _text: None)),
        **kwargs,  # type: ignore[arg-type]
    )


def _read(out: Path, name: str) -> str:
    return (out / name).read_text(encoding="utf-8")


def _slices(out: Path) -> list[dict]:
    text = _read(out, "slices.jsonl")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# The scope changes how many, never how complete
# ---------------------------------------------------------------------------


def test_a_scoped_slice_is_byte_identical_to_the_same_slice_computed_alone(
    tmp_path: Path,
) -> None:
    """Every slice DECISION writes equals the one ALL writes for that root.

    This is the property the whole feature rests on. If scoping ever shortened
    a slice, the two would differ here and nowhere else -- the counts would
    still look plausible.
    """
    decision = _slices(
        _run(CORPUS, tmp_path / "decision", SliceScope.DECISION, CORPUS_SINK)
    )
    everything = {one["id"]: one for one in _slices(
        _run(CORPUS, tmp_path / "all", SliceScope.ALL, CORPUS_SINK)
    )}
    assert decision, "no slices at DECISION scope; the comparison proves nothing"
    for one in decision:
        assert one["id"] in everything, f"{one['id']} vanished at ALL scope"
        twin = everything[one["id"]]
        # `scope` is the one field that is SUPPOSED to differ: it names the
        # emission the record came out of, which is the whole point of
        # carrying it on the record rather than only in manifest.json. Every
        # other field -- above all `member_ids` -- must be identical, because
        # a scope may drop a slice and never shorten one.
        assert one["scope"] == "DECISION"
        assert twin["scope"] == "ALL"
        assert {k: v for k, v in one.items() if k != "scope"} == {
            k: v for k, v in twin.items() if k != "scope"
        }, (
            f"{one['id']} differs between scopes: a scope may drop a slice, "
            f"never shorten one"
        )


def test_decision_roots_are_a_subset_of_all_roots(tmp_path: Path) -> None:
    decision = {one["root_id"] for one in _slices(
        _run(CORPUS, tmp_path / "decision", SliceScope.DECISION, CORPUS_SINK)
    )}
    everything = {one["root_id"] for one in _slices(
        _run(CORPUS, tmp_path / "all", SliceScope.ALL, CORPUS_SINK)
    )}
    assert decision, "DECISION precomputed nothing"
    assert decision <= everything


def test_none_writes_no_slices_and_lineage_is_still_whole(tmp_path: Path) -> None:
    none = _run(CORPUS, tmp_path / "none", SliceScope.NONE)
    everything = _run(CORPUS, tmp_path / "all", SliceScope.ALL)
    assert _read(none, "slices.jsonl") == ""
    # The claim NONE makes is that every slice stays recomputable, which is
    # only true if the lineage graph is emitted in full regardless.
    assert _read(none, "lineage.jsonl") == _read(everything, "lineage.jsonl")
    assert _read(none, "barriers.jsonl") == _read(everything, "barriers.jsonl")


def test_slice_root_is_precomputed_at_every_scope_including_none(
    tmp_path: Path,
) -> None:
    """Chasing one feature must not mean switching to the exhaustive mode."""
    with_cache = tmp_path / "probe"
    ingestor = Ingestor(cache_dir=with_cache, workers=1)
    elements, _ = ingestor.inventory(str(CORPUS))
    edges, _ = Resolver(CORPUS).resolve(elements)
    tracer = LineageTracer(CORPUS, sink_ids=())
    tracer.trace_values(elements, edges)
    roots = tracer.all_slice_roots()
    assert roots, "no roots in the corpus; the test proves nothing"
    chosen = roots[0]

    for scope in SCOPES:
        out = tmp_path / f"root_{scope}"
        analyze(
            CORPUS,
            out,
            slice_scope=scope,
            slice_roots=(chosen,),
            cache_dir=out / "cache",
            strict_gate=False,
            worker_report_sink=lambda _text: None,
        )
        rooted = {one["root_id"] for one in _slices(out)}
        assert chosen in rooted, f"--slice-root ignored at scope {scope}"


# ---------------------------------------------------------------------------
# The scope cannot change a finding
# ---------------------------------------------------------------------------


def test_findings_are_identical_at_every_scope_over_the_whole_corpus(
    tmp_path: Path,
) -> None:
    produced = {
        scope: _read(_run(CORPUS, tmp_path / str(scope), scope), "findings.jsonl")
        for scope in SCOPES
    }
    assert produced[SliceScope.DECISION].strip(), "no findings; nothing compared"
    assert produced[SliceScope.DECISION] == produced[SliceScope.ALL]
    assert produced[SliceScope.DECISION] == produced[SliceScope.NONE]


def _declared(case: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    spec = json.loads((case / "expected.json").read_text(encoding="utf-8"))
    return tuple(spec.get("declared_sink_ids", ())), tuple(spec.get("entry_ids", ()))


@pytest.mark.parametrize("case", _sink_fixtures(), ids=lambda p: p.name)
def test_findings_are_identical_at_every_scope_per_sink_fixture(
    case: Path, tmp_path: Path
) -> None:
    """Per fixture, and specifically the ones with a declared sink.

    `Slice.reaches_sink_ids` is what card 5 gates on and it is empty everywhere
    when no sink is declared, so a run with no sinks would pass this test while
    saying nothing about the path that matters. The entry points come from the
    same `expected.json`: without them `_decision_irrelevant` returns early and
    every fixture produces no findings at all, which would pass vacuously.
    """
    sinks, entries = _declared(case)
    assert sinks, "parametrised on fixtures that declare a sink"
    produced = {
        scope: _read(
            _run(case, tmp_path / str(scope), scope, sinks, entries), "findings.jsonl"
        )
        for scope in SCOPES
    }
    assert produced[SliceScope.DECISION] == produced[SliceScope.ALL]
    assert produced[SliceScope.DECISION] == produced[SliceScope.NONE]


def test_the_per_fixture_comparison_is_not_comparing_nothing(tmp_path: Path) -> None:
    """The guard on the test above: some of those fixtures must yield findings.

    Two of the three defects this project recorded as "a test passing for the
    wrong reason" were exactly this shape -- a comparison of two empty things.
    """
    total = 0
    reaching = 0
    for case in _sink_fixtures():
        sinks, entries = _declared(case)
        out = _run(case, tmp_path / case.name, SliceScope.DECISION, sinks, entries)
        total += len(
            [line for line in _read(out, "findings.jsonl").splitlines() if line.strip()]
        )
        reaching += sum(1 for one in _slices(out) if one["reaches_sink_ids"])
    assert total > 0, "no findings anywhere; the per-fixture comparison is vacuous"
    assert reaching > 0, (
        "no slice reaches a sink; the branch of card 5 that reads a slice is "
        "never entered and the comparison proves nothing about it"
    )


def test_analyze_hands_card_five_the_same_slices_at_every_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Structural, not sampled: the basis card 5 reasons from is captured.

    Comparing `findings.jsonl` across scopes shows they agree on this corpus.
    This shows they must: the scope never reaches card 5 at all. It is the
    stronger statement, and it holds for inputs the corpus does not contain --
    `_unconsumed_features` in particular reads slices and cannot fire here,
    because nothing in the pipeline inventories a FEATURE element.
    """
    import cascade_map.cli as cli_module

    seen: dict[str, tuple[str, ...]] = {}
    original = cli_module.Findings

    class Recording(original):  # type: ignore[misc,valid-type]
        def __init__(self, **kwargs: object) -> None:
            seen[Recording.scope] = tuple(
                sorted(one.id for one in kwargs.get("slices", ()))  # type: ignore[union-attr]
            )
            super().__init__(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(cli_module, "Findings", Recording)
    sinks = ("mode_b.dec_sink::final_decision",)
    for scope in SCOPES:
        Recording.scope = str(scope)  # type: ignore[attr-defined]
        _run(CORPUS, tmp_path / str(scope), scope, sinks)

    assert len(seen) == len(SCOPES)
    bases = set(seen.values())
    assert len(bases) == 1, f"card 5 saw different slices per scope: { {k: len(v) for k, v in seen.items()} }"
    assert next(iter(bases)), "card 5 saw no slices at all; the check is vacuous"


# ---------------------------------------------------------------------------
# Disclosure: a scoped artifact must never read as an exhaustive one
# ---------------------------------------------------------------------------


def test_manifest_records_the_scope_and_what_was_left_out(tmp_path: Path) -> None:
    for scope in SCOPES:
        out = _run(CORPUS, tmp_path / str(scope), scope)
        manifest = json.loads(_read(out, "manifest.json"))
        disclosed = manifest["slice_scope"]
        assert disclosed["scope"] == str(scope)
        assert disclosed["slices_written"] == len(_slices(out))
        assert (
            disclosed["roots_not_precomputed"]
            == disclosed["roots_total"] - disclosed["roots_precomputed"]
        )
        assert "lineage.jsonl" in disclosed["note"]
    # The point of the disclosure is that the narrow scopes admit it.
    narrow = json.loads(
        _read(tmp_path / str(SliceScope.NONE), "manifest.json")
    )["slice_scope"]
    assert narrow["roots_not_precomputed"] > 0


def test_summary_names_the_scope_and_the_roots_not_precomputed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from cascade_map.cli import _report

    out = tmp_path / "decision"
    _, summary = analyze(
        CORPUS,
        out,
        slice_scope=SliceScope.DECISION,
        cache_dir=out / "cache",
        strict_gate=False,
        worker_report_sink=lambda _text: None,
    )
    text = _report(summary, out, 0)
    assert "scope DECISION" in text
    assert "recomputable from lineage.jsonl" in text
    assert "roots not" in text


def test_all_scope_states_its_cost_before_paying_it(tmp_path: Path) -> None:
    said: list[str] = []
    out = tmp_path / "all"
    analyze(
        CORPUS,
        out,
        slice_scope=SliceScope.ALL,
        cache_dir=out / "cache",
        strict_gate=False,
        worker_report_sink=said.append,
    )
    spoken = "\n".join(said)
    assert "--slices all" in spoken
    assert "quadratic in OUTPUT" in spoken
    # It must name the scope that avoids the cost, or it is a complaint rather
    # than a choice.
    assert "--slices decision" in spoken
    assert "slices.jsonl will be about" in spoken


def test_narrow_scopes_say_nothing_about_cost(tmp_path: Path) -> None:
    """Only ALL warns. A warning on every run is a warning nobody reads."""
    for scope in (SliceScope.DECISION, SliceScope.NONE):
        said: list[str] = []
        out = tmp_path / str(scope)
        analyze(
            CORPUS,
            out,
            slice_scope=scope,
            cache_dir=out / "cache",
            strict_gate=False,
            worker_report_sink=said.append,
        )
        assert "quadratic in OUTPUT" not in "\n".join(said)


# ---------------------------------------------------------------------------
# Settings and flags
# ---------------------------------------------------------------------------


def test_slices_setting_and_flag_agree_and_reject_a_typo() -> None:
    from cascade_map.cli import _build_parser, _slice_scope_of
    from cascade_map.ledger import Settings, SettingsError

    parser = _build_parser()
    assert _slice_scope_of(parser.parse_args(["analyze", "."])) is SliceScope.DECISION
    assert (
        _slice_scope_of(parser.parse_args(["analyze", ".", "--slices", "all"]))
        is SliceScope.ALL
    )
    assert Settings.from_mapping({"SLICES": "none"}).slices is SliceScope.NONE
    with pytest.raises(SettingsError) as caught:
        Settings.from_mapping({"SLICES": "some"})
    # An unknown value must list the real ones, never quietly fall back.
    assert "DECISION" in str(caught.value) and "ALL" in str(caught.value)


def test_track_passes_the_scope_through_to_analyze(tmp_path: Path) -> None:
    from cascade_map.ledger import Settings, _default_analyse

    seen: dict[str, object] = {}

    def fake_analyze(root, out_dir, **kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return 0, {}

    import cascade_map.cli as cli_module

    original = cli_module.analyze
    cli_module.analyze = fake_analyze  # type: ignore[assignment]
    try:
        settings = Settings.from_mapping({"SLICES": "all", "SLICE_ROOTS": ["@feature:x"]})
        _default_analyse(tmp_path, tmp_path / "out", settings)
    finally:
        cli_module.analyze = original  # type: ignore[assignment]

    assert seen["slice_scope"] is SliceScope.ALL
    assert "@feature:x" in " ".join(seen["slice_roots"])  # type: ignore[arg-type]


def test_scoped_run_is_byte_identical_twice(tmp_path: Path) -> None:
    first = _run(CORPUS, tmp_path / "one", SliceScope.DECISION)
    second = _run(CORPUS, tmp_path / "two", SliceScope.DECISION)
    for name in ("slices.jsonl", "findings.jsonl", "lineage.jsonl"):
        assert _read(first, name) == _read(second, name)


def test_emitted_slices_round_trip_through_the_canonical_serialiser(
    tmp_path: Path,
) -> None:
    """Guards the shape of `slices.jsonl`: every line is a `Slice` and nothing
    else, because `schema.json` says so and the scope is disclosed elsewhere."""
    rows = _slices(
        _run(CORPUS, tmp_path / "decision", SliceScope.DECISION, CORPUS_SINK)
    )
    assert rows, "no slices written; the shape check would pass on an empty file"
    for row in rows:
        assert set(row) == {
            "id",
            "root_id",
            "direction",
            "member_ids",
            "edge_ids",
            "barrier_ids",
            "reaches_sink_ids",
            "confidence",
            "scope",
        }
        # The record travels: a slices.jsonl copied out of its workspace still
        # says what produced it, without manifest.json beside it.
        assert row["scope"] == "DECISION"
    assert canonical_jsonl([]) == ""


def test_every_written_slice_declares_its_own_scope(tmp_path: Path) -> None:
    """At every scope, and including the finding top-up pass.

    The top-up asks the tracer for `NONE` plus an explicit root list, so it is
    the one place a slice could end up labelled with a scope the run never
    ran at. A file whose records disagree about what produced them is exactly
    the "filtered view mistaken for the whole" failure the field exists to
    stop.
    """
    for scope, label in (
        (SliceScope.DECISION, "DECISION"),
        (SliceScope.ALL, "ALL"),
    ):
        rows = _slices(
            _run(CORPUS, tmp_path / label.lower(), scope, CORPUS_SINK)
        )
        assert rows, f"no slices at {label}; the check would pass vacuously"
        assert {row["scope"] for row in rows} == {label}


def test_scope_survives_the_round_trip_back_into_a_slice(tmp_path: Path) -> None:
    """`slices.jsonl` -> `Slice` -> `slices.jsonl` keeps the label.

    Card 6 reads slices back off disk. A field that serialises but does not
    deserialise would make a re-read run silently claim DECISION.
    """
    from cascade_map.diff import _slice_from_dict

    rows = _slices(_run(CORPUS, tmp_path / "rt", SliceScope.ALL))
    assert rows
    for row in rows:
        assert _slice_from_dict(row).scope == "ALL"
    assert canonical_jsonl(
        [_slice_from_dict(row) for row in rows]
    ) == canonical_jsonl([_slice_from_dict(row) for row in rows])


def test_the_finding_top_up_is_bounded_by_what_a_slice_answers(
    tmp_path: Path,
) -> None:
    """A slice per finding root is the quadratic DECISION exists to avoid.

    Measured on the 14.6 MB single-module target: 19,227 findings with no sink
    declared, which as roots would be 38,454 slices -- more than `ALL` writes.
    Only the two kinds whose evidence IS a data-flow path are topped up.

    The corpus contains findings of other kinds whose element IS a lineage
    node, so this asserts a real exclusion rather than an empty one.
    """
    from cascade_map.cli import _SLICE_EVIDENCED_FINDINGS

    out = _run(CORPUS, tmp_path / "decision", SliceScope.DECISION)
    findings = [
        json.loads(line)
        for line in _read(out, "findings.jsonl").splitlines()
        if line.strip()
    ]
    rooted = {one["root_id"] for one in _slices(out)}
    lineage_nodes = {
        endpoint
        for line in _read(out, "lineage.jsonl").splitlines()
        if line.strip()
        for endpoint in (
            json.loads(line)["source_id"],
            json.loads(line)["target_id"],
        )
    }
    excluded = {
        finding["element_id"]
        for finding in findings
        if finding["kind"] not in {str(kind) for kind in _SLICE_EVIDENCED_FINDINGS}
        and finding["element_id"] in lineage_nodes
    }
    assert excluded, (
        "no finding of an excluded kind sits on a lineage node in this corpus, "
        "so this test asserts nothing"
    )
    # Roots that DECISION reaches on its own, before any finding is consulted.
    ingestor = Ingestor(cache_dir=tmp_path / "probe", workers=1)
    elements, _ = ingestor.inventory(str(CORPUS))
    edges, _ = Resolver(CORPUS).resolve(elements)
    tracer = LineageTracer(CORPUS, sink_ids=())
    tracer.trace_values(elements, edges)
    own = set(tracer.decision_slice_roots())
    included = {
        finding["element_id"]
        for finding in findings
        if finding["kind"] in {str(kind) for kind in _SLICE_EVIDENCED_FINDINGS}
    }
    unexplained = rooted - own - included
    assert not unexplained, (
        f"slices rooted at {sorted(unexplained)} came from neither the scope "
        f"nor a finding a slice answers; the top-up is unbounded again"
    )
    assert excluded - rooted, (
        "every excluded-kind finding still got a slice; the bound did nothing"
    )


# ---------------------------------------------------------------------------
# DECISION is bounded by the owner's declaration, or it emits nothing
#
# A 14.8 MB single-file engine produced a 6.4 GB slices.jsonl at DECISION
# scope: with no declared sink the root set fell back to every feature plus
# the root of every finding -- thousands of roots, each pulling a slice that
# can name much of the codebase. Auto-detected sinks are candidates, and
# building thousands of expensive exact answers on top of a guess is wrong
# twice over. So: no declared sink, no principled roots, no slices, and the
# artifact says exactly that.
# ---------------------------------------------------------------------------


def test_decision_scope_with_no_declared_sink_emits_no_slices(tmp_path: Path) -> None:
    out = _run(CORPUS, tmp_path / "unrooted", SliceScope.DECISION)
    assert _read(out, "slices.jsonl") == ""
    manifest = json.loads(_read(out, "manifest.json"))["slice_scope"]
    assert manifest["slices_written"] == 0
    assert manifest["roots_not_precomputed"] == manifest["roots_total"] > 0


def test_the_corpus_really_has_features_so_that_zero_means_something(
    tmp_path: Path,
) -> None:
    """The guard on the test above.

    "Zero slices" is only evidence of the fix if there were roots to emit. The
    same corpus at ALL scope emits slices rooted at named features, which are
    precisely the roots DECISION used to pull in and no longer does.
    """
    rows = _slices(_run(CORPUS, tmp_path / "all", SliceScope.ALL))
    features = {one["root_id"] for one in rows if one["root_id"].startswith("@feature:")}
    assert len(features) >= 4, f"expected several features, saw {sorted(features)}"


def test_the_unrooted_decision_scope_states_its_reason_in_the_artifact(
    tmp_path: Path,
) -> None:
    """In `manifest.json`, not only on a terminal that has scrolled.

    A count of zero with no reason beside it reads as "this target has no
    lineage", which is the opposite of what happened.
    """
    out = _run(CORPUS, tmp_path / "unrooted", SliceScope.DECISION)
    reason = json.loads(_read(out, "manifest.json"))["slice_scope"]["reason"]
    assert "no decision sink declared" in reason
    assert "no principled set of roots" in reason
    # Every alternative named, including the one that gets the owner a single
    # feature without turning on the exhaustive mode.
    assert "--sink" in reason
    assert "--slices all" in reason
    assert "--slice-root" in reason


def test_the_summary_says_it_too(tmp_path: Path) -> None:
    from cascade_map.cli import _report

    out = tmp_path / "unrooted"
    _code, summary = _run_full(CORPUS, out, SliceScope.DECISION)
    text = _report(summary, out, 0)
    assert "slices               0" in text
    assert "no decision sink declared" in text
    assert "--slice-root" in text


def test_a_declared_sink_bounds_the_roots_to_the_sink_and_what_it_reads(
    tmp_path: Path,
) -> None:
    """The bounded root set, stated as a set and not as a size.

    Features and finding roots were the leak. With a sink declared the roots
    are the sinks plus the one-hop sources of the lineage edges that land on
    them -- bounded by the owner's declaration of what the decision is.
    """
    from cascade_map.ingest.inventory import Ingestor

    probe = tmp_path / "probe"
    ingestor = Ingestor(cache_dir=probe, workers=1)
    elements, _ = ingestor.inventory(str(CORPUS))
    edges, _ = Resolver(CORPUS).resolve(elements)
    tracer = LineageTracer(CORPUS, sink_ids=CORPUS_SINK)
    tracer.trace_values(elements, edges)
    expected = set(tracer.decision_slice_roots())
    assert expected, "the declared sink produced no roots; nothing is bounded"
    assert set(CORPUS_SINK) <= expected

    out = _run(CORPUS, tmp_path / "rooted", SliceScope.DECISION, CORPUS_SINK)
    written = {one["root_id"] for one in _slices(out)}
    assert written == expected
    # And the leak is gone: no feature is a root just for being a feature.
    features = {one for one in written if one.startswith("@feature:")}
    assert not features, f"features are automatic roots again: {sorted(features)}"


def test_no_feature_or_finding_root_is_automatic_at_decision_scope(
    tmp_path: Path,
) -> None:
    """Measured against the roots that exist, not asserted in the abstract."""
    rooted = {
        one["root_id"]
        for one in _slices(_run(CORPUS, tmp_path / "rooted", SliceScope.DECISION,
                                CORPUS_SINK))
    }
    everything = {
        one["root_id"]
        for one in _slices(_run(CORPUS, tmp_path / "all", SliceScope.ALL, CORPUS_SINK))
    }
    assert everything - rooted, "DECISION precomputed everything ALL does"
    assert len(rooted) < len(everything) / 2, (
        f"DECISION precomputed {len(rooted)} of {len(everything)} roots; the "
        f"scope is meant to be bounded by the declaration, not by the codebase"
    )


def test_a_feature_is_one_slice_root_away(tmp_path: Path) -> None:
    """The message tells the owner this; the test proves it is true.

    No declared sink, one named feature, and the slice for it is precomputed
    and exact -- identical to the one ALL writes for the same root.
    """
    everything = {
        one["id"]: one
        for one in _slices(_run(CORPUS, tmp_path / "all", SliceScope.ALL))
    }
    wanted = sorted(
        {one["root_id"] for one in everything.values()
         if one["root_id"].startswith("@feature:")}
    )[0]
    out = _run(
        CORPUS, tmp_path / "one", SliceScope.DECISION, (), (), slice_roots=(wanted,)
    )
    rows = _slices(out)
    assert {one["root_id"] for one in rows} == {wanted}
    for one in rows:
        twin = everything[one["id"]]
        assert {k: v for k, v in one.items() if k != "scope"} == {
            k: v for k, v in twin.items() if k != "scope"
        }


# ---------------------------------------------------------------------------
# The findings are a fact; the scope is a storage decision
# ---------------------------------------------------------------------------


def test_findings_are_identical_across_scopes_with_a_declared_sink(
    tmp_path: Path,
) -> None:
    produced = {
        scope: _read(
            _run(CORPUS, tmp_path / str(scope), scope, CORPUS_SINK), "findings.jsonl"
        )
        for scope in SCOPES
    }
    assert produced[SliceScope.DECISION].strip(), "no findings; nothing compared"
    assert produced[SliceScope.DECISION] == produced[SliceScope.ALL]
    assert produced[SliceScope.DECISION] == produced[SliceScope.NONE]


def test_card_five_still_sees_the_feature_roots_that_are_no_longer_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unwritten slices are COMPUTED for the finding's own use.

    DECISION with no declared sink writes nothing. If that had also emptied
    the basis card 5 reasons from, a storage decision would have changed a
    fact -- the defect this whole change exists to avoid. So the basis is
    captured directly and compared against the roots that are written.
    """
    import cascade_map.cli as cli_module

    seen: dict[str, tuple[str, ...]] = {}
    original = cli_module.Findings

    class Recording(original):  # type: ignore[misc,valid-type]
        def __init__(self, **kwargs: object) -> None:
            seen[Recording.scope] = tuple(
                sorted(one.root_id for one in kwargs.get("slices", ()))  # type: ignore[union-attr]
            )
            super().__init__(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(cli_module, "Findings", Recording)
    for scope in SCOPES:
        Recording.scope = str(scope)  # type: ignore[attr-defined]
        _run(CORPUS, tmp_path / str(scope), scope)

    bases = set(seen.values())
    assert len(bases) == 1, "card 5 saw a different basis per scope"
    basis = set(next(iter(bases)))
    assert any(one.startswith("@feature:") for one in basis), (
        "card 5 lost the feature roots when they stopped being written"
    )
    written = {
        one["root_id"]
        for one in _slices(tmp_path / str(SliceScope.DECISION))
    }
    assert written == set()
    assert basis - written, "nothing was computed-and-not-written; the check is vacuous"


# ---------------------------------------------------------------------------
# The size guard
#
# The owner discovered 6.4 GB after the fact. An estimate costs a sum over ids
# already in memory.
# ---------------------------------------------------------------------------


def test_the_size_guard_refuses_and_names_the_number(tmp_path: Path) -> None:
    from cascade_map.cli import EXIT_REFUSED

    said: list[str] = []
    out = tmp_path / "guarded"
    code, summary = _run_full(
        CORPUS, out, SliceScope.ALL, say=said, slice_size_limit=2_000
    )
    assert code == EXIT_REFUSED, "a refusal that exits 0 reads as success"
    spoken = "\n".join(said)
    assert "Refusing to write it" in spoken
    assert "roots," in spoken and "member ids" in spoken
    # Every alternative, including the override.
    assert "--sink" in spoken
    assert "--slices none" in spoken
    assert "--force-slices" in spoken
    payload = summary["slices"]
    assert payload["refused"] is True
    assert payload["estimated_bytes"] > payload["size_limit_bytes"] == 2_000
    assert payload["slices_written"] == 0


def test_the_guard_never_truncates_it_refuses_whole(tmp_path: Path) -> None:
    """Emit fewer slices, never smaller ones -- including when refusing."""
    out = tmp_path / "guarded"
    _run_full(CORPUS, out, SliceScope.ALL, slice_size_limit=2_000)
    assert _read(out, "slices.jsonl") == ""
    # And nothing else was withheld: the run is otherwise complete, so the
    # analysis does not have to be paid for twice.
    whole = _run(CORPUS, tmp_path / "whole", SliceScope.ALL)
    for name in ("lineage.jsonl", "barriers.jsonl", "findings.jsonl"):
        assert _read(out, name) == _read(whole, name)


def test_the_refusal_is_recorded_in_the_manifest(tmp_path: Path) -> None:
    out = tmp_path / "guarded"
    _run_full(CORPUS, out, SliceScope.ALL, slice_size_limit=2_000)
    disclosed = json.loads(_read(out, "manifest.json"))["slice_scope"]
    assert disclosed["refused"] is True
    assert "Refusing to write it" in disclosed["reason"]
    assert disclosed["estimated_bytes"] > 2_000


def test_force_slices_writes_it_anyway_and_exactly(tmp_path: Path) -> None:
    """A refusal the owner can override is honest; one they cannot is an
    obstruction. The override must produce the same bytes as no guard at all."""
    forced = _run(
        CORPUS, tmp_path / "forced", SliceScope.ALL,
        slice_size_limit=2_000, force_slices=True,
    )
    unguarded = _run(CORPUS, tmp_path / "unguarded", SliceScope.ALL)
    assert _read(forced, "slices.jsonl") == _read(unguarded, "slices.jsonl")
    assert _read(forced, "slices.jsonl").strip(), "the comparison is vacuous"


def test_the_guard_is_quiet_when_the_estimate_is_under_the_limit(
    tmp_path: Path,
) -> None:
    """A guard that fires on every run is a guard nobody reads."""
    said: list[str] = []
    code, summary = _run_full(CORPUS, tmp_path / "small", SliceScope.ALL, say=said)
    assert code == 0
    assert "Refusing" not in "\n".join(said)
    assert summary["slices"]["refused"] is False


def test_a_refusal_leaves_no_record_pointing_at_a_slice_that_is_not_there(
    tmp_path: Path,
) -> None:
    """The guard runs BEFORE card 16 builds its records.

    A record whose drill-down link resolves to nothing is the defect card 15
    was corrected for. The guard must not create it.
    """
    import re

    out = tmp_path / "guarded"
    _run_full(CORPUS, out, SliceScope.ALL, slice_size_limit=2_000)
    present = {one["id"] for one in _slices(out)}
    assert present == set()
    # Every artifact, not only the one that happens to link today: a general
    # sweep for any `@slice:` id that no written slice answers.
    dangling: dict[str, set[str]] = {}
    for artifact in sorted(out.glob("*.jsonl")):
        if artifact.name == "slices.jsonl":
            continue
        found = set(re.findall(r"@slice:[^\"\\]+", artifact.read_text(encoding="utf-8")))
        missing = found - present
        if missing:
            dangling[artifact.name] = missing
    assert not dangling, f"artifacts point at slices that were not written: {dangling}"


def test_the_dangling_sweep_can_actually_see_a_slice_id(tmp_path: Path) -> None:
    """The guard on the sweep above.

    The sweep is a search for a string. If no artifact ever contained a slice
    id it would pass on anything, so the same search is run against a file
    that certainly holds them.
    """
    import re

    out = _run(CORPUS, tmp_path / "all", SliceScope.ALL)
    found = set(
        re.findall(r"@slice:[^\"\\]+", _read(out, "slices.jsonl"))
    )
    assert len(found) > 10, f"the sweep pattern matches nothing: {sorted(found)[:3]}"


def test_the_guard_flag_exists_and_defaults_to_off() -> None:
    from cascade_map.cli import SLICE_SIZE_LIMIT_BYTES, _build_parser
    from cascade_map.ledger import Settings, SettingsError

    parser = _build_parser()
    assert parser.parse_args(["analyze", "."]).force_slices is False
    assert parser.parse_args(["analyze", ".", "--force-slices"]).force_slices is True
    assert SLICE_SIZE_LIMIT_BYTES == 1 << 30
    assert Settings.from_mapping({"FORCE_SLICES": True}).force_slices is True
    assert Settings.from_mapping({}).force_slices is False
    with pytest.raises(SettingsError) as caught:
        Settings.from_mapping({"FORCE_SLICES": "yes"})
    assert "true or false" in str(caught.value)


def test_the_ledger_passes_force_slices_through(tmp_path: Path) -> None:
    from cascade_map.ledger import Settings, _default_analyse

    seen: dict[str, object] = {}

    def fake_analyze(root, out_dir, **kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return 0, {}

    import cascade_map.cli as cli_module

    original = cli_module.analyze
    cli_module.analyze = fake_analyze  # type: ignore[assignment]
    try:
        _default_analyse(
            tmp_path, tmp_path / "out", Settings.from_mapping({"FORCE_SLICES": True})
        )
    finally:
        cli_module.analyze = original  # type: ignore[assignment]

    assert seen["force_slices"] is True
