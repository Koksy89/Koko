"""Card 10 — the command line, and the only place the cards meet.

Every other card was built against the contracts rather than against its
neighbours. That is what let thirteen of them be built at once, and it means
this module is the first place they are asked to agree. Expect integration to
find disagreements rather than confirm their absence.

Four commands:

* ``analyze``  static map of a target tree. Never executes it.
* ``diff``     compare two analysed versions.
* ``view``     render an analysed tree as one offline HTML page.
* ``trace``    Mode A. Not built yet, and refuses rather than pretending.

**Nothing here executes, imports, execs, evals or unpickles the target.** The
static path reads source as text and parses it with ``ast``. The one command
that would run target code is ``trace``, which belongs to the harness and is
the only command that ever needs the owner's explicit approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from cascade_map import __version__
from cascade_map.cascade import CascadeAnalyzer
from cascade_map.contracts.interfaces import (
    SCHEMA_VERSION,
    Confidence,
    DetectedCandidate,
    canonical_dumps,
    canonical_jsonl,
)
from cascade_map.diff import diff_snapshots, load_snapshot
from cascade_map.docrecords import DocumentationBuilder
from cascade_map.findings import Findings
from cascade_map.ingest import inventory
from cascade_map.lineage import LineageTracer
from cascade_map.resolve import Resolver

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_GATE_FAILED = 3
EXIT_REFUSED = 4


# ---------------------------------------------------------------------------
# Writing artifacts
# ---------------------------------------------------------------------------


def _write(out_dir: Path, name: str, text: str) -> str:
    """Write one artifact and return its content hash."""
    path = out_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _target_hashes(root: Path) -> dict[str, str]:
    """Content hash per target file, by POSIX path relative to the root.

    Read as bytes. Nothing here decodes or parses, so an unreadable or binary
    file hashes like any other rather than breaking the run.
    """
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        hashes[path.relative_to(root).as_posix()] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    return hashes


def _confidence_census(records: Sequence[Any]) -> dict[str, int]:
    """How many facts sit at each confidence level.

    The single most useful number in the report. A map that is 90% HEURISTIC
    is a different object from one that is 90% CERTAIN, and the owner should
    not have to open the artifacts to find out which they have.
    """
    census = {level.value: 0 for level in Confidence}
    for record in records:
        provenance = getattr(record, "provenance", None)
        if provenance is not None:
            census[str(provenance.confidence)] += 1
    return census


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------


def analyze(
    root: Path,
    out_dir: Path,
    *,
    entry_ids: Sequence[str] = (),
    sink_ids: Sequence[str] = (),
    config_paths: Sequence[str] = (),
    cache_dir: Path | None = None,
    strict_gate: bool = True,
) -> tuple[int, dict[str, Any]]:
    """Run the static pipeline over *root* and write artifacts to *out_dir*.

    Returns an exit code and a summary. The gate failing is a non-zero exit:
    an incomplete map that reports success is worse than one that refuses,
    because the owner acts on it either way.
    """
    started = time.time()
    summary: dict[str, Any] = {}
    artifacts: dict[str, str] = {}

    # Card 1 — inventory.
    elements, unresolved = inventory(str(root), cache_dir=cache_dir)
    summary["elements"] = len(elements)

    # Card 2 — resolution.
    resolver = Resolver(root, config_paths=tuple(config_paths))
    edges, resolve_unresolved = resolver.resolve(elements)
    unresolved = list(unresolved) + list(resolve_unresolved)
    summary["edges"] = len(edges)

    # Card 3 — CFG, ordering, decisions, reachability, detected candidates.
    analyzer = CascadeAnalyzer(root, sink_ids=tuple(sink_ids), unresolved=unresolved)
    (
        blocks,
        cfg_edges,
        order_nodes,
        decisions,
        reachability,
        candidates,
        cascade_unresolved,
    ) = analyzer.order(elements, edges, tuple(entry_ids))
    unresolved = list(unresolved) + list(cascade_unresolved)
    summary["decisions"] = len(decisions)

    # Card 4 — lineage and slices.
    tracer = LineageTracer(root, sink_ids=tuple(sink_ids))
    lineage_edges, barriers = tracer.trace_values(elements, edges)
    slices = tracer.default_slices()
    summary["lineage_edges"] = len(lineage_edges)
    summary["barriers"] = len(barriers)

    # Card 5 — findings.
    findings = Findings(
        elements=elements,
        edges=edges,
        unresolved=unresolved,
        cfg_blocks=blocks,
        cfg_edges=cfg_edges,
        decision_points=decisions,
        lineage_edges=lineage_edges,
        barriers=barriers,
        slices=slices,
        reachability=reachability,
        entry_ids=tuple(entry_ids),
    ).find()
    summary["findings"] = len(findings)

    # Card 16 — documentation records, then the gate.
    builder = DocumentationBuilder(
        elements=elements,
        edges=edges,
        order_nodes=order_nodes,
        decisions=decisions,
        lineage_edges=lineage_edges,
        slices=slices,
        findings=findings,
        decision_sink_ids=tuple(sink_ids),
    )
    records = builder.records()
    offenders = builder.completeness_gate(records)
    summary["incomplete_records"] = len(offenders)

    for name, payload in (
        ("elements.jsonl", canonical_jsonl(elements)),
        ("edges.jsonl", canonical_jsonl(edges)),
        ("unresolved.jsonl", canonical_jsonl(unresolved)),
        ("cfg_blocks.jsonl", canonical_jsonl(blocks)),
        ("cfg_edges.jsonl", canonical_jsonl(cfg_edges)),
        ("order.jsonl", canonical_jsonl(order_nodes)),
        ("decisions.jsonl", canonical_jsonl(decisions)),
        ("reachability.jsonl", canonical_jsonl(reachability)),
        ("candidates.jsonl", canonical_jsonl(candidates)),
        ("lineage.jsonl", canonical_jsonl(lineage_edges)),
        ("barriers.jsonl", canonical_jsonl(barriers)),
        ("slices.jsonl", canonical_jsonl(slices)),
        ("findings.jsonl", canonical_jsonl(findings)),
        ("records.jsonl", canonical_jsonl(records)),
    ):
        artifacts[name] = _write(out_dir, name, payload)

    summary["unresolved"] = len(unresolved)
    summary["confidence"] = _confidence_census(list(edges) + list(lineage_edges))
    summary["detected"] = [
        {"role": c.role, "element_id": c.element_id,
         "confidence": str(c.provenance.confidence)}
        for c in candidates
    ]

    # manifest.json is inside the byte-identical guarantee; run_meta.json is
    # deliberately outside it, and holds everything that legitimately varies.
    _write(
        out_dir,
        "manifest.json",
        canonical_dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "artifacts": artifacts,
                "target_hashes": _target_hashes(root),
                "entry_ids": sorted(entry_ids),
                "sink_ids": sorted(sink_ids),
            }
        )
        + "\n",
    )
    (out_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "tool_version": __version__,
                "python": platform.python_version(),
                "target_root": str(root.resolve()),
                "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "elapsed_seconds": round(time.time() - started, 1),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    if offenders and strict_gate:
        return EXIT_GATE_FAILED, summary
    return EXIT_OK, summary


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _report(summary: dict[str, Any], out_dir: Path, exit_code: int) -> str:
    """The owner-facing summary. Honest about what it could not work out."""
    lines = [
        f"Wrote {out_dir}",
        "",
        f"  elements      {summary['elements']:>8,}",
        f"  call edges    {summary['edges']:>8,}",
        f"  lineage edges {summary['lineage_edges']:>8,}",
        f"  decisions     {summary['decisions']:>8,}",
        f"  findings      {summary['findings']:>8,}",
        f"  unresolved    {summary['unresolved']:>8,}   <- reported, never dropped",
        f"  barriers      {summary['barriers']:>8,}   <- value flow stops being traceable",
        "",
        "Confidence of the edges the map is built from:",
    ]
    census = summary["confidence"]
    total = sum(census.values()) or 1
    for level in ("CERTAIN", "RESOLVED", "PROBABLE", "HEURISTIC", "UNKNOWN"):
        count = census.get(level, 0)
        lines.append(f"  {level:<10} {count:>8,}  {100 * count // total:>3}%")

    detected = summary.get("detected") or []
    if detected:
        lines += ["", "Detected, NOT confirmed — these are proposals for you:"]
        for item in detected:
            lines.append(
                f"  {item['role']:<14} {item['element_id']}  ({item['confidence']})"
            )
        lines.append("  Set them in docs/design/TARGET_PROFILE.md to make them facts.")

    if exit_code == EXIT_GATE_FAILED:
        lines += [
            "",
            f"GATE FAILED: {summary['incomplete_records']:,} elements have an "
            "incomplete documentation record.",
            "The artifacts were still written so you can see what is missing.",
        ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cascade-map",
        description="Map a Python decision engine. The static commands never "
        "execute the target.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("analyze", help="static map of a target tree")
    run.add_argument("root", type=Path)
    run.add_argument("--out", type=Path, default=Path("out/latest"))
    run.add_argument("--entry", action="append", default=[], metavar="ID",
                     help="entry point element id; repeatable. Detected if omitted.")
    run.add_argument("--sink", action="append", default=[], metavar="ID",
                     help="decision sink element id; repeatable. Detected if omitted.")
    run.add_argument("--config", action="append", default=[], metavar="PATH",
                     help="config file that wires components by name; repeatable")
    run.add_argument("--cache", type=Path, default=None)
    run.add_argument("--no-gate", action="store_true",
                     help="write artifacts even if the completeness gate fails, "
                          "and exit 0. The gate still reports.")

    cmp_ = sub.add_parser("diff", help="compare two analysed output directories")
    cmp_.add_argument("before", type=Path)
    cmp_.add_argument("after", type=Path)
    cmp_.add_argument("--out", type=Path, default=Path("out/diff"))

    show = sub.add_parser("view", help="render an analysed tree as offline HTML")
    show.add_argument("out_dir", type=Path)
    show.add_argument("--html", type=Path, default=None)

    sub.add_parser("trace", help="Mode A — run the target under the harness")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "analyze":
        if not args.root.is_dir():
            print(f"not a directory: {args.root}", file=sys.stderr)
            return EXIT_USAGE
        code, summary = analyze(
            args.root,
            args.out,
            entry_ids=tuple(args.entry),
            sink_ids=tuple(args.sink),
            config_paths=tuple(args.config),
            cache_dir=args.cache,
            strict_gate=not args.no_gate,
        )
        print(_report(summary, args.out, code))
        return EXIT_OK if args.no_gate else code

    if args.command == "diff":
        changes, impacts = diff_snapshots(
            load_snapshot(args.before), load_snapshot(args.after)
        )
        args.out.mkdir(parents=True, exist_ok=True)
        _write(args.out, "changes.jsonl", canonical_jsonl(changes))
        _write(args.out, "impacts.jsonl", canonical_jsonl(impacts))
        moved = sum(1 for i in impacts if i.decision_paths_changed)
        print(f"Wrote {args.out}\n  changes {len(changes):,}\n  impacts {len(impacts):,}")
        print(f"  {moved:,} of them change a path to a decision")
        return EXIT_OK

    if args.command == "view":
        from cascade_map import viewer

        target = args.html or (args.out_dir / "index.html")
        viewer.render_to_file(args.out_dir, target)
        print(f"Wrote {target}\nOpen it in a browser. It needs no network.")
        return EXIT_OK

    # Mode A. Refusing is the correct answer while it is unbuilt, and it must
    # not read as "nothing happened".
    print(
        "REFUSED: `trace` is not built yet.\n\n"
        "Mode A runs your engine to watch it. Card 10's Mode A half, and card "
        "15's runtime overlay, do not exist.\n\n"
        "When it does exist, read docs/design/ARCHITECTURE.md under 'What layer "
        "3 does not cover' first: the harness cannot close every escape, it "
        "says so in every run record, and the first run belongs inside a "
        "container.",
        file=sys.stderr,
    )
    return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
