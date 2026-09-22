# `margin_per_possession`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **2** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:105873` — in `<module>()`

```python
'margin_per_possession':   _f(lambda C: np.where(C['remain_s'] > 14, C['sd'] / (C['remain_s'] / 14.0), np.nan), 'elapsed', False, 'moneyline', 'lead in possessions', 'structure'),
```

