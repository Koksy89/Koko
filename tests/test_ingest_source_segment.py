"""`_source_segment` must be `ast.get_source_segment(..., padded=False)`.

Card 1 replaced the stdlib call with a hoisted-split equivalent because the
stdlib re-splits the whole file on *every* call, which is quadratic in file
length and dominated ingestion of a single very large module. The entire
safety argument for that swap is "the result is identical", so it is asserted
here against the stdlib itself rather than asserted in a comment.

The traps this is written to catch:
  * `col_offset` is a UTF-8 **byte** offset, so any line with non-ASCII
    characters produces a wrong segment under a naive character slice;
  * multi-line nodes join a first partial line, whole middle lines and a last
    partial line, and an off-by-one drops or duplicates a line;
  * `\\f`, `\\r` and `\\r\\n` split differently for the parser than for
    `str.splitlines`.
"""

from __future__ import annotations

import ast
import time

import pytest

from cascade_map.ingest.python_module import (
    _Ctx,
    _source_segment,
    _splitlines_no_ff,
)


def _ctx(source: str) -> _Ctx:
    return _Ctx(module="m", path="m.py", source=source)


# Each entry is a whole parseable module; every node in it is compared.
CORPUS: dict[str, str] = {
    "plain": "x = 1\ny = x + 2\n\n\ndef f(a, b=3):\n    return a + b\n",
    "nested_and_closures": (
        "class C:\n"
        "    attr = [1, 2, 3]\n"
        "\n"
        "    def m(self):\n"
        "        def inner():\n"
        "            return self.attr\n"
        "        return inner\n"
    ),
    "non_ascii_docstring": (
        '"""Módulo de estratégia — ação 🎯 cascata."""\n'
        "\n"
        "NAME = 'café'\n"
        "\n"
        "def naïve(paramètre='élan 🚀'):\n"
        '    """Faz a ação 🎯.\n'
        "\n"
        "    Segunda linha com acentuação: ãõçé.\n"
        '    """\n'
        "    return paramètre + 'ção'\n"
    ),
    "non_ascii_after_offset": (
        # The interesting case: a multi-byte character *before* the node on
        # the same line, so col_offset and the character index diverge.
        "d = {'é': 'ü', 'ß': naïve_value}\n"
        "t = ('αβγ', 'δε'), ('ζη',)\n"
    ),
    "multiline_expressions": (
        "value = (\n"
        "    1\n"
        "    + 2\n"
        "    + 3\n"
        ")\n"
        "call = sorted(\n"
        "    [3, 1, 2],\n"
        "    key=lambda item: (\n"
        "        item,\n"
        "        -item,\n"
        "    ),\n"
        ")\n"
    ),
    "decorators_and_async": (
        "import functools\n"
        "\n"
        "@functools.wraps\n"
        "@other(arg=1,\n"
        "       arg2=2)\n"
        "async def g():\n"
        "    async with thing() as t:\n"
        "        await t\n"
    ),
    "no_trailing_newline": "a = 1\nb = 2",
    "crlf": "a = 1\r\nb = (\r\n    2\r\n)\r\n",
    "cr_only": "a = 1\rb = 2\r",
    "form_feed": "a = 1\n\x0cb = 2\n\x0c\ndef h():\n    return 1\n",
    "empty": "",
    "only_comments": "# just a comment\n# and another\n",
    "fstring": (
        "name = 'wörld'\n"
        "msg = f\"hello {name!r:>10} and {1 + 2}\"\n"
        "multi = f'''\n"
        "{name}\n"
        "'''\n"
    ),
    "big_literal": "BLOB = '" + ("é" * 200) + "'\nAFTER = 1\n",
}


@pytest.mark.parametrize("label", sorted(CORPUS))
def test_source_segment_matches_stdlib_for_every_node(label: str) -> None:
    source = CORPUS[label]
    tree = ast.parse(source)
    ctx = _ctx(source)

    nodes = list(ast.walk(tree))
    assert nodes, "corpus entry produced no nodes"

    compared = 0
    for node in nodes:
        expected = ast.get_source_segment(source, node)
        actual = _source_segment(ctx, node)
        assert actual == expected, (
            f"{label}: {type(node).__name__} at "
            f"{getattr(node, 'lineno', '?')}:{getattr(node, 'col_offset', '?')}"
        )
        if expected is not None:
            compared += 1
    if label not in {"empty", "only_comments"}:
        assert compared > 0, f"{label}: nothing was actually compared"


def test_corpus_actually_contains_the_hard_cases() -> None:
    """Guards the guard: a corpus that quietly lost its non-ASCII and
    multi-line entries would let this file pass while proving nothing."""
    non_ascii = ast.parse(CORPUS["non_ascii_docstring"])
    ctx = _ctx(CORPUS["non_ascii_docstring"])
    segments = [_source_segment(ctx, n) for n in ast.walk(non_ascii)]
    assert any(s is not None and not s.isascii() for s in segments)
    assert any(s is not None and "🎯" in s for s in segments)

    multi = ast.parse(CORPUS["multiline_expressions"])
    mctx = _ctx(CORPUS["multiline_expressions"])
    assert any(
        getattr(n, "end_lineno", None) is not None
        and getattr(n, "lineno", None) is not None
        and n.end_lineno - n.lineno >= 3  # type: ignore[attr-defined]
        and _source_segment(mctx, n) is not None
        for n in ast.walk(multi)
    )


def test_returns_none_when_position_information_is_missing() -> None:
    ctx = _ctx("a = 1\n")
    bare = ast.Name(id="a", ctx=ast.Load())  # no position attributes at all
    assert _source_segment(ctx, bare) is None
    assert ast.get_source_segment(ctx.source, bare) is None

    partial = ast.Name(id="a", ctx=ast.Load())
    partial.lineno = 1
    partial.col_offset = 0
    partial.end_lineno = None  # type: ignore[assignment]
    partial.end_col_offset = None  # type: ignore[assignment]
    assert _source_segment(ctx, partial) is None
    assert ast.get_source_segment(ctx.source, partial) is None


@pytest.mark.parametrize("label", sorted(CORPUS))
def test_splitlines_matches_stdlib(label: str) -> None:
    source = CORPUS[label]
    expected = ast._splitlines_no_ff(source)  # type: ignore[attr-defined]
    actual = _splitlines_no_ff(source)
    # 3.12's regex-based stdlib version emits a trailing zero-width match;
    # 3.11's loop does not. Neither index is ever addressed, so compare on
    # the common prefix and require exact agreement there.
    trimmed = expected[:-1] if expected and expected[-1] == "" else expected
    assert actual == trimmed


def test_ctx_splits_the_source_only_once() -> None:
    ctx = _ctx(CORPUS["plain"])
    first = ctx.source_lines()
    second = ctx.source_lines()
    assert first is second


# ---------------------------------------------------------------------------
# scaling
# ---------------------------------------------------------------------------


def _synthetic(n_functions: int) -> str:
    parts = []
    for i in range(n_functions):
        parts.append(
            f"def fn_{i}(a, b):\n"
            f"    total = a + b + {i}\n"
            f"    if total > 0:\n"
            f"        total = total * 2\n"
            f"    return total\n"
        )
    return "\n".join(parts)


def _parse_seconds(source: str) -> float:
    from cascade_map.ingest.python_module import parse_python_file

    tree = ast.parse(source)
    raw = source.encode()
    best = float("inf")
    for _ in range(3):
        start = time.perf_counter()
        parse_python_file("syn", "syn.py", source, raw, tree, frozenset())
        best = min(best, time.perf_counter() - start)
    return best


def test_cost_is_not_quadratic_in_file_length() -> None:
    """Quadrupling the file must not roughly sixteen-fold the time.

    Compares ratios, not absolute times, so a slow machine slows both sides
    equally. The slack is deliberately wide: the assertion is only that the
    growth is nearer linear (4x) than quadratic (16x), which the pre-fix code
    fails by a wide margin (measured ~13x) and the fixed code passes with
    room (measured ~4x).
    """
    n = 400
    small = _parse_seconds(_synthetic(n))
    large = _parse_seconds(_synthetic(4 * n))

    if small < 5e-3:  # pragma: no cover - only on an implausibly fast machine
        pytest.skip("small case too fast to time reliably")

    ratio = large / small
    assert ratio < 9.0, (
        f"4x the input took {ratio:.1f}x the time "
        f"({small * 1e3:.1f}ms -> {large * 1e3:.1f}ms); "
        "expected near-linear (4x), quadratic would be ~16x"
    )
