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

#: How much of a region's inner height the headline may take, by SHAPE.
#:
#: `panel` stacks three blocks under it, so it gets the least. `card` stands
#: alone, so it gets the most. `strip` is one line ALONG a band -- a headline
#: there would BE the line, so it is sized as body text. None is the old
#: behaviour, unchanged, for every caller that does not name a shape.
_HERO_SHARE = {"panel": 0.115, "card": 0.30, "badge": 0.42, "strip": 0.0}
def esc(value) -> str:
    """Feed text, safe to interpolate into a rendered page.

    Everything a component draws from a feed is remote text nobody in this
    repo controls: a calendar SUMMARY comes from whoever shares the calendar,
    fixture names come from football-data.org. It reaches a document Chromium
    rasterises, and `compose.scope_css` isolates only CSS -- not the DOM -- so
    a single "<" in an event title parses as a tag, swallows the row's closing
    divs and collapses the whole block. `<div style=...>` in a summary escapes
    its region and can black out the panel.
    """
    return (str("" if value is None else value)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def metrics(width: int, height: int, shape: str | None = None,
            hero_share: float | None = None) -> dict:
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
    # The headline is sized for what the SHAPE has to hold, not for the
    # region's height. `inner * 0.55` assumes one line: it gives a 335px
    # column a 56px number, which is right for a clock face standing alone and
    # 65% too big above an hourly strip and five days of forecast.
    #
    # An unnamed shape keeps the old arithmetic exactly, because eight
    # components still call it that way and this must not redesign them.
    # The COMPONENT may override the share, because how much of a region the
    # headline may take is a property of what sits under it, not of the shape.
    # A weather panel stacks three blocks below its number; a clock has one
    # small label. One share for both gave the clock a 21px time in a block
    # the design draws at 48, and left half the width empty.
    share = hero_share if hero_share is not None else _HERO_SHARE.get(shape, 0.55)
    hero = max(fs, min(HERO_PX, round(inner * share)))
    return {
        "pad": pad,
        "pad_sm": max(1, round(pad * 0.4)),
        "row": row,
        # A list of one-line rows does not need prose leading. 1.55 is right
        # for a paragraph and airy for a fixture list: it fit three matches in
        # a block that has room for five.
        "row_tight": max(MIN_PX, round(fs * 1.25)),
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


#: The class the composer looks for to know a region has nothing in it.
#: On the ONE function that renders an empty state, so a component cannot
#: forget to declare itself empty -- there is nowhere else to declare it from.
EMPTY_CLASS = "rg-empty"

EMPTY_CSS = """
/* One line, left-aligned with everything else in the column. It used to be a
   centred two-line block at `--lg`: the only centred text on the panel, set
   LARGER than the rows it stands in for, so the placeholder was louder than
   the data. A thing that is not there should not shout. */
.nothing{height:100%;display:flex;align-items:center;gap:.5em;
  font-size:var(--fs)}
.nothing .hint{font-size:var(--sm);margin-left:auto;text-align:right}
.nothing.stack{flex-direction:column;align-items:flex-start;
  justify-content:center;gap:2px}
.nothing.stack .hint{margin-left:0}
"""


def empty(note: str, hint: str = "", shape: str | None = None) -> str:
    """What a region says when it has nothing to show.

    Shared because three components each grew their own and two of them grew
    NOTHING -- `quotes` with no symbols rendered an empty table and `sport`
    with no team rendered a bare em dash, while the identical components on
    the round panel said "sin simbolos" and "elige uno en los ajustes". A
    component cannot be forthcoming on one screen and silent on another.
    """
    # The hint is a sentence, and a markets cell is 108 usable pixels -- about
    # eighteen characters. It belongs in a block, where there is room to be
    # helpful; in a cell it just overflows and gets clipped.
    tail = (f'<div class="hint">{hint}</div>'
            if hint and shape not in ("badge", "strip") else "")
    # A narrow cell has no room for note and hint side by side.
    stack = " stack" if shape in ("badge", "strip") else ""
    return (f'<div class="nothing {EMPTY_CLASS}{stack}">'
            f'<div>{note}</div>{tail}</div>')


def rows(width: int, height: int, shape: str | None = None,
         tight: bool = False) -> int:
    """How many body lines fit in a region of this size.

    Shared, because `calendar`, `sport` and `quotes` all need the number and
    three private answers is exactly the drift the variant work exists to
    stop. `calendar` hard-coded eight rows in its HTML while computing a fit
    for its draw list and using neither.
    """
    m = metrics(width, height, shape=shape)
    usable = max(0, int(height) - 2 * m["pad"])
    return max(0, usable // max(1, m["row_tight" if tight else "row"]))


def page(width: int, height: int, body: str, extra_css: str = "",
         shape: str | None = None, hero_share: float | None = None) -> str:
    m = metrics(width, height, shape=shape, hero_share=hero_share)
    # On `html,body` rather than `:root`: `compose.scope_css` rewrites both to
    # the placement's wrapper, so the properties stay inside their own region
    # and two fragments on one page cannot overwrite each other's scale.
    return (f'<!doctype html><meta charset="utf-8">'
            f'<style>{BASE_CSS}'
            f'html,body{{width:{width}px;height:{height}px;'
            + "".join(f'--{k.replace("_", "-")}:{v}px;' for k, v in m.items())
            + '}'
            f'{extra_css}</style>{body}')
