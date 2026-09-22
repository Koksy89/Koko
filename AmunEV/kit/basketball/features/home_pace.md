# `home_pace`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **3** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:105884` — in `<module>()`

```python
'home_pace':               _f(lambda C: np.where(C['el'] > 0.05, C['hs'] / C['el'], np.nan), 'elapsed', False, 'home_total', 'home projected total', 'PACE_TOTAL'),
```

