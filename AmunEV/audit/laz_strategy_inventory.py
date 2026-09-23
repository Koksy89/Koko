#!/usr/bin/env python3.12
"""laz_strategy_inventory.py — EVERY strategy defined in the engine, and whether
anything reaches it. Nothing is executed.

A strategy list is a top-level assignment whose value is a list whose elements
carry a `name` (and usually `conditions`) -- written either as a dict literal or
as a call to a builder like _b(...) / _mlq(...). Found by shape, not by naming
convention, so a list nobody remembered naming V67_* is still found.
"""
import ast, collections, json, sys

path = sys.argv[1] if len(sys.argv) > 1 else 'AmunEV_Engine_V2.py'
src = open(path, encoding='utf-8').read()
tree = ast.parse(src)


def entry_name(e):
    """The strategy's own name, from a dict literal or a builder call."""
    if isinstance(e, ast.Dict):
        for k, v in zip(e.keys, e.values):
            if isinstance(k, ast.Constant) and k.value == 'name' and isinstance(v, ast.Constant):
                return str(v.value)
        return None
    if isinstance(e, ast.Call):
        for kw in e.keywords:
            if kw.arg == 'name' and isinstance(kw.value, ast.Constant):
                return str(kw.value.value)
        if e.args and isinstance(e.args[0], ast.Constant) and isinstance(e.args[0].value, str):
            return str(e.args[0].value)
    return None


def entry_conditions(e):
    if isinstance(e, ast.Dict):
        for k, v in zip(e.keys, e.values):
            if isinstance(k, ast.Constant) and k.value == 'conditions':
                try:
                    return tuple(sorted(str(x) for x in ast.literal_eval(v)))
                except Exception:
                    return (ast.unparse(v)[:200],)
    if isinstance(e, ast.Call):
        for kw in e.keywords:
            if kw.arg == 'conditions':
                try:
                    return tuple(sorted(str(x) for x in ast.literal_eval(kw.value)))
                except Exception:
                    return (ast.unparse(kw.value)[:200],)
    return None


lists = {}          # name -> dict(line, n, names, entries)
for n in tree.body:
    if not isinstance(n, ast.Assign) or len(n.targets) != 1:
        continue
    t = n.targets[0]
    if not isinstance(t, ast.Name) or not isinstance(n.value, ast.List) or not n.value.elts:
        continue
    named = [(entry_name(e), entry_conditions(e)) for e in n.value.elts]
    got = [x for x in named if x[0]]
    if len(got) < max(1, len(named) // 2):        # at least half the elements name themselves
        continue
    lists.setdefault(t.id, []).append(dict(line=n.lineno, n=len(n.value.elts), entries=got))

# which lists are reachable from a registry, i.e. named anywhere OTHER than their
# own definition line
refs = collections.defaultdict(set)
for n in ast.walk(tree):
    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in lists:
        refs[n.id].add(getattr(n, 'lineno', 0))

rows = []
for name, defs in sorted(lists.items()):
    total = sum(d['n'] for d in defs)
    own = {d['line'] for d in defs}
    used = sorted(refs[name] - own)
    rows.append(dict(name=name, defs=len(defs), n=total, used_at=used,
                     wired=bool(used), entries=[e for d in defs for e in d['entries']],
                     lines=sorted(own)))

tot = sum(r['n'] for r in rows)
wired = [r for r in rows if r['wired']]
orphan = [r for r in rows if not r['wired']]
print(f'{path}')
print(f'  {len(rows)} strategy list(s) · {tot:,} strategy entries in total')
print(f'  {len(wired)} list(s) referenced somewhere else ({sum(r["n"] for r in wired):,} entries)')
print(f'  {len(orphan)} list(s) NEVER referenced outside their own definition '
      f'({sum(r["n"] for r in orphan):,} entries)')
print()
if orphan:
    print('  ORPHANED — defined and never named again:')
    for r in sorted(orphan, key=lambda x: -x['n']):
        ex = ', '.join(e[0] for e in r['entries'][:3] if e[0])
        print(f'    {r["name"]:36} {r["n"]:>4} entries  L{r["lines"]}  e.g. {ex[:56]}')
    print()

# duplicate strategies, by NAME and by CONDITION SET
byname = collections.defaultdict(list)
bycond = collections.defaultdict(list)
for r in rows:
    for nm, cond in r['entries']:
        if nm:
            byname[nm].append(r['name'])
        if cond:
            bycond[cond].append((r['name'], nm))
dn = {k: v for k, v in byname.items() if len(v) > 1}
dc = {k: v for k, v in bycond.items() if len({x[1] for x in v}) > 1}
print(f'  {len(dn)} strategy NAME(s) appear in more than one list '
      f'({sum(len(v) for v in dn.values())} entries)')
print(f'  {len(dc)} distinct CONDITION SET(s) carry more than one name '
      f'(same bet, different names)')
for k, v in list(sorted(dn.items(), key=lambda x: -len(x[1])))[:8]:
    print(f'    {k:44} in {v}')
json.dump(dict(lists=[{k: v for k, v in r.items() if k != 'entries'} for r in rows],
               dupe_names={k: v for k, v in dn.items()}),
          open('/tmp/laz_strategies.json', 'w'), indent=1)
print('\n  -> /tmp/laz_strategies.json')
