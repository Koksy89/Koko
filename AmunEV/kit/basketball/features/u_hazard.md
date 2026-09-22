# `u_hazard`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **5** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:91507` — in `laz_featuregen__term_lines()`

```python
'u_hazard': "out['u_hazard'] = _hazard_lin(out, ho, ao)",
```

### `AmunEV_Engine_V2.py:97539` — in `laz_cox__hazard_condition()`

```python
pool['u_hazard'] = lin
```

