#!/usr/bin/env python3.12
"""laz_order_check.py — would this file survive being executed top to bottom?

Walks the module's top-level statements in order, keeping the set of names bound
so far, and reports any statement that READS a top-level name before it is bound.
Nothing is executed.

THE DISTINCTION THAT MAKES REORDERING POSSIBLE AT ALL: a function BODY is not
read when the function is defined, only its decorators and its argument defaults
are. So function definitions can be moved almost freely, while a data statement
-- a dict literal that calls a function, a list built from another list, a
for-loop that stamps defaults onto a registry -- is pinned to its position.

This is the check that has to pass before and after any move. Run it on the
untouched file first: whatever it already reports is the baseline, and a move is
only safe if the report does not grow.
"""
import ast, sys, builtins, collections

path = sys.argv[1] if len(sys.argv) > 1 else 'AmunEV_Engine_V2.py'
tree = ast.parse(open(path, encoding='utf-8').read())
BUILTIN = set(dir(builtins))


def binds(node):
    out = []
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        out.append(node.name)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        out += [(a.asname or a.name).split('.')[0] for a in node.names]
    else:
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                out.append(n.id)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.append(n.name)
    return out


def exec_reads(node):
    """Names read AT MODULE EXECUTION TIME. Function bodies excluded by design."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        src = list(node.decorator_list) + list(node.args.defaults) \
            + [d for d in node.args.kw_defaults if d]
        return {n.id for d in src for n in ast.walk(d) if isinstance(n, ast.Name)}
    if isinstance(node, ast.ClassDef):
        # A class body executes -- but its METHOD BODIES do not. Walking them made
        # every local inside every method look like a forward reference (1,785 of
        # them), which would have buried the handful of real ones.
        body = [b for b in node.body
                if not isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))]
        meth = [d for b in node.body if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))
                for d in list(b.decorator_list) + list(b.args.defaults)
                + [x for x in b.args.kw_defaults if x]]
        src = list(node.decorator_list) + list(node.bases) + body + meth
        names = {n.id for d in src for n in ast.walk(d)
                 if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        return names - {b.name for b in node.body
                        if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))}
    # a comprehension's own loop variables are local to it
    local = set()
    for n in ast.walk(node):
        if isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for g in n.generators:
                for t in ast.walk(g.target):
                    if isinstance(t, ast.Name):
                        local.add(t.id)
        if isinstance(n, (ast.Lambda,)):
            # A LAMBDA BODY IS NOT EXECUTED WHERE IT IS WRITTEN. laz_genome__BUILDERS
            # is a dict of lambdas referring to helpers defined further down the file;
            # that is late binding and it is correct. Counting those bodies reported
            # four defects that are not defects and would have sent a reorganisation
            # chasing them.
            local |= {a.arg for a in n.args.args} | {a.arg for a in n.args.kwonlyargs}
            if n.args.vararg: local.add(n.args.vararg.arg)
            if n.args.kwarg: local.add(n.args.kwarg.arg)
            for b in ast.walk(n.body):
                if isinstance(b, ast.Name):
                    local.add(b.id)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            local.add(n.name)
            local |= {a.arg for a in n.args.args} | {a.arg for a in n.args.kwonlyargs}
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in local:
            out.add(n.id)
    return out


bound, problems, guarded = set(), [], 0
for n in tree.body:
    # a statement's OWN bindings are not forward references: a for-loop body
    # assigning `c1_comp` and then reading it is ordinary, and a class body's
    # attributes live in the class namespace, not the module's.
    need = exec_reads(n) - BUILTIN - bound - set(binds(n))
    if need:
        # `X if 'X' in globals() else Y` is a deliberate guard, not a defect
        txt = ast.unparse(n)
        for name in sorted(need):
            if f"'{name}' in globals()" in txt or f'"{name}" in globals()' in txt:
                guarded += 1
                continue
            problems.append((n.lineno, name, txt.split('\n')[0][:92]))
    bound |= set(binds(n))

print(f'{path}')
print(f'  {len(tree.body):,} top-level statements')
print(f'  {guarded} read(s) explicitly guarded with "in globals()" — not counted')
print(f'  {len(problems)} statement(s) read a top-level name BEFORE it is bound')
by_name = collections.Counter(p[1] for p in problems)
for ln, name, txt in problems[:40]:
    print(f'    L{ln:<7} {name:34} {txt}')
if len(problems) > 40:
    print(f'    ... and {len(problems)-40} more')
if by_name:
    print()
    print('  most common:')
    for name, c in by_name.most_common(10):
        print(f'    {name:40} {c}')
sys.exit(0)
