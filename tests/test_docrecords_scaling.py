"""Card 16 must scale with the size of a single file, not its square.

The owner's target is one 14.6 MB module. On it, `records()` took 1227s of a
1530s `analyze` -- not because assembling a record is expensive, but because
assembling ONE record scanned whole collections: every lineage edge, every
slice, every trace event, once per element. The cost of that shape is
invisible on the fixture corpus, where the largest file is a few kilobytes,
and fatal on a real target.

This file is the guard that keeps it visible: the same generated program at N
and 4N units, asserting the time ratio is far closer to linear (4x) than to
quadratic (16x). The threshold is deliberately loose -- a slow or contended
machine moves these numbers around, and a flaky performance test gets deleted
rather than fixed -- so the assertion is only strong enough to catch a
re-introduced per-element scan. Nothing here claims a particular speed.

Nothing here executes the generated program: it is written to ``tmp_path`` and
read back with :mod:`ast`, exactly as the cards do.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from cascade_map.cascade import CascadeAnalyzer
from cascade_map.contracts.interfaces import SliceScope
from cascade_map.docrecords import DocumentationBuilder
from cascade_map.ingest.inventory import Ingestor
from cascade_map.lineage import LineageTracer
from cascade_map.resolve import Resolver

QUADRATIC_IS_16X = 16.0
MAX_ACCEPTABLE_RATIO = 8.0

SMALL_UNITS = 120
LARGE_UNITS = SMALL_UNITS * 4


def _program(units: int) -> str:
    """One module of *units* rule blocks that read features and call onward."""
    lines = ['"""Generated scaling fixture. Never executed."""', ""]
    for unit in range(units):
        previous = (unit - 1) % units
        lines += [
            f"LIMIT_{unit} = {unit + 1}",
            f"def score_{unit}(row, limit=LIMIT_{unit}):",
            f"    total = row['a_{unit}'] + row['b_{unit}']",
            "    if total > limit:",
            "        total = total - 1",
            "    return total / limit",
            "",
            f"def decide_{unit}(row):",
            f"    ratio = score_{unit}(row)",
            f"    helper = describe_{previous}(row) if ratio else ''",
            f"    if ratio > LIMIT_{unit} and helper:",
            "        return 'high'",
            "    return 'low'",
            "",
            f"def describe_{unit}(row):",
            f"    return str(LIMIT_{unit}) + str(row.get('a_{unit}', 0))",
            "",
        ]
    lines += [
        "def main(rows):",
        "    verdict = []",
        "    for row in rows:",
        "        verdict.append(decide_0(row))",
        "    return verdict",
        "",
    ]
    return "\n".join(lines)


def _prepare(root: Path, units: int) -> dict[str, object]:
    """Everything card 16 consumes, built outside the timed region."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "generated.py").write_text(_program(units), encoding="utf-8")
    with tempfile.TemporaryDirectory() as cache:
        ingestor = Ingestor(cache_dir=Path(cache), workers=1)
        elements, _ = ingestor.inventory(str(root))
    elements = list(elements)
    edges, _ = Resolver(root).resolve(elements)
    edges = list(edges)
    sink_ids = tuple(
        el.id for el in elements if el.name == "main" and el.kind.value == "FUNCTION"
    )
    analyzer = CascadeAnalyzer(root, sink_ids=sink_ids, unresolved=())
    _, _, order_nodes, decisions, _, _, _ = analyzer.order(elements, edges, ())
    tracer = LineageTracer(root, sink_ids=sink_ids)
    lineage_edges, _ = tracer.trace_values(elements, edges)
    return {
        "elements": elements,
        "edges": edges,
        "order_nodes": list(order_nodes),
        "decisions": list(decisions),
        "lineage_edges": list(lineage_edges),
        "slices": list(tracer.default_slices(SliceScope.DECISION)),
        "decision_sink_ids": sink_ids,
    }


def _time_card(inputs: dict[str, object]) -> float:
    started = time.perf_counter()
    builder = DocumentationBuilder(**inputs)  # type: ignore[arg-type]
    records = builder.records()
    offenders = builder.completeness_gate(records)
    elapsed = time.perf_counter() - started
    # The gate must pass, or the timing is of a run that would have failed.
    assert not offenders, f"{len(offenders)} incomplete records"
    assert len(records) == len(inputs["elements"])  # type: ignore[arg-type]
    return elapsed


def test_records_and_gate_scale_closer_to_linear_than_quadratic(
    tmp_path: Path,
) -> None:
    small = _prepare(tmp_path / "small", SMALL_UNITS)
    large = _prepare(tmp_path / "large", LARGE_UNITS)

    # The large program really is four times the small one, or the ratio below
    # measures nothing.
    assert len(large["elements"]) > 3.5 * len(small["elements"])  # type: ignore[arg-type]

    small_seconds = _time_card(small)
    large_seconds = _time_card(large)

    # A floor under the denominator: below a few milliseconds the ratio is
    # measuring the clock, not the algorithm.
    ratio = large_seconds / max(small_seconds, 0.02)
    assert ratio < MAX_ACCEPTABLE_RATIO, (
        f"DocumentationBuilder grew {ratio:.1f}x for 4x the input "
        f"({small_seconds:.3f}s -> {large_seconds:.3f}s); quadratic is "
        f"{QUADRATIC_IS_16X:.0f}x"
    )


def test_scaling_fixture_is_never_executed(tmp_path: Path) -> None:
    """The generated program writes a sentinel if anything ever runs it."""
    root = tmp_path / "sentinel"
    root.mkdir()
    marker = tmp_path / "executed.marker"
    (root / "generated.py").write_text(
        "import pathlib\n"
        f"pathlib.Path({str(marker)!r}).write_text('executed')\n"
        "def decide(row):\n"
        "    return 'high' if row else 'low'\n",
        encoding="utf-8",
    )
    with tempfile.TemporaryDirectory() as cache:
        elements, _ = Ingestor(cache_dir=Path(cache), workers=1).inventory(str(root))
    edges, _ = Resolver(root).resolve(list(elements))
    builder = DocumentationBuilder(elements=list(elements), edges=list(edges))
    assert builder.completeness_gate(builder.records()) == ()
    assert not marker.exists()
