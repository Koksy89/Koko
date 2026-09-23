"""Build a single-file `cascade_map.py` from the package.

An amalgamation, not a rewrite. Every line of the output came from a module
that passed verification, in the order the imports require, with the handful of
top-level names that collide between modules renamed. Rewriting 24,000 lines by
hand into one file would throw away fourteen cards of verification and six
integration defects' worth of fixes; concatenating them keeps all of it.

Comments and docstrings survive, because the file is assembled from source text
rather than round-tripped through `ast.unparse`, which drops comments. In a file
this size the explanations are most of what makes it readable.

    python3.12 tools/amalgamate.py --out cascade_map.py

Needs 3.12+ to *build* (see `_require_fstring_aware_tokenizer`); the file it
writes runs on 3.11+, like the package.

WHAT MADE THIS HARD, so nobody reintroduces it
----------------------------------------------
The first version did its editing with regexes and line-prefix matches over the
raw source. Both are blind to the difference between code and a string that
looks like code, and both went wrong in ways that produced a file which ran
fine and answered differently:

* renaming `__all__` rewrote the *string literal* `"__all__"` that the resolver
  compares an assignment target against, so it never recognised a target
  module's exports, and `import *` linked every name in the module -- 110 call
  edges where the package emits 108, which is exactly the over-linking the
  `res_import_star` fixture exists to forbid;
* stripping lines beginning `from cascade_map` deleted those lines from *inside*
  the string holding the Mode A child process's bootstrap, so `trace` shipped a
  child script with no imports.

So every edit here goes through the AST or the token stream, and three checks
fail the build rather than let a broken file out: the module-level names the
child prologue lifts, the attributes reached through a module alias, and
`ast.parse` of the result.

The remaining job is verification: `tests/test_amalgamate.py` builds the file
and requires its `analyze` and `trace` output to match the package's byte for
byte on the fixture corpus.
"""

from __future__ import annotations

import argparse
import ast
import io
import sys
import tokenize
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "cascade_map"

#: Import order. Dependencies first; a module may only use what precedes it.
#: `viewer/__init__.py` is here because it is not a pure re-export -- it defines
#: `render_to_file`, which card 10 calls.
ORDER = [
    "contracts/interfaces.py",
    "ingest/constants.py", "ingest/hashing.py", "ingest/walker.py",
    "ingest/python_module.py", "ingest/data_files.py", "ingest/cache.py",
    "ingest/inventory.py",
    "resolve.py", "cascade.py", "lineage.py", "findings.py", "diff.py",
    "dependencies.py", "ledger.py", "docrecords.py", "enrichment.py",
    "harness/errors.py", "harness/hashing.py", "harness/config.py",
    "harness/sandbox.py", "harness/harness.py", "harness/scenarios.py",
    "tracer/limits.py", "tracer/capture.py", "tracer/static_index.py",
    "tracer/recording.py", "tracer/collector.py", "tracer/nondeterminism.py",
    "tracer/contradictions.py", "tracer/tracer.py",
    "alignment.py", "narrative.py",
    "viewer/loader.py", "viewer/views.py", "viewer/html_export.py",
    "viewer/blueprint.py",
    "viewer/__init__.py",
    "cli.py",
]

#: What `cli.py`'s `_CHILD_PROLOGUE` becomes in the single file.
#:
#: The Mode A harness runs in a child interpreter, and the child needs this
#: tool's own classes. In the package it imports them. Here there is no package
#: to import, so it loads this very file by path and lifts the names off it.
#: `cli.py` marks the seam; this is the only thing that crosses it.
SINGLE_FILE_PROLOGUE = '''
import importlib.util, json, sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("_cascade_map_single", {self_file})
_mod = importlib.util.module_from_spec(_spec)
sys.modules["_cascade_map_single"] = _mod
_spec.loader.exec_module(_mod)

canonical_dumps = _mod.canonical_dumps
Harness = _mod.Harness
HarnessRefusal = _mod.HarnessRefusal
RunConfig = _mod.RunConfig
ScenarioSpec = _mod.ScenarioSpec
compute_graph_hash = _mod.compute_graph_hash
compute_target_hashes = _mod.compute_target_hashes
StaticIndex = _mod.StaticIndex
Tracer = _mod.Tracer
build_index = _mod.build_index
'''

#: Names the child prologue lifts off the loaded module. Checked against the
#: generated file, because the prologue is a string: nothing type-checks it and
#: the rename pass deliberately cannot see into it, so a name that got renamed
#: or moved would surface as a `NameError` inside a child process during a
#: Mode A run, long after the build reported success.
PROLOGUE_NAMES = (
    "canonical_dumps", "Harness", "HarnessRefusal", "RunConfig", "ScenarioSpec",
    "compute_graph_hash", "compute_target_hashes", "StaticIndex", "Tracer",
    "build_index",
)


# ---------------------------------------------------------------------------
# Resolving what a name actually refers to
# ---------------------------------------------------------------------------


def _top_level_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _absolute_module(path: Path, node: ast.ImportFrom) -> str | None:
    """The dotted module an `ImportFrom` in *path* refers to, or None if it is
    not one of ours. Relative imports are resolved against *path*'s package."""
    if node.level == 0:
        module = node.module or ""
        return module if module.split(".")[0] == "cascade_map" else None
    parts = ["cascade_map", *path.relative_to(PACKAGE).parent.parts]
    up = node.level - 1
    if up:
        parts = parts[: len(parts) - up] or ["cascade_map"]
    if node.module:
        parts.append(node.module)
    return ".".join(parts)


def _module_file(dotted: str) -> Path | None:
    """The file backing a dotted `cascade_map...` module, or None."""
    rel = dotted.removeprefix("cascade_map").lstrip(".").replace(".", "/")
    candidates = [PACKAGE / "__init__.py"] if not rel else [
        PACKAGE / f"{rel}.py",
        PACKAGE / rel / "__init__.py",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _definer(dotted: str, name: str, seen: frozenset[str] = frozenset()) -> Path | None:
    """Which file actually defines *name*, following `__init__.py` re-exports.

    `from cascade_map.tracer import StaticIndex` has to end up at
    `tracer/static_index.py`, or a renamed name is missed and the single file
    binds to the wrong definition. Returns None for a submodule import
    (`from . import views`), which is a namespace, not a definition.
    """
    if f"{dotted}:{name}" in seen:
        return None
    seen = seen | {f"{dotted}:{name}"}
    file = _module_file(dotted)
    if file is None:
        return None
    tree = _parse(file)
    if name in _top_level_names(tree):
        return file
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            if (alias.asname or alias.name) != name:
                continue
            onward = _absolute_module(file, node)
            if onward:
                found = _definer(onward, alias.name, seen)
                if found is not None:
                    return found
    return None


def _collisions(files: list[Path]) -> dict[str, list[Path]]:
    """Top-level names defined by more than one module.

    Dunders are excluded. They are per-module conventions, not collisions:
    `__all__` is defined by seventeen modules and means "what `import *`
    exports", which in one namespace means nothing -- and renaming it is
    actively wrong, because `resolve.py` decides a *target* module's exports by
    comparing an assignment target against the literal string "__all__".
    """
    seen: dict[str, list[Path]] = {}
    for path in files:
        for name in sorted(_top_level_names(_parse(path))):
            seen.setdefault(name, []).append(path)
    return {
        name: paths
        for name, paths in seen.items()
        if len(paths) > 1 and not (name.startswith("__") and name.endswith("__"))
    }


def _module_prefix(path: Path) -> str:
    return "_" + path.stem.lstrip("_")


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------


def _require_fstring_aware_tokenizer() -> None:
    """Refuse to build on an interpreter whose tokenizer hides f-strings.

    Before 3.12, `tokenize` hands back a whole f-string as one STRING token, so
    the names inside its replacement fields are invisible and go unrenamed --
    producing a file that raises `NameError: name '_text' is not defined` the
    first time it reaches one. Writing a scanner to pick expressions out of
    f-string text by hand is precisely the kind of almost-right string surgery
    that caused the bugs this module's header describes. So the build refuses
    instead. The file it generates still runs on 3.11.
    """
    if not hasattr(tokenize, "FSTRING_START"):
        raise SystemExit(
            f"amalgamate needs Python 3.12+ to build (running "
            f"{sys.version_info.major}.{sys.version_info.minor}); before 3.12 "
            f"`tokenize` cannot see the names inside f-strings, so renaming them is "
            f"not possible. The generated file still supports 3.11+. "
            f"Try: python3.12 tools/amalgamate.py"
        )


def _rename_names(source: str, mapping: dict[str, str]) -> str:
    """Rename top-level names, touching code tokens only.

    Only NAME tokens are candidates, and one preceded by `.` is skipped because
    it is an attribute, not the module-level name. Everything else -- strings,
    comments, f-string literal text, whitespace -- is copied through byte for
    byte, because the edits are applied back into the original lines at the
    exact columns `tokenize` reports.
    """
    if not mapping:
        return source

    _require_fstring_aware_tokenizer()

    edits: list[tuple[int, int, int, str]] = []
    previous: tokenize.TokenInfo | None = None
    ignore = (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT,
              tokenize.INDENT, tokenize.DEDENT)
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.NAME and token.string in mapping:
            attribute = (
                previous is not None
                and previous.type == tokenize.OP
                and previous.string == "."
            )
            if not attribute:
                edits.append(
                    (token.start[0], token.start[1], token.end[1], mapping[token.string])
                )
        if token.type not in ignore:
            previous = token

    if not edits:
        return source
    lines = source.splitlines(keepends=True)
    for row, start, end, new in sorted(edits, reverse=True):
        line = lines[row - 1]
        lines[row - 1] = line[:start] + new + line[end:]
    return "".join(lines)


def _strip_internal_imports(source: str, path: Path) -> tuple[str, set[str]]:
    """Remove the package's own imports; collect external ones to hoist.

    Driven by the AST, not by matching line prefixes, because `cli.py` holds the
    child process's bootstrap as a *string* whose lines begin `import json, sys`
    and `from cascade_map.harness import ...` at column zero.

    Nesting decides what happens to an import, so both are handled:

    * top-level, external -> hoisted to the head of the generated file
    * top-level, internal -> dropped; the name is a global here
    * nested, internal    -> dropped, same reason
    * nested, external    -> left exactly where it is (e.g. the deliberately
      late `import subprocess` inside `trace`)
    """
    tree = ast.parse(source)
    top_level = {id(node) for node in tree.body}
    lines = source.splitlines()
    drop: set[int] = set()
    hoisted: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        span = range(node.lineno, (node.end_lineno or node.lineno) + 1)
        if isinstance(node, ast.ImportFrom):
            if node.module == "__future__" or _absolute_module(path, node) is not None:
                drop.update(span)
                continue
        if id(node) in top_level:
            drop.update(span)
            hoisted.add("\n".join(lines[i - 1] for i in span).strip())

    kept = [line for number, line in enumerate(lines, 1) if number not in drop]
    return "\n".join(kept), hoisted


def _lift_settings(source: str) -> tuple[str, str]:
    """Cut `METATRON_SETTINGS` and its comment block out of cli.py.

    The owner edits this dict in place, and `cli.py` is the last module in
    `ORDER`, so leaving it where it is buries the one thing they have to change
    thirty thousand lines down. The build lifts it to the top of the generated
    file instead.

    AST-driven, like every other edit here: the assignment's own line span, then
    upwards over the contiguous comment block introducing it. Nothing is matched
    by text, so a string that happens to contain "METATRON_SETTINGS" is
    untouched. The build fails if the result does not hold exactly one
    module-level definition of the name -- see `_check_settings`.
    """
    for node in ast.parse(source).body:
        if not (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "METATRON_SETTINGS"
        ):
            continue
        lines = source.splitlines()
        start = node.lineno - 1
        while start > 0:
            candidate = lines[start - 1].strip()
            if candidate.startswith("#") or candidate == "":
                start -= 1
            else:
                break
        end = node.end_lineno or node.lineno
        block = "\n".join(lines[start:end]).strip("\n")
        remainder = "\n".join(lines[:start] + lines[end:])
        return remainder, block + "\n"
    raise SystemExit(
        "cli.py no longer defines METATRON_SETTINGS at module level. That dict is "
        "the owner's whole configuration surface and the single file is meant to "
        "open on it. Restore it or update this tool."
    )


def _check_settings(text: str) -> None:
    """Exactly one METATRON_SETTINGS, and it is near the top."""
    found = [
        node
        for node in ast.parse(text).body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "METATRON_SETTINGS"
    ]
    if len(found) != 1:
        raise SystemExit(
            f"the generated file defines METATRON_SETTINGS {len(found)} times. "
            f"Exactly one is correct: two would mean the owner edits a dict that "
            f"the later definition overwrites."
        )
    if found[0].lineno > 200:
        raise SystemExit(
            f"METATRON_SETTINGS landed at line {found[0].lineno}. It is the first "
            f"thing the owner edits and belongs at the top of the file."
        )


def _replace_child_prologue(source: str) -> str:
    """Swap cli.py's package-import child prologue for the by-path one."""
    for node in ast.parse(source).body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "_CHILD_PROLOGUE"
        ):
            lines = source.splitlines(keepends=True)
            return (
                "".join(lines[: node.lineno - 1])
                + "_CHILD_PROLOGUE = "
                + repr(SINGLE_FILE_PROLOGUE)
                + "\n"
                + "".join(lines[node.end_lineno :])
            )
    raise SystemExit(
        "cli.py no longer defines _CHILD_PROLOGUE. That assignment is the declared "
        "seam between the package and the single-file build; without it the single "
        "file's `trace` child would import a package that does not exist. Restore "
        "the seam or update this tool."
    )


# ---------------------------------------------------------------------------
# Module aliases
# ---------------------------------------------------------------------------


def _module_aliases(files: list[Path]) -> dict[str, set[str]]:
    """Local names bound to one of our *modules* (`from . import views`), and
    the attributes reached through each.

    A module object has no meaning in one file, so each such name is bound to
    this module itself: `views.browser_view` then resolves to the global
    `browser_view`, which is where the amalgamation put it. The attributes are
    collected so the build can prove every one of them exists.
    """
    aliases: dict[str, set[str]] = {}
    for path in files:
        tree = _parse(path)
        local: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            dotted = _absolute_module(path, node)
            if dotted is None:
                continue
            for alias in node.names:
                if _definer(dotted, alias.name) is None and _module_file(
                    f"{dotted}.{alias.name}"
                ):
                    local.add(alias.asname or alias.name)
        for name in local:
            aliases.setdefault(name, set())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in local
            ):
                aliases[node.value.id].add(node.attr)
    return aliases


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def _check_globals(text: str, aliases: dict[str, set[str]]) -> None:
    """Fail the build on any name the generated file promises but lacks."""
    defined = _top_level_names(ast.parse(text))
    missing = [name for name in PROLOGUE_NAMES if name not in defined]
    if missing:
        raise SystemExit(
            f"the Mode A child prologue lifts names the generated file does not "
            f"define at module level: {missing}. Either a collision renamed them or "
            f"they moved; fix SINGLE_FILE_PROLOGUE."
        )
    for alias, attrs in sorted(aliases.items()):
        absent = sorted(a for a in attrs if a not in defined)
        if absent:
            raise SystemExit(
                f"`{alias}` is aliased to this module, but these attributes reached "
                f"through it are not module-level names here: {absent}."
            )


def build() -> str:
    files = [PACKAGE / rel for rel in ORDER]
    missing = [f for f in files if not f.exists()]
    if missing:
        raise SystemExit(f"missing modules: {[str(m) for m in missing]}")

    clashes = _collisions(files)

    # Every definer is renamed, not just the later ones. Renaming only the later
    # definers leaves the first holding the bare name, so a module that imported
    # the *later* one silently binds to the *earlier* one -- no error, wrong
    # results.
    canonical = {
        (path.as_posix(), name): f"{_module_prefix(path)}_{name}"
        for name, paths in clashes.items()
        for path in paths
    }

    # Per file: its own colliding definitions, plus every colliding name it
    # imports, mapped to the definition it actually names. Imports are followed
    # through `__init__.py` re-exports and resolved for relative form, and
    # function-level imports count too -- `cli.py` has nine of them.
    renames: dict[Path, dict[str, str]] = {}
    for path in files:
        mapping = {
            name: canonical[(path.as_posix(), name)]
            for name in clashes
            if (path.as_posix(), name) in canonical
        }
        for node in ast.walk(_parse(path)):
            if not isinstance(node, ast.ImportFrom):
                continue
            dotted = _absolute_module(path, node)
            if dotted is None:
                continue
            for alias in node.names:
                definer = _definer(dotted, alias.name)
                if definer is None:
                    continue
                key = (definer.as_posix(), alias.name)
                if key in canonical:
                    mapping[alias.asname or alias.name] = canonical[key]
        if mapping:
            renames[path] = mapping

    aliases = _module_aliases(files)

    bodies: list[str] = []
    hoisted: set[str] = {"import sys"}
    settings_block = ""
    for path in files:
        source = path.read_text(encoding="utf-8")
        if path.name == "cli.py":
            source = _replace_child_prologue(source)
            source, settings_block = _lift_settings(source)
        source = _rename_names(source, renames.get(path, {}))
        body, imports = _strip_internal_imports(source, path)
        hoisted |= imports
        rel = path.relative_to(PACKAGE).as_posix()
        bodies.append(f"\n\n# {'=' * 74}\n# {rel}\n# {'=' * 74}\n\n{body.strip()}\n")

    header = '''"""CASCADE-MAP — a static and runtime map of a Python decision engine.

Single file. Generated from the package by `tools/amalgamate.py`; every line
came from a module that passed independent verification. Regenerate rather than
editing this file by hand:

    python3.12 tools/amalgamate.py --out cascade_map.py

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
    alias_block = ""
    if aliases:
        alias_block = (
            "\n# A module-object import (`from . import views`) has no module to point\n"
            "# at in one file. Everything is in this namespace, so the namespace is\n"
            "# the answer: `views.browser_view` finds the global `browser_view`.\n"
            + "".join(f"{name} = sys.modules[__name__]\n" for name in sorted(aliases))
        )
    text = (
        header
        + "\n".join(sorted(i for i in hoisted if i))
        + "\n"
        + alias_block
        + "\n\n"
        + settings_block
        + "".join(bodies)
        + '\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'
    )
    _check_globals(text, aliases)
    _check_settings(text)
    return text


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
