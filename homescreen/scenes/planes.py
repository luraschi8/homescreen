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

import html
import math
from datetime import datetime

from homescreen.cache import read_cache
from homescreen.config import feed_cache_path
from homescreen.scenes import Scene, SceneContext
from homescreen.scenes._style import page

CSS = """
.wrap{padding:18px;display:flex;flex-direction:column;height:100%}
table{width:100%;border-collapse:collapse;margin-top:8px}
td{padding:3px 0;font-size:13px}
td.r{text-align:right}
tr+tr td{border-top:1px dotted #000}
.foot{margin-top:auto}
"""


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
    env = read_cache(feed_cache_path(ctx.cache_dir, ctx.device))
    if env is None:
        return [], {"ok": False, "age_s": None}
    dwell = _dwell(env, ctx.now)
    raw = env.get("data", {}).get("aircraft")
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
        items.append({**a, "age": age + dwell})
    return items, {"ok": bool(env.get("ok")), "age_s": round(dwell, 1)}


def _dwell(env: dict, now: float) -> float:
    """Seconds this record has sat in our cache. Never negative: the Pi has no
    RTC and boots at the time timesyncd last saved, so a stamp written after a
    later NTP jump sits in the future."""
    try:
        fetched = datetime.fromisoformat(str(env.get("fetched_at")))
    except (TypeError, ValueError):
        return 0.0
    if fetched.tzinfo is None:
        return 0.0
    return max(0.0, now - fetched.timestamp())


def build(ctx: SceneContext) -> Scene:
    items, feed = _aircraft(ctx)
    # Two caps, and the smaller wins: the operator says how many are USEFUL,
    # the device says how many it can HOLD. Ignoring the device's number sends
    # a body it cannot parse -- and a parse failure reads as "no server", so
    # the panel blanks rather than showing fewer aeroplanes.
    cap = int(ctx.device.get("max_aircraft") or 20)
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
    return Scene(layout="fill", components=components, html=page(w, h, body, CSS))
