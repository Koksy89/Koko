# `overround_now`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **2** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:105858` — in `<module>()`

```python
'overround_now':           _f(lambda C: C['imp_h'] + C['imp_a'] + (np.nan_to_num(C['imp_d']) if C.get('imp_d') is not None else 0), 'tick', False, 'moneyline', 'book uncertainty', 'MARKET_SHAPE'),
```

