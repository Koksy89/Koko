# The engine, A to Z — one line per stage, with workers and time

Derived from the source (`laz_workers__map` call sites, `laz_mode3__find`, the runner's
own stage log), nothing executed.

**Worker policy, one place:** `laz_workers__count(workers, n_tasks)` returns
`max(1, min(want, cpu_count, n_tasks))` where `want` = `--workers N`, else
`LAZ_OWNER['search']['workers']` (**default 4**). `LAZ_WORKERS_NOCLIP=1` removes the
cpu_count clip. Every parallel stage goes through that one door — `laz_workers__map` —
which forks, and on failure calls `no_silent_serial()` so a stage can never quietly drop
to one core while the log still claims N.

| # | stage | what happens | workers | est. time |
|---|---|---|---|---|
| 1 | **preflight** | `go_live.py` checks the interpreter and stack, that the engine compiles, the GOD-1 and GOD-2 seals, the owner's rules, the version gate, the assembly invariant, frames and disk — statically, nothing executed | 1 | ~2 s |
| 2 | **load** | `run_m5.py` execs the whole engine into a real module (`lazarus_engine`) so forked workers resolve functions by reference rather than pickling them | 1 | 15–40 s |
| 3 | **gate** | `laz_contract__print`, the GOD-1 feature-documentation gate, the GOD-2 bible and owner-rules check | 1 | ~1 s |
| 4 | **ensure** | the frame is located and validated for the sport; missing columns are derived | 1 | 10–60 s |
| 5 | **startup / prepare** | frame loaded, h2h attached, causality guard run, settlement labelled | 1 | 1–3 min (first load ~2.5 min) |
| 6 | **library** | the owner's 482 certified features built over match chunks | **N** | 8–12 min · **~0 s cached** |
| 7 | **genome** | derived terms rebuilt in chunks | **N** | 1–4 min |
| 8 | **pool** | conditions assembled — engine + recovered + totals + **the 198-condition library** — then cast to float32 and put through the lookahead gate | 1 | 20–60 s |
| 9 | **corr** | correlation matrix computed in row blocks | **N** | 1–5 min |
| 10 | **interactions** | super-additive pairs found | **N** | 2–8 min |
| 11 | **manufacture** | the surviving pairs materialised into new conditions | **N** | 1–4 min |
| 12 | **PCA** | correlated clusters reduced to `pc_<hash>` composites, fitted IS-only, frozen to `laz_pca_<sport>.json` | 1 (parent) | 10–40 s |
| 13 | **bases** | registry bases + proposed bases + BaseFinder + tick-scan arm regions, deduped by identity | 1 | 30 s – 3 min |
| 14 | **ladder** | odds rungs built and merged; a rung below `band_min_matches_x × min_n` is skipped (**now 1.0×**) | 1 | ~5 s |
| 15 | **SWEEP** | every (base, rung) task: stack conditions, fit thresholds, IS/OOS split, permutation null, accept | **N** | **20 min – 3 h+** ← the long pole |
| 16 | **acceptance** | per leg: round-trip check, then `element_doc` written with the ordered execution spec | in-worker | inside the sweep |
| 17 | **merge** | legs merged across bases, duplicate chains collapsed | 1 | 10–60 s |
| 18 | **ledgers** | per-leg bet ledgers persisted to `mode3_<sport>.parquet` | 1 | 20–90 s |
| 19 | **combina** | arm table built, slips scored in a window, pairs scored in chunks | **N** | 2–10 min |
| 20 | **book** | accepted legs folded into `LAZ_BOOK`, tiers assigned | 1 | 10–40 s |
| 21 | **workbook** | `laz_xl__write` — the MODE3 xlsx, provenance stamped into it and into a sidecar | 1 | 30 s – 3 min |
| 22 | **DEPLOY** | `laz_deploy__emit` — registry SQL, feature module, PCA module, spec, verify script | 1 | 5–20 s |

Rough total for one sport with the library cached: **35 min – 4 h**, almost all of it the
sweep. A first run on a new frame, or the first run after a version bump, adds the 8–12
minute library build.

Times are order-of-magnitude on an 8-core machine at `--workers 4`. The sweep scales with
(bases × rungs × conditions), so a sport with a large pool and a long ladder is the
outlier rather than the average.

Other modes: `--mode 1` uses `label='mode1 cells'` (**N**), `--mode5` uses
`label='mode5 hypotheses'` (**N**), the vectorised grid uses `ProcessPoolExecutor` at
`cpu_count × 0.60`, and `laz_parallel` uses `label='cells'` (**N**).

---

# Every strategy in the engine

`audit/laz_strategy_inventory.py` finds strategy lists **by shape** — a top-level list
whose elements name themselves — so a container nobody remembered naming `V67_*` is still
found.

| | |
|---|---|
| strategy lists | **92** |
| strategy entries | **811** |
| names appearing in more than one list | **95** (191 entries) |
| distinct condition-sets carrying more than one name | **21** |

## The wiring gap — 296 strategies

`laz_registry()` is the live registry (read in ~15 places). Its own comment says:

> **SWEEP EVERY CONTAINER, DO NOT NAME THEM.** Hand-listing containers is how real
> basketball went missing … Forty-seven strategies were in the file and invisible to
> every registry-driven path: no ledgers, no sheets, no deployment contract.

The sweep was right in principle and had one filter that undid it:

```python
if not _nm or _nm in _known or _sp not in base:
    continue                      # <- skips every entry with no 'sport' key
```

**296 entries carry no `sport` key** — every legacy per-sport container, because the
*container* already said the sport:

| container | entries | sport, per the engine's own registry |
|---|---|---|
| `AB_EFB_STRATEGIES` | 44 | efootball |
| `AB_BB_STRATEGIES` | 36 | basketball |
| `BB_STRATEGIES` | 25 | basketball |
| `EFB_STRATEGIES` | 22 | efootball |
| `AB_TEN_STRATEGIES` | 22 | tennis |
| `EBB_STRATEGIES` / `AB_EBB_STRATEGIES` | 20 each | ebasketball |
| `AB_RFB_STRATEGIES` | 19 | football |
| `RFB_STRATEGIES` / `TEN_STRATEGIES` | 14 each | football / tennis |
| `RECON` | 10 | efootball |
| `NEW_*` × 5, `V5_*` × 5 | 5 each | per registry |

**All 296 resolve unambiguously. Zero ambiguous, zero undeclared.**

## The fix — by identity, nothing hand-listed

The sweep now walks every sport-keyed dict in the module and matches list objects by
`id()`, so a container's sport is read from wherever the engine already declares it —
a runner **tuple** (`SPORT_RUNNERS['football'] = (prep, fire, RFB_STRATEGIES, …)`), a
**specs dict** (`LAZ_SPECS['efootball'] = dict(specs=EFB_STRATEGIES, …)`), or a direct
**list**. A container put under a sport key tomorrow is picked up with no code change.

- entries keep their own `sport` when they have one
- an entry stamped from its container records `sport_source = 'container <NAME>'`
- **dedup by name, first wins** — the 95 duplicate names register exactly once
- a container under **no** sport key is skipped, never guessed
- counts land in `LAZ_REGISTRY_SWEEP` = `{with_sport, by_container, no_sport, deduped}`
- everything swept still passes the existing `port_status` purge — lookahead-biased and
  non-replicable entries are removed exactly as before

Proven on fixtures shaped like the engine's own registries: tuple, specs-dict and direct
list all route correctly; the duplicate name registers once; the undeclared container is
skipped; an entry that already had a sport keeps it. 8/8.


---

# The 198-condition library, wired in

`CONDITION_POOL` at L9054 holds **198 hand-built conditions** — odds-trend spikes, phase
and minute gates, ELO and H2H edges, form and streak filters, wall/cannon profiles,
market-dark windows. They had never entered the search **for any sport**.

**Why they were invisible.** The name is rebound at L38132 to a five-key *sport* dict with
**zero** shared keys, and the second wins. Every consumer of the original shape then reads
the wrong object:

```python
if cn in CONDITION_POOL:             # a condition name is never a sport name
    mask &= CONDITION_POOL[cn](...)  # so this never runs
```

and those consumers — `_build_combined_mask`, `discover_best_conditions` — are dead code.

**The live consumer they now belong to: the mode-3 pool.** That is the right owner, and
the only one that gives you everything you asked for in one place:

- the **sweep** can stack any of them into a strategy
- **BaseFinder** and the tick-scan can seed a **base** from one
- the **correlation / interaction / manufacture** stages can build compounds on them
- **PCA** can fold them into composites

**Offered to every sport, taken up where computable.** There is no sport list. Each
condition is tried against the frame the run actually prepared; one whose columns that
sport does not carry is **skipped with its reason**, recorded in
`laz_condlib__SKIPPED[sport]`, never faked and never silently dropped. Football carries
`draw_odds_vel`, basketball does not — so the draw-spike conditions arrive for one and are
reported absent for the other.

**Subject to every gate the rest of the pool is.** They are added *before* the float32
cast and *before* `LA.gate_pool`, so a condition that turns out to be forward-looking is
removed by the same lookahead gate that judges every other term. Nothing bypasses a
safeguard. Each is documented at creation under **GOD-1** with its recipe, its inputs and
its replication steps.

**Fail-closed on missing data**, which is the engine's own condition contract: a
comparison against NaN is False, so a tick with no value does not satisfy the condition.
That is why they are emitted as 0/1 rather than NaN-carrying floats.

**Proven:** all 198 recover and are callable; on a realistic frame 91 enter the pool, all
`float32`, all strictly 0/1, none constant; 107 are skipped and **every one carries a
written reason**; zero NameErrors.

> One bug this found in itself: the recovery first exec'd the assignment into an empty
> namespace, so eleven lambdas that read `np` or `pd` raised `NameError` and were lost to
> the *recovery* rather than to the frame. It now exec's into a copy of the module
> namespace, so they resolve exactly as they do in the engine.
