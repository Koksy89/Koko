# `rk_lead_off_low`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **1** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:106390` — in `<module>()`

```python
'rk_lead_off_low':     dict(cls='STATE',      use='WOE',   kf='tick',    formula='p_lead - min_odds (1.4)', why='LIVE_CERTAINTY', note='how far the leader\'s price sits above the ladder floor; a rung-position variable'),
```

### `AmunEV_Engine_V2.py:106451` — in `<module>()`

```python
'rk_lead_off_low':     _f(lambda C: np.where(C['sd'] > 0, C['ho'], np.where(C['sd'] < 0, C['ao'], np.nan)) - 1.4, 'tick', False, 'match_ml', 'leader price above the ladder floor', 'review: STATE/WOE'),
```

