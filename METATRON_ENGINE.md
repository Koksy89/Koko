# METATRON ENGINE — full documentation

**What it is:** one Python file that reads a Python codebase and draws you a complete,
drillable map of it — every element, the order things execute in, what feeds what, which
parts actually drive the final decision, which parts are plugged into nothing, and what
changed between two versions.

**What it is not:** it is not a trading engine. It analyses **Amun_Engine** and
**RA_Engine**; it is not either of them, and it never becomes part of them. Nothing it
does modifies the code it reads.

The codebase is named CASCADE-MAP internally (you will see that word in the source, in
error messages and in the repository). Metatron is your name for the shipped file. They
are the same thing.

---

## Table of contents

1. [Requirements and install](#1-requirements-and-install)
2. [Quickstart](#2-quickstart)
3. [The commands](#3-the-commands)
4. [The ideas everything rests on](#4-the-ideas-everything-rests-on)
5. [What `analyze` actually does, stage by stage](#5-what-analyze-actually-does-stage-by-stage)
6. [Every output file, explained](#6-every-output-file-explained)
7. [Reading the numbers it prints](#7-reading-the-numbers-it-prints)
7b. [The blueprint canvas](#7b-the-blueprint-canvas)
8. [Findings — the eight things it looks for](#8-findings--the-eight-things-it-looks-for)
9. [Version control and change impact](#9-version-control-and-change-impact)
9b. [`track` — the version ledger](#9b-track--the-version-ledger)
10. [Mode A — watching it run](#10-mode-a--watching-it-run)
11. [Safety: what it will and will not do](#11-safety-what-it-will-and-will-not-do)
12. [Known gaps — read this before you rely on something](#12-known-gaps--read-this-before-you-rely-on-something)
13. [Troubleshooting](#13-troubleshooting)
14. [How the file itself is built and maintained](#14-how-the-file-itself-is-built-and-maintained)
15. [Glossary](#15-glossary)

---

## 1. Requirements and install

| | |
|---|---|
| Python | 3.11 or newer |
| Dependencies | none — standard library only |
| Install | there isn't one; save the file |
| Network | never used, in any command |
| Writes | only into the `--out` directory you name |

Save `metatron_engine.py` anywhere — Desktop is fine. Check it runs:

```
python3 metatron_engine.py --version
```

Nothing to install, nothing to configure, no virtual environment. That was deliberate:
a tool you have to set up is a tool you stop using.

---

## 2. Quickstart

```
python3 metatron_engine.py analyze C:\code\amun_engine --out out\amun
python3 metatron_engine.py view out\amun
```

The first command reads Amun and writes the map into `out\amun`. It takes seconds to
a minute depending on size. **It does not import, execute, evaluate or unpickle a single
line of your engine** — it reads the text of your files and parses them, the same way
you would read them, just exhaustively.

The second command turns that map into a single self-contained HTML page and tells you
where it wrote it. Open it in any browser. It needs no internet and no server.

Do the same for RA into a different folder. The two are analysed independently and never
mixed — that is by design and is what you asked for.

---

## 3. The commands

### `analyze` — build the map

```
python3 metatron_engine.py analyze ROOT --out DIR [options]
```

| Option | What it does | When you need it |
|---|---|---|
| `ROOT` | the folder holding the engine to read | always |
| `--out DIR` | where to write the map (default `out/latest`) | always, in practice |
| `--entry ID` | declare an entry point by element id; repeatable | when auto-detection guesses wrong |
| `--sink ID` | declare a final-decision element by id; repeatable | **strongly recommended** — see below |
| `--config PATH` | a JSON/config file that wires components by name; repeatable | when your engine names classes/functions in config |
| `--env PATH` | the interpreter whose installed packages to read (`.venv-target`) | **to catch wrong-package-version bugs** — see [section 8b](#8b-package-versions--which-versions-each-element-is-applicable-to) |
| `--cache DIR` | move the incremental cache (default `./.cascade_map/cache`) | to keep it off a network drive, or out of your repo |
| `--workers N` | child processes for ingestion. Omitted or `0` = auto; `1` = in-process | when you want to cap the load, or to debug. **Workers split work across FILES, never inside one**, so a single-large-file target gains nothing -- the run measures it and says so |
| `--no-gate` | write the map even if the completeness check fails, and exit 0 | rarely; the gate exists for a reason |

**About `--sink`.** A "sink" is where your final decision comes out — the function or
variable that holds buy/sell/hold, the position size, the order. Everything the tool says
about *"which code drives the decision"* is measured as *"can this element reach a
sink"*. If you don't declare one, the tool guesses by name and clearly labels the guess
as `HEURISTIC` and prints it under **"Detected, NOT confirmed — these are proposals for
you"**. Declaring the real one with `--sink` turns those proposals into facts and makes
the reachability answers trustworthy. This is the single highest-value five minutes you
can spend on setup.

**Exit codes:** `0` fine · `2` you used it wrong · `3` the completeness gate failed
(artifacts still written) · `4` a Mode A run refused to start.

### `view` — read the map

```
python3 metatron_engine.py view DIR [--html PATH]
```

Renders the analysed directory into one offline HTML page (default `DIR/index.html`).
Everything on that page was produced by `analyze`; the viewer computes nothing of its
own. That separation is deliberate: if a number on screen looks wrong, it is wrong in the
data file, and you can go read the data file.

### `blueprint` — the interactive node canvas

```
python3 metatron_engine.py blueprint GRAPH_DIR [--html PATH] [--diff DIFF_DIR] [--run RUN_ID]
```

The visual one. Default output is `GRAPH_DIR/blueprint.html`. `--diff` takes a
`diff` output directory and lights up the Diff tab; `--run` takes a run id from `trace`
and overlays what actually executed. Both are optional, and when you leave one out the
matching tab says so and names the command that produces it. Full detail in
[section 7b](#7b-the-blueprint-canvas).

### `diff` — compare two versions

```
python3 metatron_engine.py diff BEFORE_DIR AFTER_DIR --out DIR
```

Both arguments are **analysed output directories**, not source folders. See
[section 9](#9-version-control-and-change-impact).

### `track` — the whole history, incrementally

```
python3 metatron_engine.py track [--versions DIR] [--report] [flags]
```

Point it at a folder of version folders and it analyses only the ones it has never
analysed, compares consecutive versions element by element, and keeps one JSON ledger.
Configured by the `METATRON_SETTINGS` dict at the top of this file; every setting has a
flag that overrides it. See [section 9b](#9b-track--the-version-ledger).

### `doctor` -- one file you can send back

```
python3 metatron_engine.py doctor --out DIR
python3 metatron_engine.py doctor --out DIR --target path/to/your/engine
```

Measures this machine on this target and writes
`DIR/metatron_doctor_<UTC timestamp>.log` plus the same data as JSON beside it, then
prints the full path on its own last line so you can copy it straight out of the
terminal. With no `--target` it measures metatron's own source; with `--target` it
measures yours, which is the more useful run to send.

It records per-stage timings, file and element counts, the worker scaling at 1, 4 and 8
workers with a cold cache at every point, the interpreter version, the machine's usable
core count, and the cache location and whether it was warm.

**It is safe to send.** It contains no source code, no element names, no docstrings and
no values from your engine -- only timings, counts, versions, and the root-relative path
of the file that dominated the work, which is the answer to "why did workers not help".
The file says so in its own header. Nothing in the target is executed to produce it.

### `trace` -- watch it actually run (Mode A)

```
python3 metatron_engine.py trace GRAPH_DIR --sport basketball --out DIR
python3 metatron_engine.py trace GRAPH_DIR --scenarios FILE.json --scenario NAME --out DIR
```

This is the only command that executes your code, it only does so inside a containment
harness, and it refuses to start unless a completed `analyze` map already exists. See
[section 10](#10-mode-a--watching-it-run).

| Option | What it does |
|---|---|
| `--sport NAME` | run this sport; repeatable. Overrides `SPORT`. An unknown name is an error that lists the valid ones. |
| `--all-sports` | every sport in `SPORTS`, each as its own scenario with its own run id |
| `--run-arg=ARG` | an extra flag passed through to the runner; repeatable. Use the `=` form so the flag is not read as one of this tool's own. |
| `--preflight` | say whether the run could start, and what would be active, **without executing anything** |
| `--scenarios FILE` | declare scenarios yourself. Given, it wins; omitted, scenarios are derived from `METATRON_SETTINGS`. |

**Sports need no scenarios file and no edit to any Python file.** With `--scenarios`
omitted, one scenario is derived per selected sport, named for the sport, built as
`RUNNER --engine ENGINE --sports <sport> [RUN_ARGS...]` and handed to the runner as its
`sys.argv` — which is how an `argparse`-driven launcher expects to be called. The derived
document is written to `DIR/derived_scenarios.json`, so you can read exactly what ran and
hand-edit it into a scenarios file if you need to declare a stub or a child process.
Different sports are different scenarios and are never compared against each other.

If the runner cannot be resolved to a module inside the version, the run **refuses** and
names the path it tried. It never guesses another module.

Three things will bite on a live launcher, and `trace` prints all three with every run:
the network is blocked (so package installs and data fetches fail), every write is
redirected into the sandbox (so `Workbooks/`, `production/` and `Logs/` do **not** land
where you expect them), and process spawning is blocked unless declared — so `--workers 8`
means eight children that are either blocked or, if declared, unsupervised and unrecorded.

---

## 4. The ideas everything rests on

Five decisions shape everything else. Knowing them makes the output readable.

### 4.1 Every element has a stable, structural ID

An element is anything nameable: a package, module, class, function, method, property,
module-level assignment, parameter, import, data file, config key, embedded blob, or
engineered feature. Each gets an ID built from *where it sits in the structure*, never
from its position in the file:

```
amun.features.momentum::RSIBuilder.build
└─ module ──────────────┘└─ qualified name ─┘
```

**Why this matters to you:** add a blank line at the top of a file and every line number
moves, but no ID changes. So a diff between two versions compares *the same function to
the same function*, not "line 340 to line 340". Version comparison only works because IDs
are structural.

### 4.2 Every single fact carries its provenance

No fact in the output is bare. Each one records **how** it was established (`method`) and
**how much weight it carries** (`confidence`), plus the exact file, line and column.

| Confidence | Means | Example |
|---|---|---|
| `CERTAIN` | read straight off the parsed code, no inference | a `def` exists at this path |
| `RESOLVED` | deterministic, exactly one possible answer | an absolute import naming one module |
| `PROBABLE` | needed an assumption that holds outside pathological cases | `getattr` with a literal name; method dispatch on a known class |
| `HEURISTIC` | a pattern, name or config-string match — plausible, possibly wrong | a config string that happens to equal a function name |
| `UNKNOWN` | not resolved; candidates may be listed but none is claimed | a name computed at runtime |

**Confidence composes by the weakest link.** An execution order built on one `HEURISTIC`
call edge is a `HEURISTIC` ordering no matter how certain the other forty steps were.
That rule exists so a guess can never be laundered into a fact by passing through enough
stages.

The twenty resolution methods you will see in the `method` field:
`AST_DIRECT`, `IMPORT_ABSOLUTE`, `IMPORT_RELATIVE`, `IMPORT_STAR`, `REEXPORT`,
`SCOPE_LOOKUP`, `MRO_DISPATCH`, `DECORATOR_UNWRAP`, `GETATTR_LITERAL`, `GETATTR_TRACED`,
`IMPORTLIB_LITERAL`, `REGISTRY_MEMBERSHIP`, `DECORATOR_REGISTRATION`,
`CONFIG_STRING_MATCH`, `NAME_HEURISTIC`, `DATAFLOW`, `CFG_REACHABILITY`,
`STRUCTURAL_MATCH`, `RUNTIME_OBSERVED`, `MODEL_PROPOSED`.

### 4.3 Nothing is silently dropped

Anything the tool could not work out is written to `unresolved.jsonl` with its location
and the reason (`DYNAMIC_NAME`, `MISSING_TARGET`, `SYNTAX_ERROR`, `DECODE_ERROR`,
`TOO_LARGE`, `AMBIGUOUS`, `ID_COLLISION`, `THIRD_PARTY`, `NOT_EXERCISED`).

**Why this was built in:** a map that quietly omits what it couldn't parse is worse than
no map, because you will trust it. An engine with 3.9 MB of embedded strategy books and
compressed blobs inside `.py` files *will* have things the parser cannot follow. You
need to see that list, not be protected from it.

### 4.4 The same input always produces the same output

Two runs over unchanged code produce byte-identical files: stable IDs, sorted keys, no
timestamps inside the data (they live only in `run_meta.json`), floats rejected in favour
of exact representations.

**Why:** it makes `diff` meaningful. If output could wobble between runs, every
comparison would be full of noise and you would learn to ignore it.

### 4.5 One graph, two sources of evidence

Runtime observations from `trace` are an **overlay** on the static map, keyed by the same
element IDs — never a second, competing map. Where a run disagrees with the static
analysis, the disagreement is recorded as a `Contradiction` and the static graph is left
alone.

**Why:** two maps that disagree leave you adjudicating between your own tools. One map
with a disagreement recorded on it tells you exactly where the static analysis was wrong,
which is the useful information.

---

## 5. What `analyze` actually does, stage by stage

Seven stages, each feeding the next. This is the order they run in.

### Stage 1 — Inventory
Walks the tree, content-hashes every file, parses each Python file with the standard
library's `ast` module, and mints the stable ID for every element. Also picks up data
files (JSON/YAML/CSV/config) and the config keys inside them, and flags embedded blobs.

*Why:* everything downstream addresses code by ID, so the IDs have to exist first and be
minted exactly once, in one place.

### Stage 2 — Resolution and call graph
Turns names into edges: imports, attribute chains, method dispatch through the class
hierarchy, decorators — and then the hard part, **dynamic wiring**: `getattr` with a
literal name, `importlib` with a literal name, registry dictionaries, decorator-based
registration, and names that appear as strings in your config files (that's what
`--config` is for).

Eight edge kinds: `CALLS`, `IMPORTS`, `INHERITS`, `DECORATES`, `REGISTERS`,
`INSTANTIATES`, `REFERENCES`, `CONFIGURES`.

*Why:* a launcher that reads component names out of JSON is invisible to every ordinary
call-graph tool. Following that wiring — and labelling it honestly as `CONFIG_STRING_MATCH`
at `HEURISTIC` confidence rather than pretending it's a real call — is most of the value
here.

### Stage 3 — Control flow, order, decisions, reachability
Builds a control-flow graph per function (block kinds: `ENTRY`, `NORMAL`, `BRANCH`,
`LOOP_HEAD`, `HANDLER`, `FINALLY`, `RETURN`, `RAISE`, `EXIT`), derives the **cascade
order** from the entry point forward (`SEQUENCE`, `BRANCH`, `MERGE`, `LOOP`, `UNORDERED`,
`CYCLE`), identifies every decision point with its condition text and outcomes, and marks
each element `REACHES_SINK`, `NO_SINK_PATH` or `UNKNOWN`.

*Why:* "the order it executes in" and "which elements drive the final decision" are the
two questions the whole tool exists to answer. `UNKNOWN` is a first-class answer here —
an element behind an unresolved dynamic call is *not* declared unreachable, because that
would be a false accusation.

### Stage 4 — Data and feature lineage
Tracks values from ingestion through data engineering into decision inputs. Eight lineage
kinds: `ASSIGNS`, `PARAMETER_BINDING`, `RETURNS`, `CONTAINER_WRITE`, `COLUMN_WRITE`,
`ATTRIBUTE_WRITE`, `READS`, `MUTATES`. Answers backward slices ("what produces this
feature?") and forward slices ("what does changing this affect?"). Where flow stops being
traceable it emits a **barrier** saying where and why.

*Why:* in a trading engine the question is rarely "who calls this function" — it's
"which raw column ends up inside this signal, and what breaks if I change it". Barriers
are there so the slice tells you where its own edge is instead of silently stopping.

### Stage 5 — Findings
Eight kinds of problem, each with location, evidence, confidence and a hint. See
[section 8](#8-findings--the-eight-things-it-looks-for).

### Stage 6 — Documentation records and the completeness gate
Every element gets a record: what it is, where it sits in the cascade, what it reads and
writes, its data role, its change history. **If any element lacks a complete record, the
run fails the gate and exits 3** — the artifacts are still written so you can see exactly
what is missing.

*Why:* a map with holes in it that reports success is the failure mode that matters. Every
fact in a record comes from the parsed code or the trace, never from a language model;
optional model-written prose is stored in a separate, clearly labelled field.

### Stage 7 — Write and verify
Writes every artifact, records a SHA-256 of each in `manifest.json`, and prints the
summary.

---

## 6. Every output file, explained

Everything is **JSON Lines** (one JSON object per line, keys sorted) except the two
`.json` files. That format was chosen so you can `grep` it, feed it to `jq`, load it in
pandas, or diff it with git.

| File | One line is | Why you'd open it |
|---|---|---|
| `elements.jsonl` | one element: id, kind, name, span, docstring, signature, decorators, hashes | the inventory of everything that exists |
| `edges.jsonl` | one relationship: source, target, kind, call site, method, confidence | the wiring — who calls, imports, registers, configures whom |
| `order.jsonl` | one node of the execution order tree | the cascade, from entry point onward |
| `reachability.jsonl` | one element's verdict + the path ids that prove it | **which code can reach a final decision** |
| `decisions.jsonl` | one decision point: condition source text, outcomes, what it reads | every branch that steers the cascade |
| `cfg_blocks.jsonl` / `cfg_edges.jsonl` | control-flow blocks and the jumps between them | the fine detail behind order and decisions |
| `lineage.jsonl` | one value-flow edge | how data moves |
| `slices.jsonl` | one backward/forward slice with its members, edges and barriers | "what produces X" / "what does changing X affect" |
| `barriers.jsonl` | one place lineage stops, with the reason | the honest edge of the lineage answer |
| `findings.jsonl` | one problem, with evidence and a hint | **start here** |
| `unresolved.jsonl` | one thing it couldn't work out, with location and reason | the honest edge of the whole map |
| `candidates.jsonl` | one auto-detected entry point or sink, with its evidence | things to confirm with `--entry` / `--sink` |
| `records.jsonl` | one complete documentation record per element | the drill-down detail |
| `manifest.json` | SHA-256 of every artifact | proof the set is intact and unedited |
| `run_meta.json` | timestamps, Python version, target root, tool version | the only file with wall-clock time in it |

`diff` writes two more:

| File | One line is |
|---|---|
| `changes.jsonl` | one classified change between the two versions |
| `impacts.jsonl` | that change's blast radius, ranked |

`trace` writes into `DIR/runtime/<run_id>/`:

| File | One line is |
|---|---|
| `events.jsonl` | one observed event (`CALL`, `RETURN`, `BRANCH`, `EXCEPTION`, `FEATURE_WRITE`, `DECISION`, `UNMAPPED`) with captured values |
| `narrative.jsonl` | one plain-English step of what the run did, tied to element and event ids |
| `contradictions.jsonl` | one place the run disagreed with the static map |
| `nondeterminism.jsonl` | one way this target will not repeat itself |
| `mapping.json` | how many observed events landed on known elements |
| `run.json` | the run record: what was blocked, which controls were active, what could not be guaranteed |

---

## 7. Reading the numbers it prints

Illustrative — the shape of the summary, with numbers from a small run:

```
Wrote out\amun

  elements           426
  call edges         108
  lineage edges      260
  decisions           16
  findings             3
  unresolved          52   <- reported, never dropped
  barriers            12   <- value flow stops being traceable

Confidence of the edges the map is built from:
  CERTAIN          18    4%
  RESOLVED        268   62%
  PROBABLE        111   26%
  HEURISTIC        18    4%
  UNKNOWN          11    2%

Detected, NOT confirmed — these are proposals for you:
  decision_sink  amun.execute::final_decision  (HEURISTIC)
  Set them in docs/design/TARGET_PROFILE.md to make them facts.
```

**The confidence census is the most important thing on this screen.** It tells you how
much of the map is fact and how much is inference. A high `RESOLVED` share means the
engine is wired explicitly and the map is reliable. A large `HEURISTIC` or `UNKNOWN`
share means a lot of your wiring is dynamic and the map is a good hypothesis rather than
a description — go read `unresolved.jsonl` and consider passing `--config`.

**`unresolved` is not an error count.** 52 unresolved items in a 116,000-line engine
with dynamic wiring is normal and healthy; it means 52 things were reported rather than
guessed at.

**The "Detected, NOT confirmed" block is a to-do list.** Re-run with
`--sink amun.execute::final_decision` (and `--entry` if needed) and those become facts.
The message mentions `docs/design/TARGET_PROFILE.md`, which belongs to the development
repository — with the single file, use the command-line flags instead.

---

## 7b. The blueprint canvas

`view` gives you the report: tables, records, every fact in text. `blueprint` gives you
the picture. One offline HTML file, no internet, no install, opened straight from disk.

### Three tabs, one canvas

They share the camera and the selection, so you can look at a function's call flow, press
`2`, and see the same function's data lineage without hunting for it again.

| Tab | Key | Shows |
|---|---|---|
| **Execution** | `1` | stage cards in cascade order, wired stage to stage |
| **Lineage** | `2` | values flowing from raw data into decision inputs, with barriers as visible dead ends |
| **Diff** | `3` | the same graph repainted with what changed between two versions |

### Stages, tasks, detail

The Execution tab opens as **stage cards**: a rounded card per stage, holding its steps as
a numbered list in execution order, with arrows between stages. Click a step to select it;
click **Expand** on a stage to explode it into the per-element graph with every wire;
**Show full graph** does that for everything at once.

The stages come from the execution order the analyser derived — they are not a grouping
invented for the picture. A stage is called `Stage 3`, never `Feature Engineering`: naming
your phases is your job, and a tool that guesses at it is making a claim it cannot support.
Where an element sits in no derived order, its card says so and falls back to its module.

### Flow vs. cascade order — the part worth learning

A cascade is meant to run forward. Everywhere it doesn't is worth knowing about, so the
canvas is built to show exactly that. Every connection is classified against the real
execution order:

| Class | Meaning |
|---|---|
| `FORWARD` | into a later stage — the cascade doing what it should |
| `WITHIN` | inside one stage |
| `BACKWARD` | **into an earlier stage — the cascade doubling back** |
| `UNORDERED` | one end sits in no derived order, so no direction is claimed |

Backward connections are drawn as distinct return wires routed clear of the forward lanes,
and the **Flow vs. cascade order** panel keeps a live tally with a per-stage-pair breakdown
— `Stage 4 → Stage 2: 7 calls back`. Click a count to frame those wires. There is a
one-click **only backward edges** filter.

That panel is your alignment tool. Straighten the cascade, re-run, watch the backward
count fall. `UNORDERED` is a real fourth answer, not a rounding of the other three: where
the order genuinely could not be derived, the tool says so rather than picking a direction.

### What the picture encodes

Nothing here is decoration. **Wire style carries confidence** — solid and bright for what
was read straight off your code, dashed for an assumption, dotted amber for a name or
config-string match that might be wrong, dotted red for unresolved. **Node style carries
reachability** — accented when it reaches a decision, a `no path` badge when it provably
cannot, a `?` badge when the tool could not tell. Every encoding is paired with a shape,
dash pattern or badge as well as a colour, so it survives colour-blindness and greyscale
printing, and the legend states all of it in words in whichever palette is active.

### Palettes

Picker in the top-right; your choice is remembered.

| Palette | Feel |
|---|---|
| **Blueprint Dark** | the default: deep canvas, glowing wires |
| **AI Blue** | deep navy and cyan |
| **Silver & Milk** | milk-white surface, silver and graphite structure |
| **High Contrast** | maximum legibility, and for printing |

A palette changes hues, never meanings: the confidence dash patterns and relative weights
are identical in all four, verified by test. Worst measured contrast ratio per palette:
Blueprint Dark 3.31, AI Blue 3.61, Silver & Milk 4.37, High Contrast 4.92; body text is
above 5.2:1 everywhere.

### Getting around

| Action | How |
|---|---|
| pan | drag the canvas |
| zoom | scroll, or `+` / `-` |
| fit everything | `F` |
| search | `/`, then Enter to fly to it |
| clear / deselect | `Esc` |
| switch tabs | `1` `2` `3` |
| detail | click a node or a step |
| wire detail | click a wire |

Filters cover element kind, edge kind, a confidence floor, "only reaches a decision",
"only findings", and "only backward edges". Filter state is always visible and **Clear
filters** is always one click — you should never be looking at a filtered graph believing
it is the whole graph.

### Scale

Whole modules collapse to a single card, and above 150 nodes they collapse by default, so
a 116,000-line engine stays navigable. The page opens framed and readable rather than
zoomed out to a smear — there is a test asserting the opening zoom level, because an
earlier build shipped one that opened at 8.8%.

## 8. Findings — the eight things it looks for

| Finding | What it means | Why it was built in |
|---|---|---|
| `UNREACHABLE_ELEMENT` | nothing reaches this code | dead code in a decision engine is either a bug or a maintenance cost; either way you want to know |
| `UNCONSUMED_FEATURE` | you compute this feature and nothing reads it | wasted compute, and often a signal that was silently disconnected in a refactor |
| `DANGLING_CONFIG_REFERENCE` | a config file names something that doesn't exist | the classic silent failure: the config looks right, the component never loads |
| `ORPHANED_CONFIG_ELEMENT` | a config key nothing in code reads | stale knobs that look live |
| `DEAD_BRANCH` | a branch that can never be taken | usually a condition that changed meaning |
| `SHADOWED_DEFINITION` | two definitions of the same name, one hiding the other | the one you're editing may not be the one that runs |
| `DUPLICATED_LOGIC` | the same body defined more than once | you will fix the bug in one copy |
| `DECISION_IRRELEVANT` | real, reachable code that cannot influence any decision | the thing you most want before optimising: effort spent on code that cannot move the outcome |

Every finding carries a confidence and a hint. `HEURISTIC` findings are suggestions to
check, not accusations.

---

## 8b. Package versions — which versions each element is applicable to

The failure this exists for: the machine has the wrong version of a package for what a
function is written against, and you spend a day hunting a bug in your own logic that
was never there.

Run `analyze` with `--env` pointed at the interpreter your engine actually runs under:

```
python3 metatron_engine.py analyze C:\code\amun --env C:\code\amun\.venv --out out\amun
```

### Three answers, kept apart

| Answer | Source | Artifact |
|---|---|---|
| what your project **declares** it needs | `requirements*.txt`, `pyproject.toml`, `setup.cfg`, `Pipfile`, `environment.yml`, PEP 723 headers — recorded with the file and **line** | `requirements.jsonl` |
| what is actually **installed** | `*.dist-info/METADATA` and `*.egg-info/PKG-INFO`, read **as text** | `installed.jsonl` |
| what the code actually **uses** | the import edges already in your map | `package_usage.jsonl` |

They are never merged, because every version bug is a *disagreement* between two of
them, and a single combined list would hide exactly what you came to find.

### The disagreements it reports

| Finding | What it means |
|---|---|
| `UNDECLARED_DEPENDENCY` | imported, declared nowhere — works on your machine, dies on the next one |
| `MISSING_DEPENDENCY` | imported, not installed — an `ImportError` waiting for the code path that reaches it |
| `VERSION_CONFLICT` | installed version outside the declared range — the classic "it worked yesterday" |
| `UNUSED_DEPENDENCY` | declared and installed, nothing imports it — usually a leftover pinning you for no reason |
| `INTERPRETER_TOO_OLD` | an element's own syntax needs a newer Python than you have |

Each is joined back to your map, so you do not get "pandas is pinned wrong" — you get
which elements use it, which API surface they touch (`pandas.DataFrame.append`, not just
"pandas"), and **which of them are on a path to your final decision**.

### What it will not do, on purpose

- **It will not tell you an API was removed in some version.** That would mean shipping a
  stale copy of every library's release notes and presenting guesses as facts. It shows
  you the surface you touch and the version you have; you judge.
- **It will not guess which package an import belongs to.** `cv2` is `opencv-python` and
  `sklearn` is `scikit-learn` — it learns that from the installed metadata on your disk.
  An import that matches no installed distribution, or more than one, is reported as
  unresolved with the candidates listed.
- **It will not import anything to ask its version.** Reading a package's metadata must
  not run its startup code, and `pip` is never invoked.

### Without `--env`

The installed column reads **`NOT CHECKED: no environment was read`** and names the flag.
It never renders an unchecked environment as a clean one — "nothing here says your
installed versions are right; it says they were not looked at."

### Also free: minimum Python per element

`interpreter.jsonl` records the lowest Python version each element's own **syntax**
requires — a `match` statement is 3.10+, `except*` is 3.11+, a walrus is 3.8+. Read off
the grammar, so `CERTAIN`, not inferred.

## 9. Version control and change impact

This is the workflow you described, and it is what the `diff` command was built for.

### The three-step loop

```
:: 1. map the version you have now
python3 metatron_engine.py analyze C:\code\amun_v1 --out out\amun_v1 --sink amun.execute::final_decision

:: 2. map the new version
python3 metatron_engine.py analyze C:\code\amun_v2 --out out\amun_v2 --sink amun.execute::final_decision

:: 3. compare the two maps
python3 metatron_engine.py diff out\amun_v1 out\amun_v2 --out out\amun_changes
```

Use the **same `--entry` and `--sink` flags on both sides.** Comparing a map built with a
declared sink against one built with a guessed sink produces differences that are about
your flags, not your code.

### What the comparison gives you

It matches elements across the two versions **structurally, with rename detection**, so
moving a function to another file or renaming it is reported as `MOVED` or `RENAMED`
rather than as a deletion plus an unrelated addition. Each change is classified:

`ADDED` · `REMOVED` · `RENAMED` · `MOVED` · `SIGNATURE_CHANGED` · `BODY_CHANGED` ·
`DECORATORS_CHANGED` · `UNCHANGED` · `AMBIGUOUS`

`AMBIGUOUS` is real and deliberate: where two candidate matches are equally good, it says
so and lists both rather than picking one and being confidently wrong.

Then, for each change, the impact:

```
  changes 638
  impacts 638
  14 of them change a path to a decision
```

That last line is the one you care about. Each impact record carries:

- `affected_ids` — what downstream is touched
- `decision_paths_changed` — **did this change alter a route to a final decision?**
- `reachability_flipped` — code that became reachable, or stopped being reachable
- `features_changed` — which engineered features are affected
- `findings_added` / `findings_removed` — problems this change introduced or fixed
- `rank` — ordering, most consequential first

So "what changed and how did it impact the rest of the code" is answered directly:
sort by rank, read the ones where `decision_paths_changed` is true, ignore the rest.

### Recommended discipline

- **Keep the map directories, not just the code.** `out\amun_v1` is a permanent, exact
  record of what that version's structure was. It is deterministic, so it diffs cleanly
  in git and never produces spurious changes.
- **Commit the map alongside the tag.** When you tag `amun-v2.3`, commit
  `out\amun_v2.3\` next to it. Six months later you can answer "what did v2.3 actually
  look like" without checking out and re-analysing.
- **Name output directories after versions, never `latest`.** `out\latest` is a default
  for experiments; for version control it destroys the thing you need.
- **The incremental cache is already on.** It defaults to `.cascade_map/cache` under
  whatever directory you run the command from; `--cache DIR` only moves it. Unchanged
  files are skipped by content hash, so a re-analysis after editing a few files is
  near-instant while the first analysis of an engine is not. The cache is never a source
  of truth — it is keyed on content hash and regenerated on demand — so never commit it,
  and deleting it is always safe.
- **Run it on every meaningful change, not just releases.** The impact answer is most
  useful when the change set is small enough to read.

If you want this to run automatically on every commit, it is a short git hook — say the
word and I'll write it for your setup.

---

## 9b. `track` — the version ledger

Section 9 is the manual loop: analyse, analyse, diff. `track` is the same loop run for
you across a whole folder of versions, and it never repeats work it has already done.

```
versions/
  amun_v2/                <- a whole engine tree
  amun_v3/
  amun_2026-01-14/
  amun_2026-02-03/
```

```
python3 metatron_engine.py track
```

Two of those folders carry a date and two do not, which is exactly the case where the
tool refuses to invent an order (see below). With
`"ORDER": ["amun_v2", "amun_v3", "amun_2026-01-14", "amun_2026-02-03"]` set:

```
Ledger out/metatron_ledger.json — 4 version(s) known, 1 new

  amun_2026-02-03    #3  NEW   analysed in 22.4s   (order declared in ORDER)
  amun_2026-01-14    #2        known, not re-run   (order declared in ORDER)
  amun_v3            #1        known, not re-run   (order declared in ORDER)
  amun_v2            #0        known, not re-run   (order declared in ORDER)

amun_2026-01-14 -> amun_2026-02-03
  1,284 elements before, 1,301 after, 1,304 accounted for, 0 unaccounted
  changed        48   (19 ADDED, 2 REMOVED, 2 RENAMED, 13 MOVED, 12 BODY_CHANGED)
  unchanged   1,254
  ambiguous       2   <- equally good matches, all listed, none claimed
  decision paths  6   <- changes that touch a route to your final decision
  reachability    2 flipped
  findings       +3 / -1
  runtime        not measured (MODE 1; set MODE 2 to observe execution)
```

(An engine-sized illustration; the arithmetic is the real thing's — see
"every single element" below for where 1,304 comes from.)

### `METATRON_SETTINGS`

A plain dict at the top of this file. Edit it in place; no flags needed for the common
case.

```python
METATRON_SETTINGS = {
    "MODE": 1,                              # 1 = static only. 2 = static + Mode A tracing.
    "WORKSPACE": "workspace",               # THE ONE PATH. Everything else is derived from it.
    "PROJECT": "",                          # the folder under it; empty = derived from ENGINE
    "SOURCES": True,                        # keep one copy of each DISTINCT version; False = --no-sources
    "VERSIONS_DIR": "",                     # legacy; set it and it still means the drop folder
    "OUT_DIR": "",                          # legacy; set it and it still means the artifact root
    "LEDGER": "",                           # legacy; set it and a flat copy is written there too
    "SINKS": [],                            # your final-decision element(s) — the highest-value setting here
    "ENTRIES": [],                          # entry point(s); detected when empty
    "CONFIGS": [],                          # config files that wire components, relative to each version root
    "ENV": ".venv-target",                  # interpreter whose installed packages to read, as TEXT
    "ORDER": [],                            # explicit ordering, oldest first; empty = work it out and report how
    "SCENARIOS": "scenarios.json",          # MODE 2 only
    "SCENARIO": "baseline",                 # MODE 2 only
    "WORKERS": 0,                           # 0 = auto (usable cores less one); 1 = in-process
}
```

`WORKSPACE` replaces `VERSIONS_DIR`, `OUT_DIR` and `LEDGER`, and the layout under it is:

```
workspace/
    AmunEV_Engine_V2/                             <- PROJECT, derived from ENGINE
        history/
            AmunEV_Engine_V2_history.json         <- the story. Open this one.
            AmunEV_Engine_V2_fingerprints.jsonl
            AmunEV_Engine_V2_comparisons.jsonl
            runtime/basketball_history.json       <- one per sport
        sources/<content hash>/                   <- stored ONCE per distinct content
        io/runs/<UTC timestamp>/                  <- the map from that run
        io/reports/
```

The three old keys still work. Left empty, `WORKSPACE` resolves them; set to a path,
each keeps doing exactly what it did before and `track` prints one line saying what it
now means, so upgrading mid-project neither errors nor silently empties your history.
A flat `versions/` folder beside the workspace is still read, and said out loud, until
you run `metatron migrate`.

Read it back with `metatron history`, `metatron history <PROJECT>`,
`metatron history <PROJECT> --sport etennis`, or
`metatron history <PROJECT> --element <id>` — one element's whole life across every
version.

A sport's file says whether it is genuinely that sport's map. Where the sport is chosen
at runtime and no static rule separates the branches, it declares
`scope: UNION ACROSS ALL SPORTS` with the reason, and never presents blended data as
sport-specific.

**These are data, never code.** Nothing here is ever `exec`'d, and an unknown key is an
error that names the typo:

```
unknown setting "SINK". Did you mean "SINKS"? Valid settings: CONFIGS, ENTRIES, ...
```

A misspelled `"SINK"` that quietly did nothing is how you end up trusting a map built
without the setting you thought you had applied. Every key also has a flag —
`--versions`, `--out`, `--ledger`, `--mode`, `--sink`, `--entry`, `--config`, `--env`,
`--order`, `--scenarios`, `--scenario` — and the merged result is validated as a whole,
so a typo in the dict is caught even on a fully flag-driven run.

`WORKERS` defaults to `0` = auto, which is what the machine knows rather than what a
number written on a different machine guessed. `1` means in-process, and stays available
always because it is how anything here is debugged.

**Workers parallelise ACROSS FILES, and a file is never split.** One file is one unit of
work; half a function is not parseable and the IDs minted from it would be wrong rather
than merely untidy. So if your target is ONE large file, extra workers cannot help it --
one worker takes the file and the rest idle. Every `analyze` and `track` therefore prints
what the workers actually bought on that run, measured, not asserted:

```
workers        8 requested, 1 started
parallel gain  0.99x vs single process   <- measured on this target (1 file(s), 20.5s of work done in 20.7s)
why            1 file holds 100% of the work; parallelism is across files and cannot split a single file's parse
recommendation use --workers 1 for this target; more workers help when files are many and evenly sized
```

A worker count on its own is not a benefit, so it is never printed as one.

Measured on a 14.6 MB single file and on a 47-file package, same machine, cold cache
every run, output byte-identical at every worker count:

| target | `--workers 1` | `--workers 4` | `--workers 8` |
|---|---|---|---|
| one 14.6 MB file | 20.67s | 21.58s (1 worker started) | 21.90s (1 worker started) |
| 47-file package | 1.74s | 0.83s | 0.97s |

### Identity is the tree's content hash, never the folder name

Every version is identified by a SHA-256 over the path and bytes of every file in it
(`.git`, `__pycache__` and the other caches excluded). Rename the folder and it is the
same version. Change one byte and it is a new one. Two folders with identical content are
one version, analysed once, and the report says `DUPLICATE of ...`.

### Ordering is best-effort and always names its signal

`version_time_source` is recorded on every version and printed on every line. In
descending order of honesty:

| Source | What it read |
|---|---|
| `owner_declared` | your `ORDER` setting |
| `filename` | a date in the folder name — `amun_2026-02-03` |
| `git_commit` | the commit time, read as text from `.git/logs/HEAD` |
| `file_mtime_max` | the newest file mtime in the tree |
| `directory_mtime` | the directory's own mtime |
| `unknown` | nothing |

**Filesystem timestamps lie.** Copying a tree rewrites mtime, some filesystems have no
creation time at all, and extracting an archive stamps every file with the moment you
unpacked it — so three versions unzipped in one sitting look simultaneous. Where the
order cannot be established honestly, every `ordinal` is `-1`, **nothing is compared**,
and it says so and asks for `ORDER`:

```
ORDER NOT ESTABLISHED — every ordinal is -1 and NOTHING WAS COMPARED.
  ['alpha', 'beta'] share a timestamp ('2023-11-14T22:13:20Z'), so their relative
  order is not established by anything on disk.
```

It refuses in three cases: any version with no signal at all; two versions sharing a
timestamp; and a mix of trusted signals (a name, a commit) with filesystem ones, because
comparing those two kinds against each other is a guess. A comparison against the wrong
"previous version" produces a confident, detailed, completely wrong answer, and that is
worse than no answer.

`git` is never invoked inside a version tree. A repository's own config can make git
execute commands (`core.fsmonitor`, pagers, aliases), so the reflog is read as plain
text instead.

### A version already in the ledger is never analysed again

The ledger is an **index, not a copy**: one fingerprint per element per version — id,
kind, content hash, normalised body hash, signature, span, reachability, confidence. That
is everything a comparison needs, so an old version is never re-read. A second `track`
with nothing new does no analysis at all:

```
Ledger out/metatron_ledger.json — 4 version(s) known, 0 new
...
Nothing new. No version was analysed and no tree was read beyond hashing it.
```

Add a fifth version and only the fifth is analysed. The full artifacts stay on disk under
`OUT_DIR/<version id>/` — the ledger points at them, and `track` refuses to compare, by
name, rather than diff against artifacts you have deleted.

### "Every single element" — checkable, not rhetorical

Each comparison carries its own proof:

```
1,284 elements before, 1,301 after, 1,304 accounted for, 0 unaccounted
```

`accounted for` counts element **identities**, so an element present in both versions —
unchanged, or matched through a rename or a move — counts once, not twice. Above,
1,284 + 1,301 with 1,281 matched pairs is 1,304 identities, and all 1,304 are accounted
for.

Every element of either version must land in **exactly one** classification — `ADDED`,
`REMOVED`, `RENAMED`, `MOVED`, `SIGNATURE_CHANGED`, `BODY_CHANGED`,
`DECORATORS_CHANGED`, `UNCHANGED` or `AMBIGUOUS`. Anything that does not is named
individually in `unaccounted_element_ids` as `before:<id>` or `after:<id>`, an element
claimed by *two* classifications counts as a failure just as much as one claimed by none,
and a run that produces any exits non-zero. `AMBIGUOUS` stays a first-class answer: where
two candidates match equally well, both are listed and neither is claimed.

A reformat is not a hundred behaviour changes. Bodies are compared as token streams with
comments and whitespace stripped, and the hash of that is what the ledger stores.

### Analysis time is not engine speed

`stage_millis` and `total_millis` record how long **METATRON** took, per stage, per
version. That is a real signal about your engine's size and shape — a version that
doubles the analysis time has grown or tangled — and it is not a fact about how fast your
engine runs. Nothing that refuses to execute your code can time it.

Engine performance comes only from MODE 2. When both versions have a Mode A run of the
same scenario, `runtime_delta` compares them. When they do not, it is empty, and empty is
always printed as **not measured**, never as "no change":

```
runtime        not measured (MODE 1; set MODE 2 to observe execution)
runtime        not measured (MODE 2, but no completed Mode A run for ['amun_v3'])
runtime        scenario baseline: 18,204 -> 19,001 events, 12 newly executed, ...
```

Even then it counts events, not durations — how much ran, never how fast.

### `track --report`

Prints the whole history rather than only what this run did: every version with its
id, tree hash, time and signal, counts, confidence census and per-stage timings, and
every comparison the ledger holds.

---

## 10. Mode A — watching it run

`analyze` reads your code. `trace` **runs** it, inside a containment harness, and records
what actually happened. It exists because static analysis genuinely cannot answer some
questions — what value was in that variable, which branch actually got taken, what the
real execution order was when the wiring is dynamic.

### It always builds on a completed map

`trace` takes the `analyze` output directory as its first argument and refuses to start
without it. Runtime events are keyed to the element IDs that map already minted, which is
what makes them an overlay rather than a second opinion.

### The scenarios file

Write a small JSON file describing what to run:

```json
{
  "target_root": "C:\\code\\amun_engine",
  "scenarios": {
    "baseline": {
      "module": "run_m5",
      "function": "main",
      "args": []
    }
  },
  "declared_process_names": [],
  "env_passthrough": []
}
```

| Key | Meaning |
|---|---|
| `target_root` | the same folder you analysed; it goes on the import path |
| `scenarios` | named entry points. An undeclared name is a refusal, never a guess |
| `module` | the module to import, addressed the same way element IDs are |
| `function` | called after import. Leave it empty to make the import itself the scenario (like `python -m`) |
| `args` | string arguments passed to that function |
| `argv` | replaces `sys.argv` for the duration of the scenario and is restored afterwards, including when the scenario raises. This is how an `argparse`-driven launcher is driven without adding a shim file to your own tree. Empty leaves `sys.argv` alone |
| `declared_process_names` | executables this run is allowed to spawn. **Empty means none** |
| `env_passthrough` | environment variable names the scenario may see. **Empty means none, including secrets** |

Then:

```
python3 metatron_engine.py trace out\amun --scenarios scenarios.json --scenario baseline --out out\amun_run
```

**Every one of those collections defaults to empty, and that is the mechanism, not a
convenience.** The harness denies by omission: you do not lock it down, you open exactly
what you name.

**You do not need this file for sports.** Omit `--scenarios` and the scenarios are
derived from `METATRON_SETTINGS` — `SPORTS`, `SPORT`, `ENGINE`, `RUNNER`, `RUN_ARGS` —
one per sport. Write the file only when you need something the derivation deliberately
will not give you: a declared child process, an environment variable, or a stub.

### What it guarantees

- **Outbound network blocked**, including DNS.
- **File writes redirected** into a sandbox directory, or blocked.
- **Process spawning blocked** unless you named the executable.
- **Environment hidden** unless you named the variable.
- **Undeclared external clients are a hard stop**, by construction.
- If any control cannot be verified, **the run refuses to start and says which guarantee
  it could not make**. There is no force flag, no warn-and-continue, no partial mode. A
  refusal is a correct outcome, exit code 4.

### What it cannot guarantee — read this

Printed on every single run, and recorded in `run.json`:

1. A process launched by calling the interpreter's low-level process-spawn primitive
   directly — bypassing Python's `subprocess` module — is invisible to every control the
   harness has. It can run, unblocked and unrecorded.
2. A process you explicitly declared and permitted is not supervised once it is running.
   It is a separate program with none of these controls attached.

Neither can be closed from inside the harness; closing them needs isolation *underneath*
it. **Standing recommendation: run Mode A inside a container or a throwaway VM.** These
limits are disclosed rather than hidden because a containment claim you cannot verify is
worse than no claim.

### What you get back

An ordered, readable narrative of the run; every event tied to an element ID and an event
ID; captured values (bounded in size, with sensitive-looking values redacted); a mapping
report saying what fraction of observed events landed on known elements — **unmapped
events are exactly where the static map was wrong**; contradictions where the run
disagreed with the static analysis; and a list of the ways this target will not repeat
itself (hash ordering, clock, randomness).

---

## 11. Safety: what it will and will not do

| | `analyze` / `view` / `diff` | `trace` |
|---|---|---|
| Imports your code | never | yes, inside the harness |
| Executes your code | never | yes, inside the harness |
| `eval` / `exec` / unpickle | never | never |
| Network | never | blocked, including DNS |
| Writes outside `--out` | never | sandbox only |
| Modifies your engine | never | never |

The static commands read your files as **text** and parse them. There is no code path in
them that could execute what they read. `trace` is the one sanctioned execution path and
it announces itself loudly.

Your engine never leaves your machine. This tool makes no network calls of any kind. (The
development repository contains an optional feature that can call a language model to
write *prose descriptions only* — never facts — and it is fully disabled unless an API key
is set. It is not reachable from the shipped file's commands at all.)

---

## 12. Known gaps — read this before you rely on something

Stated plainly, because a doc that only lists strengths is a sales brochure.

1. **One engine at a time.** Amun and RA are analysed independently. If they call into
   each other, that link is not shown. This is what you asked for; say so if it changes.
2. **Intent alignment is not reachable from the command line.** The ability to declare
   "this function is *meant* to do X" and get a verdict (`ALIGNED`, `MISALIGNED`,
   `UNVERIFIABLE`, `NOT_EXERCISED`, `NO_INTENT`) is fully built and tested as a library,
   and the viewer knows how to display it — but no command produces `intents.jsonl` or
   `verdicts.jsonl` yet. It is roughly an hour of work to wire up; ask when you want it.
3. **External client stubbing is not reachable from the scenarios file.** The harness can
   replace a declared external client (broker API, database, queue) with a stub or a
   replay, but that is configured in Python, not JSON. Through the CLI an undeclared
   client is simply blocked — safe, but it means a scenario that needs a live broker will
   stop rather than be faked. Ask if you need it.
4. **Optional model-written prose is not reachable from the CLI.** Facts never came from
   a model anyway; this only affects descriptive text.
5. **The two Mode A sandbox limits in section 10.** Not closable from inside; use a
   container.
6. **`TARGET_PROFILE.md` is mentioned in one message** but belongs to the development
   repository. With the single file, use `--entry` and `--sink`.

---

## 13. Troubleshooting

**"no entry point declared and none detected"** — the tool could not find your launcher.
Pass `--entry <module>::<function>`, e.g. `--entry run_m5::main`. The IDs are listed in
`elements.jsonl`.

**Everything says `NO_SINK_PATH` / reachability looks wrong** — you almost certainly have
no sink declared, or the wrong one. Check `candidates.jsonl`, then pass `--sink`.

**"GATE FAILED: N elements have an incomplete documentation record"** — exit code 3, and
the artifacts were still written so you can inspect them. This is a defect in the tool,
not in your engine; send me the number and I'll chase it. `--no-gate` gets you moving in
the meantime.

**Lots of `UNKNOWN` confidence and a big `unresolved.jsonl`** — your engine wires itself
dynamically. Pass your wiring config files with `--config` (repeatable) so config strings
can be matched to real elements.

**A `trace` run refused (exit 4)** — read the reason it printed. A refusal means a
containment guarantee could not be made. It is the correct outcome; do not look for a way
around it.

**"YOUR SCENARIO DID NOT RUN"** — the tool distinguishes three cases and tells you which:
the module could not be imported (check `target_root`), the named function does not exist
(check `function`), or your engine itself raised (that is your exception, not a tool
failure). This distinction exists because a crashed scenario otherwise produces a report
that reads exactly like a run whose analysis was wrong, and you would hunt the wrong bug.

**How long should a run take?** Measured on a 15 MB target (54,661 elements, documentation
records written in full): **3 minutes 24 seconds**, cold cache. Before the optimisation work
the same run took roughly 50 minutes. Re-running unchanged code is near-instant, because the
content-hash cache is on by default.

If your run is far slower than that, send me a `doctor` log — that is what it is for.

---

## 14. How the file itself is built and maintained

`metatron_engine.py` is ~24,000 lines and is **generated**, not hand-written. The source
lives as 35 modules in a repository; a build tool concatenates them into one file in
dependency order, renaming the handful of names that collide between modules.

That approach was chosen over writing one file by hand because the modules carry fourteen
rounds of independent verification and six integration fixes, and rewriting them by hand
would throw all of that away. Comments and docstrings survive, because the file is
assembled from source text rather than regenerated from the parse tree.

It is verified, not assumed: an automated test builds the single file and requires its
`analyze`, `view` and `trace` output to match the 35-module version **byte for byte** over
the whole fixture corpus. Two differences are allowed and named explicitly — the output
directory path you chose, and a count of frames belonging to the tool's own import
machinery rather than your engine.

Confirmed at build time: identical results on Python 3.11, 3.12 and 3.13. Full test
suite: 1162 passed, 0 failed.

To regenerate it from source: `python3.12 tools/amalgamate.py --out dist/cascade_map.py`.
The builder needs 3.12 or newer; the file it writes runs on 3.11+.

**Do not edit `metatron_engine.py` by hand.** Edits are lost on the next regeneration, and
the byte-identical guarantee no longer holds. Tell me what needs changing instead.

---

## 15. Glossary

| Term | Meaning |
|---|---|
| **Element** | anything nameable in the code: module, class, function, parameter, config key, data file, feature… |
| **Stable ID** | an element's address, derived from structure (`module::qualname`), never from line numbers |
| **Edge** | a relationship between two elements: calls, imports, inherits, decorates, registers, instantiates, references, configures |
| **Sink** | where the final decision comes out. You declare it with `--sink` |
| **Entry point** | where the cascade starts — your launcher. You declare it with `--entry` |
| **Cascade order** | the derived execution order from entry point onward |
| **Reachability** | whether an element can reach a sink: `REACHES_SINK`, `NO_SINK_PATH`, `UNKNOWN` |
| **Lineage** | how a value flows from ingestion to decision input |
| **Slice** | backward = what produces this; forward = what does changing this affect |
| **Barrier** | where lineage stops being traceable, and why |
| **Finding** | a located problem with evidence, confidence and a hint |
| **Unresolved** | something the tool could not work out, reported with location and reason |
| **Provenance** | the method and confidence attached to every fact |
| **Confidence** | `CERTAIN` > `RESOLVED` > `PROBABLE` > `HEURISTIC` > `UNKNOWN`; composes by weakest link |
| **Mode B** | static analysis. Never executes your code |
| **Mode A** | runtime tracing inside the harness. Only on explicit command |
| **Harness** | the containment that Mode A runs inside: no network, sandboxed writes, no spawning |
| **Contradiction** | a place the run disagreed with the static map |
| **Overlay** | runtime evidence layered on the static graph by shared IDs — never a second graph |
