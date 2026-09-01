"""The first component built on the provider layer.

It is here to prove the path end to end -- component declares a need, a job is
derived, a payload is fetched, a Reading comes back, pixels result -- and to
pin the contract every later component signs: adapt to the glass you are given.
"""
import pathlib
import tempfile

import pytest

from homescreen import fetch, scenes
from homescreen.reading import Reading

CFG = {"location": {"lat": 40.4, "lon": -3.7, "name": "Madrid"},
       "feeds": {"adsb": {"endpoint": "https://x"}}}

#: `sky` is the NORMALISED token every adapter emits -- not `01d`, which is
#: OpenWeather's own encoding and is decoded in the adapter now. It was missing
#: here entirely at one point, so the only production caller of `draw.icon`
#: never ran in a test and the whole shape vocabulary could be deleted with
#: the suite green.
READING = Reading(data={"temp": 21.4, "description": "cielo claro",
                        "place": "Madrid", "sky": "clear", "units": "metric",
                        # A REAL daily range, from a forecast. The old fixture
                        # carried `temp_min`/`temp_max` off the current
                        # endpoint, which are not the day's extremes at all.
                        "daily": [{"date": 1_788_213_600, "min": 17.0,
                                   "max": 26.0, "sky": "clear",
                                   "precip_pct": 0}]},
                  ok=True, age_s=120.0)


def ctx(caps, options=None, data=None):
    return scenes.SceneContext(
        cfg=CFG, cache_dir=pathlib.Path(tempfile.mkdtemp()), caps=caps,
        now=1_787_000_000.0, device={}, options=options or {},
        data=data or (lambda req: READING))


def instructions(caps, **kw):
    return scenes.build("weather", ctx(caps, **kw)).components[0]["draw"]


def drawn(caps, **kw):
    """The TEXT an instruction list puts on the glass.

    Shape-aware, because a draw list is not all text any more. It used to do
    `d["v"]` on everything, which is why adding the missing `icon` fixture key
    broke the helpers -- and presumably why the key was never added.
    """
    return [d["v"] for d in instructions(caps, **kw) if d.get("t") == "text"]


def shapes(caps, **kw):
    return [d for d in instructions(caps, **kw) if d.get("t") != "text"]


# --- the surface decides the arrangement ------------------------------------

def test_a_round_panel_gets_one_big_number():
    # The number alone. The unit moved to the line below -- not only for looks:
    # at `xl` the panel picks a bitmap face covering ASCII alone, so a degree
    # sign in the headline drew a blank box on real glass.
    values = drawn({"w": 240, "h": 240, "depth": 16, "shape": "round"})
    assert values[0] == "21"
    assert any("Madrid" in v and "°C" in v for v in values)


def test_the_big_number_is_drawable_by_the_bitmap_faces():
    # The bug, as a test: anything at `xl` must be plain ASCII, because the
    # faces the ladder reaches for at that size cover 0x20-0x7E only.
    scene = scenes.build("weather", ctx({"w": 240, "h": 240, "depth": 16}))
    for item in scene.components[0]["draw"]:
        if item.get("size") == "xl":
            assert all(ord(c) <= 0x7E for c in item["v"]), item["v"]


def test_the_temperature_is_coloured_by_what_it_feels_like():
    # A temperature is the one number here with an intuitive scale, so colour
    # says something a label would need words for.
    from homescreen.scenes.weather import _temp_tone
    assert _temp_tone(2, "metric") == "cool"
    assert _temp_tone(15, "metric") == "normal"
    assert _temp_tone(24, "metric") == "warn"
    assert _temp_tone(32, "metric") == "hot"
    assert _temp_tone(90, "imperial") == "hot", "converted, not compared raw"
    assert _temp_tone(None, "metric") == "dim"


def test_a_wide_band_gets_the_same_reading_along_the_line():
    # 764x62 has no room to stack a label under a number. Same options, same
    # data, different presentation -- the contract a component signs when it
    # declares it can draw on a surface.
    values = drawn({"w": 764, "h": 62, "depth": 1})
    assert len(values) == 1
    assert "21°C" in values[0] and "Madrid" in values[0]


def test_the_component_never_learns_what_a_region_is():
    # It is handed geometry, exactly as it is on a whole panel.
    assert drawn({"w": 764, "h": 62, "depth": 1}) != drawn(
        {"w": 240, "h": 240, "depth": 16})


# --- what it says when it has nothing ---------------------------------------

def test_with_no_data_it_says_so_rather_than_drawing_a_blank():
    # A blank panel and a wrong API key look identical from the sofa.
    values = drawn({"w": 240, "h": 240, "depth": 16},
                   data=lambda req: Reading.nothing())
    assert values[0] == "--"
    assert any("sin datos" in v for v in values)


def test_a_scene_builds_with_no_data_port_at_all():
    # Previews and unit tests must not need a daemon running.
    scene = scenes.build("weather", scenes.SceneContext(
        cfg=CFG, cache_dir=pathlib.Path(tempfile.mkdtemp()),
        caps={"w": 240, "h": 240}, now=0, device={}, options={}))
    assert scene.components


# --- options ----------------------------------------------------------------

def test_units_change_the_symbol_and_the_request():
    values = drawn({"w": 240, "h": 240}, options={"units": "imperial"})
    assert any(v.endswith("°F") for v in values)
    need = scenes.needs("weather", {"units": "imperial"}, CFG)[0]
    assert need["params"]["units"] == "imperial"


def test_a_place_can_be_overridden_per_assignment():
    values = drawn({"w": 240, "h": 240}, options={"place": "La oficina"})
    assert any("La oficina" in v for v in values)


def test_a_screen_elsewhere_asks_for_a_different_fetch():
    here = scenes.needs("weather", {}, CFG)[0]["params"]
    there = scenes.needs("weather", {"lat": "51.5", "lon": "-0.12"}, CFG)[0]["params"]
    assert here != there
    assert fetch.providers.key("openweather", here) != fetch.providers.key("openweather", there)


def test_two_screens_in_the_same_place_share_one_fetch():
    plan = fetch.derive({"a": {"scene": "weather", "options": {}},
                         "b": {"scene": "weather", "options": {}}}, CFG)
    assert len(plan) == 1


def test_hiding_the_range_hides_it():
    assert any("/" in v for v in drawn({"w": 240, "h": 240}))
    assert not any("/" in v for v in drawn({"w": 240, "h": 240},
                                           options={"show_range": False}))


# --- the provider -----------------------------------------------------------

def test_the_provider_refuses_to_fetch_without_its_key():
    # Better than a 401 loop that looks like an outage.
    from homescreen.fetch.providers import openweather
    with pytest.raises(ValueError, match="clave"):
        openweather.fetch({"lat": 1, "lon": 2}, secrets={})


def test_a_200_that_is_not_a_reading_is_a_failure_not_a_blank_temperature():
    # Some proxies return a JSON error body with a 200. Treating it as a
    # reading puts an empty temperature on the glass and calls the feed healthy.
    from homescreen.fetch.providers import openweather

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"cod": 401, "message": "Invalid API key"}

    class Session:
        def get(self, *a, **k):
            return Resp()

    with pytest.raises(ValueError, match="temperatura"):
        openweather.fetch({"lat": 1, "lon": 2}, session=Session(),
                          secrets={"api_key": "k"})


def test_the_payload_is_normalised_so_a_vendor_swap_does_not_touch_the_scene():
    from homescreen.fetch.providers import openweather

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"main": {"temp": 21.4, "temp_min": 17.0, "temp_max": 26.0,
                             "humidity": 40},
                    "weather": [{"description": "cielo claro", "icon": "01d"}],
                    "name": "Madrid"}

    class Session:
        def get(self, *a, **k):
            return Resp()

    got = openweather.fetch({"lat": 1, "lon": 2, "units": "metric"},
                            session=Session(), secrets={"api_key": "k"})
    assert got["temp"] == 21.4 and got["description"] == "cielo claro"
    assert got["place"] == "Madrid"


def test_weather_is_offered_on_a_small_panel_and_the_radar_is_not():
    tiny = {"w": 120, "h": 120, "depth": 16, "shape": "round"}
    assert scenes.supports("weather", tiny)[0] is True
    assert scenes.supports("planes", tiny)[0] is False


# --- the sky icon ------------------------------------------------------------
#
# `weather.py` is the only production caller of `draw.icon`. Until these
# existed, `icon()` could return `[]` and every test still passed.

def test_the_sky_is_drawn_not_just_described():
    got = shapes({"w": 240, "h": 240, "depth": 16, "shape": "round"})
    assert got, "a clear sky draws a sun"
    assert {s["t"] for s in got} <= {"circle", "line", "tri"}
    assert any(s["t"] == "circle" for s in got), "the sun has a disc"
    assert sum(1 for s in got if s["t"] == "line") == 8, "and eight rays"


def test_the_icon_is_expanded_server_side_so_the_device_never_sees_a_name():
    # The whole reason icons are primitives: a new icon must not be a reflash.
    for item in instructions({"w": 240, "h": 240, "depth": 16}):
        assert "icon" not in item
        assert item.get("t") in ("text", "circle", "line", "tri")


def test_a_different_sky_draws_a_different_picture():
    # Holding everything else constant, so it is the icon being tested and not
    # the temperature that happens to travel with it.
    def sky(code):
        data = dict(READING.data, sky=code)
        reading = Reading(data=data, ok=True, age_s=120.0)
        return shapes({"w": 240, "h": 240, "depth": 16},
                      data=lambda req: reading)

    clear, rain = sky("clear"), sky("rain")
    assert clear and rain
    assert clear != rain, "a clear sky and a wet one must not draw the same"


def test_a_sky_we_have_no_picture_for_still_shows_the_temperature():
    reading = Reading(data=dict(READING.data, sky="aurora"), ok=True, age_s=1.0)
    caps = {"w": 240, "h": 240, "depth": 16, "shape": "round"}
    assert shapes(caps, data=lambda req: reading) == []
    assert drawn(caps, data=lambda req: reading)[0] == "21"


def test_the_shapes_stay_on_the_glass():
    # Fractions, so every coordinate is 0..1 by construction -- and a bug that
    # broke that would put the sun off the edge of a 240px panel.
    for item in shapes({"w": 240, "h": 240, "depth": 16}):
        for key in ("cx", "cy", "x1", "y1", "x2", "y2"):
            if key in item:
                assert 0.0 <= item[key] <= 1.0, (key, item)
        for value in item.get("p", ()):
            assert 0.0 <= value <= 1.0, item


# --- the sun ------------------------------------------------------------------
#
# `sys.sunrise` and `sys.sunset` are in every OpenWeather current-conditions
# response and were being discarded. The original dashboard puts them inline
# beside the clock, so this is data we already pay for and already have.

def _raw(**overrides):
    body = {"main": {"temp": 21.4, "temp_min": 17.0, "temp_max": 26.0,
                     "humidity": 50},
            "weather": [{"description": "cielo claro", "icon": "01d"}],
            "name": "Madrid",
            "sys": {"sunrise": 1_788_000_000, "sunset": 1_788_050_000},
            "timezone": 7200}
    body.update(overrides)
    return body


class _Resp:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        pass


def _fetch(body):
    from homescreen.fetch.providers import openweather

    class Session:
        @staticmethod
        def get(url, **kw):
            return _Resp(body)

    return openweather.fetch({"lat": 40.4, "lon": -3.7, "units": "metric",
                              "lang": "es"},
                             session=Session(), secrets={"api_key": "k"})


def test_sunrise_and_sunset_reach_the_envelope():
    got = _fetch(_raw())
    assert got["sunrise"] == 1_788_000_000
    assert got["sunset"] == 1_788_050_000


def test_the_places_own_utc_offset_travels_with_them():
    # The times are UTC instants; without the offset a component would render
    # them in the SERVER's zone, which is right in Madrid and wrong anywhere
    # a second screen might be.
    assert _fetch(_raw())["tz_offset_s"] == 7200


def test_a_response_without_the_sun_is_still_a_reading():
    # Some vendors and some mocks do not send it. The temperature is the
    # reason for the request; the sun is a bonus and must not fail the fetch.
    got = _fetch(_raw(sys={}))
    assert got["temp"] == 21.4
    assert got["sunrise"] is None and got["sunset"] is None


def test_nonsense_sun_values_are_dropped_rather_than_rendered():
    got = _fetch(_raw(sys={"sunrise": "soon", "sunset": None}))
    assert got["sunrise"] is None and got["sunset"] is None


# --- the range has to be a real range -----------------------------------------
#
# `/data/2.5/weather` returns `main.temp_min` and `main.temp_max`, which read
# like today's low and high and are not: OpenWeather documents them as the
# minimum and maximum CURRENTLY OBSERVED across a large city's extent. On a
# real Madrid afternoon the panel showed "21° / 24°" while the day's actual
# range was 18.0 / 33.6. A plausible wrong number is worse than none.

def test_current_conditions_do_not_claim_a_daily_range():
    got = _fetch(_raw())
    for key in ("temp_min", "temp_max"):
        assert got.get(key) is None, (
            f"{key} from the current-conditions endpoint is the city's spread "
            f"right now, not today's extreme")


def test_a_daily_range_is_shown_only_when_the_reading_carries_one():
    from homescreen.reading import Reading
    caps = {"w": 240, "h": 240, "depth": 16, "shape": "round"}
    # No forecast at all: a current-conditions source has none, and the range
    # line collapses rather than inventing one.
    without = Reading(data={k: v for k, v in READING.data.items()
                            if k != "daily"}, ok=True, age_s=60.0)
    lines = drawn(caps, data=lambda req: without)
    assert not any("/" in v and "°" in v for v in lines), lines

    with_range = Reading(data=dict(
        READING.data,
        daily=[{"date": 1_788_213_600, "min": 18.0, "max": 33.6,
                "sky": "clear", "precip_pct": 0}]), ok=True, age_s=60.0)
    lines = drawn(caps, data=lambda req: with_range)
    assert any("18" in v and "34" in v for v in lines), lines


# --- the source is a setting --------------------------------------------------

def test_the_source_is_selectable_and_defaults_to_the_keyless_one():
    from homescreen import scenes
    schema = {o["key"]: o for o in scenes.option_schema("weather")}
    assert "source" in schema, "the operator can choose where weather comes from"
    assert set(schema["source"]["choices"]) == {"openmeteo", "openweather"}
    # Keyless by default: a screen works out of the box, and an operator who
    # never opens the settings never hits a missing-key error.
    assert schema["source"]["default"] == "openmeteo"


def test_the_chosen_source_is_the_one_the_fetcher_is_asked_for():
    from homescreen.scenes import weather
    cfg = {"location": {"lat": 40.4, "lon": -3.7, "name": "Madrid"}}
    for source in ("openmeteo", "openweather"):
        need = weather.needs({"source": source}, cfg)[0]
        assert need["provider"] == source, need


def test_two_screens_on_different_sources_are_two_fetches():
    # `plan.collect` keys a job on (provider, params), so this falls out --
    # but it is the property that makes the setting meaningful, so it is
    # pinned rather than assumed.
    from homescreen import fetch
    cfg = {"location": {"lat": 40.4, "lon": -3.7}}
    plan = fetch.derive({
        "a": {"scene": "weather", "options": {"source": "openmeteo"}},
        "b": {"scene": "weather", "options": {"source": "openweather"}}}, cfg)
    assert {j.provider for j in plan.values()} == {"openmeteo", "openweather"}


def test_the_component_reads_the_sky_not_a_vendor_code():
    # `01d` never reaches here any more. A reading that still carried one
    # would draw no icon rather than the wrong one.
    from homescreen.reading import Reading
    caps = {"w": 240, "h": 240, "depth": 16, "shape": "round"}
    clear = Reading(data=dict(READING.data, sky="clear"), ok=True, age_s=60.0)
    got = shapes(caps, data=lambda req: clear)
    assert got, "a clear sky draws a sun"
    assert sum(1 for s in got if s["t"] == "line") == 8


def test_an_unknown_sky_draws_nothing_rather_than_the_wrong_picture():
    from homescreen.reading import Reading
    caps = {"w": 240, "h": 240, "depth": 16, "shape": "round"}
    odd = Reading(data=dict(READING.data, sky="aurora"), ok=True, age_s=60.0)
    assert shapes(caps, data=lambda req: odd) == []
