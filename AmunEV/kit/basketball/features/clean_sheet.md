# `clean_sheet`

**Status:** DEFINED_IN_ENGINE · BUILDABLE

Used by **1** strategies.

## Definition in the engine

Quoted verbatim. Reproduce this arithmetic exactly in production.

### `AmunEV_Engine_V2.py:95571` — in `<module>()`

```python
laz_everything__FULL_DERIVED = {'leader_odds_rt': lambda C, M: np.where(C['sd'] > 0, C['ho'], np.where(C['sd'] < 0, C['ao'], np.nan)), 'loser_odds_rt': lambda C, M: np.where(C['sd'] > 0, C['ao'], np.where(C['sd'] < 0, C['ho'], np.nan)), 'leader_price': lambda C, M: np.where(C['sd'] > 0, C['ho'], np.where(C['sd'] < 0, C['ao'], np.nan)), 'trailer_price': lambda C, M: np.where(C['sd'] > 0, C['ao'], np.where(C['sd'] < 0, C['ho'], np.nan)), 'fav_odds_rt': lambda C, M: np.minimum(C['ho'], C['ao']), 'dog_odds_rt': lambda C, M: np.maximum(C['ho'], C['ao']), 'is_favorite': lambda C, M: (np.where(C['sd'] > 0, C['ho'], C['ao']) <= np.where(C['sd'] > 0, C['ao'], C['ho'])).astype(float), 'is_favourite': lambda C, M: (np.where(C['sd'] > 0, C['ho'], C['ao']) <= np.where(C['sd'] > 0, C['ao'], C['ho'])).astype(float), 'lead_is_dog': lambda C, M: (np.where(C['sd'] > 0, C['ho'], C['ao']) > np.where(C['sd'] > 0, C['ao'], C['ho'])).astype(float), 'fb_prog': lambda C, M: C['el'], 'ef_prog': lambda C, M: C['el'], 'tt_prog': lambda C, M: C['el'], 'bb_prog': lambda C, M: C['el'], 'remain': lambda C, M: 1.0 - C['el'], 'time_left': lambda C, M: 1.0 - C['el'], 'q': lambda C, M: np.where(np.isfinite(C['el']), np.clip(np.nan_to_num(C['el'] * 4, nan=0).astype(int) + 1, 1, 4), np.nan).astype(float), 'period': lambda C, M: np.where(np.isfinite(C['el']), np.clip(np.nan_to_num(C['el'] * 4, nan=0).astype(int) + 1, 1, 4), np.nan).astype(float), 'half': lambda C, M: np.where(np.isfinite(C['el']), np.where(C['el'] < 0.5, 1.0, 2.0), np.nan), 'imbal': lambda C, M: np.abs(C['sd']) / np.maximum(C['tot'], 1.0), 'ht_diff': lambda C, M: C['sd'], 'margin_rate': lambda C, M: C['sd'] / np.maximum(C['el'], 0.02), 'score_rate': lambda C, M: C['tot'] / np.maximum(C['el'], 0.02), 'both_scored': lambda C, M: ((C['hs'] > 0) & (C['as_'] > 0)).astype(float), 'clean_sheet': lambda C, M: ((C['hs'] == 0) | (C['as_'] == 0)).astype(float), 'imp_home': lambda C, M: 1.0 / np.where(C['ho'] > 1, C['ho'], np.nan), 'imp_away': lambda C, M: 1.0 / np.where(C['ao'] > 1, C['ao'], np.nan), 'imp_sum': lambda C, M: 1.0 / np.where(C['ho'] > 1, C['ho'], np.nan) + 1.0 / np.where(C['ao'] > 1, C['ao'], np.nan), 'price_gap': lambda C, M: np.abs(C['ho'] - C['ao']), 'price_rank_in_match': lambda C, M: (lambda u: laz_everything___rank_in_match(u, M))(np.where(C['sd'] >= 0, C['ho'], C['ao'])), 'opp_velocity': lambda C, M: laz_everything___diff_in_match(np.where(C['sd'] >= 0, C['ao'], C['ho']), M), 'u_velocity': lambda C, M: laz_everything___diff_in_match(np.where(C['sd'] >= 0, C['ho'], C['ao']), M), 'lead_vs_line': lambda C, M: C['sd'] - C['line'], 'need_vs_expected': lambda C, M: C['line'] - C['tot'] - C['tot'] / np.maximum(C['el'], 0.02) * (1 - C['el']), 'tot_vs_line': lambda C, M: C['tot'] - C['line'], 'line_gap': lambda C, M: np.abs(C['line'] - C['tot'])}
```

