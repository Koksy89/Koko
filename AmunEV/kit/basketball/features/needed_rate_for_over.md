# `needed_rate_for_over`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **4** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:106301` — in `<module>()`

```python
'needed_rate_for_over':    _f(lambda C: np.where(C['remain_s'] > 30, (C['g']('total_points_handicap') - C['tot']) / (C['remain_s'] / 60.0), np.nan) if C['g']('total_points_handicap') is not None else None, 'elapsed', False, 'match_total', 'points per minute still needed for the over', 'LINE'),
```

