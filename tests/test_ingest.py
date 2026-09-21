"""Tests for card 1 -- ingestion and inventory.

Uses `tests/fixtures/mode_b/inv_*`, owned by `fixture-writer` (card 8). This
file never creates, edits or deletes anything under `tests/fixtures/`.

Comparison strategy: `expected.json` intentionally omits default-valued
fields (see e.g. `inv_kinds/expected.json`, where `GLOBAL_VAR` has no
`signature` key). So a fixture's expected element is checked as a *subset* of
the actual element's fields: every key the fixture specifies must match; the
actual element may carry more. `content_hash` is skipped whenever
`content_hash_asserted` is `false` (the fixture's own "don't check this by
hand" marker) or the value is a known placeholder. `span.line` is
informational only -- see the note below.

Elements are matched by **`(module, qualname, kind)`**, not by the fixture's
`id` field -- see the load-bearing discrepancy below.

Known fixture defects (reported, not silently patched around; card 1's own
implementation follows the binding contract, verified separately by
`tests/test_contracts.py::test_module_and_member_ids`):
  - **`inv_kinds/expected.json`'s `id` field does not follow `make_id`.** For
    nested elements it joins every scope level with `::` and drops
    `<locals>` (e.g. `id: "inv_kinds::MyClass::class_var"` next to
    `qualname: "MyClass.class_var"`; `id: "inv_kinds::outer::inner"` next to
    `qualname: "outer.<locals>.inner"`). `make_id` is `f"{module}::{qualname}"`
    -- a single `::` between module and qualname, dots preserved inside the
    qualname -- exactly as `ARCHITECTURE.md`'s own worked example
    (`strategy.rules::RuleSet.evaluate`) and `test_module_and_member_ids`
    show. Changing card 1's ID scheme to match the fixture would break the
    settled S4 identity contract (OPEN_QUESTIONS.md) that every other card
    keys off. This file therefore matches fixture elements by `qualname`
    (which the fixture gets right throughout) instead of by `id`, and
    reports the discrepancy for card 8 to fix rather than reproducing it.
  - `inv_kinds/expected.json` hand-counts several `span.line` values off by
    one or two (e.g. `simple_func` at source line 10, expected says 11).
    Confirmed by `grep -n` against the fixture source. Line numbers are
    therefore not asserted exactly in this file.
  - `inv_blob/expected.json` gives `byte_size: 1048576`; the literal is
    wrapped in `'''...'''` with a leading and trailing newline inside the
    quotes, so the actual `str` value is 1048578 bytes. Confirmed by parsing
    the fixture with `ast` directly. `byte_size` is asserted against the
    true parsed size, not the fixture's off-by-two count.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cascade_map.contracts.interfaces import (
    ElementKind,
    UnresolvedReason,
    canonical_jsonl,
)
from cascade_map.ingest import inventory

# Relative to the repository root (where pytest is expected to run from), not
# resolved to absolute -- expected.json's span.path values are themselves
# relative ("tests/fixtures/mode_b/inv_kinds/__init__.py"), and SourceSpan.path
# is documented as "relative to the target root": using a relative root here
# is what makes that match.
FIXTURES = Path("tests/fixtures")
MODE_B = FIXTURES / "mode_b"

_HASH_PLACEHOLDERS = {"abc123", "opaque"}

# Fields never asserted exactly against expected.json -- see module docstring.
_SOFT_FIELDS = {"content_hash", "byte_size"}


def _case_dir(case_id: str) -> Path:
    return MODE_B / case_id


def _has_case(case_id: str) -> bool:
    return (_case_dir(case_id) / "expected.json").exists()


def _run_case(case_id: str, tmp_path: Path):
    return inventory(str(_case_dir(case_id)), cache_dir=tmp_path / "cache")


def _element_dict(element) -> dict:
    from dataclasses import asdict

    d = asdict(element)
    d["kind"] = str(element.kind)
    d["provenance"] = {**d["provenance"], "method": str(element.provenance.method), "confidence": str(element.provenance.confidence)}
    return d


def _assert_subset(expected: dict, actual: dict, path: str = "") -> None:
    for key, expected_value in expected.items():
        if key in _SOFT_FIELDS or key in ("content_hash_asserted",):
            continue
        if key == "content_hash" and expected.get("content_hash_asserted") is False:
            continue
        here = f"{path}.{key}" if path else key
        assert key in actual, f"missing field {here!r}"
        actual_value = actual[key]
        if key == "span" and isinstance(expected_value, dict):
            # line numbers are not load-bearing here -- see module docstring
            expected_value = {k: v for k, v in expected_value.items() if k != "line"}
            _assert_subset(expected_value, actual_value, here)
            continue
        if isinstance(expected_value, dict):
            _assert_subset(expected_value, actual_value, here)
            continue
        assert actual_value == expected_value, f"{here}: expected {expected_value!r}, got {actual_value!r}"


def _check_case(case_id: str, tmp_path: Path) -> tuple[list, list]:
    expected = json.loads((_case_dir(case_id) / "expected.json").read_text())
    elements, unresolved = _run_case(case_id, tmp_path)

    from cascade_map.contracts.interfaces import make_id

    # A list per key, not a single value: `inv_redefinition` deliberately puts
    # two elements under the same (module, qualname, kind) -- consumed in
    # source order, which both expected.json and `elements` share.
    actual_by_key: dict[tuple, list[dict]] = {}
    for e in elements:
        actual_by_key.setdefault((e.module, e.qualname, str(e.kind)), []).append(_element_dict(e))

    for exp_el in expected.get("elements", []):
        key = (exp_el["module"], exp_el["qualname"], exp_el["kind"])
        bucket = actual_by_key.get(key, [])
        assert bucket, f"{case_id}: expected element {exp_el['id']!r} ({key}) missing from actual output"
        actual = bucket.pop(0)
        exp_compare = {k: v for k, v in exp_el.items() if k != "id"}
        if "decorators" in exp_compare:
            exp_compare["decorators"] = list(exp_compare["decorators"])
        actual = {**actual, "decorators": list(actual.get("decorators", ()))}
        _assert_subset(exp_compare, actual)
        # the ID itself: verified against make_id (the binding contract), not
        # against the fixture's own (defective, see module docstring) id field
        assert actual["id"] == make_id(actual["module"], actual["qualname"]) or "#" in actual["id"]
        if exp_el.get("content_hash_asserted") is False or exp_el.get("content_hash") in _HASH_PLACEHOLDERS:
            assert actual["content_hash"], "content_hash must never be empty"
        elif exp_el.get("content_hash") is not None:
            assert actual["content_hash"] == exp_el["content_hash"]

    actual_unresolved_by_id = {}
    for u in unresolved:
        actual_unresolved_by_id.setdefault(u.id, []).append(u)
    for exp_u in expected.get("unresolved", []):
        matches = actual_unresolved_by_id.get(exp_u["id"], [])
        assert matches, f"{case_id}: expected unresolved {exp_u['id']!r} missing"
        assert any(
            m.reason == exp_u["reason"] and m.description == exp_u["description"] for m in matches
        ), f"{case_id}: no unresolved match for {exp_u!r}, got {matches!r}"

    return elements, unresolved


# ---------------------------------------------------------------------------
# inv_kinds
# ---------------------------------------------------------------------------


def test_inv_kinds(tmp_path: Path) -> None:
    assert _has_case("inv_kinds"), "fixture-writer has not built inv_kinds yet"
    elements, unresolved = _check_case("inv_kinds", tmp_path)
    kinds = {e.kind for e in elements}
    for required in (
        ElementKind.MODULE,
        ElementKind.CLASS,
        ElementKind.FUNCTION,
        ElementKind.METHOD,
        ElementKind.PROPERTY,
        ElementKind.ASSIGNMENT,
    ):
        assert required in kinds, f"inv_kinds must produce {required}"
    # nested function and closure: qualnames use Python's own <locals> convention
    by_id = {e.id: e for e in elements}
    assert "inv_kinds" in by_id
    inner = [e for e in elements if e.qualname == "outer.<locals>.inner"]
    closure = [e for e in elements if e.qualname == "with_closure.<locals>.closure"]
    assert inner and inner[0].kind == ElementKind.FUNCTION
    assert closure and closure[0].kind == ElementKind.FUNCTION


def test_inv_kinds_sentinel_not_tripped(tmp_path: Path) -> None:
    _assert_sentinel_untripped()
    elements, _ = inventory(str(_case_dir("inv_kinds")), cache_dir=tmp_path / "cache")
    _assert_sentinel_untripped()
    assert len(elements) > 5, "inventory must have actually run, not merely not-crashed"


# ---------------------------------------------------------------------------
# inv_redefinition
# ---------------------------------------------------------------------------


def test_inv_redefinition(tmp_path: Path) -> None:
    assert _has_case("inv_redefinition")
    elements, _ = _check_case("inv_redefinition", tmp_path)
    ids = [e.id for e in elements if e.name == "func"]
    assert ids == ["inv_redefinition::func", "inv_redefinition::func#2"], ids
    # the first occurrence never gets a suffix, even though a second exists
    first = next(e for e in elements if e.id == "inv_redefinition::func")
    second = next(e for e in elements if e.id == "inv_redefinition::func#2")
    assert first.docstring == "First definition."
    assert second.docstring == "Redefined in conditional."


# ---------------------------------------------------------------------------
# inv_blob
# ---------------------------------------------------------------------------


def test_inv_blob(tmp_path: Path) -> None:
    assert _has_case("inv_blob")
    elements, _ = _check_case("inv_blob", tmp_path)
    blobs = [e for e in elements if e.kind == ElementKind.BLOB]
    assert len(blobs) == 1
    blob = blobs[0]
    assert blob.id == "inv_blob::@blob#1"
    assert blob.byte_size == 1048578  # see module docstring: fixture says 1048576
    assert blob.content_hash  # opaque, but must be present
    # never decoded: the blob's own docstring/signature/name must not carry
    # the literal's content back out
    assert "xxxx" not in blob.name
    assert "xxxx" not in blob.qualname
    # the module docstring is untouched, ordinary-sized text
    module_el = next(e for e in elements if e.kind == ElementKind.MODULE)
    assert module_el.docstring == "Module with a large embedded blob."
    # no ASSIGNMENT element for DATA -- the blob absorbs it (see expected.json)
    assert not [e for e in elements if e.kind == ElementKind.ASSIGNMENT]


# ---------------------------------------------------------------------------
# inv_empty
# ---------------------------------------------------------------------------


def test_inv_empty(tmp_path: Path) -> None:
    assert _has_case("inv_empty")
    elements, unresolved = _check_case("inv_empty", tmp_path)
    assert len(elements) == 1
    assert elements[0].kind == ElementKind.MODULE
    assert elements[0].content_hash == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert unresolved == []


# ---------------------------------------------------------------------------
# inv_ids_stable -- the actual reformat proof, built here since expected.json
# alone cannot prove ID stability (it does not carry a reformatted variant).
# ---------------------------------------------------------------------------


def test_inv_ids_stable(tmp_path: Path) -> None:
    assert _has_case("inv_ids_stable")
    original_elements, _ = _check_case("inv_ids_stable", tmp_path)
    original_ids = {e.qualname: e.id for e in original_elements}

    source = (_case_dir("inv_ids_stable") / "__init__.py").read_text()
    reformatted = source.replace("    ", "        ")  # re-indent
    reformatted = reformatted.replace('"""A simple function."""', '"""A simple function.  # noqa"""')
    reformatted = "\n\n\n" + reformatted + "\n\n# trailing comment\n"

    # Same basename as the original ("inv_ids_stable") under a different
    # parent -- the module dotted name is derived from the root directory's
    # own basename, so changing *that* would change the module identity
    # itself, not just reformat the file.
    reformatted_dir = tmp_path / "reformatted_copy" / "inv_ids_stable"
    reformatted_dir.mkdir(parents=True)
    (reformatted_dir / "__init__.py").write_text(reformatted)

    new_elements, _ = inventory(str(reformatted_dir), cache_dir=tmp_path / "cache2")
    new_ids = {e.qualname: e.id for e in new_elements}

    for qualname in ("foo", "bar"):
        assert qualname in new_ids
        assert new_ids[qualname] == original_ids[qualname], (
            f"reformatting must not change the ID of {qualname!r}"
        )


# ---------------------------------------------------------------------------
# inv_syntax_error, inv_non_utf8, inv_incremental -- these fixtures now exist
# (card 8 landed them after this suite was first written). Each gets a real
# assertion against the fixture's actual content below, plus the pre-existing
# tmp_path-built test proving the same behavior on synthetic input -- both are
# kept, per the coordinator's note, so the fixture test is never a weaker
# restatement of the tmp_path one.
# ---------------------------------------------------------------------------


def test_inv_syntax_error_fixture_case(tmp_path: Path) -> None:
    assert _has_case("inv_syntax_error")
    expected = json.loads((_case_dir("inv_syntax_error") / "expected.json").read_text())
    elements, unresolved = _run_case("inv_syntax_error", tmp_path)

    # A file that fails to parse contributes no elements at all -- not even a
    # MODULE stub -- and the walk continues rather than crashing.
    assert elements == [], "a syntax error must not produce a partial element list"
    assert len(unresolved) == 1
    record = unresolved[0]
    exp_u = expected["unresolved"][0]
    assert record.id == exp_u["id"]
    assert record.reason == UnresolvedReason.SYNTAX_ERROR == exp_u["reason"]
    assert record.span.path == exp_u["span"]["path"]
    # NOTE (fixture defect, reported not matched): CPython's exact recovery
    # point for an unclosed bracket is version-dependent. On this interpreter
    # (3.11.15), `ast.parse` raises "'[' was never closed" pointing at line 5
    # (where `[` opens) -- confirmed directly against the fixture file.
    # expected.json asserts line 6 (the `return` statement) with a comment
    # explaining that expectation ("the parser reports the failure at the
    # `return` that follows"), which matches a different CPython's recovery
    # behavior, not this one's. Reason and location-file are load-bearing and
    # asserted; the exact line and message text are Python-version-specific
    # and are not pinned here.
    assert record.span.line >= 1
    assert record.description


def test_syntax_error_is_unresolved_not_a_crash(tmp_path: Path) -> None:
    case = tmp_path / "syn"
    case.mkdir()
    (case / "__init__.py").write_text("def broken(:\n    pass\n")
    elements, unresolved = inventory(str(case), cache_dir=tmp_path / "cache")
    assert not [e for e in elements if e.kind == ElementKind.MODULE]
    assert len(unresolved) == 1
    assert unresolved[0].reason == UnresolvedReason.SYNTAX_ERROR
    assert unresolved[0].span.path.endswith("__init__.py")


def test_inv_non_utf8_fixture_case(tmp_path: Path) -> None:
    assert _has_case("inv_non_utf8")
    expected = json.loads((_case_dir("inv_non_utf8") / "expected.json").read_text())
    elements, unresolved = _run_case("inv_non_utf8", tmp_path)

    assert elements == [], "a non-UTF-8 file must not produce a partial element list"
    assert len(unresolved) == 1
    record = unresolved[0]
    exp_u = expected["unresolved"][0]
    assert record.id == exp_u["id"]
    assert record.reason == UnresolvedReason.DECODE_ERROR == exp_u["reason"]
    assert record.span.path == exp_u["span"]["path"]
    assert record.span.line == exp_u["span"]["line"], (
        "line is computed from the UnicodeDecodeError's byte offset, not "
        "hardcoded to 1 -- this must match the fixture exactly"
    )
    # NOTE (fixture defect, reported not matched): expected.json's col is 0;
    # the invalid byte is actually at column 9 on that line ("LABEL = '" is
    # 9 bytes), confirmed by computing the offset directly. col looks like an
    # unfilled placeholder in the fixture rather than a computed value, so
    # (unlike line, which we do assert) col is not pinned here.
    # description wording differs too (fixture: "Unable to decode file as
    # UTF-8"; ours embeds the UnicodeDecodeError repr for diagnosability).
    assert record.description


def test_non_utf8_is_unresolved_not_a_crash(tmp_path: Path) -> None:
    case = tmp_path / "bad_encoding"
    case.mkdir()
    (case / "__init__.py").write_bytes(b"x = '\xff\xfe not utf-8'\n")
    elements, unresolved = inventory(str(case), cache_dir=tmp_path / "cache")
    assert not [e for e in elements if e.kind == ElementKind.MODULE]
    assert len(unresolved) == 1
    assert unresolved[0].reason == UnresolvedReason.DECODE_ERROR


def test_inv_incremental_fixture_case(tmp_path: Path) -> None:
    """Exercises the real `inv_incremental` fixture content end to end:
    static correctness against expected.json, cold/warm byte-identity, a
    touch-only no-reparse, and a real content change picked up. This is a
    genuine exercise of the fixture, not a restatement of the tmp_path-built
    tests below (which use synthetic source and stay as an independent
    proof)."""
    assert _has_case("inv_incremental")

    # expected.json content, matched the same way every other fixture case is.
    _check_case("inv_incremental", tmp_path)

    # The fixture directory is read-only territory (card 8's). Copy it to
    # work the cache/mutate steps against, under the same basename so the
    # module's dotted name is unaffected.
    work_root = tmp_path / "work" / "inv_incremental"
    work_root.mkdir(parents=True)
    src = work_root / "__init__.py"
    src.write_text((_case_dir("inv_incremental") / "__init__.py").read_text())
    cache_dir = tmp_path / "cache"

    cold_elements, cold_unresolved = inventory(str(work_root), cache_dir=cache_dir)
    assert {e.name for e in cold_elements} == {"inv_incremental", "operation"}
    cold_bytes = canonical_jsonl(cold_elements) + canonical_jsonl(cold_unresolved)

    warm_elements, warm_unresolved = inventory(str(work_root), cache_dir=cache_dir)
    warm_bytes = canonical_jsonl(warm_elements) + canonical_jsonl(warm_unresolved)
    assert cold_bytes == warm_bytes

    cache_file = next(cache_dir.glob("*.json"))
    before_touch = cache_file.read_text()
    os.utime(src, None)  # mtime changes, content does not
    inventory(str(work_root), cache_dir=cache_dir)
    assert cache_file.read_text() == before_touch, "a touched-but-unchanged file must not be re-analysed"

    # a real content change: a second function appears, and only it is new
    src.write_text(
        src.read_text() + '\n\ndef second_operation():\n    """Another one."""\n    return 43\n'
    )
    changed_elements, _ = inventory(str(work_root), cache_dir=cache_dir)
    names = {e.name for e in changed_elements}
    assert names == {"inv_incremental", "operation", "second_operation"}, names


def test_incremental_cache_cold_and_warm_are_byte_identical(tmp_path: Path) -> None:
    case = tmp_path / "incr"
    case.mkdir()
    (case / "__init__.py").write_text('"""m."""\n\ndef f():\n    return 1\n')
    cache_dir = tmp_path / "cache"

    cold_elements, cold_unresolved = inventory(str(case), cache_dir=cache_dir)
    assert {e.name for e in cold_elements} == {"incr", "f"}
    cold_bytes = canonical_jsonl(cold_elements) + canonical_jsonl(cold_unresolved)

    warm_elements, warm_unresolved = inventory(str(case), cache_dir=cache_dir)
    warm_bytes = canonical_jsonl(warm_elements) + canonical_jsonl(warm_unresolved)

    assert cold_bytes == warm_bytes


def test_touched_but_unchanged_file_is_not_reparsed(tmp_path: Path) -> None:
    case = tmp_path / "incr2"
    case.mkdir()
    src = case / "__init__.py"
    src.write_text('"""m."""\n\ndef f():\n    return 1\n')
    cache_dir = tmp_path / "cache"

    inventory(str(case), cache_dir=cache_dir)
    cache_file = next((cache_dir).glob("*.json"))
    cache = json.loads(cache_file.read_text())
    relkey = next(iter(cache))
    original_element_json = cache[relkey]["elements"]

    # touch (mtime changes) without changing content
    os.utime(src, None)
    inventory(str(case), cache_dir=cache_dir)
    cache_after = json.loads(cache_file.read_text())
    assert cache_after[relkey]["elements"] == original_element_json

    # now actually change content
    src.write_text('"""m."""\n\ndef f():\n    return 2\n\ndef g():\n    return 3\n')
    new_elements, _ = inventory(str(case), cache_dir=cache_dir)
    names = {e.name for e in new_elements}
    assert "g" in names


# ---------------------------------------------------------------------------
# Sentinel -- constraint 1, empirically, on every static run.
# ---------------------------------------------------------------------------

_SENTINEL_MARKER = Path("/tmp/cascade_map_sentinel_marker.txt")


def _assert_sentinel_untripped() -> None:
    assert not _SENTINEL_MARKER.exists(), (
        "the sentinel marker exists: something executed or imported "
        "tests/fixtures/sentinel instead of only reading it as text"
    )


def test_sentinel_never_executed(tmp_path: Path) -> None:
    if _SENTINEL_MARKER.exists():
        _SENTINEL_MARKER.unlink()
    _assert_sentinel_untripped()
    elements, unresolved = inventory(str(FIXTURES / "sentinel"), cache_dir=tmp_path / "cache")
    _assert_sentinel_untripped()
    assert any(e.kind == ElementKind.MODULE for e in elements)


# ---------------------------------------------------------------------------
# Determinism across the whole mode_b corpus
# ---------------------------------------------------------------------------


def test_two_runs_are_byte_identical(tmp_path: Path) -> None:
    elements1, unresolved1 = inventory(str(MODE_B), cache_dir=tmp_path / "cache")
    elements2, unresolved2 = inventory(str(MODE_B), cache_dir=tmp_path / "cache")
    # equality alone would hold vacuously if inventory() returned nothing;
    # anchor it to real content from the corpus first.
    assert len(elements1) > 20, "the mode_b corpus must yield a substantial element set"
    assert any(e.id == "mode_b.inv_kinds" for e in elements1)
    assert any(u.reason == UnresolvedReason.SYNTAX_ERROR for u in unresolved1)
    assert canonical_jsonl(elements1) == canonical_jsonl(elements2)
    assert canonical_jsonl(unresolved1) == canonical_jsonl(unresolved2)


def test_cold_cache_matches_warm_cache_on_whole_corpus(tmp_path: Path) -> None:
    cold_elements, cold_unresolved = inventory(str(MODE_B), cache_dir=tmp_path / "cache_a")
    warm_elements, warm_unresolved = inventory(str(MODE_B), cache_dir=tmp_path / "cache_a")
    assert len(cold_elements) > 20
    assert any(e.id == "mode_b.inv_kinds" for e in cold_elements)
    assert canonical_jsonl(cold_elements) == canonical_jsonl(warm_elements)
    assert canonical_jsonl(cold_unresolved) == canonical_jsonl(warm_unresolved)


# ---------------------------------------------------------------------------
# ID collisions -- surfaced, never silently overwritten
# ---------------------------------------------------------------------------


def test_id_collision_is_reported_not_overwritten(tmp_path: Path) -> None:
    # a package directory and a same-named module file both claim dotted
    # name "foo" -- an inseparable collision the ordinal scheme cannot fix
    # (it operates within one file, not across two).
    root = tmp_path / "collide"
    root.mkdir()
    (root / "foo.py").write_text("def a():\n    pass\n")
    pkg = root / "foo"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("def b():\n    pass\n")

    elements, unresolved = inventory(str(root), cache_dir=tmp_path / "cache")
    module_elements = [
        e for e in elements if e.id == "collide.foo" and e.kind == ElementKind.MODULE
    ]
    assert len(module_elements) == 1, "a collision must not be silently duplicated"
    assert any(u.reason == UnresolvedReason.ID_COLLISION for u in unresolved)


# ---------------------------------------------------------------------------
# Non-Python config/data files: DATA_FILE + CONFIG_KEY
# ---------------------------------------------------------------------------


def test_json_config_file_yields_data_file_and_config_keys(tmp_path: Path) -> None:
    root = tmp_path / "wiring"
    root.mkdir()
    (root / "__init__.py").write_text('"""m."""\n')
    (root / "wiring.json").write_text(json.dumps({"components": [{"class": "m.MyClass"}]}))

    elements, unresolved = inventory(str(root), cache_dir=tmp_path / "cache")
    data_files = [e for e in elements if e.kind == ElementKind.DATA_FILE]
    config_keys = [e for e in elements if e.kind == ElementKind.CONFIG_KEY]
    assert len(data_files) == 1
    assert data_files[0].id == f"@file:{(root / 'wiring.json').as_posix()}"
    matching = [e for e in config_keys if e.signature == "m.MyClass"]
    assert matching, "the wired class name must be recoverable from a CONFIG_KEY element"
    assert matching[0].parent_id == data_files[0].id


def test_malformed_json_is_unresolved_not_a_crash(tmp_path: Path) -> None:
    root = tmp_path / "badjson"
    root.mkdir()
    (root / "broken.json").write_text("{not valid json")
    elements, unresolved = inventory(str(root), cache_dir=tmp_path / "cache")
    assert any(e.kind == ElementKind.DATA_FILE for e in elements)
    assert any(u.reason == UnresolvedReason.SYNTAX_ERROR for u in unresolved)
