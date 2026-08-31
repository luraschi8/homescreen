"""Building a composed dashboard from the web page.

The compositor renders a view; the schedule decides when it shows; this is what
lets somebody say what is IN one without curl. Without it the e-paper's whole
point -- several components on one panel -- is configurable only by API, which
is not what "configure it from the dashboard" means.
"""
import re

import pytest

from homescreen import layout, registry
from homescreen.serve import create_app

CFG = {"location": {"lat": 40.4168, "lon": -3.7038, "name": "Madrid"},
       "feeds": {"adsb": {"source": "api", "endpoint": "https://x"}},
       "devices": []}
EPD = "ee00000000ff"
ROUND = "aabb00112233"


@pytest.fixture
def ctx(tmp_path):
    client = create_app(CFG, tmp_path, version="t").test_client()
    client.get(f"/api/devices/{EPD}/scene?w=800&h=480&depth=1")
    client.put(f"/api/devices/{EPD}/membership", json={"approved": True})
    client.get(f"/api/devices/{ROUND}/scene?w=240&h=240&depth=16&shape=round"
               "&components=radar,draw_list")
    client.put(f"/api/devices/{ROUND}/membership", json={"approved": True})
    return client, tmp_path


def _dashboard(client, cache):
    """Put the e-paper on the composed template."""
    client.put(f"/api/devices/{EPD}/schedule", json={
        "views": {"panel": {"template": "dashboard", "placements": [
            {"id": "a", "region": "masthead", "component": "clock"}]}},
        "schedule": {"default": "panel", "slots": []}})


def test_a_screen_with_one_region_is_not_offered_a_view_editor(ctx):
    # It already has one: the "Qué muestra" form. A second, more elaborate way
    # to say the same thing is two places to change one fact.
    client, _ = ctx
    html = client.get(f"/device/{ROUND}").get_data(as_text=True)
    assert "Qué contiene cada vista" not in html


def test_a_composed_screen_is_offered_one(ctx):
    client, cache = ctx
    _dashboard(client, cache)
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    assert "Qué contiene cada vista" in html
    for region in ("masthead", "main_left", "main_right", "markets"):
        assert region in html


def test_each_region_shows_the_size_a_component_will_actually_get(ctx):
    # Choosing between a 764x62 band and a 417x335 column is a different
    # decision, and the name alone does not say which is which.
    client, cache = ctx
    _dashboard(client, cache)
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    assert "764&times;62" in html and "417&times;335" in html


def test_a_region_that_holds_several_offers_several_slots(ctx):
    client, cache = ctx
    _dashboard(client, cache)
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    assert 'name="v.panel.markets.5"' in html, "the band holds six"
    assert 'name="v.panel.markets.6"' not in html


def test_placing_components_reaches_the_record(ctx):
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={
        "v.panel.masthead.0": "clock",
        "v.panel.main_left.0": "planes",
        "v.panel.markets.0": "quotes"})
    view = layout.view_for(registry.load(cache)[EPD], "panel")
    assert [(p["region"], p["component"]) for p in view["placements"]] == [
        ("masthead", "clock"), ("main_left", "planes"), ("markets", "quotes")]


def test_an_empty_select_empties_the_region(ctx):
    # The form shows every slot at once, so what it posts IS the arrangement.
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={
        "v.panel.masthead.0": "clock", "v.panel.main_left.0": "planes"})
    client.post(f"/device/{EPD}/views", data={"v.panel.masthead.0": "clock"})
    view = layout.view_for(registry.load(cache)[EPD], "panel")
    assert [p["region"] for p in view["placements"]] == ["masthead"]


def test_a_components_settings_survive_being_rearranged(ctx):
    # Options belong to the component and are edited elsewhere. Losing them
    # because a placement moved would make the two forms fight.
    client, cache = ctx
    _dashboard(client, cache)
    registry.set_layout(cache, EPD, {"panel": {
        "template": "dashboard", "placements": [
            {"id": "a", "region": "masthead", "component": "clock",
             "options": {"timezone": "Asia/Tokyo"}}]}},
        {"default": "panel", "slots": []})
    client.post(f"/device/{EPD}/views", data={
        "v.panel.masthead.0": "clock", "v.panel.markets.0": "quotes"})
    view = layout.view_for(registry.load(cache)[EPD], "panel")
    clock = [p for p in view["placements"] if p["component"] == "clock"][0]
    assert clock["options"]["timezone"] == "Asia/Tokyo"


def test_a_new_view_can_be_added_and_is_created_empty_then_filled(ctx):
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={
        "v.panel.masthead.0": "clock",
        "new_view": "mañana",
        "v.mañana.masthead.0": "weather"})
    names = layout.view_names(registry.load(cache)[EPD])
    assert "mañana" in names
    view = layout.view_for(registry.load(cache)[EPD], "mañana")
    assert view["placements"][0]["component"] == "weather"


def test_a_view_left_completely_empty_is_dropped_not_stored(ctx):
    # An empty view is not a view, and a schedule pointing at one shows
    # nothing while looking configured.
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={
        "v.panel.masthead.0": "clock", "new_view": "vacía"})
    assert "vacía" not in layout.view_names(registry.load(cache)[EPD])


def test_a_screen_cannot_be_left_with_no_view_at_all(ctx):
    client, cache = ctx
    _dashboard(client, cache)
    before = layout.view_names(registry.load(cache)[EPD])
    r = client.post(f"/device/{EPD}/views", data={})
    from urllib.parse import unquote_plus
    assert "al menos una vista" in unquote_plus(r.headers["Location"])
    assert layout.view_names(registry.load(cache)[EPD]) == before


def test_a_component_this_screen_cannot_draw_is_offered_disabled(ctx):
    client, cache = ctx
    _dashboard(client, cache)
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    assert "disabled" in html, "with the reason, rather than silently missing"


# --- each placement is configured on its own ---------------------------------

def test_two_calendars_on_one_screen_are_two_different_calendars(ctx):
    """The gap the owner named.

    Options were always per placement in the record, but nothing in the
    dashboard could set them, so in practice every placement of a component
    shared one configuration -- "configs that are global", exactly.
    """
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={
        "v.panel.main_left.0": "calendar",
        "o.panel.main_left.0.url": "https://example.invalid/trabajo.ics",
        "v.panel.main_right.0": "calendar",
        "o.panel.main_right.0.url": "https://example.invalid/casa.ics"})
    view = layout.view_for(registry.load(cache)[EPD], "panel")
    urls = {p["region"]: p["options"].get("url") for p in view["placements"]}
    assert urls == {"main_left": "https://example.invalid/trabajo.ics",
                    "main_right": "https://example.invalid/casa.ics"}


def test_two_calendars_become_two_fetches(ctx):
    from homescreen import fetch
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={
        "v.panel.main_left.0": "calendar",
        "o.panel.main_left.0.url": "https://example.invalid/trabajo.ics",
        "v.panel.main_right.0": "calendar",
        "o.panel.main_right.0.url": "https://example.invalid/casa.ics"})
    plan = fetch.derive(registry.load(cache), CFG)
    urls = {j.params.get("url") for j in plan.values() if j.provider == "ics"}
    assert len(urls) == 2


def test_the_form_offers_each_placement_its_own_fields(ctx):
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={
        "v.panel.main_left.0": "calendar",
        "o.panel.main_left.0.url": "https://example.invalid/trabajo.ics"})
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    assert 'name="o.panel.main_left.0.url"' in html
    assert "trabajo.ics" in html, "and shows what that placement holds"


def test_one_placements_settings_do_not_follow_the_other(ctx):
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={
        "v.panel.markets.0": "quotes", "o.panel.markets.0.symbols": "AAPL",
        "v.panel.markets.1": "quotes", "o.panel.markets.1.symbols": "MSFT"})
    view = layout.view_for(registry.load(cache)[EPD], "panel")
    symbols = sorted(p["options"].get("symbols") for p in view["placements"])
    assert symbols == ["AAPL", "MSFT"]


def test_a_component_with_no_options_keeps_working(ctx):
    client, cache = ctx
    _dashboard(client, cache)
    r = client.post(f"/device/{EPD}/views", data={"v.panel.masthead.0": "status"})
    assert r.status_code in (302, 303)
    view = layout.view_for(registry.load(cache)[EPD], "panel")
    assert view["placements"][0]["component"] == "status"


def test_each_placement_can_hold_its_own_credential(ctx):
    # "Different claude accounts in different UIs" -- scoped to the PLACEMENT,
    # so two of the same component on one screen do not share one key.
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={
        "v.panel.main_left.0": "claude", "o.panel.main_left.0.days": "30",
        "v.panel.main_right.0": "claude", "o.panel.main_right.0.days": "7"})
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    scopes = set(re.findall(r'name="scope" value="([^"]+)"', html))
    assert len(scopes) == 2, f"one credential field per placement, got {scopes}"
