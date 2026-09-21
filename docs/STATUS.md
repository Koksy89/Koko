# STATUS

The single place a fresh session resumes from. Run `/resume` to read it in context.

_Last updated: 2026-09-21 — design and contracts complete; cards 8 and 1 unblocked._

## Phase

**Mode B, pre-build.** The design documents and the binding contracts exist. The first
bracket of the build order — cards 8 and 1, which run in parallel — is ready to start.

## Cards

| Card | Scope | Subagent | Status |
|---|---|---|---|
| 8 | Fixture corpus | fixture-writer | **READY** |
| 1 | Ingestion & inventory | ingestion-builder | **READY** |
| 2 | Resolution & call graph | resolver-engineer | blocked on 1 |
| 3 | CFG, cascade ordering, decisions | cascade-engineer | blocked on 2 |
| 4 | Data/feature lineage & slicing | lineage-engineer | blocked on 2 |
| 5 | Unplugged detection & hints | findings-builder | blocked on 3, 4 |
| 6 | Version diff & impact | diff-impact-builder | blocked on 3, 4 |
| 16 | Documentation records & completeness gate | docs-builder | blocked on 5, 6 |
| 15 | Viewer | viewer-builder | blocked on 16 |
| 11 | Safe execution harness | harness-builder | blocked on Mode B |
| 12 | Runtime tracer & value capture | tracer-engineer | blocked on Mode B |
| 13 | Intent registry & alignment | alignment-engineer | blocked on 11, 12 |
| 14 | Execution narrative | narrative-builder | blocked on 11, 12 |
| 10 | CLI, integration & validation | the lead | blocked on 16, 15B |

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

## Next action

    /build-card 8      # fixture corpus
    /build-card 1      # ingestion & inventory

These two run in parallel — card 1 needs the corpus to test against, but both can be
delegated in the same step.

## Lessons carried forward

- The guard denies any command whose head is not on its read-only allowlist when the
  command names a protected path. A builder needing a new read-only tool adds it to
  `READ_ONLY` in the hook and says so in its report — it never routes around the guard.
- The guard blocked its own author writing documentation, because heredoc bodies were
  parsed as shell. Fixed and covered by tests. Expect more false positives of that
  shape; each is a one-line allowlist fix plus a regression test.
- `canonical_dumps` rejects floats. This is deliberate and not negotiable per card: float
  repr varies across platforms and constraint 4 cannot survive it. Emit an int, or a
  string you formatted explicitly.
- Cards 2, 4 and 5 report precision *and* recall, and precision is the priority for all
  three. A confident wrong edge, a stitched-across slice and a false unplugged finding
  each cost the owner more than an honest gap.
