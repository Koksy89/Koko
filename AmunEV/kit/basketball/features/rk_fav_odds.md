# `rk_fav_odds`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **8** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:106386` — in `<module>()`

```python
'rk_fav_odds':         dict(cls='STATE',      use='BOTH',  kf='tick',    formula='min(ho, ao) — the live favourite\'s price', why='LIVE_CERTAINTY', note=''),
```

### `AmunEV_Engine_V2.py:106447` — in `<module>()`

```python
'rk_fav_odds':         _f(lambda C: np.minimum(C['ho'], C['ao']), 'tick', False, 'match_ml', 'the live favourite\'s price', 'review: STATE'),
```

