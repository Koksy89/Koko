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


class ScenarioStageError(Exception):
    """The target's own code failed to run to completion, tagged with which
    stage of reaching it failed.

    ``stage`` is ``"import"`` (``spec.module`` itself, or something it
    imports, never loaded -- possibly a wrong ``target_root`` or a typo'd
    module name, not necessarily a defect in the target) or ``"call"``
    (the module loaded, but the declared entry point does not exist on it,
    or raised once called). ``original`` is the exception actually raised,
    kept whole -- type, message and ``__traceback__`` are all still directly
    inspectable on it.

    Distinguishing the two matters to whoever reads the result: "your
    module does not exist" and "your `main()` raised" are different
    problems. ``_execute`` catches this and turns it into
    ``RunRecord.scenario_failure`` via ``Harness._build_scenario_failure``
    -- the run still completes (this is not a refusal; every control was
    active), but is no longer indistinguishable from a clean one.
    """

    def __init__(self, stage: str, original: BaseException) -> None:
        super().__init__(str(original))
        self.stage = stage
        self.original = original
