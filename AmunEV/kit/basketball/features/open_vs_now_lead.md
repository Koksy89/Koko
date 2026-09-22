# `open_vs_now_lead`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **6** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:106385` — in `<module>()`

```python
'open_vs_now_lead':    dict(cls='STATE',      use='BOTH',  kf='tick',    formula='leader\'s opening price - p_lead', why='LIVE_CERTAINTY', note='the same move in price units'),
```

### `AmunEV_Engine_V2.py:106446` — in `<module>()`

```python
'open_vs_now_lead':    _f(lambda C: np.where(C['sd'] > 0, C['open_h'] - C['ho'], np.where(C['sd'] < 0, C['open_a'] - C['ao'], np.nan)), 'tick', False, 'match_ml', 'the leader\'s price move in price units', 'review: STATE'),
```

