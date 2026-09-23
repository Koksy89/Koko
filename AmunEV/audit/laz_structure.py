#!/usr/bin/env python3.12
"""laz_structure.py — the engine's real structure, read from the AST. Nothing is executed.

Answers the questions a reorganisation has to answer BEFORE it moves a single line:
  * what top-level statements exist, in what order, and how big is each
  * which of them RUN at import (a dict literal calling a function, a decorator,
    an assignment computed from another name) -- these pin the order
  * which names are defined more than once, and which definition currently WINS
  * what the module sections are and how scattered each one's members are
"""
import ast, sys, collections, json

path = sys.argv[1] if len(sys.argv) > 1 else 'AmunEV_Engine_V2.py'
src = open(path, encoding='utf-8').read()
lines = src.split('\n')
tree = ast.parse(src)


def bound_names(node):
    """Top-level names this statement binds."""
    out = []
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        out.append(node.name)
    elif isinstance(node, ast.Assign):
        for t in node.targets:
            for n in ast.walk(t):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    out.append(n.id)
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        out.append(node.target.id)
    elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
        out.append(node.target.id)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for a in node.names:
            out.append((a.asname or a.name).split('.')[0])
    return out


def exec_time_refs(node):
    """Names this statement READS when the module is executed.

    A function BODY is not read at definition time -- only its decorators and its
    default arguments are. That is the whole reason functions can be reordered
    freely and data statements cannot.
    """
    refs = set()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for d in node.decorator_list:
            refs |= {n.id for n in ast.walk(d) if isinstance(n, ast.Name)}
        for d in list(node.args.defaults) + [x for x in node.args.kw_defaults if x]:
            refs |= {n.id for n in ast.walk(d) if isinstance(n, ast.Name)}
        return refs
    if isinstance(node, ast.ClassDef):
        for d in node.decorator_list + node.bases:
            refs |= {n.id for n in ast.walk(d) if isinstance(n, ast.Name)}
        # a class BODY does execute
        for b in node.body:
            refs |= {n.id for n in ast.walk(b) if isinstance(n, ast.Name)}
        return refs
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


stmts = []
for i, n in enumerate(tree.body):
    stmts.append(dict(
        i=i, lineno=n.lineno, end=n.end_lineno, n_lines=n.end_lineno - n.lineno + 1,
        kind=type(n).__name__, binds=bound_names(n), reads=sorted(exec_time_refs(n)),
        node=n))

# ── module sections, from the file's own markers ────────────────────────────
marks = [(i + 1, l.split(':', 1)[1].strip())
         for i, l in enumerate(lines) if l.startswith('# LAZ_BRAIN MODULE:')]
def section_of(lineno):
    cur = '(preamble)'
    for ml, name in marks:
        if ml <= lineno:
            cur = name
        else:
            break
    return cur
for s in stmts:
    s['section'] = section_of(s['lineno'])

# ── duplicates: which definition WINS today ────────────────────────────────
by_name = collections.defaultdict(list)
for s in stmts:
    for b in s['binds']:
        by_name[b].append(s)
dupes = {k: v for k, v in by_name.items() if len(v) > 1
         and not all(x['kind'] in ('Import', 'ImportFrom') for x in v)}

# ── which statements PIN the order ─────────────────────────────────────────
defined_by = {}
for s in stmts:
    for b in s['binds']:
        defined_by.setdefault(b, s['i'])
pins = []
for s in stmts:
    if s['kind'] in ('FunctionDef', 'AsyncFunctionDef') and not s['reads']:
        continue
    need = [r for r in s['reads'] if r in by_name]
    if need:
        pins.append((s, need))

print(f'{path}')
print(f'  {len(lines):,} lines · {len(stmts):,} top-level statements · '
      f'{len(marks)} module sections')
print()
k = collections.Counter(s['kind'] for s in stmts)
print('  top-level statements by kind')
for kk, vv in k.most_common():
    tot = sum(s['n_lines'] for s in stmts if s['kind'] == kk)
    print(f'    {kk:16} {vv:>6}   {tot:>9,} lines')
print()
print(f'  {len(pins):,} statements read a top-level name AT IMPORT TIME '
      f'(these pin the order)')
print(f'  {sum(1 for s in stmts if s["kind"] in ("FunctionDef","AsyncFunctionDef") and not s["reads"]):,} '
      f'function defs read nothing at import (these can move freely)')
print()
print(f'  {len(dupes)} name(s) bound more than once:')
for name, ss in sorted(dupes.items(), key=lambda x: -len(x[1])):
    where = ', '.join(f'L{s["lineno"]}({s["section"]})' for s in ss)
    print(f'    {name:44} {len(ss)}x  {where}')
    print(f'      {"WINS: L%d" % ss[-1]["lineno"]:<12} — the LAST definition executed is the live one')
print()

# ── how scattered is each section? ─────────────────────────────────────────
sec = collections.defaultdict(list)
for s in stmts:
    sec[s['section']].append(s)
print('  section spread (a section whose statements are not contiguous is scattered)')
rows = []
for name, ss in sec.items():
    idx = [s['i'] for s in ss]
    span = max(idx) - min(idx) + 1
    rows.append((name, len(ss), span, span - len(ss), sum(s['n_lines'] for s in ss)))
for name, n, span, gap, nl in sorted(rows, key=lambda r: -r[3])[:18]:
    flag = '  <- interleaved with other sections' if gap else ''
    print(f'    {name:26} {n:>5} stmts  span {span:>5}  foreign-in-span {gap:>5}  '
          f'{nl:>8,} lines{flag}')
json.dump([{k: v for k, v in s.items() if k != 'node'} for s in stmts],
          open('/tmp/laz_stmts.json', 'w'))
print()
print('  full statement table -> /tmp/laz_stmts.json')
