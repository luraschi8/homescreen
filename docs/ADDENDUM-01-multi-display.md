# Architecture Addendum 01 — Multi-display, Pi-as-backend

**Supersedes parts of** `SPEC.md`
**Status:** Proposed. Validated in `VALIDATION-01.md`.
**Date:** August 2026

---

## 0. Task for Claude Code

Before implementing:

1. Read `epaper-dashboard-spec.md` in full, then this document.
2. **Validate** the assumptions in §9 — several are stated from reasoning, not from testing, and are marked as such.
3. Produce a revised build order that supersedes §14 of the original spec.
4. Flag any conflict between the two documents. **This document wins** where they disagree.
5. Do not begin firmware work until the server path is proven end-to-end against the directly-attached panel.

---

## 1. What changed

The original spec described **one Pi with one panel bolted to its GPIO header**. That is now a special case of a broader system:

> The Pi is a **backend**. Displays are **clients** that fetch over HTTP on the LAN.

Reason: the same Pi can feed several displays around the house, and all the heavy work — API orchestration, credential storage, text layout, dithering — lives in one place where it is easy to change, instead of being duplicated across microcontrollers that are bad at it.

A second, independent change: **every device is mains-powered.** No battery anywhere. This removes a large set of constraints (see §4).

---

## 2. Two payload patterns — the core distinction

Not every display should receive pixels. Route on what the device has to draw.

| Pattern | Server sends | Device does | Use when |
|---|---|---|---|
| **Pixel push** | Finished 1-bit framebuffer | Streams bytes to panel | Device must lay out **text** — fonts, kerning, wrapping, dithering |
| **Data push** | Small pre-computed JSON | Draws vector primitives locally | Device draws **geometry** — circles, arcs, sprites; or needs to animate between fetches |

**Decision rule:** text layout → server renders. Geometry → device renders, server just sends better numbers.

### Why this matters (worked example)

The existing plane radar (ESP32-C3 Super Mini + GC9A01, 240×240, 16-bit colour):

- Pixel push would be **115,200 bytes per frame**. Aircraft move, so ~2–3s refresh → ~40KB/s sustained, forever, on a chip with ~400KB total SRAM.
- Data push is **~20 aircraft × ~40 bytes ≈ under 1KB**. Two orders of magnitude smaller. The C3 draws twenty triangles in single-digit milliseconds.
- Pushed frames also cannot interpolate. Local rendering lets the device dead-reckon from heading and speed between polls, so the radar sweeps instead of jumping.

Conversely, an 800×480 1-bit e-paper frame is **48,000 bytes** every ten minutes — trivial — and asking a microcontroller to do font layout and dithering for it would be miserable.

---

## 3. Device classes

| Class | Hardware | Pattern | Notes |
|---|---|---|---|
| `epaper_wired` | Pi + Waveshare 7.5" HAT | Direct (no HTTP) | The desk panel. Already specced. |
| `epaper_client` | ESP32 + Waveshare e-Paper ESP32 Driver Board + 7.5" panel | Pixel push | Satellite displays |
| `gc9a01_client` | ESP32-C3 Super Mini + GC9A01 240×240 | Data push | Existing plane radar |

**Recommend the Waveshare e-Paper ESP32 Driver Board** for `epaper_client`, not a bare Super Mini. The FFC connector is built in — no jumper wiring, no pin-mapping mistakes, six fewer wires inside the enclosure.

Keep the C3 Super Minis for the round display, where wiring is already solved and proven.

**On the C3 Super Mini generally:** its known weaknesses are (a) poor deep-sleep current from the power LED and cheap LDO, and (b) a mediocre PCB antenna. (a) is now irrelevant (§4). (b) still applies — test WiFi at the intended location before building an enclosure around it. Also avoid GPIO 9 for peripherals; it is BOOT, and a peripheral on it produces a board that will not start.

---

## 4. Consequences of mains power everywhere

This simplifies more than it looks.

**Drop deep sleep entirely.** Firmware becomes a plain loop with a delay, not wake → fetch → render → sleep. This eliminates:
- RTC memory for state persistence across sleep
- WiFi re-association every cycle (2–5s, and the source of most flakiness)
- Boot-loop debugging

**Every display gets the ticking clock.** Battery was the only reason satellites would have been limited to a 15–30 minute cadence. An always-connected device can poll every 30s, receive `304 Not Modified` for a few bytes most of the time, and partial-refresh on the minute — same behaviour as the desk panel.

**Two things now become necessary** that a sleeping device got for free:

1. **Hardware watchdog + reconnect.** A device that sleeps gets a free reset each cycle. One that runs for months does not. Enable the watchdog; after N consecutive failed fetches, `ESP.restart()`.
2. **OTA updates.** With several devices on shelves, walking round with a USB cable does not scale. Always-on makes `ArduinoOTA` straightforward.

**Enclosure:** a permanently powered ESP32 + driver board runs warm. Same air gap and vent slots as the Pi enclosure (original spec §2), less urgently.

---

## 5. HTTP protocol

Plain HTTP on the LAN. **No TLS** — it is the single largest source of memory pressure and firmware complexity on ESP32, and there is nothing here worth protecting from a LAN attacker who is already inside.

### Endpoints

```
GET /api/display/{device_id}/frame     → packed 1bpp bytes    (pixel push)
GET /api/display/{device_id}/data      → application/json     (data push)
GET /api/display/{device_id}/health    → server-side status, for debugging
```

### Request

Devices report telemetry as query params so failures are visible before they matter:

```
?rssi=-64&uptime=884213&fw=1.2.0&errors=0
```

(No battery voltage — everything is mains.)

### Response headers

| Header | Purpose |
|---|---|
| `ETag` | Content hash. Device sends `If-None-Match`; unchanged content returns **304** and the device skips the refresh entirely. Saves panel wear and pointless SPI traffic. |
| `X-Poll-Seconds` | How long until the next poll. **Server-side cadence control** — tune per device, slow overnight, no reflashing. (Renamed from `X-Sleep-Seconds` in earlier discussion; nothing sleeps now.) |
| `X-Full-Refresh` | `1` instructs the device to do a full refresh rather than partial, so the server owns the anti-ghosting schedule. |

### Frame format (pixel push)

Packed 1bpp matching the panel's native buffer layout exactly. 800×480 ÷ 8 = **48,000 bytes**, no header, no compression.

- Produced by Pillow: mode `1` image → `.tobytes()`
- **Waveshare convention: 1 = white, MSB first**
- Device streams the HTTP response body in ~4KB chunks directly into the panel, so peak allocation stays negligible

> **Verify polarity on day one.** A mismatch produces a perfectly inverted image and is the single most common bring-up bug. Test with an asymmetric pattern, not a checkerboard.

---

## 6. Config changes

`config.yaml` gains a device registry. Each entry declares its render mode; `serve.py` routes on it.

```yaml
devices:
  - id: desk
    kind: epaper_wired
    render: server
    width: 800
    height: 480
    bpp: 1
    template: dashboard_full
    poll_seconds: 60

  - id: kitchen
    kind: epaper_client
    render: server
    width: 800
    height: 480
    bpp: 1
    template: dashboard_compact
    poll_seconds: 60

  - id: radar
    kind: gc9a01_client
    render: device
    feed: adsb
    home: { lat: 40.4168, lon: -3.7038 }
    radius_km: 60
    max_aircraft: 20
    poll_seconds: 5

feeds:
  adsb:
    source: api          # api | dump1090
    endpoint: "..."
```

Templates are now **parameterised by dimensions** — a satellite may not be 800×480, and `dashboard_compact` may show a different subset than the desk panel.

---

## 7. Changes to the file layout

Relative to original spec §5:

```
render.py        → render(display_id), writes cache/render/{id}.bin
serve.py         → NEW. Endpoints, ETag, routing on render mode
sources/adsb.py  → NEW. Fetch, filter to radius, compute bearing/distance,
                   sort by relevance, write cache/feed/radar.json
templates/       → now one per template name, parameterised by w/h
cache/render/    → NEW
cache/feed/      → NEW
firmware/
  epaper_client/ → NEW. Arduino/PlatformIO
  radar_client/  → MODIFIED fork of MatixYo/ESP32-Plane-Radar
```

`fetch.py`, the systemd timers, and the whole 1-bit design system (original spec §3) are **unchanged**.

---

## 8. Radar firmware changes

The existing firmware fetches ADS-B directly and parses it with ArduinoJson. Replace that with a single GET to the Pi returning a flat pre-computed array.

This is **less code than the current version**, and it moves the actual problems off-device:

- Full ADS-B API responses are large; parsing them on a C3 is a known cause of mysterious reboots
- **Rate limits** — one device polling is fine, four will get throttled. One Pi fetch fanned out to N devices fixes this permanently
- No API key ever lives in firmware
- Radius, home coordinates and altitude filters become server-side config

Keep local rendering and local interpolation. Do not push frames to this device.

### Optional: RTL-SDR

A ~€25 RTL-SDR dongle plus `dump1090` on the Pi receives aircraft transponders directly at 1090MHz — no API, no rate limits, no internet dependency, typically 200–300km range with a reasonable antenna. `dump1090` exposes a local JSON endpoint that is close to a drop-in for the current API.

Worth evaluating, not required for v1.

---

## 9. Assumptions to validate before building

These are reasoned, not tested. Confirm each.

| # | Assumption | How to check |
|---|---|---|
| 1 | Pillow mode-`1` `.tobytes()` matches Waveshare's bit order and polarity | Asymmetric test pattern on the wired panel |
| 2 | ESP32 can stream an HTTP body directly into the panel in chunks without a full 48KB buffer | Prototype against the driver board |
| 3 | `epd7in5_V2.display_Partial()` accepts arbitrary rectangles with x aligned to multiples of 8 | Read the vendored driver source; do not trust the datasheet |
| 4 | Chromium headless at exactly 800×480, scale factor 1, produces no scaling artefacts | Compare rendered PNG pixel-for-pixel against the HTML |
| 5 | Threshold 160 (not 128) preserves hairlines and thin strokes | Milestone 2 of original spec |
| 6 | 10px type is legible on real glass | **Blocks the entire type scale — do this first** |
| 7 | C3 Super Mini WiFi is adequate at the radar's intended location | Measure RSSI in place before enclosure design |
| 8 | GxEPD2 (or direct SPI) on the Waveshare driver board supports partial refresh on this panel | Prototype |

---

## 10. Open decisions

**Should the desk panel stay wired, or become an HTTP client like the others?**

- *Wired:* the HAT is already purchased, the code path is already specced, and it proves the render pipeline without any network in the way.
- *Client:* one uniform code path, and the Pi could then live anywhere — a cupboard, a shelf — rather than behind a display.

**Recommendation: keep both.** Build wired first because it is the shortest path to something working and isolates rendering bugs from network bugs. Add the client path as a second consumer of the same buffer. The wired path costs ~20 lines to retain.

---

## 11. Revised build order

Supersedes original spec §14.

1. **Panel hello-world** (unchanged) — vendored driver, SPI, black rectangle on the wired panel.
2. **Static render loop** (unchanged) — hardcoded HTML → Chromium → threshold → panel. **Answers assumption #6.** Do not proceed until the type scale is confirmed on glass.
3. **Clocks + weather** (unchanged) — a working single-panel dashboard.
4. **Calendar** (unchanged).
5. **Partial-refresh tick** (unchanged).
6. **`serve.py` + frame endpoint** — NEW. Serve the same buffer over HTTP; verify by fetching with `curl` and diffing against `cache/render/desk.bin`.
7. **First `epaper_client`** — NEW. Firmware, ETag handling, watchdog, OTA.
8. **`sources/adsb.py` + data endpoint** — NEW.
9. **Radar firmware fork** — NEW. Swap direct API fetch for the Pi endpoint.
10. **Markets band, sports, deliveries** — as original spec, in that order.

Steps 1–5 are unchanged from the original plan. **The architecture change is purely additive** — nothing already specced needs rewriting, because `render.py` already produces a buffer rather than drawing to a screen.

---

## 12. Risks introduced by this change

| Risk | Severity | Mitigation |
|---|---|---|
| Pi becomes a single point of failure for all displays | Medium | E-paper fails gracefully — shows a stale image, not a blank one. Masthead timestamp makes staleness visible. Devices keep last frame on fetch failure. |
| Buffer polarity mismatch | Medium | Assumption #1, tested at step 6 before any firmware exists |
| Firmware drift across N devices | Medium | OTA from day one; version reported in telemetry query params |
| Template proliferation as displays are added | Low | Parameterise by dimensions; resist per-device templates until genuinely needed |
| Always-on ESP32 memory leaks over months | Low | Watchdog + restart-on-N-failures |

---

## 13. Unchanged from the original spec

Explicitly still in force — do not revisit:

- The entire 1-bit design system (§3): no greys, hierarchy via size and weight, dotted rules, 10px minimum type
- Threshold at 160, no dithering for text
- `epd.sleep()` on every code path including exceptions
- The layout geometry and 8px-aligned partial-refresh window (§9)
- All data source choices and endpoints (§7)
- Failure and degradation rules (§11)
- Every decision in the original Appendix
