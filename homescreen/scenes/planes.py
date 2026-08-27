"""Planes scene: live aircraft, in both delivery shapes.

This is the one scene that must serve both device classes, so it is where the
two-delivery design either works or does not.

  data push (round display)  -> components; the device dead-reckons between
                                polls using ve/vn, which is why this device
                                is not sent pixels
  pixel push (e-paper)       -> a rendered list; a 1-bit panel has no useful
                                way to draw a moving radar

NOTE ON THE COMPONENT SHAPE. The design spec claimed the radar decomposes into
generic `rings` + `markers`. Reading radar_display.cpp, that is false: the
firmware draws eleven distinct elements, gives each marker TWO angles (nose vs
track), sizes six of them from measured text metrics, and places labels with a
six-slot collision ladder that drops a label when no slot is free. None of that
survives a generic vocabulary. So `radar` is emitted as ONE coarse component
carrying the aircraft list -- the device already knows how to draw it, and
pretending otherwise would mean rebuilding a working renderer badly.
"""

from __future__ import annotations

import logging

import html
import math
from datetime import datetime

from homescreen.cache import read_cache
from homescreen.config import feed_cache_path
from homescreen.scenes import Scene, SceneContext
from homescreen.scenes._style import page

#: A radar draws range rings, a heading vector and collision-laddered labels
#: around a centre. Under this it is a smear: the innermost ring and the
#: aircraft symbol overlap, and every label collides with every other.
SURFACES = ({"min_short": 160},)

#: Per ASSIGNMENT, with the deployment's values as the defaults.
#:
#: These lived only in config.yaml and the runtime override file, which made
#: them one setting for the whole house: two radars could not watch different
#: ranges, and changing either meant editing the Pi. Blank means "use the
#: deployment default", so a screen that has never been configured behaves
#: exactly as it did.
#:
#: `endpoint` and `fetch_seconds` are declared here because they belong to the
#: component, but they are not yet honoured per assignment: one fetch daemon
#: serves every radar, so making two screens read different upstreams needs the
#: job registry. Until that lands the dashboard says so rather than offering a
#: field that silently does nothing.
OPTIONS = (
    {"key": "radius_km", "label": "Radio (km)", "type": "int", "default": 0,
     "help": "0 usa el valor del servidor. Escala los anillos del radar."},
    {"key": "max_aircraft", "label": "Aviones como máximo", "type": "int",
     "default": 0,
     "help": "0 usa el valor del servidor. La pantalla puede imponer menos."},
    # Two radars pointed at different cities is the case this platform exists
    # for. Without these, every radar in the fleet collapses onto the
    # deployment's own coordinates and they all share one fetch.
    {"key": "lat", "label": "Latitud", "type": "text", "default": "",
     "help": "En blanco usa la ubicación del servidor."},
    {"key": "lon", "label": "Longitud", "type": "text", "default": ""},
)

CSS = """
.wrap{padding:18px;display:flex;flex-direction:column;height:100%}
table{width:100%;border-collapse:collapse;margin-top:8px}
td{padding:3px 0;font-size:13px}
td.r{text-align:right}
tr+tr td{border-top:1px dotted #000}
.foot{margin-top:auto}
"""


#: Reported when the cache stamp cannot be read. Large enough that every
#: consumer treats the feed as dead, because a feed whose age is unknowable is
#: not a feed you should draw.
log = logging.getLogger(__name__)

_UNKNOWN_DWELL = 86400.0


def _aircraft(ctx: SceneContext) -> tuple[list, dict]:
    """The cached list plus feed state. Never raises: a scene that throws
    reaches the fallback, and a blank screen is worse than a stale one.

    Ages are recomputed HERE, at serve time, exactly as `/api/display/<id>/data`
    does -- VALIDATION F4. `age` on disk is how old the fix was when we fetched
    it; by the time a device reads it, the record has also sat in our cache for
    `dwell` seconds, and the device dead-reckons from `age`. Passing it through
    untouched made a 20s-old record still claim 3.1s: the device extrapolates
    from a position five kilometres behind the aeroplane at 250 m/s, and its
    12s dimming test never fires because the number it tests never grows.

    This was correct on `/data` and wrong here, because this scene reads the
    cache file directly rather than going through `serve._servable`. Same trap,
    second door.
    """
    wanted = needs(ctx.options or {}, ctx.cfg)
    reading = ctx.data(wanted[0]) if wanted else None
    if reading is None or reading.missing:
        return [], {"ok": False, "age_s": None}
    # Dwell is how long this sky has sat in OUR cache. The device dead-reckons
    # from `age`, so passing the on-disk value through untouched made a 20s-old
    # record still claim 3.1s -- the device extrapolates from a position five
    # kilometres behind the aeroplane and its 12s dimming test never fires,
    # because the number it tests never grows. The Reading carries the age, so
    # this is now one subtraction rather than a second parse of a timestamp.
    dwell = reading.age_s if reading.age_s is not None else _UNKNOWN_DWELL
    raw = (reading.data or {}).get("aircraft")
    items = []
    for a in raw if isinstance(raw, list) else []:
        if not isinstance(a, dict):
            continue
        try:
            age = float(a.get("age", 0.0))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(age):
            continue
        items.append(_wire(a, age + dwell))
    return items, {"ok": bool(reading.ok), "age_s": round(dwell, 1)}


#: Decimal places for each wire field. The device parses into float32, so
#: anything past ~7 significant digits is noise -- and `"ve":
#: -0.13970290959420342` is 21 bytes of it, per aircraft, per poll. Rounding
#: roughly halves the body, which is the difference between a scene the device
#: can parse and one it refuses.
_WIRE_ROUNDING = {"lat": 5, "lon": 5, "ve": 5, "vn": 5, "age": 1,
                  "dst": 2, "gs": 1, "trk": 1, "nose": 1}


def _wire(a: dict, age: float) -> dict:
    out = {**a, "age": age}
    for key, places in _WIRE_ROUNDING.items():
        value = out.get(key)
        if isinstance(value, float):
            out[key] = round(value, places)
    return out


def _dwell(env: dict, now: float) -> float:
    """Seconds this record has sat in our cache. Never negative: the Pi has no
    RTC and boots at the time timesyncd last saved, so a stamp written after a
    later NTP jump sits in the future."""
    try:
        fetched = datetime.fromisoformat(str(env.get("fetched_at")))
    except (TypeError, ValueError):
        return _UNKNOWN_DWELL
    if fetched.tzinfo is None:
        # Naive means we cannot know the offset, so we cannot know the age.
        # Returning 0.0 here said "brand new", which pins feed_age_s at zero
        # forever and makes the device's staleness test permanently blind --
        # the one number it has for "the server's feed died" would never move.
        return _UNKNOWN_DWELL
    return max(0.0, now - fetched.timestamp())


def needs(options: dict, cfg: dict) -> tuple:
    """One ADS-B fetch, centred where this deployment is, at this screen's
    radius.

    The endpoint is RESOLVED here rather than left blank: a job must be
    self-describing, so two screens reading the same upstream share one fetch
    and changing the upstream visibly becomes a different job.
    """
    from homescreen.config import feed_config, home_location
    options = options or {}
    try:
        lat, lon = float(options["lat"]), float(options["lon"])
    except (KeyError, TypeError, ValueError):
        where = home_location(cfg or {})
        lat, lon = where.get("lat"), where.get("lon")
    if lat is None or lon is None:
        # Not "needs nothing" -- cannot say what it needs. Logged, because the
        # difference is invisible downstream and the symptom is a daemon that
        # looks idle while the panel goes empty.
        log.warning("radar has no location: neither the assignment nor the "
                    "config says where to look")
        return ()
    radius = _positive(options.get("radius_km")) or 60.0
    return ({"provider": "adsb",
             "params": {"lat": lat, "lon": lon, "radius_km": radius,
                        "endpoint": feed_config(cfg).get("endpoint") or ""}},)


def _positive(value):
    """A usable number, or None. Zero and nonsense both mean "not set" here,
    which is what lets one field carry both a value and "use the default"."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def build(ctx: SceneContext) -> Scene:
    items, feed = _aircraft(ctx)
    # Two caps, and the smaller wins: the operator says how many are USEFUL,
    # the device says how many it can HOLD. Ignoring the device's number sends
    # a body it cannot parse -- and a parse failure reads as "no server", so
    # the panel blanks rather than showing fewer aeroplanes.
    # Assignment first, then the deployment's value, then the floor. Blank
    # means "whatever the server was already doing", so a screen nobody has
    # configured is unchanged.
    opts = ctx.options or {}
    cap = int(_positive(opts.get("max_aircraft"))
              or ctx.device.get("max_aircraft") or 20)
    declared = ctx.device.get("max_items")
    if declared:
        cap = min(cap, int(declared))
    items = items[:cap]

    # Data push: one coarse component. See the module note.
    #
    # `radius_km` rides along because the device cannot derive it: it scales
    # the rings and decides what "off the edge" means, and without it a device
    # showing a 30 km feed on a 60 km dial is wrong in a way nothing detects.
    # Item field names are the FEED's (`dst`, `trk`, `nose`), not spec §5.3's
    # (`dist`, `rot`, `brg`): the running firmware already parses these, and
    # `brg` does not exist server-side at all -- the device computes bearing
    # from lat/lon itself so that changing the range preset stays a local,
    # instant action (ADDENDUM §2). Renaming them for a firmware that does not
    # exist yet would break the one that does. Pinned by test_scenes.py.
    radius_km = _positive(opts.get("radius_km"))
    if not radius_km:
        try:
            radius_km = float(ctx.device.get("radius_km") or 60.0)
        except (TypeError, ValueError):
            radius_km = 60.0
    # `feed_age_s` is the THIRD staleness cause, and it must stay separate from
    # the per-aircraft age. PLAN.md §3: radar_display.cpp tests its two causes
    # separately and deliberately -- summing them made targets blink once per
    # cycle. A device can be receiving fresh scenes (contact clock healthy)
    # while the feed behind them died, and only this number says so.
    components = ({"c": "radar", "items": items, "feed_ok": feed["ok"],
                   "feed_age_s": feed["age_s"], "radius_km": radius_km},)

    # Pixel push: the same data as a legible list.
    w = int(ctx.caps.get("w") or 800)
    h = int(ctx.caps.get("h") or 480)
    # Escaped: callsign, type and altitude come from a third-party feed, so
    # without this the panel's markup is partly controlled by adsb.fi.
    e = html.escape

    def cell(value, fallback=""):
        return e(str(value)) if value else fallback

    def nm(a):
        try:
            return f"{float(a.get('dst', 0)):.1f} NM"
        except (TypeError, ValueError):
            return ""

    rows = "".join(
        f'<tr><td>{cell(a.get("cs"), "-")}</td>'
        f'<td>{cell(a.get("ty"))}</td>'
        f'<td class="r">{cell(a.get("alt"))}</td>'
        f'<td class="r">{nm(a)}</td></tr>'
        for a in items[: max(1, (h - 90) // 20)])
    stamp = datetime.fromtimestamp(ctx.now).strftime("%H:%M")
    state = "" if feed["ok"] else ' <span class="pill">sin señal</span>'
    body = (f'<div class="wrap">'
            f'<div class="lab">Tráfico aéreo · {len(items)} aeronaves{state}</div>'
            f'<div class="rule" style="margin-top:6px"></div>'
            f'<table>{rows or "<tr><td class=sec>cielo despejado</td></tr>"}</table>'
            f'<div class="foot"><div class="rule"></div>'
            f'<div class="ter" style="margin-top:6px">{stamp}</div></div></div>')
    # Aircraft move continuously and the device dead-reckons between polls, so
    # this is how often the reckoning gets corrected rather than how often the
    # picture changes.
    return Scene(layout="fill", components=components, poll_s=5,
                 html=page(w, h, body, CSS))
