"""Build a single-file `cascade_map.py` from the package.

**DO NOT SHIP THE OUTPUT YET. It is not behaviour-preserving.**

Verified against the package on the fixture corpus: the amalgamated file emits
110 call edges where the package emits 108, and 11 barriers where it emits 12.
Both are internally stable across runs, so this is a deterministic behaviour
change, not flakiness.

The two extra edges are the exact defect `res_import_star` exists to catch:

    + CALLS   res_import_star -> res_import_star.names::delta   SCOPE_LOOKUP
    + IMPORTS res_import_star -> res_import_star.names::delta   IMPORT_STAR

and that fixture's `must_not_contain_edges` says why: "__all__ excludes delta;
linking on the bare name match would be a name heuristic dressed up as an
import resolution". So the amalgamation over-links on star imports — a
precision failure, in the card whose stated priority is precision.

The corpus caught it on the first comparison. That is the corpus doing exactly
what two rebuild rounds were spent making it able to do.

Root cause not yet found. The renaming machinery below is a suspect but not a
confirmed one: renaming every definer and following imports did not change the
numbers.

An amalgamation, not a rewrite. Every line here came from a module that passed
verification, in the order the imports require, with the eleven top-level names
that collide between modules renamed. Rewriting 24,500 lines by hand into one
file would throw away fourteen cards of verification and six integration
defects' worth of fixes; concatenating them keeps all of it.

Comments and docstrings survive, because the file is assembled from source text
rather than round-tripped through `ast.unparse`, which drops comments. In a file
this size the explanations are most of what makes it readable.

    python3 tools/amalgamate.py --out cascade_map.py
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "cascade_map"

#: Import order. Dependencies first; a module may only use what precedes it.
ORDER = [
    "contracts/interfaces.py",
    "ingest/constants.py", "ingest/hashing.py", "ingest/walker.py",
    "ingest/python_module.py", "ingest/data_files.py", "ingest/cache.py",
    "ingest/inventory.py",
    "resolve.py", "cascade.py", "lineage.py", "findings.py", "diff.py",
    "docrecords.py", "enrichment.py",
    "harness/errors.py", "harness/hashing.py", "harness/config.py",
    "harness/sandbox.py", "harness/harness.py",
    "tracer/limits.py", "tracer/capture.py", "tracer/static_index.py",
    "tracer/recording.py", "tracer/collector.py", "tracer/nondeterminism.py",
    "tracer/contradictions.py", "tracer/tracer.py",
    "alignment.py", "narrative.py",
    "viewer/loader.py", "viewer/views.py", "viewer/html_export.py",
    "cli.py",
]

#: Top-level names defined in more than one module. Each gets a module-derived
#: prefix in every module but the first that defines it, so one namespace can
#: hold them all. Found mechanically; see `_collisions`.
def _collisions(files: list[Path]) -> dict[str, list[Path]]:
    seen: dict[str, list[Path]] = {}
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            names = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names = [node.name]
            elif isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            for name in names:
                seen.setdefault(name, []).append(path)
    return {n: p for n, p in seen.items() if len(p) > 1}


def _module_prefix(path: Path) -> str:
    return "_" + path.stem.lstrip("_")


def _strip_internal_imports(source: str) -> tuple[str, set[str]]:
    """Remove `from cascade_map...` imports; collect stdlib ones to hoist."""
    hoisted: set[str] = set()
    out: list[str] = []
    lines = source.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("from cascade_map") or stripped.startswith("from ."):
            # May span lines via parentheses.
            if "(" in line and ")" not in line:
                while index < len(lines) and ")" not in lines[index]:
                    index += 1
            index += 1
            continue
        if stripped == "from __future__ import annotations":
            index += 1
            continue
        if (stripped.startswith("import ") or stripped.startswith("from ")) and not line[:1].isspace():
            if "(" in line and ")" not in line:
                block = [line]
                while index + 1 < len(lines) and ")" not in lines[index]:
                    index += 1
                    block.append(lines[index])
                hoisted.add("\n".join(block))
            else:
                hoisted.add(stripped)
            index += 1
            continue
        out.append(line)
        index += 1
    return "\n".join(out), hoisted


def build() -> str:
    files = [PACKAGE / rel for rel in ORDER]
    missing = [f for f in files if not f.exists()]
    if missing:
        raise SystemExit(f"missing modules: {[str(m) for m in missing]}")

    clashes = _collisions(files)

    # Every definer is renamed, not just the later ones. Renaming only the
    # later definers leaves the first holding the bare name, so a module that
    # imported the *later* one silently binds to the *earlier* one instead --
    # no error, wrong results. That produced 110 edges where the package
    # produces 108.
    canonical: dict[tuple[str, str], str] = {}
    for name, paths in clashes.items():
        for path in paths:
            canonical[(path.as_posix(), name)] = f"{_module_prefix(path)}_{name}"

    # A module's own definitions, plus whatever it imported and from where.
    renames: dict[Path, dict[str, str]] = {}
    for path in files:
        mapping: dict[str, str] = {}
        for name in clashes:
            key = (path.as_posix(), name)
            if key in canonical:
                mapping[name] = canonical[key]
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if not node.module.startswith("cascade_map"):
                continue
            source_rel = node.module.replace("cascade_map.", "").replace(".", "/") + ".py"
            for alias in node.names:
                key = ((PACKAGE / source_rel).as_posix(), alias.name)
                if key in canonical:
                    mapping[alias.asname or alias.name] = canonical[key]
        if mapping:
            renames[path] = mapping

    bodies: list[str] = []
    hoisted: set[str] = set()
    for path in files:
        source = path.read_text(encoding="utf-8")
        for old, new in renames.get(path, {}).items():
            source = re.sub(rf"(?<![\w.]){re.escape(old)}\b", new, source)
        body, imports = _strip_internal_imports(source)
        hoisted |= imports
        rel = path.relative_to(PACKAGE).as_posix()
        bodies.append(
            f"\n\n# {'=' * 74}\n# {rel}\n# {'=' * 74}\n\n{body.strip()}\n"
        )

    header = '''"""CASCADE-MAP — a static and runtime map of a Python decision engine.

Single file. Generated from the package by `tools/amalgamate.py`; every line
came from a module that passed independent verification. Regenerate rather than
editing this file by hand:

    python3 tools/amalgamate.py --out cascade_map.py

Usage:

    python3 cascade_map.py analyze <target> --out out/first
    python3 cascade_map.py view out/first
    python3 cascade_map.py trace out/first --scenarios s.json --scenario name

The static commands never execute the target. `trace` runs it inside the
harness; read the disclosed limits it prints before pointing it at anything
that matters.
"""

from __future__ import annotations

__version__ = "0.0.0"

'''
    imports = "\n".join(sorted(i for i in hoisted if i))
    footer = '\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'
    return header + imports + "\n" + "".join(bodies) + footer


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("cascade_map.py"))
    args = parser.parse_args()
    text = build()
    ast.parse(text)  # refuse to emit something that will not import
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {args.out}  ({len(text.splitlines()):,} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
