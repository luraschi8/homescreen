"""The cadence a device is told to poll at, and who decides it.

The device asks once and is told when to come back. Before this, that answer
came only from the panel's hardware -- so a clock and a radar on the same glass
polled identically, and the clock spent twelve requests a minute to catch one
change that it still got up to a poll late.
"""
import time

import pytest

from homescreen import registry, scenes


LCD = {"caps": {"depth": 16, "w": 240, "h": 240}}
EPD = {"caps": {"depth": 1, "w": 800, "h": 480}}


def test_a_scene_cadence_beats_the_hardware_default():
    # The hardware default is about the glass; the scene knows the content.
    assert registry.poll_seconds(LCD) == registry.DEFAULT_POLL_SECONDS
    assert registry.poll_seconds(LCD, scene_poll_s=47) == 47


def test_an_operator_setting_still_beats_the_scene():
    # Whoever is holding the device wins; that is the whole point of the field.
    rec = {**LCD, "poll_seconds": 12}
    assert registry.poll_seconds(rec, scene_poll_s=47) == 12


def test_a_scene_cannot_drive_the_epaper_render_queue_faster_than_it_renders():
    # A 1-bit panel takes ~3s to refresh and every frame is a Chromium render
    # with two slots for the whole fleet. A clock showing seconds asks for 1.
    assert registry.poll_seconds(EPD, scene_poll_s=1) == registry.EPAPER_POLL_SECONDS
    # ...but a LONGER cadence than the floor is always honoured.
    assert registry.poll_seconds(EPD, scene_poll_s=47) == 47


def test_the_lcd_has_no_floor_because_it_only_parses_json():
    assert registry.poll_seconds(LCD, scene_poll_s=1) == 1


@pytest.mark.parametrize("bad", [None, "soon", object(), True, False])
def test_a_scene_asking_nonsense_falls_back_rather_than_500s(bad):
    # This runs on the device's only route. A scene is the most casually edited
    # code here, and a TypeError would take the panel down, not the scene.
    assert registry.poll_seconds(LCD, scene_poll_s=bad) == registry.DEFAULT_POLL_SECONDS


def test_a_scene_asking_outside_the_bounds_is_clamped_not_ignored():
    # Clamped, never dropped: a device that stops polling cannot be recovered
    # from the dashboard, so "polls oddly" must be the worst failure available.
    assert scenes.clean_poll_s(0) == scenes.POLL_MIN_S
    assert scenes.clean_poll_s(10 ** 9) == scenes.POLL_MAX_S


def test_the_clock_asks_to_be_woken_on_the_minute_boundary():
    # The property that removes the drift: the device wakes when the picture
    # changes, not on a grid that happens to straddle it.
    cfg = _cfg()
    for second in (0, 1, 23, 59):
        base = _at_second(second)
        scene = scenes.build("clock", _ctx(cfg, base))
        assert scene.poll_s == 60 - second or (second == 0 and scene.poll_s == 60)
        # Landing exactly on the next boundary is the whole claim.
        assert (base + scene.poll_s) % 60 == 0


def test_a_clock_showing_seconds_asks_for_every_second():
    # There is no boundary to aim at when every second is a change.
    scene = scenes.build("clock", _ctx(_cfg(), _at_second(23),
                                       options={"show_seconds": True}))
    assert scene.poll_s == 1


def test_the_radar_asks_for_more_than_the_clock_does():
    # The user's framing: "a clock should poll at worst every minute and the
    # radar probably more often." This is that sentence as an assertion.
    cfg = _cfg()
    clock = scenes.build("clock", _ctx(cfg, _at_second(0)))
    radar = scenes.build("planes", _ctx(cfg, _at_second(0)))
    assert radar.poll_s < clock.poll_s


def _at_second(second: int) -> int:
    """A timestamp whose seconds-past-the-minute are exactly `second`."""
    base = int(time.time()) // 60 * 60
    return base + second


def _cfg():
    return {"location": {"lat": 40.4, "lon": -3.7, "label": "Madrid"},
            "timezone": "Europe/Madrid"}


def _ctx(cfg, now, options=None, tmp=None):
    import pathlib
    import tempfile
    return scenes.SceneContext(
        cfg=cfg, cache_dir=pathlib.Path(tempfile.mkdtemp()),
        caps={"w": 240, "h": 240, "depth": 16, "max_items": 40},
        now=now, device={}, options=options or {})
