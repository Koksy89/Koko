"""Card 18 -- METATRON_SETTINGS and the version ledger.

Drop each version of the engine into a folder; work out the order; run only
what has not already been run; keep one JSON history that can compare **every
single element** between versions without re-analysing an old one.

Three things this module exists to get right
--------------------------------------------

**1. Identity is the tree's content hash.** :func:`tree_hash` hashes every file
in a version directory (excluding VCS metadata and bytecode caches -- see
:data:`_EXCLUDED_DIRS`), and that hash *is* :attr:`VersionRecord.id`. Rename the
folder and it is the same version; change one byte and it is a new one.
:meth:`Ledger.analyse_new` skips any id already in the ledger without reading a
line of it, so a second ``track`` with nothing new does no analysis at all and
:func:`render_track_report` says so in those words.

**2. Ordering names its source, every time.** :attr:`VersionRecord.version_time`
is best effort and :attr:`version_time_source` says which signal produced it:
``owner_declared``, ``filename``, ``git_commit``, ``file_mtime_max``,
``directory_mtime`` or ``unknown``. Filesystem timestamps lie -- copying a tree
rewrites mtime, extracting an archive stamps every file with the moment it was
unpacked -- so :func:`assign_ordinals` refuses to interleave signals of
different trustworthiness, refuses to break a tie, and refuses to order
anything when any version has no signal at all. In every one of those cases
*all* ordinals are ``-1``, nothing is compared, and the report asks the owner
for ``ORDER``. A comparison run against the wrong "previous version" produces a
confident, detailed, completely wrong answer.

**3. Coverage is proved, not asserted.** :meth:`Ledger.compare` counts every
element of both versions and records how many landed in exactly one
classification. Anything that did not is NAMED in
:attr:`VersionComparison.unaccounted_element_ids` as ``before:<id>`` or
``after:<id>``, and a comparison naming any is reported as incomplete. An
element claimed by two classifications is as much a coverage failure as one
claimed by none, and both are named.

What is reused and what is new
------------------------------

The comparison itself is card 6's :func:`cascade_map.diff.diff_snapshots`,
unmodified. Nothing here re-implements matching, classification, impact or
ranking. Dependencies are card 17's, reached through card 10's ``analyze``.
This module adds identity, ordering, persistence and coverage accounting.

Why an old version is never re-read
-----------------------------------

The ledger stores an :class:`ElementFingerprint` per element per version,
including ``normalized_body_hash`` -- card 6's token-normalised body (comments
and whitespace stripped) hashed at the time the version was analysed. When two
versions are compared, :class:`_FingerprintedSnapshot` replays those hashes to
card 6's body comparison as opaque single-token bodies, so a reformat still
reads as ``UNCHANGED`` even if the owner has since deleted the source tree.
The artifact directories under ``OUT_DIR`` are still required -- they hold the
edges, slices and findings a diff needs -- and :meth:`Ledger.compare` refuses,
naming the directory, rather than diff against an empty graph and report every
element as ``REMOVED``.

What ``stage_seconds`` measures
-------------------------------

How long **this tool** took, per stage, per version. That is a real signal
about the target's size and shape and it is not a fact about the engine's
speed: nothing that refuses to execute code can time it. Engine performance
comes only from a Mode A run, which is what ``runtime_delta`` compares -- and
an empty ``runtime_delta`` means *not measured*, which the report prints in
those words and never as "no change".

Serialization
-------------

:func:`cascade_map.contracts.interfaces.canonical_dumps` rejects floats, and
``stage_seconds``/``total_seconds``/``analysis_seconds_delta`` are floats in the
contract. The ledger therefore uses :func:`ledger_dumps`, which is
``canonical_dumps`` plus an explicit float rule: every float is rounded to
three decimals at construction, so the bytes are stable for a given value. See
the card's report for the contract change this would otherwise want.
"""

from __future__ import annotations

import datetime
import difflib
import json
import re
import time
from dataclasses import dataclass, field, fields, is_dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence, get_args, get_origin

from cascade_map.contracts.interfaces import (
    SCHEMA_VERSION,
    Confidence,
    ElementFingerprint,
    ElementKind,
    Method,
    Provenance,
    ReachabilityState,
    SourceSpan,
    VersionChange,
    ChangeKind,
    VersionComparison,
    VersionRecord,
    combine,
)
from cascade_map.diff import GraphSnapshot, _normalized_body, diff_snapshots, load_snapshot
from cascade_map.ingest.hashing import sha256_hex, sha256_text

__all__ = [
    "SETTING_DEFAULTS",
    "Settings",
    "SettingsError",
    "LedgerError",
    "Ledger",
    "TrackResult",
    "track",
    "render_track_report",
    "render_history_report",
    "tree_hash",
    "ROOT_TOKEN",
    "reroot_id",
    "qualify_id",
    "version_time_of",
    "assign_ordinals",
    "ledger_dumps",
]

TOOL_VERSION_UNKNOWN = "unknown"

#: Directories that are not part of a version's identity. VCS metadata churns
#: on every fetch and bytecode caches are generated, so including either would
#: make an untouched tree look like a new version.
_EXCLUDED_DIRS = frozenset({".git", "__pycache__", ".pytest_cache", ".mypy_cache"})


class SettingsError(ValueError):
    """A METATRON_SETTINGS key that is not a setting, or a value of the wrong
    shape. Never a silent no-op: a misspelled ``"SINK"`` that does nothing is
    how an owner ends up trusting a map built without the setting they thought
    they had applied."""


class LedgerError(RuntimeError):
    """The ledger cannot do what was asked and will not guess instead."""


# ---------------------------------------------------------------------------
# Settings -- data, never code
# ---------------------------------------------------------------------------

#: The canonical settings. ``cli.METATRON_SETTINGS`` is the owner's editable
#: copy and a test asserts the two have exactly the same keys, so a key added
#: in one place cannot go missing in the other.
SETTING_DEFAULTS: dict[str, Any] = {
    "MODE": 1,
    "VERSIONS_DIR": "versions",
    "OUT_DIR": "out",
    "LEDGER": "out/metatron_ledger.json",
    "SINKS": [],
    "ENTRIES": [],
    "CONFIGS": [],
    "ENV": ".venv-target",
    "ORDER": [],
    "SCENARIOS": "scenarios.json",
    "SCENARIO": "baseline",
}

_KEY_TO_FIELD: dict[str, str] = {
    "MODE": "mode",
    "VERSIONS_DIR": "versions_dir",
    "OUT_DIR": "out_dir",
    "LEDGER": "ledger",
    "SINKS": "sinks",
    "ENTRIES": "entries",
    "CONFIGS": "configs",
    "ENV": "env",
    "ORDER": "order",
    "SCENARIOS": "scenarios",
    "SCENARIO": "scenario",
}

_LIST_KEYS = frozenset({"SINKS", "ENTRIES", "CONFIGS", "ORDER"})


@dataclass(frozen=True, slots=True)
class Settings:
    """A validated METATRON_SETTINGS. Built only by :meth:`from_mapping`."""

    mode: int = 1
    versions_dir: str = "versions"
    out_dir: str = "out"
    ledger: str = "out/metatron_ledger.json"
    sinks: tuple[str, ...] = ()
    entries: tuple[str, ...] = ()
    configs: tuple[str, ...] = ()
    env: str = ".venv-target"
    order: tuple[str, ...] = ()
    scenarios: str = "scenarios.json"
    scenario: str = "baseline"

    @staticmethod
    def from_mapping(mapping: Mapping[str, Any]) -> "Settings":
        """Validate and convert. Raises :class:`SettingsError` on an unknown
        key -- naming the typo and the nearest real key -- or a value of the
        wrong shape. A missing key takes its default; the shipped dict is
        checked against :data:`SETTING_DEFAULTS` by a test, so a *missing* key
        is an owner deleting a line, not drift."""
        values: dict[str, Any] = {}
        for key in sorted(mapping):
            if key not in _KEY_TO_FIELD:
                close = difflib.get_close_matches(key, sorted(SETTING_DEFAULTS), n=1)
                suggestion = f' Did you mean "{close[0]}"?' if close else ""
                raise SettingsError(
                    f'unknown setting "{key}".{suggestion} '
                    f"Valid settings: {', '.join(sorted(SETTING_DEFAULTS))}. "
                    f"An unknown key is an error, never a silent no-op."
                )
            raw = mapping[key]
            if key == "MODE":
                if isinstance(raw, bool) or not isinstance(raw, int) or raw not in (1, 2):
                    raise SettingsError(
                        f'MODE must be 1 (static only) or 2 (static + runtime tracing), '
                        f"not {raw!r}."
                    )
                values["mode"] = int(raw)
            elif key in _LIST_KEYS:
                if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
                    raise SettingsError(
                        f'{key} must be a list of strings, not {type(raw).__name__}. '
                        f'A bare string here would silently become a list of its '
                        f"characters."
                    )
                if any(not isinstance(item, str) for item in raw):
                    raise SettingsError(f"{key} must contain only strings: {list(raw)!r}")
                values[_KEY_TO_FIELD[key]] = tuple(raw)
            else:
                if not isinstance(raw, str):
                    raise SettingsError(
                        f"{key} must be a string path, not {type(raw).__name__}."
                    )
                values[_KEY_TO_FIELD[key]] = raw
        return Settings(**values)


# ---------------------------------------------------------------------------
# Serialization -- canonical_dumps plus an explicit float rule
# ---------------------------------------------------------------------------

_SECONDS_PLACES = 3


def _round_seconds(value: float) -> float:
    return round(float(value), _SECONDS_PLACES)


def ledger_dumps(payload: Any) -> str:
    """Canonical JSON for the ledger: sorted keys, ASCII, no spare whitespace.

    Identical to :func:`canonical_dumps` except that floats are permitted,
    because the contract types the analysis timings as floats. Every float
    reaching here has already been rounded by :func:`_round_seconds`, so two
    runs over the same measurements produce the same bytes.
    """
    return json.dumps(
        _to_jsonable(payload),
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )


def _to_jsonable(node: Any) -> Any:
    if isinstance(node, StrEnum):
        return str(node)
    if isinstance(node, float):
        return _round_seconds(node)
    if is_dataclass(node) and not isinstance(node, type):
        return {f.name: _to_jsonable(getattr(node, f.name)) for f in fields(node)}
    if isinstance(node, Mapping):
        return {str(key): _to_jsonable(value) for key, value in node.items()}
    if isinstance(node, (set, frozenset)):
        return [_to_jsonable(value) for value in sorted(node)]
    if isinstance(node, (list, tuple)):
        return [_to_jsonable(value) for value in node]
    return node


def _from_jsonable(cls: type, payload: Mapping[str, Any]) -> Any:
    """Rebuild one contract dataclass from a parsed ledger row.

    Walks the dataclass's own fields rather than describing the schema a
    second time, and coerces ``StrEnum`` fields explicitly so a round trip
    gives back the enum member, not the bare string it serialises to.
    """
    kwargs: dict[str, Any] = {}
    for spec in fields(cls):
        if spec.name not in payload:
            continue
        kwargs[spec.name] = _coerce(spec.type, payload[spec.name])
    return cls(**kwargs)


def _coerce(hint: Any, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(hint, str):
        hint = _RESOLVED_HINTS.get(hint, hint)
    if isinstance(hint, type) and issubclass(hint, StrEnum):
        return hint(value)
    if isinstance(hint, type) and is_dataclass(hint) and isinstance(value, Mapping):
        return _from_jsonable(hint, value)
    origin = get_origin(hint)
    args = get_args(hint)
    if origin is tuple:
        inner = args[0] if args else Any
        return tuple(_coerce(inner, item) for item in value)
    if origin is dict:
        return dict(value)
    if args:  # optional / union, e.g. `SourceSpan | None`
        for candidate in args:
            if candidate is type(None):
                continue
            return _coerce(candidate, value)
    return value


#: `from __future__ import annotations` makes every field type a string, so the
#: rebuilder needs the handful of contract types it can meet by name.
_RESOLVED_HINTS: dict[str, Any] = {
    "ElementKind": ElementKind,
    "ReachabilityState": ReachabilityState,
    "Confidence": Confidence,
    "Method": Method,
    "ChangeKind": ChangeKind,
    "SourceSpan": SourceSpan,
    "SourceSpan | None": SourceSpan,
    "Provenance": Provenance,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "tuple[str, ...]": tuple[str, ...],
    "dict[str, int]": dict[str, int],
    "dict[str, float]": dict[str, float],
    "dict[str, Any]": dict[str, Any],
}


# ---------------------------------------------------------------------------
# Identity: the content hash of a tree
# ---------------------------------------------------------------------------


def _tree_files(root: Path) -> list[Path]:
    """Every file that is part of a version's identity, sorted. Read as bytes
    only; nothing under *root* is imported, parsed or executed here."""
    out: list[Path] = []
    for path in sorted(root.rglob("*")):
        if any(part in _EXCLUDED_DIRS for part in path.relative_to(root).parts):
            continue
        if path.is_file() and not path.is_symlink():
            out.append(path)
    return out


def tree_hash(root: str | Path) -> str:
    """The content hash of a whole tree: path and bytes of every file.

    Paths are included, so moving a file to a new name changes the hash even
    when the bytes are unchanged. POSIX separators, sorted, so the same tree
    on two machines hashes the same.
    """
    base = Path(root)
    digest_input: list[str] = []
    for path in _tree_files(base):
        rel = path.relative_to(base).as_posix()
        digest_input.append(f"{rel}\0{sha256_hex(path.read_bytes())}")
    return sha256_text("\n".join(digest_input))


# ---------------------------------------------------------------------------
# Re-rooting: making an element id mean the same thing in two versions
# ---------------------------------------------------------------------------
#
# Card 1 makes the analysed directory's own basename the first component of
# every dotted name, by design: point the tool at `target_engine/` and every
# module is `target_engine.something`. For a single map that is right. For a
# *history* it is fatal, because each version lives in a differently named
# folder -- `amun_2026-01-14/`, `amun_v3/` -- so the same function has a
# different id in every version and a diff reports the entire engine as removed
# and re-added. Measured on the corpus before this existed: 8 ADDED and 8
# REMOVED out of 9 elements for two trees differing by two lines.
#
# `FIXTURES.md`'s card 6 cases state the rule this implements:
# "<module>::<qualname>, where <module> is the dotted path relative to the
# version root. A diff comparing two roots must strip the root component before
# matching."
#
# So the ledger strips it. The rewrite is total and mechanical -- one leading
# component, only when it is exactly the version folder's name -- and it is
# applied to the comparison view and to the stored fingerprints, never to the
# artifacts on disk, which keep card 1's ids untouched. To look an id up in
# `elements.jsonl`, put the version's folder name and a dot back on the front.

#: What the version's own root directory element becomes. Not a legal dotted
#: name, so it cannot collide with anything the target defines.
ROOT_TOKEN = "<root>"


def reroot_id(element_id: str, prefix: str) -> str:
    """Strip the version folder's component from one id. Ids that do not carry
    it -- data files and config keys, which are keyed by root-relative path --
    are returned unchanged."""
    if not prefix or not element_id:
        return element_id
    if element_id == prefix:
        return ROOT_TOKEN
    head = f"{prefix}."
    return element_id[len(head):] if element_id.startswith(head) else element_id


def qualify_id(stable_id: str, prefix: str) -> str:
    """The inverse of :func:`reroot_id`: a version-independent id as that one
    version's analysis knows it.

    SINKS and ENTRIES are written once in METATRON_SETTINGS and have to mean
    the same element in every version, so they are qualified into each
    version's own id space before analysis. An id that already carries the
    prefix is left alone, so both forms work in the settings file.
    """
    if not prefix or not stable_id:
        return stable_id
    if stable_id == ROOT_TOKEN:
        return prefix
    return stable_id if stable_id.startswith(f"{prefix}.") else f"{prefix}.{stable_id}"


def _reroot_snapshot(snapshot: GraphSnapshot, prefix: str) -> GraphSnapshot:
    """The same graph with version-independent element ids.

    Record ids (`Edge.id`, `Finding.id`, `Slice.id`) are left alone on purpose:
    they are how the owner looks a row up in that version's own artifacts, and
    nothing in the comparison matches on them.
    """
    def rid(value: str) -> str:
        return reroot_id(value, prefix)

    elements = tuple(
        replace(
            element,
            id=rid(element.id),
            module=rid(element.module),
            parent_id=rid(element.parent_id),
        )
        for element in snapshot.elements
    )
    edges = tuple(
        replace(edge, source_id=rid(edge.source_id), target_id=rid(edge.target_id))
        for edge in snapshot.edges
    )
    lineage = tuple(
        replace(item, source_id=rid(item.source_id), target_id=rid(item.target_id))
        for item in snapshot.lineage_edges
    )
    slices = tuple(
        replace(
            item,
            root_id=rid(item.root_id),
            member_ids=tuple(rid(m) for m in item.member_ids),
            reaches_sink_ids=tuple(rid(m) for m in item.reaches_sink_ids),
        )
        for item in snapshot.slices
    )
    findings = tuple(
        replace(item, element_id=rid(item.element_id)) for item in snapshot.findings
    )
    return GraphSnapshot(
        label=snapshot.label,
        elements=elements,
        edges=edges,
        lineage_edges=lineage,
        slices=slices,
        findings=findings,
        source_root=snapshot.source_root,
    )

# ---------------------------------------------------------------------------
# Ordering, and the honest bit about timestamps
# ---------------------------------------------------------------------------

#: A date, and optionally a time, written into a folder name on purpose.
#: `amun_2026-02-03`, `amun_20260203`, `v2_2026-02-03T1130`.
_DATE_IN_NAME = re.compile(
    r"(?<!\d)(\d{4})[-_.]?(\d{2})[-_.]?(\d{2})"
    r"(?:[T_ -]?(\d{2})[-:.]?(\d{2})(?:[-:.]?(\d{2}))?)?(?!\d)"
)

#: A git reflog line ends `<unix seconds> <tz>\t<action>`.
_REFLOG_TIME = re.compile(r" (\d{9,11}) ([+-]\d{4})\t")

_ISO = "%Y-%m-%dT%H:%M:%SZ"

#: Signals a human or a commit wrote on purpose.
TRUSTED_TIME_SOURCES = frozenset({"owner_declared", "filename", "git_commit"})
#: Signals the filesystem invented. Copying a tree rewrites both.
WEAK_TIME_SOURCES = frozenset({"file_mtime_max", "directory_mtime"})

TIME_SOURCE_PHRASE: dict[str, str] = {
    "owner_declared": "order declared in ORDER",
    "filename": "time from folder name",
    "git_commit": "time from the git reflog",
    "file_mtime_max": "time from newest file mtime -- filesystem timestamps lie",
    "directory_mtime": "time from directory mtime -- filesystem timestamps lie",
    "unknown": "NO TIME SIGNAL AT ALL",
}

TIME_SOURCE_CONFIDENCE: dict[str, Confidence] = {
    "owner_declared": Confidence.CERTAIN,
    "filename": Confidence.PROBABLE,
    "git_commit": Confidence.PROBABLE,
    "file_mtime_max": Confidence.HEURISTIC,
    "directory_mtime": Confidence.HEURISTIC,
    "unknown": Confidence.UNKNOWN,
}

TIME_SOURCE_METHOD: dict[str, Method] = {
    # No `Method` member describes "read off the filesystem" -- see the card's
    # contract change request. `NAME_HEURISTIC` is exact for a date parsed out
    # of a folder name; `CONFIG_STRING_MATCH` is exact for a declared ORDER;
    # the rest carry AST_DIRECT with a note saying what was actually read, and
    # a confidence that never claims more than a timestamp is worth.
    "owner_declared": Method.CONFIG_STRING_MATCH,
    "filename": Method.NAME_HEURISTIC,
    "git_commit": Method.AST_DIRECT,
    "file_mtime_max": Method.AST_DIRECT,
    "directory_mtime": Method.AST_DIRECT,
    "unknown": Method.AST_DIRECT,
}


def _utc(seconds: float) -> str:
    return time.strftime(_ISO, time.gmtime(seconds))


def _time_from_name(name: str) -> str | None:
    """A date parsed out of a folder name, or None. Often the only honest
    signal, because a human wrote it there deliberately."""
    for match in _DATE_IN_NAME.finditer(name):
        year, month, day, hour, minute, second = match.groups()
        try:
            moment = datetime.datetime(
                int(year), int(month), int(day),
                int(hour or 0), int(minute or 0), int(second or 0),
                tzinfo=datetime.timezone.utc,
            )
        except ValueError:
            continue  # `build_12345678` is not 1234-56-78
        return moment.strftime(_ISO)
    return None


def _time_from_git(root: Path) -> str | None:
    """The last commit time, read as TEXT from ``.git/logs/HEAD``.

    ``git`` is never invoked inside a version tree. A repository's own config
    can make git execute commands (``core.fsmonitor``, ``core.pager``,
    aliases, ``include.path``), and running it on an unvetted tree would be a
    way for that tree to execute code -- which constraint 1 forbids outright.
    The reflog is plain text and carries the committer timestamp, so it costs
    nothing to read directly. A repository with no reflog (a fresh clone with
    ``--no-checkout``, a worktree file, a packed-only history) simply yields no
    git signal and the next signal down is used.
    """
    reflog = root / ".git" / "logs" / "HEAD"
    if not reflog.is_file():
        return None
    try:
        text = reflog.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    last: str | None = None
    for line in text.splitlines():
        match = _REFLOG_TIME.search(line)
        if match:
            last = match.group(1)
    if last is None:
        return None
    return _utc(int(last))


def _time_from_mtimes(root: Path) -> tuple[str, str]:
    newest = 0.0
    for path in _tree_files(root):
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    if newest:
        return _utc(newest), "file_mtime_max"
    try:
        return _utc(root.stat().st_mtime), "directory_mtime"
    except OSError:
        return "", "unknown"


def version_time_of(root: str | Path) -> tuple[str, str]:
    """``(version_time, version_time_source)`` for one version directory.

    Tried in descending order of honesty: a date written into the folder name,
    a git commit date, the newest file mtime, the directory mtime. The source
    travels with the answer because the last two are worth much less than the
    first two and the owner has to be able to tell.
    """
    path = Path(root)
    from_name = _time_from_name(path.name)
    if from_name is not None:
        return from_name, "filename"
    from_git = _time_from_git(path)
    if from_git is not None:
        return from_git, "git_commit"
    return _time_from_mtimes(path)


def assign_ordinals(
    records: Sequence[VersionRecord], order: Sequence[str] = ()
) -> tuple[list[VersionRecord], str, str]:
    """Position every version in the history, or refuse to and say why.

    Returns ``(records, status, reason)``. ``status`` is one of
    ``owner_declared``, ``trusted``, ``filesystem_timestamps`` or
    ``unestablished``; ``reason`` is empty unless something needs saying.

    The refusals are the point. Ordering is declined -- every ordinal ``-1`` --
    when any version has no time signal, when trusted and filesystem signals
    would have to be compared against each other, or when two versions share a
    timestamp. Three trees unzipped in one sitting have the same mtime to the
    second; ordering them by it would be a guess dressed as a fact, and a
    comparison against the wrong predecessor is worse than no comparison.
    """
    if not records:
        return [], "unestablished", "no versions"

    by_label = {r.label: r for r in records}
    if order:
        unknown = [label for label in order if label not in by_label]
        if unknown:
            raise SettingsError(
                f"ORDER names {unknown!r}, which is not a known version. Known: "
                f"{sorted(by_label)}. A name that matches nothing is a typo, not "
                f"an instruction."
            )
        missing = sorted(set(by_label) - set(order))
        placed: list[VersionRecord] = []
        for index, label in enumerate(order):
            placed.append(_with_declared_order(by_label[label], index))
        for label in missing:
            placed.append(replace(by_label[label], ordinal=-1))
        reason = (
            ""
            if not missing
            else (
                f"ORDER does not mention {missing!r}; those versions keep ordinal "
                f"-1 and are not compared. Add them to ORDER to place them."
            )
        )
        return _sorted_records(placed), "owner_declared", reason

    sources = {r.version_time_source for r in records}
    times = [r.version_time for r in records]

    if len(records) == 1:
        return [replace(records[0], ordinal=0)], "single", (
            "one version known, so there is nothing to order it against."
        )
    if "unknown" in sources or any(not t for t in times):
        blind = sorted(r.label for r in records if r.version_time_source == "unknown")
        return (
            _sorted_records([replace(r, ordinal=-1) for r in records]),
            "unestablished",
            f"no time signal at all for {blind!r}.",
        )
    if len(times) != len(set(times)):
        tied = sorted({t for t in times if times.count(t) > 1})
        labels = sorted(r.label for r in records if r.version_time in tied)
        return (
            _sorted_records([replace(r, ordinal=-1) for r in records]),
            "unestablished",
            f"{labels!r} share a timestamp ({tied!r}), so their relative order is "
            f"not established by anything on disk.",
        )
    if sources <= TRUSTED_TIME_SOURCES:
        status = "trusted"
        reason = ""
    elif sources <= WEAK_TIME_SOURCES:
        status = "filesystem_timestamps"
        reason = (
            "this order rests only on filesystem timestamps, which copying a "
            "tree or extracting an archive rewrites. Set ORDER to make it a fact."
        )
    else:
        weak = sorted(r.label for r in records if r.version_time_source in WEAK_TIME_SOURCES)
        return (
            _sorted_records([replace(r, ordinal=-1) for r in records]),
            "unestablished",
            f"some versions are dated by a name or a commit and {weak!r} only by a "
            f"filesystem timestamp; comparing the two kinds against each other "
            f"would be a guess.",
        )

    ranked = sorted(records, key=lambda r: (r.version_time, r.id))
    placed = [replace(record, ordinal=index) for index, record in enumerate(ranked)]
    return _sorted_records(placed), status, reason


def _with_declared_order(record: VersionRecord, index: int) -> VersionRecord:
    note = (
        f"ordinal declared in ORDER; the time shown came from "
        f"{record.version_time_source or 'unknown'}"
    )
    return replace(
        record,
        ordinal=index,
        version_time_source="owner_declared",
        provenance=Provenance(
            method=TIME_SOURCE_METHOD["owner_declared"],
            confidence=TIME_SOURCE_CONFIDENCE["owner_declared"],
            note=note,
        ),
    )


def _sorted_records(records: Iterable[VersionRecord]) -> list[VersionRecord]:
    return sorted(records, key=lambda r: r.id)


# ---------------------------------------------------------------------------
# A snapshot that reads bodies from the ledger, not from the owner's tree
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _FingerprintedSnapshot(GraphSnapshot):
    """Card 6's snapshot with its body source served from stored fingerprints.

    Card 6 compares bodies by tokenising the element's source and comparing the
    token sequences. It only ever asks whether two of those sequences are
    *equal*, so replaying a stored ``normalized_body_hash`` as a single
    identifier token gives exactly the same answers without the source tree
    having to exist -- which is what makes "never re-read an old version" true.
    An element with no stored hash returns None, so card 6 falls back to
    ``content_hash`` and records the lower confidence, rather than two unknown
    bodies comparing equal and a change being claimed UNCHANGED.
    """

    normalized_bodies: Mapping[str, str] = field(default_factory=dict)

    def read_span(self, element: Any) -> str | None:
        digest = self.normalized_bodies.get(element.id, "")
        return f"h{digest}" if digest else None


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------

AnalyseFn = Callable[[Path, Path, Settings], dict[str, Any]]
TraceFn = Callable[[Path, Path, str, Path], tuple[int, str]]


def _default_analyse(source_root: Path, out_dir: Path, settings: Settings) -> dict[str, Any]:
    """Card 10's static pipeline, which is cards 1-5, 16 and 17.

    Imported inside the function: ``cli`` sits above this module in the
    amalgamator's order, and the single-file build drops internal imports and
    resolves the name against the one global namespace.
    """
    from cascade_map.cli import analyze

    env = Path(settings.env) if settings.env else None
    prefix = source_root.name
    code, summary = analyze(
        source_root,
        out_dir,
        entry_ids=tuple(qualify_id(i, prefix) for i in settings.entries),
        sink_ids=tuple(qualify_id(i, prefix) for i in settings.sinks),
        config_paths=settings.configs,
        strict_gate=False,
        env_root=env,
    )
    summary["exit_code"] = code
    return summary


class Ledger:
    """Implements :class:`cascade_map.contracts.interfaces.LedgerCard`.

    Holds versions, fingerprints and comparisons; reads and writes one JSON
    file. ``analyse_new`` is a no-op for any version already present, keyed on
    the tree's content hash -- re-running an unchanged version is the one thing
    this card exists to avoid.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        root: str | Path | None = None,
        analyse: AnalyseFn | None = None,
        trace: TraceFn | None = None,
        tool_version: str | None = None,
    ) -> None:
        self.settings = settings if settings is not None else Settings()
        self.root = Path(root) if root is not None else Path.cwd()
        self._analyse: AnalyseFn = analyse if analyse is not None else _default_analyse
        self._trace = trace
        self.tool_version = tool_version if tool_version is not None else _tool_version()
        self._versions: dict[str, VersionRecord] = {}
        self._fingerprints: dict[str, tuple[ElementFingerprint, ...]] = {}
        self._comparisons: dict[str, VersionComparison] = {}
        #: version id -> wall-clock seconds this tool spent analysing it. A
        #: fact about Metatron, never about the engine's speed.
        self.analysis_seconds: dict[str, float] = {}
        self.order_status: str = "unestablished"
        self.order_reason: str = "nothing discovered yet"
        #: label -> the label it duplicates, for trees with identical content.
        self.duplicate_labels: dict[str, str] = {}

    # -- paths ------------------------------------------------------------

    def resolve(self, relative: str | Path) -> Path:
        path = Path(relative)
        return path if path.is_absolute() else self.root / path

    def artifact_dir(self, version_id: str) -> Path:
        return self.resolve(self.settings.out_dir) / version_id

    def relative(self, path: Path) -> str:
        """A path as the ledger stores it: relative to the ledger's root when
        it lies under it, absolute otherwise. Storing absolute paths would tie
        a ledger to one machine and one checkout directory."""
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()

    def knows(self, version_id: str) -> bool:
        """True when this version has already been analysed. The whole point:
        a known id is never analysed again."""
        return version_id in self._versions

    def record(self, version_id: str) -> VersionRecord:
        if version_id not in self._versions:
            raise LedgerError(f"no version {version_id} in this ledger.")
        return self._versions[version_id]

    def has_comparison(self, before_id: str, after_id: str) -> bool:
        return _comparison_id(before_id, after_id) in self._comparisons

    # -- persistence ------------------------------------------------------

    def load(self, path: str | Path) -> "Ledger":
        """Read an existing ledger. A missing file is an empty ledger, which
        is what the first run legitimately has."""
        file = self.resolve(path)
        if not file.exists():
            return self
        document = json.loads(file.read_text(encoding="utf-8"))
        stored = document.get("schema_version", "")
        if stored and stored != SCHEMA_VERSION:
            raise LedgerError(
                f"{file} was written against schema {stored}; this tool speaks "
                f"{SCHEMA_VERSION}. Re-analyse rather than mix two schemas: the "
                f"fields a comparison reads may have changed meaning."
            )
        for row in document.get("versions", []):
            record = _from_jsonable(VersionRecord, row)
            self._versions[record.id] = record
        grouped: dict[str, list[ElementFingerprint]] = {}
        for row in document.get("fingerprints", []):
            fingerprint = _from_jsonable(ElementFingerprint, row)
            grouped.setdefault(fingerprint.version_id, []).append(fingerprint)
        for version_id, items in grouped.items():
            self._fingerprints[version_id] = tuple(sorted(items, key=lambda f: f.id))
        for row in document.get("comparisons", []):
            comparison = _from_jsonable(VersionComparison, row)
            self._comparisons[comparison.id] = comparison
        return self

    def save(self, path: str | Path) -> None:
        """Write the ledger. One record per line inside the JSON envelope:
        readable in a diff, compact in a big history, and byte-identical for
        identical input because every list is sorted and every record goes
        through :func:`ledger_dumps`."""
        file = self.resolve(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(self.to_json(), encoding="utf-8", newline="\n")

    def to_json(self) -> str:
        sections = {
            "comparisons": [self._comparisons[k] for k in sorted(self._comparisons)],
            "fingerprints": [
                fingerprint
                for version_id in sorted(self._fingerprints)
                for fingerprint in self._fingerprints[version_id]
            ],
            "versions": [self._versions[k] for k in sorted(self._versions)],
        }
        scalars = {
            "schema_version": SCHEMA_VERSION,
            "tool_version": self.tool_version,
        }
        keys = sorted([*sections, *scalars])
        lines = ["{"]
        for index, name in enumerate(keys):
            tail = "," if index < len(keys) - 1 else ""
            if name in scalars:
                lines.append(f'"{name}":{json.dumps(scalars[name])}{tail}')
                continue
            rows = [ledger_dumps(record) for record in sections[name]]
            body = ",\n".join(rows)
            lines.append(f'"{name}":[' + ("\n" + body + "\n" if rows else "") + f"]{tail}")
        lines.append("}")
        return "\n".join(lines) + "\n"

    # -- LedgerCard -------------------------------------------------------

    def discover(self, versions_dir: str) -> Sequence[VersionRecord]:
        """Every sub-directory of *versions_dir* is a version, identified by
        its content hash. Two folders with identical content are one version;
        the second is recorded as a duplicate and never analysed twice."""
        base = self.resolve(versions_dir)
        if not base.is_dir():
            raise LedgerError(
                f"VERSIONS_DIR {base} is not a directory. Create it and put one "
                f"sub-folder per version in it, or set VERSIONS_DIR."
            )
        self.duplicate_labels = {}
        found: dict[str, VersionRecord] = {}
        for child in sorted(base.iterdir(), key=lambda p: p.name):
            if not child.is_dir() or child.name.startswith(".") or child.name in _EXCLUDED_DIRS:
                continue
            digest = tree_hash(child)
            if digest in found:
                self.duplicate_labels[child.name] = found[digest].label
                continue
            found[digest] = self._record_for(digest, child)
        return _sorted_records(found.values())

    def analyse_new(self, discovered: Sequence[VersionRecord]) -> Sequence[VersionRecord]:
        """Analyse only versions the ledger has never seen.

        A version already present is skipped without its tree being read. The
        records returned are the ones actually analysed -- an empty sequence is
        the honest answer to "a second run with nothing new", and the report
        prints it as such.
        """
        analysed: list[VersionRecord] = []
        for record in _sorted_records(discovered):
            if record.id in self._versions:
                # Never re-analysed. Refresh only the facts that describe where
                # it lives now, so a renamed folder stays the same version.
                stored = self._versions[record.id]
                self._versions[record.id] = replace(
                    stored,
                    label=record.label,
                    source_path=record.source_path,
                    version_time=record.version_time,
                    version_time_source=record.version_time_source,
                    provenance=record.provenance,
                )
                continue
            started = time.time()
            self._versions[record.id] = self._analyse_one(record)
            self.analysis_seconds[record.id] = _round_seconds(time.time() - started)
            analysed.append(self._versions[record.id])
        return _sorted_records(analysed)

    def fingerprints(self, version_id: str) -> Sequence[ElementFingerprint]:
        if version_id not in self._versions:
            raise LedgerError(f"no version {version_id} in this ledger.")
        return self._fingerprints.get(version_id, ())

    def compare(self, before_id: str, after_id: str) -> VersionComparison:
        """Compare two analysed versions element by element, with coverage
        proved. Cached: a pair already in the ledger is returned, not redone."""
        key = _comparison_id(before_id, after_id)
        if key in self._comparisons:
            return self._comparisons[key]
        for version_id in (before_id, after_id):
            if version_id not in self._versions:
                raise LedgerError(f"no version {version_id} in this ledger.")
        before = self._snapshot(before_id)
        after = self._snapshot(after_id)
        changes, impacts = diff_snapshots(before, after)

        accounted, unaccounted = _coverage(before, after, changes)
        counts = {kind.value: 0 for kind in ChangeKind}
        for change in changes:
            counts[change.kind.value] += 1

        match_map = {
            change.before_id: change.after_id
            for change in changes
            if change.before_id and change.after_id
        }
        findings_added, findings_removed = _finding_deltas(before, after, match_map)

        before_record = self._versions[before_id]
        after_record = self._versions[after_id]
        stages = sorted(set(before_record.stage_seconds) | set(after_record.stage_seconds))
        seconds_delta = {
            stage: _round_seconds(
                after_record.stage_seconds.get(stage, 0.0)
                - before_record.stage_seconds.get(stage, 0.0)
            )
            for stage in stages
        }

        ambiguous = counts[ChangeKind.AMBIGUOUS.value]
        note = (
            f"card 6 diff over stored artifacts; bodies from ledger fingerprints. "
            f"{ambiguous} ambiguous match(es) claim nothing. "
            f"{len(unaccounted)} element(s) unaccounted for."
        )
        comparison = VersionComparison(
            id=key,
            before_version_id=before_id,
            after_version_id=after_id,
            change_counts=counts,
            change_ids=tuple(sorted(change.id for change in changes)),
            impact_ids=tuple(sorted(impact.id for impact in impacts)),
            elements_before=len(before.elements),
            elements_after=len(after.elements),
            elements_accounted_for=accounted,
            unaccounted_element_ids=unaccounted,
            decision_paths_changed=_decision_path_changes(changes, impacts, before, after),
            reachability_flipped=tuple(
                sorted({flip for i in impacts for flip in i.reachability_flipped})
            ),
            findings_added=findings_added,
            findings_removed=findings_removed,
            analysis_seconds_delta=seconds_delta,
            runtime_delta=self._runtime_delta(before_record, after_record, match_map),
            provenance=Provenance(
                method=Method.STRUCTURAL_MATCH,
                confidence=combine(*(change.provenance.confidence for change in changes)),
                note=note,
            ),
        )
        self._comparisons[key] = comparison
        return comparison

    # -- internals --------------------------------------------------------

    def _record_for(self, digest: str, source: Path) -> VersionRecord:
        version_time, source_kind = version_time_of(source)
        note = TIME_SOURCE_PHRASE[source_kind]
        return VersionRecord(
            id=digest,
            label=source.name,
            source_path=self.relative(source),
            tree_hash=digest,
            discovered_at=_utc(time.time()),
            version_time=version_time,
            version_time_source=source_kind,
            ordinal=-1,
            tool_version=self.tool_version,
            schema_version=SCHEMA_VERSION,
            mode=self.settings.mode,
            artifact_dir=self.relative(self.artifact_dir(digest)),
            counts={},
            confidence_census={},
            stage_seconds={},
            total_seconds=0.0,
            runtime_run_ids=(),
            provenance=Provenance(
                method=TIME_SOURCE_METHOD[source_kind],
                confidence=TIME_SOURCE_CONFIDENCE[source_kind],
                note=note,
            ),
        )

    def _analyse_one(self, record: VersionRecord) -> VersionRecord:
        out_dir = self.resolve(record.artifact_dir)
        started = time.time()
        summary = self._analyse(self.resolve(record.source_path), out_dir, self.settings)
        total = _round_seconds(time.time() - started)
        stage_seconds = {
            str(stage): _round_seconds(value)
            for stage, value in dict(summary.get("stage_seconds", {})).items()
        }
        counts = {
            name: int(summary.get(name, 0))
            for name in (
                "elements", "edges", "lineage_edges", "decisions",
                "findings", "unresolved", "barriers", "incomplete_records",
            )
        }
        analysed = replace(
            record,
            counts=counts,
            confidence_census={
                str(k): int(v) for k, v in dict(summary.get("confidence", {})).items()
            },
            stage_seconds=stage_seconds,
            total_seconds=_round_seconds(summary.get("total_seconds", total)),
        )
        self._fingerprints[record.id] = self._build_fingerprints(analysed)
        return analysed

    def _build_fingerprints(self, record: VersionRecord) -> tuple[ElementFingerprint, ...]:
        """One :class:`ElementFingerprint` per element, built while the source
        tree is still in hand. This is the only point at which a version's
        source is read for body normalisation; every later comparison uses
        these hashes."""
        out_dir = self.resolve(record.artifact_dir)
        source_root = self.resolve(record.source_path)
        snapshot = load_snapshot(out_dir, source_root=source_root)
        prefix = source_root.name
        reachability = {
            reroot_id(element_id, prefix): state
            for element_id, state in _reachability_by_element(out_dir).items()
        }
        cache: dict[str, list[str]] = {}
        built: list[ElementFingerprint] = []
        for element in snapshot.elements:
            text = _span_text(source_root, element, cache)
            normalized = _normalized_body(text)
            stable_id = reroot_id(element.id, prefix)
            built.append(
                ElementFingerprint(
                    id=f"fp::{record.id}::{stable_id}",
                    version_id=record.id,
                    element_id=stable_id,
                    kind=element.kind,
                    content_hash=element.content_hash,
                    normalized_body_hash=sha256_text(normalized) if normalized is not None else "",
                    signature=element.signature,
                    span=element.span,
                    reachability=reachability.get(stable_id, ReachabilityState.UNKNOWN),
                    confidence=element.provenance.confidence,
                    provenance=Provenance(
                        method=Method.AST_DIRECT,
                        confidence=element.provenance.confidence,
                        span=element.span,
                        note=(
                            "fingerprint of the analysed element; normalized body "
                            "hash is card 6's token normalisation (comments and "
                            "whitespace stripped)"
                            if normalized is not None
                            else "source text unavailable or not tokenizable: no "
                            "normalized body hash, so a comparison falls back to "
                            "content_hash and says so"
                        ),
                    ),
                )
            )
        return tuple(sorted(built, key=lambda f: f.id))

    def _snapshot(self, version_id: str) -> GraphSnapshot:
        record = self._versions[version_id]
        out_dir = self.resolve(record.artifact_dir)
        source_root = self.resolve(record.source_path)
        if not (out_dir / "elements.jsonl").exists():
            raise LedgerError(
                f"the artifacts for version {record.label} ({version_id}) are "
                f"missing from {out_dir}. The ledger indexes them; it is not a "
                f"copy of them. Refusing to compare against an empty graph, "
                f"which would report every element as REMOVED."
            )
        loaded = _reroot_snapshot(
            load_snapshot(out_dir, source_root=source_root), source_root.name
        )
        bodies = {
            fingerprint.element_id: fingerprint.normalized_body_hash
            for fingerprint in self._fingerprints.get(version_id, ())
        }
        return _FingerprintedSnapshot(
            label=record.label,
            elements=loaded.elements,
            edges=loaded.edges,
            lineage_edges=loaded.lineage_edges,
            slices=loaded.slices,
            findings=loaded.findings,
            source_root="",
            normalized_bodies=bodies,
        )

    def refresh_runtime_runs(self) -> None:
        """Pick up Mode A runs written under each version's artifact directory.

        A run recorded after the static analysis is new information about a
        version that is never re-analysed, so it is re-read every time.

        Any cached comparison touching a version whose runs changed is
        discarded, because its `runtime_delta` was computed against the runs
        that existed then. A stale empty delta would keep printing "not
        measured" over a run the owner had just made, which is the same defect
        in the other direction.
        """
        changed: set[str] = set()
        for version_id, record in sorted(self._versions.items()):
            run_ids = _completed_run_ids(self.resolve(record.artifact_dir))
            if run_ids != record.runtime_run_ids:
                self._versions[version_id] = replace(record, runtime_run_ids=run_ids)
                changed.add(version_id)
        if not changed:
            return
        for key in sorted(self._comparisons):
            comparison = self._comparisons[key]
            if {comparison.before_version_id, comparison.after_version_id} & changed:
                del self._comparisons[key]

    def _runtime_delta(
        self, before: VersionRecord, after: VersionRecord, match_map: Mapping[str, str]
    ) -> dict[str, Any]:
        """Observed execution differences, or ``{}`` meaning NOT MEASURED.

        Never a summary of nothing: when the two versions have no Mode A run
        against a common scenario this returns an empty dict, and
        :func:`render_track_report` prints "not measured" with the reason. An
        empty ``runtime_delta`` rendered as "no change" is this project's most
        repeated defect class.
        """
        before_dir = self.resolve(before.artifact_dir)
        after_dir = self.resolve(after.artifact_dir)
        pair = _common_scenario_runs(before, after, before_dir, after_dir)
        if pair is None:
            return {}
        scenario, before_run, after_run = pair
        before_counts = _event_counts(before_dir, before_run)
        after_counts = _event_counts(after_dir, after_run)
        mapped_before = {match_map.get(k, k): v for k, v in before_counts.items()}
        changed = {
            element: [mapped_before.get(element, 0), after_counts.get(element, 0)]
            for element in sorted(set(mapped_before) | set(after_counts))
            if mapped_before.get(element, 0) != after_counts.get(element, 0)
        }
        return {
            "scenario": scenario,
            "before_run_id": before_run,
            "after_run_id": after_run,
            "events_before": sum(before_counts.values()),
            "events_after": sum(after_counts.values()),
            "elements_executed_before": len(before_counts),
            "elements_executed_after": len(after_counts),
            "elements_newly_executed": sorted(set(after_counts) - set(mapped_before)),
            "elements_no_longer_executed": sorted(set(mapped_before) - set(after_counts)),
            "event_count_changed": changed,
            "measures": (
                "event counts, not durations. TraceEvent carries no timing field, "
                "so this says how much ran, never how fast it ran."
            ),
        }

    # -- views ------------------------------------------------------------

    @property
    def versions(self) -> tuple[VersionRecord, ...]:
        return tuple(_sorted_records(self._versions.values()))

    @property
    def comparisons(self) -> tuple[VersionComparison, ...]:
        return tuple(self._comparisons[key] for key in sorted(self._comparisons))

    def ordered_versions(self) -> list[VersionRecord]:
        """Newest first, unordered versions last -- the shape the report uses."""
        return sorted(
            self._versions.values(),
            key=lambda r: (0 if r.ordinal >= 0 else 1, -r.ordinal, r.id),
        )

    def apply_ordering(self) -> None:
        placed, status, reason = assign_ordinals(
            list(self._versions.values()), self.settings.order
        )
        self._versions = {record.id: record for record in placed}
        self.order_status = status
        self.order_reason = reason

    def consecutive_pairs(self) -> list[tuple[str, str]]:
        """Consecutive (before, after) version ids by ordinal. Empty when the
        order was not established -- nothing is compared on a guess."""
        placed = sorted(
            (r for r in self._versions.values() if r.ordinal >= 0),
            key=lambda r: r.ordinal,
        )
        return [(placed[i].id, placed[i + 1].id) for i in range(len(placed) - 1)]


def _comparison_id(before_id: str, after_id: str) -> str:
    return f"cmp::{before_id}::{after_id}"


def _tool_version() -> str:
    try:
        from cascade_map import __version__

        return str(__version__)
    except ImportError:  # pragma: no cover - the package always defines it
        return TOOL_VERSION_UNKNOWN


# ---------------------------------------------------------------------------
# Coverage: "every single element", made checkable
# ---------------------------------------------------------------------------


def _coverage(
    before: GraphSnapshot, after: GraphSnapshot, changes: Sequence[VersionChange]
) -> tuple[int, tuple[str, ...]]:
    """``(elements_accounted_for, unaccounted_element_ids)``.

    Every element of either version is one *occurrence* -- ``before:<id>`` or
    ``after:<id>`` -- and must be claimed by exactly one change. A matched pair
    claims one occurrence on each side and counts as one accounted element, so
    ``elements_accounted_for`` is a count of element identities, not of rows.

    Named in ``unaccounted_element_ids``:

    * an occurrence no change claims -- dropped, the failure this field exists
      to expose;
    * an occurrence more than one change claims -- "exactly one classification"
      is as broken by two as by none, and an owner reading a double-classified
      element would see contradictory answers depending on which row they hit.
    """
    occurrences = {f"before:{element.id}" for element in before.elements}
    occurrences |= {f"after:{element.id}" for element in after.elements}

    claimed_by: list[list[str]] = []
    claims: dict[str, int] = {key: 0 for key in occurrences}
    for change in changes:
        keys = []
        if change.before_id:
            keys.append(f"before:{change.before_id}")
        if change.after_id:
            keys.append(f"after:{change.after_id}")
        claimed_by.append(keys)
        for key in keys:
            claims[key] = claims.get(key, 0) + 1

    accounted_identities = sum(
        1
        for keys in claimed_by
        if keys and all(key in occurrences and claims[key] == 1 for key in keys)
    )
    unaccounted = tuple(sorted(key for key, count in claims.items() if count != 1))
    return accounted_identities, unaccounted


def _decision_path_changes(
    changes: Sequence[VersionChange],
    impacts: Sequence[Any],
    before: GraphSnapshot,
    after: GraphSnapshot,
) -> int:
    """Changes that touch a route to a final decision -- the figure the owner
    acts on, per the contract's own description of this field.

    Two things count, and conflating them would lose the more common one:

    * card 6's :attr:`Impact.decision_paths_changed`, which is true when the
      *set of sinks reachable* from the changed element differs between
      versions -- a route created or destroyed;
    * a change whose forward slice reaches a sink in either version -- a route
      that still exists but whose content changed.

    The second is the case the corpus case ``dif_impact_rank`` is built from:
    ``> 10`` becomes ``> 20`` inside the decision condition. The route is
    unchanged; what the route *decides* is not. Counting only the first gives
    zero for that change, measured on the corpus, and zero is the answer an
    owner would read as "this release cannot affect the decision".
    """
    # Keyed on the change's own id rather than parsed out of it: an element id
    # contains "::" itself, so splitting a change id on it is guesswork.
    by_id = {change.id: change for change in changes}
    total = 0
    for impact in impacts:
        if impact.decision_paths_changed:
            total += 1
            continue
        change = by_id.get(impact.change_id)
        if change is None:
            continue
        reaches = False
        for snapshot, element_id in ((before, change.before_id), (after, change.after_id)):
            if not element_id:
                continue
            found = snapshot.forward_slice(element_id)
            if found is not None and found.reaches_sink_ids:
                reaches = True
        if reaches:
            total += 1
    return total


def _finding_deltas(
    before: GraphSnapshot, after: GraphSnapshot, match_map: Mapping[str, str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Findings that appeared and disappeared, keyed by ``(kind, element)`` so
    that a re-issued id does not read as a new finding. Element identity runs
    through the rename/move matches card 6 emitted, read back off its own
    changes rather than recomputed here."""
    before_keys = {
        (str(f.kind), match_map.get(f.element_id, f.element_id)): f.id for f in before.findings
    }
    after_keys = {(str(f.kind), f.element_id): f.id for f in after.findings}
    added = tuple(sorted(fid for key, fid in after_keys.items() if key not in before_keys))
    removed = tuple(sorted(fid for key, fid in before_keys.items() if key not in after_keys))
    return added, removed


# ---------------------------------------------------------------------------
# Reading what analysis wrote
# ---------------------------------------------------------------------------


def _reachability_by_element(out_dir: Path) -> dict[str, ReachabilityState]:
    path = out_dir / "reachability.jsonl"
    if not path.exists():
        return {}
    states: dict[str, ReachabilityState] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        states[row["element_id"]] = ReachabilityState(row["state"])
    return states


def _span_text(source_root: Path, element: Any, cache: dict[str, list[str]]) -> str | None:
    """The element's own source text, read once per file. Read-only, never
    executed -- the same rule card 6 follows."""
    rel = element.span.path
    if rel not in cache:
        path = source_root / rel
        try:
            cache[rel] = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            cache[rel] = []
    lines = cache[rel]
    if not lines:
        return None
    start = max(element.span.line - 1, 0)
    end = element.span.end_line if element.span.end_line else element.span.line
    end = min(end, len(lines))
    if start >= end:
        return None
    return "\n".join(lines[start:end])


def _completed_run_ids(artifact_dir: Path) -> tuple[str, ...]:
    """Mode A runs under this version's artifacts that actually ran.

    A refused run and a run whose scenario raised are both excluded: comparing
    against a run in which the target never executed would produce a confident
    "everything changed". They are excluded here and named by the report.
    """
    runtime = artifact_dir / "runtime"
    if not runtime.is_dir():
        return ()
    found: list[str] = []
    for run_dir in sorted(runtime.iterdir(), key=lambda p: p.name):
        record = run_dir / "run.json"
        if not record.is_file():
            continue
        try:
            payload = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("refused") or payload.get("scenario_failure"):
            continue
        run_id = str(payload.get("run_id", run_dir.name))
        found.append(run_id)
    return tuple(sorted(found))


def _run_scenarios(artifact_dir: Path) -> dict[str, str]:
    """run id -> scenario name, for completed runs."""
    runtime = artifact_dir / "runtime"
    out: dict[str, str] = {}
    if not runtime.is_dir():
        return out
    for run_dir in sorted(runtime.iterdir(), key=lambda p: p.name):
        record = run_dir / "run.json"
        if not record.is_file():
            continue
        try:
            payload = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("refused") or payload.get("scenario_failure"):
            continue
        out[str(payload.get("run_id", run_dir.name))] = str(payload.get("scenario", ""))
    return out


def _common_scenario_runs(
    before: VersionRecord, after: VersionRecord, before_dir: Path, after_dir: Path
) -> tuple[str, str, str] | None:
    """``(scenario, before_run_id, after_run_id)`` for the one scenario both
    versions were traced under, or None. Comparing two runs of *different*
    scenarios would measure the scenarios, not the versions."""
    if not before.runtime_run_ids or not after.runtime_run_ids:
        return None
    before_runs = _run_scenarios(before_dir)
    after_runs = _run_scenarios(after_dir)
    shared = sorted(
        {s for s in before_runs.values() if s}
        & {s for s in after_runs.values() if s}
    )
    if not shared:
        return None
    scenario = shared[0]
    # Deterministic pick when a scenario was traced more than once: the
    # highest run id, which is the most recent recording of that scenario.
    before_run = max(r for r, s in sorted(before_runs.items()) if s == scenario)
    after_run = max(r for r, s in sorted(after_runs.items()) if s == scenario)
    return scenario, before_run, after_run


def _event_counts(artifact_dir: Path, run_id: str) -> dict[str, int]:
    path = artifact_dir / "runtime" / run_id / "events.jsonl"
    counts: dict[str, int] = {}
    if not path.is_file():
        return counts
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        element = str(row.get("element_id", ""))
        if not element:
            continue
        counts[element] = counts.get(element, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# track
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrackResult:
    """Everything the report prints, derived rather than narrated."""

    ledger_path: str
    versions_known: int
    new_version_ids: tuple[str, ...]
    records: tuple[VersionRecord, ...]
    comparisons: tuple[VersionComparison, ...]
    order_status: str
    order_reason: str
    duplicate_labels: dict[str, str]
    analysed_seconds: dict[str, float]
    runtime_notes: dict[str, str]
    mode: int
    trace_notes: tuple[str, ...] = ()


def track(
    settings: Settings,
    *,
    root: str | Path | None = None,
    analyse: AnalyseFn | None = None,
    trace: TraceFn | None = None,
) -> tuple[TrackResult, Ledger]:
    """Discover, analyse what is new, compare consecutive pairs, save, report.

    Reuses card 6 for the comparison and card 10's pipeline (cards 1-5, 16 and
    17) for the analysis. This function adds nothing to either; it decides
    what to run and what to skip.
    """
    ledger = Ledger(settings, root=root, analyse=analyse, trace=trace)
    ledger_path = ledger.resolve(settings.ledger)
    ledger.load(ledger_path)

    discovered = ledger.discover(settings.versions_dir)

    new_ids = [record.id for record in ledger.analyse_new(discovered)]

    trace_notes = _run_mode_a(ledger, settings, new_ids, trace)
    ledger.refresh_runtime_runs()
    ledger.apply_ordering()

    computed: list[VersionComparison] = []
    runtime_notes: dict[str, str] = {}
    for before_id, after_id in ledger.consecutive_pairs():
        fresh = not ledger.has_comparison(before_id, after_id)
        comparison = ledger.compare(before_id, after_id)
        if fresh or before_id in new_ids or after_id in new_ids:
            computed.append(comparison)
        runtime_notes[comparison.id] = _runtime_note(
            settings, ledger.record(before_id), ledger.record(after_id), comparison
        )

    if not computed and ledger.consecutive_pairs():
        # Nothing was analysed and nothing was recomputed, so there is no new
        # comparison to show -- but ending on silence would leave the owner
        # without the one answer they ran this for. The most recent pair is
        # printed from the ledger, unchanged and uncomputed.
        last_before, last_after = ledger.consecutive_pairs()[-1]
        computed.append(ledger.compare(last_before, last_after))

    ledger.save(ledger_path)
    result = TrackResult(
        ledger_path=str(ledger_path),
        versions_known=len(ledger.versions),
        new_version_ids=tuple(sorted(new_ids)),
        records=tuple(ledger.ordered_versions()),
        comparisons=tuple(computed),
        order_status=ledger.order_status,
        order_reason=ledger.order_reason,
        duplicate_labels=dict(sorted(ledger.duplicate_labels.items())),
        analysed_seconds=dict(ledger.analysis_seconds),
        runtime_notes=runtime_notes,
        mode=settings.mode,
        trace_notes=trace_notes,
    )
    return result, ledger


def _run_mode_a(
    ledger: Ledger, settings: Settings, new_ids: Sequence[str], trace: TraceFn | None
) -> tuple[str, ...]:
    """MODE 2: trace each newly analysed version under the harness.

    Only newly analysed versions are traced, for the same reason they are only
    analysed once. MODE 1 does nothing here and the report says runtime was
    not measured.
    """
    if settings.mode != 2 or not new_ids:
        return ()
    if trace is None:
        return (
            "MODE 2 was set but no harness was wired into this Ledger, so nothing "
            "was executed. Run `metatron trace` per version instead.",
        )
    scenarios = ledger.resolve(settings.scenarios)
    if not scenarios.is_file():
        return (
            f"MODE 2 was set but SCENARIOS file {scenarios} does not exist, so "
            f"nothing was executed and runtime is not measured.",
        )
    notes: list[str] = []
    for version_id in sorted(new_ids):
        out_dir = ledger.artifact_dir(version_id)
        code, message = trace(out_dir, scenarios, settings.scenario, out_dir)
        label = ledger.record(version_id).label
        head = message.splitlines()[0] if message else ""
        notes.append(f"{label}: trace exit {code} -- {head}")
    return tuple(notes)


def _runtime_note(
    settings: Settings,
    before: VersionRecord,
    after: VersionRecord,
    comparison: VersionComparison,
) -> str:
    """Why ``runtime_delta`` is empty, or what it found. Never "no change"."""
    if comparison.runtime_delta:
        delta = comparison.runtime_delta
        return (
            f"scenario {delta['scenario']}: {delta['events_before']:,} -> "
            f"{delta['events_after']:,} events, "
            f"{len(delta['elements_newly_executed'])} newly executed, "
            f"{len(delta['elements_no_longer_executed'])} no longer executed "
            f"(counts, not durations)"
        )
    if settings.mode != 2:
        return "not measured (MODE 1; set MODE 2 to observe execution)"
    missing = [r.label for r in (before, after) if not r.runtime_run_ids]
    if missing:
        return f"not measured (MODE 2, but no completed Mode A run for {missing!r})"
    return (
        "not measured (both versions have Mode A runs, but none of the same "
        "scenario; comparing different scenarios would measure the scenarios)"
    )


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def render_track_report(result: TrackResult) -> str:
    """What ``track`` prints. Every number comes from the result, not prose."""
    new = len(result.new_version_ids)
    lines = [
        f"Ledger {result.ledger_path} — {result.versions_known:,} version(s) known, "
        f"{new:,} new",
        "",
    ]
    if not result.records:
        lines.append("  no versions found in VERSIONS_DIR.")
        return "\n".join(lines)

    width = max(len(record.label) for record in result.records)
    for record in result.records:
        phrase = TIME_SOURCE_PHRASE.get(record.version_time_source, "unknown")
        if record.id in result.new_version_ids:
            seconds = result.analysed_seconds.get(record.id, record.total_seconds)
            status = f"NEW   analysed in {seconds:.1f}s"
        else:
            status = "      known, not re-run"
        ordinal = f"#{record.ordinal}" if record.ordinal >= 0 else "#?"
        lines.append(f"  {record.label:<{width}}  {ordinal:>4}  {status}   ({phrase})")

    for label, duplicate_of in result.duplicate_labels.items():
        lines.append(
            f"  {label:<{width}}        DUPLICATE of {duplicate_of} — identical "
            f"content, one version, analysed once"
        )

    if new == 0:
        lines += [
            "",
            "Nothing new. No version was analysed and no tree was read beyond "
            "hashing it.",
        ]

    if result.order_status == "unestablished":
        lines += [
            "",
            "ORDER NOT ESTABLISHED — every ordinal is -1 and NOTHING WAS COMPARED.",
            f"  {result.order_reason}",
            "  A comparison against the wrong predecessor is a confident, detailed,",
            "  completely wrong answer. Set ORDER in METATRON_SETTINGS instead:",
            "      \"ORDER\": ["
            + ", ".join(f'"{record.label}"' for record in reversed(result.records))
            + "],   # oldest first",
        ]
    elif result.order_reason:
        lines += ["", f"Ordering: {result.order_reason}"]

    for note in result.trace_notes:
        lines += ["", f"MODE 2: {note}"]

    by_id = {record.id: record for record in result.records}
    for comparison in result.comparisons:
        lines += [""] + _comparison_lines(comparison, by_id, result.runtime_notes)
    return "\n".join(lines)


def _comparison_lines(
    comparison: VersionComparison,
    by_id: Mapping[str, VersionRecord],
    runtime_notes: Mapping[str, str],
) -> list[str]:
    before = by_id.get(comparison.before_version_id)
    after = by_id.get(comparison.after_version_id)
    before_label = before.label if before else comparison.before_version_id
    after_label = after.label if after else comparison.after_version_id
    counts = comparison.change_counts
    unchanged = counts.get(ChangeKind.UNCHANGED.value, 0)
    ambiguous = counts.get(ChangeKind.AMBIGUOUS.value, 0)
    changed = sum(counts.values()) - unchanged - ambiguous
    breakdown = ", ".join(
        f"{counts[kind.value]} {kind.value}"
        for kind in ChangeKind
        if kind not in (ChangeKind.UNCHANGED, ChangeKind.AMBIGUOUS) and counts.get(kind.value)
    )
    unaccounted = len(comparison.unaccounted_element_ids)
    lines = [
        f"{before_label} -> {after_label}",
        f"  {comparison.elements_before:,} elements before, "
        f"{comparison.elements_after:,} after, "
        f"{comparison.elements_accounted_for:,} accounted for, "
        f"{unaccounted:,} unaccounted",
        f"  changed     {changed:>5,}" + (f"   ({breakdown})" if breakdown else ""),
        f"  unchanged   {unchanged:>5,}",
        f"  ambiguous   {ambiguous:>5,}"
        + ("   <- equally good matches, all listed, none claimed" if ambiguous else ""),
        f"  decision paths {comparison.decision_paths_changed:>2,}"
        "   <- changes that touch a route to your final decision",
        f"  reachability   {len(comparison.reachability_flipped):>2,} flipped",
        f"  findings       +{len(comparison.findings_added)} / "
        f"-{len(comparison.findings_removed)}",
        f"  runtime        {runtime_notes.get(comparison.id, 'not measured')}",
    ]
    if unaccounted:
        lines += [
            "",
            f"  INCOMPLETE — {unaccounted:,} element(s) did not land in exactly one",
            "  classification. Named, never dropped:",
        ]
        lines += [f"    {eid}" for eid in comparison.unaccounted_element_ids[:20]]
        if unaccounted > 20:
            lines.append(
                f"    ... and {unaccounted - 20:,} more in "
                f"unaccounted_element_ids in the ledger"
            )
    return lines


def render_history_report(ledger: Ledger) -> str:
    """The whole history, for ``track --report``.

    Every version the ledger knows and every comparison it holds, including
    pairs that are no longer consecutive because a version was inserted
    between them. Nothing is recomputed to print this.
    """
    lines = [f"History — {len(ledger.versions):,} version(s), "
             f"{len(ledger.comparisons):,} comparison(s)", ""]
    for record in ledger.ordered_versions():
        ordinal = f"#{record.ordinal}" if record.ordinal >= 0 else "#? (order not established)"
        lines += [
            f"{record.label}  {ordinal}",
            f"  id            {record.id}",
            f"  tree hash     {record.tree_hash}",
            f"  source        {record.source_path}",
            f"  artifacts     {record.artifact_dir}",
            f"  version time  {record.version_time or '(none)'}  "
            f"[{record.version_time_source}] — {TIME_SOURCE_PHRASE.get(record.version_time_source, '')}",
            f"  first seen    {record.discovered_at}  (a fact about this tool, "
            f"never used for ordering)",
            f"  mode          {record.mode}",
            "  counts        "
            + (", ".join(f"{k} {v:,}" for k, v in sorted(record.counts.items())) or "(none)"),
            "  confidence    "
            + (", ".join(f"{k} {v:,}" for k, v in sorted(record.confidence_census.items()))
               or "(none)"),
            f"  this tool took {record.total_seconds:.3f}s: "
            + (", ".join(f"{k} {v:.3f}s" for k, v in sorted(record.stage_seconds.items()))
               or "(not recorded)"),
            "                 ^ how long METATRON took, never how fast your engine runs",
            "  Mode A runs   "
            + (", ".join(record.runtime_run_ids) if record.runtime_run_ids
               else "none — runtime is not measured for this version"),
            "",
        ]
    by_id = {record.id: record for record in ledger.versions}
    notes = {
        comparison.id: (
            "measured"
            if comparison.runtime_delta
            else "not measured — no Mode A run of one scenario against both versions"
        )
        for comparison in ledger.comparisons
    }
    for comparison in ledger.comparisons:
        lines += _comparison_lines(comparison, by_id, notes) + [""]
    return "\n".join(lines).rstrip() + "\n"
