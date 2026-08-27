"""The first component built on the provider layer.

It is here to prove the path end to end -- component declares a need, a job is
derived, a payload is fetched, a Reading comes back, pixels result -- and to
pin the contract every later component signs: adapt to the glass you are given.
"""
import pathlib
import tempfile

import pytest

from homescreen import jobs, providers, scenes
from homescreen.reading import Reading

CFG = {"location": {"lat": 40.4, "lon": -3.7, "name": "Madrid"},
       "feeds": {"adsb": {"endpoint": "https://x"}}}

READING = Reading(data={"temp": 21.4, "temp_min": 17.0, "temp_max": 26.0,
                        "description": "cielo claro", "place": "Madrid",
                        "units": "metric"}, ok=True, age_s=120.0)


def ctx(caps, options=None, data=None):
    return scenes.SceneContext(
        cfg=CFG, cache_dir=pathlib.Path(tempfile.mkdtemp()), caps=caps,
        now=1_787_000_000.0, device={}, options=options or {},
        data=data or (lambda req: READING))


def drawn(caps, **kw):
    return [d["v"] for d in scenes.build("weather", ctx(caps, **kw))
            .components[0]["draw"]]


# --- the surface decides the arrangement ------------------------------------

def test_a_round_panel_gets_one_big_number():
    values = drawn({"w": 240, "h": 240, "depth": 16, "shape": "round"})
    assert values[0] == "21°C"
    assert "Madrid" in values


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
    assert values[0].endswith("°F")
    need = scenes.needs("weather", {"units": "imperial"}, CFG)[0]
    assert need["params"]["units"] == "imperial"


def test_a_place_can_be_overridden_per_assignment():
    values = drawn({"w": 240, "h": 240}, options={"place": "La oficina"})
    assert "La oficina" in values


def test_a_screen_elsewhere_asks_for_a_different_fetch():
    here = scenes.needs("weather", {}, CFG)[0]["params"]
    there = scenes.needs("weather", {"lat": "51.5", "lon": "-0.12"}, CFG)[0]["params"]
    assert here != there
    assert providers.key("openweather", here) != providers.key("openweather", there)


def test_two_screens_in_the_same_place_share_one_fetch():
    plan = jobs.collect({"a": {"scene": "weather", "options": {}},
                         "b": {"scene": "weather", "options": {}}}, CFG)
    assert len(plan) == 1


def test_hiding_the_range_hides_it():
    assert any("/" in v for v in drawn({"w": 240, "h": 240}))
    assert not any("/" in v for v in drawn({"w": 240, "h": 240},
                                           options={"show_range": False}))


# --- the provider -----------------------------------------------------------

def test_the_provider_refuses_to_fetch_without_its_key():
    # Better than a 401 loop that looks like an outage.
    from homescreen.providers import openweather
    with pytest.raises(ValueError, match="clave"):
        openweather.fetch({"lat": 1, "lon": 2}, secrets={})


def test_a_200_that_is_not_a_reading_is_a_failure_not_a_blank_temperature():
    # Some proxies return a JSON error body with a 200. Treating it as a
    # reading puts an empty temperature on the glass and calls the feed healthy.
    from homescreen.providers import openweather

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
    from homescreen.providers import openweather

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
