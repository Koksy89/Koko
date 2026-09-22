#!/usr/bin/env python3.12
"""Trace a term to its definition in the engine source. Parses and greps only.

A term the workbook's lineage sheet marked UNKNOWN may still be fully defined in
the engine -- the lineage sheet reports what the GENOME knew at write time, and a
term built directly into the pool never reaches the genome. This separates the
two cases:

  DEFINED_IN_ENGINE   a registration, a provenance entry, a builder or a direct
                      pool assignment exists; the recipe can be recovered
  DERIVABLE_BY_NAME   the name decomposes into known parts (manufactured X_a__op__b,
                      or a pc_* PCA component whose manifest names its inputs)
  TRULY_UNKNOWN       nothing in the engine defines it

Usage: laz_term_trace.py ENGINE.py TERMS.txt [--json out.json]
"""
from __future__ import annotations

import ast
import json
import re
import sys
from collections import OrderedDict


def load(engine):
    src = open(engine, encoding='utf-8').read()
    return src, src.split('\n')


def find_sites(src, lines, term):
    """Every line that plausibly DEFINES this term, classified."""
    t = re.escape(term)
    pats = [
        ('genome_register', re.compile(rf"laz_genome__register\(\s*['\"]{t}['\"]")),
        ('genome_register', re.compile(rf"_GNm\.register\(\s*['\"]{t}['\"]")),
        ('provenance', re.compile(rf"^\s*['\"]{t}['\"]\s*:\s*dict\(")),
        ('provenance', re.compile(rf"['\"]{t}['\"]\s*:\s*dict\(.*(arith|inputs|builder|formula)")),
        ('pool_assign', re.compile(rf"pool\[\s*['\"]{t}['\"]\s*\]\s*=")),
        ('pool_assign', re.compile(rf"POOL\[\s*['\"]{t}['\"]\s*\]\s*=")),
        ('out_assign', re.compile(rf"\bout\[\s*['\"]{t}['\"]\s*\]\s*=")),
        ('frame_assign', re.compile(rf"\bd\[\s*['\"]{t}['\"]\s*\]\s*=")),
        ('setdefault', re.compile(rf"setdefault\(\s*['\"]{t}['\"]")),
        ('add_term', re.compile(rf"_add\(\s*['\"]{t}['\"]")),
        ('builders_map', re.compile(rf"BUILDERS\[\s*['\"]{t}['\"]\s*\]")),
        ('recipe', re.compile(rf"['\"]{t}['\"]\s*:\s*['\"][^'\"]{{8,}}")),
        # the engine's main feature registry: 'term': _f(lambda C: ...)
        ('builder_lambda', re.compile(rf"['\"]{t}['\"]\s*:\s*_f\(")),
        ('builder_lambda', re.compile(rf"['\"]{t}['\"]\s*:\s*lambda\b")),
        ('put_call', re.compile(rf"\bput\(\s*['\"]{t}['\"]")),
        ('emit_call', re.compile(rf"\b_?(emit|add|reg|register)\(\s*['\"]{t}['\"]")),
        # 'term': <arithmetic over other columns> inside an output dict
        ('dict_formula', re.compile(rf"['\"]{t}['\"]\s*:\s*[^,}}]*?(np\.|C\[|g\(|out\[|_f\(|[a-z_]+\s*[-+*/]\s*[a-z_]+)")),
        # 'term': <a local the enclosing function computed>, e.g. 'tg_imp_over': io
        ('dict_local', re.compile(rf"['\"]{t}['\"]\s*:\s*[a-z_][a-z0-9_]*\s*[,}}]")),
    ]
    hits = []
    for i, ln in enumerate(lines, 1):
        if term not in ln:
            continue
        for kind, p in pats:
            if p.search(ln):
                hits.append(dict(kind=kind, line=i, text=ln.strip()[:400]))
                break
    return hits


MANU = re.compile(r'^X_(?P<a>.+?)__(?P<op>minus|over|x|plus)__(?P<b>.+)$')
PC = re.compile(r'^pc_[0-9a-f]{8}$')


def classify(term, hits, src):
    if hits:
        return 'DEFINED_IN_ENGINE'
    m = MANU.match(term)
    if m:
        return 'DERIVABLE_BY_NAME'
    if PC.match(term):
        return 'PCA_COMPONENT'
    return 'TRULY_UNKNOWN'


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    engine, termfile = sys.argv[1], sys.argv[2]
    src, lines = load(engine)
    terms = [t.strip() for t in open(termfile) if t.strip()]

    out = OrderedDict()
    for t in terms:
        hits = find_sites(src, lines, t)
        # every other mention, for context (capped)
        mentions = sum(1 for ln in lines if t in ln)
        out[t] = dict(term=t, verdict=classify(t, hits, src),
                      n_define_sites=len(hits), n_mentions=mentions,
                      sites=hits[:6])

    order = ['DEFINED_IN_ENGINE', 'DERIVABLE_BY_NAME', 'PCA_COMPONENT', 'TRULY_UNKNOWN']
    counts = {k: sum(1 for v in out.values() if v['verdict'] == k) for k in order}
    print(f'{len(terms)} term(s) traced against {engine}\n')
    for k in order:
        print(f'  {k:20s} {counts[k]:3d}')
    print()
    for k in order:
        names = [t for t, v in out.items() if v['verdict'] == k]
        if names:
            print(f'{k}:')
            for nme in names:
                v = out[nme]
                first = v['sites'][0]['text'][:110] if v['sites'] else ''
                print(f"  {nme:26s} {v['n_define_sites']:2d} def · {v['n_mentions']:4d} mentions  {first}")
            print()

    if '--json' in sys.argv:
        p = sys.argv[sys.argv.index('--json') + 1]
        json.dump(out, open(p, 'w'), indent=1, default=str)
        print(f'wrote {p}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
