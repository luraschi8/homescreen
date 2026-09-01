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
    # DENSE. The sparse version stepped 127 -> 200 and declared `sport`'s
    # badge unreachable when it lives at 150-199 wide -- a sampling gap
    # reported as a defect. A few thousand geometries cost milliseconds.
    grid = _grid() + [ROUND]
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


def test_nothing_overflows_a_markets_cell():
    # 116x62 is the narrowest cell SPEC §9's band produces. A component that
    # wraps there pushes itself out of the band, and `overflow:hidden` eats
    # whatever crossed the line -- silently.
    import pathlib
    import re
    import tempfile
    cell = {"w": 116, "h": 62, "depth": 1}
    for name in scenes.names():
        if scenes.variant_for(name, cell) is None:
            continue
        ctx = scenes.SceneContext(
            cfg={"location": {"lat": 40.4, "lon": -3.7, "name": "Madrid"}},
            cache_dir=pathlib.Path(tempfile.mkdtemp()), caps=cell,
            now=1_788_000_000.0, device={"hw": "p", "id": "p"},
            options=scenes.defaults(name))
        html = scenes.build(name, ctx).html or ""
        ladder = {t: int(v) for t, v in
                  re.findall(r"(--[\w-]+):(\d+)px", html)}
        body = html.split("</style>")[-1]
        text = " ".join(re.sub(r"<[^>]+>", " ", body).split())
        # Per rendered block, not per whitespace run: at ~0.58em a character,
        # a cell this wide holds roughly eighteen.
        for line in re.findall(r">([^<>]+)<", body):
            assert len(line.strip()) <= 20, (name, line.strip())
        for token, px in ladder.items():
            assert px >= 10 or token in ("--pad", "--pad-sm"), (name, token, px)


def test_no_component_refuses_a_screen_every_other_one_accepts():
    # `weather` alone returned None for every tall narrow geometry -- 26,510
    # of them -- because its panel required 200px of width to protect a
    # six-cell hourly strip. That is the strip's problem, not the shape's, and
    # it made the one component people most want on a portrait board the only
    # one that refused to draw on it.
    portrait = [{"w": w, "h": h, "depth": 1}
                for w in (100, 122, 135, 170, 199)
                for h in (240, 320, 400, 480)]
    for caps in portrait:
        refused = {name for name in scenes.names()
                   if scenes.variant_for(name, caps) is None}
        # `planes` is allowed to refuse: it draws GEOMETRY and needs room in
        # both directions, which is a real requirement rather than an
        # oversight. Nothing else may.
        assert refused <= {"planes"}, (caps, sorted(refused))


def test_the_fallback_can_be_drawn_on_anything():
    # `status` is what `safe_build` shows when something else broke. A
    # geometry it refuses is a screen that goes blank at exactly the moment it
    # needed to explain itself.
    for w in (24, 40, 60, 90, 127, 200, 417, 800):
        for h in (16, 24, 40, 62, 104, 240, 480):
            assert scenes.variant_for("status", {"w": w, "h": h}) is not None, \
                (w, h)


def test_the_hourly_strip_thins_out_rather_than_refusing_the_screen():
    import pathlib
    import re
    import tempfile
    from homescreen.reading import Reading
    day = 1_788_213_600
    reading = Reading(data={
        "temp": 24.0, "description": "cielo despejado", "place": "Madrid",
        "sky": "clear", "units": "metric", "tz_offset_s": 7200,
        "daily": [{"date": day + i * 86400, "min": 18.0, "max": 33.0,
                   "sky": "clear", "precip_pct": 0} for i in range(6)],
        "hourly": [{"time": day + i * 3600, "temp": 24, "sky": "clear"}
                   for i in range(24)]}, ok=True, age_s=60.0)
    counts = {}
    for width in (135, 170, 250, 321, 417):
        ctx = scenes.SceneContext(
            cfg={"location": {"lat": 40.4, "lon": -3.7, "name": "Madrid"}},
            cache_dir=pathlib.Path(tempfile.mkdtemp()),
            caps={"w": width, "h": 335, "depth": 1}, now=float(day),
            device={}, options=scenes.defaults("weather"),
            data=lambda req: reading)
        html = scenes.build("weather", ctx).html
        counts[width] = len(re.findall(r'<div class="hr">', html))
    assert counts[417] == 6, counts
    assert counts[135] < counts[321], counts
    assert all(c == 0 or c >= 2 for c in counts.values()), counts


def test_a_component_that_does_not_exist_cannot_be_drawn():
    # `surfaces()` swallows the KeyError and answers `()`, which reads as
    # "declares nothing, so anywhere" -- so a typo in a stored record came
    # back as a component that fits every screen.
    assert scenes.variant_for("nosuchcomponent", CELL) is None
    assert scenes.supports("nosuchcomponent", CELL)[0] is False


def test_a_device_that_has_not_said_what_it_is_still_gets_a_shape():
    # The branch the previous version of this test never reached: both its
    # cases declared geometry, so the undeclared path was never taken and
    # reverting the fix passed.
    for caps in ({}, {"w": 0, "h": 0}, None, {"depth": 1}):
        for name in scenes.names():
            got = scenes.variant_for(name, caps)
            declared = {s.get("variant") for s in scenes.surfaces(name)
                        if s.get("variant")}
            assert got in declared, (name, caps, got)


def test_the_shape_for_an_unmeasured_device_does_not_depend_on_writing_order():
    # It was `declared[0]`, so reordering entries -- legal, since they are
    # disjoint -- silently changed what a device with no capabilities was
    # drawn as.
    from homescreen.scenes import weather
    original = weather.SURFACES
    try:
        before = scenes.variant_for("weather", {})
        weather.SURFACES = tuple(reversed(original))
        assert scenes.variant_for("weather", {}) == before
    finally:
        weather.SURFACES = original
