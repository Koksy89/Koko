# `lead_changes_total`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **4** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:106318` — in `<module>()`

```python
'lead_changes_total':      _f(lambda C: pd.Series(C['lead_change'].astype(int)).groupby(C['mid']).cumsum().values, 'event', True, 'match_ml', 'lead changes so far in the match', 'structure'),
```

