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

#: What a stroke must measure ON THE GLASS. Below one device pixel a stroke is
#: drawn as partial coverage, which is a grey, and the panel has no greys -- so
#: the threshold either eats it or turns it into a dotted line depending on
#: where it happened to fall. A little over one gives it somewhere to land.
_TARGET_DEVICE_PX = 1.25

#: The box the paths are drawn in. A `stroke-width` is in THESE units, and its
#: weight on the glass is `width * px / VIEWBOX`.
VIEWBOX = 40


def _stroke(px: int) -> float:
    """Stroke width in user units, for a constant weight on the glass.

    The first version of this was a ladder of literals -- 1.6, 1.8, 2.2 --
    written as though they were device pixels. They are not: at 13px that is
    0.72 device px and at 28px it is 1.12, so the SMALLEST icon got the
    THINNEST pen, which is the exact opposite of the intent stated beside it.
    It was also discontinuous the wrong way, a 16px icon drawing thinner than
    a 15px one.

    Arithmetic rather than a table, so it cannot drift again.
    """
    return round(_TARGET_DEVICE_PX * VIEWBOX / max(1, int(px)), 2)


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
    px = max(6, int(px))
    return (f'<svg class="ar" viewBox="0 0 {VIEWBOX} {VIEWBOX}" '
            f'width="{px}" height="{px}" aria-hidden="true" fill="#000">'
            f'<path d="{path}"/></svg>')


def sky(name: str, px: int) -> str:
    """An inline SVG for a normalised sky, or "" if we have no picture.

    Empty rather than a placeholder: a component that gets nothing collapses
    the space, which is CLAUDE.md's rule, while a "no icon" glyph is a mark on
    the panel that means nothing.
    """
    path = _PATHS.get(str(name or ""))
    if not path:
        return ""
    px = max(8, int(px))
    return (f'<svg class="ic" viewBox="0 0 {VIEWBOX} {VIEWBOX}" '
            f'width="{px}" height="{px}" '
            f'aria-hidden="true" fill="none" stroke="#000" '
            f'stroke-width="{_stroke(px)}" stroke-linecap="round" '
            f'stroke-linejoin="round">{path}</svg>')
