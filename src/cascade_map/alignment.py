"""Card 13 — the intent registry and alignment verdicts.

This module answers the project's hardest question: *does this element do what it
is meant to do*. It is also the easiest place in CASCADE-MAP to produce confident
nonsense, so every rule below is written to fail towards an honest gap rather than
a confident claim.

The rules, in the order they bind:

* **Intents are owner data.** An intent loaded from the owner's spec is
  ``CONFIRMED``. Anything this module derives from a docstring or a name is
  ``PROPOSED`` (:func:`propose_intents`) and may never ground ``ALIGNED`` or
  ``MISALIGNED`` -- a proposed intent yields ``UNVERIFIABLE``, always.
* **Absence is a reported state.** No intent for an element is ``NO_INTENT``. No
  scenario exercised the element is ``NOT_EXERCISED``. Neither is a pass and
  neither is a failure, and ``NOT_EXERCISED`` is never reported as ``ALIGNED``.
* **A verdict names its evidence.** Every verdict carries the intent it was judged
  against, the event IDs and/or static edge IDs it rests on, a method and a
  confidence. ``MISALIGNED`` additionally names the one expectation and the one
  contradicting observation, both in checkable terms.
* **Unchecked is not aligned.** If any expectation of a confirmed intent could not
  be evaluated, the aggregate verdict is ``UNVERIFIABLE`` and says which one. Only
  a contradiction outranks that.
* **A model is never authoritative.** Escalation to ``claude-sonnet-5`` may attach
  a *proposed reading* to an ``UNVERIFIABLE`` verdict, labelled with its source and
  model ID and tied to concrete event IDs. It never produces a fact, an edge, a
  confidence or a final verdict, and it is fully disabled when
  ``CASCADE_MAP_API_KEY`` is unset. ``ANTHROPIC_API_KEY`` is never read.

Nothing here executes, imports or evaluates target code. Invariants are checked by
comparing *captured* values (card 12's :class:`ValueCapture` records) against a
tiny, explicitly parsed expectation language; there is no ``eval``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

from cascade_map.contracts.interfaces import (
    AlignmentVerdict,
    CaptureStatus,
    Confidence,
    Edge,
    EdgeKind,
    Element,
    EventKind,
    Intent,
    IntentStatus,
    LineageEdge,
    LineageKind,
    Method,
    Provenance,
    Reachability,
    ReachabilityState,
    RunRecord,
    SourceSpan,
    TraceEvent,
    Unresolved,
    UnresolvedReason,
    ValueCapture,
    Verdict,
    canonical_jsonl,
    combine,
    feature_id,
)

__all__ = [
    "MODEL_ID",
    "API_KEY_ENV",
    "PROMPT_PATH",
    "RETURN_VALUE_KEYS",
    "IntentRegistry",
    "load_registry",
    "parse_registry_text",
    "propose_intents",
    "CheckStatus",
    "CheckKind",
    "Check",
    "CheckResult",
    "parse_check",
    "Coverage",
    "PROPOSED_HEADER",
    "PROPOSED_STATEMENT_LIMIT",
    "registry_text",
    "AlignmentEngine",
    "AlignmentModel",
    "AnthropicAlignmentModel",
    "model_available",
    "default_model",
    "load_prompt",
    "intents_jsonl",
    "verdicts_jsonl",
    "coverage_json",
    "issues_jsonl",
]


MODEL_ID = "claude-sonnet-5"
"""The only model this card may escalate to. Proposals only -- never a verdict."""

API_KEY_ENV = "CASCADE_MAP_API_KEY"
"""The only environment variable consulted for a key. ``ANTHROPIC_API_KEY`` is
deliberately never read: it would switch the operator's own billing."""

FORBIDDEN_KEY_ENV = "ANTHROPIC_API_KEY"

PROMPT_PATH = "docs/runtime_prompts/13_alignment.md"
"""Where the escalation prompt lives. If the file is absent a built-in fallback is
used and the verdict's note records which prompt was used."""

RETURN_VALUE_KEYS: tuple[str, ...] = ("return_value", "return", "returns", "result")
"""Keys a ``RETURN`` event's ``values`` map may use for the returned value, tried in
order. The contract does not fix this name; trying a short ordered list and
reporting ``UNVERIFIABLE`` when none is present is the honest fallback."""

_INTENT_ID_PREFIX = "@intent:"
_PROPOSED_ID_PREFIX = "@proposed-intent:"
_VERDICT_ID_PREFIX = "@verdict:"
_ISSUE_ID_PREFIX = "@intent-issue:"
_NO_RUN = "no-run"

_ENTRY_KEYS = frozenset(
    {"id", "element_id", "status", "statement", "invariants", "expected_reads", "expected_writes"}
)
_DOC_KEYS = frozenset({"version", "intents"})
_WRITE_KINDS = frozenset(
    {
        LineageKind.ASSIGNS,
        LineageKind.COLUMN_WRITE,
        LineageKind.CONTAINER_WRITE,
        LineageKind.ATTRIBUTE_WRITE,
        LineageKind.RETURNS,
        LineageKind.MUTATES,
    }
)
_READ_KINDS = frozenset({LineageKind.READS, LineageKind.PARAMETER_BINDING})


# ---------------------------------------------------------------------------
# A very small YAML subset, parsed with line numbers
# ---------------------------------------------------------------------------
#
# Core dependencies are stdlib + networkx, so PyYAML is not available to this
# module. The intents spec is a flat, hand-written document, so a strict subset
# parser is enough -- and it is preferable: it gives an exact line number for every
# entry (constraint 3 wants locations) and it cannot import or construct Python
# objects the way a full YAML loader can.
#
# Supported: block mappings, block sequences, `#` comments, single- and
# double-quoted scalars, plain scalars, and empty flow collections plus flow
# sequences of scalars. Not supported, and reported as a syntax error with its
# line: anchors, aliases, tags, multi-line scalars, tab indentation, nested flow
# collections.


class _ParseError(Exception):
    def __init__(self, message: str, line: int) -> None:
        super().__init__(message)
        self.message = message
        self.line = line


@dataclass(frozen=True, slots=True)
class _Node:
    """A parsed YAML node that remembers where it came from."""

    line: int
    scalar: str | None = None
    quoted: bool = False
    items: tuple[_Node, ...] | None = None
    fields: tuple[tuple[str, _Node], ...] | None = None

    @property
    def is_scalar(self) -> bool:
        return self.scalar is not None

    @property
    def is_seq(self) -> bool:
        return self.items is not None

    @property
    def is_map(self) -> bool:
        return self.fields is not None


def _strip_comment(raw: str) -> str:
    out: list[str] = []
    quote: str | None = None
    for index, char in enumerate(raw):
        if quote is None and char == "#":
            if index == 0 or raw[index - 1] in " \t":
                break
            out.append(char)
            continue
        if quote is None and char in "\"'":
            quote = char
        elif quote is not None and char == quote:
            quote = None
        out.append(char)
    return "".join(out).rstrip()


def _logical_lines(text: str) -> list[list[Any]]:
    lines: list[list[Any]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        leading = raw[: len(raw) - len(raw.lstrip())]
        if "\t" in leading:
            raise _ParseError("tab indentation is not supported", number)
        indent = len(stripped) - len(stripped.lstrip(" "))
        content = stripped.strip()
        if content.startswith(("&", "*", "!", "---", "...", ">", "|")):
            raise _ParseError(f"unsupported YAML construct: {content[:16]!r}", number)
        lines.append([indent, content, number])
    return lines


def _split_key(content: str, line: int) -> tuple[str, str]:
    quote: str | None = None
    for index, char in enumerate(content):
        if quote is None and char in "\"'":
            quote = char
        elif quote is not None and char == quote:
            quote = None
        elif quote is None and char == ":":
            if index + 1 < len(content) and content[index + 1] != " ":
                continue
            key = content[:index].strip()
            if not key:
                raise _ParseError("mapping key is empty", line)
            return _unquote(key), content[index + 1 :].strip()
    raise _ParseError(f"expected 'key: value', got {content!r}", line)


def _unquote(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _is_quoted(text: str) -> bool:
    return len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'"


def _scalar_node(raw: str, line: int) -> _Node:
    if raw in ("[]", "{}"):
        return _Node(line=line, items=()) if raw == "[]" else _Node(line=line, fields=())
    if raw.startswith("["):
        if not raw.endswith("]"):
            raise _ParseError("unterminated flow sequence", line)
        body = raw[1:-1].strip()
        if not body:
            return _Node(line=line, items=())
        if "[" in body or "{" in body:
            raise _ParseError("nested flow collections are not supported", line)
        items = tuple(
            _Node(line=line, scalar=_unquote(part.strip()), quoted=_is_quoted(part.strip()))
            for part in _split_flow(body, line)
        )
        return _Node(line=line, items=items)
    if raw.startswith("{"):
        raise _ParseError("flow mappings are not supported", line)
    if raw[0] in "&*!|>":
        # An anchor, alias, tag or block scalar in a value position would otherwise be
        # read as the literal text of an owner's intent, which is a silent misreading
        # of owner data. Refuse it here, with its line.
        raise _ParseError(
            f"unsupported YAML construct in value: {raw[:16]!r}; quote it if it is literal text",
            line,
        )
    return _Node(line=line, scalar=_unquote(raw), quoted=_is_quoted(raw))


def _split_flow(body: str, line: int) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for char in body:
        if quote is None and char in "\"'":
            quote = char
        elif quote is not None and char == quote:
            quote = None
        if quote is None and char == ",":
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    if quote is not None:
        raise _ParseError("unterminated quoted scalar", line)
    parts.append("".join(current))
    return [part for part in parts if part.strip()]


def _looks_like_mapping(content: str) -> bool:
    """True when the line is ``key: value`` or ``key:`` outside quotes.

    ``run_linear::step_one`` and ``returns.value == 1`` are scalars, not mappings:
    a colon only opens a value when a space or the end of the line follows it.
    """
    quote: str | None = None
    for index, char in enumerate(content):
        if quote is None and char in "\"'":
            quote = char
        elif quote is not None and char == quote:
            quote = None
        elif quote is None and char == ":":
            if index + 1 == len(content) or content[index + 1] == " ":
                return bool(content[:index].strip())
    return False


def _parse_node(lines: list[list[Any]], pos: int, indent: int) -> tuple[_Node, int]:
    content = str(lines[pos][1])
    if content == "-" or content.startswith("- "):
        return _parse_seq(lines, pos, indent)
    if _looks_like_mapping(content):
        return _parse_map(lines, pos, indent)
    line = int(lines[pos][2])
    if pos + 1 < len(lines) and int(lines[pos + 1][0]) > indent:
        raise _ParseError(f"expected 'key: value', got {content!r}", line)
    return _scalar_node(content, line), pos + 1


def _parse_seq(lines: list[list[Any]], pos: int, indent: int) -> tuple[_Node, int]:
    start_line = int(lines[pos][2])
    items: list[_Node] = []
    while pos < len(lines) and int(lines[pos][0]) == indent:
        content = str(lines[pos][1])
        line = int(lines[pos][2])
        if content != "-" and not content.startswith("- "):
            break
        rest = content[1:]
        if not rest.strip():
            pos += 1
            if pos < len(lines) and int(lines[pos][0]) > indent:
                node, pos = _parse_node(lines, pos, int(lines[pos][0]))
                items.append(node)
            else:
                items.append(_Node(line=line, scalar=""))
            continue
        extra = len(rest) - len(rest.lstrip(" "))
        inner_indent = indent + 1 + extra
        virtual: list[list[Any]] = [[inner_indent, rest.strip(), line]]
        pos += 1
        while pos < len(lines) and int(lines[pos][0]) > indent:
            virtual.append(lines[pos])
            pos += 1
        node, consumed = _parse_node(virtual, 0, inner_indent)
        if consumed != len(virtual):
            raise _ParseError("inconsistent indentation inside sequence item", int(virtual[consumed][2]))
        items.append(node)
    return _Node(line=start_line, items=tuple(items)), pos


def _parse_map(lines: list[list[Any]], pos: int, indent: int) -> tuple[_Node, int]:
    start_line = int(lines[pos][2])
    fields: list[tuple[str, _Node]] = []
    while pos < len(lines) and int(lines[pos][0]) == indent:
        content = str(lines[pos][1])
        line = int(lines[pos][2])
        if content.startswith("- "):
            break
        key, rest = _split_key(content, line)
        pos += 1
        if rest:
            fields.append((key, _scalar_node(rest, line)))
            continue
        if pos < len(lines) and int(lines[pos][0]) > indent:
            node, pos = _parse_node(lines, pos, int(lines[pos][0]))
            # A block value is reported at its key's line: that is where the owner
            # looks when the tool says the entry is malformed.
            fields.append((key, replace(node, line=line)))
        else:
            fields.append((key, _Node(line=line, scalar="")))
    return _Node(line=start_line, fields=tuple(fields)), pos


def _parse_document(text: str) -> _Node:
    lines = _logical_lines(text)
    if not lines:
        return _Node(line=1, fields=())
    indent = int(lines[0][0])
    node, pos = _parse_node(lines, 0, indent)
    if pos != len(lines):
        raise _ParseError("unexpected indentation", int(lines[pos][2]))
    return node


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IntentRegistry:
    """Owner-confirmed intents, keyed by stable element ID.

    ``issues`` is a first-class output, never a log line: unknown element IDs,
    duplicate intents and malformed entries each land here with a
    :class:`SourceSpan`. ``rejected_element_ids`` names elements whose entry was
    malformed enough that loading it would have been a guess -- those elements are
    judged ``UNVERIFIABLE``, not ``NO_INTENT``, because the owner *did* write
    something and it is this tool that could not read it.
    """

    source_path: str
    present: bool
    intents: tuple[Intent, ...] = ()
    issues: tuple[Unresolved, ...] = ()
    rejected_element_ids: tuple[str, ...] = ()
    ambiguous_element_ids: tuple[str, ...] = ()
    unknown_check_performed: bool = False

    def for_element(self, element_id: str) -> tuple[Intent, ...]:
        return tuple(intent for intent in self.intents if intent.element_id == element_id)

    @property
    def by_element(self) -> Mapping[str, tuple[Intent, ...]]:
        grouped: dict[str, list[Intent]] = {}
        for intent in self.intents:
            grouped.setdefault(intent.element_id, []).append(intent)
        return {key: tuple(value) for key, value in sorted(grouped.items())}


class _IssueSink:
    """Mints deterministic, collision-free IDs for registry issues."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._seen: dict[str, int] = {}
        self.issues: list[Unresolved] = []

    def add(
        self,
        reason: UnresolvedReason,
        line: int,
        description: str,
        *,
        candidate_ids: tuple[str, ...] = (),
    ) -> None:
        base = f"{_ISSUE_ID_PREFIX}{self._path}:{line}:{reason.value}"
        count = self._seen.get(base, 0) + 1
        self._seen[base] = count
        issue_id = base if count == 1 else f"{base}#{count}"
        self.issues.append(
            Unresolved(
                id=issue_id,
                reason=reason,
                span=SourceSpan(path=self._path, line=line),
                description=description,
                attempted=(Method.AST_DIRECT,),
                candidate_ids=candidate_ids,
            )
        )


def parse_registry_text(
    text: str,
    path: str,
    known_element_ids: Iterable[str] | None = None,
) -> IntentRegistry:
    """Parse an intents document already in memory. See :func:`load_registry`."""
    sink = _IssueSink(path)
    known: frozenset[str] | None = None if known_element_ids is None else frozenset(known_element_ids)

    try:
        document = _parse_document(text)
    except _ParseError as error:
        sink.add(UnresolvedReason.SYNTAX_ERROR, error.line, f"intent spec is malformed: {error.message}")
        return IntentRegistry(
            source_path=path,
            present=True,
            issues=tuple(sorted(sink.issues, key=lambda issue: issue.id)),
            unknown_check_performed=known is not None,
        )

    entries = _document_entries(document, sink)
    intents: list[Intent] = []
    rejected: list[str] = []
    seen_ids: dict[str, int] = {}
    seen_elements: dict[str, int] = {}

    for entry in entries:
        intent, element_id, ok = _entry_to_intent(entry, path, sink)
        if not ok:
            if element_id:
                rejected.append(element_id)
            continue
        assert intent is not None
        if intent.id in seen_ids:
            sink.add(
                UnresolvedReason.ID_COLLISION,
                entry.line,
                f"duplicate intent id {intent.id!r}; first declared at line {seen_ids[intent.id]}",
                candidate_ids=(intent.id,),
            )
        else:
            seen_ids[intent.id] = entry.line
        if intent.element_id in seen_elements:
            sink.add(
                UnresolvedReason.AMBIGUOUS,
                entry.line,
                (
                    f"duplicate intent for element {intent.element_id!r}; first declared at line "
                    f"{seen_elements[intent.element_id]}. Both are kept and the element is judged "
                    f"UNVERIFIABLE: picking one would be a guess."
                ),
                candidate_ids=(intent.element_id,),
            )
        else:
            seen_elements[intent.element_id] = entry.line
        if known is not None and intent.element_id not in known:
            sink.add(
                UnresolvedReason.MISSING_TARGET,
                entry.line,
                f"intent names unknown element id {intent.element_id!r}",
                candidate_ids=(intent.element_id,),
            )
        intents.append(intent)

    ambiguous = tuple(
        sorted({intent.element_id for intent in intents if _count(intents, intent.element_id) > 1})
    )
    return IntentRegistry(
        source_path=path,
        present=True,
        intents=tuple(sorted(intents, key=lambda item: (item.id, item.element_id))),
        issues=tuple(sorted(sink.issues, key=lambda issue: issue.id)),
        rejected_element_ids=tuple(sorted(set(rejected))),
        ambiguous_element_ids=ambiguous,
        unknown_check_performed=known is not None,
    )


def _count(intents: Sequence[Intent], element_id: str) -> int:
    return sum(1 for intent in intents if intent.element_id == element_id)


def _document_entries(document: _Node, sink: _IssueSink) -> list[_Node]:
    if document.is_seq:
        return list(document.items or ())
    if not document.is_map:
        sink.add(
            UnresolvedReason.SYNTAX_ERROR,
            document.line,
            "intent spec must be a mapping with an 'intents:' key, or a sequence of entries",
        )
        return []
    entries: list[_Node] = []
    for key, value in document.fields or ():
        if key not in _DOC_KEYS:
            sink.add(
                UnresolvedReason.SYNTAX_ERROR,
                value.line,
                f"unknown top-level key {key!r}; expected one of {sorted(_DOC_KEYS)}",
            )
            continue
        if key != "intents":
            continue
        if value.is_seq:
            entries.extend(value.items or ())
        elif value.is_scalar and not value.scalar:
            continue
        else:
            sink.add(
                UnresolvedReason.SYNTAX_ERROR, value.line, "'intents' must be a sequence of entries"
            )
    return entries


def _entry_to_intent(
    entry: _Node, path: str, sink: _IssueSink
) -> tuple[Intent | None, str, bool]:
    """Validate one entry. Returns (intent, element_id, ok).

    A malformed entry is reported with its line and *not* loaded: judging against
    half an entry the owner wrote would be exactly the confident nonsense this card
    exists to avoid. The element is instead returned so it can be judged
    ``UNVERIFIABLE``.
    """
    if not entry.is_map:
        sink.add(UnresolvedReason.SYNTAX_ERROR, entry.line, "intent entry must be a mapping")
        return None, "", False

    fields = dict(entry.fields or ())
    element_node = fields.get("element_id")
    element_id = (element_node.scalar or "").strip() if element_node and element_node.is_scalar else ""

    ok = True
    for key, value in entry.fields or ():
        if key not in _ENTRY_KEYS:
            sink.add(
                UnresolvedReason.SYNTAX_ERROR,
                value.line,
                f"unknown key {key!r} in intent entry for {element_id or '<no element_id>'}; "
                f"expected one of {sorted(_ENTRY_KEYS)}. The entry is not loaded.",
                candidate_ids=(element_id,) if element_id else (),
            )
            ok = False

    if len(fields) != len(entry.fields or ()):
        sink.add(
            UnresolvedReason.SYNTAX_ERROR,
            entry.line,
            f"duplicate key in intent entry for {element_id or '<no element_id>'}",
            candidate_ids=(element_id,) if element_id else (),
        )
        ok = False

    if not element_id:
        sink.add(
            UnresolvedReason.SYNTAX_ERROR,
            entry.line,
            "intent entry has no 'element_id'; an intent must be keyed by a stable element ID",
        )
        return None, "", False

    status_node = fields.get("status")
    status_text = (status_node.scalar or "").strip() if status_node and status_node.is_scalar else ""
    if status_text not in (IntentStatus.CONFIRMED.value, IntentStatus.PROPOSED.value):
        sink.add(
            UnresolvedReason.SYNTAX_ERROR,
            status_node.line if status_node else entry.line,
            f"intent for {element_id!r} has status {status_text or '<missing>'!r}; it must be "
            f"'CONFIRMED' or 'PROPOSED'. There is no default: an unstated status must never be "
            f"read as owner confirmation.",
            candidate_ids=(element_id,),
        )
        ok = False

    statement_node = fields.get("statement")
    statement = (
        (statement_node.scalar or "").strip()
        if statement_node is not None and statement_node.is_scalar
        else ""
    )
    if not statement:
        sink.add(
            UnresolvedReason.SYNTAX_ERROR,
            statement_node.line if statement_node else entry.line,
            f"intent for {element_id!r} has no 'statement'",
            candidate_ids=(element_id,),
        )
        ok = False

    lists: dict[str, tuple[str, ...]] = {}
    for key in ("invariants", "expected_reads", "expected_writes"):
        node = fields.get(key)
        values, list_ok = _string_list(node, key, element_id, sink)
        lists[key] = values
        ok = ok and list_ok

    id_node = fields.get("id")
    intent_id = (id_node.scalar or "").strip() if id_node and id_node.is_scalar else ""
    if not intent_id:
        intent_id = f"{_INTENT_ID_PREFIX}{element_id}"

    if not ok:
        return None, element_id, False

    intent = Intent(
        id=intent_id,
        element_id=element_id,
        status=IntentStatus(status_text),
        statement=statement,
        invariants=lists["invariants"],
        expected_reads=lists["expected_reads"],
        expected_writes=lists["expected_writes"],
        provenance=Provenance(
            method=Method.AST_DIRECT,
            confidence=Confidence.CERTAIN,
            span=SourceSpan(path=path, line=entry.line),
            note="owner-confirmed intent spec" if status_text == "CONFIRMED" else "spec-declared proposal",
        ),
    )
    return intent, element_id, True


def _string_list(
    node: _Node | None, key: str, element_id: str, sink: _IssueSink
) -> tuple[tuple[str, ...], bool]:
    if node is None:
        return (), True
    if node.is_scalar and not node.scalar:
        return (), True
    if not node.is_seq:
        sink.add(
            UnresolvedReason.SYNTAX_ERROR,
            node.line,
            f"{key!r} of intent for {element_id!r} must be a list of strings",
            candidate_ids=(element_id,),
        )
        return (), False
    values: list[str] = []
    ok = True
    for item in node.items or ():
        if not item.is_scalar:
            sink.add(
                UnresolvedReason.SYNTAX_ERROR,
                item.line,
                f"entry in {key!r} of intent for {element_id!r} must be a string",
                candidate_ids=(element_id,),
            )
            ok = False
            continue
        text = (item.scalar or "").strip()
        if not text:
            sink.add(
                UnresolvedReason.SYNTAX_ERROR,
                item.line,
                f"empty entry in {key!r} of intent for {element_id!r}",
                candidate_ids=(element_id,),
            )
            ok = False
            continue
        values.append(text)
    return tuple(values), ok


def load_registry(
    path: str | os.PathLike[str] | None,
    known_element_ids: Iterable[str] | None = None,
) -> IntentRegistry:
    """Load the owner-confirmed intents spec named in ``TARGET_PROFILE.md``.

    ``None``, an empty string or the literal ``"none"`` mean the owner declared no
    spec. That is not an error: the registry is empty, ``present`` is ``False``, and
    every element is judged ``NO_INTENT``.

    A missing file, an undecodable file or a malformed document each produce an
    :class:`Unresolved` record with the path and line -- never a silent empty
    registry, which would be indistinguishable from "the owner has no intents".
    """
    if path is None:
        return IntentRegistry(source_path="", present=False)
    text_path = str(path)
    if not text_path or text_path.strip().lower() == "none":
        return IntentRegistry(source_path="", present=False)

    sink = _IssueSink(text_path)
    file_path = Path(text_path)
    if not file_path.is_file():
        sink.add(
            UnresolvedReason.MISSING_TARGET,
            1,
            f"intent spec {text_path!r} named in TARGET_PROFILE.md does not exist",
        )
        return IntentRegistry(
            source_path=text_path,
            present=False,
            issues=tuple(sink.issues),
            unknown_check_performed=known_element_ids is not None,
        )
    try:
        text = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        sink.add(
            UnresolvedReason.DECODE_ERROR,
            1,
            f"intent spec {text_path!r} is not valid UTF-8: {error.reason}",
        )
        return IntentRegistry(
            source_path=text_path,
            present=True,
            issues=tuple(sink.issues),
            unknown_check_performed=known_element_ids is not None,
        )
    return parse_registry_text(text, text_path, known_element_ids)


# ---------------------------------------------------------------------------
# Proposed intents — never authoritative
# ---------------------------------------------------------------------------


def propose_intents(elements: Sequence[Element]) -> tuple[Intent, ...]:
    """Derive ``PROPOSED`` intents from docstrings and names.

    These exist so the owner has something to confirm or correct, and for no other
    reason. Every one is ``PROPOSED``, carries ``NAME_HEURISTIC``/``HEURISTIC``
    provenance, and carries **no** invariants: an invariant this tool invented and
    then checked against the run would be marking its own homework. A proposed
    intent can never produce ``ALIGNED`` or ``MISALIGNED`` -- see
    :meth:`AlignmentEngine.judge`.
    """
    proposals: list[Intent] = []
    for element in elements:
        statement, source = _proposed_statement(element)
        if not statement:
            continue
        proposals.append(
            Intent(
                id=f"{_PROPOSED_ID_PREFIX}{element.id}",
                element_id=element.id,
                status=IntentStatus.PROPOSED,
                statement=statement,
                provenance=Provenance(
                    method=Method.NAME_HEURISTIC,
                    confidence=Confidence.HEURISTIC,
                    span=element.span,
                    note=f"proposed from {source}; not owner-confirmed and not binding",
                ),
            )
        )
    return tuple(sorted(proposals, key=lambda intent: intent.id))


#: The longest statement a proposed entry carries. A docstring's first line can
#: be a paragraph; a starter registry the owner cannot read is a starter
#: registry they will not confirm.
PROPOSED_STATEMENT_LIMIT = 200


def _yaml_scalar(text: str) -> str:
    """One double-quoted scalar this module's own parser reads back exactly.

    The parser deliberately performs no escape processing (see its header), so
    the emitter must not produce anything needing it: whitespace is collapsed
    and a double quote inside the text becomes a single one. Lossy on purpose
    and only ever applied to PROPOSED text the owner is being asked to rewrite
    -- owner-confirmed statements are never round-tripped through here.
    """
    flat = " ".join(str(text).split()).replace('"', "'")
    if len(flat) > PROPOSED_STATEMENT_LIMIT:
        flat = flat[: PROPOSED_STATEMENT_LIMIT - 3].rstrip() + "..."
    return '"' + flat + '"'


PROPOSED_HEADER = """\
# PROPOSED intents -- NOT owner-confirmed, and binding on nothing.
#
# Every entry below was derived by CASCADE-MAP from a docstring or a name. A
# PROPOSED intent can never produce an ALIGNED or a MISALIGNED verdict: it is
# reported UNVERIFIABLE until you change its `status` to CONFIRMED, which is
# you taking responsibility for the statement.
#
# What to do with this file:
#   1. Delete every entry you do not care about. A shorter registry you mean
#      is worth more than a long one you skimmed.
#   2. Rewrite each `statement` you keep so it says what the element is MEANT
#      to do, not what its docstring happens to say.
#   3. Add `invariants:` that can be checked. The language is:
#        returns.type == int            arg.NAME.value >= 0
#        values.KEY.status == FULL      calls <element id>
#        not calls <element id>         runs before <element id>
#        runs after <element id>        reaches decision
#        does not reach decision
#      `reaches decision` is the one that answers "did I build this and never
#      plug it in": it is checked against the static call graph, so `analyze`
#      settles it without running anything.
#   4. Set `status: CONFIRMED` on the ones you stand behind.
#   5. Run `metatron analyze <target> --intents <this file>`.
#
# Anything this tool cannot parse in here is reported with its line number and
# is never skipped.
version: 1
intents:
"""


def registry_text(intents: Sequence[Intent]) -> str:
    """A starter intents document, ready for the owner to edit and confirm.

    Every entry is written with the status it actually carries. Nothing is
    promoted on the way out: a PROPOSED intent that reached a file marked
    CONFIRMED would be this card lying about who said it.
    """
    lines = [PROPOSED_HEADER]
    for intent in sorted(intents, key=lambda item: (item.element_id, item.id)):
        lines.append(f"  - element_id: {_yaml_scalar(intent.element_id)}")
        lines.append(f"    status: {intent.status.value}")
        lines.append(f"    statement: {_yaml_scalar(intent.statement)}")
        if intent.invariants:
            lines.append("    invariants:")
            lines += [f"      - {_yaml_scalar(text)}" for text in intent.invariants]
        else:
            lines.append("    # invariants: []   <- add checkable expectations here")
        if intent.expected_reads:
            lines.append("    expected_reads:")
            lines += [f"      - {_yaml_scalar(text)}" for text in intent.expected_reads]
        if intent.expected_writes:
            lines.append("    expected_writes:")
            lines += [f"      - {_yaml_scalar(text)}" for text in intent.expected_writes]
    if len(lines) == 1:
        lines.append(
            "  # No element carried a docstring or a usable name, so nothing was "
            "proposed."
        )
        lines.append("  []")
    return "\n".join(lines) + "\n"


def _proposed_statement(element: Element) -> tuple[str, str]:
    docstring = (element.docstring or "").strip()
    if docstring:
        first = docstring.splitlines()[0].strip()
        if first:
            return first, "docstring"
    name = (element.name or "").strip()
    if name:
        words = [part for part in re.split(r"[_\W]+", name) if part]
        if words:
            return f"Named {name!r}, suggesting it {' '.join(words).lower()}.", "name"
    return "", ""


# ---------------------------------------------------------------------------
# The expectation language
# ---------------------------------------------------------------------------


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNVERIFIABLE = "UNVERIFIABLE"


class CheckKind(StrEnum):
    VALUE = "VALUE"
    TYPE = "TYPE"
    CAPTURE_STATUS = "CAPTURE_STATUS"
    CALLS = "CALLS"
    NOT_CALLS = "NOT_CALLS"
    RUNS_BEFORE = "RUNS_BEFORE"
    RUNS_AFTER = "RUNS_AFTER"
    REACHES_DECISION = "REACHES_DECISION"
    """The owner declares this element live: something reaches a decision sink
    from it. Checked against card 3's `Reachability`, which is static evidence,
    so this is the one expectation kind a Mode 1 run can settle on its own."""

    NOT_REACHES_DECISION = "NOT_REACHES_DECISION"
    """The owner declares this element deliberately off the decision path."""

    WRITES = "WRITES"
    READS = "READS"
    UNPARSEABLE = "UNPARSEABLE"


@dataclass(frozen=True, slots=True)
class _Literal:
    kind: str
    """``int``, ``str`` (was quoted), ``bare`` or ``list``."""

    raw: str
    number: int = 0
    members: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Check:
    """One parsed, checkable expectation drawn from an intent."""

    kind: CheckKind
    text: str
    """The invariant exactly as the owner wrote it. Quoted back in every verdict."""

    subject: str = ""
    """``returns``, an argument name, a captured-value key, or an element/feature ID."""

    op: str = ""
    literal: _Literal | None = None
    reason: str = ""
    """Why the check could not be parsed, when kind is UNPARSEABLE."""


@dataclass(frozen=True, slots=True)
class CheckResult:
    status: CheckStatus
    expectation: str
    observation: str
    evidence_ids: tuple[str, ...] = ()
    confidence: Confidence = Confidence.UNKNOWN


_COMPARISON = re.compile(
    r"^(?P<lhs>[A-Za-z_][A-Za-z0-9_.\[\]\"'@:-]*)\s*(?P<op>==|!=|>=|<=|>|<|\bin\b)\s+(?P<rhs>.+)$"
)
_CALLS = re.compile(r"^calls\s+(?P<target>\S+)$")
_NOT_CALLS = re.compile(r"^(?:not\s+calls|does\s+not\s+call)\s+(?P<target>\S+)$")
_RUNS_BEFORE = re.compile(r"^runs\s+before\s+(?P<target>\S+)$")
_RUNS_AFTER = re.compile(r"^runs\s+after\s+(?P<target>\S+)$")
#: "this element is plugged in" / "this element is deliberately not plugged in",
#: in the owner's own words. `reaches decision` is the invariant that turns
#: "I declared this strategy live" into something the static map can contradict.
_REACHES = re.compile(r"^reaches\s+(?:the\s+)?(?:decision|sink)$")
_NOT_REACHES = re.compile(
    r"^(?:not\s+reaches|does\s+not\s+reach)\s+(?:the\s+)?(?:decision|sink)$"
)
_LHS = re.compile(
    r"^(?:returns|return|arg\.(?P<arg>[A-Za-z_][A-Za-z0-9_]*)|values\.(?P<key>[^.]+))"
    r"\.(?P<attr>type|value|status)$"
)


def parse_check(text: str) -> Check:
    """Parse one invariant string. Never raises: an unparseable invariant is a
    ``Check`` of kind ``UNPARSEABLE`` carrying its reason, which makes the element
    ``UNVERIFIABLE`` rather than quietly checking fewer things than the owner wrote.
    """
    stripped = text.strip()
    if not stripped:
        return Check(kind=CheckKind.UNPARSEABLE, text=text, reason="empty invariant")

    for pattern, kind in (
        (_NOT_REACHES, CheckKind.NOT_REACHES_DECISION),
        (_REACHES, CheckKind.REACHES_DECISION),
    ):
        if pattern.match(stripped):
            return Check(kind=kind, text=stripped, subject="")

    for pattern, kind in (
        (_NOT_CALLS, CheckKind.NOT_CALLS),
        (_CALLS, CheckKind.CALLS),
        (_RUNS_BEFORE, CheckKind.RUNS_BEFORE),
        (_RUNS_AFTER, CheckKind.RUNS_AFTER),
    ):
        match = pattern.match(stripped)
        if match:
            return Check(kind=kind, text=stripped, subject=match.group("target"))

    match = _COMPARISON.match(stripped)
    if not match:
        return Check(
            kind=CheckKind.UNPARSEABLE,
            text=stripped,
            reason=(
                "not in the checkable expectation language "
                "(<returns|arg.NAME|values.KEY>.<type|value|status> <op> <literal>, "
                "'calls X', 'not calls X', 'runs before X', 'runs after X', "
                "'reaches decision', 'does not reach decision')"
            ),
        )
    lhs_match = _LHS.match(match.group("lhs"))
    if not lhs_match:
        return Check(
            kind=CheckKind.UNPARSEABLE,
            text=stripped,
            reason=f"unknown subject {match.group('lhs')!r}",
        )
    attr = lhs_match.group("attr")
    if lhs_match.group("arg"):
        subject = lhs_match.group("arg")
    elif lhs_match.group("key"):
        subject = _unquote(lhs_match.group("key"))
    else:
        subject = "returns"
    literal = _parse_literal(match.group("rhs").strip())
    if literal is None:
        return Check(
            kind=CheckKind.UNPARSEABLE, text=stripped, reason=f"unreadable literal {match.group('rhs')!r}"
        )
    op = match.group("op")
    if op == "in" and literal.kind != "list":
        return Check(kind=CheckKind.UNPARSEABLE, text=stripped, reason="'in' needs a (a, b) list")
    if op != "in" and literal.kind == "list":
        return Check(kind=CheckKind.UNPARSEABLE, text=stripped, reason="a list literal needs 'in'")
    kind = {
        "type": CheckKind.TYPE,
        "value": CheckKind.VALUE,
        "status": CheckKind.CAPTURE_STATUS,
    }[attr]
    return Check(kind=kind, text=stripped, subject=subject, op=op, literal=literal)


def _parse_literal(raw: str) -> _Literal | None:
    if raw.startswith("(") and raw.endswith(")"):
        body = raw[1:-1]
        members = tuple(_unquote(part.strip()) for part in body.split(",") if part.strip())
        if not members:
            return None
        return _Literal(kind="list", raw=raw, members=members)
    if _is_quoted(raw):
        return _Literal(kind="str", raw=_unquote(raw))
    if re.fullmatch(r"[+-]?\d+", raw):
        return _Literal(kind="int", raw=raw, number=int(raw))
    if re.fullmatch(r"[+-]?\d+\.\d+", raw):
        # Floats are rejected outright: constraint 4 cannot survive their repr, and
        # a float equality check is not a thing this tool will pretend to do.
        return None
    if re.fullmatch(r"[A-Za-z_@][\w.@:\-]*", raw):
        return _Literal(kind="bare", raw=raw)
    return None


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Coverage:
    """How much of the intent set was actually checkable, and against what.

    Every number here is an integer. Percentages are left to the reader: a float in
    an artifact breaks the byte-identical guarantee (constraint 4).
    """

    run_id: str
    elements_judged: int
    intents_total: int
    intents_confirmed: int
    intents_proposed: int
    intents_with_expectations: int
    intents_checked: int
    checks_total: int
    checks_passed: int
    checks_failed: int
    checks_unverifiable: int
    checks_unparseable: int
    verdicts: Mapping[str, int] = field(default_factory=dict)
    evidence: Mapping[str, int] = field(default_factory=dict)
    registry_issues: int = 0
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "elements_judged": self.elements_judged,
            "intents_total": self.intents_total,
            "intents_confirmed": self.intents_confirmed,
            "intents_proposed": self.intents_proposed,
            "intents_with_expectations": self.intents_with_expectations,
            "intents_checked": self.intents_checked,
            "checks_total": self.checks_total,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "checks_unverifiable": self.checks_unverifiable,
            "checks_unparseable": self.checks_unparseable,
            "verdicts": dict(sorted(self.verdicts.items())),
            "evidence": dict(sorted(self.evidence.items())),
            "registry_issues": self.registry_issues,
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# The model escalation — bounded, labelled, never authoritative
# ---------------------------------------------------------------------------


_FALLBACK_PROMPT = """\
You are reading evidence from a program trace. You do NOT decide anything.

Intent (owner-confirmed): {statement}
Element: {element_id}
Observed events (id, kind, captured values):
{evidence}

Propose, in at most two sentences, one reading of whether the observed behaviour is
consistent with the intent, and name the event ID that a human should check first.
You are proposing, not deciding. Do not output a verdict, a confidence or a fact.
"""


class AlignmentModel(Protocol):
    """A bounded proposer. It receives a prompt and returns prose, nothing else."""

    def propose(self, prompt: str) -> str: ...


def model_available() -> bool:
    """True only when ``CASCADE_MAP_API_KEY`` is set and non-empty."""
    return bool(os.environ.get(API_KEY_ENV, "").strip())


def load_prompt(prompt_path: str | os.PathLike[str] | None = None) -> tuple[str, str]:
    """Return (template, source-label) for the escalation prompt."""
    candidate = Path(prompt_path) if prompt_path is not None else Path(PROMPT_PATH)
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8"), str(candidate)
    return _FALLBACK_PROMPT, "builtin-fallback"


class AnthropicAlignmentModel:
    """The real escalation client. Never constructed in tests; never reached
    without a key. Reads :data:`API_KEY_ENV` and refuses ``ANTHROPIC_API_KEY``."""

    def __init__(self, *, model_id: str = MODEL_ID, timeout: int = 30) -> None:
        key = os.environ.get(API_KEY_ENV, "").strip()
        if not key:
            raise RuntimeError(
                f"{API_KEY_ENV} is not set. Model escalation is optional and stays off; "
                f"{FORBIDDEN_KEY_ENV} is never read."
            )
        self._key = key
        self._model_id = model_id
        self._timeout = timeout

    @property
    def model_id(self) -> str:
        return self._model_id

    def propose(self, prompt: str) -> str:  # pragma: no cover - network, never in tests
        import json
        import urllib.request

        request = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(
                {
                    "model": self._model_id,
                    "max_tokens": 300,
                    "messages": [{"role": "user", "content": prompt}],
                }
            ).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
                "x-api-key": self._key,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        parts = [block.get("text", "") for block in payload.get("content", [])]
        return "".join(parts)


def default_model() -> AlignmentModel | None:
    """The client the engine uses when the owner set a key, else ``None``."""
    if not model_available():
        return None
    return AnthropicAlignmentModel()


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Judged:
    verdict: AlignmentVerdict
    results: tuple[CheckResult, ...]
    had_expectations: bool
    was_checked: bool


class AlignmentEngine:
    """Implements :class:`~cascade_map.contracts.interfaces.AlignmentCard`.

    Static evidence (cards 2-4) and the :class:`RunRecord` are supplied to the
    constructor so that :meth:`judge` matches the protocol exactly. Static evidence
    is optional and its absence is never treated as a contradiction: without a call
    graph, an unobserved call is ``UNVERIFIABLE``, not ``MISALIGNED``.
    """

    def __init__(
        self,
        *,
        registry: IntentRegistry | None = None,
        elements: Sequence[Element] = (),
        edges: Sequence[Edge] = (),
        lineage_edges: Sequence[LineageEdge] = (),
        reachability: Sequence[Reachability] = (),
        run: RunRecord | None = None,
        model: AlignmentModel | None = None,
        prompt_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self._registry = registry
        self._elements = tuple(elements)
        self._edges = tuple(edges)
        self._lineage = tuple(lineage_edges)
        # Card 3's answer to "does this element drive the final decision". Read,
        # never derived: deriving a second one here is the exact duplication
        # `Reachability` was added to the contract to end.
        self._reachability: dict[str, Reachability] = {}
        for record in sorted(reachability, key=lambda item: item.id):
            self._reachability.setdefault(record.element_id, record)
        self._run = run
        self._model = model
        self._prompt_path = prompt_path
        self._coverage = Coverage(
            run_id="",
            elements_judged=0,
            intents_total=0,
            intents_confirmed=0,
            intents_proposed=0,
            intents_with_expectations=0,
            intents_checked=0,
            checks_total=0,
            checks_passed=0,
            checks_failed=0,
            checks_unverifiable=0,
            checks_unparseable=0,
        )

    # -- public API --------------------------------------------------------

    def coverage(self) -> Coverage:
        """Coverage of the most recent :meth:`judge` call."""
        return self._coverage

    def judge(
        self, intents: Sequence[Intent], events: Sequence[TraceEvent]
    ) -> Sequence[AlignmentVerdict]:
        return self._judge(intents, events, static=False)

    def judge_static(self, intents: Sequence[Intent]) -> Sequence[AlignmentVerdict]:
        """Verdicts from the STATIC evidence alone -- no run, nothing executed.

        This is what `analyze` can honestly say about an intent before any
        scenario has been run, and it exists because the owner's question --
        "which of the things I built are not plugged in?" -- is answerable
        against their own declaration without executing anything.

        The only expectations settled here are the ones whose evidence is
        static: ``reaches decision`` against card 3's :class:`Reachability`,
        ``calls X`` against card 2's edges, and ``expected_reads`` /
        ``expected_writes`` against card 4's lineage. Every expectation about
        a *value*, a capture status or an order of execution is
        ``UNVERIFIABLE`` here and says so, naming ``trace`` as the thing that
        settles it -- a static run must never report an unchecked expectation
        as met.

        ``NOT_EXERCISED`` is deliberately NOT produced here. Nothing ran, so
        "no scenario ran this element" would be true of every element in the
        target and would say nothing; it is the runtime path's answer, where
        it means something.
        """
        return self._judge(intents, (), static=True)

    def _judge(
        self,
        intents: Sequence[Intent],
        events: Sequence[TraceEvent],
        *,
        static: bool,
    ) -> Sequence[AlignmentVerdict]:
        run_ids = sorted({event.run_id for event in events if event.run_id})
        blocker = self._blocking_reason(run_ids)
        run_id = run_ids[0] if len(run_ids) == 1 else (self._run.run_id if self._run else "")

        by_element: dict[str, list[Intent]] = {}
        for intent in intents:
            by_element.setdefault(intent.element_id, []).append(intent)
        events_by_element = _index_events(events)

        universe = set(by_element)
        universe.update(element.id for element in self._elements)
        universe.update(event.element_id for event in events if event.element_id)
        if self._registry is not None:
            universe.update(self._registry.rejected_element_ids)

        verdicts: list[AlignmentVerdict] = []
        results: list[CheckResult] = []
        with_expectations = 0
        checked = 0
        notes: list[str] = []

        for element_id in sorted(universe):
            element_intents = sorted(by_element.get(element_id, ()), key=lambda i: i.id)
            judged = self._judge_element(
                element_id=element_id,
                element_intents=element_intents,
                events=events,
                events_by_element=events_by_element,
                run_id=run_id,
                blocker=blocker,
                static=static,
            )
            verdicts.append(judged.verdict)
            results.extend(judged.results)
            with_expectations += 1 if judged.had_expectations else 0
            checked += 1 if judged.was_checked else 0

        if static:
            notes.append(
                "static judgement: nothing was executed, so only expectations whose "
                "evidence is the static graph were settled. Every value, capture-status "
                "and execution-order expectation is UNVERIFIABLE until `metatron trace` "
                "runs the scenario."
            )
        if blocker:
            notes.append(blocker)
        if self._registry is not None and not self._registry.present:
            notes.append(
                f"no intent spec: {self._registry.source_path or 'none declared'} -- "
                f"every element is NO_INTENT, which is a reported state, not a pass"
            )
        if not self._edges:
            notes.append("no static call graph supplied: call expectations degrade to UNVERIFIABLE")
        if not self._lineage:
            notes.append("no lineage edges supplied: read/write expectations degrade to UNVERIFIABLE")
        if not self._reachability:
            notes.append(
                "no reachability records supplied: 'reaches decision' expectations degrade "
                "to UNVERIFIABLE"
            )
        if self._model is not None:
            notes.append(
                f"model escalation active ({MODEL_ID}): proposals only, attached to UNVERIFIABLE "
                f"verdicts; output is not byte-identical across runs while it is on"
            )

        self._coverage = self._build_coverage(
            run_id=run_id,
            intents=intents,
            verdicts=verdicts,
            results=results,
            with_expectations=with_expectations,
            checked=checked,
            events=events,
            notes=tuple(notes),
        )
        return tuple(verdicts)

    # -- internals ---------------------------------------------------------

    def _blocking_reason(self, run_ids: Sequence[str]) -> str:
        """A reason no verdict in this call may be grounded, or ''.

        A refused run and a mixed-run event set are both cases where producing
        ALIGNED/MISALIGNED would be nonsense, so the whole call degrades to
        UNVERIFIABLE with the reason attached rather than judging anything.
        """
        if self._run is not None and self._run.refused:
            return (
                f"run {self._run.run_id} refused to start: "
                f"{self._run.refusal_reason or 'no reason recorded'}; no runtime evidence exists"
            )
        if len(run_ids) > 1:
            return (
                "trace events span multiple runs ("
                + ", ".join(run_ids)
                + "); a verdict must be keyed to exactly one run"
            )
        if self._run is not None and run_ids and run_ids[0] != self._run.run_id:
            return (
                f"trace events belong to run {run_ids[0]} but the run record is "
                f"{self._run.run_id}; the overlay does not key onto this run"
            )
        return ""

    def _judge_element(
        self,
        *,
        element_id: str,
        element_intents: Sequence[Intent],
        events: Sequence[TraceEvent],
        events_by_element: Mapping[str, tuple[TraceEvent, ...]],
        run_id: str,
        blocker: str,
        static: bool = False,
    ) -> _Judged:
        own_events = events_by_element.get(element_id, ())
        evidence = tuple(event.event_id for event in own_events)
        rejected = bool(self._registry and element_id in self._registry.rejected_element_ids)

        if rejected and not element_intents:
            return _Judged(
                verdict=self._verdict(
                    element_id=element_id,
                    intent_id="",
                    verdict=Verdict.UNVERIFIABLE,
                    expectation="the owner wrote an intent for this element",
                    observation=(
                        f"the entry in {self._registry.source_path if self._registry else ''} is "
                        f"malformed and was not loaded; see the unresolved records for its line. "
                        f"This is not NO_INTENT: an intent exists and could not be read."
                    ),
                    evidence_ids=evidence,
                    method=Method.STRUCTURAL_MATCH,
                    confidence=Confidence.UNKNOWN,
                    run_id=run_id,
                    note="registry entry rejected",
                ),
                results=(),
                had_expectations=False,
                was_checked=False,
            )

        if not element_intents:
            source = (
                self._registry.source_path
                if self._registry and self._registry.present
                else "no intent spec declared"
            )
            return _Judged(
                verdict=self._verdict(
                    element_id=element_id,
                    intent_id="",
                    verdict=Verdict.NO_INTENT,
                    expectation="",
                    observation=(
                        f"no intent is registered for {element_id} ({source}). Reported, not "
                        f"assumed: this is neither a pass nor a failure."
                    ),
                    evidence_ids=evidence,
                    method=Method.STRUCTURAL_MATCH,
                    confidence=Confidence.CERTAIN,
                    run_id=run_id,
                    note="registry lookup",
                ),
                results=(),
                had_expectations=False,
                was_checked=False,
            )

        if len(element_intents) > 1:
            ids = ", ".join(intent.id for intent in element_intents)
            return _Judged(
                verdict=self._verdict(
                    element_id=element_id,
                    intent_id=element_intents[0].id,
                    verdict=Verdict.UNVERIFIABLE,
                    expectation="exactly one intent governs this element",
                    observation=(
                        f"{len(element_intents)} intents claim {element_id}: {ids}. Choosing one "
                        f"would be a guess, so nothing is judged."
                    ),
                    evidence_ids=evidence,
                    method=Method.STRUCTURAL_MATCH,
                    confidence=Confidence.UNKNOWN,
                    run_id=run_id,
                    note="ambiguous registry",
                ),
                results=(),
                had_expectations=False,
                was_checked=False,
            )

        intent = element_intents[0]

        if blocker:
            return _Judged(
                verdict=self._verdict(
                    element_id=element_id,
                    intent_id=intent.id,
                    verdict=Verdict.UNVERIFIABLE,
                    expectation=intent.statement,
                    observation=f"no verdict can rest on this evidence: {blocker}",
                    evidence_ids=evidence,
                    method=Method.STRUCTURAL_MATCH,
                    confidence=Confidence.UNKNOWN,
                    run_id=run_id,
                    note="evidence unusable",
                ),
                results=(),
                had_expectations=bool(_expectation_count(intent)),
                was_checked=False,
            )

        if intent.status is not IntentStatus.CONFIRMED:
            suffix = (
                ""
                if own_events or static
                else f"; the element was also not exercised in run {run_id or '<none>'}"
            )
            return _Judged(
                verdict=self._verdict(
                    element_id=element_id,
                    intent_id=intent.id,
                    verdict=Verdict.UNVERIFIABLE,
                    expectation=intent.statement,
                    observation=(
                        f"intent {intent.id} is {intent.status.value}, not owner-confirmed. A "
                        f"PROPOSED intent may never ground ALIGNED or MISALIGNED{suffix}."
                    ),
                    evidence_ids=evidence,
                    method=Method.STRUCTURAL_MATCH,
                    confidence=Confidence.UNKNOWN,
                    run_id=run_id,
                    note="intent not confirmed by the owner",
                ),
                results=(),
                had_expectations=bool(_expectation_count(intent)),
                was_checked=False,
            )

        if static:
            results = self._evaluate(intent, (), events, events_by_element, static=True)
            if not results:
                return _Judged(
                    verdict=self._verdict(
                        element_id=element_id,
                        intent_id=intent.id,
                        verdict=Verdict.UNVERIFIABLE,
                        expectation=intent.statement,
                        observation=(
                            f"the intent states {intent.statement!r} but declares no invariant, "
                            f"expected read or expected write, so the static map has nothing to "
                            f"check it against. Prose is not a check."
                        ),
                        evidence_ids=(),
                        method=Method.STRUCTURAL_MATCH,
                        confidence=Confidence.UNKNOWN,
                        run_id="",
                        note="no checkable expectation; static judgement",
                    ),
                    results=(),
                    had_expectations=False,
                    was_checked=False,
                )
            return _Judged(
                verdict=self._aggregate(
                    intent, element_id, results, "", method=Method.STRUCTURAL_MATCH
                ),
                results=results,
                had_expectations=True,
                was_checked=True,
            )

        if not own_events:
            # A contradiction outranks "it did not run". An element the owner
            # declared live, that no call path reaches, is MISALIGNED whether
            # or not this scenario entered it -- and NOT_EXERCISED there would
            # hide a fact the static map already holds. Only expectations the
            # static evidence can settle are considered; a value expectation
            # still needs a run and still leaves this NOT_EXERCISED.
            static_results = self._evaluate(intent, (), events, events_by_element, static=True)
            contradicted = [
                result for result in static_results if result.status is CheckStatus.FAIL
            ]
            if contradicted:
                first = contradicted[0]
                return _Judged(
                    verdict=self._verdict(
                        element_id=element_id,
                        intent_id=intent.id,
                        verdict=Verdict.MISALIGNED,
                        expectation=first.expectation,
                        observation=(
                            f"{first.observation}. No event in run {run_id or '<none>'} names "
                            f"this element either, so nothing this scenario did could settle "
                            f"the rest of the intent."
                        ),
                        evidence_ids=_merge_evidence(contradicted),
                        method=Method.STRUCTURAL_MATCH,
                        confidence=combine(*[r.confidence for r in contradicted]),
                        run_id=run_id,
                        note="static contradiction; the element was also not exercised",
                    ),
                    results=static_results,
                    had_expectations=True,
                    was_checked=True,
                )
            return _Judged(
                verdict=self._verdict(
                    element_id=element_id,
                    intent_id=intent.id,
                    verdict=Verdict.NOT_EXERCISED,
                    expectation=intent.statement,
                    observation=(
                        f"no event in run {run_id or '<none>'} names {element_id}; the intent is "
                        f"unverified. NOT_EXERCISED is not ALIGNED."
                    ),
                    evidence_ids=(),
                    method=Method.RUNTIME_OBSERVED,
                    confidence=Confidence.CERTAIN,
                    run_id=run_id,
                    note="absence of events in this run; other scenarios may exercise it",
                ),
                results=(),
                had_expectations=bool(_expectation_count(intent)),
                was_checked=False,
            )

        results = self._evaluate(intent, own_events, events, events_by_element)
        if not results:
            verdict = self._unverifiable_prose(intent, element_id, own_events, run_id, evidence)
            return _Judged(verdict=verdict, results=(), had_expectations=False, was_checked=False)

        return _Judged(
            verdict=self._aggregate(intent, element_id, results, run_id),
            results=results,
            had_expectations=True,
            was_checked=True,
        )

    def _evaluate(
        self,
        intent: Intent,
        own_events: Sequence[TraceEvent],
        events: Sequence[TraceEvent],
        events_by_element: Mapping[str, tuple[TraceEvent, ...]],
        *,
        static: bool = False,
    ) -> tuple[CheckResult, ...]:
        results: list[CheckResult] = []
        for text in intent.invariants:
            check = parse_check(text)
            if static:
                results.append(self._evaluate_check_static(check, intent))
            else:
                results.append(
                    self._evaluate_check(check, intent, own_events, events, events_by_element)
                )
        for feature in intent.expected_writes:
            if static:
                results.append(self._evaluate_flow_static(intent, feature, write=True))
            else:
                results.append(self._evaluate_flow(intent, feature, own_events, events, write=True))
        for feature in intent.expected_reads:
            if static:
                results.append(self._evaluate_flow_static(intent, feature, write=False))
            else:
                results.append(
                    self._evaluate_flow(intent, feature, own_events, events, write=False)
                )
        return tuple(results)

    # -- the static half ---------------------------------------------------
    #
    # Everything below decides an expectation from cards 2-4 alone. Nothing
    # here reads an event, and nothing here executes anything.

    def _evaluate_check_static(self, check: Check, intent: Intent) -> CheckResult:
        expectation = f"invariant {check.text!r}"
        if check.kind is CheckKind.UNPARSEABLE:
            return CheckResult(
                status=CheckStatus.UNVERIFIABLE,
                expectation=expectation,
                observation=(
                    f"the invariant could not be parsed ({check.reason}); it was not checked, and "
                    f"an unchecked expectation is never reported as met"
                ),
            )
        if check.kind in (CheckKind.REACHES_DECISION, CheckKind.NOT_REACHES_DECISION):
            return self._evaluate_reach(check, intent)
        if check.kind in (CheckKind.CALLS, CheckKind.NOT_CALLS):
            return self._evaluate_calls_static(check, intent)
        return CheckResult(
            status=CheckStatus.UNVERIFIABLE,
            expectation=expectation,
            observation=(
                f"this expectation is about what happens when the code RUNS, and nothing was "
                f"executed: the static map cannot settle it. `metatron trace` is what settles "
                f"it; until then it is unchecked, which is not the same as met"
            ),
        )

    def _evaluate_reach(self, check: Check, intent: Intent) -> CheckResult:
        """`reaches decision` against card 3's Reachability. Static evidence.

        An ``UNKNOWN`` reachability is never a contradiction. Card 3 biases
        toward REACHES_SINK precisely because a false "unreachable" sends an
        owner to delete live code, and this card must not undo that bias by
        reading "I could not tell" as "it does not".
        """
        expectation = f"invariant {check.text!r}"
        wants_reach = check.kind is CheckKind.REACHES_DECISION
        record = self._reachability.get(intent.element_id)
        if record is None:
            return CheckResult(
                status=CheckStatus.UNVERIFIABLE,
                expectation=expectation,
                observation=(
                    "no reachability record names this element, so whether anything reaches a "
                    "decision from it was never measured; run `metatron analyze` with your "
                    "decision sink declared (--sink) and it will be"
                ),
            )
        if record.state is ReachabilityState.UNKNOWN:
            return CheckResult(
                status=CheckStatus.UNVERIFIABLE,
                expectation=expectation,
                observation=(
                    "reachability for this element is UNKNOWN ("
                    + (record.reason or "no reason recorded")
                    + "); 'I could not tell' is never read as 'it does not', and a decision "
                    "sink may not be declared at all"
                ),
                evidence_ids=(record.id,),
            )
        reaches = record.state is ReachabilityState.REACHES_SINK
        if reaches == wants_reach:
            detail = (
                f"reachability record {record.id} says {record.state.value}"
                + (f" via {' -> '.join(record.path_ids)}" if reaches and record.path_ids else "")
            )
            return CheckResult(
                status=CheckStatus.PASS,
                expectation=expectation,
                observation=detail,
                evidence_ids=(record.id,),
                confidence=record.provenance.confidence,
            )
        if wants_reach:
            observation = (
                f"the owner declares this element live, and reachability record {record.id} says "
                f"NO_SINK_PATH: no call path in the static graph reaches a declared decision sink "
                f"({', '.join(record.sink_ids) or 'none declared'}) from it. This is 'built but "
                f"never plugged in' measured against the owner's own declaration, not guessed "
                f"from a name"
            )
        else:
            observation = (
                f"the owner declares this element off the decision path, and reachability record "
                f"{record.id} says REACHES_SINK"
                + (f" via {' -> '.join(record.path_ids)}" if record.path_ids else "")
            )
        return CheckResult(
            status=CheckStatus.FAIL,
            expectation=expectation,
            observation=observation,
            evidence_ids=(record.id,),
            confidence=record.provenance.confidence,
        )

    def _evaluate_calls_static(self, check: Check, intent: Intent) -> CheckResult:
        expectation = f"invariant {check.text!r}"
        edge = self._find_call_edge(intent.element_id, check.subject)
        wants_call = check.kind is CheckKind.CALLS
        if not self._edges:
            return CheckResult(
                status=CheckStatus.UNVERIFIABLE,
                expectation=expectation,
                observation=(
                    f"no static call graph was supplied, so whether this element calls "
                    f"{check.subject} was never measured"
                ),
            )
        if edge is not None:
            return CheckResult(
                status=CheckStatus.PASS if wants_call else CheckStatus.FAIL,
                expectation=expectation,
                observation=(
                    f"the static call graph has a CALLS edge to {check.subject} ({edge.id}, "
                    f"{edge.provenance.method.value})"
                ),
                evidence_ids=(edge.id,),
                confidence=edge.provenance.confidence,
            )
        return CheckResult(
            status=CheckStatus.FAIL if wants_call else CheckStatus.PASS,
            expectation=expectation,
            observation=(
                f"the static call graph has no CALLS edge from this element to {check.subject}. "
                f"Only dynamic dispatch could still make this true, and only a run settles that"
            ),
            confidence=Confidence.PROBABLE,
        )

    def _evaluate_flow_static(self, intent: Intent, feature: str, *, write: bool) -> CheckResult:
        word = "writes" if write else "reads"
        expectation = f"expected_{'writes' if write else 'reads'} names {feature!r}"
        if not self._lineage:
            return CheckResult(
                status=CheckStatus.UNVERIFIABLE,
                expectation=expectation,
                observation=(
                    f"no lineage was supplied, so whether this element {word} {feature} was "
                    f"never measured"
                ),
            )
        edge = self._find_lineage_edge(intent.element_id, feature, write=write)
        if edge is not None:
            return CheckResult(
                status=CheckStatus.PASS,
                expectation=expectation,
                observation=f"lineage edge {edge.id} says this element {word} {feature}",
                evidence_ids=(edge.id,),
                confidence=edge.provenance.confidence,
            )
        return CheckResult(
            status=CheckStatus.FAIL,
            expectation=expectation,
            observation=(
                f"no lineage edge shows this element {word} {feature}; the owner declares it "
                f"does"
            ),
            confidence=Confidence.PROBABLE,
        )

    def _evaluate_check(
        self,
        check: Check,
        intent: Intent,
        own_events: Sequence[TraceEvent],
        events: Sequence[TraceEvent],
        events_by_element: Mapping[str, tuple[TraceEvent, ...]],
    ) -> CheckResult:
        expectation = f"invariant {check.text!r}"
        if check.kind is CheckKind.UNPARSEABLE:
            return CheckResult(
                status=CheckStatus.UNVERIFIABLE,
                expectation=expectation,
                observation=(
                    f"the invariant could not be parsed ({check.reason}); it was not checked, and "
                    f"an unchecked expectation is never reported as met"
                ),
            )
        if check.kind in (CheckKind.REACHES_DECISION, CheckKind.NOT_REACHES_DECISION):
            # Static evidence inside a runtime verdict. A trace cannot observe
            # "nothing reaches this": one run is one path, and card 3 already
            # holds the answer over the whole graph.
            return self._evaluate_reach(check, intent)
        if check.kind in (CheckKind.CALLS, CheckKind.NOT_CALLS):
            return self._evaluate_calls(check, intent, own_events, events_by_element)
        if check.kind in (CheckKind.RUNS_BEFORE, CheckKind.RUNS_AFTER):
            return self._evaluate_order(check, own_events, events_by_element)
        return self._evaluate_value(check, own_events)

    # -- value invariants --------------------------------------------------

    def _evaluate_value(self, check: Check, own_events: Sequence[TraceEvent]) -> CheckResult:
        expectation = f"invariant {check.text!r}"
        wanted_kinds = (
            (EventKind.RETURN,) if check.subject == "returns" else (EventKind.CALL, EventKind.RETURN)
        )
        keys = RETURN_VALUE_KEYS if check.subject == "returns" else (check.subject,)

        found: list[tuple[TraceEvent, ValueCapture]] = []
        for event in own_events:
            if event.kind not in wanted_kinds:
                continue
            for key in keys:
                if key in event.values:
                    found.append((event, event.values[key]))
                    break
        if not found:
            names = " / ".join(keys)
            return CheckResult(
                status=CheckStatus.UNVERIFIABLE,
                expectation=expectation,
                observation=(
                    f"no captured value named {names} on any {'/'.join(k.value for k in wanted_kinds)} "
                    f"event for this element; the invariant was not checked"
                ),
                evidence_ids=tuple(event.event_id for event in own_events),
            )

        evidence: list[str] = []
        confidences: list[Confidence] = []
        for event, capture in found:
            evidence.append(event.event_id)
            outcome = _compare(check, capture)
            if outcome.status is CheckStatus.FAIL:
                return CheckResult(
                    status=CheckStatus.FAIL,
                    expectation=expectation,
                    observation=f"{outcome.observation} (event {event.event_id})",
                    evidence_ids=(event.event_id,),
                    confidence=Confidence.CERTAIN,
                )
            if outcome.status is CheckStatus.UNVERIFIABLE:
                return CheckResult(
                    status=CheckStatus.UNVERIFIABLE,
                    expectation=expectation,
                    observation=f"{outcome.observation} (event {event.event_id})",
                    evidence_ids=(event.event_id,),
                )
            confidences.append(outcome.confidence)
        return CheckResult(
            status=CheckStatus.PASS,
            expectation=expectation,
            observation=(
                f"held on {len(found)} observed value"
                f"{'' if len(found) == 1 else 's'}: {', '.join(evidence)}"
            ),
            evidence_ids=tuple(evidence),
            confidence=combine(*confidences) if confidences else Confidence.UNKNOWN,
        )

    # -- structural invariants --------------------------------------------

    def _evaluate_calls(
        self,
        check: Check,
        intent: Intent,
        own_events: Sequence[TraceEvent],
        events_by_element: Mapping[str, tuple[TraceEvent, ...]],
    ) -> CheckResult:
        expectation = f"invariant {check.text!r}"
        own_ids = {event.event_id for event in own_events}
        observed = [
            event
            for event in events_by_element.get(check.subject, ())
            if event.kind is EventKind.CALL and event.caller_event_id in own_ids
        ]
        if check.kind is CheckKind.CALLS:
            if observed:
                return CheckResult(
                    status=CheckStatus.PASS,
                    expectation=expectation,
                    observation=(
                        f"observed {len(observed)} CALL of {check.subject} from this element: "
                        f"{', '.join(event.event_id for event in observed)}"
                    ),
                    evidence_ids=tuple(event.event_id for event in observed),
                    confidence=Confidence.CERTAIN,
                )
            edge = self._find_call_edge(intent.element_id, check.subject)
            if edge is not None:
                return CheckResult(
                    status=CheckStatus.UNVERIFIABLE,
                    expectation=expectation,
                    observation=(
                        f"the static graph has a CALLS edge to {check.subject} ({edge.id}) but no "
                        f"CALL was observed in this run; this scenario does not settle it"
                    ),
                    evidence_ids=(edge.id,) + tuple(event.event_id for event in own_events),
                )
            if not self._edges:
                return CheckResult(
                    status=CheckStatus.UNVERIFIABLE,
                    expectation=expectation,
                    observation=(
                        f"no CALL of {check.subject} was observed, and no static call graph was "
                        f"supplied; absence in one run is not a contradiction"
                    ),
                    evidence_ids=tuple(event.event_id for event in own_events),
                )
            return CheckResult(
                status=CheckStatus.FAIL,
                expectation=expectation,
                observation=(
                    f"no CALL of {check.subject} from this element in run "
                    f"{own_events[0].run_id or '<none>'}, and the static call graph has no CALLS "
                    f"edge to it either"
                ),
                evidence_ids=tuple(event.event_id for event in own_events),
                confidence=Confidence.PROBABLE,
            )
        if observed:
            return CheckResult(
                status=CheckStatus.FAIL,
                expectation=expectation,
                observation=(
                    f"{check.subject} was called from this element at event "
                    f"{observed[0].event_id}"
                ),
                evidence_ids=(observed[0].event_id,),
                confidence=Confidence.CERTAIN,
            )
        return CheckResult(
            status=CheckStatus.PASS,
            expectation=expectation,
            observation=(
                f"no CALL of {check.subject} from this element in run "
                f"{own_events[0].run_id or '<none>'}; one run cannot prove absence in general"
            ),
            evidence_ids=tuple(event.event_id for event in own_events),
            confidence=Confidence.PROBABLE,
        )

    def _evaluate_order(
        self,
        check: Check,
        own_events: Sequence[TraceEvent],
        events_by_element: Mapping[str, tuple[TraceEvent, ...]],
    ) -> CheckResult:
        expectation = f"invariant {check.text!r}"
        other = events_by_element.get(check.subject, ())
        if not other:
            return CheckResult(
                status=CheckStatus.UNVERIFIABLE,
                expectation=expectation,
                observation=f"{check.subject} was not exercised in this run; order is unobservable",
                evidence_ids=tuple(event.event_id for event in own_events),
            )
        mine = min(own_events, key=lambda event: event.sequence)
        theirs = min(other, key=lambda event: event.sequence)
        before = mine.sequence < theirs.sequence
        wants_before = check.kind is CheckKind.RUNS_BEFORE
        evidence = (mine.event_id, theirs.event_id)
        if before == wants_before:
            return CheckResult(
                status=CheckStatus.PASS,
                expectation=expectation,
                observation=(
                    f"first event of this element is {mine.event_id} (sequence {mine.sequence}), "
                    f"{check.subject} first runs at {theirs.event_id} (sequence {theirs.sequence})"
                ),
                evidence_ids=evidence,
                confidence=Confidence.CERTAIN,
            )
        return CheckResult(
            status=CheckStatus.FAIL,
            expectation=expectation,
            observation=(
                f"this element first runs at sequence {mine.sequence} ({mine.event_id}) and "
                f"{check.subject} first runs at sequence {theirs.sequence} ({theirs.event_id}), "
                f"the opposite order"
            ),
            evidence_ids=evidence,
            confidence=Confidence.CERTAIN,
        )

    def _evaluate_flow(
        self,
        intent: Intent,
        feature: str,
        own_events: Sequence[TraceEvent],
        events: Sequence[TraceEvent],
        *,
        write: bool,
    ) -> CheckResult:
        word = "writes" if write else "reads"
        expectation = f"expected_{'writes' if write else 'reads'} names {feature!r}"
        observed = _flow_events(feature, intent.element_id, own_events, events, write=write)
        if observed:
            return CheckResult(
                status=CheckStatus.PASS,
                expectation=expectation,
                observation=(
                    f"observed {word} of {feature} at {', '.join(sorted(observed))}"
                ),
                evidence_ids=tuple(sorted(observed)),
                confidence=Confidence.CERTAIN,
            )
        edge = self._find_lineage_edge(intent.element_id, feature, write=write)
        if edge is not None:
            return CheckResult(
                status=CheckStatus.UNVERIFIABLE,
                expectation=expectation,
                observation=(
                    f"lineage edge {edge.id} says this element {word} {feature}, but no such "
                    f"event was observed in this run"
                ),
                evidence_ids=(edge.id,) + tuple(event.event_id for event in own_events),
            )
        if not self._lineage:
            return CheckResult(
                status=CheckStatus.UNVERIFIABLE,
                expectation=expectation,
                observation=(
                    f"no runtime event shows this element {word} {feature}, and no lineage was "
                    f"supplied; absence of evidence is not a contradiction"
                ),
                evidence_ids=tuple(event.event_id for event in own_events),
            )
        return CheckResult(
            status=CheckStatus.FAIL,
            expectation=expectation,
            observation=(
                f"no runtime event and no lineage edge shows this element {word} {feature}"
            ),
            evidence_ids=tuple(event.event_id for event in own_events),
            confidence=Confidence.PROBABLE,
        )

    def _find_call_edge(self, source_id: str, target_id: str) -> Edge | None:
        for edge in self._edges:
            if edge.kind is EdgeKind.CALLS and edge.source_id == source_id and edge.target_id == target_id:
                return edge
        return None

    def _find_lineage_edge(self, element_id: str, feature: str, *, write: bool) -> LineageEdge | None:
        kinds = _WRITE_KINDS if write else _READ_KINDS
        names = _feature_aliases(feature)
        for edge in self._lineage:
            if edge.kind not in kinds:
                continue
            if write and edge.source_id == element_id and edge.target_id in names:
                return edge
            if not write and edge.target_id == element_id and edge.source_id in names:
                return edge
        return None

    # -- aggregation -------------------------------------------------------

    def _aggregate(
        self,
        intent: Intent,
        element_id: str,
        results: Sequence[CheckResult],
        run_id: str,
        *,
        method: Method = Method.RUNTIME_OBSERVED,
    ) -> AlignmentVerdict:
        # `event_ids` is a RUNTIME field. A static verdict's evidence is edge
        # and reachability IDs, and filing those under `event_ids` would label
        # static structure as something a run observed.
        runtime = method is Method.RUNTIME_OBSERVED
        failures = [result for result in results if result.status is CheckStatus.FAIL]
        unverifiable = [result for result in results if result.status is CheckStatus.UNVERIFIABLE]
        passes = [result for result in results if result.status is CheckStatus.PASS]
        note = "; ".join(f"{result.status.value}: {result.expectation}" for result in results)

        if failures:
            first = failures[0]
            return self._verdict(
                element_id=element_id,
                intent_id=intent.id,
                verdict=Verdict.MISALIGNED,
                expectation=first.expectation,
                observation=first.observation,
                evidence_ids=_merge_evidence(failures),
                method=method,
                confidence=combine(*[result.confidence for result in failures]),
                run_id=run_id,
                note=note,
                event_ids=_merge_evidence(failures) if runtime else (),
            )
        if unverifiable:
            first = unverifiable[0]
            checked = len(passes)
            return self._verdict(
                element_id=element_id,
                intent_id=intent.id,
                verdict=Verdict.UNVERIFIABLE,
                expectation=first.expectation,
                observation=(
                    f"{first.observation}. {checked} of {len(results)} expectations held; an "
                    f"intent is not ALIGNED while any expectation is unchecked."
                ),
                evidence_ids=_merge_evidence(results),
                method=method,
                confidence=Confidence.UNKNOWN,
                run_id=run_id,
                note=note,
                event_ids=_merge_evidence(results) if runtime else (),
            )
        return self._verdict(
            element_id=element_id,
            intent_id=intent.id,
            verdict=Verdict.ALIGNED,
            expectation="; ".join(result.expectation for result in passes),
            observation="; ".join(result.observation for result in passes),
            evidence_ids=_merge_evidence(passes),
            method=method,
            confidence=combine(*[result.confidence for result in passes]),
            run_id=run_id,
            note=note,
            event_ids=_merge_evidence(passes) if runtime else (),
        )

    def _unverifiable_prose(
        self,
        intent: Intent,
        element_id: str,
        own_events: Sequence[TraceEvent],
        run_id: str,
        evidence: tuple[str, ...],
    ) -> AlignmentVerdict:
        """A confirmed intent with no checkable expectation. Prose is not a check."""
        observation = (
            f"the intent states {intent.statement!r} but declares no invariant, expected read or "
            f"expected write; there is nothing to check it against. The element ran "
            f"{len(own_events)} time{'' if len(own_events) == 1 else 's'} in run "
            f"{run_id or '<none>'}."
        )
        method = Method.STRUCTURAL_MATCH
        confidence = Confidence.UNKNOWN
        model_id = ""
        note = "no checkable expectation"
        if self._model is not None:
            proposal = self._propose(intent, own_events)
            if proposal:
                observation = (
                    f"{observation} MODEL PROPOSAL ({MODEL_ID}, a reading to check, not a verdict): "
                    f"{proposal}"
                )
                method = Method.MODEL_PROPOSED
                model_id = MODEL_ID
                note = f"{note}; model proposal attached, {self._prompt_label()}"
        return self._verdict(
            element_id=element_id,
            intent_id=intent.id,
            verdict=Verdict.UNVERIFIABLE,
            expectation=intent.statement,
            observation=observation,
            evidence_ids=evidence,
            method=method,
            confidence=confidence,
            run_id=run_id,
            note=note,
            model_id=model_id,
            event_ids=evidence,
        )

    def _prompt_label(self) -> str:
        _, label = load_prompt(self._prompt_path)
        return f"prompt={label}"

    def _propose(self, intent: Intent, own_events: Sequence[TraceEvent]) -> str:
        """Ask the model for a reading. Bounded, labelled, never a verdict.

        The result is flattened to a single line and truncated, and it is only ever
        used as prose inside an ``UNVERIFIABLE`` verdict tied to the event IDs below.
        A model that answers "MISALIGNED" changes nothing: the verdict is decided
        before this method is called.
        """
        if self._model is None:
            return ""
        template, _ = load_prompt(self._prompt_path)
        evidence = "\n".join(
            f"- {event.event_id} {event.kind.value} "
            + ", ".join(
                f"{key}={capture.repr_text!r} ({capture.status.value})"
                for key, capture in sorted(event.values.items())
            )
            for event in own_events
        )
        prompt = template.format(
            statement=intent.statement, element_id=intent.element_id, evidence=evidence
        )
        try:
            raw = self._model.propose(prompt)
        except Exception as error:  # a model failure never fails the run
            return f"[escalation failed: {type(error).__name__}]"
        flattened = " ".join(str(raw).split())
        return flattened[:400]

    def _verdict(
        self,
        *,
        element_id: str,
        intent_id: str,
        verdict: Verdict,
        expectation: str,
        observation: str,
        evidence_ids: tuple[str, ...],
        method: Method,
        confidence: Confidence,
        run_id: str,
        note: str,
        model_id: str = "",
        event_ids: tuple[str, ...] = (),
    ) -> AlignmentVerdict:
        return AlignmentVerdict(
            id=f"{_VERDICT_ID_PREFIX}{run_id or _NO_RUN}:{element_id}",
            element_id=element_id,
            intent_id=intent_id,
            verdict=verdict,
            expectation=expectation,
            observation=observation,
            evidence_ids=tuple(sorted(set(evidence_ids))),
            provenance=Provenance(
                method=method,
                confidence=confidence,
                note=note,
                model_id=model_id,
                run_id=run_id if method in (Method.RUNTIME_OBSERVED, Method.MODEL_PROPOSED) else "",
                event_ids=tuple(sorted(set(event_ids))),
            ),
        )

    def _build_coverage(
        self,
        *,
        run_id: str,
        intents: Sequence[Intent],
        verdicts: Sequence[AlignmentVerdict],
        results: Sequence[CheckResult],
        with_expectations: int,
        checked: int,
        events: Sequence[TraceEvent],
        notes: tuple[str, ...],
    ) -> Coverage:
        counts = {verdict.value: 0 for verdict in Verdict}
        for item in verdicts:
            counts[item.verdict.value] += 1
        unparseable = sum(
            1
            for intent in intents
            for text in intent.invariants
            if parse_check(text).kind is CheckKind.UNPARSEABLE
        )
        model_proposals = sum(
            1 for item in verdicts if item.provenance.method is Method.MODEL_PROPOSED
        )
        return Coverage(
            run_id=run_id,
            elements_judged=len(verdicts),
            intents_total=len(intents),
            intents_confirmed=sum(
                1 for intent in intents if intent.status is IntentStatus.CONFIRMED
            ),
            intents_proposed=sum(1 for intent in intents if intent.status is IntentStatus.PROPOSED),
            intents_with_expectations=with_expectations,
            intents_checked=checked,
            checks_total=len(results),
            checks_passed=sum(1 for result in results if result.status is CheckStatus.PASS),
            checks_failed=sum(1 for result in results if result.status is CheckStatus.FAIL),
            checks_unverifiable=sum(
                1 for result in results if result.status is CheckStatus.UNVERIFIABLE
            ),
            checks_unparseable=unparseable,
            verdicts=counts,
            evidence={
                "runtime_events": len(events),
                "static_edges": len(self._edges),
                "lineage_edges": len(self._lineage),
                "static_elements": len(self._elements),
                "reachability_records": len(self._reachability),
                "model_proposals": model_proposals,
            },
            registry_issues=len(self._registry.issues) if self._registry else 0,
            notes=notes,
        )


# ---------------------------------------------------------------------------
# Comparison helpers
# ---------------------------------------------------------------------------


def _compare(check: Check, capture: ValueCapture) -> CheckResult:
    literal = check.literal
    assert literal is not None
    if check.kind is CheckKind.CAPTURE_STATUS:
        return _compare_text(check, capture.status.value, "capture status")
    if check.kind is CheckKind.TYPE:
        if not capture.type_name:
            return CheckResult(
                status=CheckStatus.UNVERIFIABLE,
                expectation=check.text,
                observation="the capture records no type name",
            )
        return _compare_text(check, capture.type_name, "type")
    if capture.status is not CaptureStatus.FULL:
        return CheckResult(
            status=CheckStatus.UNVERIFIABLE,
            expectation=check.text,
            observation=(
                f"the value was captured {capture.status.value}"
                + (f" ({capture.reason})" if capture.reason else "")
                + "; a partial value cannot confirm or contradict a value invariant"
            ),
        )
    text = capture.repr_text
    if literal.kind == "int":
        try:
            actual = int(text.strip())
        except ValueError:
            return CheckResult(
                status=CheckStatus.UNVERIFIABLE,
                expectation=check.text,
                observation=f"captured value {text!r} is not an integer; it cannot be compared to "
                f"{literal.raw}",
            )
        ok = {
            "==": actual == literal.number,
            "!=": actual != literal.number,
            ">": actual > literal.number,
            ">=": actual >= literal.number,
            "<": actual < literal.number,
            "<=": actual <= literal.number,
        }[check.op]
        if ok:
            return CheckResult(
                status=CheckStatus.PASS,
                expectation=check.text,
                observation=f"observed {actual}",
                confidence=Confidence.CERTAIN,
            )
        return CheckResult(
            status=CheckStatus.FAIL,
            expectation=check.text,
            observation=f"observed value {actual}, which is not {check.op} {literal.number}",
            confidence=Confidence.CERTAIN,
        )
    if literal.kind == "str":
        return _compare_text(check, _unquote_repr(text), "value")
    return _compare_text(check, text, "value")


def _compare_text(check: Check, actual: str, label: str) -> CheckResult:
    literal = check.literal
    assert literal is not None
    if check.op == "in":
        ok = actual in literal.members
        rendered = "(" + ", ".join(literal.members) + ")"
    elif check.op == "==":
        ok = actual == literal.raw
        rendered = literal.raw
    elif check.op == "!=":
        ok = actual != literal.raw
        rendered = literal.raw
    else:
        return CheckResult(
            status=CheckStatus.UNVERIFIABLE,
            expectation=check.text,
            observation=(
                f"{check.op!r} is only defined for integer values; observed {label} {actual!r} "
                f"is not an integer"
            ),
        )
    if ok:
        return CheckResult(
            status=CheckStatus.PASS,
            expectation=check.text,
            observation=f"observed {label} {actual!r}",
            confidence=Confidence.CERTAIN,
        )
    return CheckResult(
        status=CheckStatus.FAIL,
        expectation=check.text,
        observation=f"observed {label} {actual!r}, expected {check.op} {rendered}",
        confidence=Confidence.CERTAIN,
    )


def _unquote_repr(text: str) -> str:
    stripped = text.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in "\"'":
        return stripped[1:-1]
    return stripped


def _feature_aliases(feature: str) -> frozenset[str]:
    bare = feature.split(":", 1)[1] if feature.startswith("@feature:") else feature
    return frozenset({feature, bare, feature_id(bare)})


def _flow_events(
    feature: str,
    element_id: str,
    own_events: Sequence[TraceEvent],
    events: Sequence[TraceEvent],
    *,
    write: bool,
) -> set[str]:
    """Find runtime evidence that *element_id* read or wrote *feature*.

    The contract does not fix how a FEATURE_WRITE event names its feature, so both
    readings are accepted and reported the same way: the event may be keyed to the
    feature's own ID (with the writing element as its caller), or keyed to the
    writing element with the feature among its captured value keys.
    """
    names = _feature_aliases(feature)
    own_ids = {event.event_id for event in own_events}
    found: set[str] = set()
    if write:
        for event in events:
            if event.kind is not EventKind.FEATURE_WRITE:
                continue
            if event.element_id in names and event.caller_event_id in own_ids:
                found.add(event.event_id)
            elif event.element_id == element_id and names & set(event.values):
                found.add(event.event_id)
        return found
    for event in own_events:
        if names & set(event.values):
            found.add(event.event_id)
    return found


def _index_events(events: Sequence[TraceEvent]) -> dict[str, tuple[TraceEvent, ...]]:
    grouped: dict[str, list[TraceEvent]] = {}
    for event in events:
        if not event.element_id:
            continue
        grouped.setdefault(event.element_id, []).append(event)
    return {
        key: tuple(sorted(value, key=lambda event: (event.sequence, event.event_id)))
        for key, value in grouped.items()
    }


def _merge_evidence(results: Sequence[CheckResult]) -> tuple[str, ...]:
    ids: set[str] = set()
    for result in results:
        ids.update(result.evidence_ids)
    return tuple(sorted(ids))


def _expectation_count(intent: Intent) -> int:
    return len(intent.invariants) + len(intent.expected_reads) + len(intent.expected_writes)


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


def intents_jsonl(intents: Sequence[Intent]) -> str:
    """``intents.jsonl`` -- sorted, canonical, byte-identical across runs."""
    return canonical_jsonl(intents, "id")


def verdicts_jsonl(verdicts: Sequence[AlignmentVerdict]) -> str:
    """``runtime/<run_id>/verdicts.jsonl``."""
    return canonical_jsonl(verdicts, "id")


def coverage_json(coverage: Coverage) -> str:
    """``coverage.json`` -- how much of the intent set was checkable, and against
    what. Integers only, canonical, byte-identical across runs."""
    from cascade_map.contracts.interfaces import canonical_dumps

    return canonical_dumps(coverage.to_dict()) + "\n"


def issues_jsonl(issues: Sequence[Unresolved]) -> str:
    """Registry issues, in the shared ``unresolved.jsonl`` shape."""
    return canonical_jsonl(issues, "id")
