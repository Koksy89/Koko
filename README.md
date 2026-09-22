# CASCADE-MAP

A tool that maps a Python decision engine: every element, the order it executes in, what
it does, whether it does what it is meant to do, and which elements drive the final
decision.

Two modes, one graph:

- **Mode B (static)** parses the target. It never executes, imports, execs, evals or
  unpickles it.
- **Mode A (runtime)** additionally runs the target inside a safety harness to trace it
  and capture values — only on explicit command, always on top of a completed Mode B
  graph. Runtime evidence is an overlay on the same stable IDs, never a second graph.

The engine being analysed never enters this repository. `target_engine/` and
`target_versions/` are gitignored, and a `PreToolUse` hook blocks any attempt to execute
or modify them.

## Status

**Both modes are complete and gated.** 1162 tests, 0 failing.

`cascade-map analyze` reads your engine and never executes it. `cascade-map trace` runs
it inside the safety harness and records what it did. See `docs/STATUS.md`.

## The whole tool in one file

If you do not want to install anything, take **[`dist/cascade_map.py`](dist/cascade_map.py)**.
One file, no package, no dependencies beyond the standard library; Python 3.11 or newer.

    python3 cascade_map.py analyze /path/to/metatron_engine --out out/first
    python3 cascade_map.py view out/first
    python3 cascade_map.py trace out/first --scenarios scenarios.json --scenario baseline

It is generated from the package by `tools/amalgamate.py`, not written separately, so
there is no second implementation to drift. `tests/test_amalgamate.py` runs both shapes
over the fixture corpus and requires their `analyze`, `view` and `trace` output to match
byte for byte — that test exists because two earlier attempts produced a single file that
ran without complaint and answered differently.

Regenerate it with:

    python3.12 tools/amalgamate.py --out dist/cascade_map.py

The builder needs 3.12+ (before 3.12 Python's tokenizer cannot see the names inside
f-strings, which the rename pass has to); the file it writes runs on 3.11+.

## Setup

Requires Python 3.11 or newer (develop on 3.12).

    git clone https://github.com/Koksy89/Koko.git
    cd Koko
    python3 -m venv .venv
    source .venv/bin/activate          # Windows: .venv\Scripts\activate
    pip install -e ".[dev]"
    pytest

1162 tests should pass, 0 fail.

## Putting your engine in place

Neither directory is tracked by git, so nothing here is uploaded anywhere.

    mkdir -p target_engine target_versions

Copy your engine into `target_engine/` — a **copy**, never the live deployment. Put any
older or newer versions you want diffed under `target_versions/<label>/`.

For Mode A only, create a separate interpreter with the engine's own dependencies
installed, named `.venv-target`. Mode B does not need it. Nothing may run under that
interpreter except the harness.

Then fill in `docs/design/TARGET_PROFILE.md`.

## Running it on your engine

    cascade-map analyze target_engine/ --out out/first

That is the whole thing. It parses your code with `ast` and writes the map. **Nothing in
your engine is imported, executed or evaluated** — a hook blocks it, and the sentinel
fixture proves it on every run.

It works with no configuration. Where an owner input is blank it detects candidates and
prints them as **proposals**, never as facts:

    Detected, NOT confirmed — these are proposals for you:
      decision_sink  engine.rules::final_decision  (HEURISTIC)

The one answer worth giving it up front is your decision sink — the function that returns,
or variable that holds, your engine's final decision. Four of the analyses grade relevance
by "can this reach the decision", so it changes the whole map:

    cascade-map analyze target_engine/ \
        --entry "run_m5::main" \
        --sink  "engine.rules::final_decision" \
        --config "config/wiring.json" \
        --out out/first

Then look at it:

    cascade-map view out/first

One offline HTML page. No network, no CDN.

### Reading the output

The summary ends with a confidence census. It is the most important thing on the screen:

      CERTAIN          18    4%
      RESOLVED        268   62%
      PROBABLE        111   26%
      HEURISTIC        18    4%
      UNKNOWN          11    2%

A map that is mostly `RESOLVED` is a different object from one that is mostly `HEURISTIC`,
and you should not have to open a file to find out which you have. `unresolved` and
`barriers` counts are honest gaps — places the tool says "I could not tell" rather than
guessing. A high `unresolved` count usually means dynamic wiring it could not follow;
pointing `--config` at the files that name components by string is what fixes it.

### If the gate fails

    GATE FAILED: N elements have an incomplete documentation record.

The run exits non-zero and still writes the artifacts so you can see what is missing. That
is deliberate: a partial map reporting success is worse than one that refuses, because you
would act on it either way. `--no-gate` writes anyway and exits 0 — use it to look, not to
trust.

## The engine guard

`.claude/hooks/guard_engine.py` runs before every Bash, Write and Edit call and denies:

- executing anything inside the target, including via `cd`, `sh -c`, `find -exec`,
  `env`, `timeout` and `nohup`
- any use of the `.venv-target` interpreter outside the harness
- any write to, or in-place edit of, the target

Reading is untouched — `grep -rn`, `sed -n`, `cat` and friends work normally, because
that is how every static card does its job. The one execution path left open is
`cascade-map trace`, the Mode A harness command.

It is a tripwire, not a sandbox. It inspects commands as text, and shell syntax can
always defeat static inspection. Real isolation is card 11's harness, which controls the
process, its filesystem and its network. What the guard reliably stops is the thing that
actually happens: a reflexive `python target_engine/run_m5.py` typed without thinking.

If it blocks something legitimate, add the tool to `READ_ONLY` in the hook and add a
regression test. Do not disable it.

To verify it is live:

    pytest tests/test_guard_engine.py -q

## Watching it run — Mode A

**Read `docs/design/ARCHITECTURE.md` under "What layer 3 does not cover" first, and run
the first one inside a container.** The harness is a very good tripwire. It is not a wall,
and it says so in every run record.

Declare what to run, in a JSON file:

    {
      "target_root": "target_engine",
      "scenarios": {
        "baseline": {"module": "run_m5", "function": "main"}
      },
      "declared_process_names": [],
      "env_passthrough": []
    }

Then:

    cascade-map trace out/first --scenarios scenarios.json --scenario baseline --out out/run1
    cascade-map view out/run1

Mode A always builds on a completed static graph, so `analyze` must have run first. The
harness verifies the graph still matches your engine's contents and **refuses** if it does
not — a stale map would key runtime events onto elements that have moved.

### What a run tells you

    events         9
    mapped         9/9 target events mapped (100.0%), 145 external frames skipped
    contradictions 2   <- observation vs static claim
    blocked        3   <- side effects the harness stopped

**Contradictions are the point.** They are places where what your engine actually did
disagrees with what the static map predicted — an edge that never fires, a call nobody
predicted, an order that differs. The static map is never edited to match; the
disagreement is the finding.

**Unmapped events** are where the static analysis was wrong. Python's own import
machinery is counted separately as "external" so it cannot drown that signal.

**Blocked** lists side effects the harness stopped — outbound connections, writes outside
the sandbox, process spawns. Those are findings about your engine, not noise.

Every run also prints **what it could not guarantee**. Read that first.

### If your scenario does not run

    YOUR SCENARIO DID NOT RUN — the module could not be imported.
      ModuleNotFoundError: No module named 'run_m5'

Three distinct messages, because they send you to three different places: a wrong
`target_root` or an engine that cannot import itself; a function name that does not exist,
which is a typo in your scenario file; or your own code raising, with its traceback. A run
whose scenario never executed never reads as a run whose analysis was wrong.

## Working on it

The build runs as a lead session plus specialist subagents, one per WORKPLAN card.

    /resume            # where the build is, and what is next
    /build-card 8      # build one card: delegate, verify, record, commit
    /phase-gate        # close a phase; nothing proceeds until it passes

`CLAUDE.md` holds the project memory and is loaded into every session and subagent.
