"""CASCADE-MAP viewer -- a read-only renderer over emitted artifacts.

Phase B (this module, so far): a static map over ``out/<label>/``. Phase A
adds a runtime overlay keyed onto the same element IDs, layered on top.

The viewer contains no analysis logic. Every fact it shows was produced by
another card and is read here from disk; nothing is recomputed, and nothing
here ever imports, execs or reads ``target_engine/`` / ``target_versions/``.
"""

from __future__ import annotations

from pathlib import Path

from .html_export import render_site
from .loader import ArtifactStore, LoadError
from .views import (
    DECISION_SIGNALS,
    browser_view,
    cascade_view,
    callgraph_view,
    decision_point_view,
    diff_view,
    doc_record_view,
    element_detail,
    element_summary,
    findings_view,
    lineage_view,
)

__all__ = [
    "ArtifactStore",
    "LoadError",
    "browser_view",
    "cascade_view",
    "callgraph_view",
    "decision_point_view",
    "diff_view",
    "doc_record_view",
    "element_detail",
    "element_summary",
    "findings_view",
    "lineage_view",
    "DECISION_SIGNALS",
    "render_site",
    "render_to_file",
]


def render_to_file(root: str | Path, out_path: str | Path) -> ArtifactStore:
    """Load the artifacts at *root* and write the static HTML page to
    *out_path*. Returns the loaded store so a caller (e.g. card 10) can
    inspect ``available`` / ``errors`` without reloading."""
    store = ArtifactStore.load(root)
    html = render_site(store)
    Path(out_path).write_text(html, encoding="utf-8")
    return store
