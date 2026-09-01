"""Per-assignment component options.

Configuration belongs to the ASSIGNMENT, not the component: two screens can show
a clock in different cities, or stocks with different tickers, and neither is a
property of the component itself. One schema drives the form, the validation and
the defaults, so adding an option is one edit rather than three that can
disagree.
"""
import pathlib
import tempfile

import pytest

from homescreen import registry, scenes
from homescreen.serve import create_app

CFG = {"location": {"name": "Madrid", "timezone": "Europe/Madrid"},
       "feeds": {"adsb": {"source": "api", "endpoint": "https://x"}},
       "devices": []}
HW = "aabb00112233"
Q = "w=240&h=240&depth=16&components=clock"


@pytest.fixture
def ctx(tmp_path):
    client = create_app(CFG, tmp_path, version="t").test_client()
    client.get(f"/api/devices/{HW}/scene?{Q}")
    registry.set_approval(tmp_path, HW, True)
    client.patch(f"/api/devices/{HW}", json={"name": "desk", "scene": "clock"})
    return client, tmp_path


def _drawn(client):
    body = client.get(f"/api/devices/{HW}/scene?{Q}").get_json()
    return [i["v"] for i in body["components"][0]["draw"]]


# --- the schema -------------------------------------------------------------

def test_a_component_declares_its_own_options():
    keys = [f["key"] for f in scenes.option_schema("clock")]
    assert "timezone" in keys and "show_seconds" in keys
    # The radar's range and aircraft cap were global -- one setting for the
    # whole house, so two radars could not watch different ranges and changing
    # either meant editing the Pi. They belong to the assignment now, with the
    # deployment's values as the defaults.
    radar_keys = {f["key"] for f in scenes.option_schema("planes")}
    assert {"radius_km", "max_aircraft"} <= radar_keys
    assert scenes.option_schema("nosuchscene") == ()


def test_every_declared_option_has_what_the_form_needs():
    # The dashboard renders fields from this; a field missing a key or a type
    # would render as a broken input rather than fail loudly.
    for name in scenes.names():
        for field in scenes.option_schema(name):
            assert field.get("key"), f"{name}: option without a key"
            assert field.get("label"), f"{name}: {field['key']} has no label"
            # Asked of the RENDERER rather than compared against a literal
            # list: a hardcoded list has to be edited every time a type is
            # added, and until someone does it reports a working field as
            # broken. This asks whether the thing can actually be drawn.
            from homescreen.web.fields import field as render
            markup = render(field, field.get("default"))
            assert any(tag in markup for tag in
                       ("<input", "<select", "<textarea")), \
                f"{name}: {field['key']} renders no control"
            assert "default" in field, f"{name}: {field['key']} has no default"
            if field["type"] == "choice":
                assert field.get("choices"), f"{name}: {field['key']} has no choices"
                assert field["default"] in field["choices"]


def test_defaults_are_what_an_unconfigured_assignment_behaves_as():
    d = scenes.defaults("clock")
    assert d["timezone"] == "" and d["show_seconds"] is False


# --- coercion ---------------------------------------------------------------

def test_unknown_keys_are_dropped_not_stored():
    # An option nothing reads is an option that looks configured and does
    # nothing -- worse than one that was refused.
    out = scenes.clean_options("clock", {"timezone": "Asia/Tokyo",
                                         "colour": "green", "": "x"})
    assert set(out) == {f["key"] for f in scenes.option_schema("clock")}
    assert out["timezone"] == "Asia/Tokyo"


@pytest.mark.parametrize("raw, want", [
    ("1", True), ("true", True), ("on", True), ("yes", True), (True, True),
    ("0", False), ("false", False), ("", False), (None, False), (False, False),
    ("banana", False),
])
def test_a_bool_option_accepts_what_a_form_actually_sends(raw, want):
    assert scenes.clean_options("clock", {"show_seconds": raw})["show_seconds"] is want


def test_a_text_option_is_trimmed_and_bounded():
    out = scenes.clean_options("clock", {"timezone": "  Asia/Tokyo  "})
    assert out["timezone"] == "Asia/Tokyo"
    long = scenes.clean_options("clock", {"timezone": "x" * 500})
    assert len(long["timezone"]) <= scenes.MAX_OPTION_LEN


def test_clean_options_never_raises_on_anything():
    # It runs on the serve path, where a 500 is a device with no screen.
    for raw in (None, "", 5, [], {"timezone": None}, {"timezone": []},
                {"show_seconds": {}}, {None: "x"}):
        scenes.clean_options("clock", raw)
    scenes.clean_options("nosuchscene", {"a": 1})


# --- storage ----------------------------------------------------------------

def test_options_are_stored_against_the_assignment(ctx):
    client, cache = ctx
    client.patch(f"/api/devices/{HW}", json={"options": {"timezone": "Asia/Tokyo"}})
    assert registry.load(cache)[HW]["options"]["timezone"] == "Asia/Tokyo"


def test_two_devices_can_configure_the_same_component_differently(ctx):
    # The whole reason configuration is per assignment.
    client, cache = ctx
    client.get(f"/api/devices/other/scene?{Q}")
    client.patch("/api/devices/other", json={"name": "hall", "scene": "clock"})
    client.patch(f"/api/devices/{HW}", json={"options": {"timezone": "Asia/Tokyo"}})
    client.patch("/api/devices/other",
                 json={"options": {"timezone": "America/New_York"}})
    stored = registry.load(cache)
    assert stored[HW]["options"]["timezone"] == "Asia/Tokyo"
    assert stored["other"]["options"]["timezone"] == "America/New_York"


def test_switching_component_clears_the_previous_options(ctx):
    # A clock left configured with a ticker's settings is present, meaningless
    # and invisible in the form.
    client, cache = ctx
    client.patch(f"/api/devices/{HW}", json={"options": {"timezone": "Asia/Tokyo"}})
    client.patch(f"/api/devices/{HW}", json={"scene": "planes"})
    assert registry.load(cache)[HW]["options"] == {}


def test_setting_a_scene_and_its_options_together_validates_against_the_new_one(ctx):
    # Otherwise the options are checked against the component being left.
    client, cache = ctx
    client.patch(f"/api/devices/{HW}", json={"scene": "planes"})
    r = client.patch(f"/api/devices/{HW}",
                     json={"scene": "clock", "options": {"timezone": "Asia/Tokyo"}})
    assert r.status_code == 200
    assert registry.load(cache)[HW]["options"]["timezone"] == "Asia/Tokyo"


def test_options_are_bounded_on_the_way_in(ctx):
    # Written from an unauthenticated LAN, like everything else here.
    client, cache = ctx
    client.patch(f"/api/devices/{HW}",
                 json={"options": {f"k{i}": "v" for i in range(200)}})
    assert len(registry.load(cache)[HW]["options"]) <= registry.MAX_OPTIONS


def test_a_record_with_junk_options_is_hidden_not_served(tmp_path):
    import json
    registry.touch(tmp_path, HW, now=1000.0)
    raw = json.loads(registry.registry_path(tmp_path).read_text())
    raw[HW]["options"] = "not a mapping"
    registry.registry_path(tmp_path).write_text(json.dumps(raw))
    assert HW not in registry.load(tmp_path)


# --- reaching the glass and the preview -------------------------------------

def test_an_option_changes_what_the_device_is_sent(ctx):
    client, _ = ctx
    before = _drawn(client)
    client.patch(f"/api/devices/{HW}", json={"options": {"timezone": "Asia/Tokyo"}})
    after = _drawn(client)
    assert before != after
    assert "Tokyo" in after


def test_an_option_changes_the_preview_too(ctx):
    # A preview that ignored options would show a layout the device never draws.
    client, _ = ctx
    client.patch(f"/api/devices/{HW}", json={"options": {"timezone": "Asia/Tokyo"}})
    assert "Tokyo" in client.get(f"/api/devices/{HW}/preview.svg?view=clock").get_data(as_text=True)


def test_a_bad_timezone_falls_back_rather_than_blanking_the_screen(ctx):
    client, _ = ctx
    client.patch(f"/api/devices/{HW}", json={"options": {"timezone": "Not/AZone"}})
    drawn = _drawn(client)
    assert drawn, "a bad option must not leave the screen with nothing"


def test_show_seconds_actually_shows_seconds(ctx):
    client, _ = ctx
    client.patch(f"/api/devices/{HW}", json={"options": {"show_seconds": True}})
    assert _drawn(client)[0].count(":") == 2


# --- the dashboard form -----------------------------------------------------

def test_the_form_renders_a_field_per_declared_option(ctx):
    client, _ = ctx
    html = client.get(f"/device/{HW}").get_data(as_text=True)
    for field in scenes.option_schema("clock"):
        assert f'name="opt.{field["key"]}"' in html, field["key"]


def test_the_form_shows_the_stored_values(ctx):
    client, _ = ctx
    client.patch(f"/api/devices/{HW}", json={"options": {"timezone": "Asia/Tokyo",
                                                         "show_seconds": True}})
    html = client.get(f"/device/{HW}").get_data(as_text=True)
    assert 'value="Asia/Tokyo"' in html
    assert "checked" in html


def test_applying_options_from_the_form_reaches_the_device(ctx):
    client, _ = ctx
    client.post("/home/device", data={"hw": HW, "name": "desk", "scene": "clock",
                                      "opt.timezone": "Asia/Tokyo"})
    assert "Tokyo" in _drawn(client)


def test_an_unchecked_box_turns_the_option_off(ctx):
    # A checkbox that is off sends nothing at all. Treating absence as "leave it
    # alone" would make a bool impossible to switch back off from a form.
    client, cache = ctx
    client.post("/home/device", data={"hw": HW, "name": "desk", "scene": "clock",
                                      "opt.timezone": "Asia/Tokyo",
                                      "opt.show_seconds": "1"})
    assert registry.load(cache)[HW]["options"]["show_seconds"] is True
    client.post("/home/device", data={"hw": HW, "name": "desk", "scene": "clock",
                                      "opt.timezone": "Asia/Tokyo"})
    assert registry.load(cache)[HW]["options"]["show_seconds"] is False


def test_a_form_post_with_no_option_fields_leaves_options_alone(ctx):
    # Renaming a device must not silently wipe its configuration.
    client, cache = ctx
    client.patch(f"/api/devices/{HW}", json={"options": {"timezone": "Asia/Tokyo"}})
    client.post("/home/device", data={"hw": HW, "name": "renamed",
                                      "scene": "clock"})
    assert registry.load(cache)[HW]["options"]["timezone"] == "Asia/Tokyo"


def test_a_hostile_option_value_cannot_reach_the_page_unescaped(ctx):
    client, _ = ctx
    client.post("/home/device", data={"hw": HW, "name": "desk", "scene": "clock",
                                      "opt.timezone": '"><img src=x onerror=1>'})
    html = client.get(f"/device/{HW}").get_data(as_text=True)
    assert '"><img src=x' not in html
    assert "&lt;img" in html or "&quot;&gt;" in html


def test_a_hostile_option_value_cannot_break_the_preview(ctx):
    client, _ = ctx
    client.post("/home/device", data={"hw": HW, "name": "desk", "scene": "clock",
                                      "opt.second_label": "</text><script>x</script>",
                                      "opt.second_timezone": "Asia/Tokyo"})
    svg = client.get(f"/api/devices/{HW}/preview.svg?view=clock").get_data(as_text=True)
    assert "<script>" not in svg


def test_the_registry_bounds_options_even_when_the_schema_does_not(tmp_path):
    # clean_options already limits the API to a component's own keys, so this
    # guard is unreachable from a request -- but registry.assign is a public
    # function and the record it writes is read back unvalidated on every poll.
    # Tested here directly, because a guard nothing exercises is a guard nobody
    # notices breaking.
    registry.touch(tmp_path, HW, now=1000.0)
    with pytest.raises(ValueError, match="at most"):
        registry.assign(tmp_path, HW,
                        options={f"k{i}": "v" for i in range(registry.MAX_OPTIONS + 1)})
    assert registry.load(tmp_path)[HW]["options"] == {}, "nothing was written"


def test_the_registry_refuses_options_that_are_not_a_mapping(tmp_path):
    registry.touch(tmp_path, HW, now=1000.0)
    for bad in ("string", 5, [], ("a", "b")):
        with pytest.raises(ValueError, match="mapping"):
            registry.assign(tmp_path, HW, options=bad)


def test_a_long_option_value_is_truncated_before_it_reaches_the_card(tmp_path):
    registry.touch(tmp_path, HW, now=1000.0)
    rec = registry.assign(tmp_path, HW, options={"timezone": "x" * 900})
    assert len(rec["options"]["timezone"]) <= registry.MAX_VALUE_LEN


def test_two_radars_can_watch_different_ranges(tmp_path):
    # The point of moving this off the global: it was one dial for the house.
    from homescreen import registry
    from homescreen.serve import create_app
    client = create_app(CFG, tmp_path, version="t").test_client()
    q = "w=240&h=240&depth=16&shape=round&components=radar,draw_list"
    for hw, radius in (("aa00000000ff", 20), ("bb00000000ff", 90)):
        client.get(f"/api/devices/{hw}/scene?{q}")
        client.put(f"/api/devices/{hw}/membership", json={"approved": True})
        registry.assign(tmp_path, hw, scene="planes",
                        options={"radius_km": radius})
    seen = {}
    for hw in ("aa00000000ff", "bb00000000ff"):
        body = client.get(f"/api/devices/{hw}/scene?{q}").get_json()
        seen[hw] = body["components"][0]["radius_km"]
    assert seen == {"aa00000000ff": 20.0, "bb00000000ff": 90.0}


def test_a_radar_nobody_configured_behaves_exactly_as_before(tmp_path):
    # Blank means "whatever the deployment was already doing". A screen that
    # has never been opened must not change because a field appeared.
    from homescreen import registry
    from homescreen.serve import create_app
    client = create_app(CFG, tmp_path, version="t").test_client()
    q = "w=240&h=240&depth=16&shape=round&components=radar,draw_list"
    client.get(f"/api/devices/{HW}/scene?{q}")
    client.put(f"/api/devices/{HW}/membership", json={"approved": True})
    registry.assign(tmp_path, HW, scene="planes")
    body = client.get(f"/api/devices/{HW}/scene?{q}").get_json()
    assert body["components"][0]["radius_km"] == 60.0, "the server's own default"
