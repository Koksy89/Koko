#!/usr/bin/env python3
"""Independent ground-truth check of every span-bearing record in the corpus.

Deliberately *not* a re-run of ``_regenerate_spans.py``. This walks the
expectation files, finds every object carrying a ``span`` or ``call_site``,
and resolves it against the fixture source a second time, by a second route:

* Element records for defs/classes are checked against a freshly built
  ``qualname -> node`` map, **ignoring the anchor entirely**. A wrong anchor
  therefore cannot hide a wrong line.
* Every other span is checked against its anchor, re-resolved by code written
  separately from the generator's.
* Every span-bearing record must be anchored. An unanchored span is a
  hand-transcribed line number, which is the failure mode this whole split
  exists to eliminate, so it is reported as a mismatch.

It also audits what the generator has no opinion about: paths exist, ``id``
fields obey ``make_id``, enum values are real members of the contract enums,
every list is sorted, and every file survives ``canonical_dumps``.

Usage::

    python tests/fixtures/_check_spans.py

Prints ``<records checked> records checked, <n> mismatches`` and exits
non-zero on any mismatch.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any, Iterator

FIXTURES_ROOT = Path(__file__).resolve().parent
REPO_ROOT = FIXTURES_ROOT.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from cascade_map.contracts.interfaces import (  # noqa: E402
    CaptureStatus,
    ChangeKind,
    Confidence,
    EdgeKind,
    ElementKind,
    EventKind,
    FindingKind,
    IntentStatus,
    LineageKind,
    Method,
    OrderKind,
    ReachabilityState,
    UnresolvedReason,
    Verdict,
    canonical_dumps,
    config_key_id,
    feature_id,
    file_id,
    make_id,
)

DEF_KINDS = {"FUNCTION", "METHOD", "PROPERTY", "CLASS"}

ENUM_FIELDS: dict[str, Any] = {
    "kind": None,  # resolved per collection below
    "method": Method,
    "confidence": Confidence,
    "state": ReachabilityState,
    # `status` is an IntentStatus on Intent and a CaptureStatus on ValueCapture;
    # a value that is a member of either is valid.
    "status": (IntentStatus, CaptureStatus),
    "verdict": Verdict,
    "candidate_confidence": Confidence,
}

COLLECTION_KIND_ENUM: dict[str, Any] = {
    "elements": ElementKind,
    "edges": EdgeKind,
    "findings": FindingKind,
    "lineage": LineageKind,
    "order": OrderKind,
    "changes": ChangeKind,
    "events": EventKind,
}


# ---------------------------------------------------------------------------
# a second, independent source reader
# ---------------------------------------------------------------------------


def read_source(rel_path: str) -> tuple[list[str], str | None]:
    raw = (REPO_ROOT / rel_path).read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", "replace").splitlines(), None
    return text.splitlines(), text


def def_index(text: str) -> dict[str, list[tuple[int, int, int]]]:
    """qualname -> [(lineno, end_lineno, col_offset), ...] in source order."""
    index: dict[str, list[tuple[int, int, int]]] = {}
    stack: list[tuple[ast.AST, str]] = [(ast.parse(text), "")]
    while stack:
        node, prefix = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualname = prefix + child.name
                index.setdefault(qualname, []).append(
                    (child.lineno, child.end_lineno or child.lineno, child.col_offset)
                )
                sep = "." if isinstance(child, ast.ClassDef) else ".<locals>."
                stack.append((child, qualname + sep))
            else:
                stack.append((child, prefix))
    for spans in index.values():
        spans.sort()
    return index


def nth_line(lines: list[str], needle: str, occurrence: int) -> int:
    hits = [i for i, line in enumerate(lines, start=1) if needle in line]
    if len(hits) < occurrence:
        raise LookupError(f"{needle!r} occurs {len(hits)} times, wanted #{occurrence}")
    return hits[occurrence - 1]


def expected_fields(anchor: dict[str, Any], rel_path: str) -> dict[str, int]:
    lines, text = read_source(rel_path)
    kind = anchor["kind"]
    if kind == "module":
        return {"line": 1}
    if kind == "line":
        line = nth_line(lines, anchor["line_contains"], anchor.get("occurrence", 1))
        return {"line": line, "col": lines[line - 1].index(anchor["line_contains"])}
    assert text is not None, f"{rel_path} is not UTF-8; only 'line'/'module' anchors apply"
    if kind == "def":
        line, end_line, col = def_index(text)[anchor["qualname"]][
            anchor.get("occurrence", 1) - 1
        ]
        return {"line": line, "end_line": end_line, "col": col}
    target_line = nth_line(lines, anchor["line_contains"], anchor.get("occurrence", 1))
    if kind == "stmt":
        want = ast.stmt
    elif kind == "expr":
        want = getattr(ast, anchor.get("node", "Call"))
    else:
        raise ValueError(f"unknown anchor kind {kind!r}")
    nodes = [
        n
        for n in ast.walk(ast.parse(text))
        if isinstance(n, want) and getattr(n, "lineno", None) == target_line
    ]
    if not nodes:
        raise LookupError(f"{rel_path}: no {kind} on line {target_line}")
    node = sorted(nodes, key=lambda n: n.col_offset)[0]
    return {"line": node.lineno, "end_line": node.end_lineno, "col": node.col_offset}


# ---------------------------------------------------------------------------
# walking the expectation files
# ---------------------------------------------------------------------------


def span_bearing(document: Any, pointer: str = "") -> Iterator[tuple[str, dict, dict]]:
    """Yield (pointer-to-span, span, owning record) for every span in a file."""
    if isinstance(document, dict):
        for key in ("span", "call_site"):
            value = document.get(key)
            if isinstance(value, dict) and "path" in value:
                yield f"{pointer}/{key}", value, document
        for key, value in document.items():
            yield from span_bearing(value, f"{pointer}/{key}")
    elif isinstance(document, list):
        for index, value in enumerate(document):
            yield from span_bearing(value, f"{pointer}/{index}")


def check_case(case_dir: Path) -> tuple[int, list[str]]:
    expected_file = case_dir / "expected.json"
    expected = json.loads(expected_file.read_text())
    spec = json.loads((case_dir / "spans.json").read_text())
    anchors = spec.get("spans", {})
    problems: list[str] = []
    checked = 0
    rel = case_dir.relative_to(REPO_ROOT)

    for pointer, span, record in span_bearing(expected):
        checked += 1
        rel_path = span["path"]
        if not (REPO_ROOT / rel_path).exists():
            problems.append(f"{rel}{pointer}: path {rel_path!r} does not exist")
            continue

        # Route 1 -- element defs, resolved from qualname, anchor ignored.
        top = pointer.split("/")[1] if pointer.count("/") > 1 else ""
        if (
            top.endswith("elements")
            and pointer.endswith("/span")
            and record.get("kind") in DEF_KINDS
            and record.get("qualname")
        ):
            _lines, text = read_source(rel_path)
            assert text is not None
            index = def_index(text)
            qualname = record["qualname"]
            # `m::func#2` is the second `def func` in source order.
            ordinal = int(record["id"].rsplit("#", 1)[1]) if "#" in record["id"] else 1
            if len(index.get(qualname, [])) < ordinal:
                problems.append(
                    f"{rel}{pointer}: no def/class {qualname!r} #{ordinal} in {rel_path}"
                )
                continue
            line, end_line, col = index[qualname][ordinal - 1]
            truth = {"line": line, "end_line": end_line, "col": col}
        # Route 2 -- everything else, via its anchor.
        elif pointer in anchors:
            try:
                truth = expected_fields(anchors[pointer], rel_path)
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{rel}{pointer}: anchor did not resolve: {exc}")
                continue
        else:
            problems.append(f"{rel}{pointer}: span is not anchored in spans.json")
            continue

        for field, value in truth.items():
            if span.get(field) != value:
                problems.append(
                    f"{rel}{pointer}/{field}: expected.json says {span.get(field)!r}, "
                    f"source says {value!r}"
                )
        for field in ("line", "end_line", "col"):
            if field in span and field not in truth:
                problems.append(
                    f"{rel}{pointer}/{field}: present as {span[field]!r} but the "
                    "source defines no such value"
                )

    return checked, problems


def _is_member(enum: Any, value: str) -> bool:
    try:
        enum(value)
    except ValueError:
        return False
    return True


def audit_case(case_dir: Path) -> list[str]:
    """Checks the generator has no opinion about: ids, enums, sorting, floats."""
    expected_file = case_dir / "expected.json"
    expected = json.loads(expected_file.read_text())
    rel = case_dir.relative_to(REPO_ROOT)
    problems: list[str] = []

    try:
        canonical_dumps(expected)
    except TypeError as exc:
        problems.append(f"{rel}: not canonically serializable: {exc}")

    element_records = list(expected.get("elements", []))
    element_records += list(expected.get("before_elements", []))
    element_records += list(expected.get("after_elements", []))
    for element in element_records:
        got = element["id"]
        # Non-Python nodes have their own id functions in the contract.
        if got.startswith("@feature:"):
            if got != feature_id(got[len("@feature:") :]):
                problems.append(f"{rel}: element id {got!r} violates feature_id")
            continue
        if got.startswith("@file:"):
            body = got[len("@file:") :]
            path, _, pointer = body.partition("::")
            want = config_key_id(path, pointer) if pointer else file_id(path)
            if got != want:
                problems.append(f"{rel}: element id {got!r} violates file_id/config_key_id")
            continue
        want = make_id(element["module"], element.get("qualname", ""))
        if got != want and got.split("#")[0] != want:
            problems.append(
                f"{rel}: element id {got!r} violates make_id"
                f"({element['module']!r}, {element.get('qualname', '')!r}) = {want!r}"
            )

    for collection, enum in COLLECTION_KIND_ENUM.items():
        for record in expected.get(collection, []):
            if not isinstance(record, dict) or "kind" not in record:
                continue
            try:
                enum(record["kind"])
            except ValueError:
                problems.append(
                    f"{rel}: {collection} record {record.get('id')!r} has "
                    f"kind {record['kind']!r}, not a member of {enum.__name__}"
                )

    def walk_enums(node: Any, where: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                enum = ENUM_FIELDS.get(key)
                if enum is not None and isinstance(value, str):
                    options = enum if isinstance(enum, tuple) else (enum,)
                    if not any(_is_member(option, value) for option in options):
                        names = "/".join(option.__name__ for option in options)
                        problems.append(f"{rel}{where}/{key}: {value!r} is not a {names}")
                walk_enums(value, f"{where}/{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk_enums(value, f"{where}/{index}")

    walk_enums(expected, "")

    # `reason` is an UnresolvedReason on Unresolved and Barrier, and free text
    # on Reachability -- so it is validated per collection, not generically.
    for collection in ("unresolved", "barriers"):
        for record in expected.get(collection, []):
            if not isinstance(record, dict) or "reason" not in record:
                continue
            try:
                UnresolvedReason(record["reason"])
            except ValueError:
                problems.append(
                    f"{rel}: {collection} record {record.get('id')!r} has reason "
                    f"{record['reason']!r}, not an UnresolvedReason"
                )

    for collection in ("edges", "findings", "lineage", "unresolved", "barriers"):
        ids = [r["id"] for r in expected.get(collection, []) if isinstance(r, dict) and "id" in r]
        if ids != sorted(ids):
            problems.append(f"{rel}: {collection} is not sorted by id")

    return problems


def main() -> int:
    total_checked = 0
    all_problems: list[str] = []
    cases = 0
    for spans_file in sorted(FIXTURES_ROOT.rglob("spans.json")):
        cases += 1
        checked, problems = check_case(spans_file.parent)
        total_checked += checked
        all_problems.extend(problems)
        all_problems.extend(audit_case(spans_file.parent))

    for problem in all_problems:
        print(problem)
    print(
        f"{cases} cases, {total_checked} span-bearing records checked, "
        f"{len(all_problems)} mismatches"
    )
    return 1 if all_problems else 0


if __name__ == "__main__":
    sys.exit(main())
