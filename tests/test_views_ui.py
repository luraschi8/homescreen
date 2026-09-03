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


def test_an_empty_view_can_be_created_but_never_shown(ctx):
    # Both halves matter, and they used to be in conflict. An empty view has
    # to EXIST or "añadir una vista" is a control that reports success and
    # does nothing -- its slots only appear on the page once it exists. But it
    # must never be the view that renders: a screen showing an empty view
    # shows nothing while looking configured.
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={
        "v.panel.masthead.0": "clock", "new_view": "vacía"})
    rec = registry.load(cache)[EPD]
    assert "vacía" in layout.view_names(rec), "it exists, to be filled"
    assert layout.view_for(rec, "vacía")["placements"], \
        "and never renders as itself"


def test_a_screen_cannot_be_left_with_no_view_at_all(ctx):
    client, cache = ctx
    _dashboard(client, cache)
    before = layout.view_names(registry.load(cache)[EPD])
    r = client.post(f"/device/{EPD}/views", data={})
    from urllib.parse import unquote_plus
    assert "al menos una vista" in unquote_plus(r.headers["Location"])
    assert layout.view_names(registry.load(cache)[EPD]) == before


def test_a_component_this_screen_cannot_draw_is_offered_disabled(ctx):
    # A bare `"disabled" in html` was satisfied by unrelated hidden fieldsets
    # and a line of JS, so the attribute could be dropped from the option
    # entirely and this still passed. Match the option itself, and the reason.
    client, cache = ctx
    _dashboard(client, cache)
    # The e-paper's markets band: 764x62 whole, 127x62 when six-up. Judged
    # against the DEVICE it all fits, which is why the original assertion
    # found nothing and passed on unrelated markup instead.
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    disabled = re.findall(r"<option[^>]*\sdisabled[^>]*>([^<]*)</option>", html)
    assert disabled, "a component that will not fit is offered, greyed out"
    assert any("\u2014" in text for text in disabled), (
        "and says why, rather than being silently missing", disabled)


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


# --- A view name is operator input ------------------------------------------

HOSTILE = 'x" onfocus=alert(1) autofocus q="'


def test_a_view_name_cannot_break_out_of_the_attribute_it_is_written_into(ctx):
    # The dashboard is unauthenticated on the LAN by design, which makes a
    # stored name that executes a far worse trade than it would be behind a
    # login: anyone who can reach the page can also write the name.
    #
    # Asserts the INVARIANT, not a substring. `onfocus=alert(1)` survives as
    # inert text inside a quoted title once the quotes are gone, and a test
    # that forbids the text rather than the breakout fails on something
    # harmless while missing the thing that matters.
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={
        "v.panel.masthead.0": "clock",
        "new_view": HOSTILE,
        f"v.{HOSTILE}.masthead.0": "clock"})
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    # The page has its own <script>; what must not happen is the name landing
    # inside one, or ending an attribute so it becomes markup.
    for block in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S):
        assert "alert(" not in block, block[:200]
    for name in layout.view_names(registry.load(cache)[EPD]):
        assert not set(name) & set("\"'<>&"), (
            f"stored view name {name!r} carries a character that ends an "
            f"attribute")


def test_a_view_name_keeps_its_accents_and_loses_only_what_is_dangerous():
    # The UI is Spanish. Folding to ASCII would trade an injection bug for a
    # legibility one, so only the attribute-ending and separator characters go.
    from homescreen.web import views_ui
    assert views_ui.safe_view_name("mañana") == "mañana"
    assert views_ui.safe_view_name("Casa · Día") == "Casa · Día"
    assert views_ui.safe_view_name("a.b") == "a-b"        # the field separator
    assert views_ui.safe_view_name("   ") == "vista"
    assert views_ui.safe_view_name("") == "vista"
    got = views_ui.safe_view_name(HOSTILE)
    assert '"' not in got and "<" not in got and "." not in got


def test_an_accented_view_name_survives_being_stored(ctx):
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={
        "v.panel.masthead.0": "clock",
        "new_view": "mañana",
        "v.mañana.masthead.0": "weather"})
    assert "mañana" in layout.view_names(registry.load(cache)[EPD])


def test_a_name_the_operator_typed_still_round_trips_through_the_form(ctx):
    # The name it renders and the name it parses have to agree, or saving a
    # view would silently empty it.
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={
        "v.panel.masthead.0": "clock",
        "new_view": "Casa \u00b7 D\u00eda",
        "v.Casa \u00b7 D\u00eda.masthead.0": "weather"})
    assert "Casa \u00b7 D\u00eda" in layout.view_names(registry.load(cache)[EPD])


def test_a_slot_is_judged_at_the_size_it_will_actually_get(ctx):
    # The editor used to judge every slot against the whole device, so a region
    # dividing four ways said "this fits" and then rendered the component into
    # a quarter of that. Slot 0 of main_left is the full 417x335 column; the
    # last slot is 417x83 and a radar does not belong in it.
    client, cache = ctx
    _dashboard(client, cache)
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    rows = re.findall(r'<select name="v\.panel\.main_left\.(\d)"[^>]*>(.*?)</select>',
                      html, re.S)
    assert rows, "the column offers its slots"
    by_index = dict(rows)
    def refused(markup):
        return set(re.findall(r'<option value="(\w+)"[^>]*\sdisabled', markup))
    assert "planes" not in refused(by_index["0"]), "the whole column fits a radar"
    assert "planes" in refused(by_index["3"]), "one quarter of it does not"


def test_the_markets_band_can_hold_the_ticker_it_was_designed_for(ctx):
    # SPEC §9's markets row is six cells of 127x62. Every component declared
    # `min_short: 90`, so the band that row exists for could hold nothing at
    # all -- the requirement was a square-panel rule applied to a strip.
    from homescreen import layout, scenes
    caps = {"w": 800, "h": 480, "depth": 1}
    spec = layout.regions(caps, "dashboard")["markets"]
    x, y, w, h = layout.slots(spec, 6)[5]
    cell = {**caps, "w": w, "h": h}
    assert scenes.supports("quotes", cell)[0], "the ticker fits its own band"
    assert not scenes.supports("planes", cell)[0], "the radar still does not"


# --- the visual builder ------------------------------------------------------
#
# The arrangement was a list of dropdowns, which is a description of a layout
# rather than a picture of one. SPEC §9's dashboard is a shape, and choosing
# what goes in it should look like the thing being chosen.

def _map_slots(html):
    return re.findall(r'<div class="mslot[^"]*"[^>]*data-for="([^"]+)"[^>]*'
                      r'style="([^"]+)"[^>]*>(.*?)</div>', html, re.S)


def test_the_builder_draws_one_box_per_slot(ctx):
    client, cache = ctx
    _dashboard(client, cache)
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    got = _map_slots(html)
    # dashboard: masthead 1 + main_left 5 + main_right 3 + markets 6.
    # main_left holds five because the original design stacks clock, sun
    # times, agenda, deliveries and sport in it.
    assert len(got) == 15, [g[0] for g in got]


def test_every_box_points_at_the_control_that_fills_it(ctx):
    # The picture and the form are the same state. A box that named a select
    # which does not exist would be a picture of a different layout.
    client, cache = ctx
    _dashboard(client, cache)
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    for name, _, _ in _map_slots(html):
        assert f'<select name="{name}"' in html, name


def test_the_boxes_are_positioned_in_the_panels_own_proportions(ctx):
    # The masthead spans the panel's inner width -- SPEC §9's "Outer padding:
    # 18px left/right" is a panel rule and the masthead is on the panel -- and
    # the top ninth of its height. The wireframe shows what the panel does, so
    # it moved when the region did.
    client, cache = ctx
    _dashboard(client, cache)
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    style = dict(_map_slots(html))[
        "v.panel.masthead.0"] if False else None
    for name, css, _ in _map_slots(html):
        if name == "v.panel.masthead.0":
            style = css
    assert style, "the masthead is drawn"
    flat = style.replace(" ", "")
    assert "left:2.25" in flat, flat
    assert "width:95.5" in flat, flat
    assert "top:0" in flat, flat


def test_the_markets_band_is_drawn_in_the_sketchs_proportions(ctx):
    # The FX box is flex 1.55 against five tickers at 1, so the first cell
    # must be visibly wider than the rest rather than a sixth like them.
    client, cache = ctx
    _dashboard(client, cache)
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    widths = {}
    for name, css, _ in _map_slots(html):
        if ".markets." in name:
            found = re.search(r"width:([\d.]+)%", css.replace(" ", ""))
            widths[name] = float(found.group(1))
    assert len(widths) == 6, widths
    first = widths["v.panel.markets.0"]
    rest = [v for k, v in widths.items() if k != "v.panel.markets.0"]
    assert all(first > r * 1.4 for r in rest), widths


def test_an_assigned_slot_is_labelled_with_what_is_in_it(ctx):
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={
        "v.panel.masthead.0": "clock",
        "v.panel.main_left.0": "calendar"})
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    labels = {name: body for name, _, body in _map_slots(html)}
    assert "reloj" in labels["v.panel.masthead.0"]
    assert "agenda" in labels["v.panel.main_left.0"]


def test_an_empty_slot_reads_as_empty_rather_than_blank(ctx):
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={"v.panel.masthead.0": "clock"})
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    labels = {name: body for name, _, body in _map_slots(html)}
    assert labels["v.panel.markets.5"].strip(), "an empty cell still says so"


def test_a_multi_region_screen_configures_its_components_in_one_place(ctx):
    # Two forms writing one value is how they come to disagree. On a screen
    # with regions, the arrangement owns every component and its settings, so
    # the single-component picker above it is a second control for the same
    # thing -- and the one that silently loses.
    client, cache = ctx
    _dashboard(client, cache)
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    assert 'name="scene"' not in html, "no second component picker"
    assert 'name="v.panel.masthead.0"' in html, "the arrangement is the picker"
    assert 'name="name"' in html, "renaming the screen still lives up there"


def test_a_single_region_screen_keeps_its_picker(ctx):
    # The round panel has one full-bleed region, so the arrangement editor does
    # not render at all. Hiding the picker there would leave no way to assign.
    client, cache = ctx
    html = client.get(f"/device/{ROUND}").get_data(as_text=True)
    assert 'name="scene"' in html


def test_the_first_panels_heading_describes_what_is_actually_in_it(ctx):
    client, cache = ctx
    _dashboard(client, cache)
    arranged = client.get(f"/device/{EPD}").get_data(as_text=True)
    simple = client.get(f"/device/{ROUND}").get_data(as_text=True)
    # On a composed screen the card holds a name and a cadence and nothing
    # else, so it says so. "Esta pantalla" named no action, and the identical
    # card was titled differently on the two pages -- which reads as
    # inconsistency because it is. A single-region screen's card really does
    # decide what it shows, so there it keeps its old name.
    assert "Nombre y cadencia" in arranged and "Qué muestra" not in arranged
    assert "Qué muestra" in simple


# --- proportions and headings in the builder ----------------------------------

def test_each_slot_offers_a_heading_and_a_share(ctx):
    client, cache = ctx
    _dashboard(client, cache)
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    assert 'name="l.panel.main_left.0"' in html, "a heading field"
    assert 'name="wt.panel.main_left.0"' in html, "a share field"


def test_a_heading_and_a_share_survive_the_round_trip(ctx):
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={
        "v.panel.main_left.0": "clock",
        "l.panel.main_left.0": "RELOJ",
        "wt.panel.main_left.0": "2.5",
        "v.panel.main_left.1": "calendar",
        "l.panel.main_left.1": "AGENDA",
        "wt.panel.main_left.1": "1"})
    from homescreen import registry
    placements = registry.load(cache)[EPD]["views"]["panel"]["placements"]
    by_id = {p["component"]: p for p in placements}
    assert by_id["clock"]["label"] == "RELOJ"
    assert by_id["clock"]["weight"] == 2.5
    assert by_id["calendar"]["label"] == "AGENDA"


def test_the_map_draws_the_share_that_was_asked_for(ctx):
    # The picture has to show the proportions, or it is a picture of a
    # different layout than the one being saved.
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={
        "v.panel.main_left.0": "clock", "wt.panel.main_left.0": "3",
        "v.panel.main_left.1": "calendar", "wt.panel.main_left.1": "1"})
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    heights = {}
    for name, css, _ in _map_slots(html):
        if ".main_left." in name:
            found = re.search(r"height:([\d.]+)%", css.replace(" ", ""))
            if found:
                heights[name] = float(found.group(1))
    first = heights["v.panel.main_left.0"]
    second = heights["v.panel.main_left.1"]
    assert first > second * 2, heights


def test_a_share_left_blank_defers_to_the_template(ctx):
    # This test used to assert a blank share stored 1.0, which is what caused
    # the bug: an explicit 1.0 is a DECISION and it shadows the template's own
    # proportions. Blank means "no opinion", and the region keeps its shape.
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={
        "v.panel.markets.0": "quotes", "wt.panel.markets.0": "",
        "v.panel.markets.1": "quotes", "wt.panel.markets.1": ""})
    from homescreen import layout, registry
    rec = registry.load(cache)[EPD]
    placements = rec["views"]["panel"]["placements"]
    assert all(p["weight"] is None for p in placements), placements
    caps = registry.clean_caps(rec["caps"])
    spec = layout.regions(caps, "dashboard")["markets"]
    widths = [r[2] for r in
              layout.slots(spec, 2, [p["weight"] for p in placements])]
    assert widths[0] > widths[1] * 1.4, widths


def test_the_map_draws_the_blocks_at_the_size_the_panel_will_draw_them(ctx):
    # The map divided a region by its CAPACITY while the renderer divides it
    # by what is actually IN it. With three blocks in a five-slot column the
    # picture showed them at 55/68/34px with empty space below, and the panel
    # rendered them at 116/146/73 filling the column. A picture that
    # systematically understates every block is worse than no picture.
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={
        "v.panel.main_left.0": "clock",
        "v.panel.main_left.1": "calendar",
        "v.panel.main_left.2": "sport"})
    html = client.get(f"/device/{EPD}").get_data(as_text=True)

    from homescreen import layout, registry
    caps = registry.clean_caps(registry.load(cache)[EPD]["caps"])
    view = registry.load(cache)[EPD]["views"]["panel"]
    _, _, _, column = layout.regions(caps, "dashboard")["main_left"]["rect"]

    drawn = {}
    for name, css, _ in _map_slots(html):
        if ".main_left." in name:
            found = re.search(r"height:([\d.]+)%", css.replace(" ", ""))
            if found:
                drawn[name] = float(found.group(1)) / 100 * caps["h"]
    filled = [drawn[f"v.panel.main_left.{i}"] for i in range(3)]
    assert abs(sum(filled) - column) <= 3, (filled, column)


def test_the_map_still_offers_the_slots_that_are_free(ctx):
    # Sizing by what is filled must not make an empty slot disappear -- it is
    # where the next block goes, and it has to stay a target.
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={"v.panel.main_left.0": "clock"})
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    names = [n for n, _, _ in _map_slots(html) if ".main_left." in n]
    assert len(names) == 5, names


def test_a_slot_is_judged_at_the_size_its_own_weight_buys_it(ctx):
    # The builder measured every slot at an EVEN share while the compositor
    # divides by weight, so a block given a large share was offered the picker
    # for a small one -- components refused for not fitting a rectangle they
    # were never going to be drawn in.
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={
        "v.panel.main_left.0": "clock",  "wt.panel.main_left.0": "5",
        "v.panel.main_left.1": "status", "wt.panel.main_left.1": "1"})
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    rows = dict(re.findall(
        r'<select name="v\.panel\.main_left\.(\d)"[^>]*>(.*?)</select>', html, re.S))

    def refused(markup):
        return set(re.findall(r'<option value="(\w+)"[^>]*\sdisabled', markup))

    # 5:1 of a 335px column is 279 against 56. The tall block fits a radar;
    # the short one does not, and each has to be told the truth.
    assert "planes" not in refused(rows["0"]), "the big share fits a radar"
    assert "planes" in refused(rows["1"]), "the small one does not"


# --- saving must not destroy what the form could not show ---------------------

def test_saving_with_no_edits_keeps_a_view_on_another_template(ctx):
    # The form renders every view against ONE template's regions. A view on a
    # different template has none of its fields in the page, so it posts
    # nothing, and "an empty view is not a view" then deleted it -- reporting
    # success. Pressing Guardar twice with no edits destroyed data.
    client, cache = ctx
    _dashboard(client, cache)
    client.put(f"/api/devices/{EPD}/schedule", json={
        "views": {
            "panel": {"template": "dashboard", "placements": [
                {"id": "m", "region": "masthead", "component": "clock",
                 "options": {}}]},
            "otra": {"template": "split", "placements": [
                {"id": "t", "region": "top", "component": "weather",
                 "options": {}}]}},
        "schedule": {"default": "panel", "slots": []}})

    from homescreen import registry
    before = sorted(registry.load(cache)[EPD]["views"])
    assert before == ["otra", "panel"], before

    client.post(f"/device/{EPD}/views",
                data={"v.panel.masthead.0": "clock"})
    after = sorted(registry.load(cache)[EPD]["views"])
    assert after == ["otra", "panel"], f"a save wiped a view: {after}"


def test_adding_a_view_actually_adds_it(ctx):
    # The field parsed, the redirect said "guardadas", and nothing was
    # created: a new view has no slots in the page yet, so a browser can only
    # post its name, and a nameless-and-empty view was dropped. The hint under
    # the field promised exactly the behaviour the code forbade -- and with it
    # went the schedule editor, which needs a second view to be reachable.
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={
        "v.panel.masthead.0": "clock", "new_view": "noche"})
    from homescreen import layout, registry
    names = layout.view_names(registry.load(cache)[EPD])
    assert "noche" in names, names


def test_a_view_added_from_the_form_can_then_be_filled(ctx):
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={
        "v.panel.masthead.0": "clock", "new_view": "noche"})
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    assert 'name="v.noche.masthead.0"' in html, "its slots are on the page"
    client.post(f"/device/{EPD}/views", data={
        "v.panel.masthead.0": "clock",
        "v.noche.masthead.0": "blank"})
    from homescreen import registry
    views = registry.load(cache)[EPD]["views"]
    assert views["noche"]["placements"][0]["component"] == "blank"


def test_a_view_name_set_through_the_api_cannot_inject_into_the_page(ctx):
    # `safe_view_name` guards the FORM. `PUT /schedule` stores the name as
    # given, and `_placement_options` was the one place interpolating it
    # without escaping -- so the API was a way round the sanitiser and onto
    # the operator's page. Reachable only when the placement's component has
    # an option schema, which most of them do.
    client, cache = ctx
    _dashboard(client, cache)
    client.put(f"/api/devices/{EPD}/schedule", json={
        "views": {'x"><script>alert(1)</script>': {
            "template": "dashboard", "placements": [
                {"id": "m", "region": "masthead", "component": "clock",
                 "options": {}}]}},
        "schedule": {"default": "panel", "slots": []}})
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert 'name="o.x"><script>' not in html


def test_a_free_slot_is_big_enough_to_click(ctx):
    # Sizing filled slots by what is filled left the free ones with nothing:
    # `slots()` tiles exactly, so the remainder was always zero and every
    # empty box came out 1 device pixel tall. The script makes those boxes the
    # click-to-focus targets, so once a region held one block you could not
    # click to add the next.
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={"v.panel.main_left.0": "clock"})
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    caps_h = 480
    for name, css, _ in _map_slots(html):
        if ".main_left." not in name:
            continue
        found = re.search(r"height:([\d.]+)%", css.replace(" ", ""))
        assert found, (name, css)
        pixels = float(found.group(1)) / 100 * caps_h
        assert pixels >= 14, f"{name} is {pixels:.1f}px tall — unclickable"


# --- choosing the arrangement -------------------------------------------------
#
# The dashboard template was unreachable from the web UI entirely. A device
# registers on `single`, so it has one region, so the builder was suppressed --
# and the builder was the only place that could have offered a template. The
# only way in was to PUT JSON at the schedule API.

def test_a_screen_that_could_hold_more_is_offered_the_choice(ctx):
    client, _ = ctx
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    assert 'name="template"' in html, "no way to choose an arrangement"
    for label in ("una sola cosa", "dos mitades", "panel compuesto"):
        assert label in html, label


def test_a_screen_with_no_choice_is_not_offered_one(ctx):
    # The round panel fits only `single`. A select with one option is noise.
    client, _ = ctx
    html = client.get(f"/device/{ROUND}").get_data(as_text=True)
    assert 'name="template"' not in html


def test_choosing_the_composed_panel_makes_its_regions_appear(ctx):
    client, cache = ctx
    client.post(f"/device/{EPD}/views", data={"template": "dashboard"})
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    for region in ("masthead", "main_left", "main_right", "markets"):
        assert f'name="v.panel.{region}.0"' in html, region


def test_the_chosen_arrangement_is_what_the_screen_is_served(ctx):
    client, cache = ctx
    client.post(f"/device/{EPD}/views", data={"template": "dashboard"})
    from homescreen import layout, registry
    # `chosen_template`, not `template_of(view_for(...))`: the view they just
    # created is empty, and an empty view never renders, so the display path
    # would answer `single` for an arrangement that is really `dashboard`.
    assert layout.chosen_template(registry.load(cache)[EPD]) == "dashboard"


def test_changing_the_arrangement_keeps_what_still_fits(ctx):
    # `split` has `top`; `dashboard` does not. A placement naming a region the
    # new arrangement lacks cannot be relocated -- moving it would invent a
    # layout nobody chose -- so it goes, and one that still fits stays.
    client, cache = ctx
    client.post(f"/device/{EPD}/views", data={"template": "split"})
    client.post(f"/device/{EPD}/views", data={
        "template": "split", "v.panel.top.0": "clock"})
    from homescreen import registry
    before = registry.load(cache)[EPD]["views"]["panel"]["placements"]
    assert [p["component"] for p in before] == ["clock"]

    client.post(f"/device/{EPD}/views", data={"template": "dashboard"})
    rec = registry.load(cache)[EPD]
    assert rec["views"]["panel"]["template"] == "dashboard"


def test_an_arrangement_change_never_leaves_a_screen_with_no_view(ctx):
    # Every placement can legitimately be dropped by a template change, and
    # the screen must survive it: the view stays, empty, ready to be filled.
    client, cache = ctx
    client.post(f"/device/{EPD}/views", data={"template": "dashboard"})
    from homescreen import layout, registry
    assert layout.view_names(registry.load(cache)[EPD]), "a view survives"


# --- the preview has to be of THIS panel --------------------------------------
#
# Every preview rendered the round panel's DRAW LIST: five vertical slots, a
# black ground and colour tones. Shown on an 800x480 e-paper page that is the
# wrong layout engine, the wrong palette and the wrong aspect -- the one place
# in the UI that claims to show what the panel will look like.

def test_a_composed_screen_previews_the_arrangement_not_the_components(ctx):
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={
        "v.panel.masthead.0": "clock",
        "v.panel.main_left.0": "calendar"})
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    assert f"/api/devices/{EPD}/view.html" in html, \
        "no preview of the arrangement"


def test_the_arrangement_preview_is_the_page_the_panel_receives(ctx):
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={
        "v.panel.masthead.0": "clock",
        "v.panel.main_left.0": "calendar"})
    got = client.get(f"/api/devices/{EPD}/view.html")
    assert got.status_code == 200
    body = got.get_data(as_text=True)
    # The real composed document: absolute region rectangles, both blocks.
    assert "position:absolute" in body
    assert body.count("rg-") >= 2
    assert "800px" in body and "480px" in body


def test_a_data_push_panel_keeps_the_preview_it_actually_executes(ctx):
    # The round display renders the instruction list itself, so an SVG of that
    # list IS what it will draw. Nothing to change there.
    client, _ = ctx
    html = client.get(f"/device/{ROUND}").get_data(as_text=True)
    assert "preview.svg" in html


def test_a_one_bit_preview_is_not_a_colour_negative(ctx):
    client, cache = ctx
    _dashboard(client, cache)
    svg = client.get(
        f"/api/devices/{EPD}/preview.svg?view=clock").get_data(as_text=True)
    assert "#ffd23f" not in svg and "#6f6d6f" not in svg
    assert 'fill="#fff"' in svg, "paper, not a black ground"


def test_a_heading_on_a_single_slot_region_survives_a_save(ctx):
    # The heading and share fields were rendered only when a region holds more
    # than one, while `parse` read them unconditionally -- so the masthead's
    # heading was silently erased by re-posting the form with no edits.
    client, cache = ctx
    _dashboard(client, cache)
    client.post(f"/device/{EPD}/views", data={
        "v.panel.masthead.0": "clock", "l.panel.masthead.0": "CABECERA"})
    from homescreen import registry
    stored = registry.load(cache)[EPD]["views"]["panel"]["placements"]
    assert stored[0]["label"] == "CABECERA"

    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    assert 'name="l.panel.masthead.0"' in html, "the field is on the page"
    client.post(f"/device/{EPD}/views", data={
        "v.panel.masthead.0": "clock", "l.panel.masthead.0": "CABECERA"})
    again = registry.load(cache)[EPD]["views"]["panel"]["placements"]
    assert again[0]["label"] == "CABECERA"


def test_the_share_control_says_what_it_is(ctx):
    # It was a bare number box explained only by a `title` tooltip, under an
    # equally bare title box. The one control that decides how a column is
    # divided looked like an unexplained "1" or "2,1" -- a reviewer read it as
    # a row/column coordinate.
    client, cache = ctx
    _dashboard(client, cache)
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    assert "Tamaño" in html
    assert "Título" in html
    assert "Cuánto ocupa frente a sus vecinos" in html


def test_a_multi_line_option_is_editable(ctx):
    # Rendered as a single-line input, a `lines` value came back as one
    # run-on string with the newlines collapsed -- unreadable, and with no way
    # to fix a sports list from the page that configures it.
    client, cache = ctx
    client.put(f"/api/devices/{EPD}/schedule", json={
        "views": {"panel": {"template": "dashboard", "placements": [
            {"id": "a", "region": "main_left", "component": "sport",
             "options": {"teams": "Madrid = futbol:86\nF1 = f1"}}]}},
        "schedule": {"default": "panel", "slots": []}})
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    assert "<textarea" in html, "no multi-line control on the page"
    # ...carrying the value on separate lines, not fused into one string.
    assert "Madrid = futbol:86\nF1 = f1" in html


def test_a_screen_with_several_views_can_edit_them_whatever_its_geometry(ctx):
    # The round panel has one region and one template, so the editor returned
    # "" -- while that same panel carries `tiempo` and `noche` and a
    # 23:00-07:00 schedule switching between them. The schedule editor offered
    # views this page could not create, rename or delete.
    client, _ = ctx
    client.put(f"/api/devices/{ROUND}/schedule", json={
        "views": {"tiempo": {"template": "single", "placements": [
                      {"id": "t", "region": "full", "component": "weather"}]},
                  "noche": {"template": "single", "placements": [
                      {"id": "n", "region": "full", "component": "blank"}]}},
        "schedule": {"default": "tiempo", "tz": "Europe/Madrid", "slots": [
            {"days": [1, 2, 3, 4, 5, 6, 7], "from": "23:00", "to": "07:00",
             "view": "noche"}]}})
    html = client.get(f"/device/{ROUND}").get_data(as_text=True)
    assert "Qué contiene cada vista" in html
    assert "tiempo" in html and "noche" in html


def test_a_stored_weight_does_not_make_the_form_unsubmittable(ctx):
    # `step="0.25"` against a stored 1.4 made the browser refuse the whole
    # arrangement, with the complaint pointing at a field thousands of pixels
    # away -- and in English, in a Spanish UI. The range is enforced server
    # side, where it can explain itself.
    client, cache = ctx
    client.put(f"/api/devices/{EPD}/schedule", json={
        "views": {"panel": {"template": "dashboard", "placements": [
            {"id": "a", "region": "main_left", "component": "clock",
             "weight": 1.4},
            {"id": "b", "region": "main_left", "component": "clock",
             "weight": 2.6}]}},
        "schedule": {"default": "panel", "slots": []}})
    html = client.get(f"/device/{EPD}").get_data(as_text=True)
    assert 'step="0.25"' not in html
    assert 'step="any"' in html


def test_saving_the_schedule_leaves_the_views_alone(ctx):
    # Deriving views through `view_for` made this form rewrite a section it
    # does not own: an empty view is filtered out of `usable`, so the lookup
    # missed and fell through to the DEFAULT view's body -- adding a view and
    # saving the schedule filled the new one with a copy of the old.
    client, cache = ctx
    client.put(f"/api/devices/{ROUND}/schedule", json={
        "views": {"dia": {"template": "single", "placements": [
                      {"id": "d", "region": "full", "component": "weather"}]}},
        "schedule": {"default": "dia", "tz": "Europe/Madrid", "slots": []}})
    # Added the way a person adds one: through the editor's own field, which
    # is what leaves it empty in the first place.
    client.post(f"/device/{ROUND}/views", data={
        "template": "single", "new_view": "manana",
        "v.dia.full.0": "weather"})
    before = client.get(f"/api/devices/{ROUND}/schedule").get_json()["views"]
    client.post(f"/device/{ROUND}/schedule", data={"default": "dia"})
    after = client.get(f"/api/devices/{ROUND}/schedule").get_json()["views"]
    assert after == before, "saving the schedule rewrote the views"
    if "manana" in after:
        assert not (after["manana"].get("placements") or []), \
            "the new view was filled with a copy of another"


def test_a_single_region_screen_with_several_views_has_one_editor(ctx):
    # It kept the legacy component picker AND got the view editor: two forms
    # claiming to decide what it shows, and the picker is inert because
    # `layout.view_for` reads views and never `scene`.
    client, cache = ctx
    client.put(f"/api/devices/{ROUND}/schedule", json={
        "views": {"a": {"template": "single", "placements": [
                      {"id": "x", "region": "full", "component": "weather"}]},
                  "b": {"template": "single", "placements": [
                      {"id": "y", "region": "full", "component": "blank"}]}},
        "schedule": {"default": "a", "tz": "Europe/Madrid", "slots": []}})
    html = client.get(f"/device/{ROUND}").get_data(as_text=True)
    assert "Qué contiene cada vista" in html, "the real editor is missing"
    assert 'name="scene"' not in html, "the inert picker is still offered"
