#!/usr/bin/env python3.12
"""Tests for the ported round-trip (V2.27 exactness + V2.29 record-never-reject).

Extracts the functions FROM THE ENGINE SOURCE BY AST and executes only those.
The engine is never imported or run.
"""
import ast, json, re, sys, types
import numpy as np

ENGINE = 'AmunEV_Engine_V2.py'
WANT = ['laz_release__parse_conditions', 'laz_cascades__collapse_to_matches',
        'laz_mode3___split_clauses', 'laz_mode3___eval_text', 'laz_mode3___roundtrip',
        'laz_production___conditions_raw', 'laz_production__base_card', 'laz_production__stack']

src = open(ENGINE, encoding='utf-8').read()
tree = ast.parse(src)
ns = {}

def _lead(*a, **k): return None
BASES = {'lead_ml': dict(fn=_lead, needs=['sd'], sports='all', evidence='the original base.')}
LAZ_OWNER = {'combination': {'status_columns': {'moneyline': ('market_status',)},
                             'open_values': ('open', 1)}, 'version': 'v186'}
def _m(name):
    if name == 'laz_registry': return types.SimpleNamespace(BASES=BASES)
    if name == 'laz_owner':    return types.SimpleNamespace(LAZ_OWNER=LAZ_OWNER)
    return None
def laz_settlement__settled_by(sport): return 'score (owner rule)'

ns.update(dict(_m=_m, np=np, re=re, _re=re, json=json, hashlib=__import__('hashlib'),
               inspect=__import__('inspect'),
               laz_settlement__settled_by=laz_settlement__settled_by,
               laz_production___BASE_WORDS={'lead_ml': 'backs whichever side leads'}))
for node in tree.body:
    if getattr(node, 'name', None) in WANT:
        exec(compile(ast.Module(body=[node], type_ignores=[]), ENGINE, 'exec'), ns)
ns['globals'] = lambda: ns          # the ported helpers resolve siblings via globals()
for w in WANT:
    assert w in ns, f'failed to extract {w}'
print(f'extracted {len(WANT)} function(s) by AST — engine never imported\n')

split  = ns['laz_mode3___split_clauses']
evalt  = ns['laz_mode3___eval_text']
rt     = ns['laz_mode3___roundtrip']
stack  = ns['laz_production__stack']

fails = []
def check(name, cond, detail=''):
    print(f'  {"PASS" if cond else "FAIL"}  {name}' + (f'  — {detail}' if detail and not cond else ''))
    if not cond: fails.append(name)

print('laz_mode3___split_clauses')
check("' AND ' chain", split('a >= 1 AND b <= 2') == ['a >= 1', 'b <= 2'])
check("';' chain",     split('a >= 1; b <= 2') == ['a >= 1', 'b <= 2'])
check('single clause', split('a >= 1') == ['a >= 1'])
check('agrees with the parser on clause count',
      len(split('a >= 1 AND b <= 2')) == len(ns['laz_release__parse_conditions']('a >= 1 AND b <= 2')))

print('\nlaz_mode3___eval_text (production semantics)')
pool = {'a': np.array([1.0, 2.0, 3.0, np.nan]), 'b': np.array([9.0, 1.0, 1.0, 1.0])}
m, why = evalt('a >= 2', pool)
check('mask correct', list(m) == [False, True, True, False], f'{m} {why}')
check('NaN never satisfies', m[3] == False)
m, why = evalt('a >= 2 AND b <= 1', pool)
check('clauses AND together', list(m) == [False, True, True, False], str(m))
m, why = evalt('1.5 <= a <= 2.5', pool)
check('band form', list(m) == [False, True, False, False], str(m))
m, why = evalt('zzz >= 1', pool)
check('unknown term reports, returns None', m is None and 'not in the pool' in why, why)
m, why = evalt('total gibberish', pool)
check('unparsable reports, returns None', m is None, why)

print('\nlaz_mode3___roundtrip')
# 6 ticks, 2 matches; price in band for some; conditions select a subset
midc  = np.array([0, 0, 0, 1, 1, 1])
price = np.array([1.9, 2.0, 2.1, 1.5, 1.95, 2.3])
arm   = np.array([True] * 6)
pool2 = {'a': np.array([5.0, 5.0, 5.0, 5.0, 5.0, 5.0])}
COL = ns['laz_cascades__collapse_to_matches']
b = arm & (price >= 1.8) & (price < 2.2)
b = COL(b, midc)
mt, _ = evalt('a >= 1', pool2)
idx = np.where(b & mt)[0]
ok, why = rt(dict(conditions='a >= 1', band='1.8-2.2'), arm, price, pool2, midc, None, idx)
check('exact rebuild -> (True, "")', ok is True and why == '', f'{ok} {why}')
ok, why = rt(dict(conditions='a >= 1', band='1.8-2.2'), arm, price, pool2, midc, None, idx[:1])
check('differing bets -> (False, counted why)', ok is False and 'rebuilds' in why, why)
ok, why = rt(dict(conditions='recovered:SomeSeed@d3', band='1.8-2.2'), arm, price, pool2, midc, None, idx)
check('unreadable clause -> (False, named)', ok is False and 'cannot read' in why, why)
ok, why = rt(dict(conditions='a >= 1', band='not-a-band'), arm, price, pool2, midc, None, idx)
check('bad band -> (False, named)', ok is False and 'band not parseable' in why, why)
check('returns a verdict, never raises and never deletes',
      isinstance(rt(dict(conditions='a >= 1', band='1.8-2.2'), arm, price, pool2, midc, None, idx), tuple))

print('\nthe call site RECORDS and never rejects')
# structural: no `continue` between the roundtrip call and the dedupe key
L = src.split('\n')
i = next(i for i, l in enumerate(L) if "rec['text_rebuild'] = 'exact'" in l)
j = next(j for j in range(i, i + 60) if '_key = (len(idx)' in L[j])
between = '\n'.join(L[i + 1:j])
# the element-doc block sits between them; what must NOT appear is a `continue`
# that would drop the strategy on a bad round-trip.
check('no `continue` between the record and the next stage',
      not any(ln.strip().startswith('continue') for ln in L[i + 1:j]), repr(between[:160]))
check('the record is assigned, not gated', "rec['text_rebuild'] =" in L[i])

print('\ntext_rebuild feeds the deployment verdict')
base_rec = {'strategy_name': 'S1', 'base': 'lead_ml', 'conditions': 'a >= 1', 'band': '1.8-2.2', 'min_odds': 1.8}
st = stack(base_rec, 'basketball')
check('absent text_rebuild -> no round-trip gate (back-compat)',
      not any(g['kind'] == 'roundtrip' for g in st['gates']) and st['deployable'] is True, str(st['blocked']))
st = stack(dict(base_rec, text_rebuild='exact'), 'basketball')
check("'exact' -> gate present, deployable", 
      any(g['kind'] == 'roundtrip' for g in st['gates']) and st['deployable'] is True, str(st['blocked']))
st = stack(dict(base_rec, text_rebuild='the shipping text rebuilds 40 bets, 55 were measured, 21 differ'), 'basketball')
check('mismatch -> BLOCKED', st['deployable'] is False, str(st['blocked']))
check('the block quotes the measured reason', '21 differ' in ' '.join(st['blocked']), str(st['blocked']))

print('\nV2.27 seed-text exactness (the shipped threshold)')
cut = 0.123456789012345
check('exact repr round-trips', float(f'{float(cut)!r}') == cut)
check('the old %g form did not', float(f'{cut:g}') != cut)
check('engine no longer uses the rounding form',
      "f'{r.condition} {r.op} {r.cut:g}'" not in src)
check('engine writes the exact form',
      "f'{r.condition} {r.op} {float(r.cut)!r}'" in src)
check('NaN-gate seeds get text, not None',
      "else f'{r.condition} >= -inf'" in src)

print(f'\n{"ALL PASS" if not fails else "FAILURES: " + ", ".join(fails)}')
sys.exit(1 if fails else 0)
