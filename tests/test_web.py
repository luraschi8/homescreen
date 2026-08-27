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
import homescreen.web.layout  # noqa: F401  (when() is asserted directly)


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
    # The old test warned in its own comment that a bare substring check
    # "held with zero aircraft". I replaced it with `assert "17" in html`,
    # which the palette satisfies on its own -- `--panel:#17191d`. Assert the
    # rendered FIELD, and that a different feed renders differently.
    html = web.render_settings(_status()["feed"], devices=[_source()])
    assert "<dd>17</dd>" in html
    other = web.render_settings(_status()["feed"], devices=[
        _source(feed={"ok": True, "age_s": 2.4, "aircraft": 42,
                      "fetched_at": "x", "error": None})])
    assert "<dd>42</dd>" in other and "<dd>17</dd>" not in other


def test_a_healthy_feed_and_a_stale_one_render_differently():
    healthy = web.render_settings({}, devices=[_source()])
    stale = web.render_settings({}, devices=[
        _source(feed={"ok": False, "age_s": 900, "aircraft": 0,
                      "fetched_at": "x", "error": "timeout"})])
    assert "al día" in healthy and "caducado" not in healthy
    assert "caducado" in stale and "timeout" in stale


def test_a_failure_with_no_reason_does_not_render_a_dangling_dash():
    # ok:false with error:null is reachable -- an unparseable `fetched_at`
    # gets there -- and "caducado — " reads as a truncated message.
    html = web.render_settings({}, devices=[
        _source(feed={"ok": False, "age_s": 1, "aircraft": 0,
                      "fetched_at": "garbage", "error": None})])
    assert "caducado" in html
    assert "caducado \u2014 <" not in html and "caducado \u2014 " not in html


def test_an_absent_value_renders_as_a_dash_not_as_its_entity():
    # dash()/when() feed pill(), which escapes, so returning "&mdash;" put the
    # literal text "&mdash;" on screen. Same bug class as the geometry one.
    html = web.render_fleet(_status(fleet=[
        _screen(approved=False, name=None, first_seen=None)]))
    assert "&amp;mdash;" not in html and "&mdash;" not in html


def test_a_feed_that_never_fetched_says_so():
    html = web.render_settings({}, devices=[
        _source(feed={"ok": False, "age_s": None, "aircraft": 0,
                      "fetched_at": None, "error": None})])
    assert "nunca consultado" in html


def test_a_pixel_push_device_is_shown_but_not_as_a_dead_feed():
    # Asserting only the NEGATIVE would pass on a blank page -- and it did:
    # the renderer skipped these entirely, so a configured screen vanished
    # from the only page that lists sources.
    html = web.render_settings({}, devices=[_source(id="kitchen", feed=None)])
    assert "kitchen" in html, "a screen with no feed of its own is still listed"
    assert "sin fuente propia" in html
    assert "caducado" not in html and "nunca consultado" not in html


def test_the_upstream_feed_block_shows_provider_endpoint_and_cadence():
    # `"3" in html` was vacuous too -- true with fetch_seconds=None.
    html = web.render_settings(_status()["feed"], devices=[])
    assert "adsb.fi" in html and 'value="https://x"' in html
    assert 'value="3"' in html
    blank = web.render_settings({"source": "adsb.fi", "endpoint": None,
                                 "fetch_seconds": None}, devices=[])
    assert 'value="3"' not in blank


def test_where_a_source_is_served_is_shown_somewhere():
    # The old fleet page answered "where is the data served" and the page split
    # dropped it with nowhere to land. It belongs with the source.
    html = web.render_settings({}, devices=[_source()])
    assert "/api/display/radar/data" in html


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


# --- timestamps a person can read -------------------------------------------

def test_a_recent_contact_reads_as_an_age_not_a_timestamp():
    # The column exists to answer "is this thing alive?". Six digits of
    # microseconds and a date is noise while the answer is "seconds ago".
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    assert web.layout.when((now - timedelta(seconds=5)).isoformat()) == "hace 5s"
    assert web.layout.when((now - timedelta(minutes=4)).isoformat()) == "hace 4 min"


def test_an_older_contact_falls_back_to_a_clock_then_a_date():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    today = web.layout.when((now - timedelta(hours=2)).isoformat())
    assert ":" in today and "hace" not in today
    older = web.layout.when((now - timedelta(days=2)).isoformat())
    assert "/" in older


def test_a_stamp_from_the_future_does_not_render_a_negative_age():
    # A Pi 4 has no RTC and jumps when NTP lands, so stamps ahead of now are
    # real. "hace -3s" is worse than useless.
    from datetime import datetime, timedelta, timezone
    ahead = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert "-" not in web.layout.when(ahead)


@pytest.mark.parametrize("bad", [None, "", "not-a-date", 12345, [], {}])
def test_an_unreadable_stamp_is_shown_rather_than_crashing(bad):
    # devices.json is hand-editable; this runs on the page an operator opens
    # to find out what is wrong.
    assert web.layout.when(bad)


def test_destructive_actions_ask_first():
    # Both of these are one click away from losing a screen's configuration.
    fleet = web.render_fleet(_status(fleet=[_screen(approved=False, name=None)]))
    assert "confirm(" in fleet
    page = _device_page(_screen())
    assert "confirm(" in page


def test_the_navigation_is_in_one_language():
    html = web.render_fleet(_status())
    assert ">Flota<" in html and ">Fleet<" not in html


def test_a_scene_the_server_no_longer_knows_is_flagged_not_silently_replaced():
    # With no matching option the browser selects the first one, so pressing
    # Guardar to rename a screen also reassigned it -- with nothing on the page
    # saying the stored assignment had gone unrecognised.
    html = _device_page(_screen(scene="ghostscene"),
                        options=[("clock", True, ""), ("planes", True, "")])
    assert "ya no" in html and "ghostscene" in html, "the operator is told"
    # An empty value means "leave the scene alone" downstream, so a rename
    # stays a rename.
    assert '<option value="" selected>' in html
    assert '<option value="clock" selected>' not in html
