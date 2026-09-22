# `rk_odds_ratio`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **14** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:106388` — in `<module>()`

```python
'rk_odds_ratio':       dict(cls='STATE',      use='WOE',   kf='tick',    formula='rk_dog_odds / rk_fav_odds', why='LIVE_CERTAINTY', note='the market\'s confidence as a ratio; a banding variable'),
```

### `AmunEV_Engine_V2.py:106449` — in `<module>()`

```python
'rk_odds_ratio':       _f(lambda C: np.maximum(C['ho'], C['ao']) / np.minimum(C['ho'], C['ao']), 'tick', False, 'match_ml', 'the market\'s confidence as a ratio', 'review: STATE/WOE'),
```

