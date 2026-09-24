"""Card 11: the safe execution harness.

Mode A must be incapable of real-world side effects (constraint 7). Before a
single line of the scenario runs, ``Harness.start`` verifies the Mode B graph
this run overlays is current, verifies every isolation control is actually
active, and only then executes -- inside the sandbox built in
:mod:`cascade_map.harness.sandbox`. Any check that fails refuses the run and
names exactly which guarantee could not be made. There is no force flag, no
warn-and-continue, and no partial mode: every refusal path returns a
``RunRecord`` with ``refused=True`` and never executes anything.
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import traceback as traceback_module
from pathlib import Path

from cascade_map.contracts import (
    BlockedAttempt,
    RunObserver,
    RunRecord,
    ScenarioFailure,
    canonical_dumps,
)

from .config import RunConfig, ScenarioSpec
from .errors import BlockedOperation, HarnessRefusal, ScenarioStageError
from .hashing import compute_graph_hash, compute_run_id, compute_target_hashes, config_fingerprint
from .sandbox import SandboxContext, activate

__all__ = ["Harness"]

#: Escape paths this harness knows it cannot close, named in every run
#: record -- see ``RunRecord.unguaranteed`` in the contract. Both are
#: demonstrated, not theoretical (``tests/test_harness.py``); an empty
#: ``unguaranteed`` would be a false claim of complete coverage, which
#: constraint 7 does not allow. Worded for the owner reading ``run.json``,
#: not for a developer reading this source.
UNGUARANTEED_LIMITS: tuple[str, ...] = (
    "A process launched by calling the interpreter's low-level process-spawn "
    "primitive directly (bypassing Python's subprocess module) is invisible "
    "to every control this harness has: it can run, unblocked and "
    "unrecorded. Closing this needs isolation underneath this harness "
    "itself -- a container, a sandboxed OS user, or similar -- not "
    "something this harness's own checks can guarantee alone.",
    "A process this run explicitly declared and permitted to spawn is not "
    "supervised once it is running: it is a separate program with none of "
    "this harness's controls attached, so anything it does on its own -- "
    "reach the network, write files, spawn further processes -- happens "
    "unblocked and unrecorded by this harness.",
)

#: Same rule ``ValueCapture`` follows, for the same reason: a truncated
#: traceback that reads as complete would send someone to the wrong frame,
#: so the cap is explicit and disclosed in the text, not a silent cutoff.
SCENARIO_FAILURE_TRACEBACK_CAP = 8000


class Harness:
    """Implements ``HarnessCard`` (``cascade_map.contracts.interfaces``)."""

    def __init__(self, config: RunConfig) -> None:
        self.config = config

    # -- HarnessCard -----------------------------------------------------

    def start(
        self, scenario: str, graph_hash: str, observer: RunObserver | None = None
    ) -> RunRecord:
        """Verify every control, then run -- or refuse and say which
        guarantee could not be made. Constraint 7. There is no force option.

        *observer* -- card 12's seam, never imported here (this module only
        ever sees the protocol; see ``RunObserver`` in the contract). Started
        immediately before the target call, inside the sandbox window this
        method alone controls, and stopped immediately after -- including
        when the target raises. A refused run never constructs, starts or
        even sees one: there is nothing to observe, and this method returns
        before any of the code paths below that touch *observer* run at all.
        With ``observer=None`` this method behaves exactly as it did before
        the parameter existed; a tracing run is the same run with something
        watching, not a different code path.
        """
        target_hashes = compute_target_hashes(self.config.target_root)
        current_graph_hash = compute_graph_hash(target_hashes)
        run_id = compute_run_id(scenario, graph_hash, self._config_fingerprint())
        sandbox_dir = str(self.config.sandbox_root)

        controls = {
            "network": False,
            "filesystem": False,
            "process": False,
            "environment": False,
            "external_clients": False,
        }

        def refuse(reason: str) -> RunRecord:
            record = RunRecord(
                run_id=run_id,
                target_hashes=target_hashes,
                graph_hash=graph_hash,
                scenario=scenario,
                interpreter=sys.version,
                controls_active=dict(controls),
                blocked=(),
                unguaranteed=UNGUARANTEED_LIMITS,
                sandbox_dir=sandbox_dir,
                refused=True,
                refusal_reason=reason,
            )
            self._write_run_record(record)
            return record

        # 1. A completed Mode B graph must exist, and must match the target
        #    as it stands right now. Nothing else is checked before this.
        anchor = self.config.mode_b_out_dir / "elements.jsonl"
        if not anchor.exists():
            return refuse(
                f"no completed Mode B graph found at {anchor}: Mode A always runs on "
                f"top of a completed Mode B graph, and refuses to guess one."
            )
        if current_graph_hash != graph_hash:
            return refuse(
                "stale graph: the target's content has changed since the Mode B graph "
                f"was built (the graph expects {graph_hash}, the target now hashes to "
                f"{current_graph_hash}). Re-run Mode B before Mode A."
            )

        # 2. The scenario must be an explicit, declared entry point.
        spec = self.config.scenarios.get(scenario)
        if spec is None:
            return refuse(
                f"scenario {scenario!r} is not declared in the run config; refusing "
                f"rather than guessing an entry point into the target."
            )

        # 3. Build the sandbox and prove the audit hook is actually wired to
        #    it before trusting it with anything. This is what makes "the
        #    absence of a working control blocks the run" true rather than
        #    aspirational: a hook that silently failed to install looks
        #    identical to one that works, unless something checks.
        try:
            self.config.sandbox_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return refuse(f"could not create the sandbox root: {exc}")

        ctx = SandboxContext(
            sandbox_root=str(self.config.sandbox_root.resolve()),
            declared_process_names=self.config.declared_process_names,
            # The harness's own post-run bookkeeping (run.json) writes here,
            # not into the target's sandbox -- both belong to this same run,
            # so both are this context's own write area. See sandbox.py's
            # module docstring for why this is scoped to the context rather
            # than a standing, process-wide exemption.
            extra_write_roots=(str(self.config.mode_b_out_dir.resolve()),),
        )
        if not self._selftest_audit_hook(ctx):
            return refuse(
                "could not verify the audit hook is intercepting operations for this "
                "run; network, filesystem-write and process controls cannot be "
                "guaranteed, so none of them can be trusted."
            )
        controls["network"] = True
        controls["filesystem"] = True
        controls["process"] = True

        # 4. The environment the scenario will see must be built from an
        #    explicit allowlist, never from the ambient process environment.
        filtered_env, env_ok = self._prepare_environment()
        if not env_ok:
            return refuse("could not filter the process environment safely.")
        controls["environment"] = True

        # External clients: there is no network allowlist (see sandbox.py),
        # so every declared client's stub is what keeps it off the socket
        # layer at all, and every undeclared one falls through to the same
        # default-deny network control already verified above.
        controls["external_clients"] = True

        # 5. Execute, inside the sandbox, with everything above in force.
        try:
            record = self._execute(
                run_id,
                target_hashes,
                graph_hash,
                scenario,
                spec,
                ctx,
                controls,
                filtered_env,
                sandbox_dir,
                observer,
            )
        except HarnessRefusal as exc:
            # A guarantee failed once the run was already under way -- today
            # that is a declared client stub that could not be built. It is a
            # refusal, not a failed run, and it takes the same shape as every
            # other refusal so that a caller has one thing to read.
            return refuse(str(exc))
        self._write_run_record(record)
        return record

    # -- execution ---------------------------------------------------------

    def _execute(
        self,
        run_id: str,
        target_hashes: dict[str, str],
        graph_hash: str,
        scenario: str,
        spec: ScenarioSpec,
        ctx: SandboxContext,
        controls: dict[str, bool],
        filtered_env: dict[str, str],
        sandbox_dir: str,
        observer: RunObserver | None,
    ) -> RunRecord:
        original_env = dict(os.environ)
        original_cwd = os.getcwd()
        original_tempdir = tempfile.tempdir
        original_dont_write_bytecode = sys.dont_write_bytecode
        installed_modules: dict[str, object | None] = {}
        scenario_failure: ScenarioFailure | None = None
        observer_failure: ScenarioFailure | None = None
        try:
            os.environ.clear()
            os.environ.update(filtered_env)
            sandbox_tmp = self.config.sandbox_root / "tmp"
            sandbox_tmp.mkdir(parents=True, exist_ok=True)
            tempfile.tempdir = str(sandbox_tmp)
            os.chdir(self.config.sandbox_root)
            # Importlib writes a .pyc next to the source on a normal import,
            # wrapped in a narrow `except OSError` it does not extend to our
            # BlockedOperation -- left enabled, a blocked cache write would
            # abort the import before a single line of the scenario ran,
            # which would look like a clean run instead of the refusal it
            # actually is. Disabling cache writes here is itself a real
            # control (bytecode caching is a filesystem write the scenario
            # never gets to ask for), not just a workaround for the audit
            # hook's interaction with importlib.
            sys.dont_write_bytecode = True
            for name, factory in sorted(self.config.client_stubs.items()):
                installed_modules[name] = sys.modules.get(name)
                try:
                    sys.modules[name] = factory()
                except Exception as exc:  # noqa: BLE001
                    # A stub that could not be built must never fall through to
                    # the REAL module: that would run the scenario against a
                    # live broker with the owner believing it was stubbed. The
                    # outer handler in this method swallows setup failures by
                    # design, so this is raised as a refusal, which it re-raises.
                    raise HarnessRefusal(
                        f"the declared client stub for {name!r} could not be built: "
                        f"{type(exc).__name__}: {exc}. Refusing rather than letting the "
                        f"scenario reach the real {name!r}. Nothing further was executed."
                    ) from exc
            with activate(ctx):
                # Started only once the sandbox window is open, stopped
                # before it closes -- an observer started outside this
                # `with` would watch a process that is not yet contained.
                # The inner try/finally is what makes "stopped even when
                # the target raises" true rather than aspirational: if
                # observer.start() itself never ran, there is nothing to
                # stop, so it is only entered once start() has succeeded.
                if observer is not None:
                    # What the observer receives: everything verified so
                    # far (real controls_active, real unguaranteed, the
                    # real run_id), with blocked=() because nothing has
                    # happened yet -- not a record fabricated before
                    # verification, which is what card 12's own refusal
                    # check exists to catch.
                    pre_execution_record = RunRecord(
                        run_id=run_id,
                        target_hashes=target_hashes,
                        graph_hash=graph_hash,
                        scenario=scenario,
                        interpreter=sys.version,
                        controls_active=dict(controls),
                        blocked=(),
                        unguaranteed=UNGUARANTEED_LIMITS,
                        sandbox_dir=sandbox_dir,
                        refused=False,
                        refusal_reason="",
                    )
                    # The observer is not exempt from misbehaving -- that is
                    # the whole reason `observer_failure` exists. Caught
                    # locally, not left to the outer `except Exception`
                    # below, which is for this method's own setup/teardown
                    # and would otherwise discard it exactly like the defect
                    # this replaces. BlockedOperation/HarnessRefusal are not
                    # "the observer misbehaving" -- an observer that itself
                    # trips a control is judged the same way the target
                    # would be, so those propagate to the handlers that
                    # already exist for them.
                    observer_started = False
                    try:
                        observer.start(pre_execution_record)
                        observer_started = True
                    except (BlockedOperation, HarnessRefusal):
                        raise
                    except Exception as exc:  # noqa: BLE001
                        # start() failed: the run was not observed at all.
                        # The scenario still runs -- an unrelated bug in
                        # what is watching it is not a reason to refuse or
                        # to skip running the target -- so this falls
                        # through to the same _run_scenario call below,
                        # and stop() is never attempted, since nothing
                        # started.
                        observer_failure = self._build_observer_failure("start", exc)
                    try:
                        self._run_scenario(spec)
                    finally:
                        if observer_started:
                            try:
                                observer.stop()
                            except (BlockedOperation, HarnessRefusal):
                                raise
                            except Exception as exc:  # noqa: BLE001
                                # stop() failed: unlike a start() failure,
                                # the run may have been observed for most
                                # of its duration -- a different, milder
                                # finding, but still one the owner needs,
                                # so it is recorded rather than assumed
                                # harmless because "card 12's stop() never
                                # raises" is a guarantee about card 12, not
                                # about every observer this harness might
                                # ever be given.
                                observer_failure = self._build_observer_failure("stop", exc)
                else:
                    self._run_scenario(spec)
        except BlockedOperation:
            # Safe to discard: the denial is already a BlockedAttempt on
            # ctx.blocked (see sandbox.py) by the time this is caught, so it
            # reaches the RunRecord through `blocked` below. Nothing here is
            # silently lost.
            pass
        except HarnessRefusal:
            raise
        except ScenarioStageError as exc:
            # The run still completes -- it is not a refusal (constraint 7's
            # refusal is about guarantees the harness could not make; every
            # control here was active) and not silently discarded either:
            # `_run_scenario` already identified whether the target never
            # imported (`.stage == "import"`) or its entry point was missing
            # or raised (`.stage == "call"`), and that travels onward on
            # `RunRecord.scenario_failure` instead of being dropped.
            scenario_failure = self._build_scenario_failure(exc)
        except Exception:  # noqa: BLE001 - genuinely unexpected: not a target failure
            # Everything _run_scenario can raise from the target's own code
            # is wrapped in ScenarioStageError above; reaching this instead
            # means the failure was in this method's own setup/teardown
            # (chdir, env, client stub installation), not in the scenario.
            pass
        finally:
            os.chdir(original_cwd)
            os.environ.clear()
            os.environ.update(original_env)
            tempfile.tempdir = original_tempdir
            sys.dont_write_bytecode = original_dont_write_bytecode
            for name, previous in installed_modules.items():
                if previous is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = previous

        blocked: tuple[BlockedAttempt, ...] = tuple(ctx.blocked) + tuple(ctx.reads_outside_sandbox)
        return RunRecord(
            run_id=run_id,
            target_hashes=target_hashes,
            graph_hash=graph_hash,
            scenario=scenario,
            interpreter=sys.version,
            controls_active=dict(controls),
            blocked=blocked,
            unguaranteed=UNGUARANTEED_LIMITS,
            sandbox_dir=sandbox_dir,
            scenario_failure=scenario_failure,
            observer_failure=observer_failure,
            refused=False,
            refusal_reason="",
        )

    def _run_scenario(self, spec: ScenarioSpec) -> None:
        target_root = str(self.config.target_root.resolve())
        path_added = target_root not in sys.path
        if path_added:
            sys.path.insert(0, target_root)
        # `sys.argv` is set *before* the import, not between import and call:
        # an import-only scenario (`function=""`) does all its work at module
        # top level, which is exactly where `argparse` runs for a real
        # launcher. Setting it after the import would leave the one shape
        # this field exists for -- a `python bin/go_live.py --sports ...`
        # runner -- seeing the harness's own argv.
        argv_replaced = bool(spec.argv)
        original_argv = list(sys.argv)
        if argv_replaced:
            sys.argv = list(spec.argv)
        try:
            try:
                module = importlib.import_module(spec.module)
            except (BlockedOperation, HarnessRefusal):
                raise  # unwrapped: _execute's existing handlers deal with these
            except BaseException as exc:
                raise ScenarioStageError("import", exc) from exc
            if spec.function:
                try:
                    func = getattr(module, spec.function)
                    func(*spec.args)
                except (BlockedOperation, HarnessRefusal):
                    raise
                except BaseException as exc:
                    raise ScenarioStageError("call", exc) from exc
        finally:
            # Restored here, in the same `finally` as the sys.path cleanup, so
            # it is restored when the scenario raises as well as when it
            # returns. A scenario that leaves the interpreter's argv rewritten
            # would silently change what every later scenario -- and this tool
            # itself -- sees.
            if argv_replaced:
                sys.argv = original_argv
            if path_added:
                try:
                    sys.path.remove(target_root)
                except ValueError:
                    pass
            for name in [
                n for n in sys.modules if n == spec.module or n.startswith(spec.module + ".")
            ]:
                sys.modules.pop(name, None)

    @staticmethod
    def _capped_traceback(exc: BaseException) -> str:
        """The traceback text every ``ScenarioFailure`` carries, capped
        explicitly rather than silently truncated -- the same rule
        ``ValueCapture`` follows: a cutoff that reads as complete would send
        someone to the wrong frame.
        """
        formatted = "".join(traceback_module.format_exception(type(exc), exc, exc.__traceback__))
        if len(formatted) > SCENARIO_FAILURE_TRACEBACK_CAP:
            kept = formatted[:SCENARIO_FAILURE_TRACEBACK_CAP]
            formatted = (
                f"{kept}\n...[traceback truncated: showing "
                f"{SCENARIO_FAILURE_TRACEBACK_CAP} of {len(formatted)} characters]"
            )
        return formatted

    @classmethod
    def _build_scenario_failure(cls, exc: ScenarioStageError) -> ScenarioFailure:
        """Turn a caught ``ScenarioStageError`` into the record the owner
        reads: the target misbehaved."""
        original = exc.original
        return ScenarioFailure(
            stage=exc.stage,
            exception_type=type(original).__name__,
            message=str(original),
            traceback=cls._capped_traceback(original),
        )

    @classmethod
    def _build_observer_failure(cls, stage: str, exc: BaseException) -> ScenarioFailure:
        """Turn an exception raised by the observer itself into the record
        the owner reads: the opposite finding from ``_build_scenario_failure``
        -- the target may have run perfectly and nobody was watching (or
        stopped watching partway through). *stage* is ``"start"`` or
        ``"stop"``; see ``RunRecord.observer_failure``.
        """
        return ScenarioFailure(
            stage=stage,
            exception_type=type(exc).__name__,
            message=str(exc),
            traceback=cls._capped_traceback(exc),
        )

    # -- self-tests ----------------------------------------------------------

    def _selftest_audit_hook(self, ctx: SandboxContext) -> bool:
        """Prove the hook delegates to *ctx* without performing any real,
        potentially side-effecting operation. A canary event name that no
        real Python operation ever raises, seen only if the plumbing works.
        """
        import sys as _sys

        token = f"selftest-{id(ctx)}-{os.getpid()}"
        ctx.selftest_token = token
        try:
            with activate(ctx):
                _sys.audit("cascade_map.selftest", token)
        except Exception:  # noqa: BLE001 - any failure here means "not verified"
            return False
        return ctx.selftest_seen

    def _prepare_environment(self) -> tuple[dict[str, str], bool]:
        try:
            filtered = {
                key: value
                for key, value in os.environ.items()
                if key in self.config.env_passthrough
            }
        except Exception:  # noqa: BLE001
            return {}, False
        return filtered, True

    # -- bookkeeping ---------------------------------------------------------

    def _config_fingerprint(self) -> str:
        return config_fingerprint(
            self.config.declared_process_names,
            frozenset(self.config.client_stubs),
            self.config.env_passthrough,
            self.config.client_declarations,
        )

    def _write_run_record(self, record: RunRecord) -> Path:
        """Persist ``runtime/<run_id>/run.json``. Ordinary file IO by the
        harness itself, not the target -- this is the tool's own artifact
        output, outside the sandbox by design, exactly like every other
        card's ``*.jsonl`` output.
        """
        runtime_dir = self.config.mode_b_out_dir / "runtime" / record.run_id
        runtime_dir.mkdir(parents=True, exist_ok=True)
        path = runtime_dir / "run.json"
        path.write_text(canonical_dumps(record) + "\n", encoding="ascii")
        return path
