# homescreen/sources/adsb_map.py
"""Map one raw adsb.fi record to the compact form the radar consumes.

Pure: no I/O, no clock. The fallback chains mirror the firmware -- see the
authority note in the plan before changing anything here.
"""

from __future__ import annotations

import math

KNOTS_TO_KM_PER_S = 1.852 / 3600.0

# Widths fixed by the firmware struct (callsign[9], type[5], alt[12]), less NUL.
CS_MAX = 8
TY_MAX = 4
ALT_MAX = 11


def _num(raw: dict, key: str) -> float | None:
    """Numeric read that rejects bool (matching ArduinoJson's is<int>()) and
    non-finite values.

    `1e400` is legal RFC-8259 and `json.loads` turns it into `inf`. Unfiltered
    that does two separate kinds of damage: `_round_half_up(inf)` raises
    OverflowError out of the fetch loop, and any field that skips rounding
    serialises as bare `Infinity`, which is not strict JSON. ArduinoJson is
    defaults ARDUINOJSON_ENABLE_INFINITY/NAN to 0 (a library default under a
    ^7.4.2 pin, not an explicit build flag), so one poisoned record makes
    deserializeJson reject the ENTIRE body and blank the radar for every device
    on the LAN. inf/nan is not a value; it is a broken feed.
    """
    v = raw.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def _first_num(raw: dict, keys: tuple[str, ...]) -> float:
    for k in keys:
        v = _num(raw, k)
        if v is not None:
            return v
    return 0.0


def _text(raw: dict, key: str, limit: int) -> str:
    """Truncate THEN strip trailing spaces, matching copyJsonStringTrimmed.
    The reverse order, or stripping both ends, gives different answers."""
    v = raw.get(key)
    if not isinstance(v, str):
        return ""
    return v[:limit].rstrip(" ")


def _round_half_up(x: float) -> int:
    """lroundf semantics: half away from zero, not Python's banker's rounding."""
    return int(math.floor(x + 0.5)) if x >= 0 else int(math.ceil(x - 0.5))


def _altitude_tag(raw: dict) -> str:
    if raw.get("alt_baro") == "ground":
        return "GND"
    alt = _num(raw, "alt_baro")
    if alt is None:
        alt = _num(raw, "alt_geom")
    if alt is None:
        return ""
    return f"{_round_half_up(alt)} ft"[:ALT_MAX]


def map_aircraft(raw: dict, *, show_ground: bool = False) -> dict | None:
    """Return the compact record, or None if this aircraft should be dropped."""
    if raw.get("alt_baro") == "ground" and not show_ground:
        return None

    lat = _num(raw, "lat")
    lon = _num(raw, "lon")
    if lat is None or lon is None:
        return None

    trk = _first_num(raw, ("track", "true_heading", "mag_heading", "dir"))
    gs = _first_num(raw, ("gs", "tas", "ias"))

    # Resolve the track into east/north once here, so the device dead-reckons
    # without trig per frame.
    gs_km_s = gs * KNOTS_TO_KM_PER_S
    trk_rad = math.radians(trk)

    dst = _num(raw, "dst")
    age = _num(raw, "seen_pos")

    return {
        "lat": lat,
        "lon": lon,
        "nose": _first_num(raw, ("true_heading", "mag_heading", "track", "dir")),
        "trk": trk,
        "gs": gs,
        "ve": gs_km_s * math.sin(trk_rad),
        "vn": gs_km_s * math.cos(trk_rad),
        "age": age if age is not None else 0.0,
        "dst": dst if dst is not None else -1.0,
        "cs": _text(raw, "flight", CS_MAX) or _text(raw, "hex", CS_MAX),
        "ty": _text(raw, "t", TY_MAX),
        "alt": _altitude_tag(raw),
    }
