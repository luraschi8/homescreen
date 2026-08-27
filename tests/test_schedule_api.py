"""Storing a screen's views and its schedule, and what that does to cadence."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from homescreen import registry, schedule
from homescreen.serve import create_app

HW = "aabb00112233"
Q = "w=240&h=240&depth=16&shape=round&components=radar,draw_list"
MADRID = ZoneInfo("Europe/Madrid")
CFG = {"location": {"name": "Madrid", "timezone": "Europe/Madrid",
                    "lat": 40.4, "lon": -3.7},
       "feeds": {"adsb": {"source": "api", "endpoint": "https://x"}},
       "devices": []}

DIA = {"placements": [{"region": "full", "component": "planes",
                       "options": {"radius_km": 40}}]}
NOCHE = {"placements": [{"region": "full", "component": "clock",
                         "options": {"timezone": "Europe/Madrid"}}]}


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def at(stamp: str) -> float:
    return datetime.fromisoformat(stamp).replace(tzinfo=MADRID).timestamp()


@pytest.fixture
def ctx(tmp_path):
    clock = Clock(at("2026-08-27T10:00"))
    client = create_app(CFG, tmp_path, version="t", clock=clock).test_client()
    client.get(f"/api/devices/{HW}/scene?{Q}")
    client.put(f"/api/devices/{HW}/membership", json={"approved": True})
    return client, tmp_path, clock


def _put(client, slots=(("dia", "09:00", "23:00"),), default="noche"):
    return client.put(f"/api/devices/{HW}/schedule", json={
        "views": {"dia": DIA, "noche": NOCHE},
        "schedule": {"tz": "Europe/Madrid", "default": default,
                     "slots": [{"view": v, "days": [1, 2, 3, 4, 5, 6, 7],
                                "from": f, "to": t} for v, f, t in slots]}})


def test_a_schedule_decides_what_the_device_is_served(ctx):
    client, _, clock = ctx
    _put(client)
    assert client.get(f"/api/devices/{HW}/scene?{Q}").get_json()["scene"] == "dia" \
        or client.get(f"/api/devices/{HW}/scene?{Q}").get_json()["scene"] == "planes"

    clock.t = at("2026-08-28T02:00")          # inside no slot -> the default
    body = client.get(f"/api/devices/{HW}/scene?{Q}").get_json()
    drawn = [d["v"] for d in body["components"][0]["draw"]]
    assert any(":" in v for v in drawn), "the clock view is showing"


def test_the_showing_view_carries_its_own_options(ctx):
    # Two views of the same screen, different settings. This is what "per
    # assignment" has to mean once a screen shows more than one thing.
    client, _, clock = ctx
    _put(client)
    body = client.get(f"/api/devices/{HW}/scene?{Q}").get_json()
    assert body["components"][0]["radius_km"] == 40.0


def test_the_device_is_woken_when_its_slot_flips(ctx):
    # The property that makes scheduling nearly free: a boundary is a known
    # future time, so the device sleeps until it and not a second longer.
    client, _, clock = ctx
    _put(client, slots=(("dia", "09:00", "10:30"),))
    clock.t = at("2026-08-27T10:00")
    told = int(client.get(f"/api/devices/{HW}/scene?{Q}").headers["X-Poll-Seconds"])
    assert told <= 30 * 60, "it must not sleep past 10:30"


def test_the_component_still_wins_when_it_changes_sooner(ctx):
    # min(), not "the boundary": a radar wants 5s whatever the schedule says.
    client, _, clock = ctx
    _put(client, slots=(("dia", "09:00", "23:00"),))
    told = int(client.get(f"/api/devices/{HW}/scene?{Q}").headers["X-Poll-Seconds"])
    assert told == 5, "the radar's own cadence is shorter than the boundary"


def test_a_boundary_cannot_drive_an_epaper_below_its_floor(ctx, tmp_path):
    # 1-bit glass takes ~3s to refresh and every frame is a Chromium render.
    # A slot flipping in 4 seconds must not ask for a frame in 4 seconds.
    client, _, clock = ctx
    epd = "ee00000000ff"
    client.get(f"/api/devices/{epd}/scene?w=800&h=480&depth=1")
    client.put(f"/api/devices/{epd}/membership", json={"approved": True})
    client.put(f"/api/devices/{epd}/schedule", json={
        "views": {"a": {"placements": [{"region": "full", "component": "clock"}]},
                  "b": {"placements": [{"region": "full", "component": "status"}]}},
        "schedule": {"tz": "Europe/Madrid", "default": "a",
                     "slots": [{"view": "b", "days": [1, 2, 3, 4, 5, 6, 7],
                                "from": "10:00", "to": "10:01"}]}})
    clock.t = at("2026-08-27T09:59:56")
    told = int(client.get(f"/api/devices/{epd}/scene?w=800&h=480&depth=1")
               .headers["X-Poll-Seconds"])
    assert told >= registry.EPAPER_POLL_SECONDS


def test_a_slot_naming_a_view_that_is_not_being_stored_is_refused(ctx):
    # Validated against the views AFTER this write, not the ones before it --
    # otherwise a single request can leave a slot pointing at nothing.
    client, cache, _ = ctx
    r = client.put(f"/api/devices/{HW}/schedule", json={
        "views": {"solo": NOCHE},
        "schedule": {"default": "solo",
                     "slots": [{"view": "borrada", "days": [1], "from": "09:00",
                                "to": "10:00"}]}})
    assert r.status_code == 200
    assert r.get_json()["schedule"]["slots"] == []


def test_a_view_this_screen_cannot_draw_is_not_stored(ctx):
    client, cache, _ = ctx
    r = client.put(f"/api/devices/{HW}/schedule", json={
        "views": {"imposible": {"placements": [
            {"region": "markets", "component": "clock"}]}},
        "schedule": {"default": "imposible", "slots": []}})
    assert r.status_code == 400


def test_the_readable_form_says_what_this_screen_can_do(ctx):
    client, _, _ = ctx
    _put(client)
    body = client.get(f"/api/devices/{HW}/schedule").get_json()
    assert set(body["views"]) == {"dia", "noche"}
    assert body["templates"] == ["single"], "round glass carries one layout"
    assert body["regions"]["single"]["full"] == [0, 0, 240, 240]


def test_the_fleet_list_still_says_what_a_scheduled_screen_shows(ctx):
    # Every reader that has not learned about views keeps working: `scene` is
    # still the name of what is showing.
    client, cache, _ = ctx
    _put(client)
    rec = registry.load(cache)[HW]
    assert rec["scene"] in {"clock", "planes"}


@pytest.mark.parametrize("body", [None, {}, {"views": {}}, {"views": []},
                                  {"views": {"a": {}}}, "text"])
def test_a_malformed_schedule_is_refused_without_writing(ctx, body):
    client, cache, _ = ctx
    before = registry.load(cache)[HW].get("views")
    r = (client.put(f"/api/devices/{HW}/schedule") if body is None
         else client.put(f"/api/devices/{HW}/schedule", json=body))
    assert r.status_code == 400
    assert registry.load(cache)[HW].get("views") == before


def test_an_unknown_device_is_a_404_not_a_new_record(ctx):
    client, cache, _ = ctx
    assert client.get("/api/devices/ffffffffffff/schedule").status_code == 404
    assert client.put("/api/devices/ffffffffffff/schedule",
                      json={"views": {"a": NOCHE}}).status_code == 404
