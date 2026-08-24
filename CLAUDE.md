# HomeScreen — E-Paper Desk Dashboard

Raspberry Pi 4 as a **display backend**. Wired Waveshare 7.5" e-paper desk panel
(800×480, 1-bit) plus HTTP display clients on the LAN. Madrid. Spanish UI.

## Documents, in precedence order

1. `docs/PLAN.md` — **the living build plan.** What to do next, the open questions, and
   the hardware decision. Start here.
2. `docs/VALIDATION-01.md` — test results that correct both documents below. Where it
   contradicts them it wins, because its claims were executed rather than reasoned.
3. `docs/ADDENDUM-01-multi-display.md` — the multi-display architecture. Wins over SPEC.
4. `docs/SPEC.md` — the original single-panel spec. Still in force for everything the
   addendum does not touch: the whole 1-bit design system, layout geometry, data sources,
   failure rules, and the appendix of settled decisions.

Do not relitigate the appendix decisions in SPEC.md or the design system in its §3.

---

## 1. What this is

**Not a web app, not a kiosk browser.** The panel is an SPI peripheral with no
framebuffer. There is no X server.

The Pi does all the heavy work — API orchestration, credential storage, text layout,
thresholding — so that displays never have to. Two payload patterns, routed on what the
device must draw:

| Pattern | Server sends | Use when |
|---|---|---|
| **Pixel push** | finished 1-bit framebuffer (48,000 B) | device lays out **text** |
| **Data push** | small precomputed JSON (<1 KB) | device draws **geometry**, or interpolates between polls |

Decision rule: **text layout → server renders; geometry → device renders.**

**Target architecture: the Pi is a pure backend in a cupboard, and every display is an
HTTP client.** Chosen over a Pi-attached panel mainly on thermal grounds — SPEC §2 and §16
exist to manage a Pi 4 at 60–70°C behind glass rated to 40°C, and a C3 removes that problem
along with the ~30mm depth, the air gap and the vent slots.

The **wired panel is still built, as a bring-up harness rather than the destination**: it
costs ~20 lines because `render.py` produces a buffer either way, it answers the legibility
gate with no firmware at all, and it stays in the tree as the way to tell a render bug from
a wire bug.

Device classes: `epaper_client` (C3 + Driver HAT + 7.5" panel, pixel push),
`gc9a01_client` (C3 + GC9A01 round display, data push — the existing plane radar), and
`epaper_wired` (the harness).

Render cycle for the wired panel:

```
timer → fetch APIs → JSON caches
      → headless Chromium screenshots local HTML → PNG
      → Pillow hard-thresholds to 1-bit → cache/render/{id}.bin
      → SPI push → epd.sleep() → exit
```

`serve.py` is a **mandatory always-on daemon** (systemd `Type=simple`, `Restart=always`)
— not a timer, and not the optional dev-only preview server SPEC §13 described. It
absorbs that role; there is no separate `preview.py`.

Everything is mains-powered. No deep sleep anywhere. Firmware is a plain loop, which
means devices need a **watchdog + restart-on-N-failures** and **OTA from day one**.

---

## 2. Actual device state (measured 2026-08-24)

The specs assume a provisioned Bookworm Pi. Ours is neither provisioned nor Bookworm.
**Trust this table over SPEC §2/§4.**

| | Spec assumes | Actually installed |
|---|---|---|
| OS | Raspberry Pi OS Bookworm | **Debian 13 trixie**, kernel 6.18, aarch64 |
| Python | 3.11-ish | **3.13.5**, PEP 668 externally-managed |
| RAM | 2GB+ | 1.8 GiB usable |
| Storage | SSD preferred | **microSD 15G**, no SSD, no log2ram |
| SPI | enabled | **NOT enabled** — no `/dev/spidev*` |
| Chromium | installed | **not installed** (apt candidate 151.x) |
| git, fontconfig, `iw` | installed | **not installed** |
| `python3-lgpio`, `python3-gpiozero` | needed | present ✓ |
| Network | WiFi | **eth0 192.168.1.116 (primary)** + wlan0 192.168.1.99 |
| e-paper panel | "already owned" | **not purchased yet** — see `PLAN.md` §6 |

- **No e-paper hardware exists yet.** SPEC's BOM marks the *Pi* as already owned; the
  panel is not. Phase A of `PLAN.md` is deliberately radar-only so work proceeds while the
  panel is in transit.
- **SPI must be enabled before phase B1**: `sudo raspi-config nonint do_spi 0`, reboot,
  confirm `/dev/spidev0.0`. The existing `dtoverlay=nospi10` in `config.txt` concerns a
  different bus and can stay.
- **`dashboard.local` resolves to eth0.** SPEC §4's WiFi power-save hardening addresses a
  failure mode we do not currently have, and `iw` isn't installed. Do it only if the Pi
  moves to WiFi-only. Don't fix an absent problem.
- **PEP 668 is active.** Never reach for `--break-system-packages`; the venv is mandatory
  anyway.
- **SD card, not SSD.** Cap journald retention before timers run 24/7. SPEC §16's SD-wear
  risk is live, not mitigated.
- Trixie packages the browser as `chromium`, not `chromium-browser`.

---

## 3. Access

```bash
ssh pi@dashboard.local        # key auth is installed for this workstation
```

Credentials are not recorded here — this repo is public. If key auth stops working,
re-run `ssh-copy-id`.

The Pi is a **deploy target, not the dev machine** (see §5).

---

## 4. Repo layout

SPEC §5 plus ADDENDUM §7: `render.py` takes a `display_id` and writes
`cache/render/{id}.bin`; `serve.py` owns the endpoints; `sources/adsb.py` feeds the
radar; `firmware/` holds the ESP32 projects. Build it out as milestones land rather than
scaffolding empty modules.

This repo is the source of truth; `~/dashboard` on the Pi is a deployment of it.
`cache/` and `venv/` are Pi-side artifacts and stay gitignored.

---

## 5. Development workflow

**Design and tune HTML/CSS locally against fixture JSON.** The page is a static 800×480
document; Chrome on the Mac renders it faithfully enough for layout work, and `--headless
--force-device-scale-factor=1` was verified to produce exactly 800×480 with no Retina
doubling. Iterating over SSH on a multi-second refresh is the slow path.

Only three things genuinely require hardware:

1. **Whether 10px type is legible on real glass** — the highest-severity open risk; it
   gates the entire type scale (PLAN.md phase B2).
2. **Whether the Pi's Chromium actually honours `-webkit-font-smoothing: none`** — see §6.
3. Ghosting behaviour and real partial-refresh duration.

---

## 6. Invariants — breaking any of these is a bug, not a style choice

**Colour**
- Only `#000000` and `#ffffff` in the compiled CSS. The v6 mockup's `#6b6b6b` /
  `#a8a8a8` / `#d4d4d4` are bugs. No greys exist: 4-grey mode would forfeit partial
  refresh, and we need a ticking clock.
- Hierarchy comes from **size and weight only** — 13px/500 primary, 13px/400 secondary,
  11px/400 tertiary.

**Type**
- Nothing below **10px**, anywhere. That is the floor, not a target.
- Fonts installed system-wide, referenced locally. **Never a CDN web font** — a slow
  network at boot silently swaps in a fallback face and the layout breaks.
- Global `-webkit-font-smoothing: none; text-rendering: geometricPrecision;`. **This is
  the rule that actually keeps the render binary**, not the threshold — measured, see
  `VALIDATION-01.md` #5. Its behaviour on Linux is fontconfig-dependent and unverified.

**Render pipeline**
- Viewport exactly `800×480` at `--force-device-scale-factor=1`.
- Also pass `--hide-scrollbars` (a scrollbar steals ~15px and shifts the right column)
  and `--default-background-color=FFFFFFFF` (a transparent backdrop thresholds to black).
- Threshold **160, not 128**, and **no dithering**. With smoothing off the two are
  byte-identical; 160 is the safety net for when smoothing fails.

**Panel — corrected against the driver source, see `VALIDATION-01.md` #1 and #3**
- **On the wire, `1 = black`, `0 = white`, MSB first.** The addendum says the opposite;
  the addendum is wrong. Frame payloads are `epd.getbuffer()` output — equivalently
  `bytes(b ^ 0xFF for b in img.tobytes())`. **Never serve raw Pillow `.tobytes()`**, which
  is `1 = white` and renders a photographic negative.
- **Do not call the vendored `display_Partial()` directly.** It has a broken x-alignment
  guard, expects a *window-cropped* buffer rather than a full-screen one, and takes the
  *opposite* polarity to `display()`. Wrap the panel in `epaper/driver.py` and own
  cropping, packing and polarity in one auditable place shared with the frame endpoint.
- Partial refresh needs `init_part()`, not `init()`.
- **Wire the PWR pin — BCM 18, physical 12.** `module_init()` asserts it and SPEC §2's
  wiring table omits it; without it the panel never powers up and says nothing about why.
- The panel choice is forced, not preferred: `epd7in5_V2` is the **only** 7.5" variant
  with partial refresh. The 880×528 HD has none. Do not substitute.
- On a C3 client use **GxEPD2**, never Waveshare's Arduino library — the latter has no
  partial refresh, so no ticking clock.
- **`epd.sleep()` on every code path, including exceptions.** Wrap in `try/finally`.
  Omitting it is the most common cause of a dead panel.
- Partial-refresh x-bounds must be multiples of 8, and the window is a **per-template
  property**, not a global constant. The desk template's is `(16,72)–(280,140)`, width
  264 = 33×8. That rectangle must contain **only** the Madrid numerals.
- Take a lock file before touching SPI. Two concurrent writers corrupt the display.
- Partial refresh is **~1.6s** (GxEPD2's constant for this panel), not the ~0.3s SPEC §2
  claims. Measure it for real at phase B5.

**Protocol**
- Plain HTTP on the LAN, no TLS.
- `config.yaml` is the source of truth for cadence; `X-Poll-Seconds` is its projection,
  so cadence changes without reflashing.
- The ghosting counter is **per-device**, keyed by `device_id`.
- `serve.py` serves feeds **from cache only**. It must never fetch upstream on a device
  request, or N devices polling every 5s just relocates the rate-limit problem to the Pi.

**Failure behaviour**
- **No fetcher may raise into the render path.** On failure keep the previous cache and
  set `ok: false`.
- Sections **collapse** when empty — never an empty rectangle sitting on the panel for
  six hours.
- Stale data is **shown**, not hidden; mark with a tertiary `·` past 1 hour.
- The masthead timestamp reflects the **oldest** successful fetch, so a silently dead
  fetcher is visible.
- Devices keep their last frame on fetch failure.

**Dependencies**
- venv created with `--system-site-packages`; the Waveshare `epdconfig.py` needs apt's
  `lgpio`/`gpiozero`.
- `recurring-ical-events` is required — plain `icalendar` does not expand RRULEs.
- `RPi.GPIO` does not work here. Any tutorial using it is stale.

---

## 7. Build order

**In `docs/PLAN.md`.** Summary: Phase A moves the plane radar onto the Pi first — no new
hardware, validates the whole server→client half on the simpler data-push path, and frees
~33 KB of TLS heap on the C3 as a side effect. Phase B builds the panel wired, gated on
the 10px legibility question. Phase C moves the panel onto a client. Phase D is the
remaining content.

Keep the phase tables in `PLAN.md` current; do not duplicate them here.

## 8. Verification before claiming done

Do not report a milestone complete without running the check and reading the output.

- **CSS colour audit** — anything in the compiled CSS that is not `#000`/`#fff` fails.
- **Type floor** — no `font-size` below 10px.
- **Polarity** — assert the known-good vector: an 8×2 image with black at (0,0) and (7,1)
  must serialise to `0x80 0x01` on the wire.
- **Grey fraction** — measure the intermediate-pixel share of the Pi's own render. Near 0%
  means smoothing is off. Anything higher means the threshold is load-bearing and the 10px
  tier needs re-checking on glass.
- **`epd.sleep()` reachability** — every panel code path, exceptions included.
- **Fetcher kill test** — break each source (bad key, no network, malformed response) and
  confirm the panel still renders with that section collapsed.
- **Visual check** — render and actually look at the image before saying it works.

Full acceptance criteria in SPEC §15.

---

## 9. Conventions

- **UI copy is Spanish.** Code, comments, commits and docs are English.
- Config lives in `config.yaml`. Nothing hardcoded that belongs there.
- API keys and the secret ICS URL are secrets — gitignored local config, never committed.
- Fetchers write `cache/<name>.json` as `{ fetched_at, ok, error, data }`. No exceptions
  to the shape.
