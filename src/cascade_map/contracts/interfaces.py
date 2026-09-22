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

SCHEMA_VERSION = "1.1.0"

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
    "PackageRequirement",
    "InstalledPackage",
    "PackageUsage",
    "InterpreterRequirement",
    "RunRecord",
    "BlockedAttempt",
    "ScenarioFailure",
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
    "local_id",
    "param_id",
    "key_id",
    "attr_id",
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
    "DependencyCard",
    "RunObserver",
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


def local_id(element_id: str, name: str, ordinal: int = 1) -> str:
    """ID for a local binding inside a function.

    Card 4 needs a node per *binding*, not per name: `y` before and after
    `y += 1` are different values and must not share a slice. The `#n` suffix
    carries that, the same way it separates redefinitions in `make_id`.

    The `<locals>` segment mirrors Python's own `__qualname__` convention and
    keeps a local from colliding with an attribute of the same name, which
    `attr_id` would otherwise produce.

    **The ordinal is required, not optional.** Modelling every rebinding of a
    name as one node with a self-edge merges `x = a` and `x = b`, so a backward
    slice of the final `x` returns `a` -- a value that cannot reach the point
    being queried. That is a false positive in the exact question card 4 exists
    to answer, and precision is this card's stated priority. Reassignment is
    common in data pipelines, so the cost is not hypothetical.
    """
    base = f"{element_id}.<locals>.{name}"
    return base if ordinal <= 1 else f"{base}#{ordinal}"


def param_id(element_id: str, name: str) -> str:
    """ID for a parameter of a function, method or lambda.

    Card 4 reported it had no helper and was using card 1's convention
    informally. A parameter is a binding like any other, so it needs an ID in
    the same space -- a slice that reaches a parameter must be able to name it.
    """
    return f"{element_id}.<param>.{name}"


def key_id(node_id: str, key: str) -> str:
    """ID for one key inside a container node.

    `d["price"]` is its own lineage node so that a slice of one key never drags
    in its siblings -- the distinction the owner actually reasons in.
    """
    return f"{node_id}[{key}]"


def attr_id(node_id: str, attr: str) -> str:
    """ID for an attribute of an object node, e.g. `self.threshold`."""
    return f"{node_id}.{attr}"


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

    # Card 17 — dependency and version applicability.
    UNDECLARED_DEPENDENCY = "UNDECLARED_DEPENDENCY"
    """The target imports a distribution no manifest declares. It works on the
    machine that happens to have it and fails on the one that does not."""

    MISSING_DEPENDENCY = "MISSING_DEPENDENCY"
    """The target imports a distribution the target environment does not have
    installed. An ImportError waiting for the code path that reaches it."""

    VERSION_CONFLICT = "VERSION_CONFLICT"
    """The installed version falls outside the declared constraint. The single
    most common cause of "it worked yesterday": the code is written against one
    API and the machine is running another."""

    UNUSED_DEPENDENCY = "UNUSED_DEPENDENCY"
    """Declared and installed, and nothing imports it. Not an error — reported
    because a dependency nobody uses is usually a leftover, and pinning it
    constrains upgrades for no reason."""

    INTERPRETER_TOO_OLD = "INTERPRETER_TOO_OLD"
    """An element's own syntax requires a newer Python than the environment
    provides or the project declares."""


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
# Card 17 — dependency and version applicability
# ---------------------------------------------------------------------------
#
# The question this answers: "which package versions is this element actually
# applicable to, and does this machine have them?"
#
# Three facts are gathered separately and then joined, and keeping them
# separate is the whole design. What the project DECLARES it needs, what is
# INSTALLED where the target runs, and what the code actually USES are three
# different things, and every interesting failure is a disagreement between
# two of them. A single merged "dependency" record would hide exactly the
# disagreement the owner is looking for.
#
# Constraint 1 applies to the environment as strictly as to the code: an
# installed distribution is read from its `*.dist-info/METADATA` or
# `*.egg-info/PKG-INFO` **as text**. Nothing is imported, and `pip` is never
# invoked. Reading a package's metadata must not run its `__init__`.


@dataclass(frozen=True, slots=True)
class PackageRequirement:
    """One declared dependency constraint, as written, and where it was written.

    `specifier` is stored exactly as the manifest spells it, never normalised
    into a parsed range. An owner debugging a version problem needs to see the
    string they typed, at the line they typed it, and a re-spelling of it is a
    second thing to distrust.
    """

    id: str
    distribution: str
    """PEP 503 normalised name: lowercase, runs of `-_.` collapsed to `-`.
    `Scikit_Learn` and `scikit-learn` are one distribution, and joining on the
    raw spelling silently reports one as undeclared."""

    raw: str
    """The requirement line exactly as written, for the owner to recognise."""

    specifier: str
    """The version constraint as written (`>=1.3,<2.0`). Empty means the
    manifest declared no constraint at all — which is itself worth seeing."""

    extras: tuple[str, ...]
    marker: str
    """PEP 508 environment marker as written (`python_version < "3.11"`), or
    empty. Never evaluated here: a marker decides applicability per machine,
    and this card reports rather than resolves."""

    optional_group: str
    """The extra or group this came from (`dev`, `test`), or empty for a base
    requirement. A conflict in a dev-only group is not the same finding as one
    in the runtime set."""

    span: SourceSpan
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class InstalledPackage:
    """One distribution present in the environment the target runs under.

    Read from installed metadata **as text**. Never imported.
    """

    id: str
    distribution: str
    version: str
    """The exact installed version string. Never a range, never inferred."""

    location: str
    import_names: tuple[str, ...]
    """The top-level module names this distribution provides, so `cv2` can be
    joined to `opencv-python` and `sklearn` to `scikit-learn`. An import name
    that maps to no installed distribution, or to more than one, is an
    `Unresolved`, never a guess."""

    requires: tuple[str, ...]
    """`Requires-Dist` lines as written: the transitive constraints that a
    direct upgrade has to satisfy too."""

    provenance: Provenance


@dataclass(frozen=True, slots=True)
class PackageUsage:
    """Which elements depend on one distribution, and whether that reaches a
    decision.

    This is the join that makes the card worth having. "pandas is pinned wrong"
    is a note; "pandas is pinned wrong and 37 elements use it, 9 of which are on
    a path to your final decision, and here are their ids" is a work order.
    """

    id: str
    distribution: str
    import_names: tuple[str, ...]
    element_ids: tuple[str, ...]
    attribute_paths: tuple[str, ...]
    """The API surface actually touched: `pandas.DataFrame.append`,
    `numpy.float_`. Reported as evidence, never checked against a built-in list
    of removals — this tool does not carry a database of other projects'
    release notes, and pretending to would be a heuristic dressed as a fact.
    The owner reads the surface against the installed version."""

    reaches_sink: bool
    reaches_sink_element_ids: tuple[str, ...]
    declared_ids: tuple[str, ...]
    installed_id: str
    """Empty when nothing installed provides these import names."""

    provenance: Provenance


@dataclass(frozen=True, slots=True)
class InterpreterRequirement:
    """The minimum Python an element's own syntax requires.

    Read off the grammar, not inferred: a `match` statement is 3.10+, an
    `except*` group is 3.11+, a walrus is 3.8+. CERTAIN confidence, because the
    code either contains the construct or it does not.
    """

    id: str
    element_id: str
    minimum_python: str
    feature: str
    span: SourceSpan
    provenance: Provenance


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
    dependencies: dict[str, Any] = field(default_factory=dict)
    """Card 17: the distributions this element imports, their declared
    constraints, the installed versions, and the minimum Python its syntax
    needs. Optional in the same way `runtime` is optional — an analysis with no
    manifests and no reachable environment leaves it empty, and the
    completeness gate must not fail a run for that. An EMPTY dict and a dict
    saying "no dependencies" are different claims; emit the latter when the
    element genuinely imports nothing."""

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
class ScenarioFailure:
    """The scenario raised. The run happened; it did not do what was asked.

    Swallowing this silently was the most misleading behaviour in the tool. A
    scenario pointed at the wrong root reported 524 events, 0 mapped, 0
    blocked and exit 0 -- every event being the failed import's own machinery.
    An owner reads that as "my engine ran and the static map was entirely
    wrong" and goes hunting a bug in their analysis, when not one line of their
    code executed.

    `stage` separates the two cases because they are different problems.
    `import` means the module could not be loaded at all. `call` means the
    module imported and the named function was missing or raised -- and a clean
    `AttributeError` there is a scenario-declaration error, not a fact about
    the target.

    An `import` failure stays a completed run rather than a refusal, on card
    11's reasoning: a target whose own nested imports are broken is a genuine
    finding about the target, and it cannot be reliably told apart from a
    mis-declared root. Refusing would hide the more interesting of the two.
    """

    stage: str
    exception_type: str
    message: str
    traceback: str = ""
    """Bounded. Capped explicitly rather than silently truncated -- the same
    rule `ValueCapture` follows, for the same reason."""


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
    unguaranteed: tuple[str, ...] = ()
    """Escape paths this harness knows it cannot close, named in every run.

    Constraint 7 requires Mode A to be incapable of real-world side effects and
    a run to refuse when that cannot be guaranteed. A pure `sys.audit` design
    cannot make the guarantee absolute: card 11 constructed a direct
    `_posixsubprocess.fork_exec` call that fires no audit event at all, and a
    child process permitted by `declared_process_names` is unaudited once it is
    running. Both are demonstrated, not theoretical.

    Refusing every run over a limit that no run can avoid would make Mode A
    unusable, so the honest form of constraint 7 here is disclosure: each entry
    names a path the controls do not cover, and it travels with the run record
    the owner reads. An empty tuple is a claim of complete coverage and must
    never be the default for a limit that is merely unmeasured.

    Closing these needs OS-level isolation -- seccomp, namespaces, a container
    -- underneath the harness. That is outside this card, and stating so beats
    implying a guarantee the code does not make.
    """

    sandbox_dir: str = ""
    """Where writes were redirected. Card 12 needs it to locate a recording,
    and the owner needs it to find what the run produced."""

    observed_versions: dict[str, str] = field(default_factory=dict)
    """Card 17, Mode A: distribution -> version as actually loaded in the
    traced process, tagged RUNTIME_OBSERVED.

    The static answer reads metadata off disk; this reads what the interpreter
    really imported. They disagree more often than anyone expects -- a stale
    entry earlier on `sys.path`, a second virtualenv, an editable install
    shadowing a pinned one, a `.pth` file. When the static and observed answers
    differ, THAT is the finding, and it is the one a version bug hides behind.
    Empty when no run has happened."""

    scenario_failure: ScenarioFailure | None = None
    """Set when the scenario raised. `None` means it completed."""

    observer_failure: ScenarioFailure | None = None
    """Set when the **observer** raised -- a bug in the tool, not the target.

    Kept separate from `scenario_failure` because they are opposite findings.
    One says the target misbehaved; this says the target may have run perfectly
    and nobody was watching. Conflating them would send an owner to look at
    their own code for a fault that is ours.

    An observer that fails at `start` means the run was not observed at all,
    and a report of zero events would otherwise read as "my engine did
    nothing". `stage` is `start` or `stop`.
    """

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


class DependencyCard(Protocol):
    """Card 17 — dependency and version applicability.

    Four separate answers, never merged: what the project declares, what the
    environment has installed, what the code actually uses, and what Python
    version the syntax needs. The findings come from the disagreements between
    them.

    `environment_root` is the interpreter tree whose installed metadata is
    read -- `.venv-target` in production. It is read as text. Nothing under it
    is imported or executed, and no package manager is invoked: constraint 1
    covers the target's environment exactly as it covers the target's code.
    An environment that cannot be located is an `Unresolved` with its reason,
    not an empty list that reads as "nothing installed".
    """

    def requirements(self) -> Sequence[PackageRequirement]: ...

    def installed(self, environment_root: str) -> Sequence[InstalledPackage]: ...

    def usage(
        self,
        elements: Sequence[Element],
        edges: Sequence[Edge],
        reachability: Sequence[Reachability],
    ) -> Sequence[PackageUsage]: ...

    def interpreter_requirements(
        self, elements: Sequence[Element]
    ) -> Sequence[InterpreterRequirement]: ...

    def findings(self) -> Sequence[Finding]: ...


class RunObserver(Protocol):
    """Something the harness starts and stops around the target call.

    Card 12 built `Tracer.collector()` documented as "the hook the harness
    installs around the target call". Card 11 had nowhere to install it. Both
    were built to the contract, the contract did not describe the handoff, and
    the gap was invisible until card 10 tried to make them meet.

    The observer has to be started **inside** the sandbox window and stopped
    before it closes, and only the harness controls that window -- so the
    harness owns the installation and this is the seam. Keeping it a protocol
    rather than a concrete type means card 11 never imports card 12: the
    harness must work with no observer at all, and a tracing run is the same
    run with something watching.
    """

    def start(self, run: RunRecord) -> None:
        """Begin observing. Receives the record the harness has just built.

        The record is passed in rather than handed over at construction
        because it is only complete once the harness has verified its
        controls: it carries `controls_active`, `unguaranteed` and the
        deterministic `run_id`. Card 12 refuses to trace a run whose controls
        are not active, and that refusal is only meaningful against the record
        the harness actually produced -- an observer built beforehand would be
        checking values nobody had verified yet.
        """
        ...

    def stop(self) -> None: ...


class HarnessCard(Protocol):
    def start(
        self,
        scenario: str,
        graph_hash: str,
        observer: RunObserver | None = None,
    ) -> RunRecord:
        """Verify every control, then run -- or refuse and say which guarantee
        could not be made. Constraint 7. There is no force option.

        *observer* is started immediately before the target call and stopped
        immediately after, inside the sandbox window and inside the `finally`
        that tears it down -- so an observer is stopped even when the target
        raises. A refused run never starts one: there was nothing to observe.
        """
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
