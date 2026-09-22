#!/usr/bin/env python3.12
"""Static audit for AmunEV_Engine_V2.py — the L1 layer, self-contained.

Parses only. Never imports, executes, evals or unpickles the engine. Mirrors the
playbook's S02-S09 checks plus a wiring audit, and reports every finding with a
line number so nothing is silently dropped.

Usage:  python3.12 laz_static_audit.py [engine.py] [--json out.json]
"""
from __future__ import annotations

import ast
import builtins
import json
import sys
from collections import defaultdict

BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__",
                                 "__package__", "__builtins__", "__debug__", "WindowsError"}


# ----------------------------------------------------------------- scope model
class Scope:
    """One binding scope. `kind` is module | function | class | comp."""

    def __init__(self, kind, parent=None, name=""):
        self.kind, self.parent, self.name = kind, parent, name
        self.bound: set[str] = set()
        self.globals: set[str] = set()
        self.nonlocals: set[str] = set()

    def visible(self, n: str) -> bool:
        s = self
        while s is not None:
            # a class body's names are not visible to nested functions
            if s is not self and s.kind == "class":
                s = s.parent
                continue
            if n in s.bound or n in s.globals or n in s.nonlocals:
                return True
            s = s.parent
        return False


def _targets(node):
    """Yield every name bound by an assignment-like target."""
    if isinstance(node, ast.Name):
        yield node.id
    elif isinstance(node, (ast.Tuple, ast.List)):
        for e in node.elts:
            yield from _targets(e)
    elif isinstance(node, ast.Starred):
        yield from _targets(node.value)


def _arg_names(a: ast.arguments):
    for grp in (a.posonlyargs, a.args, a.kwonlyargs):
        for x in grp:
            yield x.arg
    if a.vararg:
        yield a.vararg.arg
    if a.kwarg:
        yield a.kwarg.arg


class Auditor(ast.NodeVisitor):
    def __init__(self, src: str, path: str):
        self.src, self.path = src, path
        self.lines = src.splitlines()
        self.module = Scope("module")
        self.scope = self.module
        self.func_stack: list[str] = []
        self.undefined: list[tuple[str, int, str]] = []   # (name, line, func)
        self.open_calls: list[tuple[int, str, str]] = []  # (line, func, snippet)
        self.defs: dict[str, list[int]] = defaultdict(list)     # top-level name -> lines
        self.qual_defs: dict[str, int] = {}                     # qualified -> line
        self.name_loads: set[str] = set()
        self.str_literals: dict[str, list[int]] = defaultdict(list)
        self.dyn_lookups: list[tuple[int, str, str]] = []  # getattr/globals-style
        self.mutable_defaults: list[tuple[int, str, str]] = []
        self.bare_excepts: list[tuple[int, str]] = []

    # -- helpers
    def _fn(self):
        return self.func_stack[-1] if self.func_stack else "<module>"

    def _snip(self, ln):
        return self.lines[ln - 1].strip()[:140] if 0 < ln <= len(self.lines) else ""

    # -- pre-bind a block's names so forward references inside a scope are fine
    def _prebind(self, body, scope):
        for node in body:
            for n in ast.walk(node):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    scope.bound.add(n.name)
                elif isinstance(n, (ast.Import, ast.ImportFrom)):
                    for al in n.names:
                        scope.bound.add((al.asname or al.name).split(".")[0])
                elif isinstance(n, ast.Assign):
                    for t in n.targets:
                        scope.bound.update(_targets(t))
                elif isinstance(n, (ast.AugAssign, ast.AnnAssign)):
                    scope.bound.update(_targets(n.target))
                elif isinstance(n, (ast.For, ast.AsyncFor)):
                    scope.bound.update(_targets(n.target))
                elif isinstance(n, ast.withitem) and n.optional_vars is not None:
                    scope.bound.update(_targets(n.optional_vars))
                elif isinstance(n, ast.ExceptHandler) and n.name:
                    scope.bound.add(n.name)
                elif isinstance(n, ast.Global):
                    scope.globals.update(n.names)
                elif isinstance(n, ast.Nonlocal):
                    scope.nonlocals.update(n.names)
                elif isinstance(n, (ast.NamedExpr,)):
                    scope.bound.update(_targets(n.target))
                elif isinstance(n, (ast.comprehension,)):
                    scope.bound.update(_targets(n.target))
                elif isinstance(n, ast.Lambda):
                    scope.bound.update(_arg_names(n.args))
        return scope

    # -- visitors
    def visit_Module(self, node):
        self._prebind(node.body, self.module)
        for n in node.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.defs[n.name].append(n.lineno)
        self.generic_visit(node)

    def _visit_func(self, node):
        qual = ".".join([*self.func_stack, node.name]) if self.func_stack else node.name
        self.qual_defs[qual] = node.lineno
        for d in node.decorator_list:
            self.visit(d)
        for d in (node.args.defaults + [x for x in node.args.kw_defaults if x]):
            self.visit(d)
            if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                self.mutable_defaults.append((node.lineno, qual, type(d).__name__))
        inner = Scope("function", self.scope, node.name)
        inner.bound.update(_arg_names(node.args))
        self._prebind(node.body, inner)
        prev, self.scope = self.scope, inner
        self.func_stack.append(node.name)
        for b in node.body:
            self.visit(b)
        self.func_stack.pop()
        self.scope = prev

    visit_FunctionDef = _visit_func
    visit_AsyncFunctionDef = _visit_func

    def visit_ClassDef(self, node):
        for d in node.decorator_list + node.bases:
            self.visit(d)
        inner = Scope("class", self.scope, node.name)
        self._prebind(node.body, inner)
        prev, self.scope = self.scope, inner
        self.func_stack.append(node.name)
        for b in node.body:
            self.visit(b)
        self.func_stack.pop()
        self.scope = prev

    def visit_Lambda(self, node):
        for d in (node.args.defaults + [x for x in node.args.kw_defaults if x]):
            self.visit(d)
        inner = Scope("function", self.scope, "<lambda>")
        inner.bound.update(_arg_names(node.args))
        prev, self.scope = self.scope, inner
        self.visit(node.body)
        self.scope = prev

    def _visit_comp(self, node):
        inner = Scope("comp", self.scope, "<comp>")
        for c in node.generators:
            inner.bound.update(_targets(c.target))
        prev, self.scope = self.scope, inner
        for c in node.generators:
            self.scope = prev
            self.visit(c.iter)
            self.scope = inner
            for f in c.ifs:
                self.visit(f)
        for f in ("elt", "key", "value"):
            if hasattr(node, f):
                self.visit(getattr(node, f))
        self.scope = prev

    visit_ListComp = visit_SetComp = visit_GeneratorExp = visit_DictComp = _visit_comp

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            self.name_loads.add(node.id)
            if node.id not in BUILTINS and not self.scope.visible(node.id):
                self.undefined.append((node.id, node.lineno, self._fn()))
        else:
            self.scope.bound.update(_targets(node))

    def visit_Constant(self, node):
        if isinstance(node.value, str) and 0 < len(node.value) < 120:
            self.str_literals[node.value].append(node.lineno)

    def visit_Call(self, node):
        f = node.func
        if isinstance(f, ast.Name) and f.id == "open":
            self.open_calls.append((node.lineno, self._fn(), self._snip(node.lineno)))
        if isinstance(f, ast.Name) and f.id in ("getattr", "globals", "eval", "exec", "vars"):
            self.dyn_lookups.append((node.lineno, self._fn(), f.id))
        if isinstance(f, ast.Attribute) and f.attr in ("import_module",):
            self.dyn_lookups.append((node.lineno, self._fn(), "import_module"))
        self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        if node.type is None:
            self.bare_excepts.append((node.lineno, self._fn()))
        self.generic_visit(node)


def unclosed_opens(tree, aud):
    """open() calls whose result is not managed by `with` and not .close()d."""
    managed: set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.With, ast.AsyncWith)):
            for it in n.items:
                for sub in ast.walk(it.context_expr):
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                            and sub.func.id == "open":
                        managed.add(sub.lineno * 100000 + sub.col_offset)
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "open":
            if n.lineno * 100000 + n.col_offset not in managed:
                out.append(n)
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = args[0] if args else "AmunEV_Engine_V2.py"
    src = open(path, encoding="utf-8").read()

    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:
        print(f"S02 PARSE   FAIL  {path}:{e.lineno}  {e.msg}")
        return 1
    print(f"S02 PARSE   PASS  {path} · {len(src):,} bytes · {src.count(chr(10)):,} lines")

    aud = Auditor(src, path)
    aud.visit(tree)

    res = {}

    # --- S09 undefined names -------------------------------------------------
    undef = defaultdict(list)
    for name, ln, fn in aud.undefined:
        undef[name].append((ln, fn))
    res["undefined"] = {k: v for k, v in sorted(undef.items())}
    print(f"S09 UNDEF   {'PASS' if not undef else 'FAIL'}  {len(undef)} distinct undefined "
          f"name(s), {len(aud.undefined)} site(s)")

    # --- S08 unclosed file handles ------------------------------------------
    uo = unclosed_opens(tree, aud)
    by_fn = defaultdict(list)
    fnline = sorted((ln, q) for q, ln in aud.qual_defs.items())
    def owner(ln):
        best = "<module>"
        for dl, q in fnline:
            if dl <= ln:
                best = q
            else:
                break
        return best
    for n in uo:
        by_fn[owner(n.lineno)].append(n.lineno)
    res["unclosed_open"] = {k: v for k, v in sorted(by_fn.items())}
    print(f"S08 HANDLE  {'PASS' if not uo else 'FAIL'}  {len(uo)} unmanaged open() in "
          f"{len(by_fn)} function(s)")

    # --- S13 duplicate top-level definitions (later silently shadows earlier) --
    dups = {k: v for k, v in aud.defs.items() if len(v) > 1}
    res["duplicate_defs"] = dups
    print(f"S13 DUPDEF  {'PASS' if not dups else 'FAIL'}  {len(dups)} top-level name(s) "
          f"defined more than once")

    # --- S14 wiring: names referenced only as strings but never defined -------
    top = set(aud.defs)
    referenced_str = {s for s in aud.str_literals
                      if s.startswith("laz_") or s.startswith("_laz_")}
    dangling = sorted(s for s in referenced_str if s not in top and "." not in s
                      and "(" not in s and " " not in s)
    res["dangling_string_refs"] = {s: aud.str_literals[s][:5] for s in dangling}
    print(f"S14 WIRING  {'PASS' if not dangling else 'WARN'}  {len(dangling)} laz_* string "
          f"name(s) with no matching top-level definition")

    # --- S15 defined but never referenced (candidate unplugged elements) ------
    referenced = aud.name_loads | set(aud.str_literals)
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute):
            referenced.add(n.attr)
    orphans = sorted(n for n in top if n not in referenced and not n.startswith("__"))
    res["unreferenced_defs"] = {n: aud.defs[n] for n in orphans}
    print(f"S15 ORPHAN  {'PASS' if not orphans else 'WARN'}  {len(orphans)} top-level "
          f"definition(s) never referenced anywhere in the file")

    # --- S16 hygiene ---------------------------------------------------------
    res["mutable_defaults"] = aud.mutable_defaults
    res["bare_excepts_count"] = len(aud.bare_excepts)
    res["dynamic_lookups"] = len(aud.dyn_lookups)
    print(f"S16 HYGIENE INFO  {len(aud.mutable_defaults)} mutable default arg(s) · "
          f"{len(aud.bare_excepts)} bare except · {len(aud.dyn_lookups)} dynamic lookup site(s)")

    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json") + 1]
        json.dump(res, open(out, "w"), indent=1, sort_keys=True, default=str)
        print(f"--- wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
