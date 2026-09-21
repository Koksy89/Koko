"""Catch tests that cannot fail.

Seven times in this build a test passed while asserting nothing. The shapes
recur:

* ``for x in things: assert ...`` where ``things`` is empty — the loop runs
  zero times and the test passes.
* a function whose only statement is a conditional ``pytest.skip`` that stopped
  firing once its fixture appeared.

Both report PASSED. Neither tests anything. A red test is a finding; a green
test that cannot go red is a lie the suite tells every run, so this scans for
the shapes rather than trusting each author to remember.

The check is deliberately conservative. It only flags a loop whose body is
*entirely* assertions, and only when nothing earlier in the function
constrains the thing being iterated. Anything ambiguous passes — a guard that
cries wolf gets suppressed, and then it guards nothing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

#: Names a test may iterate without a guard: pytest parametrisation and
#: literal collections are constrained by construction.
_SELF_EVIDENT = (ast.List, ast.Tuple, ast.Set, ast.Dict, ast.Constant)


def _test_files() -> list[Path]:
    return sorted(p for p in TESTS_DIR.glob("test_*.py") if p.name != Path(__file__).name)


def _literal_bound_names(scope: ast.AST) -> set[str]:
    """Names assigned a non-empty literal collection anywhere in *scope*.

    A module-level `FORBIDDEN = ("a", "b")` cannot be empty, so iterating it
    needs no guard. Without this the check flags every table-driven test and
    becomes noise.
    """
    bound: set[str] = set()
    for node in ast.walk(scope):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if isinstance(value, (ast.List, ast.Tuple, ast.Set)) and value.elts:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    bound.add(target.id)
        elif isinstance(value, ast.Dict) and value.keys:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    bound.add(target.id)
    return bound


def _iterated_name(node: ast.For) -> str | None:
    """The name of the collection being iterated, if it is a plain name."""
    target = node.iter
    while isinstance(target, ast.Call) and target.args:
        target = target.args[0]  # sorted(x), list(x), enumerate(x)
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Name):
        return target.id
    return None


def _body_is_only_assertions(node: ast.For) -> bool:
    return bool(node.body) and all(isinstance(stmt, ast.Assert) for stmt in node.body)


def _names_constrained_before(func: ast.FunctionDef, loop: ast.For) -> set[str]:
    """Names that an earlier assert in this function already constrains."""
    constrained: set[str] = set()
    for stmt in ast.walk(func):
        if not isinstance(stmt, ast.Assert):
            continue
        if getattr(stmt, "lineno", 0) >= loop.lineno:
            continue
        for sub in ast.walk(stmt.test):
            if isinstance(sub, ast.Name):
                constrained.add(sub.id)
            elif isinstance(sub, ast.Attribute):
                constrained.add(sub.attr)
    return constrained


def _vacuous_loops(tree: ast.Module) -> list[tuple[str, int, str]]:
    findings: list[tuple[str, int, str]] = []
    module_literals = _literal_bound_names(tree)
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not func.name.startswith(("test_", "assert_", "check_")):
            continue
        for loop in ast.walk(func):
            if not isinstance(loop, ast.For) or not _body_is_only_assertions(loop):
                continue
            if isinstance(loop.iter, _SELF_EVIDENT):
                continue
            name = _iterated_name(loop)
            if name is None:
                continue
            if name in module_literals or name in _literal_bound_names(func):
                continue
            if name in _names_constrained_before(func, loop):
                continue
            findings.append((func.name, loop.lineno, name))
    return findings


def _empty_test_bodies(tree: ast.Module) -> list[tuple[str, int]]:
    """Tests whose body is only a conditional skip — green, asserting nothing."""
    findings: list[tuple[str, int]] = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not func.name.startswith("test_"):
            continue
        body = [s for s in func.body if not isinstance(s, ast.Expr)
                or not isinstance(s.value, ast.Constant)]
        if not body:
            findings.append((func.name, func.lineno))
            continue
        if all(isinstance(s, ast.If) for s in body):
            has_assert = any(isinstance(n, ast.Assert) for s in body for n in ast.walk(s))
            if not has_assert:
                findings.append((func.name, func.lineno))
    return findings


@pytest.mark.parametrize("path", _test_files(), ids=lambda p: p.name)
def test_no_unguarded_loop_assertions(path: Path) -> None:
    """A loop over an unconstrained collection passes when it is empty."""
    findings = _vacuous_loops(ast.parse(path.read_text(encoding="utf-8")))
    assert not findings, "\n".join(
        f"{path.name}:{line} in {func}(): asserts inside `for ... in {name}` but "
        f"nothing earlier constrains `{name}`. Add `assert {name}` or an expected "
        f"count before the loop — as written this passes when {name} is empty."
        for func, line, name in findings
    )


@pytest.mark.parametrize("path", _test_files(), ids=lambda p: p.name)
def test_no_test_asserts_nothing(path: Path) -> None:
    """A test whose only statement is a conditional skip reports PASSED."""
    findings = _empty_test_bodies(ast.parse(path.read_text(encoding="utf-8")))
    assert not findings, "\n".join(
        f"{path.name}:{line} in {func}(): the body asserts nothing. If the case "
        f"cannot run, skip unconditionally so it reports SKIPPED; a green empty "
        f"test claims coverage that does not exist."
        for func, line in findings
    )
