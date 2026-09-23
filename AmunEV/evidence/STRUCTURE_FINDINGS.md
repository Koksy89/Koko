# What reading the whole file found

Every number here is produced by a script in `audit/`, against the engine, with nothing
executed. Re-run them yourself:

```bash
python3 audit/laz_structure.py       AmunEV_Engine_V2.py   # the map
python3 audit/laz_assembly_audit.py  AmunEV_Engine_V2.py   # the defect class
python3 audit/laz_order_check.py     AmunEV_Engine_V2.py   # would it survive top-to-bottom
```

## The engine, measured

| | |
|---|---|
| lines | 123,549 |
| top-level statements | 3,031 |
| module sections (`# LAZ_BRAIN MODULE:`) | 81 |
| function definitions | 1,459 (56,555 lines) |
| top-level data assignments | 1,053 (41,349 lines) |
| classes | 45 (9,745 lines) |
| **function defs that read nothing at import — free to move** | **1,306** |
| **statements that pin the order** | **560** |

That last pair is the whole reorganisation question. A function *body* is not read when
the function is defined, so 1,306 of 1,459 definitions can be moved anywhere. The 560
data statements cannot: a dict literal that calls a function, a list built from another
list, a `for` loop that stamps defaults onto a registry — those execute where they are
written.

## FIXED: 220 strategies were invisible to the registry

At **L63444** the file assembles its registry, under a comment that says:

> **FINAL REGISTRY ASSEMBLY — MUST BE THE LAST THING IN THIS SECTION.** Assembling
> earlier silently drops any family defined later in the file: `V67_WALL_STRATEGIES` was
> absent from `V67_EFB_ALL` for exactly that reason, so all three Wall strategies were
> registered, documented and parity-tested **while being invisible to the evaluator**.
> Assemble LAST.

It was fixed there — and then broken again by everything added below it. Between L63444
and L80353 the four family lists are rebound **35 more times**:

| list | families folded in after the registry | strategies |
|---|---|---|
| `V67_EBB_ALL` | EBBD, EBBQ, EBBS2, REPAIRED, MONEYLINE, MISPRICE, Q4ML, WINDOW_UNDER | **69** |
| `V67_CB_ALL` | LEADER_ML, LEADER_185, WINDOW_ML, COMEBACK ×3, OTAKUMARU ×6, COMEBACK_LEADER, LEADER_60, CB_REBUILT | **74** |
| `V67_EFB_ALL` | WINDOW_UNDER, UNDER_LADDER, UNDER_70, UNDER_70V, UNDER_75, UNDER_COMBINED, WALL_UNDER, WHITELIST_UNDER, UNDER_200, TOTALS_UNDER | **67** |
| `V67_TEN_STRATEGIES` | S1W, BREAK_BOOK | **10** |
| | **total** | **220** |

`V67_REGISTRY = {'efootball': V67_EFB_ALL + V67_CB_ALL, ...}` builds a **new list** at the
line it is written on. Rebinding the name afterwards does not reach into it. So all 220
were absent from `V67_REGISTRY` and `V67_ALL_STRATEGIES`, and the three stamping loops
below it (`evaluator`, `settles`/`backs`/`entry_window`, `laz_id`) never ran over any of
them.

**Fixed additively.** A real final assembly now runs after the last family change.
Nothing above was moved or deleted, every stamp uses `setdefault`, and re-running the
block changes nothing. `audit/laz_assembly_audit.py` **fails** the engine as it was and
**passes** it now, and `go_live.py` preflight runs it on every run.

## FOUND, NOT YET FIXED — needs your call

### 198 conditions that never reach the cascade

`CONDITION_POOL` is defined twice, as two dicts of **completely different shape** and
**zero shared keys**:

| line | shape | keys |
|---|---|---|
| L9054 | `{condition_name: lambda}` | **198** |
| L38132 | `{sport: ...}` | 5 |

The second wins. Its readers (`v46_discover`, L38193) want the sport-keyed shape and are
correct. The readers that want the 198 — `_build_combined_mask`, `discover_best_conditions`
— are **dead code**: nothing in the engine reaches them.

So the 198 conditions are not silently failing in a live path. They were **never wired
into the current cascade at all**. That is a decision for you: they are a condition
library sitting unused. I have not touched them.

### 26 strategies discarded by two reset lines

| line | what it does | cost |
|---|---|---|
| L63441 | `V67_EFB_ALL = ...` **resets**, dropping what L62257 had built | `V67_EFBW_JUMP_STRATEGIES` (8) + `V67_EFBC_STRATEGIES` (5) = **13 eFootball** |
| L81157 | `BASKETBALL_V73_MONEYLINE = [...]` **resets** the MLQ list built at L81063 | **13 basketball moneyline** (MLQ03…MLQ15) |

These are *resets*, not accumulations — the earlier content is gone. Whether they were
retired deliberately or dropped by accident I cannot tell from the source. Tell me and I
will fold them in.

### `g43_g43_prep_basketball` is referenced and never defined

`SPORT_RUNNERS['basketball']` and `['ebasketball']` are
`lambda p: g43_g43_prep_basketball(p, ...)`. That name does not exist anywhere in the
file. Because it is inside a lambda it is late-bound, so it only raises if those runners
are called. One of the 18 pre-existing undefined names.

## What I have NOT done

**The full physical reorganisation.** I have not moved 123,549 lines into cascade order,
and I am not going to claim I have. What exists now is the part that makes it *safe* to
do: the map, the order checker that says whether a file survives being executed top to
bottom, and a verified baseline (17 benign forward references — `except X as e` handler
variables and `__file__`).

The move itself is mechanical once those hold, and it is verified by: every definition
byte-identical before and after, the top-level binding set unchanged, `compile()` clean,
the order check not growing, and the audits at parity. That is exactly the battery this
change was put through — see the commit.
