"""`metatron doctor` — one file the owner can send back after a real run.

It answers three questions and nothing else:

1. How long does each stage take on this machine, on this target?
2. Do workers help this target, and if not, why not?
3. What is this machine and this interpreter?

WHAT IT DELIBERATELY DOES NOT CONTAIN. No source code, no element names, no
docstrings, no literals, no configuration values — only timings, counts,
versions, and the shape of the machine. The one thing it names from the
target is the ROOT-RELATIVE PATH of the file that dominates the work, because
that is the answer to "why did workers not help" and a path is not content.
The header of the file says so, so the owner can see what they are sending
before they send it.

It never executes the target. `doctor` is Mode B only; it calls the same
static pipeline `analyze` does.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cascade_map.ingest.inventory import Ingestor
from cascade_map.ingest.parallel import cpu_budget, render_worker_report, resolve_workers

#: The worker counts measured. 1 is the control; the rest are the question.
SCALING_POINTS: tuple[int, ...] = (1, 4, 8)

_HEADER = """\
METATRON DOCTOR
This file is safe to send. It contains timings, counts, interpreter and
machine details, and the root-relative path of the file that dominated the
work. It contains no source code, no element names and no values from the
analysed target. Nothing in the target was executed to produce it.
"""


@dataclass
class ScalingPoint:
    workers_requested: int
    workers_started: int
    seconds: float
    gain: float
    unit_count: int
    largest_unit_key: str
    largest_unit_share: float
    report_text: str

    def as_dict(self) -> dict[str, Any]:
        # Milliseconds as ints and ratios as pre-formatted strings, because
        # `canonical_dumps` refuses floats outright -- they are not
        # byte-identical across platforms, and this project keeps one
        # serialiser rather than making an exception for a timing file.
        return {
            "gain_vs_single_process": f"{self.gain:.3f}",
            "largest_unit_key": self.largest_unit_key,
            "largest_unit_share": f"{self.largest_unit_share:.4f}",
            "millis": int(round(self.seconds * 1000)),
            "unit_count": self.unit_count,
            "workers_requested": self.workers_requested,
            "workers_started": self.workers_started,
        }


@dataclass
class DoctorResult:
    target: str
    target_kind: str
    file_count: int = 0
    python_file_count: int = 0
    total_bytes: int = 0
    largest_file_bytes: int = 0
    largest_file_key: str = ""
    elements: int = 0
    unresolved: int = 0
    stage_millis: dict[str, int] = field(default_factory=dict)
    scaling: list[ScalingPoint] = field(default_factory=list)
    cache_dir: str = ""
    cache_was_warm: bool = False
    errors: list[str] = field(default_factory=list)
    machine: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "cache": {"dir": self.cache_dir, "warm_at_start": self.cache_was_warm},
            "counts": {
                "elements": self.elements,
                "files": self.file_count,
                "largest_file_bytes": self.largest_file_bytes,
                "largest_file_key": self.largest_file_key,
                "python_files": self.python_file_count,
                "total_bytes": self.total_bytes,
                "unresolved": self.unresolved,
            },
            "errors": list(self.errors),
            "machine": dict(self.machine),
            "scaling": [p.as_dict() for p in self.scaling],
            "stage_millis": dict(self.stage_millis),
            "target": {"kind": self.target_kind, "path": self.target},
        }


def machine_shape() -> dict[str, Any]:
    """Interpreter and machine. `cpu_budget` rather than `os.cpu_count`
    because a container pinned to 2 of 64 cores reports 64, and that
    difference is exactly what a worker-count question turns on."""
    return {
        "cpu_count": os.cpu_count() or 0,
        "cpu_usable": cpu_budget(),
        "machine": platform.machine(),
        "platform": platform.platform(terse=True),
        "python_build": platform.python_implementation(),
        "python_version": platform.python_version(),
        "workers_auto_would_be": resolve_workers(None),
    }


def self_target(tmp_root: Path) -> tuple[Path, str]:
    """The tool's own source, as something `analyze` can walk.

    Running as a package, that is the package directory. Running as the
    single-file build there is no package directory, so the one file is
    copied into a scratch directory — which also happens to reproduce the
    owner's own shape exactly: one large module, one unit of work.
    """
    import cascade_map

    pkg_paths = list(getattr(cascade_map, "__path__", []) or [])
    if pkg_paths:
        return Path(pkg_paths[0]), "metatron's own package"

    own = Path(getattr(sys.modules["__main__"], "__file__", "") or cascade_map.__file__)
    staged = tmp_root / "self"
    staged.mkdir(parents=True, exist_ok=True)
    shutil.copy2(own, staged / own.name)
    return staged, "metatron's own single-file build"


def _measure_tree(root: Path) -> tuple[int, int, int, int, str]:
    """File counts and sizes, read-only. Sorted so two runs on the same tree
    name the same largest file when two files tie on size."""
    from cascade_map.ingest.walker import walk

    files = sorted(walk(str(root)), key=lambda f: f.path.as_posix())
    total = 0
    py = 0
    largest = 0
    largest_key = ""
    for f in files:
        try:
            size = f.path.stat().st_size
        except OSError:
            continue
        total += size
        if f.kind == "python":
            py += 1
        if size > largest:
            largest = size
            try:
                largest_key = f.path.relative_to(root).as_posix()
            except ValueError:
                largest_key = f.path.name
    return len(files), py, total, largest, largest_key


def run_doctor(
    target: Path | None = None,
    *,
    scaling_points: tuple[int, ...] = SCALING_POINTS,
    cache_dir: Path | None = None,
) -> DoctorResult:
    """Measure. Never executes the target; `analyze` is Mode B."""
    from cascade_map.cli import analyze

    with tempfile.TemporaryDirectory(prefix="metatron-doctor-") as tmp:
        tmp_root = Path(tmp)
        if target is None:
            root, kind = self_target(tmp_root)
        else:
            root, kind = target, "owner-supplied target"

        result = DoctorResult(target=str(root), target_kind=kind, machine=machine_shape())

        if not root.is_dir():
            result.errors.append(f"not a directory: {root}")
            return result

        (
            result.file_count,
            result.python_file_count,
            result.total_bytes,
            result.largest_file_bytes,
            result.largest_file_key,
        ) = _measure_tree(root)

        # Stage timings, through the real pipeline, with a cold cache of its
        # own so the numbers describe the work and not a previous run.
        pipeline_cache = cache_dir or (tmp_root / "cache")
        result.cache_dir = str(pipeline_cache)
        result.cache_was_warm = pipeline_cache.exists() and any(pipeline_cache.iterdir())
        try:
            _, summary = analyze(
                root,
                tmp_root / "out",
                cache_dir=pipeline_cache,
                strict_gate=False,
                workers=1,
                worker_report_sink=lambda _text: None,
            )
            result.elements = int(summary.get("elements", 0))
            result.unresolved = int(summary.get("unresolved", 0))
            stages = summary.get("stage_millis") or {}
            result.stage_millis = {str(k): int(v) for k, v in sorted(stages.items())}
        except Exception as exc:  # pragma: no cover - reported, never swallowed
            result.errors.append(f"pipeline: {type(exc).__name__}: {exc}")

        # Worker scaling. Ingestion only, because ingestion is the only stage
        # that takes workers; a cold cache per point, because a warm one makes
        # every point instant and proves nothing.
        for n in scaling_points:
            scale_cache = tmp_root / f"cache-w{n}"
            ing = Ingestor(cache_dir=scale_cache, workers=n)
            started = time.perf_counter()
            try:
                ing.inventory(str(root))
            except Exception as exc:  # pragma: no cover
                result.errors.append(f"workers={n}: {type(exc).__name__}: {exc}")
                continue
            elapsed = time.perf_counter() - started
            rep = ing.worker_report
            result.scaling.append(
                ScalingPoint(
                    workers_requested=n,
                    workers_started=rep.started,
                    seconds=elapsed,
                    gain=rep.gain,
                    unit_count=rep.unit_count,
                    largest_unit_key=rep.largest_unit_key,
                    largest_unit_share=rep.largest_unit_share,
                    report_text=render_worker_report(rep),
                )
            )

    return result


def render(result: DoctorResult) -> str:
    """The .log body. Plain text, because it is read by a person."""
    lines = [_HEADER, ""]
    lines.append(f"target          {result.target}")
    lines.append(f"                ({result.target_kind})")
    lines.append(
        f"size            {result.file_count:,} file(s), "
        f"{result.python_file_count:,} python, {result.total_bytes / 1e6:.2f} MB"
    )
    if result.largest_file_key:
        lines.append(
            f"largest file    {result.largest_file_key} "
            f"({result.largest_file_bytes / 1e6:.2f} MB, "
            f"{result.largest_file_bytes / max(result.total_bytes, 1) * 100:.0f}% of the tree)"
        )
    lines.append(f"elements        {result.elements:,}")
    lines.append(f"unresolved      {result.unresolved:,}")
    lines.append("")

    lines.append("MACHINE")
    for key in sorted(result.machine):
        lines.append(f"  {key:<24} {result.machine[key]}")
    lines.append("")

    lines.append("CACHE")
    lines.append(f"  location                 {result.cache_dir}")
    lines.append(f"  warm at start            {'yes' if result.cache_was_warm else 'no (cold)'}")
    lines.append("")

    lines.append("STAGE TIMINGS (single process)")
    if result.stage_millis:
        total = sum(result.stage_millis.values())
        for name in sorted(result.stage_millis, key=lambda k: (-result.stage_millis[k], k)):
            ms = result.stage_millis[name]
            share = ms / total * 100 if total else 0.0
            lines.append(f"  {name:<24} {ms / 1000:8.2f}s  {share:5.1f}%")
        lines.append(f"  {'TOTAL':<24} {total / 1000:8.2f}s")
    else:
        lines.append("  (not measured -- see ERRORS)")
    lines.append("")

    lines.append("WORKER SCALING (ingestion only, cold cache per point)")
    if result.scaling:
        control = next((p for p in result.scaling if p.workers_requested == 1), None)
        for p in result.scaling:
            measured = (
                f"  {control.seconds / p.seconds:5.2f}x measured vs --workers 1"
                if control and p.seconds > 0
                else ""
            )
            lines.append(
                f"  --workers {p.workers_requested:<3} {p.workers_started} started  "
                f"{p.seconds:7.2f}s{measured}"
            )
        lines.append("")
        lines.append("  verdict, from the highest worker count measured:")
        for line in result.scaling[-1].report_text.splitlines():
            lines.append(f"    {line}")
    else:
        lines.append("  (not measured -- see ERRORS)")
    lines.append("")

    lines.append("ERRORS")
    if result.errors:
        for err in result.errors:
            lines.append(f"  {err}")
    else:
        lines.append("  none")
    return "\n".join(lines) + "\n"


def utc_stamp(now: float | None = None) -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now if now is not None else time.time()))


def write_report(result: DoctorResult, out_dir: Path, stamp: str | None = None) -> Path:
    """Writes `<out>/metatron_doctor_<UTC>.log` and the same data as JSON
    beside it. Returns the .log path -- the one the owner sends."""
    from cascade_map.contracts.interfaces import canonical_dumps

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or utc_stamp()
    log_path = out_dir / f"metatron_doctor_{stamp}.log"
    json_path = out_dir / f"metatron_doctor_{stamp}.json"
    log_path.write_text(render(result), encoding="utf-8")
    json_path.write_text(canonical_dumps(result.as_dict()), encoding="utf-8")
    return log_path
