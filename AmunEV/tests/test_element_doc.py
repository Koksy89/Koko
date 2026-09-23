#!/usr/bin/env python3.12
"""Tests for documenting every element AT ACCEPTANCE (not at export).

Extracts the functions FROM THE ENGINE SOURCE BY AST. The engine is never imported or run.
"""
import ast, hashlib, json, re, sys, types
import numpy as np

ENGINE = 'AmunEV_Engine_V2.py'
WANT = ['laz_release__parse_conditions', 'laz_mode3___split_clauses',
        'laz_mode3___element_record', 'laz_production___conditions_raw',
        'laz_production__base_card', 'laz_production__stack',
        # [EXEC SPEC] the record now carries the ordered implementation
        'laz_mode3___execution_spec', 'laz_mode3___mask_residual',
        'laz_god2__RULES_min_odds']
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
ns.update(dict(_m=_m, np=np, re=re, _re=re, json=json, hashlib=hashlib,
               inspect=__import__('inspect'),
               laz_settlement__settled_by=lambda s: 'score (owner rule)',
               laz_production___BASE_WORDS={'lead_ml': 'backs whichever side leads'},
               _laz_code_fingerprint=lambda: 'deadbeefcafe0000',
               laz_sink__swallow=lambda tag, exc: None))
for node in tree.body:
    if isinstance(node, ast.Assign) and any(
            str(getattr(t, 'id', '')).startswith(('laz_mode3___MASK_WINDOWS', 'LAZ_GOD2_'))
            for t in node.targets):
        exec(compile(ast.Module(body=[node], type_ignores=[]), ENGINE, 'exec'), ns)
    if getattr(node, 'name', None) in WANT:
        exec(compile(ast.Module(body=[node], type_ignores=[]), ENGINE, 'exec'), ns)
ns['globals'] = lambda: ns
print(f'extracted {len(WANT)} function(s) by AST — engine never imported\n')

elem, stack = ns['laz_mode3___element_record'], ns['laz_production__stack']
fails = []
def check(name, cond, detail=''):
    print(f'  {"PASS" if cond else "FAIL"}  {name}' + (f'  — {detail}' if detail and not cond else ''))
    if not cond: fails.append(name)

pool = {'lead_streak': np.array([1.0, 2.0, 3.0]), 'u_drift': np.array([1.0, 2.0, 3.0])}
idx = np.array([0, 2])
rec = {'strategy_name': 'BB_LEAD_1', 'market': 'moneyline', 'text_rebuild': 'exact'}
chain = ['lead_streak >= 3', 'u_drift <= 2.0']

print('laz_mode3___element_record — the happy path')
d = elem(rec, 'lead_ml', 'lead_streak >= 3', 'search:lead_streak >= 3', 'search',
         1.8, 2.2, chain, idx, pool, 'basketball')
check('schema tagged', d['schema'] == 'amunev.element_doc/2')
# [EXEC SPEC] v2 carries the ordered implementation, not just the ingredients
check('the record carries the ORDERED execution', isinstance(d.get('execution'), list)
      and len(d['execution']) >= 6)
check('the gates are numbered 1..n with no gaps',
      [x['step'] for x in d['execution'] if x['step'] > 0]
      == list(range(1, sum(1 for x in d['execution'] if x['step'] > 0) + 1)))
_g = [x['gate'] for x in d['execution'] if x['step'] > 0]
check('the side is resolved first', _g[0] == 'SIDE')
check('the side is resolved before the price',
      _g.index('SIDE') < _g.index('PRICE OF THE BACKED SIDE'))
check('[GOD-2] the only price rule is the 1.40 floor; there is no ceiling',
      'MINIMUM ODDS' in _g
      and not any('BAND' in x or 'MAX' in x.upper() for x in _g))
check('[GOD-2] no base-mask window and no margin floor is a gate',
      not any(('u_elapsed' in x['trigger'] or 'abs_lead' in x['trigger'])
              for x in d['execution'] if x['step'] > 0))
check('market open is PLACEMENT, after the conditions, with the 60s wait',
      any(x['gate'] == 'PLACEMENT — MARKET OPEN' and '60s' in x['trigger']
          for x in d['execution']))
check('every step states its exact trigger and what happens on failure',
      all(x.get('trigger') and x.get('on_fail') and x.get('why') for x in d['execution']))
check('the base mask rides along as a NOTE that blocks nothing',
      all('NOT a gate' in x['gate'] for x in d['execution'] if x['step'] == 0))
check('the record names the production bet_role', 'bet_role' in d)
check('the record carries the base mask as clauses', isinstance(d.get('base_mask_clauses'), list))
check('the execution order has its own hash', len(str(d.get('exec_sha', ''))) == 16)
check('the spec hash now covers the mask, so two masks are two strategies',
      d['spec_sha'] != hashlib.sha256(json.dumps(
          dict(base=d['base'], conditions=d['conditions_text'], band=d['band'],
               seed=d['seed']['text'], market=d['market']),
          sort_keys=True, default=str).encode('utf-8')).hexdigest()[:16])
check('documented at acceptance', d['documented_at'] == 'acceptance')
check('seed text captured', d['seed']['text'] == 'lead_streak >= 3' and d['seed']['has_text'])
check('seed identity and kind captured', d['seed']['id'].startswith('search:') and d['seed']['kind'] == 'search')
check('clauses parsed', d['n_clauses'] == 2 and d['n_unreadable'] == 0, str(d['clauses']))
check('terms listed', d['terms'] == ['lead_streak', 'u_drift'], str(d['terms']))
check('band EXACT, not rounded', d['band']['lo'] == 1.8 and d['band']['hi'] == 2.2)
check('measured bets fingerprinted', d['measured']['n_bets'] == 2 and len(d['measured']['bets_sha']) == 16)
check('engine fingerprint carried', d['engine_fingerprint'] == 'deadbeefcafe0000')
check('ships unchanged', d['ships_unchanged'] is True, str(d['gaps']))
check('spec_sha is 16 hex', len(d['spec_sha']) == 16)

print('\ndeterminism')
d2 = elem(rec, 'lead_ml', 'lead_streak >= 3', 'search:lead_streak >= 3', 'search',
          1.8, 2.2, chain, idx, pool, 'basketball')
check('spec_sha stable', d2['spec_sha'] == d['spec_sha'])
check('bets_sha stable', d2['measured']['bets_sha'] == d['measured']['bets_sha'])
d3 = elem(rec, 'lead_ml', 'lead_streak >= 3', 'search:lead_streak >= 3', 'search',
          1.8, 2.2, ['lead_streak >= 4', 'u_drift <= 2.0'], idx, pool, 'basketball')
check('spec_sha moves when a threshold moves', d3['spec_sha'] != d['spec_sha'])

print('\nthe gaps it must catch AT acceptance')
d = elem(rec, 'lead_ml', None, 'tick:3', 'tick', 1.8, 2.2, chain, idx, pool, 'basketball')
check('a seed with no text blocks', d['ships_unchanged'] is False and any('no condition text' in g for g in d['gaps']), str(d['gaps']))
d = elem(rec, 'lead_ml', 'x', 's', 'search', 1.8, 2.2, ['recovered:Seed@d3'], idx, pool, 'basketball')
check('an unreadable clause blocks', d['ships_unchanged'] is False and d['n_unreadable'] == 1, str(d['gaps']))
check('the unreadable clause is NAMED, not dropped', d['unreadable'] == ['recovered:Seed@d3'], str(d['unreadable']))
d = elem(rec, 'lead_ml', 'x', 's', 'search', 1.8, 2.2, ['zzz >= 1'], idx, pool, 'basketball')
check('a term missing from the pool blocks', d['ships_unchanged'] is False and d['terms_absent_from_pool'] == ['zzz'], str(d['gaps']))
d = elem(dict(rec, text_rebuild='rebuilds 40 bets, 55 measured, 21 differ'), 'lead_ml', 'x', 's', 'search',
         1.8, 2.2, chain, idx, pool, 'basketball')
check('a failed round-trip blocks', d['ships_unchanged'] is False and any('round-trip' in g for g in d['gaps']), str(d['gaps']))

print('\nthe dossier READS the acceptance record')
good = elem(rec, 'lead_ml', 'lead_streak >= 3', 'search:lead_streak >= 3', 'search',
            1.8, 2.2, chain, idx, pool, 'basketball')
row = {'strategy_name': 'BB_LEAD_1', 'base': 'lead_ml', 'conditions': 'lead_streak >= 3 AND u_drift <= 2.0',
       'band': '1.8-2.2', 'min_odds': 1.8, 'market': 'moneyline',
       'element_doc': json.dumps(good, sort_keys=True, default=str), 'text_rebuild': 'exact'}
st = stack(row, 'basketball')
check('documented_at says acceptance', st['documented_at'] == 'acceptance', st['documented_at'])
check('spec_sha comes FROM the acceptance record', st['spec_sha'] == good['spec_sha'])
check('a SEED gate is present', any(g['kind'] == 'seed' for g in st['gates']), str([g['kind'] for g in st['gates']]))
sg = [g for g in st['gates'] if g['kind'] == 'seed'][0]
check('the seed gate carries the seed TEXT', sg['seed_text'] == 'lead_streak >= 3')
check('gate numbers still 1..n in order',
      [g['n'] for g in st['gates']] == list(range(1, len(st['gates']) + 1)),
      str([g['n'] for g in st['gates']]))
check('measured bets surfaced', (st['measured'] or {}).get('n_bets') == 2)
check('deployable', st['deployable'] is True, str(st['blocked']))

bad = elem(rec, 'lead_ml', None, 'tick:3', 'tick', 1.8, 2.2, chain, idx, pool, 'basketball')
st = stack(dict(row, element_doc=json.dumps(bad, sort_keys=True, default=str)), 'basketball')
check('a textless seed BLOCKS the dossier entry', st['deployable'] is False, str(st['blocked']))
check('the seed gate is marked not reproducible',
      [g for g in st['gates'] if g['kind'] == 'seed'][0]['reproducible'] is False)

print('\nback-compat: a row with no element_doc')
st = stack({'strategy_name': 'OLD', 'base': 'lead_ml', 'conditions': 'lead_streak >= 3',
            'band': '1.8-2.2', 'min_odds': 1.8}, 'basketball')
check('falls back to the recovery path', st['documented_at'] == 'export', st['documented_at'])
check('no seed gate invented', not any(g['kind'] == 'seed' for g in st['gates']))
check('still deployable', st['deployable'] is True, str(st['blocked']))
check('gate numbers still sequential', [g['n'] for g in st['gates']] == list(range(1, len(st['gates']) + 1)))

print('\nthe call site: documentation is the FIRST thing after validation')
L = src.split('\n')
i = next(i for i, l in enumerate(L) if "rec['text_rebuild'] = 'exact'" in l)
j = next(j for j, l in enumerate(L) if "rec['element_doc'] = json.dumps" in l)
k = next(k for k in range(j, j + 40) if '_key = (len(idx)' in L[k])
check('element_doc is written right after the round-trip verdict', 0 < j - i < 12, f'{i} -> {j}')
check('and before anything else consumes the record', j < k, f'{j} < {k}')
check('a documentation failure never loses the strategy',
      'never lose a validated strategy' in '\n'.join(L[j:k]))

print(f'\n{"ALL PASS" if not fails else "FAILURES: " + ", ".join(fails)}')
sys.exit(1 if fails else 0)
