"""ADS-B aircraft near a point.

The first provider, and the one that proves the port is the right shape: it
already existed as a bespoke daemon reading config.yaml, and becoming a
provider costs it nothing it was doing on purpose. What it loses is the
assumption that there is exactly one radar in the house.

The fetching itself still lives in `sources/adsb.py`, which has the measured
timeouts, the request spacing the public endpoint requires, and the failure
handling that keeps the last good sky. This module is the declaration -- what
parameters identify a fetch, and how often it is worth making one.
"""

from __future__ import annotations

NAME = "adsb"

#: The parameters that make one ADS-B fetch different from another. Two screens
#: watching the same centre at the same radius are one request; a second radar
#: pointed elsewhere is a second job rather than a second daemon.
PARAMS = (
    {"key": "lat", "label": "Latitud", "type": "float"},
    {"key": "lon", "label": "Longitud", "type": "float"},
    {"key": "radius_km", "label": "Radio (km)", "type": "float",
     "default": 60.0},
    {"key": "endpoint", "label": "Endpoint", "type": "text", "default": ""},
    {"key": "show_ground", "label": "Incluir tráfico en tierra", "type": "bool",
     "default": False},
)

#: adsb.fi's public limit is one request a second, and the firmware's
#: dead-reckoning horizon is 12s. See sources/adsb.py: this is the cadence that
#: keeps worst-case dwell under that horizon for a single radar.
DEFAULT_INTERVAL_S = 5

#: adsb.fi's public limit is one request a second, enforced as SPACING rather
#: than as an average. This is a property of the upstream, not of one job: five
#: radars on five centres would otherwise fire together and violate it however
#: politely each was scheduled on its own.
MIN_SPACING_S = 1.0

#: None. The public endpoint is unauthenticated; a keyed one would declare it.
SECRETS: tuple = ()


def clean_params(raw: dict) -> dict:
    """Coerce, or raise ValueError. A centre is mandatory: a radar with no
    idea where it is would fetch the wrong sky and look fine doing it."""
    raw = raw if isinstance(raw, dict) else {}
    try:
        lat = float(raw["lat"])
        lon = float(raw["lon"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("adsb necesita lat y lon") from None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise ValueError("lat/lon fuera de rango")
    try:
        radius = float(raw.get("radius_km") or 60.0)
    except (TypeError, ValueError):
        radius = 60.0
    radius = max(1.0, min(500.0, radius))
    endpoint = str(raw.get("endpoint") or "").strip()
    if endpoint and not endpoint.startswith(("http://", "https://")):
        raise ValueError("el endpoint debe empezar por http:// o https://")
    if not endpoint:
        # Required, not optional. An empty endpoint produced a schemeless path
        # that `requests` rejects forever -- a job fetching nothing while
        # looking perfectly healthy, which is the failure this module's own
        # docstring warns about.
        raise ValueError("adsb necesita un endpoint")
    return {"lat": round(lat, 5), "lon": round(lon, 5),
            "radius_km": round(radius, 2), "endpoint": endpoint,
            "show_ground": bool(raw.get("show_ground", False))}


def fetch(params: dict, *, session=None, secrets=None) -> dict:
    """One sky, as the cache stores it. Raises on failure; the caller records
    it -- keeping the last good sky is the job runner's decision, not this
    adapter's."""
    from homescreen.sources import adsb as impl
    return impl.fetch_payload(params, session=session)
