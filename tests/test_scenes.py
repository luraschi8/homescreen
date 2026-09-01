# tests/test_scenes.py
import re
from datetime import datetime
from pathlib import Path

import pytest

from homescreen import datasource
from homescreen import scenes
from homescreen.scenes import clock as _clock_mod
from homescreen.cache import write_cache


def _sky_path(cache_dir, cfg, options=None):
    """Where the radar reads its sky, derived the way the scene derives it.

    Tests used to write `cache/feed/adsb.json` -- the per-device file from when
    there was one feed and one radar. The scene now declares a requirement and
    is handed a Reading, so a fixture that hardcodes a path is one that agrees
    with itself and with nothing else.
    """
    from homescreen import fetch, scenes
    need = scenes.needs("planes", options or {}, cfg)[0]
    key = fetch.providers.key(need["provider"],
                        fetch.providers.clean_params(need["provider"], need["params"]))
    path = fetch.store.path_for(cache_dir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path



CFG = {"location": {"name": "Madrid", "timezone": "Europe/Madrid",
                    "lat": 40.4168, "lon": -3.7038},
       "secondary_clock": {"label": "BS AS",
                           "timezone": "America/Argentina/Buenos_Aires"},
       "feeds": {"adsb": {"source": "api", "endpoint": "https://x"}},
       "devices": []}
EPAPER = {"w": 800, "h": 480, "depth": 1, "layouts": ["fill"]}
ROUND = {"w": 240, "h": 240, "depth": 16, "layouts": ["fill"]}


NOW = 1_787_000_000.0


def ctx(tmp_path, caps=None, device=None, now=NOW, options=None):
    from homescreen import fetch
    from homescreen.reading import Reading

    def data(requirement):
        """Resolve exactly as serve.py does, so these tests exercise the real
        key derivation rather than a fixture that agrees with itself."""
        try:
            params = fetch.providers.clean_params(requirement["provider"],
                                            requirement.get("params"))
        except (ValueError, KeyError, TypeError):
            return Reading.nothing()
        env = fetch.store.read(tmp_path,
                            fetch.providers.key(requirement["provider"], params))
        return Reading.from_envelope(env, now=now)

    return scenes.SceneContext(
        cfg=CFG, cache_dir=tmp_path, caps=caps or EPAPER, now=now,
        options=options or {}, data=data,
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


@pytest.mark.parametrize("name", ["clock", "status", "planes", "weather",
                                  "calendar", "quotes", "sport", "claude"])
def test_pixel_push_type_is_whole_pixels_at_or_above_the_floor(tmp_path, name):
    # A 10px floor stated in px says nothing about `font-size:0.6rem`, and a
    # fractional px lands on a half-lit pixel that thresholding turns to grey.
    html = scenes.build(name, ctx(tmp_path)).html
    # The ladder is declared once as custom properties and referenced by name,
    # so resolve it before checking. The invariant is unchanged -- every size
    # that reaches the panel is a whole pixel at or above the floor -- but it
    # is now a property of the LADDER as well as of each use of it.
    ladder = {f"--{n}": v for n, v in
              re.findall(r"(--[\w-]+)\s*:\s*(\d+)px", html)}
    ladder = {n: int(v) for n, v in
              re.findall(r"(--[\w-]+)\s*:\s*(\d+)px", html)}
    for token, value in ladder.items():
        assert value >= 1, f"{name} declares {token} as {value}px"
    sizes = re.findall(r"font-size\s*:\s*([^;\"'}]+)", html)
    assert sizes, f"{name} sets no type size at all"
    for raw in sizes:
        value = raw.strip()
        ref = re.fullmatch(r"var\((--[\w-]+)\)", value)
        if ref:
            assert ref.group(1) in ladder, \
                f"{name} uses undeclared {ref.group(1)}"
            resolved = ladder[ref.group(1)]
        else:
            m = re.fullmatch(r"(\d+)px", value)
            assert m, (f"{name} sizes type as {value!r}; only whole px, or a "
                       f"ladder step that is one, is honest here")
            resolved = int(m.group(1))
        assert resolved >= 10, \
            f"{name} has type below the 10px floor ({value} = {resolved}px)"


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
    # `>= 2` passed with ONE city: the footer stamp is `%Y-%m-%d %H:%M`, which
    # supplies the second match for free. Count the clock faces themselves.
    assert re.findall(r'class="(big|sub)">(\d\d:\d\d)', html) \
        and len(re.findall(r'class="(?:big|sub)">\d\d:\d\d', html)) == 2


def test_clock_survives_a_broken_timezone(tmp_path):
    cfg = {**CFG, "secondary_clock": {"label": "X", "timezone": "Not/AZone"}}
    c = scenes.SceneContext(
        data=datasource.reader(tmp_path, lambda: 1_787_000_000.0),cfg=cfg, cache_dir=tmp_path, caps=EPAPER,
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
    write_cache(_sky(tmp_path),
                {"aircraft": [{"cs": "IBE1", "ty": "A320", "alt": "3675 ft",
                               "dst": 7.4, "ve": 0.1, "vn": 0.2, "age": 1.0}]})
    scene = scenes.build("planes", ctx(tmp_path, ROUND))
    assert len(scene.components) == 1
    comp = scene.components[0]
    assert comp["c"] == "radar"
    assert len(comp["items"]) == 1
    assert comp["items"][0]["ve"] == 0.1, "velocity survives for dead reckoning"


def test_planes_renders_a_list_for_pixel_push(tmp_path):
    write_cache(_sky(tmp_path),
                {"aircraft": [{"cs": "IBE1", "ty": "A320", "alt": "3675 ft",
                               "dst": 7.4}]})
    html = scenes.build("planes", ctx(tmp_path)).html
    assert "IBE1" in html and "A320" in html


def test_planes_respects_the_device_cap(tmp_path):
    write_cache(_sky(tmp_path),
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
    path = _sky(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if bad is not None:
        path.write_text(bad)
    scene = scenes.build("planes", ctx(tmp_path, ROUND))
    assert scene.components[0]["items"] == []


def test_planes_says_so_when_the_feed_is_down(tmp_path):
    write_cache(_sky(tmp_path), {"aircraft": []}, ok=False,
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


# --- the device-facing wire shape --------------------------------------------
# The firmware parses these names. Nothing pinned them in either direction, so
# a rename or a dropped field would have shipped silently and shown up as a
# blank radar on hardware.

RADAR_ITEM_KEYS = {"lat", "lon", "nose", "trk", "gs", "ve", "vn",
                   "age", "dst", "cs", "ty", "alt"}


def test_the_radar_component_carries_exactly_the_agreed_fields(tmp_path):
    write_cache(_sky(tmp_path), {"aircraft": [
        {"lat": 40.5, "lon": -3.6, "nose": 90.0, "trk": 91.0, "gs": 400.0,
         "ve": 0.2, "vn": 0.0, "age": 3.1, "dst": 7.4,
         "cs": "IBE3221", "ty": "A320", "alt": "3675 ft"}]})
    comp, = scenes.build("planes", ctx(tmp_path)).components
    assert set(comp) == {"c", "items", "feed_ok", "feed_age_s", "radius_km"}
    assert comp["c"] == "radar"
    assert set(comp["items"][0]) == RADAR_ITEM_KEYS


def test_the_radar_component_states_the_range_it_covers(tmp_path):
    # The device scales its rings from this. Without it, a 30 km feed drawn on
    # a 60 km dial is wrong in a way nothing on either side can detect.
    dev = {"hw": "aabb00112233", "id": "desk", "name": "desk", "feed": "adsb",
           "max_aircraft": 20, "radius_km": 30}
    assert scenes.build("planes", ctx(tmp_path, device=dev)).components[0][
        "radius_km"] == 30.0
    bad = {**dev, "radius_km": "sixty"}
    assert scenes.build("planes", ctx(tmp_path, device=bad)).components[0][
        "radius_km"] == 60.0, "an unusable value must not reach the device"


def test_the_dead_reckoning_fields_are_never_dropped(tmp_path):
    # ve/vn/age are the whole reason this device is on data push (ADDENDUM §2).
    write_cache(_sky(tmp_path), {"aircraft": [
        {"lat": 40.5, "lon": -3.6, "trk": 90.0, "gs": 400.0,
         "ve": 0.13, "vn": -0.17, "age": 3.1, "dst": 7.4, "cs": "IBE1"}]})
    item = scenes.build("planes", ctx(tmp_path)).components[0]["items"][0]
    assert (item["ve"], item["vn"], item["age"]) == (0.13, -0.17, 3.1)


def test_the_clock_survives_every_timezone_being_broken(tmp_path):
    # The all-broken fallback was uncovered: with both zones unusable the scene
    # must still produce a panel, because a device showing nothing looks
    # identical to a device that has lost the server.
    cfg = {"location": {"name": "Nowhere", "timezone": "Not/AZone"},
           "secondary_clock": {"label": "X", "timezone": "Also/Bad"},
           "feeds": {}, "devices": []}
    c = scenes.SceneContext(
        data=datasource.reader(tmp_path, lambda: 1_787_000_000.0),cfg=cfg, cache_dir=tmp_path, caps=EPAPER,
                            now=1_787_000_000.0, device={"hw": "aa"})
    html = scenes.build("clock", c).html
    assert "--:--" in html, "a broken clock says so rather than going blank"
    assert "width:800px" in html


def _sky(tmp_path):
    return _sky_path(tmp_path, CFG)


def _feed_at(tmp_path, when, aircraft):
    """Write the feed cache stamped at `when`, not at the wall clock.

    `write_cache` stamps with the real time; every test here runs on a fixed
    fake clock, so a real stamp puts the record decades in the future and the
    dwell clamps to zero -- which is exactly the value the bug produced.
    """
    import json as _json
    from datetime import timezone
    path = _sky(tmp_path)
    path.write_text(_json.dumps({
        "fetched_at": datetime.fromtimestamp(when, timezone.utc).isoformat(),
        "ok": True, "error": None, "data": {"aircraft": aircraft}}))


# --- VALIDATION F4, second door ----------------------------------------------
# The dwell correction was right on /api/display/<id>/data and absent here,
# because this scene reads the cache file directly instead of going through
# serve._servable. A device dead-reckons from `age`; passing it through
# untouched means it extrapolates from a position kilometres behind the
# aeroplane and never dims, because the number it tests never grows.

def test_aircraft_age_is_recomputed_at_serve_time(tmp_path):
    _feed_at(tmp_path, NOW, [{"lat": 40.5, "lon": -3.6, "age": 3.1, "cs": "IBE1"}])
    fetched = scenes.build("planes", ctx(tmp_path)).components[0]["items"][0]["age"]
    later = scenes.build("planes", ctx(tmp_path, now=NOW + 20.0)) \
        .components[0]["items"][0]["age"]
    assert fetched == pytest.approx(3.1, abs=0.1)
    assert later == pytest.approx(23.1, abs=0.1), \
        "20s in our cache must reach the device as 20s of extra age"


def test_the_component_reports_the_feed_age_separately(tmp_path):
    # Separate from the per-aircraft age on purpose: PLAN.md §3 records that
    # summing the two staleness causes made targets blink once per cycle.
    _feed_at(tmp_path, NOW, [{"lat": 40.5, "lon": -3.6, "age": 3.1, "cs": "IBE1"}])
    comp = scenes.build("planes", ctx(tmp_path, now=NOW + 8.0)).components[0]
    assert comp["feed_age_s"] == pytest.approx(8.0, abs=0.1)
    assert comp["items"][0]["age"] == pytest.approx(11.1, abs=0.1)


def test_a_future_stamped_cache_never_reports_negative_age(tmp_path):
    # The Pi has no RTC and boots at the time timesyncd last saved.
    _feed_at(tmp_path, NOW, [{"lat": 40.5, "lon": -3.6, "age": 3.1, "cs": "IBE1"}])
    comp = scenes.build("planes", ctx(tmp_path, now=NOW - 3600)).components[0]
    assert comp["feed_age_s"] >= 0.0
    assert comp["items"][0]["age"] == pytest.approx(3.1, abs=0.1)


def test_an_unreadable_age_is_dropped_rather_than_sent_as_zero(tmp_path):
    _feed_at(tmp_path, NOW, [{"lat": 1.0, "lon": 2.0, "age": "soon", "cs": "BAD"},
                             {"lat": 3.0, "lon": 4.0, "age": 1.0, "cs": "OK"}])
    items = scenes.build("planes", ctx(tmp_path)).components[0]["items"]
    assert [a["cs"] for a in items] == ["OK"]


def test_a_device_never_receives_more_items_than_it_declared(tmp_path):
    # ArduinoJson peaks near 44 KB parsing 100 of these against ~55 KB of free
    # heap on the C3. The device knows its own RAM; the server does not. An
    # operator raising max_aircraft must not be able to blank a panel.
    _feed_at(tmp_path, NOW, [{"lat": 40.0 + i / 100, "lon": -3.6, "age": 1.0,
                              "cs": f"IBE{i:04d}"} for i in range(300)])
    dev = {"hw": "aa", "id": "d", "feed": "adsb", "max_aircraft": 200,
           "max_items": 64}
    items = scenes.build("planes", ctx(tmp_path, device=dev)).components[0]["items"]
    assert len(items) == 64, "the device's own limit wins when it is smaller"


def test_the_operators_limit_still_wins_when_it_is_smaller(tmp_path):
    _feed_at(tmp_path, NOW, [{"lat": 40.0 + i / 100, "lon": -3.6, "age": 1.0,
                              "cs": f"IBE{i:04d}"} for i in range(300)])
    dev = {"hw": "aa", "id": "d", "feed": "adsb", "max_aircraft": 12,
           "max_items": 64}
    items = scenes.build("planes", ctx(tmp_path, device=dev)).components[0]["items"]
    assert len(items) == 12, "the operator says how many are useful"


def test_a_device_that_declares_no_limit_gets_the_operators(tmp_path):
    _feed_at(tmp_path, NOW, [{"lat": 40.0 + i / 100, "lon": -3.6, "age": 1.0,
                              "cs": f"IBE{i:04d}"} for i in range(300)])
    dev = {"hw": "aa", "id": "d", "feed": "adsb", "max_aircraft": 30}
    items = scenes.build("planes", ctx(tmp_path, device=dev)).components[0]["items"]
    assert len(items) == 30


def test_wire_floats_are_rounded_to_what_a_float32_can_hold(tmp_path):
    # `"ve": -0.13970290959420342` is 21 bytes of noise per aircraft per poll
    # into a device that parses float32. Rounding roughly halves the body, and
    # the body size decides whether the device parses the scene or refuses it.
    _feed_at(tmp_path, NOW, [{"lat": 40.512345678, "lon": -3.698765432,
                              "ve": -0.13970290959420342,
                              "vn": 0.20871234567890123, "age": 3.14159265,
                              "dst": 7.43219876, "gs": 400.123456,
                              "trk": 91.98765, "nose": 90.55555, "cs": "IBE1"}])
    item = scenes.build("planes", ctx(tmp_path)).components[0]["items"][0]
    assert item["ve"] == -0.1397 and item["vn"] == 0.20871
    assert item["lat"] == 40.51235 and item["lon"] == -3.69877
    assert item["dst"] == 7.43 and item["gs"] == 400.1
    assert len(repr(item["ve"])) <= 8, "no 17-digit floats on the wire"


def test_rounding_does_not_shift_a_position_enough_to_matter(tmp_path):
    # 5 decimal places of latitude is ~1.1 m. The panel is 240 px across 60 km.
    _feed_at(tmp_path, NOW, [{"lat": 40.5123456789, "lon": -3.6987654321,
                              "age": 1.0, "cs": "IBE1"}])
    item = scenes.build("planes", ctx(tmp_path)).components[0]["items"][0]
    assert abs(item["lat"] - 40.5123456789) < 1e-5
    assert abs(item["lon"] - -3.6987654321) < 1e-5


def test_a_cache_stamp_we_cannot_read_reports_the_feed_as_dead(tmp_path):
    # Returning 0.0 said "brand new", which pinned feed_age_s at zero forever
    # and left the device permanently blind to a dead feed -- the one number it
    # has for that failure would never move.
    import json as _json
    p = _sky(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    def comp_for(stamp):
        p.write_text(_json.dumps({"fetched_at": stamp, "ok": True,
                                  "error": None, "data": {"aircraft": [
                                      {"lat": 1.0, "lon": 2.0, "age": 1.0,
                                       "cs": "X"}]}}))
        return scenes.build("planes", ctx(tmp_path)).components[0]

    # A STRING that will not parse, or parses without an offset, reaches
    # _dwell. Naive is the dangerous one: it looks like a timestamp.
    for stamp in ("2026-08-26T19:00:00", "not-a-date", ""):
        assert comp_for(stamp)["feed_age_s"] >= 3600, f"{stamp!r} read as fresh"

    # A NON-string is rejected by read_cache before we ever see it, so the
    # scene reports no feed at all rather than a stale one -- also safe, but
    # for a different reason, and the device sees an empty sky.
    for stamp in (None, 12345, [], {}):
        c = comp_for(stamp)
        assert c["feed_age_s"] is None and c["feed_ok"] is False
        assert c["items"] == []


# --- the body has to fit in a device that has ~55 KB of heap ------------------
# ArduinoJson 7 peaks around 4.6x the body for this shape. The device declares
# max_items and refuses anything over its own byte cap, so if this grows past
# what it will accept the panel goes blank at exactly the busiest time of day --
# and the server is the half that can change without anyone reflashing.

@pytest.mark.parametrize("cap, max_bytes", [(20, 4096), (30, 6144), (40, 8192)])
def test_a_full_scene_fits_the_devices_byte_budget(tmp_path, cap, max_bytes):
    import json as _json
    _feed_at(tmp_path, NOW, [
        {"lat": 40.5 + i / 1000, "lon": -3.6 - i / 1000, "nose": 90.0,
         "trk": 91.0, "gs": 400.0, "ve": -0.13970290959420342,
         "vn": 0.20871234567890123, "age": 3.1416, "dst": 7.4321,
         "cs": "ABCDEFGH", "ty": "A388", "alt": "FL120 ft"}   # worst-case widths
        for i in range(cap + 50)])
    dev = {"hw": "aa", "id": "d", "feed": "adsb", "max_aircraft": 1000,
           "max_items": cap}
    scene = scenes.build("planes", ctx(tmp_path, device=dev))
    assert len(scene.components[0]["items"]) == cap
    body = _json.dumps({"components": list(scene.components)},
                       separators=(",", ":"))
    assert len(body) <= max_bytes, (
        f"{cap} items serialise to {len(body)} bytes; the device refuses "
        f"anything over its cap and shows nothing at all")


def test_a_placement_with_no_options_key_still_renders(tmp_path):
    # A record written before options existed, or edited by hand, has a
    # placement with no `options` at all. Indexing it raised, and the device
    # asking for its scene got a 500 -- so the panel that most needed to say
    # something showed nothing.
    from homescreen import registry
    from homescreen.serve import create_app
    hw = "aa11bb22cc33"
    app = create_app({"devices": []}, tmp_path, version="t")
    client = app.test_client()
    client.get(f"/api/devices/{hw}/scene?w=240&h=240&depth=16&shape=round")
    client.put(f"/api/devices/{hw}/membership", json={"approved": True})
    records = registry.load(tmp_path)
    records[hw]["views"] = {"panel": {"template": "single", "placements": [
        {"id": "p1", "region": "full", "component": "clock"}]}}   # no options
    registry.save(tmp_path, records)
    got = client.get(f"/api/devices/{hw}/scene?w=240&h=240&depth=16")
    assert got.status_code == 200, got.get_data(as_text=True)[:300]
