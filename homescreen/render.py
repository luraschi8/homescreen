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

import collections
import hashlib
import logging
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

log = logging.getLogger(__name__)

THRESHOLD = 160
RENDER_TIMEOUT_S = 30

# A device declares its own geometry, so these are bounds on what a device can
# make the Pi do, not on what we expect. 4096x4096 is byte-aligned and would
# render a 2 MB frame -- accepted before this cap existed.
MAX_DIMENSION = 2048
MAX_FRAME_BYTES = 512_000

# Chromium costs ~200 MB and ~3 s per invocation on a Pi 4 with 1.8 GB usable.
# Eight concurrent requests measured 8 concurrent browsers; that is an OOM on
# the target, so renders are serialised rather than merely rate-limited.
_RENDER_SLOTS = threading.Semaphore(2)
# Nothing bounds the queue BEHIND the semaphore, and Werkzeug spawns a thread
# per request. Measured: 20 concurrent requests reached 45s latency, by which
# point a device has timed out and retried, adding to the queue. Shed instead.
RENDER_QUEUE_TIMEOUT_S = 20

# Scene BUILDING is string formatting; RENDERING forks a browser. Identical
# HTML at identical geometry is therefore worth caching outright -- keyed on
# content, so it needs no TTL and cannot go stale. Without this, every poll
# forks chromium even when the response is a 304: measured 17,280 spawns per
# device per day at poll_seconds=5.
_CACHE_MAX = 16
_cache: "collections.OrderedDict[tuple, bytes]" = collections.OrderedDict()
_cache_lock = threading.Lock()
cache_hits = 0
cache_misses = 0

# --hide-scrollbars: a scrollbar steals ~15px and shifts the layout.
# --default-background-color: a transparent backdrop thresholds to solid black.
CHROMIUM_FLAGS = (
    "--headless", "--disable-gpu", "--no-sandbox",
    "--force-device-scale-factor=1", "--hide-scrollbars",
    "--default-background-color=FFFFFFFF",
)


class RenderError(RuntimeError):
    """Rendering failed. Never allowed to escape into a request handler."""


class RenderBusy(RenderError):
    """Too many renders queued. Retryable, unlike its parent -- a caller should
    tell the device to come back rather than reporting a broken scene."""


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


def check_geometry(width: int, height: int) -> None:
    """Reject a geometry before it costs anything. Raises RenderError."""
    if width < 1 or height < 1:
        raise RenderError(f"{width}x{height} is not a geometry")
    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise RenderError(f"{width}x{height} exceeds {MAX_DIMENSION}px per side")
    if width % 8:
        # Pillow pads each ROW to a byte, so the constraint is on WIDTH, not
        # on the area. (w*h)%8 lets 12x8 through, which then forks a browser,
        # renders for ~3s, fails packing and returns a retryable 503 that is
        # never cached -- a spawn loop from one misconfigured device.
        raise RenderError(f"width {width} is not a multiple of 8; "
                          f"rows pad to whole bytes")
    if width * height // 8 > MAX_FRAME_BYTES:
        raise RenderError(f"{width}x{height} would be "
                          f"{width * height // 8:,} bytes, over the "
                          f"{MAX_FRAME_BYTES:,} limit")


def is_cached(html: str, width: int, height: int) -> bool:
    """Whether `render_frame` would answer without forking a browser.

    Exposed so a caller can throttle only the EXPENSIVE path. Rate-limiting
    every request would punish a device polling correctly; rate-limiting only
    cold renders punishes exactly the traffic that costs ~3s of CPU.
    """
    key = (hashlib.sha256(html.encode()).hexdigest(), width, height)
    with _cache_lock:
        return key in _cache


def cache_stats() -> dict:
    return {"hits": cache_hits, "misses": cache_misses, "size": len(_cache)}


def clear_cache() -> None:
    """Drop every cached frame.

    Process-global state is shared between tests, which makes them
    order-dependent unless each starts clean -- a conftest fixture calls this.
    Also gives an operator a way to force a re-render without a restart.
    """
    global cache_hits, cache_misses
    with _cache_lock:
        _cache.clear()
        cache_hits = cache_misses = 0


def render_frame(html: str, width: int, height: int,
                 *, binary: str | None = None) -> bytes:
    """HTML to a packed 1-bit framebuffer. Raises RenderError, never anything else.

    Cached on the exact HTML and geometry: a poll that would produce the same
    pixels reuses them rather than forking a browser.
    """
    global cache_hits, cache_misses
    check_geometry(width, height)
    key = (hashlib.sha256(html.encode()).hexdigest(), width, height)

    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            cache_hits += 1
            return hit
        cache_misses += 1

    expected = width * height // 8
    if not _RENDER_SLOTS.acquire(timeout=RENDER_QUEUE_TIMEOUT_S):
        raise RenderBusy(f"render queue busy for {RENDER_QUEUE_TIMEOUT_S}s")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "frame.png"
            html_to_png(html, width, height, png, binary=binary)
            packed = png_to_packed(png, width, height)
    finally:
        _RENDER_SLOTS.release()
    if len(packed) != expected:
        raise RenderError(f"expected {expected} bytes, packed {len(packed)}")

    with _cache_lock:
        _cache[key] = packed
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return packed


#: A 1bpp row is packed eight pixels to a byte, and this panel's partial-refresh
#: x-bounds must be multiples of 8 (CLAUDE.md §6). Both facts point the same
#: way, so dirty rectangles are computed in BYTE columns and come out aligned by
#: construction rather than by rounding afterwards.

#: How many rectangles are worth sending. Each one costs the device its own
#: refresh cycle, so past a handful the cycles cost more than the pixels saved.
MAX_DIRTY_RECTS = 4

#: Bands closer together than this are merged. A gap of a few rows between two
#: changed bands is not worth a second refresh cycle and its fixed overhead.
_MERGE_GAP_ROWS = 24


def dirty_rects(previous: bytes, current: bytes, width: int, height: int):
    """8-aligned rectangles covering every changed pixel, top to bottom.

    Empty when nothing changed. None when the inputs are not two frames of
    this geometry -- the caller then refreshes everything, which is always
    correct and never wrong, only slower.

    What the 7.5" V2 actually does with this: nothing. Its driver disables
    windowed partial refresh outright --

        static const bool usePartialUpdateWindow = false; // set false for
                                                          // better image

    -- so `refresh(x, y, w, h)` sets a RAM window and then drives the WHOLE
    screen anyway, measured at a constant 1.578s per call whatever the
    rectangle. Confining the window saves nothing there, and asking for two
    rectangles spends two full-screen partial waveforms where one would have
    done. That panel takes a full refresh every time instead.

    What survives, and is worth keeping: the difference between "no rectangles"
    and "no header". An empty list means the server diffed the two frames and
    they are identical, so the device can skip the draw entirely -- 3.68s of
    flashing and a little more residue, saved. A missing header means it could
    not tell, and the device refreshes everything.

    The rectangles themselves are correct and cheap, and are the right thing to
    send to any display whose controller can honour them. Measured on two
    consecutive live frames: one bounding box round every change is 45.8% of
    the panel because it unions the clock with the markets band; the same
    changes as separate row bands are 4.6%.
    """
    stride = width // 8
    if width % 8 or stride <= 0 or height <= 0:
        return None
    if len(previous) != stride * height or len(current) != stride * height:
        return None
    if previous == current:
        return []

    bands = []
    for row in range(height):
        start = row * stride
        end = start + stride
        if previous[start:end] == current[start:end]:
            continue
        lo, hi = stride, -1
        for col in range(stride):
            if previous[start + col] != current[start + col]:
                if col < lo:
                    lo = col
                hi = col
        if bands and row - bands[-1][3] <= _MERGE_GAP_ROWS:
            band = bands[-1]
            band[0] = min(band[0], lo)
            band[1] = max(band[1], hi)
            band[3] = row
        else:
            bands.append([lo, hi, row, row])

    # Still too many: merge whichever neighbours are closest, repeatedly. The
    # union of two bands is cheaper than a second refresh cycle.
    while len(bands) > MAX_DIRTY_RECTS:
        gaps = [bands[i + 1][2] - bands[i][3] for i in range(len(bands) - 1)]
        i = gaps.index(min(gaps))
        a, b = bands[i], bands[i + 1]
        bands[i:i + 2] = [[min(a[0], b[0]), max(a[1], b[1]), a[2], b[3]]]

    return [(lo * 8, y0, (hi - lo + 1) * 8, y1 - y0 + 1)
            for lo, hi, y0, y1 in bands]
