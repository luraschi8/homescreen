"""Where a component goes on a screen.

A screen shows a VIEW: an ordered list of PLACEMENTS, each one component in one
region with its own options. A 240x240 round panel is the degenerate case all
the way down -- one region, one placement, one view -- and nothing about it is
special-cased. That is the whole reason for shaping the record this way now
rather than migrating it later: the small screen and the composed e-paper
dashboard are the same structure with different numbers.

A region is a rectangle with a capacity. Components never learn that regions
exist; a placement's region is resolved to a rect and handed over as the
geometry, exactly as a component already receives 240x240 or 800x480 today.
"""

from __future__ import annotations

#: The glass we know about, and how SPEC §9 divides it.
#:
#: Capacities are hard caps with a stated reason, not taste. `markets` holds 6
#: because the band is 764px and a seventh entry truncates symbols -- the
#: dashboard refuses it with a notice rather than rendering a row that lies.
SURFACES = {
    "round_240": {
        "match": {"w": 240, "h": 240},
        "regions": {
            "full": {"rect": (0, 0, 240, 240), "holds": 1, "stack": None},
        },
    },
    "epaper_800x480": {
        "match": {"w": 800, "h": 480, "depth": 1},
        "regions": {
            "masthead":   {"rect": (0, 0, 800, 53),     "holds": 1, "stack": None},
            "main_left":  {"rect": (18, 63, 417, 335),  "holds": 4, "stack": "v"},
            "main_right": {"rect": (461, 63, 321, 335), "holds": 3, "stack": "v"},
            "markets":    {"rect": (18, 406, 764, 62),  "holds": 6, "stack": "h"},
        },
    },
}

#: What any unrecognised panel gets: one region covering it.
#:
#: A new screen size must not need a table edit to work at all. It gets the
#: degenerate surface -- one component, full bleed -- which is exactly what
#: every device does today.
FALLBACK_SURFACE = "single"

MAX_PLACEMENTS = 16
MAX_VIEWS = 32


def surface_name(caps) -> str:
    """Which surface this device's glass is."""
    caps = caps if isinstance(caps, dict) else {}
    for name, surface in SURFACES.items():
        if all(caps.get(k) == v for k, v in surface["match"].items()):
            return name
    return FALLBACK_SURFACE


def regions(caps) -> dict:
    """{region_name: {rect, holds, stack}} for this device."""
    name = surface_name(caps)
    if name in SURFACES:
        return dict(SURFACES[name]["regions"])
    caps = caps if isinstance(caps, dict) else {}
    w = int(caps.get("w") or 240)
    h = int(caps.get("h") or 240)
    return {"full": {"rect": (0, 0, w, h), "holds": 1, "stack": None}}


def region_caps(caps, region: str) -> dict:
    """The capabilities a component is handed for one placement.

    Its geometry is the REGION's, not the panel's -- that is the only thing a
    component needs to know, and it already knows how to use it. Depth and the
    device's item ceiling carry through unchanged; they are properties of the
    hardware, not of the rectangle.
    """
    base = dict(caps) if isinstance(caps, dict) else {}
    rect = regions(base).get(region, {}).get("rect")
    if rect is None:
        return base
    _, _, w, h = rect
    return {**base, "w": w, "h": h}


def clean_placement(raw, caps, known_components) -> dict | None:
    """One placement, or None if it is not usable. Never raises."""
    if not isinstance(raw, dict):
        return None
    component = raw.get("component")
    if component not in set(known_components or ()):
        return None
    region = raw.get("region")
    available = regions(caps)
    if region not in available:
        # Not silently relocated: a placement in a region this glass does not
        # have is a statement about a different screen, and moving it would
        # invent a layout nobody chose.
        return None
    options = raw.get("options")
    return {"id": str(raw.get("id") or f"{region}-{component}"),
            "region": str(region), "component": str(component),
            "options": options if isinstance(options, dict) else {}}


def clean_view(raw, caps, known_components) -> dict:
    """One view: its placements, in order, within every region's capacity."""
    placements, used = [], {}
    for item in (raw or {}).get("placements", [])[:MAX_PLACEMENTS]:
        placement = clean_placement(item, caps, known_components)
        if placement is None:
            continue
        region = placement["region"]
        holds = regions(caps)[region]["holds"]
        if used.get(region, 0) >= holds:
            continue                     # the cap is the cap
        used[region] = used.get(region, 0) + 1
        placements.append(placement)
    return {"placements": placements}


def single(component: str, options=None, region: str = "full") -> dict:
    """The one-component view every screen has today."""
    return {"placements": [{"id": f"{region}-{component}", "region": region,
                            "component": str(component),
                            "options": dict(options or {})}]}


def view_for(rec: dict, name: str | None = None) -> dict:
    """The view a record is showing, whatever shape the record is in.

    Records predate views: they carry `scene` and `options`. Rather than
    migrating every file on deploy -- a write across the whole registry to
    change nothing observable -- a legacy record is READ as the view it always
    meant. It grows the new shape the next time someone edits it.
    """
    rec = rec if isinstance(rec, dict) else {}
    views = rec.get("views")
    if isinstance(views, dict):
        # Only usable views count. A view stored as null -- reachable through a
        # hand-edited file -- was handed straight back, so the caller got None
        # where it had asked for a mapping.
        usable = {k: v for k, v in views.items() if isinstance(v, dict)}
        if usable:
            if name and name in usable:
                return usable[name]
            schedule = rec.get("schedule")
            default = schedule.get("default") if isinstance(schedule, dict) else None
            if default in usable:
                return usable[default]
            return usable[sorted(usable)[0]]
    scene = rec.get("scene") or "unassigned"
    return single(scene, rec.get("options"))


def view_names(rec: dict) -> tuple[str, ...]:
    rec = rec if isinstance(rec, dict) else {}
    views = rec.get("views")
    if isinstance(views, dict):
        usable = tuple(sorted(k for k, v in views.items() if isinstance(v, dict)))
        if usable:
            return usable
    return (rec.get("scene") or "unassigned",)
