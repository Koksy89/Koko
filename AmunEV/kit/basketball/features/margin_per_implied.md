# `margin_per_implied`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **2** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:106314` — in `<module>()`

```python
'margin_per_implied':      _f(lambda C: np.where(np.isfinite(C['imp_h']), np.abs(C['sd']) / np.maximum(np.where(C['sd'] > 0, C['imp_h'], C['imp_a']), 0.05), np.nan), 'tick', False, 'match_ml', 'margin per unit of implied probability — a big lead the market still doubts', 'MARKET_SHAPE'),
```

