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
    "ReachabilityState",
    "Reachability",
    "DetectedCandidate",
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
    "Contradiction",
    "NondeterminismObservation",
    "MappingReport",
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

    normalized_body_hash: str = ""
    """Hash of the body with comments, docstrings and whitespace normalized away.

    `content_hash` covers the source text exactly, which is what incrementality
    needs but not what "is this the same logic" needs: a reformat changes it.
    Card 5 needs this to report DUPLICATED_LOGIC and card 6 to classify a
    reformat as UNCHANGED. Card 6 already derives it with `tokenize`, so
    without a field the same fact is computed twice, differently, in two
    cards — and the two will drift.

    Empty for elements with no body."""

    literal_value: str = ""
    """The repr of an assignment's value when it is a literal constant.

    Card 5 needs it for DEAD_BRANCH: a branch guarded by a name bound to a
    constant is decidable, and without the value the analysis cannot tell a
    dead branch from a live one. A string rather than the object, so
    `canonical_dumps` stays float-free and the artifact stays comparable.

    Empty when the value is not a literal — which is the common case, and must
    never be read as "the literal was empty"."""


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
    """How sure we are that the true target is **somewhere in this set** — not
    that any particular candidate is right.

    * `UNKNOWN` — the set may not contain the target at all. A name computed at
      runtime could resolve to anything, so an enumeration of plausible
      matches is a starting point, not a bound.
    * `HEURISTIC` — the set was enumerated from something real and probably
      contains the target, but which member is a guess. A star import
      resolved through a module's `__all__` is this.
    * `PROBABLE` — the set definitely contains the target and only runtime
      type decides which. MRO candidates for an overridden method are this.

    Card 2 reported the rule was unstated and that it had matched each fixture
    case individually; it was right that the contract was silent."""


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


class ReachabilityState(StrEnum):
    REACHES_SINK = "REACHES_SINK"
    NO_SINK_PATH = "NO_SINK_PATH"
    UNKNOWN = "UNKNOWN"
    """An unresolved call site lies on the way, or no decision sink is known.
    Distinct from NO_SINK_PATH, and the distinction is the point: "I could not
    tell" must never render the same as "this reaches nothing"."""


@dataclass(frozen=True, slots=True)
class Reachability:
    """Whether an element can reach a decision sink. Card 3 emits one per element.

    This exists because "every element is marked for decision reachability" had
    no canonical carrier. Card 5 and card 15 each needed the answer, found no
    field holding it, and derived their own -- which is precisely the second
    source of truth ARCHITECTURE.md exists to prevent.

    It belongs to card 3, not to `Element`: card 1 mints elements before any
    call graph exists and cannot know this.

    WORKPLAN card 3 requires a bias toward REACHES_SINK when an edge is
    uncertain, because a false "unreachable" sends the owner to delete live
    code. `reason` records why, and `provenance.confidence` carries the
    weakest edge the verdict rests on.
    """

    id: str
    element_id: str
    state: ReachabilityState
    provenance: Provenance
    sink_ids: tuple[str, ...] = ()
    path_ids: tuple[str, ...] = ()
    """One representative path to a sink. Not every path -- that is unbounded.
    Callers that need all paths walk the order graph themselves."""

    reason: str = ""
    """Required when state is UNKNOWN, or when a bias toward REACHES_SINK was
    applied. An unexplained UNKNOWN is a gate failure, not a verdict."""


@dataclass(frozen=True, slots=True)
class DetectedCandidate:
    """An owner input the tool worked out for itself, reported with evidence.

    TARGET_PROFILE leaves entry points and decision sinks blank until the owner
    fills them in, and the standing rule is to auto-detect read-only and report
    what was detected — never to pick one silently. Until now there was nowhere
    to put the answer, so card 3 exposed it through a method the contract does
    not name and card 10 would have had to know to call.

    A wrong sink mislabels the entire map, so these are proposals for the owner
    to confirm, never facts. `provenance.confidence` says how strong the signal
    was and `evidence` says what it rested on.
    """

    id: str
    role: str
    """entry_point or decision_sink."""

    element_id: str
    evidence: tuple[str, ...]
    provenance: Provenance


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
    """**The statement where the flow happens**, not where either endpoint was
    defined.

    For ``result = compute(x)`` the span is that assignment, even though ``x``
    was defined earlier and ``compute`` elsewhere. Settled because the owner's
    question of a lineage edge is "where does this value move", and the
    definition sites are already reachable through the endpoint elements. A
    span pointing at a definition would duplicate what the element already
    says and answer the wrong question.
    """


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
    AMBIGUOUS = "AMBIGUOUS"
    """Several candidates matched and none is being claimed.

    WORKPLAN card 6 requires ambiguous matches to be *reported as ambiguous*.
    Without this, verification found only one side of a tied rename carried the
    doubt: the other side rendered as a plain CERTAIN ADDED with no
    back-reference, so an owner scanning by `kind` alone saw half an ambiguity
    and a confident answer where there was none."""


@dataclass(frozen=True, slots=True)
class VersionChange:
    id: str
    kind: ChangeKind
    before_id: str
    after_id: str
    provenance: Provenance
    candidate_ids: tuple[str, ...] = ()
    """Set when kind is AMBIGUOUS: every element that matched equally well.

    Both sides of an ambiguity carry the same candidate set, so the doubt is
    visible from whichever end the owner is reading."""


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
    sandbox_dir: str = ""
    """Where writes were redirected. Card 12 needs it to locate a recording,
    and the owner needs it to find what the run produced."""

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
class Contradiction:
    """An observation that disagrees with a static claim.

    Card 12 needs these and `Unresolved` cannot carry them: it has no
    `Provenance`, so it cannot hold the run and event IDs constraint 2 requires
    of every runtime fact. A contradiction recorded without its evidence is not
    checkable, which defeats the point of recording it.

    These are among the most valuable records the tool produces. An edge the
    static graph predicted that never fires, a call it did not predict, an
    order that differs -- each marks a place the static analysis was wrong, and
    the owner wants to know exactly where. The static graph is never edited to
    match; the disagreement is the finding.
    """

    id: str
    element_id: str
    claim: str
    """What the static graph asserts, as a checkable sentence."""

    observation: str
    """What the run actually did."""

    provenance: Provenance
    """Method RUNTIME_OBSERVED, carrying run_id and event_ids."""

    static_evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NondeterminismObservation:
    """Target behaviour that will not reproduce. Recorded, never hidden.

    WORKPLAN card 12 requires nondeterminism in the *target* to be reported as
    an observed property rather than smoothed over, because an engine whose
    decisions depend on wall-clock time or dict ordering is something the owner
    needs told. The tracer's own output stays deterministic regardless.
    """

    id: str
    element_id: str
    kind: str
    """clock, randomness, hash_ordering, thread_interleaving, external_io."""

    detail: str
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class MappingReport:
    """How much of the run the static graph accounted for.

    WORKPLAN card 12 requires the mapping rate to be reported. A trace with a
    low rate is not a bad trace -- it is a precise measurement of where Mode B
    fell short, and it belongs in the output rather than in a builder's report.
    """

    run_id: str
    total_events: int
    mapped_events: int
    unmapped_events: int
    unmapped_by_reason: dict[str, int]


@dataclass(frozen=True, slots=True)
class TraceEvent:
    event_id: str
    """Format ``evt_`` plus a zero-padded 8-digit ordinal: ``evt_00000001``.

    Zero-padded because `events.jsonl` sorts by this field and an unpadded
    ``evt_10`` sorts before ``evt_2``, which would make the artifact's order
    disagree with the run's order. Settled in favour of card 12's minting;
    fixtures writing ``evt_1`` are wrong.
    """

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
        Sequence[CFGBlock],
        Sequence[CFGEdge],
        Sequence[OrderNode],
        Sequence[DecisionPoint],
        Sequence[Reachability],
        Sequence[DetectedCandidate],
        Sequence[Unresolved],
    ]:
        """Blocks, edges, ordering, decision points, one Reachability per
        element, any auto-detected entry/sink candidates, and unresolved records.

        `Reachability` is what cards 5 and 15 read to answer "does this drive
        the final decision" -- neither may derive its own.

        The last two were added after card 3 reported it had nowhere to put
        them: constraint 3 requires unresolved cases to be emitted, and the
        auto-detection rule requires detected candidates to be reported. Both
        were reachable only through methods the contract does not name, so card
        10 would have had to know to call them, and a card 10 that forgot would
        have silently dropped both.
        """
        ...


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
