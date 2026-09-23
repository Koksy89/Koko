#!/usr/bin/env python3.12
"""laz_deploy: the engine emits what production loads.

Extracted by AST; the engine is never imported, executed or unpickled. Every
expectation below is checked against the PRODUCTION SOURCE the owner supplied, not
against a restatement of it -- the point of this file is that the two agree.
"""
import ast, glob, hashlib, importlib.util, json, os, re, struct, sys, tempfile

ENGINE = 'AmunEV_Engine_V2.py'
PROD_DIR = '/root/.claude/uploads/5ae43139-c189-53a1-b53a-86251fc03117'
src = open(ENGINE, encoding='utf-8').read()
tree = ast.parse(src)

ns = {'os': os, 'json': json, 're': re, 'sys': sys}
import pandas as pd, numpy as np
ns['pd'] = pd
ns['np'] = np
_EXTRA = ('laz_mode3___mask_residual', 'laz_mode3___execution_spec',
          'laz_god2__RULES_min_odds', 'laz_god2__verify')
for n in tree.body:
    nm = getattr(n, 'name', None)
    if (nm and str(nm).startswith('laz_deploy')) or nm in _EXTRA:
        exec(compile(ast.Module(body=[n], type_ignores=[]), ENGINE, 'exec'), ns)
    elif isinstance(n, ast.Assign) and any(
            str(getattr(t, 'id', '')).startswith(
                ('laz_deploy', 'laz_mode3___MASK_WINDOWS', 'LAZ_GOD2_'))
            for t in n.targets):
        exec(compile(ast.Module(body=[n], type_ignores=[]), ENGINE, 'exec'), ns)
_SW = []
ns['_m'] = lambda x: (type('O', (), {'LAZ_OWNER': {'paths': {'output_dir': os.getcwd()}}})()
                      if x == 'laz_owner' else None)
ns['laz_sink__swallow'] = lambda t, e: _SW.append((t, type(e).__name__))
ns['laz_xl___engine_sha256'] = lambda: 'testsha00000000'
ns['LAZ_ENGINE_VERSION'] = 'test'
ns['laz_propose__BASES'] = {}
ns['laz_production___load_production_catalogue'] = lambda: (set(), set())
ns['laz_god2__RULES_min_odds'] = lambda: 1.40
ns['globals'] = lambda: ns
print(f'extracted {sum(1 for k in ns if k.startswith("laz_deploy"))} laz_deploy names by AST '
      '— engine never imported\n')

fails = []
def check(name, cond, detail=''):
    print(f'  {"PASS" if cond else "FAIL"}  {name}' + (f'  — {detail}' if detail and not cond else ''))
    if not cond:
        fails.append(name)

def prod(suffix):
    """The production file whose name ENDS with this suffix.

    The uploads carry a hash prefix, and a loose glob matched ebasketball for
    basketball -- which quietly compared the engine against the wrong evaluator.
    """
    g = [f for f in glob.glob(os.path.join(PROD_DIR, '*'))
         if os.path.basename(f).split('-', 1)[-1] == suffix]
    return open(g[0], encoding='utf-8').read() if g else None


# ── 1. the grammar is production's, not a paraphrase of it ───────────────────
print('the transcribed contract matches the production source')
bb = prod('basketball_lazarus_efb_strategies.py')
lf = None
if bb is None:
    print('  SKIP  production sources not present here — the contract cannot be '
          're-derived from them in this environment, so every other section still runs')
else:
    m = re.search(r"_COND = re\.compile\(r'(.+?)'\s*\n\s*r'(.+?)',", bb, re.S)
    prod_cond = (m.group(1) + m.group(2)) if m else None
    check('_COND is transcribed verbatim from the basketball evaluator',
          prod_cond == ns['laz_deploy__COND_RE'], f'{prod_cond!r} vs {ns["laz_deploy__COND_RE"]!r}')
    for f, sport in (('basketball_lazarus_efb_strategies.py', 'basketball'),
                     ('ebasketball_lazarus_efb_strategies.py', 'ebasketball'),
                     ('realfootball_lazarus_efb_strategies.py', 'football'),
                     ('tennis_lazarus_efb_strategies.py', 'tennis'),
                     ('eTennis_lazarus_efb_strategies.py', 'etennis'),
                     ('tabletennis_lazarus_efb_strategies.py', 'tabletennis'),
                     ('esports_lazarus_efb_strategies.py', 'esports')):
        s = prod(f)
        if not s:
            continue
        mm = re.search(r'SETTLEABLE_MARKETS\s*=\s*\{(.*?)\}', s, re.S)
        got = set(re.findall(r"'([a-z_0-9]+)'", mm.group(1))) if mm else set()
        check(f'{sport}: SETTLEABLE_MARKETS matches the evaluator',
              got == set(ns['laz_deploy__SETTLEABLE'][sport]),
              f'{sorted(got)} vs {sorted(ns["laz_deploy__SETTLEABLE"][sport])}')
    lf = prod('laz_features.py')
    if lf:
        roles = set(re.findall(r"if role == '([a-z_]+)'", lf))
        fixed = set(re.findall(r"_ROLE_FIXED = \{(.*?)\}", lf, re.S))
        fixedn = set(re.findall(r"'([A-Za-z_]+)'", list(fixed)[0])) if fixed else set()
        check('every role resolve_side answers is in the role vocabulary',
              roles <= set(ns['laz_deploy__ROLES']), str(sorted(roles - set(ns['laz_deploy__ROLES']))))
        check('_ROLE_FIXED matches laz_features exactly',
              fixedn == set(ns['laz_deploy__ROLE_FIXED']),
              f'{sorted(fixedn)} vs {sorted(ns["laz_deploy__ROLE_FIXED"])}')
        check('no role is offered that resolve_side cannot answer',
              set(ns['laz_deploy__ROLE_LIVE']) <= roles,
              str(sorted(set(ns['laz_deploy__ROLE_LIVE']) - roles)))


# ── 2. thresholds: the token production parses, or nothing ───────────────────
print('\nevery threshold is a token production can read')
COND = re.compile(ns['laz_deploy__COND_RE'], re.IGNORECASE)
T = ns['laz_deploy__threshold']
for v in (3, 3.0, 0.35, 140.0, 1.1237, 28.2017, -0.5, 1e-05, 0.0, -160.315, 2 / 3, 1e16):
    tok = T(v)
    check(f'{v!r} renders to a parseable token ({tok!r})',
          tok is not None and COND.match(f'x >= {tok}') is not None and float(tok) == float(v))
for v in (float('inf'), float('-inf'), float('nan')):
    check(f'{v!r} is REFUSED, not rendered', T(v) is None)
check("a '>= -inf' clause is refused before it reaches the registry",
      ns['laz_deploy__condition']('x', '>=', float('-inf'))[0] is None)
check('scientific notation never reaches the registry',
      'e' not in (T(1e-05) or 'e') and 'e' not in (T(1e16) or 'e'))
check('a range clause is refused (no evaluator for these sports parses one)',
      ns['laz_deploy__condition']('x', 'between', 1)[0] is None)
check('a non-identifier term is refused',
      ns['laz_deploy__condition']('a b', '>=', 1)[0] is None)
check('a boolean renders as True/False, which _COND accepts',
      ns['laz_deploy__condition']('flag', '==', True)[0] == 'flag == True')


# ── 3. column names: Postgres truncates at 63 bytes, silently ────────────────
print('\nevery column name survives Postgres')
LONG = ['X_X_u_elapsed__minus__u_time_in_lead__minus__X_u_pace__over__u_time_in_lead',
        'X_X_u_elapsed__minus__u_time_in_lead__over__X_u_pace__over__u_time_in_lead',
        'X_X_minute__x__u_pace__minus__X_u_elapsed__minus__u_time_in_lead']
taken = {}
cols = [ns['laz_deploy__column_name'](f, taken) for f in LONG]
check('every long term is shortened to <= 63 bytes',
      all(len(c.encode()) <= 63 for c in cols), str([len(c) for c in cols]))
check('shortening is collision-free', len(set(cols)) == len(cols))
check('shortening is deterministic across calls',
      cols == [ns['laz_deploy__column_name'](f, {}) for f in LONG])
check('a short name is kept as it is (nothing existing moves)',
      ns['laz_deploy__column_name']('prog_score', {}) == 'prog_score')
check('a name Postgres would fold is folded here first',
      ns['laz_deploy__column_name']('X_a__over__b', {}) == 'x_a__over__b')


# ── 4. manufactured terms: the leaves production actually reads ──────────────
print('\nmanufactured terms resolve to the same leaves laz_features reads')
if lf:
    pns = {'re': re}
    exec(lf[lf.index('_OPS = {'):lf.index('def resolve(name, f):')], pns)
    def ref(n):
        if not n.startswith('X_'):
            return [n]
        p = pns['_split_top'](n[2:])
        if p is None:
            return [n[2:]]
        return ref(p[0]) + ref(p[2])
    for t in LONG + ['X_a_score__over__home_odds', 'X_minute__x__u_pace', 'prog_score']:
        check(f'leaves match _split_top: {t[:46]}', ns['laz_deploy__leaves'](t) == ref(t),
              f'{ns["laz_deploy__leaves"](t)} vs {ref(t)}')


# ── 5. the base masks production cannot reproduce are refused ────────────────
print('\nwho is backed is READ FROM THE BASE, not from a table')
check('there is no family->role table left in the engine',
      'laz_deploy__FAMILY_ROLE' not in src and 'laz_mode3___MASK_WINDOWS' not in src)
BSIDE = ns['laz_deploy__base_side']
# stub the engine's own registries with their real shapes
_lead_src = ("def laz_registry___lead(c):\n    return (np.where(c['sd'] > 0, 'home', "
             "np.where(c['sd'] < 0, 'away', None)), 1, 'back the in-play leader')")
_q4_src = ("lambda C: np.where((C['el'] >= 0.75) & (C['sd'] > 0) & (C['sd'] <= 6), 'away', "
           "np.where((C['el'] >= 0.75) & (C['sd'] < 0) & (C['sd'] >= -6), 'home', None))")
_dog_src = ("def laz_registry___dog_lead(c):\n    dog = np.where(c['pm_fav'] == 'home', 'away', "
            "np.where(c['pm_fav'] == 'away', 'home', None))\n    return (np.where((dog == 'home') "
            "& (c['sd'] > 0) | (dog == 'away') & (c['sd'] < 0), dog, None), 1, 'the prematch "
            "underdog who is NOW leading')")
_drift_src = ("def laz_registry___drifted(c):\n    return (np.where(c['drift_h'] > c['drift_a'], "
              "'home', 'away'), 1, 'back whichever side has drifted furthest')")
_STUB = {'lead_ml': (_lead_src, 'moneyline', ''),
         'q4_close_trailer': (_q4_src, 'match_ml', ''),
         'dog_leading': (_dog_src, '', ''),
         'DRIFTED': (_drift_src, '', ''),
         'Leader_Trap': ('', '', 'Leader by 1 goal, odds <= threshold, last 75%+ of match, '
                                 'minute >= 2, bet on Draw'),
         'Leader': ('', '', 'Leader by 1 goal, odds >= threshold, not halftime/fulltime'),
         'Loser_1G': ('', '', 'Losing team down by 1 goal in 1st half, odds >= threshold'),
         'HT_Leader_1': ('', '', 'Halftime leader by 1 goal, odds >= threshold'),
         'Fav_Up_1_Type1': ('', '', 'Favorite scored first goal AND in 1st half'),
         'nothing_at_all': ('', '', '')}
ns['laz_deploy__base_source'] = lambda b: _STUB.get(
    str(b).split(':', 1)[-1] if ':' in str(b) else str(b), ('', '', ''))
for base, want, why in (
        ('lead_ml', 'leader', 'the mask returns home when sd > 0'),
        ('q4_close_trailer', 'trailer', 'the mask returns AWAY when sd > 0 — a regex race '
                                        'read this as a leader and backed the wrong team'),
        ('dog_leading', 'dog_leader', 'the filtered role is claimed before the plain one'),
        ('DRIFTED', 'drifted', 'drift_h > drift_a'),
        ('spec:Leader_Trap', 'Draw', 'its own rule says "bet on Draw" — the NAME says Leader'),
        ('spec:Leader', 'leader', 'its own rule says "Leader by 1 goal"'),
        ('spec:Loser_1G', 'trailer', 'its own rule says "Losing team down by 1 goal"'),
        ('spec:HT_Leader_1', None, 'a HALFTIME leader is not the side leading right now'),
        ('spec:Fav_Up_1_Type1', None, 'the first scorer — production has no such role')):
    got = BSIDE(base)
    check(f'{base}: {want!r} — {why}', got['role'] == want,
          f'got {got["role"]!r} ({got.get("evidence") or got.get("why","")[:70]})')
check('a base the engine never declares yields NO role, and says so',
      BSIDE('nothing_at_all')['role'] is None
      and 'states no side' in BSIDE('nothing_at_all')['why'])
check('every role it can return is one resolve_side answers',
      all((BSIDE(b)['role'] in ns['laz_deploy__ROLES'] or BSIDE(b)['role'] is None)
          for b in _STUB))
check('the derivation records the exact source fragment it read',
      bool(BSIDE('lead_ml')['evidence']))
ns['laz_deploy__base_source'] = None
del ns['laz_deploy__base_source']
import importlib as _il
for _n in tree.body:
    if getattr(_n, 'name', '') == 'laz_deploy__base_source':
        exec(compile(ast.Module(body=[_n], type_ignores=[]), ENGINE, 'exec'), ns)
check('an OVER market is refused (production prices no OVER side)',
      ns['laz_deploy__role']({}, 'football', 'total_goals_over', {})[0] is None)
check('a family-less record is the measured leader population',
      ns['laz_deploy__role'](dict(base=None, family=None), 'basketball', 'match_winner', {})[0]
      == 'leader')
check("a pandas-missing base ('nan') is not treated as a base name",
      ns['laz_deploy__role'](dict(base='nan', family='nan'), 'basketball', 'match_winner',
                             dict(base='nan'))[0] == 'leader')
check('the element doc written at acceptance wins over any re-derivation',
      ns['laz_deploy__role']({}, 'basketball', 'match_winner',
                             dict(bet_role='trailer'))[0] == 'trailer')

print('\nno market is ever defaulted')
ns['laz_propose__BASES'] = {'c': [dict(name='spread_dog_cover', outcome='match_spread'),
                                  dict(name='late_lead_hold', outcome='match_ml'),
                                  dict(name='match_total_pace', outcome='match_total')]}
mk, why = ns['laz_deploy__market'](dict(base='spread_dog_cover'), 'basketball', {})
check('a spread base is refused, not bent into a moneyline', mk is None and 'settles' in (why or ''))
mk, why = ns['laz_deploy__market'](dict(base='match_total_pace'), 'basketball', {})
check('a totals base that does not say UNDER or OVER is refused',
      mk is None and 'UNDER or OVER' in (why or ''))
check('a tg_under base settles the points total in a points sport',
      ns['laz_deploy__market'](dict(base='tg_under'), 'basketball', {})[0] == 'total_points_under')
check('the same base settles the goals total in a goals sport',
      ns['laz_deploy__market'](dict(base='tg_under'), 'football', {})[0] == 'total_goals_under')
check('an unknown sport is refused outright',
      ns['laz_deploy__market'](dict(base='lead_ml'), 'curling', {})[0] is None)


# ── 7. bands ─────────────────────────────────────────────────────────────────
print('\nthe price band is a real band')
check('a NaN bound is refused (it would admit every price)',
      ns['laz_deploy__band'](dict(band='nan-3.0'), {})[0] is None)
lo, hi, why = ns['laz_deploy__band']({}, dict(band=dict(lo=1.4, hi=4.2)))
check('[GOD-2] the floor passes through and the CEILING IS DISCARDED',
      (lo, hi, why) == (1.4, None, None))
check('[GOD-2] an inverted band still yields no ceiling',
      ns['laz_deploy__band']({}, dict(band=dict(lo=4.0, hi=2.0)))[1] is None)


# ── 8. preflight catches each gate ───────────────────────────────────────────
print('\npreflight replays every load-time rule')
good = dict(sport='basketball', strategy='S1', conditions=['a >= 1'], market='match_winner',
            min_odds=1.4, max_odds=None, tier='Alpha', family=None, entry_window=60,
            bet_role='leader', enabled=True, terms=['a'], spec_sha='x', bets_sha='y',
            n_bets=10, oos_win=60.0, oos_roi=12.0, odds=2.0)
NS = [dict(feature='a', column_name='a', kind='feature')]
check('a clean bundle passes', ns['laz_deploy__preflight']([good], NS, 'basketball') == [])
def rules(row, nsr=NS):
    return {f['rule'] for f in ns['laz_deploy__preflight']([row], nsr, 'basketball')}
check('G1 unparseable clause', 'G1' in rules({**good, 'conditions': ['a >= 1e-5']}))
check('G4 unsettleable market', 'G4' in rules({**good, 'market': 'asian_handicap'}))
check('G5 term with no namespace row', 'G5' in rules({**good, 'conditions': ['zz >= 1']}))
check('G7 unresolvable role', 'G7' in rules({**good, 'bet_role': 'prop:late_lead_hold'}))
check('G9 a null or NaN FLOOR', 'G9' in rules({**good, 'min_odds': float('nan')}))
check('G9 a floor below the owner 1.40', 'G9' in rules({**good, 'min_odds': 1.2}))
check('[GOD-2] a max_odds ceiling is itself a preflight FAILURE',
      'G2-MAX' in rules({**good, 'max_odds': 4.2}))
check('[GOD-2] no ceiling is the correct, passing state',
      ns['laz_deploy__preflight']([{**good, 'max_odds': None}], NS, 'basketball') == [])
check('G10 zero conditions', 'G10' in rules({**good, 'conditions': []}))
check('G11 a column name over 63 bytes',
      'G11' in rules(good, [dict(feature='a', column_name='a' * 64, kind='feature')]))
check('G11 two features sharing one column',
      'G11' in rules(good, [dict(feature='a', column_name='c', kind='feature'),
                            dict(feature='b', column_name='c', kind='feature')]))
check('G3 an empty registry is itself a failure',
      'G3' in {f['rule'] for f in ns['laz_deploy__preflight']([], NS, 'basketball')})


# ── 9. the PCA transform production recomputes ───────────────────────────────
print('\nthe frozen PCA transform reproduces the engine bit for bit')
pcas = {'pc_abc12345': dict(terms=['a', 'b', 'c'], mu=[1.0, 2.0, 3.0], sdv=[0.5, 1.5, 2.5],
                            w=[0.6, -0.5, 0.62], n_fit=1200)}
code = ns['laz_deploy__pca_module'](pcas, 'basketball')
d = tempfile.mkdtemp()
p = os.path.join(d, 'gen.py')
open(p, 'w').write(code)
spec = importlib.util.spec_from_file_location('gen', p)
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)
ok = True
for seed in range(6):
    rnd = np.random.default_rng(seed)
    f = {t: float(rnd.uniform(-5, 5)) for t in 'abc'}
    got = gen.compute(dict(f))['pc_abc12345']
    want = float(np.float32(((np.array([f[t] for t in 'abc']) - np.array(pcas['pc_abc12345']['mu']))
                             / np.array(pcas['pc_abc12345']['sdv'])) @ np.array(pcas['pc_abc12345']['w'])))
    ok &= (got == want)
check('the generated module equals numpy float32 exactly, every time', ok)
check('a missing input yields None, never 0',
      gen.compute({'a': 1.0, 'b': None, 'c': 3.0})['pc_abc12345'] is None)
check('a NaN input yields None, never 0',
      gen.compute({'a': 1.0, 'b': float('nan'), 'c': 3.0})['pc_abc12345'] is None)
check('the module imports nothing but the standard library',
      set(re.findall(r'^import (\w+)', code, re.M)) <= {'math', 'struct'})
check('a composite with no frozen transform is not shipped',
      ns['laz_deploy__pca_module']({}, 'basketball').count('PCA = {') == 1)


# ── 10. the SQL is ordered, idempotent and injection-safe ────────────────────
print('\nthe SQL loads in the order production reads it')
sql = ns['laz_deploy__sql']([good], NS, 'basketball')
i_col, i_ns, i_reg = (sql.index('ALTER TABLE rt_allsports_laz_features'),
                      sql.index('INSERT INTO laz_feature_namespace'),
                      sql.index('INSERT INTO laz_strategy_registry'))
check('columns, then namespace, then strategies', i_col < i_ns < i_reg)
check('one transaction', sql.startswith('-- laz_deploy') and 'BEGIN;' in sql and sql.rstrip().endswith('COMMIT;'))
check('re-runnable: every write is ADD COLUMN IF NOT EXISTS or ON CONFLICT',
      sql.count('ADD COLUMN IF NOT EXISTS') >= len(NS) and sql.count('ON CONFLICT') >= 3)
check('the placement policy (GOD-2) is written with the strategies',
      'laz_placement_policy' in sql and 'market_closed_wait_secs' in sql
      and 'nan_price_is_no_bet' in sql)
check('conditions ship as a text[] of single clauses', "ARRAY['a >= 1']::text[]" in sql)
check('what this run did not re-validate is retired',
      'SET enabled = false WHERE sport' in sql)
evil = {**good, 'strategy': "x'); DROP TABLE laz_strategy_registry;--", 'tier': "a'b"}
esql = ns['laz_deploy__sql']([evil], NS, 'basketball')
check('every string is escaped', "DROP TABLE" in esql and "''" in esql
      and ";--'" in esql.replace("''", "'"))
_nansql = ns['laz_deploy__sql']([{**good, 'oos_roi': float('nan')}], NS, 'basketball')
_vals = [l for l in _nansql.splitlines() if l.startswith('INSERT INTO laz_strategy_provenance')]
check('a NaN number is written NULL, never the literal nan',
      len(_vals) == 1 and 'nan' not in _vals[0].split('VALUES', 1)[1].lower()
      and 'NULL' in _vals[0].split('VALUES', 1)[1])


# ── 11. end to end, and deterministic ────────────────────────────────────────
print('\nthe bundle is written, complete and byte-identical between runs')
ns['laz_release__parse_conditions'] = None
# the emit path reads the base's own source, so the two bases used here must exist
_EMIT_STUB = {
    'lead_ml': ("def laz_registry___lead(c):\n    return (np.where(c['sd'] > 0, 'home', "
                "np.where(c['sd'] < 0, 'away', None)), 1, 'back the in-play leader')",
                'moneyline', ''),
    'spread_dog_cover': ('', 'spread', 'the underdog to stay inside the posted spread')}
ns['laz_deploy__base_source'] = lambda b: _EMIT_STUB.get(str(b), ('', '', ''))
rows = pd.DataFrame([dict(strategy_name='S1', base='lead_ml', family='lead_ml', tier='Alpha',
                          market='moneyline', band='1.40-4.20', conditions='', odds=2.0,
                          oos_win=60.0, oos_roi=12.0, bets=200, window_sec=60,
                          text_rebuild='exact',
                          element_doc=json.dumps(dict(
                              base='lead_ml', market='moneyline', spec_sha='abc',
                              band=dict(lo=1.4, hi=4.2), text_rebuild='exact',
                              measured=dict(n_bets=200, bets_sha='fp'),
                              clauses=[dict(term='prog_score', op='<=', value=0.35),
                                       dict(term='u_open', op='>=', value=1.2)]))),
                     dict(strategy_name='S2', base='spread_dog_cover', family='spread_dog_cover',
                          tier='Beta', market='', band='1.40-4.20', conditions='', odds=2.0,
                          oos_win=55.0, oos_roi=9.0, bets=150, window_sec=60,
                          text_rebuild='exact',
                          element_doc=json.dumps(dict(
                              base='spread_dog_cover', market='', spec_sha='def',
                              band=dict(lo=1.4, hi=4.2), text_rebuild='exact',
                              measured=dict(n_bets=150, bets_sha='fp2'),
                              clauses=[dict(term='prog_score', op='<=', value=0.5)])))])
d1, d2 = tempfile.mkdtemp(), tempfile.mkdtemp()
ns['laz_deploy___PCA_CACHE'].clear(); ns['laz_deploy___CATALOGUE_CACHE'].clear()
p1 = ns['laz_deploy__emit'](rows, 'basketball', out_dir=d1, log=lambda *a: None)
ns['laz_deploy___PCA_CACHE'].clear(); ns['laz_deploy___CATALOGUE_CACHE'].clear()
p2 = ns['laz_deploy__emit'](rows, 'basketball', out_dir=d2, log=lambda *a: None)
for f in ('deploy.sql', 'MANIFEST.json', 'verify_live.py', 'RUNBOOK.md', 'quarantine.csv'):
    check(f'{f} is written', os.path.exists(os.path.join(d1, f)))
def digest(dd):
    return {f: hashlib.sha256(open(os.path.join(dd, f), 'rb').read()).hexdigest()
            for f in sorted(os.listdir(dd)) if f != 'MANIFEST.json'}
check('two runs produce byte-identical output', digest(d1) == digest(d2))
man = json.load(open(os.path.join(d1, 'MANIFEST.json')))
check('the deployable strategy shipped', man['n_deployable'] == 1)
check('the spread strategy was withheld, not dropped', man['n_withheld'] == 1)
check('every validated strategy is accounted for',
      man['n_deployable'] + man['n_withheld'] == man['n_validated'] == 2)
check('preflight passes on what was written', man['preflight'] == 'PASS')
check('the shipped row carries a role production resolves',
      man['strategies'][0]['bet_role'] in ns['laz_deploy__ROLES'])
check('the shipped row carries its measurement fingerprint',
      man['strategies'][0]['bets_sha'] == 'fp' and man['strategies'][0]['n_bets'] == 200)
q = json.load(open(os.path.join(d1, 'quarantine.json')))
check('the withheld strategy keeps its full acceptance record',
      q[0]['element_doc'].get('spec_sha') == 'def')
check('and states exactly why it was withheld', any('settle' in str(r) for r in q[0]['reasons']))
check('verify_live.py is valid python and reads only',
      ast.parse(open(os.path.join(d1, 'verify_live.py')).read()) is not None
      and 'INSERT' not in open(os.path.join(d1, 'verify_live.py')).read().upper()
      .replace('INSERT INTO LAZ', ''))
check('a round-trip that does not rebuild the measured bets is withheld',
      not ns['laz_deploy__row'](dict(strategy_name='S3', base='lead_ml', family='lead_ml',
                                     band='1.40-4.20', text_rebuild='2 clauses the parser cannot read',
                                     element_doc=json.dumps(dict(
                                         base='lead_ml', market='moneyline',
                                         band=dict(lo=1.4, hi=4.2),
                                         text_rebuild='2 clauses the parser cannot read',
                                         clauses=[dict(term='a', op='>=', value=1)]))),
                                'basketball')['ok'])
check('nothing was swallowed during a clean emit', _SW == [], str(_SW))


# ── 12. it is wired into the run, and nothing runs the engine ────────────────
print('\nwired into the run, and nothing is executed')
xl = [n for n in tree.body if getattr(n, 'name', '') == 'laz_xl__write'][0]
calls = [n for n in ast.walk(xl) if isinstance(n, ast.Call)
         and getattr(n.func, 'id', '') == 'laz_deploy__emit']
check('laz_xl__write emits the bundle', len(calls) == 1)
guarded = False
for t in ast.walk(xl):
    if isinstance(t, ast.Try) and any(getattr(c.func, 'id', '') == 'laz_deploy__emit'
                                      for c in ast.walk(t) if isinstance(c, ast.Call)):
        guarded = True
check('the emission cannot fail the run', guarded)
api = re.search(r"'laz_deploy': \((.*?)\),", src)
check('laz_deploy is registered so _m() resolves it', api is not None)
check('every registered API name exists',
      all(f'laz_deploy__{a}' in ns for a in re.findall(r"'(\w+)'", api.group(1))) if api else False,
      str([a for a in re.findall(r"'(\w+)'", api.group(1)) if f'laz_deploy__{a}' not in ns]) if api else '')
dep = src[src.index('# LAZ_BRAIN MODULE: laz_deploy'):src.index('# LAZ_BRAIN MODULE: laz_workbook')]
check('the module never executes, imports or unpickles target code',
      not re.search(r'(?<![.\w])(exec|eval|__import__|execfile)\s*\(', dep)
      and not re.search(r'\b(pickle|subprocess|os\.system|os\.popen|runpy)\b', dep)
      and not re.search(r'(?<!re)\.compile\s*\(', dep))
check('the legacy PCA manifest merges instead of overwriting',
      '_old_pc.update({' in src and "_jp.dump(_old_pc, _pf1" in src)


# ── 13. the ordered spec: the OWNER'S rules and the strategy's own, nothing else ─
print('\nevery element is documented in the exact order it happens')
ES = ns['laz_mode3___execution_spec']
steps = ES(dict(market='moneyline'), 'late_lead_hold', None,
           ['line_move <= 4.0', 'trailer_price >= 1.92'], 1.40, 4.20, 'basketball',
           'match_winner')
gates_live = [x['gate'] for x in steps if x['step'] > 0]
notes = [x for x in steps if x['step'] == 0]
check('the gates are numbered 1..n with no gaps',
      [x['step'] for x in steps if x['step'] > 0] == list(range(1, len(gates_live) + 1)))
check('the side is resolved first', gates_live[0] == 'SIDE')
check('the price is resolved BEFORE the floor',
      gates_live.index('PRICE OF THE BACKED SIDE') < gates_live.index('MINIMUM ODDS'))
check('the floor is the owner 1.40',
      any(x['trigger'] == 'price >= 1.4' for x in steps))
check('[GOD-2] there is NO maximum odds anywhere in the order',
      not any('<= 4.2' in x['trigger'] or 'max' in x['gate'].lower()
              or 'BAND' in x['gate'] for x in steps))
check("[GOD-2] the base-mask window is NOT a gate",
      'BASE ARM MASK — WINDOW 1' not in gates_live
      and not any('u_elapsed' in x['trigger'] for x in steps if x['step'] > 0))
check("[GOD-2] the margin floor abs_lead > 3 is NOT a gate",
      not any('abs_lead' in x['trigger'] for x in steps if x['step'] > 0))
check('[GOD-2] there is no feed-freshness gate', 'FEED FRESH' not in gates_live)
check('[GOD-2] the base arm region is not in the order AT ALL, not even as a note',
      notes == [] and not any('u_elapsed' in x['trigger'] or 'abs_lead' in x['trigger']
                              for x in steps))
check('the base is read only for WHICH SIDE is backed',
      steps[0]['gate'] == 'SIDE'
      and ('resolve_side' in steps[0]['trigger']
           or 'the outcome named by base' in steps[0]['trigger']))
check("the strategy's OWN conditions are there, verbatim",
      any(x['trigger'] == 'line_move <= 4.0' for x in steps)
      and any(x['trigger'] == 'trailer_price >= 1.92' for x in steps))
check('one bet per match is an explicit gate', 'FIRST TICK ONLY' in gates_live)
check('market open is PLACEMENT, and it comes after the conditions',
      gates_live.index('PLACEMENT — MARKET OPEN') > gates_live.index('CONDITION 2'))
check('the placement step states the 60-second wait and the re-price',
      any('60s' in x['trigger'] and 'WAIT' in x['trigger'] for x in steps))
check('a closed market abandons the BET, never the strategy',
      any('abandon THIS BET only' in x['on_fail'] for x in steps))
check('settlement is the outcome the strategy names, not a hard-coded one',
      any(x['gate'] == 'SETTLE' and 'the outcome the strategy names' in x['trigger']
          for x in steps))
check('every gate says what must be true, what happens if not, and why',
      all(x['trigger'] and x['on_fail'] and x['why'] for x in steps))

print('\nGOD-2: the validation and verification bible')
import hashlib as _hh
check('the seal verifies',
      _hh.sha256(ns['LAZ_GOD2_RULE'].encode()).hexdigest() == ns['LAZ_GOD2_RULE_SHA'])
check('it names all five primary rules',
      all(k in ns['LAZ_GOD2_RULE'] for k in
          ('MINIMUM ODDS', 'IN-SAMPLE VOLUME', 'OUT-OF-SAMPLE VOLUME', 'PROFIT',
           'NO FORWARD LOOKING')))
check('it names the three secondary rules',
      all(k in ns['LAZ_GOD2_RULE'] for k in
          ('FIRST TICK ONLY', 'MARKET OPEN', 'SETTLE')))
check('it states there is NO maximum odds',
      'There is NO maximum' in ns['LAZ_GOD2_RULE']
      or 'no maximum' in ns['LAZ_GOD2_RULE'].lower())
check('it forbids adding any blocker without the owner',
      'WHAT MAY NEVER BE ADDED' in ns['LAZ_GOD2_RULE'])
check('it requires every existing blocker to be NAMED to the owner',
      'Silence about a' in ns['LAZ_GOD2_RULE'])
check('it may be changed only by "Unchained approves"',
      'Unchained approves' in ns['LAZ_GOD2_RULE'])
check('the machine-readable rules match the text',
      ns['LAZ_GOD2_RULES']['min_odds'] == 1.40
      and ns['LAZ_GOD2_RULES']['max_odds'] is None
      and ns['LAZ_GOD2_RULES']['min_n_is'] == 100
      and ns['LAZ_GOD2_RULES']['min_n_oos'] == 100
      and ns['LAZ_GOD2_RULES']['min_oos_roi'] == 0.0
      and ns['LAZ_GOD2_RULES']['market_open_wait_secs'] == 60.0)
_keep = ns['LAZ_GOD2_RULE']
ns['LAZ_GOD2_RULE'] = _keep.replace('There is NO maximum', 'A maximum may be set')
try:
    ns['laz_god2__verify'](strict=True)
    tampered = False
except RuntimeError as e:
    tampered = 'ALTERED' in str(e) and 'Unchained approves' in str(e)
ns['LAZ_GOD2_RULE'] = _keep
check('weakening the bible is DETECTED and refused', tampered)
check('the engine rules block no longer carries a band_hi_ceiling',
      "'band_hi_ceiling':" not in src,
      'the key is still set somewhere in LAZ_OWNER')
check("the engine's own floor is the owner's 1.40",
      "'min_odds_hard': 1.4" in src)
check('the release path no longer computes a ceiling',
      'THE BAND HAS NO CEILING' in src and '_LAD = [1.4, 1.8, 2.2' not in src)
check('band_hi is no longer a required registry column, so a NULL top is legal',
      "'band_lo', 'stride', 'settled_by', 'engine_sha'" in src)

print('\nnothing the search validated is thrown away')
_sql_held = ns['laz_deploy__sql'](
    [good], NS, 'basketball',
    held=[dict(strategy='H1', conditions=['a >= 1'], market='spread', min_odds=1.4,
               max_odds=4.0, tier='Beta', family=None, entry_window=60, bet_role='leader',
               blocked_reason='basketball settles no spread')])
check('a strategy production cannot settle is STILL written to the registry',
      "'H1'" in _sql_held)
check('it is written DISABLED so it cannot fire or break the load',
      'enabled, blocked_reason) VALUES' in _sql_held and 'false,' in _sql_held)
check('and it carries the exact reason, in the database',
      'basketball settles no spread' in _sql_held)
check('a term production does not compute is NOT a reason to withhold a strategy',
      ns['laz_deploy__row'](dict(strategy_name='S9', base='lead_ml', family='lead_ml',
                                 band='1.40-4.20', text_rebuild='exact',
                                 element_doc=json.dumps(dict(
                                     base='lead_ml', market='moneyline', bet_role='leader',
                                     base_mask_clauses=[], band=dict(lo=1.4, hi=4.2),
                                     text_rebuild='exact',
                                     clauses=[dict(term='a_term_nobody_has', op='>=',
                                                   value=1)]))),
                           'basketball')['ok'])
check('the row records what the bundle must supply for it',
      ns['laz_deploy__row'](dict(strategy_name='S9', base='lead_ml', family='lead_ml',
                                 band='1.40-4.20', text_rebuild='exact',
                                 element_doc=json.dumps(dict(
                                     base='lead_ml', market='moneyline', bet_role='leader',
                                     base_mask_clauses=[], band=dict(lo=1.4, hi=4.2),
                                     text_rebuild='exact',
                                     clauses=[dict(term='a_term_nobody_has', op='>=',
                                                   value=1)]))),
                           'basketball')['row']['needs_feature'] == ['a_term_nobody_has'])

print(f'\n{"ALL PASS" if not fails else str(len(fails)) + " FAILED: " + ", ".join(fails)}')
sys.exit(1 if fails else 0)
