"""Shared CSS for pixel-push scenes.

Every rule here is a CLAUDE.md invariant, not a preference:

  only #000 / #fff       The panel is 1-bit. There are no greys: 4-grey mode
                         would forfeit partial refresh, and we need a ticking
                         clock. Any other colour value is a bug.
  hierarchy by size      13px/500 primary, 13px/400 secondary, 11px/400
    and weight, not tone tertiary. Grey text at 10-13px thresholds to speckle.
  nothing below 10px     ~0.08mm stroke at 124 DPI. That is the floor, not a
                         target.
  no CDN fonts, ever     A slow network at boot swaps in a fallback face and
                         the layout silently breaks. Inter is installed
                         locally on the Pi.
  smoothing off          Keeps the render binary. Measured: the Pi honours
                         this far less than macOS (0.558% vs 0.010% grey), so
                         the 160 threshold is load-bearing there.
"""

#: CLAUDE.md's floor. Not a target.
MIN_PX = 10

#: SPEC SS9's Madrid clock, and the ceiling for any headline.
HERO_PX = 56
def metrics(width: int, height: int) -> dict:
    """The type ladder and spacing for a region of this size, in WHOLE pixels.

    Computed here rather than expressed in CSS because `compose` puts every
    fragment in ONE document: a fragment is a div, so `vh` is the whole panel
    however small its region is, and every relative unit lies. The server knows
    the rectangle, so the server does the arithmetic -- the same division of
    labour the draw vocabulary already uses for the round panel.

    Whole pixels, and no `calc()` in the scenes either: a fractional font-size
    lands on a half-lit pixel, and thresholding turns that grey on a panel that
    has no greys. `tests/test_scenes.py` pins it.

    On a full 800x480 this reproduces SPEC SS9's scale exactly -- 18px padding,
    56px Madrid clock, 31px secondary clock, 16px ticker price, 13px body, 11px
    tertiary, 10px label. It only bends when the region is smaller than the
    panel that scale was designed for, which is when it was breaking: 18px of
    padding top and bottom is 58% of a 62px markets band, and what was left
    could not hold a row.
    """
    width, height = max(1, int(width)), max(1, int(height))
    short = min(width, height)
    pad = max(2, min(18, round(short * 0.06)))
    inner = max(1, height - 2 * pad)
    fs = max(MIN_PX, min(13, round(inner * 0.22)))
    row = round(fs * 1.55)
    if pad * 2 + row > height:              # a band too shallow for padding
        pad = max(0, (height - row) // 2)
        inner = max(1, height - 2 * pad)
    hero = max(fs, min(HERO_PX, round(inner * 0.55)))
    return {
        "pad": pad,
        "pad_sm": max(1, round(pad * 0.4)),
        "row": row,
        "hero": hero,
        "sub": max(MIN_PX, round(hero * 0.55)),     # SPEC: 31 on a full panel
        "lg": max(MIN_PX, round(fs * 1.25)),        # SPEC: 16, the ticker price
        "fs": fs,                                   # SPEC: 13, body
        "sm": max(MIN_PX, round(fs * 0.85)),        # SPEC: 11, tertiary
        "xs": MIN_PX,                               # SPEC: 10, section labels
    }


BASE_CSS = """
*{-webkit-font-smoothing:none;text-rendering:geometricPrecision;
  margin:0;padding:0;box-sizing:border-box}
html,body{background:#fff;color:#000;font-family:Inter,'DejaVu Sans',sans-serif;
  overflow:hidden}
.pri{font-size:13px;font-weight:500}
.sec{font-size:13px;font-weight:400}
.ter{font-size:11px;font-weight:400}
.lab{font-size:10px;font-weight:500;letter-spacing:.14em;text-transform:uppercase}
.rule{border-top:1px solid #000}
.dot{border-top:1px dotted #000}
.pill{display:inline-block;background:#000;color:#fff;border-radius:3px;
  padding:2px 7px;font-size:11px;font-weight:500}
"""


def page(width: int, height: int, body: str, extra_css: str = "") -> str:
    m = metrics(width, height)
    # On `html,body` rather than `:root`: `compose.scope_css` rewrites both to
    # the placement's wrapper, so the properties stay inside their own region
    # and two fragments on one page cannot overwrite each other's scale.
    return (f'<!doctype html><meta charset="utf-8">'
            f'<style>{BASE_CSS}'
            f'html,body{{width:{width}px;height:{height}px;'
            + "".join(f'--{k.replace("_", "-")}:{v}px;' for k, v in m.items())
            + '}'
            f'{extra_css}</style>{body}')
