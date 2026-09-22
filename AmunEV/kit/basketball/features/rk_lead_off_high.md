# `rk_lead_off_high`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **3** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:106391` — in `<module>()`

```python
'rk_lead_off_high':    dict(cls='STATE',      use='WOE',   kf='tick',    formula='3.6 - p_lead (distance below the upper rung)', why='LIVE_CERTAINTY', note='rung position from the top'),
```

### `AmunEV_Engine_V2.py:106452` — in `<module>()`

```python
'rk_lead_off_high':    _f(lambda C: 3.6 - np.where(C['sd'] > 0, C['ho'], np.where(C['sd'] < 0, C['ao'], np.nan)), 'tick', False, 'match_ml', 'leader price below the upper rung', 'review: STATE/WOE'),
```

