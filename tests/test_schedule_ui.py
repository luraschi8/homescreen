"""The week grid.

A schedule is the one thing here genuinely better seen than read: "the last
matching slot wins" is one sentence and still hard to hold when four slots
interleave. These assert the grid shows the answer rather than asking anyone to
derive it.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from homescreen import registry
from homescreen.serve import create_app
from homescreen.web import schedule_ui

HW = "aabb00112233"
Q = "w=240&h=240&depth=16&shape=round&components=radar,draw_list"
MADRID = ZoneInfo("Europe/Madrid")
CFG = {"location": {"name": "Madrid", "timezone": "Europe/Madrid",
                    "lat": 40.4, "lon": -3.7},
       "feeds": {"adsb": {"source": "api", "endpoint": "https://x"}},
       "devices": []}


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def at(stamp):
    return datetime.fromisoformat(stamp).replace(tzinfo=MADRID).timestamp()


@pytest.fixture
def ctx(tmp_path):
    clock = Clock(at("2026-08-27T10:00"))          # a Thursday
    client = create_app(CFG, tmp_path, version="t", clock=clock).test_client()
    client.get(f"/api/devices/{HW}/scene?{Q}")
    client.put(f"/api/devices/{HW}/membership", json={"approved": True})
    client.put(f"/api/devices/{HW}/schedule", json={
        "views": {"dia": {"placements": [{"region": "full",
                                          "component": "planes"}]},
                  "noche": {"placements": [{"region": "full",
                                            "component": "clock"}]}},
        "schedule": {"tz": "Europe/Madrid", "default": "noche", "slots": [
            {"view": "dia", "days": [1, 2, 3, 4, 5], "from": "09:00",
             "to": "18:00"}]}})
    return client, tmp_path, clock


def test_the_grid_is_a_full_week_of_hours(ctx):
    client, _, _ = ctx
    html = client.get(f"/device/{HW}").get_data(as_text=True)
    assert html.count("<td") >= 7 * 24


def test_the_grid_paints_the_view_that_actually_wins(ctx):
    # Thursday 10:00 is inside the weekday slot; Saturday 10:00 is not.
    client, _, _ = ctx
    html = client.get(f"/device/{HW}").get_data(as_text=True)
    assert 'title="jue 10:00 — dia"' in html
    assert 'title="sáb 10:00 — noche"' in html, "the default, outside any slot"


def test_the_current_hour_is_marked(ctx):
    client, _, _ = ctx
    html = client.get(f"/device/{HW}").get_data(as_text=True)
    assert 'class="now"' in html


def test_saving_a_slot_from_the_form_changes_what_is_served(ctx):
    client, cache, clock = ctx
    r = client.post(f"/device/{HW}/schedule", data={
        "default": "noche",
        "slot0.view": "dia", "slot0.from": "09:00", "slot0.to": "18:00",
        "slot0.day": ["1", "2", "3", "4", "5"],
        "slot1.view": "noche", "slot1.from": "18:00", "slot1.to": "09:00",
        "slot1.day": ["1", "2", "3", "4", "5", "6", "7"]})
    assert r.status_code in (302, 303)
    plan = registry.load(cache)[HW]["schedule"]
    assert len(plan["slots"]) == 2

    clock.t = at("2026-08-27T20:00")               # inside the night slot
    body = client.get(f"/api/devices/{HW}/scene?{Q}").get_json()
    assert body["scene"] == "clock"


def test_removing_a_slot_removes_it(ctx):
    client, cache, _ = ctx
    client.post(f"/device/{HW}/schedule", data={
        "default": "noche", "slot0.view": "dia", "slot0.from": "09:00",
        "slot0.to": "18:00", "slot0.day": ["1"], "slot0.remove": "on"})
    assert registry.load(cache)[HW]["schedule"]["slots"] == []


def test_a_slot_on_no_day_is_not_a_slot(ctx):
    # The empty row the form always renders, submitted untouched.
    client, cache, _ = ctx
    client.post(f"/device/{HW}/schedule", data={
        "default": "noche", "slot0.view": "dia", "slot0.from": "09:00",
        "slot0.to": "18:00"})
    assert registry.load(cache)[HW]["schedule"]["slots"] == []


def test_the_schedule_form_does_not_disturb_the_views(ctx):
    # This form edits WHEN, not WHAT.
    client, cache, _ = ctx
    before = registry.load(cache)[HW]["views"]
    client.post(f"/device/{HW}/schedule", data={"default": "dia"})
    assert registry.load(cache)[HW]["views"] == before


def test_a_screen_with_no_schedule_yet_still_renders_its_page(ctx, tmp_path):
    client = create_app(CFG, tmp_path, version="t").test_client()
    client.get(f"/api/devices/{HW}/scene?{Q}")
    assert client.get(f"/device/{HW}").status_code == 200


def test_overlapping_slots_show_which_one_won(ctx):
    # The whole reason the grid exists: last-match-wins is easy to state and
    # hard to picture.
    client, cache, _ = ctx
    client.put(f"/api/devices/{HW}/schedule", json={
        "views": {"dia": {"placements": [{"region": "full",
                                          "component": "planes"}]},
                  "noche": {"placements": [{"region": "full",
                                            "component": "clock"}]}},
        "schedule": {"tz": "Europe/Madrid", "default": "noche", "slots": [
            {"view": "dia", "days": [1, 2, 3, 4, 5, 6, 7], "from": "00:00",
             "to": "23:59"},
            {"view": "noche", "days": [4], "from": "10:00", "to": "11:00"}]}})
    html = client.get(f"/device/{HW}").get_data(as_text=True)
    assert 'title="jue 10:00 — noche"' in html, "the later slot wins"
    assert 'title="jue 12:00 — dia"' in html


@pytest.mark.parametrize("bad", [
    {"default": "ghost"},
    {"default": "noche", "slot0.view": "ghost", "slot0.day": ["1"],
     "slot0.from": "09:00", "slot0.to": "10:00"},
    {"default": "noche", "slot0.view": "dia", "slot0.day": ["9"],
     "slot0.from": "09:00", "slot0.to": "10:00"},
    {"default": "noche", "slot0.view": "dia", "slot0.day": ["1"],
     "slot0.from": "nonsense", "slot0.to": "10:00"},
])
def test_a_bad_slot_is_dropped_rather_than_stored(ctx, bad):
    client, cache, _ = ctx
    r = client.post(f"/device/{HW}/schedule", data=bad)
    assert r.status_code in (302, 303)
    plan = registry.load(cache)[HW]["schedule"]
    assert all(s["view"] in {"dia", "noche"} for s in plan["slots"])
    assert plan["default"] in {"dia", "noche"}
