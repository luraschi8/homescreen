# tests/test_hardening.py
"""Regressions for an adversarial review. Every test here corresponds to a
reproduction that worked against the code before it.
"""
import json
import os
import stat
import tempfile
from pathlib import Path

import pytest

from homescreen import registry, render, scenes
from homescreen.cache import write_cache
from homescreen.serve import create_app

CFG = {"location": {"name": "Madrid", "timezone": "Europe/Madrid"},
       "feeds": {"adsb": {"source": "api", "endpoint": "https://x"}},
       "devices": []}


@pytest.fixture
def client(tmp_path):
    c = create_app(CFG, tmp_path, version="t").test_client()
    c.cache_dir = tmp_path          # so a test can read what the route wrote
    return c


def _needs_chromium():
    if render.find_chromium() is None:
        pytest.skip("no chromium/chrome on this machine")


# --- a malformed record must be hidden, never deleted -----------------------

def test_a_malformed_record_survives_an_unrelated_device_poll(tmp_path):
    # load() filtered invalid records and every mutator wrote the FILTERED
    # view back, so one poll from any device permanently erased another
    # device's name and scene. Read-repair-by-deletion.
    registry.touch(tmp_path, "A", now=1000.0)
    registry.assign(tmp_path, "A", name="kitchen", scene="planes")
    raw = json.loads(registry.registry_path(tmp_path).read_text())
    raw["B"] = {**raw["A"], "name": "office", "poll_seconds": True}
    registry.registry_path(tmp_path).write_text(json.dumps(raw))

    registry.touch(tmp_path, "C", now=1000.0)

    on_disk = json.loads(registry.registry_path(tmp_path).read_text())
    assert "B" in on_disk, "a malformed record must not be silently destroyed"
    assert on_disk["B"]["name"] == "office"
    assert "B" not in registry.load(tmp_path), "but it is hidden from consumers"


def test_a_malformed_record_cannot_be_assigned_over(tmp_path):
    registry.touch(tmp_path, "A", now=1000.0)
    raw = json.loads(registry.registry_path(tmp_path).read_text())
    raw["A"]["poll_seconds"] = "soon"
    registry.registry_path(tmp_path).write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="malformed"):
        registry.assign(tmp_path, "A", name="x")


# --- writes must degrade, not 500 -------------------------------------------

def test_admin_routes_return_503_on_a_read_only_card(tmp_path):
    # ext4 remounts read-only on error and CLAUDE.md flags SD wear as live.
    registry.touch(tmp_path, "A", now=1000.0)
    client = create_app(CFG, tmp_path, version="t").test_client()
    os.chmod(tmp_path, stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert client.patch("/api/devices/A", json={"name": "x"}).status_code == 503
        assert client.delete("/api/devices/A").status_code == 503
    finally:
        os.chmod(tmp_path, stat.S_IRWXU)


def test_the_config_api_returns_503_on_a_read_only_card(tmp_path):
    cfg = {**CFG, "devices": [{"id": "radar", "kind": "gc9a01_client",
                               "render": "device", "feed": "adsb",
                               "home": {"lat": 1, "lon": 2}}]}
    client = create_app(cfg, tmp_path, version="t").test_client()
    os.chmod(tmp_path, stat.S_IRUSR | stat.S_IXUSR)
    try:
        r = client.patch("/api/config/devices/radar", json={"max_aircraft": 30})
        assert r.status_code == 503
    finally:
        os.chmod(tmp_path, stat.S_IRWXU)


def test_deleting_an_override_that_does_not_exist_writes_nothing(tmp_path):
    cfg = {**CFG, "devices": [{"id": "radar", "kind": "gc9a01_client",
                               "render": "device", "feed": "adsb"}]}
    client = create_app(cfg, tmp_path, version="t").test_client()
    assert client.delete("/api/config/devices/nope").status_code == 404
    r = client.delete("/api/config/devices/radar")
    assert r.status_code == 200
    assert not (tmp_path / "overrides.json").exists(), \
        "no pointless write to a wear-limited card"


# --- the frame must match the panel that asked for it -----------------------

def test_a_poisoned_handshake_is_refused_not_rendered_wrong(client):
    # Registration is unauthenticated, so any LAN host can claim to be any
    # device. What it must NEVER get is a wrong-length body at HTTP 200: the
    # panel streams that straight at hardware and cannot detect it. The server
    # refuses instead, and says exactly what it holds.
    _needs_chromium()
    client.get("/api/device/panel/scene?w=800&h=480&depth=1")
    assert len(client.get("/api/device/panel/frame?w=800&h=480").get_data()) \
        == 800 * 480 // 8

    client.get("/api/device/panel/scene?w=1024&h=256")            # the attack

    r = client.get("/api/device/panel/frame?w=800&h=480")
    assert r.status_code == 409, "a disagreement must never render"
    assert r.get_json()["registered"] == {"w": 1024, "h": 256}
    assert r.get_json()["requested"] == {"w": 800, "h": 480}


def test_the_panel_heals_itself_on_its_next_handshake(client):
    # The device speaks every cycle; the attacker spoke once. So the damage
    # window is one poll interval, and it closes without an operator.
    _needs_chromium()
    client.get("/api/device/panel2/scene?w=800&h=480&depth=1")
    client.get("/api/device/panel2/scene?w=1024&h=256")           # the attack
    assert client.get("/api/device/panel2/frame?w=800&h=480").status_code == 409

    client.get("/api/device/panel2/scene?w=800&h=480&depth=1")    # next cycle
    assert len(client.get("/api/device/panel2/frame?w=800&h=480").get_data()) \
        == 800 * 480 // 8


def test_a_fragment_cannot_redefine_what_a_device_is(client):
    # `?components=nothing` with no geometry used to be a complete declaration:
    # it merged over the stored caps and blanked the radar until the device
    # next spoke. A capability list is now only read as part of a handshake.
    client.get("/api/device/radar1/scene?w=240&h=240&depth=16&components=radar")
    client.get("/api/device/radar1/scene?components=nothing")     # the attack
    assert registry.load(client.cache_dir)["radar1"]["caps"]["components"] \
        == ["radar"]


def test_the_frame_route_cannot_declare_anything_at_all(client):
    # Defence in depth: even a full, well-formed handshake on /frame must not
    # be persisted. The frame route reads the record; it never writes to it.
    _needs_chromium()
    client.get("/api/device/panel3/scene?w=800&h=480&depth=1&components=text")
    client.get("/api/device/panel3/frame?w=800&h=480&components=nothing&depth=16")
    caps = registry.load(client.cache_dir)["panel3"]["caps"]
    assert caps["components"] == ["text"] and caps["depth"] == 1


@pytest.mark.parametrize("caps", [
    {"w": "abc", "h": 480}, {"w": ["x"], "h": 480}, {"w": None},
    "not a dict", {"h": {"n": 1}}, {"w": True},
])
def test_a_hand_edited_capability_never_500s_a_route(tmp_path, caps):
    # `w` as a string reached int() in both routes. clean_caps prevents it over
    # HTTP; a hand-edited devices.json does not go through clean_caps, and the
    # "registry is full" message invites exactly that edit.
    _needs_chromium()
    client = create_app(CFG, tmp_path, version="t").test_client()
    client.get("/api/device/P/scene")
    raw = json.loads(registry.registry_path(tmp_path).read_text())
    raw["P"]["caps"] = caps
    registry.registry_path(tmp_path).write_text(json.dumps(raw))
    assert client.get("/api/device/P/scene").status_code == 200
    assert client.get("/api/device/P/frame").status_code == 200


@pytest.mark.parametrize("w,h", [(12, 8), (4, 2), (101, 8)])
def test_a_width_that_is_not_a_multiple_of_eight_is_refused(w, h):
    # Pillow pads each ROW, so the constraint is on width, not area. (w*h)%8
    # let 12x8 through, which forked a browser, rendered ~3s, failed packing,
    # and returned a retryable 503 that was never cached.
    with pytest.raises(render.RenderError, match="multiple of 8"):
        render.check_geometry(w, h)


# --- content from devices and upstream must not control the panel -----------

@pytest.mark.parametrize("payload", [
    '<div style="position:fixed;inset:0;background:#000">',
    '<style>*{display:none}',
    '<!--',
])
def test_a_hostile_device_name_cannot_black_out_a_panel(tmp_path, payload):
    # A full-black 800x480 refresh is the worst case for e-paper ghosting.
    # Measured before escaping: 100.00% ink.
    _needs_chromium()
    render.clear_cache()
    ctx = scenes.SceneContext(cfg=CFG, cache_dir=tmp_path, caps={"w": 800, "h": 480},
                              now=1_787_000_000.0,
                              device={"hw": "aa", "name": payload})
    packed = render.render_frame(scenes.build("status", ctx).html, 800, 480)
    ink = sum(bin(b).count("1") for b in packed) / (800 * 480)
    assert 0 < ink < 0.20, f"ink {ink:.2%} -- markup escaped the page"


def test_a_hostile_upstream_callsign_cannot_control_the_panel(tmp_path):
    # cs/ty/alt come from adsb.fi, so without escaping a third-party feed
    # partly controls what the panel draws.
    _needs_chromium()
    render.clear_cache()
    write_cache(tmp_path / "feed" / "adsb.json", {"aircraft": [
        {"cs": '<div style="position:fixed;inset:0;background:#000">',
         "ty": "A320", "alt": "1 ft", "dst": 1.0}]})
    ctx = scenes.SceneContext(cfg=CFG, cache_dir=tmp_path, caps={"w": 800, "h": 480},
                              now=1_787_000_000.0,
                              device={"hw": "aa", "id": "x", "feed": "adsb",
                                      "max_aircraft": 20})
    packed = render.render_frame(scenes.build("planes", ctx).html, 800, 480)
    ink = sum(bin(b).count("1") for b in packed) / (800 * 480)
    assert ink < 0.20, f"ink {ink:.2%} -- a feed controlled the panel"


def test_a_non_numeric_distance_from_upstream_does_not_break_the_page(tmp_path):
    write_cache(tmp_path / "feed" / "adsb.json",
                {"aircraft": [{"cs": "X", "dst": "far"}]})
    ctx = scenes.SceneContext(cfg=CFG, cache_dir=tmp_path, caps={"w": 800, "h": 480},
                              now=1_787_000_000.0,
                              device={"hw": "aa", "id": "x", "feed": "adsb",
                                      "max_aircraft": 20})
    assert "X" in scenes.build("planes", ctx).html


# --- seeding and eviction ---------------------------------------------------

def test_seeding_never_writes_a_record_its_own_validator_rejects(tmp_path):
    # A quoted number in config.yaml produced a record that vanished on the
    # next read, while the once-only marker guaranteed it never came back.
    cfg = {"devices": [{"id": "radar", "scene": "planes", "poll_seconds": "5"}]}
    assert registry.seed_from_config(cfg, tmp_path, now=1000.0) == 1
    assert "cfg:radar" in registry.load(tmp_path), "seeded record must be visible"
    assert registry.load(tmp_path)["cfg:radar"]["poll_seconds"] == 5.0


def test_a_full_registry_evicts_a_stale_unassigned_device(tmp_path):
    # Registration is unauthenticated, so without eviction a device churning
    # its id permanently locks out real hardware.
    for i in range(registry.MAX_DEVICES):
        registry.touch(tmp_path, f"{i:012x}", now=1000.0)
    registry.touch(tmp_path, "ffffffffffff", now=100_000.0)     # all others stale
    assert "ffffffffffff" in registry.load(tmp_path)
    assert len(registry.load(tmp_path)) <= registry.MAX_DEVICES


def test_a_full_registry_of_named_devices_refuses_rather_than_evicting(tmp_path):
    for i in range(registry.MAX_DEVICES):
        hw = f"{i:012x}"
        registry.touch(tmp_path, hw, now=1000.0)
        registry.assign(tmp_path, hw, name=f"screen{i}")
    with pytest.raises(ValueError, match="named, assigned or online"):
        registry.touch(tmp_path, "ffffffffffff", now=1000.0)


# --- the assignable-scenes sequence must not lie ----------------------------

def test_assignable_scenes_behaves_like_a_real_sequence():
    # As a tuple subclass, every un-overridden operation saw the empty tuple it
    # was constructed from: `A == ()` was True and `BUILTIN + A` silently
    # dropped every scene -- the exact idiom used one line away.
    A = registry.ASSIGNABLE_SCENES
    assert len(A) > 0
    assert A != ()
    assert A[0] == sorted(scenes.names())[0]
    assert list(A) == list(scenes.names())
    assert tuple(registry.BUILTIN_SCENES) + tuple(A) != tuple(registry.BUILTIN_SCENES)


# --- the render queue is not a public resource --------------------------------

def _stub_browser(monkeypatch):
    from PIL import Image

    def stub(html, w, h, out_png, binary=None):
        Image.new("1", (w, h), 1).save(out_png)

    monkeypatch.setattr("homescreen.render.html_to_png", stub)


def test_minting_hardware_ids_does_not_buy_render_slots(client, monkeypatch):
    # A per-device throttle alone is defeated by inventing devices: 40 made-up
    # ids bought 40 cold renders at ~2.9s of Chromium each, against 2 slots.
    _stub_browser(monkeypatch)
    render.clear_cache()
    before = render.cache_stats()["misses"]
    codes = []
    for i in range(120):
        codes.append(client.get(
            f"/api/device/bot{i % 40}/frame?w=800&h=480").status_code)
    forks = render.cache_stats()["misses"] - before
    assert forks <= 3, f"{forks} cold renders bought by 120 hostile GETs"
    assert codes.count(429) > 100


def test_a_configured_panel_is_served_while_the_flood_runs(client, monkeypatch):
    # The gate must protect the fleet, not join the attack. A device an
    # operator has actually assigned is never subject to the global budget.
    _stub_browser(monkeypatch)
    render.clear_cache()
    client.get("/api/device/desk/scene?w=800&h=480&depth=1")
    client.patch("/api/devices/desk", json={"name": "desk", "scene": "clock"})
    for i in range(60):
        client.get(f"/api/device/bot{i}/frame?w=800&h=480")
    r = client.get("/api/device/desk/frame?w=800&h=480")
    assert r.status_code == 200
    assert len(r.get_data()) == 800 * 480 // 8


def test_a_newly_flashed_panel_still_gets_its_first_frame(client, monkeypatch):
    # The cost of the global budget is borne by real hardware exactly once, on
    # first boot, and must be seconds -- not a lockout.
    _stub_browser(monkeypatch)
    render.clear_cache()
    r = client.get("/api/device/brandnew/frame?w=800&h=480")
    assert r.status_code == 200, "an unconfigured device is not an attacker"
    assert len(r.get_data()) == 800 * 480 // 8
