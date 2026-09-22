# `pace_vs_line_pct`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **2** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:106296` — in `<module>()`

```python
'pace_vs_line_pct':        _f(lambda C: np.where(C['el'] > 0.05, (C['tot'] / C['el']) / C['g']('total_points_handicap') - 1, np.nan) if C['g']('total_points_handicap') is not None else None, 'elapsed', False, 'match_total', 'projected total as a % of the line', 'PACE_TOTAL'),
```

