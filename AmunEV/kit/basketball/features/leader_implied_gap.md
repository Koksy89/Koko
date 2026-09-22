# `leader_implied_gap`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **5** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:106313` — in `<module>()`

```python
'leader_implied_gap':      _f(lambda C: np.where(C['sd'] > 0, C['imp_h'] - C['imp_a'], np.where(C['sd'] < 0, C['imp_a'] - C['imp_h'], np.nan)), 'tick', False, 'match_ml', 'how far the market separates leader from trailer', 'MARKET_SHAPE'),
```

### `AmunEV_Engine_V2.py:106343` — in `<module>()`

```python
'leader_implied_gap':      _f(lambda C: np.where(C['sd'] > 0, C['imp_h'] - C['imp_a'], np.where(C['sd'] < 0, C['imp_a'] - C['imp_h'], np.nan)), 'tick', False, 'match_ml', 'leader minus trailer implied', 'MARKET_SHAPE'),
```

