# `margin_range_300s`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **4** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:106319` — in `<module>()`

```python
'margin_range_300s':       _f(lambda C: pd.Series(C['sd']).groupby(C['mid']).transform(lambda z: z.rolling(60, min_periods=1).max() - z.rolling(60, min_periods=1).min()).values, 'event', True, 'match_ml', 'margin volatility over the last ~5 minutes (60 ticks at 5s)', 'structure'),
```

