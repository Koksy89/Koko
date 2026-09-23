"""Run configuration: everything the owner must declare before a Mode A run.

Every collection here defaults to empty. That is not a convenience default;
it is the mechanism. An unconfigured harness controls everything away by
omission -- default-deny is a property of these defaults, not of a caller
remembering to lock something down.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Callable


@dataclass(frozen=True)
class ScenarioSpec:
    """One Mode A scenario: what to run and how to reach it.

    *module* is imported with *target_root* on ``sys.path`` (mirrors how card
    1 addresses elements: a bare, importable module name rooted at the
    target). When *function* is set, it is called with *args* after import;
    left empty, the import alone is the scenario (mirrors ``python -m``,
    where the module's own top-level code and any ``if __name__ ==
    "__main__"`` block do the work).
    """

    name: str
    module: str
    function: str = ""
    args: tuple[str, ...] = ()
    argv: tuple[str, ...] = ()
    """`sys.argv` for the scenario, when the target is driven by command-line
    flags rather than by a callable -- which most real launchers are.

    Set, it replaces `sys.argv` for the duration of the scenario and is
    restored afterwards, so a target using `argparse` sees exactly what it
    would see from a shell. Empty leaves `sys.argv` alone.

    This exists because the first real target could not be driven at all
    without it: its runner takes `--engine`, `--sports`, `--frames` and
    `--workers`, and calling a function with string arguments cannot express
    that. The alternative was asking the owner to write a shim module inside
    their own tree, which is a change to the code under analysis -- the one
    thing this tool must never require."""


@dataclass(frozen=True)
class RunConfig:
    """Everything a ``Harness`` needs to know before it will consider a run.

    ``target_root`` -- where the scenario's code lives (``target_engine/`` in
    production; a fixture directory in this card's own tests).

    ``mode_b_out_dir`` -- the completed Mode B graph's output directory. Its
    presence (an ``elements.jsonl``) is the evidence a graph exists at all;
    its content hashes are what ``graph_hash`` is checked against. Also where
    ``runtime/<run_id>/run.json`` is written.

    ``sandbox_root`` -- every write the scenario makes lands here or is
    blocked. Not the same directory as ``mode_b_out_dir``: the sandbox is
    scoped to the *target's* writes, never to the harness's own artifacts.

    ``scenarios`` -- named entry points. An undeclared scenario name is a
    refusal, not a guess at which module to run.

    ``declared_process_names`` -- executable basenames (or the literal token
    ``"fork"``) the run config explicitly allows to spawn. Empty by default:
    every subprocess, exec and fork is blocked until named here.

    ``client_stubs`` -- external systems named in ``TARGET_PROFILE.md``,
    keyed by the module name the target imports them as. Each factory
    returns a stub or replay module installed into ``sys.modules`` before the
    scenario runs, so the target never reaches the real socket layer for a
    declared client at all. A client the target reaches for that is *not*
    here goes through the real ``socket``/``open`` calls the stub would have
    intercepted, and those are blocked like anything else undeclared -- an
    undeclared client is a hard stop by construction, not a special case.

    ``env_passthrough`` -- environment variable names visible to the
    scenario. Empty by default: nothing, including secrets, passes through
    unless named here.
    """

    target_root: Path
    mode_b_out_dir: Path
    sandbox_root: Path
    scenarios: dict[str, ScenarioSpec] = field(default_factory=dict)
    declared_process_names: frozenset[str] = frozenset()
    client_stubs: dict[str, Callable[[], ModuleType]] = field(default_factory=dict)
    env_passthrough: frozenset[str] = frozenset()
