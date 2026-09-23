# Every element in the engine that can stop a strategy

Ordered by what it costs you. `laz_god2__verify()` now fails the run if any of the
REMOVED ones comes back.

## REMOVED on your instruction, 23 Sep 2026

| what | where it was | what it cost you |
|---|---|---|
| **`band_hi_ceiling = 200.0`** plus a per-strategy ceiling | `LAZ_OWNER['rules']`, applied in the release path | Every registry row got a **maximum odds**. The ceiling was the top of the highest ladder rung `[1.4, 1.8, 2.2, 3.2, 3.6, 4.2, 5.0, 6.5, 9.0]` holding 100+ OOS bets — which for most strategies is **4.2**. A strategy validated at 12.0 shipped with a top of 4.2 and production refused every bet above it. **This is why the long odds disappeared.** |
| **`band_hi` in the required-column list** | release path | A row with no ceiling was dropped entirely — so the ceiling could not simply be deleted; it had to be deleted *and* de-required. Both done. |
| **base-mask clauses stacked into the registry** | `laz_deploy__row` (added by me last turn) | `late_lead_hold` shipped as `u_elapsed >= 0.85 AND u_elapsed <= 1.2 AND abs_lead > 3` in front of its own conditions. The margin floor cuts exactly the long-odds situations. Now **recorded as a note, enforced nowhere**. |
| **`MARKET OPEN` / `FEED FRESH` as conditions** | execution spec | Market open is now **placement policy** (wait 60s, re-price, place or abandon that bet). The feed-freshness gate is gone from the spec entirely. |
| **`max_odds` required by preflight** | `laz_deploy__preflight` | A check that *demanded* a ceiling guarantees somebody supplies one. Now a ceiling is itself a **preflight failure**. |

| **`band_min_matches_x = 2.0`** → a rung needed **200 matches** before it was searched | `laz_mode3` rung loop | Long-odds rungs are the thin ones. **176 of 394** attempts died here on one esports run, the largest at 279 matches. | **Now 1.0**, on your instruction. Every rung that can still meet `n_is >= 100 / n_oos >= 100` is searched; nothing below your own bar is admitted. |
| **hand-written family → role table** | `laz_deploy__FAMILY_ROLE`, `laz_mode3___MASK_WINDOWS` (added by me) | Decided who a strategy backs from a table instead of from the base. | **Both deleted.** The side is now read from the base's own mask and its own prose — `laz_deploy__base_side`. |

## STILL THERE — your call, I have not touched them

| what | where | what it does | my read |
|---|---|---|---|
| **`len(u) < 8` unique matches** | rejection `band_few_matches` | Skips a rung with fewer than 8 distinct matches. | Harmless — 8 matches can never reach `n >= 100`. |
| **`iA < min_n` inner 70/30 fit split** | rejection `volume_fit` | Needs 100 in the fit half *and* 50 in the confirm half. | `LAZ_INNER_SPLIT=off` already fits on the whole in-sample half. Your switch. |
| **`len(idx) < 2 × min_n` after the arming window** | rejection `arming_window` | Needs 200 armed ticks. | This one enforces your own `n_is >= 100` + `n_oos >= 100`. Correct. |
| **`min_odds = 1.5` / `min_odds_hard = 1.4`** | `LAZ_OWNER['rules']` | Mode 3 overrides to **1.40** (`override_min_odds=True`); modes 1/2/4/5 use **1.50**. | **Confirmed correct** and now written into GOD-2. No change. |
| **`tick_gap_s > 180` (basketball) / `> 300` (football)** | **your production evaluators**, not the engine | A tick more than 3 (or 5) minutes after the last one is skipped. | Production code I don't own. It is a real filter on placement. Flagging it. |
| **`laz_mode3___leg_allowed`** market allow-list | rejection `market_not_allowed` | Refuses a leg whose market the sport cannot combine. | Leave it, or tell me to open it. |
| **`dedupe` / `known`** | rejections | Drops a leg identical to one already accepted, or already in your book. | Not a value filter. Correct. |
| **`single_only` (win < 75%)** | flag only | **Does not reject.** Only marks a leg as unsuitable for stacking. | Correct — no action. |

## What would recover the most strategies next

| change | where | recovers |
|---|---|---|
| a **`first_scorer`** role in `resolve_side` | your `laz_features.py` — production, not the engine | **25 strategies** on the basketball book alone. The base backs the side that scored first; the engine knows it (`first_scorer_home`), production has no role for it. One function. |
| a **period-leader** role (`h1_leader`, `q1_leader`, `set1_winner`) | your `laz_features.py` | 5 strategies. The base backs the winner of a half/quarter/set, which is a different side from the one leading right now. |
| a **spread settler** | your basketball orchestrator | 7 strategies. `spread_dog_cover` settles a point spread; `SETTLEABLE_MARKETS` has none. |

I have **not** written any of these: they are changes to your production files, and the
rule is that I ask first.

## The one thing I did NOT do, and why

You wrote "remove condition 1 and condition 2". In the example table I sent you, those
were **`line_move <= 4.0` and `trailer_price >= 1.92` — values I invented for the
illustration.** In a real strategy, `CONDITION 1..n` are the strategy's **own mined
conditions** — the thing the search found, the thing that makes it that strategy and not
another.

Removing them would turn every strategy into "back the leader, always". So I have left
them in and removed everything around them. If you did mean the strategy's own
conditions, say so and I will change it — it is one line.
