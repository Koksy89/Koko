# `lead_vs_prematch_gap`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **2** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:105864` — in `<module>()`

```python
'lead_vs_prematch_gap':    _f(lambda C: np.where(C['sd'] != 0, np.abs(C['imp_h'] - C['imp_a']) * np.sign(C['sd']) * np.where(C['pm_fav'] == 'home', 1, -1), np.nan), 'tick', False, 'moneyline', 'how far the lead contradicts the pre-match view', 'structure'),
```

