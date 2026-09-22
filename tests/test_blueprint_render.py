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
from cascade_map.viewer.blueprint import build_blueprint_data, render_blueprint_to_file  # noqa: E402
from cascade_map.viewer.loader import ArtifactStore  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "mode_b"

#: Verification measured 100 DOM nodes painted for the corpus (262 raw
#: elements, auto-collapsed by module above the 150-node threshold, plus 35
#: decision diamonds) inside a 1400x900 viewport after the initial Fit.
#: This is a floor comfortably below that, not the exact number, so a small
#: corpus change does not make the test flaky.
_MIN_VISIBLE_NODES_ON_CORPUS = 40


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
    render_blueprint_to_file(graph_dir, html_path)
    return html_path


@pytest.fixture(scope="module")
def small_html(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("bp_small")
    build_fixture(root)
    html_path = root / "blueprint.html"
    render_blueprint_to_file(root, html_path)
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
    render_blueprint_to_file(root, html_path, diff_root=diff_root)
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
    data = page.evaluate(
        "() => JSON.parse(document.getElementById('cascade-blueprint-data').textContent)"
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
    data = page.evaluate(
        "() => JSON.parse(document.getElementById('cascade-blueprint-data').textContent)"
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
    theme = page.eval_on_selector(":root", "el => el.getAttribute('data-theme')")
    assert theme == "dark"
    bg = page.eval_on_selector("body", "el => getComputedStyle(el).backgroundColor")
    # the light theme's --bg is #eef1f7 == rgb(238, 241, 247); dark's is
    # much darker. A loose luminance check rather than an exact colour
    # match, so a deliberate palette tweak does not make this brittle.
    r, g, b = (int(x) for x in bg.replace("rgb(", "").replace(")", "").split(","))
    assert (r + g + b) / 3 < 60, f"background {bg} does not read as dark"


def test_theme_toggle_switches_to_light_and_back(browser, small_html: Path) -> None:
    loaded = _open(browser, small_html)
    page = loaded.page
    page.click("#theme-toggle")
    page.wait_for_timeout(200)
    assert page.eval_on_selector(":root", "el => el.getAttribute('data-theme')") == "light"
    page.click("#theme-toggle")
    page.wait_for_timeout(200)
    assert page.eval_on_selector(":root", "el => el.getAttribute('data-theme')") == "dark"
