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

**Mode B (static) is complete and gated. Mode A (runtime) is not built.**

`cascade-map analyze` reads your engine and produces a map. It never executes it.
`cascade-map trace` refuses, because the runtime half does not exist yet.

See `docs/STATUS.md` to resume.

## Setup

Requires Python 3.11 or newer (develop on 3.12).

    git clone https://github.com/Koksy89/Koko.git
    cd Koko
    python3 -m venv .venv
    source .venv/bin/activate          # Windows: .venv\Scripts\activate
    pip install -e ".[dev]"
    pytest

81 tests should pass. They all cover the engine guard; there is nothing else to test yet.

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

## Working on it

The build runs as a lead session plus specialist subagents, one per WORKPLAN card.

    /resume            # where the build is, and what is next
    /build-card 8      # build one card: delegate, verify, record, commit
    /phase-gate        # close a phase; nothing proceeds until it passes

`CLAUDE.md` holds the project memory and is loaded into every session and subagent.
