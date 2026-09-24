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
.scenario-failure { border: 3px solid #a94442; background: #fdeaea; padding: 0.75rem 1rem;
  margin-bottom: 1rem; }
.scenario-failure pre { white-space: pre-wrap; word-break: break-word; background: #fff;
  border: 1px solid #ccc; padding: 0.5rem; max-height: 20rem; overflow: auto; }
.observer-failure { border: 3px dashed #31708f; background: #eef6fb; padding: 0.75rem 1rem;
  margin-bottom: 1rem; }
.observer-failure pre { white-space: pre-wrap; word-break: break-word; background: #fff;
  border: 1px solid #ccc; padding: 0.5rem; max-height: 20rem; overflow: auto; }
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


# ---------------------------------------------------------------------------
# Phase A -- runtime overlay rendering.
#
# Every element in this section carries a run-tag and the "runtime-evidence"
# class so it is visually distinct from static evidence at all times, per
# the phase A contract. Nothing here merges a runtime fact into a static
# one; contradictions are rendered as two readings side by side (see
# _render_contradictions), never as a silent overwrite.
# ---------------------------------------------------------------------------


def _run_tag(run_id: str | None) -> str:
    run_id = run_id or "unknown-run"
    return f'<span class="run-tag" title="RUNTIME_OBSERVED">run: {escape(run_id)}</span>'


def _event_ref(rstore: RuntimeStore, event_id: str | None) -> str:
    if not event_id:
        return ""
    if event_id in rstore.events_by_id:
        return f'<a href="#evt-{escape(event_id)}"><code>{escape(event_id)}</code></a>'
    return f"<code>{escape(event_id)}</code>"


def _event_refs(rstore: RuntimeStore, ids: Iterable[str]) -> str:
    return ", ".join(_event_ref(rstore, i) for i in sorted(set(ids)) if i) or "&mdash;"


def _status_badge(status: str | None, original_size: int) -> str:
    if not status:
        return ""
    label = escape(status)
    if status != "FULL":
        label += " (not the complete value)"
        if original_size:
            label += f", original size {original_size}"
    return f'<span class="badge status-{escape(status)}">{label}</span>'


def _verdict_badge(verdict: str | None) -> str:
    if not verdict:
        return ""
    return f'<span class="badge verdict-{escape(verdict)}">{escape(verdict)}</span>'


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
        f"{_render_intents(store, d)}"
        f"{_render_doc_record(store, d['doc_record'])}"
    )
    return f"<div class='element-detail'>{header}{body}</div>"


def _render_intents(store: ArtifactStore, detail: dict[str, Any]) -> str:
    """The element's declared purpose and the verdicts on it, side by side.

    A verdict shown without the statement it was judged against is a label
    the reader cannot check, and card 13's whole rule is that every verdict
    is checkable by a human.
    """
    intents = detail.get("intents") or []
    verdicts = detail.get("static_verdicts") or []
    if not intents and not verdicts:
        return ""
    parts = ["<h4>intent</h4>"]
    for intent in intents:
        status = escape(str(intent.get("status") or ""))
        note = (
            "owner-confirmed"
            if status == "CONFIRMED"
            else "derived by this tool, NOT owner-confirmed and binding on nothing"
        )
        parts.append(
            f"<p class='intent-{status}'><strong>{status}</strong> &mdash; {note}<br>"
            f"{escape(str(intent.get('statement') or ''))}"
        )
        for label, key in (
            ("invariant", "invariants"),
            ("expected read", "expected_reads"),
            ("expected write", "expected_writes"),
        ):
            for text in intent.get(key) or ():
                parts.append(f"<br><code>{label}: {escape(str(text))}</code>")
        parts.append("</p>")
    for verdict in verdicts:
        parts.append(
            "<p>"
            + _verdict_badge(verdict.get("verdict"))
            + " <em>from static evidence</em><br>"
            + f"expected: {escape(str(verdict.get('expectation') or ''))}<br>"
            + f"observed: {escape(str(verdict.get('observation') or ''))}<br>"
            + f"evidence: {_refs(store, verdict.get('evidence_ids') or ())}"
            + "</p>"
        )
    return "".join(parts)


def _render_span(span: dict[str, Any] | None) -> str:
    if not span:
        return "&mdash;"
    path = escape(str(span.get("path", "")))
    line = span.get("line", "")
    return f"{path}:{line}"


def _render_scenario_failure(sf: dict[str, Any] | None) -> str:
    """`ScenarioFailure`, rendered first and unmissable. A scenario that
    raised means the run happened but did not do what was asked -- the
    exact shape of lie this tool told once already (524 events, 0 mapped,
    exit 0, from a scenario that pointed at the wrong target_root and never
    ran a line of the target's own code). The traceback is shown verbatim,
    including the harness's own truncation note when it applies, so a
    cut-off traceback never reads as a complete one."""
    if sf is None:
        return ""
    traceback_text = sf["traceback"] or "(no traceback captured)"
    return (
        "<div class='scenario-failure'>"
        f"<h4>This run's scenario did not complete -- {escape(sf['stage'])} stage</h4>"
        f"<p>{escape(sf['explanation'])}</p>"
        f"<p>exception: <code>{escape(sf['exception_type'])}</code>"
        f"{' -- ' + escape(sf['message']) if sf['message'] else ''}</p>"
        f"<pre>{escape(traceback_text)}</pre>"
        "<p><b>The event counts below describe a run in which this happened.</b> "
        "They are real, observed events -- not evidence the target's own logic ran "
        "as intended, and not suppressed.</p>"
        "</div>"
    )


def _render_observer_failure(of: dict[str, Any] | None) -> str:
    """`observer_failure` -- the opposite finding from `scenario_failure` and
    styled deliberately unlike it (`observer-failure`, not `scenario-failure`):
    this one says the tool may have missed the run, not that the target
    misbehaved. Conflating the two sends an owner to the wrong codebase, which
    is the entire reason they are rendered as two distinct blocks rather than
    one message."""
    if of is None:
        return ""
    traceback_text = of["traceback"] or "(no traceback captured)"
    return (
        "<div class='observer-failure'>"
        f"<h4>The observer itself failed -- {escape(of['stage'])} stage "
        "(this is a tool bug, not a finding about the target)</h4>"
        f"<p>{escape(of['explanation'])}</p>"
        f"<p>exception: <code>{escape(of['exception_type'])}</code>"
        f"{' -- ' + escape(of['message']) if of['message'] else ''}</p>"
        f"<pre>{escape(traceback_text)}</pre>"
        "</div>"
    )


def _render_run_overview(store: ArtifactStore, rstore: RuntimeStore) -> str:
    overview = views.runtime_overview_view(rstore)
    if not overview["available"]:
        return "<p class='missing'>run.json not available for this run -- run overview cannot be shown.</p>"
    failure_block = (
        _render_observer_failure(overview.get("observer_failure"))
        + _render_scenario_failure(overview.get("scenario_failure"))
    )
    unguaranteed_rows = "".join(
        f"<li>{escape(u)}</li>" for u in overview["unguaranteed"]
    ) or "<li>none disclosed by this run record</li>"
    blocked_rows = "".join(
        "<tr>"
        f"<td>{escape(b.get('id', ''))}</td><td>{escape(b.get('kind', ''))}</td>"
        f"<td>{escape(b.get('detail', ''))}</td><td>{_ref(store, b.get('element_id'))}</td>"
        f"<td>{_event_ref(rstore, b.get('event_id'))}</td>"
        "</tr>"
        for b in overview["blocked"]
    ) or "<tr><td colspan='5'>none recorded</td></tr>"
    refusal = (
        f"<p class='missing'><b>This run refused to start:</b> {escape(overview['refusal_reason'])}</p>"
        if overview["refused"]
        else ""
    )
    controls_rows = "".join(
        f"<tr><td>{escape(k)}</td><td>{'active' if v else 'NOT active'}</td></tr>"
        for k, v in sorted(overview["controls_active"].items())
    )
    return (
        f"{failure_block}"
        f"<div class='unguaranteed-block runtime-evidence'>{_run_tag(overview['run_id'])}"
        f"{refusal}"
        "<h4>Escape paths this harness could not close (read this first)</h4>"
        f"<ul>{unguaranteed_rows}</ul>"
        "<h4>Blocked attempts</h4>"
        "<table><tr><th>id</th><th>kind</th><th>detail</th><th>element</th><th>event</th></tr>"
        f"{blocked_rows}</table></div>"
        f"<p class='runtime-evidence'>scenario: {escape(overview['scenario'])} | "
        f"interpreter: {escape(overview['interpreter'])} | "
        f"sandbox: {escape(overview['sandbox_dir'])} | "
        f"graph_hash: {escape(overview['graph_hash'])}</p>"
        f"<table class='runtime-evidence'><tr><th>control</th><th>status</th></tr>{controls_rows}</table>"
    )


def _render_mapping(rstore: RuntimeStore) -> str:
    m = views.mapping_view(rstore)
    if not m["available"]:
        return "<p class='missing'>mapping.json not available -- mapping rate cannot be shown.</p>"
    rate = m["mapping_rate"]
    rate_text = "n/a (no events)" if rate is None else f"{rate:.4f}"
    reasons = "".join(
        f"<tr><td>{escape(k)}</td><td>{v}</td></tr>"
        for k, v in sorted(m["unmapped_by_reason"].items())
    ) or "<tr><td colspan='2'>none</td></tr>"
    return (
        f"<p class='runtime-evidence'>{_run_tag(m['run_id'])} "
        f"mapped {m['mapped_events']} / {m['total_events']} events "
        f"(rate {rate_text}); unmapped: {m['unmapped_events']}</p>"
        "<table class='runtime-evidence'><tr><th>unmapped reason</th><th>count</th></tr>"
        f"{reasons}</table>"
    )


def _render_observed_order(store: ArtifactStore, rstore: RuntimeStore) -> str:
    rows = "".join(
        "<tr class='runtime-evidence'>"
        f"<td>{e['sequence']}</td><td>{_event_ref(rstore, e['event_id'])}</td>"
        f"<td>{escape(e['kind'] or '')}</td><td>{_ref(store, e['element_id'])}</td>"
        f"<td>{e['depth']}</td><td>{escape(e['branch_taken'] or '')}</td>"
        f"<td>{_run_tag(e['run_id'])}</td>"
        "</tr>"
        for e in views.observed_order_view(rstore)
    )
    return (
        "<table><tr><th>sequence</th><th>event</th><th>kind</th><th>element</th>"
        f"<th>depth</th><th>branch taken</th><th>run</th></tr>{rows}</table>"
    )


def _render_events(store: ArtifactStore, rstore: RuntimeStore) -> str:
    rows = []
    for summary in views.observed_order_view(rstore):
        detail = views.event_detail_view(rstore, summary["event_id"])
        assert detail is not None
        value_rows = "".join(
            "<tr>"
            f"<td>{escape(name)}</td><td>{_status_badge(v['status'], v['original_size'])}</td>"
            f"<td><code>{escape(v['repr_text'])}</code></td><td>{escape(v['type_name'])}</td>"
            f"<td>{escape(v['shape'])}</td><td>{escape(v['reason'])}</td>"
            "</tr>"
            for name, v in sorted(detail["values"].items())
        ) or "<tr><td colspan='6'>no captured values</td></tr>"
        rows.append(
            f'<div class="element-detail runtime-evidence" id="evt-{escape(detail["event_id"])}">'
            f"<p><b>{escape(detail['event_id'])}</b> {_run_tag(detail['run_id'])} "
            f"kind: {escape(detail['kind'] or '')} | element: {_ref(store, detail['element_id'])} | "
            f"sequence: {detail['sequence']} | depth: {detail['depth']} | "
            f"caller: {_event_ref(rstore, detail['caller_event_id'])}"
            f"{' | branch taken: ' + escape(detail['branch_taken']) if detail['branch_taken'] else ''}"
            f"{' | note: ' + escape(detail['note']) if detail['note'] else ''}</p>"
            "<table><tr><th>name</th><th>status</th><th>repr</th><th>type</th>"
            f"<th>shape</th><th>reason</th></tr>{value_rows}</table></div>"
        )
    return "".join(rows) or "<p class='missing'>no events in this run.</p>"


def _render_unmapped(rstore: RuntimeStore) -> str:
    rows = "".join(
        "<tr class='runtime-evidence'>"
        f"<td>{_event_ref(rstore, u['event_id'])}</td><td>{escape(u['kind'] or '')}</td>"
        f"<td>{_render_span(u['location'])}</td><td>{escape(u['reason'] or '')}</td>"
        f"<td>{_run_tag(u['run_id'])}</td>"
        "</tr>"
        for u in views.unmapped_events_view(rstore)
    )
    if not rows:
        return "<p>no UNMAPPED events in this run.</p>"
    return (
        "<table><tr><th>event</th><th>kind</th><th>location</th><th>reason</th>"
        f"<th>run</th></tr>{rows}</table>"
    )


def _render_decision_branches(store: ArtifactStore, rstore: RuntimeStore) -> str:
    rows = []
    for decision_id in sorted(store.decisions_by_id):
        dv = views.decision_branch_view(store, rstore, decision_id)
        if dv is None:
            continue
        declared = ", ".join(f"{escape(label)} -> {escape(target)}" for label, target in dv["declared_outcomes"])
        observed = "".join(
            f"<li>{escape(o['branch_taken'])} (event {_event_ref(rstore, o['event_id'])}) {_run_tag(o['run_id'])}</li>"
            for o in dv["observed"]
        ) or "<li>not observed in this run</li>"
        not_observed = ", ".join(escape(l) for l in dv["not_observed_labels"]) or "&mdash;"
        rows.append(
            "<div class='element-detail runtime-evidence'>"
            f"<p><b>{escape(decision_id)}</b> ({_ref(store, dv['element_id'])})<br>"
            f"declared outcomes: {declared}<br>"
            f"observed: <ul>{observed}</ul>"
            f"declared but not observed: {not_observed}</p></div>"
        )
    return "".join(rows) or "<p>no decisions.jsonl available.</p>"


def _render_contradictions(store: ArtifactStore, rstore: RuntimeStore) -> str:
    rows = "".join(
        "<tr class='contradiction-row'>"
        f"<td>{escape(c['id'] or '')}</td><td>{_ref(store, c['element_id'])}</td>"
        f"<td>{escape(c['claim'] or '')}</td><td>{escape(c['observation'] or '')}</td>"
        f"<td>{_refs(store, c['static_evidence_ids'])}</td>"
        f"<td>{_event_refs(rstore, c['event_ids'])}</td><td>{_run_tag(c['run_id'])}</td>"
        "</tr>"
        for c in views.contradictions_view(rstore)
    )
    if not rows:
        return "<p>no contradictions recorded for this run.</p>"
    return (
        "<table><tr><th>id</th><th>element</th><th>static claim</th>"
        "<th>runtime observation</th><th>static evidence</th><th>events</th>"
        f"<th>run</th></tr>{rows}</table>"
    )


def _render_nondeterminism(store: ArtifactStore, rstore: RuntimeStore) -> str:
    rows = "".join(
        "<tr class='runtime-evidence'>"
        f"<td>{escape(n['id'] or '')}</td><td>{_ref(store, n['element_id'])}</td>"
        f"<td>{escape(n['kind'] or '')}</td><td>{escape(n['detail'] or '')}</td>"
        f"<td>{_event_refs(rstore, n['event_ids'])}</td><td>{_run_tag(n['run_id'])}</td>"
        "</tr>"
        for n in views.nondeterminism_view(rstore)
    )
    if not rows:
        return "<p>no nondeterminism observed in this run.</p>"
    return (
        "<table><tr><th>id</th><th>element</th><th>kind</th><th>detail</th>"
        f"<th>events</th><th>run</th></tr>{rows}</table>"
    )


def _render_verdicts(store: ArtifactStore, rstore: RuntimeStore) -> str:
    rows = "".join(
        "<tr class='runtime-evidence'>"
        f"<td>{escape(v['id'] or '')}</td><td>{_ref(store, v['element_id'])}</td>"
        f"<td>{escape(v['intent_id'] or '')}</td><td>{_verdict_badge(v['verdict'])}</td>"
        f"<td>{escape(v['expectation'] or '')}</td><td>{escape(v['observation'] or '')}</td>"
        f"<td>{_event_refs(rstore, v['event_ids'])}</td><td>{_run_tag(v['run_id'])}</td>"
        "</tr>"
        for v in views.verdicts_view(rstore)
    )
    if not rows:
        return "<p>no alignment verdicts recorded for this run.</p>"
    return (
        "<table><tr><th>id</th><th>element</th><th>intent</th><th>verdict</th>"
        "<th>expectation</th><th>observation</th><th>events</th>"
        f"<th>run</th></tr>{rows}</table>"
    )


def _render_narrative(store: ArtifactStore, rstore: RuntimeStore) -> str:
    items = []
    for s in views.narrative_view(rstore):
        model = (
            f"<div class='model-prose'>{escape(s['model_prose'])}<br>"
            f"<small>model: {escape(s['model_id'] or 'unknown')}</small></div>"
            if s["model_prose"]
            else ""
        )
        items.append(
            "<li class='runtime-evidence'>"
            f"<b>[{escape(s['phase'])}]</b> {escape(s['text'])} {_run_tag(s['run_id'])}<br>"
            f"elements: {_refs(store, s['element_ids'])} | events: {_event_refs(rstore, s['event_ids'])}"
            f"{model}</li>"
        )
    if not items:
        return "<p>no narrative.jsonl available for this run.</p>"
    return f"<ol>{''.join(items)}</ol>"


def _render_runtime_section(store: ArtifactStore, rstore: RuntimeStore) -> str:
    other_runs = [r for r in list_runs(store.root) if r != rstore.run_id]
    other_runs_note = (
        f"<p>other runs available but not shown here: {', '.join(escape(r) for r in other_runs)}</p>"
        if other_runs
        else ""
    )
    errors = "".join(
        f"<li>{escape(e.file)}:{e.line_number}: {escape(e.reason)}</li>" for e in rstore.errors
    )
    error_block = f"<p class='missing'>Load errors:</p><ul>{errors}</ul>" if errors else ""
    avail_rows = "".join(
        f"<tr><td>{escape(name)}</td><td>{'present' if present else 'missing -- section degrades'}</td></tr>"
        for name in sorted(rstore.available)
        for present in [rstore.available[name]]
    )
    return f"""
<section id="runtime" class="runtime-section">
<h2>Runtime overlay {_run_tag(rstore.run_id)}</h2>
{other_runs_note}
<table><tr><th>runtime artifact</th><th>status</th></tr>{avail_rows}</table>
{error_block}
<h3>Run record</h3>
{_render_run_overview(store, rstore)}
<h3>Mapping rate</h3>
{_render_mapping(rstore)}
<h3>Observed execution order</h3>
<p>Compare against the static cascade order in the "Cascade order" section above --
shown separately, never merged.</p>
{_render_observed_order(store, rstore)}
<h3>Events and captured values</h3>
{_render_events(store, rstore)}
<h3>Unmapped events</h3>
{_render_unmapped(rstore)}
<h3>Decision branches: observed vs. declared</h3>
{_render_decision_branches(store, rstore)}
<h3>Contradictions</h3>
{_render_contradictions(store, rstore)}
<h3>Nondeterminism observed</h3>
{_render_nondeterminism(store, rstore)}
<h3>Alignment verdicts</h3>
{_render_verdicts(store, rstore)}
<h3>Execution narrative</h3>
{_render_narrative(store, rstore)}
</section>
"""


def render_site(store: ArtifactStore, rstore: RuntimeStore | None = None) -> str:
    """Render the whole offline HTML page for one artifact root.

    *rstore* is phase A: when given, a runtime overlay section is appended,
    visually distinct (the ``runtime-evidence`` / ``runtime-section`` CSS
    classes) and tagged with its run ID throughout. Omitting it renders
    exactly the phase B page."""
    element_ids = sorted(store.elements_by_id)
    manifest = store.manifest
    manifest_line = (
        f"schema {escape(str(manifest.get('schema_version', 'unknown')))} | "
        f"tool {escape(str(manifest.get('tool_version', 'unknown')))}"
        if manifest
        else "no manifest.json found"
    )
    title = (
        "CASCADE-MAP &mdash; static + runtime view"
        if rstore is not None
        else "CASCADE-MAP &mdash; static view"
    )
    runtime_nav = '<a href="#runtime">Runtime overlay</a>' if rstore is not None else ""
    runtime_section = _render_runtime_section(store, rstore) if rstore is not None else ""
    sections = f"""
<header><h1>{title}</h1><p>{manifest_line}</p></header>
<nav>
<a href="#browser">Elements</a>
<a href="#cascade">Cascade order</a>
<a href="#callgraph">Call graph</a>
<a href="#findings">Findings</a>
<a href="#diff">Version diff</a>
<a href="#details">Element detail</a>
<a href="#availability">Artifact status</a>
{runtime_nav}
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
{runtime_section}
</main>
"""
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>CASCADE-MAP</title>"
        f"<style>{_STYLE}</style></head><body>{sections}</body></html>"
    )
