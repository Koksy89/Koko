# BASKETBALL — STRATEGY SPECIFICATION

Engine `engine_e2e_test`

_This document is a pure function of the engine and the book it validated: the
same run always produces the same bytes. The wall clock lives in MANIFEST.json,
so a diff between two deployments shows only what actually changed._

128 live · 14 registered but not yet settleable · 142 feature columns · 8 PCA composites

## How to read this

Each strategy below is a numbered list of gates in the order the engine applies
them. On every tick, evaluate them in order and stop at the first that is not
true. When all of them hold, and this is the first tick in the match on which
they all hold, the bet is placed at the price named in the price step.

A null value never passes a condition. Every comparison against a null or a NaN
is false, so a missing feature means the strategy does not fire — it never means
the strategy fires on a default.

## The columns every strategy reads

| feature | column on rt_allsports_laz_features | how it is produced |
|---|---|---|
| `X_X_minute__x__u_pace__minus__X_u_elapsed__minus__u_time_in_lead` | `x_x_minute__x__u_pace__minus__x_u_elapsed__minus__u_ti_34d4ce1c` | generated 1:1 from the engine builder |
| `X_X_u_elapsed__minus__u_time_in_lead__minus__X_u_pace__over__u_time_in_lead` | `x_x_u_elapsed__minus__u_time_in_lead__minus__x_u_pace__aec3c7da` | manufactured from its leaves: u_elapsed, u_time_in_lead, u_pace, u_time_in_lead |
| `X_X_u_elapsed__minus__u_time_in_lead__over__X_u_pace__over__u_time_in_lead` | `x_x_u_elapsed__minus__u_time_in_lead__over__x_u_pace___1dd07084` | generated 1:1 from the engine builder |
| `X_a_score__over__home_odds` | `x_a_score__over__home_odds` | generated 1:1 from the engine builder |
| `X_home_odds__x__point_spread_away` | `x_home_odds__x__point_spread_away` | generated 1:1 from the engine builder |
| `X_minute__minus__u_drought` | `x_minute__minus__u_drought` | manufactured from its leaves: minute, u_drought |
| `X_minute__minus__u_pace` | `x_minute__minus__u_pace` | generated 1:1 from the engine builder |
| `X_minute__over__u_pace` | `x_minute__over__u_pace` | generated 1:1 from the engine builder |
| `X_minute__over__u_vol` | `x_minute__over__u_vol` | manufactured from its leaves: minute, u_vol |
| `X_u_elapsed__minus__u_time_in_lead` | `x_u_elapsed__minus__u_time_in_lead` | generated 1:1 from the engine builder |
| `abs_margin_per_remaining_s` | `abs_margin_per_remaining_s` | supplied by the live feature layer |
| `away_odds` | `away_odds` | generated 1:1 from the engine builder |
| `backed_pts_last_60s` | `backed_pts_last_60s` | supplied by the live feature layer |
| `both_scored` | `both_scored` | supplied by the live feature layer |
| `dog_odds_vol_180` | `dog_odds_vol_180` | generated 1:1 from the engine builder |
| `dow` | `dow` | supplied by the live feature layer |
| `drift_ratio` | `drift_ratio` | generated 1:1 from the engine builder |
| `eng_minute_ge_2` | `eng_minute_ge_2` | supplied by the live feature layer |
| `eng_score_tied` | `eng_score_tied` | supplied by the live feature layer |
| `fav_lead_m30` | `fav_lead_m30` | supplied by the live feature layer |
| `fav_odds_trend_60` | `fav_odds_trend_60` | generated 1:1 from the engine builder |
| `h_score` | `h_score` | generated 1:1 from the engine builder |
| `home_pace` | `home_pace` | supplied by the live feature layer |
| `hour` | `hour` | generated 1:1 from the engine builder |
| `imbal` | `imbal` | generated 1:1 from the engine builder |
| `is_big_lead` | `is_big_lead` | generated 1:1 from the engine builder |
| `is_favorite` | `is_favorite` | generated 1:1 from the engine builder |
| `is_trailer` | `is_trailer` | supplied by the live feature layer |
| `jump_loser` | `jump_loser` | generated 1:1 from the engine builder |
| `ld_rank` | `ld_rank` | generated 1:1 from the engine builder |
| `lead_change_last_300s` | `lead_change_last_300s` | supplied by the live feature layer |
| `lead_changes_300s` | `lead_changes_300s` | supplied by the live feature layer |
| `lead_changes_total` | `lead_changes_total` | supplied by the live feature layer |
| `lead_jump` | `lead_jump` | generated 1:1 from the engine builder |
| `lead_m15` | `lead_m15` | supplied by the live feature layer |
| `lead_m30` | `lead_m30` | generated 1:1 from the engine builder |
| `lead_m75` | `lead_m75` | supplied by the live feature layer |
| `lead_odds` | `lead_odds` | generated 1:1 from the engine builder |
| `lead_size` | `lead_size` | generated 1:1 from the engine builder |
| `lead_vs_line` | `lead_vs_line` | supplied by the live feature layer |
| `lead_vs_med` | `lead_vs_med` | generated 1:1 from the engine builder |
| `leader_implied_gap` | `leader_implied_gap` | supplied by the live feature layer |
| `leader_runmax` | `leader_runmax` | supplied by the live feature layer |
| `line` | `line` | supplied by the live feature layer |
| `line_flat` | `line_flat` | supplied by the live feature layer |
| `line_move` | `line_move` | supplied by the live feature layer |
| `line_open` | `line_open` | supplied by the live feature layer |
| `line_vel` | `line_vel` | supplied by the live feature layer |
| `loser_runmax` | `loser_runmax` | supplied by the live feature layer |
| `loser_runmin` | `loser_runmin` | supplied by the live feature layer |
| `margin_per_possession` | `margin_per_possession` | supplied by the live feature layer |
| `margin_range_300s` | `margin_range_300s` | supplied by the live feature layer |
| `margin_rate` | `margin_rate` | supplied by the live feature layer |
| `motif_0` | `motif_0` | supplied by the live feature layer |
| `motif_2` | `motif_2` | supplied by the live feature layer |
| `nchg_120` | `nchg_120` | generated 1:1 from the engine builder |
| `need_frac` | `need_frac` | supplied by the live feature layer |
| `need_vs_expected` | `need_vs_expected` | supplied by the live feature layer |
| `needed_rate_for_over` | `needed_rate_for_over` | generated 1:1 from the engine builder |
| `odds_ratio` | `odds_ratio` | generated 1:1 from the engine builder |
| `open_gap` | `open_gap` | generated 1:1 from the engine builder |
| `open_vs_now_lead` | `open_vs_now_lead` | supplied by the live feature layer |
| `opp` | `opp` | generated 1:1 from the engine builder |
| `opp_drift` | `opp_drift` | generated 1:1 from the engine builder |
| `opp_o` | `opp_o` | generated 1:1 from the engine builder |
| `opp_velocity` | `opp_velocity` | generated 1:1 from the engine builder |
| `overround_now` | `overround_now` | supplied by the live feature layer |
| `pace_last_300s_vs_line` | `pace_last_300s_vs_line` | supplied by the live feature layer |
| `pace_vs_line_pct` | `pace_vs_line_pct` | supplied by the live feature layer |
| `pc_2f7f4d0c` | `pc_2f7f4d0c` | frozen PCA transform — see the PCA section |
| `pc_428f55cf` | `pc_428f55cf` | frozen PCA transform — see the PCA section |
| `pc_51f228a2` | `pc_51f228a2` | frozen PCA transform — see the PCA section |
| `pc_6a99ae09` | `pc_6a99ae09` | frozen PCA transform — see the PCA section |
| `pc_aa16b0a5` | `pc_aa16b0a5` | frozen PCA transform — see the PCA section |
| `pc_dc4196fb` | `pc_dc4196fb` | frozen PCA transform — see the PCA section |
| `pc_f3e46219` | `pc_f3e46219` | frozen PCA transform — see the PCA section |
| `pc_fea3bd2c` | `pc_fea3bd2c` | frozen PCA transform — see the PCA section |
| `pm_away` | `pm_away` | generated 1:1 from the engine builder |
| `pm_home` | `pm_home` | generated 1:1 from the engine builder |
| `pm_ratio` | `pm_ratio` | supplied by the live feature layer |
| `prematch_dog_odds` | `prematch_dog_odds` | generated 1:1 from the engine builder |
| `price_gap` | `price_gap` | generated 1:1 from the engine builder |
| `price_rank_in_match` | `price_rank_in_match` | generated 1:1 from the engine builder |
| `prog` | `prog` | supplied by the live feature layer |
| `prog_q` | `prog_q` | generated 1:1 from the engine builder |
| `prog_score` | `prog_score` | supplied by the live feature layer |
| `prog_x_margin` | `prog_x_margin` | supplied by the live feature layer |
| `pts_last_60s` | `pts_last_60s` | supplied by the live feature layer |
| `px_gap_ratio` | `px_gap_ratio` | generated 1:1 from the engine builder |
| `q` | `q` | generated 1:1 from the engine builder |
| `recovery_x` | `recovery_x` | generated 1:1 from the engine builder |
| `req_ratio` | `req_ratio` | supplied by the live feature layer |
| `retrace_lead` | `retrace_lead` | generated 1:1 from the engine builder |
| `rk_dog_odds` | `rk_dog_odds` | generated 1:1 from the engine builder |
| `rk_fav_odds` | `rk_fav_odds` | generated 1:1 from the engine builder |
| `rk_implied_edge` | `rk_implied_edge` | supplied by the live feature layer |
| `rk_lead_off_high` | `rk_lead_off_high` | supplied by the live feature layer |
| `rk_lead_off_low` | `rk_lead_off_low` | supplied by the live feature layer |
| `rk_odds_ratio` | `rk_odds_ratio` | supplied by the live feature layer |
| `runmin` | `runmin` | generated 1:1 from the engine builder |
| `scores_300s` | `scores_300s` | supplied by the live feature layer |
| `secs_since_price` | `secs_since_price` | generated 1:1 from the engine builder |
| `secs_since_quarter_start` | `secs_since_quarter_start` | supplied by the live feature layer |
| `secs_since_run_started` | `secs_since_run_started` | supplied by the live feature layer |
| `secs_since_score` | `secs_since_score` | supplied by the live feature layer |
| `spread_cover_now` | `spread_cover_now` | supplied by the live feature layer |
| `team_pace_gap` | `team_pace_gap` | supplied by the live feature layer |
| `ten_mins` | `ten_mins` | generated 1:1 from the engine builder |
| `tg_imp_over` | `tg_imp_over` | generated 1:1 from the engine builder |
| `tg_imp_under` | `tg_imp_under` | generated 1:1 from the engine builder |
| `tg_overround` | `tg_overround` | supplied by the live feature layer |
| `tg_proj_vs_line` | `tg_proj_vs_line` | supplied by the live feature layer |
| `tg_px_ratio` | `tg_px_ratio` | supplied by the live feature layer |
| `time_at_this_price` | `time_at_this_price` | supplied by the live feature layer |
| `time_left` | `time_left` | supplied by the live feature layer |
| `tot_vs_line` | `tot_vs_line` | supplied by the live feature layer |
| `total_goals_handicap_ffill` | `total_goals_handicap_ffill` | supplied by the live feature layer |
| `total_line_move` | `total_line_move` | supplied by the live feature layer |
| `total_points_handicap` | `total_points_handicap` | generated 1:1 from the engine builder |
| `total_points_over` | `total_points_over` | generated 1:1 from the engine builder |
| `total_points_under` | `total_points_under` | generated 1:1 from the engine builder |
| `total_pts` | `total_pts` | generated 1:1 from the engine builder |
| `trail` | `trail` | supplied by the live feature layer |
| `trailer_drift` | `trailer_drift` | supplied by the live feature layer |
| `trailer_price` | `trailer_price` | supplied by the live feature layer |
| `u_drift_opp` | `u_drift_opp` | generated 1:1 from the engine builder |
| `u_drought` | `u_drought` | supplied by the live feature layer |
| `u_hazard` | `u_hazard` | generated 1:1 from the engine builder |
| `u_jump` | `u_jump` | supplied by the live feature layer |
| `u_margin_abs` | `u_margin_abs` | generated 1:1 from the engine builder |
| `u_open` | `u_open` | generated 1:1 from the engine builder |
| `u_overround` | `u_overround` | generated 1:1 from the engine builder |
| `u_pace` | `u_pace` | generated 1:1 from the engine builder |
| `u_price` | `u_price` | supplied by the live feature layer |
| `u_price_open` | `u_price_open` | generated 1:1 from the engine builder |
| `u_ratio_open` | `u_ratio_open` | supplied by the live feature layer |
| `u_ratio_shift` | `u_ratio_shift` | generated 1:1 from the engine builder |
| `u_trend` | `u_trend` | supplied by the live feature layer |
| `u_vel_t` | `u_vel_t` | supplied by the live feature layer |
| `u_vf` | `u_vf` | generated 1:1 from the engine builder |
| `u_vol` | `u_vol` | supplied by the live feature layer |
| `wall_min` | `wall_min` | supplied by the live feature layer |

## The PCA composites

Each is a FROZEN linear map fitted once on this run's in-sample rows and
never refitted. `laz_pca_features_basketball.py` computes them; the numbers
below are the whole definition.

### `pc_2f7f4d0c`

Inputs, in this exact order: `line_move`, `line_open`, `wall_min`

```
pc = ((x - mu) / sdv) . w    as float32
mu  = [1.0, 2.0, 3.0]
sdv = [0.5, 1.5, 2.5]
w   = [0.6, -0.5, 0.62]
```

If ANY input is missing or non-finite on a tick, the composite is NULL —
never 0. The fit dropped exactly those rows, so a 0 would be a value the
engine never saw.

### `pc_428f55cf`

Inputs, in this exact order: `line_move`, `line_open`, `wall_min`

```
pc = ((x - mu) / sdv) . w    as float32
mu  = [1.0, 2.0, 3.0]
sdv = [0.5, 1.5, 2.5]
w   = [0.6, -0.5, 0.62]
```

If ANY input is missing or non-finite on a tick, the composite is NULL —
never 0. The fit dropped exactly those rows, so a 0 would be a value the
engine never saw.

### `pc_51f228a2`

Inputs, in this exact order: `line_move`, `line_open`, `wall_min`

```
pc = ((x - mu) / sdv) . w    as float32
mu  = [1.0, 2.0, 3.0]
sdv = [0.5, 1.5, 2.5]
w   = [0.6, -0.5, 0.62]
```

If ANY input is missing or non-finite on a tick, the composite is NULL —
never 0. The fit dropped exactly those rows, so a 0 would be a value the
engine never saw.

### `pc_6a99ae09`

Inputs, in this exact order: `line_move`, `line_open`, `wall_min`

```
pc = ((x - mu) / sdv) . w    as float32
mu  = [1.0, 2.0, 3.0]
sdv = [0.5, 1.5, 2.5]
w   = [0.6, -0.5, 0.62]
```

If ANY input is missing or non-finite on a tick, the composite is NULL —
never 0. The fit dropped exactly those rows, so a 0 would be a value the
engine never saw.

### `pc_aa16b0a5`

Inputs, in this exact order: `line_move`, `line_open`, `wall_min`

```
pc = ((x - mu) / sdv) . w    as float32
mu  = [1.0, 2.0, 3.0]
sdv = [0.5, 1.5, 2.5]
w   = [0.6, -0.5, 0.62]
```

If ANY input is missing or non-finite on a tick, the composite is NULL —
never 0. The fit dropped exactly those rows, so a 0 would be a value the
engine never saw.

### `pc_dc4196fb`

Inputs, in this exact order: `line_move`, `line_open`, `wall_min`

```
pc = ((x - mu) / sdv) . w    as float32
mu  = [1.0, 2.0, 3.0]
sdv = [0.5, 1.5, 2.5]
w   = [0.6, -0.5, 0.62]
```

If ANY input is missing or non-finite on a tick, the composite is NULL —
never 0. The fit dropped exactly those rows, so a 0 would be a value the
engine never saw.

### `pc_f3e46219`

Inputs, in this exact order: `line_move`, `line_open`, `wall_min`

```
pc = ((x - mu) / sdv) . w    as float32
mu  = [1.0, 2.0, 3.0]
sdv = [0.5, 1.5, 2.5]
w   = [0.6, -0.5, 0.62]
```

If ANY input is missing or non-finite on a tick, the composite is NULL —
never 0. The fit dropped exactly those rows, so a 0 would be a value the
engine never saw.

### `pc_fea3bd2c`

Inputs, in this exact order: `line_move`, `line_open`, `wall_min`

```
pc = ((x - mu) / sdv) . w    as float32
mu  = [1.0, 2.0, 3.0]
sdv = [0.5, 1.5, 2.5]
w   = [0.6, -0.5, 0.62]
```

If ANY input is missing or non-finite on a tick, the composite is NULL —
never 0. The fit dropped exactly those rows, so a 0 would be a value the
engine never saw.

## Live strategies

### BAS_spec:HT_Unde_0017

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `lead_vs_line <= -160.315`
6. `open_gap <= 2.2797`
7. `ten_mins <= 23.4333`
8. first tick in the match on which all of the above hold

### BAS_prop:first_s_0049

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `line_move <= 4`
7. `price_gap <= 1.89`
8. `X_minute__minus__u_drought <= 27.3487`
9. `tg_overround <= 1.1237`
10. `tg_imp_over <= 0.605`
11. first tick in the match on which all of the above hold

> This strategy reads `minute`, which the bundle supplies in `laz_features_basketball.py`.

### LZ_V270_BASK_022

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `lead_vs_med >= 1.0267`
6. `lead_size >= 7`
7. `lead_odds >= 1.5`
8. first tick in the match on which all of the above hold

### BAS_prop:drifter_0027

**LIVE** · market `match_winner` · backs `drifted` · price band `1.4` to `None` · tier Feeder

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `drifted`
4. the backed price is inside `1.4`–`None`
5. `time_at_this_price <= 28.2017`
6. `lead_vs_line <= -170.833`
7. `X_home_odds__x__point_spread_away <= 10.82`
8. `u_vol >= 0.0119`
9. first tick in the match on which all of the above hold

> This strategy reads `point_spread_away`, which the bundle supplies in `laz_features_basketball.py`.

### LZ_V270_BASK_015

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `lead_size >= 7`
6. `drift_ratio >= 0.6632`
7. `lead_odds >= 1.5`
8. first tick in the match on which all of the above hold

### BAS_late_lead_ho_0169

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `need_frac >= 0.4`
7. `open_vs_now_lead <= 0.9043`
8. `motif_0 <= 0`
9. `X_minute__minus__u_drought <= 26.2769`
10. `tg_overround <= 1.1237`
11. first tick in the match on which all of the above hold

> This strategy reads `minute`, which the bundle supplies in `laz_features_basketball.py`.

### BAS_prop:first_s_0062

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `line_move <= 4`
7. `lead_vs_line <= -143`
8. `u_ratio_open <= 2.9167`
9. `tg_overround <= 1.1237`
10. first tick in the match on which all of the above hold

### BAS_tg_under_0005

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `prog_q >= 0.5`
6. `u_open >= 1.2`
7. `is_trailer <= 0.8231`
8. first tick in the match on which all of the above hold

### BAS_late_lead_ho_0110

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `line_move <= 4`
7. `odds_ratio <= 1.227`
8. `trailer_price >= 1.8518`
9. `total_points_over <= 1.9442`
10. `lead_changes_300s <= 1`
11. first tick in the match on which all of the above hold

### BAS_prop:late_le_0181

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `lead_size >= 3`
6. `u_ratio_open <= 2.8107`
7. `open_gap <= 3.55`
8. first tick in the match on which all of the above hold

### BAS_prop:first_s_0056

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `line_move <= 4`
7. `price_gap <= 1.89`
8. `opp_drift <= 1.0811`
9. `tg_overround <= 1.1237`
10. first tick in the match on which all of the above hold

### BAS_lead_ml_0081

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `line_move <= 4`
7. `trailer_price >= 1.92`
8. `pc_f3e46219 <= 2.3868`
9. first tick in the match on which all of the above hold

### BAS_prop:first_s_0060

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `lead_vs_line <= -150.5`
7. `u_ratio_open <= 2.5724`
8. `tg_overround <= 1.1237`
9. first tick in the match on which all of the above hold

### BAS_DRIFTED_0046

**LIVE** · market `match_winner` · backs `drifted` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `drifted`
4. the backed price is inside `1.4`–`None`
5. `line >= 170`
6. `pm_home <= 2.6314`
7. first tick in the match on which all of the above hold

### BAS_spec:TG_Hist_0072

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `prog >= 0.71510175543680066`
6. `time_at_this_price < 52.915909090909054`
7. `pace_last_300s_vs_line >= -159.72`
8. first tick in the match on which all of the above hold

### BAS_prop:first_s_0059

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `u_overround <= 1.1109`
6. `req_ratio <= 1.3805`
7. first tick in the match on which all of the above hold

### BAS_tg_under_0009

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `wall_min <= 171.805`
6. `lead_m15 <= 1`
7. `pc_51f228a2 <= -0.1057`
8. `secs_since_price <= 0`
9. first tick in the match on which all of the above hold

### BAS_prop:first_s_0054

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `prog_score <= 0.35`
7. `rk_odds_ratio <= 2.3716`
8. `leader_runmax >= 1.8456`
9. `tot_vs_line <= -128.8692`
10. first tick in the match on which all of the above hold

### BAS_late_lead_ho_0007

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `line_move <= 4`
7. `price_gap <= 1.89`
8. `opp_drift <= 1.0811`
9. `trail <= 6`
10. first tick in the match on which all of the above hold

### BAS_prop:first_s_0055

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `lead_vs_line <= -140.5`
7. `req_ratio >= 1.195`
8. `tg_overround <= 1.1237`
9. first tick in the match on which all of the above hold

### BAS_prop:first_s_0098

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `rk_dog_odds >= 1.8351`
7. `opp >= 1.91`
8. first tick in the match on which all of the above hold

### BAS_prop:drifter_0029

**LIVE** · market `match_winner` · backs `drifted` · price band `1.4` to `None` · tier Feeder

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `drifted`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `pm_away <= 2.92`
7. `leader_runmax <= 3.66`
8. `secs_since_run_started >= 0`
9. `u_trend <= -5.7851`
10. `total_goals_handicap_ffill <= 197.5`
11. `team_pace_gap <= 13.3333`
12. first tick in the match on which all of the above hold

### BAS_prop:drifter_0026

**LIVE** · market `match_winner` · backs `drifted` · price band `1.4` to `None` · tier Ino

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `drifted`
4. the backed price is inside `1.4`–`None`
5. `line >= 170`
6. `X_minute__over__u_vol <= 2696.29`
7. `both_scored >= 1`
8. first tick in the match on which all of the above hold

> This strategy reads `minute`, which the bundle supplies in `laz_features_basketball.py`.

### BAS_late_lead_ho_0115

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `u_ratio_open <= 3.0575`
7. `trailer_price >= 1.8315`
8. `prematch_dog_odds >= 1.9465`
9. first tick in the match on which all of the above hold

### BAS_late_lead_ho_0103

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `dog_odds_vol_180 <= 0.15546`
6. `price_gap >= 0.04`
7. `lead_changes_300s <= 2`
8. `line_open >= 134.5`
9. first tick in the match on which all of the above hold

### BAS_spec:TG_Hist_0071

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `prog_score >= 0.45`
6. `u_drift_opp <= 11.7818`
7. first tick in the match on which all of the above hold

### BAS_prop:garbage_0127

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `lead_m15 <= 0`
6. `u_overround >= 1.0508`
7. first tick in the match on which all of the above hold

### BAS_tg_under_lag_0135

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `need_frac >= 0.4`
7. `dog_odds_vol_180 >= 0.0469`
8. `u_ratio_shift >= 0.395`
9. first tick in the match on which all of the above hold

### BAS_prop:first_s_0089

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `u_ratio_open <= 3.0575`
7. `trailer_price >= 1.8315`
8. `pc_f3e46219 <= 2.0943`
9. `lead_changes_300s <= 1`
10. first tick in the match on which all of the above hold

### BAS_prop:first_s_0047

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `prog_score <= 0.35`
7. `rk_odds_ratio <= 2.3716`
8. `X_minute__minus__u_drought <= 28.0923`
9. `tg_overround <= 1.1237`
10. first tick in the match on which all of the above hold

> This strategy reads `minute`, which the bundle supplies in `laz_features_basketball.py`.

### BAS_prop:first_s_0097

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `dog_odds_vol_180 <= 0.15546`
6. `rk_dog_odds >= 1.85`
7. `pc_f3e46219 <= 2.0943`
8. `lead_changes_300s <= 1`
9. first tick in the match on which all of the above hold

### BAS_prop:first_s_0091

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `total_points_under >= 1.75`
6. `hour >= 2`
7. `away_odds >= 1.159999966621399`
8. `tot_vs_line <= -129.823`
9. `opp >= 1.53`
10. first tick in the match on which all of the above hold

### BAS_prop:held_le_0182

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `rk_fav_odds >= 1.23`
6. `u_ratio_open <= 2.842`
7. first tick in the match on which all of the above hold

### BAS_dog_leading_0148

**LIVE** · market `match_winner` · backs `dog_leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `dog_leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `need_frac >= 0.4`
7. `open_vs_now_lead <= 0.9043`
8. `motif_0 <= 0`
9. `pc_f3e46219 >= -0.4756`
10. first tick in the match on which all of the above hold

### BAS_spec:Loser_1_0023

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Ino

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `time_at_this_price <= 28.2017`
6. `lead_vs_line <= -170.833`
7. `total_line_move >= -4`
8. `lead_m30 <= 1`
9. first tick in the match on which all of the above hold

### BAS_tg_under_lag_0136

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `rk_lead_off_high >= 0.2333`
7. `lead_m15 <= 1`
8. `line_vel <= 1`
9. first tick in the match on which all of the above hold

### BAS_lead_ml_0144

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `need_frac >= 0.4`
7. `open_vs_now_lead <= 0.9043`
8. `motif_0 <= 0`
9. `X_minute__minus__u_drought <= 26.2769`
10. `req_ratio >= 1.1951`
11. `tg_overround <= 1.1237`
12. first tick in the match on which all of the above hold

> This strategy reads `minute`, which the bundle supplies in `laz_features_basketball.py`.

### BAS_spec:TG_Hist_0014

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `line_move <= 4`
7. `price_gap <= 1.89`
8. `opp_drift <= 1.0811`
9. `need_vs_expected <= 168.5`
10. first tick in the match on which all of the above hold

### BAS_tg_under_lag_0035

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `time_at_this_price <= 28.2017`
6. `u_vf <= 0.8899`
7. `ld_rank >= 0.625`
8. `is_big_lead >= 0.2821`
9. first tick in the match on which all of the above hold

### BAS_lead_ml_0082

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `rk_implied_edge >= 0.107`
7. `u_drought <= 71.4786`
8. first tick in the match on which all of the above hold

### BAS_tg_under_0068

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `prog >= 0.71510175543680066`
6. `time_at_this_price < 52.915909090909054`
7. `X_X_u_elapsed__minus__u_time_in_lead__over__X_u_pace__over__u_time_in_lead <= 0.0249`
8. first tick in the match on which all of the above hold

> This strategy reads `u_elapsed`, `u_time_in_lead`, which the bundle supplies in `laz_features_basketball.py`.

### BAS_tg_under_0003

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `line_move <= 4`
7. `price_gap <= 1.89`
8. `opp_drift <= 1.0811`
9. `tot_vs_line >= -174.5`
10. first tick in the match on which all of the above hold

### BAS_tg_under_lag_0132

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `need_frac >= 0.4`
7. `trailer_price >= 1.8471`
8. `pace_vs_line_pct >= -0.9852`
9. `eng_minute_ge_2 >= 1`
10. first tick in the match on which all of the above hold

### BAS_late_lead_ho_0166

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `prog_score <= 0.35`
7. `rk_odds_ratio <= 2.3716`
8. `X_minute__minus__u_drought <= 28.0923`
9. `lead_changes_300s <= 2`
10. `tg_overround <= 1.1237`
11. first tick in the match on which all of the above hold

> This strategy reads `minute`, which the bundle supplies in `laz_features_basketball.py`.

### BAS_tg_under_0012

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `u_jump >= 0.9449`
6. `total_points_under <= 1.8`
7. first tick in the match on which all of the above hold

### BAS_spec:Leader_0077

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `u_price <= 1.7584`
7. `overround_now <= 1.1135`
8. first tick in the match on which all of the above hold

### BAS_spec:TG_Hist_0018

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `wall_min <= 171.805`
6. `lead_m15 <= 1`
7. `tg_imp_under >= 0.5435`
8. `secs_since_price <= 0`
9. first tick in the match on which all of the above hold

### BAS_spec:TG_Hist_0069

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `rk_odds_ratio <= 2.6602`
6. `lead_vs_line >= -173.8462`
7. first tick in the match on which all of the above hold

### BAS_spec:HT_Unde_0122

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `rk_dog_odds >= 1.8351`
7. `lead_changes_300s <= 2`
8. first tick in the match on which all of the above hold

### BAS_tg_under_0001

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `rk_odds_ratio <= 2.6602`
6. `prog_q >= 0.4444`
7. `tg_proj_vs_line >= -167.5197`
8. first tick in the match on which all of the above hold

### BAS_prop:first_s_0052

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `prog_score <= 0.35`
7. `rk_odds_ratio <= 2.3716`
8. `total_line_move >= -4`
9. first tick in the match on which all of the above hold

### BAS_late_lead_ho_0114

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `dog_odds_vol_180 <= 0.15546`
6. `u_pace <= 58.0667`
7. `needed_rate_for_over >= 2.9473`
8. first tick in the match on which all of the above hold

### BAS_prop:first_s_0058

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `lead_vs_line <= -151.823`
7. `opp_o <= 4.17`
8. `px_gap_ratio <= 1.442`
9. first tick in the match on which all of the above hold

### BAS_prop:first_s_0051

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `lead_vs_line <= -140.5`
7. `secs_since_quarter_start <= 41.6871`
8. `rk_fav_odds >= 1.2738`
9. first tick in the match on which all of the above hold

### BAS_late_lead_ho_0111

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `rk_fav_odds <= 1.75`
7. `u_overround <= 1.113`
8. first tick in the match on which all of the above hold

### BAS_prop:first_s_0096

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `odds_ratio <= 1.2094`
7. `pts_last_60s <= 6`
8. `lead_m15 <= 1`
9. `rk_odds_ratio >= 1.0318`
10. first tick in the match on which all of the above hold

### BAS_spec:TG_Hist_0070

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `price_rank_in_match >= 0.36363651141645542`
6. `secs_since_score < 97`
7. `X_minute__over__u_pace <= 1.5057`
8. first tick in the match on which all of the above hold

> This strategy reads `minute`, which the bundle supplies in `laz_features_basketball.py`.

### BAS_late_lead_ho_0116

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `prog_score <= 0.35`
7. `rk_odds_ratio <= 2.3716`
8. `rk_dog_odds >= 1.84`
9. `pc_2f7f4d0c <= 0.6721`
10. `lead_changes_300s <= 2`
11. `u_drought <= 3213.791318359375`
12. first tick in the match on which all of the above hold

### BAS_spec:HT_Unde_0123

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `total_points_under >= 1.75`
6. `pm_away <= 2.85`
7. `X_X_u_elapsed__minus__u_time_in_lead__minus__X_u_pace__over__u_time_in_lead <= -19.1179`
8. `u_vel_t <= 0.0198`
9. first tick in the match on which all of the above hold

> This strategy reads `u_elapsed`, `u_time_in_lead`, which the bundle supplies in `laz_features_basketball.py`.

### BAS_prop:first_s_0053

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `need_frac >= 0.4`
7. `prog_score <= 0.1318`
8. `px_gap_ratio <= 1.4925`
9. first tick in the match on which all of the above hold

### BAS_tg_under_0004

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `line_move <= 4`
7. `price_gap <= 1.89`
8. `opp_drift <= 1.0811`
9. `trail <= 6`
10. `pace_last_300s_vs_line >= -185.8205`
11. `prog_q >= 0.4444`
12. first tick in the match on which all of the above hold

### BAS_tg_under_lag_0037

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `line_move <= 4`
7. `price_gap <= 1.89`
8. `tg_proj_vs_line >= -168.5`
9. first tick in the match on which all of the above hold

### BAS_prop:late_le_0157

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `dog_odds_vol_180 <= 0.15546`
6. `price_gap >= 0.04`
7. `lead_changes_300s <= 2`
8. `line_flat >= 1`
9. first tick in the match on which all of the above hold

### BAS_late_lead_ho_0102

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `pm_away <= 2.92`
7. `leader_runmax <= 3.66`
8. `lead_vs_med <= 1.175`
9. `rk_dog_odds >= 1.83`
10. `u_trend <= -2.6556`
11. first tick in the match on which all of the above hold

### BAS_late_lead_ho_0105

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `need_frac >= 0.4`
7. `trailer_price >= 1.8471`
8. `pc_2f7f4d0c <= 0.6917`
9. first tick in the match on which all of the above hold

### BAS_spec:TG_Hist_0017

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `line_move <= 4`
7. `price_gap <= 1.89`
8. `tg_proj_vs_line >= -168.1895`
9. first tick in the match on which all of the above hold

### BAS_tg_under_lag_0134

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `total_points_under >= 1.75`
6. `hour >= 2`
7. `away_odds >= 1.159999966621399`
8. `prog >= 0.25`
9. `recovery_x >= 1`
10. `pace_last_300s_vs_line >= -156.5908`
11. first tick in the match on which all of the above hold

### BAS_late_lead_ho_0101

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `need_frac >= 0.4`
7. `price_gap >= 0.2`
8. `jump_loser >= 0.9075`
9. first tick in the match on which all of the above hold

### BAS_late_lead_ho_0112

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `pm_ratio <= 3.6843`
6. `imbal >= 0.0164`
7. `line_flat >= 0.8667`
8. `trailer_drift >= 1.0222`
9. first tick in the match on which all of the above hold

### BAS_spec:HT_Unde_0124

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `imbal >= 0.0217`
6. `pc_6a99ae09 >= -0.6204`
7. first tick in the match on which all of the above hold

### BAS_tg_under_lag_0031

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `time_at_this_price <= 28.2017`
6. `u_vf <= 0.8899`
7. `abs_margin_per_remaining_s >= 0.0004`
8. `lead_vs_line >= -180.341`
9. first tick in the match on which all of the above hold

### BAS_pm_underdog_0044

**LIVE** · market `match_winner` · backs `pm_underdog` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `pm_underdog`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 170`
6. `odds_ratio <= 2.4922`
7. `pc_fea3bd2c >= -0.66349512338638306`
8. first tick in the match on which all of the above hold

### BAS_dog_leading_0147

**LIVE** · market `match_winner` · backs `dog_leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `dog_leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `prog_score <= 0.35`
7. `X_X_minute__x__u_pace__minus__X_u_elapsed__minus__u_time_in_lead <= 169.724`
8. `tg_overround <= 1.1237`
9. first tick in the match on which all of the above hold

> This strategy reads `minute`, `u_elapsed`, `u_time_in_lead`, which the bundle supplies in `laz_features_basketball.py`.

### BAS_spec:Leader_0079

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `dog_odds_vol_180 <= 0.1733`
7. `req_ratio <= 1.3177`
8. first tick in the match on which all of the above hold

### BAS_tg_under_0006

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `u_hazard <= -0.1409`
6. `needed_rate_for_over <= 3.6355`
7. `prog_q >= 0.0456`
8. first tick in the match on which all of the above hold

### BAS_prop:shorten_0163

**LIVE** · market `match_winner` · backs `shortened` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `shortened`
4. the backed price is inside `1.4`–`None`
5. `pm_ratio <= 3.6843`
6. `imbal >= 0.0164`
7. `u_jump >= 0.9459`
8. first tick in the match on which all of the above hold

### BAS_late_lead_ho_0104

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `prog_score <= 0.35`
7. `h_score <= 16.1205`
8. `u_hazard <= -0.1356`
9. `opp_velocity >= -0.0842`
10. first tick in the match on which all of the above hold

### BAS_lead_ml_0085

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `dog_odds_vol_180 <= 0.15546`
6. `X_minute__over__u_vol <= 353.579`
7. `home_pace <= 23.5316`
8. first tick in the match on which all of the above hold

> This strategy reads `minute`, which the bundle supplies in `laz_features_basketball.py`.

### BAS_lead_ml_0086

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `nchg_120 <= 2`
6. `retrace_lead <= 0.3623`
7. `dow >= 2`
8. first tick in the match on which all of the above hold

### BAS_prop:first_s_0090

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `need_frac >= 0.4`
7. `trailer_price >= 1.8471`
8. `pc_f3e46219 <= 2.3571`
9. first tick in the match on which all of the above hold

### BAS_prop:garbage_0128

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `total_line_move >= -5.8769`
6. `total_line_move >= 0`
7. first tick in the match on which all of the above hold

### BAS_tg_under_0067

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `rk_odds_ratio <= 2.6602`
6. `u_drift_opp >= 0.7584`
7. `motif_2 <= 0`
8. `prog_x_margin >= -2.5026`
9. `X_u_elapsed__minus__u_time_in_lead >= -0.2546`
10. first tick in the match on which all of the above hold

> This strategy reads `u_elapsed`, `u_time_in_lead`, which the bundle supplies in `laz_features_basketball.py`.

### BAS_prop:held_le_0170

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `rk_implied_edge >= 0.1704`
6. `pc_428f55cf <= 2.0819`
7. first tick in the match on which all of the above hold

### LZ_V270_BASK_016

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `lead_odds <= 2.25`
6. `lead_size >= 6`
7. `lead_odds >= 1.5`
8. first tick in the match on which all of the above hold

### BAS_late_lead_ho_0168

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `trailer_price >= 1.34`
6. `retrace_lead <= 0.0439`
7. first tick in the match on which all of the above hold

### BAS_prop:first_s_0065

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `need_frac >= 0.4`
7. `prog_score <= 0.35`
8. `rk_odds_ratio <= 2.8601`
9. `margin_per_possession <= 0.0166`
10. `tg_overround <= 1.1236`
11. first tick in the match on which all of the above hold

### BAS_prop:dog_lea_0151

**LIVE** · market `match_winner` · backs `dog_leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `dog_leader`
4. the backed price is inside `1.4`–`None`
5. `u_overround <= 1.1109`
6. `u_ratio_open <= 2.8919`
7. `tg_imp_over <= 0.6235`
8. first tick in the match on which all of the above hold

### BAS_prop:first_s_0064

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `need_frac >= 0.4`
7. `prog_score <= 0.35`
8. `rk_odds_ratio <= 2.8601`
9. `wall_min >= 138.5`
10. first tick in the match on which all of the above hold

### BAS_spec:Leader_0078

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `loser_runmax >= 1.8722`
7. `dow >= 1.041`
8. first tick in the match on which all of the above hold

### BAS_prop:drifter_0028

**LIVE** · market `match_winner` · backs `drifted` · price band `1.4` to `None` · tier Feeder

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `drifted`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `pm_away <= 2.92`
7. `leader_runmax <= 3.66`
8. `secs_since_run_started >= 0`
9. `fav_lead_m30 >= -1`
10. `u_trend <= -6.5658`
11. first tick in the match on which all of the above hold

### BAS_tg_under_0002

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `X_a_score__over__home_odds >= 0`
6. `lead_m75 <= 1`
7. first tick in the match on which all of the above hold

### BAS_prop:first_s_0093

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `q <= 1`
6. `lead_m30 <= 1`
7. `opp >= 1.5927`
8. first tick in the match on which all of the above hold

### BAS_tg_under_0008

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `time_at_this_price <= 28.2017`
6. `price_rank_in_match >= 0.3333`
7. `tg_imp_under >= 0.5682`
8. first tick in the match on which all of the above hold

### LZ_V270_BASK_021

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `total_pts >= 122`
6. `runmin >= 1.129`
7. `lead_odds >= 1.5`
8. first tick in the match on which all of the above hold

### BAS_DRIFTED_0121

**LIVE** · market `match_winner` · backs `drifted` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `drifted`
4. the backed price is inside `1.4`–`None`
5. `rk_lead_off_low <= 2.1135`
6. `imbal >= 0.0217`
7. `u_ratio_open >= 0.6317`
8. first tick in the match on which all of the above hold

### BAS_prop:late_le_0158

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `dog_odds_vol_180 <= 0.15546`
6. `margin_range_300s >= 4`
7. `ten_mins <= 23.4436`
8. first tick in the match on which all of the above hold

### BAS_prop:shorten_0152

**LIVE** · market `match_winner` · backs `shortened` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `shortened`
4. the backed price is inside `1.4`–`None`
5. `lead_size >= 3`
6. `u_ratio_open <= 2.8107`
7. `scores_300s <= 4`
8. `fav_odds_trend_60 <= 0.2231`
9. `ten_mins <= 23.0897`
10. first tick in the match on which all of the above hold

### BAS_prop:first_s_0095

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `total_points_under >= 1.75`
6. `hour >= 2`
7. `away_odds >= 1.159999966621399`
8. `total_points_over < 1.899999976158142`
9. `tot_vs_line <= -112.908`
10. `rk_odds_ratio >= 1.0867`
11. first tick in the match on which all of the above hold

### BAS_DRIFTED_0045

**LIVE** · market `match_winner` · backs `drifted` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `drifted`
4. the backed price is inside `1.4`–`None`
5. `lead_vs_line <= -160.315`
6. `open_gap <= 2.2797`
7. `pm_away >= 1.4`
8. first tick in the match on which all of the above hold

### BAS_prop:fresh_l_0183

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `lead_vs_med <= 1.0759`
6. `margin_range_300s <= 5`
7. first tick in the match on which all of the above hold

### BAS_dog_leading_0146

**LIVE** · market `match_winner` · backs `dog_leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `dog_leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `need_frac >= 0.4`
7. `open_vs_now_lead <= 0.9043`
8. `motif_0 <= 0`
9. `X_minute__minus__u_drought <= 26.2769`
10. `tg_overround <= 1.1172`
11. first tick in the match on which all of the above hold

> This strategy reads `minute`, which the bundle supplies in `laz_features_basketball.py`.

### BAS_lead_ml_0087

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `rk_fav_odds <= 1.75`
7. `margin_rate <= 6.6917`
8. `pc_428f55cf <= 2.0819`
9. first tick in the match on which all of the above hold

### BAS_prop:first_s_0094

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `rk_fav_odds <= 1.75`
7. `leader_implied_gap >= -0.1701`
8. `tg_px_ratio <= 1.1098`
9. first tick in the match on which all of the above hold

### BAS_spec:TG_Hist_0016

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `time_at_this_price <= 28.2017`
6. `u_vf <= 0.8899`
7. `ld_rank >= 0.625`
8. `u_margin_abs >= 2`
9. `total_points_under <= 1.8388`
10. first tick in the match on which all of the above hold

### BAS_spec:HT_Unde_0042

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `need_frac >= 0.4`
7. `prog_score <= 0.35`
8. `rk_odds_ratio <= 2.8601`
9. `margin_per_possession <= 0.0166`
10. `u_price_open >= 1.34`
11. `X_minute__minus__u_pace >= -18`
12. `needed_rate_for_over >= 3.3176`
13. first tick in the match on which all of the above hold

> This strategy reads `minute`, which the bundle supplies in `laz_features_basketball.py`.

### BAS_spec:Leader_0076

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `line_move <= 4`
7. `odds_ratio <= 1.227`
8. `lead_changes_total <= 2`
9. `lead_vs_line <= -145.692`
10. `pc_f3e46219 <= 1.9746`
11. first tick in the match on which all of the above hold

### BAS_dog_leading_0141

**LIVE** · market `match_winner` · backs `dog_leader` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `dog_leader`
4. the backed price is inside `1.4`–`None`
5. `dog_odds_vol_180 <= 0.15546`
6. `price_gap >= 0.04`
7. `lead_changes_300s <= 2`
8. first tick in the match on which all of the above hold

### BAS_tg_under_0007

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `time_at_this_price <= 28.2017`
6. `u_vf <= 0.8899`
7. `ld_rank >= 0.625`
8. `is_big_lead >= 1`
9. `tg_imp_under >= 0.5462`
10. first tick in the match on which all of the above hold

### BAS_lead_ml_0080

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `dog_odds_vol_180 <= 0.15546`
6. `u_pace <= 58.0667`
7. `home_pace >= 2`
8. first tick in the match on which all of the above hold

### BAS_tg_under_lag_0038

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `u_ratio_open <= 3.0575`
7. `u_drought <= 1241.51`
8. `trailer_drift <= 1.0594`
9. first tick in the match on which all of the above hold

### BAS_dog_leading_0150

**LIVE** · market `match_winner` · backs `dog_leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `dog_leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `lead_vs_line <= -151.823`
7. `opp_o <= 4.17`
8. `tg_overround <= 1.1237`
9. `time_left <= 0.8889`
10. first tick in the match on which all of the above hold

### BAS_prop:first_s_0099

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `prog_score <= 0.35`
7. `h_score <= 16.1205`
8. `is_favorite >= 1`
9. first tick in the match on which all of the above hold

### BAS_spec:Leader_0075

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `prog_score <= 0.35`
7. `rk_dog_odds >= 1.8392`
8. `X_u_elapsed__minus__u_time_in_lead <= 1.0763`
9. `total_points_handicap >= 150.4538`
10. first tick in the match on which all of the above hold

> This strategy reads `u_elapsed`, `u_time_in_lead`, which the bundle supplies in `laz_features_basketball.py`.

### BAS_prop:held_le_0172

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `loser_runmin >= 1.2769`
7. `overround_now <= 1.121`
8. first tick in the match on which all of the above hold

### BAS_late_lead_ho_0113

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `lead_vs_line <= -151.823`
7. `opp_o <= 4.17`
8. `pace_last_300s_vs_line >= -171.0969`
9. first tick in the match on which all of the above hold

### BAS_dog_leading_0149

**LIVE** · market `match_winner` · backs `dog_leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `dog_leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `pace_last_300s_vs_line <= -115.005`
7. `tg_overround <= 1.1237`
8. first tick in the match on which all of the above hold

### BAS_prop:garbage_0057

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `odds_ratio >= 0.0833`
6. first tick in the match on which all of the above hold

### BAS_spec:Early_H_0088

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `lead_m15 <= 0`
6. `backed_pts_last_60s <= 4.4718`
7. `pc_dc4196fb <= -2.1901`
8. first tick in the match on which all of the above hold

### LZ_V270_BASK_008

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Jobber

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `prog >= 0.3672`
6. `lead_size >= 4`
7. `lead_odds >= 1.5`
8. first tick in the match on which all of the above hold

### BAS_prop:q4_clos_0129

**LIVE** · market `match_winner` · backs `trailer` · price band `1.4` to `None` · tier Feeder

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `trailer`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `pm_away <= 2.92`
7. `rk_fav_odds >= 1.0142`
8. `lead_change_last_300s >= -5`
9. first tick in the match on which all of the above hold

### BAS_trail_ml_0025

**LIVE** · market `match_winner` · backs `trailer` · price band `1.4` to `None` · tier Ino

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `trailer`
4. the backed price is inside `1.4`–`None`
5. `lead_jump <= 0.9429`
6. `pc_aa16b0a5 >= -0.6013`
7. first tick in the match on which all of the above hold

### BAS_late_lead_ho_0106

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `prog_score <= 0.35`
7. `rk_dog_odds >= 1.8392`
8. `X_u_elapsed__minus__u_time_in_lead <= 1.0763`
9. `wall_min >= 131.5`
10. first tick in the match on which all of the above hold

> This strategy reads `u_elapsed`, `u_time_in_lead`, which the bundle supplies in `laz_features_basketball.py`.

### BAS_tg_under_0066

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `total_points_under >= 1.75`
6. `pm_away <= 2.85`
7. `X_X_u_elapsed__minus__u_time_in_lead__minus__X_u_pace__over__u_time_in_lead <= -19.1179`
8. `dog_odds_vol_180 <= 0.653`
9. first tick in the match on which all of the above hold

> This strategy reads `u_elapsed`, `u_time_in_lead`, which the bundle supplies in `laz_features_basketball.py`.

### BAS_prop:held_le_0171

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Npc

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `pm_away <= 2.92`
7. `leader_runmax <= 3.66`
8. `lead_vs_med <= 1.175`
9. `line_flat >= 1.6026`
10. first tick in the match on which all of the above hold

### BAS_tg_under_lag_0131

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `dog_odds_vol_180 <= 0.15546`
6. `odds_ratio <= 1.209`
7. `spread_cover_now >= -4.4051`
8. first tick in the match on which all of the above hold

### BAS_tg_under_lag_0137

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `odds_ratio <= 1.2094`
7. `pts_last_60s <= 6`
8. `lead_m15 <= 1`
9. `eng_score_tied <= 0`
10. first tick in the match on which all of the above hold

### BAS_tg_under_lag_0133

**LIVE** · market `total_points_under` · backs `Total_Points_Under` · price band `1.4` to `None` · tier Vision

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `Total_Points_Under`
4. the backed price is inside `1.4`–`None`
5. `pm_ratio <= 3.6843`
6. `imbal >= 0.0164`
7. `line_flat >= 0.8667`
8. `pts_last_60s <= 4.4615`
9. first tick in the match on which all of the above hold

### BAS_spec:Loser_1_0024

**LIVE** · market `match_winner` · backs `leader` · price band `1.4` to `None` · tier Ino

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `pm_away <= 2.92`
7. `leader_runmax <= 3.66`
8. `secs_since_run_started >= 0`
9. `fav_lead_m30 >= -1`
10. `u_trend <= -5.7851`
11. `total_goals_handicap_ffill >= 145.5`
12. first tick in the match on which all of the above hold

## Registered, not yet live

Validated by this run and written to `laz_strategy_registry` with
`enabled = false`, so they exist in the database, joinable to their
provenance, and cannot fire or break the load.

### BAS_spread_dog_c_0156

**REGISTERED, NOT LIVE** · market `None` · backs `None` · price band `1.4` to `None` · tier Vision

> Not enabled: market '' / outcome 'spread' (base 'spread_dog_cover') is not one basketball settles; it settles ['lead_ml', 'match_winner', 'total_points_under', 'trail_ml']. Tried ['spread']

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `None`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `need_frac >= 0.4`
7. `price_gap >= 0.06`
8. `dog_odds_vol_180 <= 0.2508`
9. first tick in the match on which all of the above hold

### BAS_spread_dog_c_0154

**REGISTERED, NOT LIVE** · market `None` · backs `None` · price band `1.4` to `None` · tier Vision

> Not enabled: market '' / outcome 'spread' (base 'spread_dog_cover') is not one basketball settles; it settles ['lead_ml', 'match_winner', 'total_points_under', 'trail_ml']. Tried ['spread']

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `None`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `lead_vs_line <= -140.5`
7. `dog_odds_vol_180 <= 0.2346`
8. first tick in the match on which all of the above hold

### BAS_q1_winner_0139

**REGISTERED, NOT LIVE** · market `None` · backs `None` · price band `1.4` to `None` · tier Npc

> Not enabled: market '' / outcome 'q1_result' (base 'q1_winner') is not one basketball settles; it settles ['lead_ml', 'match_winner', 'total_points_under', 'trail_ml']. Tried ['q1_result']

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `None`
4. the backed price is inside `1.4`–`None`
5. `X_q1_lead__minus__u_overround >= 1.88889`
6. `rk_implied_edge >= 0.0764`
7. first tick in the match on which all of the above hold

### BAS_spread_dog_c_0118

**REGISTERED, NOT LIVE** · market `None` · backs `None` · price band `1.4` to `None` · tier Vision

> Not enabled: market '' / outcome 'spread' (base 'spread_dog_cover') is not one basketball settles; it settles ['lead_ml', 'match_winner', 'total_points_under', 'trail_ml']. Tried ['spread']

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `None`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `need_frac >= 0.4`
7. `prog_score <= 0.35`
8. `lead_vs_line <= -154.5`
9. `X_X_u_elapsed__minus__u_time_in_lead__over__X_u_pace__over__u_time_in_lead >= -0.0083`
10. first tick in the match on which all of the above hold

### BAS_prop:match_t_0073

**REGISTERED, NOT LIVE** · market `None` · backs `None` · price band `1.4` to `None` · tier Vision

> Not enabled: base 'prop:match_total_pace' settles a total but its own name does not say UNDER or OVER; registering the wrong direction settles the bet on the opposite event

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `None`
4. the backed price is inside `1.4`–`None`
5. `rk_odds_ratio <= 2.6602`
6. `lead_vs_line >= -173.8462`
7. first tick in the match on which all of the above hold

### BAS_q1_winner_0130

**REGISTERED, NOT LIVE** · market `None` · backs `None` · price band `1.4` to `None` · tier Vision

> Not enabled: market '' / outcome 'q1_result' (base 'q1_winner') is not one basketball settles; it settles ['lead_ml', 'match_winner', 'total_points_under', 'trail_ml']. Tried ['q1_result']

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `None`
4. the backed price is inside `1.4`–`None`
5. `X_pace_ratio__minus__q1_lead <= -2.60393`
6. `signed_lead <= 4.5385`
7. first tick in the match on which all of the above hold

### BAS_h1_winner_0177

**REGISTERED, NOT LIVE** · market `None` · backs `None` · price band `1.4` to `None` · tier Npc

> Not enabled: market '' / outcome 'h1_result' (base 'h1_winner') is not one basketball settles; it settles ['lead_ml', 'match_winner', 'total_points_under', 'trail_ml']. Tried ['h1_result']

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `None`
4. the backed price is inside `1.4`–`None`
5. `time_at_this_price <= 28.2017`
6. `price_rank_in_match >= 0.3333`
7. `u_flat <= 0.4769`
8. first tick in the match on which all of the above hold

### BAS_prop:match_t_0021

**REGISTERED, NOT LIVE** · market `None` · backs `None` · price band `1.4` to `None` · tier Vision

> Not enabled: base 'prop:match_total_pace' settles a total but its own name does not say UNDER or OVER; registering the wrong direction settles the bet on the opposite event

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `None`
4. the backed price is inside `1.4`–`None`
5. `prog_score <= 0.35`
6. `line_move <= 4`
7. `price_gap <= 1.89`
8. `opp_drift <= 1.0811`
9. `trail <= 6`
10. `pace_last_300s_vs_line >= -174.5`
11. first tick in the match on which all of the above hold

### BAS_spread_dog_c_0155

**REGISTERED, NOT LIVE** · market `None` · backs `None` · price band `1.4` to `None` · tier Vision

> Not enabled: market '' / outcome 'spread' (base 'spread_dog_cover') is not one basketball settles; it settles ['lead_ml', 'match_winner', 'total_points_under', 'trail_ml']. Tried ['spread']

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `None`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `prog_score <= 0.35`
7. `rk_dog_odds >= 1.8392`
8. `lead_changes_300s <= 1`
9. first tick in the match on which all of the above hold

### BAS_spread_dog_c_0119

**REGISTERED, NOT LIVE** · market `None` · backs `None` · price band `1.4` to `None` · tier Vision

> Not enabled: market '' / outcome 'spread' (base 'spread_dog_cover') is not one basketball settles; it settles ['lead_ml', 'match_winner', 'total_points_under', 'trail_ml']. Tried ['spread']

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `None`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `need_frac >= 0.4`
7. `prog_score <= 0.35`
8. `u_ratio_open <= 3.0051`
9. `point_spread_handicap <= -1.5`
10. first tick in the match on which all of the above hold

### BAS_spread_dog_c_0120

**REGISTERED, NOT LIVE** · market `None` · backs `None` · price band `1.4` to `None` · tier Vision

> Not enabled: market '' / outcome 'spread' (base 'spread_dog_cover') is not one basketball settles; it settles ['lead_ml', 'match_winner', 'total_points_under', 'trail_ml']. Tried ['spread']

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `None`
4. the backed price is inside `1.4`–`None`
5. `line_open >= 140`
6. `retrace_lead <= 0.3163`
7. `secs_since_run_started >= 0`
8. `X_u_elapsed__minus__u_time_in_lead >= -0.1992`
9. first tick in the match on which all of the above hold

### BAS_spread_dog_c_0178

**REGISTERED, NOT LIVE** · market `None` · backs `None` · price band `1.4` to `None` · tier Jobber

> Not enabled: market '' / outcome 'spread' (base 'spread_dog_cover') is not one basketball settles; it settles ['lead_ml', 'match_winner', 'total_points_under', 'trail_ml']. Tried ['spread']

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `None`
4. the backed price is inside `1.4`–`None`
5. `price_move_since_score <= 1`
6. `u_overround <= 1.1091`
7. first tick in the match on which all of the above hold

### BAS_prop:h1_lead_0175

**REGISTERED, NOT LIVE** · market `None` · backs `leader` · price band `1.4` to `None` · tier Npc

> Not enabled: market '' / outcome '' (base 'prop:h1_leader_hold') is not one basketball settles; it settles ['lead_ml', 'match_winner', 'total_points_under', 'trail_ml']. Tried nothing

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `leader`
4. the backed price is inside `1.4`–`None`
5. `u_elapsed >= 0.05`
6. `u_elapsed <= 0.49`
7. `line_open >= 140`
8. `rk_lead_off_high >= 0.2333`
9. `lead_m15 <= 1`
10. `X_point_spread_home__x__q1_lead >= 1.8237`
11. first tick in the match on which all of the above hold

### BAS_h1_winner_0176

**REGISTERED, NOT LIVE** · market `None` · backs `None` · price band `1.4` to `None` · tier Npc

> Not enabled: market '' / outcome 'h1_result' (base 'h1_winner') is not one basketball settles; it settles ['lead_ml', 'match_winner', 'total_points_under', 'trail_ml']. Tried ['h1_result']

Ordered gates, in this order:

1. the market is open
2. the feed is fresh
3. the side resolves: `None`
4. the backed price is inside `1.4`–`None`
5. `total_points_under <= 1.9407`
6. `lead_q1h <= 1`
7. `pc_4fa6ab33 <= 0.6116`
8. first tick in the match on which all of the above hold
