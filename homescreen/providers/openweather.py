"""Current conditions and a short forecast, from OpenWeather.

Chosen as the first non-ADS-B provider because it is one unauthenticated-shaped
call with a free tier and no rate-limit tuning to get wrong -- so it tests the
machinery rather than the vendor. Nothing in the platform depends on it being
this vendor: a different one is another module declaring the same four things.
"""

from __future__ import annotations

NAME = "openweather"

PARAMS = (
    {"key": "lat", "label": "Latitud", "type": "float"},
    {"key": "lon", "label": "Longitud", "type": "float"},
    {"key": "units", "label": "Unidades", "type": "choice",
     "choices": ("metric", "imperial"), "default": "metric"},
    {"key": "lang", "label": "Idioma", "type": "text", "default": "es"},
)

#: Weather changes on the scale of tens of minutes and the free tier is
#: generous but finite. Ten minutes is well inside both, and a screen showing a
#: temperature ten minutes old is showing the temperature.
DEFAULT_INTERVAL_S = 600

SECRETS = ("api_key",)

ENDPOINT = "https://api.openweathermap.org/data/2.5/weather"
TIMEOUT_S = (3.05, 8)


def clean_params(raw: dict) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    try:
        lat = float(raw["lat"])
        lon = float(raw["lon"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("el tiempo necesita lat y lon") from None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise ValueError("lat/lon fuera de rango")
    units = str(raw.get("units") or "metric")
    if units not in ("metric", "imperial"):
        units = "metric"
    lang = str(raw.get("lang") or "es")[:5]
    return {"lat": round(lat, 4), "lon": round(lon, 4), "units": units,
            "lang": lang}


def fetch(params: dict, *, session=None, secrets=None) -> dict:
    """Current conditions. Raises on anything that is not a usable reading.

    Normalised here, not at draw time: a component should receive
    `temp`/`description`/`icon` whichever vendor produced them, so swapping the
    provider does not touch the component.
    """
    key = (secrets or {}).get("api_key")
    if not key:
        raise ValueError("falta la clave de OpenWeather")
    if session is None:
        import requests
        session = requests.Session()
    resp = session.get(ENDPOINT, timeout=TIMEOUT_S, params={
        "lat": params["lat"], "lon": params["lon"],
        "units": params.get("units", "metric"),
        "lang": params.get("lang", "es"), "appid": key})
    resp.raise_for_status()
    body = resp.json()
    if not isinstance(body, dict):
        raise ValueError("respuesta inesperada de OpenWeather")
    main = body.get("main")
    if not isinstance(main, dict) or main.get("temp") is None:
        # An error body is still JSON and still 200-shaped on some proxies.
        # Treating it as a reading would put a blank temperature on the glass
        # and call the feed healthy.
        raise ValueError("la respuesta no trae temperatura")
    weather = (body.get("weather") or [{}])[0]
    if not isinstance(weather, dict):
        weather = {}
    return {
        "temp": _num(main.get("temp")),
        "feels_like": _num(main.get("feels_like")),
        "temp_min": _num(main.get("temp_min")),
        "temp_max": _num(main.get("temp_max")),
        "humidity": _num(main.get("humidity")),
        "description": str(weather.get("description") or "").strip(),
        "icon": str(weather.get("icon") or "").strip(),
        "place": str(body.get("name") or "").strip(),
        "units": params.get("units", "metric"),
    }


def _num(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n and abs(n) != float("inf") else None
