#!/usr/bin/env python3.12
"""Tests for the generated deploy kit: the reference implementation's four gates."""
import json, sys, os
sys.path.insert(0, 'kit/basketball')
from reference_impl import run, conditions_hold, backed_price, market_open

fails=[]
def check(n,c,d=''):
    print(f'  {"PASS" if c else "FAIL"}  {n}'+(f'  — {d}' if d and not c else ''))
    if not c: fails.append(n)

S=[dict(strategy='S1', base='lead_ml', backed='home', price_column='home_odds',
        min_odds_floor=1.8, conditions=[dict(term='a',op='>=',value='3')])]

print('gate 1 — conditions')
check('true when it holds', conditions_hold(S[0], {'a':5}))
check('false when it does not', not conditions_hold(S[0], {'a':1}))
check('NULL never satisfies', not conditions_hold(S[0], {'a':None}))
check('NaN never satisfies', not conditions_hold(S[0], {'a':float('nan')}))
check('missing column never satisfies', not conditions_hold(S[0], {}))

print('\ngate 2 — price')
rows=[dict(match_id='m1', ts=1, a=5, home_odds=1.5, away_odds=3.0, market_status='Open')]
check('below the floor -> no bet', run(S, rows)==[], str(run(S,rows)))
rows=[dict(match_id='m1', ts=1, a=5, home_odds=2.0, away_odds=3.0, market_status='Open')]
b=run(S, rows); check('at/above the floor -> bet', len(b)==1 and b[0]['price']==2.0, str(b))
check('price comes from the named column', b[0]['price']==2.0)

print('\ngate 3 — market status')
rows=[dict(match_id='m1', ts=1, a=5, home_odds=2.0, away_odds=3.0, market_status='Suspended')]
check('suspended -> no bet', run(S, rows)==[])
rows=[dict(match_id='m1', ts=1, a=5, home_odds=2.0, away_odds=3.0)]
check('absent status is not a gate', len(run(S, rows))==1)

print('\ngate 4 — one bet per match, FIRST qualifying tick')
rows=[dict(match_id='m1', ts=1, a=1, home_odds=2.0, market_status='Open'),   # fails cond
      dict(match_id='m1', ts=2, a=5, home_odds=2.0, market_status='Open'),   # FIRST pass
      dict(match_id='m1', ts=3, a=9, home_odds=2.5, market_status='Open'),   # ignored
      dict(match_id='m2', ts=1, a=7, home_odds=2.2, market_status='Open')]
b=run(S, rows)
check('one bet per match', len(b)==2, str(b))
check('arms on the FIRST qualifying tick', b[0]['ts']==2, str(b))
check('later ticks of the same match ignored', [x['match_id'] for x in b]==['m1','m2'], str(b))

print('\nthe real kit loads and is self-consistent')
K=json.load(open('kit/basketball/strategies.json'))
M=json.load(open('kit/basketball/MANIFEST.json'))
F=json.load(open('kit/basketball/features.json'))
check('manifest count matches the file', M['counts']['deployable']==len(K), f"{M['counts']['deployable']} vs {len(K)}")
check('every strategy has >=1 condition', all(len(s['conditions'])>0 for s in K))
check('every strategy has a SQL predicate', all(s['sql_predicate'] for s in K))
check('every deployable term is buildable',
      all(F[c['term']]['buildable'] for s in K for c in s['conditions']))
check('no blocked term reaches a deployable strategy',
      not ({c['term'] for s in K for c in s['conditions']} & set(M['blocked_terms'])))
check('every term has a card', all(os.path.exists(f'kit/basketball/features/{t}.md') for t in F))
check('buildable terms quote engine source',
      all(F[t]['definitions'] or F[t]['verdict']=='LINEAGE_BUILDABLE' for t in F if F[t]['buildable']))
check('excluded strategies all name a reason',
      all(s['blocked_reason'] for s in json.load(open('kit/basketball/excluded_strategies.json'))))
check('manifest states its limits', len(M['limits'])>=3)

print(f'\n{"ALL PASS" if not fails else "FAILURES: "+", ".join(fails)}')
sys.exit(1 if fails else 0)
