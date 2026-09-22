# STATUS

The single place a fresh session resumes from. Run `/resume` to read it in context.

_Last updated: 2026-09-21 — design and contracts complete; cards 8 and 1 unblocked._

## Phase

**Mode B, pre-build.** The design documents and the binding contracts exist. The first
bracket of the build order — cards 8 and 1, which run in parallel — is ready to start.

## Cards

| Card | Scope | Subagent | Status |
|---|---|---|---|
| 8 | Fixture corpus | fixture-writer | **DONE** (PASS, round 3 — escalated model) |
| 1 | Ingestion & inventory | ingestion-builder | **DONE** (PASS, round 2) |
| 2 | Resolution & call graph | resolver-engineer | **DONE** (PASS — precision 100%, recall 100%) |
| 3 | CFG, cascade ordering, decisions | cascade-engineer | **DONE** (PASS, round 2) |
| 4 | Data/feature lineage & slicing | lineage-engineer | **DONE** |
| 5 | Unplugged detection & hints | findings-builder | **DONE** (PASS, round 2) |
| 6 | Version diff & impact | diff-impact-builder | **DONE** (PASS, 1 contract follow-up) |
| 16 | Documentation records & completeness gate | docs-builder | **DONE** (PASS) |
| 15 | Viewer (phases A and B) | viewer-builder | **DONE** (both phases PASS) |
| 11 | Safe execution harness | harness-builder | **DONE** (PASS, round 4 — two limits disclosed) |
| 12 | Runtime tracer & value capture | tracer-engineer | **DONE** (PASS, 3 recorded gaps) |
| 13 | Intent registry & alignment | alignment-engineer | **DONE** (PASS, round 2) |
| 14 | Execution narrative | narrative-builder | **DONE** (PASS, round 2) |
| 10 | CLI, integration & validation | the lead | **DONE** — both modes |

## What exists

- `.claude/agents/` — 14 subagent definitions (13 builders + `verifier`).
- `.claude/hooks/guard_engine.py` — the engine guard, wired as a `PreToolUse` hook.
- `.claude/commands/` — `/build-card`, `/phase-gate`, `/resume`.
- `docs/design/ARCHITECTURE.md` — pipeline, output layout, the three settled decisions,
  incrementality, the three layers of safety, auto-detection policy.
- `docs/design/WORKPLAN.md` — the authoritative definition and acceptance criteria for
  all 14 cards.
- `docs/design/FIXTURES.md` — the full corpus specification, Mode B and Mode A.
- `src/cascade_map/contracts/interfaces.py` — the binding types. **Only the lead edits
  this.** Builders implement against it exactly and request changes in their report.
- `src/cascade_map/contracts/schema.json` — generated from `interfaces.py`; a test fails
  if it is stale.

**111 tests, all passing.** 81 cover the engine guard, 30 cover the contracts.

No analysis code exists yet. That is correct: builders write it, card by card.

## Open — owner input

None of these block cards 8 or 1. Each affected card auto-detects read-only and reports
candidates with evidence rather than choosing silently.

- **Q1 — the final decision sink.** The one that matters. Cards 3-6 grade relevance by
  reachability to it. Without it, reachability is reported `UNKNOWN` everywhere rather
  than wrong everywhere — a gap the owner can act on, not a confident error.
- **Q2 — the entry point(s).**
- **Q3 — which config files wire components.** Degrades card 2's confidence; does not stop it.
- **Q4 — Mode A scenarios and external systems.** Needed before card 11, not before then.

## Mode A phase gate — PASSED

| Check | Result |
|---|---|
| Full suite | 1187 tests, 1155 passed, 0 failed |
| Refusal paths | 7 tests, 0 failed — the harness refuses when it cannot guarantee isolation |
| Escape / adversarial | 17 tests, 0 failed — every attempt blocked **and recorded** |
| Replay determinism | byte-identical across processes and hash seeds |
| Runtime provenance | every event carries `RUNTIME_OBSERVED` with run and event IDs |

Mode A integration produced six more defects that Mode B's gate could not have
found. Three were interface halves neither card could see whole. Three were the
same shape as each other: an exception caught for a defensible reason,
discarded, and the resulting report reading as success — a crashed scenario, a
failed observer, and the viewer rendering both as clean runs.

The last one to fall was determinism. Captured values carried object memory
addresses, so no two runs agreed; and once the hex form was fixed, CPython's
decimal `id()` form still escaped. Card 12's own test had been passing
throughout, because it replayed a single recording twice — both sides shared
the same addresses. Only running the scenario twice exposed it.

## Mode B phase gate — PASSED

| Check | Result |
|---|---|
| Full suite | 1103 tests, 1071 passed, 0 failed |
| Determinism | byte-identical across relative/absolute root and differing `PYTHONHASHSEED` |
| Sentinel | marker absent after analysing the whole corpus — nothing was executed |
| Completeness gate | zero incomplete documentation records |
| Provenance | 1,287 facts, none missing method or confidence |

Integration found two defects that twelve verifier passes did not, both the same
shape — a card correct in isolation meeting an input its own tests never produced:

1. `SourceSpan.path` was emitted as given rather than relative to the target root,
   so card 2 found no source and produced **zero edges** from 426 elements. The
   absolute-root form also broke constraint 4 outright: two machines produced
   different bytes.
2. The completeness gate failed 12 `DATA_FILE`/`CONFIG_KEY` elements over fields
   meaningless for a JSON file. The gate was right; the records assumed every
   element was Python.

## Superseded next action

    /build-card 8      # fixture corpus
    /build-card 1      # ingestion & inventory

These two run in parallel — card 1 needs the corpus to test against, but both can be
delegated in the same step.

## Corpus freeze — a sequencing rule I should have set at the start

**No card whose grade depends on `tests/fixtures/` may be verified while card 8 is
rewriting it.** Cards 2, 3 and 4 are in that set. Their verification waits for card 8 to
land and pass.

This cost a real, misleading result. Card 4 reported precision 1.0 and recall 1.0. By the
time verification ran, 153 of 263 fixture files had changed underneath it and the actual
measured precision was **0.28** — not because card 4 regressed, but because its
hand-written expectations were written against fixture source that no longer exists. The
builder is not at fault for that number.

The general rule: a corpus that moves while it is being read cannot grade anything. This
is the second time the same principle has bitten — the first was a `__pycache__`
directory written into the corpus by a harness test. Both are consequences of running
thirteen builders in one tree, which was my call.

## The keystone resolved

Card 2 now measures **100% precision and 100% recall** over 109 edges, re-derived
independently by verification rather than read from its report. The earlier 37.8% was
card 8's rebuild moving the corpus underneath it, not a defect in the card.

One caveat to keep: the 17 `res_*` fixture *programs* are card 8's, which is independent,
but the *expected edge sets* are card 2's own, plus 14 further programs it wrote itself.
Verification spot-checked method and confidence on six edges of different kinds against
the fixture source and built its own over-linking trap, which the resolver passed. That
is good evidence, not proof. The number is trustworthy for edges of the shapes the corpus
contains; the real target will contain shapes it does not.

## Recorded gaps on passed cards

Card 12 passed with three gaps that are real and should not be forgotten:

1. `test_every_event_carries_runtime_provenance_with_run_and_event_id` is a `for` loop
   with no preceding `assert events`. It passes on empty output. Being fixed.
2. The `UNMAPPED` paths are exercised only through hand-constructed recordings fed to
   `materialise` directly, never through a live `TraceCollector` under `sys.settrace`
   meeting genuinely dynamic or ambiguous code.
3. The "100% mapping rate" rests on `run_linear` — three functions, no branches, no
   external calls, six events. Thin. Not strong evidence until richer fixtures land.

Gaps 2 and 3 are blocked on card 8's corpus and are not card 12's to fix.

## Lessons carried forward

- **A green test suite is not a verifier PASS.** Three defects so far were tests passing
  for the wrong reason: card 5's fixture short-circuited on an empty entry-point list
  before reaching any logic; card 14's anchoring test never exercised the one event kind
  that could break it; card 15 proved one drill-down link resolved, not all of them. Every
  card's verification now asks what the test would fail to catch.
- **Hand-transcribing mechanical data does not scale.** Card 8 twice claimed every line
  number was read from the file; an AST check found 14 wrong across 212 records both
  times. Positional fields are now generated from `ast`; only semantic expectations stay
  hand-derived. The model assignment was also wrong for the work and was escalated.
- **A missing contract field becomes two wrong answers.** Cards 5 and 15 both needed
  decision reachability, found no canonical carrier, and diverged. Card 15 reported it;
  card 5 invented one and shipped a silent blind spot. `Reachability` now exists. When a
  field is missing, report it — that is what the rule is for, and it worked.
- **Scope narrowing to make a test pass is a defect even when the narrowing is
  defensible.** Card 5 excluded MODULE and CLASS from unreachability and a genuinely dead
  module went unreported, invisibly. If a limit is real it must be visible in the emitted
  output, because the owner reads artifacts, not source.
- **Guess labels must be marked as guesses.** Card 14 named cascade phases
  "ingestion"/"data engineering" from position alone and printed them as fact. Now
  "segment 1", with the text disclosing that card 3 does not name it.
- **`ContextVar` is the wrong tool for a containment boundary.** Card 11's sandbox was
  fully bypassed by `threading.Thread`, which starts with a fresh context — network
  reached, nothing recorded. Enforcement must be process-wide and fail closed: where the
  hook cannot tell whether it is inside a run, it treats itself as inside.
- The guard hook denies any command whose head is not on its read-only allowlist when the
  command names a protected path. Add to `READ_ONLY` and say so in the report; never route
  around it.
- **Builders' self-reported numbers drift, badly and repeatedly.** Card 5 reported 24
  tests against an actual 20; card 16 reported 137 against 153; card 13 reported 332
  against 397, then 658/7 against an actual 656/38 after being corrected once. Every
  number in this project comes from a run, never from a report.
- **The empty-return probe is the sharpest single check available.** Stub a card's entry
  point to return nothing and run its suite: whatever still passes was never testing the
  card. Card 1 ran it on itself and got 0 of 19. Card 13's 15 of 99 were all registry and
  parser tests that legitimately never call `judge()` — a fair answer, not a defect. Ask
  it of every card, and prefer the builder demonstrating it over asserting it.
- **Writing a demanded test finds bugs bigger than the defect that prompted it.** Three
  times so far: card 15's exhaustive link test found dangling links across the whole page,
  not the one corner flagged; card 13's YAML test found that anchors and aliases in value
  position were silently misread as literal text, corrupting owner intent data; card 1's
  decode test found that DECODE_ERROR always reported line 1. Demand the general test, not
  a fix to the specific case.
- **My own commit hygiene is a verification obstacle.** Two verifiers have now reported
  they could not attribute changes because WIP commits bundle several cards' work, and
  one found a commit titled for card 15 whose diff touched cards 2 and 3. They fell back
  to reading on-disk state. Unavoidable while thirteen builders share one tree, but it
  costs verification accuracy and is worth naming.
- **Adversarial probing finds what code review does not.** Card 11's sandbox was fully
  bypassed by `threading.Thread`, and separately by `multiprocessing` spawn, which calls
  `_posixsubprocess.fork_exec` directly and never trips `subprocess.Popen`'s audit event.
  Neither was visible by reading; both were found by trying to get out.

## Single-file deliverable — DONE

`dist/cascade_map.py` (24,002 lines) is the whole tool in one file, generated by
`tools/amalgamate.py`. `tests/test_amalgamate.py` (5 tests) requires it to match
the package byte for byte on `analyze`, `view` and `trace` over the fixture
corpus. Verified: identical output on Python 3.11, 3.12 and 3.13; the build
itself is byte-identical from 3.12 and 3.13.

Two disclosed, benign differences in Mode A, asserted as exceptions in the test
rather than hidden:

- `run.json:sandbox_dir` — the `--out` path the caller chose.
- `mapping.json:external_frames` / `external_frames_total` / `rate_text` —
  counts of frames skipped because they are **not** the target. The two shapes
  import *themselves* differently (the package child imports from `src/`, the
  single file is already loaded), so the package child additionally sees
  importlib's own frames. Neither number describes the target;
  `total_events`, `mapped_events` and `unmapped_events`, which do, are compared
  exactly and match.

Total suite: **1162 passed, 29 skipped, 3 xfailed, 0 failed.**

### Lessons

- **Text surgery on source cannot tell code from a string that looks like code,
  and the failures are silent.** The amalgamator's first version renamed names
  with a regex and stripped imports by line prefix. Both reached inside string
  literals, and both produced a file that ran without complaint and answered
  differently:
  - renaming the colliding top-level name `__all__` also rewrote the *literal*
    `"__all__"` that `resolve.py` compares an assignment target against, so the
    resolver never recognised a target module's exports and `import *` linked
    every name in it — 110 call edges where the package emits 108, which is
    precisely the over-linking `res_import_star` was written to forbid;
  - stripping lines beginning `from cascade_map` also deleted them from inside
    the string holding the Mode A child process's bootstrap, so the single
    file's `trace` shipped a child script with no imports at all.

  Every edit now goes through the AST or the token stream. Dunders are excluded
  from renaming outright: `__all__` is a per-module convention, not a collision.
- **The corpus caught it on the first comparison.** 108 vs 110 edges and 12 vs
  11 barriers, on a fixture whose `must_not_contain_edges` says exactly why the
  extra edge is wrong. Two rebuild rounds were spent making the corpus able to
  do that; this is what they bought.
- **Refusing to build beats a clever workaround.** Before 3.12, `tokenize`
  returns a whole f-string as one token, so names inside replacement fields are
  invisible. Hand-scanning f-string text for expressions is the same class of
  almost-right string surgery that caused the bug, so the builder refuses on
  <3.12 and says why. The file it generates still supports 3.11+.
- **A shape difference belongs at one named seam, not scattered.** The child
  process's imports are the only thing that must differ between the package and
  the single file, so `cli.py` holds them in one `_CHILD_PROLOGUE` assignment
  that the amalgamator replaces, and the build fails if that assignment is gone.
  Three build-time checks fail loudly rather than emit a file that breaks later:
  the names the child prologue lifts, the attributes reached through a module
  alias (`from . import views`), and `ast.parse` of the result.
- **Confirm the deliverable's shape before building it.** The package was 24,500
  lines across 34 modules before it was established that the owner wanted one
  file. Nothing was wasted — the amalgamation reuses all of it — but the
  question costs one sentence and was never asked.

## Card 15 phase C — blueprint canvas — DONE

An interactive node canvas, `metatron blueprint`: one offline HTML file, three
tabs (execution / lineage / diff) sharing camera and selection, stage cards
derived from card 3's cascade order, pan/zoom/drag/search, module collapse,
filters, four palettes. Suite: **1235 passed, 28 skipped, 3 xfailed, 0 failed.**

Measured, not reported: fit scale 0.358 stage mode / 0.282 full graph; world
3960x2620; zero page and console errors; `elementFromPoint` over a node returns
that node; worst contrast per palette 3.31 / 3.61 / 4.37 / 4.92 with body text
above 5.2:1.

Flow-vs-order classification is the substantive feature: every execution edge is
labelled FORWARD / WITHIN / BACKWARD / UNORDERED against the derived cascade
order, backward edges route as distinct return wires, and a readout gives totals
and per-stage-pair counts. On the corpus, 19 / 77 / 1 / 35 -- the single BACKWARD
being a real circular import the classification found on its own.

### Lessons

- **A card can pass every test it has and not work at all.** 32 data tests
  passed while the page rendered a blank canvas: `#empty-state { display:flex }`
  beat the UA stylesheet's `[hidden] { display:none }`, so an opaque overlay
  covered the graph in the canvas's own background colour. Nothing in Python
  could see it. The fix that matters is not the CSS line, it is
  `tests/test_blueprint_render.py`: drive the real page in a real browser and
  assert that pointing at a node's centre hits that node.
- **Fourth instance of "a failure that renders as success."** After the crashed
  scenario, the failed observer, and the viewer showing both as clean runs. The
  build instructions named the pattern and it happened anyway -- naming a defect
  class does not prevent it; a test that can observe it does.
- **The same shape reappeared twice more in one card.** Filter handlers painting
  all three tabs into one shared container so the DOM held whichever tab was
  last in an array; and the Diff tab rendering 113 nodes of which only 12
  carried change information, because collapsed modules rolled nothing up --
  which at default collapse would have read as "nothing changed" on a real
  engine.
- **A proxy metric passes what it cannot express.** The guard on layout was
  "aspect ratio < 6:1". A 3060x9450 canvas is 3.1:1 and opens at 8.8% zoom,
  where nothing is legible. Aspect ratio cannot express "too small to read".
  Replaced with a floor on the measured fit scale, which is the actual
  requirement.
- **Recursion has no fixed point in longest-path layering.** Capped iteration
  inflated everything downstream of a cycle to ~458 layers, 137,330px wide.
  Layering the condensation into strongly-connected components fixes it
  properly: max layer 458 -> 4.
- **Percentage padding on an absolutely-positioned element resolves against the
  positioned ancestor's width.** On a multi-thousand-pixel canvas that turned a
  158px node into 1250px. Three CSS bugs in this card (this, a specificity
  accident, and a re-parented close button destroyed on the second render) were
  all invisible to data tests and all found by driving the page.
- **Verify the shipped artifact, not a rebuild of it.** `test_amalgamate.py`
  builds its own copy in a temp dir, so five green tests say nothing about
  whether the committed `dist/cascade_map.py` is current. Rebuild and `cmp`.
- **Do not name what the analysis did not name.** Stages are `Stage 3` plus
  their member list, never `Feature Engineering` -- the same discipline card 14
  had to be corrected on.
