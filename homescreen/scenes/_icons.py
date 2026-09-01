"""Sky pictures as inline SVG, for the pixel-push panel.

The original design draws these with a Tabler icon webfont. CLAUDE.md forbids
that outright and for a reason that is not stylistic: a slow network at boot
silently swaps in a fallback face, and a layout that depended on glyph metrics
breaks with nothing to show why. Inline SVG has no such failure mode -- it
either arrived with the document or the document did not arrive.

Line drawings on purpose. The panel is 1-bit, so a filled shape is a black
blob; strokes at the sizes here threshold cleanly.
"""

from __future__ import annotations

#: What a stroke would ideally measure ON THE GLASS. Below one device pixel a
#: stroke is partial coverage, which is a grey, and the panel has no greys.
_TARGET_DEVICE_PX = 1.25

#: The box the paths are drawn in. A `stroke-width` is in THESE units, and its
#: weight on the glass is `width * px / VIEWBOX`.
VIEWBOX = 40

#: The smallest gap between two strokes in each path set, in user units.
#:
#: This is the constraint that matters and the one the first two attempts both
#: missed. A stroke eats half its width from each side of a gap, so a stroke
#: wider than the gap CLOSES it -- and chasing a constant device weight put
#: 3.85 units of pen into the sun's 4.5-unit gap, leaving 0.65. The sun became
#: a blob and `fog` rendered two lines instead of three, which is worse than
#: the thin strokes that started this.
_GAP = {"detail": 4.5, "simple": 7.0}


def _stroke(px: int, gap: float) -> float:
    """Stroke width in user units: as heavy as the glass wants, as light as
    the drawing survives.

    Two bounds, and the geometry wins. Aiming only at a device weight closes
    the glyph; aiming only at the glyph leaves a sub-pixel line that thresholds
    to dots. Where they conflict -- and at 13px they do -- a readable shape at
    0.9 device px beats an unreadable one at 1.25.
    """
    px = max(1, int(px))
    wanted = _TARGET_DEVICE_PX * VIEWBOX / px
    return round(min(wanted, gap * 0.5), 2)


#: Below this, a 40-unit drawing cannot be rasterised: at 13px one unit is
#: 0.325 device pixels, so a 4.5-unit gap is one and a half pixels holding two
#: strokes. The simplified set has fewer, larger features instead of finer
#: ones, which is the only thing that actually helps.
SIMPLIFY_BELOW_PX = 17

#: The same floor CLAUDE.md sets for type. Smaller than this is not a picture.
MIN_PX = 10

#: Fewer marks, wider apart. Rain and snow were the same picture at 13 and
#: 15px -- a cloud with three small marks under it, and neither the 6-unit
#: slants nor the 4-unit crosses resolved. Two long strokes and three FILLED
#: dots do: a filled shape either covers a pixel or does not, where a thin
#: cross lands half-covered along its whole length.
_SMALL = {
    "clear": ('<circle cx="20" cy="20" r="7"/>'
              '<path d="M20 1v5M20 34v5M1 20h5M34 20h5"/>'),
    "cloud": ('<path d="M11 30h18a7.5 7.5 0 0 0 0-15 10 10 0 0 0-19-2.5'
              'A7 7 0 0 0 11 30z"/>'),
    "rain": ('<path d="M11 24h18a7.5 7.5 0 0 0 0-15 10 10 0 0 0-19-2.5'
             'A7 7 0 0 0 11 24z"/>'
             '<path d="M15 30v8M27 30v8"/>'),
    "snow": ('<path d="M11 24h18a7.5 7.5 0 0 0 0-15 10 10 0 0 0-19-2.5'
             'A7 7 0 0 0 11 24z"/>'
             '<circle cx="14" cy="34" r="2.6" fill="#000" stroke="none"/>'
             '<circle cx="21" cy="34" r="2.6" fill="#000" stroke="none"/>'
             '<circle cx="28" cy="34" r="2.6" fill="#000" stroke="none"/>'),
    "storm": ('<path d="M11 23h18a7.5 7.5 0 0 0 0-15 10 10 0 0 0-19-2.5'
              'A7 7 0 0 0 11 23z"/>'
              '<path d="M23 27l-9 8h7l-3 5z" fill="#000" stroke="none"/>'),
    # Two bands, not three: at 13px the three-band version rendered as two
    # anyway, and a picture that silently loses a stroke is a different
    # picture.
    "fog": '<path d="M5 15h30M5 27h30"/>',
}


#: Drawn in a 40x40 box and scaled, so one set of coordinates serves every
#: size the layout asks for.
_PATHS = {
    "clear": ('<circle cx="20" cy="20" r="8.5"/>'
              '<path d="M20 2v5M20 33v5M2 20h5M33 20h5'
              'M7.3 7.3l3.5 3.5M29.2 29.2l3.5 3.5'
              'M32.7 7.3l-3.5 3.5M10.8 29.2l-3.5 3.5"/>'),
    "cloud": ('<path d="M11 31h18a7 7 0 0 0 0-14 9.5 9.5 0 0 0-18-2.5'
              'A6.5 6.5 0 0 0 11 31z"/>'),
    "rain": ('<path d="M11 25h18a7 7 0 0 0 0-14 9.5 9.5 0 0 0-18-2.5'
             'A6.5 6.5 0 0 0 11 25z"/>'
             '<path d="M14 30l-2 6M22 30l-2 6M30 30l-2 6"/>'),
    "snow": ('<path d="M11 25h18a7 7 0 0 0 0-14 9.5 9.5 0 0 0-18-2.5'
             'A6.5 6.5 0 0 0 11 25z"/>'
             '<path d="M13 32h4M15 30v4M23 32h4M25 30v4M33 32h-4"/>'),
    "storm": ('<path d="M11 24h18a7 7 0 0 0 0-14 9.5 9.5 0 0 0-18-2.5'
              'A6.5 6.5 0 0 0 11 24z"/>'
              '<path d="M22 27l-6 6h6l-4 5"/>'),
    "fog": '<path d="M6 14h28M9 21h22M6 28h28"/>',
}

#: Direction, as FILLED triangles rather than the design's hairline diagonal
#: arrows. A diagonal 1px stroke is the worst case for a hard threshold -- it
#: lands half-covered along its whole length -- while a filled shape either
#: covers a pixel or does not. Solid also reads at 10px, which is where these
#: live in a markets cell.
_ARROWS = {"up": "M20 8l12 20H8z", "down": "M20 32L8 12h24z"}


def arrow(direction: str, px: int) -> str:
    """A filled triangle, up or down. "" for anything else."""
    path = _ARROWS.get(str(direction or ""))
    if not path:
        return ""
    px = max(MIN_PX, int(px))
    return (f'<svg class="ar" viewBox="0 0 {VIEWBOX} {VIEWBOX}" '
            f'width="{px}" height="{px}" aria-hidden="true" fill="#000">'
            f'<path d="{path}"/></svg>')


#: Sunrise and sunset. Their own drawing, because they are a TIME, not a sky.
#: The clock borrowed `clear` and `cloud` for them, so on a cloudy afternoon
#: the panel drew the identical cloud twice -- once meaning "nublado" and once
#: meaning "20:47". The same mark for two unrelated things.
#:
#: A filled triangle over a horizon, and nothing else. Four drawings were
#: rendered through the real 160 threshold at 13/16/20px and looked at:
#:
#:   disc + arrow + horizon  three features in 13px; both became one blob
#:   half disc on horizon    muddy at 13px; the arc closed
#:   disc above / disc sunk  pretty at 20px, but the two differ so little
#:                           that reading either one needs the other beside it
#:   arrow over a horizon    unmistakable at every size  <- this
#:
#: These two glyphs need to carry exactly one bit differently -- which way the
#: sun is going -- and on 1-bit glass at 13px a legible bit beats a picture
#: with a sun in it that nobody can resolve. Filled for the same reason the
#: market arrows are: a solid shape either covers a pixel or does not, where a
#: hairline arc lands half-covered along its whole length.
_SUN_EVENT = {
    "sunrise": "M20 6l9 16h-18z",
    "sunset": "M20 24L11 8h18z",
}

#: Sits below the mark, the full width of the box.
_HORIZON = "M3 31h34"


def sun_event(kind: str, px: int) -> str:
    """Sunrise or sunset, as a picture that means only that."""
    px = max(MIN_PX, int(px))
    mark = _SUN_EVENT.get(str(kind or ""))
    if not mark:
        return ""
    return (f'<svg class="ic" viewBox="0 0 {VIEWBOX} {VIEWBOX}" '
            f'width="{px}" height="{px}" aria-hidden="true">'
            f'<path d="{mark}" fill="#000"/>'
            f'<path d="{_HORIZON}" stroke="#000" fill="none" '
            f'stroke-width="{_stroke(px, _GAP["simple"])}" '
            f'stroke-linecap="round"/></svg>')


def sky(name: str, px: int) -> str:
    """An inline SVG for a normalised sky, or "" if we have no picture.

    Empty rather than a placeholder: a component that gets nothing collapses
    the space, which is CLAUDE.md's rule, while a "no icon" glyph is a mark on
    the panel that means nothing.

    Below `SIMPLIFY_BELOW_PX` a different, coarser drawing is used. Fattening
    the detailed one instead is what closed the sun and lost a band of the fog.
    """
    # CLAUDE.md's floor is 10px for type, and an icon smaller than the
    # smallest legible glyph is decoration nobody can read.
    px = max(MIN_PX, int(px))
    small = px < SIMPLIFY_BELOW_PX
    kind = "simple" if small else "detail"
    path = (_SMALL if small else _PATHS).get(str(name or ""))
    if not path:
        return ""
    return (f'<svg class="ic" viewBox="0 0 {VIEWBOX} {VIEWBOX}" '
            f'width="{px}" height="{px}" aria-hidden="true" fill="none" '
            f'stroke="#000" '
            f'stroke-width="{_stroke(px, _GAP[kind])}" stroke-linecap="round" '
            f'stroke-linejoin="round">{path}</svg>')
