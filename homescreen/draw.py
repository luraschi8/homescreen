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
    for item in draw or ():
        if not isinstance(item, dict) or item.get("t") != "text":
            continue
        text = item.get("v")
        if not isinstance(text, str) or not text:
            continue
        tone = item.get("tone", "normal")
        out.append({
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
