# `rk_dog_odds`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **15** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:106387` — in `<module>()`

```python
'rk_dog_odds':         dict(cls='STATE',      use='BOTH',  kf='tick',    formula='max(ho, ao)', why='LIVE_CERTAINTY', note=''),
```

### `AmunEV_Engine_V2.py:106448` — in `<module>()`

```python
'rk_dog_odds':         _f(lambda C: np.maximum(C['ho'], C['ao']), 'tick', False, 'match_ml', 'the live underdog\'s price', 'review: STATE'),
```

