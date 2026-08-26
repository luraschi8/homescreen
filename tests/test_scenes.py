# tests/test_scenes.py
import re
from pathlib import Path

import pytest

from homescreen import scenes
from homescreen.cache import write_cache

CFG = {"location": {"name": "Madrid", "timezone": "Europe/Madrid"},
       "secondary_clock": {"label": "BS AS",
                           "timezone": "America/Argentina/Buenos_Aires"},
       "feeds": {"adsb": {"source": "api", "endpoint": "https://x"}},
       "devices": []}
EPAPER = {"w": 800, "h": 480, "depth": 1, "layouts": ["fill"]}
ROUND = {"w": 240, "h": 240, "depth": 16, "layouts": ["fill"]}


def ctx(tmp_path, caps=None, device=None, now=1_787_000_000.0):
    return scenes.SceneContext(
        cfg=CFG, cache_dir=tmp_path, caps=caps or EPAPER, now=now,
        device=device or {"hw": "aabb00112233", "id": "desk",
                          "name": "desk", "feed": "adsb", "max_aircraft": 20})


# --- the vocabulary contract ------------------------------------------------

def test_every_registered_scene_builds_for_both_device_classes(tmp_path):
    for name in scenes.names():
        for caps in (EPAPER, ROUND):
            scene = scenes.build(name, ctx(tmp_path, caps))
            assert isinstance(scene, scenes.Scene)
            assert scene.html or scene.components, \
                f"{name} produces nothing for {caps['w']}x{caps['h']}"


def test_assignable_scenes_are_derived_from_the_scene_table(tmp_path):
    # One source of truth: a second hand-maintained list would drift.
    from homescreen import registry
    assert set(registry.ASSIGNABLE_SCENES) == set(scenes.names())
    for builtin in registry.BUILTIN_SCENES:
        assert builtin not in registry.ASSIGNABLE_SCENES


@pytest.mark.parametrize("name", ["clock", "status", "planes"])
def test_pixel_push_html_obeys_the_one_bit_design_system(tmp_path, name):
    html = scenes.build(name, ctx(tmp_path)).html
    colours = set(re.findall(r"#[0-9a-fA-F]{3,6}", html))
    assert colours <= {"#000", "#fff", "#000000", "#ffffff"}, \
        f"{name} uses a colour that is not black or white: {colours}"
    sizes = [int(m) for m in re.findall(r"font-size:(\d+)px", html)]
    assert sizes and min(sizes) >= 10, f"{name} has type below the 10px floor"
    assert "-webkit-font-smoothing:none" in html
    assert "http://" not in html and "https://" not in html, "no CDN fonts, ever"


@pytest.mark.parametrize("name", ["clock", "status", "planes"])
def test_pixel_push_html_declares_the_exact_viewport(tmp_path, name):
    html = scenes.build(name, ctx(tmp_path)).html
    assert "width:800px" in html and "height:480px" in html


# --- individual scenes ------------------------------------------------------

def test_clock_shows_both_cities(tmp_path):
    html = scenes.build("clock", ctx(tmp_path)).html
    assert "Madrid" in html and "BS AS" in html
    assert len(re.findall(r"\d\d:\d\d", html)) >= 2


def test_clock_survives_a_broken_timezone(tmp_path):
    cfg = {**CFG, "secondary_clock": {"label": "X", "timezone": "Not/AZone"}}
    c = scenes.SceneContext(cfg=cfg, cache_dir=tmp_path, caps=EPAPER,
                            now=1_787_000_000.0, device={"hw": "x"})
    html = scenes.build("clock", c).html
    assert "Madrid" in html, "the good clock still renders"


def test_status_names_the_hardware_id_so_a_human_can_adopt_it(tmp_path):
    html = scenes.build("status", ctx(tmp_path)).html
    assert "aabb00112233" in html


def test_planes_emits_one_coarse_radar_component_for_data_push(tmp_path):
    # The design spec claimed the radar decomposes into generic rings+markers.
    # It does not -- the firmware draws eleven elements, two angles per marker,
    # and a collision ladder for labels. So it is ONE component carrying data.
    write_cache(tmp_path / "feed" / "adsb.json",
                {"aircraft": [{"cs": "IBE1", "ty": "A320", "alt": "3675 ft",
                               "dst": 7.4, "ve": 0.1, "vn": 0.2, "age": 1.0}]})
    scene = scenes.build("planes", ctx(tmp_path, ROUND))
    assert len(scene.components) == 1
    comp = scene.components[0]
    assert comp["c"] == "radar"
    assert len(comp["items"]) == 1
    assert comp["items"][0]["ve"] == 0.1, "velocity survives for dead reckoning"


def test_planes_renders_a_list_for_pixel_push(tmp_path):
    write_cache(tmp_path / "feed" / "adsb.json",
                {"aircraft": [{"cs": "IBE1", "ty": "A320", "alt": "3675 ft",
                               "dst": 7.4}]})
    html = scenes.build("planes", ctx(tmp_path)).html
    assert "IBE1" in html and "A320" in html


def test_planes_respects_the_device_cap(tmp_path):
    write_cache(tmp_path / "feed" / "adsb.json",
                {"aircraft": [{"cs": f"A{i}", "dst": float(i)} for i in range(50)]})
    dev = {"hw": "x", "id": "desk", "name": "desk", "feed": "adsb",
           "max_aircraft": 5}
    scene = scenes.build("planes", ctx(tmp_path, ROUND, dev))
    assert len(scene.components[0]["items"]) == 5


@pytest.mark.parametrize("bad", [
    None, "{not json", '{"data": {"aircraft": 5}}',
    '{"fetched_at":"x","ok":true,"data":{"aircraft":[1,2,"three"]}}',
])
def test_planes_never_raises_on_a_damaged_cache(tmp_path, bad):
    path = tmp_path / "feed" / "adsb.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if bad is not None:
        path.write_text(bad)
    scene = scenes.build("planes", ctx(tmp_path, ROUND))
    assert scene.components[0]["items"] == []


def test_planes_says_so_when_the_feed_is_down(tmp_path):
    write_cache(tmp_path / "feed" / "adsb.json", {"aircraft": []}, ok=False,
                error="boom")
    html = scenes.build("planes", ctx(tmp_path)).html
    assert "sin señal" in html


# --- the fallback contract --------------------------------------------------

def test_an_unknown_scene_falls_back_and_says_so(tmp_path):
    scene = scenes.safe_build("no-such-scene", ctx(tmp_path))
    assert "unknown scene" in scene.html
    assert "no-such-scene" in scene.html


def test_a_scene_that_raises_falls_back_rather_than_blanking_a_screen(tmp_path, monkeypatch):
    real = scenes._registry()          # capture BEFORE patching, or it recurses

    def boom(_ctx):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("homescreen.scenes._registry",
                        lambda: {**real, "planes": boom})
    scene = scenes.safe_build("planes", ctx(tmp_path))
    assert "RuntimeError" in scene.html, "the failure names itself"
    assert "planes" in scene.html


def test_safe_build_survives_the_scene_table_itself_being_broken(tmp_path, monkeypatch):
    # _fallback calls status.build directly rather than going back through the
    # table, precisely so a broken table still yields a screen.
    def broken():
        raise ValueError("table gone")

    monkeypatch.setattr("homescreen.scenes._registry", broken)
    scene = scenes.safe_build("clock", ctx(tmp_path))
    assert scene.html, "a screen must still get something to show"
    assert "ValueError" in scene.html
