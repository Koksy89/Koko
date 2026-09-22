# `spread_cover_now`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **2** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:105890` — in `<module>()`

```python
'spread_cover_now':        _f(lambda C: C['sd'] + C['g']('point_spread_handicap') if C['g']('point_spread_handicap') is not None else None, 'tick', False, 'match_spread', 'current cover margin', 'LINE'),
```

