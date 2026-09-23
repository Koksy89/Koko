"""Cards 3 and 4 must scale with the size of a single file, not its square.

The owner's target is one 14.8 MB module. Every quadratic in these two cards
was invisible on the fixture corpus, where the largest file is a few kilobytes,
and only showed up as a run that did not finish. This file is the guard that
keeps them visible: the same generated program at N and 4N units, asserting the
time ratio is far closer to linear (4x) than to quadratic (16x).

The threshold is deliberately loose. A slow or contended machine moves these
numbers around, and a flaky performance test gets deleted rather than fixed, so
the assertion is only strong enough to catch a re-introduced quadratic --
nothing here claims a particular speed.

Two things this file deliberately does **not** cover:

* ``LineageTracer.default_slices`` is quadratic by construction -- it emits one
  transitive closure per feature, key and sink, so both its time and its bytes
  grow as roots x closure. That is a property of the artifact, not a defect in
  the walk, and asserting linearity on it would be asserting something false.
* Card 1's ingestion and card 2's resolution are measured by their own cards.

Nothing here executes the generated program: it is written to ``tmp_path`` and
read back with :mod:`ast`, exactly as the cards do.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from cascade_map.cascade import CascadeAnalyzer
from cascade_map.contracts.interfaces import Edge, Element
from cascade_map.ingest.inventory import Ingestor
from cascade_map.lineage import LineageTracer
from cascade_map.resolve import Resolver

# 4x the input. Quadratic would be 16x; this passes at up to 8x, which no
# linear or n log n implementation approaches and no quadratic one survives.
QUADRATIC_IS_16X = 16.0
MAX_ACCEPTABLE_RATIO = 8.0

# Small enough for a test, large enough that the N run is not pure timer noise.
SMALL_UNITS = 120
LARGE_UNITS = SMALL_UNITS * 4


def _program(units: int) -> str:
    """One module holding *units* independent rule blocks that never call out.

    The shape that matters: module-level bindings that accumulate (so a walker
    carrying a per-statement environment grows it), branches and loops in every
    function (so every branch merges that environment), and cross-unit calls
    (so there is a call graph and a lineage graph to walk).
    """
    lines = ['"""Generated scaling fixture. Never executed."""', ""]
    for unit in range(units):
        previous = (unit - 1) % units
        lines += [
            f"LIMIT_{unit} = {unit + 1}",
            f"NAMES_{unit} = ['a_{unit}', 'b_{unit}']",
            "",
            f"def score_{unit}(row, limit=LIMIT_{unit}):",
            f"    total = row['a_{unit}'] + row['b_{unit}']",
            "    for name in NAMES_%d:" % unit,
            "        if name in row:",
            "            total = total + row[name]",
            "        else:",
            "            total = total - 1",
            "    try:",
            "        ratio = total / limit",
            "    except ZeroDivisionError:",
            "        ratio = 0",
            "    return ratio",
            "",
            f"def decide_{unit}(row):",
            f"    ratio = score_{unit}(row)",
            f"    helper = describe_{previous}(row) if ratio else ''",
            f"    if ratio > LIMIT_{unit} and helper:",
            "        return 'high'",
            f"    elif ratio > 0 or helper == 'x':",
            "        return 'medium'",
            "    return 'low'",
            "",
            f"def describe_{unit}(row):",
            f"    return str(LIMIT_{unit}) + str(row.get('a_{unit}', 0))",
            "",
        ]
    lines += [
        "def main(rows):",
        "    out = []",
        "    for row in rows:",
        "        out.append(decide_0(row))",
        "    return out",
        "",
    ]
    return "\n".join(lines)


def _prepare(root: Path, units: int) -> tuple[list[Element], list[Edge]]:
    """Inventory and resolve, outside the timed region and on a cold cache."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "generated.py").write_text(_program(units), encoding="utf-8")
    with tempfile.TemporaryDirectory() as cache:
        ingestor = Ingestor(cache_dir=Path(cache), workers=1)
        elements, _ = ingestor.inventory(str(root))
    edges, _ = Resolver(root).resolve(elements)
    return list(elements), list(edges)


def _time_cards(root: Path, elements: list[Element], edges: list[Edge]) -> tuple[float, float]:
    started = time.perf_counter()
    CascadeAnalyzer(root, sink_ids=(), unresolved=()).order(elements, edges, ())
    cascade = time.perf_counter() - started

    started = time.perf_counter()
    LineageTracer(root, sink_ids=()).trace_values(elements, edges)
    lineage = time.perf_counter() - started
    return cascade, lineage


def test_cascade_and_lineage_scale_closer_to_linear_than_quadratic(
    tmp_path: Path,
) -> None:
    small_root = tmp_path / "small"
    large_root = tmp_path / "large"
    small = _prepare(small_root, SMALL_UNITS)
    large = _prepare(large_root, LARGE_UNITS)

    # The large program really is four times the small one, or the ratio below
    # measures nothing.
    assert len(large[0]) > 3.5 * len(small[0])

    small_cascade, small_lineage = _time_cards(small_root, *small)
    large_cascade, large_lineage = _time_cards(large_root, *large)

    # A floor under the denominator: below a few milliseconds the ratio is
    # measuring the clock, not the algorithm.
    floor = 0.02
    cascade_ratio = large_cascade / max(small_cascade, floor)
    lineage_ratio = large_lineage / max(small_lineage, floor)

    assert cascade_ratio < MAX_ACCEPTABLE_RATIO, (
        f"CascadeAnalyzer.order grew {cascade_ratio:.1f}x for 4x the input "
        f"({small_cascade:.3f}s -> {large_cascade:.3f}s); quadratic is "
        f"{QUADRATIC_IS_16X:.0f}x"
    )
    assert lineage_ratio < MAX_ACCEPTABLE_RATIO, (
        f"LineageTracer.trace_values grew {lineage_ratio:.1f}x for 4x the "
        f"input ({small_lineage:.3f}s -> {large_lineage:.3f}s); quadratic is "
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
    edges, _ = Resolver(root).resolve(elements)
    CascadeAnalyzer(root, sink_ids=(), unresolved=()).order(list(elements), list(edges), ())
    LineageTracer(root, sink_ids=()).trace_values(list(elements), list(edges))
    assert not marker.exists()
