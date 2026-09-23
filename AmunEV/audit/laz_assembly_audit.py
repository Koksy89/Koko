#!/usr/bin/env python3.12
"""laz_assembly_audit.py — the defect class: ASSEMBLED TOO EARLY. Nothing is executed.

THE PATTERN. A module-level statement reads a list (or dict) and either copies it
into a registry, or walks it to stamp defaults onto every member:

    V67_REGISTRY = {'efootball': V67_EFB_ALL + V67_CB_ALL, ...}   # L63444
    for _s in V67_ALL_STRATEGIES: _s['evaluator'] = ...           # L63952

and then, FURTHER DOWN THE FILE, the source list keeps growing:

    V67_EFB_ALL = V67_EFB_ALL + V68_EFB_UNDER_LADDER              # L71439
    ... 33 more of these ...

`V67_EFB_ALL + V67_CB_ALL` built a NEW list at line 63444. Rebinding the name
later does not reach into it. So every family added after that line is absent
from the registry, and every strategy added after the for-loop never gets the
key that loop assigns.

The engine's own comment at L63439 documents this happening once before:
    "Assembling earlier silently drops any family defined later in the file:
     V67_WALL_STRATEGIES was absent from V67_EFB_ALL for exactly that reason,
     so all three Wall strategies were registered, documented and parity-tested
     while being INVISIBLE TO THE EVALUATOR. Assemble LAST."

It was fixed at that line and then broken again by everything added below it.
This audit finds every instance, not just that one.
"""
import ast, collections, sys

path = sys.argv[1] if len(sys.argv) > 1 else 'AmunEV_Engine_V2.py'
src = open(path, encoding='utf-8').read()
tree = ast.parse(src)

# every module-level REBINDING of a name, in file order
rebind = collections.defaultdict(list)
for n in tree.body:
    if isinstance(n, ast.Assign):
        for t in n.targets:
            if isinstance(t, ast.Name):
                rebind[t.id].append(n.lineno)

def reads(node):
    return {x.id for x in ast.walk(node) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)}

findings = []
consumers = collections.defaultdict(list)     # name -> [(line, kind, text)]
for n in tree.body:
    if isinstance(n, ast.Assign):
        kind, at = 'assembles', n.lineno
        consumed = reads(n) - {t.id for t in n.targets if isinstance(t, ast.Name)}
    elif isinstance(n, ast.For):
        kind, at = 'iterates and mutates', n.lineno
        consumed = reads(n.iter)
    else:
        continue
    for name in sorted(consumed):
        if name in rebind:
            consumers[name].append((at, kind, ast.unparse(n).split('\n')[0][:88]))

# THE INVARIANT: for a name that is rebound at module level, the LAST statement that
# consumes it must come AFTER the LAST statement that rebinds it. Anything else means
# a consumer took a snapshot and the source kept changing underneath it.
#
# An EARLIER consumer being superseded by a later one is not a defect -- that is what
# a final assembly IS. Only a consumer with no later one is a defect.
for name, cs in sorted(consumers.items()):
    last_bind = max(rebind[name])
    last_use = max(c[0] for c in cs)
    if last_use >= last_bind:
        continue                       # something consumes it after the last change
    stale = [c for c in cs if c[0] < last_bind]
    findings.append(dict(source=name, last_bind=last_bind, consumers=stale,
                         n_after=sum(1 for ln in rebind[name] if ln > last_use)))

print(f'{path}')
if not findings:
    n_ok = sum(1 for name in consumers
               if max(c[0] for c in consumers[name]) >= max(rebind[name]))
    print(f'  PASS — every one of the {n_ok} module-level list(s) that is consumed is '
          f'consumed AFTER its last change')
    sys.exit(0)
print(f'  FAIL — {len(findings)} name(s) are consumed and then changed again, so the '
      f'consumer holds a stale snapshot\n')
for f in findings:
    print(f'  {f["source"]}  — last changed at L{f["last_bind"]}, '
          f'{f["n_after"]} change(s) after the last consumer')
    for ln, kind, txt in f['consumers']:
        print(f'    L{ln:<7} {kind:22} {txt}')
    print()
sys.exit(1)
