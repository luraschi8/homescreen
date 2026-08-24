# E-Paper Desk Dashboard — Build Specification

**Target:** Raspberry Pi 4 + Waveshare 7.5" e-Paper HAT (V2), 800×480, 1-bit
**Location:** Madrid, Spain · Spanish-language UI
**Status:** Design locked (v6 mockup). This document is the implementation brief.

---

## 1. What this is (read this first)

This is **not a web application** and **not a kiosk browser**. The e-paper panel is an
SPI peripheral with no framebuffer — you cannot point a browser at it, there is no X
server, and nothing runs continuously.

The system is a **batch job on a timer**. Each cycle:

```
timer fires
  → fetch APIs, write JSON caches
  → headless Chromium screenshots a local HTML file → PNG
  → Pillow thresholds PNG to 1-bit
  → Waveshare driver pushes buffer over SPI
  → epd.sleep()
  → process exits
```

Between cycles the Pi is idle. Chromium is used purely as a rendering engine — a
one-shot invocation that exits immediately.

There is one optional long-running process: a small preview server (§13) used only
during development.

---

## 2. Hardware

### Bill of materials

| Item | Notes |
|---|---|
| Raspberry Pi 4 (2GB+) | Already owned |
| Waveshare 7.5inch e-Paper HAT (V2) | **Must select "driver HAT included"** at purchase |
| microSD 32GB A2, or USB SSD | SSD strongly preferred for 24/7 logging |
| Official USB-C PSU 5.1V 3A | |
| Right-angle USB-C adapter | Port protrudes from Pi's side; dictates enclosure width otherwise |
| BME280 breakout (optional) | I²C indoor temp/humidity |
| M2.5 heat-set inserts + standoffs | For enclosure |

**No PoE HAT** — decided against: adds ~20mm stack height, a second heat source next
to a panel rated to ~40°C, and a fan. WiFi + USB-C instead.

### Panel specifications

| Property | Value |
|---|---|
| Driver module | `waveshare_epd.epd7in5_V2` |
| Resolution | 800 × 480 |
| Active area | 163.2 × 97.92 mm |
| Outline | 170.2 × 111.2 mm |
| Effective DPI | ~124.5 (4.9 px/mm) |
| Full refresh | ~4 s |
| Partial refresh | Supported, ~0.3 s |
| Colour mode | 1-bit (see §3 — critical) |
| Max operating temp | ~40°C |

### Wiring (BCM numbering)

Mount the driver HAT **remotely** via the PH2.0 8-pin cable rather than stacking it on
the GPIO header. This keeps the panel away from the Pi's heat and decouples enclosure
layout from the short FFC ribbon.

| Panel signal | BCM | Physical pin |
|---|---|---|
| VCC | — | 3.3V (pin 1) |
| GND | — | GND (pin 6) |
| DIN (MOSI) | GPIO 10 | 19 |
| CLK (SCLK) | GPIO 11 | 23 |
| CS | GPIO 8 | 24 |
| DC | GPIO 25 | 22 |
| RST | GPIO 17 | 11 |
| BUSY | GPIO 24 | 18 |

### Enclosure constraints

- Active area is **not centred** on the panel outline; offsets are asymmetric with more
  dead glass on the ribbon edge. Pull exact offsets from Waveshare's mechanical drawing
  before cutting the bezel aperture.
- Keep the Pi physically offset from the panel back, with an air gap and vent slots.
  A Pi 4 in a sealed box reaches 60–70°C; the panel is rated to ~40°C. Heat causes
  ghosting and permanent degradation.
- **No acrylic front cover.** It is a reflective display with no backlight — a clear
  cover adds glare and destroys the paper-like quality. Use a recessed bezel over bare
  glass.
- Plan ~30mm depth (Pi + standoffs). 15–20° desk tilt reads best at seated height.

---

## 3. The 1-bit constraint (affects every design decision)

The panel supports a 4-grey mode, **but 4-grey does not support partial refresh.** We
need a ticking clock, therefore we run **1-bit mode**, therefore **there are no greys**.

The v6 mockup uses `#6b6b6b`, `#a8a8a8`, `#d4d4d4` for hierarchy. Those must not be
carried into the implementation as-is. At 10–13px, grey text becomes speckled dither
noise and looks broken.

### Substitution rules

| Mockup usage | 1-bit implementation |
|---|---|
| Grey body text (`#6b6b6b`, `#a8a8a8`) | Black at reduced size and/or weight 400 |
| Grey section labels | Black, 10px, weight 500, `letter-spacing: 0.14em` |
| Hairline rules (`#ececec`, `#d4d4d4`) | `border-top: 1px dotted #000` |
| Major rules (`#000`) | `border-top: 1px solid #000` |
| Silhouettes (obelisk `#e8e8e8`) | 1px solid black **outline only**, no fill |
| Inverted pills | Unchanged — solid black fill, white text |

**Only `#000000` and `#ffffff` may appear in the CSS.** Any other value is a bug.

Hierarchy is carried by **size and weight**, not tone. Three tiers:

- **Primary** — 13px / weight 500 (next event, delivery arriving today)
- **Secondary** — 13px / weight 400
- **Tertiary** — 11px / weight 400

### Typography

- Body/UI: **Inter** (or IBM Plex Sans). Install system-wide to
  `~/.local/share/fonts/`, then `fc-cache -fv`.
- **Never load web fonts over CDN.** On a boot where WiFi comes up slowly, Chromium
  renders with a fallback face and the layout silently breaks.
- Disable subpixel antialiasing globally:
  ```css
  * { -webkit-font-smoothing: none; text-rendering: geometricPrecision; }
  ```
- Minimum type size anywhere on the panel: **10px** (~0.08mm stroke at 124 DPI). Do not
  go below this. The v6 mockup's 8px labels scale to 10px at true resolution — that is
  the floor, not a target.

---

## 4. Software installation

```bash
# Enable SPI
sudo raspi-config nonint do_spi 0

sudo apt update && sudo apt full-upgrade -y
sudo apt install -y git python3-venv chromium fontconfig \
                    python3-lgpio python3-gpiozero
```

Bookworm packages the browser as `chromium`; older images use `chromium-browser`.
Detect at install time.

```bash
mkdir -p ~/dashboard && cd ~/dashboard
python3 -m venv --system-site-packages venv
venv/bin/pip install pillow requests icalendar recurring-ical-events pyyaml

git clone --depth 1 https://github.com/waveshareteam/e-Paper /tmp/ep
cp -r /tmp/ep/RaspberryPi_JetsonNano/python/lib/waveshare_epd ~/dashboard/
rm -rf /tmp/ep
```

**`--system-site-packages` is required.** The Waveshare driver's `epdconfig.py` needs
`lgpio`/`gpiozero` from apt. `RPi.GPIO` does not work on Bookworm — if you find a
tutorial using it, the tutorial is stale.

**`recurring-ical-events` is required.** Plain `icalendar` does not expand RRULEs, so
recurring meetings appear exactly once, on their original creation date.

### WiFi hardening

```bash
sudo iw wlan0 set power_save off
```

Make persistent via a systemd unit. Raspberry Pi OS enables WiFi power management by
default; on an idle headless Pi this causes intermittent unreachability over SSH and is
the single most common "my Pi keeps dropping off the network" cause.

Prefer 2.4GHz unless the router is close — range matters more than throughput for a few
KB of JSON every ten minutes.

Set WiFi credentials, hostname, and SSH public key in **Raspberry Pi Imager** before
flashing. First boot then joins the network with no monitor or keyboard required.

---

## 5. Repository layout

```
~/dashboard/
├── config.yaml              # all user configuration (§6)
├── venv/
├── waveshare_epd/           # vendored driver
├── fetch.py                 # entry: refresh all data caches
├── render.py                # entry: HTML → PNG → 1-bit → panel (full refresh)
├── tick.py                  # entry: clock-only partial refresh
├── preview.py               # entry: dev-only HTTP preview server
├── sources/
│   ├── __init__.py
│   ├── weather.py           # Open-Meteo
│   ├── calendar_ics.py      # ICS + RRULE expansion
│   ├── rates.py             # dolarapi
│   ├── quotes.py            # Twelve Data / Stooq + CoinGecko
│   ├── sports.py            # football-data.org + TheSportsDB
│   └── deliveries.py        # Gmail parsing
├── epaper/
│   ├── __init__.py
│   ├── driver.py            # panel wrapper, refresh policy, ghosting counter
│   └── dither.py            # PNG → 1-bit conversion
├── templates/
│   └── dashboard.html.j2
├── static/
│   ├── style.css
│   └── icons/               # inline SVG, Tabler outline set
├── cache/                   # gitignored, one JSON per source
│   ├── weather.json
│   ├── calendar.json
│   └── ...
└── systemd/
    ├── dashboard-update.service / .timer
    └── dashboard-tick.service / .timer
```

---

## 6. Configuration schema (`config.yaml`)

```yaml
location:
  name: Madrid
  latitude: 40.4168
  longitude: -3.7038
  timezone: Europe/Madrid

secondary_clock:
  label: BS AS
  timezone: America/Argentina/Buenos_Aires

calendar:
  ics_url: "https://calendar.google.com/calendar/ical/.../basic.ics"
  max_events_today: 3
  show_tomorrow_preview: true

rates:
  enabled: true

quotes:
  provider: twelvedata          # twelvedata | stooq
  api_key: "..."
  symbols:                       # max 5 — see §9 layout constraint
    - { symbol: VWCE.DE, label: VWCE, currency: EUR }
    - { symbol: AAPL,    label: AAPL, currency: USD }
    - { symbol: NVDA,    label: NVDA, currency: USD }
    - { symbol: MELI,    label: MELI, currency: USD }
  crypto:
    - { id: bitcoin, label: BTC }

sports:
  max_fixtures: 3
  competitions: [argentina_national, primera_argentina, laliga, f1]

deliveries:
  enabled: true
  method: gmail                  # gmail | manual
  max_items: 2

refresh:
  full_minutes: 10
  tick_seconds: 60
  full_refresh_every_n_partials: 30
  quiet_hours: { start: "00:30", end: "06:30" }   # skip ticks, save panel life
```

---

## 7. Data sources

Every fetcher writes `cache/<name>.json` with this envelope:

```json
{ "fetched_at": "2026-08-24T14:32:11+02:00", "ok": true, "error": null, "data": {} }
```

**No fetcher may raise into the render path.** On failure, keep the previous cache and
set `ok: false`. See §11.

### 7.1 Weather — Open-Meteo (no key, no signup)

One call covers current conditions, hourly, 5-day, sunrise/sunset, and UV.

```
https://api.open-meteo.com/v1/forecast
  ?latitude=40.4168&longitude=-3.7038
  &current=temperature_2m,weather_code,wind_speed_10m,relative_humidity_2m
  &hourly=temperature_2m,weather_code
  &daily=weather_code,temperature_2m_max,temperature_2m_min,
         precipitation_probability_max,sunrise,sunset,uv_index_max
  &timezone=Europe/Madrid
  &forecast_days=6
```

`forecast_days=6` because day 0 is today (used for sunrise/sunset) and days 1–5 fill
the vertical forecast list.

Hourly strip shows the **next 6 hours from now**, not from midnight. Slice the hourly
array by current time.

**WMO weather code → icon mapping:**

| Codes | Icon |
|---|---|
| 0 | `sun` (or `moon` if after sunset) |
| 1, 2 | `cloud-sun` |
| 3 | `cloud` |
| 45, 48 | `mist` |
| 51–57, 61–65, 80–82 | `cloud-rain` |
| 66, 67, 71–77, 85, 86 | `snowflake` |
| 95–99 | `cloud-storm` |

Precipitation probability renders as `—` when 0, otherwise `NN%`. Values ≥50% render at
weight 500 (this is the one number that changes behaviour).

### 7.2 Calendar — ICS

Google Calendar → Settings → *Integrate calendar* → **Secret address in iCal format**.
Far simpler than OAuth (~15 lines vs several hundred) and sufficient for read-only.

Expand recurrences with `recurring_ical_events.of(cal).between(today, tomorrow_end)`.
Sort by start. Take `max_events_today`. If more exist, append a tertiary row reading
`+N más` — **never** let overflow push the deliveries or sports sections off the panel.

The next upcoming event gets the countdown pill (§10).

### 7.3 Rates — dolarapi (no key)

```
https://dolarapi.com/v1/dolares/blue      → { compra, venta, fechaActualizacion }
https://dolarapi.com/v1/cotizaciones/eur  → { compra, venta, ... }
```

Display `venta`. Arrows compare against the **previous cached value**, not against the
official rate — otherwise they point the same direction for weeks and stop meaning
anything. Persist the prior value in the cache envelope.

### 7.4 Quotes

- **Twelve Data** — `https://api.twelvedata.com/quote?symbol=X&apikey=K`. Free tier:
  800 req/day, 8 req/min. Covers US + European exchanges. Non-US symbols need an
  exchange suffix (`VWCE.DE`, `SAN.MC`).
- **Stooq** — free, no key, EOD CSV. Sufficient if daily closes are acceptable.
- **CoinGecko** — `/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true`

Budget check: 5 symbols × 6 fetches/hour × 24h = 720 req/day. Within the free tier, but
only just — batch symbols into one call where the provider supports it.

Show price + daily % change only. **No sparklines** (dropped in v6; they forced the
price down to 12px).

Outside market hours, equities return a flat 0,0% which is indistinguishable from a
broken fetch. Render a tertiary `cerrado` marker instead of the arrow when the exchange
is closed. Crypto trades 24/7 and is exempt.

### 7.5 Sports

No single free source covers Argentine domestic football, La Liga, and F1. Merge:

- **football-data.org** — free tier, La Liga + Champions + major internationals.
- **TheSportsDB** — free, covers Primera División and F1, community-maintained so
  quality varies.

Merge, sort by kickoff, take top `max_fixtures`. **Text only** — team names and times.
Do not fetch crests or logos.

### 7.6 Deliveries — Gmail

There is no public Amazon order API. Parse shipment-notification emails via the Gmail
API: query `from:shipment-tracking@amazon.es newer_than:7d`, extract item name and
promised date.

**This is the fragile component.** Amazon retouches email templates a few times a year.
Requirements:

- Parse defensively; a template change must yield an empty list, never an exception.
- On failure, the section collapses entirely (§11) — no empty rectangle.
- Log parse failures loudly so you notice before you wonder why deliveries never appear.

Fallback if this becomes tiresome: `method: manual`, reading tracking numbers from a
Google Sheet. Never breaks, and also covers Correos and SEUR which Amazon's emails
won't.

---

## 8. Rendering pipeline

### Step 1 — HTML → PNG

```bash
chromium --headless --disable-gpu --no-sandbox \
         --force-device-scale-factor=1 \
         --window-size=800,480 \
         --screenshot=/tmp/dash.png \
         file:///home/pi/dashboard/build/dashboard.html
```

Viewport must be **exactly 800×480 at scale factor 1**. Any scaling introduces
antialiasing greys that survive thresholding as noise.

### Step 2 — PNG → 1-bit

Hard threshold, **no dithering**, for text and rules:

```python
img = Image.open("/tmp/dash.png").convert("L")
bw  = img.point(lambda p: 255 if p > 160 else 0, mode="1")
```

Threshold **160, not 128**. Antialiased strokes on thin type sit in the 140–200 range;
at 128 they vanish and hairlines disappear.

Use Floyd–Steinberg (`img.convert("1")`) only if photographic content is ever added.
None currently exists, by design.

### Step 3 — push

```python
epd = epd7in5_V2.EPD()
epd.init()
epd.display(epd.getbuffer(bw))
epd.sleep()          # MANDATORY
```

**Always call `epd.sleep()`.** Leaving the panel in its driven high-voltage state
degrades it. Most "my display died after a month" reports trace back to omitting this.
Wrap in `try/finally`.

---

## 9. Layout geometry (800 × 480)

Outer padding: 18px left/right, 12px bottom.

### Vertical bands

| Band | y range | Height |
|---|---|---|
| Flag stripe (top) | 0–5 | 5 |
| Masthead content | 5–47 | 42 |
| Flag stripe (bottom) | 47–52 | 5 |
| Rule (solid) | 52–53 | 1 |
| Gap | 53–63 | 10 |
| **Main area** | 63–398 | 335 |
| Rule (solid) | 398–399 | 1 |
| Padding | 399–406 | 7 |
| **Markets band** | 406–468 | 62 |
| Bottom padding | 468–480 | 12 |

### Main area columns

Inner width 764 (800 − 36). Split 1.3 : 1 with a 26px gutter containing a 1px rule.

| Zone | x range | Width |
|---|---|---|
| Left column | 18–435 | 417 |
| Vertical rule | 448 | 1 |
| Right column | 461–782 | 321 |

**Left column** (top to bottom): clock block → agenda → deliveries → sports.
**Right column**: current conditions → hourly strip (6 cols) → 5-day vertical list.

### Markets band cells

Total 764. FX box flex 1.55, five tickers flex 1 each (6.55 units).

| Cell | Width |
|---|---|
| FX box (USD + EUR blue) | 181 |
| Each ticker | ~117 |

A heavier (solid, vs dotted) divider separates the FX box from the equities so the band
reads as two groups.

**Five tickers is the hard ceiling.** Six starts truncating symbols. If the portfolio is
longer, show top holdings by weight.

### Type scale at true resolution

Mockup values × 1.176:

| Element | Size |
|---|---|
| Madrid clock | 56px / weight 500 |
| Secondary clock | 31px / weight 500 |
| Masthead date | 15px / weight 500 |
| Section labels | 10px / weight 500 / ls 0.14em |
| Agenda + list rows | 13px |
| Tertiary rows | 11px |
| Ticker symbol | 11px / weight 500 |
| Ticker price | 16px / weight 500 |
| Ticker % change | 11px |

### Clock partial-refresh window

`display_Partial()` requires **x coordinates aligned to multiples of 8**.

| Bound | Value |
|---|---|
| x0 | 16 |
| y0 | 72 |
| x1 | 280 |
| y1 | 140 |

Width 264 = 33 × 8 ✓. This rectangle must contain **only** the Madrid numerals — no
rules, no labels, no sunrise times. Anything else inside it accumulates ghosting.

---

## 10. Visual details worth preserving

- **Masthead** — two 5px horizontal bands (flag reference) with the Sol de Mayo rendered
  as inline SVG line art: circle + 12 radiating strokes, 1.4px stroke. Public-domain
  national symbol. **Do not use the AFA crest** — trademarked.
- **Obelisco** — 1px black outline (no fill in 1-bit), sitting between the two clocks so
  it separates the cities rather than acting as a background watermark. Never place
  graphics behind text; contrast is the scarcest resource on a reflective display.
- **Inverted pills** — solid black, white text, 3px radius. Two maximum on screen: the
  imminent event countdown and a delivery arriving today. This is the loudest signal
  available without colour; using it more than twice devalues it.
- **Sun times** sit inline to the right of the Madrid clock. Grey out (→ tertiary size)
  whichever has already passed, so at 14:32 sunset carries the emphasis.
- **Rules** — solid black for section boundaries, 1px dotted black for item separators.

---

## 11. Failure and degradation

Rules, in priority order:

1. **The render never crashes.** Wrap every section in a template guard. A failed
   fetcher yields an empty section, not a traceback.
2. **Sections collapse; they do not leave empty boxes.** If deliveries returns nothing,
   that block is omitted and the remaining sections expand. Never show an empty
   rectangle for six hours.
3. **Stale data is shown, not hidden.** If a fetch fails, render the last good value.
   If it is more than 1 hour stale, append a tertiary `·` marker to the section label.
4. **The masthead timestamp reflects the oldest successful fetch**, so a silently dead
   fetcher is visible.
5. **Fetch and render are separately scheduled** so a flaky sports API never blanks the
   clock.
6. **Agenda overflow** truncates to `max_events_today` + a `+N más` row. Never let it
   push other sections off the panel.

---

## 12. Scheduling

Two timers. Do not run Chromium every minute — it costs ~3s and ~200MB on a Pi 4, and
99% of the page is unchanged.

```ini
# systemd/dashboard-update.timer
[Unit]
Description=E-paper dashboard full update
[Timer]
OnCalendar=*:0/10
Persistent=true
[Install]
WantedBy=timers.target
```

```ini
# systemd/dashboard-update.service
[Unit]
Description=E-paper dashboard full update
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
User=pi
WorkingDirectory=/home/pi/dashboard
ExecStart=/home/pi/dashboard/venv/bin/python fetch.py
ExecStart=/home/pi/dashboard/venv/bin/python render.py
```

`dashboard-tick.timer` follows the same pattern with `OnCalendar=*:*:00`, running
`tick.py` only.

Use systemd rather than cron — `journalctl -u dashboard-update` is worth it at 1am.

### Refresh policy (`epaper/driver.py`)

- Persist a partial-refresh counter across invocations (`cache/refresh_state.json`).
- After `full_refresh_every_n_partials` (default 30, ≈ hourly), force a full refresh to
  clear accumulated ghosting.
- Skip ticks during `quiet_hours` — nobody reads it at 3am and it saves panel life.
- Use a lock file. Two concurrent SPI writers will corrupt the display.

**Panel lifetime maths:** at one tick per minute, that is ~1,440 partial refreshes/day.
Partials are cheap, but the hourly full refresh (~4s) is the wearing operation at ~24/day.
This is sustainable; a full refresh every minute would not be.

---

## 13. Preview server (development only)

~20 lines of Flask serving `dash.png` and the live rendered HTML on port 8080. Not part
of the pipeline. Purpose: pull up `http://raspberrypi.local:8080` from a phone or laptop
and iterate on layout without walking to the panel.

Design the HTML/CSS to be openable directly in a desktop browser at 800×480 with fixture
JSON. Most tuning should happen there, not on the Pi.

---

## 14. Build order

Ship in this sequence — each milestone is independently useful.

1. **Panel hello-world.** Vendored driver, SPI enabled, push a black rectangle. Confirms
   wiring before any application code exists.
2. **Static render loop.** Hardcoded HTML → Chromium → threshold → panel. Confirms the
   pipeline and, critically, **whether 10px type is legible on real glass.** Do this
   before building anything else — the whole type scale depends on the answer.
3. **Clocks + weather.** Open-Meteo, `zoneinfo`, full layout. This alone is a working
   dashboard.
4. **Calendar.** ICS + RRULE expansion + countdown pill.
5. **Partial-refresh tick.** Clock-only updates, ghosting counter, quiet hours.
6. **Markets band.** Rates then quotes.
7. **Sports.**
8. **Deliveries.** Last, because it is the most fragile and least essential.

---

## 15. Acceptance criteria

- [ ] Cold boot → panel populated within 90s, no manual intervention
- [ ] Full refresh completes in under 8s including fetches
- [ ] Clock ticks every minute; partial-refresh region shows no visible ghosting after
      an hour
- [ ] Every fetcher can be killed (bad key, no network, malformed response) and the
      panel still renders correctly with the section collapsed
- [ ] Pi survives an unplanned power cut with no SD corruption (test 10×)
- [ ] `epd.sleep()` is reached on every code path including exceptions
- [ ] No colour value other than `#000000` / `#ffffff` in the compiled CSS
- [ ] All type ≥10px
- [ ] Panel back stays below 40°C after 4h continuous operation

---

## 16. Known risks

| Risk | Severity | Mitigation |
|---|---|---|
| 10px type unreadable on real glass | High | Milestone 2 answers this early; fallback is dropping to 4 tickers and 3 agenda rows at larger sizes |
| Amazon email template changes | Medium | Defensive parsing, graceful collapse, `manual` fallback mode |
| Twelve Data rate limit | Medium | Batch symbols; fall back to Stooq EOD |
| SD card wear | Medium | USB SSD, or `log2ram` + reduced journald retention |
| Panel heat from Pi | Medium | Remote-mount driver HAT, air gap, vent slots |
| Ghosting from partial refresh | Low | Hourly forced full refresh |
| Sports API quality | Low | Section collapses cleanly if empty |

---

## Appendix — decisions already made (do not relitigate)

- **B/W panel over colour Spectra 6.** The 7.3" colour panel has a 25s refresh and no
  partial refresh, so the ticking clock would be impossible. Colour was wanted for the
  Argentina theme; the theme lives in the *content* (rates, fixtures, Sol de Mayo,
  Obelisco) instead.
- **No PoE HAT.** Height, heat, fan noise.
- **No sparklines** in the markets band. They forced the price type below the legibility
  floor.
- **Sunrise/sunset inline with the clock**, not on their own row — saved 26px that went
  to the agenda's tomorrow-preview row.
- **Vertical 5-day list, horizontal hourly strip.** Five columns could not fit both max
  and min temperatures.
- **Daylight duration line dropped.** Pleasant, never actionable.
