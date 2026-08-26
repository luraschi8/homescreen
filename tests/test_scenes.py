# tests/test_scenes.py
import re
from pathlib import Path

import pytest

from homescreen import scenes
from homescreen.scenes import clock as _clock_mod
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


# The panel has two inks. Anything a browser would resolve to a third is a
# defect that only shows up as mud on real hardware, so the grammar here is a
# whitelist: the earlier `#[0-9a-f]{3,6}` scan happily passed `color:grey`,
# `rgb(128,128,128)` and `font-size:9.5pt`.
INK = {"#000", "#fff", "#000000", "#ffffff", "black", "white",
       "transparent", "inherit", "currentcolor", "none"}
COLOUR_PROPS = ("color", "background", "background-color", "border-color",
                "border", "border-top", "border-bottom", "border-left",
                "border-right", "outline", "fill", "stroke", "box-shadow",
                "text-shadow")
_DECL = re.compile(r"([a-z-]+)\s*:\s*([^;\"'}]+)")


@pytest.mark.parametrize("name", ["clock", "status", "planes"])
def test_pixel_push_html_uses_only_the_two_inks(tmp_path, name):
    html = scenes.build(name, ctx(tmp_path)).html
    assert not re.search(r"\b(rgba?|hsla?|color-mix|oklch|lab)\s*\(", html), \
        f"{name} computes a colour the panel cannot show"
    for prop, value in _DECL.findall(html):
        if prop not in COLOUR_PROPS:
            continue
        for token in re.split(r"\s+", value.strip()):
            if re.fullmatch(r"[\d.]+(px|%)?|solid|dashed|dotted|repeat|no-repeat|"
                            r"center|left|right|top|bottom|inset", token):
                continue  # widths, styles and positions, not inks
            assert token.lower() in INK, \
                f"{name} sets {prop}:{value.strip()} -- {token} is not an ink"


@pytest.mark.parametrize("name", ["clock", "status", "planes"])
def test_pixel_push_type_is_whole_pixels_at_or_above_the_floor(tmp_path, name):
    # A 10px floor stated in px says nothing about `font-size:0.6rem`, and a
    # fractional px lands on a half-lit pixel that thresholding turns to grey.
    html = scenes.build(name, ctx(tmp_path)).html
    sizes = re.findall(r"font-size\s*:\s*([^;\"'}]+)", html)
    assert sizes, f"{name} sets no type size at all"
    for raw in sizes:
        value = raw.strip()
        m = re.fullmatch(r"(\d+)px", value)
        assert m, f"{name} sizes type as {value!r}; only whole px is honest here"
        assert int(m.group(1)) >= 10, f"{name} has type below the 10px floor"


@pytest.mark.parametrize("name", ["clock", "status", "planes"])
def test_pixel_push_hierarchy_comes_from_size_and_weight(tmp_path, name):
    # CLAUDE.md: hierarchy by size+weight, because there is no grey to lean on.
    html = scenes.build(name, ctx(tmp_path)).html
    weights = {int(w) for w in re.findall(r"font-weight\s*:\s*(\d+)", html)}
    sizes = {int(p) for p in re.findall(r"font-size\s*:\s*(\d+)px", html)}
    assert len(sizes) >= 2 or len(weights) >= 2, \
        f"{name} is one flat block of type: sizes={sizes} weights={weights}"
    assert all(w in (400, 500, 600, 700, 800, 900) for w in weights), \
        f"{name} uses a weight the bundled face cannot render: {weights}"


@pytest.mark.parametrize("name", ["clock", "status", "planes"])
def test_pixel_push_html_is_self_contained(tmp_path, name):
    html = scenes.build(name, ctx(tmp_path)).html
    assert "-webkit-font-smoothing:none" in html
    assert "http://" not in html and "https://" not in html, "no CDN fonts, ever"
    assert "@import" not in html and "url(" not in html, \
        f"{name} would make the renderer wait on a fetch that cannot resolve"


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
    assert "escena desconocida" in scene.html
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


# The panel sits on a desk in Spain next to a radar whose firmware already says
# "aeronaves". Mixing an English sentence into Spanish chrome looked like a bug
# on the glass, so the rule is: glass is Spanish, operator surfaces are English.
ENGLISH_ON_GLASS = ("no scene", "not assigned", "unknown scene", "failed",
                    "error:", "no signal", "aircraft", "clear sky")


@pytest.mark.parametrize("name", ["clock", "status", "planes"])
def test_no_scene_puts_english_prose_on_the_glass(tmp_path, name):
    html = scenes.build(name, ctx(tmp_path)).html.lower()
    for phrase in ENGLISH_ON_GLASS:
        assert phrase not in html, f"{name} shows English on the panel: {phrase!r}"


@pytest.mark.parametrize("bad", ["nosuchscene", "clock"])
def test_the_failure_fallback_explains_itself_in_spanish(tmp_path, monkeypatch, bad):
    # Both fallback paths -- unknown name and a scene that raises -- reach the
    # same status panel, and both used to answer in English.
    if bad == "clock":
        monkeypatch.setattr(_clock_mod, "build",
                            lambda c: (_ for _ in ()).throw(RuntimeError("x")))
    html = scenes.safe_build(bad, ctx(tmp_path)).html
    assert "escena desconocida" in html or "fallo en" in html
    for phrase in ENGLISH_ON_GLASS:
        assert phrase not in html.lower()


def test_an_empty_sky_collapses_rather_than_showing_an_empty_frame(tmp_path):
    # A table with zero rows renders as a title and a rule over 400px of white,
    # which reads as a broken screen rather than a quiet one.
    html = scenes.build("planes", ctx(tmp_path)).html
    assert "cielo despejado" in html
    assert "<tr><td" in html, "the table must never be structurally empty"


def test_a_device_with_no_name_still_labels_itself(tmp_path):
    c = ctx(tmp_path, device={"hw": "aabb00112233"})
    html = scenes.build("status", c).html
    assert "sin asignar" in html, "an unnamed device must still say what it is"
    assert "aabb00112233" in html, "the hw id is what you type to assign it"


def test_a_scene_cannot_ship_a_layout_no_device_can_draw(tmp_path):
    # Spec §5.4 names `grid`; nothing implements it. Without this the word
    # could reach a device as a silent no-op that renders as a blank panel.
    assert scenes.LAYOUTS == ("fill",)
    with pytest.raises(ValueError, match="grid"):
        scenes.Scene(layout="grid")
    with pytest.raises(ValueError):
        scenes.Scene(layout="")


@pytest.mark.parametrize("name", ["clock", "status", "planes"])
def test_every_built_scene_declares_a_carried_layout(tmp_path, name):
    assert scenes.build(name, ctx(tmp_path)).layout in scenes.LAYOUTS
