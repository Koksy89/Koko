# `abs_margin_per_remaining_s`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **2** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:105851` — in `<module>()`

```python
'abs_margin_per_remaining_s': _f(lambda C: np.where(C['remain_s'] > 0, np.abs(C['sd']) / np.maximum(C['remain_s'], 1), np.nan), 'elapsed', False, 'moneyline', 'side-agnostic form for trailer bases', 'MARGIN_RATE'),
```

