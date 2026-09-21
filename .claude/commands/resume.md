---
description: Pick up the build in a fresh session — read STATUS and say what happens next.
---

You are the LEAD resuming this build in a clean context. Establish the state, then stop
and report. Do not start building in this command.

## 1. Read, in this order

1. `docs/STATUS.md` — the single source of truth for where the build is.
2. `docs/design/OPEN_QUESTIONS.md` — anything blocked on the owner.
3. `docs/design/TARGET_PROFILE.md` — which owner inputs are still blank.
4. `git log --oneline -15` — what actually landed, versus what STATUS claims.

## 2. Reconcile

If `docs/STATUS.md` and the git history disagree, trust the repository and say so — a
stale STATUS is itself a defect worth reporting. Run the test suite to confirm the tree
is where STATUS says it is.

## 3. Report, in under 200 words

- which cards are DONE, and the phase the build is in
- the next card in the build order, and whether its dependencies are met
- anything blocked on an owner decision, stated as a specific question
- known gaps carried forward from earlier cards
- the exact command to continue, e.g. `/build-card 2`

Keep your own context lean. Read builder summaries and STATUS, not whole modules.
