"""Card 2 -- resolution and the call graph.

Every ``res_*`` case in ``docs/design/FIXTURES.md`` is graded here. Three of
them exist on disk (``res_import_absolute``, ``res_getattr_computed``,
``res_overlink_trap``) and are used directly; the other fourteen are not in
``tests/fixtures/`` yet, so each is exercised as the smallest program that
still proves the same property, written into ``tmp_path``. Card 2 does not own
``tests/fixtures/`` and does not fabricate cases there.

Card 1 is being built in parallel, so ``_inventory`` below is a deliberately
small stand-in that mints ``Element`` records with ``make_id``. It exists to
feed the ``ResolutionCard`` protocol, not to duplicate card 1: the resolver
uses elements only to map ``(module, qualname)`` to an ID, and mints the
``make_id`` form when an element is absent.

Expectations are hand-written from reading each program, never recorded from
resolver output.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import pytest

from cascade_map.contracts.interfaces import (
    Confidence,
    Edge,
    EdgeKind,
    Element,
    ElementKind,
    Method,
    Provenance,
    SourceSpan,
    Unresolved,
    UnresolvedReason,
    canonical_jsonl,
    config_key_id,
    make_id,
)
from cascade_map.resolve import Resolver, build_graph, import_cycles, resolve

REPO_ROOT = Path(__file__).resolve().parent.parent
MODE_B = REPO_ROOT / "tests" / "fixtures" / "mode_b"
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SENTINEL_MARKER = Path("/tmp/cascade_map_sentinel_marker.txt")


# ---------------------------------------------------------------------------
# card 1 stand-in
# ---------------------------------------------------------------------------

_PROVENANCE = Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN)


def _module_name(rel: str) -> str:
    parts = Path(rel).with_suffix("").parts
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _inventory(root: Path) -> list[Element]:
    """Minimal stand-in for card 1: elements with ``make_id`` identity."""
    elements: list[Element] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if path.name == "expected.json":
            continue  # card 8's expectation files are harness data, not target config
        if path.suffix == ".py":
            try:
                elements.extend(_python_elements(path, rel))
            except (UnicodeDecodeError, SyntaxError, ValueError):
                # card 1 emits DECODE_ERROR / SYNTAX_ERROR records here; the
                # stand-in only needs to keep the module element so the
                # resolver still sees, and reports on, the file.
                elements.append(
                    Element(
                        id=make_id(_module_name(rel)),
                        kind=ElementKind.MODULE,
                        name=path.stem,
                        qualname="",
                        module=_module_name(rel),
                        span=SourceSpan(path=rel, line=1),
                        provenance=_PROVENANCE,
                        content_hash="stub",
                    )
                )
        elif path.suffix in {".json", ".yaml", ".yml", ".ini", ".cfg", ".toml"}:
            elements.append(
                Element(
                    id=make_id(rel),
                    kind=ElementKind.DATA_FILE,
                    name=path.name,
                    qualname="",
                    module="",
                    span=SourceSpan(path=rel, line=1),
                    provenance=_PROVENANCE,
                    content_hash="stub",
                )
            )
    return elements


def _python_elements(path: Path, rel: str) -> list[Element]:
    module = _module_name(rel)
    is_package = path.name == "__init__.py"
    source = path.read_text(encoding="utf-8")
    out = [
        Element(
            id=make_id(module),
            kind=ElementKind.PACKAGE if is_package else ElementKind.MODULE,
            name=module.rsplit(".", 1)[-1],
            qualname="",
            module=module,
            span=SourceSpan(path=rel, line=1),
            provenance=_PROVENANCE,
            content_hash="stub",
        )
    ]
    tree = ast.parse(source, filename=rel)
    counts: dict[str, int] = {}

    def add(kind: ElementKind, name: str, qualname: str, node: ast.AST, parent: str) -> str:
        counts[qualname] = counts.get(qualname, 0) + 1
        element_id = make_id(module, qualname, counts[qualname])
        out.append(
            Element(
                id=element_id,
                kind=kind,
                name=name,
                qualname=qualname,
                module=module,
                span=SourceSpan(path=rel, line=int(getattr(node, "lineno", 1))),
                provenance=_PROVENANCE,
                content_hash="stub",
                parent_id=parent,
            )
        )
        return element_id

    def walk(body: Iterable[ast.stmt], prefix: str, parent: str, in_class: bool) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                qualname = f"{prefix}{node.name}"
                element_id = add(ElementKind.CLASS, node.name, qualname, node, parent)
                walk(node.body, f"{qualname}.", element_id, True)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}{node.name}"
                decorators = {ast.unparse(d).split("(")[0] for d in node.decorator_list}
                if in_class and decorators & {"property", "cached_property"}:
                    kind = ElementKind.PROPERTY
                elif in_class:
                    kind = ElementKind.METHOD
                else:
                    kind = ElementKind.FUNCTION
                element_id = add(kind, node.name, qualname, node, parent)
                walk(node.body, f"{qualname}.", element_id, False)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        add(
                            ElementKind.ASSIGNMENT,
                            target.id,
                            f"{prefix}{target.id}",
                            node,
                            parent,
                        )
            elif isinstance(node, (ast.If, ast.Try)):
                walk(node.body, prefix, parent, in_class)
                walk(node.orelse, prefix, parent, in_class)
                if isinstance(node, ast.Try):
                    for handler in node.handlers:
                        walk(handler.body, prefix, parent, in_class)
                    walk(node.finalbody, prefix, parent, in_class)

    walk(tree.body, "", make_id(module), False)
    return out


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body.lstrip("\n"), encoding="utf-8")
    return root


def _run(
    root: Path, *, config_paths: Sequence[str] = ()
) -> tuple[list[Edge], list[Unresolved], Resolver]:
    elements = _inventory(root)
    resolver = Resolver(root, config_paths=config_paths)
    edges, unresolved = resolver.resolve(elements)
    return list(edges), list(unresolved), resolver


def _triples(edges: Iterable[Edge]) -> set[tuple[str, str, str]]:
    return {(str(e.kind), e.source_id, e.target_id) for e in edges}


def _find(edges: Iterable[Edge], kind: EdgeKind, source: str, target: str) -> list[Edge]:
    return [
        e for e in edges if e.kind is kind and e.source_id == source and e.target_id == target
    ]


def _one(edges: Iterable[Edge], kind: EdgeKind, source: str, target: str) -> Edge:
    found = _find(edges, kind, source, target)
    assert found, f"no {kind} edge {source} -> {target}"
    return found[0]


def _modules_of(root: Path) -> set[str]:
    return {
        _module_name(p.relative_to(root).as_posix())
        for p in root.rglob("*.py")
    }


def _in_inventory(edge: Edge, modules: set[str]) -> bool:
    def owner(node_id: str) -> str:
        return node_id.split("::", 1)[0].split("#", 1)[0]

    if edge.kind is EdgeKind.CONFIGURES:
        return owner(edge.target_id) in modules
    return owner(edge.source_id) in modules and owner(edge.target_id) in modules


# ---------------------------------------------------------------------------
# the graded corpus -- one entry per FIXTURES.md res_* case
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """A program plus the edges a reader of that program says must exist.

    ``expected`` is the complete set of edges between elements of the program
    itself; external targets (stdlib, third party) and builtins are graded
    separately in the per-case tests, because they are not part of the
    engine's own wiring.
    """

    case_id: str
    files: dict[str, str]
    expected: set[tuple[str, str, str]]
    config_paths: tuple[str, ...] = ()


CASES: list[Case] = []


def _case(
    case_id: str,
    files: dict[str, str],
    expected: set[tuple[str, str, str]],
    config_paths: tuple[str, ...] = (),
) -> Case:
    case = Case(case_id, files, expected, config_paths)
    CASES.append(case)
    return case


RES_IMPORT_RELATIVE = _case(
    "res_import_relative",
    {
        "pkg/__init__.py": "",
        "pkg/b.py": "def helper():\n    return 1\n",
        "pkg/a.py": "from .b import helper\n\n\ndef use():\n    return helper()\n",
        "pkg/sub/__init__.py": "",
        "pkg/sub/deep.py": "from ..b import helper\n\n\ndef reach():\n    return helper()\n",
    },
    {
        ("IMPORTS", "pkg.a", "pkg.b::helper"),
        ("CALLS", "pkg.a::use", "pkg.b::helper"),
        ("IMPORTS", "pkg.sub.deep", "pkg.b::helper"),
        ("CALLS", "pkg.sub.deep::reach", "pkg.b::helper"),
    },
)

RES_IMPORT_STAR = _case(
    "res_import_star",
    {
        "pkg/__init__.py": "",
        "pkg/lib.py": (
            '__all__ = ["alpha"]\n\n\ndef alpha():\n    return 1\n\n\ndef beta():\n    return 2\n'
        ),
        "pkg/use.py": (
            "from .lib import *\nfrom os.path import *\n\n\n"
            "def run():\n    return alpha()\n\n\ndef stray():\n    return beta()\n"
        ),
    },
    {
        ("IMPORTS", "pkg.use", "pkg.lib"),
        ("IMPORTS", "pkg.use", "pkg.lib::alpha"),
        ("CALLS", "pkg.use::run", "pkg.lib::alpha"),
    },
)

RES_IMPORT_CONDITIONAL = _case(
    "res_import_conditional",
    {
        "helpers.py": "class Thing:\n    def go(self):\n        return 1\n",
        "fastpath.py": "def boost():\n    return 1\n",
        "slowpath.py": "def boost():\n    return 0\n",
        "m.py": (
            "from typing import TYPE_CHECKING\n\n"
            "if TYPE_CHECKING:\n    from helpers import Thing\n\n"
            "try:\n    from fastpath import boost\n"
            "except ImportError:\n    from slowpath import boost\n\n\n"
            "def run(thing: Thing):\n    return thing.go()\n"
        ),
    },
    {
        ("IMPORTS", "m", "helpers::Thing"),
        ("IMPORTS", "m", "fastpath::boost"),
        ("IMPORTS", "m", "slowpath::boost"),
        ("CALLS", "m::run", "helpers::Thing.go"),
    },
)

RES_IMPORT_LOCAL = _case(
    "res_import_local",
    {
        "lib.py": "def helper():\n    return 1\n",
        "m.py": "def run():\n    from lib import helper\n\n    return helper()\n",
    },
    {
        ("IMPORTS", "m::run", "lib::helper"),
        ("CALLS", "m::run", "lib::helper"),
    },
)

RES_IMPORT_CYCLE = _case(
    "res_import_cycle",
    {
        "a.py": "from b import beta\n\n\ndef alpha():\n    return beta()\n",
        "b.py": "from a import alpha\n\n\ndef beta():\n    return 1\n",
    },
    {
        ("IMPORTS", "a", "b::beta"),
        ("IMPORTS", "b", "a::alpha"),
        ("CALLS", "a::alpha", "b::beta"),
    },
)

RES_REEXPORT = _case(
    "res_reexport",
    {
        "pkg/__init__.py": 'from .impl import Thing\n\n__all__ = ["Thing"]\n',
        "pkg/impl.py": "class Thing:\n    def run(self):\n        return 1\n",
        "app.py": (
            "from pkg import Thing\n\n\n"
            "def main():\n    thing = Thing()\n    return thing.run()\n"
        ),
    },
    {
        ("IMPORTS", "pkg", "pkg.impl::Thing"),
        ("IMPORTS", "app", "pkg.impl::Thing"),
        ("INSTANTIATES", "app::main", "pkg.impl::Thing"),
        ("CALLS", "app::main", "pkg.impl::Thing.run"),
    },
)

RES_MRO = _case(
    "res_mro",
    {
        "h.py": (
            "class Base:\n"
            "    def run(self):\n        return 'base'\n\n"
            "    def helper(self):\n        return 1\n\n\n"
            "class Middle(Base):\n"
            "    def run(self):\n        return super().run() + 'm'\n\n\n"
            "class Leaf(Middle):\n"
            "    def run(self):\n        return super().run() + 'l'\n\n"
            "    def go(self):\n        return self.helper()\n\n\n"
            "def drive():\n    leaf = Leaf()\n    return leaf.run()\n"
        ),
    },
    {
        ("INHERITS", "h::Middle", "h::Base"),
        ("INHERITS", "h::Leaf", "h::Middle"),
        ("CALLS", "h::Middle.run", "h::Base.run"),
        ("CALLS", "h::Leaf.run", "h::Middle.run"),
        ("CALLS", "h::Leaf.go", "h::Base.helper"),
        ("INSTANTIATES", "h::drive", "h::Leaf"),
        ("CALLS", "h::drive", "h::Leaf.run"),
    },
)

RES_DECORATOR = _case(
    "res_decorator",
    {
        "d.py": (
            "def trace(fn):\n"
            "    def inner(*args):\n        return fn(*args)\n\n"
            "    return inner\n\n\n"
            "@trace\ndef work():\n    return 1\n\n\n"
            "def main():\n    return work()\n"
        ),
    },
    {
        ("DECORATES", "d::trace", "d::work"),
        ("CALLS", "d::main", "d::work"),
        ("REFERENCES", "d::trace", "d::trace.inner"),
    },
)

RES_GETATTR_LITERAL = _case(
    "res_getattr_literal",
    {
        "g.py": (
            "class Ops:\n"
            "    def run(self):\n        return 1\n\n\n"
            "ops = Ops()\n\n\n"
            "def main():\n"
            "    fn = getattr(ops, 'run')\n"
            "    return fn()\n"
        ),
    },
    {
        ("INSTANTIATES", "g", "g::Ops"),
        ("REFERENCES", "g::main", "g::Ops.run"),
        ("CALLS", "g::main", "g::Ops.run"),
    },
)

RES_IMPORTLIB = _case(
    "res_importlib",
    {
        "plugins/__init__.py": "",
        "plugins/alpha.py": "def run():\n    return 1\n",
        "i.py": (
            "import importlib\n\n\n"
            "def load():\n"
            "    mod = importlib.import_module('plugins.alpha')\n"
            "    return mod.run()\n"
        ),
    },
    {
        ("IMPORTS", "i::load", "plugins.alpha"),
        ("CALLS", "i::load", "plugins.alpha::run"),
    },
)

RES_REGISTRY_DICT = _case(
    "res_registry_dict",
    {
        "r.py": (
            "def alpha():\n    return 1\n\n\n"
            "def beta():\n    return 2\n\n\n"
            "REGISTRY = {'a': alpha, 'b': beta}\n\n\n"
            "def run_a():\n    return REGISTRY['a']()\n\n\n"
            "def run_any(key):\n    return REGISTRY[key]()\n"
        ),
    },
    {
        ("REGISTERS", "r::REGISTRY", "r::alpha"),
        ("REGISTERS", "r::REGISTRY", "r::beta"),
        ("REFERENCES", "r", "r::alpha"),
        ("REFERENCES", "r", "r::beta"),
        ("CALLS", "r::run_a", "r::alpha"),
    },
)

RES_REGISTRY_DECORATOR = _case(
    "res_registry_decorator",
    {
        "rd.py": (
            "HANDLERS = {}\n\n\n"
            "def handler(name):\n"
            "    def wrap(fn):\n"
            "        HANDLERS[name] = fn\n"
            "        return fn\n\n"
            "    return wrap\n\n\n"
            "@handler('alpha')\ndef do_alpha():\n    return 1\n\n\n"
            "@handler('beta')\ndef do_beta():\n    return 2\n"
        ),
    },
    {
        ("DECORATES", "rd::handler", "rd::do_alpha"),
        ("DECORATES", "rd::handler", "rd::do_beta"),
        ("REGISTERS", "rd::HANDLERS", "rd::do_alpha"),
        ("REGISTERS", "rd::HANDLERS", "rd::do_beta"),
        ("REFERENCES", "rd::handler", "rd::handler.wrap"),
    },
)

RES_CONFIG_WIRING = _case(
    "res_config_wiring",
    {
        "app/__init__.py": "",
        "app/rules.py": "class AlphaRule:\n    def evaluate(self):\n        return 1\n",
        "config/wiring.json": json.dumps(
            {"components": [{"class": "app.rules.AlphaRule"}]}, indent=2
        )
        + "\n",
    },
    {
        (
            "CONFIGURES",
            config_key_id("config/wiring.json", "/components/0/class"),
            "app.rules::AlphaRule",
        ),
    },
    config_paths=("config/wiring.json",),
)

RES_CONFIG_DANGLING = _case(
    "res_config_dangling",
    {
        "app/__init__.py": "",
        "app/rules.py": "class AlphaRule:\n    def evaluate(self):\n        return 1\n",
        "config/wiring.json": json.dumps(
            {"components": [{"class": "app.rules.MissingRule"}]}, indent=2
        )
        + "\n",
    },
    set(),
    config_paths=("config/wiring.json",),
)


# ---------------------------------------------------------------------------
# on-disk fixtures (card 8)
# ---------------------------------------------------------------------------


def _run_subset(
    root: Path, prefix: str, *, config_paths: Sequence[str] = ()
) -> tuple[list[Edge], list[Unresolved], Resolver]:
    """Inventory *root* but hand the resolver only one case's elements.

    Keeps module names as the fixture's ``expected.json`` writes them
    (``res_import_absolute``, not ``""``) without dragging the rest of the
    corpus into the case under test.
    """
    elements = [
        e for e in _inventory(root) if e.span.path.split("/")[0] == prefix
    ]
    resolver = Resolver(root, config_paths=config_paths)
    edges, unresolved = resolver.resolve(elements)
    return list(edges), list(unresolved), resolver


def _fixture_run(case_id: str) -> tuple[list[Edge], list[Unresolved], Resolver]:
    assert (MODE_B / case_id).is_dir(), f"fixture {case_id} is missing"
    return _run_subset(MODE_B, case_id)


def test_res_import_absolute_fixture() -> None:
    """RESOLVED edge, IMPORT_ABSOLUTE -- to a target outside the inventory."""
    edges, unresolved, _ = _fixture_run("res_import_absolute")
    edge = _one(edges, EdgeKind.IMPORTS, "res_import_absolute", "json")
    assert edge.provenance.method is Method.IMPORT_ABSOLUTE
    assert edge.provenance.confidence is Confidence.RESOLVED
    assert edge.call_site is not None and edge.call_site.line == 3
    # json.dumps is named exactly, even though json is not inventoried.
    assert _find(edges, EdgeKind.CALLS, "res_import_absolute", "json::dumps")
    assert not unresolved


def test_res_getattr_computed_fixture() -> None:
    """UNKNOWN with a candidate set -- and emphatically not a guessed edge."""
    edges, unresolved, _ = _fixture_run("res_getattr_computed")
    method_a = "res_getattr_computed::Helper.method_a"
    method_b = "res_getattr_computed::Helper.method_b"

    assert not [e for e in edges if e.target_id in {method_a, method_b}], (
        "a computed getattr must not produce an edge to either candidate"
    )
    dynamic = [u for u in unresolved if u.reason is UnresolvedReason.DYNAMIC_NAME]
    assert len(dynamic) == 1
    record = dynamic[0]
    assert set(record.candidate_ids) == {method_a, method_b}
    assert record.candidate_confidence is Confidence.PROBABLE
    assert Method.GETATTR_LITERAL in record.attempted
    assert record.span.path.endswith("__init__.py")
    assert record.span.line == 16  # the getattr call


def test_res_overlink_trap_fixture() -> None:
    """Two same-named methods on unrelated classes must not be linked."""
    edges, unresolved, _ = _fixture_run("res_overlink_trap")
    caller = "res_overlink_trap::caller"
    a_process = "res_overlink_trap::ClassA.process"
    b_process = "res_overlink_trap::ClassB.process"

    a_edges = _find(edges, EdgeKind.CALLS, caller, a_process)
    b_edges = _find(edges, EdgeKind.CALLS, caller, b_process)
    assert len(a_edges) == 1 and len(b_edges) == 1
    assert a_edges[0].provenance.method is Method.MRO_DISPATCH
    assert a_edges[0].provenance.confidence is Confidence.PROBABLE
    assert a_edges[0].call_site is not None and a_edges[0].call_site.line == 20
    assert b_edges[0].call_site is not None and b_edges[0].call_site.line == 21
    # exactly two CALLS edges out of caller: no cross-linking of the two
    # same-named methods, and no speculative third target.
    assert len([e for e in edges if e.kind is EdgeKind.CALLS and e.source_id == caller]) == 2
    assert not [u for u in unresolved if u.reason is UnresolvedReason.AMBIGUOUS]


def test_fnd_unknown_fixture_reachable_only_dynamically() -> None:
    """Card 5's precondition: the helper is unresolved, never silently absent."""
    edges, unresolved, _ = _fixture_run("fnd_unknown_not_unplugged")
    helper = "fnd_unknown_not_unplugged::Handler.helper"
    assert not [e for e in edges if e.target_id == helper and e.kind is EdgeKind.CALLS]
    records = [u for u in unresolved if helper in u.candidate_ids]
    assert records, "the dynamically reached method must appear as a candidate"
    assert records[0].reason is UnresolvedReason.DYNAMIC_NAME


# ---------------------------------------------------------------------------
# per-case tests for the FIXTURES.md cases with no fixture on disk
# ---------------------------------------------------------------------------


def _case_run(tmp_path: Path, case: Case) -> tuple[list[Edge], list[Unresolved], Resolver]:
    _write(tmp_path, case.files)
    return _run(tmp_path, config_paths=case.config_paths)


def test_res_import_relative(tmp_path: Path) -> None:
    edges, _, _ = _case_run(tmp_path, RES_IMPORT_RELATIVE)
    single = _one(edges, EdgeKind.IMPORTS, "pkg.a", "pkg.b::helper")
    assert single.provenance.method is Method.IMPORT_RELATIVE
    assert single.provenance.confidence is Confidence.RESOLVED
    multi = _one(edges, EdgeKind.IMPORTS, "pkg.sub.deep", "pkg.b::helper")
    assert multi.provenance.method is Method.IMPORT_RELATIVE
    assert _one(edges, EdgeKind.CALLS, "pkg.sub.deep::reach", "pkg.b::helper")


def test_res_import_star(tmp_path: Path) -> None:
    edges, unresolved, _ = _case_run(tmp_path, RES_IMPORT_STAR)
    bound = _one(edges, EdgeKind.IMPORTS, "pkg.use", "pkg.lib::alpha")
    assert bound.provenance.method is Method.IMPORT_STAR
    # beta is not in __all__, so the star import does not bind it.
    assert not _find(edges, EdgeKind.IMPORTS, "pkg.use", "pkg.lib::beta")
    assert not _find(edges, EdgeKind.CALLS, "pkg.use::stray", "pkg.lib::beta")
    # the residue: a star import from outside the inventory is reported.
    residue = [
        u
        for u in unresolved
        if u.reason is UnresolvedReason.THIRD_PARTY and "os.path" in u.description
    ]
    assert residue and Method.IMPORT_STAR in residue[0].attempted
    stray = [u for u in unresolved if u.id.startswith("unresolved:pkg.use::stray")]
    assert stray, "a call to an unbound star-import name must be reported"


def test_res_import_conditional(tmp_path: Path) -> None:
    edges, unresolved, _ = _case_run(tmp_path, RES_IMPORT_CONDITIONAL)
    type_checking = _one(edges, EdgeKind.IMPORTS, "m", "helpers::Thing")
    assert type_checking.provenance.confidence is Confidence.PROBABLE
    assert "TYPE_CHECKING" in type_checking.provenance.note
    for module in ("fastpath", "slowpath"):
        edge = _one(edges, EdgeKind.IMPORTS, "m", f"{module}::boost")
        assert edge.provenance.confidence is Confidence.PROBABLE
        assert "conditional" in edge.provenance.note
    # the annotation still types the parameter, so the method call resolves
    assert _one(edges, EdgeKind.CALLS, "m::run", "helpers::Thing.go")
    # boost() is bound by two branches: a candidate set, never a coin flip
    assert not [e for e in edges if e.kind is EdgeKind.CALLS and "boost" in e.target_id]


def test_res_import_local(tmp_path: Path) -> None:
    edges, _, _ = _case_run(tmp_path, RES_IMPORT_LOCAL)
    imports = _one(edges, EdgeKind.IMPORTS, "m::run", "lib::helper")
    assert imports.provenance.method is Method.IMPORT_ABSOLUTE
    assert _one(edges, EdgeKind.CALLS, "m::run", "lib::helper")


def test_res_import_cycle(tmp_path: Path) -> None:
    edges, _, _ = _case_run(tmp_path, RES_IMPORT_CYCLE)
    assert _one(edges, EdgeKind.IMPORTS, "a", "b::beta")
    assert _one(edges, EdgeKind.IMPORTS, "b", "a::alpha")
    assert _one(edges, EdgeKind.CALLS, "a::alpha", "b::beta")
    assert import_cycles(edges) == [("a", "b")]


def test_res_reexport(tmp_path: Path) -> None:
    edges, _, _ = _case_run(tmp_path, RES_REEXPORT)
    edge = _one(edges, EdgeKind.IMPORTS, "app", "pkg.impl::Thing")
    assert edge.provenance.method is Method.REEXPORT
    assert edge.provenance.confidence is Confidence.RESOLVED
    assert _one(edges, EdgeKind.INSTANTIATES, "app::main", "pkg.impl::Thing")
    assert _one(edges, EdgeKind.CALLS, "app::main", "pkg.impl::Thing.run")
    # the re-export resolves to the original definition, not to the package
    assert not _find(edges, EdgeKind.IMPORTS, "app", "pkg::Thing")


def test_res_mro(tmp_path: Path) -> None:
    edges, _, _ = _case_run(tmp_path, RES_MRO)
    super_edge = _one(edges, EdgeKind.CALLS, "h::Leaf.run", "h::Middle.run")
    assert super_edge.provenance.method is Method.MRO_DISPATCH
    assert super_edge.provenance.confidence is Confidence.RESOLVED
    inherited = _one(edges, EdgeKind.CALLS, "h::Leaf.go", "h::Base.helper")
    assert inherited.provenance.confidence is Confidence.PROBABLE
    assert "inherited" in inherited.provenance.note
    # three-level dispatch lands on the override, not on the base
    assert _one(edges, EdgeKind.CALLS, "h::drive", "h::Leaf.run")
    assert not _find(edges, EdgeKind.CALLS, "h::drive", "h::Base.run")


def test_res_decorator(tmp_path: Path) -> None:
    edges, _, _ = _case_run(tmp_path, RES_DECORATOR)
    wrapper = _one(edges, EdgeKind.DECORATES, "d::trace", "d::work")
    assert wrapper.provenance.method is Method.DECORATOR_UNWRAP
    wrapped = _one(edges, EdgeKind.CALLS, "d::main", "d::work")
    assert wrapped.provenance.confidence is Confidence.RESOLVED


def test_res_getattr_literal(tmp_path: Path) -> None:
    edges, _, _ = _case_run(tmp_path, RES_GETATTR_LITERAL)
    call = _one(edges, EdgeKind.CALLS, "g::main", "g::Ops.run")
    assert call.provenance.method is Method.GETATTR_LITERAL
    assert call.provenance.confidence is Confidence.PROBABLE


def test_res_importlib(tmp_path: Path) -> None:
    edges, _, _ = _case_run(tmp_path, RES_IMPORTLIB)
    edge = _one(edges, EdgeKind.IMPORTS, "i::load", "plugins.alpha")
    assert edge.provenance.method is Method.IMPORTLIB_LITERAL
    assert edge.provenance.confidence is Confidence.RESOLVED
    assert _one(edges, EdgeKind.CALLS, "i::load", "plugins.alpha::run")


def test_res_importlib_computed_is_unresolved(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "plugins/__init__.py": "",
            "plugins/alpha.py": "def run():\n    return 1\n",
            "j.py": (
                "import importlib\n\n\n"
                "def load(name):\n"
                "    return importlib.import_module(name)\n"
            ),
        },
    )
    edges, unresolved, _ = _run(tmp_path)
    assert not [e for e in edges if e.kind is EdgeKind.IMPORTS and e.source_id == "j::load"]
    record = [u for u in unresolved if u.id.startswith("unresolved:j::load")]
    assert record and record[0].reason is UnresolvedReason.DYNAMIC_NAME
    assert Method.IMPORTLIB_LITERAL in record[0].attempted


def test_res_registry_dict(tmp_path: Path) -> None:
    edges, unresolved, _ = _case_run(tmp_path, RES_REGISTRY_DICT)
    for member in ("alpha", "beta"):
        edge = _one(edges, EdgeKind.REGISTERS, "r::REGISTRY", f"r::{member}")
        assert edge.provenance.method is Method.REGISTRY_MEMBERSHIP
        assert edge.provenance.confidence is Confidence.RESOLVED
    literal = _one(edges, EdgeKind.CALLS, "r::run_a", "r::alpha")
    assert literal.provenance.confidence is Confidence.PROBABLE
    assert not _find(edges, EdgeKind.CALLS, "r::run_a", "r::beta")
    # a computed key is a candidate set, not two invented edges
    assert not [e for e in edges if e.source_id == "r::run_any"]
    dynamic = [u for u in unresolved if u.id.startswith("unresolved:r::run_any")]
    assert dynamic and set(dynamic[0].candidate_ids) == {"r::alpha", "r::beta"}
    assert dynamic[0].reason is UnresolvedReason.AMBIGUOUS


def test_res_registry_decorator(tmp_path: Path) -> None:
    edges, _, resolver = _case_run(tmp_path, RES_REGISTRY_DECORATOR)
    for member in ("do_alpha", "do_beta"):
        edge = _one(edges, EdgeKind.REGISTERS, "rd::HANDLERS", f"rd::{member}")
        assert edge.provenance.method is Method.DECORATOR_REGISTRATION
        assert edge.provenance.confidence is Confidence.PROBABLE
        assert _one(edges, EdgeKind.DECORATES, "rd::handler", f"rd::{member}")
    alpha = _one(edges, EdgeKind.REGISTERS, "rd::HANDLERS", "rd::do_alpha")
    assert "'alpha'" in alpha.provenance.note


def test_res_config_wiring(tmp_path: Path) -> None:
    edges, unresolved, _ = _case_run(tmp_path, RES_CONFIG_WIRING)
    key = config_key_id("config/wiring.json", "/components/0/class")
    edge = _one(edges, EdgeKind.CONFIGURES, key, "app.rules::AlphaRule")
    assert edge.provenance.method is Method.CONFIG_STRING_MATCH
    assert edge.provenance.confidence is Confidence.PROBABLE
    assert "/components/0/class" in edge.provenance.note
    assert not unresolved


def test_res_config_wiring_undeclared_is_weaker(tmp_path: Path) -> None:
    _write(tmp_path, RES_CONFIG_WIRING.files)
    edges, _, _ = _run(tmp_path)  # no owner-declared config paths (Q3 blank)
    key = config_key_id("config/wiring.json", "/components/0/class")
    edge = _one(edges, EdgeKind.CONFIGURES, key, "app.rules::AlphaRule")
    assert edge.provenance.confidence is Confidence.HEURISTIC


def test_res_config_dangling(tmp_path: Path) -> None:
    edges, unresolved, _ = _case_run(tmp_path, RES_CONFIG_DANGLING)
    assert not [e for e in edges if e.kind is EdgeKind.CONFIGURES]
    key = config_key_id("config/wiring.json", "/components/0/class")
    records = [u for u in unresolved if u.id.startswith(f"unresolved:{key}")]
    assert records, "a config key naming nothing must be reported"
    assert records[0].reason is UnresolvedReason.MISSING_TARGET
    assert Method.CONFIG_STRING_MATCH in records[0].attempted


# ---------------------------------------------------------------------------
# dynamic wiring beyond the fixture list, from the card definition
# ---------------------------------------------------------------------------


def test_getattr_fstring_over_known_constant(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "f.py": (
                "PREFIX = 'run'\n\n\n"
                "class Ops:\n"
                "    def run_fast(self):\n        return 1\n\n"
                "    def run_slow(self):\n        return 2\n\n\n"
                "ops = Ops()\n\n\n"
                "def go(speed):\n"
                "    return getattr(ops, f'{PREFIX}_{speed}')()\n"
            )
        },
    )
    edges, unresolved, _ = _run(tmp_path)
    assert not [e for e in edges if e.source_id == "f::go" and e.kind is EdgeKind.CALLS]
    record = [u for u in unresolved if u.id.startswith("unresolved:f::go")]
    assert record
    assert set(record[0].candidate_ids) == {"f::Ops.run_fast", "f::Ops.run_slow"}
    assert record[0].candidate_confidence is Confidence.PROBABLE


def test_getattr_traced_constant_resolves(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "t.py": (
                "ACTION = 'run'\n\n\n"
                "class Ops:\n"
                "    def run(self):\n        return 1\n\n"
                "    def stop(self):\n        return 2\n\n\n"
                "ops = Ops()\n\n\n"
                "def go():\n    return getattr(ops, ACTION)()\n"
            )
        },
    )
    edges, _, _ = _run(tmp_path)
    edge = _one(edges, EdgeKind.CALLS, "t::go", "t::Ops.run")
    assert edge.provenance.method is Method.GETATTR_TRACED
    assert edge.provenance.confidence is Confidence.PROBABLE
    assert not _find(edges, EdgeKind.CALLS, "t::go", "t::Ops.stop")


def test_setattr_literal_registers(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "s.py": (
                "class Bag:\n    pass\n\n\n"
                "def handler():\n    return 1\n\n\n"
                "bag = Bag()\n"
                "setattr(bag, 'run', handler)\n"
            )
        },
    )
    edges, _, _ = _run(tmp_path)
    edge = _one(edges, EdgeKind.REGISTERS, "s::Bag", "s::handler")
    assert edge.provenance.method is Method.GETATTR_LITERAL
    assert edge.provenance.confidence is Confidence.PROBABLE


def test_init_subclass_registration(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "k.py": (
                "class Rule:\n"
                "    def __init_subclass__(cls, **kwargs):\n"
                "        super().__init_subclass__(**kwargs)\n\n\n"
                "class AlphaRule(Rule):\n    def evaluate(self):\n        return 1\n\n\n"
                "class BetaRule(Rule):\n    def evaluate(self):\n        return 2\n"
            )
        },
    )
    edges, _, _ = _run(tmp_path)
    for subclass in ("AlphaRule", "BetaRule"):
        edge = _one(edges, EdgeKind.REGISTERS, "k::Rule", f"k::{subclass}")
        assert edge.provenance.method is Method.REGISTRY_MEMBERSHIP
        assert edge.provenance.confidence is Confidence.PROBABLE


def test_pkgutil_discovery_is_unresolved_with_candidates(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "plugins/__init__.py": "",
            "plugins/alpha.py": "def run():\n    return 1\n",
            "plugins/beta.py": "def run():\n    return 2\n",
            "disc.py": (
                "import pkgutil\n\nimport plugins\n\n\n"
                "def discover():\n"
                "    return list(pkgutil.iter_modules(plugins.__path__))\n"
            ),
        },
    )
    edges, unresolved, _ = _run(tmp_path)
    record = [
        u
        for u in unresolved
        if u.id.startswith("unresolved:disc::discover")
        and u.reason is UnresolvedReason.DYNAMIC_NAME
    ]
    assert record
    assert set(record[0].candidate_ids) == {"plugins.alpha", "plugins.beta"}
    assert record[0].candidate_confidence is Confidence.HEURISTIC
    assert not [e for e in edges if e.source_id == "disc::discover" and e.kind is EdgeKind.IMPORTS]


def test_entry_points_discovery_is_unresolved(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "e.py": (
                "from importlib.metadata import entry_points\n\n\n"
                "def load():\n"
                "    return entry_points(group='engine.rules')\n"
            )
        },
    )
    _, unresolved, _ = _run(tmp_path)
    record = [u for u in unresolved if u.id.startswith("unresolved:e::load")]
    assert record
    assert "engine.rules" in record[0].description
    assert record[0].candidate_ids == ()


def test_property_access_calls_the_getter(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "p.py": (
                "class Model:\n"
                "    @property\n"
                "    def score(self):\n        return 1\n\n\n"
                "def read(model: Model):\n    return model.score\n"
            )
        },
    )
    edges, _, _ = _run(tmp_path)
    edge = _one(edges, EdgeKind.CALLS, "p::read", "p::Model.score")
    assert edge.provenance.method is Method.MRO_DISPATCH
    assert "getter" in edge.provenance.note


def test_classmethod_and_staticmethod_dispatch(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "c.py": (
                "class Factory:\n"
                "    @classmethod\n"
                "    def build(cls):\n        return cls.helper()\n\n"
                "    @staticmethod\n"
                "    def helper():\n        return 1\n\n\n"
                "def go():\n    return Factory.build()\n"
            )
        },
    )
    edges, _, _ = _run(tmp_path)
    assert _one(edges, EdgeKind.CALLS, "c::Factory.build", "c::Factory.helper")
    direct = _one(edges, EdgeKind.CALLS, "c::go", "c::Factory.build")
    assert direct.provenance.confidence is Confidence.RESOLVED


def test_subclass_override_is_reported_as_ambiguous(tmp_path: Path) -> None:
    """The edge is PROBABLE and the override is recorded -- neither is dropped."""
    _write(
        tmp_path,
        {
            "o.py": (
                "class Base:\n    def run(self):\n        return 1\n\n\n"
                "class Sub(Base):\n    def run(self):\n        return 2\n\n\n"
                "def drive(thing: Base):\n    return thing.run()\n"
            )
        },
    )
    edges, unresolved, _ = _run(tmp_path)
    edge = _one(edges, EdgeKind.CALLS, "o::drive", "o::Base.run")
    assert edge.provenance.confidence is Confidence.PROBABLE
    record = [u for u in unresolved if u.reason is UnresolvedReason.AMBIGUOUS]
    assert record and set(record[0].candidate_ids) == {"o::Base.run", "o::Sub.run"}


def test_eval_is_reported_never_stitched(tmp_path: Path) -> None:
    _write(tmp_path, {"v.py": "def run(src):\n    return eval(src)\n"})
    edges, unresolved, _ = _run(tmp_path)
    assert not [e for e in edges if e.source_id == "v::run"]
    record = [u for u in unresolved if u.id.startswith("unresolved:v::run")]
    assert record and record[0].reason is UnresolvedReason.DYNAMIC_NAME


def test_module_level_alias(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "al.py": (
                "def original():\n    return 1\n\n\n"
                "alias = original\n\n\n"
                "def go():\n    return alias()\n"
            )
        },
    )
    edges, _, _ = _run(tmp_path)
    assert _one(edges, EdgeKind.REFERENCES, "al::alias", "al::original")
    assert _one(edges, EdgeKind.CALLS, "al::go", "al::original")


def test_unknown_call_carries_candidates_not_an_edge(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "x.py": "class A:\n    def process(self):\n        return 1\n",
            "y.py": "class B:\n    def process(self):\n        return 2\n",
            "z.py": "def go(thing):\n    return thing.process()\n",
        },
    )
    edges, unresolved, _ = _run(tmp_path)
    assert not [e for e in edges if e.source_id == "z::go"]
    record = [u for u in unresolved if u.id.startswith("unresolved:z::go")]
    assert record
    assert set(record[0].candidate_ids) == {"x::A.process", "y::B.process"}
    assert record[0].candidate_confidence is Confidence.HEURISTIC


# ---------------------------------------------------------------------------
# contract conformance, determinism, safety
# ---------------------------------------------------------------------------


def test_resolver_satisfies_the_protocol_shape(tmp_path: Path) -> None:
    _write(tmp_path, {"m.py": "def f():\n    return 1\n"})
    elements = _inventory(tmp_path)
    edges, unresolved = Resolver(tmp_path).resolve(elements)
    assert isinstance(edges, Sequence) and isinstance(unresolved, Sequence)
    assert all(isinstance(e, Edge) for e in edges)
    assert all(isinstance(u, Unresolved) for u in unresolved)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.case_id)
def test_every_edge_carries_method_and_confidence(tmp_path: Path, case: Case) -> None:
    edges, unresolved, _ = _case_run(tmp_path, case)
    for edge in edges:
        assert isinstance(edge.provenance.method, Method)
        assert isinstance(edge.provenance.confidence, Confidence)
        assert edge.provenance.confidence is not Confidence.UNKNOWN, (
            f"{edge.id} claims a target at UNKNOWN confidence; it should be an "
            "Unresolved record instead"
        )
        assert edge.provenance.method is not Method.MODEL_PROPOSED
        assert edge.id and edge.source_id and edge.target_id
    for record in unresolved:
        assert record.span.path
        assert record.description
        if record.candidate_ids:
            assert record.candidate_confidence is not Confidence.UNKNOWN


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.case_id)
def test_two_runs_are_byte_identical(tmp_path: Path, case: Case) -> None:
    _write(tmp_path, case.files)
    first_edges, first_unresolved, _ = _run(tmp_path, config_paths=case.config_paths)
    second_edges, second_unresolved, _ = _run(tmp_path, config_paths=case.config_paths)
    assert canonical_jsonl(first_edges) == canonical_jsonl(second_edges)
    assert canonical_jsonl(first_unresolved) == canonical_jsonl(second_unresolved)


def test_edge_ids_survive_reformatting(tmp_path: Path) -> None:
    """IDs are structural: comments and blank lines must not move an edge."""
    tight = tmp_path / "tight"
    loose = tmp_path / "loose"
    _write(
        tight,
        {"m.py": "def a():\n    return 1\n\n\ndef b():\n    return a()\n"},
    )
    _write(
        loose,
        {
            "m.py": (
                "# a comment that did not exist before\n\n\n"
                "def a():\n"
                "    # explain the constant\n"
                "    return 1\n\n\n\n\n"
                "def b():\n"
                "    return a()\n"
            )
        },
    )
    tight_edges, _, _ = _run(tight)
    loose_edges, _, _ = _run(loose)
    assert {e.id for e in tight_edges} == {e.id for e in loose_edges}


def test_nothing_is_dropped_every_call_site_is_an_edge_or_a_record(tmp_path: Path) -> None:
    source = (
        "import json\n\n\n"
        "def known():\n    return 1\n\n\n"
        "def run(thing, name):\n"
        "    known()\n"
        "    thing.mystery()\n"
        "    json.dumps({})\n"
        "    globals()[name]()\n"
        "    return 0\n"
    )
    _write(tmp_path, {"n.py": source})
    edges, unresolved, _ = _run(tmp_path)
    accounted = len([e for e in edges if e.source_id == "n::run" and e.kind is EdgeKind.CALLS])
    accounted += len([u for u in unresolved if u.id.startswith("unresolved:n::run")])
    # known(), thing.mystery(), json.dumps(), globals(), globals()[name]()
    assert accounted >= 4


def test_builtin_calls_are_counted_even_when_not_emitted(tmp_path: Path) -> None:
    _write(tmp_path, {"b.py": "def run(items):\n    return len(items)\n"})
    edges, _, resolver = _run(tmp_path)
    assert not [e for e in edges if e.target_id.startswith("builtins::")]
    assert resolver.statistics()["builtin_calls"] == 1
    elements = _inventory(tmp_path)
    opt_in = Resolver(tmp_path, include_builtin_calls=True)
    opt_edges, _ = opt_in.resolve(elements)
    assert [e for e in opt_edges if e.target_id == "builtins::len"]


def test_statistics_are_integers_only(tmp_path: Path) -> None:
    _write(tmp_path, RES_MRO.files)
    _, _, resolver = _run(tmp_path)
    stats = resolver.statistics()
    assert stats and all(isinstance(v, int) for v in stats.values())


def test_syntax_error_is_recorded_and_the_run_continues(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "good.py": "def helper():\n    return 1\n",
            "bad.py": "def broken(:\n    pass\n",
            "use.py": "from good import helper\n\n\ndef go():\n    return helper()\n",
        },
    )
    edges, unresolved, _ = _run(tmp_path)
    assert _one(edges, EdgeKind.CALLS, "use::go", "good::helper")
    assert [u for u in unresolved if u.reason is UnresolvedReason.SYNTAX_ERROR]


def test_missing_yaml_adapter_degrades_to_a_record(tmp_path: Path) -> None:
    pytest.importorskip  # noqa: B018 - documents the optional dependency
    _write(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/rules.py": "class AlphaRule:\n    pass\n",
            "config/wiring.yaml": "components:\n  - class: app.rules.AlphaRule\n",
        },
    )
    edges, unresolved, _ = _run(tmp_path, config_paths=("config/wiring.yaml",))
    try:
        import yaml  # noqa: F401
    except ImportError:
        assert [
            u
            for u in unresolved
            if u.reason is UnresolvedReason.THIRD_PARTY and "PyYAML" in u.description
        ]
        assert not [e for e in edges if e.kind is EdgeKind.CONFIGURES]
    else:
        assert [e for e in edges if e.kind is EdgeKind.CONFIGURES]


def test_build_graph_when_networkx_is_available(tmp_path: Path) -> None:
    pytest.importorskip("networkx")
    _write(tmp_path, RES_MRO.files)
    elements = _inventory(tmp_path)
    edges, _ = Resolver(tmp_path).resolve(elements)
    graph = build_graph(elements, edges)
    assert graph.has_edge("h::drive", "h::Leaf")
    assert graph["h::drive"]["h::Leaf"]["kind"] == "INSTANTIATES"


def test_sentinel_is_never_executed() -> None:
    """Constraint 1, empirically: resolving the sentinel must not run it."""
    if SENTINEL_MARKER.exists():
        SENTINEL_MARKER.unlink()
    assert (FIXTURES / "sentinel" / "__init__.py").is_file(), "the sentinel fixture is missing"
    edges, unresolved, _ = _run_subset(FIXTURES, "sentinel")
    assert not SENTINEL_MARKER.exists(), "resolution executed the sentinel module"
    # and it was still analysed, not skipped
    assert edges or unresolved


def test_whole_mode_b_corpus_resolves_without_executing_anything() -> None:
    if SENTINEL_MARKER.exists():
        SENTINEL_MARKER.unlink()
    edges, unresolved, resolver = _run(FIXTURES)
    assert not SENTINEL_MARKER.exists()
    assert resolver.statistics()["modules_parsed"] >= 3
    assert isinstance(edges, list) and isinstance(unresolved, list)


# ---------------------------------------------------------------------------
# precision and recall -- the number card 2 is graded on
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Score:
    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0

    def plus(self, other: "Score") -> "Score":
        return Score(
            self.true_positive + other.true_positive,
            self.false_positive + other.false_positive,
            self.false_negative + other.false_negative,
        )


def _score_case(tmp_path: Path, case: Case) -> tuple[Score, set[tuple[str, str, str]], set[tuple[str, str, str]]]:
    root = tmp_path / case.case_id
    _write(root, case.files)
    edges, _, _ = _run(root, config_paths=case.config_paths)
    modules = _modules_of(root)
    emitted = _triples(e for e in edges if _in_inventory(e, modules))
    expected = case.expected
    true_positive = emitted & expected
    return (
        Score(len(true_positive), len(emitted - expected), len(expected - emitted)),
        emitted - expected,
        expected - emitted,
    )


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.case_id)
def test_case_precision_is_perfect(tmp_path: Path, case: Case) -> None:
    """Precision is the priority: no edge this program does not contain."""
    score, spurious, missing = _score_case(tmp_path, case)
    assert not spurious, f"{case.case_id}: over-linked edges {sorted(spurious)}"
    assert not missing, f"{case.case_id}: missing edges {sorted(missing)}"
    assert score.false_positive == 0


def test_corpus_precision_and_recall(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The reported numbers, over every hand-labelled case at once."""
    total = Score()
    for case in CASES:
        score, _, _ = _score_case(tmp_path, case)
        total = total.plus(score)
    emitted = total.true_positive + total.false_positive
    expected = total.true_positive + total.false_negative
    # integers only: permille, so no float ever reaches an artifact or a log
    precision = (1000 * total.true_positive) // emitted if emitted else 0
    recall = (1000 * total.true_positive) // expected if expected else 0
    with capsys.disabled():
        print(
            f"\ncard 2 corpus: {len(CASES)} labelled cases, "
            f"{expected} expected edges, {emitted} emitted; "
            f"precision {precision / 10:.1f}%, recall {recall / 10:.1f}%"
        )
    assert precision == 1000, "precision must be exact: a wrong edge is worse than a gap"
    assert recall == 1000
