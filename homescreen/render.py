"""Scene -> HTML -> PNG -> 1-bit framebuffer, for pixel-push devices.

The panel is an SPI peripheral with no framebuffer: there is no browser to
point at it. Chromium is a rendering engine invoked once per frame, not a
runtime.

Every constant here is load-bearing and measured, not chosen:

  threshold 160, not 128   On the Pi, `-webkit-font-smoothing: none` is NOT
                           honoured the way macOS honours it -- measured 0.558%
                           intermediate greys vs 0.010%, with 304 pixels
                           differing between the two thresholds. Thin strokes
                           live in that band. (VALIDATION-01 #5)
  1 = black on the wire    Pillow's mode-1 `.tobytes()` is 1 = WHITE. The panel
                           is the opposite, so the buffer is inverted before it
                           leaves. Serving raw tobytes() renders a photographic
                           negative on every device. (VALIDATION-01 #1)
  no dithering             Text and rules only. Floyd-Steinberg would turn
                           hairlines into speckle.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

THRESHOLD = 160
RENDER_TIMEOUT_S = 30

# --hide-scrollbars: a scrollbar steals ~15px and shifts the layout.
# --default-background-color: a transparent backdrop thresholds to solid black.
CHROMIUM_FLAGS = (
    "--headless", "--disable-gpu", "--no-sandbox",
    "--force-device-scale-factor=1", "--hide-scrollbars",
    "--default-background-color=FFFFFFFF",
)


class RenderError(RuntimeError):
    """Rendering failed. Never allowed to escape into a request handler."""


def find_chromium() -> str | None:
    """Bookworm and trixie package it as `chromium`; older images use
    `chromium-browser`; macOS has neither. Detect rather than assume."""
    for name in ("chromium", "chromium-browser", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    mac = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    return str(mac) if mac.exists() else None


def html_to_png(html: str, width: int, height: int, out_png: Path,
                *, binary: str | None = None) -> None:
    binary = binary or find_chromium()
    if binary is None:
        raise RenderError("no chromium/chrome binary found")
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "page.html"
        page.write_text(html, encoding="utf-8")
        cmd = [binary, *CHROMIUM_FLAGS,
               f"--window-size={width},{height}",
               f"--screenshot={out_png}", page.as_uri()]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=RENDER_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RenderError(f"chromium failed to run: {exc}") from None
    if not out_png.exists() or out_png.stat().st_size == 0:
        tail = proc.stderr.decode(errors="replace")[-300:]
        raise RenderError(f"chromium produced no image: {tail}")


def png_to_packed(png: Path, width: int, height: int) -> bytes:
    """Threshold to 1-bit and pack in the panel's own wire order.

    MSB is the leftmost pixel, 1 = black. Pillow gives 1 = white, hence the
    XOR -- this is exactly what epd.getbuffer() does, and getting it backwards
    renders a negative.
    """
    from PIL import Image
    img = Image.open(png).convert("L")
    if img.size != (width, height):
        raise RenderError(f"expected {width}x{height}, got {img.size}")
    bw = img.point(lambda p: 255 if p > THRESHOLD else 0, mode="1")
    return bytes(b ^ 0xFF for b in bw.tobytes())


def grey_fraction(png: Path) -> float:
    """Share of pixels that are neither black nor white. Near 0 means the
    render is already binary; anything higher means the threshold is doing
    real work and the smallest type tier deserves a look on glass."""
    from PIL import Image
    hist = Image.open(png).convert("L").histogram()
    total = sum(hist)
    return (total - hist[0] - hist[255]) / total if total else 0.0


def render_frame(html: str, width: int, height: int,
                 *, binary: str | None = None) -> bytes:
    """HTML to a packed 1-bit framebuffer. Raises RenderError, never anything else."""
    expected = width * height // 8
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "frame.png"
        html_to_png(html, width, height, png, binary=binary)
        packed = png_to_packed(png, width, height)
    if len(packed) != expected:
        raise RenderError(f"expected {expected} bytes, packed {len(packed)}")
    return packed
