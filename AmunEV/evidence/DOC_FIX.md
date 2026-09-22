# Accurate strategy documentation — evidence

**Target:** `AmunEV_Engine_V2.py`, the owner's reference copy `bc39725f09f9c0d3`
(the version that produced the 175 strategies). Named in PLAYBOOK §6 M0 as
"the owner's own reference copy".

**Goal:** every validated strategy documented completely and exactly — its
conditions, its features, and how they stack — so production reproduces the
identical bets.

**Method:** static only. The engine was never imported, executed or run. All
analysis is AST-based; the new code is unit-tested by extracting the functions
from the source with `ast` and executing only those, against synthetic records.

---

## Defects found and fixed

### DOC-FIX 1 — every condition's build explanation rendered as dict key names

`laz_featdoc__document_feature` was **defined twice** (lines 98082 and 98182).
The first returns a list of markdown lines; the second returns a dict. The
second silently wins.

`laz_featdoc__document_condition` does:

```python
out = [f'CONDITION: {test}', '']
out += laz_featdoc__document_feature(term)     # out is a list of strings
```

`list += dict` extends with the dict's **keys**, so every condition explanation
in the shipped documentation rendered as:

```
CONDITION: lead_streak >= 3

feature
depth
kind
arithmetic
parents
source_columns
resolves_at
notes
```

No exception, no warning. Same at line 118809 (`'\n'.join(FD.document_feature(t))`).

**Fix:** the markdown-lines version is renamed `laz_featdoc__document_feature_lines`
(with its three recursive calls), and the two callers that need the lines form
now call it. The dict version keeps the name `document_feature`, so
`rebuild_recipe` / `flatten_feature` are untouched.

### DOC-FIX 2 — conditions silently lost before reaching the dossier

`laz_production__strategy_record` read the chain with

```python
conds = [c.strip() for c in str(rec.get('conditions','')).split(' AND ') if c.strip()]
```

which is the exact pattern `_laz_conditions_of` (line 2874) was written to
replace — its own docstring records seven strategies reaching the book with no
recorded trigger. A record carrying the chain under any other key produced `[]`,
and the dossier then printed *"No conditions on record — treat the ledger as
the spec"* and stamped `port_status='NEEDS_FEATURE_BUILD'`.

**Fix:** `laz_production___conditions_raw` recovers the chain from
`conditions` / `trigger` / `triggers` / `raw_spec` / `spec` / `condition`,
handles list and string forms, ignores `'nan'`/`'none'`, and reports which key
it used (`conditions_source_key` is now in the record and the document).

### DOC-FIX 3 — the base arm mask was never documented (the 165-of-175 defect)

A strategy's conditions are only half its specification. The **base** decides
which ticks are eligible at all, which side is backed and which price series is
read. It reached the dossier only as an English sentence from `BASE_WORDS`.

The mask itself is a Python function — `laz_registry__BASES[base]['fn']` — and
its source was never emitted. A reader had the conditions but not the mask they
sit on, so the documented text could not rebuild the measured bets. This is
PLAYBOOK §9's "165 of 175 strategies would have shipped without their seed's
conditions": the seed's conditions **are** the base mask.

**Fix:** `laz_production__base_card` emits the base's executable source, the
frame arrays it consumes (`needs`), its market, tie rule, blocked status and
evidence. A base with no recorded mask now **blocks** the strategy instead of
being described in prose.

### DOC-FIX 4 — nothing stated how the conditions stack

The dossier listed conditions but never said how they compose or what else must
hold. "Every condition holds AND the price is at or above min_odds" omits the
base mask, the band ceiling, the market-status gate, the NaN rule and the
one-bet-per-match rule.

**Fix:** `laz_production__stack` returns the ordered gates a tick passes:

| # | gate |
|---|---|
| 1 | BASE / ARM MASK — eligibility, backed side, price series, tie rule |
| 2..n | CONDITION 1..n — in recorded order; NaN never satisfies |
| n+1 | PRICE — `>= min_odds`, inside the band |
| n+2 | MARKET STATUS — open at this tick |
| n+3 | FIRST TICK — first passing tick is the bet; one bet per match at measurement |
| n+4 | SETTLEMENT — `laz_settlement__settled_by(sport)` |

plus a canonical boolean `expression` and a deterministic `spec_sha` production
can echo back to prove its implementation matches.

### DOC-FIX 5 — a measured clause could vanish silently

`laz_release__parse_conditions` — the parser behind the **deployment SQL** — had
no `else` branch: a clause matching no pattern fell off the end of the loop and
disappeared. The strategy then shipped with fewer conditions than it was
measured on, and production placed bets the search never measured.

It also split on `' AND '` only. On a semicolon-joined chain the fallback regex
matched across the separator:

```
OLD parser on  'a >= 1; b < 2'  ->  ('a', '>=', '1;')
```

— a corrupt threshold `'1;'`, and `b < 2` gone. That reached the registry SQL.

**Fix:** the parser accepts `' AND '` or `';'`, and with `keep_unparsed=True`
returns the clause as `{'op': 'UNPARSED', 'raw': ...}` so the caller can refuse
it. The default stays `False`, so every existing caller — the registry SQL
included — is byte-identical for `' AND '` chains. Semicolon chains now parse
correctly instead of corruptly; that is a deliberate correction, noted here
because it changes registry SQL content for any strategy stored that way.

### DOC-FIX 6 — deployability verdict and machine-readable contract

* Every strategy now carries `deployable` / `blocked` / `port_status`. A
  strategy whose stack has an unreproducible gate is marked **BLOCKED — DO NOT
  STAKE** at its own heading, with the reason, and is listed in a summary table
  at the top of the dossier. Nothing is presented as ready to stake unless its
  documented text rebuilds it.
* `laz_production__export` writes `<sport>_deployment.json`: the ordered gate
  stack, canonical expression, base card, band, settlement, spec hash and
  verdict per strategy, plus the terms production must still build and the
  engine fingerprint.
* Three unmanaged `open()` handles on this path wrapped in `with` (PLAYBOOK W1).

---

## Verification

`tests/test_production_docs.py` — extracts the five changed/added functions from
the engine source by AST and executes only those. The engine is never imported.

```
extracted 5 function(s) from AmunEV_Engine_V2.py by AST — engine never imported
... 37 assertions ...
ALL PASS
```

Covered: both delimiters; unparsed clauses kept and counted, never dropped;
back-compat of the default parser path; condition recovery from every alternate
key; gate order and numbering; base mask source emitted; band → lo/hi; canonical
expression; `spec_sha` deterministic across calls and sensitive to a threshold
change; unknown base blocks; pool-built base accepted; every legacy record key
preserved; JSON-serialisable.

Static audit, reference vs patched:

| check | reference | patched |
|---|---|---|
| S02 parse | PASS | PASS |
| S09 undefined names | 18 / 47 sites | 18 / 47 sites (unchanged) |
| S08 unmanaged `open()` | 44 in 38 fns | **41 in 36 fns** |
| S13 duplicate top-level defs | 9 | **8** |

Diff of the two audits: **no new undefined name, no new duplicate, no new
unmanaged handle.** Fixed: `laz_featdoc__document_feature` (duplicate),
`laz_production__export` and `laz_production__implementation_docs` (handles).

---

## Not done, and why

* **The 14 undefined names in the legacy ledger region** (`_bb_load`, `_bb_rows`,
  `_fb_stack_frame`, `_period_mask`, `_read_tick_compat`, `norm_ts`, …, lines
  34140–34940) are called but **defined nowhere in the file**. They are loader
  helpers for pasted-in legacy ledger builders. They cannot be repaired by
  renaming and inventing them would be inventing behaviour. Listed here; they
  are off the production-documentation path.
* **8 remaining duplicate top-level definitions** (`detect_sport`,
  `lay_favorite_ledger`, `_merge_v3`, `assert_no_shadowed_containers`,
  `_chart_monthly`, `_chart_top_players`, `_monthly_agg`, `_write_table`) — all
  in the legacy region, none on the documentation path. Each needs its own
  decision about which definition is intended.
* **41 remaining unmanaged file handles** — PLAYBOOK W1 scope, mechanical, not
  required for documentation accuracy.
* **Nothing was executed**, so no claim is made here about runtime behaviour on
  a real book. The next step is the owner's: regenerate the docs.

---

## How to regenerate the documents

No search re-run is needed — `laz_production__implementation_docs` is read-only
and regenerable, and reads the existing book. Per sport it writes:

| file | what it is |
|---|---|
| `IMPLEMENTATION_<SPORT>.md` | the per-strategy dossier: base mask source, ordered stack, every term's build recipe, deploy steps, acceptance test, DEPLOYABLE/BLOCKED verdict |
| `<sport>_deployment.json` | the machine-readable contract production consumes |
| `<sport>_strategies.json` | per-strategy records incl. stack and spec hash |
| `<sport>_terms.json` | per-term lineage: arithmetic, inputs, builder source, causality, resolves_at |
| `<sport>_gap.md` | terms production must still build |
| `<sport>_reference.parquet` | (match_id, ts, term, value) at every tick for the first N matches — diff production against this |

Read the top of the dossier first: it states how many of the strategies are
deployable and names every blocked one with its reason.
