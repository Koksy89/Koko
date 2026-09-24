"""Tests for card 18 round 2 -- the per-script workspace.

These run the **real** pipeline. Every version is analysed by
`cascade_map.cli.analyze` over a real copy of a fixture tree, so every number
asserted here is one an owner would get. Nothing under any version tree is
imported or executed.

`tests/fixtures/` is never written to. Every tree is copied into `tmp_path`
first -- a corpus that moves while it is being read cannot grade anything, and
this build has paid for that lesson twice.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cascade_map import cli
from cascade_map.ledger import Settings, layout_for, render_track_report, track
from cascade_map.workspace import (
    DEFAULT_WORKSPACE,
    UNION_SCOPE,
    Workspace,
    WorkspaceError,
    disk_usage,
    element_life,
    migrate,
    project_name_for,
    resolve_layout,
    resolve_sport_scope,
    store_source,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
PROJECT = "AmunEV_Engine_V2"  # the shipped ENGINE's stem, and so the default


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _place(tmp_path: Path, source: Path, label: str, folder: str = "versions") -> Path:
    target = tmp_path / folder / label
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    return target


def _place_pair(tmp_path: Path, case: str, folder: str = "versions") -> None:
    root = FIXTURES / "versions" / case
    _place(tmp_path, root / "before", f"{case}_2026-01-14", folder)
    _place(tmp_path, root / "after", f"{case}_2026-02-03", folder)


def _settings(**overrides) -> Settings:
    base = {"ENV": ""}
    base.update(overrides)
    return Settings.from_mapping(base)


def _space(tmp_path: Path, project: str = PROJECT) -> Workspace:
    return Workspace(root=tmp_path / DEFAULT_WORKSPACE, project=project)


def _history(tmp_path: Path, project: str = PROJECT) -> dict:
    return json.loads(_space(tmp_path, project).history_file.read_text(encoding="utf-8"))


def _run(tmp_path: Path, case: str = "dif_signature", **overrides):
    _place_pair(tmp_path, case)
    return track(_settings(**overrides), root=tmp_path)


# ---------------------------------------------------------------------------
# 1. WORKSPACE is the one path, and PROJECT comes from the script
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "candidate,expected",
    [
        ("AmunEV_Engine_V2.py", "AmunEV_Engine_V2"),
        ("bin/go_live.py", "go_live"),
        ("/a/b/LazarusRunner", "LazarusRunner"),
        ("my engine v2.py", "my_engine_v2"),
        ("weird!!name", "weird_name"),
    ],
)
def test_project_is_derived_from_the_script_name(candidate: str, expected: str) -> None:
    """Naming the script is the whole instruction."""
    assert project_name_for(candidate) == expected


def test_a_project_name_is_never_invented() -> None:
    with pytest.raises(WorkspaceError) as exc:
        project_name_for("", None, "...")
    assert "PROJECT" in str(exc.value)
    assert "never guessed" in str(exc.value)


def test_every_file_carries_the_project_name(tmp_path: Path) -> None:
    """Nothing is ambiguous when a file is copied out or opened months later."""
    _run(tmp_path)
    space = _space(tmp_path)
    assert space.history_file.name == "AmunEV_Engine_V2_history.json"
    assert space.fingerprints_file.name == "AmunEV_Engine_V2_fingerprints.jsonl"
    assert space.comparisons_file.name == "AmunEV_Engine_V2_comparisons.jsonl"
    for path in (space.history_file, space.fingerprints_file, space.comparisons_file):
        assert path.is_file(), path
    assert space.runtime_history_file("etennis").name == "etennis_history.json"


def test_the_layout_is_exactly_the_one_the_design_fixes(tmp_path: Path) -> None:
    _run(tmp_path)
    project_dir = tmp_path / DEFAULT_WORKSPACE / PROJECT
    for relative in ("history", "history/runtime", "sources", "io/runs"):
        assert (project_dir / relative).is_dir(), relative
    runs = sorted((project_dir / "io" / "runs").iterdir())
    assert len(runs) == 2
    for run in runs:
        # io/runs/<UTC timestamp>/ -- the map from that run, with a suffix
        # when two versions were first seen in the same second.
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z(-\d+)?", run.name), run.name
        assert (run / "elements.jsonl").is_file()


def test_the_bulk_is_json_lines_and_the_story_is_json(tmp_path: Path) -> None:
    """One element one line, so appending a version appends lines and nothing
    is rewritten -- and every line is still JSON."""
    _result, ledger = _run(tmp_path)
    space = _space(tmp_path)
    lines = space.fingerprints_file.read_text(encoding="utf-8").splitlines()
    expected = sum(len(ledger.fingerprints(r.id)) for r in ledger.versions)
    assert len(lines) == expected > 0
    for line in lines:
        assert set(json.loads(line)) >= {"element_id", "version_id", "normalized_body_hash"}
    comparisons = space.comparisons_file.read_text(encoding="utf-8").splitlines()
    assert len(comparisons) == len(ledger.comparisons) == 1
    assert json.loads(comparisons[0])["change_counts"]


# ---------------------------------------------------------------------------
# 2. The three old keys keep working, and say what they now mean
# ---------------------------------------------------------------------------


def test_an_owner_who_upgrades_mid_project_keeps_their_paths(tmp_path: Path) -> None:
    """Not an error, and -- worse -- not a silently empty history."""
    _place_pair(tmp_path, "dif_signature", folder="old_versions")
    result, ledger = track(
        _settings(
            VERSIONS_DIR="old_versions",
            OUT_DIR="old_out",
            LEDGER="old_out/metatron_ledger.json",
        ),
        root=tmp_path,
    )
    assert len(ledger.versions) == 2
    assert (tmp_path / "old_out" / "metatron_ledger.json").is_file()
    # Each old key kept doing its old job: one artifact directory per version id.
    for record in ledger.versions:
        assert record.artifact_dir == f"old_out/{record.id}"
    # And the workspace copy exists alongside, so the history is in one place.
    assert _space(tmp_path).history_file.is_file()
    assert len(result.layout_notices) == 3


@pytest.mark.parametrize("key", ["VERSIONS_DIR", "OUT_DIR", "LEDGER"])
def test_each_legacy_key_prints_one_line_saying_what_it_now_means(
    tmp_path: Path, key: str
) -> None:
    layout = resolve_layout(
        root=tmp_path,
        workspace_root="workspace",
        project=PROJECT,
        versions_dir="v",
        out_dir="o",
        ledger="l.json",
        legacy_keys=[key],
    )
    assert layout.legacy_keys == (key,)
    assert len(layout.notices) == 1
    assert key in layout.notices[0]
    assert "still honoured" in layout.notices[0]


def test_an_unset_legacy_key_produces_no_notice_at_all(tmp_path: Path) -> None:
    layout = layout_for(_settings(), tmp_path)
    assert layout.legacy_keys == ()
    assert layout.notices == ()
    assert layout.versions_dir == layout.workspace.sources_dir
    assert layout.ledger_file == layout.workspace.history_file


def test_a_flat_versions_folder_is_found_and_the_run_says_so(tmp_path: Path) -> None:
    """The upgrade path with no settings at all: the old folder is read, said
    out loud, and left exactly where it is."""
    result, ledger = _run(tmp_path)
    assert len(ledger.versions) == 2
    assert (tmp_path / "versions").is_dir()
    notice = "".join(result.layout_notices)
    assert "metatron migrate" in notice
    assert "Nothing was moved" in notice
    # Relative, never absolute: an absolute path here would tie the document
    # to one machine and break constraint 4.
    assert str(tmp_path) not in notice


def test_the_source_store_is_a_fixed_point(tmp_path: Path) -> None:
    """Reading the store back discovers the same versions and stores nothing,
    which is what makes 'no settings at all' work after a migration."""
    _result, first = _run(tmp_path)
    shutil.rmtree(tmp_path / "versions")
    result, second = track(_settings(), root=tmp_path)
    assert {r.id for r in second.versions} == {r.id for r in first.versions}
    assert result.new_version_ids == ()
    assert all("already filed" in note or "already stored" in note
               for note in result.source_notes)


# ---------------------------------------------------------------------------
# 3. Sources: once per DISTINCT content, and never in silence
# ---------------------------------------------------------------------------


def test_a_distinct_version_is_stored_once_and_a_rerun_stores_nothing(
    tmp_path: Path,
) -> None:
    _result, ledger = _run(tmp_path)
    space = _space(tmp_path)
    stored = sorted(p.name for p in space.sources_dir.iterdir())
    assert stored == sorted(r.id for r in ledger.versions)
    before = disk_usage(space).sources_bytes
    assert before > 0

    result, _ledger = track(_settings(), root=tmp_path)
    assert disk_usage(space).sources_bytes == before
    assert result.new_version_ids == ()
    assert len(result.source_notes) == 2
    for note in result.source_notes:
        assert "already stored" in note or "already filed" in note


def test_two_folders_with_identical_content_cost_one_snapshot(tmp_path: Path) -> None:
    source = FIXTURES / "versions" / "dif_signature" / "before"
    _place(tmp_path, source, "alpha_2026-01-14")
    _place(tmp_path, source, "beta_2026-02-03")
    _result, ledger = track(_settings(), root=tmp_path)
    assert len(ledger.versions) == 1
    assert len(list(_space(tmp_path).sources_dir.iterdir())) == 1


def test_no_sources_records_the_hash_and_keeps_no_copy(tmp_path: Path) -> None:
    """And says which of the two happened. An absent snapshot must never be
    left looking like a stored one."""
    _place_pair(tmp_path, "dif_signature")
    result, ledger = track(_settings(SOURCES=False), root=tmp_path)
    space = _space(tmp_path)
    assert len(ledger.versions) == 2
    assert not space.sources_dir.exists() or not list(space.sources_dir.iterdir())
    assert disk_usage(space).sources_bytes == 0
    document = _history(tmp_path)
    assert len(document["sources"]) == 2
    for row in document["sources"]:
        assert row["stored"] is False
        assert row["path"] == ""
        assert "--no-sources" in row["reason"]
        # The hash is still recorded: that is what keeps the history readable.
        assert row["content_hash"] in {r.id for r in ledger.versions}


def test_store_source_names_every_outcome(tmp_path: Path) -> None:
    space = Workspace(root=tmp_path / "ws", project="P")
    tree = _place(tmp_path, FIXTURES / "versions" / "dif_move" / "before", "v1")

    fresh = store_source(tree, space, "hash1", keep=True)
    assert fresh.stored and "had not been seen before" in fresh.reason
    assert fresh.file_count > 0 and fresh.byte_count > 0

    again = store_source(tree, space, "hash1", keep=True)
    assert again.stored and "already stored" in again.reason

    skipped = store_source(tree, space, "hash2", keep=False)
    assert not skipped.stored and "--no-sources" in skipped.reason
    assert not space.source_dir("hash2").exists()

    itself = store_source(space.source_dir("hash1"), space, "hash1", keep=True)
    assert itself.stored and "already filed" in itself.reason


def test_the_history_reports_what_the_workspace_costs(tmp_path: Path) -> None:
    """So it never grows in silence."""
    result, _ledger = _run(tmp_path)
    disk = _history(tmp_path)["disk"]
    space = _space(tmp_path)
    measured = disk_usage(space)
    assert disk["sources_bytes"] == measured.sources_bytes > 0
    assert disk["sources_distinct"] == 2
    assert disk["total_bytes"] >= disk["sources_bytes"] + disk["history_bytes"]
    assert 0 <= disk["sources_share_percent"] <= 100
    assert "DISTINCT" in disk["note"]
    assert result.disk["total_bytes"] == disk["total_bytes"]
    # And the terminal prints the same number as the file, not a second
    # measurement taken a moment later that would quietly disagree with it.
    from cascade_map.ledger import render_track_report

    printed = render_track_report(result)
    assert f"{disk['total_bytes']:,} B over" in printed
    assert f"{disk['sources_bytes']:,} B in 2 distinct snapshot(s)" in printed


# ---------------------------------------------------------------------------
# 4. The per-sport rule, and the honesty rule under it
# ---------------------------------------------------------------------------


def test_a_sport_the_tool_cannot_separate_declares_the_union(tmp_path: Path) -> None:
    """The failure the owner is protecting against: a sport-specific file that
    is secretly the union."""
    _run(tmp_path)
    space = _space(tmp_path)
    for sport in Settings().sports:
        document = json.loads(
            space.runtime_history_file(sport).read_text(encoding="utf-8")
        )
        assert document["scope"] == UNION_SCOPE == "UNION ACROSS ALL SPORTS"
        assert document["separation"]["separated"] is False
        assert "no static rule separates" in document["reason"]
        assert "UNION over every sport" in document["reason"]
        assert document["separation"]["confidence"] == "UNKNOWN"


def test_two_sports_that_cannot_be_separated_get_the_same_data_and_say_so(
    tmp_path: Path,
) -> None:
    """Copying one blended map into seven files is the same distorted data in
    seven places. It is allowed only because every one of them declares it."""
    _run(tmp_path)
    space = _space(tmp_path)
    first = json.loads(space.runtime_history_file("etennis").read_text(encoding="utf-8"))
    second = json.loads(
        space.runtime_history_file("basketball").read_text(encoding="utf-8")
    )
    a = [dict(v, runs=[]) for v in first["versions"]]
    b = [dict(v, runs=[]) for v in second["versions"]]
    assert a == b, "unseparated sports must not differ; they were not computed apart"
    assert first["scope"] == second["scope"] == UNION_SCOPE


def test_a_statically_resolvable_sport_is_genuinely_computed_apart() -> None:
    spans = {
        "eng::sports.basketball::run": "sports/basketball.py",
        "eng::sports.basketball::score": "sports/basketball.py",
        "eng::sports.etennis::run": "sports/etennis.py",
        "eng::shared::util": "shared/util.py",
    }
    sports = ("basketball", "etennis", "ebasketball")
    basketball = resolve_sport_scope("basketball", element_spans=spans, other_sports=sports)
    etennis = resolve_sport_scope("etennis", element_spans=spans, other_sports=sports)
    assert basketball.separated and etennis.separated
    assert basketball.scope_text == "BASKETBALL ONLY"
    assert basketball.entry_ids != etennis.entry_ids
    assert basketball.provenance.confidence == "HEURISTIC"
    assert "sports/basketball.py" in basketball.evidence


def test_basketball_is_never_matched_inside_ebasketball() -> None:
    """If basketball's data is inferred onto another sport the tool loses all
    credibility, and a substring match is exactly how that happens."""
    spans = {"eng::sports.ebasketball::run": "sports/ebasketball.py"}
    sports = ("basketball", "ebasketball")
    scope = resolve_sport_scope("basketball", element_spans=spans, other_sports=sports)
    assert not scope.separated
    assert scope.scope_text == UNION_SCOPE
    # The positive control, so this cannot pass against something that simply
    # never separates anything: ebasketball's own file DOES separate it.
    owner = resolve_sport_scope("ebasketball", element_spans=spans, other_sports=sports)
    assert owner.separated
    assert owner.entry_ids == ("eng::sports.ebasketball::run",)


def test_a_declared_entry_naming_the_sport_beats_every_heuristic() -> None:
    scope = resolve_sport_scope(
        "basketball",
        element_spans={},
        other_sports=("basketball", "etennis"),
        declared_entry_ids=("eng::run_basketball", "eng::run_etennis"),
    )
    assert scope.separated
    assert scope.entry_ids == ("eng::run_basketball",)
    assert scope.provenance.confidence == "CERTAIN"
    assert scope.provenance.method == "AST_DIRECT"


def test_elements_and_hashes_stay_once_at_script_level(tmp_path: Path) -> None:
    """Properties of the source text do not vary by sport; a sport's file
    references them by id rather than copying them."""
    _run(tmp_path)
    space = _space(tmp_path)
    document = json.loads(
        space.runtime_history_file("basketball").read_text(encoding="utf-8")
    )
    assert len(document["versions"]) == 2
    for version in document["versions"]:
        assert set(version) == {
            "entry_ids",
            "finding_ids",
            "label",
            "no_sink_path_ids",
            "out_of_scope_ids",
            "reaches_sink_ids",
            "runs",
            "unknown_ids",
            "version_id",
        }
    # Content hashes appear exactly once, in the script-level fingerprints.
    text = space.runtime_history_file("basketball").read_text(encoding="utf-8")
    assert "content_hash" not in text
    assert "normalized_body_hash" not in text


def test_an_unmeasured_sport_says_not_measured_never_no_change(tmp_path: Path) -> None:
    _run(tmp_path)
    document = json.loads(
        _space(tmp_path).runtime_history_file("etennis").read_text(encoding="utf-8")
    )
    assert document["measured"] is False
    assert "NOT MEASURED FOR ETENNIS" in document["note"]
    assert "NEVER 'NO CHANGE'" in document["note"]
    for version in document["versions"]:
        assert version["runs"] == []


def test_a_version_whose_artifacts_are_gone_says_so_not_nothing(tmp_path: Path) -> None:
    """Empty lists would render as 'nothing reaches a decision'. That is a
    claim; this is an absence."""
    settings = _settings(SINKS=["engine::decide"])
    _place_pair(tmp_path, "dif_impact_rank")
    _result, ledger = track(settings, root=tmp_path)
    gone = ledger.versions[0]
    shutil.rmtree(tmp_path / gone.artifact_dir)
    track(settings, root=tmp_path)
    document = json.loads(
        _space(tmp_path).runtime_history_file("football").read_text(encoding="utf-8")
    )
    rows = {row["version_id"]: row for row in document["versions"]}
    assert len(rows) == 2
    assert "NOT MEASURED" in rows[gone.id]["unavailable"]
    assert "never 'nothing reaches a decision'" in rows[gone.id]["unavailable"]
    survivor = next(r for vid, r in rows.items() if vid != gone.id)
    assert "unavailable" not in survivor
    assert survivor["reaches_sink_ids"] and survivor["no_sink_path_ids"]


def test_the_history_records_the_project_and_every_sport(tmp_path: Path) -> None:
    _run(tmp_path)
    document = _history(tmp_path)
    assert document["project"] == PROJECT
    assert document["sports"] == list(Settings().sports)
    assert document["files"]["fingerprints"] == "AmunEV_Engine_V2_fingerprints.jsonl"
    assert document["files"]["comparisons"] == "AmunEV_Engine_V2_comparisons.jsonl"


def test_the_scope_block_says_one_shared_answer_once(tmp_path: Path) -> None:
    """Seven near-identical paragraphs are not seven times the honesty: they
    bury the line the owner needs and train them to skip the block."""
    result, _ledger = _run(tmp_path)
    printed = render_track_report(result)
    assert f"scope       {UNION_SCOPE} — all 7 sport(s)" in printed
    assert "a UNION file is NOT that sport's map" in printed
    # The reason appears ONCE, and never names one sport as if it were the
    # only one affected.
    assert " ".join(printed.split()).count("the sport is selected at runtime") == 1
    for sport in Settings().sports:
        assert f"Run mode 2 for {sport}" not in printed
    # But the full per-sport reason is still in that sport's JSON, which is
    # where a machine reads it and where nothing may be lost.
    document = json.loads(
        _space(tmp_path).runtime_history_file("etennis").read_text(encoding="utf-8")
    )
    assert "Run mode 2 for etennis to get its real path." in document["reason"]


SPORT_SINKS = ("sports.basketball::decide", "sports.etennis::decide")


def _sport_tree(tmp_path: Path, label: str, extra: str = "") -> Path:
    """A version whose sport selection IS statically resolvable: one module
    per sport, named for it, each with its own decision."""
    root = tmp_path / "versions" / label
    (root / "sports").mkdir(parents=True)
    (root / "shared.py").write_text(
        "def helper(x):\n    return x\n", encoding="utf-8"
    )
    for sport in ("basketball", "etennis"):
        (root / "sports" / f"{sport}.py").write_text(
            f"def decide(x):{extra}\n    return x > 1\n\n\n"
            f"def run_{sport}(x):\n    return decide(x)\n\n\n"
            f"def unused_{sport}(x):\n    return x\n",
            encoding="utf-8",
        )
    return root


def test_sports_with_different_answers_are_grouped_not_repeated(
    tmp_path: Path,
) -> None:
    _sport_tree(tmp_path, "sporty_2026-01-14")
    _sport_tree(tmp_path, "sporty_2026-02-03", extra="\n    x = x + 0")
    result, _ledger = track(_settings(SINKS=list(SPORT_SINKS)), root=tmp_path)

    kinds = {sport: kind for sport, kind, _reason in result.sport_scopes}
    assert kinds["basketball"] == "SEPARATED"
    assert kinds["etennis"] == "SEPARATED"
    assert kinds["football"] == "UNION"

    printed = render_track_report(result)
    assert "scope       SEPARATED: etennis, basketball  ·  UNION:" in printed
    # Two distinct answers, so exactly two reasons -- not seven.
    assert printed.count("source files name exactly one sport") == 1
    flat = " ".join(printed.split())
    assert flat.count("the sport is selected at runtime") == 1
    assert flat.count("source files name exactly one sport") == 1
    assert "etennis, basketball — source files name" in flat


def test_a_separated_sport_is_computed_apart_end_to_end(tmp_path: Path) -> None:
    """Two sports' files differ because they were COMPUTED differently, never
    because one map was copied twice."""
    _sport_tree(tmp_path, "sporty_2026-01-14")
    _sport_tree(tmp_path, "sporty_2026-02-03", extra="\n    x = x + 0")
    track(_settings(SINKS=list(SPORT_SINKS)), root=tmp_path)
    space = _space(tmp_path)

    basketball = json.loads(
        space.runtime_history_file("basketball").read_text(encoding="utf-8")
    )
    etennis = json.loads(
        space.runtime_history_file("etennis").read_text(encoding="utf-8")
    )
    football = json.loads(
        space.runtime_history_file("football").read_text(encoding="utf-8")
    )
    assert basketball["scope"] == "BASKETBALL ONLY"
    assert etennis["scope"] == "ETENNIS ONLY"
    assert football["scope"] == UNION_SCOPE

    b_row, e_row = basketball["versions"][0], etennis["versions"][0]
    assert b_row["entry_ids"] and e_row["entry_ids"]
    assert b_row["entry_ids"] != e_row["entry_ids"]
    # Each sport's scope holds its own module and nothing of the other's --
    # which is the difference between separating and filing one map twice.
    assert b_row["reaches_sink_ids"], "basketball reaches its own decision"
    assert all("basketball" in i for i in b_row["reaches_sink_ids"])
    assert all("etennis" in i for i in e_row["reaches_sink_ids"])
    assert b_row["reaches_sink_ids"] != e_row["reaches_sink_ids"]
    # The other sport is OUT OF SCOPE here, not absent and not unreachable.
    assert any("etennis" in i for i in b_row["out_of_scope_ids"])
    assert any("basketball" in i for i in e_row["out_of_scope_ids"])
    # The UNION file is neither of them: it holds every element of both.
    union_row = football["versions"][0]
    assert set(union_row["reaches_sink_ids"]) >= set(b_row["reaches_sink_ids"])
    assert set(union_row["reaches_sink_ids"]) >= set(e_row["reaches_sink_ids"])
    assert union_row["out_of_scope_ids"] == []


# ---------------------------------------------------------------------------
# 5. --mode from the terminal
# ---------------------------------------------------------------------------


def test_mode_two_on_trace_without_a_static_map_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    code = cli.main(["trace", str(empty), "--mode", "2"])
    assert code == cli.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "Nothing was executed" in err
    assert "Run mode 1 first" in err
    # Against `cli._PROG`, not a hardcoded name. These two assertions pinned
    # the OLD name and failed the moment the command was renamed -- a rename
    # the code had done correctly. A test that breaks on a correct change is
    # noise; this one now follows the rename.
    assert f"{cli._PROG} analyze" in err


def test_mode_one_on_trace_refuses_to_execute_anything(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    graph = tmp_path / "graph"
    graph.mkdir()
    (graph / "elements.jsonl").write_text('{"id": "x"}\n', encoding="utf-8")
    assert cli.main(["trace", str(graph), "--mode", "1"]) == cli.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "MODE 1 never executes your engine" in err
    assert "Nothing was executed" in err


def test_mode_two_on_analyze_builds_the_map_and_names_the_next_command(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    target = _place(tmp_path, FIXTURES / "versions" / "dif_move" / "before", "v1")
    out = tmp_path / "out"
    code = cli.main(
        ["analyze", str(target), "--out", str(out), "--no-gate", "--mode", "2"]
    )
    assert code == cli.EXIT_OK
    assert (out / "elements.jsonl").is_file()
    printed = capsys.readouterr().out
    assert "never executes anything" in printed
    assert f"{cli._PROG} trace {out} --mode 2" in printed


def test_the_mode_flag_overrides_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(cli.METATRON_SETTINGS, "MODE", 2)
    assert cli._mode_of(_Namespace(mode=None)) == 2
    assert cli._mode_of(_Namespace(mode=1)) == 1
    monkeypatch.setitem(cli.METATRON_SETTINGS, "MODE", 1)
    assert cli._mode_of(_Namespace(mode=2)) == 2


class _Namespace:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


# ---------------------------------------------------------------------------
# 6. history, in its four forms
# ---------------------------------------------------------------------------


def test_history_lists_every_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _run(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["history"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "1 project(s)" in out
    assert f"{PROJECT}: 2 version(s), 1 comparison(s)" in out


def test_history_with_no_workspace_says_absence_not_no_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli.main(["history"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "That is an absence, not an empty history" in out
    assert "metatron migrate" in out


def test_history_of_one_project_tells_its_whole_story(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _run(tmp_path, "dif_impact_rank", SINKS=["engine::decide"])
    monkeypatch.chdir(tmp_path)
    assert cli.main(["history", PROJECT]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert f"{PROJECT} — 2 version(s), 1 comparison(s)" in out
    assert "Disk —" in out
    assert "distinct snapshot(s)" in out
    assert "move a path to a decision" in out
    assert "NOT MEASURED" in out


def test_history_of_one_sport_declares_that_sport_s_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _run(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["history", PROJECT, "--sport", "etennis"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert f"scope:  {UNION_SCOPE}" in out
    assert "THIS FILE IS NOT SPORT-SPECIFIC" in out
    assert "NOT MEASURED for etennis, never 'no change'" in out


def test_history_of_one_element_is_its_whole_life(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The point of the whole design, and answerable only because every
    version's fingerprints were kept."""
    _result, ledger = _run(tmp_path, "dif_signature")
    space = _space(tmp_path)
    changed = None
    for row in space.fingerprints_file.read_text(encoding="utf-8").splitlines():
        record = json.loads(row)
        if record["element_id"].endswith("::price"):
            changed = record["element_id"]
    assert changed, "the fixture must contain a function that changed"

    life = element_life(space, changed)
    assert life.appeared_label
    assert len(life.versions) == 2
    assert len(life.body_changes) == 1
    assert life.first_observed_run == ""
    assert "NOT MEASURED" in life.note

    monkeypatch.chdir(tmp_path)
    assert cli.main(["history", PROJECT, "--element", changed]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "appeared in" in out
    assert "Decision reachability:" in out
    assert "Observed executing:" in out
    assert "Every version:" in out


def test_an_element_in_no_version_is_an_absence_that_says_so(tmp_path: Path) -> None:
    _run(tmp_path)
    with pytest.raises(WorkspaceError) as exc:
        element_life(_space(tmp_path), "engine::no_such_thing")
    assert "absence, not a" in str(exc.value)


def test_history_of_a_project_that_does_not_exist_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _run(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["history", "NotAProject"]) == cli.EXIT_USAGE
    assert "does not exist" in capsys.readouterr().err


def test_a_malformed_jsonl_line_refuses_rather_than_shortens_the_history(
    tmp_path: Path,
) -> None:
    _run(tmp_path)
    space = _space(tmp_path)
    space.fingerprints_file.write_text("{}\nnot json\n", encoding="utf-8")
    with pytest.raises(WorkspaceError) as exc:
        element_life(space, "anything")
    assert "shorter history" in str(exc.value)


# ---------------------------------------------------------------------------
# 7. migrate
# ---------------------------------------------------------------------------


def test_migrate_moves_a_flat_layout_and_reuses_every_version_id(
    tmp_path: Path,
) -> None:
    _place_pair(tmp_path, "dif_wiring")
    _result, flat = track(
        _settings(
            VERSIONS_DIR="versions", OUT_DIR="out", LEDGER="out/metatron_ledger.json"
        ),
        root=tmp_path,
    )
    ids = sorted(r.id for r in flat.versions)
    shutil.rmtree(tmp_path / DEFAULT_WORKSPACE)

    space = _space(tmp_path)
    result = migrate(
        root=tmp_path,
        workspace=space,
        ledger_path=tmp_path / "out" / "metatron_ledger.json",
        versions_dir=tmp_path / "versions",
        out_dir=tmp_path / "out",
    )
    assert list(result.reused_version_ids) == ids
    assert list(result.versions_moved) == ids
    assert list(result.artifacts_moved) == ids
    assert not result.did_nothing
    for version_id in ids:
        assert (space.sources_dir / version_id).is_dir()
        assert (space.runs_dir / version_id / "elements.jsonl").is_file()
    # The flat layout is COPIED, never destroyed: a half-finished migration
    # must not take the only copy of a map with it.
    assert (tmp_path / "out" / "metatron_ledger.json").is_file()
    assert (tmp_path / "versions").is_dir()


def test_migrate_is_idempotent_and_a_second_run_says_so(tmp_path: Path) -> None:
    _place_pair(tmp_path, "dif_wiring")
    track(
        _settings(
            VERSIONS_DIR="versions", OUT_DIR="out", LEDGER="out/metatron_ledger.json"
        ),
        root=tmp_path,
    )
    shutil.rmtree(tmp_path / DEFAULT_WORKSPACE)
    space = _space(tmp_path)
    arguments = dict(
        root=tmp_path,
        workspace=space,
        ledger_path=tmp_path / "out" / "metatron_ledger.json",
        versions_dir=tmp_path / "versions",
        out_dir=tmp_path / "out",
    )
    first = migrate(**arguments)
    second = migrate(**arguments)
    assert not first.did_nothing
    assert second.did_nothing
    assert second.versions_moved == ()
    assert second.artifacts_moved == ()
    assert second.versions_already_present == first.versions_moved
    assert any("idempotent" in note for note in second.notes)


def test_migrate_refuses_to_migrate_a_workspace_into_itself(tmp_path: Path) -> None:
    """It would copy every artifact a second time and double the disk use,
    silently."""
    _run(tmp_path)
    space = _space(tmp_path)
    before = disk_usage(space).total_bytes
    with pytest.raises(WorkspaceError) as exc:
        migrate(root=tmp_path, workspace=space, ledger_path=space.history_file)
    assert "already inside the workspace" in str(exc.value)
    assert "Nothing was done" in str(exc.value)
    assert disk_usage(space).total_bytes == before


def test_migrate_with_no_ledger_refuses_by_name(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError) as exc:
        migrate(
            root=tmp_path,
            workspace=_space(tmp_path),
            ledger_path=tmp_path / "out" / "metatron_ledger.json",
        )
    assert "nothing here to move" in str(exc.value)


def test_a_migrated_history_is_not_re_analysed(tmp_path: Path) -> None:
    """Version ids are content hashes, so nothing has to be recomputed."""
    _place_pair(tmp_path, "dif_wiring")
    legacy = _settings(
        VERSIONS_DIR="versions", OUT_DIR="out", LEDGER="out/metatron_ledger.json"
    )
    track(legacy, root=tmp_path)
    shutil.rmtree(tmp_path / DEFAULT_WORKSPACE)
    migrate(
        root=tmp_path,
        workspace=_space(tmp_path),
        ledger_path=tmp_path / "out" / "metatron_ledger.json",
        versions_dir=tmp_path / "versions",
        out_dir=tmp_path / "out",
    )
    shutil.rmtree(tmp_path / "versions")
    shutil.rmtree(tmp_path / "out")

    calls: list[str] = []

    def analyse(source_root, out_dir, settings):
        calls.append(source_root.name)
        raise AssertionError("a migrated version must never be re-analysed")

    result, ledger = track(_settings(), root=tmp_path, analyse=analyse)
    assert calls == []
    assert len(ledger.versions) == 2
    assert result.new_version_ids == ()
    # Self-contained: every path was repointed at the workspace, so deleting
    # the old flat layout -- which is the first thing anyone does after
    # migrating -- does not strand the history.
    for record in ledger.versions:
        assert record.source_path.startswith(f"{DEFAULT_WORKSPACE}/{PROJECT}/sources/")
        assert record.artifact_dir.startswith(f"{DEFAULT_WORKSPACE}/{PROJECT}/io/runs/")
        assert (tmp_path / record.artifact_dir / "elements.jsonl").is_file()
    # And a comparison still works from those paths alone.
    before, after = ledger.consecutive_pairs()[0]
    ledger._comparisons.clear()
    assert ledger.compare(before, after).elements_after > 0


def test_the_migrate_command_reports_what_it_did(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _place_pair(tmp_path, "dif_wiring")
    track(
        _settings(
            VERSIONS_DIR="versions", OUT_DIR="out", LEDGER="out/metatron_ledger.json"
        ),
        root=tmp_path,
    )
    shutil.rmtree(tmp_path / DEFAULT_WORKSPACE)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["migrate"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "2 reused, 0 recomputed" in out
    assert "nothing was re-analysed" in out
    assert cli.main(["migrate"]) == cli.EXIT_OK
    assert "NOTHING TO DO" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 8. Determinism
# ---------------------------------------------------------------------------


def test_two_runs_are_byte_identical_including_the_disk_figures(
    tmp_path: Path,
) -> None:
    _place_pair(tmp_path, "dif_wiring")
    settings = _settings()
    track(settings, root=tmp_path)
    space = _space(tmp_path)
    files = [space.history_file, space.fingerprints_file, space.comparisons_file]
    files += [space.runtime_history_file(s) for s in settings.sports]
    first = {path.name: path.read_bytes() for path in files}
    # Two empty documents are also identical, so the content is asserted first.
    assert json.loads(first[space.history_file.name])["versions"]
    assert first[space.fingerprints_file.name].strip()

    track(settings, root=tmp_path)
    second = {path.name: path.read_bytes() for path in files}
    # Run 1 stored the snapshots; run 2 found them already there, and the
    # source record SAYS which happened. That one string is the only thing
    # that may differ, and it differs because the two runs genuinely did
    # different work -- it is the honesty rule, not drift.
    changed = {name for name in first if first[name] != second[name]}
    assert changed == {space.history_file.name}
    a = json.loads(first[space.history_file.name])
    b = json.loads(second[space.history_file.name])
    assert [r.pop("reason") for r in a["sources"]] != [r.pop("reason") for r in b["sources"]]
    assert a == b

    # From run 2 on it is a steady state, byte for byte, disk figures included.
    track(settings, root=tmp_path)
    for path in files:
        assert path.read_bytes() == second[path.name], path.name


def test_two_fresh_runs_agree_across_hash_seeds(tmp_path: Path) -> None:
    outputs = []
    for index, seed in enumerate(("0", "7919")):
        root = tmp_path / f"run{index}"
        root.mkdir()
        _place_pair(root, "dif_rename")
        completed = subprocess.run(
            [sys.executable, "-m", "cascade_map.cli", "track", "--env", "no-such-env"],
            cwd=root,
            env={
                **os.environ,
                "PYTHONHASHSEED": seed,
                "PYTHONPATH": str(REPO_ROOT / "src"),
            },
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert completed.returncode == 0, completed.stderr
        space = _space(root)
        # A sport file is entirely derived from the graph, so unlike the story
        # it carries nothing wall-clock at all and is compared verbatim.
        text = space.runtime_history_file("basketball").read_text(encoding="utf-8")
        document = json.loads(text)
        assert len(document["versions"]) == 2
        assert document["scope"] == UNION_SCOPE
        outputs.append(text + space.fingerprints_file.read_text(encoding="utf-8"))
    assert outputs[0] == outputs[1]


def test_the_workspace_holds_no_absolute_paths(tmp_path: Path) -> None:
    """An absolute path in an artifact ties it to one machine, and two machines
    must produce the same bytes."""
    result, _ledger = _run(tmp_path)
    space = _space(tmp_path)
    for path in (space.history_file, space.fingerprints_file, space.comparisons_file):
        assert str(tmp_path) not in path.read_text(encoding="utf-8"), path.name
    for sport in Settings().sports:
        text = space.runtime_history_file(sport).read_text(encoding="utf-8")
        assert str(tmp_path) not in text
    assert all(str(tmp_path) not in notice for notice in result.layout_notices)


# ---------------------------------------------------------------------------
# 9. Nothing is executed
# ---------------------------------------------------------------------------


def test_nothing_under_a_version_tree_is_ever_executed(tmp_path: Path) -> None:
    """The sentinel: a version whose import would write a marker file."""
    marker = tmp_path / "sentinel_marker.txt"
    tree = tmp_path / "versions" / "sentinel_2026-01-14"
    tree.mkdir(parents=True)
    (tree / "boom.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed')\n"
        "def decide(x):\n    return x > 1\n",
        encoding="utf-8",
    )
    _result, ledger = track(_settings(), root=tmp_path)
    assert len(ledger.versions) == 1
    assert not marker.exists(), "the target was executed"
    # And the whole workspace was still written.
    assert _space(tmp_path).history_file.is_file()
