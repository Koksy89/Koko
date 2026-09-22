# `tg_proj_vs_line`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **3** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:114045` — in `laz_totals__conditions()`

```python
out = {'tg_line': L, 'tg_now': now, 'tg_to_go': L - now, 'tg_over_px': ov, 'tg_under_px': un, 'tg_imp_over': io, 'tg_imp_under': iu, 'tg_overround': io + iu, 'tg_px_ratio': np.where(un > 1, ov / un, np.nan), 'tg_pace': np.where(el > 0.02, now / np.maximum(el, 0.02), np.nan), 'tg_proj_vs_line': np.where(el > 0.02, now / np.maximum(el, 0.02), np.nan) - L, 'tg_wall_vs_handicap': num('tg_wall_vs_handicap'), 'tg_remaining_rate': np.where(el < 0.98, (L - now) / np.maximum(1 - el, 0.02), np.nan)}
```

