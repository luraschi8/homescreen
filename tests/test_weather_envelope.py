"""One weather envelope, whichever vendor answered.

A component that decodes `01d` has learned which vendor it is talking to, and
a source cannot then be swapped without editing it. The envelope is the
contract: providers normalise INTO it, the component reads only it, and
"configurable source" becomes a dropdown rather than a rewrite.
"""
import pytest

from homescreen.fetch.providers import _weather


class _Resp:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        pass


def _session(body):
    class Session:
        @staticmethod
        def get(url, **kw):
            return _Resp(body)
    return Session()


OPENWEATHER_BODY = {
    "main": {"temp": 22.4, "feels_like": 22.0, "temp_min": 21.4,
             "temp_max": 23.6, "humidity": 40},
    "weather": [{"description": "cielo claro", "icon": "01d"}],
    "name": "Madrid",
    "sys": {"sunrise": 1_788_000_000, "sunset": 1_788_050_000},
    "timezone": 7200,
}

OPENMETEO_BODY = {
    "current": {"temperature_2m": 22.8, "apparent_temperature": 22.1,
                "relative_humidity_2m": 40, "weather_code": 0,
                "is_day": 1},
    # Real shapes, captured from the live API on 2026-09-01: with
    # `timeformat=unixtime` every time field is an int epoch, not an ISO
    # string. The first version of this fixture guessed ISO and the adapter
    # dutifully returned None for every sunrise.
    "daily": {"time": [1_788_213_600, 1_788_300_000],
              "temperature_2m_max": [33.6, 31.0],
              "temperature_2m_min": [18.0, 17.5],
              "weather_code": [0, 3],
              "precipitation_probability_max": [0, 20],
              "sunrise": [1_788_241_332], "sunset": [1_788_291_240]},
    "hourly": {"time": [1_788_213_600, 1_788_217_200],
               "temperature_2m": [26.0, 28.0],
               "weather_code": [0, 3]},
    "utc_offset_seconds": 7200,
    "timezone": "Europe/Madrid",
}


def _both():
    from homescreen.fetch.providers import openmeteo, openweather
    params = {"lat": 40.4168, "lon": -3.7038, "units": "metric", "lang": "es"}
    return {
        "openweather": openweather.fetch(
            params, session=_session(OPENWEATHER_BODY),
            secrets={"api_key": "k"}),
        "openmeteo": openmeteo.fetch(
            openmeteo.clean_params(params), session=_session(OPENMETEO_BODY)),
    }


@pytest.mark.parametrize("vendor", ["openweather", "openmeteo"])
def test_every_source_emits_the_same_required_fields(vendor):
    got = _both()[vendor]
    for field in _weather.REQUIRED:
        assert field in got, f"{vendor} omits {field}"


@pytest.mark.parametrize("vendor", ["openweather", "openmeteo"])
def test_the_sky_is_a_normalised_token_not_a_vendor_code(vendor):
    # `01d` is OpenWeather's. A component that reads it cannot be pointed at
    # anything else, which is the whole reason this contract exists.
    got = _both()[vendor]
    assert got["sky"] in _weather.SKY, (vendor, got["sky"])


@pytest.mark.parametrize("vendor", ["openweather", "openmeteo"])
def test_no_source_leaks_its_own_encoding(vendor):
    got = _both()[vendor]
    assert "weather_code" not in got
    assert got.get("icon") in (None, ""), "raw vendor codes stay in the adapter"


def test_both_sources_read_the_same_clear_sky_the_same_way():
    both = _both()
    assert both["openweather"]["sky"] == both["openmeteo"]["sky"] == "clear"


def test_a_source_with_a_forecast_provides_one_and_the_other_does_not():
    # Not every source can answer every question, and the component has to
    # cope: `daily` is absent rather than empty-and-lying.
    both = _both()
    assert both["openmeteo"]["daily"], "open-meteo has a forecast"
    assert both["openmeteo"]["daily"][0]["max"] == 33.6
    assert both["openmeteo"]["daily"][0]["min"] == 18.0
    assert not both["openweather"].get("daily"), \
        "current conditions are not a forecast"


def test_the_forecast_entries_have_the_shape_the_component_expects():
    day = _both()["openmeteo"]["daily"][0]
    for field in ("date", "min", "max", "sky"):
        assert field in day, field
    assert day["sky"] in _weather.SKY


def test_the_hourly_strip_is_normalised_too():
    hourly = _both()["openmeteo"]["hourly"]
    assert len(hourly) >= 2
    assert hourly[0]["temp"] == 26.0
    assert hourly[0]["sky"] in _weather.SKY
    assert "time" in hourly[0]


def test_sunrise_and_sunset_arrive_as_epochs_from_either_source():
    for vendor, got in _both().items():
        assert isinstance(got["sunrise"], int), vendor
        assert isinstance(got["sunset"], int), vendor
        assert got["sunset"] > got["sunrise"], vendor
