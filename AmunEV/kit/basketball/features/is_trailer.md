# `is_trailer`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **1** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:95180` — in `<module>()`

```python
laz_everything__BUILDERS = {'efb_prog': lambda P, C: C['el'], 'cb_prog': lambda P, C: C['el'], 'prog_t': lambda P, C: C['el'], 'ebb_prog': lambda P, C: C['el'], 'ten_prog': lambda P, C: C['el'], 'prog': lambda P, C: C['el'], 'progress': lambda P, C: C['el'], 'minute': lambda P, C: C['el'] * laz_everything___clk(C) if laz_everything___clk(C) is not None else None, 'ten_minute': lambda P, C: C['el'] * laz_everything___clk(C) if laz_everything___clk(C) is not None else None, 'league_minutes': lambda P, C: C['el'] * laz_everything___clk(C) if laz_everything___clk(C) is not None else None, 'league_nominal_minutes': lambda P, C: (laz_everything___clk(C) * np.ones_like(C['el']) if np.isscalar(laz_everything___clk(C)) else laz_everything___clk(C)) if laz_everything___clk(C) is not None else None, 'elapsed': lambda P, C: C['el'], 'u_prog': lambda P, C: C['el'], 'sd_abs': lambda P, C: np.abs(C['sd']), 'sd': lambda P, C: C['sd'], 'lead': lambda P, C: C['sd'], 'margin': lambda P, C: C['sd'], 'is_leader': lambda P, C: (C['sd'] > 0).astype(float), 'is_trailer': lambda P, C: (C['sd'] < 0).astype(float), 'score_diff': lambda P, C: C['sd'], 'total_goals': lambda P, C: C['tot'], 'tg': lambda P, C: C['tot'], 'current_total_goals': lambda P, C: C['tot'], 'odds_ratio': lambda P, C: np.where(C['ao'] > 0, C['ho'] / C['ao'], np.nan), 'pm_ratio': lambda P, C: np.where(C['ao'] > 0, C['ho'] / C['ao'], np.nan), 'leader_price': lambda P, C: np.where(C['sd'] > 0, C['ho'], np.where(C['sd'] < 0, C['ao'], np.nan)), 'loser_odds': lambda P, C: np.where(C['sd'] > 0, C['ao'], np.where(C['sd'] < 0, C['ho'], np.nan)), 'draw_odds': lambda P, C: C['do'], 'home_odds': lambda P, C: C['ho'], 'away_odds': lambda P, C: C['ao'], 'overround': lambda P, C: 1 / np.where(C['ho'] > 1, C['ho'], np.nan) + 1 / np.where(C['ao'] > 1, C['ao'], np.nan)}
```

