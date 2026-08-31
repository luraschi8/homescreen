"""Several components on one page.

The e-paper takes a framebuffer, so a composed dashboard is one HTML document
with each placement drawn into its measured rectangle. The interesting problem
is isolation: every component was written believing it owned the document, and
two of them style `.big`.
"""
import pathlib
import re
import tempfile

import pytest

from homescreen import compose, layout, registry, scenes
from homescreen.serve import create_app

EPAPER = {"w": 800, "h": 480, "depth": 1}
CFG = {"location": {"lat": 40.4168, "lon": -3.7038, "name": "Madrid"},
       "feeds": {"adsb": {"source": "api", "endpoint": "https://x"}},
       "devices": []}


def _build(component, options, region_caps):
    ctx = scenes.SceneContext(
        cfg=CFG, cache_dir=pathlib.Path(tempfile.mkdtemp()), caps=region_caps,
        now=1_787_000_000.0, device={}, options=options)
    return scenes.build(component, ctx).html


VIEW = {"template": "dashboard", "placements": [
    {"id": "p1", "region": "masthead", "component": "clock", "options": {}},
    {"id": "p2", "region": "main_left", "component": "planes", "options": {}},
    {"id": "p3", "region": "markets", "component": "quotes",
     "options": {"symbols": "AAPL,MSFT"}}]}


def test_every_placement_lands_in_its_measured_rectangle():
    html = compose.compose(VIEW, EPAPER, _build)
    regions = layout.regions(EPAPER, "dashboard")
    for placement in VIEW["placements"]:
        x, y, w, h = regions[placement["region"]]["rect"]
        assert f'#rg-{placement["id"]}{{position:absolute;left:{x}px;' in html
        assert f"width:{w}px;height:{h}px" in html


def test_one_components_styles_cannot_reach_another():
    # clock styles `.big`; so would a stocks component. Each was written
    # believing it owned the document, which is true right up until they share
    # one.
    html = compose.compose(VIEW, EPAPER, _build)
    assert "#rg-p1 .big" in html
    assert "}.big{" not in html and ">.big{" not in html


def test_a_fragments_body_rule_sizes_its_own_box_not_the_page():
    # `page()` emits `html,body{width:800px}`. Unscoped, the first fragment
    # would set the size of the whole composition to its own region.
    css = compose.scope_css("html,body{width:417px;height:335px}.x{color:#000}",
                            "rg-p2")
    assert "#rg-p2{width:417px" in css
    assert "#rg-p2 .x{color:#000}" in css


def test_a_media_query_is_kept_and_its_contents_scoped():
    css = compose.scope_css("@media print{.x{color:#000}}", "rg-p1")
    assert css.startswith("@media print{")
    assert "#rg-p1 .x" in css


def test_a_component_that_fails_costs_its_region_not_the_page():
    bad = {"template": "dashboard", "placements": [
        {"id": "ok", "region": "masthead", "component": "clock"},
        {"id": "bad", "region": "main_left", "component": "clock"}]}

    def build(component, options, region_caps):
        if region_caps["w"] == 417:
            raise RuntimeError("this one is broken")
        return _build(component, options, region_caps)

    html = compose.compose(bad, EPAPER, build)
    assert "rg-ok" in html and "rg-bad" not in html


def test_a_placement_in_a_region_this_template_lacks_is_skipped():
    view = {"template": "dashboard", "placements": [
        {"id": "ghost", "region": "nowhere", "component": "clock"}]}
    assert compose.compose(view, EPAPER, _build) == ""


def test_a_placement_id_that_could_be_a_selector_is_made_safe():
    view = {"template": "dashboard", "placements": [
        {"id": '"><script>x</script>', "region": "masthead",
         "component": "clock"}]}
    html = compose.compose(view, EPAPER, _build)
    assert "<script>" not in html


# --- through the frame route ------------------------------------------------

@pytest.fixture
def ctx(tmp_path):
    return create_app(CFG, tmp_path, version="t").test_client(), tmp_path


def _epaper(client, cache, hw="ee00000000ff"):
    client.get(f"/api/devices/{hw}/scene?w=800&h=480&depth=1")
    client.put(f"/api/devices/{hw}/membership", json={"approved": True})
    return hw


def test_a_single_placement_still_takes_the_original_path(ctx, monkeypatch):
    # Composing one component into a full-bleed region would be the same
    # pixels by a longer route, and a longer route is a second thing that can
    # differ from the first.
    client, cache = ctx
    hw = _epaper(client, cache)
    registry.assign(cache, hw, scene="clock")
    from homescreen import serve as serve_module
    seen = {}
    real = compose.compose
    monkeypatch.setattr(compose, "compose",
                        lambda *a, **k: seen.setdefault("called", True) or real(*a, **k))
    client.get(f"/api/devices/{hw}/frame?w=800&h=480")
    assert "called" not in seen


def test_a_composed_view_reaches_the_renderer_as_one_page(ctx):
    client, cache = ctx
    hw = _epaper(client, cache)
    client.put(f"/api/devices/{hw}/schedule", json={
        "views": {"panel": VIEW}, "schedule": {"default": "panel", "slots": []}})
    stored = registry.load(cache)[hw]
    view = layout.view_for(stored, "panel")
    assert len(view["placements"]) == 3, "all three survived validation"
    html = compose.compose(view, {"w": 800, "h": 480, "depth": 1}, _build)
    assert html.count("position:absolute") == 3


# --- Several placements in one region ----------------------------------------
#
# `main_left` declares `holds: 4, stack: "v"` and the view editor offers all
# four. Until slots existed, every one of them was given the region's whole
# rectangle, so a four-component column rendered as four components on top of
# each other.

STACKED = {"template": "dashboard", "placements": [
    {"id": "a", "region": "main_left", "component": "clock", "options": {}},
    {"id": "b", "region": "main_left", "component": "planes", "options": {}},
    {"id": "c", "region": "main_left", "component": "status", "options": {}}]}


def _rects(html):
    return {m[0]: tuple(int(v) for v in m[1:]) for m in re.findall(
        r"#rg-(\w+)\{position:absolute;left:(\d+)px;top:(\d+)px;"
        r"width:(\d+)px;height:(\d+)px", html)}


def test_placements_sharing_a_region_do_not_share_a_rectangle():
    got = _rects(compose.compose(STACKED, EPAPER, _build))
    assert len(set(got.values())) == 3, got


def test_a_shared_region_is_divided_along_its_declared_stack_axis():
    got = _rects(compose.compose(STACKED, EPAPER, _build))
    x, y, w, h = layout.regions(EPAPER, "dashboard")["main_left"]["rect"]
    ordered = [got["a"], got["b"], got["c"]]
    assert [r[0] for r in ordered] == [x] * 3        # a column, not a row
    assert [r[2] for r in ordered] == [w] * 3
    assert ordered[0][1] == y
    assert sum(r[3] for r in ordered) == h           # tiles the region exactly


def test_a_component_is_told_the_size_of_its_slot_not_of_the_region():
    # The failure this prevents is silent: a component handed 417x335 lays out
    # for 335px of height and then renders into 111, and the overflow is
    # clipped rather than reported.
    seen = []

    def spy(component, options, region_caps):
        seen.append((region_caps["w"], region_caps["h"]))
        assert region_caps["depth"] == 1, "depth is the glass's, not the slot's"
        return _build(component, options, region_caps)

    compose.compose(STACKED, EPAPER, spy)
    _, _, w, h = layout.regions(EPAPER, "dashboard")["main_left"]["rect"]
    assert [s[0] for s in seen] == [w] * 3
    assert sum(s[1] for s in seen) == h
    assert all(s[1] < h for s in seen)


def test_a_placement_that_fails_does_not_shift_the_others_off_their_slots():
    # Slots are assigned from the view, not from what survived rendering, so a
    # broken component leaves a hole rather than sliding the rest upward.
    def flaky(component, options, region_caps):
        if component == "planes":
            raise RuntimeError("upstream down")
        return _build(component, options, region_caps)

    got = _rects(compose.compose(STACKED, EPAPER, flaky))
    whole = _rects(compose.compose(STACKED, EPAPER, _build))
    assert "b" not in got
    assert got["a"] == whole["a"] and got["c"] == whole["c"]
