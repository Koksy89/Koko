"""Card 15 phase C -- drives the real blueprint page in headless Chromium.

`test_blueprint.py` proved the Python-side data model: correct, escaped,
deterministic JSON. None of those 32 tests execute a line of JavaScript, so
none of them could have caught round 1's D1 -- an `[hidden]` element that an
ID-selector CSS rule (`#empty-state { display:flex; ... }`) painted over the
whole canvas anyway, because an author-origin rule beats the UA stylesheet's
`[hidden]{display:none}` regardless of specificity. No page error, no
console error, a perfectly valid data island underneath -- and a blank
screen. This file is the general guard against that whole class of defect,
not a regression test for one line: it drives the actual page and reads the
actual, rendered pixels back.

`playwright` is a **dev-only** check, never a project dependency:
`pytest.importorskip` skips this entire file when it is not installed, and
`_chromium_executable()` skips it again when no real, on-disk Chromium
binary can be found -- not from `p.chromium.executable_path`, which in the
environment this was written in names a browser version (1243) newer than
what was actually downloaded (1194) and returns a path that does not
exist. Both guards keep the rest of the suite green and network-free on a
machine with neither.

Nothing here writes into `tests/fixtures/`; every artifact goes to
`tmp_path`/`tmp_path_factory`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

from test_viewer import DECIDE_ID, build_fixture  # noqa: E402 -- see sys.path note in test_blueprint.py

from cascade_map.cli import analyze  # noqa: E402
from cascade_map.contracts.interfaces import (  # noqa: E402
    ChangeKind,
    Confidence,
    Impact,
    Method,
    Provenance,
    VersionChange,
    canonical_jsonl,
)
from cascade_map.viewer.blueprint import (  # noqa: E402
    BlueprintView,
    build_blueprint_data,
    render_blueprint_to_file,
)
from cascade_map.viewer.loader import ArtifactStore  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "mode_b"

#: Verification measured 100 DOM nodes painted for the corpus (262 raw
#: elements, auto-collapsed by module above the 150-node threshold, plus 35
#: decision diamonds) inside a 1400x900 viewport after the initial Fit.
#: This is a floor comfortably below that, not the exact number, so a small
#: corpus change does not make the test flaky.
_MIN_VISIBLE_NODES_ON_CORPUS = 40

#: D11: verification measured the pre-fix layout landing Fit at scale
#: 0.088 (a "grey speck", unreadable without nine zoom-ins). This is the
#: floor this builder can actually hold post-fix, checked on the real
#: fixture corpus in every mode combination below -- not an aspirational
#: number, a measured one (post-fix runs landed 0.28-0.64).
_MIN_FIT_SCALE = 0.2

_SCALE_RE = re.compile(r"matrix\(([-0-9.]+),")

#: Round 5 made `cascade` the default scope, which carries no lineage edges
#: and only the decision-relevant slice of the graph. Every fixture below
#: that existed before round 5 renders at `--scope full` so that it still
#: drives the page shape it was written for; round 5's own bounds get their
#: own fixtures and their own tests at the end of this file.
FULL = BlueprintView(scope="full", max_nodes=0)


def _fit_scale(page) -> float:
    transform = page.eval_on_selector("#world", "el => getComputedStyle(el).transform")
    match = _SCALE_RE.match(transform)
    assert match, f"unexpected transform: {transform!r}"
    return float(match.group(1))


def _chromium_executable() -> str | None:
    """A real, on-disk Chromium binary for playwright to drive, or None.

    See the module docstring for why this does not use
    `p.chromium.executable_path`.
    """
    roots = []
    env_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env_root:
        roots.append(Path(env_root))
    roots.append(Path.home() / ".cache" / "ms-playwright")
    for root in roots:
        if not root.is_dir():
            continue
        candidates = sorted(root.glob("chromium-*/chrome-linux*/chrome"))
        if candidates:
            return str(candidates[-1])
    return None


_CHROMIUM = _chromium_executable()
pytestmark = pytest.mark.skipif(_CHROMIUM is None, reason="no on-disk Chromium found for playwright")


@pytest.fixture(scope="module")
def browser():
    if _CHROMIUM is None:
        pytest.skip("no on-disk Chromium found for playwright")
    with playwright_sync.sync_playwright() as p:
        b = p.chromium.launch(executable_path=_CHROMIUM)
        yield b
        b.close()


class _Loaded:
    def __init__(self, page, errors: list[str], console_errors: list[str]) -> None:
        self.page = page
        self.errors = errors
        self.console_errors = console_errors


def _open(browser, html_path: Path) -> _Loaded:
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    errors: list[str] = []
    console_errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.goto(html_path.resolve().as_uri())
    # Round 6: the island is decompressed asynchronously, so "the page has
    # loaded" is no longer "the script tag has run". `data-cascade-ready`
    # is set by the last line of `main`; waiting on a fixed timeout instead
    # would make every test below a race.
    page.wait_for_function(
        "document.documentElement.getAttribute('data-cascade-ready') === '1'",
        timeout=60000,
    )
    page.wait_for_timeout(1500)
    return _Loaded(page, errors, console_errors)


# ---------------------------------------------------------------------------
# Fixtures: one corpus-scale render, one small hand-built render, one with a
# real diff loaded -- built once per module, not once per test.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def corpus_html(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("bp_corpus")
    graph_dir = root / "graph"
    code, summary = analyze(CORPUS, graph_dir, strict_gate=False)
    assert summary["elements"] > 0
    html_path = root / "blueprint.html"
    render_blueprint_to_file(graph_dir, html_path, view=FULL)
    return html_path


@pytest.fixture(scope="module")
def small_html(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("bp_small")
    build_fixture(root)
    html_path = root / "blueprint.html"
    render_blueprint_to_file(root, html_path, view=FULL)
    return html_path


@pytest.fixture(scope="module")
def diff_html(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from test_viewer import COMPUTE_ID, DECIDE_ID as _DECIDE_ID, SCORE_FEATURE_ID

    root = tmp_path_factory.mktemp("bp_diff")
    build_fixture(root)
    diff_root = root / "diff"
    diff_root.mkdir()
    prov = Provenance(method=Method.STRUCTURAL_MATCH, confidence=Confidence.CERTAIN)
    changes = [
        VersionChange(
            id="c1", kind=ChangeKind.BODY_CHANGED, before_id=COMPUTE_ID, after_id=COMPUTE_ID,
            provenance=prov,
        ),
    ]
    impacts = [
        Impact(
            id="i1", change_id="c1", affected_ids=(_DECIDE_ID,), decision_paths_changed=True,
            features_changed=(SCORE_FEATURE_ID,), reachability_flipped=(_DECIDE_ID,),
            findings_added=(), findings_removed=(), rank=1,
        ),
    ]
    (diff_root / "changes.jsonl").write_text(canonical_jsonl(changes), encoding="utf-8")
    (diff_root / "impacts.jsonl").write_text(canonical_jsonl(impacts), encoding="utf-8")
    html_path = root / "blueprint.html"
    render_blueprint_to_file(root, html_path, diff_root=diff_root, view=FULL)
    return html_path


@pytest.fixture(scope="module")
def corpus_diff_html(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Corpus-scale (modules auto-collapsed by default) with a diff spread
    across many elements in many different modules, reproducing D5:
    verification found only 12 of 113 painted nodes on a real diff carried
    any change information, because collapsed module aggregates rolled
    nothing up from their members.
    """
    import random

    root = tmp_path_factory.mktemp("bp_corpus_diff")
    graph_dir = root / "graph"
    analyze(CORPUS, graph_dir, strict_gate=False)
    store = ArtifactStore.load(graph_dir)
    element_ids = sorted(store.elements_by_id)

    prov = Provenance(method=Method.STRUCTURAL_MATCH, confidence=Confidence.CERTAIN)
    kinds = [
        ChangeKind.ADDED, ChangeKind.BODY_CHANGED, ChangeKind.SIGNATURE_CHANGED,
        ChangeKind.RENAMED, ChangeKind.REMOVED, ChangeKind.AMBIGUOUS,
    ]
    rng = random.Random(1)
    sample = rng.sample(element_ids, min(30, len(element_ids)))
    changes = []
    impacts = []
    for i, eid in enumerate(sample):
        kind = kinds[i % len(kinds)]
        before_id = eid if kind != ChangeKind.ADDED else ""
        after_id = eid if kind != ChangeKind.REMOVED else ""
        cid = f"c{i}"
        changes.append(VersionChange(
            id=cid, kind=kind, before_id=before_id, after_id=after_id, provenance=prov,
            candidate_ids=(eid,) if kind == ChangeKind.AMBIGUOUS else (),
        ))
        impacts.append(Impact(
            id=f"i{i}", change_id=cid, affected_ids=(eid,), decision_paths_changed=(i % 5 == 0),
            features_changed=(), reachability_flipped=(), findings_added=(), findings_removed=(), rank=i + 1,
        ))
    diff_root = graph_dir / "diff"
    diff_root.mkdir()
    (diff_root / "changes.jsonl").write_text(canonical_jsonl(changes), encoding="utf-8")
    (diff_root / "impacts.jsonl").write_text(canonical_jsonl(impacts), encoding="utf-8")

    html_path = root / "blueprint.html"
    render_blueprint_to_file(graph_dir, html_path, diff_root=diff_root, view=FULL)
    return html_path


# ---------------------------------------------------------------------------
# D1: the empty-state overlay must never paint while `hidden`
# ---------------------------------------------------------------------------


def test_no_page_or_console_errors_on_the_real_corpus(browser, corpus_html: Path) -> None:
    loaded = _open(browser, corpus_html)
    assert loaded.errors == []
    assert loaded.console_errors == []


def test_element_from_point_over_a_node_hits_the_node_not_an_overlay(browser, small_html: Path) -> None:
    """The exact assertion that would have caught D1: a node the CSSOM
    reports as visible must actually be hit-testable at its own centre,
    not obscured by a `hidden` element whose `display` rule won anyway."""
    loaded = _open(browser, small_html)
    page = loaded.page
    center = page.eval_on_selector(
        ".node",
        "el => { var r = el.getBoundingClientRect(); return {x: r.left + r.width/2, y: r.top + r.height/2}; }",
    )
    hit_is_node_or_descendant = page.evaluate(
        "(pt) => { var e = document.elementFromPoint(pt.x, pt.y); "
        "return !!(e && e.closest('.node')); }",
        center,
    )
    assert hit_is_node_or_descendant is True


def test_diff_tab_empty_state_is_visible_and_sits_at_canvas_centre(browser, corpus_html: Path) -> None:
    """Pins both halves of the D1 bug: the message must be non-empty text
    AND the top (hit-testable) element at the canvas centre -- not merely
    present somewhere in the DOM with `hidden` doing nothing."""
    loaded = _open(browser, corpus_html)
    page = loaded.page
    page.click('.tab-btn[data-tab="diff"]')
    page.wait_for_timeout(400)

    state = page.eval_on_selector(
        "#empty-state", "el => ({hidden: el.hidden, text: el.textContent, display: getComputedStyle(el).display})"
    )
    assert state["hidden"] is False
    assert state["display"] == "flex"
    assert state["text"].strip() != ""
    assert "metatron diff" in state["text"]

    center_hit_id = page.evaluate(
        "() => { var r = document.getElementById('canvas-wrap').getBoundingClientRect();"
        " var e = document.elementFromPoint(r.left + r.width/2, r.top + r.height/2);"
        " return e ? e.id : null; }"
    )
    assert center_hit_id == "empty-state"


# ---------------------------------------------------------------------------
# D2/D3: layout is bounded and the camera fits it on load
# ---------------------------------------------------------------------------


def test_initial_camera_fits_a_minimum_of_nodes_in_the_viewport(browser, corpus_html: Path) -> None:
    loaded = _open(browser, corpus_html)
    page = loaded.page
    visible_count = page.eval_on_selector_all(
        ".node", "els => els.filter(e => getComputedStyle(e).display !== 'none').length"
    )
    assert visible_count >= _MIN_VISIBLE_NODES_ON_CORPUS


def test_world_is_not_a_single_wildly_elongated_strip(browser, corpus_html: Path) -> None:
    """D2's own reproduction: 262 nodes rendered at ~500px of width each in
    one row (137330 x 7512, aspect ratio ~18:1) because a cyclic call graph
    inflated layer numbers without bound. The fixed layout must be within a
    sane aspect ratio regardless of the corpus's exact node count."""
    loaded = _open(browser, corpus_html)
    page = loaded.page
    world = page.eval_on_selector("#world", "el => ({w: el.offsetWidth, h: el.offsetHeight})")
    assert world["w"] > 0 and world["h"] > 0
    aspect = max(world["w"], world["h"]) / min(world["w"], world["h"])
    assert aspect < 6, f"world {world} is too elongated (aspect {aspect:.1f}:1)"


# ---------------------------------------------------------------------------
# D11: aspect ratio alone does not catch "too small to read" -- a direct
# floor on the actual fit scale, in every mode combination.
# ---------------------------------------------------------------------------


def test_fit_scale_is_readable_in_stage_mode(browser, corpus_html: Path) -> None:
    loaded = _open(browser, corpus_html)
    scale = _fit_scale(loaded.page)
    assert scale >= _MIN_FIT_SCALE, f"stage mode fit scale {scale} is unreadable"


def test_fit_scale_is_readable_in_full_graph_mode(browser, corpus_html: Path) -> None:
    loaded = _open(browser, corpus_html)
    page = loaded.page
    page.click("#btn-toggle-stage-mode")
    page.wait_for_timeout(500)
    scale = _fit_scale(page)
    assert scale >= _MIN_FIT_SCALE, f"full-graph mode fit scale {scale} is unreadable"


def test_fit_scale_is_readable_with_a_diff_loaded_in_both_modes(browser, corpus_diff_html: Path) -> None:
    loaded = _open(browser, corpus_diff_html)
    page = loaded.page
    stage_scale = _fit_scale(page)
    assert stage_scale >= _MIN_FIT_SCALE, f"stage mode (diff loaded) fit scale {stage_scale} is unreadable"

    page.click("#btn-toggle-stage-mode")
    page.wait_for_timeout(500)
    full_scale = _fit_scale(page)
    assert full_scale >= _MIN_FIT_SCALE, f"full-graph mode (diff loaded) fit scale {full_scale} is unreadable"


# ---------------------------------------------------------------------------
# D3: fit happens on first paint and after layout changes
# ---------------------------------------------------------------------------


def test_camera_is_not_the_untransformed_default_on_first_paint(browser, corpus_html: Path) -> None:
    loaded = _open(browser, corpus_html)
    page = loaded.page
    transform = page.eval_on_selector("#world", "el => getComputedStyle(el).transform")
    # `matrix(1, 0, 0, 1, 40, 40)` is the pre-fit default this bug shipped;
    # a real corpus never fits at scale 1 in a 1400x900 viewport.
    assert transform != "matrix(1, 0, 0, 1, 40, 40)"
    assert transform != "none"


# ---------------------------------------------------------------------------
# All three tabs paint their own data, not a stale copy of another tab's
# ---------------------------------------------------------------------------


def test_switching_tabs_paints_that_tabs_own_nodes(browser, diff_html: Path) -> None:
    loaded = _open(browser, diff_html)
    page = loaded.page
    # round 4: the Execution tab's default is grouped stage cards, whose
    # ids are not in `data["execution"]["nodes"]` (they are a separate
    # `stages` list) -- this test is about per-element painting, which
    # "Show full graph" restores, matching R1's own requirement that the
    # ungrouped view stays reachable and unchanged.
    page.click("#btn-toggle-stage-mode")
    page.wait_for_timeout(300)
    data = page.evaluate(
        "() => window.CASCADE_BLUEPRINT_DATA"
    )
    for tab in ("execution", "lineage", "diff"):
        expected_ids = {n["id"] for n in data[tab]["nodes"]}
        assert expected_ids, f"{tab} has no nodes in the data island to check against"
        page.click(f'.tab-btn[data-tab="{tab}"]')
        page.wait_for_timeout(300)
        painted_ids = set(page.eval_on_selector_all(".node", "els => els.map(e => e.dataset.id)"))
        assert painted_ids, f"{tab} painted no DOM nodes at all"
        # every painted id is a real node of *this* tab -- not left over
        # from whichever tab a shared DOM container was last filled by.
        assert painted_ids <= expected_ids, (tab, painted_ids - expected_ids)


def test_dom_node_count_matches_the_data_island_below_the_collapse_threshold(
    browser, small_html: Path
) -> None:
    """The hand-built fixture is well under the 150-node auto-collapse
    threshold, so every element is its own painted node and the two counts
    must match exactly -- the collapse feature does not apply here, and
    this is the one case where "DOM count == data count" is the right
    literal check rather than an aggregated one."""
    loaded = _open(browser, small_html)
    page = loaded.page
    # round 4: stage cards are the default; ungroup to check per-element
    # painting, which is exactly what this test is about.
    page.click("#btn-toggle-stage-mode")
    page.wait_for_timeout(300)
    data = page.evaluate(
        "() => window.CASCADE_BLUEPRINT_DATA"
    )
    expected = len(data["execution"]["nodes"])
    assert expected > 0
    painted = page.eval_on_selector_all(".node", "els => els.length")
    assert painted == expected


# ---------------------------------------------------------------------------
# Search flies to and selects a known element; clicking a node opens detail
# ---------------------------------------------------------------------------


def test_search_flies_to_and_selects_a_known_element(browser, small_html: Path) -> None:
    loaded = _open(browser, small_html)
    page = loaded.page
    # round 4: with stages grouped, flying to an element inside a
    # collapsed stage correctly selects that *stage card* instead (see
    # `flyTo`'s `displayIdOf` fallback) -- this test is about the
    # per-element case, so ungroup first.
    page.click("#btn-toggle-stage-mode")
    page.wait_for_timeout(300)
    page.fill("#search-box", "decide")
    page.wait_for_timeout(300)
    results = page.eval_on_selector_all(".search-result", "els => els.length")
    assert results > 0
    page.click(".search-result")
    page.wait_for_timeout(500)

    selected_id = page.eval_on_selector(".node.selected", "el => el ? el.dataset.id : null")
    assert selected_id == DECIDE_ID
    detail_hidden = page.eval_on_selector("#detail-panel", "el => el.hidden")
    assert detail_hidden is False
    detail_text = page.eval_on_selector("#detail-content", "el => el.textContent")
    assert DECIDE_ID in detail_text


def test_clicking_a_node_opens_the_detail_panel_with_its_id(browser, small_html: Path) -> None:
    loaded = _open(browser, small_html)
    page = loaded.page
    node_ids = page.eval_on_selector_all(".node", "els => els.map(e => e.dataset.id)")
    assert node_ids
    target_id = sorted(node_ids)[0]
    page.evaluate(
        "(id) => document.querySelector('.node[data-id=\"' + id.replace(/\"/g, '\\\\\"') + '\"]').click()",
        target_id,
    )
    page.wait_for_timeout(400)
    detail_hidden = page.eval_on_selector("#detail-panel", "el => el.hidden")
    assert detail_hidden is False
    detail_text = page.eval_on_selector("#detail-content", "el => el.textContent")
    assert target_id in detail_text


# ---------------------------------------------------------------------------
# D4: dark by default
# ---------------------------------------------------------------------------


def test_theme_defaults_to_dark(browser, small_html: Path) -> None:
    loaded = _open(browser, small_html)
    page = loaded.page
    palette = page.eval_on_selector(":root", "el => el.getAttribute('data-palette')")
    assert palette == "blueprint-dark"
    bg = page.eval_on_selector("body", "el => getComputedStyle(el).backgroundColor")
    # a loose luminance check rather than an exact colour match, so a
    # deliberate palette tweak does not make this brittle.
    r, g, b = (int(x) for x in bg.replace("rgb(", "").replace(")", "").split(","))
    assert (r + g + b) / 3 < 60, f"background {bg} does not read as dark"


def test_theme_toggle_switches_to_light_and_back(browser, small_html: Path) -> None:
    loaded = _open(browser, small_html)
    page = loaded.page
    page.select_option("#palette-picker", "silver-milk")
    page.wait_for_timeout(200)
    assert page.eval_on_selector(":root", "el => el.getAttribute('data-palette')") == "silver-milk"
    page.select_option("#palette-picker", "blueprint-dark")
    page.wait_for_timeout(200)
    assert page.eval_on_selector(":root", "el => el.getAttribute('data-palette')") == "blueprint-dark"


def test_flow_readout_collapse_toggles_and_is_remembered(browser, corpus_html: Path) -> None:
    loaded = _open(browser, corpus_html)
    page = loaded.page
    assert page.eval_on_selector("#flow-readout-body", "el => el.hidden") is False
    page.click("#flow-readout-header")
    page.wait_for_timeout(200)
    assert page.eval_on_selector("#flow-readout-body", "el => el.hidden") is True
    # a reload (same context, same localStorage) must remember the
    # collapsed state -- persisted the same way the palette is. A fresh
    # `browser.new_page()` would use a fresh, isolated context instead, so
    # this reloads the same page rather than opening a second one.
    page.reload()
    page.wait_for_timeout(500)
    assert page.eval_on_selector("#flow-readout-body", "el => el.hidden") is True


# ---------------------------------------------------------------------------
# Round 4, R3: four palettes, meanings unchanged
# ---------------------------------------------------------------------------

_ALL_PALETTES = ("blueprint-dark", "ai-blue", "silver-milk", "high-contrast")


def test_every_palette_actually_changes_computed_colour(browser, small_html: Path) -> None:
    loaded = _open(browser, small_html)
    page = loaded.page
    backgrounds = {}
    for palette in _ALL_PALETTES:
        page.select_option("#palette-picker", palette)
        page.wait_for_timeout(150)
        backgrounds[palette] = page.eval_on_selector("body", "el => getComputedStyle(el).backgroundColor")
    assert len(set(backgrounds.values())) == len(_ALL_PALETTES), backgrounds


def test_confidence_dash_patterns_are_unchanged_across_every_palette(browser, corpus_html: Path) -> None:
    """The palette must change hues, never meanings: a CERTAIN wire is
    solid and a HEURISTIC wire is dotted in every palette, and the
    stroke-width ordering (CERTAIN thickest) must hold in all four too."""
    loaded = _open(browser, corpus_html)
    page = loaded.page
    dash_by_palette = {}
    for palette in _ALL_PALETTES:
        page.select_option("#palette-picker", palette)
        page.wait_for_timeout(150)
        info = page.evaluate(
            """
            () => {
              function firstOfClass(cls) {
                var el = document.querySelector('.' + cls);
                if (!el) return null;
                var cs = getComputedStyle(el);
                return { dash: cs.strokeDasharray, width: parseFloat(cs.strokeWidth) };
              }
              return {
                CERTAIN: firstOfClass('wire-conf-CERTAIN'),
                HEURISTIC: firstOfClass('wire-conf-HEURISTIC'),
                UNKNOWN: firstOfClass('wire-conf-UNKNOWN'),
              };
            }
            """
        )
        dash_by_palette[palette] = info
    certains = [dash_by_palette[p]["CERTAIN"] for p in _ALL_PALETTES if dash_by_palette[p]["CERTAIN"]]
    heuristics = [dash_by_palette[p]["HEURISTIC"] for p in _ALL_PALETTES if dash_by_palette[p]["HEURISTIC"]]
    assert certains, "no CERTAIN wire painted on the real corpus to check"
    assert heuristics, "no HEURISTIC wire painted on the real corpus to check"
    assert len({c["dash"] for c in certains}) == 1
    assert len({h["dash"] for h in heuristics}) == 1
    for c in certains:
        assert c["dash"] in ("none", ""), c
    for h in heuristics:
        assert h["dash"] not in ("none", ""), h
    for palette in _ALL_PALETTES:
        d = dash_by_palette[palette]
        if d["CERTAIN"] and d["HEURISTIC"]:
            assert d["CERTAIN"]["width"] > d["HEURISTIC"]["width"], (palette, d)


def test_legend_rerenders_in_the_active_palette(browser, small_html: Path) -> None:
    """The legend panel reads the same custom properties as the canvas, so
    switching palette must visibly change it too -- it must never describe
    a palette that is not the one on screen. Checked on the panel
    background (`--panel`), which is deliberately distinct in all four
    palettes; the CERTAIN swatch is not used here because blueprint-dark
    and ai-blue deliberately share one green so that "CERTAIN" reads as
    the same colour family across palettes -- a design choice, not a bug,
    and covered instead by the dash-pattern test below."""
    loaded = _open(browser, small_html)
    page = loaded.page
    colors = {}
    for palette in _ALL_PALETTES:
        page.select_option("#palette-picker", palette)
        page.wait_for_timeout(150)
        colors[palette] = page.eval_on_selector(
            "#legend", "el => getComputedStyle(el).backgroundColor"
        )
    assert len(set(colors.values())) == len(_ALL_PALETTES), colors


# ---------------------------------------------------------------------------
# D5: a collapsed module aggregate must carry its members' change rollup
# ---------------------------------------------------------------------------


def test_collapsed_module_aggregates_carry_the_change_rollup(browser, corpus_diff_html: Path) -> None:
    """Reproduces the exact verification finding: 113 painted nodes, only
    12 (all plain elements, none a module aggregate) carried change
    information. Asserts the DOM count of aggregates-with-a-rollup is
    greater than zero AND matches, exactly, the number of module
    aggregates whose own `change_counts` (computed by `computeLayout` from
    the data island, not re-derived here) is non-empty -- so this cannot
    pass by coincidence."""
    loaded = _open(browser, corpus_diff_html)
    page = loaded.page
    page.click('.tab-btn[data-tab="diff"]')
    page.wait_for_timeout(600)

    counts = page.evaluate(
        "() => { var nodes = Array.from(document.querySelectorAll('.node.node-module-agg'));"
        " var withRollup = nodes.filter(n => Array.from(n.classList).some(c => c.indexOf('change-') === 0));"
        " return { aggregates: nodes.length, withRollupDom: withRollup.length }; }"
    )
    assert counts["aggregates"] > 0
    assert counts["withRollupDom"] > 0

    # `layoutCache` is module-private (inside the page's IIFE) and not
    # reachable from here, so cross-check independently from
    # the data island itself instead -- for every element that changed,
    # which module it belongs to, intersected with which modules are
    # actually collapsed in this render (all of them: corpus scale exceeds
    # the auto-collapse threshold).
    data = page.evaluate(
        "() => window.CASCADE_BLUEPRINT_DATA"
    )
    changed_modules = set()
    for node in data["diff"]["nodes"]:
        if node.get("changes") and node.get("module"):
            changed_modules.add(node["module"])
    assert changed_modules, "the synthetic diff fixture produced no changed, modularised elements"
    assert counts["withRollupDom"] == len(changed_modules)


def test_expanding_a_rolled_up_module_still_shows_per_element_changes(
    browser, corpus_diff_html: Path
) -> None:
    loaded = _open(browser, corpus_diff_html)
    page = loaded.page
    page.click('.tab-btn[data-tab="diff"]')
    page.wait_for_timeout(600)
    agg = page.query_selector(".node.node-module-agg[class*='change-']")
    assert agg is not None
    agg.click()
    page.wait_for_timeout(300)
    expand_btn = page.query_selector("#detail-content .action-btn")
    assert expand_btn is not None
    expand_btn.click()
    page.wait_for_timeout(400)
    # the module's members are now painted as ordinary element nodes, each
    # still carrying its own per-element change-* class
    member_change_nodes = page.eval_on_selector_all(
        ".node:not(.node-module-agg)[class*='change-']", "els => els.length"
    )
    assert member_change_nodes > 0


# ---------------------------------------------------------------------------
# D8: NO_SINK_PATH / UNKNOWN nodes must stay comfortably readable
# ---------------------------------------------------------------------------

_MIN_DIM_NODE_OPACITY = 0.9


def test_no_sink_path_node_opacity_is_above_the_readability_floor(browser, corpus_html: Path) -> None:
    loaded = _open(browser, corpus_html)
    page = loaded.page
    # round 4: individual elements are hidden inside collapsed stage cards
    # by default; expand everything to see real per-element reachability.
    page.click("#btn-expand-all-stages")
    page.wait_for_timeout(500)
    op = page.evaluate(
        "() => { var n = document.querySelector('.node.reach-NO_SINK_PATH:not(.dim)');"
        " return n ? parseFloat(getComputedStyle(n).opacity) : null; }"
    )
    assert op is not None, "no reach-NO_SINK_PATH node painted on the real corpus to check"
    assert op >= _MIN_DIM_NODE_OPACITY, f"NO_SINK_PATH opacity {op} reads as near-invisible"


def test_unknown_reachability_node_opacity_is_above_the_readability_floor(browser, corpus_html: Path) -> None:
    loaded = _open(browser, corpus_html)
    page = loaded.page
    page.click("#btn-expand-all-stages")
    page.wait_for_timeout(500)
    op = page.evaluate(
        "() => { var n = document.querySelector('.node.reach-UNKNOWN:not(.dim)');"
        " return n ? parseFloat(getComputedStyle(n).opacity) : null; }"
    )
    assert op is not None, "no reach-UNKNOWN node painted on the real corpus to check"
    assert op >= _MIN_DIM_NODE_OPACITY, f"UNKNOWN opacity {op} reads as near-invisible"


# ---------------------------------------------------------------------------
# D7: decision diamonds must not overflow their own shape
# ---------------------------------------------------------------------------


def test_decision_node_computed_size_matches_its_own_inline_style(browser, corpus_html: Path) -> None:
    """Reproduces the exact D7 regression: a percentage `padding` on an
    absolutely-positioned decision node resolved against `#world`'s width
    (thousands of px), not the node's own declared size, inflating a
    158x158 node to ~1250x1416. The computed box must equal the inline
    style, i.e. padding must never be able to grow the box."""
    loaded = _open(browser, corpus_html)
    page = loaded.page
    # round 4: decision nodes are only painted once their owning
    # element's stage is expanded.
    page.click("#btn-expand-all-stages")
    page.wait_for_timeout(500)
    size = page.evaluate(
        "() => { var n = document.querySelector('.node.kind-DECISION'); var cs = getComputedStyle(n);"
        " return { inline: n.style.width, computed: cs.width }; }"
    )
    assert size["inline"] == size["computed"]


# ---------------------------------------------------------------------------
# D9: Fit centres the content, not just fits it to one corner
# ---------------------------------------------------------------------------


def test_fit_centres_content_on_both_axes(browser, corpus_html: Path) -> None:
    loaded = _open(browser, corpus_html)
    page = loaded.page
    metrics = page.evaluate(
        """
        () => {
          var world = document.getElementById('world');
          var viewport = document.getElementById('viewport').getBoundingClientRect();
          var nodes = Array.from(document.querySelectorAll('.node'));
          var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
          nodes.forEach(function (n) {
            var r = n.getBoundingClientRect();
            minX = Math.min(minX, r.left); minY = Math.min(minY, r.top);
            maxX = Math.max(maxX, r.right); maxY = Math.max(maxY, r.bottom);
          });
          var contentCx = (minX + maxX) / 2, contentCy = (minY + maxY) / 2;
          var viewCx = viewport.left + viewport.width / 2, viewCy = viewport.top + viewport.height / 2;
          return { dx: Math.abs(contentCx - viewCx), dy: Math.abs(contentCy - viewCy), vw: viewport.width, vh: viewport.height };
        }
        """
    )
    # culling hides far-offscreen nodes, so this is measured only over
    # currently-painted (on-screen) nodes -- a loose tolerance (15% of the
    # viewport) rather than a pixel-exact centre for that reason.
    assert metrics["dx"] < metrics["vw"] * 0.15, metrics
    assert metrics["dy"] < metrics["vh"] * 0.15, metrics


# ---------------------------------------------------------------------------
# Round 4, R1: grouped stage layout is the Execution tab's default
# ---------------------------------------------------------------------------


def test_stages_render_by_default_and_member_counts_match_the_data_island(
    browser, corpus_html: Path
) -> None:
    loaded = _open(browser, corpus_html)
    page = loaded.page
    data = page.evaluate(
        "() => window.CASCADE_BLUEPRINT_DATA"
    )
    stages = data["execution"]["stages"]
    assert stages, "the real corpus produced no stages to check"
    total_expected_members = sum(len(s["member_ids"]) for s in stages)
    total_execution_elements = sum(1 for n in data["execution"]["nodes"] if n.get("is_element"))
    assert total_expected_members == total_execution_elements

    stage_card_count = page.eval_on_selector_all(".node.kind-STAGE", "els => els.length")
    assert stage_card_count == len(stages)

    counted = page.eval_on_selector(
        ".node.kind-STAGE", "el => el.querySelectorAll('.stage-list-item, .stage-list-more').length"
    )
    assert counted > 0


def test_expanding_a_stage_paints_its_members(browser, corpus_html: Path) -> None:
    loaded = _open(browser, corpus_html)
    page = loaded.page
    stage_cards_before = page.eval_on_selector_all(".node.kind-STAGE", "els => els.length")
    elements_before = page.eval_on_selector_all(".node:not(.kind-STAGE)", "els => els.length")
    assert stage_cards_before > 0

    stage = page.query_selector(".node.kind-STAGE")
    stage.click()
    page.wait_for_timeout(300)
    expand_btn = page.query_selector("#detail-content .action-btn")
    assert expand_btn is not None
    expand_btn.click()
    page.wait_for_timeout(400)

    elements_after = page.eval_on_selector_all(".node:not(.kind-STAGE)", "els => els.length")
    stage_cards_after = page.eval_on_selector_all(".node.kind-STAGE", "els => els.length")
    assert elements_after > elements_before
    # exactly one fewer stage card: that one stage's members are now
    # painted individually instead of behind its card.
    assert stage_cards_after == stage_cards_before - 1


def test_full_graph_remains_reachable_via_show_full_graph(browser, corpus_html: Path) -> None:
    """R1's own requirement: "Expand all" (here, "Show full graph") must
    still give the graph that existed before stages were added."""
    loaded = _open(browser, corpus_html)
    page = loaded.page
    data = page.evaluate(
        "() => window.CASCADE_BLUEPRINT_DATA"
    )
    expected_elements = sum(1 for n in data["execution"]["nodes"] if n.get("is_element"))
    assert expected_elements > 0
    page.click("#btn-toggle-stage-mode")
    page.wait_for_timeout(500)
    stage_cards = page.eval_on_selector_all(".kind-STAGE", "els => els.length")
    assert stage_cards == 0
    non_stage_nodes = page.eval_on_selector_all(".node", "els => els.length")
    assert non_stage_nodes > 0


# ---------------------------------------------------------------------------
# Round 4, R2: out-of-order (backward) flow is the point of the tool
# ---------------------------------------------------------------------------


def test_known_backward_edge_is_classified_and_rendered_distinctly(browser, corpus_html: Path) -> None:
    """`mode_b.res_import_cycle` is a real mutual-import fixture in the
    corpus (module `a` imports `b`, `b` imports back `a`); under
    stage-index classification this is a genuine BACKWARD edge -- no
    synthetic fixture needed. Both halves are checked: the data island
    classifies it, and the DOM actually paints a `.wire-flow-BACKWARD`
    element."""
    loaded = _open(browser, corpus_html)
    page = loaded.page
    data = page.evaluate(
        "() => window.CASCADE_BLUEPRINT_DATA"
    )
    backward_wires = [w for w in data["execution"]["wires"] if w.get("flow") == "BACKWARD"]
    assert backward_wires, "expected at least one BACKWARD-classified wire in the real corpus"
    cycle_backward = [w for w in backward_wires if "res_import_cycle" in w["source_id"]]
    assert cycle_backward, backward_wires

    page.click("#btn-expand-all-stages")
    page.wait_for_timeout(600)
    backward_dom_count = page.eval_on_selector_all(".wire-flow-BACKWARD", "els => els.length")
    assert backward_dom_count > 0


def test_flow_readout_totals_sum_to_the_total_wire_count(browser, corpus_html: Path) -> None:
    loaded = _open(browser, corpus_html)
    page = loaded.page
    data = page.evaluate(
        "() => window.CASCADE_BLUEPRINT_DATA"
    )
    totals = data["execution"]["flow_totals"]
    assert sum(totals.values()) == len(data["execution"]["wires"])

    chip_texts = page.eval_on_selector_all(
        "#flow-totals .flow-total-chip", "els => els.map(e => e.textContent)"
    )
    assert chip_texts
    rendered_total = 0
    for text in chip_texts:
        rendered_total += int(text.split(":")[1].strip())
    assert rendered_total == len(data["execution"]["wires"])


def test_only_backward_edges_filter_shows_only_backward_wires(browser, corpus_html: Path) -> None:
    loaded = _open(browser, corpus_html)
    page = loaded.page
    page.click("#btn-expand-all-stages")
    page.wait_for_timeout(500)
    page.click("#filters-panel summary")
    page.wait_for_timeout(150)
    page.click("#filter-backward")
    page.wait_for_timeout(400)
    classes = page.eval_on_selector_all("#wires-g path", "els => els.map(e => e.className.baseVal)")
    assert classes, "expected at least the known backward edge to remain drawn"
    assert all("wire-flow-BACKWARD" in c for c in classes)


def test_clicking_a_backward_flow_pair_frames_those_edges(browser, corpus_html: Path) -> None:
    loaded = _open(browser, corpus_html)
    page = loaded.page
    page.click(".flow-pair-row.flow-pair-BACKWARD")
    page.wait_for_timeout(600)
    highlighted = page.eval_on_selector_all(".wire.path-highlight", "els => els.length")
    assert highlighted > 0


# ---------------------------------------------------------------------------
# Round 5 -- the bounded default, driven in the real browser
#
# The Python tests prove the island is bounded and labelled. Only the
# browser can prove the labels are on the screen: that the scope banner is
# painted, that the lineage tab's "pick an element" is the top element at
# the canvas centre rather than a blank page, and that a stage card whose
# members the scope dropped still says so.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cascade_html(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The real corpus at round 5's DEFAULT: cascade scope, budget 2000."""
    root = tmp_path_factory.mktemp("bp_cascade")
    graph_dir = root / "graph"
    analyze(CORPUS, graph_dir, strict_gate=False)
    html_path = root / "blueprint.html"
    render_blueprint_to_file(graph_dir, html_path)
    return html_path


@pytest.fixture(scope="module")
def truncated_html(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The real corpus squeezed into 30 nodes, so truncation is certain."""
    root = tmp_path_factory.mktemp("bp_truncated")
    graph_dir = root / "graph"
    analyze(CORPUS, graph_dir, strict_gate=False)
    html_path = root / "blueprint.html"
    render_blueprint_to_file(graph_dir, html_path, view=BlueprintView(max_nodes=30))
    return html_path


def test_lineage_empty_state_is_the_top_element_at_canvas_centre(
    browser, cascade_html: Path
) -> None:
    """The worst offender's tab: 402,596 edges on the owner's engine. It is
    empty by default now, and the emptiness must be a readable instruction
    sitting under the pointer -- not a blank canvas, which is what a user
    would report as "the lineage tab is broken"."""
    loaded = _open(browser, cascade_html)
    page = loaded.page
    page.click('.tab-btn[data-tab="lineage"]')
    page.wait_for_timeout(500)

    state = page.eval_on_selector(
        "#empty-state",
        "el => ({hidden: el.hidden, text: el.textContent,"
        " display: getComputedStyle(el).display, opacity: getComputedStyle(el).opacity})",
    )
    assert state["hidden"] is False
    assert state["display"] == "flex"
    assert float(state["opacity"]) > 0.9
    assert "focus" in state["text"].lower()
    assert "--focus" in state["text"]
    assert "lineage edges" in state["text"]

    hit = page.evaluate(
        "() => { var r = document.getElementById('canvas-wrap').getBoundingClientRect();"
        " var e = document.elementFromPoint(r.left + r.width/2, r.top + r.height/2);"
        " return e ? e.id : null; }"
    )
    assert hit == "empty-state"
    assert page.eval_on_selector_all("#nodes-layer .node", "els => els.length") == 0
    assert loaded.errors == [] and loaded.console_errors == []


def test_scope_banner_is_painted_and_names_what_is_missing(
    browser, truncated_html: Path
) -> None:
    loaded = _open(browser, truncated_html)
    page = loaded.page
    banner = page.eval_on_selector(
        "#scope-banner",
        "el => ({text: el.textContent, h: el.getBoundingClientRect().height,"
        " display: getComputedStyle(el).display})",
    )
    assert banner["h"] > 8, "the scope banner must actually occupy space on the page"
    assert banner["display"] != "none"
    assert "scope cascade" in banner["text"]
    assert "not shown" in banner["text"]
    assert "ranked by decision relevance" in banner["text"]
    assert "--max-nodes" in banner["text"]
    assert loaded.errors == [] and loaded.console_errors == []


def test_a_stage_card_says_what_the_scope_left_out(browser, truncated_html: Path) -> None:
    """Never render an omission as emptiness. At a 30-node budget most
    stage members are gone; every card that lost members must say so, in
    the attention colour, with the flag that brings them back."""
    loaded = _open(browser, truncated_html)
    page = loaded.page
    page.wait_for_timeout(400)
    notes = page.eval_on_selector_all(
        ".stage-list-omitted", "els => els.map(e => e.textContent)"
    )
    assert notes, "a truncated page painted no omission notice at all"
    assert any("not included" in n for n in notes)
    assert any("--scope full" in n or "--max-nodes" in n for n in notes)
    painted = page.eval_on_selector_all("#nodes-layer .node", "els => els.length")
    assert painted > 0, "the truncated page must still draw its stage cards"
    assert loaded.errors == [] and loaded.console_errors == []


def test_default_page_is_interactive_and_hit_testable(browser, cascade_html: Path) -> None:
    """The round-4 failure was a file the browser never survived to parse.
    This is the positive form of that check, driven on the real page: the
    default view paints nodes, and pointing at one hits that node."""
    loaded = _open(browser, cascade_html)
    page = loaded.page
    painted = page.eval_on_selector_all("#nodes-layer .node", "els => els.length")
    assert painted > 0
    hit = page.evaluate(
        "() => { var n = document.querySelector('#nodes-layer .node');"
        " var r = n.getBoundingClientRect();"
        " var e = document.elementFromPoint(r.left + r.width/2, r.top + r.height/2);"
        " var c = e && e.closest ? e.closest('.node') : null;"
        " return c ? c.dataset.id : null; }"
    )
    assert hit is not None
    assert _fit_scale(page) >= _MIN_FIT_SCALE
    assert loaded.errors == [] and loaded.console_errors == []


# ---------------------------------------------------------------------------
# Round 6 -- the compressed island, decoded by the real browser
#
# `test_blueprint_compression.py` proves the Python round trip. That is not
# the claim: the claim is that the BROWSER rebuilds the same graph. These
# tests render the same artifacts twice -- once compressed, once with
# `--no-compress` -- open both, and compare the two reconstructed objects
# inside the page, deeply, with types. Not a count, not a spot check: every
# node, every edge, every field.
# ---------------------------------------------------------------------------

#: A type-strict deep comparison, run in the page. Returns "" when the two
#: structures are identical and the path to the first difference otherwise,
#: so a failure names the field rather than saying "not equal".
_DEEP_DIFF_JS = """
function deepDiff(a, b, path) {
  var ta = a === null ? 'null' : Array.isArray(a) ? 'array' : typeof a;
  var tb = b === null ? 'null' : Array.isArray(b) ? 'array' : typeof b;
  if (ta !== tb) { return path + ': type ' + ta + ' vs ' + tb; }
  if (ta === 'array') {
    if (a.length !== b.length) { return path + ': length ' + a.length + ' vs ' + b.length; }
    for (var i = 0; i < a.length; i++) {
      var d = deepDiff(a[i], b[i], path + '[' + i + ']');
      if (d) { return d; }
    }
    return '';
  }
  if (ta === 'object') {
    var ka = Object.keys(a).sort(), kb = Object.keys(b).sort();
    if (ka.length !== kb.length) { return path + ': ' + ka.length + ' keys vs ' + kb.length; }
    for (var j = 0; j < ka.length; j++) {
      if (ka[j] !== kb[j]) { return path + ': key ' + ka[j] + ' vs ' + kb[j]; }
      var e = deepDiff(a[ka[j]], b[ka[j]], path + '.' + ka[j]);
      if (e) { return e; }
    }
    return '';
  }
  if (a !== b) { return path + ': ' + String(a) + ' vs ' + String(b); }
  return '';
}
"""

#: How many leaves the comparison must have walked before its "identical"
#: verdict means anything. Measured on the corpus render, which carries far
#: more than this; the floor only has to rule out comparing two empty pages.
_MIN_LEAVES_COMPARED = 5000

_COUNT_LEAVES_JS = """
function countLeaves(v) {
  if (v === null || typeof v !== 'object') { return 1; }
  if (Array.isArray(v)) { var n = 0; for (var i = 0; i < v.length; i++) { n += countLeaves(v[i]); } return n; }
  var k = Object.keys(v), m = 0;
  for (var j = 0; j < k.length; j++) { m += 1 + countLeaves(v[k[j]]); }
  return m;
}
"""


@pytest.fixture(scope="module")
def compressed_and_plain(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """The same corpus graph rendered both ways, into one directory."""
    root = tmp_path_factory.mktemp("bp_compress")
    graph_dir = root / "graph"
    code, summary = analyze(CORPUS, graph_dir, strict_gate=False)
    assert summary["elements"] > 0
    gz = root / "compressed.html"
    plain = root / "plain.html"
    render_blueprint_to_file(graph_dir, gz, view=FULL)
    render_blueprint_to_file(
        graph_dir, plain,
        view=BlueprintView(scope="full", max_nodes=0, compress=False),
    )
    assert gz.stat().st_size < plain.stat().st_size
    return gz, plain


def _data_from(browser, path: Path) -> str:
    """The page's reconstructed data, serialized for transfer, plus its leaf count."""
    loaded = _open(browser, path)
    assert loaded.errors == [] and loaded.console_errors == []
    return loaded


def test_browser_rebuilds_the_compressed_island_exactly(browser, compressed_and_plain) -> None:
    """Deep, type-strict equality between the compressed page's data and the
    uncompressed page's data, computed inside the browser over the whole
    structure -- every node, every edge, every field."""
    gz, plain = compressed_and_plain
    gz_page = _data_from(browser, gz)
    plain_page = _data_from(browser, plain)

    reference = plain_page.page.evaluate("() => JSON.stringify(window.CASCADE_BLUEPRINT_DATA)")
    verdict = gz_page.page.evaluate(
        "(ref) => {" + _DEEP_DIFF_JS + _COUNT_LEAVES_JS
        + " var other = JSON.parse(ref);"
          " return { diff: deepDiff(window.CASCADE_BLUEPRINT_DATA, other, '$'),"
          "          leaves: countLeaves(window.CASCADE_BLUEPRINT_DATA) }; }",
        reference,
    )
    assert verdict["diff"] == "", verdict["diff"]
    assert verdict["leaves"] > _MIN_LEAVES_COMPARED, (
        f"only {verdict['leaves']} leaves compared -- the verdict proves nothing"
    )


def test_compressed_page_renders_the_same_graph(browser, compressed_and_plain) -> None:
    """The decoded data is the same; so is what is drawn from it."""
    gz, plain = compressed_and_plain
    counts = []
    for path in (gz, plain):
        loaded = _open(browser, path)
        counts.append(loaded.page.eval_on_selector_all("#nodes-layer .node", "els => els.length"))
        assert loaded.errors == [] and loaded.console_errors == []
        assert _fit_scale(loaded.page) >= _MIN_FIT_SCALE
    assert counts[0] == counts[1] > 0


def test_page_without_native_gzip_falls_back_and_says_so(browser, compressed_and_plain) -> None:
    """`DecompressionStream` removed before the page's own script runs.

    The embedded inflater must produce exactly the same object -- proved by
    the same deep comparison, not by the page merely not crashing -- and
    the page must SAY which path it took rather than pretending.
    """
    gz, plain = compressed_and_plain
    plain_page = _data_from(browser, plain)
    reference = plain_page.page.evaluate("() => JSON.stringify(window.CASCADE_BLUEPRINT_DATA)")

    page = browser.new_page(viewport={"width": 1400, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.add_init_script("delete window.DecompressionStream;")
    page.goto(gz.resolve().as_uri())
    page.wait_for_function(
        "document.documentElement.getAttribute('data-cascade-ready') === '1'", timeout=120000
    )
    assert page.evaluate("() => typeof DecompressionStream") == "undefined"
    verdict = page.evaluate(
        "(ref) => {" + _DEEP_DIFF_JS + _COUNT_LEAVES_JS
        + " return { diff: deepDiff(window.CASCADE_BLUEPRINT_DATA, JSON.parse(ref), '$'),"
          "          leaves: countLeaves(window.CASCADE_BLUEPRINT_DATA) }; }",
        reference,
    )
    assert verdict["diff"] == "", verdict["diff"]
    assert verdict["leaves"] > _MIN_LEAVES_COMPARED
    note = page.eval_on_selector("#boot-status", "e => e.textContent + '|' + e.className")
    assert "no native gzip" in note
    assert "Nothing is missing" in note
    assert page.eval_on_selector_all("#nodes-layer .node", "els => els.length") > 0
    assert errors == []
    page.close()


def test_a_corrupt_island_says_so_instead_of_rendering_nothing(browser, tmp_path: Path) -> None:
    """The fourth instance of "a failure that renders as success" is the one
    this card keeps being bitten by. A page that cannot decode its data
    must state that, not paint an empty canvas."""
    graph_dir = tmp_path / "graph"
    analyze(CORPUS, graph_dir, strict_gate=False)
    good = tmp_path / "good.html"
    render_blueprint_to_file(graph_dir, good, view=FULL)
    html = good.read_text(encoding="utf-8")
    marker = 'id="cascade-blueprint-data-gz">'
    start = html.index(marker) + len(marker)
    end = html.index("</script>", start)
    broken = tmp_path / "broken.html"
    broken.write_text(html[:start] + "Tm90IGd6aXAgYXQgYWxs" + html[end:], encoding="utf-8")

    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(broken.resolve().as_uri())
    page.wait_for_function(
        "document.getElementById('boot-status').className === 'boot-error'", timeout=60000
    )
    text = page.eval_on_selector("#boot-status", "e => e.textContent")
    assert "could not be decoded" in text
    assert "--no-compress" in text
    page.close()
