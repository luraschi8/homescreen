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

#: Stroke gets HEAVIER as the icon shrinks. A 1px stroke at the panel's ~124
#: DPI thresholds to a dotted line, so a 13px icon needs a proportionally
#: fatter pen than a 28px one to survive the same threshold.
def _stroke(px: int) -> float:
    if px >= 24:
        return 1.6
    if px >= 16:
        return 1.8
    return 2.2


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
    return (f'<svg class="ic" viewBox="0 0 40 40" width="{px}" height="{px}" '
            f'aria-hidden="true" fill="none" stroke="#000" '
            f'stroke-width="{_stroke(px)}" stroke-linecap="round" '
            f'stroke-linejoin="round">{path}</svg>')
