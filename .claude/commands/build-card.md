---
description: Build one WORKPLAN card end to end — delegate, verify, record, commit.
argument-hint: <card number> [phase A|B]
---

Build card **$ARGUMENTS** as the LEAD. You do not write module code; the builder does.

## 1. Check dependencies

Read `docs/STATUS.md`. Confirm every card this one depends on is `DONE`, per the build order:

- Mode B: `[8, 1] → [2] → [3, 4] → [5, 6] → [16, 15 phase B] → card 10 Mode B`
- Mode A: `[11, 12] → [13, 14] → [15 phase A] → card 10 Mode A`

If a dependency is not DONE, stop and say which one. Do not build ahead of the order.

## 2. Delegate

Pick the subagent from the role table in `CLAUDE.md` and launch it with:

- the card number, and the phase if the card has one
- the lessons from `docs/STATUS.md` that this builder needs
- any contract changes made since the card was last touched

Do **not** paste whole files into the prompt. Builders read the repo themselves.

Card 10 is yours — build it directly, following `docs/prompts/10a_integration_modeB.md` or
`docs/prompts/10b_integration_modeA.md`.

## 3. Verify

When the builder reports, launch `verifier` on the card. Never accept a builder's own
account of its work as verification.

On **FAIL**: return the defect list to the *same* builder and re-verify. At most two
rounds. If it still fails, stop and put the defects to the owner with your recommendation.

## 4. Record

On **PASS**, update `docs/STATUS.md`: status, test counts, precision/recall where the card
reports them, known gaps, and the lessons the next builder needs. Then commit:

    git commit -m "card N: <summary>"

## 5. Hand off

Report to the owner in under 200 words: what was built, test results, gaps, and any
contract change the builder requested. Then suggest `/clear` followed by `/resume` to
start the next card with a clean context.
