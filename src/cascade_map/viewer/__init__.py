"""CASCADE-MAP viewer -- a read-only renderer over emitted artifacts.

Phase B (this module, so far): a static map over ``out/<label>/``. Phase A
adds a runtime overlay keyed onto the same element IDs, layered on top.

The viewer contains no analysis logic. Every fact it shows was produced by
another card and is read here from disk; nothing is recomputed, and nothing
here ever imports, execs or reads ``target_engine/`` / ``target_versions/``.
"""

from __future__ import annotations

from pathlib import Path

from .blueprint import render_blueprint_to_file
from .html_export import render_site
from .loader import ArtifactStore, LoadError, RuntimeStore, list_runs
from .views import (
    DECISION_SIGNALS,
    browser_view,
    cascade_view,
    callgraph_view,
    contradictions_view,
    decision_branch_view,
    decision_point_view,
    diff_view,
    doc_record_view,
    element_detail,
    element_reachability,
    element_summary,
    event_detail_view,
    findings_view,
    lineage_view,
    mapping_view,
    narrative_view,
    nondeterminism_view,
    observed_order_view,
    runtime_overview_view,
    unmapped_events_view,
    verdicts_view,
)

__all__ = [
    "ArtifactStore",
    "LoadError",
    "RuntimeStore",
    "list_runs",
    "browser_view",
    "cascade_view",
    "callgraph_view",
    "contradictions_view",
    "decision_branch_view",
    "decision_point_view",
    "diff_view",
    "doc_record_view",
    "element_detail",
    "element_reachability",
    "element_summary",
    "event_detail_view",
    "findings_view",
    "lineage_view",
    "mapping_view",
    "narrative_view",
    "nondeterminism_view",
    "observed_order_view",
    "runtime_overview_view",
    "unmapped_events_view",
    "verdicts_view",
    "DECISION_SIGNALS",
    "render_site",
    "render_to_file",
    "render_to_file_with_runtime",
    "render_blueprint_to_file",
]


def render_to_file(
    root: str | Path, out_path: str | Path, run_id: str | None = None
) -> ArtifactStore:
    """Load the artifacts at *root* and write the static HTML page to
    *out_path*. Returns the loaded :class:`ArtifactStore` so a caller (e.g.
    card 10) can inspect ``available`` / ``errors`` without reloading -- this
    return shape is unchanged from phase B.

    *run_id* is phase A: when given, the runtime overlay for that run
    (``runtime/<run_id>/*``) is loaded (via :func:`render_to_file_with_runtime`
    for callers that also need the loaded :class:`RuntimeStore`) and layered
    onto the page. Multiple run directories may exist under
    ``root/runtime/`` (see :func:`list_runs`); picking one is the caller's
    job, not this function's -- there is no reliable, non-clock signal in the
    contract for "the latest" run.
    """
    store, _rstore = render_to_file_with_runtime(root, out_path, run_id)
    return store


def render_to_file_with_runtime(
    root: str | Path, out_path: str | Path, run_id: str | None = None
) -> tuple[ArtifactStore, RuntimeStore | None]:
    """Same as :func:`render_to_file`, but also returns the loaded
    :class:`RuntimeStore` (``None`` when *run_id* is not given)."""
    store = ArtifactStore.load(root)
    rstore = RuntimeStore.load(root, run_id) if run_id is not None else None
    html = render_site(store, rstore)
    Path(out_path).write_text(html, encoding="utf-8")
    return store, rstore
