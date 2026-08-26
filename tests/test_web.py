# tests/test_web.py
"""The status page's whole job is telling a human what state the system is in.

Mutation testing found that hardcoding "0 aircraft" or "healthy" passed the
entire suite -- behavioural regressions a human would see and no test would.
These assert the rendered values, not merely that the page renders.
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


# --- the page must show the real values ------------------------------------

def _status(**over):
    base = {"service": "homescreen", "version": "abc1234", "server_time": 1,
            "uptime_s": 3661,
            "feed": {"source": "adsb.fi", "endpoint": "https://x",
                     "fetch_seconds": 3},
            "devices": [], "fleet": []}
    base.update(over)
    return base


def _device(**over):
    base = {"id": "radar", "kind": "gc9a01_client", "render": "device",
            "feed_name": "adsb", "poll_seconds": 5,
            "feed": {"ok": True, "age_s": 2.4, "aircraft": 17,
                     "fetched_at": "2026-08-26T10:00:00+02:00", "error": None},
            "endpoints": {"data": "/api/display/radar/data",
                          "health": "/api/display/radar/health"},
            "last_telemetry": None}
    base.update(over)
    return base


def test_the_aircraft_count_is_the_real_one(monkeypatch):
    # `assert "3" in body` was the previous check; "3" appears in #e3e3e3 and
    # in the version string, so it held with zero aircraft.
    html = web.render_home(_status(devices=[_device()]))
    assert ">17<" in html or "17</dd>" in html, "the real count must appear"
    other = web.render_home(_status(devices=[
        _device(feed={"ok": True, "age_s": 1.0, "aircraft": 42,
                      "fetched_at": "x", "error": None})]))
    assert "42" in other and "17" not in other.replace("abc1234", "")


def test_a_healthy_feed_and_a_stale_one_render_differently():
    healthy = web.render_home(_status(devices=[_device()]))
    stale = web.render_home(_status(devices=[
        _device(feed={"ok": False, "age_s": 900.0, "aircraft": 3,
                      "fetched_at": "x", "error": "no route to host"})]))
    assert "healthy" in healthy and "healthy" not in stale
    assert "stale" in stale
    assert "no route to host" in stale, "the reason reaches the reader"


def test_a_feed_that_never_fetched_says_so():
    html = web.render_home(_status(devices=[
        _device(feed={"ok": False, "age_s": 0.0, "aircraft": 0,
                      "fetched_at": None, "error": None})]))
    assert "never" in html.lower()


def test_a_pixel_push_device_is_not_reported_as_a_dead_feed():
    html = web.render_home(_status(devices=[_device(feed=None)]))
    assert "no feed" in html
    assert "stale" not in html


def test_the_version_and_uptime_are_rendered():
    html = web.render_home(_status(version="deadbee-dirty", uptime_s=3661))
    assert "deadbee-dirty" in html
    assert "1h 1m" in html


def test_the_upstream_feed_block_shows_provider_endpoint_and_cadence():
    html = web.render_home(_status())
    assert "adsb.fi" in html and "https://x" in html and "3s" in html


# --- the fleet section ------------------------------------------------------

def _fleet(**over):
    base = {"hw": "a4cf12ab3c44", "name": "radar", "scene": "planes",
            "fw": "0.2.0", "poll_seconds": 5, "online": True,
            "last_seen": "2026-08-26T10:00:00+02:00",
            "first_seen": "2026-08-26T09:00:00+02:00",
            "caps": {"w": 240, "h": 240}, "telemetry": {}}
    base.update(over)
    return base


def test_online_and_offline_render_differently():
    on = web.render_home(_status(fleet=[_fleet()]))
    off = web.render_home(_status(fleet=[_fleet(online=False)]))
    assert "online" in on and "offline" not in on
    assert "offline" in off


def test_the_header_counts_devices_and_how_many_are_up():
    html = web.render_home(_status(fleet=[
        _fleet(), _fleet(hw="b", name="two", online=False)]))
    assert "2 device(s) registered" in html
    assert "1 online" in html


def test_an_unnamed_device_still_shows_its_hardware_id():
    html = web.render_home(_status(fleet=[_fleet(name=None)]))
    assert "a4cf12ab3c44" in html, "so a human can adopt it"
    assert "unnamed" in html


def test_declared_geometry_is_shown_when_present():
    assert "240x240" in web.render_home(_status(fleet=[_fleet()]))
    assert "240x240" not in web.render_home(_status(fleet=[_fleet(caps={})]))


def test_an_empty_fleet_says_so_rather_than_rendering_nothing():
    html = web.render_home(_status())
    assert "no devices have called in yet" in html


def test_telemetry_is_shown_when_a_device_has_reported_any():
    html = web.render_home(_status(fleet=[_fleet(telemetry={"rssi": "-64"})]))
    assert "rssi=-64" in html


# --- escaping ---------------------------------------------------------------

@pytest.mark.parametrize("field,value", [
    ("name", "<b>bold"), ("scene", "<i>x"), ("fw", "<script>a"),
    ("hw", "<em>h"),
])
def test_every_device_supplied_field_is_escaped(field, value):
    html = web.render_home(_status(fleet=[_fleet(**{field: value})]))
    assert value not in html
    assert "&lt;" in html


def test_telemetry_values_are_escaped():
    html = web.render_home(_status(fleet=[
        _fleet(telemetry={"x": "<script>alert(1)</script>"})]))
    assert "<script>" not in html


# --- the page must be self-contained ---------------------------------------

def test_the_page_makes_no_external_requests():
    html = web.render_home(_status(devices=[_device()], fleet=[_fleet()]))
    for scheme in ("http://", "//fonts.", "<script src"):
        assert scheme not in html, f"{scheme} would break on a LAN with no WAN"


def test_the_page_renders_with_everything_missing():
    # A partially-built status dict must not take the page down.
    assert web.render_home({"version": "v", "uptime_s": 0,
                            "feed": {"source": None, "endpoint": None,
                                     "fetch_seconds": None}})
