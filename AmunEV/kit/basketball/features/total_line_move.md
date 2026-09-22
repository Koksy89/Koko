# `total_line_move`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **4** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:106298` — in `<module>()`

```python
'total_line_move':         _f(lambda C: C['g']('total_points_handicap') - pd.Series(C['g']('total_points_handicap')).groupby(C['mid']).transform('first').values if C['g']('total_points_handicap') is not None else None, 'tick', False, 'match_total', 'how far the line has moved from its open', 'LINE'),
```

### `AmunEV_Engine_V2.py:106330` — in `<module>()`

```python
'total_line_move':         _f(lambda C: C['g']('total_goals_handicap') - pd.Series(C['g']('total_goals_handicap')).groupby(C['mid']).transform('first').values if C['g']('total_goals_handicap') is not None else None, 'tick', False, 'match_total', 'line move from open', 'LINE'),
```

