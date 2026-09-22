# Basketball workbooks — what they contain, and what can go to production

Sources (read-only):
* `LAZARUS_BASKETBALL_COMBINA_20260922_1027.xlsx` — **175 strategies validated**, 22 Sep 2026 10:27 UTC
* `LAZARUS_BASKETBALL_MODE3_20260922_202748.xlsx` — **142 strategies validated**, 22 Sep 2026 20:27 UTC

## Which engine made the 10:27 book?

**The workbook does not say, and cannot be made to say it after the fact.**

* `dc:creator` is `openpyxl`. There is no engine version, sha or fingerprint anywhere
  in either file.
* I fingerprinted both books against the two engine builds by their header strings
  (40 tested: 19 in both, **0 exclusive to either**) and by sheet names (31 stems:
  25 in both, **0 exclusive**). The workbook-writer code is identical between V2.25
  and V2.31, so the output cannot distinguish them.

What the evidence does support, circumstantially but strongly:

1. The 10:27 run is **175 basketball strategies on 22 Sep** — the dashboard says so.
2. The V2.26 note inside `6e83f03f` records *"basketball 22 Sep: 165 of 175 legs would
   have shipped without their seed's conditions"*. That is this run.
3. V2.26 was written **in response to it**, so the run predates V2.26.
4. `bc39725f` is V2.25 — the last build before V2.26.

**Conclusion: a V2.25-era engine, `bc39725f` or a near-identical sibling.** Not provable
to the byte, because no build stamped itself. That is fixed going forward (below).

## The merged production set

| | strategies | replicable |
|---|---|---|
| 10:27 book | 175 rows -> **169 unique** | 80 |
| 20:27 book | 142 rows -> **132 novel** | 37 |
| **merged** | **301 unique** | **117** |

* 6 rows inside the 10:27 book share a condition signature with another row — the same
  bet under two names. Collapsed.
* 10 of the 20:27 strategies already existed in the 10:27 book. The other 132 are new.

Written to `production_strategies.csv` and `.json`: every strategy with its base, tier,
odds, OOS win/ROI, bets, backed side, price column, settlement, min-odds floor, hold and
window seconds, market-status columns, reconciliation gap, and its full condition chain.

## Why 184 of 301 cannot go to production

Not an opinion — **the workbooks' own `FEATURE_LINEAGE` sheets say so.** 54 of the 175
distinct terms are marked `kind = UNKNOWN`, with the note *"not in the genome — no source
found in any transcript, version or frame"* and *"this term cannot be rebuilt from the
workbook"*. A strategy whose terms the engine cannot rebuild cannot be reproduced in
production, whatever its ROI.

Terms blocking the most strategies:

| term | strategies blocked |
|---|---|
| `tg_overround` | 33 |
| `trailer_price` | 16 |
| `rk_dog_odds` | 15 |
| `lead_changes_300s` | 14 |
| `rk_odds_ratio` | 14 |
| `pc_f3e46219` | 8 |
| `pace_last_300s_vs_line` | 8 |
| `rk_fav_odds` | 8 |

Fixing `tg_overround` alone unblocks 33. The `pc_*` terms are PCA components whose
manifest was not carried into the book.

Also note: the thresholds in `05_IMPLEMENTATION` are already rounded — `-160.315`,
`2.2797`, `23.4333`. Even a replicable strategy will not rebuild bit-exact from those
digits. The engine now records thresholds exactly (V2.27 port), but these two books
predate that.

## So it never happens again

1. **Every workbook now stamps the engine that made it.** `laz_xl__write` writes the
   engine version, `_laz_code_fingerprint()`, the engine file's sha256, the run time,
   the strategy count and element-doc coverage into the workbook properties — where
   `dc:creator` said only `openpyxl` — and beside it as `<workbook>.provenance.json`.
2. **Every strategy is documented at acceptance**, not rebuilt later from a lossy row:
   seed text and identity, exact thresholds and band edges, the terms, the measured
   bets fingerprinted, and the reasons it cannot ship unchanged.
3. **The round-trip is recorded on every leg** — whether its shipping text rebuilds the
   bets it was measured on. It records, it never rejects.

Together: a book identifies its build, and a strategy carries its own reproduction
recipe plus a measured verdict on whether that recipe actually works.

## Reproduce

```bash
python3.12 tools/laz_book_merge.py out books/COMBINA_1027.xlsx books/MODE3_202748.xlsx
```
