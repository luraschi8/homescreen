# tests/test_devices_api.py
import json
from pathlib import Path

import pytest

from homescreen import registry
from homescreen.serve import create_app

HW = "a4cf12ab3c44"
HW2 = "deadbeef0000"
CFG = {"feeds": {"adsb": {"source": "api", "endpoint": "https://x",
                          "fetch_seconds": 3}},
       "devices": []}


class Clock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def ctx(tmp_path):
    clock = Clock()
    return create_app(CFG, tmp_path, clock=clock, version="t").test_client(), tmp_path, clock


# --- fleet listing ----------------------------------------------------------

def test_empty_fleet_lists_nothing(ctx):
    client, _, _ = ctx
    body = client.get("/api/devices").get_json()
    assert body["devices"] == []
    assert set(body["scenes"]) == set(registry.ASSIGNABLE_SCENES)


def test_builtin_fallback_scenes_are_not_offered_as_assignable(ctx):
    client, _, _ = ctx
    offered = client.get("/api/devices").get_json()["scenes"]
    assert "unassigned" not in offered and "error" not in offered


def test_fleet_reports_identity_state_and_assignment(ctx):
    client, cache, clock = ctx
    registry.touch(cache, HW, fw="0.2.0", caps={"w": 240, "h": 240}, now=clock.t)
    registry.assign(cache, HW, name="radar", scene="planes")
    dev = client.get("/api/devices").get_json()["devices"][0]
    assert (dev["hw"], dev["name"], dev["scene"], dev["fw"]) == \
           (HW, "radar", "planes", "0.2.0")
    assert dev["online"] is True
    assert dev["caps"] == {"w": 240, "h": 240}


def test_a_silent_device_shows_offline(ctx):
    client, cache, clock = ctx
    registry.touch(cache, HW, now=clock.t)
    assert client.get("/api/devices").get_json()["devices"][0]["online"] is True
    clock.t += 3600
    assert client.get("/api/devices").get_json()["devices"][0]["online"] is False


def test_get_one_device_returns_the_full_record(ctx):
    client, cache, _ = ctx
    registry.touch(cache, HW, fw="0.2.0", caps={"w": 240}, now=1_700_000_000.0)
    body = client.get(f"/api/devices/{HW}").get_json()
    assert body["caps"] == {"w": 240}
    assert body["first_seen"] and body["last_seen"]
    assert client.get("/api/devices/nope").status_code == 404


# --- assignment -------------------------------------------------------------

def test_patch_sets_name_and_scene(ctx):
    client, cache, _ = ctx
    registry.touch(cache, HW, now=1_700_000_000.0)
    r = client.patch(f"/api/devices/{HW}", json={"name": "radar", "scene": "planes"})
    assert r.status_code == 200
    rec = registry.load(cache)[HW]
    assert (rec["name"], rec["scene"]) == ("radar", "planes")


@pytest.mark.parametrize("body,why", [
    ({"scene": "no-such-scene"}, "unknown scene"),
    ({"scene": "error"}, "a server-chosen fallback, not an assignment"),
    ({"scene": "unassigned"}, "same"),
    ({"name": "has/slash"}, "would break the URL alias"),
    ({"name": ""}, "empty"),
    ({"name": "x" * 100}, "absurd"),
    ({"poll_seconds": 0}, "liveness would be meaningless"),
    ({"poll_seconds": "soon"}, "not a number"),
    ({"fw": "1.0"}, "device-reported, not human-settable"),
    ({"caps": {}}, "same"),
    ({"telemetry": {}}, "same"),
    ({"last_seen": "now"}, "same"),
    ({"first_seen": "now"}, "same"),
    ({"hw": "other"}, "not settable at all"),
])
def test_invalid_patches_are_rejected_and_persist_nothing(ctx, body, why):
    client, cache, _ = ctx
    registry.touch(cache, HW, now=1_700_000_000.0)
    before = registry.load(cache)[HW]
    r = client.patch(f"/api/devices/{HW}", json=body)
    assert r.status_code == 400, why
    assert registry.load(cache)[HW] == before, f"{why}: nothing may reach disk"


@pytest.mark.parametrize("body", [None, {}, [1, 2], "text", 5])
def test_malformed_patch_bodies_are_400(ctx, body):
    client, cache, _ = ctx
    registry.touch(cache, HW, now=1_700_000_000.0)
    r = client.patch(f"/api/devices/{HW}", json=body)
    assert r.status_code == 400


def test_patch_body_that_is_not_json_at_all(ctx):
    client, cache, _ = ctx
    registry.touch(cache, HW, now=1_700_000_000.0)
    r = client.patch(f"/api/devices/{HW}", data="not json",
                     content_type="application/json")
    assert r.status_code == 400


def test_two_devices_cannot_share_a_name(ctx):
    client, cache, _ = ctx
    registry.touch(cache, HW, now=1_700_000_000.0)
    registry.touch(cache, HW2, now=1_700_000_000.0)
    assert client.patch(f"/api/devices/{HW}", json={"name": "radar"}).status_code == 200
    r = client.patch(f"/api/devices/{HW2}", json={"name": "radar"})
    assert r.status_code == 400
    assert "already" in r.get_json()["error"]


def test_patch_and_delete_on_an_unknown_device_are_404(ctx):
    client, _, _ = ctx
    assert client.patch("/api/devices/nope", json={"name": "x"}).status_code == 404
    assert client.delete("/api/devices/nope").status_code == 404


def test_delete_forgets_a_retired_board(ctx):
    client, cache, _ = ctx
    registry.touch(cache, HW, now=1_700_000_000.0)
    assert client.delete(f"/api/devices/{HW}").status_code == 200
    assert registry.load(cache) == {}


# --- the device-facing call -------------------------------------------------

def test_first_contact_registers_and_says_it_is_unassigned(ctx):
    client, cache, _ = ctx
    body = client.get(f"/api/device/{HW}/scene?fw=0.2.0&rssi=-64&uptime=99").get_json()
    assert body["assigned"] is False
    assert body["scene"] == "unassigned"
    assert body["hw"] == HW, "a newly flashed board can tell you its id"
    assert "message" in body
    rec = registry.load(cache)[HW]
    assert rec["fw"] == "0.2.0"
    assert rec["telemetry"]["rssi"] == "-64"


def test_an_assigned_device_gets_its_scene(ctx):
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene")
    registry.assign(cache, HW, name="radar", scene="planes")
    body = client.get(f"/api/device/{HW}/scene").get_json()
    assert (body["assigned"], body["scene"], body["name"]) == (True, "planes", "radar")
    assert "message" not in body


def test_the_device_call_updates_liveness(ctx):
    client, _, clock = ctx
    client.get(f"/api/device/{HW}/scene")
    clock.t += 3600
    assert client.get("/api/devices").get_json()["devices"][0]["online"] is False
    client.get(f"/api/device/{HW}/scene")
    assert client.get("/api/devices").get_json()["devices"][0]["online"] is True


def test_poll_seconds_is_advertised_so_cadence_stays_server_controlled(ctx):
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene")
    registry.assign(cache, HW, poll_seconds=30)
    assert client.get(f"/api/device/{HW}/scene").headers["X-Poll-Seconds"] == "30"


def test_capabilities_are_recorded_and_range_checked(ctx):
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene?w=240&h=240&depth=16&layouts=fill"
               "&components=text,rings,markers")
    caps = registry.load(cache)[HW]["caps"]
    assert (caps["w"], caps["h"], caps["depth"]) == (240, 240, 16)
    assert caps["layouts"] == ["fill"]
    assert caps["components"] == ["text", "rings", "markers"]


@pytest.mark.parametrize("qs", [
    "w=&h=&depth=", "w=abc", "w=1e999", "w=0x10", "w=-5", "w=99999999999999999999",
    "layouts=,,,", "layouts=%20", "components=", "w=240&w=480",
    "depth=-5", "h=0",
])
def test_malformed_capability_query_strings_never_500(ctx, qs):
    client, cache, _ = ctx
    assert client.get(f"/api/device/{HW}/scene?{qs}").status_code == 200
    caps = registry.load(cache)[HW]["caps"]
    for key in ("w", "h", "depth"):
        if key in caps:
            assert 1 <= caps[key] <= 4096, f"{key} out of range survived: {caps}"


@pytest.mark.parametrize("hw", ["", "   ", "has%2Fslash", "x" * 200])
def test_an_unusable_hardware_id_is_rejected_not_registered(ctx, hw):
    client, cache, _ = ctx
    r = client.get(f"/api/device/{hw}/scene")
    assert r.status_code in (400, 404)
    assert registry.load(cache) == {}


def test_one_call_cannot_write_an_unbounded_registry(ctx, tmp_path):
    client, cache, _ = ctx
    qs = "&".join(f"k{i}=" + "v" * 400 for i in range(200))
    assert client.get(f"/api/device/{HW}/scene?{qs}").status_code == 200
    rec = registry.load(cache)[HW]
    assert len(rec["telemetry"]) <= registry.MAX_TELEMETRY_KEYS
    assert all(len(v) <= registry.MAX_VALUE_LEN for v in rec["telemetry"].values())


def test_the_fleet_is_bounded_against_a_registration_flood(ctx):
    client, cache, _ = ctx
    for i in range(registry.MAX_DEVICES):
        client.get(f"/api/device/{i:012x}/scene")
    r = client.get("/api/device/ffffffffffff/scene")
    assert r.status_code == 400
    assert "full" in r.get_json()["error"]


# --- corruption and degradation ---------------------------------------------

@pytest.mark.parametrize("junk", [
    "{not json", "[]", "null", '{"hw": "not a record"}',
    '{"hw": {"name": 5, "scene": 7, "first_seen": "x", "last_seen": "y"}}',
])
def test_a_damaged_registry_never_500s_any_route(ctx, junk):
    client, cache, _ = ctx
    registry.registry_path(cache).parent.mkdir(parents=True, exist_ok=True)
    registry.registry_path(cache).write_text(junk)
    for route in ("/api/devices", "/api/status", "/home", "/"):
        assert client.get(route).status_code == 200, f"{route} on {junk!r}"


def test_a_damaged_registry_still_lets_a_device_register(ctx):
    client, cache, _ = ctx
    registry.registry_path(cache).parent.mkdir(parents=True, exist_ok=True)
    registry.registry_path(cache).write_text("{not json")
    assert client.get(f"/api/device/{HW}/scene").status_code == 200
    assert list(registry.load(cache)) == [HW]
