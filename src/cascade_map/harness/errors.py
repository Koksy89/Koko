"""Exceptions internal to the harness.

``BlockedOperation`` is raised from inside the audit hook to abort the
operation the target attempted -- the actual isolation mechanism (constraint
7). ``HarnessRefusal`` is raised before anything is executed, when a guarantee
this card owns cannot be made. There is no flag anywhere that turns either of
these into a warning: catching one always means "this did not happen."
"""

from __future__ import annotations


class BlockedOperation(Exception):
    """Raised inside the audit hook to abort a denied operation.

    By the time this is raised the denial is already recorded as a
    ``BlockedAttempt`` on the active ``SandboxContext``, so a caller that
    catches and discards it loses nothing from the run record.
    """


class HarnessRefusal(Exception):
    """The run cannot start. Carries the exact reason.

    Constraint 7: a refusal is a correct outcome, never a warning to proceed
    past. There is no force flag and nothing here should ever be suppressed
    to let a run continue -- ``Harness.start`` catches this internally and
    turns it into a refused ``RunRecord``; it is exposed for callers that want
    to fail loudly instead.
    """
