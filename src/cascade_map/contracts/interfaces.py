"""Binding interfaces between CASCADE-MAP cards.

Every card reads and writes these types and nothing else. A card that needs a
field this module does not have reports the gap; it does not invent one.

Three decisions are encoded here, settled as Q5, Q6 and Q7 in OPEN_QUESTIONS.md:

* **Identity** (`make_id`) is structural, never positional. An element keeps its
  ID across reformatting, comment changes and edits elsewhere in the file, which
  is what lets card 6 diff two versions and card 12 key runtime events onto the
  static graph.
* **Confidence** is an ordered enum, not a number, and derived facts take the
  *minimum* of their inputs (`combine`). Thirteen builders applying one of five
  named levels stay consistent; thirteen builders inventing floats do not.
* **Serialization** (`canonical_dumps`) is one function every card must use, so
  that "two runs produce identical output" is a property of the contract rather
  than of each builder's diligence.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Iterable, Protocol, Sequence

SCHEMA_VERSION = "1.0.0"

__all__ = [
    "SCHEMA_VERSION",
    "Confidence",
    "Method",
    "Provenance",
    "SourceSpan",
    "ElementKind",
    "Element",
    "EdgeKind",
    "Edge",
    "UnresolvedReason",
    "Unresolved",
    "BlockKind",
    "CFGBlock",
    "CFGEdge",
    "OrderKind",
    "OrderNode",
    "DecisionPoint",
    "LineageKind",
    "LineageEdge",
    "Barrier",
    "Slice",
    "FindingKind",
    "Finding",
    "ChangeKind",
    "VersionChange",
    "Impact",
    "DocRecord",
    "RunRecord",
    "BlockedAttempt",
    "CaptureStatus",
    "ValueCapture",
    "EventKind",
    "TraceEvent",
    "IntentStatus",
    "Intent",
    "Verdict",
    "AlignmentVerdict",
    "NarrativeStep",
    "make_id",
    "feature_id",
    "file_id",
    "config_key_id",
    "combine",
    "canonical_dumps",
    "canonical_jsonl",
    "IngestionCard",
    "ResolutionCard",
    "CascadeCard",
    "LineageCard",
    "FindingsCard",
    "DiffCard",
    "DocsCard",
    "HarnessCard",
    "TracerCard",
    "AlignmentCard",
    "NarrativeCard",
]


# ---------------------------------------------------------------------------
# Provenance — Q6
# ---------------------------------------------------------------------------


class Confidence(StrEnum):
    """How much weight a fact carries. Ordered; see :func:`combine`."""

    CERTAIN = "CERTAIN"
    """Read directly off the AST. No inference. A `def` exists at this path."""

    RESOLVED = "RESOLVED"
    """Deterministic resolution with exactly one outcome. An absolute import
    that names one module; a call to a module-level function in scope."""

    PROBABLE = "PROBABLE"
    """Resolution required an assumption that holds outside pathological cases.
    MRO dispatch on a known class; `getattr` with a literal name."""

    HEURISTIC = "HEURISTIC"
    """Pattern, name or config-string match. Plausible and possibly wrong."""

    UNKNOWN = "UNKNOWN"
    """Not resolved. Candidates may be listed, but none is being claimed."""


_CONFIDENCE_ORDER: dict[str, int] = {
    Confidence.CERTAIN: 4,
    Confidence.RESOLVED: 3,
    Confidence.PROBABLE: 2,
    Confidence.HEURISTIC: 1,
    Confidence.UNKNOWN: 0,
}


def combine(*confidences: Confidence) -> Confidence:
    """Confidence of a fact derived from others: the weakest link.

    A cascade order built on a HEURISTIC call edge is a HEURISTIC ordering, no
    matter how certain every other step was. Composing by minimum is the only
    rule that cannot quietly launder a guess into a fact.

    With no arguments this is UNKNOWN -- a fact resting on nothing claims
    nothing.
    """
    if not confidences:
        return Confidence.UNKNOWN
    return min(confidences, key=lambda c: _CONFIDENCE_ORDER[c])


class Method(StrEnum):
    """How a fact was established. Always recorded next to its confidence."""

    AST_DIRECT = "AST_DIRECT"
    IMPORT_ABSOLUTE = "IMPORT_ABSOLUTE"
    IMPORT_RELATIVE = "IMPORT_RELATIVE"
    IMPORT_STAR = "IMPORT_STAR"
    REEXPORT = "REEXPORT"
    SCOPE_LOOKUP = "SCOPE_LOOKUP"
    MRO_DISPATCH = "MRO_DISPATCH"
    DECORATOR_UNWRAP = "DECORATOR_UNWRAP"
    GETATTR_LITERAL = "GETATTR_LITERAL"
    GETATTR_TRACED = "GETATTR_TRACED"
    IMPORTLIB_LITERAL = "IMPORTLIB_LITERAL"
    REGISTRY_MEMBERSHIP = "REGISTRY_MEMBERSHIP"
    DECORATOR_REGISTRATION = "DECORATOR_REGISTRATION"
    CONFIG_STRING_MATCH = "CONFIG_STRING_MATCH"
    NAME_HEURISTIC = "NAME_HEURISTIC"
    DATAFLOW = "DATAFLOW"
    CFG_REACHABILITY = "CFG_REACHABILITY"
    STRUCTURAL_MATCH = "STRUCTURAL_MATCH"
    RUNTIME_OBSERVED = "RUNTIME_OBSERVED"
    MODEL_PROPOSED = "MODEL_PROPOSED"
    """A language model suggested this. Never a fact -- label only, and it must
    carry evidence a human can check. See constraint 8."""


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """Where a fact lives. Paths are POSIX and relative to the target root."""

    path: str
    line: int
    end_line: int | None = None
    col: int | None = None


@dataclass(frozen=True, slots=True)
class Provenance:
    """Attached to every fact, edge and verdict. Constraint 2."""

    method: Method
    confidence: Confidence
    span: SourceSpan | None = None
    note: str = ""
    model_id: str = ""
    """Set only when method is MODEL_PROPOSED. Names the model that wrote it."""

    run_id: str = ""
    """Set only for RUNTIME_OBSERVED facts. Constraint 2."""

    event_ids: tuple[str, ...] = ()
    """Set only for RUNTIME_OBSERVED facts. Constraint 2."""


# ---------------------------------------------------------------------------
# Identity — Q5
# ---------------------------------------------------------------------------

_ORDINAL = re.compile(r"#(\d+)$")


def make_id(module: str, qualname: str = "", ordinal: int = 1) -> str:
    """Build a stable element ID.

    ``module::qualname`` for anything inside a module, ``module`` for the module
    itself, and a ``#n`` suffix only when an earlier element already claimed the
    same name -- a conditional redefinition, a name reassigned in two branches.
    The first occurrence in source order carries no suffix, so adding a second
    definition later never renames the first.

    Deliberately positional information is absent. Line numbers would make every
    element a different element after a reformat, which would defeat card 6 and
    strand every runtime event card 12 tries to key onto the graph.
    """
    base = f"{module}::{qualname}" if qualname else module
    return base if ordinal <= 1 else f"{base}#{ordinal}"


def feature_id(name: str) -> str:
    """ID for a named feature -- a dict key or dataframe column card 4 tracks.

    Features get their own namespace because the owner reasons in them. A column
    named in a config file and a column written by a function are the same
    feature and must land on the same node.
    """
    return f"@feature:{name}"


def file_id(path: str) -> str:
    """ID for a non-Python file: config, data, fixture."""
    return f"@file:{path}"


def config_key_id(path: str, pointer: str) -> str:
    """ID for one key inside a config file, addressed by JSON Pointer.

    ``config_key_id("config/wiring.json", "/components/0/class")`` is the node a
    CONFIG_STRING_MATCH edge starts from, so a dangling reference points at the
    exact key rather than the whole file.
    """
    return f"@file:{path}::{pointer}"


# ---------------------------------------------------------------------------
# Serialization — Q7
# ---------------------------------------------------------------------------


def canonical_dumps(payload: Any) -> str:
    """The one serializer every card uses. Constraint 4.

    Sorted keys, no insignificant whitespace, ASCII-escaped, and no trailing
    newline. Determinism is a property of this function, not of each builder
    remembering to sort things.

    Floats are rejected rather than formatted. Their repr varies across
    platforms and a byte-identical guarantee cannot survive that -- emit an int,
    or a string you formatted yourself and can explain.
    """

    def _check(node: Any) -> Any:
        if isinstance(node, float):
            raise TypeError(
                "floats are not permitted in emitted artifacts: they break the "
                "byte-identical guarantee across platforms. Emit an int or a "
                "string you formatted explicitly."
            )
        if isinstance(node, dict):
            return {key: _check(value) for key, value in node.items()}
        if isinstance(node, (list, tuple)):
            return [_check(value) for value in node]
        return node

    return json.dumps(
        _check(payload),
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
        default=_default,
    )


def _default(node: Any) -> Any:
    if isinstance(node, StrEnum):
        return str(node)
    if hasattr(node, "__dataclass_fields__"):
        return asdict(node)
    if isinstance(node, (set, frozenset)):
        return sorted(node)
    raise TypeError(f"not serializable: {type(node).__name__}")


def canonical_jsonl(records: Iterable[Any], sort_key: str = "id") -> str:
    """Render records as sorted JSON Lines, the on-disk artifact format.

    One record per line, sorted by *sort_key*, so that a byte comparison of two
    runs is a meaningful test and `git diff` of two versions is readable. Ends
    with a single trailing newline, or is empty when there are no records.
    """
    rows = [canonical_dumps(record) for record in records]
    keyed = sorted(rows, key=lambda row: (json.loads(row).get(sort_key, ""), row))
    return "".join(f"{row}\n" for row in keyed)


# ---------------------------------------------------------------------------
# Card 1 — inventory
# ---------------------------------------------------------------------------


class ElementKind(StrEnum):
    PACKAGE = "PACKAGE"
    MODULE = "MODULE"
    CLASS = "CLASS"
    FUNCTION = "FUNCTION"
    METHOD = "METHOD"
    PROPERTY = "PROPERTY"
    ASSIGNMENT = "ASSIGNMENT"
    PARAMETER = "PARAMETER"
    IMPORT = "IMPORT"
    DATA_FILE = "DATA_FILE"
    CONFIG_KEY = "CONFIG_KEY"
    BLOB = "BLOB"
    """An embedded string or bytes literal held as opaque data. Never decoded,
    decompressed or unpickled -- constraint 1. ~3.9 MB of the target is this."""

    FEATURE = "FEATURE"


@dataclass(frozen=True, slots=True)
class Element:
    id: str
    kind: ElementKind
    name: str
    qualname: str
    module: str
    span: SourceSpan
    provenance: Provenance
    content_hash: str
    """Hash of the element's own source text. Drives incrementality (constraint
    5) and card 6's UNCHANGED classification."""

    decorators: tuple[str, ...] = ()
    signature: str = ""
    docstring: str = ""
    parent_id: str = ""
    byte_size: int = 0
    """Set for BLOB elements, so the owner can see where the 3.9 MB lives."""


# ---------------------------------------------------------------------------
# Card 2 — resolution and the call graph
# ---------------------------------------------------------------------------


class EdgeKind(StrEnum):
    CALLS = "CALLS"
    IMPORTS = "IMPORTS"
    INHERITS = "INHERITS"
    DECORATES = "DECORATES"
    REGISTERS = "REGISTERS"
    INSTANTIATES = "INSTANTIATES"
    REFERENCES = "REFERENCES"
    CONFIGURES = "CONFIGURES"
    """A config key naming an element. Source is a CONFIG_KEY id."""


@dataclass(frozen=True, slots=True)
class Edge:
    id: str
    kind: EdgeKind
    source_id: str
    target_id: str
    provenance: Provenance
    call_site: SourceSpan | None = None


class UnresolvedReason(StrEnum):
    DYNAMIC_NAME = "DYNAMIC_NAME"
    MISSING_TARGET = "MISSING_TARGET"
    SYNTAX_ERROR = "SYNTAX_ERROR"
    DECODE_ERROR = "DECODE_ERROR"
    TOO_LARGE = "TOO_LARGE"
    AMBIGUOUS = "AMBIGUOUS"
    ID_COLLISION = "ID_COLLISION"
    THIRD_PARTY = "THIRD_PARTY"
    NOT_EXERCISED = "NOT_EXERCISED"


@dataclass(frozen=True, slots=True)
class Unresolved:
    """Constraint 3: nothing is silently dropped.

    An unresolved record is a first-class output, not an absence. Over-linking
    is the failure mode to fear in card 2: an honest UNKNOWN with a candidate
    set is worth more to the owner than a confident wrong edge.
    """

    id: str
    reason: UnresolvedReason
    span: SourceSpan
    description: str
    attempted: tuple[Method, ...] = ()
    candidate_ids: tuple[str, ...] = ()
    candidate_confidence: Confidence = Confidence.UNKNOWN


# ---------------------------------------------------------------------------
# Card 3 — control flow, ordering, decisions
# ---------------------------------------------------------------------------


class BlockKind(StrEnum):
    ENTRY = "ENTRY"
    NORMAL = "NORMAL"
    BRANCH = "BRANCH"
    LOOP_HEAD = "LOOP_HEAD"
    HANDLER = "HANDLER"
    FINALLY = "FINALLY"
    RETURN = "RETURN"
    RAISE = "RAISE"
    EXIT = "EXIT"


@dataclass(frozen=True, slots=True)
class CFGBlock:
    id: str
    element_id: str
    kind: BlockKind
    span: SourceSpan
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class CFGEdge:
    id: str
    source_id: str
    target_id: str
    condition: str = ""
    taken_when: bool | None = None
    provenance: Provenance | None = None


class OrderKind(StrEnum):
    SEQUENCE = "SEQUENCE"
    BRANCH = "BRANCH"
    MERGE = "MERGE"
    LOOP = "LOOP"
    UNORDERED = "UNORDERED"
    """Elements that run, in no order the analysis can fix. Never flatten these
    into a SEQUENCE: the owner needs to see where order is genuinely open."""

    CYCLE = "CYCLE"


@dataclass(frozen=True, slots=True)
class OrderNode:
    id: str
    kind: OrderKind
    element_ids: tuple[str, ...]
    children: tuple[str, ...] = ()
    provenance: Provenance | None = None


@dataclass(frozen=True, slots=True)
class DecisionPoint:
    id: str
    element_id: str
    condition_source: str
    reads_ids: tuple[str, ...]
    outcomes: tuple[tuple[str, str], ...]
    """(label, target order-node or element id) per outcome."""

    is_sink: bool = False
    provenance: Provenance | None = None


# ---------------------------------------------------------------------------
# Card 4 — lineage and slicing
# ---------------------------------------------------------------------------


class LineageKind(StrEnum):
    ASSIGNS = "ASSIGNS"
    PARAMETER_BINDING = "PARAMETER_BINDING"
    RETURNS = "RETURNS"
    CONTAINER_WRITE = "CONTAINER_WRITE"
    COLUMN_WRITE = "COLUMN_WRITE"
    ATTRIBUTE_WRITE = "ATTRIBUTE_WRITE"
    READS = "READS"
    MUTATES = "MUTATES"


@dataclass(frozen=True, slots=True)
class LineageEdge:
    id: str
    kind: LineageKind
    source_id: str
    target_id: str
    provenance: Provenance
    span: SourceSpan | None = None


@dataclass(frozen=True, slots=True)
class Barrier:
    """A point where value flow stops being traceable.

    Recorded rather than stitched across. Reflection, `eval`-built calls and
    opaque third-party functions end a slice here, and the slice says so.
    """

    id: str
    element_id: str
    span: SourceSpan
    reason: UnresolvedReason
    description: str


@dataclass(frozen=True, slots=True)
class Slice:
    id: str
    root_id: str
    direction: str
    """"backward" -- what produces this. "forward" -- what a change affects."""

    member_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]
    barrier_ids: tuple[str, ...]
    reaches_sink_ids: tuple[str, ...]
    confidence: Confidence


# ---------------------------------------------------------------------------
# Card 5 — findings
# ---------------------------------------------------------------------------


class FindingKind(StrEnum):
    UNREACHABLE_ELEMENT = "UNREACHABLE_ELEMENT"
    UNCONSUMED_FEATURE = "UNCONSUMED_FEATURE"
    DANGLING_CONFIG_REFERENCE = "DANGLING_CONFIG_REFERENCE"
    ORPHANED_CONFIG_ELEMENT = "ORPHANED_CONFIG_ELEMENT"
    DEAD_BRANCH = "DEAD_BRANCH"
    SHADOWED_DEFINITION = "SHADOWED_DEFINITION"
    DUPLICATED_LOGIC = "DUPLICATED_LOGIC"
    DECISION_IRRELEVANT = "DECISION_IRRELEVANT"


@dataclass(frozen=True, slots=True)
class Finding:
    id: str
    kind: FindingKind
    element_id: str
    span: SourceSpan
    summary: str
    hint: str
    evidence_ids: tuple[str, ...]
    """Edges, slices and config keys that support this. A finding with an empty
    evidence chain does not ship."""

    provenance: Provenance


# ---------------------------------------------------------------------------
# Card 6 — diff and impact
# ---------------------------------------------------------------------------


class ChangeKind(StrEnum):
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    RENAMED = "RENAMED"
    MOVED = "MOVED"
    SIGNATURE_CHANGED = "SIGNATURE_CHANGED"
    BODY_CHANGED = "BODY_CHANGED"
    DECORATORS_CHANGED = "DECORATORS_CHANGED"
    UNCHANGED = "UNCHANGED"


@dataclass(frozen=True, slots=True)
class VersionChange:
    id: str
    kind: ChangeKind
    before_id: str
    after_id: str
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class Impact:
    id: str
    change_id: str
    affected_ids: tuple[str, ...]
    decision_paths_changed: bool
    features_changed: tuple[str, ...]
    reachability_flipped: tuple[str, ...]
    findings_added: tuple[str, ...]
    findings_removed: tuple[str, ...]
    rank: int
    """Ordered by decision impact, not diff size. A one-line change inside a
    decision condition outranks a 500-line refactor that reaches no sink."""


# ---------------------------------------------------------------------------
# Card 16 — documentation records
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DocRecord:
    """One per element. The completeness gate fails the run if any is missing
    or has an unfilled required field. Constraint 8."""

    id: str
    element_id: str
    identity: dict[str, Any]
    cascade_position: dict[str, Any]
    data_role: dict[str, Any]
    decision_relevance: dict[str, Any]
    finding_ids: tuple[str, ...]
    change_ids: tuple[str, ...]
    provenance: Provenance
    runtime: dict[str, Any] = field(default_factory=dict)
    model_prose: str = ""
    """Model-written text, structurally separate from every field above and
    never fed back into the graph. Empty unless enrichment ran."""

    model_id: str = ""


# ---------------------------------------------------------------------------
# Cards 11-12 — harness and tracer
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BlockedAttempt:
    """A side effect the harness refused. A finding the owner wants, not noise."""

    id: str
    kind: str
    detail: str
    element_id: str = ""
    event_id: str = ""


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    target_hashes: dict[str, str]
    graph_hash: str
    """Hash of the Mode B graph this run overlays. A stale graph is a refusal."""

    scenario: str
    interpreter: str
    controls_active: dict[str, bool]
    blocked: tuple[BlockedAttempt, ...]
    refused: bool = False
    refusal_reason: str = ""
    """Set when the run refused to start. Constraint 7: a refusal is a correct
    outcome, never a warning to proceed past."""


class CaptureStatus(StrEnum):
    FULL = "FULL"
    SUMMARIZED = "SUMMARIZED"
    REDACTED = "REDACTED"
    DROPPED = "DROPPED"


@dataclass(frozen=True, slots=True)
class ValueCapture:
    """A captured value, or an explicit account of why it is not the whole one.

    The target moves multi-megabyte frames. A truncation that reads like a
    complete value is worse than no capture, so status is mandatory and
    original_size is recorded whenever the value was not kept whole.
    """

    status: CaptureStatus
    repr_text: str
    type_name: str = ""
    shape: str = ""
    original_size: int = 0
    reason: str = ""


class EventKind(StrEnum):
    CALL = "CALL"
    RETURN = "RETURN"
    BRANCH = "BRANCH"
    EXCEPTION = "EXCEPTION"
    FEATURE_WRITE = "FEATURE_WRITE"
    DECISION = "DECISION"
    UNMAPPED = "UNMAPPED"
    """An event that maps to no static element. Never dropped: these are exactly
    where the static analysis was wrong, which is worth reporting."""


@dataclass(frozen=True, slots=True)
class TraceEvent:
    event_id: str
    run_id: str
    kind: EventKind
    element_id: str
    sequence: int
    depth: int
    caller_event_id: str = ""
    values: dict[str, ValueCapture] = field(default_factory=dict)
    branch_taken: str = ""
    provenance: Provenance | None = None


# ---------------------------------------------------------------------------
# Cards 13-14 — alignment and narrative
# ---------------------------------------------------------------------------


class IntentStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    """Owner-confirmed. Only these may ground an ALIGNED or MISALIGNED verdict."""

    PROPOSED = "PROPOSED"
    """Derived from a docstring or a name. Never authoritative."""


@dataclass(frozen=True, slots=True)
class Intent:
    id: str
    element_id: str
    status: IntentStatus
    statement: str
    invariants: tuple[str, ...] = ()
    expected_reads: tuple[str, ...] = ()
    expected_writes: tuple[str, ...] = ()
    provenance: Provenance | None = None


class Verdict(StrEnum):
    ALIGNED = "ALIGNED"
    MISALIGNED = "MISALIGNED"
    UNVERIFIABLE = "UNVERIFIABLE"
    NOT_EXERCISED = "NOT_EXERCISED"
    """No scenario ran this. Unverified -- distinct from ALIGNED, and saying so
    is the honest answer."""

    NO_INTENT = "NO_INTENT"


@dataclass(frozen=True, slots=True)
class AlignmentVerdict:
    id: str
    element_id: str
    intent_id: str
    verdict: Verdict
    expectation: str
    observation: str
    evidence_ids: tuple[str, ...]
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class NarrativeStep:
    """One step of the execution narrative. Anchored or it does not ship."""

    id: str
    run_id: str
    sequence: int
    phase: str
    text: str
    element_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    children: tuple[str, ...] = ()
    model_prose: str = ""
    model_id: str = ""


# ---------------------------------------------------------------------------
# Card entry points
# ---------------------------------------------------------------------------


class IngestionCard(Protocol):
    def inventory(self, root: str) -> tuple[Sequence[Element], Sequence[Unresolved]]: ...


class ResolutionCard(Protocol):
    def resolve(
        self, elements: Sequence[Element]
    ) -> tuple[Sequence[Edge], Sequence[Unresolved]]: ...


class CascadeCard(Protocol):
    def order(
        self, elements: Sequence[Element], edges: Sequence[Edge], entry_ids: Sequence[str]
    ) -> tuple[
        Sequence[CFGBlock], Sequence[CFGEdge], Sequence[OrderNode], Sequence[DecisionPoint]
    ]: ...


class LineageCard(Protocol):
    def trace_values(
        self, elements: Sequence[Element], edges: Sequence[Edge]
    ) -> tuple[Sequence[LineageEdge], Sequence[Barrier]]: ...

    def slice(self, root_id: str, direction: str) -> Slice: ...


class FindingsCard(Protocol):
    def find(self) -> Sequence[Finding]: ...


class DiffCard(Protocol):
    def diff(
        self, before_root: str, after_root: str
    ) -> tuple[Sequence[VersionChange], Sequence[Impact]]: ...


class DocsCard(Protocol):
    def records(self) -> Sequence[DocRecord]: ...

    def completeness_gate(self, records: Sequence[DocRecord]) -> Sequence[str]:
        """Return the IDs of elements with missing or incomplete records.

        A non-empty result fails the run. There is no flag to downgrade it.
        """
        ...


class HarnessCard(Protocol):
    def start(self, scenario: str, graph_hash: str) -> RunRecord:
        """Verify every control, then run -- or refuse and say which guarantee
        could not be made. Constraint 7. There is no force option."""
        ...


class TracerCard(Protocol):
    def trace(self, run: RunRecord) -> Sequence[TraceEvent]: ...


class AlignmentCard(Protocol):
    def judge(
        self, intents: Sequence[Intent], events: Sequence[TraceEvent]
    ) -> Sequence[AlignmentVerdict]: ...


class NarrativeCard(Protocol):
    def narrate(
        self,
        events: Sequence[TraceEvent],
        order_nodes: Sequence[OrderNode],
        decisions: Sequence[DecisionPoint],
        run: RunRecord,
    ) -> Sequence[NarrativeStep]:
        """Render a trace as an ordered, anchored account of the run.

        The static structures are parameters rather than fields copied onto
        each event, because WORKPLAN card 14 requires things a trace alone
        cannot supply:

        * **Cascade phase.** `EventKind` distinguishes a feature write from a
          decision, but nothing in it separates ingestion from data
          engineering. That grouping lives in card 3's `OrderNode`s.
        * **Branches not taken.** A trace records the branch that ran.
          `DecisionPoint.outcomes` is the only record of the ones that did not,
          and reporting them is explicitly part of this card.
        * **Blocked side-effect attempts.** These are card 11 findings and live
          on `RunRecord`, not on any event.

        Passing them in keeps one source of truth. Denormalising a `phase`
        field onto every `TraceEvent` would duplicate card 3's answer into
        card 12's output, where it would drift -- the same reason
        ARCHITECTURE.md makes runtime evidence an overlay rather than a second
        graph.
        """
        ...
