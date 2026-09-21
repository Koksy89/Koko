# TARGET_PROFILE

Owner inputs. Fill in what you know; write `auto-detect` for anything you would rather
CASCADE-MAP work out and report back. Anything left as `{...}` is treated as unanswered
and blocks the cards that depend on it.

This file is owned by the LEAD. Builders read it; they never edit it.

## Location

| Field | Value |
|---|---|
| Engine name | **metatron_engine** |
| Target engine (read-only copy, never the live deployment) | `target_engine/` |
| Older/newer versions for diff testing | `target_versions/<label>/` |
| Target Python version | `{e.g. 3.12}` |
| Mode A interpreter, engine dependencies installed | `.venv-target` |

## Entry and exit

| Field | Value |
|---|---|
| Entry point(s) | `{e.g. run_m5.py:main}` |
| Final decision sink(s) — the function or variable holding the final decision | `{...}` |

The decision sink is the most important answer on this page. Cards 3, 4, 5 and 6 all
measure relevance by "can this element reach the decision", so a wrong sink quietly
mislabels the entire map.

## Wiring

| Field | Value |
|---|---|
| Config/data files that wire components (paths inside the engine) | `{...}` |
| Known dynamic-wiring patterns (getattr, importlib, registries, decorators) | `{...}` |

## Mode A

| Field | Value |
|---|---|
| Scenarios — recorded inputs, fixture datasets, configs to run | `{...}` |
| External systems to stub or replay — brokers, APIs, DBs, queues, file outputs | `{...}` |
| Values to redact from captured traces | `{...}` |

Every external system must be listed. An undeclared client that the target reaches for
at runtime is a hard stop, not a pass-through — the harness refuses the run.

## Optional

| Field | Value |
|---|---|
| Intent spec — owner-confirmed intents YAML | `{path, or "none"}` |
