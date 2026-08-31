"""A screen deliberately showing nothing.

There was no way to say "off at night". An unassigned screen shows the status
card -- correctly, because it has to explain itself -- so silence and
misconfiguration looked identical and the only way to darken a bedroom panel
was to unplug it.
"""
import pathlib
import tempfile

from homescreen import draw, scenes

ROUND = {"w": 240, "h": 240, "depth": 16, "shape": "round"}
EPAPER = {"w": 800, "h": 480, "depth": 1}


def ctx(caps, **options):
    return scenes.SceneContext(
        cfg={}, cache_dir=pathlib.Path(tempfile.mkdtemp()), caps=caps,
        now=1_787_000_000.0, device={}, options=options)


def test_it_paints_the_whole_panel_and_says_nothing():
    scene = scenes.build("blank", ctx(ROUND))
    drawn = scene.components[0]["draw"]
    assert drawn == [{"t": "fill", "tone": "off"}]
    assert not any(item.get("t") == "text" for item in drawn)


def test_a_blank_screen_is_not_an_empty_instruction_list():
    # The distinction the firmware depends on: an EMPTY list still means
    # "nothing came through" and draws the status card. A blank screen is a
    # positive instruction, so the two cannot be confused.
    assert scenes.build("blank", ctx(ROUND)).components[0]["draw"]


def test_the_colour_is_configurable():
    scene = scenes.build("blank", ctx(ROUND, tone="bad"))
    assert scene.components[0]["draw"][0]["tone"] == "bad"


def test_a_colour_the_vocabulary_does_not_have_falls_back_to_off():
    scene = scenes.build("blank", ctx(ROUND, tone="chartreuse"))
    assert scene.components[0]["draw"][0]["tone"] == "off"


def test_blank_on_e_paper_is_white_rather_than_black():
    # Physics, not taste: e-paper holds an image with no power, so a black
    # page costs nothing to keep but takes a full ~3s refresh to reach and
    # leaves the worst ghosting. No ink is the state it rests in.
    assert "background:#fff" in scenes.build("blank", ctx(EPAPER)).html
    assert "background:#000" in scenes.build("blank", ctx(ROUND)).html


def test_it_fits_every_screen_including_the_narrowest_cell():
    # The one component with no legibility floor, because there is nothing to
    # read. A markets cell is 127x62 and a blank one is a legitimate choice.
    for caps in (ROUND, EPAPER, {"w": 127, "h": 62, "depth": 1},
                 {"w": 64, "h": 32, "depth": 1}):
        assert scenes.supports("blank", caps)[0], caps


def test_it_asks_again_rarely_because_nothing_changes():
    scene = scenes.build("blank", ctx(ROUND))
    assert scene.poll_s >= 600, "a dark panel is not the busiest thing here"


def test_the_fill_reaches_the_preview_as_a_full_bleed_rectangle():
    svg = draw.to_svg([draw.fill("off")], 240, 240)
    assert '<rect x="0" y="0" width="240" height="240" fill="#000000"/>' in svg


def test_a_fill_covers_whatever_was_drawn_before_it():
    # It is a background as well as a blank screen, so order matters and both
    # resolvers draw in the order the server emitted.
    got = draw.resolve([draw.text("center", "gone", "md"), draw.fill("off")],
                       240, 240)
    assert [item["t"] for item in got] == ["text", "fill"]
