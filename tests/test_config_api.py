# tests/test_config_api.py
from pathlib import Path

import pytest

from homescreen import overrides
from homescreen.serve import create_app

CFG = {
    "feeds": {"adsb": {"source": "api", "endpoint": "https://x", "fetch_seconds": 3}},
    "devices": [{"id": "radar", "kind": "gc9a01_client", "render": "device",
                 "feed": "adsb", "home": {"lat": 40.4168, "lon": -3.7038},
                 "radius_km": 60, "max_aircraft": 40, "poll_seconds": 5}],
}


@pytest.fixture
def client(tmp_path):
    return create_app(CFG, tmp_path, version="t").test_client()


def test_get_config_reports_settable_keys_and_current_values(client):
    body = client.get("/api/config").get_json()
    assert set(body["settable"]) == set(overrides.SETTABLE_KEYS)
    dev = body["devices"][0]
    assert dev["effective"]["max_aircraft"] == 40
    assert dev["effective"]["home.lat"] == pytest.approx(40.4168)
    assert dev["overridden"] == {}


def test_patch_changes_the_effective_value_and_persists(client, tmp_path):
    r = client.patch("/api/config/devices/radar", json={"max_aircraft": 55})
    assert r.status_code == 200
    assert r.get_json()["effective"]["max_aircraft"] == 55
    # Persisted where the OTHER process will find it.
    assert overrides.load(tmp_path)["radar"]["max_aircraft"] == 55
    assert client.get("/api/config").get_json()["devices"][0]["effective"]["max_aircraft"] == 55


def test_patch_takes_effect_on_the_data_endpoint_without_a_restart(client, tmp_path):
    from homescreen.cache import write_cache
    write_cache(tmp_path / "feed" / "radar.json", {"aircraft": []})
    assert client.get("/api/display/radar/data").headers["X-Poll-Seconds"] == "5"
    client.patch("/api/config/devices/radar", json={"poll_seconds": 30})
    assert client.get("/api/display/radar/data").headers["X-Poll-Seconds"] == "30"


def test_nested_home_coordinates_are_settable(client):
    r = client.patch("/api/config/devices/radar",
                     json={"home.lat": 41.0, "home.lon": -4.0})
    assert r.status_code == 200
    assert r.get_json()["effective"]["home.lat"] == 41.0
    assert r.get_json()["effective"]["home.lon"] == -4.0


@pytest.mark.parametrize("body,why", [
    ({"kind": "epaper_client"}, "structural: would re-route the device"),
    ({"id": "other"}, "structural: would rename the cache file"),
    ({"render": "server"}, "structural: would change endpoint routing"),
    ({"feed": "weather"}, "structural: would repoint the cache"),
    ({"fetch_seconds": 1}, "not device-scoped; interacts with the rate limit"),
])
def test_structural_fields_are_not_settable(client, tmp_path, body, why):
    r = client.patch("/api/config/devices/radar", json=body)
    assert r.status_code == 400, why
    assert "settable" in r.get_json()
    assert overrides.load(tmp_path) == {}, "nothing may reach disk"


@pytest.mark.parametrize("body", [
    {"max_aircraft": 0},          # serves nothing
    {"max_aircraft": "lots"},
    {"radius_km": 0},             # fetches nothing, forever
    {"radius_km": -5},
    {"home.lat": 95},             # not a latitude
    {"home.lon": 400},
    {"poll_seconds": 0},
])
def test_invalid_values_are_rejected_before_anything_is_persisted(client, tmp_path, body):
    # This file is written from the network and re-read by the fetch daemon, so
    # an unvalidated write is a remote way to wedge the service.
    r = client.patch("/api/config/devices/radar", json=body)
    assert r.status_code == 400
    assert overrides.load(tmp_path) == {}, f"{body} must not reach disk"


def test_a_rejected_patch_leaves_an_earlier_good_one_intact(client, tmp_path):
    client.patch("/api/config/devices/radar", json={"max_aircraft": 50})
    client.patch("/api/config/devices/radar", json={"max_aircraft": -1})
    assert overrides.load(tmp_path)["radar"]["max_aircraft"] == 50


def test_unknown_device_and_malformed_bodies(client):
    assert client.patch("/api/config/devices/nope", json={"max_aircraft": 5}).status_code == 404
    assert client.patch("/api/config/devices/radar", json={}).status_code == 400
    assert client.patch("/api/config/devices/radar", json=[1, 2]).status_code == 400
    assert client.patch("/api/config/devices/radar", data="not json",
                        content_type="application/json").status_code == 400


def test_delete_reverts_to_the_file(client, tmp_path):
    client.patch("/api/config/devices/radar", json={"max_aircraft": 55})
    assert client.delete("/api/config/devices/radar").status_code == 200
    assert overrides.load(tmp_path) == {}
    assert client.get("/api/config").get_json()["devices"][0]["effective"]["max_aircraft"] == 40


def test_a_corrupt_overrides_file_is_ignored_not_fatal(client, tmp_path):
    # Written from the network, so a bad one must never wedge either daemon.
    overrides.overrides_path(tmp_path).write_text("{not json")
    assert client.get("/api/config").status_code == 200
    assert client.get("/api/status").status_code == 200
    assert overrides.apply(CFG, tmp_path)["devices"][0]["max_aircraft"] == 40


def test_apply_never_mutates_the_loaded_config(tmp_path):
    overrides.save(tmp_path, {"radar": {"max_aircraft": 99}})
    out = overrides.apply(CFG, tmp_path)
    assert out["devices"][0]["max_aircraft"] == 99
    assert CFG["devices"][0]["max_aircraft"] == 40, "the caller's cfg is untouched"


def test_a_hand_written_overlay_cannot_change_structural_fields(tmp_path):
    # Defence in depth: the endpoint's whitelist blocks these, but someone can
    # also edit cache/overrides.json directly on the Pi. Letting `kind` or
    # `feed` through there would re-route a live device's cache and endpoints.
    overrides.save(tmp_path, {"radar": {
        "kind": "epaper_client", "render": "server", "feed": "weather",
        "id": "other", "max_aircraft": 55,
    }})
    dev = overrides.apply(CFG, tmp_path)["devices"][0]
    assert dev["max_aircraft"] == 55, "the settable key still applies"
    assert dev["kind"] == "gc9a01_client"
    assert dev["render"] == "device"
    assert dev["feed"] == "adsb"
    assert dev["id"] == "radar"
