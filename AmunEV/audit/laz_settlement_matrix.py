#!/usr/bin/env python3.12
"""laz_settlement_matrix.py — every distinct way a bet can settle, per sport.

Nothing is executed. Four sources, kept apart because they answer different questions:

  A. HOW THE RESULT IS DERIVED   laz_settlement__SCORE_SPORTS vs ODDS_INFER_SPORTS
     A score sport settles from the recorded score. An odds-inference sport has no
     usable final score in the feed, so the winner is the side priced <= 1.35 on the
     last row where both prices are valid; below that the match is UNSETTLEABLE and
     excluded, never proxy-settled.

  B. WHAT THE ENGINE'S BASES SETTLE ON   the `outcome` on every base
  C. WHAT THE STRATEGIES SETTLE ON       the `market` on every strategy entry
  D. WHAT PRODUCTION CAN ACTUALLY SETTLE the evaluators' SETTLEABLE_MARKETS

B and C are what the engine can FIND. D is what the live stack can PAY OUT. The gap
between them is the set of bets that would be placed and never resolve.
"""
import ast, collections, json, os, re, sys

ENGINE = sys.argv[1] if len(sys.argv) > 1 else 'AmunEV_Engine_V2.py'
PROD = sys.argv[2] if len(sys.argv) > 2 else '/root/.claude/uploads/5ae43139-c189-53a1-b53a-86251fc03117'
src = open(ENGINE, encoding='utf-8').read()
tree = ast.parse(src)


def const(name):
    for n in tree.body:
        if isinstance(n, ast.Assign) and getattr(n.targets[0], 'id', '') == name:
            try:
                return ast.literal_eval(n.value)
            except Exception:
                return None
    return None


SCORE = set(const('laz_settlement__SCORE_SPORTS') or ())
INFER = set(const('laz_settlement__ODDS_INFER_SPORTS') or ())
THRESH = const('laz_settlement__INFER_THRESHOLD')

# ── B. base outcomes ────────────────────────────────────────────────────────
base_outcome = {}
for n in ast.walk(tree):
    if isinstance(n, ast.Call) and getattr(n.func, 'id', '') == '_b' and len(n.args) >= 2:
        try:
            base_outcome[ast.literal_eval(n.args[0])] = ast.literal_eval(n.args[1])
        except Exception:
            pass
for n in ast.walk(tree):
    if isinstance(n, ast.Assign) and any(getattr(x, 'id', '') == 'laz_registry__BASES'
                                         for x in n.targets):
        for k, v in zip(n.value.keys, n.value.values):
            for kw in v.keywords:
                if kw.arg == 'market':
                    try:
                        base_outcome.setdefault(k.value, ast.literal_eval(kw.value))
                    except Exception:
                        pass

# ── C. strategy markets, per sport ──────────────────────────────────────────
def kv(e, key):
    if isinstance(e, ast.Dict):
        for k, v in zip(e.keys, e.values):
            if isinstance(k, ast.Constant) and k.value == key and isinstance(v, ast.Constant):
                return v.value
    if isinstance(e, ast.Call):
        for w in e.keywords:
            if w.arg == key and isinstance(w.value, ast.Constant):
                return w.value.value
    return None


per_sport = collections.defaultdict(collections.Counter)
no_sport = collections.Counter()
for n in tree.body:
    if not (isinstance(n, ast.Assign) and len(n.targets) == 1
            and isinstance(n.targets[0], ast.Name)
            and isinstance(n.value, ast.List) and n.value.elts):
        continue
    for e in n.value.elts:
        nm = kv(e, 'name')
        if not nm:
            continue
        mk = kv(e, 'market') or kv(e, 'settles_on') or '(none stated)'
        sp = kv(e, 'sport')
        (per_sport[sp] if sp else no_sport)[mk] += 1

# ── D. what production settles ──────────────────────────────────────────────
prod = {}
if os.path.isdir(PROD):
    for f in os.listdir(PROD):
        # eFootball's evaluator is NOT named *_lazarus_efb_strategies.py -- it is
        # efootball_book_evaluator.py, and it declares SETTLEABLE (not
        # SETTLEABLE_MARKETS). Globbing on the one pattern reported efootball as
        # settling NOTHING, which is wrong and would have been reported as fact.
        if not (f.endswith('_lazarus_efb_strategies.py')
                or f.endswith('efootball_book_evaluator.py')):
            continue
        txt = open(os.path.join(PROD, f), encoding='utf-8', errors='ignore').read()
        sp = re.search(r"SPORT\s*=\s*'([a-z]+)'", txt)
        mm = (re.search(r'SETTLEABLE_MARKETS\s*=\s*\{(.*?)\}', txt, re.S)
              or re.search(r'^SETTLEABLE\s*=\s*\{(.*?)\}', txt, re.S | re.M))
        if sp and mm:
            prod[sp.group(1)] = sorted(set(re.findall(r"'([a-z_0-9]+)'", mm.group(1))))

ALL = sorted(SCORE | INFER | set(prod))
print(f'{ENGINE}\n')
print('A.  HOW THE RESULT IS DERIVED')
print(f'    score sports          : {sorted(SCORE)}')
print(f'    odds-inference sports : {sorted(INFER)}  (winner = side priced <= {THRESH} '
      f'on the last valid row; otherwise UNSETTLEABLE and excluded)')
print()
print('D.  WHAT PRODUCTION CAN SETTLE, per sport')
for sp in sorted(prod):
    print(f'    {sp:14} {len(prod[sp])}  {prod[sp]}')
print()
print('C.  WHAT THE STRATEGIES SETTLE ON, per sport (from every strategy entry)')
for sp in sorted(per_sport):
    c = per_sport[sp]
    print(f'    {sp:14} {len(c)} distinct  ' +
          ', '.join(f'{k}×{v}' for k, v in c.most_common()))
if no_sport:
    print(f'    (no sport key) {len(no_sport)} distinct  ' +
          ', '.join(f'{k}×{v}' for k, v in no_sport.most_common(12)))
print()
print('B.  DISTINCT BASE OUTCOMES IN THE ENGINE')
oc = collections.Counter(base_outcome.values())
print(f'    {len(oc)} distinct across {len(base_outcome)} bases')
for k, v in oc.most_common():
    print(f'      {k:22} {v} base(s)')
json.dump(dict(score_sports=sorted(SCORE), infer_sports=sorted(INFER),
               infer_threshold=THRESH, production_settleable=prod,
               strategy_markets={k: dict(v) for k, v in per_sport.items()},
               strategy_markets_no_sport=dict(no_sport),
               base_outcomes=base_outcome),
          open('/tmp/laz_settlement.json', 'w'), indent=1)
print('\n  -> /tmp/laz_settlement.json')
