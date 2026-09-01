"""The instruction vocabulary and its resolver.

The resolver is reimplemented in C++ in the firmware, so everything here is
also a specification of what that code must do. Anything clever added on this
side is a thing to get wrong twice.
"""
import pytest

import json
import tempfile
import re

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


def test_every_tone_renders_as_its_own_colour():
    # The text is held CONSTANT and only the tone varies. The old version of
    # this compared "up" against "down", so the two SVGs differed on the words
    # and the whole palette could be flattened to white with the suite green.
    seen = {}
    for tone in draw.TONES:
        svg = draw.to_svg([draw.text("center", "x", tone=tone)], 240, 240)
        colour = re.search(r'fill="(#[0-9a-fA-F]{6})"', svg).group(1)
        assert colour not in seen, f"{tone} renders the same as {seen.get(colour)}"
        seen[colour] = tone
    assert len(seen) == len(draw.TONES)


def test_a_tone_reaches_a_shape_and_not_only_text():
    # Shapes take their colour through a different branch of `to_svg`.
    first = draw.to_svg([draw.circle(0.5, 0.5, 0.2, "good")], 240, 240)
    second = draw.to_svg([draw.circle(0.5, 0.5, 0.2, "bad")], 240, 240)
    assert first != second, "a shape's tone must reach its fill"


def test_bad_is_lighter_than_dim_so_it_reads_as_urgent():
    # Red's luma coefficient is 0.2126, so a pure red lands at the same
    # luminance as `dim` -- the tone that must jump out reading as the tone
    # that means ignore me. This pins the fix, not the taste.
    def luma(svg):
        hexed = re.search(r'fill="#([0-9a-fA-F]{6})"', svg).group(1)
        r, g, b = (int(hexed[i:i + 2], 16) for i in (0, 2, 4))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    bad = luma(draw.to_svg([draw.text("center", "x", tone="bad")], 240, 240))
    dim = luma(draw.to_svg([draw.text("center", "x", tone="dim")], 240, 240))
    assert bad > dim * 1.2, f"bad ({bad:.0f}) must out-read dim ({dim:.0f})"


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
    # The middle dot is a separator, not a dash: mapping it to "-" put a minus
    # sign immediately before a number.
    assert instruction["v"] == "manana   21°"
    assert "manana   21°" in draw.to_svg([instruction], 240, 240)


# --- the wire has to fit the device that reads it ---------------------------
#
# `scene_client.cpp` holds the draw array in a fixed buffer and REFUSES a list
# that does not fit, which is the right failure and an invisible one: the panel
# draws SIN ASIGNAR and nothing says why. A clear-sky weather scene grew to 942
# bytes against a 768-byte buffer and did exactly that on real glass.

#: Must equal `kMaxDrawBytes` in firmware/src/services/scene_client.cpp.
DEVICE_DRAW_BYTES = 4096


def _wire(instructions):
    return len(json.dumps(instructions, separators=(",", ":")))


def test_the_firmware_buffer_this_is_measured_against_is_the_real_one():
    # Reads the constant out of the C++ rather than trusting a copy of it: two
    # numbers that must agree, with only a comment holding them together, is
    # how they came to disagree in the first place.
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "firmware" / "src" / "services" / "scene_client.cpp").read_text()
    found = re.search(r"constexpr size_t kMaxDrawBytes = (\d+);", src)
    assert found, "kMaxDrawBytes disappeared from the firmware"
    assert int(found.group(1)) == DEVICE_DRAW_BYTES


def test_every_scene_fits_the_device_buffer_on_the_smallest_panel():
    # The round panel is the one with a fixed buffer. Built for real, not with
    # a synthetic list, so a component that grows an icon is caught here.
    from homescreen import scenes
    caps = {"w": 240, "h": 240, "depth": 16, "shape": "round",
            "components": ["radar", "draw_list"], "max_items": 40}
    for name in scenes.names():
        ctx = scenes.SceneContext(
            cfg={"location": {"lat": 40.4, "lon": -3.7, "name": "Madrid"},
                 "feeds": {"adsb": {"endpoint": "https://x"}}},
            cache_dir=pathlib.Path(tempfile.mkdtemp()), caps=caps,
            now=1_787_000_000.0, device={"hw": "aa", "id": "aa"},
            options=scenes.defaults(name))
        for component in scenes.build(name, ctx).components or ():
            size = _wire(component.get("draw") or [])
            assert size < DEVICE_DRAW_BYTES, (
                f"{name} sends {size} B, buffer is {DEVICE_DRAW_BYTES}")


def test_a_full_instruction_list_still_fits():
    # The ceiling, not the typical case: MAX_INSTRUCTIONS of the widest
    # instruction the vocabulary has. If this ever exceeds the buffer the cap
    # is in the wrong place, and the panel will refuse a list the server
    # considers legal.
    worst = [draw.line(0.1234, 0.1234, 0.9876, 0.9876, "accent", 0.0123)
             for _ in range(draw.MAX_INSTRUCTIONS)]
    assert _wire(worst) < DEVICE_DRAW_BYTES, _wire(worst)


# --- how wide a slot actually is --------------------------------------------
#
# `ROUND_USABLE = 0.72` was one constant standing in for a function. On a round
# panel the usable width is a CHORD: nearly the full diameter across the middle
# and much less at the rim, so a single ratio is simultaneously too generous at
# the top and too mean in the centre.

def test_a_round_panel_is_widest_across_its_middle():
    centre = draw.slot_width("center", 240, 240, "round", 62)
    rim = draw.slot_width("rim_bottom", 240, 240, "round", 13)
    assert centre > rim * 1.4, (centre, rim)
    assert centre <= 240, "still cannot exceed the glass"


def test_the_rim_slots_are_symmetric():
    top = draw.slot_width("rim_top", 240, 240, "round", 13)
    bottom = draw.slot_width("rim_bottom", 240, 240, "round", 13)
    assert top == bottom


def test_taller_text_is_narrower_at_the_rim():
    # Text is centred on the slot, so it extends above and below it. Near the
    # rim the far edge is what runs out of glass first.
    small = draw.slot_width("rim_bottom", 240, 240, "round", 12)
    large = draw.slot_width("rim_bottom", 240, 240, "round", 40)
    assert large < small, (small, large)


def test_a_rectangular_panel_is_the_same_width_everywhere():
    widths = {draw.slot_width(slot, 800, 480, "rect", 20) for slot in draw.SLOTS}
    assert len(widths) == 1, widths


def test_the_chord_never_exceeds_the_glass_on_any_slot_or_size():
    for slot in draw.SLOTS:
        for px in (10, 30, 62, 120):
            got = draw.slot_width(slot, 240, 240, "round", px)
            assert 0 <= got <= 240, (slot, px, got)


def test_a_slot_with_no_room_left_says_zero_rather_than_a_negative():
    # 120px of type centred on the rim reaches past the edge of the glass.
    # There is no width to be had, and the honest answer is none.
    assert draw.slot_width("rim_top", 240, 240, "round", 120) == 0
    assert draw.slot_width("center", 240, 240, "round", 30) > 0


# --- nothing runs off the glass ---------------------------------------------

def test_a_line_too_long_for_its_slot_is_truncated_not_clipped():
    long = "seguimiento del proyecto de la casa"
    got = draw.clip(long, "rim_bottom", "xs", 240, 240, "round")
    assert got != long
    assert got.endswith("...")
    assert len(got) < len(long)


def test_a_line_that_fits_is_left_exactly_alone():
    assert draw.clip("22:53", "center", "xl", 240, 240, "round") == "22:53"


def test_truncation_uses_glyphs_the_panel_actually_has():
    # The embedded face is 95 glyphs and has no ellipsis character.
    got = draw.clip("x" * 200, "rim_bottom", "sm", 240, 240, "round")
    assert "…" not in got
    assert all(ord(c) <= 0x7E or c == "°" for c in got), got


def test_clipping_never_returns_nothing():
    # A slot too narrow for any text must still say something rather than
    # going blank: "..." is a better answer than an empty panel.
    got = draw.clip("something", "rim_bottom", "xl", 240, 240, "round")
    assert got, "never empty"


# --- a preview of a 1-bit panel is black on white -----------------------------

def test_a_one_bit_preview_is_ink_on_paper_not_a_negative():
    # `to_svg` always painted a black ground and coloured tones -- the round
    # OLED's palette. Shown for an 800x480 e-paper it is a colour negative of
    # a panel that has no colours: CLAUDE.md allows #000000 and #ffffff there
    # and nothing else.
    svg = draw.to_svg([draw.text("center", "22:53", "xl"),
                       draw.text("below", "Madrid", "sm", "dim"),
                       draw.circle(0.5, 0.2, 0.1, "warn")],
                      800, 480, round_panel=False, depth=1)
    colours = set(re.findall(r'fill="(#[0-9a-fA-F]{3,6})"', svg))
    colours |= set(re.findall(r'stroke="(#[0-9a-fA-F]{3,6})"', svg))
    assert colours <= {"#000", "#fff", "#000000", "#ffffff"}, colours
    assert any(c in colours for c in ("#fff", "#ffffff")), "paper"
    assert any(c in colours for c in ("#000", "#000000")), "ink"


def test_a_colour_panel_still_gets_its_palette():
    svg = draw.to_svg([draw.text("center", "x", "md", "warn")], 240, 240,
                      depth=16)
    assert "#ffd23f" in svg


def test_one_bit_keeps_hierarchy_without_grey():
    # There are no greys to dim with, so `dim` must still be legible rather
    # than vanishing into the paper.
    svg = draw.to_svg([draw.text("center", "x", "md", "dim")], 800, 480,
                      round_panel=False, depth=1)
    assert "#6f6d6f" not in svg
    assert 'fill="#000"' in svg or 'fill="#000000"' in svg
