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

def test_no_chromium_anywhere_raises_rendererror(monkeypatch, tmp_path):
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
    with pytest.raises(render.RenderError):
        render.html_to_png(_page(), 800, 480, tmp_path / "o.png",
                           binary="/bin/false")


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
