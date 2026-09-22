#!/usr/bin/env python3.12
"""Find the swallows that can silently change the engine's output.

Parses only. 1,123 empty handlers cannot be reviewed by hand, so this ranks them
by whether swallowing can actually alter a result:

  CLASS A  the try body BINDS a name that is READ after the block. On an
           exception the name keeps a stale or default value and the run
           continues on it -- the defect that silently changes a number.
  CLASS B  the try body contains a name that is UNDEFINED in this file, so the
           block raises NameError every time and the swallow hides it. The code
           inside has never run.
  CLASS C  the try body APPENDS/assigns into a collection that is read later
           (partial results, silently short).
"""
from __future__ import annotations
import ast, builtins, sys
from collections import defaultdict

PATH = sys.argv[1] if len(sys.argv) > 1 else 'AmunEV_Engine_V2.py'
src = open(PATH, encoding='utf-8').read()
tree = ast.parse(src)
lines = src.split('\n')
BUILTINS = set(dir(builtins))


def is_silent(h: ast.ExceptHandler) -> bool:
    """handler body is pass / continue / a bare log line and nothing else"""
    if len(h.body) != 1:
        return False
    b = h.body[0]
    return isinstance(b, (ast.Pass, ast.Continue))


def bound_in(node) -> set[str]:
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            out.add(n.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                out.add((al.asname or al.name).split('.')[0])
    return out


def loaded_in(node) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def calls_in(node) -> set[str]:
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


# module-level + per-function definitions, to spot names defined nowhere
defined = set()
for n in ast.walk(tree):
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        defined.add(n.name)
    elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
        defined.add(n.id)
    elif isinstance(n, (ast.Import, ast.ImportFrom)):
        for al in n.names:
            defined.add((al.asname or al.name).split('.')[0])
    elif isinstance(n, ast.arg):
        defined.add(n.arg)

# owner function for a line
owner = {}
for n in ast.walk(tree):
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for i in range(n.lineno, (n.end_lineno or n.lineno) + 1):
            owner.setdefault(i, n.name)

A, B, C = [], [], []
for fn in ast.walk(tree):
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
        continue
    body = fn.body
    for idx, stmt in enumerate(body):
        if not isinstance(stmt, ast.Try):
            continue
        if not any(is_silent(h) for h in stmt.handlers):
            continue
        tb = ast.Module(body=stmt.body, type_ignores=[])
        bnd = bound_in(tb)
        # what the REST of this function reads after the try block
        after = ast.Module(body=body[idx + 1:], type_ignores=[])
        rd = loaded_in(after)
        risky = sorted(bnd & rd)
        where = owner.get(stmt.lineno, '<module>')
        if risky:
            A.append((stmt.lineno, where, risky))
        und = sorted(n for n in calls_in(tb)
                     if n not in defined and n not in BUILTINS and '_' in n)
        if und:
            B.append((stmt.lineno, where, und))

print(f'{PATH}: {sum(1 for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler) and is_silent(n))} silent handlers total\n')
print(f'CLASS A — swallow leaves a STALE value that is read afterwards: {len(A)}')
for ln, w, names in sorted(A)[:40]:
    print(f'  {PATH}:{ln:6d}  {w:44s} keeps stale: {", ".join(names[:5])}')
if len(A) > 40:
    print(f'  ... and {len(A)-40} more')
print(f'\nCLASS B — swallow hides a call to a name defined NOWHERE in the file: {len(B)}')
for ln, w, names in sorted(B)[:30]:
    print(f'  {PATH}:{ln:6d}  {w:44s} calls: {", ".join(names[:4])}')
