# tests/test_config_api.py
import json
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


def _CFG():
    import copy
    return copy.deepcopy(CFG)


@pytest.fixture
def client(tmp_path):
    return create_app(_CFG(), tmp_path, version="t").test_client()


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
    write_cache(tmp_path / "feed" / "adsb.json", {"aircraft": []})
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


# --- overrides.json is written from the network and read on every request -----

def test_a_non_dict_override_value_cannot_500_every_route(tmp_path):
    # `apply` is documented "never raises" and `_live()` calls it per request,
    # so one bad value here is a 500 on every route, not a bad override.
    # Removing the isinstance filter in `load` survived the whole suite.
    from homescreen import overrides
    overrides.overrides_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    overrides.overrides_path(tmp_path).write_text(
        json.dumps({"radar": "not-a-dict", "desk": {"max_aircraft": 30}}))
    loaded = overrides.load(tmp_path)
    assert loaded == {"desk": {"max_aircraft": 30}}, "the good one survives"
    cfg = overrides.apply({"devices": [{"id": "desk"}, {"id": "radar"}]}, tmp_path)
    assert cfg["devices"][0]["max_aircraft"] == 30


@pytest.mark.parametrize("root", ["[]", "null", '"a string"', "42", "{not json"])
def test_a_damaged_overrides_root_degrades_to_no_overrides(tmp_path, root):
    from homescreen import overrides
    overrides.overrides_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    overrides.overrides_path(tmp_path).write_text(root)
    assert overrides.load(tmp_path) == {}
    base = {"devices": [{"id": "desk", "max_aircraft": 12}]}
    assert overrides.apply(base, tmp_path)["devices"][0]["max_aircraft"] == 12


def test_a_non_string_override_key_is_dropped(tmp_path):
    from homescreen import overrides
    overrides.overrides_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    overrides.overrides_path(tmp_path).write_text('{"5": {"max_aircraft": 3}}')
    assert list(overrides.load(tmp_path)) == ["5"], "JSON keys are always strings"


# --- validate the way the consumer consumes -----------------------------------

@pytest.mark.parametrize("value", ["20.5", 20.5, "abc", None, [20]])
def test_a_fractional_aircraft_cap_is_refused_before_it_reaches_the_fetcher(
        tmp_path, value):
    # check_device validated with float(); adsb.py slices with int(). So
    # `max_aircraft: "20.5"` passed validation, passed the startup check, and
    # then raised inside EVERY fetch forever -- one unauthenticated PATCH wedged
    # the feed permanently and /health reported a Python parse error.
    client = create_app(_CFG(), tmp_path, version="t").test_client()
    r = client.patch("/api/config/devices/radar", json={"max_aircraft": value})
    assert r.status_code == 400, r.get_data()
    assert not overrides.overrides_path(tmp_path).exists(), \
        "a rejected override must not reach the card"


def test_a_whole_aircraft_cap_is_still_accepted(tmp_path):
    client = create_app(_CFG(), tmp_path, version="t").test_client()
    assert client.patch("/api/config/devices/radar",
                        json={"max_aircraft": 30}).status_code == 200
    assert client.patch("/api/config/devices/radar",
                        json={"max_aircraft": "30"}).status_code == 200


@pytest.mark.parametrize("value", ["A" * 500, 5, [], {"x": 1}, "maybe"])
def test_show_ground_is_validated_like_every_other_settable_key(tmp_path, value):
    # SETTABLE_KEYS listed six keys and check_device validated five. The
    # unchecked one was a free write channel into a file that `_live()` parses
    # on every request and the fetch daemon re-reads every cycle.
    client = create_app(_CFG(), tmp_path, version="t").test_client()
    assert client.patch("/api/config/devices/radar",
                        json={"show_ground": value}).status_code == 400


@pytest.mark.parametrize("value", [True, False, "true", "no", "1"])
def test_a_real_boolean_is_still_accepted(tmp_path, value):
    client = create_app(_CFG(), tmp_path, version="t").test_client()
    assert client.patch("/api/config/devices/radar",
                        json={"show_ground": value}).status_code == 200


def test_an_oversized_body_is_refused_without_being_parsed(tmp_path):
    # 8 MB of text was accepted, fsynced to the wear-limited microSD, and then
    # re-parsed on every request: 0.11ms became 10.2ms.
    client = create_app(_CFG(), tmp_path, version="t").test_client()
    r = client.patch("/api/config/devices/radar",
                     data=json.dumps({"show_ground": "A" * 8_000_000}),
                     content_type="application/json")
    assert r.status_code == 413
    assert not overrides.overrides_path(tmp_path).exists()


def test_reverting_an_override_on_a_read_only_card_is_a_503(tmp_path):
    # The PATCH 503 was tested and the DELETE 503 was not, though both write to
    # the same card and ext4 remounts read-only on error.
    client = create_app(_CFG(), tmp_path, version="t").test_client()
    assert client.patch("/api/config/devices/radar",
                        json={"max_aircraft": 30}).status_code == 200

    def boom(*a, **k):
        raise OSError(30, "Read-only file system")

    import homescreen.serve as serve_mod
    real = serve_mod.overrides.save
    serve_mod.overrides.save = boom
    try:
        r = client.delete("/api/config/devices/radar")
    finally:
        serve_mod.overrides.save = real
    assert r.status_code == 503
    assert "unavailable" in r.get_json()["error"]


@pytest.mark.parametrize("cfg_devices", [None, "a string", 42, {"id": "x"}])
def test_apply_survives_a_config_whose_devices_are_not_a_list(tmp_path,
                                                              cfg_devices):
    # `apply` is called per request by `_live()`, so anything it raises on is a
    # 500 on every route. Both shape guards survived mutation because no test
    # combined a damaged config WITH a stored overlay.
    overrides.save(tmp_path, {"radar": {"max_aircraft": 30}})
    out = overrides.apply({"devices": cfg_devices}, tmp_path)
    assert out["devices"] == cfg_devices


def test_apply_skips_a_device_entry_that_is_not_a_mapping(tmp_path):
    overrides.save(tmp_path, {"radar": {"max_aircraft": 30}})
    out = overrides.apply(
        {"devices": ["not a dict", {"id": "radar"}, None]}, tmp_path)
    assert out["devices"][0] == "not a dict"
    assert out["devices"][1]["max_aircraft"] == 30
    assert out["devices"][2] is None


def test_a_dotted_override_over_a_scalar_does_not_500_the_config_route(tmp_path):
    # `home: "x"` in config.yaml plus a `home.lat` override: _set_dotted walked
    # into a string. That is a 500 on /api/config and on every device route.
    overrides.save(tmp_path, {"radar": {"home.lat": 41.0}})
    out = overrides.apply({"devices": [{"id": "radar", "home": "x"}]}, tmp_path)
    assert out["devices"][0]["home"] == {"lat": 41.0}
