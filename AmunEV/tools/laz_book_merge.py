#!/usr/bin/env python3.12
"""Merge validated strategies from LAZARUS workbooks into one production set.

Reads each workbook's IMPLEMENTATION sheet (the per-strategy production contract)
and FEATURE_LINEAGE sheet (every term, and whether the engine could state how to
rebuild it), then:

  * unions the runs, oldest first, so the earlier run's strategies are the base
    and a later run contributes only what is NOVEL;
  * matches on the CONDITION SIGNATURE as well as the name, so a renamed but
    identical strategy is not counted twice;
  * gives every strategy a replication verdict by checking each of its terms
    against the lineage sheet -- a strategy whose terms the engine itself says
    it cannot rebuild CANNOT be reproduced in production, whatever its ROI.

Reads only. Writes CSV and JSON next to the output directory.

Usage:
  laz_book_merge.py OUT_DIR BOOK.xlsx [BOOK.xlsx ...]
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import OrderedDict

import openpyxl

IMPL_HINT = 'IMPLEMENTATION'
LIN_HINT = 'FEATURE_LINEAGE'
VALID_HINT = 'FULLY_VALIDATED'
UNBUILDABLE = re.compile(r'not in the genome|cannot be rebuilt|no formula found|no source found', re.I)


def _sheet(wb, hint):
    for s in wb.sheetnames:
        if hint in s.upper():
            return wb[s]
    return None


def _header_row(ws, must_have, limit=8):
    """The first row that looks like a header (contains a known column name)."""
    for i, r in enumerate(ws.iter_rows(max_row=limit, values_only=True), 1):
        vals = [str(c).strip().lower() if c is not None else '' for c in r]
        if any(m in vals for m in must_have):
            return i, vals
    return None, []


def read_impl(path):
    """[{strategy, base, tier, odds, ..., conditions:[{term,op,value}]}] from one book."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = _sheet(wb, IMPL_HINT)
    if ws is None:
        wb.close()
        return [], {}
    hi, hdr = _header_row(ws, ('strategy name', 'strategy'))
    if hi is None:
        wb.close()
        return [], {}
    col = {name: j for j, name in enumerate(hdr) if name}
    out = []
    for r in ws.iter_rows(min_row=hi + 1, values_only=True):
        if r is None or all(c is None for c in r):
            continue
        name = r[col.get('strategy name', 0)]
        if name is None or not str(name).strip():
            continue
        def g(k):
            j = col.get(k)
            return r[j] if j is not None and j < len(r) else None
        conds = []
        for n in range(1, 7):
            t, o, v = g(f'cond{n} term'), g(f'cond{n} op'), g(f'cond{n} value')
            if t is None or str(t).strip() == '':
                continue
            conds.append(dict(term=str(t).strip(), op=str(o).strip() if o is not None else None,
                              value=(str(v).strip() if v is not None else None)))
        out.append(OrderedDict(
            strategy=str(name).strip(),
            base=(str(g('base')).strip() if g('base') is not None else None),
            tier=(str(g('tier')).strip() if g('tier') is not None else None),
            odds=g('odds'), oos_win=g('oos win'), oos_roi=g('oos roi'), bets=g('bets'),
            backed=g('backed'), price_column=g('price column'),
            graded_against=g('graded against'), min_odds_floor=g('min odds floor'),
            hold_sec=g('hold sec'), window_sec=g('window sec'),
            market_status_columns=g('market status columns'),
            reconciliation_gap=g('reconciliation gap'),
            conditions=conds))

    # dashboard headline, for provenance
    meta = {}
    dash = _sheet(wb, 'DASHBOARD')
    if dash is not None:
        for r in dash.iter_rows(max_row=6, values_only=True):
            line = ' '.join(str(c) for c in (r or ()) if c is not None).strip()
            m = re.search(r'(\d+)\s+strategies validated this run', line)
            if m:
                meta['validated_headline'] = int(m.group(1))
            m2 = re.search(r'(\d{1,2} \w+ \d{4} \d{2}:\d{2}) UTC', line)
            if m2:
                meta['run_utc'] = m2.group(1)
    wb.close()
    return out, meta


def read_lineage(path):
    """{term: {'status': 'BUILDABLE'|'UNBUILDABLE', 'note': ...}}"""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = _sheet(wb, LIN_HINT)
    if ws is None:
        wb.close()
        return {}
    hi, hdr = _header_row(ws, ('term',))
    if hi is None:
        wb.close()
        return {}
    col = {name: j for j, name in enumerate(hdr) if name}
    ti = col.get('term', 0)
    out = {}
    for r in ws.iter_rows(min_row=hi + 1, values_only=True):
        if r is None or ti >= len(r) or r[ti] is None:
            continue
        term = str(r[ti]).strip()
        if not term:
            continue
        # THE ENGINE'S OWN VERDICT, not our reading of the prose. The lineage sheet
        # carries a `kind` column and writes UNKNOWN when it found no source for the
        # term in any transcript, version or frame. Scanning the whole row instead
        # over-matches: a buildable feature's provenance cell can mention a missing
        # source for something else entirely.
        kind = (str(r[col['kind']]).strip() if 'kind' in col and col['kind'] < len(r)
                and r[col['kind']] is not None else '')
        arith = (str(r[col['arithmetic']]).strip() if 'arithmetic' in col and col['arithmetic'] < len(r)
                 and r[col['arithmetic']] is not None else '')
        bad = (kind.upper() == 'UNKNOWN') or bool(UNBUILDABLE.search(arith))
        out[term] = dict(status=('UNBUILDABLE' if bad else 'BUILDABLE'),
                         kind=(kind or None), arithmetic=arith[:200],
                         note=(' | '.join(str(c) for c in r if c is not None))[:220])
    wb.close()
    return out


def signature(s):
    """What makes two strategies the SAME bet: base + the condition set, order-free."""
    cs = sorted((c['term'], c['op'], str(c['value'])) for c in s['conditions'])
    return (str(s.get('base') or ''), tuple(cs))


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    out_dir, books = sys.argv[1], sys.argv[2:]
    os.makedirs(out_dir, exist_ok=True)

    lineage, runs, merged, by_sig, by_name = {}, [], [], {}, {}
    for bi, b in enumerate(books):
        impl, meta = read_impl(b)
        lin = read_lineage(b)
        for t, d in lin.items():
            # worst result wins: once a book says a term is unbuildable, it stays flagged
            if t not in lineage or d['status'] == 'UNBUILDABLE':
                lineage[t] = d
        label = os.path.basename(b)
        runs.append(dict(book=label, strategies=len(impl), **meta))
        n_new = 0
        for s in impl:
            sig, nm = signature(s), s['strategy']
            if sig in by_sig:
                by_sig[sig]['also_in'].append(label)
                continue
            if nm in by_name:
                by_name[nm]['also_in'].append(label)
                by_name[nm]['name_reused_with_different_conditions'] = True
                continue
            rec = OrderedDict(s)
            rec['first_seen_in'] = label
            rec['also_in'] = []
            rec['novel'] = bi > 0            # NOVEL means: not present in any EARLIER book
            merged.append(rec)
            by_sig[sig] = rec
            by_name[nm] = rec
            n_new += 1
        runs[-1]['contributed_new'] = n_new

    # replication verdict per strategy
    for s in merged:
        terms = sorted({c['term'] for c in s['conditions']})
        bad = [t for t in terms if (lineage.get(t) or {}).get('status') == 'UNBUILDABLE']
        unknown = [t for t in terms if t not in lineage]
        s['terms'] = terms
        s['terms_unbuildable'] = bad
        s['terms_not_in_lineage'] = unknown
        s['replicable'] = not bad and not unknown
        s['blocked_reason'] = ('' if s['replicable'] else
                               '; '.join(filter(None, [
                                   (f'{len(bad)} term(s) the engine cannot rebuild: ' + ', '.join(bad)) if bad else '',
                                   (f'{len(unknown)} term(s) absent from the lineage sheet: ' + ', '.join(unknown)) if unknown else ''])))

    ready = [s for s in merged if s['replicable']]
    blocked = [s for s in merged if not s['replicable']]

    summary = dict(
        runs=runs, total_unique=len(merged),
        replicable=len(ready), blocked=len(blocked),
        distinct_terms=len({t for s in merged for t in s['terms']}),
        terms_unbuildable=sorted({t for s in merged for t in s['terms_unbuildable']}),
        terms_not_in_lineage=sorted({t for s in merged for t in s['terms_not_in_lineage']}))

    with open(os.path.join(out_dir, 'production_strategies.json'), 'w') as fh:
        json.dump(dict(summary=summary, strategies=merged), fh, indent=1, default=str)

    import csv
    with open(os.path.join(out_dir, 'production_strategies.csv'), 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['strategy', 'first_seen_in', 'novel', 'replicable', 'blocked_reason', 'base', 'tier',
                    'odds', 'oos_win', 'oos_roi', 'bets', 'backed', 'price_column', 'graded_against',
                    'min_odds_floor', 'hold_sec', 'window_sec', 'market_status_columns',
                    'reconciliation_gap', 'n_conditions', 'conditions'])
        for s in merged:
            w.writerow([s['strategy'], s['first_seen_in'], s['novel'], s['replicable'], s['blocked_reason'],
                        s['base'], s['tier'], s['odds'], s['oos_win'], s['oos_roi'], s['bets'], s['backed'],
                        s['price_column'], s['graded_against'], s['min_odds_floor'], s['hold_sec'],
                        s['window_sec'], s['market_status_columns'], s['reconciliation_gap'],
                        len(s['conditions']),
                        ' AND '.join(f"{c['term']} {c['op']} {c['value']}" for c in s['conditions'])])

    print(f'runs read: {len(runs)}')
    for r in runs:
        print(f"  {r['book']:34s} {r.get('validated_headline', '?')} validated "
              f"({r['strategies']} rows) · {r.get('run_utc', '?')} · contributed {r['contributed_new']} new")
    print(f'\nunique strategies      : {len(merged)}')
    print(f'  replicable today     : {len(ready)}')
    print(f'  BLOCKED              : {len(blocked)}')
    print(f'distinct terms used    : {summary["distinct_terms"]}')
    print(f'  engine cannot rebuild: {len(summary["terms_unbuildable"])}')
    print(f'  absent from lineage  : {len(summary["terms_not_in_lineage"])}')
    print(f'\nwrote {out_dir}/production_strategies.json and .csv')
    return 0


if __name__ == '__main__':
    sys.exit(main())
