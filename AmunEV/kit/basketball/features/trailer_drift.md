# `trailer_drift`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **3** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:106315` — in `<module>()`

```python
'trailer_drift':           _f(lambda C: np.where(C['sd'] > 0, C['drift_a'], np.where(C['sd'] < 0, C['drift_h'], np.nan)), 'tick', False, 'match_ml', 'how far the trailer has drifted from open', 'DRIFT'),
```

### `AmunEV_Engine_V2.py:106344` — in `<module>()`

```python
'trailer_drift':           _f(lambda C: np.where(C['sd'] > 0, C['drift_a'], np.where(C['sd'] < 0, C['drift_h'], np.nan)), 'tick', False, 'match_ml', 'trailer drift from open', 'DRIFT'),
```

