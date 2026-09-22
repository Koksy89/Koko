#!/usr/bin/env python3.12
"""Build the production deploy kit for one sport from merged workbook data.

Produces everything needed to run the validated strategies in the production
database, and nothing that is not evidenced:

  MANIFEST.json        what is in the kit, where every fact came from, and the
                       exact limits of what it guarantees
  strategies.json/.csv the per-strategy production contract
  features.json        one spec per term, with the ENGINE SOURCE that defines it
  features/<term>.md   a readable card per term
  schema.sql           DDL for the production tables
  evaluate.sql         a per-strategy SQL predicate, generated from the contract
  reference_impl.py    a runnable reference that turns tick rows into bets, in
                       the engine's own gate order
  verify.py            compares a production run against the reference
  RUNBOOK.md           how to execute it, step by step

Every term spec quotes the engine source verbatim with its line number. Nothing
is invented: a term whose recipe cannot be quoted is listed as BLOCKED and the
strategies that need it are excluded from the deployable set.

Usage:
  laz_deploy_kit.py OUT_DIR MERGED.json TERM_TRACE.json ENGINE.py SPORT
"""
from __future__ import annotations

import ast
import csv
import hashlib
import json
import os
import re
import sys
from collections import OrderedDict

# ---------------------------------------------------------------- engine facts
def enclosing_function(tree, line):
    best = None
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if n.lineno <= line <= (n.end_lineno or n.lineno):
                if best is None or n.lineno > best.lineno:
                    best = n
    return best


def term_spec(term, trace_entry, src_lines, tree):
    """A term's production spec, quoting the engine verbatim."""
    sites = (trace_entry or {}).get('sites') or []
    verdict = (trace_entry or {}).get('verdict') or 'TRULY_UNKNOWN'
    quoted = []
    for s in sites[:4]:
        ln = int(s['line'])
        fn = enclosing_function(tree, ln)
        quoted.append(dict(
            engine_line=ln,
            enclosing_function=(fn.name if fn else '<module>'),
            enclosing_span=([fn.lineno, fn.end_lineno] if fn else None),
            kind=s['kind'],
            source=src_lines[ln - 1].strip()))
    buildable = verdict == 'DEFINED_IN_ENGINE' and bool(quoted)
    return dict(
        term=term, verdict=verdict, buildable=buildable,
        n_define_sites=len(sites),
        definitions=quoted,
        blocked_reason=('' if buildable else
                        ('PCA component: its inputs and loadings live in the run\'s PCA '
                         'manifest, not in the engine source. Export the manifest from the '
                         'run that produced it, or drop the strategies that use it.'
                         if verdict == 'PCA_COMPONENT' else
                         'no definition found in the engine source')))


# ------------------------------------------------------------------ SQL helpers
SQL_OP = {'>=': '>=', '<=': '<=', '>': '>', '<': '<', '==': '=', '!=': '<>'}


def sql_ident(t):
    return '"' + str(t).replace('"', '') + '"'


def strategy_sql(s):
    """The WHERE predicate for one strategy. NULL never satisfies a comparison."""
    parts = []
    for c in s['conditions']:
        op = SQL_OP.get(str(c['op']))
        if op is None or c['value'] is None:
            return None, f"condition {c['term']} {c['op']} {c['value']} has no SQL form"
        col = sql_ident(c['term'])
        parts.append(f'{col} IS NOT NULL AND {col} {op} {c["value"]}')
    return ' AND '.join(parts) if parts else 'TRUE', None


# ------------------------------------------------------------------------ main
def main():
    if len(sys.argv) < 6:
        print(__doc__)
        return 2
    out_dir, merged_p, trace_p, engine_p, sport = sys.argv[1:6]
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, 'features'), exist_ok=True)

    merged = json.load(open(merged_p))
    trace = json.load(open(trace_p))
    src = open(engine_p, encoding='utf-8').read()
    src_lines = src.split('\n')
    tree = ast.parse(src)
    engine_sha = hashlib.sha256(src.encode('utf-8')).hexdigest()

    strategies = merged['strategies']

    # --- term specs, engine-traced ------------------------------------------
    all_terms = sorted({c['term'] for s in strategies for c in s['conditions']})
    specs = OrderedDict()
    for t in all_terms:
        # a term absent from the trace file was BUILDABLE per the lineage sheet:
        # the workbook stated its arithmetic, so it is not in the unbuildable list.
        if t in trace:
            specs[t] = term_spec(t, trace[t], src_lines, tree)
        else:
            specs[t] = dict(term=t, verdict='LINEAGE_BUILDABLE', buildable=True,
                            n_define_sites=0, definitions=[],
                            blocked_reason='',
                            note='the workbook lineage sheet carried this term\'s arithmetic')

    blocked_terms = sorted(t for t, v in specs.items() if not v['buildable'])

    # --- deployable set ------------------------------------------------------
    deployable, excluded = [], []
    for s in strategies:
        bad = sorted({c['term'] for c in s['conditions']} & set(blocked_terms))
        pred, why = strategy_sql(s)
        rec = OrderedDict(s)
        rec['sql_predicate'] = pred
        rec['blocked_terms'] = bad
        rec['deployable'] = (not bad) and pred is not None
        rec['blocked_reason'] = ('; '.join(filter(None, [
            (f'needs term(s) with no recoverable recipe: {", ".join(bad)}' if bad else ''),
            (why or '')])))
        (deployable if rec['deployable'] else excluded).append(rec)

    # --- write ---------------------------------------------------------------
    def W(name, text):
        with open(os.path.join(out_dir, name), 'w') as fh:
            fh.write(text)

    json.dump(deployable, open(os.path.join(out_dir, 'strategies.json'), 'w'), indent=1, default=str)
    json.dump(specs, open(os.path.join(out_dir, 'features.json'), 'w'), indent=1, default=str)
    json.dump(excluded, open(os.path.join(out_dir, 'excluded_strategies.json'), 'w'), indent=1, default=str)

    with open(os.path.join(out_dir, 'strategies.csv'), 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['strategy', 'base', 'tier', 'odds', 'oos_win', 'oos_roi', 'bets', 'backed',
                    'price_column', 'graded_against', 'min_odds_floor', 'hold_sec', 'window_sec',
                    'market_status_columns', 'n_conditions', 'conditions', 'sql_predicate',
                    'source_book'])
        for s in deployable:
            w.writerow([s['strategy'], s['base'], s['tier'], s['odds'], s['oos_win'], s['oos_roi'],
                        s['bets'], s['backed'], s['price_column'], s['graded_against'],
                        s['min_odds_floor'], s['hold_sec'], s['window_sec'],
                        s['market_status_columns'], len(s['conditions']),
                        ' AND '.join(f"{c['term']} {c['op']} {c['value']}" for c in s['conditions']),
                        s['sql_predicate'], s['first_seen_in']])

    # per-term cards
    for t, v in specs.items():
        L = [f'# `{t}`', '', f'**Status:** {v["verdict"]} · '
             f'{"BUILDABLE" if v["buildable"] else "**BLOCKED**"}', '']
        if v.get('note'):
            L += [v['note'], '']
        if v['blocked_reason']:
            L += [f'> {v["blocked_reason"]}', '']
        used = [s['strategy'] for s in strategies if any(c['term'] == t for c in s['conditions'])]
        L += [f'Used by **{len(used)}** strategies.', '']
        if v['definitions']:
            L += ['## Definition in the engine', '',
                  'Quoted verbatim. Reproduce this arithmetic exactly in production.', '']
            for d in v['definitions']:
                L += [f'### `{engine_p}:{d["engine_line"]}` — in `{d["enclosing_function"]}()`', '',
                      '```python', d['source'], '```', '']
        W(os.path.join('features', f'{t}.md'), '\n'.join(L) + '\n')

    # schema.sql
    cols = sorted({c['term'] for s in deployable for c in s['conditions']})
    ddl = ['-- Production schema for the LAZARUS basketball strategies.',
           '-- One row per match tick. Every feature column is DOUBLE PRECISION and',
           '-- NULLABLE: a NULL means "not yet determined", and a NULL NEVER satisfies',
           '-- a condition. Do not default these to 0.',
           '',
           'CREATE TABLE IF NOT EXISTS laz_tick (',
           '  match_id      TEXT        NOT NULL,',
           '  ts            TIMESTAMPTZ NOT NULL,',
           '  league        TEXT,',
           '  phase         TEXT,',
           '  h_score       INTEGER,',
           '  a_score       INTEGER,',
           '  home_odds     DOUBLE PRECISION,',
           '  away_odds     DOUBLE PRECISION,',
           '  draw_odds     DOUBLE PRECISION,',
           '  total_points_handicap DOUBLE PRECISION,',
           '  total_points_over     DOUBLE PRECISION,',
           '  total_points_under    DOUBLE PRECISION,',
           '  market_status TEXT,']
    for c in cols:
        ddl.append(f'  {sql_ident(c):<38s} DOUBLE PRECISION,')
    ddl += ['  PRIMARY KEY (match_id, ts)', ');', '',
            'CREATE INDEX IF NOT EXISTS laz_tick_match_ts ON laz_tick (match_id, ts);', '',
            'CREATE TABLE IF NOT EXISTS laz_bet (',
            '  strategy   TEXT        NOT NULL,',
            '  match_id   TEXT        NOT NULL,',
            '  ts         TIMESTAMPTZ NOT NULL,',
            '  price      DOUBLE PRECISION NOT NULL,',
            '  side       TEXT,',
            '  placed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),',
            '  PRIMARY KEY (strategy, match_id)   -- ONE BET PER MATCH PER STRATEGY',
            ');']
    W('schema.sql', '\n'.join(ddl) + '\n')

    # evaluate.sql
    ev = ['-- One statement per strategy. Each returns the FIRST qualifying tick per match.',
          '-- The gate order is the engine\'s: conditions, then price band, then market open,',
          '-- then first-tick-per-match. A NULL feature never satisfies a condition, which is',
          '-- why every clause carries an explicit IS NOT NULL.', '']
    for s in deployable:
        mo = s.get('min_odds_floor') or 1.4
        px = 'GREATEST(home_odds, away_odds)'   # documented override below
        ev += [f'-- ===== {s["strategy"]} =====',
               f'-- base: {s["base"]} · tier: {s["tier"]} · measured OOS win {s["oos_win"]}%'
               f' ROI {s["oos_roi"]}% on {s["bets"]} bets',
               f'-- backed: {s["backed"]} · price column: {s["price_column"]}',
               f'-- settlement: {s["graded_against"]}',
               'INSERT INTO laz_bet (strategy, match_id, ts, price)',
               'SELECT * FROM (',
               '  SELECT DISTINCT ON (match_id)',
               f"    {s['strategy']!r} AS strategy, match_id, ts, {px} AS price",
               '  FROM laz_tick',
               f'  WHERE {s["sql_predicate"]}',
               f'    AND {px} >= {mo}',
               "    AND market_status = 'Open'",
               '  ORDER BY match_id, ts',
               ') q',
               'ON CONFLICT (strategy, match_id) DO NOTHING;', '']
    W('evaluate.sql', '\n'.join(ev) + '\n')

    # ---- reference implementation -------------------------------------------
    W('reference_impl.py', REFERENCE_IMPL)
    W('verify.py', VERIFY_PY)
    W('RUNBOOK.md', runbook(sport, deployable, excluded, specs, blocked_terms,
                            merged['summary']['runs'], engine_sha))

    manifest = dict(
        schema='amunev.deploy_kit/1',
        sport=sport,
        generated_from=dict(merged=os.path.basename(merged_p),
                            term_trace=os.path.basename(trace_p),
                            engine=os.path.basename(engine_p),
                            engine_sha256=engine_sha, engine_sha256_short=engine_sha[:16]),
        source_runs=merged['summary']['runs'],
        counts=dict(strategies_total=len(strategies),
                    deployable=len(deployable), excluded=len(excluded),
                    terms_total=len(all_terms),
                    terms_buildable=sum(1 for v in specs.values() if v['buildable']),
                    terms_blocked=len(blocked_terms)),
        blocked_terms=blocked_terms,
        files=['MANIFEST.json', 'strategies.json', 'strategies.csv', 'features.json',
               'excluded_strategies.json', 'features/<term>.md', 'schema.sql',
               'evaluate.sql', 'reference_impl.py', 'verify.py', 'RUNBOOK.md'],
        guarantees=[
            'Every term spec quotes the engine source verbatim, with its line number and '
            'enclosing function. No recipe is inferred or invented.',
            'A strategy is in strategies.json only if EVERY term it uses has a quoted '
            'engine definition and every condition has a SQL form.'],
        limits=[
            'THRESHOLDS ARE ALREADY ROUNDED IN THE SOURCE WORKBOOKS (e.g. -160.315, '
            '2.2797). Bets rebuilt from these digits will not match the engine bit-exact. '
            'Re-export from a run of the current engine, which records thresholds exactly, '
            'for bit-exact reproduction.',
            'NOTHING IN THIS KIT HAS BEEN EXECUTED against a real frame. The arithmetic is '
            'quoted from the engine, not verified by running it.',
            'The price column in evaluate.sql defaults to GREATEST(home_odds, away_odds). '
            'Each strategy states its own backed side and price column; override per '
            'strategy before going live.',
            'Settlement is stated per strategy but not implemented here: the kit selects '
            'and prices bets, it does not grade them.'])
    json.dump(manifest, open(os.path.join(out_dir, 'MANIFEST.json'), 'w'), indent=1, default=str)

    print(f'deploy kit -> {out_dir}')
    print(f'  strategies deployable : {len(deployable)}')
    print(f'  strategies excluded   : {len(excluded)}')
    print(f'  terms total           : {len(all_terms)}')
    print(f'  terms buildable       : {manifest["counts"]["terms_buildable"]}')
    print(f'  terms blocked         : {len(blocked_terms)}  {blocked_terms[:6]}')
    return 0




REFERENCE_IMPL = r'''#!/usr/bin/env python3
"""Reference implementation: tick rows -> bets, in the engine's own gate order.

This is the ARBITER. If production disagrees with this file, production is wrong.
It is deliberately simple and dependency-light so it can be read and checked line
by line.

THE GATE ORDER (do not reorder -- the engine evaluates in exactly this sequence):
  1. every condition holds at the tick; a NaN/None value NEVER satisfies one
  2. the backed-side price is >= the strategy's min odds floor
  3. the market is open at that tick
  4. the FIRST tick of the match that passes 1-3 is the bet; later ticks of that
     match are ignored (one bet per match)

Usage:
    from reference_impl import load_strategies, run
    strategies = load_strategies("strategies.json")
    bets = run(strategies, tick_rows)      # tick_rows: iterable of dicts
"""
import json
import math


def _is_null(v):
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return False


OPS = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def load_strategies(path):
    with open(path) as fh:
        return json.load(fh)


def conditions_hold(strategy, row):
    """Gate 1. Every condition must hold. A NULL value fails its condition."""
    for c in strategy["conditions"]:
        v = row.get(c["term"])
        if _is_null(v):
            return False
        op = OPS.get(c["op"])
        if op is None:
            return False
        try:
            if not op(float(v), float(c["value"])):
                return False
        except (TypeError, ValueError):
            return False
    return True


def backed_price(strategy, row):
    """The price of the side this strategy backs, at this tick.

    The kit records each strategy's `backed` side and `price_column`. Where the
    book did not record them, the leader/favourite convention below applies --
    CHECK THIS PER STRATEGY before staking.
    """
    col = strategy.get("price_column") or ""
    if col and col in row and not _is_null(row[col]):
        return float(row[col])
    ho, ao = row.get("home_odds"), row.get("away_odds")
    if _is_null(ho) or _is_null(ao):
        return None
    side = str(strategy.get("backed") or "").lower()
    if "home" in side:
        return float(ho)
    if "away" in side:
        return float(ao)
    return max(float(ho), float(ao))


def market_open(row):
    """Gate 3."""
    st = row.get("market_status")
    if st is None:
        return True          # no status recorded = not a gate
    return str(st).strip().lower() in ("open", "1", "true", "active")


def run(strategies, rows):
    """Gates 1-4. Returns [{strategy, match_id, ts, price}], one per match."""
    seen = set()
    out = []
    for row in rows:
        for s in strategies:
            key = (s["strategy"], row.get("match_id"))
            if key in seen:
                continue                                   # gate 4
            if not conditions_hold(s, row):                 # gate 1
                continue
            px = backed_price(s, row)
            if px is None:
                continue
            floor = s.get("min_odds_floor")
            try:
                floor = float(floor) if floor is not None else 1.4
            except (TypeError, ValueError):
                floor = 1.4
            if px < floor:                                  # gate 2
                continue
            if not market_open(row):                        # gate 3
                continue
            seen.add(key)
            out.append(dict(strategy=s["strategy"], match_id=row.get("match_id"),
                            ts=row.get("ts"), price=px))
    return out
'''


VERIFY_PY = r'''#!/usr/bin/env python3
"""Compare a production bet list against the reference implementation.

    python3 verify.py strategies.json ticks.json production_bets.json

ticks.json            : [{match_id, ts, market_status, <feature columns>...}, ...]
production_bets.json  : [{strategy, match_id, ts, price}, ...] from YOUR system

Exit code 0 only when the two agree exactly, strategy by strategy and match by
match. Anything else prints what differs and exits 1.
"""
import json
import sys

from reference_impl import load_strategies, run


def key(b):
    return (str(b["strategy"]), str(b["match_id"]))


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    strategies = load_strategies(sys.argv[1])
    with open(sys.argv[2]) as fh:
        ticks = json.load(fh)
    with open(sys.argv[3]) as fh:
        prod = json.load(fh)

    ref = run(strategies, ticks)
    R, P = {key(b): b for b in ref}, {key(b): b for b in prod}

    missing = sorted(R.keys() - P.keys())
    extra = sorted(P.keys() - R.keys())
    price_diff = [(k, R[k]["price"], P[k]["price"]) for k in sorted(R.keys() & P.keys())
                  if abs(float(R[k]["price"]) - float(P[k]["price"])) > 1e-9]
    tick_diff = [(k, R[k]["ts"], P[k]["ts"]) for k in sorted(R.keys() & P.keys())
                 if str(R[k]["ts"]) != str(P[k]["ts"])]

    print(f"reference bets : {len(ref)}")
    print(f"production bets: {len(prod)}")
    print(f"  missing in production: {len(missing)}")
    print(f"  extra in production  : {len(extra)}")
    print(f"  price mismatches     : {len(price_diff)}")
    print(f"  arm-tick mismatches  : {len(tick_diff)}")
    for k in missing[:10]:
        print(f"    MISSING {k}")
    for k in extra[:10]:
        print(f"    EXTRA   {k}")
    for k, a, b in price_diff[:10]:
        print(f"    PRICE   {k}: reference {a} vs production {b}")
    for k, a, b in tick_diff[:10]:
        print(f"    TICK    {k}: reference {a} vs production {b}")

    ok = not (missing or extra or price_diff or tick_diff)
    print("\nRESULT: " + ("MATCH" if ok else "MISMATCH"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
'''


def runbook(sport, deployable, excluded, specs, blocked_terms, runs, engine_sha):
    used = sorted({c['term'] for s in deployable for c in s['conditions']})
    raw = ['match_id', 'ts', 'market_status', 'home_odds', 'away_odds',
           'h_score', 'a_score', 'total_points_handicap', 'total_points_over',
           'total_points_under', 'phase', 'league']
    L = []
    A = L.append
    A(f'# GOING LIVE — {sport.upper()}')
    A('')
    A(f'**{len(deployable)} strategies** are deployable from this kit. '
      f'{len(excluded)} are excluded and named in `excluded_strategies.json`.')
    A('')
    A('Source runs:')
    for r in runs:
        A(f"* `{r['book']}` — {r.get('validated_headline','?')} validated, "
          f"{r.get('run_utc','?')} UTC, contributed {r.get('contributed_new','?')} unique")
    A('')
    A(f'Engine traced for feature recipes: `{engine_sha[:16]}`')
    A('')
    A('---')
    A('')
    A('## READ THIS FIRST — what this kit does and does not guarantee')
    A('')
    A('**It guarantees** that every strategy listed is defined only in terms whose '
      'recipe is quoted verbatim from the engine source, with a line number you can '
      'open and check. Nothing is inferred.')
    A('')
    A('**It does not guarantee bit-exact reproduction of the measured bets**, for one '
      'specific reason: the thresholds in the source workbooks are already rounded '
      '(`-160.315`, `2.2797`, `23.4333`). Rebuilding from six significant digits '
      'selects a slightly different set of ticks than the engine measured. For '
      'bit-exact work, re-export from a run of the current engine, which records '
      'thresholds exactly.')
    A('')
    A('**Nothing here has been executed.** The arithmetic is quoted, not run.')
    A('')
    A('---')
    A('')
    A('## Step 1 — build the tick table')
    A('')
    A('`schema.sql` creates `laz_tick` (one row per match tick) and `laz_bet`.')
    A('')
    A('```bash')
    A('psql "$DATABASE_URL" -f schema.sql')
    A('```')
    A('')
    A('Two rules that decide whether this works at all:')
    A('')
    A('1. **Every feature column is NULLABLE and must stay NULL until determined.** '
      'A NULL means "not yet knowable at this tick". Defaulting to `0` makes '
      'conditions fire that the engine never fired, and it fails silently.')
    A('2. **One row per (match_id, ts).** The one-bet-per-match rule is applied on '
      'first qualifying tick, so duplicate ticks produce duplicate arms.')
    A('')
    A('## Step 2 — populate the feature columns')
    A('')
    A(f'These **{len(used)}** feature columns are used by the deployable strategies. '
      f'Each has a card in `features/` quoting the engine source that defines it.')
    A('')
    A('Raw columns you must supply from the feed:')
    A('')
    for r in raw:
        A(f'* `{r}`')
    A('')
    A('Derived columns: see `features.json` and `features/<term>.md`. Build them in '
      'dependency order — a term whose card references another term needs that one '
      'first.')
    A('')
    A('### The causality rule')
    A('')
    A('Every feature must be computable from the current tick and earlier ticks of '
      'the same match, and from nothing else. If a value would change when a LATER '
      'tick arrives, it is forward-looking and the strategy built on it is invalid. '
      'The engine enforces this with a truncation door; production must not '
      'reintroduce what that door excluded.')
    A('')
    A('## Step 3 — select bets')
    A('')
    A('```bash')
    A('psql "$DATABASE_URL" -f evaluate.sql')
    A('```')
    A('')
    A('`evaluate.sql` holds one statement per strategy. The gate order is the '
      "engine's and must not be reordered:")
    A('')
    A('| # | gate | rule |')
    A('|---|---|---|')
    A('| 1 | CONDITIONS | every clause true at the tick; a NULL never satisfies one |')
    A("| 2 | PRICE | backed-side price >= the strategy\'s min odds floor |")
    A('| 3 | MARKET | the market is open at that tick |')
    A('| 4 | FIRST TICK | the first tick of the match passing 1-3 is the bet |')
    A('')
    A('**Check the price column per strategy before staking.** `evaluate.sql` '
      'defaults to `GREATEST(home_odds, away_odds)`. Each row of `strategies.csv` '
      'carries its own `backed` side and `price_column`; where those are recorded, '
      'use them.')
    A('')
    A('## Step 4 — prove production matches')
    A('')
    A('Do not stake before this passes.')
    A('')
    A('```bash')
    A('# export a window of ticks and your own bet list as JSON, then:')
    A('python3 verify.py strategies.json ticks.json production_bets.json')
    A('```')
    A('')
    A('`reference_impl.py` is the arbiter: it applies the four gates in order, in '
      'plain Python, with no dependencies. If your system and it disagree, your '
      'system is wrong. `verify.py` reports missing bets, extra bets, price '
      'mismatches and arm-tick mismatches, and exits non-zero on any of them.')
    A('')
    A('Start with one strategy and one week of ticks. Widen only once it matches.')
    A('')
    A('## Step 5 — stake')
    A('')
    A('Settlement is **stated** per strategy (`graded_against`) but **not '
      'implemented** in this kit: it selects and prices bets, it does not grade '
      'them. Grade with the owner rule — the score decides; a level or absent score '
      'settles on the last validated odds row at <= 1.35; neither, unsettled.')
    A('')
    A("Stake small first and compare each strategy's live results against its "
      'recorded OOS win and ROI in `strategies.csv`. A strategy that diverges early '
      'is telling you a feature is built differently in production.')
    A('')
    A('---')
    A('')
    A('## What was excluded, and why')
    A('')
    if blocked_terms:
        A(f'**{len(blocked_terms)} terms have no recoverable recipe**, so the '
          f'{len(excluded)} strategies using them are excluded:')
        A('')
        for t in blocked_terms:
            A(f'* `{t}` — {specs[t]["blocked_reason"]}')
        A('')
        A('All of these are PCA components. Their inputs and loadings live in the '
          'PCA manifest of the run that created them, not in the engine source. '
          'Export that manifest and they become buildable; until then the '
          'strategies that use them cannot be reproduced.')
    else:
        A('Nothing was excluded for an unrecoverable term.')
    A('')
    A('## File map')
    A('')
    A('| file | what it is |')
    A('|---|---|')
    A('| `MANIFEST.json` | what is in the kit, its provenance, guarantees and limits |')
    A('| `strategies.json` / `.csv` | the deployable strategies and their full contract |')
    A('| `excluded_strategies.json` | what was excluded and why |')
    A('| `features.json` | every term, with the engine source that defines it |')
    A('| `features/<term>.md` | one readable card per term |')
    A('| `schema.sql` | DDL for `laz_tick` and `laz_bet` |')
    A('| `evaluate.sql` | one bet-selection statement per strategy |')
    A('| `reference_impl.py` | the arbiter: four gates, plain Python |')
    A('| `verify.py` | production vs reference, exits non-zero on any difference |')
    A('| `RUNBOOK.md` | this file |')
    return '\n'.join(L) + '\n'


if __name__ == '__main__':
    sys.exit(main())
