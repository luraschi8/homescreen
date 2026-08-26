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
    return (f'<!doctype html><meta charset="utf-8">'
            f'<style>{BASE_CSS}'
            f'html,body{{width:{width}px;height:{height}px}}'
            f'{extra_css}</style>{body}')
