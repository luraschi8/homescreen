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


# --- the ordering hazard, ended rather than worked around ---------------------
#
# `variant_for` returns the first match and the constraints were all MINIMUMS,
# so a permissive entry matched every larger glass and shadowed a stricter one.
# The reachability test above is an EXISTENCE check: it caught the case where a
# shape was unreachable everywhere, and missed partial shadowing -- `strip`
# swallowed 600x140, 800x200 and 417x104, blocks with room for a list, while
# `card` stayed reachable at 417x121 and the test stayed green.

def _grid():
    return [{"w": w, "h": h, "depth": 1}
            for w in range(24, 801, 8) for h in range(24, 481, 8)]


def test_no_geometry_matches_two_shapes():
    # With disjoint entries, first-match-wins is a no-op and the hazard is
    # gone -- rather than guarded by a comment telling the next person to
    # order carefully.
    from homescreen import surface as _surface
    for name in scenes.names():
        declared = [s for s in scenes.surfaces(name) if s.get("variant")]
        if len(declared) < 2:
            continue
        for caps in _grid():
            screen = _surface.describe(caps)
            hit = [s["variant"] for s in declared
                   if _surface.fits(screen, **{k: v for k, v in s.items()
                                               if k != "variant" and k != "at"})]
            assert len(hit) <= 1, (name, caps, hit)


def test_every_shape_is_reached_at_the_size_it_was_written_for():
    # An entry states the rectangle it was designed for, so "designed for"
    # becomes an executable claim. Reachability somewhere is not the same as
    # reachability WHERE IT MATTERS.
    for name in scenes.names():
        for spec in scenes.surfaces(name):
            at = spec.get("at")
            if not at:
                continue
            caps = {"w": at[0], "h": at[1], "depth": 1}
            got = scenes.variant_for(name, caps)
            assert got == spec["variant"], (name, at, spec["variant"], got)


def test_a_misspelled_constraint_is_refused_rather_than_ignored():
    # `except TypeError: continue` swallows an entry naming a key `fits` does
    # not have, so it silently never matches. A shape that can never be
    # reached is dead code nobody notices.
    from homescreen import surface as _surface
    import inspect
    allowed = set(inspect.signature(_surface.fits).parameters) | {"variant", "at"}
    for name in scenes.names():
        for spec in scenes.surfaces(name):
            unknown = set(spec) - allowed
            assert not unknown, f"{name} declares {unknown}"


def test_an_undeclared_geometry_does_not_invent_a_shape():
    # Falling back to a global default handed a component a shape it never
    # declared: `quotes` at 127x62 reported "panel". Harmless only while
    # nothing branches on it, and branching on it is the whole point.
    for name in scenes.names():
        declared = {s.get("variant") for s in scenes.surfaces(name)
                    if s.get("variant")}
        if not declared:
            continue
        for caps in ({"w": 127, "h": 62, "depth": 1}, {"w": 800, "h": 480}):
            got = scenes.variant_for(name, caps)
            assert got is None or got in declared, (name, caps, got)


# --- every component, not just the first one ---------------------------------

def test_every_component_declares_its_shapes():
    # `weather` was the only one of nine reading `ctx.variant`; the rest
    # declared unnamed surfaces, so `clock` at 800x53 reported "panel" and the
    # mechanism reached one component out of nine.
    unnamed = [name for name in scenes.names()
               if scenes.surfaces(name)
               and not any(s.get("variant") for s in scenes.surfaces(name))]
    assert not unnamed, f"still shapeless: {unnamed}"


def test_the_dashboards_own_slots_get_sensible_shapes():
    # The golden map. This is the test that fails when a layout regresses,
    # which is the thing that actually matters -- reachability somewhere is
    # not the same as the right answer HERE.
    from homescreen import layout
    caps = {"w": 800, "h": 480, "depth": 1}
    regions = layout.regions(caps, "dashboard")
    want = {
        ("masthead", 1): "strip",     # 800x53
        ("main_left", 2): "card",     # 417x167
        ("main_right", 1): "panel",   # 321x335
        ("markets", 6): "badge",      # 127x62
        ("markets", 1): "strip",      # 764x62
    }
    for (region, count), shape in want.items():
        x, y, w, h = layout.slots(regions[region], count)[0]
        for name in ("weather", "quotes", "calendar", "sport"):
            got = scenes.variant_for(name, {**caps, "w": w, "h": h})
            assert got in (shape, None), (name, region, count, w, h, got)
