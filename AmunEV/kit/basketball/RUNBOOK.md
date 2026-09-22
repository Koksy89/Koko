# GOING LIVE — BASKETBALL

**270 strategies** are deployable from this kit. 31 are excluded and named in `excluded_strategies.json`.

Source runs:
* `COMBINA_1027.xlsx` — 175 validated, 22 Sep 2026 10:27 UTC, contributed 169 unique
* `MODE3_202748.xlsx` — 142 validated, 22 Sep 2026 20:27 UTC, contributed 132 unique

Engine traced for feature recipes: `69bd4611031f0379`

---

## READ THIS FIRST — what this kit does and does not guarantee

**It guarantees** that every strategy listed is defined only in terms whose recipe is quoted verbatim from the engine source, with a line number you can open and check. Nothing is inferred.

**It does not guarantee bit-exact reproduction of the measured bets**, for one specific reason: the thresholds in the source workbooks are already rounded (`-160.315`, `2.2797`, `23.4333`). Rebuilding from six significant digits selects a slightly different set of ticks than the engine measured. For bit-exact work, re-export from a run of the current engine, which records thresholds exactly.

**Nothing here has been executed.** The arithmetic is quoted, not run.

---

## Step 1 — build the tick table

`schema.sql` creates `laz_tick` (one row per match tick) and `laz_bet`.

```bash
psql "$DATABASE_URL" -f schema.sql
```

Two rules that decide whether this works at all:

1. **Every feature column is NULLABLE and must stay NULL until determined.** A NULL means "not yet knowable at this tick". Defaulting to `0` makes conditions fire that the engine never fired, and it fails silently.
2. **One row per (match_id, ts).** The one-bet-per-match rule is applied on first qualifying tick, so duplicate ticks produce duplicate arms.

## Step 2 — populate the feature columns

These **162** feature columns are used by the deployable strategies. Each has a card in `features/` quoting the engine source that defines it.

Raw columns you must supply from the feed:

* `match_id`
* `ts`
* `market_status`
* `home_odds`
* `away_odds`
* `h_score`
* `a_score`
* `total_points_handicap`
* `total_points_over`
* `total_points_under`
* `phase`
* `league`

Derived columns: see `features.json` and `features/<term>.md`. Build them in dependency order — a term whose card references another term needs that one first.

### The causality rule

Every feature must be computable from the current tick and earlier ticks of the same match, and from nothing else. If a value would change when a LATER tick arrives, it is forward-looking and the strategy built on it is invalid. The engine enforces this with a truncation door; production must not reintroduce what that door excluded.

## Step 3 — select bets

```bash
psql "$DATABASE_URL" -f evaluate.sql
```

`evaluate.sql` holds one statement per strategy. The gate order is the engine's and must not be reordered:

| # | gate | rule |
|---|---|---|
| 1 | CONDITIONS | every clause true at the tick; a NULL never satisfies one |
| 2 | PRICE | backed-side price >= the strategy's min odds floor |
| 3 | MARKET | the market is open at that tick |
| 4 | FIRST TICK | the first tick of the match passing 1-3 is the bet |

**Check the price column per strategy before staking.** `evaluate.sql` defaults to `GREATEST(home_odds, away_odds)`. Each row of `strategies.csv` carries its own `backed` side and `price_column`; where those are recorded, use them.

## Step 4 — prove production matches

Do not stake before this passes.

```bash
# export a window of ticks and your own bet list as JSON, then:
python3 verify.py strategies.json ticks.json production_bets.json
```

`reference_impl.py` is the arbiter: it applies the four gates in order, in plain Python, with no dependencies. If your system and it disagree, your system is wrong. `verify.py` reports missing bets, extra bets, price mismatches and arm-tick mismatches, and exits non-zero on any of them.

Start with one strategy and one week of ticks. Widen only once it matches.

## Step 5 — stake

Settlement is **stated** per strategy (`graded_against`) but **not implemented** in this kit: it selects and prices bets, it does not grade them. Grade with the owner rule — the score decides; a level or absent score settles on the last validated odds row at <= 1.35; neither, unsettled.

Stake small first and compare each strategy's live results against its recorded OOS win and ROI in `strategies.csv`. A strategy that diverges early is telling you a feature is built differently in production.

---

## What was excluded, and why

**11 terms have no recoverable recipe**, so the 31 strategies using them are excluded:

* `pc_2f7f4d0c` — PCA component: its inputs and loadings live in the run's PCA manifest, not in the engine source. Export the manifest from the run that produced it, or drop the strategies that use it.
* `pc_428f55cf` — PCA component: its inputs and loadings live in the run's PCA manifest, not in the engine source. Export the manifest from the run that produced it, or drop the strategies that use it.
* `pc_4fa6ab33` — PCA component: its inputs and loadings live in the run's PCA manifest, not in the engine source. Export the manifest from the run that produced it, or drop the strategies that use it.
* `pc_51f228a2` — PCA component: its inputs and loadings live in the run's PCA manifest, not in the engine source. Export the manifest from the run that produced it, or drop the strategies that use it.
* `pc_6a99ae09` — PCA component: its inputs and loadings live in the run's PCA manifest, not in the engine source. Export the manifest from the run that produced it, or drop the strategies that use it.
* `pc_7b8a13a5` — PCA component: its inputs and loadings live in the run's PCA manifest, not in the engine source. Export the manifest from the run that produced it, or drop the strategies that use it.
* `pc_aa16b0a5` — PCA component: its inputs and loadings live in the run's PCA manifest, not in the engine source. Export the manifest from the run that produced it, or drop the strategies that use it.
* `pc_c8103e63` — PCA component: its inputs and loadings live in the run's PCA manifest, not in the engine source. Export the manifest from the run that produced it, or drop the strategies that use it.
* `pc_dc4196fb` — PCA component: its inputs and loadings live in the run's PCA manifest, not in the engine source. Export the manifest from the run that produced it, or drop the strategies that use it.
* `pc_f3e46219` — PCA component: its inputs and loadings live in the run's PCA manifest, not in the engine source. Export the manifest from the run that produced it, or drop the strategies that use it.
* `pc_fea3bd2c` — PCA component: its inputs and loadings live in the run's PCA manifest, not in the engine source. Export the manifest from the run that produced it, or drop the strategies that use it.

All of these are PCA components. Their inputs and loadings live in the PCA manifest of the run that created them, not in the engine source. Export that manifest and they become buildable; until then the strategies that use them cannot be reproduced.

## File map

| file | what it is |
|---|---|
| `MANIFEST.json` | what is in the kit, its provenance, guarantees and limits |
| `strategies.json` / `.csv` | the deployable strategies and their full contract |
| `excluded_strategies.json` | what was excluded and why |
| `features.json` | every term, with the engine source that defines it |
| `features/<term>.md` | one readable card per term |
| `schema.sql` | DDL for `laz_tick` and `laz_bet` |
| `evaluate.sql` | one bet-selection statement per strategy |
| `reference_impl.py` | the arbiter: four gates, plain Python |
| `verify.py` | production vs reference, exits non-zero on any difference |
| `RUNBOOK.md` | this file |
