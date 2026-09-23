"""Card 18 round 2 -- the per-script workspace.

One folder per script, accumulating every version and every run, so the whole
development story of one script lives in one place and can be read back from a
file rather than re-derived.

``docs/design/WORKSPACE_LAYOUT.md`` is the binding design; this module is its
implementation. Four things it exists to get right:

**1. One path in settings.** ``WORKSPACE`` is the root. ``PROJECT`` is derived
from the script being analysed, so naming the script is the whole instruction
and there is nothing to point at and nothing to mis-point. The three old keys
-- ``VERSIONS_DIR``, ``OUT_DIR``, ``LEDGER`` -- keep working and resolve into
the new layout, each with a one-line notice saying what it now means. An owner
who upgrades mid-project gets their history, not an error and not silence.

**2. Sources stored once per DISTINCT content.** A version is its tree hash, so
``sources/<hash>/`` either exists already or does not. A re-run of unchanged
code stores nothing. At 14.8 MB that is the difference between 14.8 MB per real
change and ~740 MB after fifty runs. :func:`disk_usage` measures it and the
history file prints it, so the workspace never grows in silence.
``--no-sources`` records the hash and skips the copy, and the record says which
of the two happened -- an absent snapshot is never left looking like a stored
one.

**3. The per-sport rule, and the honesty rule under it.** The sport is a
parameter of the ANALYSIS, not of the filing. Elements and content hashes are
properties of the source text, do not vary by sport, and stay once at script
level. Everything derived from a path through the code -- reachability,
decision relevance and the findings that rest on them -- is computed per sport
from that sport's entry points and stored per sport.

Static analysis can only do that where the sport's selection is statically
resolvable. Where it is not, :class:`SportScope` comes back ``UNION`` and the
sport's file says so in the words the design fixes::

    scope: UNION ACROSS ALL SPORTS
    reason: <why the branches could not be separated>

Blended data is never presented as sport-specific. A sport-specific file that
is secretly the union is the exact failure the rule exists to prevent.

**4. Nothing empty reads as nothing changed.** A runtime history with no
measurement says "not measured for <sport>", never "no change" -- the same
discipline the ledger already applies to ``runtime_delta``.

Determinism
-----------
Every document here goes through :func:`canonical_dumps`, the project's one
serialiser, which rejects floats and sorts keys. Nothing wall-clock is written
inside the data: the run directory's UTC stamp is a *path*, allocated only when
a version is actually analysed, so a second run with nothing new allocates
nothing and the bytes are identical.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from cascade_map.contracts.interfaces import (
    SCHEMA_VERSION,
    Confidence,
    ElementFingerprint,
    Method,
    Provenance,
    ReachabilityState,
    VersionComparison,
    VersionRecord,
    canonical_dumps,
)

__all__ = [
    "WorkspaceError",
    "Workspace",
    "Layout",
    "SourceStoreResult",
    "DiskUsage",
    "SportScope",
    "ElementLife",
    "MigrationResult",
    "DEFAULT_WORKSPACE",
    "EXCLUDED_DIRS",
    "UNION_SCOPE",
    "project_name_for",
    "resolve_layout",
    "store_source",
    "disk_usage",
    "resolve_sport_scope",
    "write_history",
    "write_runtime_history",
    "read_history",
    "read_fingerprints",
    "read_comparisons",
    "element_life",
    "render_history",
    "render_element_life",
    "migrate",
    "render_migration",
    "list_projects",
]


#: Where the workspace lives when the owner has not said. A relative path, so
#: it lands beside whatever they run the tool in rather than somewhere only
#: this machine has.
DEFAULT_WORKSPACE = "workspace"

#: Not part of a version's identity and never copied into a snapshot. VCS
#: metadata churns on every fetch and bytecode caches are generated, so either
#: would make an untouched tree look like a new version.
EXCLUDED_DIRS = frozenset({".git", "__pycache__", ".pytest_cache", ".mypy_cache"})

#: The exact words the design fixes for a file that could not be separated.
UNION_SCOPE = "UNION ACROSS ALL SPORTS"


class WorkspaceError(RuntimeError):
    """The workspace cannot do what was asked and will not guess instead."""


# ---------------------------------------------------------------------------
# PROJECT -- naming the script is the whole instruction
# ---------------------------------------------------------------------------

#: Anything outside this is replaced, so a project name is always a safe single
#: path component on every filesystem the tool runs on.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def project_name_for(*candidates: str | Path | None) -> str:
    """The PROJECT name, from the first candidate that yields one.

    A ``.py`` file gives its stem -- ``AmunEV_Engine_V2.py`` is project
    ``AmunEV_Engine_V2``, which is the whole point: the owner names the script
    and the tool resolves every path itself. A directory gives its name.

    Raises :class:`WorkspaceError` rather than inventing a name. A workspace
    whose folder is called ``_`` is worse than a refusal that says which
    candidates were tried.
    """
    tried: list[str] = []
    for candidate in candidates:
        if candidate is None:
            continue
        raw = str(candidate).strip()
        if not raw:
            continue
        tried.append(raw)
        path = Path(raw)
        name = path.stem if path.suffix.lower() == ".py" else path.name
        cleaned = _UNSAFE.sub("_", name).strip("._-")
        if cleaned:
            return cleaned
    raise WorkspaceError(
        "cannot derive PROJECT from "
        + (", ".join(repr(t) for t in tried) if tried else "nothing")
        + ". Set PROJECT in METATRON_SETTINGS or pass --project. PROJECT names "
        "the folder your script's whole history lives in, so it is never "
        "guessed."
    )


# ---------------------------------------------------------------------------
# The layout
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Workspace:
    """``<root>/<project>/`` and everything under it.

    Paths only. Nothing here reads, writes or creates anything, so the same
    object describes a workspace that exists and one that is about to.
    """

    root: Path
    project: str

    @property
    def project_dir(self) -> Path:
        return self.root / self.project

    @property
    def history_dir(self) -> Path:
        return self.project_dir / "history"

    @property
    def runtime_dir(self) -> Path:
        return self.history_dir / "runtime"

    @property
    def sources_dir(self) -> Path:
        return self.project_dir / "sources"

    @property
    def io_dir(self) -> Path:
        return self.project_dir / "io"

    @property
    def runs_dir(self) -> Path:
        return self.io_dir / "runs"

    @property
    def reports_dir(self) -> Path:
        return self.io_dir / "reports"

    @property
    def history_file(self) -> Path:
        """``<PROJECT>_history.json`` -- the story. The file to open."""
        return self.history_dir / f"{self.project}_history.json"

    @property
    def fingerprints_file(self) -> Path:
        return self.history_dir / f"{self.project}_fingerprints.jsonl"

    @property
    def comparisons_file(self) -> Path:
        return self.history_dir / f"{self.project}_comparisons.jsonl"

    def runtime_history_file(self, sport: str) -> Path:
        return self.runtime_dir / f"{sport}_history.json"

    def source_dir(self, content_hash: str) -> Path:
        return self.sources_dir / content_hash

    def run_dir(self, stamp: str) -> Path:
        return self.runs_dir / stamp


@dataclass(frozen=True, slots=True)
class Layout:
    """Where each concern resolved to, and why.

    ``notices`` carries one line per legacy key that is still doing work,
    saying what it now means. Printed, never silent: an owner who upgrades
    mid-project must be told their old key was honoured, because the
    alternative is a silently empty history that looks like a lost one.
    """

    workspace: Workspace
    versions_dir: Path
    artifact_root: Path
    ledger_file: Path
    notices: tuple[str, ...] = ()
    legacy_keys: tuple[str, ...] = ()

    @property
    def timestamped_runs(self) -> bool:
        """True when run directories go under ``io/runs/<UTC stamp>/``.

        False whenever a legacy ``OUT_DIR`` is doing the work, because that
        key's whole meaning is "one directory per version id" and changing it
        under an owner mid-project would strand every artifact the ledger
        already points at.
        """
        return "OUT_DIR" not in self.legacy_keys


#: Legacy key -> the one line printed when it is still set.
_LEGACY_MEANING: dict[str, str] = {
    "VERSIONS_DIR": (
        "VERSIONS_DIR is still honoured and now means the DROP FOLDER this run "
        "reads versions from; snapshots are filed under "
        "<WORKSPACE>/<PROJECT>/sources/<content hash>/. Delete the key to read "
        "versions straight from the source store."
    ),
    "OUT_DIR": (
        "OUT_DIR is still honoured and now means the ARTIFACT ROOT, one "
        "directory per version id, in place of <WORKSPACE>/<PROJECT>/io/runs/. "
        "Existing artifacts keep working; delete the key to move new runs into "
        "the workspace."
    ),
    "LEDGER": (
        "LEDGER is still honoured and now means a SECOND copy of the history, "
        "written at that path in the flat format; the workspace copy at "
        "<WORKSPACE>/<PROJECT>/history/<PROJECT>_history.json is written "
        "either way. Delete the key to keep only the workspace copy."
    ),
}


def resolve_layout(
    *,
    root: Path,
    workspace_root: str,
    project: str,
    versions_dir: str,
    out_dir: str,
    ledger: str,
    legacy_keys: Sequence[str] = (),
) -> Layout:
    """Resolve the one path in settings into every path the tool uses.

    *legacy_keys* names the old keys the owner actually set -- an owner whose
    ``VERSIONS_DIR`` still says ``"versions"`` because that is the default has
    not set it, and gets the new layout with no notice at all.
    """

    def absolute(value: str, fallback: Path) -> Path:
        if not value:
            return fallback
        path = Path(value)
        return path if path.is_absolute() else root / path

    space = Workspace(
        root=absolute(workspace_root or DEFAULT_WORKSPACE, root / DEFAULT_WORKSPACE),
        project=project,
    )
    legacy = tuple(sorted(set(legacy_keys) & set(_LEGACY_MEANING)))
    notices = tuple(_LEGACY_MEANING[key] for key in legacy)
    return Layout(
        workspace=space,
        versions_dir=(
            absolute(versions_dir, space.sources_dir)
            if "VERSIONS_DIR" in legacy
            else space.sources_dir
        ),
        artifact_root=(
            absolute(out_dir, space.runs_dir) if "OUT_DIR" in legacy else space.runs_dir
        ),
        ledger_file=(
            absolute(ledger, space.history_file)
            if "LEDGER" in legacy
            else space.history_file
        ),
        notices=notices,
        legacy_keys=legacy,
    )


# ---------------------------------------------------------------------------
# Sources -- stored once per distinct content
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceStoreResult:
    """What happened to one version's snapshot, in full.

    ``stored`` false with ``reason`` empty is impossible by construction: every
    path through :func:`store_source` sets one. An owner reading this must be
    able to tell "already there" from "you asked me not to keep it", because
    only one of those means the bytes exist.
    """

    content_hash: str
    path: str
    stored: bool
    reason: str
    file_count: int = 0
    byte_count: int = 0


def _relative(path: Path, root: Path) -> str:
    """A path as a document stores it: relative to the root where it can be.
    An absolute path would tie the workspace to one machine."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _tree_files(root: Path) -> list[Path]:
    """Every file that is part of a version, sorted.

    Read as bytes only. Nothing under *root* is imported, parsed or executed
    anywhere in this module.
    """
    out: list[Path] = []
    for path in sorted(root.rglob("*")):
        if any(part in EXCLUDED_DIRS for part in path.relative_to(root).parts):
            continue
        if path.is_file() and not path.is_symlink():
            out.append(path)
    return out


def store_source(
    tree_root: Path,
    workspace: Workspace,
    content_hash: str,
    *,
    keep: bool = True,
) -> SourceStoreResult:
    """File one version's tree under ``sources/<content hash>/``, once.

    Four outcomes, each named rather than inferred:

    * the tree IS the store entry -- a ``track`` reading straight from
      ``sources/`` re-hashes to the same folder it came from, which is a fixed
      point and must not copy a tree onto itself;
    * the hash is already stored -- nothing is written, which is the whole
      economy of the design;
    * ``keep`` is false -- the hash is recorded, the copy is not made, and the
      result says which;
    * otherwise the tree is copied, file by file, preserving relative paths so
      the snapshot re-hashes to the same id.
    """
    destination = workspace.source_dir(content_hash)
    try:
        same = tree_root.resolve() == destination.resolve()
    except OSError:  # pragma: no cover - resolve() on a vanished path
        same = False
    if same:
        files = _tree_files(destination) if destination.is_dir() else []
        return SourceStoreResult(
            content_hash=content_hash,
            path=destination.as_posix(),
            stored=True,
            reason="this version was read from the source store; it is already filed",
            file_count=len(files),
            byte_count=sum(f.stat().st_size for f in files),
        )
    if not keep:
        return SourceStoreResult(
            content_hash=content_hash,
            path="",
            stored=False,
            reason=(
                "--no-sources: the content hash is recorded and no copy was kept. "
                "Comparisons keep working from the stored fingerprints; the "
                "source text itself is only in your own version control."
            ),
        )
    if destination.is_dir():
        files = _tree_files(destination)
        return SourceStoreResult(
            content_hash=content_hash,
            path=destination.as_posix(),
            stored=True,
            reason="already stored: this exact content was filed by an earlier run",
            file_count=len(files),
            byte_count=sum(f.stat().st_size for f in files),
        )
    sources = _tree_files(tree_root)
    total = 0
    destination.mkdir(parents=True, exist_ok=True)
    for source in sources:
        target = destination / source.relative_to(tree_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        total += target.stat().st_size
    return SourceStoreResult(
        content_hash=content_hash,
        path=destination.as_posix(),
        stored=True,
        reason="stored: this content had not been seen before",
        file_count=len(sources),
        byte_count=total,
    )


# ---------------------------------------------------------------------------
# Disk -- so the workspace never grows in silence
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DiskUsage:
    """What the workspace costs, broken out by what it is spent on."""

    total_bytes: int
    total_files: int
    sources_bytes: int
    sources_files: int
    sources_distinct: int
    history_bytes: int
    io_bytes: int

    def as_document(self) -> dict[str, Any]:
        share = (
            int(round(self.sources_bytes * 100 / self.total_bytes))
            if self.total_bytes
            else 0
        )
        return {
            "history_bytes": self.history_bytes,
            "io_bytes": self.io_bytes,
            "note": (
                "measured from the workspace on disk, excluding this file itself, "
                "which cannot report a size that includes the bytes it is about "
                "to write. Sources are stored once per DISTINCT content, so a "
                "re-run of unchanged code costs nothing; each real change costs "
                "one snapshot. --no-sources records the hashes and keeps none of "
                "the copies."
            ),
            "sources_bytes": self.sources_bytes,
            "sources_distinct": self.sources_distinct,
            "sources_files": self.sources_files,
            "sources_share_percent": share,
            "total_bytes": self.total_bytes,
            "total_files": self.total_files,
        }


def _dir_size(path: Path, skip: Path | None = None) -> tuple[int, int]:
    if not path.is_dir():
        return (0, 0)
    files = [f for f in _tree_files(path) if skip is None or f != skip]
    return (sum(f.stat().st_size for f in files), len(files))


def disk_usage(workspace: Workspace, *, exclude_story: bool = False) -> DiskUsage:
    """Measure the workspace. A function of its content, so two runs over the
    same content agree byte for byte.

    *exclude_story* leaves the ``<PROJECT>_history.json`` out, and is set when
    the measurement is about to be written INTO that file. A number cannot
    include the bytes of the file it is about to be written to without
    changing them, so it is left out and the note says so rather than
    reporting a figure that is quietly wrong by the size of one file.
    """
    skip = workspace.history_file if exclude_story else None
    sources_bytes, sources_files = _dir_size(workspace.sources_dir)
    history_bytes, _ = _dir_size(workspace.history_dir, skip)
    io_bytes, _ = _dir_size(workspace.io_dir)
    total_bytes, total_files = _dir_size(workspace.project_dir, skip)
    distinct = (
        len([p for p in sorted(workspace.sources_dir.iterdir()) if p.is_dir()])
        if workspace.sources_dir.is_dir()
        else 0
    )
    return DiskUsage(
        total_bytes=total_bytes,
        total_files=total_files,
        sources_bytes=sources_bytes,
        sources_files=sources_files,
        sources_distinct=distinct,
        history_bytes=history_bytes,
        io_bytes=io_bytes,
    )


# ---------------------------------------------------------------------------
# The per-sport rule and the honesty rule under it
# ---------------------------------------------------------------------------

#: A sport name inside an identifier or a path, on token boundaries. Matching
#: ``basketball`` inside ``ebasketball`` would file one sport's code under
#: another, which is precisely the blending the honesty rule forbids.
def _sport_pattern(sport: str) -> re.Pattern[str]:
    return re.compile(rf"(?:^|[^0-9A-Za-z]){re.escape(sport)}(?:[^0-9A-Za-z]|$)")


@dataclass(frozen=True, slots=True)
class SportScope:
    """Whether this sport could be separated statically, and on what evidence.

    ``separated`` false is not a failure -- it is the honest answer for an
    engine that picks its sport from ``argv``, and it is the answer that keeps
    the tool credible. ``reason`` is required in that case and says what was
    searched and not found; the file it goes into declares
    ``scope: UNION ACROSS ALL SPORTS``.
    """

    sport: str
    separated: bool
    reason: str
    """The full reason, naming THIS sport and its own counts. Written into
    this sport's JSON, where a machine reads it and nothing is lost."""

    summary_reason: str = ""
    """The same answer with the sport's name and its counts taken out, so that
    seven sports which could not be separated share one string and the
    terminal can say it ONCE.

    This exists because seven near-identical paragraphs are not seven times
    the honesty: they bury the line the owner needs and train them to skip the
    block, and a warning nobody reads is not a warning. Falls back to
    :attr:`reason`, so a scope built without one still says something true."""

    entry_ids: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    provenance: Provenance = field(
        default_factory=lambda: Provenance(
            method=Method.NAME_HEURISTIC,
            confidence=Confidence.UNKNOWN,
            note="no separation attempted",
        )
    )

    @property
    def scope_text(self) -> str:
        return f"{self.sport.upper()} ONLY" if self.separated else UNION_SCOPE

    @property
    def scope_kind(self) -> str:
        """``SEPARATED`` or ``UNION``. The grouping key the terminal uses; the
        per-sport `scope_text` stays in the file."""
        return "SEPARATED" if self.separated else "UNION"

    @property
    def grouped_reason(self) -> str:
        return self.summary_reason or self.reason


def resolve_sport_scope(
    sport: str,
    *,
    element_spans: Mapping[str, str],
    other_sports: Sequence[str] = (),
    declared_entry_ids: Sequence[str] = (),
    config_keys: Sequence[str] = (),
) -> SportScope:
    """Can this sport's path through the code be separated statically?

    *element_spans* maps element id to the POSIX source path it was found at --
    everything this needs from the graph, and nothing that would make this
    module depend on a card that owns one.

    Separable when the owner declared an entry naming the sport, or when the
    source tree holds files whose path names this sport and no other. Anything
    else -- and in particular a sport chosen from ``argv``, which no static
    rule follows -- is ``UNION``, and says so.
    """
    pattern = _sport_pattern(sport)
    declared = tuple(sorted(e for e in declared_entry_ids if pattern.search(e)))
    if declared:
        return SportScope(
            sport=sport,
            separated=True,
            reason=(
                f"the owner declared {len(declared)} entry point(s) naming "
                f"{sport}; the graph below is constrained to them."
            ),
            summary_reason=(
                "the owner declared entry points naming each of these sports; "
                "each sport's graph is constrained to its own."
            ),
            entry_ids=declared,
            evidence=declared,
            provenance=Provenance(
                method=Method.AST_DIRECT,
                confidence=Confidence.CERTAIN,
                note=f"ENTRIES / --entry names {sport}",
            ),
        )

    others = [p for p in (_sport_pattern(s) for s in other_sports if s != sport)]
    owned = sorted(
        element_id
        for element_id, path in element_spans.items()
        if pattern.search(path) and not any(o.search(path) for o in others)
    )
    if owned:
        paths = sorted({element_spans[e] for e in owned})
        return SportScope(
            sport=sport,
            separated=True,
            reason=(
                f"{len(paths)} source file(s) name {sport} and no other sport, so "
                f"the elements in them are this sport's and the graph below is "
                f"constrained to them."
            ),
            summary_reason=(
                "source files name exactly one sport, so the elements in them are "
                "that sport's and each sport's graph is constrained to its own."
            ),
            entry_ids=tuple(owned),
            evidence=tuple(paths[:20]),
            provenance=Provenance(
                method=Method.NAME_HEURISTIC,
                confidence=Confidence.HEURISTIC,
                note=(
                    "the file path names exactly one sport. A name match is "
                    "plausible and possibly wrong, which is what HEURISTIC says."
                ),
            ),
        )

    searched = [
        "declared entry points (ENTRIES / --entry)",
        "source paths naming exactly one sport",
    ]
    if config_keys:
        searched.append(f"{len(config_keys)} config key(s)")
    return SportScope(
        sport=sport,
        separated=False,
        reason=(
            f"no static rule separates {sport} from the other sports. Searched: "
            + "; ".join(searched)
            + ". The sport is selected at runtime from a value this tool cannot "
            "follow, so order and reachability below are the UNION over every "
            f"sport. Run mode 2 for {sport} to get its real path."
        ),
        summary_reason=(
            "the sport is selected at runtime from a value this tool cannot "
            "follow. Searched: "
            + "; ".join(searched)
            + ". Order and reachability are the union over every sport. Run mode "
            "2 for a sport to get its real path."
        ),
        provenance=Provenance(
            method=Method.NAME_HEURISTIC,
            confidence=Confidence.UNKNOWN,
            note="no statically resolvable sport selection; nothing is claimed",
        ),
    )


def reachable_from(
    entry_ids: Iterable[str], edges: Mapping[str, Sequence[str]]
) -> tuple[str, ...]:
    """Forward closure over an adjacency map. Sorted, so it never carries set
    iteration order into an artifact."""
    seen: set[str] = set()
    stack = list(entry_ids)
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(edges.get(current, ()))
    return tuple(sorted(seen))


# ---------------------------------------------------------------------------
# Writing the history
# ---------------------------------------------------------------------------


def _jsonl(records: Iterable[Any]) -> str:
    rows = [canonical_dumps(record) for record in records]
    return ("\n".join(rows) + "\n") if rows else ""


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_history(
    workspace: Workspace,
    *,
    tool_version: str,
    versions: Sequence[VersionRecord],
    fingerprints: Sequence[ElementFingerprint],
    comparisons: Sequence[VersionComparison],
    sources: Sequence[SourceStoreResult],
    order_status: str,
    order_reason: str,
    sports: Sequence[str] = (),
    layout_notices: Sequence[str] = (),
) -> tuple[dict[str, Path], dict[str, Any]]:
    """Write the three named files; return where each went and what it cost.

    The disk figures come back rather than being re-measured by the caller,
    because a second measurement taken a moment later is a DIFFERENT number
    and the terminal would then disagree with the file it is describing.

    Called LAST, after the per-sport files, because it measures the workspace
    and a measurement taken before half of it was written would report a
    number that changes on the next run for no reason the owner can see.

    The split is by access pattern, not by taste. The story is one JSON that
    stays in the low hundreds of KB after a hundred versions and is the file to
    open, read, send and keep. One fingerprint per element per version is 50,000
    lines per version at the owner's scale, so those go to JSON Lines: appending
    a version appends lines and nothing is rewritten. Same for comparisons.

    Disk is measured AFTER the bulk files are written and BEFORE the story, so
    the number the story reports includes the lines it is reporting on.
    """
    _write_text(workspace.fingerprints_file, _jsonl(sorted(fingerprints, key=lambda f: f.id)))
    _write_text(workspace.comparisons_file, _jsonl(sorted(comparisons, key=lambda c: c.id)))

    usage = disk_usage(workspace, exclude_story=True)
    document = {
        "comparisons": [
            {
                "after_version_id": c.after_version_id,
                "before_version_id": c.before_version_id,
                "change_counts": dict(sorted(c.change_counts.items())),
                "decision_paths_changed": c.decision_paths_changed,
                "elements_accounted_for": c.elements_accounted_for,
                "id": c.id,
                "incomplete": bool(c.unaccounted_element_ids),
                "runtime_measured": bool(c.runtime_delta),
                "unaccounted_element_ids": list(c.unaccounted_element_ids),
            }
            for c in sorted(comparisons, key=lambda c: c.id)
        ],
        "disk": usage.as_document(),
        "files": {
            "comparisons": workspace.comparisons_file.name,
            "fingerprints": workspace.fingerprints_file.name,
            "runtime_dir": "history/runtime",
        },
        "layout_notices": list(layout_notices),
        "order": {"reason": order_reason, "status": order_status},
        "project": workspace.project,
        "schema_version": SCHEMA_VERSION,
        "sources": [
            {
                "byte_count": s.byte_count,
                "content_hash": s.content_hash,
                "file_count": s.file_count,
                "path": s.path,
                "reason": s.reason,
                "stored": s.stored,
            }
            for s in sorted(sources, key=lambda s: s.content_hash)
        ],
        "sports": list(sports),
        "tool_version": tool_version,
        "versions": [
            json.loads(canonical_dumps(record))
            for record in sorted(versions, key=lambda v: (v.ordinal, v.id))
        ],
    }
    _write_text(workspace.history_file, canonical_dumps(document) + "\n")
    return (
        {
            "comparisons": workspace.comparisons_file,
            "fingerprints": workspace.fingerprints_file,
            "history": workspace.history_file,
        },
        document["disk"],
    )


def write_runtime_history(
    workspace: Workspace,
    sport: str,
    scope: SportScope,
    *,
    tool_version: str,
    versions: Sequence[Mapping[str, Any]],
) -> Path:
    """One sport's file. Mode 2 fills it; mode 1 leaves it saying so.

    The scope declaration comes first and is never abbreviated away. A file
    whose ``scope`` reads ``UNION ACROSS ALL SPORTS`` is telling the reader
    that the order and reachability under it are the union over every sport and
    must not be read as this sport's -- which is the one thing that would cost
    the tool its credibility if it were left implied.
    """
    measured = any(version.get("runs") for version in versions)
    document = {
        "measured": measured,
        "note": (
            f"{len(versions)} version(s) recorded for {sport}."
            if measured
            else f"EMPTY MEANS NOT MEASURED FOR {sport.upper()}, NEVER 'NO CHANGE'. "
            f"No completed Mode A run of {sport} is recorded against any "
            f"version here. Run `metatron track --mode 2 --sport {sport}`."
        ),
        "project": workspace.project,
        "reason": scope.reason,
        "schema_version": SCHEMA_VERSION,
        "scope": scope.scope_text,
        "separation": {
            "confidence": str(scope.provenance.confidence),
            "entry_ids": list(scope.entry_ids),
            "evidence": list(scope.evidence),
            "method": str(scope.provenance.method),
            "note": scope.provenance.note,
            "separated": scope.separated,
        },
        "sport": sport,
        "tool_version": tool_version,
        "versions": [dict(sorted(version.items())) for version in versions],
    }
    path = workspace.runtime_history_file(sport)
    _write_text(path, canonical_dumps(document) + "\n")
    return path


# ---------------------------------------------------------------------------
# Reading it back
# ---------------------------------------------------------------------------


def read_history(workspace: Workspace) -> dict[str, Any]:
    if not workspace.history_file.is_file():
        raise WorkspaceError(
            f"no history for project {workspace.project!r}: "
            f"{workspace.history_file} does not exist. Run `metatron track` "
            f"first, or `metatron migrate` if this project predates the "
            f"workspace layout."
        )
    loaded = json.loads(workspace.history_file.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):  # pragma: no cover - a hand-edited file
        raise WorkspaceError(f"{workspace.history_file} is not a history document.")
    return loaded


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Stream a JSON Lines file. A blank line is skipped; a malformed one is an
    error naming the file and the line, never a silently shorter history."""
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise WorkspaceError(
                    f"{path}:{number} is not valid JSON ({exc.msg}). One line is "
                    f"one record; a history is never read past a line it cannot "
                    f"parse, because the result would be a shorter history that "
                    f"looks complete."
                ) from exc


def read_fingerprints(workspace: Workspace) -> Iterator[dict[str, Any]]:
    yield from _read_jsonl(workspace.fingerprints_file)


def read_comparisons(workspace: Workspace) -> Iterator[dict[str, Any]]:
    yield from _read_jsonl(workspace.comparisons_file)


def list_projects(root: Path) -> tuple[str, ...]:
    """Every project folder in the workspace that carries a history file."""
    if not root.is_dir():
        return ()
    found = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if (child / "history" / f"{child.name}_history.json").is_file():
            found.append(child.name)
    return tuple(found)


# ---------------------------------------------------------------------------
# One element's life -- the point of the whole design
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ElementLife:
    """One element across every version the workspace has kept.

    Answerable only because every version's fingerprints were kept, which is
    what the JSON Lines file buys.
    """

    element_id: str
    appeared_in: str
    appeared_label: str
    body_changes: tuple[dict[str, str], ...]
    reachability_changes: tuple[dict[str, str], ...]
    versions: tuple[dict[str, str], ...]
    first_observed_run: str
    first_observed_version: str
    absent_from: tuple[str, ...]
    note: str


def element_life(
    workspace: Workspace,
    element_id: str,
    *,
    history: Mapping[str, Any] | None = None,
) -> ElementLife:
    """When it appeared, every body change, every reachability flip, and which
    run first observed it executing.

    A version the element is absent from is listed rather than skipped: an
    element that vanished and came back is a different story from one that was
    there throughout, and a gap that is not shown reads as the second.
    """
    document = history if history is not None else read_history(workspace)
    ordered = [
        (str(v.get("id", "")), str(v.get("label", "")), int(v.get("ordinal", -1)))
        for v in document.get("versions", [])
    ]
    ordered.sort(key=lambda row: (row[2], row[0]))
    labels = {vid: label for vid, label, _ in ordered}
    observed = {
        str(vid): dict(sorted(runs.items()))
        for vid, runs in (document.get("observed_elements") or {}).items()
    }

    rows: dict[str, dict[str, Any]] = {}
    for row in read_fingerprints(workspace):
        if row.get("element_id") == element_id:
            rows[str(row.get("version_id", ""))] = row

    if not rows:
        raise WorkspaceError(
            f"no element {element_id!r} in any version of project "
            f"{workspace.project!r}. {workspace.fingerprints_file.name} holds "
            f"every element of every version, so this is an absence, not a "
            f"missing file."
        )

    present = [(vid, label) for vid, label, _ in ordered if vid in rows]
    absent = tuple(label for vid, label, _ in ordered if vid not in rows)
    appeared_id, appeared_label = present[0] if present else ("", "")

    body: list[dict[str, str]] = []
    reach: list[dict[str, str]] = []
    versions: list[dict[str, str]] = []
    previous: dict[str, Any] | None = None
    previous_label = ""
    for vid, label in present:
        row = rows[vid]
        versions.append(
            {
                "label": label,
                "normalized_body_hash": str(row.get("normalized_body_hash", "")),
                "reachability": str(row.get("reachability", "")),
                "signature": str(row.get("signature", "")),
                "version_id": vid,
            }
        )
        if previous is not None:
            if row.get("normalized_body_hash") != previous.get("normalized_body_hash"):
                body.append({"after": label, "before": previous_label, "version_id": vid})
            before_state = str(previous.get("reachability", ""))
            after_state = str(row.get("reachability", ""))
            if before_state != after_state:
                reach.append(
                    {
                        "after": after_state,
                        "before": before_state,
                        "label": label,
                        "version_id": vid,
                    }
                )
        previous = row
        previous_label = label

    first_run = ""
    first_version = ""
    for vid, _label in present:
        runs = observed.get(vid, {})
        for run_id in sorted(runs):
            if element_id in runs[run_id]:
                first_run, first_version = run_id, vid
                break
        if first_run:
            break

    return ElementLife(
        element_id=element_id,
        appeared_in=appeared_id,
        appeared_label=appeared_label,
        body_changes=tuple(body),
        reachability_changes=tuple(reach),
        versions=tuple(versions),
        first_observed_run=first_run,
        first_observed_version=first_version,
        absent_from=absent,
        note=(
            ""
            if first_run
            else "no Mode A run recorded this element executing. NOT MEASURED, "
            "never 'it did not run'."
        ),
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _bytes_text(count: int) -> str:
    if count < 1024:
        return f"{count:,} B"
    if count < 1024 * 1024:
        return f"{count / 1024:.1f} KB"
    return f"{count / (1024 * 1024):.1f} MB"


def render_history(
    document: Mapping[str, Any],
    *,
    workspace: Workspace,
    sport: str = "",
) -> str:
    """``metatron history <PROJECT>`` and ``--sport``."""
    versions = sorted(
        document.get("versions", []),
        key=lambda v: (int(v.get("ordinal", -1)), str(v.get("id", ""))),
    )
    lines = [
        f"{document.get('project', workspace.project)} — {len(versions):,} version(s), "
        f"{len(document.get('comparisons', [])):,} comparison(s)",
        f"  workspace   {workspace.project_dir}",
        f"  order       {document.get('order', {}).get('status', 'unknown')}"
        f" — {document.get('order', {}).get('reason', '') or 'no caveat recorded'}",
        "",
    ]
    disk = document.get("disk", {})
    lines += [
        f"Disk — {_bytes_text(int(disk.get('total_bytes', 0)))} over "
        f"{int(disk.get('total_files', 0)):,} file(s)",
        f"  sources   {_bytes_text(int(disk.get('sources_bytes', 0)))} in "
        f"{int(disk.get('sources_distinct', 0)):,} distinct snapshot(s) "
        f"({int(disk.get('sources_share_percent', 0))}% of the workspace)",
        f"  history   {_bytes_text(int(disk.get('history_bytes', 0)))}",
        f"  io        {_bytes_text(int(disk.get('io_bytes', 0)))}",
        "",
    ]
    for notice in document.get("layout_notices", []):
        lines += [f"NOTICE  {notice}", ""]

    if sport:
        return "\n".join(lines + _sport_lines(workspace, sport)).rstrip() + "\n"

    for version in versions:
        ordinal = int(version.get("ordinal", -1))
        counts = version.get("counts", {}) or {}
        lines += [
            f"{version.get('label', '')}  "
            + (f"#{ordinal}" if ordinal >= 0 else "#? (order not established)"),
            f"  id          {version.get('id', '')}",
            f"  version time {version.get('version_time', '') or '(none)'} "
            f"[{version.get('version_time_source', 'unknown')}]",
            f"  artifacts   {version.get('artifact_dir', '')}",
            "  counts      "
            + (", ".join(f"{k} {v:,}" for k, v in sorted(counts.items())) or "(none)"),
            "  Mode A runs "
            + (
                ", ".join(version.get("runtime_run_ids", []))
                or "none — runtime is not measured for this version"
            ),
            "",
        ]
    for comparison in document.get("comparisons", []):
        counts = comparison.get("change_counts", {}) or {}
        moving = {k: v for k, v in sorted(counts.items()) if v}
        lines += [
            f"{comparison.get('before_version_id', '')[:12]} -> "
            f"{comparison.get('after_version_id', '')[:12]}",
            "  changes     " + (", ".join(f"{k} {v:,}" for k, v in moving.items()) or "none"),
            f"  decision    {comparison.get('decision_paths_changed', 0):,} change(s) "
            f"move a path to a decision",
            "  runtime     "
            + (
                "measured"
                if comparison.get("runtime_measured")
                else "NOT MEASURED — no Mode A run of one scenario against both versions"
            ),
            "",
        ]
    sports = document.get("sports", [])
    if sports:
        lines += ["Per sport:"]
        for name in sports:
            lines.append(f"  {name}: " + _sport_headline(workspace, name))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _sport_document(workspace: Workspace, sport: str) -> dict[str, Any] | None:
    path = workspace.runtime_history_file(sport)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _sport_headline(workspace: Workspace, sport: str) -> str:
    document = _sport_document(workspace, sport)
    if document is None:
        return f"no file — {sport} has not been analysed in this workspace"
    return (
        f"scope {document.get('scope', '')}; "
        + ("measured" if document.get("measured") else "not measured")
    )


def _sport_lines(workspace: Workspace, sport: str) -> list[str]:
    document = _sport_document(workspace, sport)
    if document is None:
        return [
            f"No file for sport {sport!r} in {workspace.runtime_dir}.",
            "Nothing has been computed for this sport. That is an absence, not a "
            "finding of 'no change'.",
        ]
    separation = document.get("separation", {})
    lines = [
        f"scope:  {document.get('scope', '')}",
        f"reason: {document.get('reason', '')}",
        f"        method {separation.get('method', '')}, "
        f"confidence {separation.get('confidence', '')}",
        "",
    ]
    if document.get("scope") == UNION_SCOPE:
        lines += [
            "THIS FILE IS NOT SPORT-SPECIFIC. Order and reachability below are the "
            "union over every sport.",
            "",
        ]
    for version in document.get("versions", []):
        lines += [
            f"{version.get('label', '')}  {str(version.get('version_id', ''))[:12]}",
            f"  reaches sink  {len(version.get('reaches_sink_ids', [])):,}",
            f"  no sink path  {len(version.get('no_sink_path_ids', [])):,}",
            f"  unknown       {len(version.get('unknown_ids', [])):,}",
            f"  findings      {len(version.get('finding_ids', [])):,}",
            "  Mode A runs   "
            + (
                ", ".join(version.get("runs", []))
                or f"none — NOT MEASURED for {sport}, never 'no change'"
            ),
            "",
        ]
    if not document.get("versions"):
        lines.append(str(document.get("note", "")))
    return lines


def render_element_life(life: ElementLife, *, project: str) -> str:
    lines = [
        f"{life.element_id} — in project {project}",
        f"  appeared in   {life.appeared_label} ({life.appeared_in[:12]})",
        f"  present in    {len(life.versions):,} version(s)",
        "  absent from   "
        + (", ".join(life.absent_from) if life.absent_from else "no version"),
        "",
        "Body:",
    ]
    if life.body_changes:
        for change in life.body_changes:
            lines.append(f"  changed in {change['after']} (was {change['before']})")
    else:
        lines.append("  never changed after normalisation (comments and whitespace stripped)")
    lines += ["", "Decision reachability:"]
    if life.reachability_changes:
        for change in life.reachability_changes:
            lines.append(f"  {change['label']}: {change['before']} -> {change['after']}")
    else:
        state = life.versions[0]["reachability"] if life.versions else "UNKNOWN"
        lines.append(f"  {state} throughout — never flipped")
    lines += ["", "Observed executing:"]
    if life.first_observed_run:
        lines.append(
            f"  first seen in run {life.first_observed_run} "
            f"(version {life.first_observed_version[:12]})"
        )
    else:
        lines.append(f"  {life.note}")
    lines += ["", "Every version:"]
    for version in life.versions:
        lines.append(
            f"  {version['label']:<24} body {version['normalized_body_hash'][:12] or '(none)':<12} "
            f"{version['reachability']}"
        )
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """What moved, what was already there, and what was left alone.

    A second run does nothing and says so: every step below is keyed on a
    destination that either exists or does not, so idempotence is a property of
    the shape rather than a flag that has to be maintained.
    """

    project: str
    workspace: str
    ledger_source: str
    versions_moved: tuple[str, ...]
    versions_already_present: tuple[str, ...]
    artifacts_moved: tuple[str, ...]
    artifacts_already_present: tuple[str, ...]
    history_written: tuple[str, ...]
    reused_version_ids: tuple[str, ...]
    did_nothing: bool
    notes: tuple[str, ...]


def migrate(
    *,
    root: Path,
    workspace: Workspace,
    ledger_path: Path,
    versions_dir: Path | None = None,
    out_dir: Path | None = None,
    keep_sources: bool = True,
) -> MigrationResult:
    """Move a flat layout into the workspace shape.

    Reuses the version ids already computed -- they are content hashes, so
    re-deriving them would produce the same strings at the cost of re-reading
    every tree -- and nothing is re-analysed. Artifact directories are COPIED
    rather than moved, so a half-finished migration cannot destroy the only
    copy of a map.
    """
    notes: list[str] = []
    try:
        inside = ledger_path.resolve().is_relative_to(workspace.project_dir.resolve())
    except (OSError, ValueError):  # pragma: no cover - a path that cannot resolve
        inside = False
    if inside:
        # Migrating a workspace into itself would copy every artifact
        # directory a second time under a new name and double the disk use,
        # silently. `migrate` moves a FLAT layout in; it is not a repair tool.
        raise WorkspaceError(
            f"{ledger_path} is already inside the workspace at "
            f"{workspace.project_dir}. `migrate` moves an existing FLAT layout "
            f"into the workspace; pointing it at the workspace's own history "
            f"would copy every artifact a second time. Nothing was done."
        )
    if not ledger_path.is_file():
        raise WorkspaceError(
            f"no ledger at {ledger_path}. `migrate` moves an existing flat "
            f"layout into the workspace; there is nothing here to move. Run "
            f"`metatron track` to start a history instead."
        )
    document = json.loads(ledger_path.read_text(encoding="utf-8"))
    records = document.get("versions", [])
    reused = tuple(sorted(str(r.get("id", "")) for r in records if r.get("id")))

    moved: list[str] = []
    present: list[str] = []
    art_moved: list[str] = []
    art_present: list[str] = []

    for record in sorted(records, key=lambda r: str(r.get("id", ""))):
        version_id = str(record.get("id", ""))
        if not version_id:
            notes.append("a ledger row carries no id and was left where it is.")
            continue
        source = record.get("source_path", "")
        tree = (root / source) if source and not Path(source).is_absolute() else Path(source)
        if versions_dir is not None and not tree.is_dir():
            tree = versions_dir / str(record.get("label", ""))
        destination = workspace.source_dir(version_id)
        if destination.is_dir():
            present.append(version_id)
        elif not keep_sources:
            notes.append(
                f"--no-sources: {record.get('label', version_id)} kept its hash and "
                f"no copy was made."
            )
        elif tree.is_dir():
            result = store_source(tree, workspace, version_id, keep=True)
            (moved if result.stored else present).append(version_id)
        else:
            notes.append(
                f"the source tree for {record.get('label', version_id)} is gone "
                f"({tree}); its fingerprints carry the history and no snapshot "
                f"could be filed."
            )

        artifacts = record.get("artifact_dir", "")
        art_source = (
            (root / artifacts)
            if artifacts and not Path(artifacts).is_absolute()
            else Path(artifacts or "")
        )
        if out_dir is not None and not art_source.is_dir():
            art_source = out_dir / version_id
        art_destination = workspace.runs_dir / version_id
        if art_destination.is_dir():
            art_present.append(version_id)
        elif art_source.is_dir() and art_source.resolve() != art_destination.resolve():
            art_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(art_source, art_destination)
            art_moved.append(version_id)
        elif not art_source.is_dir():
            notes.append(
                f"no artifacts at {art_source} for "
                f"{record.get('label', version_id)}; a comparison against this "
                f"version will refuse until it is re-analysed."
            )

    written: list[str] = []
    if not workspace.history_file.is_file():
        # Copied in with every path REPOINTED at where the files now are. A
        # verbatim copy would leave the records pointing at the flat layout,
        # so the migrated workspace would only work while the old one survived
        # -- and the first thing an owner does after migrating is delete it.
        # Anything that did not actually arrive keeps its old path and is
        # named in the notes rather than repointed at nothing.
        carried = json.loads(ledger_path.read_text(encoding="utf-8"))
        repointed = 0
        for record in carried.get("versions", []):
            version_id = str(record.get("id", ""))
            source = workspace.source_dir(version_id)
            artifacts = workspace.runs_dir / version_id
            if source.is_dir():
                record["source_path"] = _relative(source, root)
                repointed += 1
            if artifacts.is_dir():
                record["artifact_dir"] = _relative(artifacts, root)
        _write_text(
            workspace.history_file,
            json.dumps(carried, indent=1, sort_keys=True) + "\n",
        )
        written.append(workspace.history_file.name)
        notes.append(
            f"the flat ledger was copied in with {repointed} version path(s) "
            f"repointed at the workspace, so the old layout can now be deleted. "
            f"The next `track` rewrites it in the workspace format without "
            f"re-analysing anything, because every version id is a content hash "
            f"that is already known."
        )

    did_nothing = not (moved or art_moved or written)
    if did_nothing:
        notes.append(
            "nothing to do: this workspace already holds every version and every "
            "artifact directory in the ledger. `migrate` is idempotent."
        )
    return MigrationResult(
        project=workspace.project,
        workspace=str(workspace.project_dir),
        ledger_source=str(ledger_path),
        versions_moved=tuple(sorted(moved)),
        versions_already_present=tuple(sorted(present)),
        artifacts_moved=tuple(sorted(art_moved)),
        artifacts_already_present=tuple(sorted(art_present)),
        history_written=tuple(written),
        reused_version_ids=reused,
        did_nothing=did_nothing,
        notes=tuple(notes),
    )


def render_migration(result: MigrationResult) -> str:
    lines = [
        f"migrate — project {result.project}",
        f"  from        {result.ledger_source}",
        f"  into        {result.workspace}",
        f"  version ids {len(result.reused_version_ids):,} reused, "
        f"0 recomputed — they are content hashes and nothing was re-analysed",
        "",
    ]
    if result.did_nothing:
        lines.append("NOTHING TO DO. This workspace is already in the new shape.")
    else:
        lines += [
            f"  sources    {len(result.versions_moved):,} filed, "
            f"{len(result.versions_already_present):,} already present",
            f"  artifacts  {len(result.artifacts_moved):,} copied, "
            f"{len(result.artifacts_already_present):,} already present",
            f"  history    "
            + (", ".join(result.history_written) or "already written"),
        ]
    if result.notes:
        lines += ["", "Notes — nothing here was skipped silently:"]
        lines += [f"  - {note}" for note in result.notes]
    return "\n".join(lines).rstrip() + "\n"
