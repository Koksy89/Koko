# `scoring_rate`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **1** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:106402` — in `<module>()`

```python
'scoring_rate':        dict(cls='STATE',      use='BOTH',  kf='elapsed', formula='tot / minutes elapsed', why='LIVE_CERTAINTY', note='= current_rate_per_min'),
```

### `AmunEV_Engine_V2.py:106460` — in `<module>()`

```python
'scoring_rate':        _f(lambda C: np.where(C['elapsed_s'] > 60, C['tot'] / (C['elapsed_s'] / 60.0), np.nan), 'elapsed', False, 'match_total', 'points per minute so far', 'review: STATE'),
```

