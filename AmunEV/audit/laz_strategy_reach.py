#!/usr/bin/env python3.12
"""laz_strategy_reach.py — is this strategy list reachable by code that runs?

Two graphs, both from the AST, nothing executed:

  1. CALL GRAPH over top-level functions, seeded from the engine's entry points.
     Whatever is not reachable here is dead code, and a strategy list read only
     from dead code is not wired in.
  2. MODULE-LEVEL DATA GRAPH: name -> the names its right-hand side reads. A
     strategy list reaches a live consumer if some LIVE function reads a name
     that transitively contains it.

A list that fails both is defined, sized, documented -- and never seen by
anything the engine runs.
"""
import ast, collections, json, sys

path = sys.argv[1] if len(sys.argv) > 1 else 'AmunEV_Engine_V2.py'
tree = ast.parse(open(path, encoding='utf-8').read())

defs, rhs, line = {}, collections.defaultdict(set), {}
for n in tree.body:
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        defs[n.name] = n
        line[n.name] = n.lineno
    elif isinstance(n, ast.Assign):
        rd = {x.id for x in ast.walk(n.value) if isinstance(x, ast.Name)}
        for t in n.targets:
            if isinstance(t, ast.Name):
                rhs[t.id] |= rd
                line.setdefault(t.id, n.lineno)

# 1. live functions
uses = {k: {x.id for x in ast.walk(v) if isinstance(x, ast.Name)}
        | {x.attr for x in ast.walk(v) if isinstance(x, ast.Attribute)}
        | {x.value for x in ast.walk(v)
           if isinstance(x, ast.Constant) and isinstance(x.value, str)}
        for k, v in defs.items()}
ENTRY = {'main', '_entrypoint', 'laz_runner__main', 'laz_mode3__find', 'laz_mode1__run',
         'laz_mode2__run', 'laz_mode5__explore', 'laz_startup__prepare',
         'laz_engine_main__run', 'run_pipeline_sequential', 'laz_audit__gate',
         'laz_production__export', 'laz_batch__plan', 'laz_spawn__boot', 'laz_xl__write'}
mod_reads = set()
for n in tree.body:
    if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        mod_reads |= {x.id for x in ast.walk(n) if isinstance(x, ast.Name)}
live, stack = set(), [e for e in ENTRY | mod_reads if e in defs]
while stack:
    f = stack.pop()
    if f in live:
        continue
    live.add(f)
    stack += [c for c in uses.get(f, ()) if c in defs and c not in live]

# 2. names LIVE code reads
live_names = set()
for f in live:
    live_names |= uses[f]
live_names |= mod_reads

# 3. expand through the data graph
seen, stack = set(), list(live_names)
while stack:
    x = stack.pop()
    if x in seen:
        continue
    seen.add(x)
    stack += [y for y in rhs.get(x, ()) if y not in seen]

S = json.load(open('/tmp/laz_strategies.json'))
rows = S['lists']
wired = [r for r in rows if r['name'] in seen]
orph = [r for r in rows if r['name'] not in seen]
print(f'{path}')
print(f'  live functions {len(live):,} of {len(defs):,}')
print(f'  strategy lists WIRED   : {len(wired):>3}  ({sum(r["n"] for r in wired):>4} strategies)')
print(f'  strategy lists ORPHANED: {len(orph):>3}  ({sum(r["n"] for r in orph):>4} strategies)')
if orph:
    print('\n  ORPHANED — nothing the engine runs can see these:')
    for r in sorted(orph, key=lambda x: -x['n']):
        print(f'    {r["name"]:36} {r["n"]:>4}  L{r["lines"][0]}')
json.dump([r['name'] for r in orph], open('/tmp/laz_orphans.json', 'w'))
