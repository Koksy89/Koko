# CASCADE-MAP — project memory

This repository builds CASCADE-MAP, a tool that maps the Python engine in `target_engine/` statically (Mode B) and, on explicit command, by observing it run inside a safety harness (Mode A). This file is loaded into the main session and every subagent.

## Project context

<project_context>
TARGET CODEBASE
- ~116,000 lines of Python, ~14 MB, incl. ~3.9 MB of embedded strategy books and compressed blobs inside .py files
- A launcher (e.g. run_m5.py) starts a cascade: ingestion → data engineering → feature engineering → decision logic (rule cascades and/or tree models) → final decision
- Wiring is explicit (imports, lists, decorators) and/or dynamic (names in JSON/config, getattr, importlib)

OWNER INPUTS (owner fills these in; if blank, auto-detect read-only and report what you detected)
- Target engine location: target_engine/ (read-only copy, never the live deployment)
- Older/newer versions for diff testing: target_versions/<label>/
- Entry points: {e.g. run_m5.py:main}
- Final decision sink(s): {function/variable holding the final decision}
- Config/data files that wire components: {paths inside target_engine/}
- Target Python version: {e.g. 3.12}
- Mode A interpreter (engine dependencies installed): .venv-target
- Mode A scenarios: {recorded inputs / fixture datasets / configs to run}
- External systems to stub or replay in Mode A: {brokers, APIs, DBs, queues, file outputs}
- Intent spec (optional): {path to owner-confirmed intents YAML}

OPERATING MODES
- Mode B (Static, light): parses only. Never executes, imports, execs, evals or unpickles target code.
- Mode A (Runtime, full): everything in Mode B, plus executing the target inside the safe harness to trace it, capture values, explain each step, and verify alignment with intended purpose. Runs only on explicit command; always builds on a completed Mode B graph.
- One graph, two evidence sources: runtime evidence is an overlay keyed by the same stable IDs, never a separate graph.

PURPOSE
A precise, navigable, drill-down map of every element, the order it executes in, what it does, whether it does what it is meant to do, and which elements drive the final decision — so the owner can optimize at a granular level and see exactly what a change affects.

NON-NEGOTIABLE CONSTRAINTS
1. Static analysis never executes target code. Execution happens only in Mode A, only inside the harness, only on explicit command.
2. Every fact, edge and verdict carries provenance: method + confidence. Runtime facts are tagged RUNTIME_OBSERVED with run ID and event ID.
3. Anything unresolved is reported with location and reason. Nothing is silently dropped.
4. Deterministic: identical input → byte-identical static output (stable IDs, sorted keys).
5. Incremental: unchanged files (content hash) are not re-analyzed.
6. Tool runs on Python 3.11+; core deps: stdlib + networkx; other tools are optional adapters.
7. Mode A must be incapable of real-world side effects: outbound network blocked by default, file writes redirected to a sandbox, declared external clients stubbed or replayed. If this cannot be guaranteed for a run, the run refuses to start and states why.
8. Every element has a complete documentation record. Facts come from the AST or the trace, never from a language model. Model-written text is always labelled with its source.

SOURCE OF TRUTH
docs/design/ and src/cascade_map/contracts/ override this block once they exist. If you find a defect in them, stop and report it rather than silently diverging.
</project_context>

## Repository layout
- `.claude/agents/` — builder subagents (one per WORKPLAN card) and `verifier`
- `.claude/commands/` — `/build-card`, `/phase-gate`, `/resume`
- `.claude/hooks/guard_engine.py` — blocks accidental execution of the target engine
- `docs/prompts/` — prompts the owner runs at fixed points
- `docs/runtime_prompts/` — prompts CASCADE-MAP itself sends to models at runtime
- `docs/design/` — TARGET_PROFILE.md, ARCHITECTURE.md, WORKPLAN.md, FIXTURES.md, OPEN_QUESTIONS.md, REVIEW.md, REVIEW_DECISIONS.md
- `src/cascade_map/contracts/` — interfaces.py, schema.json: the binding contracts between agents
- `src/cascade_map/`, `tests/` — the tool and its tests; `tests/fixtures/` — fixture corpus
- `docs/STATUS.md` — build progress; the single place a fresh session resumes from
- `target_engine/`, `target_versions/` — READ-ONLY, gitignored

## Roles
The main session is the LEAD (Opus 5, effort high). The lead plans, delegates, reviews, integrates, owns the design docs and contracts, and talks to the owner. The lead does not write module code; builders do.

| Card | Scope | Subagent | Model | Effort |
|---|---|---|---|---|
| 8 | Fixture corpus (Mode B + Mode A) | fixture-writer | Haiku 4.5 | default |
| 1 | Ingestion & inventory | ingestion-builder | Sonnet 5 | medium |
| 2 | Resolution & call graph | resolver-engineer | Opus 5 | high |
| 3 | CFG, cascade ordering, decisions | cascade-engineer | Opus 5 | high |
| 4 | Data/feature lineage & slicing | lineage-engineer | Opus 5 | high |
| 5 | Unplugged detection & hints | findings-builder | Sonnet 5 | medium |
| 6 | Version diff & impact | diff-impact-builder | Sonnet 5 | high |
| 16 | Documentation records, completeness gate, enrichment client | docs-builder | Sonnet 5 | medium |
| 15 | Viewer (phase B, later phase A) | viewer-builder | Sonnet 5 | high |
| 11 | Safe execution harness | harness-builder | Sonnet 5 | high |
| 12 | Runtime tracer & value capture | tracer-engineer | Opus 5 | high |
| 13 | Intent registry & alignment | alignment-engineer | Opus 5 | high |
| 14 | Execution narrative | narrative-builder | Sonnet 5 | medium |
| 10 | CLI, integration & validation | the lead itself | Opus 5 | high |
| — | Verification of every card and gate | verifier | Sonnet 5 | medium |

## Build order (cards in the same brackets run in parallel)
Mode B: [8, 1] → [2] → [3, 4] → [5, 6] → [16, 15 phase B] → card 10 Mode B (docs/prompts/10a_integration_modeB.md)
Mode A: [11, 12] → [13, 14] → [15 phase A] → card 10 Mode A (docs/prompts/10b_integration_modeA.md)

## Rules for the lead
1. Before delegating a card, confirm its dependencies are DONE in docs/STATUS.md.
2. Delegate by subagent name with: card number, phase (if any), and lessons from STATUS.md the builder needs. Do not paste whole files; builders read them.
3. After a builder reports, delegate verification to `verifier`. On FAIL, return the defects to the same builder (max 2 rounds), then stop and ask the owner.
4. Only the lead edits docs/design/ and src/cascade_map/contracts/, and only after telling the owner what changes and why. Builders request contract changes; they never make them.
5. On PASS: update docs/STATUS.md (status, test counts, gaps, lessons), then `git commit -m "card N: <summary>"`.
6. Keep your own context lean: read builder summaries, not whole modules. At the end of each build step, suggest `/clear` then `/resume`.

## GOD-2 — THE VALIDATION AND VERIFICATION BIBLE (owner's rules, sealed)

Sealed in `AmunEV/AmunEV_Engine_V2.py` as `LAZ_GOD2_RULE`,
sha256 `e5782c68d982f8d999f683d6ae8828b7bf21efea43176566cd49af1819b60005`.
It may be changed **only** by the owner, and only by the explicit reply
**"Unchained approves"**.

**The owner's PRIMARY rules — a strategy is validated when, and only when:**
1. mean odds **>= 1.40**. **THERE IS NO MAXIMUM** — no ceiling, no band top, no rung cap.
2. `n_is >= 100`
3. `n_oos >= 100`
4. out-of-sample **ROI > 0**
5. **no forward looking** — every condition reads this tick and earlier only, and the
   conditions stack in an order reproducible in production.

**SECONDARY rules — placement and settlement. Policy, never conditions, and they never
filter the search:**
6. **first tick only** — one bet per match, at the first tick every condition holds.
7. **market open** — if the market is closed at that tick the bet **waits up to 60
   seconds**; the moment it opens, place it if the price is still >= 1.40, otherwise
   abandon **that bet**. A closed market never invalidates the strategy.
8. **settle** on the outcome the strategy itself names. Never a hard-coded market.

**NEVER ADD A BLOCKER.** No rule, gate, filter, threshold, band, cap, window, mask
clause, quarantine or "safety" check that can stop a strategy being found, validated,
registered, enabled or placed may be added by anyone but the owner — not by a refactor,
a port, a convenience, or an agent acting in good faith. If a change would reduce the
strategies found or the bets placed, **refuse it and ask**. Any such element already in
the engine must be **named to the owner** with what it blocks and what it costs; silence
about a blocker is itself a violation.

`laz_god2__verify()` checks both the seal and that `LAZ_OWNER['rules']` still matches.

## Safety rules (all agents)
- Never execute, import or run anything in `target_engine/`, `target_versions/`, or with `.venv-target`, except through the Mode A harness command (`cascade-map trace`), and only after the owner approves that command. A hook enforces this; never work around it. If it blocks you, stop and explain.
- Never edit `target_engine/` or `target_versions/`.
- Fixtures in `tests/fixtures/` may be executed only by harness and tracer tests.
- No network access except package installs from PyPI.
- CASCADE-MAP's runtime model calls read the key from `CASCADE_MAP_API_KEY`. Never use or set `ANTHROPIC_API_KEY` (it would switch Claude Code's own billing). Runtime model IDs: enrichment `claude-haiku-4-5-20251001`; semantic alignment and escalations `claude-sonnet-5`. All runtime model features are optional and fully disabled when no key is set.
- `target_engine/` and `target_versions/` are gitignored: search them with `grep -rn` or read files by path, because ignore-aware search may skip them.

## Engineering standards
- Develop on Python 3.12 (tool must support 3.11+); type hints everywhere; pytest; no network in tests.
- Core dependencies: stdlib + networkx. Everything else behind an adapter and optional.
- Deterministic outputs, stable IDs, sorted keys. Every emitted fact, edge and verdict carries provenance.

## Definition of done (every card)
- Implements the contracts exactly; needed contract changes are reported, not made.
- All relevant FIXTURES.md cases pass as pytest tests; precision/recall reported where applicable.
- The sentinel fixture proves nothing was executed (static cards).
- Two runs produce identical output.
- Full test suite green, not only the card's tests.
- Final report ≤ 300 words: files changed, tests and results, approximations, known gaps, contract change requests.
