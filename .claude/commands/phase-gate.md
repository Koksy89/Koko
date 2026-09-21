---
description: Run the gate at the end of a build phase — nothing proceeds until it passes.
argument-hint: [Mode B | Mode A]
---

Run the phase gate for **$ARGUMENTS**. A gate is a hard stop: the next phase does not
begin until every check below passes.

## Checks

1. **Every card in the phase is DONE** in `docs/STATUS.md`, each with a PASS from `verifier`.
2. **Full suite green.** Run the whole test suite, not the union of the cards' own tests.
3. **Determinism.** Produce the phase's output twice into separate directories and diff
   them byte for byte. Any difference fails the gate.
4. **Sentinel.** For Mode B: the sentinel fixture's marker is absent after a full run.
5. **Completeness.** Card 16's gate passes — every element has a complete documentation
   record, and every "unknown" is explicit and carries a reason.
6. **Provenance.** Spot-check emitted artifacts: every fact, edge and verdict carries a
   method and a confidence; Mode A facts carry `RUNTIME_OBSERVED` with run ID and event ID.
7. **Mode A only.** A test proves the harness refuses to start when isolation cannot be
   guaranteed, and the adversarial fixtures are blocked and recorded.
8. **Contracts.** `docs/design/` and `src/cascade_map/contracts/` match what the code
   actually does. Drift between them is a gate failure, not a documentation nit.

## Then

Delegate the whole gate to `verifier` for an independent PASS/FAIL. Do not self-certify.

On PASS: record the gate in `docs/STATUS.md`, commit, and tell the owner the phase is
closed and what the next phase needs from them.

On FAIL: list every defect with `file:line` and a reproduction command, say which card
owns each, and stop. Do not start the next phase.
