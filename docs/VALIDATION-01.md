# Validation 01 — Addendum 01 assumptions

**Answers** `ADDENDUM-01-multi-display.md` §0.2 and §0.4.
**Date:** 2026-08-24
**Method:** vendored Waveshare driver source read at
`github.com/waveshareteam/e-Paper@HEAD`; Pillow 11 and Chrome headless run locally on
macOS; GxEPD2 `GxEPD2_750_T7.h` read at `github.com/ZinggJM/GxEPD2@master`.

**No hardware was involved.** SPI is not yet enabled on the Pi and the panel is
unverified, so #6 and #7 remain open. Everything below marked CONFIRMED or REFUTED was
established from source or from an executed test, not from reasoning.

---

## Summary

| # | Assumption | Verdict |
|---|---|---|
| 1 | Pillow `.tobytes()` matches Waveshare bit order **and polarity** | **Bit order CONFIRMED · polarity REFUTED** |
| 2 | ESP32 streams body into panel without a 48KB buffer | **Premise unnecessary** — and harder than stated |
| 3 | `display_Partial()` accepts arbitrary rects with x aligned to 8 | **REFUTED** — three separate defects |
| 4 | Chromium 800×480 @ SF1 produces no scaling artefacts | **CONFIRMED** (macOS; re-check on Pi) |
| 5 | Threshold 160 preserves hairlines | **CONFIRMED but conditional** — not the load-bearing rule |
| 6 | 10px type legible on real glass | **OPEN** — blocked on hardware |
| 7 | C3 Super Mini WiFi adequate in place | **OPEN** — blocked on hardware |
| 8 | GxEPD2 supports partial refresh on this panel | **CONFIRMED** — but contradicts spec's timing |

Two findings change the protocol and must be fixed before firmware exists: **#1
(polarity)** and **#3 (partial refresh)**. One gap in the protocol itself is described in
Conflict C3.

---

## #1 — Frame polarity is inverted from what Addendum §5 states

**Addendum §5 says:** "Waveshare convention: **1 = white**, MSB first."

**Bit order: CONFIRMED.** MSB is the leftmost pixel. Tested: an 8×2 mode-`1` image with
black at `x=0` of row 0 yields byte `0x7F` (`01111111`) — the high bit is the pixel at
x=0.

**Polarity: REFUTED.** On the wire, **1 = black**. Two independent pieces of evidence in
the vendored driver:

1. `getbuffer()` XORs every byte with `0xFF`, with the comment:
   > *"the bytes need to be inverted, because in the PIL world 0=black and 1=white, but
   > in the e-paper world 0=white and 1=black"*

2. `Clear()` — which by definition produces a **white** screen — sends `[0x00]*48000`
   to command `0x13` (the B/W image register). Therefore `0` is white and `1` is black.

`display(image)` passes its argument straight to `0x13`, so `display()` expects
`getbuffer()` output, i.e. **1 = black**.

### Consequence

Addendum §5's frame format says the payload is "Produced by Pillow: mode `1` image →
`.tobytes()`". Pillow's `.tobytes()` gives **1 = white**. Serving that raw makes every
pixel-push client render a perfect photographic negative — precisely the bug §5 warns
about in its own callout.

### Required correction to Addendum §5

> **Waveshare convention: 1 = black, 0 = white, MSB first.**
> The frame endpoint serves `bytes(b ^ 0xFF for b in img.tobytes())` — equivalently,
> exactly what `epd.getbuffer()` returns. Never serve raw `.tobytes()`.

Verified test vector for bring-up (an asymmetric pattern, per §5's own advice — a
checkerboard cannot detect this):

| | byte 0 | byte 1 |
|---|---|---|
| 8×2 image, black at (0,0) and (7,1) | | |
| PIL `.tobytes()` | `0x7F` | `0xFE` |
| **on the wire (correct)** | **`0x80`** | **`0x01`** |

---

## #2 — The "no 48KB buffer" constraint is self-imposed

Two problems with the assumption as written.

**It is harder than stated.** `display()` writes to *two* registers: `0x10` (the previous
frame, bit-inverted) and `0x13` (the new frame). A single HTTP response body can only be
streamed once. A device that streams straight to the panel can therefore only feed one of
the two registers.

**It is also unnecessary.** The recommended `epaper_client` hardware — the Waveshare
e-Paper ESP32 Driver Board — is an ESP32-WROOM-32 with **520KB SRAM**. A 48,000-byte
frame is ~9% of that. Buffering the whole frame is entirely affordable and removes the
constraint that makes the two-register problem hard.

**Recommendation:** drop the streaming requirement for `epaper_client`. Buffer the frame,
then drive `0x10` and `0x13` from it. Keep streaming as an optimisation only if a future
device is genuinely memory-constrained — the C3 Super Mini would be, but it is on the
data-push path and never receives frames.

Still needs a prototype: whether writing only `0x13` (skipping `0x10`) gives a clean full
refresh on this panel. If it does, streaming becomes viable after all.

---

## #3 — `display_Partial()` has three defects; do not call it directly

**a) The x-alignment guard is broken Python.**

```python
if((Xstart % 8 + Xend % 8 == 8 & Xstart % 8 > Xend % 8) | Xstart % 8 + Xend % 8 == 0 | (Xend - Xstart)%8 == 0):
```

In Python, `&` and `|` bind **tighter than comparison operators**. Parsing this with `ast`
confirms it is not the intended boolean logic at all — it is a *chained comparison*
(`ops=[Eq(), Gt()]`) whose comparators are bitwise sub-expressions.

Its behaviour is therefore incidental rather than designed. Tested:

| `Xstart`, `Xend` | guard returns |
|---|---|
| 16, 280 (both 8-aligned — spec §9's window) | `True` ✓ |
| 17, 281 | `False` |
| 20, 100 | `False` |

It happens to take the correct branch for already-aligned coordinates, which is the only
reason this has not bitten anyone. **Mitigation: always pass pre-aligned coordinates** and
never depend on the guard to normalise them. Spec §9's window already qualifies.

**b) It expects a window-sized buffer, not a full-screen one.**

```python
Width = (Xend - Xstart) // 8
for j in range(Height):
    for i in range(Width):
        image1[i + j * Width] = ~Image[i + j * Width]
```

`Image` is indexed at `Width` bytes per row, where `Width` is the **window** width in
bytes (33 for our clock window), not the screen's 100. Passing `epd.getbuffer(full_image)`
reads the wrong rows entirely. The caller must **crop to the window first**, then pack.

**c) Its polarity is the opposite of `display()`'s.**

`display()` sends its argument unmodified to `0x13`, so it wants **1 = black**
(`getbuffer()` output). `display_Partial()` applies `~` before sending to `0x13`, so it
wants **1 = white** (raw PIL `.tobytes()`). Feeding `getbuffer()` output to
`display_Partial()` produces an inverted clock.

**d) Undocumented in both specs:** partial refresh requires `init_part()`, not `init()`.
The driver exposes `init()`, `init_fast()`, `init_part()` and `init_4Gray()`.

### Recommendation

`epaper/driver.py` should **not** call `display()`/`display_Partial()` directly. Wrap the
panel with our own thin functions that own cropping, packing and polarity explicitly, and
call `send_command`/`send_data2`. This is a handful of lines, makes the polarity a single
auditable place shared with the frame endpoint, and avoids inheriting (a)–(c).

---

## #4 — Chromium at 800×480 SF1: CONFIRMED

Rendered a representative page (real type scale, Inter loaded from a local file) with:

```
--headless --disable-gpu --no-sandbox --force-device-scale-factor=1
--window-size=800,480 --hide-scrollbars --default-background-color=FFFFFFFF
```

Output PNG: **exactly 800×480**. No Retina doubling on a 2× display — the flag holds.

Two additions worth carrying into `render.py` that the spec omits: `--hide-scrollbars`
(a scrollbar steals ~15px of width and silently shifts the right column) and
`--default-background-color=FFFFFFFF` (prevents a transparent backdrop thresholding to
black).

Re-verify on the Pi once Chromium is installed — Linux font rasterisation differs (see #5).

---

## #5 — Threshold 160: CONFIRMED, but it is not the load-bearing rule

Measured grey content over the same page, with and without the spec's global
`-webkit-font-smoothing: none`:

| Render | Intermediate px | black @128 | black @160 | difference |
|---|---|---|---|---|
| `font-smoothing: none` | 20 (**0.01%**) | 12,212 | 12,212 | **0** |
| default antialiasing | 10,842 (**2.82%**) | 12,623 | 13,834 | **1,211** |

Two conclusions:

1. **When smoothing is off, threshold 128 and 160 produce byte-identical output.** No
   pixel lands in the 129–160 band. The threshold is a safety net, not a mechanism.
2. **When smoothing is on, 160 recovers 1,211 pixels that 128 discards** — about 10% more
   ink, concentrated in exactly the thin strokes and hairlines the spec cares about. The
   spec's reasoning is sound; it just fires only in the failure case.

**The rule actually doing the work is `-webkit-font-smoothing: none`.** That is a
significant shift in where the risk sits, because on **macOS** that property reliably
disables smoothing, while on **Linux** Chromium's rasterisation is governed by fontconfig
and the property is not honoured the same way. The Pi may antialias regardless.

**Action:** keep threshold 160. Add a check at milestone 2 that measures the intermediate-
pixel fraction of the Pi's own render. If it is not ~0%, the panel is relying on the
threshold rather than on aliased text, and the 10px tier needs re-examination on glass.

### Bonus: the dotted/solid rule hierarchy survives the pipeline

Spec §3 distinguishes section boundaries (solid) from item separators (1px dotted).
Measured across the 764px content width: solid rules render **764/764** dark pixels,
dotted rules render **383/764** in a clean 1-on-1-off stipple. The distinction is real at
the pixel level. Whether a 1px stipple reads as a line or as noise **on glass** is a
milestone-2 question.

---

## #6 / #7 — Open, blocked on hardware

**#6 (10px legibility)** is unchanged as the highest-severity risk and still gates the
whole type scale. At pixel level, 10px Inter at `letter-spacing: 0.14em` produces
distinct, unbroken glyphs — necessary but not sufficient. Real glass decides.

Blocked on: SPI enabled, panel wired, milestone 2.

**#7 (C3 WiFi at the radar's location)** requires the device in place. Note the Pi's own
WiFi currently reports a healthy path, but the Pi is on ethernet and is not a proxy for a
C3's PCB antenna.

---

## #8 — GxEPD2 partial refresh: CONFIRMED, with a timing correction

`GxEPD2_750_T7` (the 800×480 7.5" V2 class):

| Constant | Value |
|---|---|
| `hasPartialUpdate` | `true` |
| `hasFastPartialUpdate` | `true` |
| `full_refresh_time` | 4200 ms |
| `partial_refresh_time` | **1600 ms** |

`refresh(x, y, w, h)` and `writeImagePart()` provide the region API, and GxEPD2 handles
8px alignment internally.

**Correction to original spec §2.** The spec's panel table claims partial refresh is
**"~0.3 s"**. GxEPD2's constant for this exact panel is **1600 ms** — over 5× slower. The
0.3s figure is plausible for a small 2.13" panel, not for a 7.5".

This does not break the design — a 1.6s partial on the minute is still far better than a
4.2s full refresh — but it changes two things:

- The tick is **visible**, not instantaneous. Worth seeing on glass before committing to a
  60s cadence.
- Spec §12's "partials are cheap" wear calculation should be revisited with the real
  number, measured at milestone 5.

---

## Conflicts between the two documents

Addendum §0.4 asks for these. The addendum wins where they disagree, except where noted.

**C1 — The Pi is no longer idle between cycles.** Original §1 states "Between cycles the
Pi is idle" and "There is one optional long-running process". `serve.py` is a **mandatory
always-on daemon**. Original §1 and §12's framing are superseded; `serve.py` needs its own
always-on systemd unit (`Type=simple`, `Restart=always`), not a timer.

**C2 — Two HTTP servers is one too many.** Original §13 specifies a dev-only Flask preview
server on port 8080. `serve.py` is a superset. **Merge them:** `serve.py` absorbs the
preview role behind a dev-only route. Original §13 as a separate `preview.py` is dropped.

**C3 — The protocol cannot express *which region* to partial-refresh.** This is a genuine
gap, not a conflict. Addendum §5 defines `ETag`, `X-Poll-Seconds` and `X-Full-Refresh` —
the server owns *whether* to do a full refresh, but never says *where* a partial one
applies. A pixel-push client receiving a full 48,000-byte frame cannot know which pixels
changed without diffing against the previous frame, which needs a second 48KB buffer and
contradicts assumption #2.

> **Proposed addition:** `X-Partial-Window: x0,y0,x1,y1` (x aligned to 8), sent alongside
> a body cropped to that window. The device writes exactly those bytes. This keeps the
> server owning the refresh policy — consistent with `X-Full-Refresh`'s intent — and keeps
> the device buffer-free for ticks.

**C4 — The partial-refresh window must become per-template.** Original §9 fixes the clock
window at `(16,72)–(280,140)` as a global constant. Addendum §6 parameterises templates by
dimensions, and `dashboard_compact` on a different-sized panel will not have its clock
there. The window becomes a **property of the template**, emitted into `X-Partial-Window`.

**C5 — The ghosting counter must become per-device.** Original §12 persists one partial-
refresh counter in `cache/refresh_state.json`. With N devices refreshing independently,
this must be keyed by `device_id`, and `X-Full-Refresh` derived per device.

**C6 — `poll_seconds` is defined twice.** Config §6 sets it per device; `X-Poll-Seconds`
sends it per response. State the direction explicitly: **config is the source of truth,
the header is its projection**, so cadence can be changed server-side without reflashing —
which is the header's stated purpose.

**C7 — Radar polling would move the rate-limit problem, not solve it.** Config §6 sets the
radar to `poll_seconds: 5` — 17,280 requests/day/device. Addendum §8 correctly says
fanning one Pi fetch out to N devices fixes upstream rate limits, but only if the server
**decouples** its upstream fetch cadence from the device poll cadence. `sources/adsb.py`
must write `cache/feed/radar.json` on its own timer and `serve.py` must serve from that
cache, never fetching upstream on a device request. Worth stating, because the obvious
implementation does the opposite.

**C8 — Internal inconsistency in the addendum.** §5 says polarity must be verified "on day
one"; §12's risk table schedules that verification at **step 6**. Day one is right — and
it is now resolved from source anyway (#1). Fold the asymmetric-pattern check into **step
1** (hello-world), where it costs nothing.

---

## Revised build order

Moved to `PLAN.md`, which is the living build plan. It supersedes SPEC §14 and
ADDENDUM §11, reorders to start with the radar (no new hardware required), and treats
all-displays-as-clients as the target architecture with the wired panel retained as a
bring-up harness.

---

## Findings added after the initial pass

### F1 — SPEC §2's wiring table is missing the PWR pin

`epdconfig.py` defines `PWR_PIN = 18` and `module_init()` opens with
`self.GPIO_PWR_PIN.on()`. SPEC §2 lists VCC, GND, DIN, CLK, CS, DC, RST and BUSY — **no
PWR row**. Wire it as the spec lists and the panel never powers up, with no error message.

**Add: PWR — BCM 18 — physical pin 12.** Stacking the Driver HAT on the 40-pin header
supplies it automatically; this only bites when remote-mounting on the PH2.0 cable, which
SPEC §2 explicitly recommends.

### F2 — Panel choice reconfirmed: no 7.5" alternative supports partial refresh

Checked every 7.5" variant in the vendored driver:

| Driver | Resolution | `display_Partial` | `init_part` |
|---|---|---|---|
| **`epd7in5_V2`** | **800×480** | **yes** | **yes** |
| `epd7in5_HD` | 880×528 | no | no |
| `epd7in5b_V2` | 800×480 B/W/red | yes | yes |
| `epd7in5` (V1) | 640×384 | no | no |

The 880×528 HD is tempting — more pixels directly de-risks the 10px legibility gate — but
it has **no partial-refresh implementation at all**, so no ticking clock, which is the sole
reason the design is 1-bit rather than 4-grey. Waveshare's own documentation describes
partial and fast refresh as V2 features. The B/W/red variant is the slow three-colour
trade the SPEC appendix already rejected.

**Conclusion: `epd7in5_V2` 800×480 is not a preference, it is the only option that meets
the requirements.**

### F3 — Waveshare's Arduino library cannot tick the clock

`Arduino/epd7in5_V2/` exists — confirming the Driver HAT drives non-Pi MCUs over plain SPI
(RST, DC, CS, BUSY, PWR) — but it contains **no `DisplayPartial` and no `Init_Part`**.

Any C3 client must use **GxEPD2** (`GxEPD2_750_T7`, `hasFastPartialUpdate = true`), not
Waveshare's Arduino code. Note this means the client path runs a *different driver
implementation* from the one findings #1 and #3 were established against — see PLAN §4,
V9.

### F4 — Serving the radar from a cache breaks its dead reckoning three ways

The most consequential finding for Phase A. `radar_display.cpp:534` extrapolates from
`pos_age_s + fetch_age_s` against a `kExtrapolationHorizonSec` of only **12 s**, where
`pos_age_s` is the fix's age at the API and `fetch_age_s` is time since *the device's own*
fetch.

Inserting the Pi between them:

1. **Under-extrapolation** — cache dwell time is invisible to the device. At ~250 m/s a
   4 s cache age is ~1 km of error, and 4 s is a third of the entire horizon.
2. **Late dimming** — the `pos_age_s >= horizon` test fires late, so stale targets render
   as fresh.
3. **Stall detection breaks** — today a stalled feed is caught because the device's fetch
   fails. Via the Pi the device's fetch keeps succeeding while the data rots, so
   minute-old traffic shows as live with no indication.

Fixes, both cheap: the server recomputes age **at serve time**
(`api_seen_pos + (now − server_fetch_time)`), which keeps the device's existing arithmetic
correct with no render-path change; and the response carries `feed.age_s` / `feed.ok` for
the firmware to test as a **third** staleness cause alongside `fetch_age_raw` — never in
place of it. Substituting would remove the device's only detection of *its own* link
failing: both causes would then derive from Pi-side timestamps carried in the body, so a
device that loses the LAN sees both frozen and never dims.

`radar_display.cpp` tests the two staleness causes separately and deliberately — its
comment records that summing them made targets blink once per cycle. Preserve that
structure and add the new cause as a third `||` term. Do not merge them, and do not
substitute. Full endpoint schema in `PLAN.md` §3.

---

## What must change in the documents

- **Addendum §5** — polarity is `1 = black`, and the frame body is `getbuffer()` output,
  never raw `.tobytes()`.
- **Addendum §5** — add `X-Partial-Window` (C3).
- **Addendum §9 #2** — drop the no-buffer requirement for `epaper_client`.
- **Addendum §12** — polarity is verified at step 1, not step 6.
- **Spec §2** — partial refresh is ~1.6s, not ~0.3s.
- **Spec §13** — `preview.py` is dropped; `serve.py` absorbs it.
- **Spec §9** — the partial-refresh window is per-template, not a global constant.
- **Spec §12** — the refresh counter is per-device.
- **Spec §2 wiring table** — add **PWR, BCM 18, physical pin 12** (F1).
- **Spec §2** — panel choice is forced, not preferred: `epd7in5_V2` is the only 7.5"
  variant with partial refresh (F2).
