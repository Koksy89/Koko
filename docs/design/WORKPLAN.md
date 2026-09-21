# WORKPLAN

The authoritative definition of every card. A builder's own prompt summarises its card;
where the two differ, this document wins.

Each card is done when it meets the Definition of Done in `CLAUDE.md` **and** the
acceptance criteria below, verified independently by `verifier`.

## Build order

    Mode B: [8, 1] → [2] → [3, 4] → [5, 6] → [16, 15 phase B] → 10 Mode B
    Mode A: [11, 12] → [13, 14] → [15 phase A] → 10 Mode A

Cards in the same bracket run in parallel. Nothing starts before its bracket.

---

## Card 8 — Fixture corpus

**Agent** `fixture-writer` · **Depends on** nothing · **Emits** `tests/fixtures/`

Build the corpus specified in `FIXTURES.md`: a small program per case plus a hand-written
expectation file. Includes the sentinel.

**Acceptance**
- Every case in `FIXTURES.md` exists with an expectation file.
- Expectations are written from reading the fixture, never recorded from tool output.
- The sentinel writes a marker if imported or executed, and is trivial to assert on.
- Mode A fixtures include adversarial programs attempting network, out-of-sandbox writes
  and subprocesses.
- No fixture reaches outside `tests/fixtures/`.

## Card 1 — Ingestion & inventory

**Agent** `ingestion-builder` · **Depends on** 8 · **Emits** `elements.jsonl`, `unresolved.jsonl`

Walk the target, hash each file, parse with `ast`, mint stable IDs, emit the inventory.

**Acceptance**
- Every element kind in `ElementKind` is produced where the corpus contains one.
- IDs follow `make_id` and survive reformatting; collisions become `ID_COLLISION` records.
- Embedded blobs are recorded as opaque `BLOB` elements with size and location, never
  decoded, decompressed or unpickled.
- Syntax errors, non-UTF-8 files, empty files and oversized files become `Unresolved`
  records; the run continues.
- Memory stays bounded on a 14 MB tree: stream per file, do not hold every AST.
- Cold run and warm run produce identical output; a changed file is re-analysed.
- Blank owner inputs produce reported auto-detected candidates, never a silent choice.

## Card 2 — Resolution & call graph

**Agent** `resolver-engineer` · **Depends on** 1 · **Emits** `edges.jsonl`, `unresolved.jsonl`

Resolve names and wiring into a call graph over card 1's IDs.

**Acceptance**
- Static: absolute, relative, aliased, star and conditional imports; function-local and
  `TYPE_CHECKING` imports; re-exports through `__init__.py` and `__all__`; attribute
  chains; MRO dispatch; `super()`; decorator unwrapping to both wrapper and wrapped.
- Dynamic: `getattr`/`setattr` (literal and traced), `importlib.import_module`,
  `__import__`, registry dicts and lists, decorator registration, `__init_subclass__`,
  and component names in config files resolved to elements with the key as evidence.
- Every edge carries a `Method` and a `Confidence`; the two are consistent.
- Unresolved call sites emit candidate sets, never a promoted guess.
- **Precision and recall reported against the corpus. Precision is the priority**: a
  confident wrong edge is worse than an honest `UNKNOWN`.

## Card 3 — CFG, cascade ordering, decisions

**Agent** `cascade-engineer` · **Depends on** 2 · **Emits** `cfg_blocks.jsonl`, `cfg_edges.jsonl`, `order.jsonl`, `decisions.jsonl`

**Acceptance**
- CFG covers branches, loops, `try`/`except`/`finally`, `with`, comprehensions, `match`,
  early exits, and short-circuit `and`/`or` as branches.
- Ordering gives `SEQUENCE` only where control flow fixes one; otherwise `BRANCH`,
  `MERGE`, `LOOP`, `UNORDERED` or `CYCLE`. Flattening a real branch into a sequence is a
  defect, not a simplification.
- Order confidence is `combine`d from the edge confidences it rests on.
- Decision points record the condition source, the elements it reads, and each outcome.
- Every element is marked for decision reachability. **Bias toward reachable when an edge
  is uncertain, and record why** — a false "unreachable" sends the owner to delete live code.

## Card 4 — Data/feature lineage & slicing

**Agent** `lineage-engineer` · **Depends on** 2 · **Emits** `lineage.jsonl`, `barriers.jsonl`, `slices.jsonl`

**Acceptance**
- Covers assignment, augmented assignment, unpacking, walrus, parameter binding, returns,
  container and attribute writes, dataframe column operations, closures, defaults, and
  mutation through aliases.
- A named feature — dict key or dataframe column — is its own node, not collapsed into
  its container.
- Backward and forward slices return reproducible ID sets with per-hop evidence.
- Flow through reflection, `eval`-built calls and opaque third-party calls ends in a
  `Barrier` record, never stitched across by assumption.
- Precision and recall reported; over- and under-approximation stated separately.

## Card 5 — Unplugged detection & hints

**Agent** `findings-builder` · **Depends on** 3, 4 · **Emits** `findings.jsonl`

**Acceptance**
- Every kind in `FindingKind` detected where the corpus contains one.
- Every finding carries an evidence chain of IDs; one with an empty chain does not ship.
- Confidence flows from the provenance it rests on.
- Elements behind an unresolved call site are `UNKNOWN`, **not** unplugged.
- Derives everything from the graph; re-parsing the target here is a defect.
- **Precision and recall reported. Precision is the priority** — a false positive costs
  the owner real time.

## Card 6 — Version diff & impact

**Agent** `diff-impact-builder` · **Depends on** 3, 4 · **Emits** `changes.jsonl`, `impacts.jsonl`

**Acceptance**
- Matches by ID first, then detects renames and moves with evidence and confidence;
  ambiguous matches are reported as ambiguous.
- Formatting-only and comment-only changes classify as `UNCHANGED`; the normalisation is
  documented in the card's report.
- Wiring diffed too: call edges gained and lost, config keys added, removed, repointed.
- Impact answers: decision path changed, feature slice changed, reachability flipped,
  findings appeared or disappeared.
- Ranked by decision impact, not diff size.
- Symmetric: A→B and B→A agree on what changed.

## Card 16 — Documentation records, completeness gate, enrichment

**Agent** `docs-builder` · **Depends on** 5, 6 · **Emits** `records.jsonl`

**Acceptance**
- One `DocRecord` per inventoried element, assembled from the graph, never by re-parsing.
- The gate fails the run and lists offenders when any element lacks a record or any
  required field is unfilled. "Unknown" is legitimate only when explicit and reasoned.
  **There is no flag to downgrade the gate to a warning.**
- Enrichment: key from `CASCADE_MAP_API_KEY`, never `ANTHROPIC_API_KEY`; output labelled
  with source and model ID; stored separately from facts; never fed back into the graph.
- With no key set, the card works fully and passes all its tests.
- Deterministic artifacts are byte-identical whether or not enrichment ran.

## Card 15 — Viewer

**Agent** `viewer-builder` · **Phase B depends on** 16 · **Phase A depends on** 13, 14

**Acceptance (phase B)**
- Read-only over emitted artifacts. Analysis logic in the viewer is a defect: it creates
  a second source of truth that will drift.
- Element browser, cascade order, call graph with resolution method and confidence
  visible, lineage slices, findings with evidence links, ranked diff, doc records.
- Branches, merges, loops and unordered sets shown as what they are.
- Every view lands on an element ID; every ID is reachable from every view naming it.
- Offline: no CDN fetches, no telemetry. Degrades usefully without optional adapters.

**Acceptance (phase A)**
- Runtime evidence visually distinct from static at all times, tagged with run ID.
- Contradictions between observation and static claim are shown, never merged away.

## Card 11 — Safe execution harness

**Agent** `harness-builder` · **Depends on** Mode B complete · **Emits** `runtime/<run_id>/run.json`

**Acceptance**
- Default-deny: network (including DNS), filesystem writes outside the sandbox, and
  process spawning. Absence of a working control blocks the run rather than allowing it.
- Declared external systems stubbed or replayed; an **undeclared** client is a hard stop.
- Refuses to start when any guarantee cannot be made, naming which. **No force flag, no
  warn-and-continue, no partial mode.** A refusal is a tested, correct outcome.
- Verifies the Mode B graph exists and matches current target hashes; a stale graph is a
  refusal.
- Run record lists controls active and every blocked attempt with what and from where.
- Developed and tested entirely against `tests/fixtures/`.

## Card 12 — Runtime tracer & value capture

**Agent** `tracer-engineer` · **Depends on** Mode B complete · **Emits** `runtime/<run_id>/events.jsonl`

**Acceptance**
- Runs only as a component of a card 11 run; never starts a process itself.
- Every event keyed to a static ID, or emitted as `UNMAPPED` with location and reason.
  **Mapping rate is reported** — unmapped events are where the static analysis was wrong.
- Captures element entry/exit, depth, caller event, arguments, returns, branch taken at
  each decision point, tracked feature values, exceptions, and each final decision.
- Bounded capture: per-value and per-event caps, deterministic summarisation beyond the
  cap, redaction at capture time. A truncation that reads like a complete value is a
  defect; `CaptureStatus` and `original_size` are mandatory when not `FULL`.
- Event IDs deterministic within a run; replaying a recorded run is byte-identical.
- Target nondeterminism recorded as an observed property, not hidden.
- Contradictions with the static graph emitted as records; the static graph is never edited.

## Card 13 — Intent registry & alignment

**Agent** `alignment-engineer` · **Depends on** 11, 12 · **Emits** `intents.jsonl`, `runtime/<run_id>/verdicts.jsonl`

**Acceptance**
- Registry keyed by element ID; unknown IDs, duplicates and malformed entries reported.
- Intents from the owner are `CONFIRMED`; anything inferred is `PROPOSED` and may not
  ground an `ALIGNED` or `MISALIGNED` verdict.
- `NOT_EXERCISED` is never reported as `ALIGNED`. Coverage reported: how many intents
  were checkable, against what.
- `MISALIGNED` names the specific expectation and the specific contradicting observation.
- Model escalation to `claude-sonnet-5` may only *propose*, always labelled with source
  and model ID, always tied to checkable evidence. Never a fact, edge, confidence or
  final verdict. With no key set the card works and passes its tests.

## Card 14 — Execution narrative

**Agent** `narrative-builder` · **Depends on** 11, 12 · **Emits** `runtime/<run_id>/narrative.jsonl`

**Acceptance**
- Every step anchored to element IDs and event IDs. An unanchored sentence does not ship.
- Structured by cascade phase with drill-down; loops summarised with counts and the
  iterations that mattered, not transcribed.
- Decision points carry the condition, the values read, the branch taken, and the
  branches not taken. The path from inputs to final decision is reconstructable from the
  narrative alone.
- Reports what did *not* happen: skipped steps, elements never entered, swallowed
  exceptions, summarised or redacted values, blocked side-effect attempts.
- Deterministic phrasing: the same trace gives byte-identical text.

## Card 10 — CLI, integration & validation

**Owner** the lead · **Mode B depends on** 16, 15B · **Mode A depends on** 13, 14, 15A

`cascade-map analyze`, `diff`, `trace`, `view`. Wires the cards, owns end-to-end
validation on the corpus and on the real target, and produces the owner-facing report.

**Acceptance**
- `cascade-map trace` is the only command that executes target code, and it verifies
  owner approval and a current Mode B graph first.
- End-to-end run on the real target completes, with unresolved counts and confidence
  distribution reported.
- Two full runs byte-identical.
- Full suite green.
