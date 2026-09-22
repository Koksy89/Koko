# `price_move_since_score`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **5** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:91495` — in `laz_featuregen__term_lines()`

```python
'price_move_since_score': "out['price_move_since_score'] = (lambda _h: (ho / _h) if (not _isnan(_h) and _h > 0 and not _isnan(ho)) else np.nan)(S.ho_at_last_score(tot, ho))",   # pool: ho / ho at the last score change (eTennis replay proof failed on it at tick 69)
```

### `AmunEV_Engine_V2.py:105865` — in `<module>()`

```python
'price_move_since_score':  _f(lambda C: np.where(C['ho_at_last_score'] > 0, C['ho'] / C['ho_at_last_score'], np.nan) if C.get('ho_at_last_score') is not None else None, 'event', True, 'moneyline', 'has the price digested the last score', 'structure'),
```

