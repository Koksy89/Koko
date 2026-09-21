"""Card 4 -- data and feature lineage, and slicing.

Grading notes, stated plainly because the card is graded on precision and recall:

* All nine `lin_*` cases in `FIXTURES.md` are exercised against the real fixture
  sources in `tests/fixtures/mode_b/`, read as text and parsed, never imported or
  executed.
* `test_precision_and_recall` grades the *complete* expected edge set of every
  case it can: the eight non-barrier fixtures (`FIXTURE_EXPECTED`) plus two
  larger constructed cases (`GRADED`). Precision must be 1 -- a confident wrong
  edge is the failure this card fears -- and recall is asserted at 1 and printed.
* The fixtures are deliberately small, so the rest of the card's surface
  (`**kwargs`, dataframe merge/groupby/apply, config-named features, alias
  mutation, mutation through a parameter, decision sinks) is covered by the
  constructed cases below, with expectations hand-written from reading them.

**Known conflict, reported not papered over.** `mode_b/lin_assign_chain/
expected.json` writes lineage node IDs as `module::function::variable`, while
the binding contract's `make_id(module, qualname)` yields
`module::function.variable` (one `::`, dotted qualname -- as card 1's own
`res_getattr_computed` expectation uses for `Helper.method_a`). This card follows
the contract. `test_fixture_lin_assign_chain_expected_lineage` grades against
card 8's expectation through the explicit translation in `contract_id`, so the
conflict is visible rather than silently resolved. Only the lead can settle it.
"""

from __future__ import annotations

import ast
import json
from fractions import Fraction
from pathlib import Path
from typing import Iterable, Sequence

import pytest

from cascade_map.contracts.interfaces import (
    Confidence,
    Edge,
    EdgeKind,
    Element,
    ElementKind,
    LineageEdge,
    LineageKind,
    Method,
    Provenance,
    SourceSpan,
    canonical_dumps,
    config_key_id,
    feature_id,
    make_id,
)
from cascade_map.lineage import LineageTracer

FIXTURES = Path(__file__).parent / "fixtures"
SENTINEL_MARKER = Path("/tmp/cascade_map_sentinel_marker.txt")

#: Every lineage case in FIXTURES.md.
LINEAGE_CASES = (
    "lin_assign_chain",
    "lin_barrier",
    "lin_closure",
    "lin_container",
    "lin_dataframe",
    "lin_feature_named",
    "lin_params",
    "lin_slice_backward",
    "lin_slice_forward",
)

CERTAIN = Provenance(method=Method.AST_DIRECT, confidence=Confidence.CERTAIN)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def module_element(module: str, path: str) -> Element:
    return Element(
        id=make_id(module),
        kind=ElementKind.MODULE,
        name=module,
        qualname="",
        module=module,
        span=SourceSpan(path=path, line=1),
        provenance=CERTAIN,
        content_hash=f"hash-of-{module}",
    )


def analyze(
    tmp_path: Path,
    sources: dict[str, str],
    *,
    sinks: Sequence[str] = (),
    extra_elements: Sequence[Element] = (),
    edges: Sequence[Edge] = (),
) -> LineageTracer:
    """Run the tracer over sources written under *tmp_path*. Never executes them."""
    elements: list[Element] = []
    for module, text in sorted(sources.items()):
        path = f"{module}.py"
        (tmp_path / path).write_text(text, encoding="utf-8")
        elements.append(module_element(module, path))
    elements.extend(extra_elements)
    tracer = LineageTracer(root=tmp_path, sink_ids=sinks)
    tracer.trace_values(elements, list(edges))
    return tracer


def fixture_tracer(case: str, *, sinks: Sequence[str] = ()) -> LineageTracer:
    """Parse one `mode_b` fixture. Reads the file as text; never imports it."""
    path = f"mode_b/{case}/__init__.py"
    assert (FIXTURES / path).exists(), f"fixture {case} is missing"
    tracer = LineageTracer(root=FIXTURES, sink_ids=sinks)
    tracer.trace_values([module_element(case, path)], [])
    return tracer


def contract_id(fixture_node_id: str) -> str:
    """Translate card 8's `m::f::v` node ID into the contract's `make_id` form."""
    module, _, rest = fixture_node_id.partition("::")
    return make_id(module, rest.replace("::", ".")) if rest else make_id(module)


def triples(edges: Iterable[LineageEdge]) -> set[tuple[str, str, str]]:
    return {(str(edge.kind), edge.source_id, edge.target_id) for edge in edges}


def has(tracer: LineageTracer, kind: LineageKind, source: str, target: str) -> bool:
    return (str(kind), source, target) in triples(tracer.lineage_edges)


def edges_between(tracer: LineageTracer, source: str, target: str) -> list[LineageEdge]:
    return [
        edge
        for edge in tracer.lineage_edges
        if edge.source_id == source and edge.target_id == target
    ]


# ---------------------------------------------------------------------------
# The FIXTURES.md lineage corpus. Complete expected edge sets, hand-written from
# reading each fixture source.
# ---------------------------------------------------------------------------

# Node-ID prefixes, kept short so the expected sets stay readable.
_P = "lin_assign_chain::process_data."
_A = "lin_assign_chain::augmented_example."

FIXTURE_EXPECTED: dict[str, set[tuple[str, str, str]]] = {
    # x = input_value; y = x + 1; z = y * 2; result = z - 1; return result
    # total = 0; total += data; total *= 2; return total
    "lin_assign_chain": {
        ("ASSIGNS", f"{_P}input_value", f"{_P}x"),
        ("ASSIGNS", f"{_P}x", f"{_P}y"),
        ("ASSIGNS", f"{_P}y", f"{_P}z"),
        ("ASSIGNS", f"{_P}z", f"{_P}result"),
        ("RETURNS", f"{_P}result", f"{_P}@return"),
        ("ASSIGNS", f"{_A}total", f"{_A}total#2"),
        ("ASSIGNS", f"{_A}data", f"{_A}total#2"),
        ("ASSIGNS", f"{_A}total#2", f"{_A}total#3"),
        ("RETURNS", f"{_A}total#3", f"{_A}@return"),
    },
    # def process(x, y, **kwargs): z = x + y; return z
    "lin_params": {
        ("ASSIGNS", "lin_params::process.x", "lin_params::process.z"),
        ("ASSIGNS", "lin_params::process.y", "lin_params::process.z"),
        ("RETURNS", "lin_params::process.z", "lin_params::process.@return"),
    },
    # config = {}; config["key"] = "value"; config["other"] = 42; return config
    # Both values are literals, so the two keys have no incoming edge -- but each
    # is its own node and each mutates the container.
    "lin_container": {
        ("MUTATES", "@feature:key", "lin_container::setup.config"),
        ("MUTATES", "@feature:other", "lin_container::setup.config"),
        ("RETURNS", "lin_container::setup.config", "lin_container::setup.@return"),
    },
    # def transform(df): df["new_col"] = df["old_col"] * 2; return df
    "lin_dataframe": {
        ("COLUMN_WRITE", "@feature:old_col", "@feature:new_col"),
        ("MUTATES", "@feature:new_col", "lin_dataframe::transform.df"),
        ("RETURNS", "lin_dataframe::transform.df", "lin_dataframe::transform.@return"),
    },
    # features = {"score": 0.5}; features["score"] = features["score"] * 2
    # One node for the key, whether it is written in a literal or by subscript.
    "lin_feature_named": {
        ("CONTAINER_WRITE", "@feature:score", "lin_feature_named::process.features"),
        ("MUTATES", "@feature:score", "lin_feature_named::process.features"),
        ("RETURNS", "lin_feature_named::process.features", "lin_feature_named::process.@return"),
    },
    # def outer(x): def inner(): return x; return inner
    "lin_closure": {
        ("RETURNS", "lin_closure::outer.x", "lin_closure::outer.inner.@return"),
        ("RETURNS", "lin_closure::outer.inner", "lin_closure::outer.@return"),
    },
    # def compute(a, b): x = a + 1; y = b * 2; z = x + y; return z
    "lin_slice_backward": {
        ("ASSIGNS", "lin_slice_backward::compute.a", "lin_slice_backward::compute.x"),
        ("ASSIGNS", "lin_slice_backward::compute.b", "lin_slice_backward::compute.y"),
        ("ASSIGNS", "lin_slice_backward::compute.x", "lin_slice_backward::compute.z"),
        ("ASSIGNS", "lin_slice_backward::compute.y", "lin_slice_backward::compute.z"),
        ("RETURNS", "lin_slice_backward::compute.z", "lin_slice_backward::compute.@return"),
    },
    # def transform(x): y = x * 2; z = y + 1; return z
    "lin_slice_forward": {
        ("ASSIGNS", "lin_slice_forward::transform.x", "lin_slice_forward::transform.y"),
        ("ASSIGNS", "lin_slice_forward::transform.y", "lin_slice_forward::transform.z"),
        ("RETURNS", "lin_slice_forward::transform.z", "lin_slice_forward::transform.@return"),
    },
}


@pytest.mark.parametrize("case", sorted(FIXTURE_EXPECTED))
def test_fixture_lineage_is_exactly_as_expected(case: str) -> None:
    tracer = fixture_tracer(case)
    assert triples(tracer.lineage_edges) == FIXTURE_EXPECTED[case]


def test_fixture_lin_assign_chain_expected_lineage() -> None:
    """Graded directly against card 8's `expected.json` lineage records.

    Node IDs are translated by `contract_id`: card 8 writes `m::f::v`, the
    contract's `make_id` yields `m::f.v`. See this module's docstring.

    Second divergence, pinned rather than hidden: card 8 gives each edge the line
    where its *source* was bound (6 and 7). This card gives the line where the
    flow happens -- the assignment statement that reads the source (7 and 8) --
    because the source node's own span already records where it was bound, and an
    edge that cannot be located at its own statement is not evidence. Only the
    lead can settle which convention the contract means.
    """
    flow_line = {"lineage_x_y": 7, "lineage_y_z": 8}
    expected = json.loads(
        (FIXTURES / "mode_b" / "lin_assign_chain" / "expected.json").read_text(encoding="utf-8")
    )
    records = expected.get("lineage", [])
    assert records, "lin_assign_chain/expected.json lost its lineage records"
    tracer = fixture_tracer("lin_assign_chain")
    by_pair = {(e.source_id, e.target_id): e for e in tracer.lineage_edges}
    for record in records:
        source = contract_id(record["source_id"])
        target = contract_id(record["target_id"])
        edge = by_pair.get((source, target))
        assert edge is not None, f"missing {record['id']}: {source} -> {target}"
        assert str(edge.kind) == record["kind"]
        assert str(edge.provenance.method) == record["provenance"]["method"]
        assert str(edge.provenance.confidence) == record["provenance"]["confidence"]
        assert edge.span is not None
        assert edge.span.line == flow_line[record["id"]]
        assert record["span"]["line"] == edge.span.line - 1, (
            "the fixture's span convention changed; re-read it before editing this"
        )


def test_fixture_lin_container_keys_are_separate_nodes() -> None:
    tracer = fixture_tracer("lin_container")
    assert feature_id("key") in tracer.feature_ids()
    assert feature_id("other") in tracer.feature_ids()
    backward = tracer.slice(feature_id("key"), "backward")
    assert feature_id("other") not in backward.member_ids


def test_fixture_lin_dataframe_columns_are_first_class() -> None:
    tracer = fixture_tracer("lin_dataframe")
    assert tracer.feature_ids() == (feature_id("new_col"), feature_id("old_col"))
    backward = tracer.slice(feature_id("new_col"), "backward")
    assert feature_id("old_col") in backward.member_ids


def test_fixture_lin_feature_named_is_one_node() -> None:
    tracer = fixture_tracer("lin_feature_named")
    assert tracer.feature_ids() == (feature_id("score"),)


def test_fixture_lin_closure_captures_the_enclosing_value() -> None:
    tracer = fixture_tracer("lin_closure")
    backward = tracer.slice("lin_closure::outer.inner.@return", "backward")
    assert "lin_closure::outer.x" in backward.member_ids


def test_fixture_lin_slice_backward_is_exact() -> None:
    tracer = fixture_tracer("lin_slice_backward")
    backward = tracer.slice("lin_slice_backward::compute.z", "backward")
    assert set(backward.member_ids) == {
        "lin_slice_backward::compute.a",
        "lin_slice_backward::compute.b",
        "lin_slice_backward::compute.x",
        "lin_slice_backward::compute.y",
        "lin_slice_backward::compute.z",
    }
    narrow = tracer.slice("lin_slice_backward::compute.x", "backward")
    assert set(narrow.member_ids) == {
        "lin_slice_backward::compute.a",
        "lin_slice_backward::compute.x",
    }


def test_fixture_lin_slice_forward_reaches_the_sink_and_stops() -> None:
    sink = "lin_slice_forward::transform.@return"
    tracer = fixture_tracer("lin_slice_forward", sinks=[sink])
    forward = tracer.slice("lin_slice_forward::transform.x", "forward")
    assert set(forward.member_ids) == {
        "lin_slice_forward::transform.x",
        "lin_slice_forward::transform.y",
        "lin_slice_forward::transform.z",
        sink,
    }
    assert forward.reaches_sink_ids == (sink,)
    downstream = tracer.slice("lin_slice_forward::transform.z", "forward")
    assert set(downstream.member_ids) == {"lin_slice_forward::transform.z", sink}


def test_every_fixtures_md_lineage_case_is_covered() -> None:
    covered = set(FIXTURE_EXPECTED) | {"lin_barrier"}
    assert covered == set(LINEAGE_CASES)
    for case in LINEAGE_CASES:
        assert (FIXTURES / "mode_b" / case / "__init__.py").exists(), case


# ---------------------------------------------------------------------------
# Supplementary constructed cases: what the fixtures do not reach.
# lin_assign_chain -- unpacking and walrus, which the fixture omits
# ---------------------------------------------------------------------------

CHAIN_SRC = '''\
BASE = 3


def compute(raw):
    x = raw
    y = x * 2
    y += BASE
    a, b = y, x
    c = (d := a + b)
    return c
'''

CHAIN_EXPECTED = {
    ("ASSIGNS", "chain::compute.raw", "chain::compute.x"),
    ("ASSIGNS", "chain::compute.x", "chain::compute.y"),
    ("ASSIGNS", "chain::compute.y", "chain::compute.y#2"),
    ("ASSIGNS", "chain::BASE", "chain::compute.y#2"),
    ("ASSIGNS", "chain::compute.y#2", "chain::compute.a"),
    ("ASSIGNS", "chain::compute.x", "chain::compute.b"),
    ("ASSIGNS", "chain::compute.a", "chain::compute.d"),
    ("ASSIGNS", "chain::compute.b", "chain::compute.d"),
    ("ASSIGNS", "chain::compute.d", "chain::compute.c"),
    ("RETURNS", "chain::compute.c", "chain::compute.@return"),
}


def test_lin_assign_chain(tmp_path: Path) -> None:
    tracer = analyze(tmp_path, {"chain": CHAIN_SRC})
    assert triples(tracer.lineage_edges) == CHAIN_EXPECTED
    assert tracer.barriers == ()


def test_lin_assign_chain_each_binding_is_its_own_node(tmp_path: Path) -> None:
    """`y` before and after `+=` are different nodes, per the contract's #n."""
    tracer = analyze(tmp_path, {"chain": CHAIN_SRC})
    sliced = tracer.slice("chain::compute.y", "backward")
    assert "chain::BASE" not in sliced.member_ids
    sliced_after = tracer.slice("chain::compute.y#2", "backward")
    assert "chain::BASE" in sliced_after.member_ids


# ---------------------------------------------------------------------------
# lin_container -- dict keys and attribute writes, tracked per key
# ---------------------------------------------------------------------------

CONTAINER_SRC = '''\
class Holder:
    def __init__(self, seed):
        self.value = seed

    def bump(self, delta):
        self.value = self.value + delta


def build(alpha, beta):
    bag = {}
    bag["left"] = alpha
    bag["right"] = beta
    holder = Holder(bag["left"])
    holder.bump(beta)
    return holder.value
'''

CONTAINER_EXPECTED = {
    ("ATTRIBUTE_WRITE", "cont::Holder.__init__.seed", "cont::Holder.value"),
    ("ATTRIBUTE_WRITE", "cont::Holder.value", "cont::Holder.value#2"),
    ("ATTRIBUTE_WRITE", "cont::Holder.bump.delta", "cont::Holder.value#2"),
    ("CONTAINER_WRITE", "cont::build.alpha", "@feature:left"),
    ("CONTAINER_WRITE", "cont::build.beta", "@feature:right"),
    ("MUTATES", "@feature:left", "cont::build.bag"),
    ("MUTATES", "@feature:right", "cont::build.bag"),
    ("PARAMETER_BINDING", "@feature:left", "cont::Holder.__init__.seed"),
    ("PARAMETER_BINDING", "cont::Holder.@instance", "cont::Holder.__init__.self"),
    ("ASSIGNS", "cont::Holder.@instance", "cont::build.holder"),
    ("PARAMETER_BINDING", "cont::build.holder", "cont::Holder.bump.self"),
    ("PARAMETER_BINDING", "cont::build.beta", "cont::Holder.bump.delta"),
    ("RETURNS", "cont::Holder.value", "cont::build.@return"),
    ("RETURNS", "cont::Holder.value#2", "cont::build.@return"),
}


def test_lin_container(tmp_path: Path) -> None:
    tracer = analyze(tmp_path, {"cont": CONTAINER_SRC})
    assert triples(tracer.lineage_edges) == CONTAINER_EXPECTED


def test_lin_container_keys_are_not_collapsed(tmp_path: Path) -> None:
    """The point of the card: a key is a node, not part of its container."""
    tracer = analyze(tmp_path, {"cont": CONTAINER_SRC})
    left = tracer.slice(feature_id("left"), "backward")
    assert "cont::build.alpha" in left.member_ids
    assert "cont::build.beta" not in left.member_ids
    assert feature_id("right") not in left.member_ids


def test_lin_container_attribute_write_on_self_and_on_an_object_share_a_node(
    tmp_path: Path,
) -> None:
    tracer = analyze(tmp_path, {"cont": CONTAINER_SRC})
    assert has(
        tracer, LineageKind.ATTRIBUTE_WRITE, "cont::Holder.bump.delta", "cont::Holder.value#2"
    )
    backward = tracer.slice("cont::build.@return", "backward")
    assert "cont::build.beta" in backward.member_ids
    assert "cont::build.alpha" in backward.member_ids


# ---------------------------------------------------------------------------
# lin_params -- parameter binding and return flow, including **kwargs
# ---------------------------------------------------------------------------

PARAMS_SRC = '''\
DEFAULT_GAIN = 2


def scale(value, gain=DEFAULT_GAIN):
    return value * gain


def blend(first, second, *rest, weight=1, **options):
    total = first + second + weight
    for item in rest:
        total = total + item
    return total


def run(raw, extras):
    scaled = scale(raw)
    named = blend(scaled, 4, weight=raw)
    spread = blend(scaled, 4, **{"weight": raw})
    opaque = blend(scaled, 4, **extras)
    return named + spread + opaque
'''


def test_lin_params_positional_keyword_and_default(tmp_path: Path) -> None:
    tracer = analyze(tmp_path, {"p": PARAMS_SRC})
    assert has(tracer, LineageKind.PARAMETER_BINDING, "p::run.raw", "p::scale.value")
    assert has(tracer, LineageKind.PARAMETER_BINDING, "p::DEFAULT_GAIN", "p::scale.gain")
    assert has(tracer, LineageKind.RETURNS, "p::scale.@return", "p::run.scaled")
    assert has(tracer, LineageKind.PARAMETER_BINDING, "p::run.scaled", "p::blend.first")
    assert has(tracer, LineageKind.PARAMETER_BINDING, "p::run.raw", "p::blend.weight")


def test_lin_params_literal_kwargs_are_keyed_exactly(tmp_path: Path) -> None:
    """`**{"weight": raw}` is traceable, so it binds `weight` and nothing else."""
    tracer = analyze(tmp_path, {"p": PARAMS_SRC})
    literal = [
        edge
        for edge in edges_between(tracer, "p::run.raw", "p::blend.weight")
        if "literal key" in edge.provenance.note
    ]
    assert literal, "**{'weight': raw} must bind weight by its literal key"
    assert literal[0].provenance.confidence is Confidence.RESOLVED


def test_lin_params_opaque_kwargs_are_over_approximate_and_say_so(tmp_path: Path) -> None:
    """`**extras` reaches the parameters but cannot name which: HEURISTIC, labelled."""
    tracer = analyze(tmp_path, {"p": PARAMS_SRC})
    spread = [
        edge
        for edge in tracer.lineage_edges
        if edge.source_id == "p::run.extras" and edge.target_id.startswith("p::blend.")
    ]
    assert spread, "**extras must still be followed into the callee"
    # Parameters already bound positionally at this call site cannot be rebound by
    # **extras, so they are correctly absent.
    assert {edge.target_id for edge in spread} == {"p::blend.options", "p::blend.weight"}
    unkeyed = [edge for edge in spread if edge.target_id != "p::blend.options"]
    assert all(edge.provenance.confidence is Confidence.HEURISTIC for edge in unkeyed)
    assert all(edge.provenance.note.startswith("over-approximate: ") for edge in unkeyed)
    assert all(edge.id in tracer.over_approximate_edge_ids() for edge in unkeyed)


def test_lin_params_star_args(tmp_path: Path) -> None:
    tracer = analyze(tmp_path, {"p": PARAMS_SRC})
    assert has(tracer, LineageKind.PARAMETER_BINDING, "p::run.scaled", "p::blend.first")
    loop = tracer.slice("p::blend.@return", "backward")
    assert "p::blend.rest" in loop.member_ids  # the loop-carried value is followed


# ---------------------------------------------------------------------------
# lin_dataframe -- every named column is its own node
# ---------------------------------------------------------------------------

FRAME_SRC = '''\
import pandas as pd


def zscore(series):
    return series - 1


def engineer(raw_df, lookup_df):
    df = pd.DataFrame(raw_df)
    df["spread"] = df["ask"] - df["bid"]
    df = df.assign(mid=df["spread"] / 2)
    joined = df.merge(lookup_df, on="symbol")
    grouped = joined.groupby("symbol")
    df["z"] = df["mid"].apply(zscore)
    renamed = df.rename(columns={"z": "zscore"})
    return grouped, renamed
'''


def test_lin_dataframe_columns_are_nodes(tmp_path: Path) -> None:
    tracer = analyze(tmp_path, {"frames": FRAME_SRC})
    for column in ("ask", "bid", "spread", "mid", "symbol", "z", "zscore"):
        assert feature_id(column) in tracer.feature_ids(), column


def test_lin_dataframe_column_write_kinds(tmp_path: Path) -> None:
    tracer = analyze(tmp_path, {"frames": FRAME_SRC})
    assert has(tracer, LineageKind.COLUMN_WRITE, feature_id("ask"), feature_id("spread"))
    assert has(tracer, LineageKind.COLUMN_WRITE, feature_id("bid"), feature_id("spread"))
    assert has(tracer, LineageKind.COLUMN_WRITE, feature_id("spread"), feature_id("mid"))
    assert has(tracer, LineageKind.COLUMN_WRITE, feature_id("z"), feature_id("zscore"))


def test_lin_dataframe_apply_binds_the_applied_function(tmp_path: Path) -> None:
    tracer = analyze(tmp_path, {"frames": FRAME_SRC})
    assert has(tracer, LineageKind.PARAMETER_BINDING, feature_id("mid"), "frames::zscore.series")
    assert has(tracer, LineageKind.COLUMN_WRITE, "frames::zscore.@return", feature_id("z"))


def test_lin_dataframe_merge_and_groupby_keep_the_key(tmp_path: Path) -> None:
    tracer = analyze(tmp_path, {"frames": FRAME_SRC})
    joined = tracer.slice("frames::engineer.joined", "backward")
    assert feature_id("symbol") in joined.member_ids
    assert "frames::engineer.lookup_df" in joined.member_ids
    grouped = tracer.slice("frames::engineer.grouped", "backward")
    assert feature_id("symbol") in grouped.member_ids


def test_lin_dataframe_one_column_slice_excludes_its_siblings(tmp_path: Path) -> None:
    tracer = analyze(tmp_path, {"frames": FRAME_SRC})
    spread = tracer.slice(feature_id("spread"), "backward")
    assert feature_id("ask") in spread.member_ids
    assert feature_id("symbol") not in spread.member_ids


# ---------------------------------------------------------------------------
# lin_feature_named -- config and code land on one node
# ---------------------------------------------------------------------------

NAMED_SRC = '''\
def engineer(raw):
    features = {}
    features["momentum"] = raw * 2
    return features
'''


def config_elements() -> list[Element]:
    key_id = config_key_id("config/features.json", "/features/0/name")
    return [
        Element(
            id=key_id,
            kind=ElementKind.CONFIG_KEY,
            name="momentum",
            qualname="/features/0/name",
            module="",
            span=SourceSpan(path="config/features.json", line=1),
            provenance=Provenance(
                method=Method.CONFIG_STRING_MATCH, confidence=Confidence.RESOLVED
            ),
            content_hash="config-hash",
        )
    ]


def test_lin_feature_named_config_and_code_are_one_node(tmp_path: Path) -> None:
    tracer = analyze(
        tmp_path, {"named": NAMED_SRC}, extra_elements=config_elements()
    )
    key_id = config_key_id("config/features.json", "/features/0/name")
    assert feature_id("momentum") in tracer.feature_ids()
    assert has(tracer, LineageKind.ASSIGNS, key_id, feature_id("momentum"))
    backward = tracer.slice(feature_id("momentum"), "backward")
    assert key_id in backward.member_ids
    assert "named::engineer.raw" in backward.member_ids


def test_lin_feature_named_config_edge_via_card_two_configures(tmp_path: Path) -> None:
    key_id = config_key_id("config/features.json", "/features/0/name")
    edge = Edge(
        id="e1",
        kind=EdgeKind.CONFIGURES,
        source_id=key_id,
        target_id=feature_id("momentum"),
        provenance=Provenance(
            method=Method.CONFIG_STRING_MATCH,
            confidence=Confidence.RESOLVED,
            span=SourceSpan(path="config/features.json", line=1),
        ),
    )
    tracer = analyze(tmp_path, {"named": NAMED_SRC}, edges=[edge])
    assert has(tracer, LineageKind.ASSIGNS, key_id, feature_id("momentum"))
    hop = [
        e for e in edges_between(tracer, key_id, feature_id("momentum"))
    ][0]
    assert hop.provenance.method is Method.CONFIG_STRING_MATCH


# ---------------------------------------------------------------------------
# lin_closure -- closure capture and mutation through an alias
# ---------------------------------------------------------------------------

CLOSURE_SRC = '''\
def make_accumulator(seed):
    history = []
    alias = history

    def push(value):
        alias.append(value + seed)
        return alias

    return push


def collect(items, start):
    push = make_accumulator(start)
    for item in items:
        push(item)
    return push
'''


def test_lin_closure_captures_the_enclosing_binding(tmp_path: Path) -> None:
    tracer = analyze(tmp_path, {"clo": CLOSURE_SRC})
    backward = tracer.slice("clo::make_accumulator.push.@return", "backward")
    assert "clo::make_accumulator.alias" in backward.member_ids
    assert "clo::make_accumulator.history" in backward.member_ids


def test_lin_closure_mutation_through_an_alias_reaches_the_original(tmp_path: Path) -> None:
    tracer = analyze(tmp_path, {"clo": CLOSURE_SRC})
    assert has(
        tracer, LineageKind.MUTATES, "clo::make_accumulator.push.value",
        "clo::make_accumulator.alias",
    )
    assert has(
        tracer, LineageKind.MUTATES, "clo::make_accumulator.push.value",
        "clo::make_accumulator.history",
    )
    through_alias = edges_between(
        tracer, "clo::make_accumulator.push.value", "clo::make_accumulator.history"
    )[0]
    assert "alias" in through_alias.provenance.note
    assert through_alias.provenance.confidence is Confidence.PROBABLE


def test_lin_closure_free_variable_capture_is_labelled(tmp_path: Path) -> None:
    tracer = analyze(tmp_path, {"clo": CLOSURE_SRC})
    assert has(
        tracer, LineageKind.MUTATES, "clo::make_accumulator.seed",
        "clo::make_accumulator.alias",
    )


MUTATING_PARAM_SRC = '''\
def fill(bucket, value):
    bucket.append(value)


def run(payload):
    store = []
    fill(store, payload)
    return store
'''


def test_mutation_through_a_parameter_reaches_the_caller(tmp_path: Path) -> None:
    tracer = analyze(tmp_path, {"mut": MUTATING_PARAM_SRC})
    assert has(tracer, LineageKind.MUTATES, "mut::fill.bucket", "mut::run.store")
    backward = tracer.slice("mut::run.@return", "backward")
    assert "mut::run.payload" in backward.member_ids


# ---------------------------------------------------------------------------
# lin_barrier -- the fixture that exists, plus reflection and opaque calls
# ---------------------------------------------------------------------------


def test_fixture_lin_barrier(tmp_path: Path) -> None:
    """The one lineage fixture card 8 has written. Read as text, never executed."""
    case = FIXTURES / "mode_b" / "lin_barrier" / "__init__.py"
    assert case.exists(), "fixture mode_b/lin_barrier is missing"
    element = module_element("lin_barrier", "mode_b/lin_barrier/__init__.py")
    tracer = LineageTracer(root=FIXTURES)
    lineage, barriers = tracer.trace_values([element], [])

    assert len(barriers) == 1
    barrier = barriers[0]
    assert barrier.element_id == "lin_barrier::evaluate"
    assert barrier.span.path == "mode_b/lin_barrier/__init__.py"
    assert barrier.span.line == 19
    assert str(barrier.reason) == "DYNAMIC_NAME"
    assert "eval" in barrier.description

    # The expression reaches the barrier, and the result comes out of it ...
    assert has(tracer, LineageKind.READS, "lin_barrier::evaluate.expr", barrier.id)
    assert has(tracer, LineageKind.ASSIGNS, barrier.id, "lin_barrier::evaluate.result")
    # ... but nothing is stitched across it.
    assert not has(
        tracer, LineageKind.ASSIGNS, "lin_barrier::evaluate.expr",
        "lin_barrier::evaluate.result",
    )
    assert not has(
        tracer, LineageKind.ASSIGNS, "lin_barrier::evaluate.x",
        "lin_barrier::evaluate.result",
    )
    backward = tracer.slice("lin_barrier::process.answer", "backward")
    assert backward.barrier_ids == (barrier.id,)
    assert backward.confidence is Confidence.UNKNOWN
    assert "lin_barrier::compute_expression.computed" in backward.member_ids


REFLECTION_SRC = '''\
import requests


def fetch(url):
    payload = requests.get(url)
    return payload


def pick(obj, key, fallback):
    known = getattr(obj, "price", fallback)
    unknown = getattr(obj, key, fallback)
    return known + unknown
'''


def test_opaque_third_party_call_is_a_barrier(tmp_path: Path) -> None:
    tracer = analyze(tmp_path, {"refl": REFLECTION_SRC})
    third_party = [b for b in tracer.barriers if str(b.reason) == "THIRD_PARTY"]
    assert len(third_party) == 1
    assert "requests.get" in third_party[0].description
    assert has(tracer, LineageKind.READS, "refl::fetch.url", third_party[0].id)
    assert has(tracer, LineageKind.ASSIGNS, third_party[0].id, "refl::fetch.payload")


def test_reflection_with_a_literal_name_is_not_a_barrier(tmp_path: Path) -> None:
    tracer = analyze(tmp_path, {"refl": REFLECTION_SRC})
    literal = [
        edge
        for edge in tracer.lineage_edges
        if edge.target_id == "refl::pick.known" and edge.source_id.endswith(".@attr.price")
    ]
    assert literal, "getattr with a literal name resolves to the attribute node"


def test_reflection_with_a_computed_name_is_a_barrier(tmp_path: Path) -> None:
    tracer = analyze(tmp_path, {"refl": REFLECTION_SRC})
    dynamic = [
        b
        for b in tracer.barriers
        if str(b.reason) == "DYNAMIC_NAME" and "getattr" in b.description
    ]
    assert len(dynamic) == 1
    assert has(tracer, LineageKind.ASSIGNS, dynamic[0].id, "refl::pick.unknown")


def test_stdlib_pure_call_passes_the_value_through(tmp_path: Path) -> None:
    src = "import math\n\n\ndef f(x):\n    y = math.sqrt(x)\n    return y\n"
    tracer = analyze(tmp_path, {"pure": src})
    assert tracer.barriers == ()
    assert has(tracer, LineageKind.ASSIGNS, "pure::f.x", "pure::f.y")


# ---------------------------------------------------------------------------
# lin_slice_backward / lin_slice_forward
# ---------------------------------------------------------------------------

CASCADE_SRC = '''\
THRESHOLD = 10
UNUSED_CONSTANT = 99


def ingest(raw):
    return {"price": raw, "noise": 0}


def engineer(row):
    features = {}
    features["momentum"] = row["price"] * 2
    features["unused"] = row["noise"]
    return features


def decide(features):
    score = features["momentum"]
    if score > THRESHOLD:
        return "BUY"
    return "HOLD"


def main(raw):
    row = ingest(raw)
    features = engineer(row)
    return decide(features)
'''

SINK = "cascade::decide.@return"


def cascade(tmp_path: Path) -> LineageTracer:
    return analyze(tmp_path, {"cascade": CASCADE_SRC}, sinks=[SINK])


def test_lin_slice_backward_is_exact(tmp_path: Path) -> None:
    tracer = cascade(tmp_path)
    backward = tracer.slice("cascade::decide.score", "backward")
    assert backward.direction == "backward"
    assert backward.root_id == "cascade::decide.score"
    assert backward.id == "@slice:backward:cascade::decide.score"
    assert feature_id("momentum") in backward.member_ids
    assert feature_id("price") in backward.member_ids
    assert "cascade::main.raw" in backward.member_ids
    # Nothing that does not produce the score is in it.
    assert feature_id("noise") not in backward.member_ids
    assert feature_id("unused") not in backward.member_ids
    assert "cascade::UNUSED_CONSTANT" not in backward.member_ids
    assert "cascade::THRESHOLD" not in backward.member_ids
    assert backward.barrier_ids == ()
    assert backward.confidence is Confidence.RESOLVED


def test_lin_slice_backward_carries_per_hop_evidence(tmp_path: Path) -> None:
    tracer = cascade(tmp_path)
    backward = tracer.slice("cascade::decide.score", "backward")
    assert backward.edge_ids
    hops = tracer.hops(backward)
    assert len(hops) == len(backward.edge_ids)
    for hop in hops:
        assert hop["method"]
        assert hop["confidence"]
        assert hop["path"] == "cascade.py"
        assert int(hop["line"]) > 0
    # Reproducible: the same query gives the same IDs.
    assert tracer.slice("cascade::decide.score", "backward") == backward


def test_lin_slice_forward_reaches_the_sink_and_stops(tmp_path: Path) -> None:
    tracer = cascade(tmp_path)
    forward = tracer.slice("cascade::main.raw", "forward")
    assert forward.direction == "forward"
    assert SINK in forward.member_ids
    assert forward.reaches_sink_ids == (SINK,)
    # It stops at the sink: what the sink flows into afterwards is not in the slice.
    assert "cascade::main.@return" not in forward.member_ids
    assert "cascade::UNUSED_CONSTANT" not in forward.member_ids


def test_forward_slice_of_an_unconsumed_feature_reaches_no_sink(tmp_path: Path) -> None:
    tracer = cascade(tmp_path)
    forward = tracer.slice(feature_id("unused"), "forward")
    assert forward.reaches_sink_ids == ()


def test_forward_slice_of_a_threshold_reaches_the_decision(tmp_path: Path) -> None:
    """A constant read only by a branch condition still drives the decision."""
    tracer = cascade(tmp_path)
    forward = tracer.slice("cascade::THRESHOLD", "forward")
    assert forward.reaches_sink_ids == (SINK,)


def test_slice_direction_is_validated(tmp_path: Path) -> None:
    tracer = cascade(tmp_path)
    with pytest.raises(ValueError):
        tracer.slice("cascade::main.raw", "sideways")


def test_slice_of_an_unknown_root_is_empty_and_recorded(tmp_path: Path) -> None:
    tracer = cascade(tmp_path)
    result = tracer.slice("cascade::nope", "backward")
    assert result.member_ids == ("cascade::nope",)
    assert result.edge_ids == ()
    assert result.confidence is Confidence.UNKNOWN
    assert any(item.id == "@slice-root:cascade::nope" for item in tracer.unresolved)


# ---------------------------------------------------------------------------
# contract conformance, determinism, safety
# ---------------------------------------------------------------------------


def test_implements_the_lineage_card_protocol(tmp_path: Path) -> None:
    tracer = cascade(tmp_path)
    assert callable(tracer.trace_values)
    assert callable(tracer.slice)
    for edge in tracer.lineage_edges:
        assert isinstance(edge, LineageEdge)
        assert isinstance(edge.kind, LineageKind)
        assert isinstance(edge.provenance.method, Method)
        assert isinstance(edge.provenance.confidence, Confidence)
        assert edge.span is not None and edge.span.path


def test_every_edge_carries_provenance_and_a_span(tmp_path: Path) -> None:
    tracer = analyze(tmp_path, {"frames": FRAME_SRC, "cascade": CASCADE_SRC})
    assert tracer.lineage_edges
    for edge in tracer.lineage_edges:
        assert edge.provenance.method in set(Method)
        assert edge.provenance.confidence in set(Confidence)
        assert edge.span is not None
        assert edge.span.line >= 1


def test_confidence_of_a_slice_is_combined_from_its_hops(tmp_path: Path) -> None:
    tracer = analyze(tmp_path, {"p": PARAMS_SRC})
    backward = tracer.slice("p::blend.options", "backward")
    hops = [tracer._edges[eid].provenance.confidence for eid in backward.edge_ids]
    order = ["UNKNOWN", "HEURISTIC", "PROBABLE", "RESOLVED", "CERTAIN"]
    assert backward.confidence is min(hops, key=lambda c: order.index(str(c)))


def test_two_runs_are_byte_identical(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = analyze(
        tmp_path / "a", {"cascade": CASCADE_SRC, "frames": FRAME_SRC, "cont": CONTAINER_SRC},
        sinks=[SINK],
    )
    second = analyze(
        tmp_path / "b", {"cascade": CASCADE_SRC, "frames": FRAME_SRC, "cont": CONTAINER_SRC},
        sinks=[SINK],
    )
    left = first.emit()
    right = second.emit()
    assert sorted(left) == ["barriers.jsonl", "lineage.jsonl", "slices.jsonl"]
    for name in left:
        assert left[name] == right[name], name
    # and a second emit from the same tracer is identical too
    assert first.emit() == left


def test_artifacts_are_sorted_json_lines_without_floats(tmp_path: Path) -> None:
    tracer = analyze(tmp_path, {"frames": FRAME_SRC}, sinks=[SINK])
    artifacts = tracer.emit()
    for name, text in artifacts.items():
        if not text:
            continue
        assert text.endswith("\n"), name
        rows = [json.loads(line) for line in text.splitlines()]
        assert [row["id"] for row in rows] == sorted(row["id"] for row in rows), name
        for row in rows:
            canonical_dumps(row)  # raises on floats
            assert json.dumps(row, sort_keys=True) is not None


def test_nothing_is_silently_dropped(tmp_path: Path) -> None:
    src = "def f():\n    return undefined_name\n"
    tracer = analyze(tmp_path, {"gap": src})
    assert any(
        item.reason.value == "MISSING_TARGET" and "undefined_name" in item.description
        for item in tracer.unresolved
    )


def test_a_syntax_error_is_recorded_and_the_run_continues(tmp_path: Path) -> None:
    good = "def f(a):\n    b = a\n    return b\n"
    (tmp_path / "broken.py").write_text("def f(:\n", encoding="utf-8")
    (tmp_path / "good.py").write_text(good, encoding="utf-8")
    tracer = LineageTracer(root=tmp_path)
    tracer.trace_values(
        [module_element("broken", "broken.py"), module_element("good", "good.py")], []
    )
    assert any(item.reason.value == "SYNTAX_ERROR" for item in tracer.unresolved)
    assert has(tracer, LineageKind.ASSIGNS, "good::f.a", "good::f.b")


def test_a_missing_module_file_is_recorded(tmp_path: Path) -> None:
    tracer = LineageTracer(root=tmp_path)
    tracer.trace_values([module_element("absent", "absent.py")], [])
    assert any(item.reason.value == "MISSING_TARGET" for item in tracer.unresolved)


def test_card_two_call_edge_is_used_and_its_confidence_composed(tmp_path: Path) -> None:
    src = (
        "def helper(v):\n    return v\n\n\n"
        "def run(raw):\n    handler = helper\n    out = handler(raw)\n    return out\n"
    )
    edge = Edge(
        id="c1",
        kind=EdgeKind.CALLS,
        source_id="dyn::run",
        target_id="dyn::helper",
        provenance=Provenance(method=Method.GETATTR_LITERAL, confidence=Confidence.PROBABLE),
        call_site=SourceSpan(path="dyn.py", line=7),
    )
    tracer = analyze(tmp_path, {"dyn": src}, edges=[edge])
    bindings = edges_between(tracer, "dyn::run.raw", "dyn::helper.v")
    assert bindings, "card 2's CALLS edge must be honoured"
    assert bindings[0].provenance.confidence is Confidence.PROBABLE
    assert "card 2" in bindings[0].provenance.note


def test_parameter_element_ids_from_card_one_are_preferred(tmp_path: Path) -> None:
    src = "def helper(v):\n    return v\n\n\ndef run(raw):\n    return helper(raw)\n"
    param = Element(
        id="pe::helper.v",
        kind=ElementKind.PARAMETER,
        name="v",
        qualname="helper.v",
        module="pe",
        span=SourceSpan(path="pe.py", line=1),
        provenance=CERTAIN,
        content_hash="h",
        parent_id="pe::helper",
    )
    tracer = analyze(tmp_path, {"pe": src}, extra_elements=[param])
    assert has(tracer, LineageKind.PARAMETER_BINDING, "pe::run.raw", "pe::helper.v")


def test_no_target_code_is_executed(tmp_path: Path) -> None:
    """The sentinel proves constraint 1 empirically for this card."""
    if SENTINEL_MARKER.exists():
        SENTINEL_MARKER.unlink()
    sentinel = FIXTURES / "sentinel" / "__init__.py"
    assert sentinel.exists(), "the sentinel fixture is missing"
    element = module_element("sentinel", "sentinel/__init__.py")
    tracer = LineageTracer(root=FIXTURES)
    tracer.trace_values([element], [])
    tracer.default_slices()
    tracer.emit()
    assert not SENTINEL_MARKER.exists(), "lineage executed the sentinel fixture"


def test_ids_survive_reformatting(tmp_path: Path) -> None:
    """Structural IDs, per the contract: a reformat changes no lineage node."""
    reformatted = CHAIN_SRC.replace("BASE = 3", "# a comment\nBASE   =   3").replace(
        "    y = x * 2", "    y = (\n        x * 2\n    )"
    )
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    plain = analyze(tmp_path / "a", {"chain": CHAIN_SRC})
    changed = analyze(tmp_path / "b", {"chain": reformatted})
    assert triples(plain.lineage_edges) == triples(changed.lineage_edges)


def test_default_slices_cover_every_feature(tmp_path: Path) -> None:
    tracer = cascade(tmp_path)
    slices = tracer.default_slices()
    ids = {s.id for s in slices}
    for name in ("momentum", "price", "noise", "unused"):
        assert f"@slice:backward:{feature_id(name)}" in ids
        assert f"@slice:forward:{feature_id(name)}" in ids
    assert list(slices) == sorted(slices, key=lambda s: s.id)




# ---------------------------------------------------------------------------
# precision and recall
# ---------------------------------------------------------------------------

#: Constructed cases with a complete hand-written edge set, graded alongside the
#: eight non-barrier fixtures.
GRADED: dict[str, tuple[dict[str, str], set[tuple[str, str, str]]]] = {
    "constructed_assign_chain": ({"chain": CHAIN_SRC}, CHAIN_EXPECTED),
    "constructed_container": ({"cont": CONTAINER_SRC}, CONTAINER_EXPECTED),
}


def test_precision_and_recall(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Precision is the priority: a confident wrong edge fails this card."""
    emitted_total = 0
    expected_total = 0
    correct_total = 0
    lines = []
    cases: list[tuple[str, set[tuple[str, str, str]], set[tuple[str, str, str]]]] = []
    for case, expected in sorted(FIXTURE_EXPECTED.items()):
        cases.append((case, triples(fixture_tracer(case).lineage_edges), expected))
    for case, (sources, expected) in sorted(GRADED.items()):
        directory = tmp_path / case
        directory.mkdir()
        cases.append((case, triples(analyze(directory, sources).lineage_edges), expected))
    for case, emitted, expected in cases:
        correct = emitted & expected
        emitted_total += len(emitted)
        expected_total += len(expected)
        correct_total += len(correct)
        lines.append(
            f"{case}: emitted={len(emitted)} expected={len(expected)} "
            f"correct={len(correct)} false_positives={sorted(emitted - expected)} "
            f"missed={sorted(expected - emitted)}"
        )
    precision = Fraction(correct_total, emitted_total)
    recall = Fraction(correct_total, expected_total)
    with capsys.disabled():
        print("\n".join(lines))
        print(f"lineage precision={precision} recall={recall}")
    assert precision == 1, "a lineage edge that is not in the hand-written expectation"
    assert recall == 1, "a hand-written expected edge was not produced"


def test_over_and_under_approximation_are_reported_separately(tmp_path: Path) -> None:
    tracer = analyze(tmp_path, {"p": PARAMS_SRC, "refl": REFLECTION_SRC})
    over = tracer.over_approximate_edge_ids()
    under = tuple(b.id for b in tracer.barriers)
    assert over, "**kwargs expansion is over-approximate and must be labelled"
    assert under, "opaque calls are under-approximate and must be barriers"
    assert set(over).isdisjoint(set(under))
    for edge_id in over:
        assert tracer._edges[edge_id].provenance.note.startswith("over-approximate: ")


def test_the_card_cannot_execute_target_code() -> None:
    """Constraint 1, checked structurally: no execution primitive is called here."""
    module = Path(__file__).parent.parent / "src" / "cascade_map" / "lineage.py"
    tree = ast.parse(module.read_text(encoding="utf-8"))
    called: set[str] = set()
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert called.isdisjoint(
        {"eval", "exec", "compile", "__import__", "import_module", "loads", "system", "popen"}
    )
    assert imported.isdisjoint({"importlib", "subprocess", "pickle", "marshal", "runpy"})
    assert "MODEL_PROPOSED" not in {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
