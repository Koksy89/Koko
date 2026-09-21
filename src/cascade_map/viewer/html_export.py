"""A single, offline, static HTML page over a loaded :class:`ArtifactStore`.

No CDN fetches, no telemetry, no JavaScript that reaches outside the page --
everything needed to browse the map is inlined. This is the phase B viewer's
only rendering target; phase A adds a runtime overlay to the same page later.

Determinism: every list here is sorted before it is written, and nothing in
this module reads the clock, the environment or the filesystem outside the
artifact root already loaded into the store. Two runs over the same store
produce byte-identical HTML.

Linking discipline: an ``<a href="#el-ID">`` is only ever emitted for an ID
that has a real ``id="el-ID"`` anchor -- i.e. an ID present in
``elements.jsonl``, which is the only artifact this page renders one detail
section per record for. Every other kind of ID this page mentions (order
nodes, decisions, findings, changes, slices, barriers, lineage edges,
unresolved records, intents -- none of which are elements) is rendered as
plain, still-visible ``<code>`` text rather than a dangling link. See
:func:`_ref`.
"""

from __future__ import annotations

from html import escape
from typing import Any, Iterable

from . import views
from .loader import ArtifactStore, RuntimeStore, list_runs

_STYLE = """
body { font-family: -apple-system, sans-serif; margin: 0; padding: 0; color: #1a1a1a; }
header { background: #16233b; color: #fff; padding: 1rem 1.5rem; }
header h1 { margin: 0; font-size: 1.25rem; }
nav { padding: 0.5rem 1.5rem; background: #eef1f6; position: sticky; top: 0; }
nav a { margin-right: 1rem; }
main { padding: 1rem 1.5rem; }
section { margin-bottom: 2.5rem; }
table { border-collapse: collapse; width: 100%; margin-bottom: 1rem; font-size: 0.85rem; }
th, td { border: 1px solid #ccc; padding: 0.3rem 0.5rem; text-align: left; vertical-align: top; }
th { background: #f4f4f4; }
.badge { display: inline-block; padding: 0 0.4rem; border-radius: 3px; font-size: 0.75rem; }
.conf-CERTAIN { background: #cdebd4; }
.conf-RESOLVED { background: #dcedc8; }
.conf-PROBABLE { background: #fff3cd; }
.conf-HEURISTIC { background: #ffe0b2; }
.conf-UNKNOWN { background: #f5c6cb; }
.reach-REACHES_SINK { background: #cdebd4; }
.reach-NO_SINK_PATH { background: #d9edf7; }
.reach-UNKNOWN { background: #f5c6cb; }
.reach-fallback { border: 1px dashed #a94442; }
.model-prose { background: #fff8e1; border-left: 4px solid #f0ad4e; padding: 0.5rem; }
.model-prose::before { content: "MODEL-WRITTEN, not a fact: "; font-weight: bold; }
.facts { background: #f4f8fb; border-left: 4px solid #2e6da4; padding: 0.5rem; }
.order-UNORDERED { border-left: 4px solid #999; }
.order-BRANCH { border-left: 4px solid #2e6da4; }
.order-MERGE { border-left: 4px solid #5bc0de; }
.order-LOOP { border-left: 4px solid #f0ad4e; }
.order-CYCLE { border-left: 4px solid #d9534f; }
.order-SEQUENCE { border-left: 4px solid #5cb85c; }
li.order-node { margin: 0.25rem 0; padding-left: 0.5rem; }
.missing { color: #a94442; font-style: italic; }
.element-detail { border: 1px solid #ccc; padding: 0.75rem; margin-bottom: 1rem; }
.runtime-section { border-top: 4px double #6a3fa0; margin-top: 2.5rem; padding-top: 1rem; }
.run-tag { display: inline-block; background: #efe3fb; color: #5b2c86; border: 1px solid #8a5cc7;
  font-size: 0.7rem; padding: 0 0.4rem; border-radius: 3px; margin-left: 0.35rem; }
.runtime-evidence { background: #f7f1fc; }
.status-FULL { background: #cdebd4; }
.status-SUMMARIZED { background: #fff3cd; }
.status-REDACTED { background: #ffe0b2; }
.status-DROPPED { background: #f5c6cb; }
.verdict-ALIGNED { background: #cdebd4; }
.verdict-MISALIGNED { background: #f5c6cb; }
.verdict-NOT_EXERCISED { background: #d9edf7; border: 1px dashed #2e6da4; }
.verdict-UNVERIFIABLE { background: #eee; }
.verdict-NO_INTENT { background: #eee; }
.contradiction-row { background: #fdf2f2; }
.contradiction-row td { border-color: #d9534f; }
.unguaranteed-block { border: 2px solid #d9534f; background: #fff5f5; padding: 0.75rem 1rem;
  margin-bottom: 1rem; }
"""


def _ref(store: ArtifactStore, id_: str | None, label: str | None = None) -> str:
    """Render one ID.

    A drill-down link (``#el-ID``) when *id_* is a real element -- every
    element gets a detail section, so the link always resolves. Plain
    ``<code>`` text otherwise: the ID is still shown, never dropped, but not
    offered as a link with nothing at the other end.
    """
    if not id_:
        return ""
    if id_ in store.elements_by_id:
        text = escape(label or id_)
        return f'<a href="#el-{escape(id_)}">{text}</a>'
    return f"<code>{escape(label or id_)}</code>"


def _refs(store: ArtifactStore, ids: Iterable[str]) -> str:
    return ", ".join(_ref(store, i) for i in sorted(set(ids)) if i) or "&mdash;"


def _badge(confidence: str | None) -> str:
    if not confidence:
        return ""
    return f'<span class="badge conf-{escape(confidence)}">{escape(confidence)}</span>'


def _reach_badge(info: dict[str, Any]) -> str:
    state = info.get("state") or "UNKNOWN"
    source = info.get("source") or ""
    is_fallback = source.startswith("fallback:")
    cls = f"badge reach-{escape(state)}" + (" reach-fallback" if is_fallback else "")
    label = escape(state) + (" (approximated)" if is_fallback else "")
    title = escape(info.get("reason") or "")
    return f'<span class="{cls}" title="{title}">{label}</span>'


def _render_available(store: ArtifactStore) -> str:
    rows = []
    for name in sorted(store.available):
        present = store.available[name]
        mark = "present" if present else "missing -- view degrades"
        rows.append(f"<tr><td>{escape(name)}.jsonl</td><td>{mark}</td></tr>")
    errors = "".join(
        f"<li>{escape(e.file)}:{e.line_number}: {escape(e.reason)}</li>" for e in store.errors
    )
    error_block = f"<p class='missing'>Load errors:</p><ul>{errors}</ul>" if errors else ""
    return f"<table><tr><th>artifact</th><th>status</th></tr>{''.join(rows)}</table>{error_block}"


def _render_browser(store: ArtifactStore) -> str:
    rows = []
    for e in views.browser_view(store):
        reach = views.element_reachability(store, e["id"])
        rows.append(
            "<tr>"
            f"<td>{_ref(store, e['id'])}</td>"
            f"<td>{escape(e['kind'] or '')}</td>"
            f"<td>{escape(e['module'] or '')}</td>"
            f"<td>{escape(e['qualname'] or e['name'] or '')}</td>"
            f"<td>{_badge(e['confidence'])}</td>"
            f"<td>{escape(e['method'] or '')}</td>"
            f"<td>{_reach_badge(reach)}</td>"
            "</tr>"
        )
    return (
        "<table><tr><th>id</th><th>kind</th><th>module</th><th>qualname</th>"
        f"<th>confidence</th><th>method</th><th>decision reachability</th></tr>"
        f"{''.join(rows)}</table>"
    )


def _render_order_node(store: ArtifactStore, node: dict[str, Any]) -> str:
    if node.get("missing"):
        return f"<li class='order-node missing'>{escape(node['id'])} (order node not found)</li>"
    kind = node.get("kind") or "SEQUENCE"
    members = _refs(store, node.get("element_ids") or ())
    # Decision IDs are DecisionPoint ids, not element ids -- shown as plain
    # text via _refs (which falls back to <code> for anything that is not a
    # known element), never as a link with no anchor behind it.
    decisions = _refs(store, node.get("decision_ids") or ())
    if node.get("cycle_back_reference"):
        # node["id"] here is an OrderNode id, not an element -- _refs
        # correctly renders it as plain <code> text.
        body = f"back-reference to {_refs(store, [node['id']])} (cycle closes here)"
    else:
        children = "".join(
            f"<ul>{_render_order_node(store, c)}</ul>" for c in node.get("children") or ()
        )
        body = (
            f"members: {members}"
            + (f" | decisions: {decisions}" if node.get("decision_ids") else "")
            + children
        )
    return (
        f"<li class='order-node order-{escape(kind)}'><b>{escape(kind)}</b> "
        f"({escape(node['id'])}) &mdash; {body}</li>"
    )


def _render_cascade(store: ArtifactStore) -> str:
    tree = views.cascade_view(store)
    if not tree:
        return "<p class='missing'>order.jsonl not available or empty.</p>"
    return "<ul>" + "".join(_render_order_node(store, n) for n in tree) + "</ul>"


def _render_callgraph(store: ArtifactStore) -> str:
    cg = views.callgraph_view(store)
    edge_rows = "".join(
        "<tr>"
        f"<td>{_ref(store, e['source_id'])}</td><td>{escape(e['kind'] or '')}</td>"
        f"<td>{_ref(store, e['target_id'])}</td>"
        f"<td>{escape(e['method'] or '')}</td><td>{_badge(e['confidence'])}</td>"
        "</tr>"
        for e in cg["edges"]
    )
    unresolved_rows = "".join(
        "<tr>"
        f"<td>{escape(u['id'] or '')}</td><td>{escape(u['reason'] or '')}</td>"
        f"<td>{escape((u['span'] or {}).get('path', ''))}:{(u['span'] or {}).get('line', '')}</td>"
        f"<td>{escape(u['description'] or '')}</td><td>{_refs(store, u['candidate_ids'])}</td>"
        f"<td>{_badge(u['candidate_confidence'])}</td>"
        "</tr>"
        for u in cg["unresolved"]
    )
    return (
        "<h3>Edges</h3>"
        "<table><tr><th>source</th><th>kind</th><th>target</th><th>method</th><th>confidence</th></tr>"
        f"{edge_rows}</table>"
        "<h3>Unresolved call sites</h3>"
        "<table><tr><th>id</th><th>reason</th><th>location</th><th>description</th>"
        f"<th>candidates</th><th>candidate confidence</th></tr>{unresolved_rows}</table>"
    )


def _render_findings(store: ArtifactStore) -> str:
    rows = []
    for f in views.findings_view(store):
        rows.append(
            "<tr>"
            f"<td>{escape(f['id'] or '')}</td><td>{escape(f['kind'] or '')}</td>"
            f"<td>{_ref(store, f['element_id'])}</td><td>{escape(f['summary'] or '')}</td>"
            f"<td>{escape(f['hint'] or '')}</td><td>{_refs(store, f['evidence_ids'])}</td>"
            f"<td>{_badge(f['confidence'])}</td>"
            "</tr>"
        )
    return (
        "<table><tr><th>id</th><th>kind</th><th>element</th><th>summary</th><th>hint</th>"
        f"<th>evidence</th><th>confidence</th></tr>{''.join(rows)}</table>"
    )


def _render_diff(store: ArtifactStore) -> str:
    rows = []
    for r in views.diff_view(store):
        rank = "&mdash;" if r["rank"] is None else str(r["rank"])
        rows.append(
            "<tr>"
            f"<td>{escape(rank)}</td><td>{escape(r['change_kind'] or '')}</td>"
            f"<td>{_ref(store, r['before_id'])}</td><td>{_ref(store, r['after_id'])}</td>"
            f"<td>{'yes' if r['decision_paths_changed'] else 'no'}</td>"
            f"<td>{_refs(store, r['affected_ids'])}</td>"
            f"<td>{_refs(store, r['reachability_flipped'])}</td>"
            f"<td>{escape(r.get('note', ''))}</td>"
            "</tr>"
        )
    return (
        "<table><tr><th>rank</th><th>kind</th><th>before</th><th>after</th>"
        "<th>decision paths changed</th><th>affected</th><th>reachability flipped</th>"
        f"<th>note</th></tr>{''.join(rows)}</table>"
    )


def _render_doc_record(store: ArtifactStore, record: dict[str, Any] | None) -> str:
    if record is None:
        return "<p class='missing'>No documentation record (records.jsonl not available, "\
            "or the completeness gate has not run).</p>"
    facts_rows = "".join(
        f"<tr><th>{escape(k)}</th><td><pre>{escape(str(v))}</pre></td></tr>"
        for k, v in sorted(record["facts"].items())
    )
    model = record["model_authored"]
    model_block = (
        f"<div class='model-prose'>{escape(model['prose'])}<br>"
        f"<small>model: {escape(model['model_id'] or 'unknown')}</small></div>"
        if model["is_present"]
        else "<p class='missing'>No model-written prose (enrichment did not run for this element).</p>"
    )
    return (
        f"<div class='facts'><table>{facts_rows}</table>"
        f"<p>findings: {_refs(store, record['finding_ids'])} | "
        f"changes: {_refs(store, record['change_ids'])} | "
        f"confidence: {_badge(record['confidence'])}</p></div>"
        f"{model_block}"
    )


def _render_element_detail(store: ArtifactStore, element_id: str) -> str:
    d = views.element_detail(store, element_id)
    el = d["element"]
    if el is None:
        header = f'<h3 id="el-{escape(element_id)}">{escape(element_id)} <span class="missing">(no Element record)</span></h3>'
    else:
        header = (
            f'<h3 id="el-{escape(element_id)}">{escape(el["qualname"] or el["name"] or element_id)}'
            f" <small>{escape(el['kind'] or '')}</small> {_badge(el['confidence'])}"
            f" {_reach_badge(d['reachability'])}</h3>"
            f"<p>module: {escape(el['module'] or '')} | id: <code>{escape(element_id)}</code></p>"
        )
    body = (
        f"<p>reachability: {_reach_badge(d['reachability'])} "
        f"<small>({escape(d['reachability'].get('source', ''))})</small><br>"
        f"outgoing edges: {_refs(store, (e['target_id'] for e in d['outgoing_edges']))}<br>"
        f"incoming edges: {_refs(store, (e['source_id'] for e in d['incoming_edges']))}<br>"
        f"unresolved candidate for: {_refs(store, (u['id'] for u in d['unresolved_as_candidate']))}<br>"
        f"order nodes: {_refs(store, d['order_node_ids'])}<br>"
        f"decision (as condition owner): {_refs(store, d['decision_as_condition'])}<br>"
        f"decision (reads this): {_refs(store, d['decision_reads_this'])}<br>"
        f"lineage out: {_refs(store, (e['id'] for e in d['lineage_out'] if e.get('id')))}<br>"
        f"lineage in: {_refs(store, (e['id'] for e in d['lineage_in'] if e.get('id')))}<br>"
        f"barriers: {_refs(store, d['barrier_ids'])}<br>"
        f"backward slice: {_refs(store, d['slice_ids_rooted_here'].get('backward', []))}<br>"
        f"forward slice: {_refs(store, d['slice_ids_rooted_here'].get('forward', []))}<br>"
        f"member of slices: {_refs(store, d['slice_ids_as_member'])}<br>"
        f"findings: {_refs(store, d['finding_ids'])}<br>"
        f"findings (as evidence): {_refs(store, d['finding_ids_as_evidence'])}<br>"
        f"changes (before): {_refs(store, d['change_ids_before'])}<br>"
        f"changes (after): {_refs(store, d['change_ids_after'])}<br>"
        f"intents: {_refs(store, d['intent_ids'])}</p>"
        f"{_render_doc_record(store, d['doc_record'])}"
    )
    return f"<div class='element-detail'>{header}{body}</div>"


def render_site(store: ArtifactStore) -> str:
    """Render the whole offline HTML page for one artifact root."""
    element_ids = sorted(store.elements_by_id)
    manifest = store.manifest
    manifest_line = (
        f"schema {escape(str(manifest.get('schema_version', 'unknown')))} | "
        f"tool {escape(str(manifest.get('tool_version', 'unknown')))}"
        if manifest
        else "no manifest.json found"
    )
    sections = f"""
<header><h1>CASCADE-MAP &mdash; static view</h1><p>{manifest_line}</p></header>
<nav>
<a href="#browser">Elements</a>
<a href="#cascade">Cascade order</a>
<a href="#callgraph">Call graph</a>
<a href="#findings">Findings</a>
<a href="#diff">Version diff</a>
<a href="#details">Element detail</a>
<a href="#availability">Artifact status</a>
</nav>
<main>
<section id="availability"><h2>Artifact status</h2>{_render_available(store)}</section>
<section id="browser"><h2>Element browser</h2>{_render_browser(store)}</section>
<section id="cascade"><h2>Cascade order</h2>{_render_cascade(store)}</section>
<section id="callgraph"><h2>Call graph</h2>{_render_callgraph(store)}</section>
<section id="findings"><h2>Findings</h2>{_render_findings(store)}</section>
<section id="diff"><h2>Version diff, ranked by decision impact</h2>{_render_diff(store)}</section>
<section id="details"><h2>Element detail</h2>
{''.join(_render_element_detail(store, eid) for eid in element_ids)}
</section>
</main>
"""
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>CASCADE-MAP</title>"
        f"<style>{_STYLE}</style></head><body>{sections}</body></html>"
    )
