"""Card 17 — dependency and version applicability.

The question this module answers, without running anything: *which package
versions is this element applicable to, and does this machine have them?*

Four facts are gathered **separately** and joined afterwards, because every
interesting failure is a disagreement between two of them:

* :meth:`Dependencies.requirements` — what the project DECLARES it needs, and
  the file and line where it said so.
* :meth:`Dependencies.installed` — what is actually INSTALLED in the
  environment the target runs under, read from ``*.dist-info`` metadata **as
  text**.
* :meth:`Dependencies.usage` — which elements actually USE a distribution,
  what API surface they touch, and whether any of them reaches a decision sink.
* :meth:`Dependencies.interpreter_requirements` — the minimum Python each
  element's own syntax requires, read off the grammar.

A single merged "dependency" record would hide exactly the disagreement the
owner is looking for, so the four stay apart and :meth:`Dependencies.findings`
reports where they contradict each other.

**Constraint 1 applies to the environment as strictly as to the code.**
Nothing here imports, executes, ``exec``s, ``eval``s or unpickles anything —
not a target module, and not an installed package. Installed metadata is read
with :func:`pathlib.Path.read_text`. ``pip`` is never invoked and
``importlib.metadata`` is never pointed at a live path. Reading a package's
metadata must not run its ``__init__``.

Stdlib only. In particular ``packaging`` is *not* a dependency of this project
and must not become one, so PEP 440 version comparison is implemented here.
Where a specifier cannot be evaluated exactly, the answer is ``None`` and the
finding says UNKNOWN naming the specifier — never a guess at satisfaction.
"""

from __future__ import annotations

import ast
import configparser
import re
import sys
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cascade_map.contracts.interfaces import (
    Confidence,
    Edge,
    Element,
    ElementKind,
    Finding,
    FindingKind,
    InstalledPackage,
    InterpreterRequirement,
    Method,
    PackageRequirement,
    PackageUsage,
    Provenance,
    Reachability,
    ReachabilityState,
    SourceSpan,
    Unresolved,
    UnresolvedReason,
)

__all__ = [
    "Dependencies",
    "evaluate_specifier",
    "normalize_distribution",
    "parse_version",
]

#: A manifest bigger than this is not read. Manifests are kilobytes; anything
#: this large is not one, and the run says so rather than stalling.
MAX_MANIFEST_BYTES = 1_000_000

#: A source file bigger than this is not parsed for imports.
#:
#: Round 6: raised from 4,000,000, which was chosen against a fixture corpus
#: and silently excluded the very kind of target this tool exists for. The
#: owner's engine is 14,804,021 bytes in ONE file; at 4 MB its imports and
#: interpreter requirements were never read, and the map said so in a way
#: that read as "this file is not described at all".
#:
#: Measured on that file, on this machine, Python 3.12 (see the builder's
#: report for the run): read 0.31 s, `ast.parse` 23.6 s, the scan's four
#: `ast.walk` passes 3.0 s -- 30.9 s in total -- for a peak RSS of 679 MB
#: over 895,811 AST nodes. The limit is set at 20 MB, which is that
#: measurement plus the headroom of one more chapter of the same engine:
#: roughly 42 s and under 1 GB, which is a cost a once-per-analysis scan
#: can pay. It is not raised further, because `ast.parse` cost and memory
#: both grow with the file and an analysis that is OOM-killed reports
#: nothing at all.
#:
#: Card 1 has its own, separate limit for inventory (16 MB, and it did read
#: this file). Override this one with `--max-source-mb`.
MAX_SOURCE_BYTES = 20_000_000

#: Attribute paths reported per distribution. Capped explicitly — a truncated
#: list that reads as complete is worse than a short one that says it is short.
MAX_ATTRIBUTE_PATHS = 200

_SKIP_DIRS = frozenset(
    {"__pycache__", ".git", ".hg", ".svn", ".tox", ".mypy_cache", ".pytest_cache",
     "node_modules", ".eggs"}
)


# ---------------------------------------------------------------------------
# PEP 503 — name normalisation
# ---------------------------------------------------------------------------


_NORMALIZE_RE = re.compile(r"[-_.]+")


def normalize_distribution(name: str) -> str:
    """PEP 503 normalised distribution name.

    Lowercase, with runs of ``-``, ``_`` and ``.`` collapsed to a single ``-``.
    Joining on the raw spelling instead makes ``Scikit_Learn`` and
    ``scikit-learn`` two distributions, and reports one of them as undeclared.
    """
    return _NORMALIZE_RE.sub("-", name.strip()).lower()


# ---------------------------------------------------------------------------
# PEP 440 — versions and specifiers, implemented here rather than depended on
# ---------------------------------------------------------------------------


class _InfinityType:
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Infinity"

    def __hash__(self) -> int:
        return hash(repr(self))

    def __lt__(self, other: object) -> bool:
        return False

    def __le__(self, other: object) -> bool:
        return False

    def __eq__(self, other: object) -> bool:
        return isinstance(other, self.__class__)

    def __gt__(self, other: object) -> bool:
        return True

    def __ge__(self, other: object) -> bool:
        return True


class _NegativeInfinityType:
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "-Infinity"

    def __hash__(self) -> int:
        return hash(repr(self))

    def __lt__(self, other: object) -> bool:
        return True

    def __le__(self, other: object) -> bool:
        return True

    def __eq__(self, other: object) -> bool:
        return isinstance(other, self.__class__)

    def __gt__(self, other: object) -> bool:
        return False

    def __ge__(self, other: object) -> bool:
        return False


_INFINITY = _InfinityType()
_NEG_INFINITY = _NegativeInfinityType()

_VERSION_PATTERN = r"""
    v?
    (?:(?P<epoch>[0-9]+)!)?
    (?P<release>[0-9]+(?:\.[0-9]+)*)
    (?P<pre>
        [-_.]?
        (?P<pre_l>alpha|beta|preview|pre|rc|a|b|c)
        [-_.]?
        (?P<pre_n>[0-9]+)?
    )?
    (?P<post>
        (?:-(?P<post_n1>[0-9]+))
        |
        (?:[-_.]?(?P<post_l>post|rev|r)[-_.]?(?P<post_n2>[0-9]+)?)
    )?
    (?P<dev>[-_.]?(?P<dev_l>dev)[-_.]?(?P<dev_n>[0-9]+)?)?
    (?:\+(?P<local>[a-z0-9]+(?:[-_.][a-z0-9]+)*))?
"""

_VERSION_RE = re.compile(
    r"^\s*" + _VERSION_PATTERN + r"\s*$", re.VERBOSE | re.IGNORECASE
)

_PRE_LETTERS = {"alpha": "a", "a": "a", "beta": "b", "b": "b",
                "c": "rc", "pre": "rc", "preview": "rc", "rc": "rc"}


@dataclass(frozen=True)
class Version:
    """A parsed PEP 440 version. Ordering is the PEP's, not string order."""

    text: str
    epoch: int
    release: tuple[int, ...]
    pre: tuple[str, int] | None
    post: int | None
    dev: int | None
    local: tuple[Any, ...] | None

    @property
    def is_prerelease(self) -> bool:
        return self.pre is not None or self.dev is not None

    @property
    def key(self) -> tuple[Any, ...]:
        release = tuple(
            reversed(
                list(
                    _drop_leading_zeros(list(reversed(self.release)))
                )
            )
        )
        if self.pre is None and self.post is None and self.dev is not None:
            pre: Any = _NEG_INFINITY
        elif self.pre is None:
            pre = _INFINITY
        else:
            pre = self.pre
        post: Any = _NEG_INFINITY if self.post is None else self.post
        dev: Any = _INFINITY if self.dev is None else self.dev
        if self.local is None:
            local: Any = _NEG_INFINITY
        else:
            local = tuple(
                (item, "") if isinstance(item, int) else (_NEG_INFINITY, item)
                for item in self.local
            )
        return (self.epoch, release, pre, post, dev, local)


def _drop_leading_zeros(reversed_release: list[int]) -> list[int]:
    out = list(reversed_release)
    while out and out[0] == 0:
        out.pop(0)
    return out


def parse_version(text: str) -> Version | None:
    """Parse a PEP 440 version, or ``None`` when it is not one.

    ``None`` is a refusal, not a zero: the caller reports the string it could
    not read rather than comparing something it did not understand.
    """
    match = _VERSION_RE.match(text or "")
    if match is None:
        return None
    pre: tuple[str, int] | None = None
    if match.group("pre_l"):
        letter = _PRE_LETTERS[match.group("pre_l").lower()]
        pre = (letter, int(match.group("pre_n") or 0))
    post: int | None = None
    if match.group("post_n1"):
        post = int(match.group("post_n1"))
    elif match.group("post_l"):
        post = int(match.group("post_n2") or 0)
    dev: int | None = None
    if match.group("dev_l"):
        dev = int(match.group("dev_n") or 0)
    local: tuple[Any, ...] | None = None
    if match.group("local"):
        local = tuple(
            int(part) if part.isdigit() else part.lower()
            for part in re.split(r"[-_.]", match.group("local"))
        )
    return Version(
        text=text.strip(),
        epoch=int(match.group("epoch") or 0),
        release=tuple(int(part) for part in match.group("release").split(".")),
        pre=pre,
        post=post,
        dev=dev,
        local=local,
    )


_CLAUSE_RE = re.compile(
    r"^\s*(?P<op>===|==|!=|<=|>=|~=|\^|~|<|>|=)\s*(?P<ver>.+?)\s*$"
)


def _expand_dialect(op: str, raw: str, dialect: str) -> list[tuple[str, str]] | None:
    """Rewrite one dialect-specific clause into PEP 440 clauses.

    Poetry's ``^`` and ``~`` and conda's bare ``=`` are not PEP 440 operators.
    They are expanded here, at evaluation time only: `PackageRequirement`
    still stores the string the owner typed. An expansion this function cannot
    do returns ``None`` and the caller reports the specifier as unevaluable.
    """
    if raw.endswith("*") and op in {"==", "!="}:
        # A prefix match is evaluated whole, later; there is no version to
        # parse here and parsing one would refuse a form we can answer.
        return [(op, raw)]
    version = parse_version(raw)
    if version is None:
        return None
    release = version.release
    if op in {"^", "~"} and dialect != "poetry":
        # `^` and `~` are poetry syntax. Read anywhere else they are not a
        # form this tool evaluates, and saying so beats assuming poetry.
        return None
    if op == "^":  # poetry caret: up to the next left-most non-zero bump
        upper: tuple[int, ...]
        if release and release[0] != 0:
            upper = (release[0] + 1,)
        elif len(release) > 1 and release[1] != 0:
            upper = (0, release[1] + 1)
        elif len(release) > 2:
            upper = (0, 0, release[2] + 1)
        else:
            upper = (1,)
        return [(">=", raw), ("<", ".".join(str(p) for p in upper))]
    if op == "~":  # poetry tilde: allow the last named segment to move
        if len(release) >= 2:
            upper = (release[0], release[1] + 1)
        else:
            upper = (release[0] + 1,)
        return [(">=", raw), ("<", ".".join(str(p) for p in upper))]
    if op == "=" and dialect == "conda":
        # `pkg=1.2` in an environment.yml is a prefix match, not equality.
        return [("==", raw if raw.endswith("*") else raw + ".*")]
    if op == "=":
        return None
    return [(op, raw)]


def _clauses(specifier: str, dialect: str) -> list[tuple[str, str]] | None:
    """Split a specifier into (operator, version) pairs, or ``None``."""
    text = (specifier or "").strip()
    if not text or text in {"*", "any"}:
        return []
    out: list[tuple[str, str]] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        match = _CLAUSE_RE.match(part)
        if match is None:
            return None
        expanded = _expand_dialect(
            match.group("op"), match.group("ver").strip(), dialect
        )
        if expanded is None:
            return None
        out.extend(expanded)
    return out


def _prefix_match(candidate: Version, raw: str) -> bool | None:
    """``==1.4.*`` — compare the release segments the pattern names."""
    prefix = raw[:-2] if raw.endswith(".*") else raw[:-1]
    pinned = parse_version(prefix)
    if pinned is None or pinned.pre or pinned.post is not None or pinned.dev is not None:
        return None
    wanted = pinned.release
    return candidate.epoch == pinned.epoch and candidate.release[: len(wanted)] == wanted


def evaluate_specifier(
    specifier: str, version: str, *, dialect: str = "pep508"
) -> tuple[bool | None, str]:
    """Does *version* satisfy *specifier*?

    Returns ``(satisfied, reason)``. ``satisfied`` is ``None`` when the
    specifier or the version could not be evaluated exactly; *reason* then
    names what defeated it, and the caller must report UNKNOWN rather than a
    verdict.

    Two documented departures from a full resolver, both deliberate:

    * **Pre-releases are compared by ordering, not filtered out.** The question
      here is "is the version on this machine inside the declared range", and
      an installed ``2.0rc1`` against ``>=1.0`` is inside it. A resolver
      choosing what to *download* excludes pre-releases by default; reporting
      an installed one as a conflict would be a false positive.
    * **A local version segment in the specifier** (``==1.2+local``) is
      refused rather than approximated.
    """
    candidate = parse_version(version)
    if candidate is None:
        return None, f"installed version {version!r} is not a PEP 440 version"
    clauses = _clauses(specifier, dialect)
    if clauses is None:
        return None, f"specifier {specifier!r} is not a form this tool evaluates"
    for op, raw in clauses:
        if op == "===":
            if candidate.text != raw:
                return False, f"{version} does not match arbitrary equality {op}{raw}"
            continue
        if raw.endswith("*"):
            if op not in {"==", "!="}:
                return None, f"wildcard is not valid with {op!r} in {specifier!r}"
            matched = _prefix_match(candidate, raw)
            if matched is None:
                return None, f"cannot evaluate prefix match {op}{raw}"
            if (op == "==" and not matched) or (op == "!=" and matched):
                return False, f"{version} fails {op}{raw}"
            continue
        if "+" in raw:
            return None, (
                f"specifier {op}{raw} carries a local version segment, which this "
                "tool does not compare"
            )
        pinned = parse_version(raw)
        if pinned is None:
            return None, f"{raw!r} in {specifier!r} is not a PEP 440 version"
        if op == "~=":
            if len(pinned.release) < 2:
                return None, f"~={raw} needs at least two release segments"
            upper = pinned.release[:-1]
            upper = upper[:-1] + (upper[-1] + 1,)
            if not (
                candidate.key >= pinned.key
                and candidate.epoch == pinned.epoch
                and candidate.release[: len(upper) - 1] == upper[: len(upper) - 1]
                and candidate.release[len(upper) - 1 : len(upper)] < upper[-1:]
            ):
                return False, f"{version} fails ~={raw}"
            continue
        ok = _compare(op, candidate, pinned)
        if ok is None:
            return None, f"operator {op!r} in {specifier!r} is not evaluated"
        if not ok:
            return False, f"{version} fails {op}{raw}"
    return True, "every clause is satisfied"


def _compare(op: str, candidate: Version, pinned: Version) -> bool | None:
    left = candidate.key
    right = pinned.key
    if op == "==":
        # Equality ignores a local segment the specifier did not name.
        return (candidate.epoch, candidate.release, candidate.pre, candidate.post,
                candidate.dev) == (pinned.epoch, pinned.release, pinned.pre,
                                   pinned.post, pinned.dev)
    if op == "!=":
        return not _compare("==", candidate, pinned)
    if op == "<=":
        return left <= right
    if op == ">=":
        return left >= right
    if op == "<":
        if left >= right:
            return False
        # PEP 440: `<V` must not admit a pre-release of V unless V is one.
        if pinned.pre is None and candidate.pre is not None:
            return candidate.release != pinned.release
        return True
    if op == ">":
        if left <= right:
            return False
        # PEP 440: `>V` must not admit a post-release of V.
        if pinned.post is None and candidate.post is not None:
            return candidate.release != pinned.release
        return True
    return None


# ---------------------------------------------------------------------------
# Declared — manifests, read as text, with the line the owner typed it on
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RawReq:
    """One requirement as a manifest spelled it, before it becomes a record."""

    distribution: str
    raw: str
    specifier: str
    extras: tuple[str, ...]
    marker: str
    group: str
    path: str
    line: int
    dialect: str


@dataclass(frozen=True)
class _PythonDecl:
    """A declared ``requires-python``, wherever the manifest put it."""

    specifier: str
    path: str
    line: int
    dialect: str


_REQ_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*"
    r"(?:\[(?P<extras>[^\]]*)\])?\s*"
    r"(?P<rest>.*)$"
)

#: Options a requirements file may carry that are not requirements.
_REQ_OPTION_PREFIXES = ("-", "--")


def _split_marker(rest: str) -> tuple[str, str]:
    head, sep, marker = rest.partition(";")
    return head.strip(), marker.strip() if sep else ""


def _parse_requirement_text(
    text: str, *, group: str, path: str, line: int, dialect: str
) -> tuple[_RawReq | None, str]:
    """Parse one PEP 508 requirement. Returns the record, or a reason it failed.

    ``specifier`` comes back exactly as written. Nothing is re-spelled: an
    owner debugging a version problem needs to recognise the string they
    typed, and a normalised re-rendering is a second thing to distrust.
    """
    raw = text.strip()
    if not raw:
        return None, "empty"
    match = _REQ_RE.match(raw)
    if match is None:
        return None, f"not a PEP 508 requirement: {raw!r}"
    rest, marker = _split_marker(match.group("rest"))
    extras = tuple(
        sorted(e.strip() for e in (match.group("extras") or "").split(",") if e.strip())
    )
    if rest.startswith("@"):
        specifier = ""
    else:
        specifier = rest.strip()
    return (
        _RawReq(
            distribution=normalize_distribution(match.group("name")),
            raw=raw,
            specifier=specifier,
            extras=extras,
            marker=marker,
            group=group,
            path=path,
            line=line,
            dialect=dialect,
        ),
        "",
    )


def _find_line(lines: Sequence[str], needles: Sequence[str], start: int = 0) -> int:
    """1-based line of the first line at/after *start* containing every needle.

    Line numbers for TOML, INI and YAML tables are located in the raw text
    this way rather than written down: a derived fact is generated from the
    source of truth or it is not emitted. 0 means not found, and the caller
    says so rather than substituting a plausible number.

    A parsed TOML string can differ from its source spelling wherever the
    source escaped a quote, so a needle containing a quote is retried on the
    portion before it. The retry still matches text that is genuinely in the
    file; it only shortens what is looked for.
    """
    attempts: list[Sequence[str]] = [needles]
    shortened = [
        needle.split('"')[0].split("'")[0] if ('"' in needle or "'" in needle) else needle
        for needle in needles
    ]
    if shortened != list(needles) and all(part.strip() for part in shortened):
        attempts.append(shortened)
    for candidate in attempts:
        for offset in range(start, len(lines)):
            line = lines[offset]
            if all(needle in line for needle in candidate):
                return offset + 1
    return 0


class _ManifestReader:
    """Reads every declared constraint out of one target tree.

    Each format gets its own small parser. A format that defeats one of them
    produces an `Unresolved` naming the file and the reason — visible in the
    emitted artifact, because the owner reads artifacts, not source.
    """

    def __init__(self, root: Path, max_source_bytes: int = MAX_SOURCE_BYTES) -> None:
        self.max_source_bytes = max_source_bytes
        self.root = root
        #: Resolved once, so an include reached through `-r ../base.txt`
        #: still lands on a path relative to the target root. An absolute
        #: path in a span breaks the byte-identical guarantee outright: two
        #: machines then produce different output for the same tree.
        self.resolved_root = root.resolve()
        self.requirements: list[_RawReq] = []
        self.python_declarations: list[_PythonDecl] = []
        self.unresolved: list[Unresolved] = []
        self.manifests: list[str] = []
        self._seen_files: set[Path] = set()

    # -- entry --------------------------------------------------------

    def read(self) -> None:
        for path in self._candidate_paths():
            self._dispatch(path)

    def _candidate_paths(self) -> list[Path]:
        out: list[Path] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            if any(
                part.endswith((".dist-info", ".egg-info", ".data"))
                for part in path.parts
            ):
                continue
            name = path.name
            if (
                (name.startswith("requirements") and name.endswith(".txt"))
                or (name.startswith("constraints") and name.endswith(".txt"))
                or name in {"pyproject.toml", "setup.cfg", "Pipfile"}
                or name in {"environment.yml", "environment.yaml"}
                or name.endswith(".py")
            ):
                out.append(path)
        return out

    def _rel(self, path: Path) -> str:
        for base in (self.root, self.resolved_root):
            try:
                return path.relative_to(base).as_posix()
            except ValueError:
                continue
        try:
            return path.resolve().relative_to(self.resolved_root).as_posix()
        except ValueError:
            # An include that genuinely leaves the tree. Named by its own
            # name only: an absolute path here would differ per machine.
            return f"<outside target root>/{path.name}"

    def _note(self, path: Path) -> None:
        rel = self._rel(path)
        if rel not in self.manifests:
            self.manifests.append(rel)

    def _unreadable(self, path: Path, reason: UnresolvedReason, detail: str) -> None:
        rel = self._rel(path)
        self.unresolved.append(
            Unresolved(
                id=f"dep::manifest::{rel}",
                reason=reason,
                span=SourceSpan(path=rel, line=1),
                description=detail,
                attempted=(Method.AST_DIRECT,),
            )
        )

    def _text(self, path: Path) -> str | None:
        try:
            if path.stat().st_size > MAX_MANIFEST_BYTES and path.suffix != ".py":
                self._unreadable(
                    path,
                    UnresolvedReason.TOO_LARGE,
                    f"manifest is {path.stat().st_size} bytes, over the "
                    f"{MAX_MANIFEST_BYTES}-byte limit; its constraints were not read",
                )
                return None
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            self._unreadable(
                path,
                UnresolvedReason.DECODE_ERROR,
                f"manifest is not UTF-8 ({exc.reason}); its constraints were not read",
            )
            return None
        except OSError as exc:
            self._unreadable(
                path, UnresolvedReason.MISSING_TARGET, f"manifest unreadable: {exc}"
            )
            return None

    def _dispatch(self, path: Path) -> None:
        if path in self._seen_files:
            return
        self._seen_files.add(path)
        name = path.name
        if name.endswith(".py"):
            self._read_pep723(path)
        elif name == "pyproject.toml":
            self._read_pyproject(path)
        elif name == "setup.cfg":
            self._read_setup_cfg(path)
        elif name == "Pipfile":
            self._read_pipfile(path)
        elif name in {"environment.yml", "environment.yaml"}:
            self._read_environment_yml(path)
        else:
            self._read_requirements_txt(path)

    # -- requirements.txt ---------------------------------------------

    def _read_requirements_txt(self, path: Path) -> None:
        text = self._text(path)
        if text is None:
            return
        self._note(path)
        rel = self._rel(path)
        lines = text.splitlines()
        buffer = ""
        buffer_line = 0
        for number, physical in enumerate(lines, start=1):
            stripped = physical.strip()
            if buffer:
                stripped = buffer + " " + stripped
            else:
                buffer_line = number
            if stripped.endswith("\\"):
                buffer = stripped[:-1].strip()
                continue
            buffer = ""
            content = _strip_comment(stripped)
            if not content:
                continue
            if content.startswith(_REQ_OPTION_PREFIXES):
                self._requirements_option(content, path, buffer_line, rel)
                continue
            record, reason = _parse_requirement_text(
                content, group="", path=rel, line=buffer_line, dialect="pep508"
            )
            if record is None:
                self.unresolved.append(
                    Unresolved(
                        id=f"dep::req::{rel}::{buffer_line}",
                        reason=UnresolvedReason.SYNTAX_ERROR,
                        span=SourceSpan(path=rel, line=buffer_line),
                        description=f"requirement not parsed: {reason}",
                        attempted=(Method.AST_DIRECT,),
                    )
                )
                continue
            self._accept(record, rel, buffer_line)

    def _requirements_option(
        self, content: str, path: Path, line: int, rel: str
    ) -> None:
        parts = content.split(None, 1)
        flag = parts[0]
        argument = parts[1].strip().strip('"').strip("'") if len(parts) > 1 else ""
        if flag in {"-r", "--requirement", "-c", "--constraint"} and argument:
            included = (path.parent / argument).resolve()
            if not included.is_file():
                self.unresolved.append(
                    Unresolved(
                        id=f"dep::include::{rel}::{line}",
                        reason=UnresolvedReason.MISSING_TARGET,
                        span=SourceSpan(path=rel, line=line),
                        description=(
                            f"{flag} {argument} names a file that does not exist; "
                            "the constraints it holds were not read"
                        ),
                        attempted=(Method.AST_DIRECT,),
                    )
                )
                return
            self._dispatch(included)
            return
        if flag in {"-e", "--editable"}:
            self.unresolved.append(
                Unresolved(
                    id=f"dep::editable::{rel}::{line}",
                    reason=UnresolvedReason.DYNAMIC_NAME,
                    span=SourceSpan(path=rel, line=line),
                    description=(
                        f"editable install {argument!r} declares no version "
                        "constraint; what it resolves to depends on the checkout"
                    ),
                    attempted=(Method.AST_DIRECT,),
                )
            )

    # -- pyproject.toml ------------------------------------------------

    def _read_pyproject(self, path: Path) -> None:
        text = self._text(path)
        if text is None:
            return
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            self._unreadable(
                path, UnresolvedReason.SYNTAX_ERROR, f"pyproject.toml does not parse: {exc}"
            )
            return
        self._note(path)
        rel = self._rel(path)
        lines = text.splitlines()

        project = data.get("project")
        if isinstance(project, dict):
            self._toml_list(project.get("dependencies"), "", rel, lines, path)
            optional = project.get("optional-dependencies")
            if isinstance(optional, dict):
                for group in sorted(optional):
                    self._toml_list(optional[group], group, rel, lines, path)
            requires_python = project.get("requires-python")
            if isinstance(requires_python, str):
                self.python_declarations.append(
                    _PythonDecl(
                        specifier=requires_python,
                        path=rel,
                        line=_find_line(lines, ["requires-python"]) or 1,
                        dialect="pep508",
                    )
                )

        groups = data.get("dependency-groups")
        if isinstance(groups, dict):
            for group in sorted(groups):
                self._toml_list(groups[group], group, rel, lines, path)

        poetry = data.get("tool", {}).get("poetry") if isinstance(data.get("tool"), dict) else None
        if isinstance(poetry, dict):
            self._poetry_table(poetry.get("dependencies"), "", rel, lines)
            self._poetry_table(poetry.get("dev-dependencies"), "dev", rel, lines)
            poetry_groups = poetry.get("group")
            if isinstance(poetry_groups, dict):
                for group in sorted(poetry_groups):
                    entry = poetry_groups[group]
                    if isinstance(entry, dict):
                        self._poetry_table(entry.get("dependencies"), group, rel, lines)

    def _toml_list(
        self,
        value: Any,
        group: str,
        rel: str,
        lines: Sequence[str],
        path: Path,
    ) -> None:
        if value is None:
            return
        if not isinstance(value, list):
            self._unreadable(
                path,
                UnresolvedReason.SYNTAX_ERROR,
                f"dependencies group {group or 'project'!r} is not a list; not read",
            )
            return
        for item in value:
            if not isinstance(item, str):
                self._unreadable(
                    path,
                    UnresolvedReason.SYNTAX_ERROR,
                    f"dependency entry {item!r} in group {group or 'project'!r} is "
                    "not a string; not read",
                )
                continue
            line = _find_line(lines, [item])
            record, reason = _parse_requirement_text(
                item, group=group, path=rel, line=line or 1, dialect="pep508"
            )
            if record is None:
                self.unresolved.append(
                    Unresolved(
                        id=f"dep::req::{rel}::{line}::{item}",
                        reason=UnresolvedReason.SYNTAX_ERROR,
                        span=SourceSpan(path=rel, line=line or 1),
                        description=f"requirement not parsed: {reason}",
                        attempted=(Method.AST_DIRECT,),
                    )
                )
                continue
            self._accept(record, rel, record.line, located=bool(line))

    def _poetry_table(
        self, table: Any, group: str, rel: str, lines: Sequence[str]
    ) -> None:
        if not isinstance(table, dict):
            return
        for name in sorted(table):
            value = table[name]
            line = _find_line(lines, [name, "="])
            if name.lower() == "python":
                if isinstance(value, str):
                    self.python_declarations.append(
                        _PythonDecl(
                            specifier=value, path=rel, line=line or 1, dialect="poetry"
                        )
                    )
                continue
            source_line = lines[line - 1].strip() if line else ""
            if isinstance(value, str):
                specifier = value
                raw = source_line or f"{name} = {value!r}"
            elif isinstance(value, dict) and isinstance(value.get("version"), str):
                specifier = value["version"]
                raw = source_line or f"{name} = {value!r}"
            else:
                self.unresolved.append(
                    Unresolved(
                        id=f"dep::poetry::{rel}::{name}",
                        reason=UnresolvedReason.DYNAMIC_NAME,
                        span=SourceSpan(path=rel, line=line or 1),
                        description=(
                            f"poetry dependency {name!r} declares no version string "
                            f"(it is {type(value).__name__}); no constraint to check"
                        ),
                        attempted=(Method.AST_DIRECT,),
                    )
                )
                specifier = ""
                raw = source_line or f"{name} = {value!r}"
            extras = ()
            if isinstance(value, dict) and isinstance(value.get("extras"), list):
                extras = tuple(sorted(str(e) for e in value["extras"]))
            marker = ""
            if isinstance(value, dict) and isinstance(value.get("markers"), str):
                marker = value["markers"]
            self._accept(
                _RawReq(
                    distribution=normalize_distribution(name),
                    raw=raw,
                    specifier=specifier,
                    extras=extras,
                    marker=marker,
                    group=group,
                    path=rel,
                    line=line or 1,
                    dialect="poetry",
                ),
                rel,
                line or 1,
                located=bool(line),
            )

    # -- setup.cfg ------------------------------------------------------

    def _read_setup_cfg(self, path: Path) -> None:
        text = self._text(path)
        if text is None:
            return
        parser = configparser.ConfigParser()
        try:
            parser.read_string(text)
        except configparser.Error as exc:
            self._unreadable(
                path, UnresolvedReason.SYNTAX_ERROR, f"setup.cfg does not parse: {exc}"
            )
            return
        self._note(path)
        rel = self._rel(path)
        lines = text.splitlines()
        sections: list[tuple[str, str]] = []
        if parser.has_option("options", "install_requires"):
            sections.append((parser.get("options", "install_requires"), ""))
        if parser.has_section("options.extras_require"):
            for group in sorted(parser.options("options.extras_require")):
                sections.append((parser.get("options.extras_require", group), group))
        for blob, group in sections:
            for item in blob.splitlines():
                item = _strip_comment(item.strip())
                if not item:
                    continue
                line = _find_line(lines, [item])
                record, reason = _parse_requirement_text(
                    item, group=group, path=rel, line=line or 1, dialect="pep508"
                )
                if record is None:
                    self.unresolved.append(
                        Unresolved(
                            id=f"dep::req::{rel}::{line}::{item}",
                            reason=UnresolvedReason.SYNTAX_ERROR,
                            span=SourceSpan(path=rel, line=line or 1),
                            description=f"requirement not parsed: {reason}",
                            attempted=(Method.AST_DIRECT,),
                        )
                    )
                    continue
                self._accept(record, rel, record.line, located=bool(line))
        if parser.has_option("options", "python_requires"):
            value = parser.get("options", "python_requires")
            self.python_declarations.append(
                _PythonDecl(
                    specifier=value,
                    path=rel,
                    line=_find_line(lines, ["python_requires"]) or 1,
                    dialect="pep508",
                )
            )

    # -- Pipfile --------------------------------------------------------

    def _read_pipfile(self, path: Path) -> None:
        text = self._text(path)
        if text is None:
            return
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            self._unreadable(
                path, UnresolvedReason.SYNTAX_ERROR, f"Pipfile does not parse: {exc}"
            )
            return
        self._note(path)
        rel = self._rel(path)
        lines = text.splitlines()
        for section, group in (("packages", ""), ("dev-packages", "dev")):
            table = data.get(section)
            if not isinstance(table, dict):
                continue
            for name in sorted(table):
                value = table[name]
                line = _find_line(lines, [name, "="])
                if isinstance(value, str):
                    specifier = "" if value.strip() in {"*", ""} else value
                elif isinstance(value, dict) and isinstance(value.get("version"), str):
                    specifier = "" if value["version"].strip() == "*" else value["version"]
                else:
                    self.unresolved.append(
                        Unresolved(
                            id=f"dep::pipfile::{rel}::{name}",
                            reason=UnresolvedReason.DYNAMIC_NAME,
                            span=SourceSpan(path=rel, line=line or 1),
                            description=(
                                f"Pipfile entry {name!r} declares no version string; "
                                "no constraint to check"
                            ),
                            attempted=(Method.AST_DIRECT,),
                        )
                    )
                    specifier = ""
                self._accept(
                    _RawReq(
                        distribution=normalize_distribution(name),
                        raw=(lines[line - 1].strip() if line else f"{name} = {value!r}"),
                        specifier=specifier,
                        extras=(),
                        marker="",
                        group=group,
                        path=rel,
                        line=line or 1,
                        dialect="pep508",
                    ),
                    rel,
                    line or 1,
                    located=bool(line),
                )
        requires = data.get("requires")
        if isinstance(requires, dict):
            for key in ("python_full_version", "python_version"):
                value = requires.get(key)
                if isinstance(value, str) and value:
                    self.python_declarations.append(
                        _PythonDecl(
                            specifier=f"=={value}.*" if key == "python_version" else f"=={value}",
                            path=rel,
                            line=_find_line(lines, [key]) or 1,
                            dialect="pep508",
                        )
                    )
                    break

    # -- environment.yml -------------------------------------------------

    def _read_environment_yml(self, path: Path) -> None:
        """Conda environment files, parsed line by line.

        Deliberately not routed through PyYAML: the shape used here is a
        ``dependencies:`` list of scalars with an optional nested ``- pip:``
        list, a line-oriented parser reads it exactly, and the line numbers
        come out of the file rather than out of a search. PyYAML is an
        optional adapter in this project and this card must work without it.
        Anything inside ``dependencies:`` that is not that shape becomes an
        `Unresolved` naming the line.
        """
        text = self._text(path)
        if text is None:
            return
        self._note(path)
        rel = self._rel(path)
        in_deps = False
        in_pip = False
        for number, physical in enumerate(text.splitlines(), start=1):
            if not physical.strip() or physical.lstrip().startswith("#"):
                continue
            indent = len(physical) - len(physical.lstrip())
            stripped = physical.strip()
            if indent == 0:
                in_deps = stripped.split(":")[0].strip() == "dependencies"
                in_pip = False
                continue
            if not in_deps:
                continue
            if not stripped.startswith("-"):
                self.unresolved.append(
                    Unresolved(
                        id=f"dep::conda::{rel}::{number}",
                        reason=UnresolvedReason.SYNTAX_ERROR,
                        span=SourceSpan(path=rel, line=number),
                        description=(
                            "line inside `dependencies:` is not a list item; this "
                            "card's line-oriented conda reader did not read it"
                        ),
                        attempted=(Method.AST_DIRECT,),
                    )
                )
                continue
            item = _strip_comment(stripped[1:].strip())
            if item.rstrip().endswith(":"):
                in_pip = item.rstrip()[:-1].strip() == "pip"
                continue
            if not item:
                continue
            if item.startswith("python") and not in_pip:
                specifier = item[len("python"):].strip()
                self.python_declarations.append(
                    _PythonDecl(
                        specifier=specifier,
                        path=rel,
                        line=number,
                        dialect="conda",
                    )
                )
                continue
            dialect = "pep508" if in_pip else "conda"
            record, reason = _parse_requirement_text(
                item, group="pip" if in_pip else "", path=rel, line=number, dialect=dialect
            )
            if record is None:
                self.unresolved.append(
                    Unresolved(
                        id=f"dep::conda::{rel}::{number}",
                        reason=UnresolvedReason.SYNTAX_ERROR,
                        span=SourceSpan(path=rel, line=number),
                        description=f"conda dependency not parsed: {reason}",
                        attempted=(Method.AST_DIRECT,),
                    )
                )
                continue
            self._accept(record, rel, number)

    # -- PEP 723 inline script metadata -----------------------------------

    def _read_pep723(self, path: Path) -> None:
        try:
            if path.stat().st_size > self.max_source_bytes:
                return
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return  # card 1 already reports unreadable source; not re-reported here
        if "# /// script" not in text:
            return
        rel = self._rel(path)
        lines = text.splitlines()
        start = next(
            (i for i, line in enumerate(lines) if line.strip() == "# /// script"), None
        )
        if start is None:
            return
        body: list[str] = []
        body_lines: list[int] = []
        end = None
        for offset in range(start + 1, len(lines)):
            line = lines[offset]
            if line.strip() == "# ///":
                end = offset
                break
            if not line.startswith("#"):
                break
            body.append(line[2:] if line.startswith("# ") else line[1:])
            body_lines.append(offset + 1)
        if end is None:
            self.unresolved.append(
                Unresolved(
                    id=f"dep::pep723::{rel}",
                    reason=UnresolvedReason.SYNTAX_ERROR,
                    span=SourceSpan(path=rel, line=start + 1),
                    description="PEP 723 block opened and never closed with `# ///`",
                    attempted=(Method.AST_DIRECT,),
                )
            )
            return
        self._note(path)
        try:
            data = tomllib.loads("\n".join(body))
        except tomllib.TOMLDecodeError as exc:
            self.unresolved.append(
                Unresolved(
                    id=f"dep::pep723::{rel}",
                    reason=UnresolvedReason.SYNTAX_ERROR,
                    span=SourceSpan(path=rel, line=start + 1),
                    description=f"PEP 723 block does not parse as TOML: {exc}",
                    attempted=(Method.AST_DIRECT,),
                )
            )
            return
        deps = data.get("dependencies")
        if isinstance(deps, list):
            for item in deps:
                if not isinstance(item, str):
                    continue
                offset = _find_line(body, [item])
                line = body_lines[offset - 1] if offset else start + 1
                record, reason = _parse_requirement_text(
                    item, group="", path=rel, line=line, dialect="pep508"
                )
                if record is None:
                    self.unresolved.append(
                        Unresolved(
                            id=f"dep::req::{rel}::{line}::{item}",
                            reason=UnresolvedReason.SYNTAX_ERROR,
                            span=SourceSpan(path=rel, line=line),
                            description=f"requirement not parsed: {reason}",
                            attempted=(Method.AST_DIRECT,),
                        )
                    )
                    continue
                self._accept(record, rel, line, located=bool(offset))
        requires_python = data.get("requires-python")
        if isinstance(requires_python, str):
            offset = _find_line(body, ["requires-python"])
            self.python_declarations.append(
                _PythonDecl(
                    specifier=requires_python,
                    path=rel,
                    line=body_lines[offset - 1] if offset else start + 1,
                    dialect="pep508",
                )
            )

    # -- shared -----------------------------------------------------------

    def _accept(self, record: _RawReq, rel: str, line: int, *, located: bool = True) -> None:
        self.requirements.append(record)
        if not located:
            self.unresolved.append(
                Unresolved(
                    id=f"dep::line::{rel}::{record.distribution}::{record.group}",
                    reason=UnresolvedReason.AMBIGUOUS,
                    span=SourceSpan(path=rel, line=line),
                    description=(
                        f"the text of requirement {record.raw!r} was not found in "
                        f"{rel}; the line recorded is the enclosing table's, not the "
                        "requirement's own"
                    ),
                    attempted=(Method.AST_DIRECT,),
                )
            )
        if record.raw.startswith(record.distribution) and "@" in record.raw.split(";")[0]:
            self.unresolved.append(
                Unresolved(
                    id=f"dep::direct::{rel}::{line}::{record.distribution}",
                    reason=UnresolvedReason.DYNAMIC_NAME,
                    span=SourceSpan(path=rel, line=line),
                    description=(
                        f"{record.raw!r} is a direct reference: the version comes "
                        "from a URL, not from a specifier, so it cannot be compared "
                        "against what is installed"
                    ),
                    attempted=(Method.AST_DIRECT,),
                )
            )


def _strip_comment(text: str) -> str:
    """Drop a trailing ``#`` comment, keeping ``#`` inside a URL fragment."""
    if text.startswith("#"):
        return ""
    for index, char in enumerate(text):
        if char == "#" and (index == 0 or text[index - 1] in " \t"):
            return text[:index].strip()
    return text.strip()


# ---------------------------------------------------------------------------
# Installed — the environment, read as text and never imported
# ---------------------------------------------------------------------------


def _parse_metadata(text: str) -> dict[str, list[str]]:
    """RFC 822 headers out of a METADATA / PKG-INFO file.

    Hand-parsed rather than handed to a library: this must read a file and
    nothing else. The body after the first blank line is the long
    description and is not read.
    """
    headers: dict[str, list[str]] = {}
    key: str | None = None
    for line in text.splitlines():
        if not line.strip():
            break
        if line[:1] in " \t" and key is not None and headers.get(key):
            headers[key][-1] = headers[key][-1] + " " + line.strip()
            continue
        name, separator, value = line.partition(":")
        if not separator:
            continue
        key = name.strip().lower()
        headers.setdefault(key, []).append(value.strip())
    return headers


def _import_names_from_record(text: str) -> tuple[str, ...]:
    """Top-level import names implied by a RECORD file's paths.

    Used only when `top_level.txt` is absent. A distribution whose RECORD
    lists ``yaml/__init__.py`` provides the import name ``yaml``; the mapping
    is derived from the installed metadata, never from a table of guesses.
    """
    names: set[str] = set()
    for line in text.splitlines():
        path = line.split(",", 1)[0].strip().strip('"')
        if not path or path.startswith(("/", "..")):
            continue
        parts = path.replace("\\", "/").split("/")
        head = parts[0]
        if head.endswith((".dist-info", ".egg-info", ".data", ".libs")):
            continue
        if head in {"__pycache__", ".", ""}:
            continue
        if len(parts) == 1:
            if head.endswith(".py"):
                names.add(head[:-3])
            continue
        if any(part == "__pycache__" for part in parts):
            continue
        if head.isidentifier():
            names.add(head)
    return tuple(sorted(names))


class _EnvironmentReader:
    """Reads installed distribution metadata out of an interpreter tree.

    Every file here is opened with :meth:`pathlib.Path.read_text`. Nothing is
    imported, ``pip`` is never invoked, and no live path is handed to
    ``importlib.metadata`` — reading a package's metadata must not run its
    ``__init__``.
    """

    def __init__(self, environment_root: Path, label: str = "") -> None:
        self.root = environment_root
        #: How the environment is named in emitted artifacts. Never the
        #: caller's absolute path: that differs per machine and would break
        #: the byte-identical guarantee the moment it reached a record. The
        #: full path goes to the run summary, which is outside it.
        self.label = label or environment_root.name or "<environment>"
        self.packages: list[InstalledPackage] = []
        self.unresolved: list[Unresolved] = []
        self.located = False
        self.python_version = ""

    def _loc(self, path: Path) -> str:
        try:
            return f"{self.label}/{path.relative_to(self.root).as_posix()}"
        except ValueError:
            return f"{self.label}/{path.name}"

    def read(self) -> None:
        if not self.root.is_dir():
            self.unresolved.append(
                Unresolved(
                    id="dep::environment",
                    reason=UnresolvedReason.MISSING_TARGET,
                    span=SourceSpan(path=self.label, line=1),
                    description=(
                        f"no environment at {self.label}: nothing could be "
                        "read about what is installed. This is NOT a report that "
                        "nothing is installed — the installed half of this card is "
                        "absent for this run"
                    ),
                    attempted=(Method.STRUCTURAL_MATCH,),
                )
            )
            return
        self.located = True
        self._read_python_version()
        seen: dict[str, Path] = {}
        for directory in sorted(self.root.rglob("*.dist-info")):
            if not directory.is_dir():
                continue
            self._read_dist_info(directory, seen)
        for directory in sorted(self.root.rglob("*.egg-info")):
            if directory.is_dir():
                self._read_egg_info(directory, seen)
            elif directory.is_file():
                self._read_egg_info_file(directory, seen)
        if not self.packages:
            self.unresolved.append(
                Unresolved(
                    id="dep::environment::empty",
                    reason=UnresolvedReason.MISSING_TARGET,
                    span=SourceSpan(path=self.label, line=1),
                    description=(
                        f"{self.label} exists but holds no *.dist-info or "
                        "*.egg-info metadata; the installed half of this card could "
                        "not be read from it"
                    ),
                    attempted=(Method.STRUCTURAL_MATCH,),
                )
            )

    def _read_python_version(self) -> None:
        config = self.root / "pyvenv.cfg"
        if config.is_file():
            try:
                text = config.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                text = ""
            for line in text.splitlines():
                name, separator, value = line.partition("=")
                if separator and name.strip() in {"version", "version_info"}:
                    self.python_version = value.strip()
                    return
        for directory in sorted(self.root.rglob("python3.*")):
            match = re.fullmatch(r"python(3\.\d+)", directory.name)
            if match and directory.is_dir():
                self.python_version = match.group(1)
                return

    def _text(self, path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def _import_names(self, directory: Path, distribution: str) -> tuple[str, ...]:
        top_level = directory / "top_level.txt"
        if top_level.is_file():
            text = self._text(top_level) or ""
            names = tuple(
                sorted({line.strip() for line in text.splitlines() if line.strip()})
            )
            if names:
                return names
        record = directory / "RECORD"
        if record.is_file():
            names = _import_names_from_record(self._text(record) or "")
            if names:
                return names
        sources = directory / "SOURCES.txt"
        if sources.is_file():
            names = _import_names_from_record(self._text(sources) or "")
            if names:
                return names
        self.unresolved.append(
            Unresolved(
                id=f"dep::importnames::{distribution}",
                reason=UnresolvedReason.MISSING_TARGET,
                span=SourceSpan(path=self._loc(directory), line=1),
                description=(
                    f"{distribution} has no top_level.txt, RECORD or SOURCES.txt; "
                    "the import names it provides are unknown, so an import that "
                    "belongs to it may be reported as missing"
                ),
                attempted=(Method.STRUCTURAL_MATCH,),
            )
        )
        return ()

    def _record(
        self,
        directory: Path,
        metadata_path: Path,
        seen: dict[str, Path],
    ) -> None:
        text = self._text(metadata_path)
        if text is None:
            self.unresolved.append(
                Unresolved(
                    id=f"dep::installed::{directory.name}",
                    reason=UnresolvedReason.MISSING_TARGET,
                    span=SourceSpan(path=self._loc(metadata_path), line=1),
                    description="installed metadata could not be read",
                    attempted=(Method.STRUCTURAL_MATCH,),
                )
            )
            return
        headers = _parse_metadata(text)
        name = (headers.get("name") or [""])[0]
        version = (headers.get("version") or [""])[0]
        if not name:
            self.unresolved.append(
                Unresolved(
                    id=f"dep::installed::{directory.name}",
                    reason=UnresolvedReason.SYNTAX_ERROR,
                    span=SourceSpan(path=self._loc(metadata_path), line=1),
                    description="installed metadata carries no Name header",
                    attempted=(Method.STRUCTURAL_MATCH,),
                )
            )
            return
        distribution = normalize_distribution(name)
        previous = seen.get(distribution)
        if previous is not None:
            self.unresolved.append(
                Unresolved(
                    id=f"dep::installed::duplicate::{distribution}",
                    reason=UnresolvedReason.AMBIGUOUS,
                    span=SourceSpan(path=self._loc(metadata_path), line=1),
                    description=(
                        f"{distribution} has installed metadata in two places: "
                        f"{self._loc(previous)} and {self._loc(directory)}. Both are "
                        "reported; which one the interpreter loads depends on "
                        "sys.path order, which only a Mode A run can observe"
                    ),
                    attempted=(Method.STRUCTURAL_MATCH,),
                )
            )
        else:
            seen[distribution] = directory
        self.packages.append(
            InstalledPackage(
                id=f"installed::{distribution}::{version}",
                distribution=distribution,
                version=version,
                location=self._loc(directory),
                import_names=self._import_names(directory, distribution),
                requires=tuple(headers.get("requires-dist") or ()),
                provenance=Provenance(
                    method=Method.STRUCTURAL_MATCH,
                    confidence=Confidence.CERTAIN,
                    span=SourceSpan(path=self._loc(metadata_path), line=1),
                    note="read as text from installed metadata; nothing was imported",
                ),
            )
        )

    def _read_dist_info(self, directory: Path, seen: dict[str, Path]) -> None:
        metadata = directory / "METADATA"
        if not metadata.is_file():
            self.unresolved.append(
                Unresolved(
                    id=f"dep::installed::{directory.name}",
                    reason=UnresolvedReason.MISSING_TARGET,
                    span=SourceSpan(path=self._loc(directory), line=1),
                    description=f"{directory.name} holds no METADATA file",
                    attempted=(Method.STRUCTURAL_MATCH,),
                )
            )
            return
        self._record(directory, metadata, seen)

    def _read_egg_info(self, directory: Path, seen: dict[str, Path]) -> None:
        metadata = directory / "PKG-INFO"
        if not metadata.is_file():
            return
        self._record(directory, metadata, seen)

    def _read_egg_info_file(self, path: Path, seen: dict[str, Path]) -> None:
        self._record(path.parent, path, seen)


# ---------------------------------------------------------------------------
# Used — what the source actually imports and touches
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Site:
    """One place a name bound to an outside distribution is used."""

    top: str
    dotted: str
    path: str
    line: int
    col: int


@dataclass
class _FileScan:
    path: str
    bindings: dict[str, str] = field(default_factory=dict)
    binding_lines: dict[str, int] = field(default_factory=dict)
    sites: list[_Site] = field(default_factory=list)
    features: list[tuple[str, str, int, int, int]] = field(default_factory=list)


#: Grammar constructs and the Python that first accepted them. Read off the
#: construct, never inferred: the code contains it or it does not.
_FEATURE_MINIMUMS: tuple[tuple[str, str], ...] = (
    ("match statement", "3.10"),
    ("except* group", "3.11"),
    ("walrus operator", "3.8"),
    ("positional-only parameters", "3.8"),
    ("type alias statement", "3.12"),
    ("PEP 695 type parameters", "3.12"),
)


def _version_tuple(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in text.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _attribute_chain(node: ast.AST) -> list[str] | None:
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        parts.reverse()
        return parts
    return None


def _scan_source(rel_path: str, text: str) -> _FileScan:
    """Imports, third-party attribute paths and grammar features in one file.

    The file is parsed with :mod:`ast`. Nothing in it is imported, executed or
    evaluated.
    """
    scan = _FileScan(path=rel_path)
    tree = ast.parse(text)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                origin = alias.name if alias.asname else alias.name.split(".")[0]
                scan.bindings[bound] = origin
                scan.binding_lines.setdefault(bound, node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound = alias.asname or alias.name
                scan.bindings[bound] = f"{node.module}.{alias.name}"
                scan.binding_lines.setdefault(bound, node.lineno)

    inner: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            inner.add(id(node.value))

    for node in ast.walk(tree):
        chain: list[str] | None = None
        if isinstance(node, ast.Attribute) and id(node) not in inner:
            chain = _attribute_chain(node)
        elif isinstance(node, ast.Name) and id(node) not in inner:
            chain = [node.id]
        if not chain:
            continue
        origin = scan.bindings.get(chain[0])
        if origin is None:
            continue
        dotted = ".".join([origin, *chain[1:]])
        scan.sites.append(
            _Site(
                top=origin.split(".")[0],
                dotted=dotted,
                path=rel_path,
                line=node.lineno,
                col=node.col_offset,
            )
        )

    for node in ast.walk(tree):
        feature = _feature_of(node)
        if feature is None:
            continue
        name, minimum, anchor = feature
        scan.features.append(
            (
                name,
                minimum,
                anchor.lineno,
                anchor.end_lineno or anchor.lineno,
                anchor.col_offset,
            )
        )
    return scan


_MATCH = getattr(ast, "Match", None)
_TRY_STAR = getattr(ast, "TryStar", None)
_TYPE_ALIAS = getattr(ast, "TypeAlias", None)


def _feature_of(node: ast.AST) -> tuple[str, str, Any] | None:
    """The feature this node is, with the node whose span locates it.

    The span comes from the construct itself -- an ``ast.arguments`` node
    carries no position, so a positional-only marker is located by its first
    positional-only parameter rather than by a line someone wrote down.
    """
    if _MATCH is not None and isinstance(node, _MATCH):
        return ("match statement", "3.10", node)
    if _TRY_STAR is not None and isinstance(node, _TRY_STAR):
        return ("except* group", "3.11", node)
    if _TYPE_ALIAS is not None and isinstance(node, _TYPE_ALIAS):
        return ("type alias statement", "3.12", node)
    if isinstance(node, ast.NamedExpr):
        return ("walrus operator", "3.8", node)
    if isinstance(node, ast.arguments) and node.posonlyargs:
        return ("positional-only parameters", "3.8", node.posonlyargs[0])
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        params = getattr(node, "type_params", ())
        if params:
            return ("PEP 695 type parameters", "3.12", params[0])
    return None


# ---------------------------------------------------------------------------
# The card
# ---------------------------------------------------------------------------


_CONTAINER_KINDS = frozenset(
    {ElementKind.FUNCTION, ElementKind.METHOD, ElementKind.PROPERTY, ElementKind.CLASS}
)


class Dependencies:
    """Implements ``DependencyCard``.

    Construct one per analysed tree. ``environment_root`` is the interpreter
    tree whose installed metadata is read -- ``.venv-target`` in production.
    Passing nothing is allowed and is **not** the same as an empty
    environment: every artifact and the summary then say the installed half is
    absent and name the flag that supplies it.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        environment_root: str | Path | None = None,
        elements: Sequence[Element] = (),
        edges: Sequence[Edge] = (),
        reachability: Sequence[Reachability] = (),
        max_source_bytes: int = MAX_SOURCE_BYTES,
    ) -> None:
        self.root = Path(root)
        self.environment_root = Path(environment_root) if environment_root else None
        #: The per-file byte limit this run scans under. Stated in every
        #: record it causes, together with the flag that raises it.
        self.max_source_bytes = int(max_source_bytes)
        self._elements = list(elements)
        self._edges = list(edges)
        self._reachability = list(reachability)

        self._unresolved: list[Unresolved] = []
        self._manifest: _ManifestReader | None = None
        self._environment: _EnvironmentReader | None = None
        self._scans: dict[str, _FileScan] | None = None
        self._requirements: tuple[PackageRequirement, ...] | None = None
        self._installed: tuple[InstalledPackage, ...] | None = None
        self._usage: tuple[PackageUsage, ...] | None = None
        self._interpreter: tuple[InterpreterRequirement, ...] | None = None
        self._truncated_surfaces: list[str] = []
        #: Distributions that an ambiguous import name might belong to. They
        #: are never reported UNUSED: `import shared` is right there in the
        #: source, and "nothing imports it" would be a false statement
        #: dressed as a finding.
        self._ambiguous_distributions: set[str] = set()
        #: requirement id -> the manifest dialect it was written in. Kept
        #: beside the record rather than inside it: `PackageRequirement` is
        #: the contract's shape and this card does not extend it.
        self._dialects: dict[str, str] = {}

    # -- public API ----------------------------------------------------

    def requirements(self) -> Sequence[PackageRequirement]:
        if self._requirements is not None:
            return self._requirements
        reader = self._manifests()
        records: list[PackageRequirement] = []
        counters: dict[tuple[str, str, int], int] = {}
        for raw in reader.requirements:
            key = (raw.path, raw.distribution, raw.line)
            counters[key] = counters.get(key, 0) + 1
            ordinal = counters[key]
            suffix = "" if ordinal == 1 else f"#{ordinal}"
            requirement_id = (
                f"req::{raw.distribution}::{raw.group or '-'}::"
                f"{raw.path}::{raw.line}{suffix}"
            )
            self._dialects[requirement_id] = raw.dialect
            records.append(
                PackageRequirement(
                    id=(
                        f"req::{raw.distribution}::{raw.group or '-'}::"
                        f"{raw.path}::{raw.line}{suffix}"
                    ),
                    distribution=raw.distribution,
                    raw=raw.raw,
                    specifier=raw.specifier,
                    extras=raw.extras,
                    marker=raw.marker,
                    optional_group=raw.group,
                    span=SourceSpan(path=raw.path, line=raw.line),
                    provenance=Provenance(
                        method=Method.AST_DIRECT,
                        confidence=Confidence.CERTAIN,
                        span=SourceSpan(path=raw.path, line=raw.line),
                        note=(
                            f"declared in {raw.path} ({raw.dialect} syntax); the "
                            "specifier is stored exactly as written"
                        ),
                    ),
                )
            )
        self._requirements = tuple(sorted(records, key=lambda r: r.id))
        return self._requirements

    def installed(self, environment_root: str = "") -> Sequence[InstalledPackage]:
        if environment_root:
            candidate = Path(environment_root)
            if self.environment_root != candidate:
                self.environment_root = candidate
                self._environment = None
                self._installed = None
                self._usage = None
        if self._installed is not None:
            return self._installed
        reader = self._env()
        self._installed = tuple(sorted(reader.packages, key=lambda p: p.id))
        return self._installed

    def usage(
        self,
        elements: Sequence[Element] | None = None,
        edges: Sequence[Edge] | None = None,
        reachability: Sequence[Reachability] | None = None,
    ) -> Sequence[PackageUsage]:
        if elements is not None:
            self._elements = list(elements)
            self._usage = None
        if edges is not None:
            self._edges = list(edges)
            self._usage = None
        if reachability is not None:
            self._reachability = list(reachability)
            self._usage = None
        if self._usage is None:
            self._usage = self._build_usage()
        return self._usage

    def interpreter_requirements(
        self, elements: Sequence[Element] | None = None
    ) -> Sequence[InterpreterRequirement]:
        if elements is not None:
            self._elements = list(elements)
            self._interpreter = None
        if self._interpreter is None:
            self._interpreter = self._build_interpreter()
        return self._interpreter

    def findings(self) -> Sequence[Finding]:
        return self._build_findings()

    def unresolved(self) -> Sequence[Unresolved]:
        """Everything this card could not resolve. Constraint 3.

        Computed after the four collectors have run, so calling it last
        returns the complete set.
        """
        self.requirements()
        self.installed()
        self.usage()
        self.interpreter_requirements()
        records = list(self._unresolved)
        records.extend(self._manifests().unresolved)
        records.extend(self._env().unresolved)
        deduped = {record.id: record for record in records}
        return tuple(sorted(deduped.values(), key=lambda u: (u.id, u.description)))

    # -- inputs ---------------------------------------------------------

    def _manifests(self) -> _ManifestReader:
        if self._manifest is None:
            reader = _ManifestReader(self.root, self.max_source_bytes)
            reader.read()
            self._manifest = reader
        return self._manifest

    def environment_label(self) -> str:
        """How the environment is named inside artifacts.

        Relative to the target root when it lives there, otherwise its final
        path component. The owner sees the full path in the run summary; an
        absolute path inside an artifact would make two machines disagree
        byte for byte on the same tree.
        """
        if self.environment_root is None:
            return ""
        try:
            return (
                self.environment_root.resolve()
                .relative_to(self.root.resolve())
                .as_posix()
            )
        except ValueError:
            return self.environment_root.name or self.environment_root.as_posix()

    def _env(self) -> _EnvironmentReader:
        if self._environment is None:
            reader = _EnvironmentReader(
                self.environment_root or Path("/nonexistent"),
                self.environment_label(),
            )
            if self.environment_root is None:
                reader.unresolved.append(
                    Unresolved(
                        id="dep::environment",
                        reason=UnresolvedReason.MISSING_TARGET,
                        span=SourceSpan(path="", line=0),
                        description=(
                            "no environment was given, so nothing is known about what "
                            "is installed. This is NOT a report that the dependencies "
                            "are fine: pass --env PATH (.venv-target in production) "
                            "to read the installed half"
                        ),
                        attempted=(Method.STRUCTURAL_MATCH,),
                    )
                )
            else:
                reader.read()
            self._environment = reader
        return self._environment

    @property
    def environment_located(self) -> bool:
        return self.environment_root is not None and self._env().located

    def _source_scans(self) -> dict[str, _FileScan]:
        """Parse every ``.py`` file under the root, one at a time.

        Memory stays bounded: each tree is parsed, reduced to bindings, use
        sites and grammar features, and dropped. No file's AST outlives the
        loop iteration that produced it.
        """
        if self._scans is not None:
            return self._scans
        scans: dict[str, _FileScan] = {}
        environment = (
            self.environment_root.resolve() if self.environment_root else None
        )
        for path in sorted(self.root.rglob("*.py")):
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            if environment is not None:
                try:
                    path.resolve().relative_to(environment)
                    continue
                except ValueError:
                    pass
            rel = path.relative_to(self.root).as_posix()
            try:
                size = path.stat().st_size
                if size > self.max_source_bytes:
                    megabytes = size / (1024 * 1024)
                    self._unresolved.append(
                        Unresolved(
                            id=f"dep::source::{rel}",
                            reason=UnresolvedReason.TOO_LARGE,
                            span=SourceSpan(path=rel, line=1),
                            description=(
                                f"imports and interpreter requirements were not read "
                                f"for {rel} ({megabytes:.1f} MB, over the dependency "
                                f"scanner's {self.max_source_bytes}-byte limit; raise "
                                f"it with --max-source-mb). Every other analysis read "
                                f"this file under its own limits; this skip is about "
                                f"this file and this scan only"
                            ),
                            attempted=(Method.AST_DIRECT,),
                        )
                    )
                    continue
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                self._unresolved.append(
                    Unresolved(
                        id=f"dep::source::{rel}",
                        reason=UnresolvedReason.DECODE_ERROR,
                        span=SourceSpan(path=rel, line=1),
                        description=(
                            f"file is not UTF-8 at byte {exc.start}; its imports and "
                            "its syntax requirements were not read"
                        ),
                        attempted=(Method.AST_DIRECT,),
                    )
                )
                continue
            except OSError as exc:
                self._unresolved.append(
                    Unresolved(
                        id=f"dep::source::{rel}",
                        reason=UnresolvedReason.MISSING_TARGET,
                        span=SourceSpan(path=rel, line=1),
                        description=f"file unreadable: {exc}",
                        attempted=(Method.AST_DIRECT,),
                    )
                )
                continue
            try:
                scans[rel] = _scan_source(rel, text)
            except SyntaxError as exc:
                self._unresolved.append(
                    Unresolved(
                        id=f"dep::source::{rel}",
                        reason=UnresolvedReason.SYNTAX_ERROR,
                        span=SourceSpan(path=rel, line=exc.lineno or 1,
                                        col=exc.offset),
                        description=(
                            f"{exc.msg}; its imports and its syntax requirements were "
                            f"not read. Parsed by Python "
                            f"{sys.version_info.major}.{sys.version_info.minor} -- a "
                            "target written for a newer Python can fail here"
                        ),
                        attempted=(Method.AST_DIRECT,),
                    )
                )
            except ValueError as exc:
                self._unresolved.append(
                    Unresolved(
                        id=f"dep::source::{rel}",
                        reason=UnresolvedReason.SYNTAX_ERROR,
                        span=SourceSpan(path=rel, line=1),
                        description=f"source could not be parsed: {exc}",
                        attempted=(Method.AST_DIRECT,),
                    )
                )
        self._scans = scans
        return scans

    # -- element geometry ------------------------------------------------

    def _containers(self) -> dict[str, list[tuple[int, int, str]]]:
        by_path: dict[str, list[tuple[int, int, str]]] = {}
        for element in self._elements:
            if element.kind not in _CONTAINER_KINDS:
                continue
            span = element.span
            end = span.end_line or span.line
            by_path.setdefault(span.path, []).append((span.line, end, element.id))
        for spans in by_path.values():
            spans.sort()
        return by_path

    def _module_elements(self) -> dict[str, str]:
        by_path: dict[str, str] = {}
        for element in sorted(self._elements, key=lambda e: e.id):
            if element.kind in {ElementKind.MODULE, ElementKind.PACKAGE}:
                by_path.setdefault(element.span.path, element.id)
        return by_path

    def _import_elements(self) -> dict[tuple[str, int], list[str]]:
        by_site: dict[tuple[str, int], list[str]] = {}
        for element in sorted(self._elements, key=lambda e: e.id):
            if element.kind is ElementKind.IMPORT:
                by_site.setdefault((element.span.path, element.span.line), []).append(
                    element.id
                )
        return by_site

    def _innermost(
        self,
        containers: dict[str, list[tuple[int, int, str]]],
        modules: dict[str, str],
        path: str,
        line: int,
    ) -> str:
        """The element that owns *line*, by span containment.

        Derived from card 1's own spans rather than by re-deriving card 1's
        naming: this card never mints an element ID.
        """
        best: tuple[int, int, str] | None = None
        for start, end, element_id in containers.get(path, ()):
            if start <= line <= end:
                if best is None or (start, -end, element_id) > (best[0], -best[1], best[2]):
                    best = (start, end, element_id)
        if best is not None:
            return best[2]
        return modules.get(path, "")

    # -- the join --------------------------------------------------------

    def _local_top_names(self) -> frozenset[str]:
        names: set[str] = set()
        for element in self._elements:
            if element.kind in {ElementKind.MODULE, ElementKind.PACKAGE} and element.module:
                names.add(element.module.split(".")[0])
        for edge in self._edges:
            if edge.kind.value == "IMPORTS" and edge.target_id:
                names.add(edge.target_id.split("::")[0].split(".")[0])
        for path in self._source_scans():
            head = path.split("/")[0]
            names.add(head[:-3] if head.endswith(".py") else head)
        return frozenset(names)

    def _installed_by_import_name(self) -> dict[str, list[InstalledPackage]]:
        index: dict[str, list[InstalledPackage]] = {}
        for package in self.installed():
            for name in package.import_names:
                index.setdefault(name, []).append(package)
        for packages in index.values():
            packages.sort(key=lambda p: p.id)
        return index

    def _build_usage(self) -> tuple[PackageUsage, ...]:
        scans = self._source_scans()
        containers = self._containers()
        modules = self._module_elements()
        import_elements = self._import_elements()
        local = self._local_top_names()
        stdlib = sys.stdlib_module_names

        # import name -> the facts gathered about it, before any join.
        element_ids: dict[str, set[str]] = {}
        surfaces: dict[str, set[str]] = {}
        spans: dict[str, SourceSpan] = {}

        def note(name: str, path: str, line: int, dotted: str = "") -> None:
            owner = self._innermost(containers, modules, path, line)
            if owner:
                element_ids.setdefault(name, set()).add(owner)
            else:
                element_ids.setdefault(name, set())
            for import_id in import_elements.get((path, line), ()):
                element_ids[name].add(import_id)
            if dotted and dotted.count(".") >= 1:
                surfaces.setdefault(name, set()).add(dotted)
            current = spans.get(name)
            if current is None or (path, line) < (current.path, current.line):
                spans[name] = SourceSpan(path=path, line=line)

        for scan in (scans[key] for key in sorted(scans)):
            for bound, origin in sorted(scan.bindings.items()):
                top = origin.split(".")[0]
                if top in local or top in stdlib:
                    continue
                note(top, scan.path, scan.binding_lines.get(bound, 1))
            for site in scan.sites:
                if site.top in local or site.top in stdlib:
                    continue
                note(site.top, site.path, site.line, site.dotted)

        installed_index = self._installed_by_import_name()
        declared = {r.distribution for r in self.requirements()}
        env_located = self.environment_located

        # import name -> (key, distribution, installed_id, confidence, method, note)
        joined: dict[str, tuple[str, str, str, Confidence, Method, str]] = {}
        for name in sorted(element_ids):
            candidates = installed_index.get(name, [])
            if len(candidates) == 1:
                package = candidates[0]
                joined[name] = (
                    package.distribution,
                    package.distribution,
                    package.id,
                    Confidence.RESOLVED,
                    Method.STRUCTURAL_MATCH,
                    (
                        f"import name {name!r} is provided by {package.distribution} "
                        f"{package.version}, per its installed metadata"
                    ),
                )
                continue
            if len(candidates) > 1:
                joined[name] = (
                    f"?{name}",
                    "",
                    "",
                    Confidence.UNKNOWN,
                    Method.STRUCTURAL_MATCH,
                    (
                        f"import name {name!r} is provided by more than one installed "
                        f"distribution ({', '.join(p.distribution for p in candidates)}); "
                        "none is being claimed"
                    ),
                )
                self._ambiguous_distributions.update(
                    package.distribution for package in candidates
                )
                self._unresolved.append(
                    Unresolved(
                        id=f"dep::ambiguous::{name}",
                        reason=UnresolvedReason.AMBIGUOUS,
                        span=spans.get(name, SourceSpan(path="", line=0)),
                        description=(
                            f"the import name {name!r} maps to {len(candidates)} "
                            "installed distributions; which one provides it cannot be "
                            "decided from metadata alone"
                        ),
                        attempted=(Method.STRUCTURAL_MATCH,),
                        candidate_ids=tuple(p.id for p in candidates),
                        candidate_confidence=Confidence.PROBABLE,
                    )
                )
                continue
            normalized = normalize_distribution(name)
            if normalized in declared:
                joined[name] = (
                    normalized,
                    normalized,
                    "",
                    Confidence.HEURISTIC,
                    Method.NAME_HEURISTIC,
                    (
                        f"import name {name!r} was matched to the declared "
                        f"distribution {normalized!r} by name alone; no installed "
                        "metadata confirmed it"
                    ),
                )
                continue
            joined[name] = (
                normalized,
                normalized,
                "",
                Confidence.UNKNOWN,
                Method.NAME_HEURISTIC,
                (
                    f"no installed metadata maps import name {name!r} to a "
                    f"distribution; {normalized!r} is the import name itself, not a "
                    "resolved distribution"
                ),
            )
            if env_located:
                self._unresolved.append(
                    Unresolved(
                        id=f"dep::unmapped::{name}",
                        reason=UnresolvedReason.MISSING_TARGET,
                        span=spans.get(name, SourceSpan(path="", line=0)),
                        description=(
                            f"the import name {name!r} matches no installed "
                            "distribution's top_level.txt or RECORD in "
                            f"{self.environment_label()}"
                        ),
                        attempted=(Method.STRUCTURAL_MATCH,),
                    )
                )

        reach = {
            r.element_id
            for r in self._reachability
            if r.state is ReachabilityState.REACHES_SINK
        }
        requirement_ids: dict[str, list[str]] = {}
        for requirement in self.requirements():
            requirement_ids.setdefault(requirement.distribution, []).append(requirement.id)

        grouped: dict[str, dict[str, Any]] = {}
        for name in sorted(joined):
            key, distribution, installed_id, confidence, method, text = joined[name]
            bucket = grouped.setdefault(
                key,
                {
                    "distribution": distribution,
                    "import_names": set(),
                    "element_ids": set(),
                    "surfaces": set(),
                    "installed_id": installed_id,
                    "confidence": confidence,
                    "method": method,
                    "notes": [],
                    "span": spans.get(name),
                },
            )
            bucket["import_names"].add(name)
            bucket["element_ids"].update(element_ids.get(name, ()))
            bucket["surfaces"].update(surfaces.get(name, ()))
            bucket["notes"].append(text)
            if installed_id and not bucket["installed_id"]:
                bucket["installed_id"] = installed_id

        records: list[PackageUsage] = []
        for key in sorted(grouped):
            bucket = grouped[key]
            paths = sorted(bucket["surfaces"])
            if len(paths) > MAX_ATTRIBUTE_PATHS:
                self._truncated_surfaces.append(key)
                self._unresolved.append(
                    Unresolved(
                        id=f"dep::surface::{key}",
                        reason=UnresolvedReason.TOO_LARGE,
                        span=bucket["span"] or SourceSpan(path="", line=0),
                        description=(
                            f"{len(paths)} distinct attribute paths were found for "
                            f"{key}; only the first {MAX_ATTRIBUTE_PATHS} in sorted "
                            "order are reported"
                        ),
                        attempted=(Method.AST_DIRECT,),
                    )
                )
                paths = paths[:MAX_ATTRIBUTE_PATHS]
            reaching = sorted(bucket["element_ids"] & reach)
            records.append(
                PackageUsage(
                    id=f"usage::{key}",
                    distribution=bucket["distribution"],
                    import_names=tuple(sorted(bucket["import_names"])),
                    element_ids=tuple(sorted(bucket["element_ids"])),
                    attribute_paths=tuple(paths),
                    reaches_sink=bool(reaching),
                    reaches_sink_element_ids=tuple(reaching),
                    declared_ids=tuple(
                        sorted(requirement_ids.get(bucket["distribution"], ()))
                    ),
                    installed_id=bucket["installed_id"],
                    provenance=Provenance(
                        method=bucket["method"],
                        confidence=bucket["confidence"],
                        span=bucket["span"],
                        note="; ".join(sorted(set(bucket["notes"]))),
                    ),
                )
            )
        return tuple(sorted(records, key=lambda u: u.id))

    # -- interpreter -------------------------------------------------------

    def _build_interpreter(self) -> tuple[InterpreterRequirement, ...]:
        scans = self._source_scans()
        containers = self._containers()
        modules = self._module_elements()
        best: dict[str, tuple[tuple[int, ...], str, str, SourceSpan]] = {}
        for path in sorted(scans):
            scan = scans[path]
            for feature, minimum, line, end_line, col in scan.features:
                span = SourceSpan(path=path, line=line, end_line=end_line, col=col)
                owners = {
                    self._innermost(containers, modules, path, line),
                    modules.get(path, ""),
                }
                for owner in owners:
                    if not owner:
                        continue
                    key = _version_tuple(minimum)
                    current = best.get(owner)
                    if current is None or (key, feature) > (current[0], current[1]):
                        best[owner] = (key, feature, minimum, span)
        records = [
            InterpreterRequirement(
                id=f"pyreq::{element_id}",
                element_id=element_id,
                minimum_python=minimum,
                feature=feature,
                span=span,
                provenance=Provenance(
                    method=Method.AST_DIRECT,
                    confidence=Confidence.CERTAIN,
                    span=span,
                    note=(
                        f"the {feature} at {span.path}:{span.line} is present in this "
                        f"element's source; it does not parse before Python {minimum}"
                    ),
                ),
            )
            for element_id, (_key, feature, minimum, span) in sorted(best.items())
        ]
        return tuple(sorted(records, key=lambda r: r.id))

    # -- findings ----------------------------------------------------------

    def _declared_python_floor(self) -> tuple[str, _PythonDecl] | None:
        """The oldest Python any manifest promises to support.

        The weakest promise is the one that matters: if the project says it
        runs on 3.8 and an element needs 3.12, the project is wrong on 3.8.
        """
        best: tuple[tuple[int, ...], str, _PythonDecl] | None = None
        for declaration in self._manifests().python_declarations:
            clauses = _clauses(declaration.specifier, declaration.dialect)
            if not clauses:
                continue
            for op, raw in clauses:
                if op not in {">=", "==", "~=", ">"}:
                    continue
                text = raw[:-2] if raw.endswith(".*") else raw
                version = parse_version(text)
                if version is None:
                    continue
                key = version.release
                if best is None or key < best[0]:
                    best = (key, text, declaration)
        if best is None:
            return None
        return best[1], best[2]

    def _element_span(self, element_id: str) -> SourceSpan | None:
        for element in self._elements:
            if element.id == element_id:
                return element.span
        return None

    def _anchor(self, usage: PackageUsage) -> tuple[str, SourceSpan]:
        for element_id in usage.element_ids:
            span = self._element_span(element_id)
            if span is not None:
                return element_id, span
        return "", usage.provenance.span or SourceSpan(path="", line=0)

    def _build_findings(self) -> tuple[Finding, ...]:
        usages = self.usage()
        requirements = self.requirements()
        installed = self.installed()
        interpreter = self.interpreter_requirements()

        declared: dict[str, list[PackageRequirement]] = {}
        for requirement in requirements:
            declared.setdefault(requirement.distribution, []).append(requirement)
        installed_by_dist = {package.distribution: package for package in installed}
        used = {u.distribution for u in usages if u.distribution}
        env_located = self.environment_located
        has_manifest = bool(self._manifests().manifests)
        unknown_import_names = sorted(
            package.distribution for package in installed if not package.import_names
        )

        findings: list[Finding] = []

        if not has_manifest:
            self._unresolved.append(
                Unresolved(
                    id="dep::manifests::none",
                    reason=UnresolvedReason.MISSING_TARGET,
                    span=SourceSpan(path="", line=0),
                    description=(
                        "no dependency manifest was found under the target root, so "
                        "what the project declares is unknown. Nothing is reported as "
                        "undeclared, because there is nothing to have declared it in"
                    ),
                    attempted=(Method.AST_DIRECT,),
                )
            )

        for usage in usages:
            element_id, span = self._anchor(usage)
            evidence = (usage.id, *usage.element_ids)
            reach_note = (
                f" {len(usage.reaches_sink_element_ids)} of them reach a decision sink."
                if usage.reaches_sink
                else ""
            )
            if has_manifest and usage.distribution and usage.distribution not in declared:
                findings.append(
                    Finding(
                        id=f"finding::UNDECLARED_DEPENDENCY::{usage.distribution}",
                        kind=FindingKind.UNDECLARED_DEPENDENCY,
                        element_id=element_id,
                        span=span,
                        summary=(
                            f"{usage.distribution} is imported by "
                            f"{len(usage.element_ids)} element(s) and no manifest "
                            f"declares it.{reach_note}"
                        ),
                        hint=(
                            f"Add {usage.distribution} to your requirements with the "
                            "version range you have tested. As it stands it works on "
                            "the machine that happens to have it and fails on the one "
                            "that does not."
                        ),
                        evidence_ids=evidence,
                        provenance=Provenance(
                            method=usage.provenance.method,
                            confidence=usage.provenance.confidence,
                            span=span,
                            note=usage.provenance.note,
                        ),
                    )
                )
            if env_located and usage.distribution and not usage.installed_id:
                confidence = (
                    Confidence.HEURISTIC if unknown_import_names else Confidence.RESOLVED
                )
                caveat = (
                    " Note: "
                    + ", ".join(unknown_import_names)
                    + " publish no import-name metadata, so one of them may provide it."
                    if unknown_import_names
                    else ""
                )
                findings.append(
                    Finding(
                        id=f"finding::MISSING_DEPENDENCY::{usage.id}",
                        kind=FindingKind.MISSING_DEPENDENCY,
                        element_id=element_id,
                        span=span,
                        summary=(
                            f"nothing in {self.environment_label()} "
                            f"provides the import name(s) "
                            f"{', '.join(usage.import_names)}.{reach_note}{caveat}"
                        ),
                        hint=(
                            "Install it into that environment, or point --env at the "
                            "interpreter the engine actually runs under. Until then "
                            "this is an ImportError waiting for the code path that "
                            "reaches it."
                        ),
                        evidence_ids=evidence,
                        provenance=Provenance(
                            method=Method.STRUCTURAL_MATCH,
                            confidence=confidence,
                            span=span,
                            note=(
                                "installed metadata was read as text from "
                                f"{self.environment_label()}"
                            ),
                        ),
                    )
                )

        usage_by_dist = {u.distribution: u for u in usages if u.distribution}
        for distribution in sorted(set(declared) & set(installed_by_dist)):
            package = installed_by_dist[distribution]
            for requirement in sorted(declared[distribution], key=lambda r: r.id):
                if not requirement.specifier:
                    continue
                satisfied, reason = evaluate_specifier(
                    requirement.specifier,
                    package.version,
                    dialect=self._dialects.get(requirement.id, "pep508"),
                )
                usage = usage_by_dist.get(distribution)
                element_id, span = (
                    self._anchor(usage) if usage is not None else ("", requirement.span)
                )
                evidence = (requirement.id, package.id) + (
                    (usage.id, *usage.element_ids) if usage is not None else ()
                )
                group = (
                    f" (group {requirement.optional_group!r})"
                    if requirement.optional_group
                    else ""
                )
                if satisfied is None:
                    findings.append(
                        Finding(
                            id=(
                                f"finding::VERSION_CONFLICT::{distribution}::"
                                f"{requirement.id}::unevaluable"
                            ),
                            kind=FindingKind.VERSION_CONFLICT,
                            element_id=element_id,
                            span=requirement.span,
                            summary=(
                                f"{distribution}: the declared specifier "
                                f"{requirement.specifier!r}{group} could not be "
                                f"evaluated against the installed {package.version} -- "
                                f"{reason}. No verdict is being claimed."
                            ),
                            hint=(
                                "Check this one by hand. This tool refuses to guess "
                                "at satisfaction, so an unevaluated specifier is "
                                "reported rather than assumed fine."
                            ),
                            evidence_ids=evidence,
                            provenance=Provenance(
                                method=Method.STRUCTURAL_MATCH,
                                confidence=Confidence.UNKNOWN,
                                span=requirement.span,
                                note=reason,
                            ),
                        )
                    )
                    continue
                if satisfied:
                    continue
                findings.append(
                    Finding(
                        id=f"finding::VERSION_CONFLICT::{distribution}::{requirement.id}",
                        kind=FindingKind.VERSION_CONFLICT,
                        element_id=element_id,
                        span=requirement.span,
                        summary=(
                            f"{distribution} {package.version} is installed, and "
                            f"{requirement.span.path}:{requirement.span.line} declares "
                            f"{requirement.raw!r}{group} -- {reason}."
                            + (
                                f" {len(usage.element_ids)} element(s) import it."
                                + (
                                    f" {len(usage.reaches_sink_element_ids)} of them "
                                    "reach a decision sink."
                                    if usage.reaches_sink
                                    else ""
                                )
                                if usage is not None
                                else ""
                            )
                        ),
                        hint=(
                            f"Either install a {distribution} inside "
                            f"{requirement.specifier}, or change the declaration to "
                            "the version you are actually running. This is the single "
                            "most common cause of code written against one API meeting "
                            "another."
                        ),
                        evidence_ids=evidence,
                        provenance=Provenance(
                            method=Method.STRUCTURAL_MATCH,
                            confidence=Confidence.RESOLVED,
                            span=requirement.span,
                            note=(
                                f"PEP 440 comparison of {package.version!r} against "
                                f"{requirement.specifier!r}"
                            ),
                        ),
                    )
                )

        if env_located:
            unused = (
                set(declared) & set(installed_by_dist) - used
                - self._ambiguous_distributions
            )
            for distribution in sorted(unused):
                package = installed_by_dist[distribution]
                requirement = sorted(declared[distribution], key=lambda r: r.id)[0]
                findings.append(
                    Finding(
                        id=f"finding::UNUSED_DEPENDENCY::{distribution}",
                        kind=FindingKind.UNUSED_DEPENDENCY,
                        element_id="",
                        span=requirement.span,
                        summary=(
                            f"{distribution} is declared at {requirement.span.path}:"
                            f"{requirement.span.line} and installed at "
                            f"{package.version}, and no import in the tree names it."
                        ),
                        hint=(
                            "Not an error. Check whether it is a leftover: pinning a "
                            "dependency nobody imports constrains every upgrade for no "
                            "reason. It may still be loaded dynamically, or be a "
                            "plugin something else discovers."
                        ),
                        evidence_ids=tuple(
                            sorted(r.id for r in declared[distribution])
                        ) + (package.id,),
                        provenance=Provenance(
                            method=Method.STRUCTURAL_MATCH,
                            confidence=Confidence.HEURISTIC,
                            span=requirement.span,
                            note=(
                                "no static import names it; a dynamic import or an "
                                "entry-point plugin would not be visible here"
                            ),
                        ),
                    )
                )

        findings.extend(self._interpreter_findings(interpreter))
        return tuple(sorted(findings, key=lambda f: f.id))

    def _interpreter_findings(
        self, interpreter: Sequence[InterpreterRequirement]
    ) -> list[Finding]:
        env_version = self._env().python_version if self.environment_located else ""
        floor = self._declared_python_floor()
        if not env_version and floor is None:
            if interpreter:
                self._unresolved.append(
                    Unresolved(
                        id="dep::interpreter::unknown",
                        reason=UnresolvedReason.MISSING_TARGET,
                        span=SourceSpan(path="", line=0),
                        description=(
                            "the minimum Python each element needs was read off the "
                            "grammar, but neither an environment nor a declared "
                            "requires-python was available to compare it against"
                        ),
                        attempted=(Method.STRUCTURAL_MATCH,),
                    )
                )
            return []

        available: list[tuple[tuple[int, ...], str, str]] = []
        if env_version:
            available.append(
                (
                    _version_tuple(env_version),
                    env_version,
                    f"the environment at {self.environment_label()} "
                    f"runs Python {env_version}",
                )
            )
        if floor is not None:
            text, declaration = floor
            available.append(
                (
                    _version_tuple(text),
                    text,
                    f"{declaration.path}:{declaration.line} declares "
                    f"{declaration.specifier!r}, which allows Python {text}",
                )
            )

        out: list[Finding] = []
        for requirement in interpreter:
            needed = _version_tuple(requirement.minimum_python)
            for key, text, why in available:
                if key[: len(needed)] >= needed:
                    continue
                out.append(
                    Finding(
                        id=f"finding::INTERPRETER_TOO_OLD::{requirement.element_id}::{text}",
                        kind=FindingKind.INTERPRETER_TOO_OLD,
                        element_id=requirement.element_id,
                        span=requirement.span,
                        summary=(
                            f"{requirement.element_id} uses a {requirement.feature}, "
                            f"which needs Python {requirement.minimum_python}, and "
                            f"{why}."
                        ),
                        hint=(
                            f"Either raise the Python to "
                            f"{requirement.minimum_python} or rewrite the "
                            f"{requirement.feature} at {requirement.span.path}:"
                            f"{requirement.span.line}. As it stands this file does not "
                            "parse there, so the failure is an import-time SyntaxError, "
                            "not a runtime one."
                        ),
                        evidence_ids=(requirement.id, requirement.element_id),
                        provenance=Provenance(
                            method=Method.AST_DIRECT,
                            confidence=Confidence.RESOLVED,
                            span=requirement.span,
                            note=(
                                f"grammar construct present in source; compared with "
                                f"Python {text}"
                            ),
                        ),
                    )
                )
        return out

    # -- card 16 projection and the owner-facing summary --------------------

    def doc_dependencies(self) -> dict[str, dict[str, Any]]:
        """``DocRecord.dependencies``, one entry per element this card saw.

        An EMPTY dict and a dict saying "this element imports nothing" are
        different claims. Every element in a file this card parsed gets the
        latter; an element this card never looked at (a data file, a config
        key) gets no entry at all, and card 16 leaves its dict empty.
        """
        usages = self.usage()
        interpreter = {r.element_id: r for r in self.interpreter_requirements()}
        requirements = {r.id: r for r in self.requirements()}
        installed = {p.id: p for p in self.installed()}

        scanned_paths = set(self._source_scans())
        by_element: dict[str, dict[str, Any]] = {}
        for element in self._elements:
            if element.span.path in scanned_paths:
                by_element[element.id] = {
                    "distributions": [],
                    "environment": "read" if self.environment_located else "absent",
                    "minimum_python": "",
                    "minimum_python_feature": "",
                }

        for usage in usages:
            for element_id in usage.element_ids:
                entry = by_element.setdefault(
                    element_id,
                    {
                        "distributions": [],
                        "environment": "read" if self.environment_located else "absent",
                        "minimum_python": "",
                        "minimum_python_feature": "",
                    },
                )
                package = installed.get(usage.installed_id)
                entry["distributions"].append(
                    {
                        "distribution": usage.distribution,
                        "import_names": list(usage.import_names),
                        "usage_id": usage.id,
                        "installed_version": package.version if package else "",
                        "declared": [
                            {
                                "specifier": requirements[req_id].specifier,
                                "raw": requirements[req_id].raw,
                                "path": requirements[req_id].span.path,
                                "line": requirements[req_id].span.line,
                                "group": requirements[req_id].optional_group,
                            }
                            for req_id in usage.declared_ids
                            if req_id in requirements
                        ],
                        "reaches_sink": element_id in usage.reaches_sink_element_ids,
                        "confidence": str(usage.provenance.confidence),
                    }
                )

        for element_id, entry in by_element.items():
            entry["distributions"].sort(key=lambda item: (item["distribution"], item["usage_id"]))
            requirement = interpreter.get(element_id)
            if requirement is not None:
                entry["minimum_python"] = requirement.minimum_python
                entry["minimum_python_feature"] = requirement.feature
            if not entry["distributions"]:
                entry["note"] = (
                    "this element imports no distribution from outside the target "
                    "tree; that is a measurement, not a missing answer"
                )
            if not self.environment_located:
                entry["environment_note"] = (
                    "no environment was read, so no installed version is known for "
                    "any of these; pass --env PATH"
                )
        return by_element

    def summary(self) -> dict[str, Any]:
        """Counts and disagreements for the ``analyze`` report.

        Silence about an absent environment would be a lie by omission, so
        ``environment`` always says which of the two states this run is in.
        """
        findings = self.findings()
        counts = {kind.value: 0 for kind in (
            FindingKind.UNDECLARED_DEPENDENCY,
            FindingKind.MISSING_DEPENDENCY,
            FindingKind.VERSION_CONFLICT,
            FindingKind.UNUSED_DEPENDENCY,
            FindingKind.INTERPRETER_TOO_OLD,
        )}
        for finding in findings:
            if finding.kind.value in counts:
                counts[finding.kind.value] += 1
        usages = self.usage()
        return {
            "requirements": len(self.requirements()),
            "manifests": list(self._manifests().manifests),
            "installed": len(self.installed()),
            "environment": (
                self.environment_label()
                if self.environment_located
                else ""
            ),
            "environment_located": self.environment_located,
            "distributions_used": len(usages),
            "reaching_sink": sorted(
                u.distribution or u.id for u in usages if u.reaches_sink
            ),
            "interpreter_requirements": len(self.interpreter_requirements()),
            "findings": counts,
            "unresolved": len(self.unresolved()),
        }
