# Unique settlement possibilities, per sport

From the engine's base registries and the production evaluators. Nothing executed.
Re-run: `python3 audit/laz_settlement_matrix.py`

## 1. How the result is derived — two classes, not eight

| class | sports | rule |
|---|---|---|
| **score** | football, efootball, basketball, ebasketball | the recorded score decides. A null score at the last tick → **unsettleable**, the bet is removed, never counted as a loss |
| **odds inference** | tennis, etennis, tabletennis, esports | the feed carries no usable final score. Winner = the side priced **≤ 1.35** on the last row where both prices are valid and > 1.0. Above that threshold the match is **unsettleable and excluded — never proxy-settled** |

Draw handling splits the score class again: **football and efootball treat a level score
as a result** (draw bets win, home/away lose). Basketball and eBasketball have no draw.

## 2. The headline: what the engine can find vs what production can pay out

| sport | result from | engine can produce | production settles | **gap** |
|---|---|---|---|---|
| basketball | score | 12 | **4** | 8 |
| ebasketball | score | 12 | **2** | 10 |
| football | score | 13 | **3** | 10 |
| efootball | score | 13 | **3** | 10 |
| tennis | odds ≤ 1.35 | 9 | **1** | 8 |
| etennis | odds ≤ 1.35 | 9 | **1** | 8 |
| tabletennis | odds ≤ 1.35 | 9 | **3** | 6 |
| esports | odds ≤ 1.35 | 9 | **1** | 8 |

**Across the engine: 46 distinct base outcomes across 112 bases. Production settles 6
distinct markets in total.** A strategy found on any of the other 40 is a bet that would
be placed and never resolve — which is why the deploy bundle refuses them at the gate
rather than shipping them.

## 3. Per sport, in full

### basketball — score
- **production settles (4):** `match_winner`, `total_points_under`, `lead_ml`, `trail_ml`
- engine can produce (12): away_total, h1_dnb, h1_result, h1_total, h2_ml, home_total, match_ml, match_spread, match_total, q1_spread, q3_result, q4_result

### ebasketball — score
- **production settles (2):** `match_winner`, `total_points_under`
- engine can produce (12): same twelve as basketball (both use the `quarter` base family)

### football — score, draw is a result
- **production settles (3):** `match_winner`, `total_goals_under`, `total_goals_over`
- engine can produce (13): asian_hcap, away_total, btts, clean_sheet_home, dnb, h1_result, h1_total, h2_result, hcap_3way, home_total, match_ml, match_total, win_to_nil_home

### efootball — score, draw is a result
- **production settles (3):** `match_winner`, `total_goals_under`, `total_goals_over`
  (declared as `SETTLEABLE` in `efootball_book_evaluator.py`, not `SETTLEABLE_MARKETS`)
- engine can produce (13): same thirteen as football (`goal` base family)

### tennis / etennis — odds inference
- **production settles (1):** `match_winner`
- engine can produce (9): any_set_to_nil, deciding_set, match_games_total, match_ml, set1_games_total, set1_hcap, set1_winner, set2_winner, straight_sets_home

### tabletennis — odds inference
- **production settles (3):** `match_winner`, `total_points_under`, `total_points_over`
- engine can produce (9): same nine as tennis (`racquet` base family)

### esports — odds inference
- **production settles (1):** `match_winner`
- engine can produce (9): map1_h1_winner, map1_h2_winner, map1_overtime, map1_result, map1_round_hcap, map1_total_rounds, map2_result, maps_2_0, match_ml

## 4. What the strategies in the file actually settle on today

| sport | distinct | breakdown |
|---|---|---|
| ebasketball | 2 | total_points_under ×112, match_winner ×46 |
| efootball | 3 | total_goals_under ×99, match_winner ×97, match_winner (trailing side) ×5 |
| football | 3 | match_winner ×43, total_goals_over ×15, total_goals_under ×8 |
| basketball | 4 | total_points_under ×21, trailing_ml ×2, leading_ml ×1, total_points_over ×1 |
| tennis | 2 | match_winner ×21, match_winner (moneyline) ×16 |

Plus **220 entries that state no market at all** — the legacy containers whose market,
like their sport, was implied by the container rather than written on the entry.

## 5. The honest caveat

`efootball` first came back as **settling nothing**. That was my scan globbing
`*_lazarus_efb_strategies.py`, and eFootball's evaluator is `efootball_book_evaluator.py`
declaring `SETTLEABLE`, not `SETTLEABLE_MARKETS`. Fixed before reporting. If you have a
ninth stack I have not seen, its markets are not in this table.
