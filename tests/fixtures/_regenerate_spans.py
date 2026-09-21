#!/usr/bin/env python3
"""Fill the *mechanical* fields of every ``expected.json`` in the corpus.

Why this exists
---------------
Card 8's expectations are split by how each value is knowable.

* **Semantic** expectations -- which findings fire, which edges exist and with
  what ``Method``/``Confidence``, what must come back ``UNKNOWN`` with a
  candidate set, what must produce a ``Barrier``, what must produce exactly
  zero results -- are hand-derived from reading the fixture and are **never**
  touched by this script.
* **Mechanical** values -- ``line``, ``end_line``, ``col`` and ``byte_size`` --
  are derived here from the fixture source with :mod:`ast` and plain text
  reads. There is no judgement in a line number, so there is nothing to
  corrupt; and :mod:`ast` is the same ground truth CASCADE-MAP itself reads,
  independent of any CASCADE-MAP implementation. Hand-transcribing hundreds of
  line numbers is the wrong method: it produces a few percent error no matter
  who does it, and a few percent is fatal when every other card is graded
  against these files.

How a record is anchored
------------------------
``expected.json`` itself stays free of generator bookkeeping (adding keys to it
would break the subset-comparison strategy its consumers use). Each case
directory therefore carries a sibling ``spans.json``::

    {
      "spans": {"/elements/0/span": {"kind": "module"}, ...},
      "byte_sizes": {"/elements/1": {"kind": "str_assign", ...}}
    }

Keys are JSON Pointers into ``expected.json``. The pointed-at span object
supplies its own ``path`` (which is *authored*, not generated: a path is part
of a record's identity). Anchor kinds:

``module``        line 1, no ``end_line``/``col`` -- a module starts at its
                  first byte, including when the file is empty.
``def``           the ``FunctionDef``/``AsyncFunctionDef``/``ClassDef`` with
                  the given Python-convention ``qualname`` (``<locals>`` and
                  all). ``line`` is the ``def``/``class`` keyword line, not a
                  decorator line -- ``node.lineno``.
``stmt``          the outermost statement starting on the Nth source line
                  containing ``line_contains``.
``expr``          the ``node`` (e.g. ``Call``) with the smallest column that
                  starts on the Nth source line containing ``line_contains``.
``line``          a plain text anchor: the Nth line containing
                  ``line_contains``. ``col`` is the byte column of the match.
                  Used for files :mod:`ast` cannot parse (syntax errors,
                  non-UTF-8, JSON config) and for whole-line facts.

Idempotence: running this twice changes nothing. It only ever writes ``line``,
``end_line``, ``col`` and ``byte_size``, and it removes those keys when an
anchor does not define them.

Usage::

    python tests/fixtures/_regenerate_spans.py            # rewrite in place
    python tests/fixtures/_regenerate_spans.py --check    # exit 1 if stale
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any, Iterator

FIXTURES_ROOT = Path(__file__).resolve().parent
REPO_ROOT = FIXTURES_ROOT.parent.parent

MECHANICAL_SPAN_FIELDS = ("line", "end_line", "col")


# ---------------------------------------------------------------------------
# source access
# ---------------------------------------------------------------------------


class Source:
    """One fixture file, read as text and (when possible) as an AST."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.raw = path.read_bytes()
        try:
            self.text = self.raw.decode("utf-8")
            self.decodable = True
        except UnicodeDecodeError:
            self.text = self.raw.decode("utf-8", "replace")
            self.decodable = False
        self.lines = self.text.splitlines()
        self._tree: ast.AST | None = None
        self._defs: dict[str, ast.AST] | None = None

    @property
    def tree(self) -> ast.AST:
        if self._tree is None:
            self._tree = ast.parse(self.text)
        return self._tree

    @property
    def defs(self) -> dict[str, ast.AST]:
        """Map Python-convention qualname -> def/class node."""
        if self._defs is None:
            found: dict[str, ast.AST] = {}
            _collect_defs(self.tree, "", found)
            self._defs = found
        return self._defs

    def nth_line_containing(self, needle: str, occurrence: int) -> int:
        seen = 0
        for index, line in enumerate(self.lines, start=1):
            if needle in line:
                seen += 1
                if seen == occurrence:
                    return index
        raise LookupError(
            f"{self.path}: no occurrence #{occurrence} of {needle!r} "
            f"(found {seen} in {len(self.lines)} lines)"
        )


def _collect_defs(node: ast.AST, prefix: str, out: dict[str, ast.AST]) -> None:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            qualname = f"{prefix}{child.name}"
            if qualname in out:
                raise LookupError(f"duplicate qualname {qualname!r}; anchor is ambiguous")
            out[qualname] = child
            inner = (
                f"{qualname}."
                if isinstance(child, ast.ClassDef)
                else f"{qualname}.<locals>."
            )
            _collect_defs(child, inner, out)
        else:
            _collect_defs(child, prefix, out)


# ---------------------------------------------------------------------------
# anchors
# ---------------------------------------------------------------------------


def resolve_span(anchor: dict[str, Any], source: Source) -> dict[str, int | None]:
    """Return the mechanical fields an anchor defines. Missing = not defined."""
    kind = anchor["kind"]

    if kind == "module":
        return {"line": 1}

    if kind == "def":
        qualname = anchor["qualname"]
        node = source.defs.get(qualname)
        if node is None:
            raise LookupError(
                f"{source.path}: no def/class {qualname!r}; "
                f"have {sorted(source.defs)}"
            )
        return {
            "line": node.lineno,  # type: ignore[attr-defined]
            "end_line": node.end_lineno,  # type: ignore[attr-defined]
            "col": node.col_offset,  # type: ignore[attr-defined]
        }

    if kind == "stmt":
        line = source.nth_line_containing(
            anchor["line_contains"], anchor.get("occurrence", 1)
        )
        candidates = [
            n
            for n in ast.walk(source.tree)
            if isinstance(n, ast.stmt) and n.lineno == line
        ]
        if not candidates:
            raise LookupError(f"{source.path}: no statement starting on line {line}")
        node = min(candidates, key=lambda n: n.col_offset)
        return {"line": node.lineno, "end_line": node.end_lineno, "col": node.col_offset}

    if kind == "expr":
        line = source.nth_line_containing(
            anchor["line_contains"], anchor.get("occurrence", 1)
        )
        node_type = getattr(ast, anchor.get("node", "Call"))
        candidates = [
            n
            for n in ast.walk(source.tree)
            if isinstance(n, node_type) and getattr(n, "lineno", None) == line
        ]
        if not candidates:
            raise LookupError(
                f"{source.path}: no {anchor.get('node', 'Call')} starting on line {line}"
            )
        node = min(candidates, key=lambda n: n.col_offset)
        return {"line": node.lineno, "end_line": node.end_lineno, "col": node.col_offset}

    if kind == "line":
        needle = anchor["line_contains"]
        line = source.nth_line_containing(needle, anchor.get("occurrence", 1))
        return {"line": line, "col": source.lines[line - 1].index(needle)}

    raise ValueError(f"unknown anchor kind {kind!r}")


def resolve_byte_size(anchor: dict[str, Any], source: Source) -> int:
    """Size in bytes of a literal, computed from the parsed value.

    Never printed, never decoded further, never executed -- only measured.
    """
    if anchor["kind"] != "str_assign":
        raise ValueError(f"unknown byte_size anchor kind {anchor['kind']!r}")
    target = anchor["target"]
    for node in ast.walk(source.tree):
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if target not in names:
            continue
        value = node.value
        if not isinstance(value, ast.Constant):
            raise LookupError(f"{source.path}: {target} is not a literal constant")
        if isinstance(value.value, bytes):
            return len(value.value)
        if isinstance(value.value, str):
            return len(value.value.encode("utf-8"))
        raise LookupError(f"{source.path}: {target} is not a str/bytes literal")
    raise LookupError(f"{source.path}: no module-level assignment to {target!r}")


# ---------------------------------------------------------------------------
# JSON Pointer
# ---------------------------------------------------------------------------


def pointer_get(document: Any, pointer: str) -> Any:
    node = document
    for token in pointer.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        node = node[int(token)] if isinstance(node, list) else node[token]
    return node


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------


def case_dirs() -> Iterator[Path]:
    for spans_file in sorted(FIXTURES_ROOT.rglob("spans.json")):
        yield spans_file.parent


def regenerate(case_dir: Path) -> list[str]:
    """Rewrite one case. Returns a line per field whose value changed."""
    expected_file = case_dir / "expected.json"
    spans_file = case_dir / "spans.json"
    expected = json.loads(expected_file.read_text())
    spec = json.loads(spans_file.read_text())
    changes: list[str] = []
    sources: dict[str, Source] = {}

    def source_for(rel_path: str) -> Source:
        if rel_path not in sources:
            sources[rel_path] = Source(REPO_ROOT / rel_path)
        return sources[rel_path]

    for pointer, anchor in sorted(spec.get("spans", {}).items()):
        span = pointer_get(expected, pointer)
        if "path" not in span:
            raise KeyError(f"{expected_file}: {pointer} has no authored 'path'")
        resolved = resolve_span(anchor, source_for(span["path"]))
        for field in MECHANICAL_SPAN_FIELDS:
            new = resolved.get(field)
            old = span.get(field, "<absent>")
            if new is None:
                if field in span:
                    del span[field]
                    changes.append(f"{pointer}/{field}: {old!r} -> <absent>")
                continue
            if old != new:
                span[field] = new
                changes.append(f"{pointer}/{field}: {old!r} -> {new!r}")
            else:
                span[field] = new

    for pointer, anchor in sorted(spec.get("byte_sizes", {}).items()):
        record = pointer_get(expected, pointer)
        rel_path = anchor["path"]
        new_size = resolve_byte_size(anchor, source_for(rel_path))
        old_size = record.get("byte_size", "<absent>")
        if old_size != new_size:
            record["byte_size"] = new_size
            changes.append(f"{pointer}/byte_size: {old_size!r} -> {new_size!r}")

    rendered = json.dumps(expected, indent=2, sort_keys=True) + "\n"
    if rendered != expected_file.read_text():
        expected_file.write_text(rendered)
        if not changes:
            changes.append(f"{expected_file.name}: reformatted (no value changed)")
    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 if any file would change",
    )
    args = parser.parse_args(argv)

    total_changes = 0
    total_cases = 0
    for case_dir in case_dirs():
        total_cases += 1
        if args.check:
            before = (case_dir / "expected.json").read_text()
            changes = regenerate(case_dir)
            after = (case_dir / "expected.json").read_text()
            if before != after:
                (case_dir / "expected.json").write_text(before)
        else:
            changes = regenerate(case_dir)
        rel = case_dir.relative_to(REPO_ROOT)
        for change in changes:
            print(f"{rel}: {change}")
        total_changes += len(changes)

    verb = "would change" if args.check else "changed"
    print(f"{total_cases} cases, {total_changes} fields {verb}")
    return 1 if (args.check and total_changes) else 0


if __name__ == "__main__":
    sys.exit(main())
