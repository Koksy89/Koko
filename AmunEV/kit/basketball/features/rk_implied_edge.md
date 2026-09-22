# `rk_implied_edge`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **7** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:106389` — in `<module>()`

```python
'rk_implied_edge':     dict(cls='STATE',      use='BOTH',  kf='tick',    formula='|1/ho - 1/ao|', why='LIVE_CERTAINTY', note='the implied gap; same family as imbal'),
```

### `AmunEV_Engine_V2.py:106450` — in `<module>()`

```python
'rk_implied_edge':     _f(lambda C: np.abs(C['imp_h'] - C['imp_a']), 'tick', False, 'match_ml', 'the implied gap', 'review: STATE'),
```

