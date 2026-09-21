"""Card 2 -- resolution and the call graph.

All seventeen ``res_*`` cases in ``docs/design/FIXTURES.md`` exist in
``tests/fixtures/mode_b/`` and are graded here against the property the
FIXTURES.md table says each one proves.

They are **not** graded against their ``expected.json``. Those files carry
``"edges": []`` for cases that plainly contain edges -- ``res_import_local``
imports ``json`` and calls ``json.dumps``, ``res_mro`` calls ``super()`` --
while ``res_import_absolute`` does list its import edge. Grading card 2
against an empty expected set would mean asserting the resolver finds nothing.
The divergence is reported to the lead rather than worked around by weakening
a test or by editing card 8's fixtures, which this card does not own.

Several fixtures are the minimum shape of their feature, so each is paired
with a deeper program in ``tmp_path``: multi-level relative imports, a
three-level hierarchy with inherited dispatch, a re-export actually consumed
by a third module. Those are test inputs, not fixtures, and nothing is written
under ``tests/fixtures/``.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

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
        if path.name in {"expected.json", "spans.json"}:
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
    "res_import_relative_deep",
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
    "res_import_star_deep",
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
    "res_import_conditional_deep",
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
        ("IMPORTS", "m", "helpers"),
        ("IMPORTS", "m", "helpers::Thing"),
        ("IMPORTS", "m", "fastpath"),
        ("IMPORTS", "m", "fastpath::boost"),
        ("IMPORTS", "m", "slowpath"),
        ("IMPORTS", "m", "slowpath::boost"),
        ("CALLS", "m::run", "helpers::Thing.go"),
    },
)

RES_IMPORT_LOCAL = _case(
    "res_import_local_deep",
    {
        "lib.py": "def helper():\n    return 1\n",
        "m.py": "def run():\n    from lib import helper\n\n    return helper()\n",
    },
    {
        ("IMPORTS", "m::run", "lib"),
        ("IMPORTS", "m::run", "lib::helper"),
        ("CALLS", "m::run", "lib::helper"),
    },
)

RES_IMPORT_CYCLE = _case(
    "res_import_cycle_deep",
    {
        "a.py": "from b import beta\n\n\ndef alpha():\n    return beta()\n",
        "b.py": "from a import alpha\n\n\ndef beta():\n    return 1\n",
    },
    {
        ("IMPORTS", "a", "b"),
        ("IMPORTS", "a", "b::beta"),
        ("IMPORTS", "b", "a"),
        ("IMPORTS", "b", "a::alpha"),
        ("CALLS", "a::alpha", "b::beta"),
    },
)

RES_REEXPORT = _case(
    "res_reexport_deep",
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
        ("IMPORTS", "app", "pkg"),
        ("IMPORTS", "app", "pkg.impl::Thing"),
        ("INSTANTIATES", "app::main", "pkg.impl::Thing"),
        ("CALLS", "app::main", "pkg.impl::Thing.run"),
    },
)

RES_MRO = _case(
    "res_mro_deep",
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
    "res_decorator_deep",
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
        # after decoration the name `work` is bound to trace's inner function
        ("CALLS", "d::main", "d::trace.<locals>.inner"),
        # and `fn` inside that wrapper is the undecorated `work`
        ("CALLS", "d::trace.<locals>.inner", "d::work"),
        ("REFERENCES", "d::trace", "d::trace.<locals>.inner"),
    },
)

RES_GETATTR_LITERAL = _case(
    "res_getattr_literal_deep",
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
    "res_importlib_deep",
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
    "res_registry_dict_deep",
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
        # a runtime key reaches every member, at HEURISTIC and no higher
        ("CALLS", "r::run_any", "r::alpha"),
        ("CALLS", "r::run_any", "r::beta"),
    },
)

RES_REGISTRY_DECORATOR = _case(
    "res_registry_decorator_deep",
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
        ("REFERENCES", "rd::handler", "rd::handler.<locals>.wrap"),
    },
)

RES_CONFIG_WIRING = _case(
    "res_config_wiring_deep",
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
    "res_config_dangling_deep",
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

    Module names are relative to *root* (``res_import_absolute``, as the
    fixture's ``expected.json`` writes them) while span paths stay relative to
    the repository, which is card 1's convention and is what makes
    ``config_key_id`` land on the path the expectation names.
    """
    rel_root = root.relative_to(REPO_ROOT).as_posix()
    elements = [
        _reroot(e, rel_root)
        for e in _inventory(root)
        if e.span.path.split("/")[0] == prefix
    ]
    resolver = Resolver(
        REPO_ROOT,
        config_paths=[f"{rel_root}/{p}" for p in config_paths],
    )
    edges, unresolved = resolver.resolve(elements)
    return list(edges), list(unresolved), resolver


def _reroot(element: Element, rel_root: str) -> Element:
    """Repository-relative span path, root-relative module name."""
    span = element.span
    return Element(
        id=element.id,
        kind=element.kind,
        name=element.name,
        qualname=element.qualname,
        module=element.module,
        span=SourceSpan(
            path=f"{rel_root}/{span.path}",
            line=span.line,
            end_line=span.end_line,
            col=span.col,
        ),
        provenance=element.provenance,
        content_hash=element.content_hash,
        decorators=element.decorators,
        signature=element.signature,
        docstring=element.docstring,
        parent_id=element.parent_id,
        byte_size=element.byte_size,
    )


def _fixture_run(
    case_id: str, *, config_paths: Sequence[str] = ()
) -> tuple[list[Edge], list[Unresolved], Resolver]:
    assert (MODE_B / case_id).is_dir(), f"fixture {case_id} is missing"
    return _run_subset(MODE_B, case_id, config_paths=config_paths)


def _fixture_modules(case_id: str) -> set[str]:
    return {
        _module_name(p.relative_to(MODE_B).as_posix())
        for p in (MODE_B / case_id).rglob("*.py")
    }


RES_CASES: tuple[str, ...] = tuple(
    sorted(p.name for p in MODE_B.glob("res_*") if p.is_dir())
)


def _expectation(case_id: str) -> dict[str, Any]:
    """Card 8's hand-written expectation for a case. The grading standard."""
    return json.loads((MODE_B / case_id / "expected.json").read_text(encoding="utf-8"))


def _quintuples(edges: Iterable[Edge]) -> set[tuple[str, str, str, str, str]]:
    return {
        (
            str(e.kind),
            e.source_id,
            e.target_id,
            str(e.provenance.method),
            str(e.provenance.confidence),
        )
        for e in edges
    }


def _expected_quintuples(expectation: Mapping[str, Any]) -> set[tuple[str, str, str, str, str]]:
    return {
        (
            e["kind"],
            e["source_id"],
            e["target_id"],
            e["provenance"]["method"],
            e["provenance"]["confidence"],
        )
        for e in expectation.get("edges", [])
    }


def _forbidden(
    expectation: Mapping[str, Any], edges: Iterable[Edge]
) -> list[tuple[str, str, str]]:
    banned = {
        (m["source_id"], m["target_id"]) for m in expectation.get("must_not_contain_edges", [])
    }
    return sorted(
        (str(e.kind), e.source_id, e.target_id)
        for e in edges
        if (e.source_id, e.target_id) in banned
    )


def test_every_fixtures_md_resolution_case_is_graded() -> None:
    """FIXTURES.md names seventeen res_* cases; all seventeen must be on disk."""
    assert len(RES_CASES) == 17, f"res_* fixtures present: {RES_CASES}"


@pytest.mark.parametrize("case_id", RES_CASES)
def test_fixture_expected_edges_are_all_produced(case_id: str) -> None:
    """Every edge the fixture's author wrote down, with its exact provenance."""
    expectation = _expectation(case_id)
    edges, _, _ = _fixture_run(case_id)
    produced = _quintuples(edges)
    missing = sorted(_expected_quintuples(expectation) - produced)
    detail = ""
    if missing:
        by_triple = {q[:3]: q[3:] for q in produced}
        detail = "; ".join(
            f"{m} (produced with {by_triple.get(m[:3], 'no edge at all')})" for m in missing
        )
    assert not missing, f"{case_id}: {detail}"


@pytest.mark.parametrize("case_id", RES_CASES)
def test_fixture_forbidden_edges_are_never_produced(case_id: str) -> None:
    """`must_not_contain_edges` is the over-linking trap. It must stay empty."""
    expectation = _expectation(case_id)
    edges, _, _ = _fixture_run(case_id)
    assert not _forbidden(expectation, edges)


@pytest.mark.parametrize("case_id", RES_CASES)
def test_fixture_exact_edge_counts_hold(case_id: str) -> None:
    expectation = _expectation(case_id)
    if "edge_count_exact" not in expectation:
        pytest.skip("this case does not fix the edge count")
    edges, _, _ = _fixture_run(case_id)
    produced = sorted(_quintuples(edges))
    assert len(edges) == expectation["edge_count_exact"], produced


@pytest.mark.parametrize("case_id", RES_CASES)
def test_fixture_expected_unresolved_records_are_all_produced(case_id: str) -> None:
    """The gaps the fixture's author wrote down, by ID, reason and candidates."""
    expectation = _expectation(case_id)
    _, unresolved, _ = _fixture_run(case_id)
    produced = {u.id: u for u in unresolved}
    for want in expectation.get("unresolved", []):
        got = produced.get(want["id"])
        assert got is not None, (
            f"{case_id}: no unresolved record {want['id']!r}; produced {sorted(produced)}"
        )
        assert str(got.reason) == want["reason"]
        assert set(want.get("attempted", [])) <= {str(m) for m in got.attempted}
        if "candidate_ids" in want:
            assert list(got.candidate_ids) == want["candidate_ids"]
        if "candidate_confidence" in want:
            assert str(got.candidate_confidence) == want["candidate_confidence"]
        assert got.description


@pytest.mark.parametrize("case_id", RES_CASES)
def test_fixture_mro_matches_the_declared_linearisation(case_id: str) -> None:
    expectation = _expectation(case_id)
    if "mro_of_leaf" not in expectation:
        pytest.skip("this case declares no MRO")
    _, _, resolver = _fixture_run(case_id)
    leaf = expectation["mro_of_leaf"][0]
    module, _, qualname = leaf.partition("::")
    order, complete = resolver._mro((module, qualname))
    assert complete
    assert [f"{m}::{q}" for m, q in order] == expectation["mro_of_leaf"]


@pytest.mark.parametrize("case_id", RES_CASES)
def test_fixture_runs_are_byte_identical(case_id: str) -> None:
    first_edges, first_unresolved, _ = _fixture_run(case_id)
    second_edges, second_unresolved, _ = _fixture_run(case_id)
    assert canonical_jsonl(first_edges) == canonical_jsonl(second_edges)
    assert canonical_jsonl(first_unresolved) == canonical_jsonl(second_unresolved)


@pytest.mark.parametrize("case_id", RES_CASES)
def test_fixture_edges_carry_method_and_confidence(case_id: str) -> None:
    edges, unresolved, _ = _fixture_run(case_id)
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
        assert record.description and record.span.path


# --- the properties each case proves, beyond the recorded edge lists --------


def test_fixture_res_import_absolute_never_invents_a_third_party_node() -> None:
    """An import of a module outside the tree gets a record, not a fake node."""
    edges, unresolved, resolver = _fixture_run("res_import_absolute")
    assert not [e for e in edges if e.target_id == "json" or e.target_id.startswith("json::")]
    record = [u for u in unresolved if u.id == "res_import_absolute::json"][0]
    assert record.reason is UnresolvedReason.THIRD_PARTY
    # json.dumps(...) is a resolved third-party call, counted rather than faked
    assert resolver.statistics()["third_party_calls"] >= 1


def test_fixture_res_import_relative_resolves_both_depths() -> None:
    edges, _, _ = _fixture_run("res_import_relative")
    one = _one(edges, EdgeKind.IMPORTS, "res_import_relative", "res_import_relative.sibling")
    two = _one(
        edges,
        EdgeKind.IMPORTS,
        "res_import_relative.pkg.deep",
        "res_import_relative.sibling::helper",
    )
    assert one.provenance.method is two.provenance.method is Method.IMPORT_RELATIVE


def test_fixture_res_import_star_stops_at_dunder_all() -> None:
    """`delta` is defined but not exported, so nothing may bind it here."""
    edges, unresolved, _ = _fixture_run("res_import_star")
    assert not [e for e in edges if e.target_id.endswith("::delta")]
    record = [u for u in unresolved if u.id == "res_import_star::delta"][0]
    assert record.candidate_ids == ("res_import_star.names::delta",)
    assert record.candidate_confidence is Confidence.HEURISTIC


def test_fixture_res_import_conditional_keeps_every_branch() -> None:
    """Both arms of the `if` are imported; neither is picked over the other."""
    edges, _, _ = _fixture_run("res_import_conditional")
    for module in ("fast", "slow"):
        edge = _one(
            edges,
            EdgeKind.IMPORTS,
            "res_import_conditional",
            f"res_import_conditional.{module}::encode",
        )
        assert edge.provenance.confidence is Confidence.RESOLVED
    guarded = _one(
        edges, EdgeKind.IMPORTS, "res_import_conditional", "res_import_conditional.models::Record"
    )
    assert "TYPE_CHECKING" in guarded.provenance.note


def test_fixture_res_import_local_does_not_promote_to_module_level() -> None:
    edges, _, _ = _fixture_run("res_import_local")
    edge = _one(
        edges, EdgeKind.IMPORTS, "res_import_local::encode", "res_import_local.codec::to_text"
    )
    assert edge.call_site is not None and edge.call_site.line == 6
    assert not [e for e in edges if e.source_id == "res_import_local" and e.kind is EdgeKind.IMPORTS]


def test_fixture_res_import_cycle_is_reported_as_a_cycle() -> None:
    """A cycle terminates and is named, rather than raising or hanging."""
    edges, _, _ = _fixture_run("res_import_cycle")
    assert import_cycles(edges) == [("res_import_cycle.a", "res_import_cycle.b")]
    expectation = _expectation("res_import_cycle")
    assert [
        tuple(node["element_ids"]) for node in expectation["order"] if node["kind"] == "CYCLE"
    ] == [("res_import_cycle.a", "res_import_cycle.b")]


def test_fixture_res_reexport_lands_on_the_original_definition() -> None:
    """The consumer imports through the package root and still reaches impl."""
    edges, _, _ = _fixture_run("res_reexport")
    edge = _one(
        edges, EdgeKind.IMPORTS, "res_reexport.consumer", "res_reexport.impl::Helper"
    )
    assert edge.provenance.method is Method.REEXPORT
    assert not [e for e in edges if e.target_id == "res_reexport::Helper"]


def test_fixture_res_mro_reaches_the_nearest_override_only() -> None:
    edges, _, _ = _fixture_run("res_mro")
    assert _one(edges, EdgeKind.CALLS, "res_mro::dispatch", "res_mro::Leaf.classify")
    assert _one(edges, EdgeKind.CALLS, "res_mro::dispatch", "res_mro::Base.shared")
    assert not _find(edges, EdgeKind.CALLS, "res_mro::dispatch", "res_mro::Base.classify")
    super_edge = _one(
        edges, EdgeKind.CALLS, "res_mro::Leaf.classify", "res_mro::Middle.classify"
    )
    assert super_edge.provenance.method is Method.MRO_DISPATCH
    assert super_edge.provenance.confidence is Confidence.PROBABLE


def test_fixture_res_decorator_reaches_wrapper_and_wrapped() -> None:
    edges, _, _ = _fixture_run("res_decorator")
    wrapped = _one(edges, EdgeKind.CALLS, "res_decorator::caller", "res_decorator::compute")
    wrapper = _one(
        edges,
        EdgeKind.CALLS,
        "res_decorator::caller",
        "res_decorator::trace.<locals>.wrapper",
    )
    assert wrapped.provenance.confidence is Confidence.RESOLVED
    assert wrapper.provenance.confidence is Confidence.PROBABLE
    # and `func` inside the wrapper is the decorated function, not a free name
    inner = _one(
        edges,
        EdgeKind.CALLS,
        "res_decorator::trace.<locals>.wrapper",
        "res_decorator::compute",
    )
    assert inner.provenance.method is Method.DECORATOR_UNWRAP


def test_fixture_res_getattr_literal_resolves_only_the_named_method() -> None:
    edges, _, _ = _fixture_run("res_getattr_literal")
    call = _one(
        edges, EdgeKind.CALLS, "res_getattr_literal::run", "res_getattr_literal::Engine.start"
    )
    assert call.provenance.method is Method.GETATTR_LITERAL
    assert call.provenance.confidence is Confidence.PROBABLE
    assert not [e for e in edges if e.target_id.endswith("Engine.stop")]


def test_fixture_res_getattr_computed_claims_nothing() -> None:
    edges, unresolved, _ = _fixture_run("res_getattr_computed")
    method_a = "res_getattr_computed::Helper.method_a"
    method_b = "res_getattr_computed::Helper.method_b"
    assert not [e for e in edges if e.target_id in {method_a, method_b}]
    record = [u for u in unresolved if u.id == "res_getattr_computed::run::getattr@method_name"][0]
    assert record.candidate_ids == (method_a, method_b)
    assert record.candidate_confidence is Confidence.UNKNOWN
    assert record.span.line == 23


def test_fixture_res_importlib_separates_literal_from_computed() -> None:
    edges, unresolved, _ = _fixture_run("res_importlib")
    edge = _one(edges, EdgeKind.IMPORTS, "res_importlib", "res_importlib.plugin")
    assert edge.provenance.method is Method.IMPORTLIB_LITERAL
    assert edge.provenance.confidence is Confidence.PROBABLE
    computed = [u for u in unresolved if u.id == "res_importlib::CHOSEN"][0]
    assert computed.candidate_ids == ("res_importlib.plugin",)
    assert computed.candidate_confidence is Confidence.HEURISTIC


def test_fixture_res_registry_dict_reaches_members_only() -> None:
    edges, _, _ = _fixture_run("res_registry_dict")
    for member in ("handle_buy", "handle_sell"):
        call = _one(
            edges, EdgeKind.CALLS, "res_registry_dict::dispatch", f"res_registry_dict::{member}"
        )
        assert call.provenance.confidence is Confidence.HEURISTIC
    assert not _find(
        edges,
        EdgeKind.CALLS,
        "res_registry_dict::dispatch",
        "res_registry_dict::handle_unregistered",
    )


def test_fixture_res_registry_decorator_registers_only_decorated() -> None:
    edges, _, _ = _fixture_run("res_registry_decorator")
    for member in ("scale_operation", "shift_operation"):
        edge = _one(
            edges,
            EdgeKind.REGISTERS,
            "res_registry_decorator::_REGISTRY",
            f"res_registry_decorator::{member}",
        )
        assert edge.provenance.method is Method.DECORATOR_REGISTRATION
    assert not [
        e
        for e in edges
        if e.target_id == "res_registry_decorator::unregistered_operation"
    ]


def test_fixture_res_config_wiring_points_at_the_key_not_the_file() -> None:
    edges, _, _ = _fixture_run("res_config_wiring")
    key = config_key_id("tests/fixtures/mode_b/res_config_wiring/wiring.json", "/pipeline/class")
    edge = _one(edges, EdgeKind.CONFIGURES, key, "res_config_wiring::ScoreComponent")
    assert edge.provenance.method is Method.CONFIG_STRING_MATCH
    assert edge.provenance.confidence is Confidence.HEURISTIC
    assert edge.call_site is not None and edge.call_site.line == 4
    assert not [e for e in edges if e.target_id.endswith("UnwiredComponent")]


def test_fixture_res_config_wiring_is_stronger_when_the_owner_declares_the_file() -> None:
    """Q3: a declared wiring file lifts a dotted match; a bare name stays weak."""
    declared = "tests/fixtures/mode_b/res_config_wiring/wiring.json"
    edges, _, _ = _fixture_run(
        "res_config_wiring", config_paths=["res_config_wiring/wiring.json"]
    )
    edge = _one(
        edges,
        EdgeKind.CONFIGURES,
        config_key_id(declared, "/pipeline/class"),
        "res_config_wiring::ScoreComponent",
    )
    # still HEURISTIC: a bare class name is a name match however it was found
    assert edge.provenance.confidence is Confidence.HEURISTIC
    assert "owner-declared" in edge.provenance.note


def test_fixture_res_config_dangling_reports_the_key() -> None:
    edges, unresolved, _ = _fixture_run("res_config_dangling")
    assert not [e for e in edges if e.kind is EdgeKind.CONFIGURES]
    key = config_key_id("tests/fixtures/mode_b/res_config_dangling/config.json", "/handler")
    record = [u for u in unresolved if u.id == key][0]
    assert record.reason is UnresolvedReason.MISSING_TARGET
    assert record.span.line == 2 and record.span.col == 13


def test_fixture_res_overlink_trap_links_each_receiver_once() -> None:
    edges, unresolved, _ = _fixture_run("res_overlink_trap")
    caller = "res_overlink_trap::caller"
    for cls in ("ClassA", "ClassB"):
        found = _find(edges, EdgeKind.CALLS, caller, f"res_overlink_trap::{cls}.process")
        assert len(found) == 1
        assert found[0].provenance.confidence is Confidence.PROBABLE
    assert not [u for u in unresolved if u.reason is UnresolvedReason.AMBIGUOUS]


def test_fnd_unknown_fixture_reachable_only_dynamically() -> None:
    """Card 5's precondition: the helper is unresolved, never silently absent."""
    edges, unresolved, _ = _fixture_run("fnd_unknown_not_unplugged")
    helpers = [
        e.target_id
        for e in edges
        if e.kind is EdgeKind.CALLS and "helper" in e.target_id
    ]
    assert not helpers, "a computed getattr must not produce a call edge"
    assert [u for u in unresolved if u.reason is UnresolvedReason.DYNAMIC_NAME]


# ---------------------------------------------------------------------------
# per-case tests for the FIXTURES.md cases with no fixture on disk
# ---------------------------------------------------------------------------


def _case_run(tmp_path: Path, case: Case) -> tuple[list[Edge], list[Unresolved], Resolver]:
    _write(tmp_path, case.files)
    return _run(tmp_path, config_paths=case.config_paths)


def test_deep_res_import_relative(tmp_path: Path) -> None:
    edges, _, _ = _case_run(tmp_path, RES_IMPORT_RELATIVE)
    single = _one(edges, EdgeKind.IMPORTS, "pkg.a", "pkg.b::helper")
    assert single.provenance.method is Method.IMPORT_RELATIVE
    assert single.provenance.confidence is Confidence.RESOLVED
    multi = _one(edges, EdgeKind.IMPORTS, "pkg.sub.deep", "pkg.b::helper")
    assert multi.provenance.method is Method.IMPORT_RELATIVE
    assert _one(edges, EdgeKind.CALLS, "pkg.sub.deep::reach", "pkg.b::helper")


def test_deep_res_import_star(tmp_path: Path) -> None:
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


def test_deep_res_import_conditional(tmp_path: Path) -> None:
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


def test_deep_res_import_local(tmp_path: Path) -> None:
    edges, _, _ = _case_run(tmp_path, RES_IMPORT_LOCAL)
    imports = _one(edges, EdgeKind.IMPORTS, "m::run", "lib::helper")
    assert imports.provenance.method is Method.IMPORT_ABSOLUTE
    assert _one(edges, EdgeKind.CALLS, "m::run", "lib::helper")


def test_deep_res_import_cycle(tmp_path: Path) -> None:
    edges, _, _ = _case_run(tmp_path, RES_IMPORT_CYCLE)
    assert _one(edges, EdgeKind.IMPORTS, "a", "b::beta")
    assert _one(edges, EdgeKind.IMPORTS, "b", "a::alpha")
    assert _one(edges, EdgeKind.CALLS, "a::alpha", "b::beta")
    assert import_cycles(edges) == [("a", "b")]


def test_deep_res_reexport(tmp_path: Path) -> None:
    edges, _, _ = _case_run(tmp_path, RES_REEXPORT)
    edge = _one(edges, EdgeKind.IMPORTS, "app", "pkg.impl::Thing")
    assert edge.provenance.method is Method.REEXPORT
    assert edge.provenance.confidence is Confidence.RESOLVED
    assert _one(edges, EdgeKind.INSTANTIATES, "app::main", "pkg.impl::Thing")
    assert _one(edges, EdgeKind.CALLS, "app::main", "pkg.impl::Thing.run")
    # the re-export resolves to the original definition, not to the package
    assert not _find(edges, EdgeKind.IMPORTS, "app", "pkg::Thing")


def test_deep_res_mro(tmp_path: Path) -> None:
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


def test_deep_res_decorator(tmp_path: Path) -> None:
    edges, _, _ = _case_run(tmp_path, RES_DECORATOR)
    wrapper = _one(edges, EdgeKind.DECORATES, "d::trace", "d::work")
    assert wrapper.provenance.method is Method.DECORATOR_UNWRAP
    wrapped = _one(edges, EdgeKind.CALLS, "d::main", "d::work")
    assert wrapped.provenance.confidence is Confidence.RESOLVED


def test_deep_res_getattr_literal(tmp_path: Path) -> None:
    edges, _, _ = _case_run(tmp_path, RES_GETATTR_LITERAL)
    call = _one(edges, EdgeKind.CALLS, "g::main", "g::Ops.run")
    assert call.provenance.method is Method.GETATTR_LITERAL
    assert call.provenance.confidence is Confidence.PROBABLE


def test_deep_res_importlib(tmp_path: Path) -> None:
    edges, _, _ = _case_run(tmp_path, RES_IMPORTLIB)
    edge = _one(edges, EdgeKind.IMPORTS, "i::load", "plugins.alpha")
    assert edge.provenance.method is Method.IMPORTLIB_LITERAL
    assert edge.provenance.confidence is Confidence.RESOLVED
    assert _one(edges, EdgeKind.CALLS, "i::load", "plugins.alpha::run")


def test_deep_res_importlib_computed_is_unresolved(tmp_path: Path) -> None:
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


def test_deep_res_registry_dict(tmp_path: Path) -> None:
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


def test_deep_res_registry_decorator(tmp_path: Path) -> None:
    edges, _, resolver = _case_run(tmp_path, RES_REGISTRY_DECORATOR)
    for member in ("do_alpha", "do_beta"):
        edge = _one(edges, EdgeKind.REGISTERS, "rd::HANDLERS", f"rd::{member}")
        assert edge.provenance.method is Method.DECORATOR_REGISTRATION
        assert edge.provenance.confidence is Confidence.PROBABLE
        assert _one(edges, EdgeKind.DECORATES, "rd::handler", f"rd::{member}")
    alpha = _one(edges, EdgeKind.REGISTERS, "rd::HANDLERS", "rd::do_alpha")
    assert "'alpha'" in alpha.provenance.note


def test_deep_res_config_wiring(tmp_path: Path) -> None:
    edges, unresolved, _ = _case_run(tmp_path, RES_CONFIG_WIRING)
    key = config_key_id("config/wiring.json", "/components/0/class")
    edge = _one(edges, EdgeKind.CONFIGURES, key, "app.rules::AlphaRule")
    assert edge.provenance.method is Method.CONFIG_STRING_MATCH
    assert edge.provenance.confidence is Confidence.PROBABLE
    assert "/components/0/class" in edge.provenance.note
    assert not unresolved


def test_deep_res_config_wiring_undeclared_is_weaker(tmp_path: Path) -> None:
    _write(tmp_path, RES_CONFIG_WIRING.files)
    edges, _, _ = _run(tmp_path)  # no owner-declared config paths (Q3 blank)
    key = config_key_id("config/wiring.json", "/components/0/class")
    edge = _one(edges, EdgeKind.CONFIGURES, key, "app.rules::AlphaRule")
    assert edge.provenance.confidence is Confidence.HEURISTIC


def test_deep_res_config_dangling(tmp_path: Path) -> None:
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
    # the module-level convenience wrapper is the same call
    plain_edges, plain_unresolved = resolve(elements, tmp_path)
    assert canonical_jsonl(plain_edges) == canonical_jsonl(edges)
    assert canonical_jsonl(plain_unresolved) == canonical_jsonl(unresolved)


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
    """The reported numbers, over every hand-labelled case at once.

    All seventeen ``res_*`` fixtures plus the fourteen deeper programs. The
    denominator is edges between elements of the program under test: external
    and builtin targets are resolved but are not the engine's own wiring, and
    are asserted case by case instead.
    """
    total = Score()
    for case_id in sorted(FIXTURE_EXPECTED):
        edges, _, _ = _fixture_run(case_id)
        emitted_case = _triples(
            e for e in edges if _in_inventory(e, _fixture_modules(case_id))
        )
        expected_case = FIXTURE_EXPECTED[case_id]
        hit = emitted_case & expected_case
        total = total.plus(
            Score(
                len(hit),
                len(emitted_case - expected_case),
                len(expected_case - emitted_case),
            )
        )
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
            f"\ncard 2 corpus: {len(FIXTURE_EXPECTED)} res_* fixtures + "
            f"{len(CASES)} deeper programs, "
            f"{expected} expected edges, {emitted} emitted; "
            f"precision {precision / 10:.1f}%, recall {recall / 10:.1f}%"
        )
    assert precision == 1000, "precision must be exact: a wrong edge is worse than a gap"
    assert recall == 1000
