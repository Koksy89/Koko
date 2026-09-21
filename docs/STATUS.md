# STATUS

The single place a fresh session resumes from. Run `/resume` to read it in context.

_Last updated: 2026-09-21 — scaffolding complete, no cards built._

## Phase

**Pre-build.** The scaffolding is in place. No WORKPLAN card has been started, and none
can start until the design documents exist (see Blockers).

## Cards

| Card | Scope | Subagent | Status |
|---|---|---|---|
| 8 | Fixture corpus | fixture-writer | NOT STARTED |
| 1 | Ingestion & inventory | ingestion-builder | NOT STARTED |
| 2 | Resolution & call graph | resolver-engineer | NOT STARTED |
| 3 | CFG, cascade ordering, decisions | cascade-engineer | NOT STARTED |
| 4 | Data/feature lineage & slicing | lineage-engineer | NOT STARTED |
| 5 | Unplugged detection & hints | findings-builder | NOT STARTED |
| 6 | Version diff & impact | diff-impact-builder | NOT STARTED |
| 16 | Documentation records & completeness gate | docs-builder | NOT STARTED |
| 15 | Viewer | viewer-builder | NOT STARTED |
| 11 | Safe execution harness | harness-builder | NOT STARTED |
| 12 | Runtime tracer & value capture | tracer-engineer | NOT STARTED |
| 13 | Intent registry & alignment | alignment-engineer | NOT STARTED |
| 14 | Execution narrative | narrative-builder | NOT STARTED |
| 10 | CLI, integration & validation | the lead | NOT STARTED |

## What exists

- `.claude/agents/` — 14 subagent definitions (13 builders + `verifier`), models and
  effort levels matching the role table in `CLAUDE.md`.
- `.claude/hooks/guard_engine.py` — the engine guard, wired in `.claude/settings.json`
  as a `PreToolUse` hook. 81 tests, all passing.
- `.claude/commands/` — `/build-card`, `/phase-gate`, `/resume`.
- `pyproject.toml` — Python 3.11+, networkx the only runtime dependency.
- `docs/design/TARGET_PROFILE.md`, `docs/design/OPEN_QUESTIONS.md`.

No `src/cascade_map/` code exists yet. That is correct: builders write it, card by card.

## Blockers — owner input needed

Every builder's prompt says to read `docs/design/WORKPLAN.md` first. It does not exist
yet, and cannot be written responsibly until `docs/design/TARGET_PROFILE.md` is filled
in. See `docs/design/OPEN_QUESTIONS.md` for the specific questions — Q1 (the decision
sink) is the one that matters most.

Nothing else is blocked on anything.

## Next action

1. Owner fills in `docs/design/TARGET_PROFILE.md`, or writes `auto-detect` per field.
2. Lead writes `ARCHITECTURE.md`, `WORKPLAN.md`, `FIXTURES.md` and the contracts in
   `src/cascade_map/contracts/`, then puts them to the owner for approval.
3. `/build-card 8` and `/build-card 1` — the first bracket, which run in parallel.

## Lessons carried forward

- The guard denies any command whose head is not on its read-only allowlist when the
  command names a protected path. A builder needing a new read-only tool adds it to
  `READ_ONLY` in the hook and says so in its report — it never routes around the guard.
- The guard blocked its own author writing documentation, because heredoc bodies were
  being parsed as shell. Fixed, and covered by tests. Expect more false positives of
  that shape; each one is a one-line allowlist fix plus a regression test, never a
  reason to disable the hook.
