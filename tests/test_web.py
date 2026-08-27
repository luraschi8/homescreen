# tests/test_web.py
"""The dashboard's whole job is telling a human what state the fleet is in.

Mutation testing found that hardcoding "0 aircraft" or "healthy" passed the
entire suite -- regressions a human would see and no test would. These assert
the rendered values, not merely that a page renders.

The page split (fleet list / one screen / settings) moved several of these
assertions rather than retiring them: telemetry, substitutions and geometry are
facts about ONE screen and now live on its page, and feed health is a fact
about a source and lives with the source. What each test protects is unchanged.
"""
import pytest

from homescreen import web


# --- duration(): every boundary, since a fleet view is mostly ages ----------

@pytest.mark.parametrize("seconds,expected", [
    (0, "0s"), (1, "1s"), (59, "59s"),
    (60, "1m 0s"), (61, "1m 1s"), (3599, "59m 59s"),
    (3600, "1h 0m"), (3661, "1h 1m"), (86399, "23h 59m"),
    (86400, "1d 0h"), (90000, "1d 1h"), (172800, "2d 0h"),
])
def test_duration_boundaries(seconds, expected):
    assert web.duration(seconds) == expected


@pytest.mark.parametrize("seconds", [-1, -60, -100000])
def test_a_negative_uptime_clamps_rather_than_rendering_nonsense(seconds):
    # A Pi 4 has no RTC, so a pre-NTP clock jump can make uptime negative.
    assert web.duration(seconds) == "0s"


def test_duration_truncates_rather_than_rounding():
    assert web.duration(119) == "1m 59s"
    assert web.duration(7199) == "1h 59m"


# --- fixtures ---------------------------------------------------------------

def _status(**over):
    base = {"service": "homescreen", "version": "abc1234", "server_time": 1,
            "uptime_s": 3661,
            "feed": {"source": "adsb.fi", "endpoint": "https://x",
                     "fetch_seconds": 3},
            "devices": [], "fleet": []}
    base.update(over)
    return base


def _source(**over):
    base = {"id": "radar", "kind": "gc9a01_client", "render": "device",
            "feed_name": "adsb", "poll_seconds": 5,
            "feed": {"ok": True, "age_s": 2.4, "aircraft": 17,
                     "fetched_at": "2026-08-26T10:00:00+02:00", "error": None},
            "endpoints": {"data": "/api/display/radar/data"},
            "last_telemetry": None}
    base.update(over)
    return base


def _screen(**over):
    base = {"hw": "a4cf12ab3c44", "name": "radar", "scene": "planes",
            "fw": "0.2.0", "poll_seconds": 5, "online": True, "approved": True,
            "last_seen": "2026-08-26T10:00:00+02:00",
            "first_seen": "2026-08-26T09:00:00+02:00",
            "caps": {"w": 240, "h": 240}, "telemetry": {}, "options": {}}
    base.update(over)
    return base


def _device_page(screen, **kw):
    kw.setdefault("options", [("planes", True, ""), ("clock", True, "")])
    kw.setdefault("schemas", {})
    kw.setdefault("name_max", 32)
    return web.render_device(screen, **kw)


# --- the source's real numbers reach a human --------------------------------

def test_the_aircraft_count_is_the_real_one():
    html = web.render_settings(_status()["feed"], devices=[_source()])
    assert "17" in html


def test_a_healthy_feed_and_a_stale_one_render_differently():
    healthy = web.render_settings({}, devices=[_source()])
    stale = web.render_settings({}, devices=[
        _source(feed={"ok": False, "age_s": 900, "aircraft": 0,
                      "fetched_at": "x", "error": "timeout"})])
    assert "al día" in healthy and "caducado" not in healthy
    assert "caducado" in stale and "timeout" in stale


def test_a_feed_that_never_fetched_says_so():
    html = web.render_settings({}, devices=[
        _source(feed={"ok": False, "age_s": None, "aircraft": 0,
                      "fetched_at": None, "error": None})])
    assert "nunca consultado" in html


def test_a_pixel_push_device_is_not_reported_as_a_dead_feed():
    html = web.render_settings({}, devices=[_source(feed=None)])
    assert "caducado" not in html and "nunca consultado" not in html


def test_the_upstream_feed_block_shows_provider_endpoint_and_cadence():
    html = web.render_settings(_status()["feed"], devices=[])
    assert "adsb.fi" in html and "https://x" in html and "3" in html


def test_the_version_and_uptime_are_rendered():
    html = web.render_fleet(_status())
    assert "abc1234" in html and "1h 1m" in html


# --- the fleet list ---------------------------------------------------------

def test_online_and_offline_render_differently():
    on = web.render_fleet(_status(fleet=[_screen()]))
    off = web.render_fleet(_status(fleet=[_screen(online=False)]))
    assert "en línea" in on and "sin conexión" not in on
    assert "sin conexión" in off


def test_a_device_waiting_to_be_let_in_is_not_counted_as_a_member():
    # It is neither online nor offline -- it is not in the fleet at all, and
    # showing it in the members table would make approval look decorative.
    html = web.render_fleet(_status(fleet=[
        _screen(), _screen(hw="bb", name=None, approved=False)]))
    assert "Quieren unirse (1)" in html
    assert "1 pantalla(s)" in html, "the member count excludes it"


def test_the_header_counts_screens_and_how_many_are_up():
    html = web.render_fleet(_status(fleet=[
        _screen(), _screen(hw="b", name="two", online=False)]))
    assert "2 pantalla(s)" in html and "1 en línea" in html


def test_an_unnamed_device_still_shows_its_hardware_id():
    html = web.render_fleet(_status(fleet=[_screen(name=None)]))
    assert "a4cf12ab3c44" in html, "so a human can adopt it"
    assert "sin nombre" in html


def test_an_empty_fleet_says_so_rather_than_rendering_nothing():
    assert "Ninguna pantalla" in web.render_fleet(_status())


def test_every_row_links_to_the_screens_own_page():
    html = web.render_fleet(_status(fleet=[_screen()]))
    assert 'href="/device/a4cf12ab3c44"' in html


# --- one screen's page ------------------------------------------------------

def test_declared_geometry_is_shown_when_present():
    assert "240\u00d7240" in _device_page(_screen())
    assert "240\u00d7240" not in _device_page(_screen(caps={}))


def test_telemetry_is_shown_when_a_device_has_reported_any():
    assert "rssi=-64" in _device_page(_screen(telemetry={"rssi": "-64"}))


def test_the_page_shows_what_the_server_substituted():
    # A green card for a panel the server is quietly serving something else is
    # worse than no card: the operator stops looking.
    html = _device_page(_screen(unsupported=["radar"],
                                scene_error="fallo en planes: KeyError"))
    # `"radar" in html` held from the device NAME regardless of the list.
    assert "<dt>descartado</dt>" in html
    assert "esta pantalla no lo declara" in html
    assert "fallo en planes: KeyError" in html


def test_a_healthy_screen_carries_no_substitution_rows():
    html = _device_page(_screen())
    assert "<dt>descartado</dt>" not in html
    assert "<dt>escena</dt>" not in html


def test_a_substitution_message_is_escaped():
    html = _device_page(_screen(scene_error="<script>alert(1)</script>"))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_a_screen_waiting_for_approval_offers_the_way_to_admit_it():
    html = _device_page(_screen(approved=False))
    assert "esperando aprobación" in html
    assert 'action="/device/a4cf12ab3c44/approval"' in html


def test_an_admitted_screen_is_offered_removal_and_revocation():
    html = _device_page(_screen())
    assert "Sacar de la flota" in html
    assert 'action="/device/a4cf12ab3c44/remove"' in html


def test_only_the_chosen_components_settings_are_submitted():
    # A hidden input still posts its value, so every unchosen component's
    # options would ride along with the one that was picked.
    html = _device_page(_screen(scene="clock"), schemas={
        "planes": [{"key": "radius_km", "label": "Radio", "type": "int"}],
        "clock": [{"key": "timezone", "label": "Zona", "type": "text"}]})
    assert 'data-scene="clock" style' in html, "the chosen one is enabled"
    assert 'data-scene="planes" hidden disabled' in html


def test_a_timezone_field_offers_the_zones_rather_than_demanding_one():
    # It used to be a free-text box that required knowing "Europe/Madrid".
    html = _device_page(_screen(scene="clock"), schemas={
        "clock": [{"key": "timezone", "label": "Zona", "type": "text",
                   "datalist": "timezones"}]})
    assert 'list="dl-timezones"' in html
    assert '<datalist id="dl-timezones">' in html
    assert 'value="Europe/Madrid"' in html


# --- escaping ---------------------------------------------------------------

@pytest.mark.parametrize("field,value", [
    ("name", "<b>bold"), ("scene", "<i>x"), ("hw", "<em>h"),
])
def test_every_device_supplied_field_is_escaped_on_the_fleet_page(field, value):
    # fw is not on this page -- it is a fact about one screen. It is covered
    # below, where it is actually rendered.
    html = web.render_fleet(_status(fleet=[_screen(**{field: value})]))
    assert value not in html and "&lt;" in html


@pytest.mark.parametrize("field,value", [
    ("name", "<b>bold"), ("scene", "<i>x"), ("fw", "<script>a"),
])
def test_every_device_supplied_field_is_escaped_on_the_device_page(field, value):
    html = _device_page(_screen(**{field: value}))
    assert value not in html and "&lt;" in html


def test_telemetry_values_are_escaped():
    html = _device_page(_screen(telemetry={"x": "<script>alert(1)</script>"}))
    assert "<script>alert(1)</script>" not in html


# --- the pages must be self-contained --------------------------------------

@pytest.mark.parametrize("html", [
    web.render_fleet(_status(fleet=[_screen()])),
    web.render_settings(_status()["feed"], devices=[_source()]),
    web.render_device(_screen(), options=[("clock", True, "")], schemas={},
                      name_max=32),
])
def test_no_page_makes_an_external_request(html):
    # The dashboard is how you debug the network. Needing the network to render
    # it means it stops working exactly when you need it.
    for bad in ("http://", "https://fonts.", "//fonts.", "<script src",
                '<link rel="stylesheet"'):
        assert bad not in html, f"{bad} would break on a LAN with no WAN"


def test_the_fleet_page_renders_with_everything_missing():
    # A partially-built status dict must not take the page down.
    assert web.render_fleet({"version": "v", "uptime_s": 0,
                             "feed": {"source": None, "endpoint": None,
                                      "fetch_seconds": None}})


def test_the_device_page_renders_with_everything_missing():
    assert web.render_device({"hw": "aa"}, options=[], schemas={}, name_max=32)
