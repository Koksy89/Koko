"""Cross-interpreter portability of the ingestion hashes.

`Element.normalized_body_hash` is the "is this the same logic?" test that
cards 6 and 18 use to tell a genuine behaviour change from a reformat. It was
built from `tok.type` -- a CPython-internal token *number* that was renumbered
between 3.11 and 3.12 -- so merely changing interpreter changed every hash at
once and the version ledger reported the whole engine as rewritten.

A single-interpreter test asserting a hard-coded digest is exactly how that
defect survived: it passes on whichever version happened to run it and proves
nothing. So these tests shell out to python3.11, python3.12 and python3.13,
skip cleanly for each interpreter that is absent, and fail if any two present
interpreters disagree.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cascade_map.ingest.python_module import _norm_tokens

_INTERPRETERS = ("python3.11", "python3.12", "python3.13")

# Every construct whose tokenization differs between 3.11 and 3.12+ belongs
# here. f-strings are the load-bearing case: 3.11 emits one STRING token,
# 3.12+ emits FSTRING_START / FSTRING_MIDDLE / FSTRING_END around the
# replacement fields. Quote styles are deliberately mixed (never the same
# quote reused inside a replacement field, which 3.11 cannot parse at all).
_CORPUS = '''\
"""Module docstring, stripped from the normalized hash."""

import dataclasses
from typing import Any

ALL_CAPS = [1, 2, 3]


def formats(width: int, mapping: dict[str, Any]) -> str:
    """Docstring, stripped."""
    simple = f"value={width}"
    spec = f"{width:>{width}.3f}"
    conv = f"{mapping!r:^10s}"
    nested_quotes = f"{mapping['key']} and {mapping[\'other\']}"
    nested_fstring = f"{f'{width}-inner'}-outer"
    multiline = f"""
    line one {width}
    line two {mapping['key']:>8}
    """
    raw = rf"\\d+{width}"
    concat = f"{width}" "plain" f'{width!s}'
    byte = b"\\x00\\x01"
    return simple + spec + conv + nested_quotes + nested_fstring + multiline + raw + concat


class Cascade:
    """Docstring, stripped."""

    threshold: float = 0.5

    def __init__(self, name: str) -> None:
        self.name = name  # a comment, stripped
        self._cache: dict[str, int] = {}

    @property
    def label(self) -> str:
        return f"{self.name}:{self.threshold:.2f}"

    async def run(self, *args: int, **kwargs: str) -> int:
        match args:
            case (0, *rest):
                return len(rest)
            case _:
                pass
        total = sum(x * 2 for x in args if x)
        try:
            total //= len(kwargs)
        except ZeroDivisionError:
            total = -1
        with open("nope") as handle:  # never executed; parsed only
            del handle
        return total


def outer(a):
    def inner(b=lambda: 0):
        yield from range(a)
    return inner


UNICODE = "n\\u00e3o-ascii \\u2014 caf\\u00e9"
'''

_DRIVER = '''\
import json, sys
sys.path.insert(0, sys.argv[1])
from cascade_map.ingest.inventory import inventory

elements, unresolved = inventory(sys.argv[2], cache_dir=sys.argv[3], workers=0)
rows = sorted(
    [e.id, e.kind.value, e.content_hash, e.normalized_body_hash] for e in elements
)
sys.stdout.write(json.dumps({"rows": rows, "unresolved": len(unresolved)}, sort_keys=True))
'''


def _present() -> list[str]:
    return [name for name in _INTERPRETERS if shutil.which(name) is not None]


def _run(interpreter: str, driver: Path, src_root: str, corpus: Path, cache: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [interpreter, str(driver), src_root, str(corpus), str(cache)],
        env=env,
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, f"{interpreter}: {result.stderr.decode('utf-8', 'replace')}"
    return json.loads(result.stdout.decode("utf-8"))


def _write_corpus(tmp_path: Path) -> Path:
    pkg = tmp_path / "corpus" / "engine"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("from .mod import Cascade\n", encoding="utf-8")
    (pkg / "mod.py").write_text(_CORPUS, encoding="utf-8")
    return tmp_path / "corpus"


def test_normalized_body_hash_is_identical_across_interpreters(tmp_path: Path) -> None:
    """The defect test. Each present interpreter gets its own cache directory,
    so a warm cache cannot carry another version's hash across and fake a pass."""
    present = _present()
    if len(present) < 2:
        pytest.skip(f"need two of {_INTERPRETERS}; found {present}")

    driver = tmp_path / "driver.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    corpus = _write_corpus(tmp_path)
    src_root = str(Path(__file__).resolve().parents[1] / "src")

    results = {
        name: _run(name, driver, src_root, corpus, tmp_path / f"cache-{name}")
        for name in present
    }

    first = present[0]
    baseline = results[first]
    # The corpus must actually produce body hashes, or agreement is vacuous.
    body_hashes = [row[3] for row in baseline["rows"] if row[3]]
    assert len(body_hashes) >= 8, baseline["rows"]
    assert any("fstring" in row[0] or "formats" in row[0] for row in baseline["rows"])

    for name in present[1:]:
        other = results[name]
        assert [r[0] for r in other["rows"]] == [r[0] for r in baseline["rows"]], (
            f"{first} and {name} disagree on element ids"
        )
        mismatched = [
            (b[0], b[3], o[3])
            for b, o in zip(baseline["rows"], other["rows"])
            if b[3] != o[3]
        ]
        assert not mismatched, f"normalized_body_hash differs {first} vs {name}: {mismatched}"


def test_content_hash_is_identical_across_interpreters(tmp_path: Path) -> None:
    """`content_hash` hashes source text, so it should already be portable --
    checked rather than assumed, since it shares `_source_segment` with the
    body hash and that helper reimplements a stdlib function that differs
    between 3.11 and 3.12."""
    present = _present()
    if len(present) < 2:
        pytest.skip(f"need two of {_INTERPRETERS}; found {present}")

    driver = tmp_path / "driver.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    corpus = _write_corpus(tmp_path)
    src_root = str(Path(__file__).resolve().parents[1] / "src")

    results = {
        name: _run(name, driver, src_root, corpus, tmp_path / f"cache-{name}")
        for name in present
    }
    first = present[0]
    baseline = results[first]
    assert len({row[2] for row in baseline["rows"]}) >= 8
    for name in present[1:]:
        mismatched = [
            (b[0], b[2], o[2])
            for b, o in zip(baseline["rows"], results[name]["rows"])
            if b[2] != o[2]
        ]
        assert not mismatched, f"content_hash differs {first} vs {name}: {mismatched}"


def test_normalized_token_stream_carries_no_token_numbers() -> None:
    """Guards the regression directly: the hashed representation must contain
    token *names*, never the interpreter's token numbers."""
    stream = _norm_tokens("a = f'{b:>{w}}' + 1\n")
    assert "NAME\x00a" in stream
    assert "NUMBER\x001" in stream
    assert "STRING\x00f'{b:>{w}}'" in stream
    # FSTRING_* must have been collapsed away, on every interpreter.
    assert "FSTRING" not in stream
    for part in stream.split("\x01"):
        kind = part.split("\x00")[0]
        assert not kind.isdigit(), f"token number leaked into the hash: {part!r}"


def test_fstring_collapses_to_the_same_record_as_a_plain_string() -> None:
    """A 3.12+ FSTRING_START/MIDDLE/END run must reduce to exactly the single
    ("STRING", source text) record 3.11 emits unaided -- so an f-string and
    its own source text tokenize to the same record on every version."""
    for literal in (
        "f'plain'",
        'f"{x!r:>{w}.2f}"',
        "f'{d[\"k\"]}-{e}'",
        "f'{f\"{inner}\"}'",
        'rf"\\\\d{n}"',
    ):
        stream = _norm_tokens(f"v = {literal}\n")
        assert stream.endswith("STRING\x00" + literal), (literal, stream)
        assert "FSTRING" not in stream


def test_comments_and_docstrings_still_normalize_away() -> None:
    """The fix must not weaken the hash's actual job."""
    a = _norm_tokens("x = 1  # one\nreturn x\n")
    b = _norm_tokens("x    =    1\n\n\nreturn x\n")
    assert a == b
    assert _norm_tokens("x = 1\n") != _norm_tokens("x = 2\n")
