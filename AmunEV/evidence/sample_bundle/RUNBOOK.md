# GO LIVE — BASKETBALL

Engine `engine_e2e_test`  ·  128 strategies deployable  ·  14 withheld  ·  146 feature columns  ·  8 PCA composites

## What this bundle is

Everything the live stack needs to run this run's strategies, generated at the
moment they were validated. Nothing here is retyped, re-derived or approximated:
each strategy's clauses, thresholds and price band come from the record written
inside the search worker the instant the strategy was accepted.

## Run it

```bash
# 1. schema, namespace, strategies and provenance, in one transaction
psql "$BETSMITH_DSN" -v ON_ERROR_STOP=1 -f deploy.sql

# 2. the generated PCA module, if this run froze any composites
cp laz_pca_features_basketball.py  /opt/betsmith/

# 3. prove the database now matches this bundle (reads only)
python3 verify_live.py "$BETSMITH_DSN"
```

## Wire the PCA module in (once)

In `basketball_lazarus_efb_strategies.py`, beside the existing book merge:

```python
import laz_pca_features_basketball as _PCA
...
    g = self._book.merge(st, g, period_len=...)
    g = _PCA.compute(g)          # <- frozen transforms, after the merge
```

It must run AFTER the book merge (its inputs are book features) and BEFORE
the condition loop. It adds only its own `pc_*` keys and never overwrites one.

## The gates every shipped row already passed

| # | Rule | What production does when it is violated |
|---|---|---|
| G1 | every clause matches the evaluator's `_COND` regex | `unparseable condition` — the sport loads **zero** strategies |
| G2 | every threshold is a plain decimal | same; `1e-05`, `inf` and `nan` are all unparseable |
| G4 | the market is in this sport's `SETTLEABLE_MARKETS` | `market not settleable here` — the sport loads **zero** strategies |
| G5 | every condition term has a namespace row | `has no namespace row` — the sport loads **zero** strategies |
| G6 | every namespace column exists on `rt_allsports_laz_features` | `namespace names columns absent` — the sport will not start |
| G7 | `bet_role` is one `resolve_side` answers | **nothing at all** — the strategy silently never fires again |
| G9 | the price band is finite and ordered | **nothing at all** — a NaN bound admits every price |
| G10 | at least one condition survives | **nothing at all** — it fires on the first tick of every match |
| G11 | every column name is ≤ 63 bytes and unique | Postgres truncates with a notice; the namespace stops matching |
| G12 | the shipping text rebuilds the measured bets | **nothing at all** — production places bets the search never measured |

G7, G9, G10 and G12 raise no error anywhere. They are the reason this file
exists: a registry that loads cleanly is not the same thing as a registry that
bets what the engine measured.

## Preflight

Every rule above was re-checked against the written bundle, in the loader's
own order, and passed.

## Withheld — 14 strategies

Validated by this run, but not shippable unchanged. Each is here with the
exact reason; none of them is in `deploy.sql`, because one bad row takes the
whole sport down at load. See `quarantine.csv` for the full list.

- 7 × market '' / outcome 'spread'
- 2 × market '' / outcome 'q1_result'
- 2 × base 'prop:match_total_pace' settles a total but its own name does not say UNDER or OVER
- 2 × market '' / outcome 'h1_result'
- 1 × market '' / outcome ''

### Bases production cannot arm

These need a market or a side production can express, not a feature.
A base with a residual (a window, a margin, a clock) would need that
predicate as an ordinary condition before it could ship.

| base | strategies withheld | what is missing |
|---|---|---|
| `spread_dog_cover` | 7 | no settleable production market |
| `q1_winner` | 2 | no settleable production market |
| `prop:match_total_pace` | 2 | no settleable production market |
| `h1_winner` | 2 | no settleable production market |
| `prop:h1_leader_hold` | 1 | the first-half leader |

## Terms the generator could not write

`laz_features_basketball.py` emits 71 of 143
terms 1:1 from the engine builders. These it could not, so it wrote `NaN`
for them rather than invent a value — a strategy reading one will not fire
until the term is implemented. The strategies are still registered and
enabled: they were validated, and nothing about them is wrong.

| term | strategies reading it |
|---|---|
| `X_X_u_elapsed__minus__u_time_in_lead__minus__X_u_pace__over__u_time_in_lead` | 0 |
| `X_minute__minus__u_drought` | 0 |
| `X_minute__over__u_vol` | 0 |
| `abs_margin_per_remaining_s` | 0 |
| `backed_pts_last_60s` | 0 |
| `both_scored` | 0 |
| `dow` | 0 |
| `eng_minute_ge_2` | 0 |
| `eng_score_tied` | 0 |
| `fav_lead_m30` | 0 |
| `first_scorer_is_backed` | 0 |
| `home_pace` | 0 |
| `is_trailer` | 0 |
| `lead_change_last_300s` | 0 |
| `lead_changes_300s` | 0 |
| `lead_changes_total` | 0 |
| `lead_m15` | 0 |
| `lead_m75` | 0 |
| `lead_vs_line` | 0 |
| `leader_implied_gap` | 0 |
| `leader_runmax` | 0 |
| `line` | 0 |
| `line_flat` | 0 |
| `line_move` | 0 |
| `line_open` | 0 |
| `line_vel` | 0 |
| `loser_runmax` | 0 |
| `loser_runmin` | 0 |
| `margin_per_possession` | 0 |
| `margin_range_300s` | 0 |
| `margin_rate` | 0 |
| `motif_0` | 0 |
| `motif_2` | 0 |
| `need_frac` | 0 |
| `need_vs_expected` | 0 |
| `open_vs_now_lead` | 0 |
| `overround_now` | 0 |
| `pace_last_300s_vs_line` | 0 |
| `pace_vs_line_pct` | 0 |
| `pm_ratio` | 0 |

## Files

- `STRATEGY_SPEC.md` — EVERY strategy, every element, in the exact order it happens
- `laz_features_basketball.py` — every term implemented 1:1 from the engine builders
- `quarantine.csv` — every validated strategy this run withheld, and the exact reason
- `verify_live.py` — re-checks the live database against this bundle; reads only
- `laz_pca_features_basketball.py` — the frozen PCA transforms, as importable production code
- `MANIFEST.json` — the machine-readable bundle: every row, column and hash
- `strategies_full.json` — the same, machine-readable: every ordered step, every threshold, every column
- `quarantine.json` — the same, with each strategy's full acceptance record attached
- `deploy.sql` — the whole deployment, one transaction, idempotent
