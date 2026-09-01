"""The instruction vocabulary a component ships, and how it resolves to pixels.

A component does not say "draw a clock". It says "put this text in this slot at
this size". Two things then execute that list:

  * this module, rasterising it to a PNG for the dashboard preview
  * the firmware, drawing it onto the panel

Neither invents layout, so they cannot disagree about what goes where. That is
the whole point: a preview drawn by a different program than the device is a
guess, and it drifts silently every time either side changes.

`radar` is the documented exception. It stays opaque -- the device projects,
dead-reckons between polls and runs a label-collision ladder per frame -- so its
preview is approximate. Everything else is exact.

The resolver is deliberately dull arithmetic. It is reimplemented in C++ in the
firmware, and `tests/test_draw_parity.py` pins both against one golden fixture,
so "dull" is a feature: every clever thing here is a thing to get wrong twice.
"""

from __future__ import annotations

import math

#: Vertical position of each slot, as a fraction of panel height.
#:
#: Fractions, not pixels, because the same component has to land sensibly on a
#: 240x240 round panel and an 800x480 rectangle. On a circle the usable width
#: narrows towards the top and bottom, which is why `rim_*` sit at 0.12/0.88
#: rather than hard against the edge.
SLOTS = {
    "rim_top": 0.12,
    "above": 0.34,
    "center": 0.50,
    "below": 0.66,
    "rim_bottom": 0.88,
}

#: Type size as a fraction of the panel's SHORT side, so a component keeps its
#: proportions on a square panel and does not overflow a narrow one.
SIZES = {"xl": 0.26, "lg": 0.17, "md": 0.11, "sm": 0.075, "xs": 0.055}

#: Tones are names, not colours. A component says what a value MEANS and the
#: device decides how to show it -- `bad` cannot be red on 1-bit glass, so
#: there it becomes an inverted block instead.
#:
#: The first four were the whole vocabulary, which made every panel white text
#: and grey labels on black. These are the meanings worth distinguishing on a
#: colour display, and no more: a palette is only useful while a glance can
#: tell two entries apart.
TONES = ("normal",   # the thing you came to read
         "dim",      # its label, its units, its footnote
         "good",     # up, healthy, live
         "bad",      # down, failing, expired
         "accent",   # the identity of the thing -- a city, a symbol, a team
         "warn",     # needs attention but is not wrong
         "cool",     # cold end of a scale
         "hot",      # hot end of a scale
         "off")      # the panel's own background: a blank screen

#: How many drawables one component may emit.
#:
#: Mirrors the firmware's kMaxPlacements. Truncating in the same place on both
#: sides is what keeps the preview honest: a preview showing a sun the panel
#: dropped is exactly the drift this design exists to prevent.
MAX_INSTRUCTIONS = 40

#: Smallest legible type on these panels. CLAUDE.md puts the floor at 10px for
#: the e-paper; the round display is denser but the same floor holds.
MIN_TEXT_PX = 10


def _round_half_up(value: float) -> int:
    """Round halves AWAY FROM ZERO, which is what C's roundf() does.

    Python's built-in round() is banker's rounding -- it breaks ties to even --
    so `round(120.5)` is 120 while `roundf(120.5f)` is 121. A 241px panel puts
    the centre slot exactly on that tie, and the two resolvers disagreed by one
    pixel. Caught by the parity fixture on its first run, which is the entire
    reason that fixture exists.

    Every fraction and extent here is positive, so this is exact.
    """
    return int(math.floor(value + 0.5))


def size_px(token: str, w: int, h: int) -> int:
    """Resolve a size token to pixels for this panel."""
    frac = SIZES.get(str(token), SIZES["md"])
    return max(MIN_TEXT_PX, _round_half_up(frac * min(int(w), int(h))))


def slot_y(slot: str, h: int) -> int:
    """Resolve a slot to a baseline-centre y for this panel."""
    frac = SLOTS.get(str(slot), SLOTS["center"])
    return _round_half_up(frac * int(h))


def resolve(draw: list, w: int, h: int) -> list:
    """Instruction list -> absolute placements, centred horizontally.

    Returns one dict per drawable with `x`, `y`, `px`, `text`, `tone`. Anything
    unrecognised is dropped rather than guessed at: a device that cannot draw an
    instruction and a preview that invents one are the same bug seen from two
    sides.
    """
    out = []
    for item in (draw or ())[:MAX_INSTRUCTIONS]:
        if not isinstance(item, dict):
            continue
        if item.get("t") == "fill":
            out.append({"t": "fill", "text": "", "x": 0, "y": 0, "px": 0,
                        "x2": 0, "y2": 0, "x3": 0, "y3": 0, "fill": True,
                        "tone": (item.get("tone")
                                 if item.get("tone") in TONES else "off")})
            continue
        if item.get("t") in ("circle", "line", "tri"):
            # Three points or it is not a triangle. Padding the missing corner
            # with a zero draws a wedge to the top-left of the glass, which is
            # worse than drawing nothing: the firmware drops it, so this must
            # too or the preview shows a mark the panel will not have.
            if item["t"] == "tri" and len(item.get("p") or ()) < 6:
                continue
            out.append(_shape_px(item, w, h))
            continue
        if item.get("t") != "text":
            continue
        text = item.get("v")
        if not isinstance(text, str) or not text:
            continue
        tone = item.get("tone", "normal")
        out.append({
            "t": "text",
            "x": int(w) // 2,
            "y": slot_y(item.get("slot", "center"), h),
            "px": size_px(item.get("size", "md"), w, h),
            "text": text,
            "tone": tone if tone in TONES else "normal",
        })
    return out


#: Rough width of one character as a fraction of type height, for the faces
#: these panels carry. An estimate on purpose: the exact answer needs the font,
#: which lives on the device. Being approximately right here is the difference
#: between a list and a smear, not between two correct layouts.
CHAR_WIDTH_RATIO = 0.58

#: A circle's usable width across the rows a list occupies. The rim slots sit
#: at 12% and 88% of the height, where the chord is well short of the diameter.
ROUND_USABLE = 0.72

#: Kept off the bezel. A chord measured to the very edge of the glass puts the
#: last pixel of a glyph under the rim, so both shapes hold a little back.
ROUND_INSET = 0.94
RECT_USABLE = 0.94

#: What a shortened line ends with. Three dots, not an ellipsis: the panel's
#: embedded face has 95 glyphs and no U+2026.
TRUNCATION = "..."

#: Vertical room one row needs, as a multiple of its type height, before rows
#: start touching.
ROW_PITCH = 2.0


def _slots_for(count: int) -> tuple:
    """The `count` slots a centred list occupies, middle outward."""
    order = sorted(SLOTS, key=lambda name: abs(SLOTS[name] - 0.5))
    return tuple(order[:max(1, min(int(count), len(order)))])


def lines_fit(lines, w: int, h: int, *, size: str = "sm",
              shape: str = "rect") -> bool:
    """Can these lines all be shown at once on this glass?

    Asked by every component that has a list and a small screen, so it is one
    rule rather than one per component. It measures the LONGEST LINE, because
    that is what actually decides: "BINANCE:BTCUSDT 63,120 ▲ 2.90%" needs three
    times the width of "AAPL 227.40", and a rule about panel size cannot see
    the difference -- which is how a round 240px panel came to stack three rows
    that each ran off both edges.
    """
    lines = [str(x) for x in (lines or ()) if str(x)]
    if not lines:
        return True
    if len(lines) > len(SLOTS):
        return False                     # more rows than the vocabulary has
    px = size_px(size, w, h)
    if h / len(lines) < px * ROW_PITCH:
        return False                     # rows would touch
    # The NARROWEST slot these rows will occupy, not a flat ratio. A list
    # spreads outward from the middle, so the more rows there are the closer
    # the outermost one sits to the rim and the less width it has. Measuring
    # the widest row against the centre's chord is how three rows came to be
    # accepted and then drawn running off both edges.
    usable = min(slot_width(slot, w, h, shape, px)
                 for slot in _slots_for(len(lines)))
    return max(len(line) for line in lines) * px * CHAR_WIDTH_RATIO <= usable


#: What the panel's embedded face can actually draw: 95 glyphs, 0x21-0xB0.
#: It has a degree sign and no accented letters, no arrows, no middle dot and
#: no em dash -- which is most of written Spanish and half the punctuation a
#: component reaches for.
#:
#: Substituted rather than dropped, because a missing glyph is a blank box or
#: nothing at all, and "maana" is at least a word you can read. The mapping is
#: deliberately boring; `tests/test_draw.py` pins every entry against the font
#: file itself, so a different embedded face fails the test rather than the
#: panel.
DEVICE_SUBSTITUTIONS = {
    "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ü": "u", "ñ": "n",
    "Á": "A", "É": "E", "Í": "I", "Ó": "O", "Ú": "U", "Ü": "U", "Ñ": "N",
    "¿": "", "¡": "", "—": "-", "–": "-",
    # A separator, not a dash. Mapping it to "-" put a minus sign immediately
    # before a number: "tokens - 30 dias" reads as arithmetic.
    "·": " ",
    "▲": "+", "▼": "-", "“": '"', "”": '"', "‘": "'", "’": "'",
    "€": "EUR", "£": "GBP", "…": "...",
}

#: Codepoints the face carries. Anything outside this, after substitution, is
#: dropped: a box on the glass is worse than a shorter word.
DEVICE_MIN_CP, DEVICE_MAX_CP = 0x20, 0xB0


def for_device(value) -> str:
    """Text an instruction list can carry, from text a person wrote.

    Applied inside `text()` so every component is safe by construction and
    none of them has to know what the panel's font holds. The SVG preview runs
    the same instruction list, so preview and glass still agree -- which is the
    property the whole two-executor design exists to protect.
    """
    out = []
    for char in str(value):
        char = DEVICE_SUBSTITUTIONS.get(char, char)
        for piece in char:
            if DEVICE_MIN_CP <= ord(piece) <= DEVICE_MAX_CP:
                out.append(piece)
    return "".join(out)


# --- shapes -----------------------------------------------------------------
#
# The wire carries PRIMITIVES, not icon names. A sun is three circles and eight
# lines by the time it leaves here, so adding a weather icon is a change to
# this file and to nothing else -- the same bargain `draw_list` struck for
# components. A firmware that can draw a circle, a line and a triangle can draw
# every icon we invent afterwards, including ones that did not exist when it
# was flashed.
#
# Coordinates are FRACTIONS of the panel, like slots and sizes, so a shape
# lands in the same place on any glass.


#: Fractions are rounded to this many places on the wire. Three is a third of
#: a pixel on an 800px panel and half a pixel on a 240px one -- below what
#: either resolver can draw, and it is 8 bytes a coordinate rather than 9.
_WIRE_DP = 3


def _defaults_off(item: dict, tone: str) -> dict:
    """Drop what the device would assume anyway.

    Every byte here is multiplied by the instruction count and measured against
    a fixed device buffer: a sun is nine shapes, and `"tone":"normal"` on each
    of them is 96 bytes of saying nothing. Both resolvers already default a
    missing tone to `normal` and a missing `fill` to true, so silence and the
    explicit value mean the same thing -- which is what makes this safe rather
    than clever.
    """
    if tone != "normal":
        item["tone"] = tone
    return item


def fill(tone: str = "off") -> dict:
    """Paint the whole panel one colour.

    The instruction a blank screen is made of. `off` is the panel's own
    background, which on the round display is black -- as close to "not on" as
    a backlit LCD gets without a backlight pin we do not have.
    """
    return {"t": "fill", "tone": tone}


def circle(cx: float, cy: float, r: float, tone: str = "normal",
           fill: bool = True) -> dict:
    out = {"t": "circle", "cx": round(cx, _WIRE_DP), "cy": round(cy, _WIRE_DP),
           "r": round(r, _WIRE_DP)}
    if not fill:
        out["fill"] = False
    return _defaults_off(out, tone)


def line(x1: float, y1: float, x2: float, y2: float, tone: str = "normal",
         w: float = 0.012) -> dict:
    return _defaults_off(
        {"t": "line", "x1": round(x1, _WIRE_DP), "y1": round(y1, _WIRE_DP),
         "x2": round(x2, _WIRE_DP), "y2": round(y2, _WIRE_DP),
         "w": round(w, _WIRE_DP)}, tone)


def tri(points, tone: str = "normal") -> dict:
    """A filled triangle from three (x, y) fractions."""
    flat = [round(v, _WIRE_DP) for point in points for v in point]
    return _defaults_off({"t": "tri", "p": flat}, tone)


#: Icons, as functions of (cx, cy, size) in panel fractions.
#:
#: Deliberately drawn rather than fonted: a glyph needs a face that has it, and
#: the panel's face has 95 characters. A circle is a circle on any hardware.
def _sun(cx, cy, s, tone):
    out = [circle(cx, cy, s * 0.30, tone)]
    for i in range(8):
        import math
        a = math.pi * i / 4.0
        out.append(line(cx + math.cos(a) * s * 0.42,
                        cy + math.sin(a) * s * 0.42,
                        cx + math.cos(a) * s * 0.52,
                        cy + math.sin(a) * s * 0.52, tone, s * 0.07))
    return out


def _cloud(cx, cy, s, tone):
    return [circle(cx - s * 0.22, cy + s * 0.06, s * 0.20, tone),
            circle(cx + s * 0.20, cy + s * 0.08, s * 0.17, tone),
            circle(cx - s * 0.01, cy - s * 0.10, s * 0.26, tone)]


def _rain(cx, cy, s, tone):
    out = _cloud(cx, cy - s * 0.12, s, tone)
    for dx in (-0.20, 0.02, 0.24):
        out.append(line(cx + s * dx, cy + s * 0.26,
                        cx + s * (dx - 0.06), cy + s * 0.50, "cool", s * 0.06))
    return out


def _snow(cx, cy, s, tone):
    out = _cloud(cx, cy - s * 0.12, s, tone)
    for dx in (-0.20, 0.02, 0.24):
        out.append(circle(cx + s * dx, cy + s * 0.38, s * 0.05, "cool"))
    return out


def _storm(cx, cy, s, tone):
    out = _cloud(cx, cy - s * 0.12, s, tone)
    out.append(tri([(cx - s * 0.10, cy + s * 0.22),
                    (cx + s * 0.14, cy + s * 0.22),
                    (cx - s * 0.02, cy + s * 0.54)], "warn"))
    return out


def _flame(cx, cy, s, tone):
    return [tri([(cx, cy - s * 0.46), (cx + s * 0.30, cy + s * 0.18),
                 (cx - s * 0.30, cy + s * 0.18)], "hot"),
            circle(cx, cy + s * 0.18, s * 0.30, "hot"),
            circle(cx, cy + s * 0.24, s * 0.15, "warn")]


def _ice(cx, cy, s, tone):
    out = []
    import math
    for i in range(3):
        a = math.pi * i / 3.0
        out.append(line(cx - math.cos(a) * s * 0.45, cy - math.sin(a) * s * 0.45,
                        cx + math.cos(a) * s * 0.45, cy + math.sin(a) * s * 0.45,
                        "cool", s * 0.07))
    return out


def _up(cx, cy, s, tone):
    return [tri([(cx, cy - s * 0.34), (cx + s * 0.32, cy + s * 0.24),
                 (cx - s * 0.32, cy + s * 0.24)], tone or "good")]


def _down(cx, cy, s, tone):
    return [tri([(cx, cy + s * 0.34), (cx + s * 0.32, cy - s * 0.24),
                 (cx - s * 0.32, cy - s * 0.24)], tone or "bad")]


ICONS = {"sun": _sun, "cloud": _cloud, "rain": _rain, "snow": _snow,
         "storm": _storm, "flame": _flame, "ice": _ice, "up": _up,
         "down": _down}


def icon(name: str, cx: float, cy: float, size: float = 0.22,
         tone: str = "normal") -> list:
    """A named icon, expanded to primitives HERE.

    Returns a list, so a component splices it into its instruction list. The
    device never learns the name -- which is what lets a new icon ship without
    touching firmware.
    """
    maker = ICONS.get(str(name))
    return list(maker(cx, cy, size, tone)) if maker else []


def _shape_px(item: dict, w: int, h: int) -> dict:
    """A shape in pixels, in the SAME vocabulary a resolved text uses.

    `x`, `y`, `px` and the extra corners, not `cx`/`r`/`x1` -- because the
    device stores one `Placement` struct whatever the shape is, and the parity
    fixture compares field by field. Two names for one resolved position is how
    the whole shape vocabulary came to be pinned by nothing: the fixture
    compared `x` against a dict that only had `cx`, read 0 for both sides, and
    agreed.

    Radii and stroke widths scale off the SHORT side, so a circle stays a
    circle on a panel that is not square.
    """
    short = min(int(w), int(h))
    out = {"t": item["t"],
           "tone": item.get("tone") if item.get("tone") in TONES else "normal",
           "text": "", "x2": 0, "y2": 0, "x3": 0, "y3": 0, "fill": True}
    if item["t"] == "circle":
        out.update(x=_round_half_up(item.get("cx", 0.5) * w),
                   y=_round_half_up(item.get("cy", 0.5) * h),
                   px=max(1, _round_half_up(item.get("r", 0.1) * short)),
                   fill=bool(item.get("fill", True)))
    elif item["t"] == "line":
        out.update(x=_round_half_up(item.get("x1", 0) * w),
                   y=_round_half_up(item.get("y1", 0) * h),
                   x2=_round_half_up(item.get("x2", 0) * w),
                   y2=_round_half_up(item.get("y2", 0) * h),
                   px=max(1, _round_half_up(item.get("w", 0.01) * short)))
    else:
        pts = list(item.get("p") or [])[:6]
        px = [_round_half_up(v * (w if i % 2 == 0 else h))
              for i, v in enumerate(pts)]
        px += [0] * (6 - len(px))
        out.update(x=px[0], y=px[1], x2=px[2], y2=px[3], x3=px[4], y3=px[5],
                   px=0)
    return out


def slot_width(slot: str, w: int, h: int, shape: str = "rect",
               px: int = 0) -> int:
    """How many pixels of glass a line in this slot actually has.

    On a round panel this is a CHORD, not a constant. `ROUND_USABLE = 0.72`
    was one number standing in for a function, and it was wrong in both
    directions at once: too generous at the rim, where text slid under the
    bezel, and far too mean across the middle, where a headline had a third
    more room than it was allowed to use.

    Text is centred on the slot, so it occupies a BAND from `y - px/2` to
    `y + px/2`. The edge of that band furthest from the centre line is the one
    that runs out of glass first, and it is the one measured here.
    """
    w, h = int(w), int(h)
    if str(shape) != "round":
        return int(w * RECT_USABLE)
    radius = min(w, h) / 2.0
    centre_y = h / 2.0
    y = SLOTS.get(str(slot), SLOTS["center"]) * h
    # The worse of the two edges of the text band.
    far = max(abs(y - px / 2.0 - centre_y), abs(y + px / 2.0 - centre_y))
    if far >= radius:
        return 0                         # no glass here at this size
    # Whole pixels: `0.12 * 240` and `0.88 * 240` are not equidistant from the
    # centre in binary, and two slots that are mirror images should not differ
    # by a hundredth of a pixel.
    return int(2.0 * math.sqrt(radius * radius - far * far) * ROUND_INSET)


def text_width(value: str, px: int) -> float:
    """Estimated width of a rendered line. Approximate on purpose.

    The real metrics live in the panel's font file and the server does not
    have them, so this is an average advance width. It is used to DECIDE
    (truncate, or step a size down), never to position: everything is centred,
    so an estimate that is a few percent out costs a few percent of margin
    rather than a misplaced glyph.
    """
    return len(str(value)) * int(px) * CHAR_WIDTH_RATIO


def clip(value: str, slot: str, size: str, w: int, h: int,
         shape: str = "rect") -> str:
    """`value`, shortened until it fits its slot on this glass.

    Server-side and final: the device is sent the string it should draw, so
    there is no second truncation rule to keep in step with this one. That is
    also why the marker is "..." rather than an ellipsis -- the embedded face
    is 95 glyphs and has neither.
    """
    value = str(value)
    if not value:
        return value
    px = size_px(size, w, h)
    room = slot_width(slot, w, h, shape, px)
    if text_width(value, px) <= room:
        return value
    per_char = max(1.0, px * CHAR_WIDTH_RATIO)
    keep = int(room / per_char) - len(TRUNCATION)
    if keep <= 0:
        # Narrower than the marker itself. Say something rather than nothing:
        # a blank line reads as a broken panel, three dots as a full one.
        return TRUNCATION
    return value[:keep].rstrip() + TRUNCATION


#: Largest to smallest. The ladder `fit` walks when a line is too wide.
SIZE_ORDER = ("xl", "lg", "md", "sm", "xs")

#: How far `fit` may shrink a line before it truncates instead.
#:
#: Two steps, not five. A headline that drops from `xl` to `xs` to avoid losing
#: a character has kept the text and thrown away the hierarchy, which is the
#: thing the size token was chosen for. Past two steps, cutting is the more
#: honest answer.
MAX_STEPS_DOWN = 2


def fit(value: str, slot: str, size: str, w: int, h: int,
        shape: str = "rect") -> tuple:
    """`(text, size)` that will actually fit this slot on this glass.

    Shrink first, cut second. `21:00:00` at `xl` is wider than a 240px circle,
    and the useful answer is a slightly smaller clock rather than `21:...` --
    truncation is right for prose and wrong for a number. Only once the line
    has shrunk as far as it is allowed to does it lose characters.

    Returns the size too, because that is the whole point: the wire carries the
    token the device should draw, so there is no second rule on the far side.
    """
    value = str(value)
    if not value:
        return value, size
    try:
        start = SIZE_ORDER.index(str(size))
    except ValueError:
        start = SIZE_ORDER.index("md")
    floor = min(start + MAX_STEPS_DOWN, len(SIZE_ORDER) - 1)
    for index in range(start, floor + 1):
        token = SIZE_ORDER[index]
        px = size_px(token, w, h)
        if text_width(value, px) <= slot_width(slot, w, h, shape, px):
            return value, token
    token = SIZE_ORDER[floor]
    return clip(value, slot, token, w, h, shape), token


def text(slot: str, value: str, size: str = "md",
         tone: str = "normal") -> dict:
    """Build one text instruction. Components use this rather than dict literals
    so a vocabulary change is one edit, not a search."""
    return {"t": "text", "slot": slot, "v": for_device(value), "size": size,
            "tone": tone}


def to_svg(draw: list, w: int, h: int, *, round_panel: bool = True) -> str:
    """Rasterise an instruction list to an SVG preview.

    SVG rather than PNG deliberately: no Chromium fork, no render queue, no
    temp files -- a preview must not be able to spend the resource a device
    needs for its frame. It also means the dashboard stays a plain page with no
    external dependency, which is the same constraint the scenes live under.

    This is a PREVIEW, not the frame. It shows what goes where and how big; the
    device's fonts and antialiasing are its own. That is the honest boundary --
    claiming pixel fidelity for a panel the server never draws would be the
    drift this whole design exists to avoid.
    """
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" role="img">'
    ]
    if round_panel:
        r = min(w, h) // 2
        parts.append(f'<circle cx="{w // 2}" cy="{h // 2}" r="{r}" fill="#000"/>')
        parts.append(f'<circle cx="{w // 2}" cy="{h // 2}" r="{r - 1}" '
                     f'fill="none" stroke="#333" stroke-width="1"/>')
    else:
        parts.append(f'<rect width="{w}" height="{h}" fill="#000"/>')

    # The preview's colours are the panel's, so a preview is not a nicer
    # picture of a duller screen.
    # These are the DEVICE's colours, converted from its RGB565. A preview in
    # different colours is a prettier picture of a duller screen.
    #
    # `bad` is brighter than a pure red would be, on purpose: red's luma
    # coefficient is 0.2126, so #ff3431 lands at the same luminance as `dim`
    # -- the tone that must jump out reading as the tone that means ignore me.
    # Lifting the red alone was not enough: at #ff6b5e it still came out only
    # 6% above `dim`. `dim` moved down too, which it wanted anyway -- 52% grey
    # is loud for a tone whose whole job is to recede. Now 1.36x, and pinned.
    fill = {"normal": "#ffffff", "dim": "#6f6d6f", "good": "#29ce41",
            "bad": "#ff7a68", "accent": "#5acae6", "warn": "#ffd23f",
            "cool": "#4d8fff", "hot": "#f6894a", "off": "#000000"}
    for item in resolve(draw, w, h):
        colour = fill.get(item["tone"], "#fff")
        if item["t"] == "fill":
            parts.append(f'<rect x="0" y="0" width="{w}" height="{h}" '
                         f'fill="{colour}"/>')
            continue
        if item["t"] == "circle":
            parts.append(f'<circle cx="{item["x"]}" cy="{item["y"]}" '
                         f'r="{item["px"]}" fill="{colour}"/>' if item["fill"]
                         else f'<circle cx="{item["x"]}" cy="{item["y"]}" '
                              f'r="{item["px"]}" fill="none" '
                              f'stroke="{colour}"/>')
            continue
        if item["t"] == "line":
            parts.append(f'<line x1="{item["x"]}" y1="{item["y"]}" '
                         f'x2="{item["x2"]}" y2="{item["y2"]}" '
                         f'stroke="{colour}" stroke-width="{item["px"]}" '
                         f'stroke-linecap="round"/>')
            continue
        if item["t"] == "tri":
            pts = (f'{item["x"]},{item["y"]} {item["x2"]},{item["y2"]} '
                   f'{item["x3"]},{item["y3"]}')
            parts.append(f'<polygon points="{pts}" fill="{colour}"/>')
            continue
        # dominant-baseline centres the glyph box on the slot's y, which is
        # what the firmware's middle_center datum does.
        parts.append(
            f'<text x="{item["x"]}" y="{item["y"]}" fill="{colour}" '
            f'font-family="Helvetica,Arial,sans-serif" '
            f'font-size="{item["px"]}" text-anchor="middle" '
            f'dominant-baseline="central">{_escape(item["text"])}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _escape(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
