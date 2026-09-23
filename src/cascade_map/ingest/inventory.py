"""Card 1 orchestrator: walk, hash, parse, mint IDs, emit the inventory.

Implements `cascade_map.contracts.interfaces.IngestionCard`. Never imports,
execs, evals or unpickles anything under the walked root -- `ast` and
`pathlib` only.
"""

from __future__ import annotations

import ast
import time
from collections.abc import Callable
from dataclasses import dataclass, field
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
from .parallel import WorkerReport, resolve_workers, run_units
from .python_module import parse_python_file
from .walker import module_dotted_name, walk


@dataclass
class _Step:
    """One file's place in the output, decided serially in walk order before
    any worker starts. Exactly one of `job_key` (work still to do) or the
    inline `elements`/`unresolved` (already known) is populated."""

    elements: list[Element] = field(default_factory=list)
    unresolved: list[Unresolved] = field(default_factory=list)
    #: Set when this file's records came from the cache; carried forward.
    reuse_key: str | None = None
    #: Set when this file still has to be parsed; the key into the results.
    job_key: str | None = None
    content_hash: str = ""


def _run_unit(payload: tuple) -> tuple[list[Element], list[Unresolved]]:
    """The whole of one file's work, and the only thing a worker ever runs.

    Module level and pure: a `ProcessPoolExecutor` pickles what it is given,
    and this takes bytes and returns dataclasses, both of which pickle. It
    parses; it does not read files, write the cache, or order anything.
    """
    tag = payload[0]
    if tag == "python":
        _, module, relkey, raw, local_top_names = payload
        return Ingestor._parse_python(module, relkey, raw, local_top_names)
    _, kind, relkey, raw = payload
    return parse_data_file(kind, relkey, raw)


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

    def __init__(
        self, cache_dir: str | Path | None = None, workers: int | None = None
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir is not None else _default_cache_dir()
        #: `None` = auto (see `parallel.resolve_workers`); 0 or 1 = in-process.
        self.workers = resolve_workers(workers)
        #: Filled in by every `inventory()` call: what the workers bought.
        #: Read by the CLI and printed; never written into an artifact,
        #: because it is a timing and constraint 4 forbids timings in output.
        self.worker_report = WorkerReport()

    def inventory(
        self,
        root: str,
        *,
        on_unit: Callable[[int, int], None] | None = None,
    ) -> tuple[list[Element], list[Unresolved]]:
        """`on_unit(done, total)` is called once per PARSED FILE, never inside
        a per-node loop: one call per unit of work costs nothing measurable and
        a per-node counter would show up in the stage it is reporting on.
        A cached file is not a unit -- it is not parsed -- so the count is of
        work actually being done."""
        stage_started = time.perf_counter()
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

        # ---- pass 1: plan, serially. -------------------------------------
        # Everything that touches the filesystem, the cache, or the ordering
        # of records happens here, in walk order, in this process. A worker
        # only ever receives bytes already in memory and returns facts; it
        # never reads a file, never sees the cache, and never decides where
        # its output lands. That is what keeps the parallel run byte-identical
        # to the in-process one (and to itself) rather than merely usually
        # equal.
        steps: list[_Step] = []
        jobs: list[tuple[str, tuple]] = []

        for f in files:
            relkey = _relative_to_root(f.path, root_resolved)
            if relkey is None:
                steps.append(
                    _Step(
                        unresolved=[
                            Unresolved(
                                id=file_id(f.path.as_posix()),
                                reason=UnresolvedReason.AMBIGUOUS,
                                span=SourceSpan(path=f.path.name, line=1),
                                description=(
                                    f"{f.path} resolves outside the target root "
                                    f"{root_resolved} (a symlink escaping the tree, or "
                                    "the root itself is a symlink elsewhere) -- no "
                                    "root-relative path can be emitted for it"
                                ),
                            )
                        ]
                    )
                )
                continue
            try:
                raw = f.path.read_bytes()
            except OSError as exc:
                steps.append(
                    _Step(
                        unresolved=[
                            Unresolved(
                                id=file_id(relkey),
                                reason=UnresolvedReason.DECODE_ERROR,
                                span=SourceSpan(path=relkey, line=1),
                                description=f"could not read {relkey}: {exc}",
                            )
                        ]
                    )
                )
                continue

            content_hash = sha256_hex(raw)

            if f.kind == "python":
                module = module_dotted_name(root_path, f.path)
                module_names_seen.add(module)

                if len(raw) > MAX_FILE_BYTES:
                    steps.append(
                        _Step(
                            unresolved=[
                                Unresolved(
                                    id=module,
                                    reason=UnresolvedReason.TOO_LARGE,
                                    span=SourceSpan(path=relkey, line=1),
                                    description=(
                                        f"{relkey} is {len(raw)} bytes, exceeds the "
                                        f"{MAX_FILE_BYTES}-byte parse limit"
                                    ),
                                )
                            ]
                        )
                    )
                    continue
                payload: tuple = ("python", module, relkey, raw, local_top_names)
            else:
                payload = ("data", f.kind, relkey, raw)

            cached = cache.get(relkey, content_hash)
            if cached is not None:
                els, unr = cached
                steps.append(_Step(elements=els, unresolved=unr, reuse_key=relkey))
                continue

            # A unit is a WHOLE file and is never split. Half a function is
            # not parseable, and the IDs minted from it would be wrong rather
            # than merely ugly.
            steps.append(_Step(job_key=relkey, content_hash=content_hash))
            jobs.append((relkey, payload))

        # ---- pass 2: do the work, in-process or across the pool. ---------
        self.worker_report = WorkerReport()
        results = run_units(
            _run_unit, jobs, self.workers, self.worker_report, on_unit=on_unit
        )

        # ---- pass 3: assemble, serially, in walk order. ------------------
        # Results are looked up by key in the order pass 1 recorded, so the
        # order workers happened to finish in cannot reach the output. The
        # parent owns every cache write: no child ever touches the cache
        # file, so there is nothing for concurrent writes to corrupt.
        for step in steps:
            if step.job_key is not None:
                got = results.get(step.job_key)
                if got is None:  # pragma: no cover - run_units returns every key
                    unresolved.append(
                        Unresolved(
                            id=file_id(step.job_key),
                            reason=UnresolvedReason.AMBIGUOUS,
                            span=SourceSpan(path=step.job_key, line=1),
                            description=(
                                f"worker returned no result for {step.job_key}; "
                                "the file was not analysed"
                            ),
                        )
                    )
                    continue
                els, unr = got
                cache.put(step.job_key, step.content_hash, els, unr)
                elements.extend(els)
                unresolved.extend(unr)
                continue
            if step.reuse_key is not None:
                cache.reuse(step.reuse_key)
            elements.extend(step.elements)
            unresolved.extend(step.unresolved)

        cache.save()

        elements.extend(_synthesize_packages(module_names_seen, root_path, root_resolved))

        elements, collision_unresolved = _resolve_id_collisions(elements)
        unresolved.extend(collision_unresolved)

        # The gain the owner feels is over the WHOLE stage, including the
        # serial walking, reading, hashing and cache writing that no worker
        # count removes. Recording it here rather than around the pool alone
        # is the difference between an honest number and a flattering one.
        self.worker_report.total_seconds = time.perf_counter() - stage_started

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


def inventory(
    root: str,
    cache_dir: str | Path | None = None,
    workers: int | None = None,
) -> tuple[list[Element], list[Unresolved]]:
    """Functional convenience wrapper around `Ingestor`.

    `workers` is `None` for auto, 0 or 1 for in-process. Callers that
    want the worker-effectiveness report build an `Ingestor` themselves
    and read `.worker_report`; this wrapper keeps the two-value return
    the `IngestionCard` contract specifies."""
    return Ingestor(cache_dir=cache_dir, workers=workers).inventory(root)
