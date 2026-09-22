# `leader_drift`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **1** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:98930` — in `fb_features__build()`

```python
put('leader_drift', lo / g('pm_ratio').replace(0, np.nan))
```

### `AmunEV_Engine_V2.py:106316` — in `<module>()`

```python
'leader_drift':            _f(lambda C: np.where(C['sd'] > 0, C['drift_h'], np.where(C['sd'] < 0, C['drift_a'], np.nan)), 'tick', False, 'match_ml', 'how far the leader has shortened from open', 'DRIFT'),
```

### `AmunEV_Engine_V2.py:106345` — in `<module>()`

```python
'leader_drift':            _f(lambda C: np.where(C['sd'] > 0, C['drift_h'], np.where(C['sd'] < 0, C['drift_a'], np.nan)), 'tick', False, 'match_ml', 'leader shortening from open', 'DRIFT'),
```

