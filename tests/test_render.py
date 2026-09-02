# tests/test_render.py
import subprocess
from pathlib import Path

import pytest

from homescreen import render

PAGE = """<!doctype html><meta charset="utf-8">
<style>*{margin:0;-webkit-font-smoothing:none}
html,body{width:%dpx;height:%dpx;background:#fff;color:#000;font-family:sans-serif}
</style><div style="font-size:40px">TEST</div>"""


def _page(w=800, h=480):
    return PAGE % (w, h)


@pytest.fixture(scope="module")
def chromium():
    binary = render.find_chromium()
    if binary is None:
        pytest.skip("no chromium/chrome on this machine")
    return binary


# --- the wire format --------------------------------------------------------

def test_a_frame_is_exactly_width_times_height_over_eight(chromium):
    # A device streams this straight at the panel. Any other length is a
    # corrupt screen, not an error it can detect.
    for w, h in ((800, 480), (240, 240)):
        assert len(render.render_frame(_page(w, h), w, h)) == w * h // 8


def test_black_is_one_on_the_wire(chromium, tmp_path):
    # Pillow's mode-1 tobytes() is 1 = WHITE; the panel is the opposite.
    # Serving raw tobytes() renders a photographic negative on every device.
    black = '<!doctype html><style>html,body{width:80px;height:80px;' \
            'background:#000;margin:0}</style>'
    white = '<!doctype html><style>html,body{width:80px;height:80px;' \
            'background:#fff;margin:0}</style>'
    assert render.render_frame(black, 80, 80) == b"\xff" * 800
    assert render.render_frame(white, 80, 80) == b"\x00" * 800


def test_the_most_significant_bit_is_the_leftmost_pixel(tmp_path):
    # Asymmetric on purpose: a checkerboard cannot detect a bit-order flip.
    from PIL import Image
    img = Image.new("1", (8, 2), 1)
    img.putpixel((0, 0), 0)          # black at the far left of row 0
    img.putpixel((7, 1), 0)          # black at the far right of row 1
    png = tmp_path / "a.png"
    img.save(png)
    packed = render.png_to_packed(png, 8, 2)
    assert packed == b"\x80\x01", packed.hex()


def test_the_threshold_is_160_not_128(tmp_path):
    # Thin antialiased strokes sit in the 129-160 band. On the Pi that band is
    # populated (measured 304 px), so this constant is load-bearing there.
    from PIL import Image
    img = Image.new("L", (8, 1), 255)
    for x, value in enumerate((0, 100, 129, 140, 160, 161, 200, 255)):
        img.putpixel((x, 0), value)
    png = tmp_path / "ramp.png"
    img.save(png)
    packed = render.png_to_packed(png, 8, 1)
    # 1 = black; everything <= 160 must be black.
    assert packed == bytes([0b11111000]), bin(packed[0])


def test_grey_fraction_measures_antialiasing(tmp_path):
    from PIL import Image
    img = Image.new("L", (10, 10), 255)
    for x in range(5):
        img.putpixel((x, 0), 128)     # 5 of 100 px are neither black nor white
    png = tmp_path / "g.png"
    img.save(png)
    assert render.grey_fraction(png) == pytest.approx(0.05)


# --- failure paths ----------------------------------------------------------

def test_no_chromium_anywhere_raises_rendererror(monkeypatch, cold_frame_cache):
    monkeypatch.setattr(render, "find_chromium", lambda: None)
    with pytest.raises(render.RenderError, match="no chromium"):
        render.render_frame(_page(), 800, 480)


def test_a_nonexistent_binary_raises_rendererror_not_oserror(tmp_path):
    # subprocess raises FileNotFoundError; a request handler must see one
    # exception type it knows how to turn into a 503.
    with pytest.raises(render.RenderError, match="failed to run"):
        render.html_to_png(_page(), 800, 480, tmp_path / "o.png",
                           binary="/nonexistent/chromium")


def test_a_binary_that_fails_raises_rendererror(tmp_path):
    # /usr/bin/false, not /bin/false: the latter does not exist on macOS, so
    # this test used to hit FileNotFoundError and merely duplicate the one
    # below -- leaving the branch it is named for uncovered on the dev machine.
    assert Path("/usr/bin/false").exists(), "expected a POSIX false(1)"
    with pytest.raises(render.RenderError):
        render.html_to_png(_page(), 800, 480, tmp_path / "o.png",
                           binary="/usr/bin/false")


def test_a_binary_that_succeeds_but_writes_nothing_raises_rendererror(tmp_path):
    # The guard this covers is the last thing standing between a failed render
    # and a 500: without it png_to_packed raises FileNotFoundError, which is
    # not a RenderError, so device_frame's handler misses it.
    with pytest.raises(render.RenderError, match="produced no image"):
        render.html_to_png(_page(), 800, 480, tmp_path / "o.png",
                           binary="/usr/bin/true")


@pytest.mark.parametrize("flag,why", [
    ("--hide-scrollbars", "a scrollbar steals ~15px and shifts the layout"),
    ("--default-background-color=FFFFFFFF",
     "a transparent backdrop thresholds to solid black"),
    ("--force-device-scale-factor=1", "Retina doubling breaks the geometry"),
    ("--headless", "there is no display on the Pi"),
])
def test_the_chromium_flags_that_carry_claude_md_invariants_are_present(flag, why):
    assert flag in render.CHROMIUM_FLAGS, why


def test_the_flags_actually_reach_the_subprocess(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        raise OSError("stop here")

    monkeypatch.setattr(render.subprocess, "run", fake_run)
    with pytest.raises(render.RenderError):
        render.html_to_png(_page(), 800, 480, tmp_path / "o.png",
                           binary="/usr/bin/true")
    for flag in render.CHROMIUM_FLAGS:
        assert flag in seen["cmd"]
    assert "--window-size=800,480" in seen["cmd"]


def test_a_render_timeout_raises_rendererror(tmp_path, monkeypatch):
    def hang(*a, **k):
        raise subprocess.TimeoutExpired(cmd="chromium", timeout=1)

    monkeypatch.setattr(render.subprocess, "run", hang)
    with pytest.raises(render.RenderError, match="failed to run"):
        render.html_to_png(_page(), 800, 480, tmp_path / "o.png",
                           binary="/bin/true")


def test_a_wrong_sized_png_is_refused(tmp_path):
    # A silently resized render would pack to the wrong length and corrupt a
    # panel; better to fail here.
    from PIL import Image
    png = tmp_path / "small.png"
    Image.new("L", (100, 100), 255).save(png)
    with pytest.raises(render.RenderError, match="expected 800x480"):
        render.png_to_packed(png, 800, 480)


def test_find_chromium_prefers_the_packaged_name(monkeypatch):
    calls = []

    def which(name):
        calls.append(name)
        return "/usr/bin/chromium" if name == "chromium" else None

    monkeypatch.setattr(render.shutil, "which", which)
    assert render.find_chromium() == "/usr/bin/chromium"
    assert calls[0] == "chromium", "trixie packages it under this name"


# --- the frame cache, which is what makes this endpoint affordable ----------

def test_identical_html_is_rendered_once(chromium, cold_frame_cache):
    # Without this, every poll forks a browser even when the response is a
    # 304: measured 17,280 spawns per device per day at poll_seconds=5, on a
    # Pi 4 where each costs ~200 MB and ~3 s.
    before = render.cache_stats()
    render.render_frame(_page(240, 240), 240, 240)
    render.render_frame(_page(240, 240), 240, 240)
    after = render.cache_stats()
    assert after["misses"] - before["misses"] == 1
    assert after["hits"] - before["hits"] == 1


def test_different_html_is_not_confused(chromium, cold_frame_cache):
    a = render.render_frame(_page(240, 240), 240, 240)
    b = render.render_frame(_page(240, 240).replace("TEST", "OTHER"), 240, 240)
    assert a != b, "the cache must key on content, not just geometry"


def test_the_same_html_at_a_different_geometry_is_not_confused(chromium, cold_frame_cache):
    a = render.render_frame(_page(240, 240), 240, 240)
    b = render.render_frame(_page(80, 80), 80, 80)
    assert len(a) != len(b)


def test_the_cache_is_bounded(cold_frame_cache, monkeypatch):
    # Stub the browser: this is testing eviction, and 21 real renders would
    # cost a minute for no extra confidence.
    monkeypatch.setattr(render, "html_to_png", lambda *a, **k: None)
    monkeypatch.setattr(render, "png_to_packed", lambda p, w, h: b"\x00" * (w * h // 8))
    for i in range(render._CACHE_MAX + 5):
        render.render_frame(f"<html>{i}</html>", 80, 80)
    assert render.cache_stats()["size"] == render._CACHE_MAX


def test_eviction_is_least_recently_used(cold_frame_cache, monkeypatch):
    monkeypatch.setattr(render, "html_to_png", lambda *a, **k: None)
    monkeypatch.setattr(render, "png_to_packed", lambda p, w, h: b"\x00" * (w * h // 8))
    for i in range(render._CACHE_MAX):
        render.render_frame(f"<html>{i}</html>", 80, 80)
    render.render_frame("<html>0</html>", 80, 80)          # touch the oldest
    hits = render.cache_stats()["hits"]
    render.render_frame("<html>new</html>", 80, 80)        # force one eviction
    render.render_frame("<html>0</html>", 80, 80)          # still resident
    assert render.cache_stats()["hits"] == hits + 1


# --- geometry bounds: a device declares its own, so these bound the device --

@pytest.mark.parametrize("w,h,why", [
    (4096, 4096, "2 MB frame -- accepted before this cap"),
    (2049, 8, "over the per-side limit"),
    (0, 480, "not a geometry"),
    (-8, 480, "negative"),
    (101, 101, "not byte-aligned; w*h/8 would truncate"),
])
def test_an_abusive_geometry_is_refused_before_any_work(w, h, why):
    with pytest.raises(render.RenderError):
        render.check_geometry(w, h)


@pytest.mark.parametrize("w,h", [(800, 480), (240, 240), (400, 300), (8, 8)])
def test_real_geometries_are_accepted(w, h):
    render.check_geometry(w, h)


# --- resource bounds ----------------------------------------------------------
# Every one of these survived mutation before: MAX_DIMENSION x10, MAX_FRAME_BYTES
# x1000, Semaphore(2)->Semaphore(64), _CACHE_MAX 16->4096 (up to 2 GB of cached
# frames on a 1.8 GB Pi), and the packed-length guard deleted outright. The old
# tests compared each constant to itself, so they moved with the mutation.

def test_the_render_bounds_are_the_numbers_we_reasoned_about():
    assert render.MAX_DIMENSION == 2048
    assert render.MAX_FRAME_BYTES == 512_000
    assert render._CACHE_MAX == 16
    assert render._RENDER_SLOTS._value <= 2
    assert render.RENDER_QUEUE_TIMEOUT_S == 20
    assert render.THRESHOLD == 160


def test_a_side_over_the_limit_is_refused_by_the_side_limit_alone():
    # 4096x8: byte-aligned, only 4,096 bytes packed, well under the byte cap.
    # Nothing in the suite isolated MAX_DIMENSION -- every "too big" case was
    # already caught by the mod-8 or byte rules, so raising it x10 changed
    # no test.
    assert 4096 * 8 // 8 < render.MAX_FRAME_BYTES and 4096 % 8 == 0
    with pytest.raises(render.RenderError, match="2048"):
        render.check_geometry(4096, 8)


def test_a_frame_over_the_byte_cap_is_refused_by_the_byte_cap_alone():
    # 2048x2048 = 524,288 B: both sides legal, byte-aligned, over the cap.
    # render.py's byte-cap raise never executed in the whole suite.
    assert 2048 <= render.MAX_DIMENSION and 2048 * 2048 // 8 > render.MAX_FRAME_BYTES
    with pytest.raises(render.RenderError, match="bytes"):
        render.check_geometry(2048, 2048)


def test_a_browser_that_returns_a_short_frame_is_never_served(monkeypatch):
    # The last thing standing between a device and a corrupt screen, and it
    # never executed: deleting it changed no test.
    monkeypatch.setattr(render, "html_to_png", lambda *a, **k: None)
    monkeypatch.setattr(render, "png_to_packed", lambda *a, **k: b"\x00" * 10)
    render.clear_cache()
    with pytest.raises(render.RenderError, match="expected"):
        render.render_frame("<p>x</p>", 800, 480)


def test_a_busy_queue_raises_rather_than_waiting_forever(monkeypatch):
    # The shedding path was entirely dead in tests.
    monkeypatch.setattr(render._RENDER_SLOTS, "acquire", lambda timeout=None: False)
    render.clear_cache()
    with pytest.raises(render.RenderBusy, match="busy"):
        render.render_frame("<p>unique-for-this-test</p>", 800, 480)


def test_the_cache_holds_exactly_the_stated_number_of_frames(monkeypatch):
    from PIL import Image

    def stub(html, w, h, out_png, binary=None):
        Image.new("1", (w, h), 1).save(out_png)

    monkeypatch.setattr(render, "html_to_png", stub)
    render.clear_cache()
    for i in range(render._CACHE_MAX + 5):
        render.render_frame(f"<p>{i}</p>", 800, 480)
    assert render.cache_stats()["size"] == 16, "a literal, not the constant"


# --- dirty rectangles: why the whole panel used to fog ------------------------

W, H = 800, 480
STRIDE = W // 8


def _blank():
    return bytes(STRIDE * H)


def _with(*cells):
    """A frame with the given (row, byte_column) bytes set."""
    buf = bytearray(STRIDE * H)
    for row, col in cells:
        buf[row * STRIDE + col] = 0xFF
    return bytes(buf)


def test_nothing_changed_is_no_rectangles_not_the_whole_screen():
    assert render.dirty_rects(_blank(), _blank(), W, H) == []


def test_a_geometry_mismatch_asks_for_everything_rather_than_guessing():
    assert render.dirty_rects(_blank(), _blank()[:-1], W, H) is None
    assert render.dirty_rects(_blank(), _blank(), 801, H) is None


def test_a_rectangle_is_eight_aligned_by_construction():
    # The panel's partial-refresh x-bounds must be multiples of 8 (CLAUDE.md
    # §6), and a 1bpp row packs eight pixels to a byte. Computing in byte
    # columns satisfies both without rounding afterwards.
    for x, _y, w, _h in render.dirty_rects(_blank(), _with((10, 3)), W, H):
        assert x % 8 == 0 and w % 8 == 0


def test_one_changed_byte_is_one_small_rectangle():
    assert render.dirty_rects(_blank(), _with((10, 3)), W, H) == [(24, 10, 8, 1)]


def test_two_distant_changes_stay_two_rectangles():
    # The clock and the markets band are 350 rows apart. Unioning them into one
    # bounding box put 45.8% of the panel under a partial waveform; kept
    # separate the same changes are 4.6%.
    rects = render.dirty_rects(_blank(), _with((80, 3), (450, 60)), W, H)
    assert len(rects) == 2
    assert sum(w * h for _x, _y, w, h in rects) < 0.01 * W * H


def test_bands_a_few_rows_apart_are_merged_rather_than_refreshed_twice():
    rects = render.dirty_rects(_blank(), _with((80, 3), (84, 3)), W, H)
    assert len(rects) == 1


def test_the_rectangle_count_is_bounded():
    scattered = _with(*[(row, 3) for row in range(0, 400, 50)])
    rects = render.dirty_rects(_blank(), scattered, W, H)
    assert 0 < len(rects) <= render.MAX_DIRTY_RECTS


def test_every_changed_pixel_is_inside_some_rectangle():
    changed = _with((5, 0), (200, 55), (300, 99), (479, 12))
    rects = render.dirty_rects(_blank(), changed, W, H)
    for row in range(H):
        for col in range(STRIDE):
            if changed[row * STRIDE + col] == 0:
                continue
            x = col * 8
            assert any(rx <= x and x < rx + rw and ry <= row < ry + rh
                       for rx, ry, rw, rh in rects), (row, col)
