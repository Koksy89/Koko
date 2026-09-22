# `prog_period`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **2** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:82433` — in `<module>()`

```python
'prog_period': dict(rt=['phase', 'timestamp'], live="(seconds since this period began) / (the previous period's observed length in this match, else nominal/periods), clipped 0..1",
```

### `AmunEV_Engine_V2.py:91498` — in `laz_featuregen__term_lines()`

```python
'prog_period': "out['prog_period'] = S.period_clock(ph_now, ts_now, _FAMILY, _SPORT_SECONDS, _league_periods(tick))[1]",
```

### `AmunEV_Engine_V2.py:106472` — in `<module>()`

```python
'prog_period':         _f(lambda C: C['prog_period'], 'tick', True, 'match_ml', '% of the CURRENT period elapsed: time since this period began over the previous period\'s observed length in this match (else nominal / periods)', 'observed on this match\'s own ticks; the owner\'s "within X% of the quarter"'),
```

