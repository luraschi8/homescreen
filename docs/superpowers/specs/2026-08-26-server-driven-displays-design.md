# Server-Driven Displays — Design

**Status:** Approved in brainstorming, 2026-08-26. Supersedes Phase A4 of `docs/PLAN.md`.
**Scope:** Three subsystems, one spec, built in dependency order.
**Phase 3 is provisional by agreement** — see §7.

---

## 1. Goal

Any screen can be made to show anything — clock, stock ticker, BTC price, weather,
radar — by changing something on the Pi, never by reflashing the device.

Today the opposite is true. The radar firmware knows it is a radar: the rings, the
sweep, the aircraft triangles and the dead reckoning are compiled into it. Showing a
clock on that screen means new firmware.

## 2. What this supersedes, and why

Phase A4 was "swap the firmware's `adsb.fi` call for the Pi's `/api/display/radar/data`,
keep everything else". Under this design the device stops parsing aircraft records
entirely, so A4's field mapping, its `Aircraft` struct wiring and its `pos_age_s`
handling would all be built and then discarded.

**A4 is dropped.** What was going to be built inside it and still gets built here:

- dropping TLS (~33 KB pinned heap, plus two ~16.4 KB blocks per fetch)
- the watchdog and OTA (ADDENDUM §4 — always-on devices get no free reset)
- the 304 clock-split analysis (`PLAN.md` §3) — it was never about aircraft records
- the Pi-side `/api/display/radar/data` endpoint, which becomes a *source* that scenes
  draw from rather than something a device consumes directly

## 3. Architecture

**A scene is the shared abstraction. Delivery is chosen per device.**

```
                    SCENE  (server-side: layout + components)
                              │
             ┌────────────────┴────────────────┐
             ▼                                 ▼
   render: server                       render: device
   Pi composes → HTML → PNG →           Pi sends components as JSON,
   1-bit → 48 KB framebuffer            device lays out and interpolates
             │                                 │
   e-paper 800×480                      GC9A01 240×240
   arbitrary layout, any grid           one component, slot hints
```

This preserves ADDENDUM §2's reasoning rather than overturning it. Pixel-pushing the
round display would be 115,200 B/frame on a chip with ~400 KB SRAM, and pushed frames
cannot interpolate — the radar sweeps between polls precisely because rendering is
local. Conversely, asking an ESP32 to do font layout for an 800×480 panel is work the
Pi is better at, which is why that panel takes pixels.

**What grows is the scene vocabulary on the server, not the wire protocol.**

## 4. Phase 1 — Device registry

### 4.1 Config vs registry

`config.yaml` stops declaring devices. It declares **scenes and feeds** — things you
author. Devices are **discovered** and live in a registry the server writes.

This resolves a real conflict: `check_config` validates `config.yaml` at startup and
exits 78 on anything malformed, which is correct for authored config and wrong for a
list that a device can append to over the network.

### 4.2 Identity

Devices are identified by immutable hardware id (chip/MAC). A friendly name is assigned
server-side and is presentation only.

**The device always talks by hardware id.** It cannot poll by name, because it does not
know its name until someone assigns one. Naming it in the URL would create a bootstrap
problem with no good answer.

```
device  → GET /api/device/a4cf12ab3c44/scene?fw=0.2.0&rssi=-64&uptime=884213&caps=...
human   → GET /api/display/radar/...        friendly alias, resolves name → hw id
```

First contact from an unknown id auto-registers it as **unassigned**. The only flash-time
configuration is the server address.

Reflashing preserves identity, name and assignment. Swapping the board produces a new
unassigned device you rename to adopt.

### 4.3 Registry record

Persisted to `cache/devices.json` using the existing atomic write.

```json
"a4cf12ab3c44": {
  "name": "radar",
  "scene": "planes",
  "first_seen": "2026-08-26T09:14:02+02:00",
  "last_seen":  "2026-08-26T14:31:55+02:00",
  "fw": "0.2.0",
  "caps": {"w": 240, "h": 240, "depth": 16, "layouts": ["fill"],
           "components": ["text", "rings", "markers", "hand", "spark", "icon"]},
  "telemetry": {"rssi": -64, "uptime": 884213, "errors": 0}
}
```

### 4.4 Liveness is derived, never stored

A device is offline when `last_seen` is older than 3× its poll interval. Storing a
status field would require a background sweep and could go stale; a derived value
cannot be wrong.

### 4.5 Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/devices` | fleet list: name, hw id, online, fw, scene, last seen |
| `GET /api/devices/{hw}` | one device, full record |
| `PATCH /api/devices/{hw}` | set `name`, set `scene` |
| `DELETE /api/devices/{hw}` | forget a retired board |
| `GET /api/device/{hw}/scene` | **the device call**; registers on first contact |
| `GET /` | fleet dashboard, extending the existing status page |

### 4.6 Compatibility

`/api/display/radar/data` keeps working as an alias throughout. Phase 1 therefore ships
against the radar firmware **exactly as it is today**, with no reflash and nothing to
un-build.

### 4.7 Migration from the current config

`config.yaml` currently declares one device, `radar`, and a live deployment is serving it.
The upgrade must not require a coordinated stop.

On first start after the upgrade, any device still declared in `config.yaml` is **seeded
into the registry** under a synthetic hardware id (`cfg:radar`), keeping its name and
gaining its scene from a new `scene:` key on the config entry. The device section of
`config.yaml` then becomes legacy input that is read once and never again.

When the real board later self-registers under its true hardware id, it appears as a
second, unassigned device. Adopting it is renaming it and deleting the seeded entry —
a deliberate manual step, because the server cannot know which physical board a config
entry referred to, and silently merging them on a guess would be worse than asking.

`check_config` continues to validate whatever devices remain in `config.yaml`, so the
existing startup guarantees are unaffected during the overlap.

### 4.8 Deliberately omitted

No authentication and no pending-approval state. This is a LAN service; a device that
guesses the URL and registers itself as unassigned is harmless. Consistent with
ADDENDUM §5's "no TLS" reasoning.

## 5. Phase 2 — Scene and component model

### 5.1 A scene is a function

One module per scene in `scenes/`, each exporting:

```python
build(feeds: dict, caps: dict, now: float) -> {"layout": str, "components": list}
```

Not a declarative DSL. A DSL is a language to design, test, document and debug; a Python
function is already testable, already composable, and can branch on `caps` in ways a
declarative format would need new syntax for. If scenes later prove repetitive enough to
warrant a data format, it can be added over this without changing the device contract.

### 5.2 Component vocabulary, v1

Chosen so that **the radar is not a special case** — it decomposes into two general
components. That is the test of whether the vocabulary is at the right level.

| Component | Fields | Covers |
|---|---|---|
| `text` | `slot`, `text`, `size`, `tone` | labels, prices, clock digits, deltas |
| `rings` | `radii`, `labels` | radar ranges, gauge tracks |
| `markers` | `items[{brg,dist,rot,ve,vn,age,label}]` | aircraft, and anything that moves |
| `hand` | `angle`, `rate`, `length` | clock hands, compass needle |
| `spark` | `points`, `min`, `max` | price history, trends |
| `icon` | `name` | weather glyphs |

Sizes and tones are **tokens** (`sm`/`md`/`xl`, `normal`/`dim`/`good`/`bad`), not pixel
values or RGB. The device resolves them. This is what lets the same scene later drive a
1-bit panel, where `tone: bad` cannot be red.

### 5.3 Markers carry polar coordinates, and interpolation lives here

```json
{"c":"markers","items":[{"brg":143.0,"dist":7.4,"rot":143,
                         "ve":0.13,"vn":-0.17,"age":3.1,"label":"IBE3221"}]}
```

**Polar, not screen coordinates.** The firmware lets the user cycle range presets with
the BOOT button. Sending `x,y` would move range control to the server and make it a
network round-trip; sending bearing and distance keeps it instant and local, and reuses
the projection code that already exists.

`ve`/`vn`/`age` are the fields the Pi already computes and serves. The device
dead-reckons between polls, so the radar continues to sweep rather than jump — the
reason ADDENDUM §2 kept this device on data push in the first place.

The radar scene is then:

```json
{"layout":"fill","components":[
  {"c":"rings","radii":[10,20,30],"labels":true},
  {"c":"markers","items":[...]},
  {"c":"text","slot":"rim_bottom","text":"20 ac"}
]}
```

A "ships" or "buses" screen is the same two components with different numbers — a
server-side change only.

### 5.4 Layout

- **`fill`** — one component region plus a fixed slot set the device knows how to place:
  `center`, `primary`, `secondary`, `delta`, `rim_top`, `rim_bottom`.
- **`grid`** — rows/cols with cell placements.

The device declares which modes it supports. The round screen claims only `fill`, which
is honest: at 240×240 there is no room for more.

### 5.5 Capability mismatch is the server's problem

If a scene requests a component the device did not declare, **the server drops it** and
records the substitution in the fleet view. The device never receives something it
cannot draw and therefore needs no error path for it.

### 5.6 The e-paper path reuses all of this

For `render: server` devices the same `build()` output is run through the existing
HTML → PNG → 1-bit pipeline and shipped as a framebuffer. `grid` is free on that panel
because the Pi does the layout in a renderer that is genuinely good at it.

**This half is designed but unexercised** — no panel exists yet. It is a phase-2
interface with a Phase-B consumer.

## 6. Failure and degradation

Extends SPEC §11 rather than replacing it.

1. **An unassigned device gets a valid scene, not an error.** A built-in `unassigned`
   scene showing the hardware id and "not assigned" — so a newly flashed board tells you
   what to type into the fleet view.
2. **A scene that raises does not reach the device.** The server falls back to a
   built-in `error` scene naming the failure, and records it in the fleet view. A broken
   scene must never blank a screen with no explanation.
3. **A device holds its last good scene** on fetch failure, exactly as it holds its last
   good aircraft list today.
4. **The registry is written from the network**, so a corrupt `devices.json` degrades to
   an empty registry rather than stopping the daemon — the rule already applied to
   `overrides.json`.
5. **Scene assignment is validated before persisting**, like the config API: an unknown
   scene name is rejected at PATCH time, never written and then discovered at render.

## 7. Phase 3 — Firmware (PROVISIONAL)

**By agreement, this section is expected to change.** It is written from analysis, not
from hardware, and will be revised after Phase 2 exists and again once the firmware is
under test. Treat the loop shape as settled and the details as a starting position.

### 7.1 Loop

```
every poll_seconds:   GET /api/device/{hw}/scene   (If-None-Match)
                        200 → parse, swap the component list
                        304 → keep the current one
every ~100 ms:        render current list, advancing markers by ve·dt
```

### 7.2 What carries over

Unchanged from the radar firmware: LovyanGFX setup, the 240×240×16bpp double-buffered
sprite, the embedded VLW font, WiFiManager provisioning, the dead-reckoning maths, the
range presets and NVS settings, BOOT-button handling.

Genuinely new: a JSON→component parser, and a `switch` over component types calling the
nine LovyanGFX primitives already in use (`drawString`, `setTextDatum`, `setTextColor`,
`fillRect`, `fillScreen`, `drawWideLine`, `fillTriangle`, `fillCircle`, `drawCircle`).

### 7.3 Memory

Dropping TLS returns ~33 KB pinned plus two ~16.4 KB per-fetch blocks. The current parse
is a filtered walk over a ~27 KB body peaking ~9.6 KB; a scene is 2–6 KB. Against
measured free heap of 22–28 KB with a ~9 KB largest block, this should turn a tight
budget into a comfortable one, and is expected to resolve the documented
`IncompleteInput` and WiFiManager blank-page failures.

### 7.4 The 304 clock split still applies

`PLAN.md` §3's three firmware requirements survive this protocol change intact. A device
holding a cached scene and interpolating still needs a **content clock** (frozen on 304;
drives extrapolation and the 12 s staleness test) and a **contact clock** (refreshed on
304; drives the 60 s expiry). 304 must move off the failure path it currently takes at
`adsb_client.cpp:316`, and must skip both the parse and `publish()`.

### 7.5 Capabilities

Declared on every call as a compact query param, not a separate handshake — so a server
restart cannot lose them and there is no registration state machine to get wrong.

### 7.6 Watchdog and OTA from day one

Per ADDENDUM §4. Always-on devices get no free reset, and walking round with a USB cable
does not scale past two boards.

## 8. Build order

| Phase | Ships | Firmware change |
|---|---|---|
| 1 | Registry, fleet view, self-registration, assignment | **none** |
| 2 | Scenes, components, `/api/device/{hw}/scene` | none |
| 3 | Agnostic firmware | yes |

Phase 1 delivers visible value against today's firmware. Phase 2 can be exercised with
`curl` before any device consumes it. Phase 3 is the only phase that needs hardware.

## 9. Open questions, deferred deliberately

- **Scene rotation and scheduling.** One scene per device for now. Rotation is a
  server-side addition later that does not change the device contract.
- **`grid` on a device.** Only the server does grid layout in v1. A future colour panel
  large enough to warrant it would declare `layouts: ["fill","grid"]`.
- **Screen-coordinate markers.** v1 is polar only, because that is what the radar needs.
  A scatter or map scene would want `x,y`; add a coordinate-space field then, not now.
- **Component vocabulary revision.** Expect one revision after the first non-radar scene
  renders on hardware. Better discovered than guessed.
