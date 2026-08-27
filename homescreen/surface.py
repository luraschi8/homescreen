"""What a piece of glass is, described rather than enumerated.

The fleet is not two panels. It is however many screens someone buys, in sizes
and shapes nobody has picked yet, and a new one must show something without a
code change. So nothing here matches on a known resolution: a screen is a set
of measurements, and everything downstream asks questions about those
measurements instead of asking which panel this is.

The 240x240 round display and the 7.5" e-paper are two points in that domain,
not two cases in a switch.
"""

from __future__ import annotations

#: Shapes we can reason about. A round panel loses its corners, so anything
#: laid out to the edges is wrong there; that is the only distinction the
#: layout engine needs, and adding a shape means adding a value here plus the
#: templates that declare they fit it.
SHAPES = ("rect", "round")

#: Below this, a region cannot hold legible text: CLAUDE.md puts the floor at
#: 10px and draw.py will not render smaller, so a band under this is a band
#: that can only lie about what it contains.
MIN_REGION_PX = 24


def describe(caps) -> dict:
    """A screen, from whatever a device declared. Never raises.

    `shape` is declared by the device, defaulting to rect: a panel that says
    nothing gets the assumption that costs least, since a rectangular layout on
    round glass loses its corners while a full-bleed layout is right on both.
    """
    caps = caps if isinstance(caps, dict) else {}
    try:
        w = int(caps.get("w") or 0)
        h = int(caps.get("h") or 0)
    except (TypeError, ValueError):
        w = h = 0
    w, h = max(w, 0), max(h, 0)
    shape = str(caps.get("shape") or "rect").lower()
    if shape not in SHAPES:
        shape = "rect"
    try:
        depth = int(caps.get("depth") or 16)
    except (TypeError, ValueError):
        depth = 16
    return {
        "w": w, "h": h, "depth": depth, "shape": shape,
        "short": min(w, h), "long": max(w, h),
        # Guard the division rather than the caller: a zero-height record is
        # reachable through a hand-edited file and this runs on the serve path.
        "aspect": (w / h) if h else 0.0,
        # 1-bit glass is not merely monochrome -- its refresh costs seconds,
        # which is why a component may legitimately render differently there.
        "monochrome": depth <= 1,
    }


def fits(screen: dict, *, shape=None, min_short=0, min_w=0, min_h=0,
         max_aspect=None, min_aspect=None) -> bool:
    """Does this screen satisfy a set of requirements? Never raises."""
    screen = screen if isinstance(screen, dict) else describe(None)
    if shape is not None and screen.get("shape") != shape:
        return False
    if screen.get("short", 0) < min_short:
        return False
    if screen.get("w", 0) < min_w or screen.get("h", 0) < min_h:
        return False
    aspect = screen.get("aspect", 0.0)
    if max_aspect is not None and aspect > max_aspect:
        return False
    if min_aspect is not None and aspect < min_aspect:
        return False
    return True


def describes_same_glass(a, b) -> bool:
    """Whether two capability sets are the same screen for layout purposes."""
    left, right = describe(a), describe(b)
    return all(left[k] == right[k] for k in ("w", "h", "shape", "depth"))
