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


def _run(
    root: Path,
    out: Path,
    scope: SliceScope,
    sinks: tuple[str, ...] = (),
    entries: tuple[str, ...] = (),
) -> Path:
    analyze(
        root,
        out,
        sink_ids=sinks,
        entry_ids=entries,
        slice_scope=scope,
        cache_dir=out / "cache",
        strict_gate=False,
        worker_report_sink=lambda _text: None,
    )
    return out


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
    decision = _slices(_run(CORPUS, tmp_path / "decision", SliceScope.DECISION))
    everything = {one["id"]: one for one in _slices(
        _run(CORPUS, tmp_path / "all", SliceScope.ALL)
    )}
    assert decision, "no slices at DECISION scope; the comparison proves nothing"
    for one in decision:
        assert one["id"] in everything, f"{one['id']} vanished at ALL scope"
        assert one == everything[one["id"]], (
            f"{one['id']} differs between scopes: a scope may drop a slice, "
            f"never shorten one"
        )


def test_decision_roots_are_a_subset_of_all_roots(tmp_path: Path) -> None:
    decision = {one["root_id"] for one in _slices(
        _run(CORPUS, tmp_path / "decision", SliceScope.DECISION)
    )}
    everything = {one["root_id"] for one in _slices(
        _run(CORPUS, tmp_path / "all", SliceScope.ALL)
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
    rows = _slices(_run(CORPUS, tmp_path / "decision", SliceScope.DECISION))
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
        }
    assert canonical_jsonl([]) == ""
