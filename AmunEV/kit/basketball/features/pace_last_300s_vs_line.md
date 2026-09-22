# `pace_last_300s_vs_line`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **8** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:106295` — in `<module>()`

```python
'pace_last_300s_vs_line':  _f(lambda C: (C['scores_300'] * 2 / 300.0 * C['nominal']) - C['g']('total_points_handicap') if C['nominal'] and C['g']('total_points_handicap') is not None else None, 'event', True, 'match_total', 'recent pace against the line', 'PACE_TOTAL'),
```

