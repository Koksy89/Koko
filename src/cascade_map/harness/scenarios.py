"""Card 11: scenarios derived from settings, and sports as first-class runs.

The first real target is a library, not a program. ``AmunEV_Engine_V2.py`` is
~60 modules in one 14.8 MB file that never calls itself; the thing that
*runs* is a launcher taking ``--engine`` and ``--sports`` on the command
line. Nothing in a scenario file of module-plus-function shape can express
that, and asking the owner to add a shim module to their own tree would be a
change to the code under analysis -- the one thing this tool must never
require.

So a scenario can carry ``argv`` (see :class:`~cascade_map.harness.config.ScenarioSpec`),
and this module builds those scenarios from settings so the owner can name a
sport on the command line and never open a Python file:

    metatron_engine.py trace out/amun --sport basketball

Everything here is **data**. Nothing in this module execs, evals, imports or
otherwise runs a line of the target: it checks that files exist, turns a
relative path into an importable module name, and builds a dict. The one
thing it will not do is guess: a runner it cannot resolve is a refusal
naming the path it tried, never a fallback to some other module.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any, Sequence

__all__ = [
    "ScenarioDerivationError",
    "SPORT_SCENARIO_KIND",
    "derive_scenario_document",
    "derive_scenarios",
    "harness_warnings",
    "runner_module_name",
    "select_sports",
]


class ScenarioDerivationError(RuntimeError):
    """A scenario could not be derived from settings.

    Always carries the reason and the path that was tried. Raised rather than
    returning a best guess: a Mode A run pointed at the wrong module executes
    owner code that nobody asked for, which is exactly what constraint 7
    exists to prevent.
    """


#: What a derived scenario is, for anything that has to explain itself to the
#: owner. Not a marker the harness reads -- the harness sees an ordinary
#: ``ScenarioSpec`` and cannot tell a derived one from a hand-written one.
SPORT_SCENARIO_KIND = "sport"


# ---------------------------------------------------------------------------
# Sport selection
# ---------------------------------------------------------------------------


def select_sports(
    known: Sequence[str],
    sport: str = "",
    requested: Sequence[str] = (),
    all_sports: bool = False,
) -> tuple[str, ...]:
    """Which sports this run covers, in the order *known* declares them.

    Precedence: ``--all-sports`` beats ``--sport``, which beats the ``SPORT``
    setting, which beats "every sport in ``SPORTS``". An unknown name raises
    :class:`ValueError` **listing the valid ones** -- a typo that silently
    ran a different sport, or no sport at all, is how an owner ends up
    reading a map of something they did not run.
    """
    valid = tuple(known)
    if all_sports:
        if requested:
            raise ValueError(
                "--all-sports and --sport cannot both be given: one means every "
                "sport, the other means these sports. Pick one."
            )
        return valid
    chosen: tuple[str, ...]
    if requested:
        chosen = tuple(requested)
    elif sport:
        chosen = (sport,)
    else:
        return valid
    for name in chosen:
        if name not in valid:
            raise ValueError(
                f"unknown sport {name!r}. Valid sports: "
                f"{', '.join(valid) if valid else '(SPORTS is empty)'}."
            )
    # Deduplicated, in SPORTS order, so `--sport a --sport a` and the order
    # the flags happened to be typed in cannot change a run id or an output.
    return tuple(name for name in valid if name in set(chosen))


# ---------------------------------------------------------------------------
# Resolving the runner
# ---------------------------------------------------------------------------


def runner_module_name(target_root: Path, runner: str) -> str:
    """``bin/go_live.py`` -> ``bin.go_live``, checked against the target tree.

    Resolved the way card 1 addresses elements: a bare importable name rooted
    at the target, which is exactly what ``Harness._run_scenario`` imports
    with *target_root* on ``sys.path``. A directory without ``__init__.py``
    still imports as a namespace package, so no ``__init__.py`` is required
    of the owner's tree.

    Raises :class:`ScenarioDerivationError`, naming the path tried, when the
    runner is absent, is not a ``.py`` file, or is not spellable as a module
    name. Never falls back to guessing a module.
    """
    if not runner:
        raise ScenarioDerivationError(
            "RUNNER is empty, so there is nothing to run. Set RUNNER to the "
            "launcher's path relative to the version root (for example "
            '"bin/go_live.py"), or pass a scenarios file.'
        )
    relative = PurePosixPath(runner.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ScenarioDerivationError(
            f"RUNNER {runner!r} must be a path relative to the version root and "
            f"must not climb out of it with '..'. Tried: {runner!r}."
        )
    candidate = target_root / Path(*relative.parts)
    if not candidate.is_file():
        raise ScenarioDerivationError(
            f"cannot resolve RUNNER {runner!r}: no such file. Tried: {candidate}. "
            f"Refusing rather than guessing which module to run."
        )
    if relative.suffix != ".py":
        raise ScenarioDerivationError(
            f"cannot resolve RUNNER {runner!r} to a module: it is not a .py file. "
            f"Tried: {candidate}."
        )
    parts = relative.with_suffix("").parts
    if not parts or not all(part.isidentifier() for part in parts):
        raise ScenarioDerivationError(
            f"cannot resolve RUNNER {runner!r} to an importable module name: "
            f"{'.'.join(parts)!r} is not a legal dotted name. Tried: {candidate}."
        )
    return ".".join(parts)


def _check_engine(target_root: Path, engine: str) -> Path:
    if not engine:
        raise ScenarioDerivationError(
            "ENGINE is empty. Set ENGINE to the engine file's path relative to "
            "the version root, or pass a scenarios file."
        )
    relative = PurePosixPath(engine.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ScenarioDerivationError(
            f"ENGINE {engine!r} must be a path relative to the version root and "
            f"must not climb out of it with '..'. Tried: {engine!r}."
        )
    candidate = target_root / Path(*relative.parts)
    if not candidate.is_file():
        raise ScenarioDerivationError(
            f"the engine file {engine!r} is not in this version. Tried: "
            f"{candidate}. The runner is passed --engine {engine}, so it would "
            f"fail on its own preflight; refusing before executing anything."
        )
    return candidate


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


def derive_scenarios(
    target_root: Path,
    *,
    sports: Sequence[str],
    runner: str,
    engine: str,
    run_args: Sequence[str] = (),
) -> dict[str, dict[str, Any]]:
    """One scenario per sport, named for the sport.

    ``module`` is the runner's module name, ``function`` is empty (the module
    is executed for its side effects, the way ``python bin/go_live.py``
    executes it), and ``argv`` is what a shell would have handed it.
    """
    module = runner_module_name(target_root, runner)
    _check_engine(target_root, engine)
    if not sports:
        raise ScenarioDerivationError(
            "no sport was selected and SPORTS is empty, so there is no scenario "
            "to derive. Name one with --sport, or set SPORTS."
        )
    scenarios: dict[str, dict[str, Any]] = {}
    for sport in sports:
        scenarios[sport] = {
            "module": module,
            "function": "",
            "args": [],
            "argv": [runner, "--engine", engine, "--sports", sport, *run_args],
        }
    return scenarios


def derive_scenario_document(
    target_root: Path,
    *,
    sports: Sequence[str],
    runner: str,
    engine: str,
    run_args: Sequence[str] = (),
) -> dict[str, Any]:
    """The same document a scenarios file holds, built from settings.

    ``declared_process_names`` and ``env_passthrough`` are **empty**: default
    deny is a property of these defaults, not of a caller remembering to lock
    something down. An owner who needs a child process or a secret declares
    it in a scenarios file, which is an explicit, reviewable act.
    """
    return {
        "target_root": str(target_root),
        "scenarios": derive_scenarios(
            target_root,
            sports=sports,
            runner=runner,
            engine=engine,
            run_args=run_args,
        ),
        "declared_process_names": [],
        "env_passthrough": [],
        "derived_from": "METATRON_SETTINGS",
    }


# ---------------------------------------------------------------------------
# What the owner is told before they trust the run
# ---------------------------------------------------------------------------


def harness_warnings(
    sandbox_dir: str,
    declared_process_names: Sequence[str] = (),
    run_args: Sequence[str] = (),
) -> tuple[str, ...]:
    """The three things that bite on a real launcher, printed with the run.

    Not documentation. A run whose engine could not install a package, could
    not find the workbook it just wrote, and quietly started eight children
    nothing was watching is a run an owner will misread, and they read
    output, not manuals.
    """
    worker_flags = [arg for arg in run_args if "worker" in arg or "proc" in arg]
    lines = [
        "NETWORK IS BLOCKED, including DNS. If this engine's launcher installs "
        "packages or fetches data, those calls fail inside the harness. Every "
        "blocked attempt is recorded in run.json under `blocked` -- read them "
        "as findings, not as noise. There is no allowlist and no --force.",
        f"EVERY FILE WRITE IS REDIRECTED into the sandbox at {sandbox_dir}. "
        f"Workbooks/, production/, Logs/ and anything else this engine "
        f"normally writes will NOT land in their usual places. Look for them "
        f"under the sandbox; nothing was written outside it.",
    ]
    process = (
        "PROCESS SPAWNING IS BLOCKED unless the run config declares the "
        "executable by name. An undeclared spawn is blocked and recorded."
    )
    if declared_process_names:
        process += (
            " This run DECLARED "
            + ", ".join(sorted(declared_process_names))
            + ": a declared child process runs with none of this harness's "
            "controls attached. It is UNSUPERVISED and UNRECORDED -- whatever "
            "it does to the network, the filesystem or further processes "
            "happens unblocked and does not appear in this run's `blocked` "
            "list."
        )
    if worker_flags:
        process += (
            " These run arguments ask for child processes: "
            + ", ".join(worker_flags)
            + ". Any child they start is either blocked (undeclared) or "
            "unsupervised and unrecorded (declared). Neither is observed."
        )
    lines.append(process)
    return tuple(lines)
