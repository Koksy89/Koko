#!/usr/bin/env python3.12
"""Unit tests for the repaired production-documentation path.

Extracts the functions FROM THE ENGINE SOURCE BY AST and executes only those,
with stubs for everything else. The engine is never imported or run.
"""
import ast, hashlib, json, re, sys, types

class _NpStub:
    """the extracted code touches numpy only for isfinite"""
    @staticmethod
    def isfinite(x):
        try:
            f = float(x)
        except Exception:
            return False
        return f == f and f not in (float('inf'), float('-inf'))
np = _NpStub()

ENGINE = 'AmunEV_Engine_V2.py'
WANT = ['laz_release__parse_conditions', 'laz_production___conditions_raw',
        'laz_production__base_card', 'laz_production__stack',
        'laz_production__strategy_record']

src = open(ENGINE, encoding='utf-8').read()
tree = ast.parse(src)
lines = src.split('\n')
ns = {}

# ---- stubs for everything the extracted functions reach out to -------------
def _lead(*a, **k):
    """the real base mask stands in as a named function with source"""
    return None

BASES = {
    'lead_ml': dict(fn=_lead, needs=['sd'], sports='all', evidence='Gate 0 EDGE in six sports.'),
    'model_pick': dict(fn=None, needs=[], sports=('efootball',), market='moneyline',
                       pool_built=True, backs='the side the scorer favours', evidence='built in the sweep'),
    'wall_under': dict(fn=_lead, needs=['h5', 'a5', 's00'], market='totals',
                       blocked_by='totals market status', evidence='BLOCKED base'),
}
LAZ_OWNER = {'combination': {'status_columns': {'moneyline': ('market_status',),
                                                'any': ('total_goals_market_active', 'market_status')},
                             'open_values': ('open', 'OPEN', 1, True)},
             'version': 'v186'}

def _m(name):
    if name == 'laz_registry':
        return types.SimpleNamespace(BASES=BASES)
    if name == 'laz_owner':
        return types.SimpleNamespace(LAZ_OWNER=LAZ_OWNER)
    return None

def laz_settlement__settled_by(sport):
    return 'score (owner rule); else last validated odds <= 1.35; else UNSETTLED'

ns.update(dict(_m=_m, np=np, re=re, json=json, hashlib=hashlib, inspect=__import__('inspect'),
               laz_settlement__settled_by=laz_settlement__settled_by,
               laz_production___BASE_WORDS={'lead_ml': 'backs whichever side is leading'},
               _re=re))

for node in tree.body:
    if getattr(node, 'name', None) in WANT:
        exec(compile(ast.Module(body=[node], type_ignores=[]), ENGINE, 'exec'), ns)
for w in WANT:
    assert w in ns, f'failed to extract {w}'
print(f'extracted {len(WANT)} function(s) from {ENGINE} by AST — engine never imported\n')

parse_conditions = ns['laz_release__parse_conditions']
stack = ns['laz_production__stack']
record = ns['laz_production__strategy_record']
conds_raw = ns['laz_production___conditions_raw']

fails = []
def check(name, cond, detail=''):
    print(f'  {"PASS" if cond else "FAIL"}  {name}' + (f'  — {detail}' if detail and not cond else ''))
    if not cond:
        fails.append(name)

# ---------------------------------------------------------------- parser ----
print('laz_release__parse_conditions')
c = parse_conditions('lead_streak >= 3 AND 1.5 <= u_drift <= 2.5')
check('AND chain + band parsed', len(c) == 2 and c[0]['op'] == '>=' and c[1]['op'] == 'band', str(c))
c = parse_conditions('lead_streak >= 3; u_flat < 0.2')
check('SEMICOLON delimiter parsed', len(c) == 2, str(c))
c = parse_conditions('lead_streak >= 3 AND some weird clause')
check('unparsed clause DROPPED by default (back-compat)', len(c) == 1, str(c))
c = parse_conditions('lead_streak >= 3 AND some weird clause', keep_unparsed=True)
check('unparsed clause RETURNED when asked', len(c) == 2 and c[1]['op'] == 'UNPARSED', str(c))
check('unparsed carries the raw text', c[1].get('raw') == 'some weird clause', str(c[1]))

# ------------------------------------------------------ condition recovery --
print('\nlaz_production___conditions_raw')
check('reads conditions', conds_raw({'conditions': 'a >= 1'}) == ('a >= 1', 'conditions'))
check('falls back to trigger', conds_raw({'trigger': 'b < 2'}) == ('b < 2', 'trigger'))
check('falls back to raw_spec', conds_raw({'raw_spec': 'c == 3'}) == ('c == 3', 'raw_spec'))
check('joins a list form', conds_raw({'conditions': ['a >= 1', 'b < 2']})[0] == 'a >= 1 AND b < 2')
check("ignores 'nan'", conds_raw({'conditions': 'nan', 'spec': 'd > 4'}) == ('d > 4', 'spec'))
check('empty when truly absent', conds_raw({'name': 'x'}) == ('', None))

# ------------------------------------------------------------- the stack ----
print('\nlaz_production__stack')
rec = {'strategy_name': 'BB_LEAD_1', 'base': 'lead_ml', 'conditions': 'lead_streak >= 3 AND 1.5 <= u_drift <= 2.5',
       'band': '1.8-2.2', 'min_odds': 1.8, 'market': 'moneyline'}
st = stack(rec, 'basketball')
kinds = [g['kind'] for g in st['gates']]
check('base gate is FIRST', kinds[0] == 'base', str(kinds))
check('both conditions present', kinds.count('condition') == 2, str(kinds))
check('price/status/dedupe/settle gates appended',
      kinds[-4:] == ['price', 'status', 'dedupe', 'settle'], str(kinds[-4:]))
check('gate numbers are 1..n in order', [g['n'] for g in st['gates']] == list(range(1, len(st['gates']) + 1)))
check('base mask SOURCE is documented', bool(st['gates'][0]['mask_source']), 'mask_source empty')
check('base needs recorded', st['gates'][0]['frame_arrays_required'] == ['sd'])
check('band becomes lo/hi', st['gates'][-4]['band_lo'] == 1.8 and st['gates'][-4]['band_hi'] == 2.2)
check('expression names the base', st['expression'].startswith('BASE(lead_ml)'), st['expression'])
check('expression carries both clauses and the price gate',
      'lead_streak >= 3' in st['expression'] and 'u_drift' in st['expression']
      and 'backed_price >= 1.8' in st['expression'], st['expression'])
check('deployable when everything reproduces', st['deployable'] is True, str(st['blocked']))
check('spec_sha is 16 hex', len(st['spec_sha']) == 16 and all(ch in '0123456789abcdef' for ch in st['spec_sha']))

# determinism
check('spec_sha deterministic across calls', stack(rec, 'basketball')['spec_sha'] == st['spec_sha'])
rec2 = dict(rec, conditions='lead_streak >= 4 AND 1.5 <= u_drift <= 2.5')
check('spec_sha changes when a threshold changes', stack(rec2, 'basketball')['spec_sha'] != st['spec_sha'])

# ---- the 165/175 defect: a base with no recorded mask must BLOCK -----------
print('\nthe 165-of-175 defect')
st_bad = stack({'strategy_name': 'X', 'base': 'seed_tick_hotspot_7', 'conditions': 'a >= 1'}, 'basketball')
check('unknown base BLOCKS the strategy', st_bad['deployable'] is False, str(st_bad['blocked']))
check('the block names the base', 'seed_tick_hotspot_7' in ' '.join(st_bad['blocked']), str(st_bad['blocked']))
st_pool = stack({'strategy_name': 'Y', 'base': 'model_pick', 'conditions': 'a >= 1'}, 'efootball')
check('pool-built base is accepted', st_pool['deployable'] is True, str(st_pool['blocked']))

# ---- an unparsable measured clause must BLOCK, never vanish ----------------
st_u = stack({'strategy_name': 'Z', 'base': 'lead_ml', 'conditions': 'a >= 1 AND totally unparsable'}, 'basketball')
check('unparsable clause BLOCKS', st_u['deployable'] is False, str(st_u['blocked']))
check('unparsable clause still COUNTED, not dropped', st_u['n_conditions'] == 2 and st_u['n_unparsed'] == 1,
      f"n={st_u['n_conditions']} unparsed={st_u['n_unparsed']}")

# ---- a record with NO conditions key at all --------------------------------
st_alt = stack({'strategy_name': 'W', 'base': 'lead_ml', 'trigger': 'lead_streak >= 3'}, 'basketball')
check('conditions recovered from an alternate key', st_alt['n_conditions'] == 1
      and st_alt['conditions_source_key'] == 'trigger', str(st_alt['conditions_source_key']))

# ------------------------------------------------------- strategy record ----
print('\nlaz_production__strategy_record')
r = record(rec, 'basketball')
for k in ('name', 'laz_id', 'sport', 'tier', 'family', 'base', 'side_rule', 'market', 'min_odds',
          'conditions', 'condition_terms', 'arming', 'settlement', 'oos_win', 'oos_roi', 'odds',
          'bets', 'n_is', 'n_oos', 'gate', 'engine_version', 'enabled', 'port_status'):
    if k not in r:
        fails.append(f'record missing legacy key {k}')
check('every legacy key preserved', not [f for f in fails if 'legacy key' in f])
check('new keys present', all(k in r for k in ('base_card', 'stack', 'stack_expression',
                                               'spec_sha', 'deployable', 'blocked', 'n_conditions')))
check('condition_terms carries term/op/value', r['condition_terms'][0]['term'] == 'lead_streak'
      and r['condition_terms'][0]['op'] == '>=', str(r['condition_terms'][:1]))
check('port_status READY when deployable', r['port_status'] == 'READY', r['port_status'])
rb = record({'strategy_name': 'Q', 'base': 'unknown_base', 'conditions': 'a >= 1'}, 'basketball')
check('port_status BLOCKED when not', rb['port_status'] == 'BLOCKED', rb['port_status'])
check('band recorded', r['band'] == '1.8-2.2')
check('json-serialisable (the bundle is written as JSON)',
      isinstance(json.dumps(r, default=str), str))

print(f'\n{"ALL PASS" if not fails else "FAILURES: " + ", ".join(fails)}')
sys.exit(1 if fails else 0)
