"""One component, several shapes.

The owner's steer: "the components should be configurable... they could have
different rendering options like widgets in the iphone screen and the size
determines what and how they show up."

The mechanism already half-existed and only reached the ROUND panel:
`weather` computed `wide_band`, `quotes` computed `stacked`, `calendar`
computed a row count -- and none of them reached the HTML, so the e-paper got
the 240x240 layout letterboxed into whatever rectangle it was given.

These are named SHAPES, not a size ladder. iOS widgets sit on a near-square
grid, so small/medium/large describes them; our slots run from 15:1 to 0.96:1,
and aspect decides the presentation while area decides the amount. `strip` is
not "smaller than `badge`" -- it is a different shape.
"""
import pytest

from homescreen import scenes

MASTHEAD = {"w": 800, "h": 53, "depth": 1}
BAND = {"w": 764, "h": 62, "depth": 1}
CELL = {"w": 127, "h": 62, "depth": 1}
COLUMN = {"w": 321, "h": 335, "depth": 1}
WIDE_COLUMN = {"w": 417, "h": 335, "depth": 1}
ROUND = {"w": 240, "h": 240, "depth": 16, "shape": "round"}


def test_a_component_reports_which_shape_it_would_use():
    assert scenes.variant_for("weather", COLUMN) == "panel"
    assert scenes.variant_for("weather", BAND) == "strip"
    assert scenes.variant_for("weather", CELL) == "badge"


def test_a_glass_it_cannot_serve_gets_no_variant():
    # The same answer `supports` gives, from the same declaration -- one
    # mechanism rather than two that can disagree.
    assert scenes.variant_for("planes", CELL) is None
    assert scenes.supports("planes", CELL)[0] is False


def test_supports_and_variant_for_never_disagree():
    for name in scenes.names():
        for caps in (MASTHEAD, BAND, CELL, COLUMN, WIDE_COLUMN, ROUND):
            fits = scenes.supports(name, caps)[0]
            assert fits == (scenes.variant_for(name, caps) is not None), \
                (name, caps)


def test_every_declared_shape_is_reachable():
    # The ordering guard. Entries are tried in order and the first match wins,
    # so a broad one written above a narrow one silently shadows it -- and the
    # shadowed shape is dead code nobody notices. Sampling a geometry grid
    # makes that fail the moment it is written rather than on the glass.
    grid = [{"w": w, "h": h, "depth": 1}
            for w in (60, 127, 200, 321, 417, 600, 764, 800)
            for h in (24, 40, 53, 62, 90, 140, 200, 335, 480)]
    grid.append(ROUND)
    for name in scenes.names():
        declared = {s.get("variant") for s in scenes.surfaces(name)
                    if s.get("variant")}
        if not declared:
            continue
        reached = {scenes.variant_for(name, caps) for caps in grid}
        missing = declared - reached
        assert not missing, f"{name} declares {missing} and never reaches them"


def test_the_shape_reaches_the_component():
    # What makes it useful: the component is TOLD by `build`, rather than each
    # one re-deriving it from `caps` and drifting apart. A bare context has no
    # component and so no shape -- `build` is what settles it.
    import pathlib
    import tempfile
    seen = {}

    def spy(ctx):
        seen["variant"] = ctx.variant
        return scenes.Scene(layout="fill")

    original = scenes._registry
    scenes._registry = lambda: {"spy": spy}
    try:
        ctx = scenes.SceneContext(
            cfg={}, cache_dir=pathlib.Path(tempfile.mkdtemp()), caps=BAND,
            now=1_788_000_000.0, device={}, options={})
        scenes.build("spy", ctx)
    finally:
        scenes._registry = original
    # `spy` declares no surface, so it is drawn as the default everywhere --
    # which is what every component written before variants existed means.
    assert seen["variant"] == scenes.DEFAULT_VARIANT


def test_the_shape_a_real_component_is_given_matches_its_declaration():
    import pathlib
    import tempfile
    for caps, want in ((BAND, "strip"), (CELL, "badge"), (COLUMN, "panel")):
        ctx = scenes.SceneContext(
            cfg={"location": {"lat": 40.4, "lon": -3.7, "name": "Madrid"}},
            cache_dir=pathlib.Path(tempfile.mkdtemp()), caps=caps,
            now=1_788_000_000.0, device={}, options=scenes.defaults("weather"))
        assert ctx.variant_of("weather") == want, (caps, want)


def test_the_shape_is_derived_not_trusted():
    # A caller cannot hand a component a shape its geometry contradicts.
    import pathlib
    import tempfile
    ctx = scenes.SceneContext(
        cfg={}, cache_dir=pathlib.Path(tempfile.mkdtemp()), caps=COLUMN,
        now=1_788_000_000.0, device={}, options={})
    assert ctx.variant_of("weather") == "panel"


def test_a_component_that_declares_nothing_still_works():
    # Declaring no surface means "anywhere", and that must not become "no
    # shape, therefore crash".
    ctx_variant = scenes.variant_for("blank", CELL)
    assert ctx_variant is not None
