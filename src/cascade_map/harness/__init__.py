"""Card 11: the safe execution harness.

Mode A executes the target only inside this package, only on the owner's
explicit command (``cascade-map trace``), and only on top of a completed
Mode B graph. Default-deny on network (including DNS), filesystem writes and
process spawning; declared external clients stubbed or replayed; an
undeclared client is a hard stop. Any control the run cannot verify makes the
run refuse to start and say exactly which guarantee it could not make -- no
force flag, no warn-and-continue, no partial mode.

See ``docs/design/WORKPLAN.md`` (Card 11) and
``src/cascade_map/contracts/interfaces.py`` (``HarnessCard``, ``RunRecord``,
``BlockedAttempt``) for the binding definition this implements.
"""

from __future__ import annotations

from .config import RunConfig, ScenarioSpec
from .errors import BlockedOperation, HarnessRefusal
from .harness import Harness
from .hashing import compute_graph_hash, compute_run_id, compute_target_hashes
from .sandbox import SandboxContext, activate, within_sandbox
from .scenarios import (
    ScenarioDerivationError,
    derive_scenario_document,
    derive_scenarios,
    harness_warnings,
    runner_module_name,
    select_sports,
)

__all__ = [
    "Harness",
    "RunConfig",
    "ScenarioSpec",
    "BlockedOperation",
    "HarnessRefusal",
    "SandboxContext",
    "activate",
    "within_sandbox",
    "compute_graph_hash",
    "compute_run_id",
    "compute_target_hashes",
    "ScenarioDerivationError",
    "derive_scenario_document",
    "derive_scenarios",
    "harness_warnings",
    "runner_module_name",
    "select_sports",
]
