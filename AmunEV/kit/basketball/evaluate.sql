-- One statement per strategy. Each returns the FIRST qualifying tick per match.
-- The gate order is the engine's: conditions, then price band, then market open,
-- then first-tick-per-match. A NULL feature never satisfies a condition, which is
-- why every clause carries an explicit IS NOT NULL.

-- ===== BAS_spec:HT_Unde_0017 =====
-- base: None · tier: Jobber · measured OOS win 49.6% ROI 21.6% on 273 bets
-- backed: None · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:HT_Unde_0017' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_vs_line" IS NOT NULL AND "lead_vs_line" <= -160.315 AND "open_gap" IS NOT NULL AND "open_gap" <= 2.2797 AND "ten_mins" IS NOT NULL AND "ten_mins" <= 23.4333
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:Leader_0056 =====
-- base: spec:Leader · tier: Npc · measured OOS win 73.5% ROI 16.2% on 374 bets
-- backed: spec:Leader · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:Leader_0056' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "imbal" IS NOT NULL AND "imbal" >= 0.0139
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0219 =====
-- base: late_lead_hold · tier: Jobber · measured OOS win 47% ROI 16.2% on 355 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0219' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "req_ratio" IS NOT NULL AND "req_ratio" >= 1.1619
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0224 =====
-- base: late_lead_hold · tier: Jobber · measured OOS win 49.8% ROI 15.7% on 419 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0224' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1237
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:late_le_0246 =====
-- base: prop:late_lead_hold · tier: Vision · measured OOS win 50.9% ROI 15.3% on 323 bets
-- backed: prop:late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:late_le_0246' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "tot_vs_line" IS NOT NULL AND "tot_vs_line" <= -76.8692
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:late_le_0252 =====
-- base: prop:late_lead_hold · tier: Vision · measured OOS win 51.9% ROI 15% on 306 bets
-- backed: prop:late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:late_le_0252' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prematch_dog_odds" IS NOT NULL AND "prematch_dog_odds" <= 4.45
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:late_le_0241 =====
-- base: prop:late_lead_hold · tier: Vision · measured OOS win 50% ROI 12.1% on 317 bets
-- backed: prop:late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:late_le_0241' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "dog_odds_trend_60" IS NOT NULL AND "dog_odds_trend_60" >= -0.6769
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== LZ_V270_BASK_022 =====
-- base: None · tier: Npc · measured OOS win 64.4% ROI 18.4% on 235 bets
-- backed: None · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'LZ_V270_BASK_022' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_vs_med" IS NOT NULL AND "lead_vs_med" >= 1.0267 AND "lead_size" IS NOT NULL AND "lead_size" >= 7 AND "lead_odds" IS NOT NULL AND "lead_odds" >= 1.50
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0220 =====
-- base: late_lead_hold · tier: Jobber · measured OOS win 47.8% ROI 17.3% on 290 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0220' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "req_ratio" IS NOT NULL AND "req_ratio" >= 1.2596
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_dog_leading_0156 =====
-- base: dog_leading · tier: Jobber · measured OOS win 46.4% ROI 15.6% on 301 bets
-- backed: dog_leading · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_dog_leading_0156' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "req_ratio" IS NOT NULL AND "req_ratio" >= 1.2493
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0225 =====
-- base: late_lead_hold · tier: Jobber · measured OOS win 46.2% ROI 12.1% on 316 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0225' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "retrace_lo" IS NOT NULL AND "retrace_lo" <= 0.1263
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== LZ_V270_BASK_015 =====
-- base: None · tier: Vision · measured OOS win 57.5% ROI 11.8% on 346 bets
-- backed: None · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'LZ_V270_BASK_015' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_size" IS NOT NULL AND "lead_size" >= 7 AND "drift_ratio" IS NOT NULL AND "drift_ratio" >= 0.6632 AND "lead_odds" IS NOT NULL AND "lead_odds" >= 1.50
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0226 =====
-- base: late_lead_hold · tier: Jobber · measured OOS win 49.7% ROI 11.2% on 364 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0226' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "leader_implied_gap" IS NOT NULL AND "leader_implied_gap" >= -0.4524
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:shorten_0187 =====
-- base: prop:shortener_vs_open · tier: Npc · measured OOS win 67.1% ROI 10.2% on 295 bets
-- backed: prop:shortener_vs_open · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:shorten_0187' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "imbal" IS NOT NULL AND "imbal" >= 0.0217
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_dog_leading_0172 =====
-- base: dog_leading · tier: Jobber · measured OOS win 44.4% ROI 9.7% on 384 bets
-- backed: dog_leading · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_dog_leading_0172' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1237
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_dog_leading_0164 =====
-- base: dog_leading · tier: Jobber · measured OOS win 47.5% ROI 9% on 344 bets
-- backed: dog_leading · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_dog_leading_0164' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1169
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0035 =====
-- base: prop:first_scorer_hold · tier: Jobber · measured OOS win 48.6% ROI 8.9% on 305 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0035' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "px_gap_ratio" IS NOT NULL AND "px_gap_ratio" <= 1.5 AND "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1237
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0228 =====
-- base: late_lead_hold · tier: Jobber · measured OOS win 44.8% ROI 8.4% on 349 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0228' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "u_drift" IS NOT NULL AND "u_drift" <= 1.8124
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_dog_leading_0168 =====
-- base: dog_leading · tier: Jobber · measured OOS win 46.1% ROI 8.3% on 322 bets
-- backed: dog_leading · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_dog_leading_0168' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1148 AND "req_ratio" IS NOT NULL AND "req_ratio" >= 1.2386
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:drifter_0006 =====
-- base: prop:drifter_vs_open · tier: Ino · measured OOS win 19.6% ROI 8.3% on 294 bets
-- backed: prop:drifter_vs_open · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:drifter_0006' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "X_minute__over__u_vol" IS NOT NULL AND "X_minute__over__u_vol" <= 2696.29 AND "clean_sheet" IS NOT NULL AND "clean_sheet" <= 0 AND "lead_m15" IS NOT NULL AND "lead_m15" <= 1
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0091 =====
-- base: late_lead_hold · tier: Npc · measured OOS win 68.7% ROI 7.9% on 343 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0091' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "loser_runmax" IS NOT NULL AND "loser_runmax" >= 1.8722
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:HT_Unde_0010 =====
-- base: spec:HT_Underdog_Fade · tier: Jobber · measured OOS win 40.9% ROI 7.2% on 344 bets
-- backed: spec:HT_Underdog_Fade · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:HT_Unde_0010' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "tp_vel_n" IS NOT NULL AND "tp_vel_n" >= 0.0108
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0032 =====
-- base: prop:first_scorer_hold · tier: Jobber · measured OOS win 43.5% ROI 7.1% on 336 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0032' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "nchg_120" IS NOT NULL AND "nchg_120" <= 1
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_dog_leading_0134 =====
-- base: dog_leading · tier: Npc · measured OOS win 62.6% ROI 7% on 331 bets
-- backed: dog_leading · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_dog_leading_0134' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "fav_odds_trend_60" IS NOT NULL AND "fav_odds_trend_60" <= 0.2231
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:shorten_0188 =====
-- base: prop:shortener_vs_open · tier: Npc · measured OOS win 64.9% ROI 6.9% on 300 bets
-- backed: prop:shortener_vs_open · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:shorten_0188' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "rk_dog_odds" IS NOT NULL AND "rk_dog_odds" >= 1.84
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:drifter_0007 =====
-- base: prop:drifter_vs_open · tier: Feeder · measured OOS win 21.2% ROI 6.8% on 344 bets
-- backed: prop:drifter_vs_open · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:drifter_0007' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_vs_line" IS NOT NULL AND "lead_vs_line" <= -170.833
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0088 =====
-- base: late_lead_hold · tier: Npc · measured OOS win 67.9% ROI 6.5% on 348 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0088' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "trailer_price" IS NOT NULL AND "trailer_price" >= 1.8518
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_q1_winner_0125 =====
-- base: q1_winner · tier: Npc · measured OOS win 70.6% ROI 6.3% on 388 bets
-- backed: q1_winner · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_q1_winner_0125' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "rk_implied_edge" IS NOT NULL AND "rk_implied_edge" >= 0.0536
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0227 =====
-- base: late_lead_hold · tier: Jobber · measured OOS win 47.7% ROI 6.3% on 302 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0227' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "u_vel_t" IS NOT NULL AND "u_vel_t" >= -0.0412
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_dog_leading_0169 =====
-- base: dog_leading · tier: Jobber · measured OOS win 46.4% ROI 6.3% on 341 bets
-- backed: dog_leading · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_dog_leading_0169' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1169 AND "opp_drift" IS NOT NULL AND "opp_drift" <= 1.0436
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0036 =====
-- base: prop:first_scorer_hold · tier: Jobber · measured OOS win 44.1% ROI 6.3% on 306 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0036' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "loser_runmax" IS NOT NULL AND "loser_runmax" <= 2.42
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:HT_Unde_0011 =====
-- base: spec:HT_Underdog_Fade · tier: Jobber · measured OOS win 41% ROI 6.2% on 347 bets
-- backed: spec:HT_Underdog_Fade · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:HT_Unde_0011' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "u_drift" IS NOT NULL AND "u_drift" <= 1
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0093 =====
-- base: late_lead_hold · tier: Npc · measured OOS win 66.1% ROI 6% on 341 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0093' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "pm_away" IS NOT NULL AND "pm_away" <= 2.77
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0090 =====
-- base: late_lead_hold · tier: Npc · measured OOS win 66.3% ROI 5.8% on 352 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0090' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_changes_total" IS NOT NULL AND "lead_changes_total" <= 2
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:garbage_0121 =====
-- base: prop:garbage_under · tier: Npc · measured OOS win 60% ROI 5.8% on 337 bets
-- backed: prop:garbage_under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:garbage_0121' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "motif_1" IS NOT NULL AND "motif_1" <= 0
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_q1_winner_0133 =====
-- base: q1_winner · tier: Npc · measured OOS win 72% ROI 5.7% on 336 bets
-- backed: q1_winner · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_q1_winner_0133' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "rk_implied_edge" IS NOT NULL AND "rk_implied_edge" >= 0.1704
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:match_s_0151 =====
-- base: prop:match_spread_cover · tier: Vision · measured OOS win 55.5% ROI 5.7% on 271 bets
-- backed: prop:match_spread_cover · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:match_s_0151' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "spread_cover_now" IS NOT NULL AND "spread_cover_now" <= 10.8231 AND "price_rank_in_match" IS NOT NULL AND "price_rank_in_match" >= 0.0186
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_dog_leading_0165 =====
-- base: dog_leading · tier: Jobber · measured OOS win 45% ROI 5.7% on 318 bets
-- backed: dog_leading · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_dog_leading_0165' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "loser_runmin" IS NOT NULL AND "loser_runmin" >= 1.28
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_0045 =====
-- base: tg_under · tier: Vision · measured OOS win 55.6% ROI 5.6% on 367 bets
-- backed: tg_under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_0045' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "X_X_u_elapsed__minus__u_time_in_lead__over__X_u_pace__over__u_time_in_lead" IS NOT NULL AND "X_X_u_elapsed__minus__u_time_in_lead__over__X_u_pace__over__u_time_in_lead" <= 0.0224
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spread_dog_c_0201 =====
-- base: spread_dog_cover · tier: Vision · measured OOS win 55% ROI 5.6% on 295 bets
-- backed: spread_dog_cover · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spread_dog_c_0201' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "X_u_elapsed__x__u_time_in_lead" IS NOT NULL AND "X_u_elapsed__x__u_time_in_lead" >= 0.2114
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:late_le_0251 =====
-- base: prop:late_lead_hold · tier: Jobber · measured OOS win 46.9% ROI 5.3% on 338 bets
-- backed: prop:late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:late_le_0251' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "retrace_lead" IS NOT NULL AND "retrace_lead" <= 0.3623
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:q4_clos_0253 =====
-- base: prop:q4_close_trailer · tier: Feeder · measured OOS win 27.1% ROI 5.3% on 331 bets
-- backed: prop:q4_close_trailer · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:q4_clos_0253' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "away_odds" IS NOT NULL AND "away_odds" <= 4.0695
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_SHORTENED_0049 =====
-- base: SHORTENED · tier: Npc · measured OOS win 66% ROI 5.2% on 311 bets
-- backed: SHORTENED · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_SHORTENED_0049' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "trailer_price" IS NOT NULL AND "trailer_price" >= 1.8315
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:fresh_l_0257 =====
-- base: prop:fresh_lead · tier: Npc · measured OOS win 66.5% ROI 4.9% on 353 bets
-- backed: prop:fresh_lead · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:fresh_l_0257' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "margin_range_300s" IS NOT NULL AND "margin_range_300s" <= 5
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:shorten_0185 =====
-- base: prop:shortener_vs_open · tier: Npc · measured OOS win 63.4% ROI 4.9% on 347 bets
-- backed: prop:shortener_vs_open · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:shorten_0185' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "dog_odds_vol_180" IS NOT NULL AND "dog_odds_vol_180" <= 0.367
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:shorten_0189 =====
-- base: prop:shortener_vs_open · tier: Npc · measured OOS win 63.5% ROI 4.9% on 321 bets
-- backed: prop:shortener_vs_open · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:shorten_0189' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_vs_prematch_gap" IS NOT NULL AND "lead_vs_prematch_gap" <= -0.0106
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0026 =====
-- base: prop:first_scorer_hold · tier: Jobber · measured OOS win 47.1% ROI 4.8% on 317 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0026' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "price_gap" IS NOT NULL AND "price_gap" <= 1.89
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_lag_0123 =====
-- base: tg_under_lag · tier: Vision · measured OOS win 55.2% ROI 4.7% on 347 bets
-- backed: tg_under_lag · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_lag_0123' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_vel" IS NOT NULL AND "line_vel" <= 3
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0042 =====
-- base: prop:first_scorer_hold · tier: Jobber · measured OOS win 46.4% ROI 4.7% on 323 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0042' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_period" IS NOT NULL AND "prog_period" <= 0.3414 AND "rk_implied_edge" IS NOT NULL AND "rk_implied_edge" <= 0.4534
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0027 =====
-- base: prop:first_scorer_hold · tier: Jobber · measured OOS win 43.7% ROI 4.6% on 323 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0027' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_period" IS NOT NULL AND "prog_period" <= 0.5091
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:shorten_0192 =====
-- base: prop:shortener_vs_open · tier: Npc · measured OOS win 62.9% ROI 4.5% on 319 bets
-- backed: prop:shortener_vs_open · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:shorten_0192' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_vs_med" IS NOT NULL AND "lead_vs_med" <= 0.941 AND "ten_mins" IS NOT NULL AND "ten_mins" <= 16.5308
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_dog_leading_0160 =====
-- base: dog_leading · tier: Jobber · measured OOS win 43.7% ROI 4.5% on 337 bets
-- backed: dog_leading · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_dog_leading_0160' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1175 AND "req_ratio" IS NOT NULL AND "req_ratio" >= 1.195
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_lead_ml_0069 =====
-- base: lead_ml · tier: Npc · measured OOS win 66.5% ROI 4.4% on 396 bets
-- backed: lead_ml · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_lead_ml_0069' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "dp_lead_open" IS NOT NULL AND "dp_lead_open" <= 2.75 AND "lead_m15" IS NOT NULL AND "lead_m15" <= 1
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:shorten_0183 =====
-- base: prop:shortener_vs_open · tier: Npc · measured OOS win 62.9% ROI 4.3% on 330 bets
-- backed: prop:shortener_vs_open · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:shorten_0183' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "X_minute__over__u_elapsed" IS NOT NULL AND "X_minute__over__u_elapsed" <= 43.0064
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:late_le_0208 =====
-- base: prop:late_lead_hold · tier: Npc · measured OOS win 64.6% ROI 4.1% on 357 bets
-- backed: prop:late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:late_le_0208' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "leader_implied_gap" IS NOT NULL AND "leader_implied_gap" >= 0.0232
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0089 =====
-- base: late_lead_hold · tier: Npc · measured OOS win 64.1% ROI 4.1% on 339 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0089' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "X_minute__over__u_vol" IS NOT NULL AND "X_minute__over__u_vol" <= 353.579
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0221 =====
-- base: late_lead_hold · tier: Jobber · measured OOS win 44.3% ROI 4.1% on 280 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0221' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1237 AND "secs_since_quarter_start" IS NOT NULL AND "secs_since_quarter_start" <= 41.6871
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_q1_winner_0128 =====
-- base: q1_winner · tier: Npc · measured OOS win 70.3% ROI 4% on 341 bets
-- backed: q1_winner · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_q1_winner_0128' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "rk_implied_edge" IS NOT NULL AND "rk_implied_edge" >= 0.107
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spread_dog_c_0102 =====
-- base: spread_dog_cover · tier: Vision · measured OOS win 57.7% ROI 3.9% on 349 bets
-- backed: spread_dog_cover · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spread_dog_c_0102' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "price_move_since_score" IS NOT NULL AND "price_move_since_score" <= 1
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:TG_Hist_0046 =====
-- base: spec:TG_Historical_vs_Handicap_Under · tier: Vision · measured OOS win 54.8% ROI 3.9% on 320 bets
-- backed: spec:TG_Historical_vs_Handicap_Under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:TG_Hist_0046' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "dog_odds_vol_180" IS NOT NULL AND "dog_odds_vol_180" <= 0.3005
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:shorten_0212 =====
-- base: prop:shortener_vs_open · tier: Jobber · measured OOS win 44.4% ROI 3.9% on 298 bets
-- backed: prop:shortener_vs_open · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:shorten_0212' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "tg_px_ratio" IS NOT NULL AND "tg_px_ratio" <= 1.1543
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:shorten_0190 =====
-- base: prop:shortener_vs_open · tier: Npc · measured OOS win 63.2% ROI 3.8% on 361 bets
-- backed: prop:shortener_vs_open · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:shorten_0190' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "u_vel_t" IS NOT NULL AND "u_vel_t" <= 0
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:garbage_0120 =====
-- base: prop:garbage_under · tier: Vision · measured OOS win 58.6% ROI 3.8% on 354 bets
-- backed: prop:garbage_under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:garbage_0120' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "pts_last_60s" IS NOT NULL AND "pts_last_60s" >= 1.5
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_dog_leading_0171 =====
-- base: dog_leading · tier: Jobber · measured OOS win 43.4% ROI 3.8% on 318 bets
-- backed: dog_leading · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_dog_leading_0171' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "opp_drift" IS NOT NULL AND "opp_drift" <= 1.0492 AND "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1169
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:HT_Unde_0110 =====
-- base: spec:HT_Underdog_Fade · tier: Npc · measured OOS win 60.1% ROI 3.7% on 348 bets
-- backed: spec:HT_Underdog_Fade · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:HT_Unde_0110' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_vs_med" IS NOT NULL AND "lead_vs_med" <= 1.1401
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0040 =====
-- base: prop:first_scorer_hold · tier: Jobber · measured OOS win 46% ROI 3.7% on 316 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0040' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "loser_runmin" IS NOT NULL AND "loser_runmin" >= 1.2769 AND "u_jump" IS NOT NULL AND "u_jump" <= 1.0976
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0038 =====
-- base: prop:first_scorer_hold · tier: Jobber · measured OOS win 46.5% ROI 3.6% on 372 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0038' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "tg_px_ratio" IS NOT NULL AND "tg_px_ratio" >= 1
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_dog_covering_0074 =====
-- base: dog_covering_late · tier: Vision · measured OOS win 57.5% ROI 3.5% on 341 bets
-- backed: dog_covering_late · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_dog_covering_0074' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "u_ratio_open" IS NOT NULL AND "u_ratio_open" <= 2.8369
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spread_dog_c_0105 =====
-- base: spread_dog_cover · tier: Vision · measured OOS win 57.4% ROI 3.4% on 308 bets
-- backed: spread_dog_cover · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spread_dog_c_0105' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "price_move_since_score" IS NOT NULL AND "price_move_since_score" <= 1 AND "backed_pts_last_60s" IS NOT NULL AND "backed_pts_last_60s" <= 3.8538
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0082 =====
-- base: prop:first_scorer_hold · tier: Npc · measured OOS win 64.8% ROI 3.3% on 328 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0082' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "tg_imp_over" IS NOT NULL AND "tg_imp_over" >= 0.5165 AND "lead_changes_total" IS NOT NULL AND "lead_changes_total" <= 1
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:Leader_0054 =====
-- base: spec:Leader · tier: Npc · measured OOS win 66.7% ROI 3.1% on 413 bets
-- backed: spec:Leader · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:Leader_0054' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "trailer_price" IS NOT NULL AND "trailer_price" >= 1.92
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_lead_ml_0060 =====
-- base: lead_ml · tier: Npc · measured OOS win 64.3% ROI 3.1% on 405 bets
-- backed: lead_ml · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_lead_ml_0060' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "dow" IS NOT NULL AND "dow" >= 2
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0086 =====
-- base: prop:first_scorer_hold · tier: Npc · measured OOS win 64.3% ROI 3.1% on 361 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0086' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "trailer_price" IS NOT NULL AND "trailer_price" >= 1.84 AND "tg_imp_over" IS NOT NULL AND "tg_imp_over" >= 0.5126
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:shorten_0184 =====
-- base: prop:shortener_vs_open · tier: Npc · measured OOS win 63.3% ROI 3.1% on 342 bets
-- backed: prop:shortener_vs_open · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:shorten_0184' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "imbal" IS NOT NULL AND "imbal" >= 0.0164 AND "line_flat" IS NOT NULL AND "line_flat" >= 0.8667
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_dog_leading_0167 =====
-- base: dog_leading · tier: Jobber · measured OOS win 42.4% ROI 3.1% on 309 bets
-- backed: dog_leading · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_dog_leading_0167' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "X_minute__x__u_pace" IS NOT NULL AND "X_minute__x__u_pace" <= 324.3
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_lead_ml_0147 =====
-- base: lead_ml · tier: Jobber · measured OOS win 46.1% ROI 3% on 335 bets
-- backed: lead_ml · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_lead_ml_0147' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1237 AND "lead_m15" IS NOT NULL AND "lead_m15" <= 0
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_lead_ml_0062 =====
-- base: lead_ml · tier: Npc · measured OOS win 67.2% ROI 2.9% on 339 bets
-- backed: lead_ml · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_lead_ml_0062' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "trailer_price" IS NOT NULL AND "trailer_price" >= 1.92 AND "u_time_in_lead" IS NOT NULL AND "u_time_in_lead" >= 0.0009
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spread_dog_c_0256 =====
-- base: spread_dog_cover · tier: Npc · measured OOS win 64.9% ROI 2.9% on 365 bets
-- backed: spread_dog_cover · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spread_dog_c_0256' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "trailer_drift" IS NOT NULL AND "trailer_drift" <= 4.1282
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:shorten_0200 =====
-- base: prop:shortener_vs_open · tier: Npc · measured OOS win 62.8% ROI 2.9% on 291 bets
-- backed: prop:shortener_vs_open · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:shorten_0200' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "req_ratio" IS NOT NULL AND "req_ratio" >= 1.0985 AND "lead_vs_prematch_gap" IS NOT NULL AND "lead_vs_prematch_gap" <= -0.0126
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_dog_leading_0173 =====
-- base: dog_leading · tier: Jobber · measured OOS win 44.5% ROI 2.9% on 366 bets
-- backed: dog_leading · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_dog_leading_0173' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1169 AND "open_vs_now_lead" IS NOT NULL AND "open_vs_now_lead" <= 0.73
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0080 =====
-- base: prop:first_scorer_hold · tier: Npc · measured OOS win 64.9% ROI 2.8% on 355 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0080' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "rk_dog_odds" IS NOT NULL AND "rk_dog_odds" >= 1.85
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_lead_ml_0150 =====
-- base: lead_ml · tier: Jobber · measured OOS win 46.6% ROI 2.8% on 353 bets
-- backed: lead_ml · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_lead_ml_0150' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1236 AND "u_hazard" IS NOT NULL AND "u_hazard" <= -0.1409
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:shorten_0210 =====
-- base: prop:shortener_vs_open · tier: Jobber · measured OOS win 45.7% ROI 2.8% on 299 bets
-- backed: prop:shortener_vs_open · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:shorten_0210' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "u_jump" IS NOT NULL AND "u_jump" >= 0.9449
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0101 =====
-- base: late_lead_hold · tier: Npc · measured OOS win 63.4% ROI 2.6% on 356 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0101' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "tot_vs_line" IS NOT NULL AND "tot_vs_line" >= -167.5 AND "pace_last_300s_vs_line" IS NOT NULL AND "pace_last_300s_vs_line" <= -72.8785
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:garbage_0122 =====
-- base: prop:garbage_under · tier: Vision · measured OOS win 58% ROI 2.5% on 357 bets
-- backed: prop:garbage_under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:garbage_0122' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "total_line_move" IS NOT NULL AND "total_line_move" >= -5.8769
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:dog_lea_0181 =====
-- base: prop:dog_leading_late · tier: Jobber · measured OOS win 42.2% ROI 2.5% on 315 bets
-- backed: prop:dog_leading_late · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:dog_lea_0181' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_vs_line" IS NOT NULL AND "lead_vs_line" <= -154.5
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0098 =====
-- base: late_lead_hold · tier: Npc · measured OOS win 66.9% ROI 2.4% on 332 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0098' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "price_gap" IS NOT NULL AND "price_gap" >= 0.2 AND "jump_loser" IS NOT NULL AND "jump_loser" >= 0.9075
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0023 =====
-- base: prop:first_scorer_hold · tier: Jobber · measured OOS win 47.1% ROI 2.4% on 313 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0023' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1237 AND "lead_poss" IS NOT NULL AND "lead_poss" >= 0.7268
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_dog_leading_0159 =====
-- base: dog_leading · tier: Jobber · measured OOS win 43.2% ROI 2.3% on 339 bets
-- backed: dog_leading · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_dog_leading_0159' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "tg_overround" IS NOT NULL AND "tg_overround" <= 1.117 AND "open_vs_now_lead" IS NOT NULL AND "open_vs_now_lead" <= 0.7331
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:Leader_0051 =====
-- base: spec:Leader · tier: Npc · measured OOS win 63.2% ROI 2.2% on 396 bets
-- backed: spec:Leader · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:Leader_0051' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "tg_imp_under" IS NOT NULL AND "tg_imp_under" <= 0.578
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0099 =====
-- base: late_lead_hold · tier: Npc · measured OOS win 63.2% ROI 2.2% on 365 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0099' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "pace_last_300s_vs_line" IS NOT NULL AND "pace_last_300s_vs_line" <= -81.9154
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_dog_covering_0235 =====
-- base: dog_covering_late · tier: Vision · measured OOS win 55.8% ROI 2.2% on 321 bets
-- backed: dog_covering_late · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_dog_covering_0235' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "imp_away" IS NOT NULL AND "imp_away" <= 0.7797
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_lag_0008 =====
-- base: tg_under_lag · tier: Vision · measured OOS win 57.3% ROI 2.1% on 343 bets
-- backed: tg_under_lag · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_lag_0008' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_vs_line" IS NOT NULL AND "lead_vs_line" >= -179.5
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_DRIFTED_0021 =====
-- base: DRIFTED · tier: Jobber · measured OOS win 41% ROI 2.1% on 347 bets
-- backed: DRIFTED · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_DRIFTED_0021' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_age_s" IS NOT NULL AND "lead_age_s" <= 176.862
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0100 =====
-- base: late_lead_hold · tier: Npc · measured OOS win 63.3% ROI 2% on 337 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0100' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "h_score" IS NOT NULL AND "h_score" <= 16.1205 AND "u_hazard" IS NOT NULL AND "u_hazard" <= -0.1356 AND "opp_velocity" IS NOT NULL AND "opp_velocity" >= -0.0842
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:shorten_0196 =====
-- base: prop:shortener_vs_open · tier: Npc · measured OOS win 61.3% ROI 2% on 339 bets
-- backed: prop:shortener_vs_open · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:shorten_0196' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_jump" IS NOT NULL AND "lead_jump" <= 0.9519
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spread_dog_c_0104 =====
-- base: spread_dog_cover · tier: Vision · measured OOS win 56.6% ROI 2% on 366 bets
-- backed: spread_dog_cover · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spread_dog_c_0104' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "tg_overround" IS NOT NULL AND "tg_overround" <= 1.112 AND "price_move_since_score" IS NOT NULL AND "price_move_since_score" <= 1
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0078 =====
-- base: prop:first_scorer_hold · tier: Npc · measured OOS win 64.4% ROI 1.9% on 406 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0078' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "leader_implied_gap" IS NOT NULL AND "leader_implied_gap" >= 0.0204
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:shorten_0198 =====
-- base: prop:shortener_vs_open · tier: Npc · measured OOS win 63.3% ROI 1.9% on 325 bets
-- backed: prop:shortener_vs_open · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:shorten_0198' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "leader_implied_gap" IS NOT NULL AND "leader_implied_gap" >= 0.0192
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_DRIFTED_0020 =====
-- base: DRIFTED · tier: Jobber · measured OOS win 41.5% ROI 1.9% on 335 bets
-- backed: DRIFTED · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_DRIFTED_0020' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "point_spread_home" IS NOT NULL AND "point_spread_home" >= 1.6895
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:late_le_0205 =====
-- base: prop:late_lead_hold · tier: Npc · measured OOS win 63.3% ROI 1.8% on 341 bets
-- backed: prop:late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:late_le_0205' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prematch_dog_odds" IS NOT NULL AND "prematch_dog_odds" <= 2.9642
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== LZ_V270_BASK_016 =====
-- base: None · tier: Vision · measured OOS win 58.5% ROI 1.8% on 489 bets
-- backed: None · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'LZ_V270_BASK_016' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_odds" IS NOT NULL AND "lead_odds" <= 2.25 AND "lead_size" IS NOT NULL AND "lead_size" >= 6 AND "lead_odds" IS NOT NULL AND "lead_odds" >= 1.50
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:HT_Unde_0111 =====
-- base: spec:HT_Underdog_Fade · tier: Vision · measured OOS win 58.2% ROI 1.8% on 310 bets
-- backed: spec:HT_Underdog_Fade · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:HT_Unde_0111' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_changes_300s" IS NOT NULL AND "lead_changes_300s" <= 1
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:late_le_0242 =====
-- base: prop:late_lead_hold · tier: Jobber · measured OOS win 47.1% ROI 1.8% on 331 bets
-- backed: prop:late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:late_le_0242' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "loser_runmin" IS NOT NULL AND "loser_runmin" >= 1.168
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_dog_leading_0158 =====
-- base: dog_leading · tier: Jobber · measured OOS win 44.4% ROI 1.8% on 319 bets
-- backed: dog_leading · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_dog_leading_0158' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1173 AND "opp_drift" IS NOT NULL AND "opp_drift" <= 1.0811
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_SHORTENED_0072 =====
-- base: SHORTENED · tier: Jobber · measured OOS win 42.1% ROI 1.8% on 357 bets
-- backed: SHORTENED · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_SHORTENED_0072' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "rk_fav_odds" IS NOT NULL AND "rk_fav_odds" >= 1.23 AND "u_open" IS NOT NULL AND "u_open" >= 1.2538
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:held_le_0232 =====
-- base: prop:held_lead · tier: Npc · measured OOS win 66.2% ROI 1.7% on 291 bets
-- backed: prop:held_lead · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:held_le_0232' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "trailer_price" IS NOT NULL AND "trailer_price" >= 1.84 AND "lead_delta_last_120s" IS NOT NULL AND "lead_delta_last_120s" >= -2.4923
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_lead_ml_0068 =====
-- base: lead_ml · tier: Npc · measured OOS win 66% ROI 1.6% on 403 bets
-- backed: lead_ml · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_lead_ml_0068' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "trailer_price" IS NOT NULL AND "trailer_price" >= 1.8471 AND "pace_vs_line_pct" IS NOT NULL AND "pace_vs_line_pct" >= -0.9852
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_0002 =====
-- base: None · tier: Npc · measured OOS win 61.5% ROI 1.6% on 813 bets
-- backed: None · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_0002' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "X_a_score__over__home_odds" IS NOT NULL AND "X_a_score__over__home_odds" >= 0 AND "lead_m75" IS NOT NULL AND "lead_m75" <= 1
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== LZ_V270_BASK_021 =====
-- base: None · tier: Vision · measured OOS win 56.7% ROI 1.6% on 311 bets
-- backed: None · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'LZ_V270_BASK_021' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "total_pts" IS NOT NULL AND "total_pts" >= 122 AND "runmin" IS NOT NULL AND "runmin" >= 1.129 AND "lead_odds" IS NOT NULL AND "lead_odds" >= 1.50
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_over_0022 =====
-- base: tg_over · tier: Vision · measured OOS win 52.5% ROI 1.6% on 274 bets
-- backed: tg_over · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_over_0022' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "total_points_handicap" IS NOT NULL AND "total_points_handicap" >= 135.5 AND "opp_o" IS NOT NULL AND "opp_o" >= 1.1423
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:shorten_0195 =====
-- base: prop:shortener_vs_open · tier: Npc · measured OOS win 61.8% ROI 1.5% on 325 bets
-- backed: prop:shortener_vs_open · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:shorten_0195' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_jump" IS NOT NULL AND "lead_jump" <= 0.9429
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:HT_Unde_0112 =====
-- base: spec:HT_Underdog_Fade · tier: Vision · measured OOS win 59.1% ROI 1.5% on 311 bets
-- backed: spec:HT_Underdog_Fade · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:HT_Unde_0112' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_vs_med" IS NOT NULL AND "lead_vs_med" <= 1.175 AND "rk_dog_odds" IS NOT NULL AND "rk_dog_odds" >= 1.83 AND "u_ratio_shift" IS NOT NULL AND "u_ratio_shift" >= 0.5724
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0215 =====
-- base: late_lead_hold · tier: Jobber · measured OOS win 43.7% ROI 1.5% on 303 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0215' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "home_pace" IS NOT NULL AND "home_pace" <= 9
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spread_dog_c_0255 =====
-- base: spread_dog_cover · tier: Npc · measured OOS win 63.8% ROI 1.4% on 339 bets
-- backed: spread_dog_cover · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spread_dog_c_0255' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "proj_gap_n" IS NOT NULL AND "proj_gap_n" <= -0.0208
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0077 =====
-- base: prop:first_scorer_hold · tier: Npc · measured OOS win 62% ROI 1.3% on 326 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0077' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "rk_dog_odds" IS NOT NULL AND "rk_dog_odds" >= 1.8392 AND "X_u_elapsed__minus__u_time_in_lead" IS NOT NULL AND "X_u_elapsed__minus__u_time_in_lead" <= 1.0763
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_0043 =====
-- base: tg_under · tier: Vision · measured OOS win 53.4% ROI 1.3% on 414 bets
-- backed: tg_under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_0043' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "dog_odds_vol_180" IS NOT NULL AND "dog_odds_vol_180" <= 0.8196
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:fresh_l_0261 =====
-- base: prop:fresh_lead · tier: Jobber · measured OOS win 45.2% ROI 1.3% on 378 bets
-- backed: prop:fresh_lead · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:fresh_l_0261' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "leader_drift" IS NOT NULL AND "leader_drift" >= 0.8495
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_SHORTENED_0070 =====
-- base: SHORTENED · tier: Jobber · measured OOS win 40.4% ROI 1.3% on 399 bets
-- backed: SHORTENED · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_SHORTENED_0070' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "u_ratio_open" IS NOT NULL AND "u_ratio_open" <= 3.0305
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:HT_Unde_0115 =====
-- base: spec:HT_Underdog_Fade · tier: Vision · measured OOS win 59% ROI 1.2% on 338 bets
-- backed: spec:HT_Underdog_Fade · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:HT_Unde_0115' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "imbal" IS NOT NULL AND "imbal" >= 0.0217 AND "lead_changes_300s" IS NOT NULL AND "lead_changes_300s" <= 1
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_dog_leading_0135 =====
-- base: dog_leading · tier: Vision · measured OOS win 59.1% ROI 1.1% on 316 bets
-- backed: dog_leading · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_dog_leading_0135' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "price_gap" IS NOT NULL AND "price_gap" >= 0.04 AND "lead_changes_300s" IS NOT NULL AND "lead_changes_300s" <= 2
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_pm_underdog_0116 =====
-- base: pm_underdog · tier: Vision · measured OOS win 58.3% ROI 1.1% on 325 bets
-- backed: pm_underdog · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_pm_underdog_0116' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "rk_dog_odds" IS NOT NULL AND "rk_dog_odds" >= 1.83
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0092 =====
-- base: late_lead_hold · tier: Npc · measured OOS win 65.9% ROI 1% on 351 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0092' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "u_price" IS NOT NULL AND "u_price" <= 1.7584
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:Leader_0057 =====
-- base: spec:Leader · tier: Npc · measured OOS win 63.6% ROI 1% on 354 bets
-- backed: spec:Leader · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:Leader_0057' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "dow" IS NOT NULL AND "dow" >= 2 AND "ten_mins" IS NOT NULL AND "ten_mins" >= 0.6949
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0222 =====
-- base: late_lead_hold · tier: Jobber · measured OOS win 44.8% ROI 1% on 335 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0222' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "trailer_price" IS NOT NULL AND "trailer_price" >= 1.34
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_lag_0124 =====
-- base: tg_under_lag · tier: Vision · measured OOS win 53.2% ROI 0.9% on 439 bets
-- backed: tg_under_lag · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_lag_0124' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "opp_pts_last_60s" IS NOT NULL AND "opp_pts_last_60s" <= 3
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:dog_lea_0174 =====
-- base: prop:dog_leading_late · tier: Jobber · measured OOS win 44.4% ROI 0.8% on 329 bets
-- backed: prop:dog_leading_late · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:dog_lea_0174' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "u_jump" IS NOT NULL AND "u_jump" <= 1.0162
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_0044 =====
-- base: tg_under · tier: Vision · measured OOS win 52.8% ROI 0.7% on 370 bets
-- backed: tg_under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_0044' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_m15" IS NOT NULL AND "lead_m15" <= 1
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:garbage_0057 =====
-- base: None · tier: Npc · measured OOS win 60.7% ROI 0.6% on 848 bets
-- backed: None · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:garbage_0057' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "odds_ratio" IS NOT NULL AND "odds_ratio" >= 0.0833
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:Leader_0055 =====
-- base: spec:Leader · tier: Npc · measured OOS win 63.8% ROI 0.6% on 328 bets
-- backed: spec:Leader · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:Leader_0055' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "scoring_rate" IS NOT NULL AND "scoring_rate" >= 1.8663 AND "imbal" IS NOT NULL AND "imbal" >= 0.0161
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0096 =====
-- base: late_lead_hold · tier: Npc · measured OOS win 61.1% ROI 0.6% on 298 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0096' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "u_vel_t" IS NOT NULL AND "u_vel_t" <= -0.0054 AND "wall_min" IS NOT NULL AND "wall_min" <= 171.805 AND "lead_m15" IS NOT NULL AND "lead_m15" <= 1
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:dog_lea_0180 =====
-- base: prop:dog_leading_late · tier: Jobber · measured OOS win 43.7% ROI 0.6% on 352 bets
-- backed: prop:dog_leading_late · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:dog_lea_0180' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "u_overround" IS NOT NULL AND "u_overround" <= 1.1109
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:dog_lea_0177 =====
-- base: prop:dog_leading_late · tier: Jobber · measured OOS win 43.2% ROI 0.6% on 303 bets
-- backed: prop:dog_leading_late · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:dog_lea_0177' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "rk_lead_off_high" IS NOT NULL AND "rk_lead_off_high" >= 0.2333 AND "lead_m15" IS NOT NULL AND "lead_m15" <= 1
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_lead_ml_0146 =====
-- base: lead_ml · tier: Jobber · measured OOS win 41.8% ROI 0.6% on 301 bets
-- backed: lead_ml · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_lead_ml_0146' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "tg_overround" IS NOT NULL AND "tg_overround" <= 1.123 AND "lead_changes_total" IS NOT NULL AND "lead_changes_total" <= 2
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_pm_underdog_0019 =====
-- base: pm_underdog · tier: Jobber · measured OOS win 39.6% ROI 0.6% on 326 bets
-- backed: pm_underdog · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_pm_underdog_0019' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "needed_rate_for_over" IS NOT NULL AND "needed_rate_for_over" >= 2.8864
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0079 =====
-- base: prop:first_scorer_hold · tier: Npc · measured OOS win 63.3% ROI 0.5% on 455 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0079' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "rk_dog_odds" IS NOT NULL AND "rk_dog_odds" >= 1.84
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_lead_ml_0061 =====
-- base: lead_ml · tier: Npc · measured OOS win 62.3% ROI 0.5% on 364 bets
-- backed: lead_ml · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_lead_ml_0061' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "u_hazard" IS NOT NULL AND "u_hazard" <= -0.1381 AND "margin_per_implied" IS NOT NULL AND "margin_per_implied" >= 1.48
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0095 =====
-- base: late_lead_hold · tier: Npc · measured OOS win 61.6% ROI 0.5% on 316 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0095' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "u_vel_t" IS NOT NULL AND "u_vel_t" <= -0.0052 AND "lead_m15" IS NOT NULL AND "lead_m15" <= 1 AND "total_points_over" IS NOT NULL AND "total_points_over" <= 1.93
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:TG_Hist_0003 =====
-- base: spec:TG_Historical_vs_Handicap_Under · tier: Vision · measured OOS win 56.2% ROI 0.5% on 369 bets
-- backed: spec:TG_Historical_vs_Handicap_Under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:TG_Hist_0003' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "needed_rate_for_over" IS NOT NULL AND "needed_rate_for_over" <= 3.716 AND "tg_overround" IS NOT NULL AND "tg_overround" >= 1.0873 AND "margin_per_implied" IS NOT NULL AND "margin_per_implied" >= 2.2315
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== LZ_V270_BASK_008 =====
-- base: None · tier: Jobber · measured OOS win 48.8% ROI 0.5% on 806 bets
-- backed: None · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'LZ_V270_BASK_008' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog" IS NOT NULL AND "prog" >= 0.3672 AND "lead_size" IS NOT NULL AND "lead_size" >= 4 AND "lead_odds" IS NOT NULL AND "lead_odds" >= 1.50
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:dog_lea_0178 =====
-- base: prop:dog_leading_late · tier: Jobber · measured OOS win 45.5% ROI 0.5% on 359 bets
-- backed: prop:dog_leading_late · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:dog_lea_0178' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1149
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:late_le_0204 =====
-- base: prop:late_lead_hold · tier: Npc · measured OOS win 63.5% ROI 0.4% on 319 bets
-- backed: prop:late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:late_le_0204' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "rk_fav_odds" IS NOT NULL AND "rk_fav_odds" <= 1.75 AND "margin_rate" IS NOT NULL AND "margin_rate" <= 6.6917
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:late_le_0203 =====
-- base: prop:late_lead_hold · tier: Vision · measured OOS win 59.6% ROI 0.4% on 300 bets
-- backed: prop:late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:late_le_0203' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "margin_range_300s" IS NOT NULL AND "margin_range_300s" >= 4 AND "home_odds" IS NOT NULL AND "home_odds" >= 1.58
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spread_dog_c_0106 =====
-- base: spread_dog_cover · tier: Vision · measured OOS win 55.8% ROI 0.4% on 307 bets
-- backed: spread_dog_cover · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spread_dog_c_0106' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "price_move_since_score" IS NOT NULL AND "price_move_since_score" <= 1 AND "pts_last_60s" IS NOT NULL AND "pts_last_60s" <= 5 AND "u_ratio_open" IS NOT NULL AND "u_ratio_open" >= 0.1537
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spread_dog_c_0236 =====
-- base: spread_dog_cover · tier: Vision · measured OOS win 54.8% ROI 0.4% on 316 bets
-- backed: spread_dog_cover · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spread_dog_c_0236' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "u_ratio_open" IS NOT NULL AND "u_ratio_open" <= 3.0613
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:dog_lea_0143 =====
-- base: prop:dog_leading_late · tier: Vision · measured OOS win 59.8% ROI 0.3% on 390 bets
-- backed: prop:dog_leading_late · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:dog_lea_0143' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "price_gap" IS NOT NULL AND "price_gap" >= 0.06
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:HT_Unde_0113 =====
-- base: spec:HT_Underdog_Fade · tier: Vision · measured OOS win 57% ROI 0.3% on 305 bets
-- backed: spec:HT_Underdog_Fade · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:HT_Unde_0113' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "h_score" IS NOT NULL AND "h_score" <= 25.3513
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:TG_Hist_0002 =====
-- base: spec:TG_Historical_vs_Handicap_Under · tier: Vision · measured OOS win 56.1% ROI 0.3% on 371 bets
-- backed: spec:TG_Historical_vs_Handicap_Under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:TG_Hist_0002' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_vs_line" IS NOT NULL AND "lead_vs_line" >= -178.5
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spread_dog_c_0109 =====
-- base: spread_dog_cover · tier: Vision · measured OOS win 55.7% ROI 0.2% on 333 bets
-- backed: spread_dog_cover · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spread_dog_c_0109' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "X_u_elapsed__minus__u_time_in_lead" IS NOT NULL AND "X_u_elapsed__minus__u_time_in_lead" >= 0.0019
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:dog_lea_0176 =====
-- base: prop:dog_leading_late · tier: Jobber · measured OOS win 44.7% ROI 0.2% on 384 bets
-- backed: prop:dog_leading_late · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:dog_lea_0176' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_flat" IS NOT NULL AND "line_flat" >= 2
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:fresh_l_0260 =====
-- base: prop:fresh_lead · tier: Jobber · measured OOS win 44.1% ROI 0.2% on 336 bets
-- backed: prop:fresh_lead · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:fresh_l_0260' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_m30" IS NOT NULL AND "lead_m30" <= 0
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0254 =====
-- base: prop:first_scorer_hold · tier: Feeder · measured OOS win 25.4% ROI 0.2% on 290 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0254' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_margin_m30" IS NOT NULL AND "lead_margin_m30" <= 3 AND "point_spread_home" IS NOT NULL AND "point_spread_home" >= 1.6575
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:Leader_0052 =====
-- base: spec:Leader · tier: Npc · measured OOS win 61.1% ROI 0.1% on 389 bets
-- backed: spec:Leader · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:Leader_0052' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "u_pace" IS NOT NULL AND "u_pace" <= 58.0667
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0076 =====
-- base: prop:first_scorer_hold · tier: Npc · measured OOS win 61.4% ROI 0.1% on 341 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0076' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "tg_imp_under" IS NOT NULL AND "tg_imp_under" <= 0.578
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0049 =====
-- base: prop:first_scorer_hold · tier: Vision · measured OOS win 52.2% ROI 14.1% on 321 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0049' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "line_move" IS NOT NULL AND "line_move" <= 4.0 AND "price_gap" IS NOT NULL AND "price_gap" <= 1.89 AND "X_minute__minus__u_drought" IS NOT NULL AND "X_minute__minus__u_drought" <= 27.3487 AND "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1237 AND "tg_imp_over" IS NOT NULL AND "tg_imp_over" <= 0.605
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:drifter_0027 =====
-- base: prop:drifter_vs_open · tier: Feeder · measured OOS win 21.8% ROI 17.5% on 300 bets
-- backed: prop:drifter_vs_open · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:drifter_0027' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "time_at_this_price" IS NOT NULL AND "time_at_this_price" <= 28.2017 AND "lead_vs_line" IS NOT NULL AND "lead_vs_line" <= -170.833 AND "X_home_odds__x__point_spread_away" IS NOT NULL AND "X_home_odds__x__point_spread_away" <= 10.82 AND "u_vol" IS NOT NULL AND "u_vol" >= 0.0119
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spread_dog_c_0156 =====
-- base: spread_dog_cover · tier: Vision · measured OOS win 58.5% ROI 12.5% on 306 bets
-- backed: spread_dog_cover · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spread_dog_c_0156' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "need_frac" IS NOT NULL AND "need_frac" >= 0.4 AND "price_gap" IS NOT NULL AND "price_gap" >= 0.06 AND "dog_odds_vol_180" IS NOT NULL AND "dog_odds_vol_180" <= 0.2508
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0169 =====
-- base: late_lead_hold · tier: Jobber · measured OOS win 46.9% ROI 11.3% on 301 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0169' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "need_frac" IS NOT NULL AND "need_frac" >= 0.4 AND "open_vs_now_lead" IS NOT NULL AND "open_vs_now_lead" <= 0.9043 AND "motif_0" IS NOT NULL AND "motif_0" <= 0.0 AND "X_minute__minus__u_drought" IS NOT NULL AND "X_minute__minus__u_drought" <= 26.2769 AND "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1237
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0062 =====
-- base: prop:first_scorer_hold · tier: Jobber · measured OOS win 48.4% ROI 11.1% on 320 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0062' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "line_move" IS NOT NULL AND "line_move" <= 4.0 AND "lead_vs_line" IS NOT NULL AND "lead_vs_line" <= -143.0 AND "u_ratio_open" IS NOT NULL AND "u_ratio_open" <= 2.9167 AND "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1237
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_0005 =====
-- base: tg_under · tier: Npc · measured OOS win 61.9% ROI 10.6% on 303 bets
-- backed: tg_under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_0005' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_q" IS NOT NULL AND "prog_q" >= 0.5 AND "u_open" IS NOT NULL AND "u_open" >= 1.2 AND "is_trailer" IS NOT NULL AND "is_trailer" <= 0.8231
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0110 =====
-- base: late_lead_hold · tier: Npc · measured OOS win 70.6% ROI 10.5% on 335 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0110' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "line_move" IS NOT NULL AND "line_move" <= 4.0 AND "odds_ratio" IS NOT NULL AND "odds_ratio" <= 1.227 AND "trailer_price" IS NOT NULL AND "trailer_price" >= 1.8518 AND "total_points_over" IS NOT NULL AND "total_points_over" <= 1.9442 AND "lead_changes_300s" IS NOT NULL AND "lead_changes_300s" <= 1.0
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:late_le_0181 =====
-- base: prop:late_lead_hold · tier: Jobber · measured OOS win 49.7% ROI 10.5% on 332 bets
-- backed: prop:late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:late_le_0181' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_size" IS NOT NULL AND "lead_size" >= 3.0 AND "u_ratio_open" IS NOT NULL AND "u_ratio_open" <= 2.8107 AND "open_gap" IS NOT NULL AND "open_gap" <= 3.55
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0056 =====
-- base: prop:first_scorer_hold · tier: Jobber · measured OOS win 49.3% ROI 8.5% on 286 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0056' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "line_move" IS NOT NULL AND "line_move" <= 4.0 AND "price_gap" IS NOT NULL AND "price_gap" <= 1.89 AND "opp_drift" IS NOT NULL AND "opp_drift" <= 1.0811 AND "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1237
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0060 =====
-- base: prop:first_scorer_hold · tier: Jobber · measured OOS win 47.1% ROI 8% on 298 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0060' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "lead_vs_line" IS NOT NULL AND "lead_vs_line" <= -150.5 AND "u_ratio_open" IS NOT NULL AND "u_ratio_open" <= 2.5724 AND "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1237
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spread_dog_c_0154 =====
-- base: spread_dog_cover · tier: Vision · measured OOS win 56% ROI 7.7% on 297 bets
-- backed: spread_dog_cover · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spread_dog_c_0154' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "lead_vs_line" IS NOT NULL AND "lead_vs_line" <= -140.5 AND "dog_odds_vol_180" IS NOT NULL AND "dog_odds_vol_180" <= 0.2346
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_DRIFTED_0046 =====
-- base: DRIFTED · tier: Jobber · measured OOS win 43.8% ROI 7.6% on 275 bets
-- backed: DRIFTED · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_DRIFTED_0046' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line" IS NOT NULL AND "line" >= 170.0 AND "pm_home" IS NOT NULL AND "pm_home" <= 2.6314
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:TG_Hist_0072 =====
-- base: spec:TG_Historical_vs_Handicap_Under · tier: Vision · measured OOS win 56.4% ROI 7.5% on 483 bets
-- backed: spec:TG_Historical_vs_Handicap_Under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:TG_Hist_0072' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog" IS NOT NULL AND "prog" >= 0.7151017554368007 AND "time_at_this_price" IS NOT NULL AND "time_at_this_price" < 52.915909090909054 AND "pace_last_300s_vs_line" IS NOT NULL AND "pace_last_300s_vs_line" >= -159.72
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0059 =====
-- base: prop:first_scorer_hold · tier: Jobber · measured OOS win 47.9% ROI 7.4% on 347 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0059' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "u_overround" IS NOT NULL AND "u_overround" <= 1.1109 AND "req_ratio" IS NOT NULL AND "req_ratio" <= 1.3805
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0054 =====
-- base: prop:first_scorer_hold · tier: Jobber · measured OOS win 47.9% ROI 7.1% on 318 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0054' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "rk_odds_ratio" IS NOT NULL AND "rk_odds_ratio" <= 2.3716 AND "leader_runmax" IS NOT NULL AND "leader_runmax" >= 1.8456 AND "tot_vs_line" IS NOT NULL AND "tot_vs_line" <= -128.8692
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0007 =====
-- base: None · tier: Jobber · measured OOS win 45.7% ROI 6.8% on 392 bets
-- backed: None · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0007' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "line_move" IS NOT NULL AND "line_move" <= 4.0 AND "price_gap" IS NOT NULL AND "price_gap" <= 1.89 AND "opp_drift" IS NOT NULL AND "opp_drift" <= 1.0811 AND "trail" IS NOT NULL AND "trail" <= 6.0
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0055 =====
-- base: prop:first_scorer_hold · tier: Jobber · measured OOS win 45.5% ROI 6.8% on 300 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0055' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "lead_vs_line" IS NOT NULL AND "lead_vs_line" <= -140.5 AND "req_ratio" IS NOT NULL AND "req_ratio" >= 1.195 AND "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1237
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_q1_winner_0139 =====
-- base: q1_winner · tier: Npc · measured OOS win 71.3% ROI 6.5% on 371 bets
-- backed: q1_winner · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_q1_winner_0139' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "X_q1_lead__minus__u_overround" IS NOT NULL AND "X_q1_lead__minus__u_overround" >= 1.88889 AND "rk_implied_edge" IS NOT NULL AND "rk_implied_edge" >= 0.0764
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0098 =====
-- base: prop:first_scorer_hold · tier: Npc · measured OOS win 67.9% ROI 6.4% on 344 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0098' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "rk_dog_odds" IS NOT NULL AND "rk_dog_odds" >= 1.8351 AND "opp" IS NOT NULL AND "opp" >= 1.91
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:drifter_0029 =====
-- base: prop:drifter_vs_open · tier: Feeder · measured OOS win 21.4% ROI 6.2% on 327 bets
-- backed: prop:drifter_vs_open · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:drifter_0029' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "pm_away" IS NOT NULL AND "pm_away" <= 2.92 AND "leader_runmax" IS NOT NULL AND "leader_runmax" <= 3.66 AND "secs_since_run_started" IS NOT NULL AND "secs_since_run_started" >= 0.0 AND "u_trend" IS NOT NULL AND "u_trend" <= -5.7851 AND "total_goals_handicap_ffill" IS NOT NULL AND "total_goals_handicap_ffill" <= 197.5
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:drifter_0026 =====
-- base: prop:drifter_vs_open · tier: Ino · measured OOS win 18.8% ROI 6% on 316 bets
-- backed: prop:drifter_vs_open · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:drifter_0026' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line" IS NOT NULL AND "line" >= 170.0 AND "X_minute__over__u_vol" IS NOT NULL AND "X_minute__over__u_vol" <= 2696.29 AND "both_scored" IS NOT NULL AND "both_scored" >= 1.0
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0115 =====
-- base: late_lead_hold · tier: Npc · measured OOS win 67.6% ROI 5.9% on 420 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0115' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "u_ratio_open" IS NOT NULL AND "u_ratio_open" <= 3.0575 AND "trailer_price" IS NOT NULL AND "trailer_price" >= 1.8315 AND "prematch_dog_odds" IS NOT NULL AND "prematch_dog_odds" >= 1.9465
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0103 =====
-- base: late_lead_hold · tier: Npc · measured OOS win 66.1% ROI 5.9% on 354 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0103' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "dog_odds_vol_180" IS NOT NULL AND "dog_odds_vol_180" <= 0.15546 AND "price_gap" IS NOT NULL AND "price_gap" >= 0.04 AND "lead_changes_300s" IS NOT NULL AND "lead_changes_300s" <= 2.0 AND "line_open" IS NOT NULL AND "line_open" >= 134.5
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:TG_Hist_0071 =====
-- base: spec:TG_Historical_vs_Handicap_Under · tier: Vision · measured OOS win 56.2% ROI 5.9% on 371 bets
-- backed: spec:TG_Historical_vs_Handicap_Under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:TG_Hist_0071' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" >= 0.45 AND "u_drift_opp" IS NOT NULL AND "u_drift_opp" <= 11.7818
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spread_dog_c_0118 =====
-- base: spread_dog_cover · tier: Vision · measured OOS win 58.7% ROI 5.6% on 305 bets
-- backed: spread_dog_cover · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spread_dog_c_0118' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "need_frac" IS NOT NULL AND "need_frac" >= 0.4 AND "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "lead_vs_line" IS NOT NULL AND "lead_vs_line" <= -154.5 AND "X_X_u_elapsed__minus__u_time_in_lead__over__X_u_pace__over__u_time_in_lead" IS NOT NULL AND "X_X_u_elapsed__minus__u_time_in_lead__over__X_u_pace__over__u_time_in_lead" >= -0.0083
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:garbage_0127 =====
-- base: prop:garbage_under · tier: Vision · measured OOS win 59.7% ROI 5.4% on 340 bets
-- backed: prop:garbage_under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:garbage_0127' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_m15" IS NOT NULL AND "lead_m15" <= 0.0 AND "u_overround" IS NOT NULL AND "u_overround" >= 1.0508
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_lag_0135 =====
-- base: tg_under_lag · tier: Vision · measured OOS win 55.1% ROI 5.3% on 361 bets
-- backed: tg_under_lag · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_lag_0135' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "need_frac" IS NOT NULL AND "need_frac" >= 0.4 AND "dog_odds_vol_180" IS NOT NULL AND "dog_odds_vol_180" >= 0.0469 AND "u_ratio_shift" IS NOT NULL AND "u_ratio_shift" >= 0.395
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0047 =====
-- base: prop:first_scorer_hold · tier: Jobber · measured OOS win 48.7% ROI 5.2% on 320 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0047' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "rk_odds_ratio" IS NOT NULL AND "rk_odds_ratio" <= 2.3716 AND "X_minute__minus__u_drought" IS NOT NULL AND "X_minute__minus__u_drought" <= 28.0923 AND "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1237
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0091 =====
-- base: prop:first_scorer_hold · tier: Npc · measured OOS win 64.7% ROI 5% on 331 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0091' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "total_points_under" IS NOT NULL AND "total_points_under" >= 1.75 AND "hour" IS NOT NULL AND "hour" >= 2.0 AND "away_odds" IS NOT NULL AND "away_odds" >= 1.159999966621399 AND "tot_vs_line" IS NOT NULL AND "tot_vs_line" <= -129.823 AND "opp" IS NOT NULL AND "opp" >= 1.53
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:held_le_0182 =====
-- base: prop:held_lead · tier: Jobber · measured OOS win 49% ROI 4.9% on 304 bets
-- backed: prop:held_lead · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:held_le_0182' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "rk_fav_odds" IS NOT NULL AND "rk_fav_odds" >= 1.23 AND "u_ratio_open" IS NOT NULL AND "u_ratio_open" <= 2.842
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:Loser_1_0023 =====
-- base: spec:Loser_1G · tier: Ino · measured OOS win 18.4% ROI 4.9% on 309 bets
-- backed: spec:Loser_1G · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:Loser_1_0023' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "time_at_this_price" IS NOT NULL AND "time_at_this_price" <= 28.2017 AND "lead_vs_line" IS NOT NULL AND "lead_vs_line" <= -170.833 AND "total_line_move" IS NOT NULL AND "total_line_move" >= -4.0 AND "lead_m30" IS NOT NULL AND "lead_m30" <= 1.0
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_lag_0136 =====
-- base: tg_under_lag · tier: Vision · measured OOS win 55.2% ROI 4.8% on 418 bets
-- backed: tg_under_lag · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_lag_0136' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "rk_lead_off_high" IS NOT NULL AND "rk_lead_off_high" >= 0.2333 AND "lead_m15" IS NOT NULL AND "lead_m15" <= 1.0 AND "line_vel" IS NOT NULL AND "line_vel" <= 1.0
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_lead_ml_0144 =====
-- base: lead_ml · tier: Jobber · measured OOS win 43.9% ROI 4.8% on 327 bets
-- backed: lead_ml · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_lead_ml_0144' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "need_frac" IS NOT NULL AND "need_frac" >= 0.4 AND "open_vs_now_lead" IS NOT NULL AND "open_vs_now_lead" <= 0.9043 AND "motif_0" IS NOT NULL AND "motif_0" <= 0.0 AND "X_minute__minus__u_drought" IS NOT NULL AND "X_minute__minus__u_drought" <= 26.2769 AND "req_ratio" IS NOT NULL AND "req_ratio" >= 1.1951
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:TG_Hist_0014 =====
-- base: spec:TG_Historical_vs_Handicap_Under · tier: Vision · measured OOS win 58.5% ROI 4.7% on 430 bets
-- backed: spec:TG_Historical_vs_Handicap_Under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:TG_Hist_0014' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "line_move" IS NOT NULL AND "line_move" <= 4.0 AND "price_gap" IS NOT NULL AND "price_gap" <= 1.89 AND "opp_drift" IS NOT NULL AND "opp_drift" <= 1.0811 AND "need_vs_expected" IS NOT NULL AND "need_vs_expected" <= 168.5
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:match_t_0073 =====
-- base: prop:match_total_pace · tier: Vision · measured OOS win 54.5% ROI 4.7% on 365 bets
-- backed: prop:match_total_pace · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:match_t_0073' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "rk_odds_ratio" IS NOT NULL AND "rk_odds_ratio" <= 2.6602 AND "lead_vs_line" IS NOT NULL AND "lead_vs_line" >= -173.8462
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_lag_0035 =====
-- base: tg_under_lag · tier: Vision · measured OOS win 58.8% ROI 4.6% on 380 bets
-- backed: tg_under_lag · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_lag_0035' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "time_at_this_price" IS NOT NULL AND "time_at_this_price" <= 28.2017 AND "u_vf" IS NOT NULL AND "u_vf" <= 0.8899 AND "ld_rank" IS NOT NULL AND "ld_rank" >= 0.625 AND "is_big_lead" IS NOT NULL AND "is_big_lead" >= 0.2821
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_lead_ml_0082 =====
-- base: lead_ml · tier: Npc · measured OOS win 69.8% ROI 4.3% on 330 bets
-- backed: lead_ml · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_lead_ml_0082' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "rk_implied_edge" IS NOT NULL AND "rk_implied_edge" >= 0.107 AND "u_drought" IS NOT NULL AND "u_drought" <= 71.4786
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_0068 =====
-- base: tg_under · tier: Vision · measured OOS win 54.9% ROI 4.3% on 412 bets
-- backed: tg_under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_0068' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog" IS NOT NULL AND "prog" >= 0.7151017554368007 AND "time_at_this_price" IS NOT NULL AND "time_at_this_price" < 52.915909090909054 AND "X_X_u_elapsed__minus__u_time_in_lead__over__X_u_pace__over__u_time_in_lead" IS NOT NULL AND "X_X_u_elapsed__minus__u_time_in_lead__over__X_u_pace__over__u_time_in_lead" <= 0.0249
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_0003 =====
-- base: tg_under · tier: Vision · measured OOS win 58.3% ROI 4.2% on 418 bets
-- backed: tg_under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_0003' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "line_move" IS NOT NULL AND "line_move" <= 4.0 AND "price_gap" IS NOT NULL AND "price_gap" <= 1.89 AND "opp_drift" IS NOT NULL AND "opp_drift" <= 1.0811 AND "tot_vs_line" IS NOT NULL AND "tot_vs_line" >= -174.5
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_lag_0132 =====
-- base: tg_under_lag · tier: Vision · measured OOS win 54.2% ROI 4.1% on 330 bets
-- backed: tg_under_lag · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_lag_0132' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "need_frac" IS NOT NULL AND "need_frac" >= 0.4 AND "trailer_price" IS NOT NULL AND "trailer_price" >= 1.8471 AND "pace_vs_line_pct" IS NOT NULL AND "pace_vs_line_pct" >= -0.9852 AND "eng_minute_ge_2" IS NOT NULL AND "eng_minute_ge_2" >= 1.0
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0166 =====
-- base: late_lead_hold · tier: Jobber · measured OOS win 46% ROI 4.1% on 319 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0166' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "rk_odds_ratio" IS NOT NULL AND "rk_odds_ratio" <= 2.3716 AND "X_minute__minus__u_drought" IS NOT NULL AND "X_minute__minus__u_drought" <= 28.0923 AND "lead_changes_300s" IS NOT NULL AND "lead_changes_300s" <= 2.0 AND "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1237
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_0012 =====
-- base: tg_under · tier: Vision · measured OOS win 58.7% ROI 4% on 805 bets
-- backed: tg_under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_0012' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "u_jump" IS NOT NULL AND "u_jump" >= 0.9449 AND "total_points_under" IS NOT NULL AND "total_points_under" <= 1.8
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:Leader_0077 =====
-- base: spec:Leader · tier: Npc · measured OOS win 67.3% ROI 3.7% on 353 bets
-- backed: spec:Leader · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:Leader_0077' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "u_price" IS NOT NULL AND "u_price" <= 1.7584 AND "overround_now" IS NOT NULL AND "overround_now" <= 1.1135
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:TG_Hist_0018 =====
-- base: spec:TG_Historical_vs_Handicap_Under · tier: Vision · measured OOS win 58.5% ROI 3.7% on 555 bets
-- backed: spec:TG_Historical_vs_Handicap_Under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:TG_Hist_0018' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "wall_min" IS NOT NULL AND "wall_min" <= 171.805 AND "lead_m15" IS NOT NULL AND "lead_m15" <= 1.0 AND "tg_imp_under" IS NOT NULL AND "tg_imp_under" >= 0.5435 AND "secs_since_price" IS NOT NULL AND "secs_since_price" <= 0.0
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:TG_Hist_0069 =====
-- base: spec:TG_Historical_vs_Handicap_Under · tier: Vision · measured OOS win 54% ROI 3.6% on 365 bets
-- backed: spec:TG_Historical_vs_Handicap_Under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:TG_Hist_0069' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "rk_odds_ratio" IS NOT NULL AND "rk_odds_ratio" <= 2.6602 AND "lead_vs_line" IS NOT NULL AND "lead_vs_line" >= -173.8462
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:HT_Unde_0122 =====
-- base: spec:HT_Underdog_Fade · tier: Vision · measured OOS win 59.4% ROI 3.5% on 321 bets
-- backed: spec:HT_Underdog_Fade · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:HT_Unde_0122' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "rk_dog_odds" IS NOT NULL AND "rk_dog_odds" >= 1.8351 AND "lead_changes_300s" IS NOT NULL AND "lead_changes_300s" <= 2.0
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_0001 =====
-- base: tg_under · tier: Vision · measured OOS win 57.7% ROI 3.5% on 338 bets
-- backed: tg_under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_0001' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "rk_odds_ratio" IS NOT NULL AND "rk_odds_ratio" <= 2.6602 AND "prog_q" IS NOT NULL AND "prog_q" >= 0.4444 AND "tg_proj_vs_line" IS NOT NULL AND "tg_proj_vs_line" >= -167.5197
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0052 =====
-- base: prop:first_scorer_hold · tier: Jobber · measured OOS win 46.9% ROI 3.5% on 326 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0052' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "rk_odds_ratio" IS NOT NULL AND "rk_odds_ratio" <= 2.3716 AND "total_line_move" IS NOT NULL AND "total_line_move" >= -4.0
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0114 =====
-- base: late_lead_hold · tier: Npc · measured OOS win 63.4% ROI 3.4% on 338 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0114' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "dog_odds_vol_180" IS NOT NULL AND "dog_odds_vol_180" <= 0.15546 AND "u_pace" IS NOT NULL AND "u_pace" <= 58.0667 AND "needed_rate_for_over" IS NOT NULL AND "needed_rate_for_over" >= 2.9473
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0058 =====
-- base: prop:first_scorer_hold · tier: Jobber · measured OOS win 46.3% ROI 3.4% on 314 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0058' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "lead_vs_line" IS NOT NULL AND "lead_vs_line" <= -151.823 AND "opp_o" IS NOT NULL AND "opp_o" <= 4.17 AND "px_gap_ratio" IS NOT NULL AND "px_gap_ratio" <= 1.442
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0051 =====
-- base: prop:first_scorer_hold · tier: Jobber · measured OOS win 46.2% ROI 3.3% on 329 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0051' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "lead_vs_line" IS NOT NULL AND "lead_vs_line" <= -140.5 AND "secs_since_quarter_start" IS NOT NULL AND "secs_since_quarter_start" <= 41.6871 AND "rk_fav_odds" IS NOT NULL AND "rk_fav_odds" >= 1.2738
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0111 =====
-- base: late_lead_hold · tier: Npc · measured OOS win 67.6% ROI 3.2% on 343 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0111' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "rk_fav_odds" IS NOT NULL AND "rk_fav_odds" <= 1.75 AND "u_overround" IS NOT NULL AND "u_overround" <= 1.113
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_h1_winner_0177 =====
-- base: h1_winner · tier: Npc · measured OOS win 67.3% ROI 3.2% on 328 bets
-- backed: h1_winner · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_h1_winner_0177' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "time_at_this_price" IS NOT NULL AND "time_at_this_price" <= 28.2017 AND "price_rank_in_match" IS NOT NULL AND "price_rank_in_match" >= 0.3333 AND "u_flat" IS NOT NULL AND "u_flat" <= 0.4769
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0096 =====
-- base: prop:first_scorer_hold · tier: Npc · measured OOS win 64.2% ROI 3.2% on 323 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0096' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "odds_ratio" IS NOT NULL AND "odds_ratio" <= 1.2094 AND "pts_last_60s" IS NOT NULL AND "pts_last_60s" <= 6.0 AND "lead_m15" IS NOT NULL AND "lead_m15" <= 1.0 AND "rk_odds_ratio" IS NOT NULL AND "rk_odds_ratio" >= 1.0318
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:TG_Hist_0070 =====
-- base: spec:TG_Historical_vs_Handicap_Under · tier: Vision · measured OOS win 54.4% ROI 3.2% on 339 bets
-- backed: spec:TG_Historical_vs_Handicap_Under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:TG_Hist_0070' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "price_rank_in_match" IS NOT NULL AND "price_rank_in_match" >= 0.3636365114164554 AND "secs_since_score" IS NOT NULL AND "secs_since_score" < 97.0 AND "X_minute__over__u_pace" IS NOT NULL AND "X_minute__over__u_pace" <= 1.5057
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:match_t_0021 =====
-- base: prop:match_total_pace · tier: Vision · measured OOS win 57.7% ROI 3.1% on 414 bets
-- backed: prop:match_total_pace · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:match_t_0021' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "line_move" IS NOT NULL AND "line_move" <= 4.0 AND "price_gap" IS NOT NULL AND "price_gap" <= 1.89 AND "opp_drift" IS NOT NULL AND "opp_drift" <= 1.0811 AND "trail" IS NOT NULL AND "trail" <= 6.0 AND "pace_last_300s_vs_line" IS NOT NULL AND "pace_last_300s_vs_line" >= -174.5
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:HT_Unde_0123 =====
-- base: spec:HT_Underdog_Fade · tier: Vision · measured OOS win 59.5% ROI 3% on 331 bets
-- backed: spec:HT_Underdog_Fade · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:HT_Unde_0123' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "total_points_under" IS NOT NULL AND "total_points_under" >= 1.75 AND "pm_away" IS NOT NULL AND "pm_away" <= 2.85 AND "X_X_u_elapsed__minus__u_time_in_lead__minus__X_u_pace__over__u_time_in_lead" IS NOT NULL AND "X_X_u_elapsed__minus__u_time_in_lead__minus__X_u_pace__over__u_time_in_lead" <= -19.1179 AND "u_vel_t" IS NOT NULL AND "u_vel_t" <= 0.0198
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0053 =====
-- base: prop:first_scorer_hold · tier: Jobber · measured OOS win 45.9% ROI 3% on 314 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0053' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "need_frac" IS NOT NULL AND "need_frac" >= 0.4 AND "prog_score" IS NOT NULL AND "prog_score" <= 0.1318 AND "px_gap_ratio" IS NOT NULL AND "px_gap_ratio" <= 1.4925
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_0004 =====
-- base: tg_under · tier: Vision · measured OOS win 57.6% ROI 2.9% on 343 bets
-- backed: tg_under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_0004' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "line_move" IS NOT NULL AND "line_move" <= 4.0 AND "price_gap" IS NOT NULL AND "price_gap" <= 1.89 AND "opp_drift" IS NOT NULL AND "opp_drift" <= 1.0811 AND "trail" IS NOT NULL AND "trail" <= 6.0 AND "pace_last_300s_vs_line" IS NOT NULL AND "pace_last_300s_vs_line" >= -185.8205
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_lag_0037 =====
-- base: tg_under_lag · tier: Vision · measured OOS win 57.5% ROI 2.9% on 466 bets
-- backed: tg_under_lag · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_lag_0037' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "line_move" IS NOT NULL AND "line_move" <= 4.0 AND "price_gap" IS NOT NULL AND "price_gap" <= 1.89 AND "tg_proj_vs_line" IS NOT NULL AND "tg_proj_vs_line" >= -168.5
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:late_le_0157 =====
-- base: prop:late_lead_hold · tier: Npc · measured OOS win 62.8% ROI 2.8% on 339 bets
-- backed: prop:late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:late_le_0157' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "dog_odds_vol_180" IS NOT NULL AND "dog_odds_vol_180" <= 0.15546 AND "price_gap" IS NOT NULL AND "price_gap" >= 0.04 AND "lead_changes_300s" IS NOT NULL AND "lead_changes_300s" <= 2.0 AND "line_flat" IS NOT NULL AND "line_flat" >= 1.0
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0102 =====
-- base: late_lead_hold · tier: Npc · measured OOS win 63.2% ROI 2.7% on 314 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0102' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "pm_away" IS NOT NULL AND "pm_away" <= 2.92 AND "leader_runmax" IS NOT NULL AND "leader_runmax" <= 3.66 AND "lead_vs_med" IS NOT NULL AND "lead_vs_med" <= 1.175 AND "rk_dog_odds" IS NOT NULL AND "rk_dog_odds" >= 1.83 AND "u_trend" IS NOT NULL AND "u_trend" <= -2.6556
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:TG_Hist_0017 =====
-- base: spec:TG_Historical_vs_Handicap_Under · tier: Vision · measured OOS win 57.3% ROI 2.5% on 447 bets
-- backed: spec:TG_Historical_vs_Handicap_Under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:TG_Hist_0017' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "line_move" IS NOT NULL AND "line_move" <= 4.0 AND "price_gap" IS NOT NULL AND "price_gap" <= 1.89 AND "tg_proj_vs_line" IS NOT NULL AND "tg_proj_vs_line" >= -168.1895
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_lag_0134 =====
-- base: tg_under_lag · tier: Vision · measured OOS win 53.8% ROI 2.5% on 559 bets
-- backed: tg_under_lag · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_lag_0134' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "total_points_under" IS NOT NULL AND "total_points_under" >= 1.75 AND "hour" IS NOT NULL AND "hour" >= 2.0 AND "away_odds" IS NOT NULL AND "away_odds" >= 1.159999966621399 AND "prog" IS NOT NULL AND "prog" >= 0.25 AND "recovery_x" IS NOT NULL AND "recovery_x" >= 1.0 AND "pace_last_300s_vs_line" IS NOT NULL AND "pace_last_300s_vs_line" >= -156.5908
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0112 =====
-- base: late_lead_hold · tier: Npc · measured OOS win 63.9% ROI 2.4% on 387 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0112' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "pm_ratio" IS NOT NULL AND "pm_ratio" <= 3.6843 AND "imbal" IS NOT NULL AND "imbal" >= 0.0164 AND "line_flat" IS NOT NULL AND "line_flat" >= 0.8667 AND "trailer_drift" IS NOT NULL AND "trailer_drift" >= 1.0222
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spread_dog_c_0155 =====
-- base: spread_dog_cover · tier: Vision · measured OOS win 53.4% ROI 2.4% on 439 bets
-- backed: spread_dog_cover · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spread_dog_c_0155' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "rk_dog_odds" IS NOT NULL AND "rk_dog_odds" >= 1.8392 AND "lead_changes_300s" IS NOT NULL AND "lead_changes_300s" <= 1.0
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_lag_0031 =====
-- base: tg_under_lag · tier: Vision · measured OOS win 57.1% ROI 2.3% on 433 bets
-- backed: tg_under_lag · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_lag_0031' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "time_at_this_price" IS NOT NULL AND "time_at_this_price" <= 28.2017 AND "u_vf" IS NOT NULL AND "u_vf" <= 0.8899 AND "abs_margin_per_remaining_s" IS NOT NULL AND "abs_margin_per_remaining_s" >= 0.0004 AND "lead_vs_line" IS NOT NULL AND "lead_vs_line" >= -180.341
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spread_dog_c_0119 =====
-- base: spread_dog_cover · tier: Vision · measured OOS win 56.8% ROI 2.3% on 360 bets
-- backed: spread_dog_cover · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spread_dog_c_0119' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "need_frac" IS NOT NULL AND "need_frac" >= 0.4 AND "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "u_ratio_open" IS NOT NULL AND "u_ratio_open" <= 3.0051 AND "point_spread_handicap" IS NOT NULL AND "point_spread_handicap" <= -1.5
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_dog_leading_0147 =====
-- base: dog_leading · tier: Jobber · measured OOS win 41.6% ROI 2.3% on 322 bets
-- backed: dog_leading · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_dog_leading_0147' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "X_X_minute__x__u_pace__minus__X_u_elapsed__minus__u_time_in_lead" IS NOT NULL AND "X_X_minute__x__u_pace__minus__X_u_elapsed__minus__u_time_in_lead" <= 169.724 AND "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1237
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:Leader_0079 =====
-- base: spec:Leader · tier: Npc · measured OOS win 62.7% ROI 2.2% on 356 bets
-- backed: spec:Leader · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:Leader_0079' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "dog_odds_vol_180" IS NOT NULL AND "dog_odds_vol_180" <= 0.1733 AND "req_ratio" IS NOT NULL AND "req_ratio" <= 1.3177
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_0006 =====
-- base: tg_under · tier: Vision · measured OOS win 57.4% ROI 2.2% on 408 bets
-- backed: tg_under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_0006' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "u_hazard" IS NOT NULL AND "u_hazard" <= -0.1409 AND "needed_rate_for_over" IS NOT NULL AND "needed_rate_for_over" <= 3.6355 AND "prog_q" IS NOT NULL AND "prog_q" >= 0.0456
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:shorten_0163 =====
-- base: prop:shortener_vs_open · tier: Jobber · measured OOS win 45.1% ROI 2.1% on 332 bets
-- backed: prop:shortener_vs_open · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:shorten_0163' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "pm_ratio" IS NOT NULL AND "pm_ratio" <= 3.6843 AND "imbal" IS NOT NULL AND "imbal" >= 0.0164 AND "u_jump" IS NOT NULL AND "u_jump" >= 0.9459
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0104 =====
-- base: late_lead_hold · tier: Npc · measured OOS win 63.3% ROI 2% on 337 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0104' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "h_score" IS NOT NULL AND "h_score" <= 16.1205 AND "u_hazard" IS NOT NULL AND "u_hazard" <= -0.1356 AND "opp_velocity" IS NOT NULL AND "opp_velocity" >= -0.0842
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_lead_ml_0085 =====
-- base: lead_ml · tier: Npc · measured OOS win 63.3% ROI 2% on 322 bets
-- backed: lead_ml · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_lead_ml_0085' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "dog_odds_vol_180" IS NOT NULL AND "dog_odds_vol_180" <= 0.15546 AND "X_minute__over__u_vol" IS NOT NULL AND "X_minute__over__u_vol" <= 353.579 AND "home_pace" IS NOT NULL AND "home_pace" <= 23.5316
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_lead_ml_0086 =====
-- base: lead_ml · tier: Npc · measured OOS win 63.6% ROI 1.9% on 396 bets
-- backed: lead_ml · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_lead_ml_0086' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "nchg_120" IS NOT NULL AND "nchg_120" <= 2.0 AND "retrace_lead" IS NOT NULL AND "retrace_lead" <= 0.3623 AND "dow" IS NOT NULL AND "dow" >= 2.0
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:garbage_0128 =====
-- base: prop:garbage_under · tier: Vision · measured OOS win 57.8% ROI 1.9% on 307 bets
-- backed: prop:garbage_under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:garbage_0128' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "total_line_move" IS NOT NULL AND "total_line_move" >= -5.8769 AND "total_line_move" IS NOT NULL AND "total_line_move" >= 0.0
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_0067 =====
-- base: tg_under · tier: Vision · measured OOS win 53.1% ROI 1.9% on 300 bets
-- backed: tg_under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_0067' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "rk_odds_ratio" IS NOT NULL AND "rk_odds_ratio" <= 2.6602 AND "u_drift_opp" IS NOT NULL AND "u_drift_opp" >= 0.7584 AND "motif_2" IS NOT NULL AND "motif_2" <= 0.0 AND "prog_x_margin" IS NOT NULL AND "prog_x_margin" >= -2.5026 AND "X_u_elapsed__minus__u_time_in_lead" IS NOT NULL AND "X_u_elapsed__minus__u_time_in_lead" >= -0.2546
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0168 =====
-- base: late_lead_hold · tier: Jobber · measured OOS win 45.8% ROI 1.8% on 412 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0168' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "trailer_price" IS NOT NULL AND "trailer_price" >= 1.34 AND "retrace_lead" IS NOT NULL AND "retrace_lead" <= 0.0439
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0065 =====
-- base: prop:first_scorer_hold · tier: Jobber · measured OOS win 45.8% ROI 1.8% on 316 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0065' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "need_frac" IS NOT NULL AND "need_frac" >= 0.4 AND "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "rk_odds_ratio" IS NOT NULL AND "rk_odds_ratio" <= 2.8601 AND "margin_per_possession" IS NOT NULL AND "margin_per_possession" <= 0.0166 AND "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1236
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:dog_lea_0151 =====
-- base: prop:dog_leading_late · tier: Jobber · measured OOS win 43.8% ROI 1.8% on 339 bets
-- backed: prop:dog_leading_late · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:dog_lea_0151' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "u_overround" IS NOT NULL AND "u_overround" <= 1.1109 AND "u_ratio_open" IS NOT NULL AND "u_ratio_open" <= 2.8919 AND "tg_imp_over" IS NOT NULL AND "tg_imp_over" <= 0.6235
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0064 =====
-- base: prop:first_scorer_hold · tier: Jobber · measured OOS win 43.6% ROI 1.8% on 337 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0064' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "need_frac" IS NOT NULL AND "need_frac" >= 0.4 AND "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "rk_odds_ratio" IS NOT NULL AND "rk_odds_ratio" <= 2.8601 AND "wall_min" IS NOT NULL AND "wall_min" >= 138.5
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:Leader_0078 =====
-- base: spec:Leader · tier: Npc · measured OOS win 64.4% ROI 1.7% on 338 bets
-- backed: spec:Leader · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:Leader_0078' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "loser_runmax" IS NOT NULL AND "loser_runmax" >= 1.8722 AND "dow" IS NOT NULL AND "dow" >= 1.041
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:drifter_0028 =====
-- base: prop:drifter_vs_open · tier: Feeder · measured OOS win 20% ROI 1.7% on 348 bets
-- backed: prop:drifter_vs_open · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:drifter_0028' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "pm_away" IS NOT NULL AND "pm_away" <= 2.92 AND "leader_runmax" IS NOT NULL AND "leader_runmax" <= 3.66 AND "secs_since_run_started" IS NOT NULL AND "secs_since_run_started" >= 0.0 AND "fav_lead_m30" IS NOT NULL AND "fav_lead_m30" >= -1.0 AND "u_trend" IS NOT NULL AND "u_trend" <= -6.5658
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0093 =====
-- base: prop:first_scorer_hold · tier: Npc · measured OOS win 62.1% ROI 1.6% on 418 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0093' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "q" IS NOT NULL AND "q" <= 1.0 AND "lead_m30" IS NOT NULL AND "lead_m30" <= 1.0 AND "opp" IS NOT NULL AND "opp" >= 1.5927
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_0008 =====
-- base: tg_under · tier: Vision · measured OOS win 59.1% ROI 1.6% on 239 bets
-- backed: tg_under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_0008' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "time_at_this_price" IS NOT NULL AND "time_at_this_price" <= 28.2017 AND "price_rank_in_match" IS NOT NULL AND "price_rank_in_match" >= 0.3333 AND "tg_imp_under" IS NOT NULL AND "tg_imp_under" >= 0.5682
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_DRIFTED_0121 =====
-- base: DRIFTED · tier: Npc · measured OOS win 65.4% ROI 1.5% on 351 bets
-- backed: DRIFTED · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_DRIFTED_0121' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "rk_lead_off_low" IS NOT NULL AND "rk_lead_off_low" <= 2.1135 AND "imbal" IS NOT NULL AND "imbal" >= 0.0217 AND "u_ratio_open" IS NOT NULL AND "u_ratio_open" >= 0.6317
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:late_le_0158 =====
-- base: prop:late_lead_hold · tier: Npc · measured OOS win 61.5% ROI 1.5% on 336 bets
-- backed: prop:late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:late_le_0158' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "dog_odds_vol_180" IS NOT NULL AND "dog_odds_vol_180" <= 0.15546 AND "margin_range_300s" IS NOT NULL AND "margin_range_300s" >= 4.0 AND "ten_mins" IS NOT NULL AND "ten_mins" <= 23.4436
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:shorten_0152 =====
-- base: prop:shortener_vs_open · tier: Npc · measured OOS win 62% ROI 1.5% on 317 bets
-- backed: prop:shortener_vs_open · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:shorten_0152' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_size" IS NOT NULL AND "lead_size" >= 3.0 AND "u_ratio_open" IS NOT NULL AND "u_ratio_open" <= 2.8107 AND "scores_300s" IS NOT NULL AND "scores_300s" <= 4.0 AND "fav_odds_trend_60" IS NOT NULL AND "fav_odds_trend_60" <= 0.2231 AND "ten_mins" IS NOT NULL AND "ten_mins" <= 23.0897
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spread_dog_c_0120 =====
-- base: spread_dog_cover · tier: Vision · measured OOS win 56.4% ROI 1.5% on 309 bets
-- backed: spread_dog_cover · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spread_dog_c_0120' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "retrace_lead" IS NOT NULL AND "retrace_lead" <= 0.3163 AND "secs_since_run_started" IS NOT NULL AND "secs_since_run_started" >= 0.0 AND "X_u_elapsed__minus__u_time_in_lead" IS NOT NULL AND "X_u_elapsed__minus__u_time_in_lead" >= -0.1992
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0095 =====
-- base: prop:first_scorer_hold · tier: Npc · measured OOS win 65.7% ROI 1.4% on 328 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0095' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "total_points_under" IS NOT NULL AND "total_points_under" >= 1.75 AND "hour" IS NOT NULL AND "hour" >= 2.0 AND "away_odds" IS NOT NULL AND "away_odds" >= 1.159999966621399 AND "total_points_over" IS NOT NULL AND "total_points_over" < 1.899999976158142 AND "tot_vs_line" IS NOT NULL AND "tot_vs_line" <= -112.908 AND "rk_odds_ratio" IS NOT NULL AND "rk_odds_ratio" >= 1.0867
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_DRIFTED_0045 =====
-- base: DRIFTED · tier: Jobber · measured OOS win 41.9% ROI 1.4% on 328 bets
-- backed: DRIFTED · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_DRIFTED_0045' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_vs_line" IS NOT NULL AND "lead_vs_line" <= -160.315 AND "open_gap" IS NOT NULL AND "open_gap" <= 2.2797 AND "pm_away" IS NOT NULL AND "pm_away" >= 1.4
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:fresh_l_0183 =====
-- base: prop:fresh_lead · tier: Npc · measured OOS win 64.2% ROI 1.3% on 369 bets
-- backed: prop:fresh_lead · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:fresh_l_0183' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "lead_vs_med" IS NOT NULL AND "lead_vs_med" <= 1.0759 AND "margin_range_300s" IS NOT NULL AND "margin_range_300s" <= 5.0
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spread_dog_c_0178 =====
-- base: spread_dog_cover · tier: Jobber · measured OOS win 49.2% ROI 1.3% on 350 bets
-- backed: spread_dog_cover · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spread_dog_c_0178' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "price_move_since_score" IS NOT NULL AND "price_move_since_score" <= 1.0 AND "u_overround" IS NOT NULL AND "u_overround" <= 1.1091
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_dog_leading_0146 =====
-- base: dog_leading · tier: Jobber · measured OOS win 42.6% ROI 1.3% on 330 bets
-- backed: dog_leading · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_dog_leading_0146' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "need_frac" IS NOT NULL AND "need_frac" >= 0.4 AND "open_vs_now_lead" IS NOT NULL AND "open_vs_now_lead" <= 0.9043 AND "motif_0" IS NOT NULL AND "motif_0" <= 0.0 AND "X_minute__minus__u_drought" IS NOT NULL AND "X_minute__minus__u_drought" <= 26.2769 AND "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1172
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0094 =====
-- base: prop:first_scorer_hold · tier: Npc · measured OOS win 64.8% ROI 1.2% on 321 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0094' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "rk_fav_odds" IS NOT NULL AND "rk_fav_odds" <= 1.75 AND "leader_implied_gap" IS NOT NULL AND "leader_implied_gap" >= -0.1701 AND "tg_px_ratio" IS NOT NULL AND "tg_px_ratio" <= 1.1098
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:TG_Hist_0016 =====
-- base: spec:TG_Historical_vs_Handicap_Under · tier: Vision · measured OOS win 56.8% ROI 1.2% on 411 bets
-- backed: spec:TG_Historical_vs_Handicap_Under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:TG_Hist_0016' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "time_at_this_price" IS NOT NULL AND "time_at_this_price" <= 28.2017 AND "u_vf" IS NOT NULL AND "u_vf" <= 0.8899 AND "ld_rank" IS NOT NULL AND "ld_rank" >= 0.625 AND "u_margin_abs" IS NOT NULL AND "u_margin_abs" >= 2.0 AND "total_points_under" IS NOT NULL AND "total_points_under" <= 1.8388
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:HT_Unde_0042 =====
-- base: spec:HT_Underdog_Fade · tier: Jobber · measured OOS win 41.6% ROI 1.2% on 278 bets
-- backed: spec:HT_Underdog_Fade · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:HT_Unde_0042' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "need_frac" IS NOT NULL AND "need_frac" >= 0.4 AND "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "rk_odds_ratio" IS NOT NULL AND "rk_odds_ratio" <= 2.8601 AND "margin_per_possession" IS NOT NULL AND "margin_per_possession" <= 0.0166 AND "u_price_open" IS NOT NULL AND "u_price_open" >= 1.34
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_dog_leading_0141 =====
-- base: dog_leading · tier: Vision · measured OOS win 59.1% ROI 1.1% on 316 bets
-- backed: dog_leading · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_dog_leading_0141' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "dog_odds_vol_180" IS NOT NULL AND "dog_odds_vol_180" <= 0.15546 AND "price_gap" IS NOT NULL AND "price_gap" >= 0.04 AND "lead_changes_300s" IS NOT NULL AND "lead_changes_300s" <= 2.0
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_0007 =====
-- base: tg_under · tier: Vision · measured OOS win 56.5% ROI 1% on 314 bets
-- backed: tg_under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_0007' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "time_at_this_price" IS NOT NULL AND "time_at_this_price" <= 28.2017 AND "u_vf" IS NOT NULL AND "u_vf" <= 0.8899 AND "ld_rank" IS NOT NULL AND "ld_rank" >= 0.625 AND "is_big_lead" IS NOT NULL AND "is_big_lead" >= 1.0 AND "tg_imp_under" IS NOT NULL AND "tg_imp_under" >= 0.5462
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_lead_ml_0080 =====
-- base: lead_ml · tier: Npc · measured OOS win 62.6% ROI 0.9% on 339 bets
-- backed: lead_ml · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_lead_ml_0080' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "dog_odds_vol_180" IS NOT NULL AND "dog_odds_vol_180" <= 0.15546 AND "u_pace" IS NOT NULL AND "u_pace" <= 58.0667 AND "home_pace" IS NOT NULL AND "home_pace" >= 2.0
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_lag_0038 =====
-- base: tg_under_lag · tier: Vision · measured OOS win 56.2% ROI 0.9% on 353 bets
-- backed: tg_under_lag · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_lag_0038' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "u_ratio_open" IS NOT NULL AND "u_ratio_open" <= 3.0575 AND "u_drought" IS NOT NULL AND "u_drought" <= 1241.51 AND "trailer_drift" IS NOT NULL AND "trailer_drift" <= 1.0594
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_dog_leading_0150 =====
-- base: dog_leading · tier: Jobber · measured OOS win 41% ROI 0.9% on 310 bets
-- backed: dog_leading · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_dog_leading_0150' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "lead_vs_line" IS NOT NULL AND "lead_vs_line" <= -151.823 AND "opp_o" IS NOT NULL AND "opp_o" <= 4.17 AND "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1237 AND "time_left" IS NOT NULL AND "time_left" <= 0.8889
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:first_s_0099 =====
-- base: prop:first_scorer_hold · tier: Npc · measured OOS win 62.3% ROI 0.8% on 339 bets
-- backed: prop:first_scorer_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:first_s_0099' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "h_score" IS NOT NULL AND "h_score" <= 16.1205 AND "is_favorite" IS NOT NULL AND "is_favorite" >= 1.0
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:Leader_0075 =====
-- base: spec:Leader · tier: Npc · measured OOS win 63.4% ROI 0.8% on 330 bets
-- backed: spec:Leader · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:Leader_0075' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "rk_dog_odds" IS NOT NULL AND "rk_dog_odds" >= 1.8392 AND "X_u_elapsed__minus__u_time_in_lead" IS NOT NULL AND "X_u_elapsed__minus__u_time_in_lead" <= 1.0763 AND "total_points_handicap" IS NOT NULL AND "total_points_handicap" >= 150.4538
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:held_le_0172 =====
-- base: prop:held_lead · tier: Npc · measured OOS win 65.1% ROI 0.7% on 368 bets
-- backed: prop:held_lead · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:held_le_0172' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "loser_runmin" IS NOT NULL AND "loser_runmin" >= 1.2769 AND "overround_now" IS NOT NULL AND "overround_now" <= 1.121
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0113 =====
-- base: late_lead_hold · tier: Npc · measured OOS win 62.4% ROI 0.7% on 361 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0113' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "lead_vs_line" IS NOT NULL AND "lead_vs_line" <= -151.823 AND "opp_o" IS NOT NULL AND "opp_o" <= 4.17 AND "pace_last_300s_vs_line" IS NOT NULL AND "pace_last_300s_vs_line" >= -171.0969
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_dog_leading_0149 =====
-- base: dog_leading · tier: Jobber · measured OOS win 41.3% ROI 0.7% on 348 bets
-- backed: dog_leading · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_dog_leading_0149' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "pace_last_300s_vs_line" IS NOT NULL AND "pace_last_300s_vs_line" <= -115.005 AND "tg_overround" IS NOT NULL AND "tg_overround" <= 1.1237
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:q4_clos_0129 =====
-- base: prop:q4_close_trailer · tier: Feeder · measured OOS win 20.8% ROI 0.5% on 311 bets
-- backed: prop:q4_close_trailer · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:q4_clos_0129' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "pm_away" IS NOT NULL AND "pm_away" <= 2.92 AND "rk_fav_odds" IS NOT NULL AND "rk_fav_odds" >= 1.0142 AND "lead_change_last_300s" IS NOT NULL AND "lead_change_last_300s" >= -5.0
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:h1_lead_0175 =====
-- base: prop:h1_leader_hold · tier: Npc · measured OOS win 64.2% ROI 0.4% on 369 bets
-- backed: prop:h1_leader_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:h1_lead_0175' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "rk_lead_off_high" IS NOT NULL AND "rk_lead_off_high" >= 0.2333 AND "lead_m15" IS NOT NULL AND "lead_m15" <= 1.0 AND "X_point_spread_home__x__q1_lead" IS NOT NULL AND "X_point_spread_home__x__q1_lead" >= 1.8237
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_late_lead_ho_0106 =====
-- base: late_lead_hold · tier: Npc · measured OOS win 62.7% ROI 0.4% on 335 bets
-- backed: late_lead_hold · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_late_lead_ho_0106' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "rk_dog_odds" IS NOT NULL AND "rk_dog_odds" >= 1.8392 AND "X_u_elapsed__minus__u_time_in_lead" IS NOT NULL AND "X_u_elapsed__minus__u_time_in_lead" <= 1.0763 AND "wall_min" IS NOT NULL AND "wall_min" >= 131.5
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_0066 =====
-- base: tg_under · tier: Vision · measured OOS win 53% ROI 0.4% on 431 bets
-- backed: tg_under · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_0066' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "total_points_under" IS NOT NULL AND "total_points_under" >= 1.75 AND "pm_away" IS NOT NULL AND "pm_away" <= 2.85 AND "X_X_u_elapsed__minus__u_time_in_lead__minus__X_u_pace__over__u_time_in_lead" IS NOT NULL AND "X_X_u_elapsed__minus__u_time_in_lead__minus__X_u_pace__over__u_time_in_lead" <= -19.1179 AND "dog_odds_vol_180" IS NOT NULL AND "dog_odds_vol_180" <= 0.653
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_prop:held_le_0171 =====
-- base: prop:held_lead · tier: Npc · measured OOS win 64.8% ROI 0.3% on 315 bets
-- backed: prop:held_lead · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_prop:held_le_0171' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "pm_away" IS NOT NULL AND "pm_away" <= 2.92 AND "leader_runmax" IS NOT NULL AND "leader_runmax" <= 3.66 AND "lead_vs_med" IS NOT NULL AND "lead_vs_med" <= 1.175 AND "line_flat" IS NOT NULL AND "line_flat" >= 1.6026
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_lag_0131 =====
-- base: tg_under_lag · tier: Vision · measured OOS win 52.7% ROI 0.3% on 293 bets
-- backed: tg_under_lag · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_lag_0131' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "dog_odds_vol_180" IS NOT NULL AND "dog_odds_vol_180" <= 0.15546 AND "odds_ratio" IS NOT NULL AND "odds_ratio" <= 1.209 AND "spread_cover_now" IS NOT NULL AND "spread_cover_now" >= -4.4051
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_lag_0137 =====
-- base: tg_under_lag · tier: Vision · measured OOS win 52.4% ROI 0.3% on 306 bets
-- backed: tg_under_lag · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_lag_0137' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "prog_score" IS NOT NULL AND "prog_score" <= 0.35 AND "odds_ratio" IS NOT NULL AND "odds_ratio" <= 1.2094 AND "pts_last_60s" IS NOT NULL AND "pts_last_60s" <= 6.0 AND "lead_m15" IS NOT NULL AND "lead_m15" <= 1.0 AND "eng_score_tied" IS NOT NULL AND "eng_score_tied" <= 0.0
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_tg_under_lag_0133 =====
-- base: tg_under_lag · tier: Vision · measured OOS win 52.5% ROI 0.2% on 347 bets
-- backed: tg_under_lag · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_tg_under_lag_0133' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "pm_ratio" IS NOT NULL AND "pm_ratio" <= 3.6843 AND "imbal" IS NOT NULL AND "imbal" >= 0.0164 AND "line_flat" IS NOT NULL AND "line_flat" >= 0.8667 AND "pts_last_60s" IS NOT NULL AND "pts_last_60s" <= 4.4615
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

-- ===== BAS_spec:Loser_1_0024 =====
-- base: spec:Loser_1G · tier: Ino · measured OOS win 19.4% ROI 0.1% on 327 bets
-- backed: spec:Loser_1G · price column: backed-side price at arm
-- settlement: final_winner (recorded settlement, never derived)
INSERT INTO laz_bet (strategy, match_id, ts, price)
SELECT * FROM (
  SELECT DISTINCT ON (match_id)
    'BAS_spec:Loser_1_0024' AS strategy, match_id, ts, GREATEST(home_odds, away_odds) AS price
  FROM laz_tick
  WHERE "line_open" IS NOT NULL AND "line_open" >= 140.0 AND "pm_away" IS NOT NULL AND "pm_away" <= 2.92 AND "leader_runmax" IS NOT NULL AND "leader_runmax" <= 3.66 AND "secs_since_run_started" IS NOT NULL AND "secs_since_run_started" >= 0.0 AND "fav_lead_m30" IS NOT NULL AND "fav_lead_m30" >= -1.0 AND "u_trend" IS NOT NULL AND "u_trend" <= -5.7851
    AND GREATEST(home_odds, away_odds) >= 1.4
    AND market_status = 'Open'
  ORDER BY match_id, ts
) q
ON CONFLICT (strategy, match_id) DO NOTHING;

