"""Where components go on a screen, at whatever size the screen is.

A view is an ordered list of PLACEMENTS -- one component, in one region, with
its own options. A region comes from a TEMPLATE: a way of dividing glass,
expressed in fractions so it resolves against any geometry.

The earlier version of this file listed two panels and their pixel rectangles.
That is the wrong shape for a fleet that can grow a screen nobody has bought
yet: it makes "support a new size" a code edit, and it makes the small screen
look like an exception when it is just a screen where only one template fits.

So templates declare what they REQUIRE of the glass, and every template that
fits is offered. A 240x240 round panel fits `single`. A 7.5" e-paper fits
`single`, `split` and `dashboard`. A 1024x600 nobody has tried fits the same
three, with no edit here, because the question asked of it is about its
measurements and not its name.
"""

from __future__ import annotations

from homescreen import surface

MAX_PLACEMENTS = 16
MAX_VIEWS = 32

#: Ways of dividing a screen.
#:
#: Rects are FRACTIONS of the screen: (x, y, w, h) in 0..1. `holds` is a hard
#: cap with a reason, and `requires` is what the glass must satisfy for the
#: template to be offered at all.
#:
#: `dashboard`'s proportions are SPEC §9's measured 800x480 layout expressed as
#: fractions -- masthead 53/480, margin 18/800 -- so the design that was drawn
#: for that panel is preserved there and still resolves on a larger one.
TEMPLATES = {
    "single": {
        "label": "una sola cosa",
        "requires": {},                  # anything with pixels
        "regions": {
            "full": {"rect": (0.0, 0.0, 1.0, 1.0), "holds": 1, "stack": None},
        },
    },
    "split": {
        "label": "dos mitades",
        # Round glass loses its corners, and a horizontal seam across a circle
        # leaves two lens shapes that nothing lays out well.
        "requires": {"shape": "rect", "min_short": 200},
        "regions": {
            "top":    {"rect": (0.0, 0.0, 1.0, 0.5), "holds": 3, "stack": "v"},
            "bottom": {"rect": (0.0, 0.5, 1.0, 0.5), "holds": 3, "stack": "v"},
        },
    },
    "dashboard": {
        "label": "panel compuesto",
        # Four regions on glass under this size gives bands too short for
        # legible text; the smallest is the masthead at 11% of height.
        "requires": {"shape": "rect", "min_w": 600, "min_h": 380,
                     "min_aspect": 1.2},
        "regions": {
            "masthead":   {"rect": (0.0,    0.0,   1.0,   0.110),
                           "holds": 1, "stack": None},
            # Five, because the original design stacks clock, sun times,
            # agenda, deliveries and sport here.
            "main_left":  {"rect": (0.0225, 0.131, 0.521, 0.698),
                           "holds": 5, "stack": "v"},
            "main_right": {"rect": (0.576,  0.131, 0.401, 0.698),
                           "holds": 3, "stack": "v"},
            # SPEC SS9: an FX box at flex 1.55 and five tickers at 1 each,
            # across an inner width of 764. Five tickers is the hard ceiling
            # there -- a sixth starts truncating symbols.
            "markets":    {"rect": (0.0225, 0.846, 0.955, 0.129),
                           "holds": 6, "stack": "h",
                           "weights": (1.55, 1, 1, 1, 1, 1)},
        },
    },
}

DEFAULT_TEMPLATE = "single"

#: A slot may ask for more of its region than its neighbours, but not for a
#: multiple so large that everything else rounds to nothing.
MAX_WEIGHT = 20.0


def _resolve(rect, screen) -> tuple[int, int, int, int]:
    fx, fy, fw, fh = rect
    w, h = screen.get("w", 0), screen.get("h", 0)
    return (round(fx * w), round(fy * h), round(fw * w), round(fh * h))


def templates_for(caps) -> tuple[str, ...]:
    """Every template this glass can carry, smallest first.

    `single` always qualifies: whatever the screen is, showing one thing on all
    of it is possible, so no device can end up with nothing to choose.
    """
    screen = surface.describe(caps)
    out = []
    for name, template in TEMPLATES.items():
        if not surface.fits(screen, **template["requires"]):
            continue
        # A template whose smallest region cannot hold legible text is not
        # offered, however well it fits on paper.
        rects = [_resolve(r["rect"], screen) for r in template["regions"].values()]
        if any(min(w, h) < surface.MIN_REGION_PX for _, _, w, h in rects):
            continue
        out.append(name)
    return tuple(out) or (DEFAULT_TEMPLATE,)


def template_of(view) -> str:
    name = (view or {}).get("template")
    return name if name in TEMPLATES else DEFAULT_TEMPLATE


def regions(caps, template: str = DEFAULT_TEMPLATE) -> dict:
    """{region: {rect, holds, stack}} in PIXELS for this glass."""
    screen = surface.describe(caps)
    spec = TEMPLATES.get(template) or TEMPLATES[DEFAULT_TEMPLATE]
    return {name: {**region, "rect": _resolve(region["rect"], screen)}
            for name, region in spec["regions"].items()}


def slots(region: dict, count: int, weights=None) -> list:
    """`count` sub-rects tiling a region, laid out along its stack axis.

    `holds` and `stack` have described every region since the templates were
    written; this is the arithmetic that finally reads them. Slots tile the
    region exactly -- leftover pixels go to the leading slots rather than
    leaving a seam of background between two components.

    `count` is how many placements the region actually carries, not `holds`: a
    region that can hold four and carries two splits in half. A region with no
    stack axis still divides if it is somehow asked to carry more than one,
    because overlapping is the one answer that is never what was meant.

    `weights` from the VIEW override the template's. The template fixes the
    markets band because SPEC SS9 does; a column is different, because what goes
    in it is the operator's choice and so how the height is shared has to be
    theirs. The original design's left column is five blocks of visibly
    different heights and equal fifths cannot express it.
    """
    x, y, w, h = region["rect"]
    count = max(1, int(count))
    if count == 1:
        return [(x, y, w, h)]
    horizontal = region.get("stack") == "h"
    span = w if horizontal else h
    sizes = _shares(span, count, weights, region.get("weights"))
    out, offset = [], 0
    for size in sizes:
        out.append((x + offset, y, size, h) if horizontal
                   else (x, y + offset, w, size))
        offset += size
    return out


def _weights(asked, fallback, count: int) -> list:
    """`count` positive finite weights. Never raises, never collapses a region.

    Per SLOT, not per list: a view that sets a share on its first block and
    leaves the rest blank must not thereby discard the template's own
    proportions for the others. Passing a list of blanks used to do exactly
    that, and it flattened SPEC SS9's markets band to six equal cells.

    A weight arrives from a stored file or a form field, so every kind of
    nonsense reaches here -- a string, a zero, a negative, a phone number.
    Anything unusable falls through to the template's, then to 1: an even
    share is always legible, and a region of zero-height slots is not.
    """
    def _at(source, index):
        if source is None:
            return None
        try:
            return list(source)[index]
        except (IndexError, TypeError):
            return None

    out = []
    for index in range(count):
        for candidate in (_at(asked, index), _at(fallback, index), 1.0):
            try:
                number = float(candidate)
            except (TypeError, ValueError):
                continue
            if number == number and 0 < number <= MAX_WEIGHT:
                out.append(number)
                break
        else:
            out.append(1.0)
    return out


def _shares(span: int, count: int, weights=None, fallback=None) -> list:
    """`count` whole-pixel shares of `span`, tiling it exactly.

    Weights are optional and proportional: SPEC SS9's markets band is an FX box
    at flex 1.55 and five tickers at 1 each, which equal division cannot
    express. The LEADING weights are used when a region carries fewer
    placements than it can hold, so two tickers in a six-cell band still put
    the wide box first rather than falling back to halves.

    The remainder is distributed by largest fractional part -- the same rule a
    seat apportionment uses -- so the widest cell does not systematically lose
    the rounding to the narrowest.
    """
    share = _weights(weights, fallback, count)
    total = sum(share) or float(count)
    exact = [span * part / total for part in share]
    sizes = [int(value) for value in exact]
    short = span - sum(sizes)
    # Whoever was cut by the most gets the spare pixels first.
    order = sorted(range(count), key=lambda i: exact[i] - sizes[i],
                   reverse=True)
    for i in order[:short]:
        sizes[i] += 1
    return sizes


def clean_placement(raw, caps, known_components, template=DEFAULT_TEMPLATE):
    """One placement, or None if unusable. Never raises."""
    if not isinstance(raw, dict):
        return None
    component = raw.get("component")
    if component not in set(known_components or ()):
        return None
    region = raw.get("region")
    if region not in regions(caps, template):
        # Not relocated. A placement naming a region this template does not
        # have is a statement about a different layout, and moving it would
        # invent an arrangement nobody chose.
        return None
    options = raw.get("options")
    return {"id": str(raw.get("id") or f"{region}-{component}"),
            "region": str(region), "component": str(component),
            # How much of its region this block asks for, against its
            # neighbours. Absent means an even share, which is what every
            # stored view written before this said by saying nothing.
            "weight": _weight_of(raw.get("weight")),
            # The section heading, as the original design has over AGENDA and
            # ENTREGAS. A property of the PLACEMENT rather than the component:
            # the same calendar is "agenda" in one column and "cumpleanos" in
            # another, and only the person arranging them knows which.
            "label": str(raw.get("label") or "")[:24],
            "options": options if isinstance(options, dict) else {}}


def _weight_of(raw):
    """The share this block asked for, or None if it did not ask.

    None, NOT 1.0. An explicit 1.0 is a decision -- "even, please" -- and it
    shadows the template's own proportions, which is how SPEC SS9's markets band
    came to render as six equal cells in every view an operator could make
    while `_weights`'s fallback chain sat there working perfectly and unreached.

    An unusable value is also no decision: "abc" is not a considered request
    for an even share, and the template's proportions are the better default.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not (value == value) or value <= 0 or value > MAX_WEIGHT:
        return None
    return round(value, 2)


def clean_view(raw, caps, known_components) -> dict:
    """One view: a template plus its placements, within every capacity."""
    raw = raw if isinstance(raw, dict) else {}
    template = template_of(raw)
    if template not in templates_for(caps):
        template = DEFAULT_TEMPLATE
    available = regions(caps, template)
    placements, used = [], {}
    for item in (raw.get("placements") or [])[:MAX_PLACEMENTS]:
        placement = clean_placement(item, caps, known_components, template)
        if placement is None:
            continue
        region = placement["region"]
        if used.get(region, 0) >= available[region]["holds"]:
            continue
        used[region] = used.get(region, 0) + 1
        placements.append(placement)
    return {"template": template, "placements": placements}


def single(component: str, options=None, region: str = "full") -> dict:
    """The one-component view every screen can show."""
    return {"template": DEFAULT_TEMPLATE,
            "placements": [{"id": f"{region}-{component}", "region": region,
                            "component": str(component),
                            "options": dict(options or {})}]}


def view_for(rec: dict, name: str | None = None) -> dict:
    """The view a record is showing, whatever shape the record is in."""
    rec = rec if isinstance(rec, dict) else {}
    views = rec.get("views")
    if isinstance(views, dict):
        # A view with no placements can EXIST -- the builder creates one so
        # its slots appear on the page to be filled -- but it can never be the
        # one that renders. A screen showing an empty view shows nothing while
        # looking configured, which is the failure this guard exists for.
        usable = {k: v for k, v in views.items()
                  if isinstance(v, dict) and (v.get("placements") or [])}
        if usable:
            if name and name in usable:
                return usable[name]
            schedule = rec.get("schedule")
            default = schedule.get("default") if isinstance(schedule, dict) else None
            if default in usable:
                return usable[default]
            return usable[sorted(usable)[0]]
    # Records predate views: they carry `scene` and `options`, and are read as
    # the view they always meant rather than rewritten on deploy.
    return single(rec.get("scene") or "unassigned", rec.get("options"))


def view_names(rec: dict) -> tuple[str, ...]:
    rec = rec if isinstance(rec, dict) else {}
    views = rec.get("views")
    if isinstance(views, dict):
        usable = tuple(sorted(k for k, v in views.items() if isinstance(v, dict)))
        if usable:
            return usable
    return (rec.get("scene") or "unassigned",)
