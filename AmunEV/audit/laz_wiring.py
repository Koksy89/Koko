#!/usr/bin/env python3.12
"""Wiring audit: call-graph reachability from the engine's real entry points.

Parses only. Answers "is this element actually wired in, and is it reachable
from a run?" -- the question a broken bulk rename makes urgent.
"""
from __future__ import annotations
import ast, json, sys
from collections import defaultdict, deque

PATH = sys.argv[1] if len(sys.argv) > 1 else "AmunEV_Engine_V2.py"
src = open(PATH, encoding="utf-8").read()
tree = ast.parse(src)

# ---- top-level definitions and the lines they span -------------------------
defs: dict[str, ast.AST] = {}
span: dict[str, tuple[int, int]] = {}
for n in tree.body:
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        defs[n.name] = n
        span[n.name] = (n.lineno, getattr(n, "end_lineno", n.lineno))

# ---- edges: which top-level names does each definition mention? -------------
edges: dict[str, set[str]] = {}
for name, node in defs.items():
    out = set()
    for s in ast.walk(node):
        if isinstance(s, ast.Name) and isinstance(s.ctx, ast.Load):
            out.add(s.id)
        elif isinstance(s, ast.Attribute):
            out.add(s.attr)
        elif isinstance(s, ast.Constant) and isinstance(s.value, str):
            out.add(s.value)              # dispatch-by-string wiring
    edges[name] = {o for o in out if o in defs and o != name}

# module-level statements are always live
root = set()
for n in tree.body:
    if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        for s in ast.walk(n):
            if isinstance(s, ast.Name) and isinstance(s.ctx, ast.Load) and s.id in defs:
                root.add(s.id)
            elif isinstance(s, ast.Constant) and isinstance(s.value, str) and s.value in defs:
                root.add(s.value)

ENTRIES = [e for e in ("laz_runner__main", "laz_runner__emit", "laz_runner__source",
                       "main", "_entrypoint", "laz_mode3__find", "laz_startup__prepare",
                       "laz_audit__gate", "laz_production__export", "laz_ledger__export",
                       "run_pipeline_sequential", "laz_spawn__boot", "laz_batch__plan")
           if e in defs]
seed = set(ENTRIES) | root
live, q = set(), deque(seed)
while q:
    f = q.popleft()
    if f in live:
        continue
    live.add(f)
    q.extend(edges.get(f, ()))

print(f"entry points seeded : {len(seed)} ({', '.join(ENTRIES)})")
print(f"top-level defs      : {len(defs)}")
print(f"reachable (LIVE)    : {len(live)}")
print(f"unreachable (DEAD)  : {len(defs) - len(live)}")

json.dump({"live": sorted(live), "dead": sorted(set(defs) - live),
           "span": {k: list(v) for k, v in span.items()}},
          open(sys.argv[2] if len(sys.argv) > 2 else "/dev/null", "w"), indent=1)
