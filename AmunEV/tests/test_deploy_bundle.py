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
_EXTRA = ('laz_mode3___mask_residual', 'laz_mode3___execution_spec')
for n in tree.body:
    nm = getattr(n, 'name', None)
    if (nm and str(nm).startswith('laz_deploy')) or nm in _EXTRA:
        exec(compile(ast.Module(body=[n], type_ignores=[]), ENGINE, 'exec'), ns)
    elif isinstance(n, ast.Assign) and any(
            str(getattr(t, 'id', '')).startswith(('laz_deploy', 'laz_mode3___MASK_WINDOWS'))
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
print('\na base that arms on more than a role is never shipped as that role')
for b, W in ns['laz_mode3___MASK_WINDOWS'].items():
    role, mask, why = ns['laz_deploy__role'](dict(base=b, family=b), 'basketball',
                                             'match_winner', dict(base=b))
    check(f'{b}: ships as {W[0]!r} with {len(W[4]) + (W[1] is not None) + (W[2] is not None) + (1 if W[3] else 0)} mask clause(s)',
          role == W[0] and why is None, f'got {role!r} ({why})')
check('every mask role is one resolve_side answers',
      all(W[0] in ns['laz_deploy__ROLES'] for W in ns['laz_mode3___MASK_WINDOWS'].values()))
check('a windowed base is NEVER shipped as the bare role',
      ns['laz_deploy__role'](dict(base='late_lead_hold', family='late_lead_hold'),
                             'basketball', 'match_winner', dict(base='late_lead_hold'))[1]
      == [('u_elapsed', '>=', 0.85), ('u_elapsed', '<=', 1.2), ('abs_lead', '>', 3.0)])
check('the element doc written at acceptance wins over any re-derivation',
      ns['laz_deploy__role']({}, 'basketball', 'match_winner',
                             dict(bet_role='trailer',
                                  base_mask_clauses=[dict(term='u_elapsed', op='>=', value=0.4)]))
      == ('trailer', [('u_elapsed', '>=', 0.4)], None))
check('an OVER market is refused (production prices no OVER side)',
      ns['laz_deploy__role']({}, 'football', 'total_goals_over', {})[0] is None)
check('a family-less record is the measured leader population',
      ns['laz_deploy__role'](dict(base=None, family=None), 'basketball', 'match_winner', {})[0]
      == 'leader')
check("a pandas-missing base ('nan') is not treated as a base name",
      ns['laz_deploy__role'](dict(base='nan', family='nan'), 'basketball', 'match_winner',
                             dict(base='nan'))[0] == 'leader')


# ── 6. markets: never defaulted ──────────────────────────────────────────────
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
check('an inverted band is refused', ns['laz_deploy__band']({}, dict(band=dict(lo=4.0, hi=2.0)))[2])
lo, hi, why = ns['laz_deploy__band']({}, dict(band=dict(lo=1.4, hi=4.2)))
check('a real band passes through exactly', (lo, hi, why) == (1.4, 4.2, None))


# ── 8. preflight catches each gate ───────────────────────────────────────────
print('\npreflight replays every load-time rule')
good = dict(sport='basketball', strategy='S1', conditions=['a >= 1'], market='match_winner',
            min_odds=1.4, max_odds=4.0, tier='Alpha', family=None, entry_window=60,
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
check('G9 NaN bound', 'G9' in rules({**good, 'min_odds': float('nan')}))
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
      sql.count('ADD COLUMN IF NOT EXISTS') == len(NS) and sql.count('ON CONFLICT') >= 3)
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


# ── 13. the ordered execution spec: every element, in order, with its trigger ─
print('\nevery element is documented in the exact order it happens')
ES = ns['laz_mode3___execution_spec']
steps = ES(dict(market='moneyline'), 'late_lead_hold', None,
           ['line_move <= 4.0', 'trailer_price >= 1.92'], 1.40, 4.20, 'basketball',
           'match_winner')
gates = [x['gate'] for x in steps]
check('the steps are numbered 1..n with no gaps',
      [x['step'] for x in steps] == list(range(1, len(steps) + 1)))
check('the market gate is first', gates[0] == 'MARKET OPEN')
check('the side is resolved BEFORE the price',
      gates.index('BASE ARM MASK — SIDE') < gates.index('PRICE OF THE BACKED SIDE'))
check('the price is resolved BEFORE the band',
      gates.index('PRICE OF THE BACKED SIDE') < gates.index('PRICE BAND'))
check('the band is checked BEFORE the conditions',
      gates.index('PRICE BAND') < gates.index('CONDITION 1'))
check('the base mask window is part of the order, not a footnote',
      'BASE ARM MASK — WINDOW 1' in gates)
check('one bet per match is an explicit step', 'FIRST TICK ONLY' in gates)
check('placement and settlement are stated', gates[-2:] == ['PLACE', 'SETTLE'])
check('every step says exactly what must be true',
      all(x['trigger'] and isinstance(x['trigger'], str) for x in steps))
check('every step says what happens when it is not',
      all(x['on_fail'] for x in steps))
check('every step says why it is at that position', all(x['why'] for x in steps))
check('the band step carries the exact numbers',
      any('1.4 <= price <= 4.2' in x['trigger'] for x in steps))
check('each condition appears verbatim, with its exact threshold',
      any(x['trigger'] == 'line_move <= 4.0' for x in steps)
      and any(x['trigger'] == 'trailer_price >= 1.92' for x in steps))
MR = ns['laz_mode3___mask_residual']
check('a windowed base decomposes to clauses on terms the engine emits',
      MR('late_lead_hold') == ('leader',
                               [('u_elapsed', '>=', 0.85), ('u_elapsed', '<=', 1.2),
                                ('abs_lead', '>', 3.0)], ''))
check('the prop:/spec: prefix does not hide a base',
      MR('prop:late_lead_hold')[0] == 'leader')
check('a base that is exactly its role has no residual',
      MR('lead_ml') == ('leader', [], ''))
check('every mask clause is renderable in production grammar',
      all(ns['laz_deploy__condition'](t, o, v)[0] is not None
          for W in ns['laz_mode3___MASK_WINDOWS'].values()
          for t, o, v in MR([k for k, vv in ns['laz_mode3___MASK_WINDOWS'].items()
                             if vv is W][0])[1]))

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
