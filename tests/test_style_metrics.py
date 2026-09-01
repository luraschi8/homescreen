"""Scene HTML has to fit the region it is given, not the panel it was drawn for.

Every scene's CSS hardcoded SPEC §9's full-panel scale -- `padding:18px`,
`font-size:22px` -- and `compose` then dropped it into a region as small as
764x62. 36px of vertical padding in a 62px band leaves 26px for a 34px row, so
the row overflowed and `overflow:hidden` ate it: the markets band rendered
completely empty on a real 800x480 frame.

Relative CSS units cannot fix this. In a composed page the fragments are DIVs
in one document, so `vh` is the whole panel however small the region is. The
sizes have to be computed from the region, server-side, exactly as the draw
vocabulary computes its own.
"""
import re

from homescreen.scenes import _style

FULL = (800, 480)
MASTHEAD = (800, 53)
MARKETS = (764, 62)
COLUMN = (417, 335)


def test_the_full_panel_keeps_specs_own_scale():
    # SPEC §9: 18px outer padding, 13px body, 56px Madrid clock. The fix must
    # not quietly redesign the panel it was written for.
    got = _style.metrics(*FULL)
    assert got["pad"] == 18
    assert got["fs"] == 13
    assert got["hero"] == 56


def test_a_shallow_band_gets_padding_it_can_afford():
    # 18px top and bottom of a 62px band is 58% of it.
    got = _style.metrics(*MARKETS)
    assert got["pad"] * 2 < 62 * 0.25, got


def test_a_row_fits_the_band_it_is_drawn_in():
    for w, h in (MARKETS, MASTHEAD):
        got = _style.metrics(w, h)
        assert got["pad"] * 2 + got["row"] <= h, (w, h, got)


def test_a_headline_fits_its_region():
    for w, h in (FULL, MASTHEAD, MARKETS, COLUMN):
        got = _style.metrics(w, h)
        assert got["pad"] * 2 + got["hero"] <= h, (w, h, got)


def test_type_never_goes_under_the_ten_pixel_floor():
    # CLAUDE.md's floor, and it holds however small the region gets.
    for w, h in (FULL, MASTHEAD, MARKETS, COLUMN, (60, 20), (1, 1)):
        got = _style.metrics(w, h)
        assert got["fs"] >= 10, (w, h, got)
        assert got["hero"] >= got["fs"], (w, h, got)


def test_the_metrics_reach_the_page_as_custom_properties():
    html = _style.page(764, 62, "<div></div>")
    assert "--pad:" in html and "--fs:" in html and "--hero:" in html


def test_the_properties_are_scoped_to_the_fragment_not_the_document():
    # `compose.scope_css` rewrites root selectors to the placement's wrapper,
    # so two regions on one page must not overwrite each other's sizes.
    from homescreen import compose
    scoped = compose.scope_css(
        _style.page(764, 62, "<div></div>").split("<style>")[1].split("</style>")[0],
        "rg-x")
    assert "--pad" in scoped
    assert ":root{" not in scoped, "a bare :root would leak across regions"


def _extra_css(name, w, h):
    """One scene's own CSS, as it reaches the page."""
    import pathlib
    import tempfile
    from homescreen import scenes
    ctx = scenes.SceneContext(
        cfg={"location": {"lat": 40.4, "lon": -3.7, "name": "Madrid"}},
        cache_dir=pathlib.Path(tempfile.mkdtemp()),
        caps={"w": w, "h": h, "depth": 1}, now=1_788_000_000.0,
        device={"hw": "p", "id": "p"}, options=scenes.defaults(name))
    html = scenes.build(name, ctx).html or ""
    css = html.split("<style>")[-1].split("</style>")[0]
    # The SCENE's own CSS: everything after the shared block and the sizing
    # rule `page()` writes. Taking the whole <style> would compare BASE_CSS
    # against itself, which is how the first version of the collision test
    # accused `calendar` of redefining a class it never mentions.
    from homescreen.scenes import _style
    css = css.replace(_style.BASE_CSS, "")
    return css.split("}", 1)[1] if "html,body{width:" in css else css


def test_no_scene_hardcodes_the_full_panel_padding():
    # The specific value that emptied the markets band.
    from homescreen import scenes
    for name in scenes.names():
        css = _extra_css(name, *MARKETS)
        assert "padding:18px" not in css, name


def test_no_scene_sets_type_a_shallow_band_cannot_hold():
    from homescreen import scenes
    for name in scenes.names():
        css = _extra_css(name, *MARKETS)
        for size in re.findall(r"font-size:(\d+)px", css):
            assert int(size) <= 62, (name, size)


def _visible_text(name, w, h):
    import pathlib
    import tempfile
    from homescreen import scenes
    ctx = scenes.SceneContext(
        cfg={"location": {"lat": 40.4, "lon": -3.7, "name": "Madrid"}},
        cache_dir=pathlib.Path(tempfile.mkdtemp()),
        caps={"w": w, "h": h, "depth": 1}, now=1_788_000_000.0,
        device={"hw": "p", "id": "p"}, options=scenes.defaults(name))
    html = scenes.build(name, ctx).html or ""
    body = html.split("</style>")[-1]
    return " ".join(re.sub(r"<[^>]+>", " ", body).split())


#: What each component looks like with its key setting deliberately CLEARED,
#: which is different from unset: defaults fill an unset option, so only this
#: reaches the branch where a component has nothing to show.
CLEARED = {"quotes": {"symbols": ""}, "calendar": {"url": ""},
           "sport": {"team": ""}}


def test_no_region_renders_completely_blank_with_a_setting_cleared():
    from homescreen import scenes
    for name in scenes.names():
        if name == "blank":
            continue
        options = scenes.clean_options(name, CLEARED.get(name, {}))
        for w, h in (COLUMN, MARKETS):
            import pathlib
            import tempfile
            ctx = scenes.SceneContext(
                cfg={"location": {"lat": 40.4, "lon": -3.7, "name": "Madrid"}},
                cache_dir=pathlib.Path(tempfile.mkdtemp()),
                caps={"w": w, "h": h, "depth": 1}, now=1_788_000_000.0,
                device={"hw": "p", "id": "p"}, options=options)
            html = scenes.build(name, ctx).html or ""
            body = html.split("</style>")[-1]
            text = " ".join(re.sub(r"<[^>]+>", " ", body).split())
            assert len(text.strip(" -\u2014\u00b7")) > 1, (
                f"{name} at {w}x{h} says nothing usable: {text!r}")


def test_no_region_renders_completely_blank_with_no_data():
    # `calendar` with no URL emitted `<table></table>` -- a 417x335 hole in the
    # dashboard with nothing to say why, while the SAME component on the round
    # panel said "sin calendario". A panel that cannot explain itself is the
    # one failure this project keeps deciding is unacceptable.
    from homescreen import scenes
    for name in scenes.names():
        if name == "blank":
            continue                     # the one component that means silence
        for w, h in (COLUMN, (321, 335), MARKETS):
            got = _visible_text(name, w, h)
            assert got, f"{name} at {w}x{h} renders nothing at all"


# --- the shared classes are shared --------------------------------------------

def test_no_scene_redefines_a_shared_class():
    # `BASE_CSS` defines `.lab` as the SECTION LABEL tier: 10px, 500, .14em
    # tracking, uppercase. `weather` and `claude` each redefined `.lab` to set
    # a size -- and inherited the tracking and the uppercase, so a place name
    # rendered as "M A D R I D" in 16px caps where the design has an 8px label
    # the eye skips over. The scene CSS is concatenated after the shared CSS,
    # so a name collision is a silent partial override.
    import re

    from homescreen import scenes
    from homescreen.scenes import _style
    shared = set(re.findall(r"^\.([\w-]+)\{", _style.BASE_CSS, re.M))
    assert shared, "BASE_CSS defines no classes; this test would pass vacuously"
    for name in scenes.names():
        css = _extra_css(name, *COLUMN)
        for token in re.findall(r"^\.([\w-]+)\{", css, re.M):
            assert token not in shared, (
                f"{name} redefines the shared .{token}; give it its own name")


# --- the hero is sized for the SHAPE, not the region --------------------------

def test_a_panel_hero_leaves_room_for_what_sits_under_it():
    # `hero = inner * 0.55` assumes the region holds one line. A `panel` holds
    # three stacked blocks -- current conditions, an hourly strip, a list of
    # days -- and a 56px number in a 335px column ate 42px to say one thing,
    # next to a 28px icon that then looked like a mistake.
    tall = _style.metrics(321, 335, shape="panel")
    assert 30 <= tall["hero"] <= 40, tall


def test_a_card_hero_is_bigger_than_a_panels_because_it_stands_alone():
    card = _style.metrics(417, 168, shape="card")
    panel = _style.metrics(321, 335, shape="panel")
    assert card["hero"] > panel["hero"], (card, panel)


def test_a_strip_has_no_hero_at_all():
    # One line along a band: a headline would be the line.
    strip = _style.metrics(764, 62, shape="strip")
    assert strip["hero"] <= strip["lg"] + 2, strip


def test_an_unnamed_shape_keeps_the_old_behaviour():
    # Every caller that does not name a shape must get exactly what it got
    # before, or this change is a silent redesign of eight components.
    for w, h in (FULL, MASTHEAD, MARKETS, COLUMN):
        assert _style.metrics(w, h) == _style.metrics(w, h, shape=None)
    assert _style.metrics(*FULL)["hero"] == 56


def test_the_declared_tiers_still_fit_the_region():
    for shape in ("strip", "badge", "card", "panel"):
        for w, h in (FULL, MASTHEAD, MARKETS, COLUMN, (417, 168)):
            got = _style.metrics(w, h, shape=shape)
            assert got["pad"] * 2 + got["hero"] <= h, (shape, w, h, got)
            assert got["fs"] >= 10, (shape, w, h, got)
