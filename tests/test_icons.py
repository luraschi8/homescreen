"""Sky pictures, and whether they survive a 1-bit threshold.

Two failed attempts are recorded here because the second was worse than the
first and the test could not tell.

The first ladder wrote 1.6/1.8/2.2 as though those were device pixels. They
are user units in a 40-unit box, so the 13px icon drew at 0.72 device px and
the 28px one at 1.12 -- the smallest icon got the thinnest pen.

The second chased a constant 1.25 device px, which in user units is 3.85 at
13px. The sun's gap between its disc and its rays is 4.5 units, so the stroke
ate 3.85 of it and the glyph closed into a blob; `fog`'s three bands rendered
as two. Measurably worse ink than the bug it replaced.

The test that let that through computed `_stroke(px) * px / 40` -- the exact
inverse of `_stroke` -- so it returned 1.25 whatever the formula was, and
every assertion in it was a tautology. These read the EMITTED SVG and check it
against the geometry it has to survive.
"""
import re

import pytest

from homescreen.scenes import _icons

SIZES = (8, 10, 13, 15, 16, 17, 20, 22, 28, 40)
SKIES = ("clear", "cloud", "rain", "snow", "storm", "fog")


def _svg(sky: str, px: int) -> str:
    return _icons.sky(sky, px)


def _stroke_units(sky: str, px: int) -> float:
    return float(re.search(r'stroke-width="([\d.]+)"', _svg(sky, px)).group(1))


def _drawn_px(sky: str, px: int) -> int:
    """The size actually emitted, which is not always the size asked for."""
    return int(re.search(r'width="(\d+)"', _svg(sky, px)).group(1))


def _gap_units(px: int) -> float:
    drawn = max(px, _icons.MIN_PX)
    return _icons._GAP["simple" if drawn < _icons.SIMPLIFY_BELOW_PX
                       else "detail"]


@pytest.mark.parametrize("px", SIZES)
def test_a_stroke_never_closes_the_gap_it_sits_in(px):
    if px < _icons.SIMPLIFY_BELOW_PX:
        pytest.skip("the small set is filled; it has no strokes to close a gap")
    # THE constraint, and the one both previous attempts missed. A stroke eats
    # half its width from each side of a gap, so a stroke as wide as the gap
    # closes it -- and a closed sun is a black blob, not a sun.
    stroke = _stroke_units("clear", px)
    gap = _gap_units(px)
    assert stroke <= gap * 0.5 + 0.01, (px, stroke, gap)
    assert gap - stroke >= 1.5, f"{px}px leaves only {gap - stroke:.2f}u clear"


@pytest.mark.parametrize("px", SIZES)
def test_a_stroke_is_as_close_to_a_whole_device_pixel_as_the_drawing_allows(px):
    if px < _icons.SIMPLIFY_BELOW_PX:
        pytest.skip("the small set is filled; a fill has no stroke width")
    # Not a hard floor: where the geometry and the glass disagree, a readable
    # shape at 0.9 device px beats an unreadable one at 1.25.
    # Measured at the size DRAWN, not the size requested: below the floor the
    # icon is emitted larger than asked for rather than illegibly small.
    device = (_stroke_units("clear", px) * _drawn_px("clear", px)
              / _icons.VIEWBOX)
    assert 0.85 <= device <= 1.35, (px, device)


def test_a_small_icon_is_a_different_drawing_not_a_fatter_one():
    # The actual fix. At 13px one user unit is 0.325 device px, so a 4.5-unit
    # gap is one and a half pixels holding two strokes: no stroke weight
    # rescues that, only fewer and larger features.
    assert _svg("clear", 13) != _svg("clear", 28).replace("28", "13")
    small = _svg("clear", 13)
    large = _svg("clear", 28)
    assert small.count("M") < large.count("M"), "fewer rays, not thicker ones"


def test_both_drawings_exist_for_every_sky():
    assert sorted(_icons._SMALL) == sorted(_icons._PATHS) == sorted(SKIES)


@pytest.mark.parametrize("px", (13, 15))
def test_rain_and_snow_differ_by_more_than_mark_count(px):
    rain, snow = _svg("rain", px), _svg("snow", px)
    assert rain != snow
    # Both are FILLED below the simplify threshold now, which is the point: a
    # filled shape either covers a pixel or does not, where a thin stroke
    # lands half-covered along its whole length and thresholds away. Rain's
    # marks are bars and snow's are discs, so they differ in shape and not
    # only in how many there are.
    if px < _icons.SIMPLIFY_BELOW_PX:
        assert 'fill="#000"' in rain and 'fill="#000"' in snow
        assert "<rect" in rain and "<circle" in snow
    else:
        assert 'fill="#000"' in snow


def test_fog_keeps_every_band_it_draws():
    # It drew three and rendered two, so it was cut to two deliberately. That
    # was a workaround for STROKES closing up at this size; filled bars of a
    # definite height do not, so fog has its third band back.
    small = _svg("fog", 13)
    assert small.count("<rect") == 3, "the small fog is three filled bands"
    assert "stroke" not in small


def test_an_unknown_sky_has_no_picture_at_any_size():
    for px in SIZES:
        assert _icons.sky("aurora", px) == ""
        assert _icons.sky("", px) == ""


def test_an_icon_uses_only_the_two_colours_the_panel_has():
    for sky in SKIES:
        for px in (13, 28):
            for colour in re.findall(r'(?:fill|stroke)="([^"]+)"', _svg(sky, px)):
                assert colour in ("#000", "#fff", "#000000", "#ffffff", "none"), \
                    (sky, px, colour)


def test_a_direction_arrow_is_filled_rather_than_stroked():
    up = _icons.arrow("up", 10)
    assert 'fill="#000"' in up and "stroke" not in up
    assert _icons.arrow("sideways", 10) == ""


def test_sunrise_and_sunset_are_not_weather_glyphs():
    # The clock borrowed `clear` and `cloud` for them, so on a cloudy
    # afternoon the panel drew the identical cloud twice: once meaning
    # "nublado" and once meaning "20:47".
    rise = _icons.sun_event("sunrise", 13)
    set_ = _icons.sun_event("sunset", 13)
    assert rise and set_
    assert rise != set_, "sunrise and sunset are the same picture"
    assert rise != _icons.sky("clear", 13)
    assert set_ != _icons.sky("cloud", 13)
    for svg in (rise, set_):
        # Filled, because a hairline arc at 13px thresholds to noise.
        assert 'fill="#000"' in svg
    assert _icons.sun_event("nonsense", 13) == ""


def test_the_sun_event_glyphs_hold_the_type_floor():
    assert 'width="10"' in _icons.sun_event("sunrise", 4)


def test_the_two_sun_events_differ_where_it_matters():
    # They carry exactly one bit differently -- which way the sun is going --
    # and the panel never shows one beside the other for comparison.
    rise = _icons.sun_event("sunrise", 13)
    set_ = _icons.sun_event("sunset", 13)
    # Same horizon, different mark: the difference is the direction, not the
    # furniture around it.
    assert _icons._HORIZON in rise and _icons._HORIZON in set_
    assert _icons._SUN_EVENT["sunrise"] in rise
    assert _icons._SUN_EVENT["sunset"] in set_


def test_the_small_set_is_filled_because_strokes_do_not_survive_the_threshold():
    # The hourly strip drew `-[]-` six times: a stroked ring one pixel thick
    # thresholds to a hollow box, and none of the six was a sun. Rendered
    # through the real 160 threshold at 12, 13, 15 and 20px and looked at.
    for sky in SKIES:
        small = _svg(sky, 13)
        assert "stroke" not in small, sky
        assert 'fill="#000"' in small, sky
    # ...and the large set still strokes, which is right: at 28px the detailed
    # drawing has room for its gaps.
    assert "stroke=" in _svg("clear", 28)


def test_the_small_skies_are_all_different_pictures():
    seen = {sky: _svg(sky, 13) for sky in SKIES}
    assert len(set(seen.values())) == len(SKIES)
