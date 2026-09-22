# GO LIVE — BASKETBALL

Engine `engine_e2e_test`  ·  65 strategies deployable  ·  77 withheld  ·  107 feature columns  ·  7 PCA composites

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

## Withheld — 77 strategies

Validated by this run, but not shippable unchanged. Each is here with the
exact reason; none of them is in `deploy.sql`, because one bad row takes the
whole sport down at load. See `quarantine.csv` for the full list.

- 24 × base 'prop:first_scorer_hold' arms on more than a role: production would have to reproduce
- 16 × base 'late_lead_hold' arms on more than a role: production would have to reproduce elapsed
- 10 × production computes no minute
- 8 × production computes no u_elapsed, u_time_in_lead
- 7 × market '' / outcome 'spread'
- 4 × base 'prop:held_lead' arms on more than a role: production would have to reproduce seconds
- 3 × base 'prop:late_lead_hold' arms on more than a role: production would have to reproduce el
- 2 × market '' / outcome 'q1_result'
- 2 × base 'prop:match_total_pace' settles a total but its own name does not say UNDER or OVER
- 2 × market '' / outcome 'h1_result'
- 1 × production computes no point_spread_away
- 1 × production computes no q1_lead
- 1 × production computes no pace_ratio, q1_lead
- 1 × production computes no minute, u_elapsed, u_time_in_lead
- 1 × base 'prop:dog_leading_late' arms on more than a role: production would have to reproduce 

### Build these features and the strategies come back

Each line is one feature to implement in `laz_features.py`, and the
number of withheld strategies that would then ship. Nothing else about
them has to change: they are already validated, already documented and
already in `quarantine.json` with their exact clauses.

| feature | strategies recovered |
|---|---|
| `minute` | 11 |
| `u_elapsed` | 9 |
| `u_time_in_lead` | 9 |
| `q1_lead` | 3 |
| `point_spread_away` | 1 |
| `pace_ratio` | 1 |
| `point_spread_home` | 1 |

### Bases production cannot arm

These need a market or a side production can express, not a feature.
A base with a residual (a window, a margin, a clock) would need that
predicate as an ordinary condition before it could ship.

| base | strategies withheld | what is missing |
|---|---|---|
| `prop:first_scorer_hold` | 24 | the side that scored FIRST, held from the event; laz_features computes no first-scorer flag |
| `late_lead_hold` | 16 | elapsed in [0.85, 1.2]; margin > 3 |
| `spread_dog_cover` | 7 | no settleable production market |
| `prop:held_lead` | 4 | seconds since the lead last changed >= 600; laz_features computes no lead-change clock |
| `prop:late_lead_hold` | 3 | elapsed in [0.85, 1.2]; margin > 3 |
| `q1_winner` | 2 | no settleable production market |
| `prop:match_total_pace` | 2 | no settleable production market |
| `h1_winner` | 2 | no settleable production market |
| `prop:dog_leading_late` | 1 | elapsed in [0.75, 1.2] |
| `prop:fresh_lead` | 1 | seconds since the lead last changed <= 60; laz_features computes no lead-change clock |
| `prop:q4_close_trailer` | 1 | elapsed >= 0.75; margin <= 6 |
| `prop:h1_leader_hold` | 1 | the first-half leader |

## Files

- `quarantine.csv` — every validated strategy this run withheld, and the exact reason
- `verify_live.py` — re-checks the live database against this bundle; reads only
- `laz_pca_features_basketball.py` — the frozen PCA transforms, as importable production code
- `MANIFEST.json` — the machine-readable bundle: every row, column and hash
- `quarantine.json` — the same, with each strategy's full acceptance record attached
- `deploy.sql` — the whole deployment, one transaction, idempotent
