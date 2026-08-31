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
         "hot")      # hot end of a scale

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
        if item.get("t") in ("circle", "line", "tri"):
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

#: Vertical room one row needs, as a multiple of its type height, before rows
#: start touching.
ROW_PITCH = 2.0


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
    usable = w * (ROUND_USABLE if shape == "round" else 0.94)
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
    "¿": "", "¡": "", "·": "-", "—": "-", "–": "-",
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


def circle(cx: float, cy: float, r: float, tone: str = "normal",
           fill: bool = True) -> dict:
    return {"t": "circle", "cx": round(cx, 4), "cy": round(cy, 4),
            "r": round(r, 4), "tone": tone, "fill": bool(fill)}


def line(x1: float, y1: float, x2: float, y2: float, tone: str = "normal",
         w: float = 0.012) -> dict:
    return {"t": "line", "x1": round(x1, 4), "y1": round(y1, 4),
            "x2": round(x2, 4), "y2": round(y2, 4), "w": round(w, 4),
            "tone": tone}


def tri(points, tone: str = "normal") -> dict:
    """A filled triangle from three (x, y) fractions."""
    flat = [round(v, 4) for point in points for v in point]
    return {"t": "tri", "p": flat, "tone": tone}


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
    """A shape in pixels. Fractions scale off the SHORT side for radii and
    widths, so a circle stays a circle on a panel that is not square."""
    short = min(int(w), int(h))
    out = {"t": item["t"],
           "tone": item.get("tone") if item.get("tone") in TONES else "normal"}
    if item["t"] == "circle":
        out.update(cx=_round_half_up(item.get("cx", 0.5) * w),
                   cy=_round_half_up(item.get("cy", 0.5) * h),
                   r=max(1, _round_half_up(item.get("r", 0.1) * short)),
                   fill=bool(item.get("fill", True)))
    elif item["t"] == "line":
        out.update(x1=_round_half_up(item.get("x1", 0) * w),
                   y1=_round_half_up(item.get("y1", 0) * h),
                   x2=_round_half_up(item.get("x2", 0) * w),
                   y2=_round_half_up(item.get("y2", 0) * h),
                   w=max(1, _round_half_up(item.get("w", 0.01) * short)))
    else:
        pts = list(item.get("p") or [])[:6]
        out["p"] = [_round_half_up(v * (w if i % 2 == 0 else h))
                    for i, v in enumerate(pts)]
    return out


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
    fill = {"normal": "#ffffff", "dim": "#8a8a8a", "good": "#5ad16b",
            "bad": "#e05a5a", "accent": "#5ac8e0", "warn": "#e8b23a",
            "cool": "#6aa9f0", "hot": "#f08a4b"}
    for item in resolve(draw, w, h):
        colour = fill.get(item["tone"], "#fff")
        if item["t"] == "circle":
            parts.append(f'<circle cx="{item["cx"]}" cy="{item["cy"]}" '
                         f'r="{item["r"]}" fill="{colour}"/>' if item["fill"]
                         else f'<circle cx="{item["cx"]}" cy="{item["cy"]}" '
                              f'r="{item["r"]}" fill="none" stroke="{colour}"/>')
            continue
        if item["t"] == "line":
            parts.append(f'<line x1="{item["x1"]}" y1="{item["y1"]}" '
                         f'x2="{item["x2"]}" y2="{item["y2"]}" '
                         f'stroke="{colour}" stroke-width="{item["w"]}" '
                         f'stroke-linecap="round"/>')
            continue
        if item["t"] == "tri":
            pts = " ".join(f"{item['p'][i]},{item['p'][i+1]}"
                           for i in range(0, len(item["p"]), 2))
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
