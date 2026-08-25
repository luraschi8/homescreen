# Build Plan

**Living document.** Supersedes SPEC §14 and ADDENDUM §11, and replaces the build order
previously carried in `VALIDATION-01.md`. Findings that justify these choices live in
`VALIDATION-01.md`; this file is what to do next.

---

## 1. Target architecture

**The Pi is a pure backend. Every display is an HTTP client.**

```
                  ┌─────────────────────────────────┐
                  │  Raspberry Pi 4  (cupboard)     │
                  │  fetch.py · render.py           │
                  │  serve.py  (always-on daemon)   │
                  └──────┬───────────────────┬──────┘
                         │  LAN, plain HTTP  │
              ┌──────────▼──────────┐  ┌─────▼───────────────┐
              │ ESP32-C3 + GC9A01   │  │ ESP32-C3 + Driver   │
              │ "radar"             │  │ HAT + 7.5" panel    │
              │ data push, ~1.8 KB  │  │ "desk"              │
              │ draws geometry      │  │ pixel push, 48 KB   │
              └─────────────────────┘  └─────────────────────┘
```

Chosen over a Pi-attached panel primarily on **thermal and enclosure grounds**: SPEC §2
and §16 exist to manage a Pi 4 reaching 60–70°C behind glass rated to 40°C. A C3 draws a
few hundred milliwatts and removes that problem, along with the ~30mm depth, the air gap
and the vent slots. The Pi moves to a cupboard on ethernet, where it already is.

**The wired path is still built — as a bring-up harness, not the destination.** It costs
~20 lines (`epd.init()` / `epd.display(buf)` / `epd.sleep()` in a `try/finally`) because
`render.py` produces a buffer either way. It earns its keep twice: it answers the
legibility gate without any firmware, and forever after it is how you tell a render bug
from a wire bug — push the same buffer over SPI and see which end is lying.

---

## 2. Phase order

Reordered from ADDENDUM §11 to start with the radar, which needs no new hardware and can
proceed while the panel is in transit.

### Phase A — Radar as the first client *(no new hardware; do this now)*

Validates the entire server→client half: config device registry, routing, ETag, telemetry,
cadence control, cache decoupling. Uses a device that is already debugged, on the simpler
data-push path — no polarity, no framebuffer, no partial refresh.

| # | Step | Notes |
|---|---|---|
| A1 | Pi bring-up (server subset) — git, venv, `config.yaml` | **DONE** 2026-08-25 |
| A2 | `sources/adsb.py` — fetch, filter to radius, compute bearing/distance/velocity, write `cache/feed/radar.json` | **DONE** — own cadence, decoupled from device polling (VALIDATION C7) |
| A3 | `serve.py` + `/api/display/radar/data` (and `/health`) | **DONE** — live at `http://dashboard.local:8080`; ETag, `X-Poll-Seconds`, `X-Feed-Age`/`X-Feed-Ok`, telemetry |
| A4 | Radar firmware fork — swap `adsb.fi` for the Pi | See §3; drops TLS entirely |
| A5 | Soak — confirm heap recovery and stall detection | Measure against `OPS.md` baselines |

**Phase A delivers real value independent of the dashboard:** it removes mbedTLS from the
radar, returning ~33 KB of pinned heap and the two ~16.4 KB per-fetch blocks, on a device
whose healthy free heap is 22–28 KB with a ~9 KB largest block. Two documented bugs
(`IncompleteInput` every 2–3 min, and the WiFiManager `/wifi` blank page) plausibly
disappear as a side effect. Verify that rather than assume it.

### Phase B — Panel, wired *(starts when the panel arrives; overlaps Phase A)*

| # | Step | Notes |
|---|---|---|
| B0 | Pi bring-up (render subset) — enable SPI, Chromium, fontconfig, Inter, vendored driver | See CLAUDE.md §2 for actual device state |
| B1 | Panel hello-world + **asymmetric polarity vector** | `0x80 0x01`, see VALIDATION #1 |
| B2 | **Static render loop — the legibility gate** | Answers the highest-severity risk |
| B3 | Clocks + weather | A working dashboard |
| B4 | Calendar | |
| B5 | Partial-refresh tick via our own wrapper | Not `display_Partial()` — VALIDATION #3 |

**B2 gates everything downstream.** Do not build the type scale on an unverified 10px
tier. At B2 also measure the Pi's intermediate-pixel fraction (VALIDATION #5) and look at
the dotted-rule stipple on glass.

### Phase C — Panel as a client

| # | Step | Notes |
|---|---|---|
| C1 | `X-Partial-Window` protocol design | The gap in ADDENDUM §5 — see §4 |
| C2 | `/api/display/{id}/frame` + per-device ETag and ghosting counters | |
| C3 | `epaper_client` firmware on C3 — GxEPD2, streaming, watchdog, OTA | Not Waveshare's Arduino lib — no partial refresh in it |
| C4 | Move the panel off the Pi; Pi to the cupboard | Keep the wired path in-tree |

### Phase D — Remaining content

Markets band → sports → deliveries, in that order, per SPEC §14. Deliveries last: most
fragile, least essential.

---

## 3. Radar endpoint specification

Not specified in either source document. Derived from what the firmware actually consumes
(`include/services/adsb_client.h`, `src/ui/radar_display.cpp`).

### `GET /api/display/radar/data`

```json
{
  "server_time": 1756049531,
  "feed": { "ok": true, "age_s": 2.4, "source": "adsb.fi" },
  "aircraft": [
    { "lat": 40.51, "lon": -3.62, "nose": 143.0, "trk": 143.0, "gs": 421.0,
      "ve": 0.130, "vn": -0.173, "age": 3.1, "dst": 7.4,
      "cs": "IBE3221", "ty": "A320", "alt": "34000 ft" }
  ]
}
```

Short keys deliberately: 20 aircraft ≈ 1.8 KB, and ArduinoJson's filter can be retired
because the Pi already sends only these fields.

`alt` is a pre-formatted tag, never a flight level: the firmware's
`formatAltitudeTag` emits only `"<N> ft"`, `"GND"`, or `""`, and
`radar_display.cpp` renders the string verbatim. `dst` is **nautical miles**
while `radius_km` is kilometres — convert at every comparison.

`ve`/`vn` are `vel_e_km_s`/`vel_n_km_s`, computed server-side — this removes a `sinf`
and a `cosf` per aircraft per fetch from the C3, and is the "server sends better numbers"
principle doing actual work.

### The `pos_age_s` trap — read before implementing

`radar_display.cpp:534` dead-reckons from `pos_age_s + fetch_age_s`, where `pos_age_s` is
the position's age *at the API* and `fetch_age_s` is time since **this device's** fetch.
`kExtrapolationHorizonSec` is only **12 s**.

Inserting the Pi breaks three things at once:

1. **Under-extrapolation.** The time data sits in the Pi's cache is invisible to the
   device. At ~250 m/s a 4 s cache age is ~1 km of position error, and 4 s is a third of
   the whole 12 s horizon.
2. **Late dimming.** The `pos_age_s >= horizon` staleness test fires late, so genuinely
   stale aircraft render as fresh.
3. **Stall detection breaks entirely — the important one.** Today `fetch_age_raw` catches
   a stalled feed because the device's own fetch fails. With the Pi in the middle the
   device's fetch keeps *succeeding* while the upstream data rots. The device would show
   minute-old traffic as live, with no indication.

**Fixes:**

- The server recomputes age **at serve time**, not fetch time:
  `age = api_seen_pos + (now − server_fetch_time)`. The device's existing
  `pos_age_s + fetch_age_s` arithmetic then stays correct with **no change to the render
  path**.
- The response carries `feed.age_s` and `feed.ok` (and, on a bodiless `304`, the
  `X-Feed-Age` / `X-Feed-Ok` headers). The firmware tests staleness against `feed.age_s`
  **in addition to** `fetch_age_raw`, as a third cause — **not instead of it**.
  Substituting would delete the device's only detection of *its own* link failing: both
  causes would then derive from Pi-side timestamps carried in the body, so a device that
  loses the LAN sees both frozen at their last-received values and never dims, leaving
  only the 60 s `kDataExpirySec` — five times slower than today.

Note `radar_display.cpp` tests the two staleness causes **separately and deliberately** —
the comment there records that summing them made targets blink once per cycle. Preserve
that structure and add `feed.age_s` as a **third** separately-tested cause. Do not merge
them, and do not substitute it for `fetch_age_raw`.

**Three requirements this places on the firmware (Phase A4):**

- **A `304` needs the device's single clock split in two — and 304 must stop being a
  failure.** `adsb_client.cpp` has one `s_last_update_ms` (declared line 41, assigned only
  in `publish()` line 84) feeding three consumers that want different things on a 304:

  | Consumer | Used for | On a 304 |
  |---|---|---|
  | `secondsSinceUpdate()` | dead-reckoning base | **freeze** — the fix really is that old |
  | `secondsSinceUpdateRaw()` | the 12 s dim test | **freeze** |
  | `dataExpired()` | the 60 s blank-to-grid | **refresh** — we did just hear from the server |

  "Don't reset the timestamp" reads as freeze-all-three, and that dims every target at
  12 s and drops the panel to grid-only at 60 s **with perfectly current data**. An empty
  sky returns a byte-identical body indefinitely, so that run of 304s is the normal case,
  not an edge one. Split it into a **content clock** (frozen) and a **contact clock**
  (refreshed), and move `dataExpired()`'s `!= 0` never-fetched sentinel onto the contact
  clock rather than copying it verbatim. Give 304 its own branch: the current success path
  continues into `deserializeJson` → the array guard → `publish()`, and a 304 has no body,
  so it must skip both. Today `adsb_client.cpp:316` treats any `code != HTTP_CODE_OK` as
  failure, including 304 — so this reads as "already handled" and is not.
- **Read `X-Feed-Age` / `X-Feed-Ok`, not just the body.** They are set on the 304 as well
  as the 200 precisely because a 304 carries no body; without them a device that sends
  `If-None-Match` cannot see the feed's liveness at all.
- **`feed.age_s` is a THIRD staleness cause, tested separately — it does not replace
  `fetch_age_raw`.** `radar_display.cpp:532-533` is
  `pos_age_s >= horizon || fetch_age_raw >= horizon`. Substituting deletes the second
  term, which is the device's only detection of *its own* link failing: both causes would
  then derive from Pi-side timestamps in the body, so a device that loses the LAN sees
  both frozen and never dims, leaving only the 60 s expiry — five times slower. Add a
  third `||` term; do not merge and do not substitute.

`dst` (`dst_nm`) is currently the API's own distance, kept as the independent check that
caught a missing `cos(latitude)` term, with `test_geo` asserting against it. If the Pi
computes it, that independence is lost. **Keep passing the upstream value through
unmodified**, and let the Pi's own projection be checked against it the same way.

---

## 4. Open — pending validation

| # | Question | Blocked on | Phase |
|---|---|---|---|
| V6 | **Is 10px type legible on real glass?** Highest-severity risk; gates the type scale | panel | B2 |
| V7 | C3 WiFi RSSI at the radar's intended location | nothing — **testable now** | A5 |
| V2b | Does writing only `0x13` (skipping `0x10`) give a clean full refresh? Decides stream-vs-buffer | panel | B1 |
| V5b | Does the Pi's Chromium honour `-webkit-font-smoothing: none`? Linux rasterisation is fontconfig-dependent | Chromium installed | B2 |
| V8b | Does GxEPD2 accept a **pushed** window buffer via `writeImagePart`, or only locally-drawn bitmaps? | panel + C3 | C3 |
| V9 | GxEPD2's polarity and alignment behaviour — all VALIDATION #1/#3 findings are against the **Python** driver | panel + C3 | C3 |
| V10 | 48 KB contiguous allocation on a C3 under fragmentation — stream, or allocate at boot? | panel + C3 | C3 |
| V11 | Real partial-refresh duration (GxEPD2 says 1600 ms, SPEC §2 claims 300 ms) | panel | B5 |
| V12 | Does removing TLS actually fix the two documented radar bugs? | nothing — **Phase A answers it** | A5 |

**V7 and V12 are testable without buying anything.** Everything else waits on the panel.

---

## 5. Open — spec gaps needing a decision

| # | Gap | Status |
|---|---|---|
| S1 | `X-Partial-Window: x0,y0,x1,y1` — the protocol cannot say *where* a partial refresh applies. With every screen a client this is core, not an addendum | **design needed, Phase C1** |
| S2 | Partial-refresh window must become a **per-template** property, not SPEC §9's global constant | design needed |
| S3 | Ghosting counter must be **per-device**, keyed by `device_id` | design needed |
| S4 | `dashboard_compact` — what does a satellite drop relative to the desk panel? Undefined | deferred to Phase C |
| S5 | Device provisioning — how a new client learns its `device_id` and the Pi's address. WiFiManager `/param` is the obvious hook | deferred to Phase C |
| S6 | What a client renders when the Pi is unreachable at cold boot (no last frame yet) | deferred to Phase C |

### Corrections that override the documents

These live here and in `CLAUDE.md`; `SPEC.md` and `ADDENDUM-01` are deliberately left
frozen as written, per the precedence order in `CLAUDE.md`. Nothing below has been edited
into those files, and nothing below should be.

- **ADDENDUM §5** — polarity is `1 = black`, not `1 = white`. Frame body is `getbuffer()`
  output, never raw `.tobytes()`.
- **SPEC §2 wiring table** — missing **PWR, BCM 18, physical 12**. `module_init()` asserts
  it; without it the panel never powers up.
- **SPEC §2** — partial refresh is ~1.6 s, not ~0.3 s.
- **SPEC §13** — `preview.py` dropped; `serve.py` absorbs it.
- **Panel choice reconfirmed** — `epd7in5_V2` (800×480) is the *only* 7.5" Waveshare
  variant with `display_Partial` + `init_part`. The 880×528 HD has no partial refresh and
  therefore no ticking clock. Do not substitute.

---

## 6. Hardware to buy

**One** Waveshare **7.5inch e-Paper HAT (V2)** — 800×480 black/white, **the bundle that
includes the Driver HAT** (it is sold both ways; the bare panel gives you an FFC ribbon
with nothing to plug it into).

Buy one, not several: B2 may change the type scale or layout density, and that would
change what a satellite should be.

Nothing else. The Driver HAT is the FFC breakout for both the Pi and a C3, so this
purchase serves the wired harness and the eventual client with no rework. You already
have the C3s and the Pi.
