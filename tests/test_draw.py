"""The instruction vocabulary and its resolver.

The resolver is reimplemented in C++ in the firmware, so everything here is
also a specification of what that code must do. Anything clever added on this
side is a thing to get wrong twice.
"""
import pytest

from homescreen import draw


# --- resolving --------------------------------------------------------------

def test_slots_are_ordered_top_to_bottom():
    # An operator reads a screen top to bottom; if the resolver disagreed with
    # the slot names, a component author would place things by trial and error.
    ys = [draw.slot_y(s, 240) for s in
          ("rim_top", "above", "center", "below", "rim_bottom")]
    assert ys == sorted(ys)
    assert ys[0] > 0 and ys[-1] < 240, "nothing sits off the panel"


def test_sizes_scale_with_the_short_side():
    # The same component has to land on a 240x240 circle and an 800x480
    # rectangle. Scaling by the SHORT side is what stops xl text overflowing a
    # narrow panel.
    assert draw.size_px("xl", 240, 240) < draw.size_px("xl", 800, 480)
    assert draw.size_px("xl", 800, 480) == draw.size_px("xl", 480, 480), \
        "a wide panel must not make type taller than its height allows"


def test_type_never_goes_below_the_legibility_floor():
    # CLAUDE.md puts the floor at 10px. A tiny panel must clamp, not shrink.
    assert draw.size_px("xs", 40, 40) == draw.MIN_TEXT_PX


def test_text_is_centred_horizontally():
    placed = draw.resolve([draw.text("center", "22:53", "xl")], 240, 240)
    assert placed[0]["x"] == 120


def test_an_unknown_slot_or_size_falls_back_rather_than_vanishing():
    # A component from a newer server must degrade to something visible, not to
    # a blank screen.
    placed = draw.resolve(
        [{"t": "text", "slot": "nowhere", "v": "x", "size": "enormous"}],
        240, 240)
    assert len(placed) == 1
    assert placed[0]["y"] == draw.slot_y("center", 240)
    assert placed[0]["px"] == draw.size_px("md", 240, 240)


def test_an_unknown_instruction_is_dropped_not_guessed_at():
    # A device that cannot draw an instruction and a preview that invents one
    # are the same bug seen from two sides.
    placed = draw.resolve([{"t": "hologram", "v": "x"},
                           draw.text("center", "ok")], 240, 240)
    assert [p["text"] for p in placed] == ["ok"]


@pytest.mark.parametrize("bad", [None, "", 5, [], {}, {"t": "text"},
                                 {"t": "text", "v": ""},
                                 {"t": "text", "v": 5}])
def test_malformed_instructions_never_raise(bad):
    # This runs on the serve path. A 500 here is a device with no screen.
    assert draw.resolve([bad], 240, 240) == []


def test_an_unknown_tone_becomes_normal():
    placed = draw.resolve([draw.text("center", "x", tone="chartreuse")], 240, 240)
    assert placed[0]["tone"] == "normal"


# --- the preview ------------------------------------------------------------

def test_the_preview_carries_every_string_the_device_would_draw():
    svg = draw.to_svg([draw.text("center", "22:53", "xl"),
                       draw.text("below", "Madrid", "sm", "dim")], 240, 240)
    assert "22:53" in svg and "Madrid" in svg


def test_the_preview_places_text_where_the_resolver_says():
    # The preview must not do its own layout, or it is a different program's
    # opinion of the same data.
    items = [draw.text("center", "22:53", "xl")]
    placed = draw.resolve(items, 240, 240)[0]
    svg = draw.to_svg(items, 240, 240)
    assert f'x="{placed["x"]}"' in svg and f'y="{placed["y"]}"' in svg
    assert f'font-size="{placed["px"]}"' in svg


def test_a_round_panel_is_drawn_round_and_a_rectangle_is_not():
    assert "<circle" in draw.to_svg([], 240, 240, round_panel=True)
    assert "<circle" not in draw.to_svg([], 800, 480, round_panel=False)


def test_the_preview_needs_no_external_resource():
    # Same constraint the scenes live under: no CDN, no font fetch. A preview
    # that needs the internet is useless on the LAN this thing lives on.
    # An actual FETCH, not any URL: xmlns="http://www.w3.org/2000/svg" is a
    # namespace identifier that nothing dereferences, and asserting on
    # "http://" would flag it. Check for things a renderer would go and get.
    svg = draw.to_svg([draw.text("center", "x")], 240, 240)
    for bad in ('<script', '@import', 'url(', 'src="http', 'href="http',
                '<image', 'xlink:href'):
        assert bad not in svg, f"the preview must not fetch {bad!r}"


def test_hostile_text_cannot_break_out_of_the_preview():
    # Component text can carry a device name or an upstream callsign.
    svg = draw.to_svg([draw.text("center", '</text><script>alert(1)</script>')],
                      240, 240)
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg


def test_tones_are_visually_distinct():
    good = draw.to_svg([draw.text("center", "up", tone="good")], 240, 240)
    bad = draw.to_svg([draw.text("center", "down", tone="bad")], 240, 240)
    assert good != bad, "good and bad must not render identically"


# --- the two resolvers must round identically -------------------------------

def test_ties_round_away_from_zero_like_c_does():
    # Python's round() is banker's rounding and C's roundf() is not, so a 241px
    # panel put the centre slot exactly on a tie and the two resolvers disagreed
    # by a pixel. The parity fixture caught it on its first run; this pins the
    # rule so nobody "simplifies" it back to round().
    assert draw.slot_y("center", 241) == 121, "0.50 * 241 = 120.5 -> 121"
    assert round(0.50 * 241) == 120, "...which built-in round() would give"


@pytest.mark.parametrize("h", range(200, 260))
def test_no_panel_height_makes_the_two_resolvers_disagree(h):
    # Every fraction times every plausible height, checked against the same
    # arithmetic the C++ does: floor(x + 0.5) on positives.
    import math
    for slot, frac in draw.SLOTS.items():
        assert draw.slot_y(slot, h) == math.floor(frac * h + 0.5)
    for size, frac in draw.SIZES.items():
        assert draw.size_px(size, h, h) == max(
            draw.MIN_TEXT_PX, math.floor(frac * h + 0.5))


# --- what the panel's font can actually draw --------------------------------
#
# Found by inspecting the embedded face rather than by looking at the glass:
# it holds 95 glyphs and none of them are accented letters, arrows, middle
# dots or em dashes. Every Spanish string and half the punctuation a component
# reaches for would have rendered as a blank box. The clock never caught it
# because "Madrid" and "Buenos Aires" are ASCII.

import pathlib
import struct


def _font_codepoints():
    """Every glyph the embedded VLW actually carries."""
    blob = pathlib.Path("firmware/data/ui_font.vlw").read_bytes()
    count = struct.unpack(">i", blob[0:4])[0]
    out, offset = set(), 24
    for _ in range(count):
        out.add(struct.unpack(">i", blob[offset:offset + 4])[0])
        offset += 28
    return out


def test_every_substitution_lands_on_a_glyph_the_font_has():
    # Pinned against the FONT FILE, so swapping the embedded face fails here
    # rather than on the panel.
    have = _font_codepoints()
    for source, replacement in draw.DEVICE_SUBSTITUTIONS.items():
        for char in replacement:
            assert ord(char) in have or char == " ", \
                f"{source!r} -> {replacement!r} is not drawable either"


def test_the_declared_range_matches_the_font():
    have = _font_codepoints()
    assert max(have) <= draw.DEVICE_MAX_CP
    assert min(have) >= draw.DEVICE_MIN_CP


@pytest.mark.parametrize("scene_name", [
    "clock", "weather", "quotes", "calendar", "sport", "claude", "planes",
])
def test_no_component_can_emit_a_character_the_panel_cannot_draw(scene_name):
    """The test that would have caught this.

    Every component, on the round panel, with no data -- which is the state
    that draws the most prose and therefore the most accented Spanish.
    """
    import tempfile
    from homescreen import scenes
    have = _font_codepoints() | {0x20}
    ctx = scenes.SceneContext(
        cfg={"location": {"lat": 40.4, "lon": -3.7, "name": "Madrid"}},
        cache_dir=pathlib.Path(tempfile.mkdtemp()),
        caps={"w": 240, "h": 240, "depth": 16, "shape": "round"},
        now=1_787_000_000.0, device={}, options=scenes.defaults(scene_name))
    for component in scenes.build(scene_name, ctx).components:
        for instruction in component.get("draw") or ():
            for char in instruction.get("v", ""):
                assert ord(char) in have, \
                    f"{scene_name} would draw {char!r}, which the font lacks"


def test_the_preview_shows_exactly_what_the_panel_will_show():
    # Substitution happens in `text()`, so the SVG preview runs the same
    # instruction list. If it happened later, the preview would promise an
    # accent the glass cannot keep -- the drift this design exists to prevent.
    instruction = draw.text("center", "mañana · 21°")
    assert instruction["v"] == "manana - 21°"
    assert "manana - 21°" in draw.to_svg([instruction], 240, 240)
