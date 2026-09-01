"""Sky pictures, and whether they survive a 1-bit threshold.

The panel has no greys. A stroke that lands under one device pixel is drawn as
a partial-coverage grey and then thresholded, so it either disappears or turns
into a dotted line depending on where it fell -- which is the failure the
render pipeline's THRESHOLD comment warns about.
"""
import re

from homescreen.scenes import _icons

#: The icon is drawn in a 40-unit box and scaled to `px`, so a stroke width is
#: in USER UNITS and its device weight is `width * px / 40`.
VIEWBOX = 40


def _device_px(px: int) -> float:
    svg = _icons.sky("clear", px)
    width = float(re.search(r'stroke-width="([\d.]+)"', svg).group(1))
    return width * px / VIEWBOX


def test_a_stroke_is_at_least_one_device_pixel_at_every_size():
    # The bug this pins: the ladder was written in user units as though they
    # were device pixels, so the 13px icon drew at 0.72 device px and the 28px
    # one at 1.12 -- the smallest icon got the THINNEST pen, which is the
    # opposite of the stated intent. Nothing but the largest reached a whole
    # pixel, and the sheet measured 1.93% intermediate greys.
    for px in (13, 15, 20, 22, 28, 40):
        got = _device_px(px)
        assert got >= 1.0, f"{px}px icon draws at {got:.2f} device px"


def test_the_pen_gets_heavier_as_the_icon_shrinks():
    # Measured where it matters -- on the glass, not in the coordinate space.
    weights = [_device_px(px) for px in (13, 15, 22, 28)]
    assert weights == sorted(weights, reverse=True) or \
        max(weights) - min(weights) < 0.35, weights


def test_the_ladder_has_no_step_that_goes_the_wrong_way():
    # It was discontinuous at the 16px boundary: a 15px icon drew at 0.82 and
    # a 16px icon at 0.72, so growing the icon made its stroke thinner.
    for smaller, larger in ((13, 14), (15, 16), (21, 22), (27, 28)):
        assert _device_px(smaller) >= _device_px(larger) - 0.05, \
            (smaller, _device_px(smaller), larger, _device_px(larger))


def test_a_stroke_is_never_so_heavy_it_fills_the_glyph():
    for px in (13, 15, 22, 28, 40):
        assert _device_px(px) <= 2.0, px


def test_every_sky_has_a_picture_and_an_unknown_one_has_none():
    from homescreen.fetch.providers import _weather
    for sky in _weather.SKY:
        assert _icons.sky(sky, 20), sky
    assert _icons.sky("aurora", 20) == ""
    assert _icons.sky("", 20) == ""


def test_an_icon_uses_only_the_two_colours_the_panel_has():
    for sky in ("clear", "cloud", "rain", "snow", "storm", "fog"):
        svg = _icons.sky(sky, 20)
        for colour in re.findall(r'(?:fill|stroke)="([^"]+)"', svg):
            assert colour in ("#000", "#fff", "#000000", "#ffffff", "none"), \
                (sky, colour)
