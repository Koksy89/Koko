# The engine, A to Z — one line per stage, with worker counts

Derived from the source (`laz_workers__map` call sites, `laz_mode3__find`, the runner's
own stage log), nothing executed.

**Worker policy, one place:** `laz_workers__count(workers, n_tasks)` returns
`max(1, min(want, cpu_count, n_tasks))` where `want` = `--workers N`, else
`LAZ_OWNER['search']['workers']` (**default 4**). `LAZ_WORKERS_NOCLIP=1` removes the
cpu_count clip. Every parallel stage goes through that one door — `laz_workers__map` —
which forks, and on failure calls `no_silent_serial()` so a stage can never quietly drop
to one core while the log still claims N.

| # | stage | what happens | workers |
|---|---|---|---|
| 1 | **boot** | `go_live.py` preflights statically, extracts `run_m5_<sha8>.py` from the engine | 1 |
| 2 | **load** | `run_m5.py` execs the whole engine into a real module (`lazarus_engine`) so forked workers can resolve functions by reference | 1 |
| 3 | **gate** | `laz_contract__print`, GOD-1 seal + feature-documentation gate, GOD-2 bible + owner-rules check | 1 |
| 4 | **ensure** | frame located and validated per sport; columns derived if missing | 1 |
| 5 | **startup / prepare** | frame loaded, h2h attached, causality guard, settlement labelled | 1 |
| 6 | **library** | the owner's feature library built over match chunks | **N** (`label='library'`) |
| 7 | **genome** | derived terms rebuilt in chunks | **N** (`label='genome'`) |
| 8 | **pool** | conditions assembled — engine + recovered + proposed — then cast to float32 | 1 |
| 9 | **corr** | correlation matrix in row blocks | **N** (`label='corr'`) |
| 10 | **interactions** | super-additive pairs found | **N** (`label='interactions'`) |
| 11 | **manufacture** | the surviving pairs materialised into new conditions | **N** (`label='manufacture'`) |
| 12 | **PCA** | correlated clusters reduced to `pc_<hash>` composites, fitted IS-only, frozen to `laz_pca_<sport>.json` | 1 (parent) |
| 13 | **bases** | registry bases + proposed bases + BaseFinder + tick-scan arm regions, deduped by identity | 1 |
| 14 | **ladder** | odds rungs built and merged; rungs below `band_min_matches_x × min_n` skipped (**now 1.0×**) | 1 |
| 15 | **SWEEP** | every (base, rung) task: stack conditions, fit thresholds, IS/OOS split, permutation null, accept | **N** (`label='mode3 sweep'`) ← the long pole |
| 16 | **acceptance** | per leg: round-trip check, then `element_doc` written with the ordered execution spec | in-worker |
| 17 | **merge** | legs merged across bases, duplicate chains collapsed | 1 |
| 18 | **ledgers** | per-leg bet ledgers persisted to `mode3_<sport>.parquet` | 1 |
| 19 | **combina** | arm table built, slips scored in a window, pairs scored in chunks | **N** (`label='combina pairs'`) |
| 20 | **book** | accepted legs folded into `LAZ_BOOK`, tiers assigned | 1 |
| 21 | **workbook** | `laz_xl__write` — the MODE3 xlsx, provenance stamped into it and a sidecar | 1 |
| 22 | **DEPLOY** | `laz_deploy__emit` — registry SQL, feature module, PCA module, spec, verify script | 1 |

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
