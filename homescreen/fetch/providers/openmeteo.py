"""Current conditions and a real forecast, from Open-Meteo.

Keyless, which is the whole argument: no account, no quota to run out on a
Sunday, and nothing to leak through an error string onto an unauthenticated
LAN page.

Chosen over OpenWeather's free forecast for a correctness reason rather than a
convenience one. `/data/2.5/weather` returns `temp_min`/`temp_max`, which read
like today's low and high and are not -- they are the spread across a large
city's extent at this instant. The panel showed "21 / 24" on an afternoon that
ran 18.0 to 33.6. Open-Meteo's `daily.temperature_2m_min/max` are the actual
daily extremes, so the number on the glass means what it says.
"""

from __future__ import annotations

from homescreen.fetch.providers import _weather

NAME = "openmeteo"
ENDPOINT = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 10

PARAMS = (
    {"key": "lat", "label": "Latitud", "type": "float"},
    {"key": "lon", "label": "Longitud", "type": "float"},
    {"key": "units", "label": "Unidades", "type": "choice",
     "choices": ("metric", "imperial"), "default": "metric"},
    {"key": "days", "label": "Días de previsión", "type": "int", "default": 6},
    {"key": "place", "label": "Nombre del sitio", "type": "text", "default": ""},
)

#: The same ten minutes OpenWeather gets, for the same reason: weather moves
#: on the scale of tens of minutes, and a temperature ten minutes old is the
#: temperature. Keyless does not mean free to hammer -- this is somebody's
#: server, and a screen refreshing every ten minutes is a rounding error to
#: them and indistinguishable from live to us.
DEFAULT_INTERVAL_S = 600

#: None. That is the point of choosing it.
SECRETS = ()

#: WMO 4677 present-weather codes, folded onto the six skies this panel can
#: draw. The vendor's distinctions between "light" and "moderate" drizzle
#: cannot survive a 1-bit line drawing, so they are not preserved.
_WMO = {
    0: "clear", 1: "clear", 2: "cloud", 3: "cloud",
    45: "fog", 48: "fog",
    51: "rain", 53: "rain", 55: "rain", 56: "rain", 57: "rain",
    61: "rain", 63: "rain", 65: "rain", 66: "rain", 67: "rain",
    71: "snow", 73: "snow", 75: "snow", 77: "snow",
    80: "rain", 81: "rain", 82: "rain",
    85: "snow", 86: "snow",
    95: "storm", 96: "storm", 99: "storm",
}

#: Spanish, because everything that lands on glass is Spanish.
_WORDS = {"clear": "cielo despejado", "cloud": "nubes", "rain": "lluvia",
          "snow": "nieve", "storm": "tormenta", "fog": "niebla"}


def sky_of(code) -> str:
    return _WMO.get(_weather.epoch(code), "cloud")


def clean_params(raw: dict) -> dict:
    lat, lon = _weather.num((raw or {}).get("lat")), _weather.num((raw or {}).get("lon"))
    if lat is None or lon is None or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise ValueError("hacen falta latitud y longitud válidas")
    units = (raw or {}).get("units")
    return {"lat": round(lat, 4), "lon": round(lon, 4),
            "units": units if units in ("metric", "imperial") else "metric",
            # SIX, because the panel shows five days from TOMORROW. Asking
            # for five and dropping today left four rows under a heading that
            # promises five.
            "days": max(1, min(8, _weather.epoch((raw or {}).get("days")) or 6)),
            # Carried through, because this vendor answers coordinates and has
            # no name to give back. Part of the job key, so two screens
            # labelling the same coordinates differently are still one fetch
            # only if they agree -- which is the honest reading of "different
            # place".
            "place": str((raw or {}).get("place") or "").strip()[:40]}


def fetch(params: dict, *, session=None, secrets=None) -> dict:
    """Current conditions plus a daily and hourly forecast."""
    if session is None:
        import requests
        session = requests.Session()
    imperial = params.get("units") == "imperial"
    resp = session.get(ENDPOINT, timeout=TIMEOUT_S, params={
        "latitude": params["lat"], "longitude": params["lon"],
        "current": "temperature_2m,apparent_temperature,"
                   "relative_humidity_2m,weather_code,is_day",
        "daily": "temperature_2m_max,temperature_2m_min,weather_code,"
                 "precipitation_probability_max,sunrise,sunset",
        "hourly": "temperature_2m,weather_code",
        "forecast_days": params.get("days", 5),
        "timeformat": "unixtime",
        "timezone": "auto",
        "temperature_unit": "fahrenheit" if imperial else "celsius",
    })
    resp.raise_for_status()
    body = resp.json()
    current = body.get("current") or {}
    temp = _weather.num(current.get("temperature_2m"))
    if temp is None:
        raise ValueError("la respuesta no trae temperatura")

    sky = sky_of(current.get("weather_code"))
    daily = body.get("daily") or {}
    hourly = body.get("hourly") or {}
    return {
        "temp": temp,
        "feels_like": _weather.num(current.get("apparent_temperature")),
        "humidity": _weather.num(current.get("relative_humidity_2m")),
        "description": _WORDS.get(sky, ""),
        "sky": sky,
        # Open-Meteo answers coordinates, not names. The component falls back
        # to the operator's own `place`, which is a better label anyway --
        # "Casa" beats whichever suburb a reverse lookup would have picked.
        "place": str((params or {}).get("place") or "").strip(),
        "sunrise": _first_epoch(daily.get("sunrise")),
        "sunset": _first_epoch(daily.get("sunset")),
        "tz_offset_s": _weather.epoch(body.get("utc_offset_seconds")),
        "units": params.get("units", "metric"),
        "daily": _days(daily),
        "hourly": _hours(hourly),
    }


def _first_epoch(values):
    """The first timestamp, whichever form it came in.

    We ask for `timeformat=unixtime` and the API honours it. The ISO fallback
    is here because the failure it guards is silent: a vendor that quietly
    ignored the parameter would cost the clock row its sun times, and nothing
    would say so.
    """
    if not isinstance(values, list) or not values:
        return None
    raw = values[0]
    stamp = _weather.epoch(raw)
    if stamp is not None:
        return stamp
    try:
        import datetime
        return int(datetime.datetime.fromisoformat(str(raw)).timestamp())
    except (TypeError, ValueError):
        return None


def _days(daily: dict) -> list:
    times = daily.get("time") or []
    out = []
    for index, when in enumerate(times):
        out.append(_weather.day(
            when,
            _weather.num(_at(daily.get("temperature_2m_min"), index)),
            _weather.num(_at(daily.get("temperature_2m_max"), index)),
            sky_of(_at(daily.get("weather_code"), index)),
            _weather.num(_at(daily.get("precipitation_probability_max"), index)),
        ))
    return out


def _hours(hourly: dict) -> list:
    times = hourly.get("time") or []
    out = []
    for index, when in enumerate(times):
        out.append(_weather.hour(
            when,
            _weather.num(_at(hourly.get("temperature_2m"), index)),
            sky_of(_at(hourly.get("weather_code"), index)),
        ))
    return out


def _at(values, index):
    if not isinstance(values, list) or index >= len(values):
        return None
    return values[index]
