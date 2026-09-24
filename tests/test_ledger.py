"""Tests for card 18 -- METATRON_SETTINGS and the version ledger.

These run the **real** pipeline. Every version is analysed by
`cascade_map.cli.analyze` over a real copy of a fixture tree, so the coverage
numbers and change classifications here are the ones an owner would get, not
values a test constructed to agree with itself. Nothing under any version tree
is ever imported or executed: `test_sentinel_is_never_executed` proves it by
tracking a version whose top level writes a marker file.

Version trees are copied into `tmp_path`. `tests/fixtures/` is never written
to -- a corpus that moves while it is being read cannot grade anything, and
this build has paid for that lesson twice.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from argparse import Namespace
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from cascade_map import cli
from cascade_map.contracts.interfaces import (
    SCHEMA_VERSION,
    canonical_dumps,
    ChangeKind,
    Confidence,
    ElementFingerprint,
    Method,
    Provenance,
    ReachabilityState,
    VersionComparison,
    VersionRecord,
)
from cascade_map.diff import diff_snapshots, load_snapshot
from cascade_map.ledger import (
    ROOT_TOKEN,
    SETTING_DEFAULTS,
    Ledger,
    LedgerError,
    Settings,
    SettingsError,
    _from_jsonable,
    _span_text,
    assign_ordinals,
    qualify_id,
    render_history_report,
    render_track_report,
    reroot_id,
    track,
    tree_hash,
    version_time_of,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
VERSION_CASES = sorted(
    p.name for p in (FIXTURES / "versions").iterdir()
    if (p / "before").is_dir() and (p / "after").is_dir()
)
SENTINEL_MARKER = Path("/tmp/cascade_map_sentinel_marker.txt")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _place(tmp_path: Path, source: Path, label: str) -> Path:
    """Copy a tree into `tmp_path/versions/<label>`. Never writes to fixtures."""
    target = tmp_path / "versions" / label
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    return target


def _settings(tmp_path: Path, **overrides) -> Settings:
    base = {
        "VERSIONS_DIR": "versions",
        "OUT_DIR": "out",
        "LEDGER": "out/metatron_ledger.json",
        "ENV": "",
    }
    base.update(overrides)
    return Settings.from_mapping(base)


def _place_pair(tmp_path: Path, case: str) -> None:
    root = FIXTURES / "versions" / case
    _place(tmp_path, root / "before", f"{case}_2026-01-14")
    _place(tmp_path, root / "after", f"{case}_2026-02-03")


def _counting_analyse():
    """The real analysis, wrapped in a counter, so 'never re-analysed' is
    measured rather than asserted."""
    from cascade_map.ledger import _default_analyse

    calls: list[str] = []

    def analyse(source_root: Path, out_dir: Path, settings: Settings):
        calls.append(source_root.name)
        return _default_analyse(source_root, out_dir, settings)

    return analyse, calls


# ---------------------------------------------------------------------------
# METATRON_SETTINGS -- data, never code
# ---------------------------------------------------------------------------


def test_cli_settings_dict_and_the_canonical_spec_have_the_same_keys() -> None:
    assert cli.METATRON_SETTINGS == SETTING_DEFAULTS


def test_unknown_setting_is_an_error_that_names_the_typo() -> None:
    with pytest.raises(SettingsError) as excinfo:
        Settings.from_mapping({**SETTING_DEFAULTS, "SINK": ["engine::decide"]})
    message = str(excinfo.value)
    assert '"SINK"' in message
    assert 'Did you mean "SINKS"?' in message


def test_unknown_setting_with_no_near_match_still_lists_the_valid_keys() -> None:
    # Was "WORKERS", which is a real setting now. The point of the test is a
    # key with no near match, so it needs one that will not become real.
    with pytest.raises(SettingsError) as excinfo:
        Settings.from_mapping({"QQZZ_NOT_A_SETTING": 8})
    assert "QQZZ_NOT_A_SETTING" in str(excinfo.value)
    assert "VERSIONS_DIR" in str(excinfo.value)


def test_workers_must_be_a_non_negative_int() -> None:
    assert Settings.from_mapping({"WORKERS": 8}).workers == 8
    assert Settings.from_mapping({}).workers == 0, "0 = auto is the default"
    for bad in (-1, "4", True, 1.5, None):
        with pytest.raises(SettingsError, match="WORKERS"):
            Settings.from_mapping({"WORKERS": bad})


@pytest.mark.parametrize("bad", [0, 3, "1", True, None])
def test_mode_must_be_1_or_2(bad) -> None:
    with pytest.raises(SettingsError):
        Settings.from_mapping({"MODE": bad})


def test_a_bare_string_for_a_list_setting_is_an_error_not_a_list_of_letters() -> None:
    with pytest.raises(SettingsError) as excinfo:
        Settings.from_mapping({"SINKS": "engine::decide"})
    assert "list of strings" in str(excinfo.value)


def test_a_path_setting_given_a_number_is_an_error() -> None:
    with pytest.raises(SettingsError):
        Settings.from_mapping({"OUT_DIR": 7})


def test_the_shipped_settings_dict_validates() -> None:
    settings = Settings.from_mapping(cli.METATRON_SETTINGS)
    assert settings.mode == 1
    # The three keys WORKSPACE replaced ship EMPTY, which is what makes the
    # workspace the default rather than an opt-in. Non-empty means the owner
    # set it and the old behaviour is kept for them, key by key.
    assert settings.workspace == "workspace"
    assert settings.versions_dir == ""
    assert settings.out_dir == ""
    assert settings.ledger == ""
    assert settings.legacy_keys == ()
    assert settings.sources is True
    assert settings.env == ".venv-target"


def test_flags_override_the_settings_dict() -> None:
    args = Namespace(
        mode=2, versions_dir=None, versions=Path("elsewhere"), out=Path("o"),
        ledger=Path("o/l.json"), sink=["a::b"], entry=None, config=None,
        env=None, order=["v1", "v2"], scenarios=None, scenario="smoke",
    )
    settings = cli._settings_from_args(args)
    assert settings.mode == 2
    assert settings.versions_dir == "elsewhere"
    assert settings.out_dir == "o"
    assert settings.sinks == ("a::b",)
    assert settings.order == ("v1", "v2")
    assert settings.scenario == "smoke"
    # Untouched keys keep the dict's value.
    assert settings.env == cli.METATRON_SETTINGS["ENV"]


def test_settings_are_never_executed() -> None:
    """A settings file is data. Nothing in this module compiles or runs one."""
    source = (REPO_ROOT / "src" / "cascade_map" / "ledger.py").read_text(encoding="utf-8")
    for forbidden in ("exec(", "eval(", "pickle", "marshal", "__import__"):
        assert forbidden not in source, f"{forbidden} appears in ledger.py"


# ---------------------------------------------------------------------------
# Identity is the tree's content hash
# ---------------------------------------------------------------------------


def test_identity_survives_a_rename_and_breaks_on_one_byte(tmp_path: Path) -> None:
    first = _place(tmp_path, FIXTURES / "versions" / "dif_rename" / "before", "a_2026-01-01")
    second = _place(tmp_path, FIXTURES / "versions" / "dif_rename" / "before", "b_2026-02-01")
    assert tree_hash(first) == tree_hash(second)

    victim = next(second.rglob("*.py"))
    victim.write_text(victim.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert tree_hash(first) != tree_hash(second)


def test_identity_ignores_bytecode_caches_and_git_metadata(tmp_path: Path) -> None:
    tree = _place(tmp_path, FIXTURES / "versions" / "dif_signature" / "before", "v_2026-01-01")
    before = tree_hash(tree)
    (tree / "__pycache__").mkdir()
    (tree / "__pycache__" / "x.pyc").write_bytes(b"\x00\x01")
    (tree / ".git").mkdir()
    (tree / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    assert tree_hash(tree) == before


def test_identity_notices_a_file_moved_without_a_byte_changing(tmp_path: Path) -> None:
    tree = _place(tmp_path, FIXTURES / "versions" / "dif_signature" / "before", "v_2026-01-01")
    before = tree_hash(tree)
    victim = next(tree.rglob("*.py"))
    victim.rename(victim.with_name("renamed.py"))
    assert tree_hash(tree) != before


# ---------------------------------------------------------------------------
# Ordering names its source, every time
# ---------------------------------------------------------------------------


def _record(label: str, when: str, source: str) -> VersionRecord:
    return VersionRecord(
        id=f"id::{label}", label=label, source_path=f"versions/{label}",
        tree_hash=f"id::{label}", discovered_at="2026-01-01T00:00:00Z",
        version_time=when, version_time_source=source, ordinal=-1,
        tool_version="0", schema_version=SCHEMA_VERSION, mode=1,
        artifact_dir=f"out/id::{label}", counts={}, confidence_census={},
        stage_millis={}, total_millis=0, runtime_run_ids=(),
        provenance=Provenance(method=Method.AST_DIRECT, confidence=Confidence.UNKNOWN),
    )


@pytest.mark.parametrize(
    "name,expected",
    [
        ("amun_2026-02-03", "2026-02-03T00:00:00Z"),
        ("amun_20260203", "2026-02-03T00:00:00Z"),
        ("2026-02-03T11-30", "2026-02-03T11:30:00Z"),
        ("release.2026.02.03", "2026-02-03T00:00:00Z"),
    ],
)
def test_a_date_written_into_a_folder_name_is_read(tmp_path: Path, name, expected) -> None:
    directory = tmp_path / name
    directory.mkdir()
    when, source = version_time_of(directory)
    assert (when, source) == (expected, "filename")


def test_a_number_that_is_not_a_date_is_not_read_as_one(tmp_path: Path) -> None:
    directory = tmp_path / "build_12345678"
    directory.mkdir()
    _when, source = version_time_of(directory)
    assert source in ("file_mtime_max", "directory_mtime")


def test_the_git_commit_date_is_read_as_text_without_running_git(tmp_path: Path) -> None:
    directory = tmp_path / "checkout"
    (directory / ".git" / "logs").mkdir(parents=True)
    (directory / "a.py").write_text("x = 1\n", encoding="utf-8")
    (directory / ".git" / "logs" / "HEAD").write_text(
        "0000000000000000000000000000000000000000 "
        "1111111111111111111111111111111111111111 "
        "Someone <s@example.com> 1770000000 +0000\tcommit (initial): first\n",
        encoding="utf-8",
    )
    when, source = version_time_of(directory)
    assert source == "git_commit"
    assert when == "2026-02-02T02:40:00Z"


def test_filename_dates_order_the_history_and_name_their_source() -> None:
    records = [
        _record("b", "2026-02-03T00:00:00Z", "filename"),
        _record("a", "2026-01-14T00:00:00Z", "filename"),
    ]
    placed, status, reason = assign_ordinals(records)
    assert status == "trusted"
    assert reason == ""
    assert {r.label: r.ordinal for r in placed} == {"a": 0, "b": 1}


def test_a_shared_timestamp_refuses_to_order_and_says_why() -> None:
    records = [
        _record("a", "2026-01-14T00:00:00Z", "file_mtime_max"),
        _record("b", "2026-01-14T00:00:00Z", "file_mtime_max"),
    ]
    placed, status, reason = assign_ordinals(records)
    assert status == "unestablished"
    assert [r.ordinal for r in placed] == [-1, -1]
    assert "share a timestamp" in reason


def test_mixing_a_trusted_signal_with_a_filesystem_one_refuses_to_order() -> None:
    records = [
        _record("named_2026-01-14", "2026-01-14T00:00:00Z", "filename"),
        _record("copied", "2026-02-03T00:00:00Z", "file_mtime_max"),
    ]
    placed, status, reason = assign_ordinals(records)
    assert status == "unestablished"
    assert {r.ordinal for r in placed} == {-1}
    assert "would be a guess" in reason


def test_no_time_signal_at_all_refuses_to_order() -> None:
    records = [
        _record("a", "", "unknown"),
        _record("b", "2026-02-03T00:00:00Z", "filename"),
    ]
    placed, status, reason = assign_ordinals(records)
    assert status == "unestablished"
    assert {r.ordinal for r in placed} == {-1}
    assert "no time signal at all" in reason


def test_filesystem_timestamps_order_but_say_loudly_what_they_rest_on() -> None:
    records = [
        _record("a", "2026-01-14T00:00:00Z", "file_mtime_max"),
        _record("b", "2026-02-03T00:00:00Z", "file_mtime_max"),
    ]
    placed, status, reason = assign_ordinals(records)
    assert status == "filesystem_timestamps"
    assert {r.label: r.ordinal for r in placed} == {"a": 0, "b": 1}
    assert "filesystem timestamps" in reason


def test_owner_declared_order_wins_and_is_recorded_as_such() -> None:
    records = [
        _record("new_2026-02-03", "2026-02-03T00:00:00Z", "filename"),
        _record("old_2026-01-14", "2026-01-14T00:00:00Z", "filename"),
    ]
    placed, status, _reason = assign_ordinals(
        records, ["new_2026-02-03", "old_2026-01-14"]
    )
    assert status == "owner_declared"
    assert {r.label: r.ordinal for r in placed} == {"new_2026-02-03": 0, "old_2026-01-14": 1}
    assert {r.version_time_source for r in placed} == {"owner_declared"}


def test_an_order_naming_a_version_that_does_not_exist_is_an_error() -> None:
    with pytest.raises(SettingsError) as excinfo:
        assign_ordinals([_record("a", "2026-01-14T00:00:00Z", "filename")], ["typo"])
    assert "typo" in str(excinfo.value)


def test_a_version_missing_from_a_declared_order_keeps_ordinal_minus_one() -> None:
    records = [
        _record("a", "2026-01-14T00:00:00Z", "filename"),
        _record("b", "2026-02-03T00:00:00Z", "filename"),
    ]
    placed, status, reason = assign_ordinals(records, ["a"])
    assert status == "owner_declared"
    assert {r.label: r.ordinal for r in placed} == {"a": 0, "b": -1}
    assert "'b'" in reason


def test_an_unordered_history_compares_nothing_and_asks_for_order(tmp_path: Path) -> None:
    root = FIXTURES / "versions" / "dif_added_removed"
    _place(tmp_path, root / "before", "alpha")
    _place(tmp_path, root / "after", "beta")
    for path in (tmp_path / "versions").rglob("*"):
        os.utime(path, (1_700_000_000, 1_700_000_000))
    os.utime(tmp_path / "versions" / "alpha", (1_700_000_000, 1_700_000_000))
    os.utime(tmp_path / "versions" / "beta", (1_700_000_000, 1_700_000_000))

    result, ledger = track(_settings(tmp_path), root=tmp_path)
    assert result.order_status == "unestablished"
    assert ledger.consecutive_pairs() == []
    assert result.comparisons == ()
    report = render_track_report(result)
    assert "ORDER NOT ESTABLISHED" in report
    assert "NOTHING WAS COMPARED" in report
    assert '"ORDER": [' in report


# ---------------------------------------------------------------------------
# A version already in the ledger is NEVER re-analysed
# ---------------------------------------------------------------------------


def test_a_second_track_with_nothing_new_does_no_analysis_and_says_so(
    tmp_path: Path,
) -> None:
    _place_pair(tmp_path, "dif_signature")
    analyse, calls = _counting_analyse()

    first, _ = track(_settings(tmp_path), root=tmp_path, analyse=analyse)
    assert len(first.new_version_ids) == 2
    assert len(calls) == 2

    second, _ = track(_settings(tmp_path), root=tmp_path, analyse=analyse)
    assert second.new_version_ids == ()
    assert len(calls) == 2, "an already-known version was analysed again"
    report = render_track_report(second)
    assert "0 new" in report
    assert "Nothing new." in report
    assert "known, not re-run" in report


def test_renaming_a_version_folder_does_not_make_it_a_new_version(
    tmp_path: Path,
) -> None:
    _place_pair(tmp_path, "dif_move")
    analyse, calls = _counting_analyse()
    track(_settings(tmp_path), root=tmp_path, analyse=analyse)
    assert len(calls) == 2

    (tmp_path / "versions" / "dif_move_2026-02-03").rename(
        tmp_path / "versions" / "dif_move_2026-02-04"
    )
    result, ledger = track(_settings(tmp_path), root=tmp_path, analyse=analyse)
    assert len(calls) == 2, "a renamed folder was analysed as if it were new"
    assert result.new_version_ids == ()
    assert "dif_move_2026-02-04" in {r.label for r in ledger.versions}


def test_two_folders_with_identical_content_are_one_version(tmp_path: Path) -> None:
    root = FIXTURES / "versions" / "dif_format_only"
    _place(tmp_path, root / "before", "one_2026-01-14")
    _place(tmp_path, root / "before", "copy_2026-02-03")
    analyse, calls = _counting_analyse()
    result, ledger = track(_settings(tmp_path), root=tmp_path, analyse=analyse)
    assert len(calls) == 1
    assert len(ledger.versions) == 1
    assert result.duplicate_labels == {"one_2026-01-14": "copy_2026-02-03"}
    assert "DUPLICATE of copy_2026-02-03" in render_track_report(result)


def test_a_new_version_analyses_only_itself(tmp_path: Path) -> None:
    root = FIXTURES / "versions" / "dif_added_removed"
    _place(tmp_path, root / "before", "eng_2026-01-14")
    analyse, calls = _counting_analyse()
    track(_settings(tmp_path), root=tmp_path, analyse=analyse)
    assert calls == ["eng_2026-01-14"]

    _place(tmp_path, root / "after", "eng_2026-02-03")
    result, _ = track(_settings(tmp_path), root=tmp_path, analyse=analyse)
    assert calls == ["eng_2026-01-14", "eng_2026-02-03"]
    assert len(result.new_version_ids) == 1
    assert len(result.comparisons) == 1


# ---------------------------------------------------------------------------
# "Every single element" -- on every fixture pair, not a sample
# ---------------------------------------------------------------------------


def _independent_coverage(before_snap, after_snap, changes) -> tuple[list, list, list, int]:
    """Coverage recomputed from scratch, deliberately not sharing code with
    `ledger._coverage`. Returns (missing, doubled, foreign, paired)."""
    occurrences = {("before", e.id) for e in before_snap.elements}
    occurrences |= {("after", e.id) for e in after_snap.elements}
    claims: Counter = Counter()
    paired = 0
    for change in changes:
        if change.before_id:
            claims[("before", change.before_id)] += 1
        if change.after_id:
            claims[("after", change.after_id)] += 1
        if change.before_id and change.after_id:
            paired += 1
    missing = sorted(o for o in occurrences if claims[o] == 0)
    doubled = sorted(o for o in occurrences if claims[o] > 1)
    foreign = sorted(o for o in claims if o not in occurrences)
    return missing, doubled, foreign, paired


@pytest.mark.parametrize("case", VERSION_CASES)
def test_every_element_of_both_versions_lands_in_exactly_one_classification(
    tmp_path: Path, case: str
) -> None:
    _place_pair(tmp_path, case)
    _result, ledger = track(_settings(tmp_path), root=tmp_path)
    pairs = ledger.consecutive_pairs()
    assert len(pairs) == 1, f"{case}: expected one consecutive pair"
    before_id, after_id = pairs[0]
    comparison = ledger.compare(before_id, after_id)

    before_snap = ledger._snapshot(before_id)
    after_snap = ledger._snapshot(after_id)
    changes, _impacts = diff_snapshots(before_snap, after_snap)

    missing, doubled, foreign, paired = _independent_coverage(
        before_snap, after_snap, changes
    )
    assert missing == [], f"{case}: elements no classification claims"
    assert doubled == [], f"{case}: elements two classifications claim"
    assert foreign == [], f"{case}: a change names an element in neither version"
    assert comparison.unaccounted_element_ids == ()
    assert comparison.elements_before == len(before_snap.elements)
    assert comparison.elements_after == len(after_snap.elements)

    occurrences = len(before_snap.elements) + len(after_snap.elements)
    assert comparison.elements_accounted_for == occurrences - paired
    assert sum(comparison.change_counts.values()) == len(comparison.change_ids)


@pytest.mark.parametrize("case", VERSION_CASES)
def test_the_change_counts_cover_every_change_kind_explicitly(
    tmp_path: Path, case: str
) -> None:
    """A missing key and a zero are different claims. Every kind is present."""
    _place_pair(tmp_path, case)
    _result, ledger = track(_settings(tmp_path), root=tmp_path)
    before_id, after_id = ledger.consecutive_pairs()[0]
    counts = ledger.compare(before_id, after_id).change_counts
    assert set(counts) == {kind.value for kind in ChangeKind}


def test_an_unaccounted_element_is_named_and_the_run_does_not_report_success(
    tmp_path: Path,
) -> None:
    """The coverage guarantee has to be able to fail. A change that claims an
    element twice must be caught, not smoothed over."""
    from cascade_map.ledger import _coverage

    _place_pair(tmp_path, "dif_signature")
    _result, ledger = track(_settings(tmp_path), root=tmp_path)
    before_id, after_id = ledger.consecutive_pairs()[0]
    before_snap = ledger._snapshot(before_id)
    after_snap = ledger._snapshot(after_id)
    changes, _ = diff_snapshots(before_snap, after_snap)

    accounted, unaccounted = _coverage(before_snap, after_snap, changes[:-1])
    assert unaccounted, "dropping a change must leave an element named"
    assert accounted < len(before_snap.elements) + len(after_snap.elements)
    assert all(e.startswith(("before:", "after:")) for e in unaccounted)

    doubled_accounted, doubled_unaccounted = _coverage(
        before_snap, after_snap, [*changes, changes[0]]
    )
    assert doubled_unaccounted, "an element claimed twice must be named too"
    assert doubled_accounted < accounted + 2


def test_incomplete_coverage_makes_the_cli_exit_non_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _place_pair(tmp_path, "dif_signature")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["track", "--env", "no-such-env"]) == cli.EXIT_OK

    real_compare = Ledger.compare

    def lossy(self, before_id: str, after_id: str) -> VersionComparison:
        return replace(
            real_compare(self, before_id, after_id),
            unaccounted_element_ids=("before:engine::gone",),
        )

    monkeypatch.setattr(Ledger, "compare", lossy)
    shutil.rmtree(tmp_path / "workspace")
    assert cli.main(["track", "--env", "no-such-env"]) == cli.EXIT_GATE_FAILED


# ---------------------------------------------------------------------------
# Normalisation, and never re-reading an old version
# ---------------------------------------------------------------------------


def test_a_reformat_is_not_a_behaviour_change(tmp_path: Path) -> None:
    _place_pair(tmp_path, "dif_format_only")
    _result, ledger = track(_settings(tmp_path), root=tmp_path)
    before_id, after_id = ledger.consecutive_pairs()[0]
    counts = ledger.compare(before_id, after_id).change_counts
    assert counts[ChangeKind.BODY_CHANGED.value] == 0
    assert counts[ChangeKind.UNCHANGED.value] > 0
    assert counts[ChangeKind.ADDED.value] == 0
    assert counts[ChangeKind.REMOVED.value] == 0


def test_a_comparison_still_works_after_the_source_trees_are_deleted(
    tmp_path: Path,
) -> None:
    """The ledger is an index. Once a version is analysed its source is never
    read again, and this is the proof: delete every tree, reload the ledger
    from disk, and the comparison is the same."""
    _place_pair(tmp_path, "dif_format_only")
    settings = _settings(tmp_path)
    _result, ledger = track(settings, root=tmp_path)
    before_id, after_id = ledger.consecutive_pairs()[0]
    expected = ledger.compare(before_id, after_id)

    shutil.rmtree(tmp_path / "versions")
    reloaded = Ledger(settings, root=tmp_path).load(settings.ledger)
    reloaded._comparisons.clear()
    after_delete = reloaded.compare(before_id, after_id)
    assert after_delete.change_counts == expected.change_counts
    assert after_delete.elements_accounted_for == expected.elements_accounted_for
    assert after_delete.unaccounted_element_ids == ()


def test_missing_artifacts_refuse_rather_than_report_everything_removed(
    tmp_path: Path,
) -> None:
    _place_pair(tmp_path, "dif_signature")
    settings = _settings(tmp_path)
    _result, ledger = track(settings, root=tmp_path)
    before_id, after_id = ledger.consecutive_pairs()[0]
    ledger._comparisons.clear()
    shutil.rmtree(tmp_path / "out" / before_id)
    with pytest.raises(LedgerError) as excinfo:
        ledger.compare(before_id, after_id)
    assert before_id in str(excinfo.value)
    assert "REMOVED" in str(excinfo.value)


def test_span_text_agrees_with_card_sixes_own_reader(tmp_path: Path) -> None:
    """The fingerprint builder reads spans with a per-file cache instead of
    card 6's `read_span`. Drift between the two would silently change what
    'the same body' means, so they are checked against each other."""
    _place_pair(tmp_path, "dif_impact_rank")
    _result, ledger = track(_settings(tmp_path), root=tmp_path)
    record = ledger.versions[0]
    source_root = ledger.resolve(record.source_path)
    snapshot = load_snapshot(ledger.resolve(record.artifact_dir), source_root=source_root)
    assert snapshot.elements
    cache: dict[str, list[str]] = {}
    for element in snapshot.elements:
        assert _span_text(source_root, element, cache) == snapshot.read_span(element)


def test_element_ids_are_version_independent(tmp_path: Path) -> None:
    """Card 1 makes the analysed folder's name the first dotted component, so
    two versions in differently named folders share no ids at all. The ledger
    strips it -- without that, this pair reports every element added and
    removed."""
    _place_pair(tmp_path, "dif_impact_rank")
    _result, ledger = track(_settings(tmp_path), root=tmp_path)
    before_id, after_id = ledger.consecutive_pairs()[0]
    before_ids = {f.element_id for f in ledger.fingerprints(before_id)}
    after_ids = {f.element_id for f in ledger.fingerprints(after_id)}
    assert before_ids == after_ids
    assert "engine::decide" in before_ids
    assert ROOT_TOKEN in before_ids
    counts = ledger.compare(before_id, after_id).change_counts
    assert counts[ChangeKind.ADDED.value] == 0
    assert counts[ChangeKind.REMOVED.value] == 0


@pytest.mark.parametrize(
    "raw,prefix,expected",
    [
        ("amun_v1.engine::decide", "amun_v1", "engine::decide"),
        ("amun_v1", "amun_v1", ROOT_TOKEN),
        ("amun_v1.2.engine", "amun_v1.2", "engine"),
        ("config.json::rules", "amun_v1", "config.json::rules"),
    ],
)
def test_rerooting_strips_exactly_one_leading_component(raw, prefix, expected) -> None:
    assert reroot_id(raw, prefix) == expected


@pytest.mark.parametrize(
    "raw,prefix",
    [
        ("amun_v1.engine::decide", "amun_v1"),
        ("amun_v1", "amun_v1"),
        ("amun_v1.2.engine", "amun_v1.2"),
    ],
)
def test_rerooting_is_reversible_for_ids_that_carry_the_prefix(raw, prefix) -> None:
    assert qualify_id(reroot_id(raw, prefix), prefix) == raw


def test_qualifying_an_id_that_already_carries_the_prefix_changes_nothing() -> None:
    assert qualify_id("amun_v1.engine::decide", "amun_v1") == "amun_v1.engine::decide"


# ---------------------------------------------------------------------------
# Impact
# ---------------------------------------------------------------------------


def test_a_one_line_change_inside_the_decision_is_counted_as_decision_impact(
    tmp_path: Path,
) -> None:
    """`dif_impact_rank`: `> 10` becomes `> 20` inside the sink, and eight
    lines are rewritten in a function nothing reaches. One of those touches
    the decision."""
    _place_pair(tmp_path, "dif_impact_rank")
    settings = _settings(tmp_path, SINKS=["engine::decide"], ENTRIES=["engine::main"])
    _result, ledger = track(settings, root=tmp_path)
    before_id, after_id = ledger.consecutive_pairs()[0]
    comparison = ledger.compare(before_id, after_id)
    assert comparison.decision_paths_changed == 1


def test_the_diff_is_symmetric_through_the_ledger(tmp_path: Path) -> None:
    _place_pair(tmp_path, "dif_symmetric")
    _result, ledger = track(_settings(tmp_path), root=tmp_path)
    before_id, after_id = ledger.consecutive_pairs()[0]
    forward = ledger.compare(before_id, after_id)
    backward = ledger.compare(after_id, before_id)
    assert forward.elements_before == backward.elements_after
    assert forward.elements_after == backward.elements_before
    assert forward.unaccounted_element_ids == backward.unaccounted_element_ids == ()
    assert len(forward.change_ids) == len(backward.change_ids)
    mirrored = {
        ChangeKind.ADDED.value: ChangeKind.REMOVED.value,
        ChangeKind.REMOVED.value: ChangeKind.ADDED.value,
    }
    for kind, count in forward.change_counts.items():
        assert backward.change_counts[mirrored.get(kind, kind)] == count


# ---------------------------------------------------------------------------
# runtime_delta -- empty means NOT MEASURED
# ---------------------------------------------------------------------------


def _fake_run(artifact_dir: Path, run_id: str, scenario: str, events: dict[str, int],
              **flags) -> None:
    """Write what a Mode A run leaves behind. Nothing is executed: these are
    the artifacts, hand-built, exactly as `trace` would have written them."""
    run_dir = artifact_dir / "runtime" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": run_id, "scenario": scenario, "refused": False, **flags}
    (run_dir / "run.json").write_text(json.dumps(payload), encoding="utf-8")
    lines = []
    sequence = 0
    for element_id, count in sorted(events.items()):
        for _ in range(count):
            sequence += 1
            lines.append(json.dumps({
                "event_id": f"evt_{sequence:08d}", "run_id": run_id,
                "kind": "CALL", "element_id": element_id,
                "sequence": sequence, "depth": 1,
            }))
    (run_dir / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_mode_one_reports_runtime_as_not_measured_never_as_no_change(
    tmp_path: Path,
) -> None:
    _place_pair(tmp_path, "dif_signature")
    result, ledger = track(_settings(tmp_path), root=tmp_path)
    before_id, after_id = ledger.consecutive_pairs()[0]
    assert ledger.compare(before_id, after_id).runtime_delta == {}
    report = render_track_report(result)
    assert "not measured (MODE 1; set MODE 2 to observe execution)" in report
    assert "no change" not in report.lower()


def test_runtime_delta_is_measured_when_both_versions_share_a_scenario(
    tmp_path: Path,
) -> None:
    _place_pair(tmp_path, "dif_signature")
    settings = _settings(tmp_path, MODE=2)
    _first, ledger = track(settings, root=tmp_path, trace=lambda *a: (0, ""))
    before_id, after_id = ledger.consecutive_pairs()[0]
    _fake_run(tmp_path / "out" / before_id, "run_a", "baseline", {"api::call": 2})
    _fake_run(tmp_path / "out" / after_id, "run_b", "baseline",
              {"api::call": 5, "api::extra": 1})

    result, ledger = track(settings, root=tmp_path, trace=lambda *a: (0, ""))
    comparison = ledger.compare(before_id, after_id)
    delta = comparison.runtime_delta
    assert delta["scenario"] == "baseline"
    assert delta["events_before"] == 2
    assert delta["events_after"] == 6
    assert delta["elements_newly_executed"] == ["api::extra"]
    assert delta["event_count_changed"]["api::call"] == [2, 5]
    assert "not durations" in delta["measures"]
    assert "not measured" not in render_track_report(result)


def test_a_refused_or_failed_run_is_not_treated_as_a_measurement(
    tmp_path: Path,
) -> None:
    _place_pair(tmp_path, "dif_signature")
    settings = _settings(tmp_path, MODE=2)
    _first, ledger = track(settings, root=tmp_path, trace=lambda *a: (0, ""))
    before_id, after_id = ledger.consecutive_pairs()[0]
    _fake_run(tmp_path / "out" / before_id, "run_a", "baseline", {"api::call": 2},
              refused=True)
    _fake_run(tmp_path / "out" / after_id, "run_b", "baseline", {"api::call": 5},
              scenario_failure={"stage": "import", "exception_type": "ImportError",
                                "message": "boom"})
    result, ledger = track(settings, root=tmp_path, trace=lambda *a: (0, ""))
    comparison = ledger.compare(before_id, after_id)
    assert comparison.runtime_delta == {}
    assert "no completed Mode A run" in render_track_report(result)


def test_two_runs_of_different_scenarios_are_not_compared(tmp_path: Path) -> None:
    _place_pair(tmp_path, "dif_signature")
    settings = _settings(tmp_path, MODE=2)
    _first, ledger = track(settings, root=tmp_path, trace=lambda *a: (0, ""))
    before_id, after_id = ledger.consecutive_pairs()[0]
    _fake_run(tmp_path / "out" / before_id, "run_a", "baseline", {"api::call": 2})
    _fake_run(tmp_path / "out" / after_id, "run_b", "stress", {"api::call": 5})
    result, ledger = track(settings, root=tmp_path, trace=lambda *a: (0, ""))
    assert ledger.compare(before_id, after_id).runtime_delta == {}
    report = render_track_report(result)
    assert "none of the same" in report


# ---------------------------------------------------------------------------
# MODE 2 wiring -- proved without executing a line of any target
# ---------------------------------------------------------------------------


def test_mode_one_never_reaches_the_harness(tmp_path: Path) -> None:
    _place_pair(tmp_path, "dif_signature")
    calls: list[tuple] = []

    def trace(*args):
        calls.append(args)
        return 0, ""

    result, ledger = track(_settings(tmp_path), root=tmp_path, trace=trace)
    assert len(ledger.versions) == 2, "nothing was analysed, so nothing was proved"
    assert calls == []
    assert result.trace_notes == ()


def test_mode_two_traces_each_new_version_once_through_the_trace_seam(
    tmp_path: Path,
) -> None:
    _place_pair(tmp_path, "dif_signature")
    (tmp_path / "scenarios.json").write_text("{}", encoding="utf-8")
    calls: list[tuple] = []

    def trace(graph_dir, scenarios, scenario, out_root, intents_path=None):
        calls.append((Path(graph_dir).name, Path(scenarios).name, scenario, intents_path))
        return 0, "ok"

    settings = _settings(tmp_path, MODE=2, SCENARIOS="scenarios.json")
    result, ledger = track(settings, root=tmp_path, trace=trace)
    assert len(calls) == 2
    assert {c[1] for c in calls} == {"scenarios.json"}
    assert {c[2] for c in calls} == {"baseline"}
    assert {c[3] for c in calls} == {None}, "no INTENTS was set, so none was passed"
    assert {c[0] for c in calls} == {r.id for r in ledger.versions}
    assert len(result.trace_notes) == 2

    again, _ = track(settings, root=tmp_path, trace=trace)
    assert len(calls) == 2, "a known version was traced again"
    assert again.trace_notes == ()


def test_mode_two_without_a_scenarios_file_executes_nothing_and_says_so(
    tmp_path: Path,
) -> None:
    _place_pair(tmp_path, "dif_signature")
    calls: list[tuple] = []
    settings = _settings(tmp_path, MODE=2, SCENARIOS="absent.json")
    result, _ledger = track(
        settings, root=tmp_path, trace=lambda *a: (calls.append(a), (0, ""))[1]
    )
    assert calls == []
    assert any("does not exist" in note for note in result.trace_notes)
    assert "MODE 2:" in render_track_report(result)


# ---------------------------------------------------------------------------
# Determinism and round trips
# ---------------------------------------------------------------------------


def _blank_wallclock(text: str, *, disk: bool = False) -> str:
    document = json.loads(text)
    for record in document["versions"]:
        record["discovered_at"] = ""
        record["stage_millis"] = {}
        record["total_millis"] = 0
        # The run directory is named by `discovered_at`, which is blanked one
        # line above. It is the SAME wall-clock value, not a second one: the
        # stamp is never re-read from the clock, so no new non-determinism is
        # introduced by the workspace layout.
        record["artifact_dir"] = ""
    for comparison in document.get("comparisons", []):
        comparison.pop("analysis_millis_delta", None)
    if disk:
        document.pop("disk", None)
    return json.dumps(document, sort_keys=True, indent=1)


def test_two_runs_in_one_directory_are_byte_identical(tmp_path: Path) -> None:
    _place_pair(tmp_path, "dif_wiring")
    settings = _settings(tmp_path)
    _result, ledger = track(settings, root=tmp_path)
    first = (tmp_path / "out" / "metatron_ledger.json").read_bytes()
    # Two empty ledgers are also identical, so the content is asserted first.
    assert len(ledger.versions) == 2
    assert len(ledger.comparisons) == 1
    assert json.loads(first)["fingerprints"]
    track(settings, root=tmp_path)
    assert (tmp_path / "out" / "metatron_ledger.json").read_bytes() == first


def test_two_fresh_runs_agree_across_hash_seeds(tmp_path: Path) -> None:
    """Separate directories, separate processes, different PYTHONHASHSEED.
    Everything but the two fields that are wall-clock by definition."""
    outputs = []
    for index, seed in enumerate(("0", "7919")):
        workspace = tmp_path / f"run{index}"
        workspace.mkdir()
        _place_pair(workspace, "dif_rename")
        environment = {
            **os.environ,
            "PYTHONHASHSEED": seed,
            "PYTHONPATH": str(REPO_ROOT / "src"),
        }
        completed = subprocess.run(
            [sys.executable, "-m", "cascade_map.cli", "track", "--env", "no-such-env"],
            cwd=workspace, env=environment, capture_output=True, text=True, timeout=300,
        )
        assert completed.returncode == 0, completed.stderr
        text = (
            workspace
            / "workspace"
            / "AmunEV_Engine_V2"
            / "history"
            / "AmunEV_Engine_V2_history.json"
        ).read_text(encoding="utf-8")
        document = json.loads(text)
        # Two empty ledgers agree trivially. What has to agree is a full one.
        assert len(document["versions"]) == 2
        assert len(document["comparisons"]) == 1
        fingerprints = (
            workspace
            / "workspace"
            / "AmunEV_Engine_V2"
            / "history"
            / "AmunEV_Engine_V2_fingerprints.jsonl"
        ).read_text(encoding="utf-8")
        assert len(fingerprints.splitlines()) >= 2
        # `disk` is blanked because it MEASURES the workspace, and the
        # workspace contains `run_meta.json`, which this project deliberately
        # keeps outside the byte-identical guarantee (it holds elapsed
        # seconds). Two fresh runs differ there by a byte or two, which is the
        # measurement being right rather than the history being unstable. The
        # same-directory re-run below compares `disk` exactly.
        outputs.append(_blank_wallclock(text, disk=True) + fingerprints)
    assert outputs[0] == outputs[1]


def test_the_ledger_round_trips_through_json(tmp_path: Path) -> None:
    _place_pair(tmp_path, "dif_added_removed")
    settings = _settings(tmp_path)
    _result, ledger = track(settings, root=tmp_path)
    first = (tmp_path / "out" / "metatron_ledger.json").read_text(encoding="utf-8")

    reloaded = Ledger(settings, root=tmp_path).load(settings.ledger)
    assert reloaded.to_json() == first
    assert reloaded.versions == ledger.versions
    assert reloaded.comparisons == ledger.comparisons
    sample = reloaded.fingerprints(ledger.versions[0].id)[0]
    assert isinstance(sample.reachability, ReachabilityState)
    assert isinstance(sample.confidence, Confidence)
    assert isinstance(sample.provenance.method, Method)


def test_the_ledger_holds_no_floats_and_uses_the_one_serialiser(
    tmp_path: Path,
) -> None:
    """`canonical_dumps` refuses floats outright, so a float anywhere in a
    ledger record would raise rather than be rounded by a second serialiser."""
    _place_pair(tmp_path, "dif_signature")
    _result, ledger = track(_settings(tmp_path), root=tmp_path)
    assert ledger.versions
    for record in ledger.versions:
        assert isinstance(record.total_millis, int)
        assert record.stage_millis
        assert all(isinstance(v, int) for v in record.stage_millis.values())
        canonical_dumps(record)
    assert ledger.comparisons
    for comparison in ledger.comparisons:
        assert all(isinstance(v, int) for v in comparison.analysis_millis_delta.values())
        canonical_dumps(comparison)

    def _no_floats(node) -> None:
        assert not isinstance(node, float), f"a float reached the ledger: {node!r}"
        if isinstance(node, dict):
            for value in node.values():
                _no_floats(value)
        elif isinstance(node, list):
            for value in node:
                _no_floats(value)

    _no_floats(json.loads(ledger.to_json()))


def test_a_version_timestamp_is_labelled_as_a_filesystem_fact(tmp_path: Path) -> None:
    """A date read off the disk is never AST_DIRECT: copying a tree rewrites
    mtime and an archive extract stamps everything at once."""
    _place_pair(tmp_path, "dif_signature")
    _result, ledger = track(_settings(tmp_path), root=tmp_path)
    assert ledger.versions
    for record in ledger.versions:
        assert record.provenance.method is Method.FILE_METADATA
        assert record.provenance.confidence is not Confidence.CERTAIN
    # The element fingerprints stay AST facts, because that is what they are.
    fingerprints = ledger.fingerprints(ledger.versions[0].id)
    assert fingerprints
    for fingerprint in fingerprints:
        assert fingerprint.provenance.method is Method.AST_DIRECT


def test_an_owner_declared_order_is_not_a_filesystem_fact(tmp_path: Path) -> None:
    _place_pair(tmp_path, "dif_signature")
    settings = _settings(
        tmp_path, ORDER=["dif_signature_2026-02-03", "dif_signature_2026-01-14"]
    )
    _result, ledger = track(settings, root=tmp_path)
    assert ledger.versions
    for record in ledger.versions:
        assert record.provenance.method is Method.CONFIG_STRING_MATCH
        assert record.provenance.confidence is Confidence.CERTAIN


def test_a_ledger_from_another_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "old.json"
    path.write_text(
        json.dumps({"schema_version": "0.9.0", "versions": []}), encoding="utf-8"
    )
    with pytest.raises(LedgerError) as excinfo:
        Ledger(_settings(tmp_path), root=tmp_path).load(path)
    assert "0.9.0" in str(excinfo.value)
    assert SCHEMA_VERSION in str(excinfo.value)


def test_a_missing_ledger_is_an_empty_ledger_not_an_error(tmp_path: Path) -> None:
    ledger = Ledger(_settings(tmp_path), root=tmp_path).load("out/absent.json")
    assert ledger.versions == ()


def test_an_unknown_version_id_is_refused(tmp_path: Path) -> None:
    ledger = Ledger(_settings(tmp_path), root=tmp_path)
    with pytest.raises(LedgerError):
        ledger.fingerprints("nope")
    with pytest.raises(LedgerError):
        ledger.compare("nope", "also-nope")


def test_a_missing_versions_dir_is_refused_by_name(tmp_path: Path) -> None:
    ledger = Ledger(_settings(tmp_path, VERSIONS_DIR="not_here"), root=tmp_path)
    with pytest.raises(LedgerError) as excinfo:
        ledger.discover("not_here")
    assert "not_here" in str(excinfo.value)


def test_from_jsonable_rebuilds_a_fingerprint_exactly() -> None:
    fingerprint = ElementFingerprint(
        id="fp::v::engine::decide", version_id="v", element_id="engine::decide",
        kind="FUNCTION", content_hash="a", normalized_body_hash="b",
        signature="(score)", span=None, reachability=ReachabilityState.REACHES_SINK,
        confidence=Confidence.CERTAIN,
        provenance=Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN),
    )
    rebuilt = _from_jsonable(ElementFingerprint, json.loads(canonical_dumps(fingerprint)))
    assert rebuilt.reachability is ReachabilityState.REACHES_SINK
    assert rebuilt.provenance.method is Method.AST_DIRECT
    assert canonical_dumps(rebuilt) == canonical_dumps(fingerprint)


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def test_the_track_command_reports_what_it_did(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _place_pair(tmp_path, "dif_impact_rank")
    monkeypatch.chdir(tmp_path)
    code = cli.main(["track", "--sink", "engine::decide", "--env", "no-such-env"])
    assert code == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "2 version(s) known, 2 new" in out
    assert "decision paths" in out
    assert "accounted for, 0 unaccounted" in out
    history = tmp_path / "workspace" / "AmunEV_Engine_V2" / "history"
    assert (history / "AmunEV_Engine_V2_history.json").exists()
    assert (history / "AmunEV_Engine_V2_fingerprints.jsonl").exists()
    assert (history / "AmunEV_Engine_V2_comparisons.jsonl").exists()


def test_the_track_command_rejects_a_typo_in_the_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(cli.METATRON_SETTINGS, "SINK", [])
    assert cli.main(["track"]) == cli.EXIT_USAGE
    assert 'Did you mean "SINKS"?' in capsys.readouterr().err


def test_track_report_prints_the_whole_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _place_pair(tmp_path, "dif_move")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["track", "--report", "--env", "no-such-env"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "History — 2 version(s), 1 comparison(s)" in out
    assert "tree hash" in out
    assert "this tool took 0." in out
    assert "never how fast your engine runs" in out
    assert "runtime is not measured for this version" in out


def test_the_history_report_names_every_known_version(tmp_path: Path) -> None:
    _place_pair(tmp_path, "dif_wiring")
    _result, ledger = track(_settings(tmp_path), root=tmp_path)
    text = render_history_report(ledger)
    assert ledger.versions
    for record in ledger.versions:
        assert record.label in text
        assert record.tree_hash in text


# ---------------------------------------------------------------------------
# Constraint 1
# ---------------------------------------------------------------------------


def test_sentinel_is_never_executed(tmp_path: Path) -> None:
    SENTINEL_MARKER.unlink(missing_ok=True)
    _place(tmp_path, FIXTURES / "sentinel", "sentinel_2026-01-14")
    result, ledger = track(_settings(tmp_path), root=tmp_path)
    assert len(ledger.versions) == 1
    assert ledger.versions[0].counts["elements"] > 0
    assert render_track_report(result)
    assert not SENTINEL_MARKER.exists(), "the ledger executed the sentinel fixture"
