"""Card 1 orchestrator: walk, hash, parse, mint IDs, emit the inventory.

Implements `cascade_map.contracts.interfaces.IngestionCard`. Never imports,
execs, evals or unpickles anything under the walked root -- `ast` and
`pathlib` only.
"""

from __future__ import annotations

import ast
from pathlib import Path

from cascade_map.contracts.interfaces import (
    Confidence,
    Element,
    ElementKind,
    Method,
    Provenance,
    SourceSpan,
    Unresolved,
    UnresolvedReason,
    file_id,
)

from .cache import Cache
from .constants import MAX_FILE_BYTES
from .data_files import parse_data_file
from .hashing import locate_byte_offset, sha256_hex, sha256_text
from .python_module import parse_python_file
from .walker import module_dotted_name, walk


def _default_cache_dir() -> Path:
    return Path.cwd() / ".cascade_map" / "cache"


def _relative_to_root(path: Path, root_resolved: Path) -> str | None:
    """`SourceSpan.path` is contractually POSIX and relative to the target
    root (interfaces.py), not "whatever prefix the caller's `root` string
    happened to have". Resolving both sides makes this invariant to relative
    vs. absolute `root`, and to `.`/`..` in either -- the property
    `test_span_path_is_identical_for_relative_and_absolute_root` checks
    directly.

    Returns None when `path` resolves outside `root_resolved` (a symlink
    escaping the tree, or the root itself being a symlink to somewhere the
    file is not under) -- that is a real case, not a silent absolute-path
    fallback; the caller turns it into an Unresolved record."""
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        resolved = path.resolve()
    try:
        return resolved.relative_to(root_resolved).as_posix()
    except ValueError:
        return None




class Ingestor:
    """`IngestionCard`. `cache_dir` defaults to a location outside any
    scanned tree, so the default never risks writing into a read-only
    target -- `inventory(root)` itself takes no extra arguments, matching the
    contract's `IngestionCard` protocol exactly."""

    def __init__(self, cache_dir: str | Path | None = None) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir is not None else _default_cache_dir()

    def inventory(self, root: str) -> tuple[list[Element], list[Unresolved]]:
        root_path = Path(root)
        root_resolved = root_path.resolve()
        files = list(walk(root))
        py_files = [f for f in files if f.kind == "python"]
        local_top_names = frozenset(
            module_dotted_name(root_path, f.path).split(".")[0] for f in py_files
        )

        cache = Cache(self.cache_dir / f"{sha256_text(str(root_resolved))}.json")

        elements: list[Element] = []
        unresolved: list[Unresolved] = []
        module_names_seen: set[str] = set()

        for f in files:
            relkey = _relative_to_root(f.path, root_resolved)
            if relkey is None:
                unresolved.append(
                    Unresolved(
                        id=file_id(f.path.as_posix()),
                        reason=UnresolvedReason.AMBIGUOUS,
                        span=SourceSpan(path=f.path.name, line=1),
                        description=(
                            f"{f.path} resolves outside the target root {root_resolved} "
                            "(a symlink escaping the tree, or the root itself is a symlink "
                            "elsewhere) -- no root-relative path can be emitted for it"
                        ),
                    )
                )
                continue
            try:
                raw = f.path.read_bytes()
            except OSError as exc:
                unresolved.append(
                    Unresolved(
                        id=file_id(relkey),
                        reason=UnresolvedReason.DECODE_ERROR,
                        span=SourceSpan(path=relkey, line=1),
                        description=f"could not read {relkey}: {exc}",
                    )
                )
                continue

            content_hash = sha256_hex(raw)

            if f.kind == "python":
                module = module_dotted_name(root_path, f.path)
                module_names_seen.add(module)

                if len(raw) > MAX_FILE_BYTES:
                    unresolved.append(
                        Unresolved(
                            id=module,
                            reason=UnresolvedReason.TOO_LARGE,
                            span=SourceSpan(path=relkey, line=1),
                            description=(
                                f"{relkey} is {len(raw)} bytes, exceeds the "
                                f"{MAX_FILE_BYTES}-byte parse limit"
                            ),
                        )
                    )
                    continue

                cached = cache.get(relkey, content_hash)
                if cached is not None:
                    els, unr = cached
                    cache.reuse(relkey)
                else:
                    els, unr = self._parse_python(module, relkey, raw, local_top_names)
                    cache.put(relkey, content_hash, els, unr)
                elements.extend(els)
                unresolved.extend(unr)
            else:
                cached = cache.get(relkey, content_hash)
                if cached is not None:
                    els, unr = cached
                    cache.reuse(relkey)
                else:
                    els, unr = parse_data_file(f.kind, relkey, raw)
                    cache.put(relkey, content_hash, els, unr)
                elements.extend(els)
                unresolved.extend(unr)

        cache.save()

        elements.extend(_synthesize_packages(module_names_seen, root_path, root_resolved))

        elements, collision_unresolved = _resolve_id_collisions(elements)
        unresolved.extend(collision_unresolved)

        return elements, unresolved

    @staticmethod
    def _parse_python(
        module: str, relkey: str, raw: bytes, local_top_names: frozenset[str]
    ) -> tuple[list[Element], list[Unresolved]]:
        try:
            source = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            line, col = locate_byte_offset(raw, exc.start)
            return [], [
                Unresolved(
                    id=module,
                    reason=UnresolvedReason.DECODE_ERROR,
                    span=SourceSpan(path=relkey, line=line, col=col),
                    description=f"{relkey} is not valid UTF-8: {exc}",
                    attempted=(Method.AST_DIRECT,),
                )
            ]
        try:
            tree = ast.parse(source, filename=relkey)
        except SyntaxError as exc:
            return [], [
                Unresolved(
                    id=module,
                    reason=UnresolvedReason.SYNTAX_ERROR,
                    span=SourceSpan(path=relkey, line=exc.lineno or 1, col=exc.offset),
                    description=str(exc),
                    attempted=(Method.AST_DIRECT,),
                )
            ]
        return parse_python_file(module, relkey, source, raw, tree, local_top_names)


def _synthesize_packages(
    module_names: set[str], root_path: Path, root_resolved: Path
) -> list[Element]:
    """A PACKAGE element for every directory prefix that organizes submodules
    but has no `__init__.py` of its own (PEP 420 namespace packages). A
    directory *with* `__init__.py` is already represented -- its MODULE
    element carries the package's dotted name."""
    prefixes: set[str] = set()
    for name in module_names:
        parts = name.split(".")
        for i in range(1, len(parts)):
            prefixes.add(".".join(parts[:i]))

    base = root_path.parent if root_path.parent != root_path else root_path
    out: list[Element] = []
    for prefix in sorted(prefixes - module_names):
        dir_path = base.joinpath(*prefix.split("."))
        try:
            listing = sorted(p.name for p in dir_path.iterdir())
        except OSError:
            listing = []
        span_path = _relative_to_root(dir_path, root_resolved)
        if span_path is None:
            # Every prefix here was derived from a file actually found under
            # root, so this directory should always resolve inside it; this
            # branch only guards against a pathological symlink underneath.
            span_path = dir_path.name
        out.append(
            Element(
                id=prefix,
                kind=ElementKind.PACKAGE,
                name=prefix.rsplit(".", 1)[-1],
                qualname="",
                module=prefix,
                span=SourceSpan(path=span_path or ".", line=1),
                provenance=Provenance(
                    method=Method.AST_DIRECT,
                    confidence=Confidence.CERTAIN,
                    note="namespace package: directory has no __init__.py",
                ),
                content_hash=sha256_text("\n".join(listing)),
            )
        )
    return out


def _resolve_id_collisions(elements: list[Element]) -> tuple[list[Element], list[Unresolved]]:
    """Constraint: an inseparable ID collision is an ID_COLLISION record,
    never a silent overwrite. The `#n` ordinal scheme already separates
    every legitimate redefinition before elements reach here; what remains
    is a genuine defect (e.g. a directory and a `.py` file both claiming the
    same dotted name)."""
    seen: dict[str, Element] = {}
    kept: list[Element] = []
    unresolved: list[Unresolved] = []
    for el in elements:
        prior = seen.get(el.id)
        if prior is None:
            seen[el.id] = el
            kept.append(el)
            continue
        if prior.content_hash == el.content_hash and prior.span == el.span:
            continue  # the same fact reached twice (e.g. reused cache entry); not a collision
        unresolved.append(
            Unresolved(
                id=el.id,
                reason=UnresolvedReason.ID_COLLISION,
                span=el.span,
                description=(
                    f"id '{el.id}' claimed by both {prior.span.path}:{prior.span.line} "
                    f"and {el.span.path}:{el.span.line}"
                ),
                candidate_ids=(prior.id, el.id),
            )
        )
    return kept, unresolved


def inventory(root: str, cache_dir: str | Path | None = None) -> tuple[list[Element], list[Unresolved]]:
    """Functional convenience wrapper around `Ingestor`."""
    return Ingestor(cache_dir=cache_dir).inventory(root)
