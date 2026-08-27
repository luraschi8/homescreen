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


@pytest.mark.parametrize("hw, status", [
    ("", 404),                # never reaches the view: Werkzeug routing
    ("has%2Fslash", 404),     # ditto -- a slash is a path separator
    ("   ", 400),             # reaches _check_hw
    ("x" * 200, 400),
])
def test_an_unusable_hardware_id_is_rejected_not_registered(ctx, hw, status):
    # `in (400, 404)` hid which ids the server actually validates and which
    # never arrive at all -- so _check_hw could have stopped running entirely.
    client, cache, _ = ctx
    assert client.get(f"/api/device/{hw}/scene").status_code == status
    assert registry.load(cache) == {}


def test_one_call_cannot_write_an_unbounded_registry(ctx, tmp_path):
    client, cache, _ = ctx
    qs = "&".join(f"k{i}=" + "v" * 400 for i in range(200))
    assert client.get(f"/api/device/{HW}/scene?{qs}").status_code == 200
    rec = registry.load(cache)[HW]
    assert len(rec["telemetry"]) <= registry.MAX_TELEMETRY_KEYS
    assert all(len(v) <= registry.MAX_VALUE_LEN for v in rec["telemetry"].values())


def test_a_registration_flood_is_bounded_on_disk(ctx):
    client, cache, _ = ctx
    for i in range(registry.MAX_DEVICES * 3):
        client.get(f"/api/device/{i:012x}/scene")
    assert len(registry.load(cache)) <= registry.MAX_DEVICES


def test_a_registration_flood_does_not_lock_out_real_hardware(ctx):
    # Eviction used to require the victim to be offline, so 64 ids kept fresh
    # by one GET each held the door shut against new hardware indefinitely.
    client, cache, _ = ctx
    for i in range(registry.MAX_DEVICES):
        client.get(f"/api/device/{i:012x}/scene")
    r = client.get("/api/device/realpanel/scene?w=800&h=480&depth=1")
    assert r.status_code == 200, r.get_data()
    assert "realpanel" in registry.load(cache)


def test_a_fully_configured_fleet_refuses_a_new_registration(ctx):
    # With nothing left to evict, refusing is right: the alternative is
    # dropping a panel somebody configured.
    client, cache, _ = ctx
    for i in range(registry.MAX_DEVICES):
        hw = f"{i:012x}"
        client.get(f"/api/device/{hw}/scene")
        client.patch(f"/api/devices/{hw}", json={"name": f"d{i}", "scene": "clock"})
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


def test_get_one_device_reports_online_state(ctx):
    client, cache, clock = ctx
    registry.touch(cache, HW, now=clock.t)
    assert client.get(f"/api/devices/{HW}").get_json()["online"] is True
    clock.t += 3600
    assert client.get(f"/api/devices/{HW}").get_json()["online"] is False


# --- the scene endpoint is conditional too ------------------------------------

def test_an_unchanged_scene_is_a_304(ctx):
    # Spec §6.3 asserts the server-side half of "a device holds its last good
    # scene" is implemented: /frame had an ETag and this route had none, so a
    # firmware sending If-None-Match here got a full body every poll.
    client, cache, _ = ctx
    first = client.get(f"/api/device/{HW}/scene?w=240&h=240&components=text")
    etag = first.headers["ETag"]
    again = client.get(f"/api/device/{HW}/scene?w=240&h=240&components=text",
                       headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.get_data() == b""
    assert again.headers["X-Poll-Seconds"], "cadence still reaches the device"
    assert again.headers["ETag"] == etag


def test_assigning_a_scene_changes_the_etag(ctx):
    client, cache, _ = ctx
    q = f"/api/device/{HW}/scene?w=240&h=240&components=radar"
    before = client.get(q).headers["ETag"]
    client.patch(f"/api/devices/{HW}", json={"name": "r", "scene": "planes"})
    after = client.get(q).headers["ETag"]
    assert before != after, "a device must not 304 past the scene it was given"


def test_a_renamed_device_changes_the_etag(ctx):
    # The name is in the body, so a device that 304'd would keep showing the
    # old one on the status panel.
    client, cache, _ = ctx
    q = f"/api/device/{HW}/scene?w=800&h=480&depth=1"
    before = client.get(q).headers["ETag"]
    client.patch(f"/api/devices/{HW}", json={"name": "escritorio"})
    assert client.get(q).headers["ETag"] != before


@pytest.mark.parametrize("field", ["fw", "caps", "last_seen", "first_seen",
                                   "telemetry"])
def test_a_device_reported_field_is_refused_by_name(ctx, field):
    # Replacing `readonly = sorted(...)` with `[]` survived: the same requests
    # still 400'd via the `unknown` branch, only the message changed, and no
    # test read messages. _DEVICE_READONLY could have been deleted outright.
    client, cache, _ = ctx
    registry.touch(cache, HW, now=1000.0)
    r = client.patch(f"/api/devices/{HW}", json={field: "x"})
    assert r.status_code == 400
    assert "device-reported" in r.get_json()["error"], \
        "a field the DEVICE owns must say so, not read as a typo"
    assert field in r.get_json()["error"]


def test_an_unknown_field_is_refused_with_the_settable_list(ctx):
    client, cache, _ = ctx
    registry.touch(cache, HW, now=1000.0)
    r = client.patch(f"/api/devices/{HW}", json={"colour": "red"})
    assert r.status_code == 400
    body = r.get_json()
    assert body["error"] == "not settable: ['colour']"
    assert set(body["settable"]) == {"name", "scene", "poll_seconds"}


# --- spec §5.5 / §6.2: the fleet view records what the server substituted -----

def test_a_dropped_component_shows_up_where_an_operator_looks(ctx):
    # It reached the DEVICE's own response and nowhere else, so an operator
    # staring at a panel showing the wrong thing had to diff two JSON payloads
    # by hand to learn the server had dropped something.
    client, cache, _ = ctx
    client.get("/api/device/rd/scene?w=240&h=240&components=text")
    client.patch("/api/devices/rd", json={"name": "rd", "scene": "planes"})
    client.get("/api/device/rd/scene?w=240&h=240&components=text")
    entry, = [d for d in client.get("/api/devices").get_json()["devices"]
              if d["hw"] == "rd"]
    assert entry["unsupported"] == ["radar"]


def test_a_scene_that_raises_is_recorded_against_the_device(ctx, monkeypatch):
    client, cache, _ = ctx
    from homescreen.scenes import clock as clock_mod
    client.get(f"/api/device/{HW}/scene?w=800&h=480&depth=1")
    client.patch(f"/api/devices/{HW}", json={"name": "d", "scene": "clock"})
    monkeypatch.setattr(clock_mod, "build",
                        lambda c: (_ for _ in ()).throw(RuntimeError("boom")))
    client.get(f"/api/device/{HW}/scene?w=800&h=480&depth=1")
    entry, = [d for d in client.get("/api/devices").get_json()["devices"]
              if d["hw"] == HW]
    assert "fallo en clock" in entry["scene_error"]
    assert "RuntimeError" in entry["scene_error"]


def test_a_recovered_scene_clears_the_note(ctx, monkeypatch):
    # A stale error is worse than none: it sends an operator looking for a
    # fault that fixed itself.
    client, cache, _ = ctx
    client.get("/api/device/rd2/scene?w=240&h=240&components=text")
    client.patch("/api/devices/rd2", json={"name": "rd2", "scene": "planes"})
    client.get("/api/device/rd2/scene?w=240&h=240&components=text")
    client.get("/api/device/rd2/scene?w=240&h=240&components=radar")
    entry, = [d for d in client.get("/api/devices").get_json()["devices"]
              if d["hw"] == "rd2"]
    assert "unsupported" not in entry


def test_every_route_tells_a_device_the_same_cadence(ctx):
    # A self-registered 1-bit panel was told 30 by /scene, /frame and
    # /api/devices, and 5 by /api/display/<name>/data -- while the fleet view
    # judged it offline against 3 x 30. Which one the firmware obeys decides
    # whether the panel refreshes every 5s or every 30s.
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene?w=800&h=480&depth=1")
    client.patch(f"/api/devices/{HW}", json={"name": "salon", "scene": "clock"})

    scene = client.get(f"/api/device/{HW}/scene?w=800&h=480&depth=1")
    data = client.get("/api/display/salon/data")
    fleet, = [d for d in client.get("/api/devices").get_json()["devices"]
              if d["hw"] == HW]
    assert scene.headers["X-Poll-Seconds"] == "30"
    assert data.headers["X-Poll-Seconds"] == "30", "this route computed its own"
    assert fleet["poll_seconds"] == 30


def test_an_operator_cadence_reaches_every_route_too(ctx):
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene?w=800&h=480&depth=1")
    client.patch(f"/api/devices/{HW}",
                 json={"name": "salon2", "scene": "clock", "poll_seconds": 90})
    assert client.get("/api/display/salon2/data").headers["X-Poll-Seconds"] == "90"
    assert client.get(f"/api/device/{HW}/scene?w=800&h=480&depth=1"
                      ).headers["X-Poll-Seconds"] == "90"


# --- the scene ETag must be able to 304 -------------------------------------
# Ages are recomputed at serve time, so `dwell` advances on every request. That
# made every scene response unique and the 304 unreachable: measured, an empty
# sky 50 ms later produced a different ETag. A conditional GET that can never
# succeed is not a feature, and the device pays a ~30 KB parse peak every poll
# to learn nothing.

def _seed_sky(cache, when, aircraft):
    import json as _json
    from datetime import datetime, timezone
    p = cache / "feed" / "adsb.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_json.dumps({
        "fetched_at": datetime.fromtimestamp(when, timezone.utc).isoformat(),
        "ok": True, "error": None, "data": {"aircraft": aircraft}}))


def test_an_unchanged_sky_answers_304_despite_the_clock_moving(ctx):
    client, cache, clock = ctx
    _seed_sky(cache, clock.t, [{"lat": 40.5, "lon": -3.6, "age": 1.0,
                                "cs": "IBE1"}])
    q = f"/api/device/{HW}/scene?w=240&h=240&depth=16&components=radar"
    client.get(q)
    client.patch(f"/api/devices/{HW}", json={"name": "r", "scene": "planes"})
    etag = client.get(q).headers["ETag"]
    clock.t += 0.05
    assert client.get(q, headers={"If-None-Match": etag}).status_code == 304


def test_the_hidden_age_drift_is_bounded_by_one_bucket(ctx):
    from homescreen.serve import AGE_BUCKET_S
    client, cache, clock = ctx
    _seed_sky(cache, clock.t, [{"lat": 40.5, "lon": -3.6, "age": 1.0,
                                "cs": "IBE1"}])
    q = f"/api/device/{HW}/scene?w=240&h=240&depth=16&components=radar"
    client.get(q)
    client.patch(f"/api/devices/{HW}", json={"name": "r", "scene": "planes"})
    etag = client.get(q).headers["ETag"]
    clock.t += AGE_BUCKET_S * 2 + 0.1
    r = client.get(q, headers={"If-None-Match": etag})
    assert r.status_code == 200, "past one bucket the device must get real ages"
    assert r.get_json()["components"][0]["items"][0]["age"] > 1.0


def test_a_changed_sky_still_changes_the_etag_within_a_bucket(ctx):
    # Bucketing must quantise only the clocks. If it swallowed a real change
    # the device would hold a stale picture and never learn otherwise.
    client, cache, clock = ctx
    _seed_sky(cache, clock.t, [{"lat": 40.5, "lon": -3.6, "age": 1.0,
                                "cs": "IBE1"}])
    q = f"/api/device/{HW}/scene?w=240&h=240&depth=16&components=radar"
    client.get(q)
    client.patch(f"/api/devices/{HW}", json={"name": "r", "scene": "planes"})
    etag = client.get(q).headers["ETag"]
    _seed_sky(cache, clock.t, [{"lat": 41.9, "lon": -3.6, "age": 1.0,
                                "cs": "IBE1"}])          # it moved
    assert client.get(q, headers={"If-None-Match": etag}).status_code == 200


def test_a_new_aircraft_changes_the_etag(ctx):
    client, cache, clock = ctx
    _seed_sky(cache, clock.t, [{"lat": 40.5, "lon": -3.6, "age": 1.0,
                                "cs": "IBE1"}])
    q = f"/api/device/{HW}/scene?w=240&h=240&depth=16&components=radar"
    client.get(q)
    client.patch(f"/api/devices/{HW}", json={"name": "r", "scene": "planes"})
    etag = client.get(q).headers["ETag"]
    _seed_sky(cache, clock.t, [{"lat": 40.5, "lon": -3.6, "age": 1.0,
                                "cs": "IBE1"},
                               {"lat": 40.9, "lon": -3.9, "age": 1.0,
                                "cs": "RYR2"}])
    assert client.get(q, headers={"If-None-Match": etag}).status_code == 200


def test_a_feed_going_down_changes_the_etag(ctx):
    # feed_ok is part of the identity, not a clock: the device must be told.
    import json as _json
    from datetime import datetime, timezone
    client, cache, clock = ctx
    _seed_sky(cache, clock.t, [{"lat": 40.5, "lon": -3.6, "age": 1.0,
                                "cs": "IBE1"}])
    q = f"/api/device/{HW}/scene?w=240&h=240&depth=16&components=radar"
    client.get(q)
    client.patch(f"/api/devices/{HW}", json={"name": "r", "scene": "planes"})
    etag = client.get(q).headers["ETag"]
    p = cache / "feed" / "adsb.json"
    env = _json.loads(p.read_text())
    env["ok"] = False
    p.write_text(_json.dumps(env))
    assert client.get(q, headers={"If-None-Match": etag}).status_code == 200


def test_the_api_can_put_a_device_back_to_unassigned(ctx):
    # The only route back used to be DELETE, which loses the name.
    client, cache, _ = ctx
    registry.touch(cache, HW, now=1000.0)
    client.patch(f"/api/devices/{HW}", json={"name": "desk", "scene": "planes"})
    r = client.patch(f"/api/devices/{HW}", json={"scene": "unassigned"})
    assert r.status_code == 200
    assert r.get_json()["scene"] == "unassigned"
    assert r.get_json()["name"] == "desk"


def test_an_unassigned_device_is_served_the_unassigned_scene_again(ctx):
    # Round trip: the device must actually see the change, not just the record.
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene?w=240&h=240&depth=16&components=radar")
    client.patch(f"/api/devices/{HW}", json={"name": "d", "scene": "planes"})
    client.patch(f"/api/devices/{HW}", json={"scene": "unassigned"})
    body = client.get(f"/api/device/{HW}/scene?w=240&h=240&depth=16"
                      "&components=radar").get_json()
    assert body["assigned"] is False
    assert body["scene"] == "unassigned"
    assert body["components"] == []
    assert "message" in body


# --- the dashboard as a control surface, not just a report -------------------
# `/home` rendered the fleet and nothing else: changing what a screen showed
# needed a curl, while the server told unassigned devices to "elige una escena
# en el panel" -- a promise the panel did not keep.

def _home(client, **q):
    from urllib.parse import urlencode
    return client.get("/home" + ("?" + urlencode(q) if q else "")).get_data(as_text=True)


def test_the_dashboard_offers_every_assignable_scene_and_unassigned(ctx):
    client, cache, _ = ctx
    registry.touch(cache, HW, now=1000.0)
    html = _home(client)
    for scene in registry.ASSIGNABLE_SCENES:
        assert f'<option value="{scene}"' in html, scene
    assert '<option value="unassigned"' in html, \
        "taking a screen out of service must not require DELETE"


def test_the_current_scene_is_preselected(ctx):
    client, cache, _ = ctx
    registry.touch(cache, HW, now=1000.0)
    registry.assign(cache, HW, name="salon", scene="planes")
    html = _home(client)
    assert '<option value="planes" selected>' in html
    assert '<option value="clock" selected>' not in html


def test_applying_a_scene_from_the_dashboard_changes_what_the_device_is_served(ctx):
    # The whole point: the operator picks, and the device follows.
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene?w=240&h=240&depth=16&components=radar")
    r = client.post("/home/device",
                    data={"hw": HW, "name": "salon", "scene": "planes"})
    assert r.status_code in (302, 303), "a form POST must redirect, not render"
    body = client.get(f"/api/device/{HW}/scene?w=240&h=240&depth=16"
                      "&components=radar").get_json()
    assert body["scene"] == "planes" and body["assigned"] is True
    assert [c["c"] for c in body["components"]] == ["radar"]


def test_the_dashboard_can_put_a_device_back_to_unassigned(ctx):
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene?w=240&h=240&depth=16&components=radar")
    client.post("/home/device", data={"hw": HW, "name": "salon", "scene": "planes"})
    client.post("/home/device", data={"hw": HW, "name": "salon",
                                      "scene": "unassigned"})
    body = client.get(f"/api/device/{HW}/scene?w=240&h=240&depth=16"
                      "&components=radar").get_json()
    assert body["assigned"] is False
    assert registry.load(cache)[HW]["name"] == "salon", "the name must survive"


def test_renaming_from_the_dashboard_changes_what_the_name_route_serves(ctx):
    # The name is what /api/display/<name>/ routes on, so this is not cosmetic.
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene?w=240&h=240&depth=16&components=radar")
    client.post("/home/device", data={"hw": HW, "name": "cocina", "scene": "planes"})
    assert client.get("/api/display/cocina/health").status_code == 200


def test_the_form_and_the_json_api_share_one_validator(ctx):
    # Two write paths that disagreed about what a valid scene is would be two
    # places to fix every time the rules change.
    client, cache, _ = ctx
    registry.touch(cache, HW, now=1000.0)
    r = client.post("/home/device",
                    data={"hw": HW, "name": "x", "scene": "../../etc/passwd"})
    assert r.status_code in (302, 303)
    assert registry.load(cache)[HW]["scene"] == "unassigned", "nothing was written"
    assert "unknown scene" in _home(client, m="unknown scene 'x'")


def test_a_rejected_apply_tells_the_operator_why(ctx):
    # A silent redirect back to an unchanged page is indistinguishable from a
    # save that worked.
    client, cache, _ = ctx
    registry.touch(cache, HW, now=1000.0)
    r = client.post("/home/device", data={"hw": HW, "scene": "nosuchscene"})
    assert "m=" in r.headers["Location"]
    assert "unknown+scene" in r.headers["Location"] or \
           "unknown%20scene" in r.headers["Location"]


def test_an_apply_for_an_unknown_device_is_refused_not_created(ctx):
    client, cache, _ = ctx
    r = client.post("/home/device", data={"hw": "ghost", "scene": "planes"})
    assert r.status_code in (302, 303)
    assert "ghost" not in registry.load(cache), "a form must not register a device"


def test_a_name_containing_a_slash_is_refused_outright(ctx):
    # `/` would break /api/display/<name>/ routing, so _check_name rejects it --
    # which also happens to stop `</script>`.
    client, cache, _ = ctx
    registry.touch(cache, HW, now=1000.0)
    client.post("/home/device",
                data={"hw": HW, "name": "<script>alert(1)</script>",
                      "scene": "planes"})
    assert registry.load(cache)[HW]["name"] is None


def test_a_hostile_name_that_IS_accepted_cannot_reach_the_page_unescaped(ctx):
    # The dangerous case is a payload with no slash in it, which _check_name
    # accepts. It reaches the fleet card, the form's value= attribute, and the
    # glass -- so escaping is the only thing between it and execution.
    client, cache, _ = ctx
    registry.touch(cache, HW, now=1000.0)
    payload = "<img src=x onerror=alert(1)>"
    client.post("/home/device", data={"hw": HW, "name": payload,
                                      "scene": "planes"})
    assert registry.load(cache)[HW]["name"] == payload, "precondition: accepted"
    html = _home(client)
    assert payload not in html
    assert "&lt;img" in html
    assert "onerror=alert(1)&gt;" in html or "&lt;img src=x onerror" in html


def test_the_notice_is_escaped_too(ctx):
    # It comes straight off the query string.
    client, cache, _ = ctx
    html = _home(client, m="<img src=x onerror=alert(1)>")
    assert "<img src=x" not in html
    assert "&lt;img" in html


def test_the_form_needs_no_javascript(ctx):
    # Same constraint the scenes live under: no JS, no CDN. It has to work from
    # a phone on a bad connection, and degrade to a page reload.
    client, cache, _ = ctx
    registry.touch(cache, HW, now=1000.0)
    html = _home(client)
    assert "<form" in html and 'method="post"' in html
    # External DEPENDENCIES, not any URL: the page legitimately renders the
    # upstream feed's endpoint as text, and asserting on "https://" would
    # flag that.
    for bad in ("<script", "onclick=", "onsubmit=", "fetch(",
                "addEventListener", "<link rel=\"stylesheet\"",
                "src=\"http", "href=\"http"):
        assert bad not in html, f"the dashboard must not need {bad!r}"


def test_applying_the_same_values_does_not_rewrite_the_card(ctx):
    # A form has an apply button and people press it twice. The poll path has a
    # wear guard and this one did not, so every press wrote and fsynced the
    # card. Lower stakes than a per-poll write -- it is human-paced -- but free
    # to avoid, and "changing nothing writes nothing" is the rule everywhere
    # else in this server.
    import os
    client, cache, _ = ctx
    registry.touch(cache, HW, now=1000.0)
    client.post("/home/device", data={"hw": HW, "name": "x", "scene": "planes"})
    before = os.stat(registry.registry_path(cache)).st_mtime_ns
    client.post("/home/device", data={"hw": HW, "name": "x", "scene": "planes"})
    assert os.stat(registry.registry_path(cache)).st_mtime_ns == before


# --- the dashboard must not offer a choice that cannot work ------------------

def test_a_data_push_device_is_only_offered_scenes_it_can_draw(ctx):
    # A round display draws its own geometry from components. `clock` is HTML
    # only, so picking it put "escena no soportada" on the glass -- and the
    # operator had no way to know that before pressing apply.
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene?w=240&h=240&depth=16&components=radar")
    html = _home(client)
    assert '<option value="planes"' in html
    assert 'disabled' in html, "clock and status are not drawable here"
    assert 'value="clock" disabled' in html or 'value="clock"  disabled' in html \
        or 'value="clock" selected disabled' in html


def test_a_disabled_option_says_why(ctx):
    # "clock — no components for this device" beats a silent grey row.
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene?w=240&h=240&depth=16&components=radar")
    html = _home(client)
    assert "no components for this device" in html


def test_a_pixel_push_device_is_offered_every_scene(ctx):
    # An e-paper takes a rendered framebuffer, so any scene with html works.
    client, cache, _ = ctx
    client.get("/api/device/epap/scene?w=800&h=480&depth=1")
    html = _home(client)
    for scene in ("clock", "planes", "status"):
        assert f'<option value="{scene}"' in html
    card = html[html.index("epap"):]
    card = card[:card.index("</form>")]
    assert "disabled" not in card, "a pixel-push device can render all of them"


def test_a_device_that_declares_a_component_we_have_no_scene_for(ctx):
    # Declaring `text` today matches no scene, so everything is disabled -- and
    # every row says what it would need.
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene?w=240&h=240&depth=16&components=text")
    html = _home(client)
    assert "needs radar" in html, "planes should say what it wants"


def test_the_offered_list_still_lets_you_unassign(ctx):
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene?w=240&h=240&depth=16&components=radar")
    assert '<option value="unassigned"' in _home(client)


# --- the preview: pick by looking, not by reading a name ---------------------

def test_a_drawable_scene_previews_what_the_device_would_draw(ctx):
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene?w=240&h=240&depth=16&components=clock")
    r = client.get(f"/preview/{HW}/clock.svg")
    assert r.status_code == 200
    assert r.mimetype == "image/svg+xml"
    body = r.get_data(as_text=True)
    assert "<svg" in body and ":" in body, "the time should be in there"


def test_the_preview_is_drawn_at_the_devices_own_geometry(ctx):
    # A preview at the wrong size is worse than none: it shows a layout the
    # device will never produce.
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene?w=240&h=240&depth=16&components=clock")
    client.get("/api/device/EP/scene?w=800&h=480&depth=1")
    assert 'viewBox="0 0 240 240"' in client.get(f"/preview/{HW}/clock.svg").get_data(as_text=True)
    assert 'viewBox="0 0 800 480"' in client.get("/preview/EP/clock.svg").get_data(as_text=True)


def test_a_square_panel_previews_round_and_a_wide_one_does_not(ctx):
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene?w=240&h=240&depth=16&components=clock")
    client.get("/api/device/EP/scene?w=800&h=480&depth=1")
    assert "<circle" in client.get(f"/preview/{HW}/clock.svg").get_data(as_text=True)
    assert "<circle" not in client.get("/preview/EP/clock.svg").get_data(as_text=True)


def test_a_component_with_no_instructions_says_so_rather_than_faking_one(ctx):
    # radar is opaque today: the device projects and dead-reckons. Drawing an
    # approximation and calling it a preview would be exactly the drift this
    # design exists to prevent.
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene?w=240&h=240&depth=16&components=radar")
    body = client.get(f"/preview/{HW}/planes.svg").get_data(as_text=True)
    assert "sin vista previa" in body


def test_the_preview_never_forks_a_browser(ctx, monkeypatch):
    # Previews are refreshed on every dashboard load. If one could take a render
    # slot, opening the page would compete with the devices asking for frames.
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene?w=240&h=240&depth=16&components=clock")

    def boom(*a, **k):
        raise AssertionError("a preview must never render HTML")

    monkeypatch.setattr("homescreen.serve.render_frame", boom)
    assert client.get(f"/preview/{HW}/clock.svg").status_code == 200


def test_an_unknown_device_or_scene_is_a_404_not_a_blank_image(ctx):
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene?w=240&h=240&depth=16&components=clock")
    assert client.get("/preview/ghost/clock.svg").status_code == 404
    assert client.get(f"/preview/{HW}/nosuchscene.svg").status_code == 404


def test_a_scene_that_raises_is_a_503_not_a_500(ctx, monkeypatch):
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene?w=240&h=240&depth=16&components=clock")
    from homescreen.scenes import clock as clock_mod
    monkeypatch.setattr(clock_mod, "build",
                        lambda c: (_ for _ in ()).throw(RuntimeError("x")))
    assert client.get(f"/preview/{HW}/clock.svg").status_code == 503


def test_the_dashboard_shows_a_preview_for_every_drawable_scene(ctx):
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene?w=240&h=240&depth=16&components=clock")
    html = _home(client)
    assert f'src="/preview/{HW}/clock.svg"' in html
    assert 'loading="lazy"' in html, "a fleet of screens should not block on thumbnails"


def test_the_dashboard_does_not_thumbnail_a_scene_the_device_cannot_draw(ctx):
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene?w=240&h=240&depth=16&components=clock")
    html = _home(client)
    assert f'src="/preview/{HW}/planes.svg"' not in html, \
        "planes needs a radar component this device did not declare"
