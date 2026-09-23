"""Card 15 round 6 -- the coverage banner must not overstate a limitation.

On the owner's real map the top of the page read, in red:

    1 source file was NOT read -- this map does not describe it:
    AmunEV_Engine_V2.py

That was false. The map is 10,164 elements *from that file*. What had
actually happened is that card 17's per-file size limit skipped the 14.8 MB
engine for **dependency analysis only**: its imports and interpreter
requirements were not read. The record was correct and narrow; the sentence
the page built from it was neither.

Overstating a limitation costs trust exactly as fast as hiding one, so the
banner is now built from a fact already in the artifacts -- how many
elements of this map came from that path -- and states what was and was not
covered, naming the analysis that skipped it.

Nothing here writes into `tests/fixtures/`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cascade_map.cli import analyze  # noqa: E402
from cascade_map.viewer.blueprint import (  # noqa: E402
    BlueprintView,
    build_blueprint_data,
)
from cascade_map.viewer.loader import ArtifactStore  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "mode_b"

FULL = BlueprintView(scope="full", max_nodes=0)


@pytest.fixture(scope="module")
def corpus_graph(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("banner_corpus")
    graph_dir = root / "graph"
    code, summary = analyze(CORPUS, graph_dir, strict_gate=False)
    assert summary["elements"] > 0
    return graph_dir


def _a_covered_path(graph_dir: Path) -> tuple[str, int]:
    """A real `.py` path this map holds elements for, and how many."""
    counts: dict[str, int] = {}
    for line in (graph_dir / "elements.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        span = json.loads(line).get("span") or {}
        path = span.get("path") or ""
        if isinstance(path, str) and path.endswith(".py"):
            counts[path] = counts.get(path, 0) + 1
    assert counts, "corpus produced no python elements"
    best = max(sorted(counts), key=lambda p: counts[p])
    return best, counts[best]


def _append_unresolved(graph_dir: Path, record: dict) -> None:
    with (graph_dir / "unresolved.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def _diagnostics(graph_dir: Path) -> list[dict]:
    store = ArtifactStore.load(graph_dir)
    data = build_blueprint_data(store, view=FULL)
    return data["diagnostics"]["unparsed_files"]


def test_a_narrow_skip_is_not_reported_as_a_file_missing_from_the_map(
    corpus_graph: Path, tmp_path: Path
) -> None:
    """Card 17's exact record, on a file the map is full of."""
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    for artifact in corpus_graph.iterdir():
        if artifact.is_file():
            (graph_dir / artifact.name).write_bytes(artifact.read_bytes())
    path, element_count = _a_covered_path(graph_dir)
    _append_unresolved(graph_dir, {
        "attempted": ["AST_DIRECT"],
        "candidate_confidence": "UNKNOWN",
        "candidate_ids": [],
        "description": (
            f"imports and interpreter requirements were not read for {path} "
            "(14.1 MB, over the dependency scanner's 20000000-byte limit; raise "
            "it with --max-source-mb)"
        ),
        "id": f"dep::source::{path}",
        "reason": "TOO_LARGE",
        "span": {"col": None, "end_line": None, "line": 1, "path": path},
    })

    # The corpus deliberately contains genuinely unreadable files; this
    # test is about the one that IS covered.
    rows = [r for r in _diagnostics(graph_dir) if r["path"] == path]
    assert len(rows) == 1
    row = rows[0]
    assert row["path"] == path
    assert row["elements_from_file"] == element_count > 0
    assert row["absent_from_map"] is False
    # The analysis is named, from the id prefix the card itself writes.
    assert row["analysis"] == "the dependency scanner (card 17)"
    # The record's own words are carried verbatim -- the viewer states the
    # skip, it does not re-describe it.
    assert "imports and interpreter requirements were not read" in row["description"]


def test_a_file_genuinely_absent_from_the_map_is_still_reported_as_absent(
    corpus_graph: Path, tmp_path: Path
) -> None:
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    for artifact in corpus_graph.iterdir():
        if artifact.is_file():
            (graph_dir / artifact.name).write_bytes(artifact.read_bytes())
    _append_unresolved(graph_dir, {
        "attempted": ["AST_DIRECT"],
        "candidate_confidence": "UNKNOWN",
        "candidate_ids": [],
        "description": "invalid syntax (<unknown>, line 3)",
        "id": "never_parsed_module",
        "reason": "SYNTAX_ERROR",
        "span": {"col": 1, "end_line": None, "line": 3, "path": "never_parsed.py"},
    })

    rows = [r for r in _diagnostics(graph_dir) if r["path"] == "never_parsed.py"]
    assert len(rows) == 1
    assert rows[0]["elements_from_file"] == 0
    assert rows[0]["absent_from_map"] is True
    # No id prefix this table knows: no analysis is named, rather than a
    # guessed one.
    assert rows[0]["analysis"] == ""


def test_the_corpus_files_that_really_are_absent_are_classified_as_absent(
    corpus_graph: Path,
) -> None:
    """The corpus contains files no analysis can read -- a non-UTF-8 module
    and a syntactically invalid one. Those ARE absent from the map, and the
    banner must still say so in the strong form: the fix must not have
    turned every warning into a reassurance."""
    rows = _diagnostics(corpus_graph)
    assert rows, "the corpus should still produce unreadable-file records"
    for row in rows:
        assert row["absent_from_map"] is (row["elements_from_file"] == 0)
    assert any(row["absent_from_map"] for row in rows)
