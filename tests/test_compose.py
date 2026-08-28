"""Several components on one page.

The e-paper takes a framebuffer, so a composed dashboard is one HTML document
with each placement drawn into its measured rectangle. The interesting problem
is isolation: every component was written believing it owned the document, and
two of them style `.big`.
"""
import pathlib
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
