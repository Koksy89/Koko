"""Card 15 round 6 -- the data island is smaller and *nothing was dropped*.

The compression here is two mechanical, exactly reversible transforms:
interning (`_pack_data`) and gzip+base64 (`_compress_island`). The code is
easy; the claim is not. "Lossless" is only worth anything if the suite can
fail when it stops being true, so this file does not check sizes and call
it done -- it reconstructs the island and compares it to the original
**field by field, with types**, and separately drives the real browser
decoder over the same data (`test_blueprint_render.py`).

Why a plain `==` is not enough: Python considers ``True == 1 == 1.0``, so
``{"a": True} == {"a": 1}`` and a round trip that silently turned every
boolean into an integer would pass an equality assertion. Every comparison
below goes through :func:`assert_identical`, which requires the *type* to
match at every leaf.

Nothing here writes into `tests/fixtures/`.
"""

from __future__ import annotations

import base64
import gzip
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_viewer import build_fixture  # noqa: E402

from cascade_map.cli import analyze  # noqa: E402
from cascade_map.viewer.blueprint import (  # noqa: E402
    PACK_FORMAT,
    BlueprintTooLarge,
    BlueprintView,
    _compress_island,
    _island_json,
    _pack_data,
    _safe_json,
    _unpack_data,
    build_blueprint_data,
    render_blueprint,
    render_blueprint_to_file,
)
from cascade_map.viewer.loader import ArtifactStore  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "mode_b"

FULL = BlueprintView(scope="full", max_nodes=0)
FULL_PLAIN = BlueprintView(scope="full", max_nodes=0, compress=False)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def assert_identical(left: Any, right: Any, path: str = "$") -> None:
    """Deep equality that also requires the JSON type to match.

    Raises on the first difference, naming the exact path to it, so a
    failure says *which field* of *which record* changed rather than
    "structures differ".
    """
    assert type(left) is type(right), f"{path}: {type(left).__name__} != {type(right).__name__}"
    if isinstance(left, dict):
        assert sorted(left) == sorted(right), (
            f"{path}: keys differ; only in left {sorted(set(left) - set(right))!r}, "
            f"only in right {sorted(set(right) - set(left))!r}"
        )
        for key in sorted(left):
            assert_identical(left[key], right[key], f"{path}.{key}")
    elif isinstance(left, list):
        assert len(left) == len(right), f"{path}: {len(left)} items != {len(right)}"
        for index, (a, b) in enumerate(zip(left, right)):
            assert_identical(a, b, f"{path}[{index}]")
    else:
        assert left == right, f"{path}: {left!r} != {right!r}"


def count_leaves(node: Any) -> int:
    if isinstance(node, dict):
        return sum(count_leaves(v) for v in node.values()) + len(node)
    if isinstance(node, list):
        return sum(count_leaves(v) for v in node)
    return 1


def island_of(html: str) -> dict[str, Any]:
    """The packed island as the page carries it, still packed."""
    for marker, decode in (
        ('id="cascade-blueprint-data-gz">', _gunzip_b64),
        ('id="cascade-blueprint-data">', json.loads),
    ):
        if marker in html:
            start = html.index(marker) + len(marker)
            end = html.index("</script>", start)
            return decode(html[start:end])
    raise AssertionError("page carries no data island")


def _gunzip_b64(text: str) -> dict[str, Any]:
    return json.loads(gzip.decompress(base64.b64decode(text)).decode("utf-8"))


@pytest.fixture(scope="module")
def corpus_graph(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("compress_corpus")
    graph_dir = root / "graph"
    code, summary = analyze(CORPUS, graph_dir, strict_gate=False)
    assert summary["elements"] > 0
    return graph_dir


@pytest.fixture(scope="module")
def corpus_data(corpus_graph: Path) -> dict[str, Any]:
    store = ArtifactStore.load(corpus_graph)
    return build_blueprint_data(store, view=FULL)


# ---------------------------------------------------------------------------
# 1. Losslessness, the whole point
# ---------------------------------------------------------------------------


def test_round_trip_reconstructs_the_corpus_island_exactly(corpus_data: dict) -> None:
    """Every node, edge, detail and field of the real corpus, byte for byte.

    Not a spot check and not a count: `assert_identical` walks the entire
    structure and requires matching types at every leaf.
    """
    packed = _pack_data(corpus_data)
    assert packed["format"] == PACK_FORMAT
    restored = _unpack_data(packed)
    assert_identical(restored, corpus_data)
    # And the walk really did touch the whole graph, so the assertion above
    # cannot have passed by comparing two empty structures.
    assert count_leaves(corpus_data) > 10_000


def test_round_trip_survives_gzip_and_base64(corpus_data: dict) -> None:
    packed = _pack_data(corpus_data)
    text = base64.b64decode(_compress_island(packed))
    restored = _unpack_data(json.loads(gzip.decompress(text).decode("utf-8")))
    assert_identical(restored, corpus_data)


def test_every_execution_lineage_and_diff_record_survives(corpus_data: dict) -> None:
    """The tabs' own records, compared one by one.

    A whole-structure comparison already covers these; this states the
    per-record claim separately so that a failure reads as "edge 41 of the
    lineage tab lost its confidence", which is what the owner needs.
    """
    restored = _unpack_data(_pack_data(corpus_data))
    assert corpus_data["execution"]["nodes"] and corpus_data["lineage"]["nodes"]
    for tab in ("execution", "lineage", "diff"):
        before, after = corpus_data[tab], restored[tab]
        assert len(before["nodes"]) == len(after["nodes"])
        for index, (a, b) in enumerate(zip(before["nodes"], after["nodes"])):
            assert_identical(a, b, f"{tab}.nodes[{index}]")
        for index, (a, b) in enumerate(zip(before["wires"], after["wires"])):
            assert_identical(a, b, f"{tab}.wires[{index}]")
    for element_id in sorted(corpus_data["element_details"]):
        assert_identical(
            corpus_data["element_details"][element_id],
            restored["element_details"][element_id],
            f"element_details[{element_id}]",
        )


def test_types_json_keeps_apart_are_not_conflated() -> None:
    """`True`, `1` and `1.0` are three values, and stay three values.

    Python hashes them equal, so a pool keyed on the value alone would
    merge them and hand back the wrong type -- the exact shape of a
    "lossless" transform that quietly is not.
    """
    data = {
        "flags": [True, False, 1, 0, 1.0, 0.0],
        "nulls": [None, "", "null", 0],
        "nested": {"1": 1, "1.0": 1.0, "true": True},
    }
    restored = _unpack_data(_pack_data(data))
    assert_identical(restored, data)
    assert [type(v).__name__ for v in restored["flags"]] == [
        "bool", "bool", "int", "int", "float", "float"
    ]


def test_unicode_and_hostile_text_survive_both_encodings() -> None:
    hostile = "</script><img src=x onerror=alert(1)>&<>—é\U0001f600\\\"'\n\t"
    data = {"note": hostile, "list": [hostile, {"k": hostile}]}
    assert_identical(_unpack_data(_pack_data(data)), data)
    packed = _pack_data(data)
    assert_identical(_unpack_data(_gunzip_b64(_compress_island(packed))), data)


def test_decoder_refuses_an_island_it_does_not_understand() -> None:
    with pytest.raises(ValueError, match="cascade-blueprint-pack/1"):
        _unpack_data({"format": "something-else", "pool": [], "shapes": [], "root": 0})


# ---------------------------------------------------------------------------
# 2. Determinism -- the tables must be sorted, never insertion-ordered
# ---------------------------------------------------------------------------


def test_pool_and_shape_tables_are_sorted(corpus_data: dict) -> None:
    packed = _pack_data(corpus_data)
    strings = [value for value in packed["pool"] if isinstance(value, str)]
    assert strings == sorted(strings)
    integers = [value for value in packed["pool"] if isinstance(value, int) and value is not True]
    assert integers == sorted(integers)
    shapes = [tuple(shape) for shape in packed["shapes"]]
    assert len(set(shapes)) == len(shapes), "a shape was stored twice"


def test_two_renders_are_byte_identical(corpus_graph: Path, tmp_path: Path) -> None:
    store = ArtifactStore.load(corpus_graph)
    for view in (FULL, FULL_PLAIN):
        first = render_blueprint(store, view=view, compress=view.compress)
        second = render_blueprint(store, view=view, compress=view.compress)
        assert first == second, f"{view.compress=} render is not deterministic"
    # ...and across processes, which is where a gzip mtime header or a set
    # iteration order would show up.
    out = tmp_path / "a.html"
    render_blueprint_to_file(corpus_graph, out, view=FULL)
    first_bytes = out.read_bytes()
    out.unlink()
    render_blueprint_to_file(corpus_graph, out, view=FULL)
    assert out.read_bytes() == first_bytes


# ---------------------------------------------------------------------------
# 3. It is actually smaller -- each technique's contribution, separately
# ---------------------------------------------------------------------------


def test_each_technique_shrinks_the_island(corpus_data: dict) -> None:
    raw = len(_safe_json(corpus_data))
    packed = _pack_data(corpus_data)
    interned = len(_safe_json(packed))
    gzipped_only = len(base64.b64encode(gzip.compress(
        json.dumps(corpus_data, sort_keys=True, ensure_ascii=False,
                   separators=(",", ":")).encode("utf-8"),
        compresslevel=9, mtime=0)))
    both = len(_compress_island(packed))
    assert interned < raw, f"interning grew the island: {raw} -> {interned}"
    assert gzipped_only < raw
    assert both < gzipped_only, "interning must still help after gzip"
    assert both < raw / 5, f"{raw} -> {both} is less than 5x"


def test_compressed_page_is_smaller_than_the_uncompressed_one(corpus_graph: Path,
                                                              tmp_path: Path) -> None:
    store = ArtifactStore.load(corpus_graph)
    small = len(render_blueprint(store, view=FULL, compress=True))
    large = len(render_blueprint(store, view=FULL_PLAIN, compress=False))
    assert small < large


# ---------------------------------------------------------------------------
# 4. The size guard now judges what the browser pays
# ---------------------------------------------------------------------------


def test_guard_measures_the_compressed_island_not_the_raw_json(tmp_path: Path) -> None:
    """A page whose raw JSON is over the limit but which compresses under it
    is written, and the estimate says so with the measured number."""
    build_fixture(tmp_path)
    store = ArtifactStore.load(tmp_path)
    raw = len(_safe_json(build_blueprint_data(store, view=FULL)))
    limit = raw // 2
    out = tmp_path / "page" / "blueprint.html"
    view = BlueprintView(scope="full", max_nodes=0, size_limit_bytes=limit)
    _store, _selection, estimate = render_blueprint_to_file(tmp_path, out, view=view)
    assert out.exists()
    assert estimate.measured
    assert estimate.actual_bytes <= limit
    assert estimate.actual_bytes < estimate.total_bytes


def test_guard_still_refuses_and_writes_nothing_when_even_compressed_is_too_big(
    tmp_path: Path,
) -> None:
    build_fixture(tmp_path)
    out = tmp_path / "nested" / "blueprint.html"
    view = BlueprintView(scope="full", max_nodes=0, size_limit_bytes=200)
    with pytest.raises(BlueprintTooLarge):
        render_blueprint_to_file(tmp_path, out, view=view)
    assert not out.exists()
    assert not out.parent.exists()


def test_force_still_writes_whatever_the_size(tmp_path: Path) -> None:
    build_fixture(tmp_path)
    out = tmp_path / "blueprint.html"
    view = BlueprintView(scope="full", max_nodes=0, size_limit_bytes=1, force=True)
    _store, _selection, estimate = render_blueprint_to_file(tmp_path, out, view=view)
    assert out.exists()
    assert not estimate.over


# ---------------------------------------------------------------------------
# 5. The uncompressed island is a real, usable page, not a stub
# ---------------------------------------------------------------------------


def test_no_compress_writes_a_plain_readable_island(corpus_graph: Path,
                                                    tmp_path: Path) -> None:
    out = tmp_path / "plain.html"
    render_blueprint_to_file(corpus_graph, out, view=FULL_PLAIN)
    html = out.read_text(encoding="utf-8")
    assert 'id="cascade-blueprint-data"' in html
    assert 'id="cascade-blueprint-data-gz">' not in html
    # Still escaped: no literal `<`, `>` or `&` inside the island.
    start = html.index('id="cascade-blueprint-data">') + len('id="cascade-blueprint-data">')
    end = html.index("</script>", start)
    body = html[start:end]
    assert "<" not in body and ">" not in body and "&" not in body
    store = ArtifactStore.load(corpus_graph)
    assert_identical(_unpack_data(json.loads(body)),
                     build_blueprint_data(store, view=FULL))


def test_compressed_island_needs_no_html_escaping(corpus_graph: Path) -> None:
    """base64's alphabet contains no HTML-significant character, so the
    island cannot break out of its own script tag."""
    store = ArtifactStore.load(corpus_graph)
    html = render_blueprint(store, view=FULL, compress=True)
    marker = 'id="cascade-blueprint-data-gz">'
    start = html.index(marker) + len(marker)
    end = html.index("</script>", start)
    body = html[start:end]
    assert body
    assert set(body) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
    )


def test_island_json_is_compact_and_sorted(corpus_data: dict) -> None:
    text = _island_json(_pack_data(corpus_data))
    assert ", " not in text[:200]
    assert text.startswith('{"format":')
