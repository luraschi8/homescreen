# Round-Screen Firmware (Phase 3a)

**Goal:** A server-driven firmware for the ESP32-C3 + GC9A01 round display that renders
whatever scene the Pi has assigned it, so changing what a screen shows never again
requires a reflash.

**Spec:** `docs/superpowers/specs/2026-08-26-server-driven-displays-design.md` §7, plus
`docs/PLAN.md` §3's three firmware requirements and `docs/ADDENDUM-01-multi-display.md` §2.
**Code:** `firmware/`. **Reference (read-only, never modified):** `ESP32-Plane-Radar`.

---

## Why this document is short now

Revisions 1–3 embedded the implementation: **2,738 of 3,785 lines were inside code
fences.** Three review rounds returned 28, 15 and 16 blocking findings and never
converged, because about fifty of those were transcription errors — code that would not
compile, tests that could not fail, constants disagreeing with each other — in prose that
had never been near a compiler. Each round replaced broken unverified code with new
unverified code at roughly the same defect rate. That is a treadmill, not convergence.

Six findings justified the reviews, and every one was architectural: the wrong clock
driving expiry, the missing socket teardown, the unbounded parse, and — found by reading
this plan against the *running server* — two live server bugs and a dead 304 path.

So the split is now explicit:

- **This document carries decisions, sequence and acceptance criteria.** It is reviewable
  by a human and does not rot.
- **Code lives in `firmware/`**, where a compiler and 55 host tests judge it in seconds.
- **Reviewers see diffs of verified work** and are asked design questions, not asked to
  act as a slow compiler.
- **Verified work is frozen.** A task that is green and mutation-checked is not reopened
  by a later review unless its *design* is challenged.

---

## Decisions

These are the arguments worth reviewing. Each is implemented and defended by a named test
that fails when the decision is reversed.

| # | Decision | Why | Defended by |
|---|---|---|---|
| D1 | Plain HTTP; no TLS in the image | Reclaims ~35 KB pinned once — two ~16.4 KB mbedTLS content buffers plus context. Measured: static RAM 55,012 B → 20,512 B | the `pio run` figures in each commit |
| D2 | Two clocks: content (frozen by a 304), contact (refreshed by one) | A 304 means the bytes did not change, so the fix really is that old and must keep ageing — but we did just hear from the server, so the 60 s bound must not fire. Collapsing them dims every target with current data, or shows minutes-old traffic as live | `test_a_304_freezes_the_content_clock_and_refreshes_the_contact_clock` |
| D3 | Expiry is contact-age **or** feed-age, never `feed_ok` | `cache.write_failure` only runs when a fetch RUNS and fails, so a daemon that was stopped, hung, or exited 78 leaves `ok: true` on disk while the data rots. And since it keeps the last good aircraft, expiring on the flag blanks the radar for a poll on every upstream hiccup | `test_a_server_that_answers_with_a_stale_feed_expires`, `test_one_upstream_hiccup_does_not_blank_the_radar` |
| D4 | Drop the socket on every failure path and on link-down | `HTTPClient` keeps a keep-alive socket across `end()`; a Pi that vanishes without a FIN leaves `connected()` true and every later poll times out after 8 s, permanently, with nothing to break it | `test_every_failure_path_drops_the_socket`, `test_losing_the_link_drops_the_socket` |
| D5 | Refuse a body over `kMaxBodyBytes` before parsing | ArduinoJson peaks ~4.6× the body. Measured against the real server: 40 items = 6,274 B (~29 KB peak), 64 = 9,940 B (~46 KB) against ~55 KB of heap. Also catches chunked responses, which `getStreamPtr()` does not decode | `test_an_oversized_body_is_refused_before_it_is_parsed`, `test_a_chunked_response_is_refused_not_misparsed` |
| D6 | `kMaxAircraft = 40`, declared as `max_items` | The device knows its own RAM; the server does not. Without it, an operator raising `max_aircraft` blanks the panel at the busiest time of day | `test_the_request_declares_everything_the_server_needs`; server-side `test_a_full_scene_fits_the_devices_byte_budget` |
| D7 | The task calls `pollTick`, never `pollOnce` | The watchdog feed and the link-down teardown both live in `pollTick`. A task calling `pollOnce` reaches neither — and the watchdog then panic-reboots the board on a 60 s loop | `test_every_tick_feeds_the_watchdog` |
| D8 | Chunk the post-poll sleep at 5 s, feeding each chunk | The server may legitimately ask for a 10-minute cadence while the watchdog fires at 60 s. Without chunking, one `PATCH {"poll_seconds": 120}` is a reboot loop that survives the reboot | `test_the_tick_reports_the_delay_the_task_should_use` |
| D9 | Clocks and scene metadata stamped inside the mutex, with the buffer swap | `radar_display` reads the list and the clocks under one lock and multiplies them. Stamped outside, a frame draws new positions against an old content time: ~1 km at 400 kt, a jump-and-snap once per poll | `test_the_content_clock_is_stamped_with_the_swap_under_one_lock` |
| D10 | Watchdog on the poll task only, 60 s | Nothing else can reset this board: Arduino subscribes `loopTask` only via `enableLoopWDT()`, and the idle check is off in this SDK config. 60 s because `hostByName` can block ~31 s and `setConnectTimeout` does not bound it | `test_the_watchdog_period_outlasts_a_slow_poll` |
| D11 | The wire fixture is generated from the server, pinned from both sides | A hand-written fixture drifts the moment a field is renamed, and the failure shows up as a blank screen on hardware rather than a red test | `tests/test_wire_contract.py` |
| D12 | Range presets no longer size the fetch | The server decides the feed radius. BOOT still changes display scale instantly and locally (ADDENDUM §2), but a preset wider than the server's `radius_km` shows an empty rim | T5 |

**Deliberately deferred:** OTA (needs a partition change and a server endpoint that does
not exist); `text`/`spark`/`icon` components; `grid` layout; the e-paper.

---

## Server prerequisites — DONE

Found by reviewing this plan against the running server, which is the highest-value thing
the reviews produced. All committed with tests.

- **`/scene` never aged its aircraft** — VALIDATION F4, second door. A 20 s-old record
  reached the device still claiming 3.1 s. `afbf339`
- **`/scene` could never answer 304** once ages became live, so the device paid its
  largest allocation every poll to learn nothing. The ETag now hashes with clocks
  quantised, as `/data` already did.
- **`_dwell` reported an unreadable or naive timestamp as *fresh***, pinning `feed_age_s`
  at zero forever and leaving the device permanently blind to a dead feed. `bf1c65c`
- **Wire floats rounded** to what a float32 carries — 17 significant digits was half the
  body. `bf1c65c`
- **`max_items` capability**, plus a `clean_caps` whitelist that was silently dropping it.

---

## Sequence

Verified tasks are frozen. Each remaining task ends green on `pio test -e native`, builds
`pio run -e c3`, is mutation-checked where it encodes a decision, and is reviewed as a diff.

| | Task | State |
|---|---|---|
| T0 | Mocks extended — Preferences strings, WiFi MAC/RSSI, HTTP headers + 304, socket counter, WiFiManager registry, mutex give-hook, gfx queries, watchdog | **done** · 10 tests |
| T1 | Skeleton, `config.h`, both build envs | **done** · 3 tests |
| T2 | `device_id`, `server_config` | **done** · 9 tests |
| T3 | Wire fixture generator + contract test | **done** · 25 server tests |
| T4 | `scene_client` | **done** · 33 tests, 5 mutations killed |
| T5 | Port renderer, geometry, runway overlay; repoint at `scene_client`; third staleness term | next |
| T6 | Rewrite `test_display` for the scene wire format | after T5 |
| T7 | Status screens + component dispatcher | after T6 |
| T8 | Provisioning (server field) + `main.cpp` | after T7 |
| T9 | Flash and bring up | needs the board |
| T10 | Measure, soak, document | needs the board |

### T5 — acceptance criteria

- `radar_display.cpp` repointed at `services::scene::`; `grep -n adsb` prints nothing.
- `runway_overlay.cpp` and `large_airports_data.cpp` come across **in the same task** —
  `test_display` includes them, so splitting leaves the tree unbuildable.
- `include/hardware/display_font.h` and `include/data/large_airports.h` copied. The native
  build masks the first with a mock, so its absence only detonates at `pio run -e c3`.
- `radar_range.h` keeps `fetchRadiusKm()`: `runway_overlay.cpp` bounds airport drawing by it.
- **Third staleness cause** (`PLAN.md` §3) added at `radar_display.cpp:532` as a separate
  `||` term, never summed — summing them made targets blink once per cycle.
- Green: `test_geo`, `test_render_policy`, `test_settings`, `test_runway_cap`, `test_debug_log`.

### T6 — acceptance criteria

- `payloadFor()` emits a scene envelope and **keeps the km→lat/lon projection**: all ~54
  call sites give offsets in km, and dropping it puts every target off the dial while the
  suite stays green.
- `ve`/`vn` computed in the helper from `gs`/`track`, matching the server — otherwise every
  extrapolation test runs against a stationary target.
- A test asserting a moving target's drawn x actually changes between frames.
- Every appended test has a `RUN_TEST`; verified by diffing definitions against
  registrations — the suites declare `static void`, so a bare `void` pattern matches nothing.
- `setUp()` calls `resetForTest()`: the scene client carries file-static state and the
  suite moves the clock backwards.

### T7 — acceptance criteria

- Status screens fit 240 px. The server's real message is 43 bytes, ~259 px. Reuse the
  existing truncate-with-`…` path, and select a font before measuring — `textWidth` is
  font-dependent.
- `kDeclaredComponents` and the dispatcher's `switch` cannot disagree; a test asserts the
  declared string is what the client actually sends.
- A device that has never reached the server says so **and shows the address it is trying**.
- An unassigned device shows its hardware id — the string the operator types.
- Fixtures put the radar centre where the fixture traffic is (`kWireHomeLat/Lon`), or every
  drawing assertion passes vacuously against targets 1,480 km off the dial.

### T8 — acceptance criteria

- Server field registered first in `attachPortalParams()`, saved via `onPortalParamsSaved()`.
- A rejected address leaves the working one intact.
- `WIFI_POWER_8_5dBm` still appears twice in `wifi_setup.cpp` — the Super Mini browns out
  at full TX power, and that guard rides on a file we hand-edit.
- `test_main` adapted by hand, not `sed`: it pokes `s_task` directly to reach the retry path.

### T9 / T10 — acceptance criteria

- **Identify the attached board before flashing.** `/dev/cu.usbmodem2101` may be the
  working radar; a `Plane Radar` banner means stop and ask.
- Assign a scene from the Mac and watch the glass change **without a reflash**.
- `systemctl stop homescreen-serve` → dead-reckon, dim at 12 s, rings-only at 60 s, and
  **recover within one poll on restart, without a reboot**. That is D4 on real hardware;
  no host test can prove it.
- `systemctl stop homescreen-fetch` → `feed_ok` stays **true** while `feed_age_s` climbs,
  and the picture drops once it passes 60 s. That is D3 — and getting this expectation
  wrong is what round 3 caught.
- Record heap against the reference's measured baseline: ~22–28 KB free, ~9 KB largest
  block. If the two documented symptoms (WiFiManager's page blanking at ~16 APs;
  `IncompleteInput` every 2–3 min) do not disappear, **say so plainly** rather than
  restating the estimate.

---

## Working rules

1. **Code is written in `firmware/`, not in this document.** To illustrate a decision,
   quote the committed file and line.
2. **Every commit is green on `pio test -e native` and builds `pio run -e c3`.**
3. **Every decision above is mutation-checked**: break it deliberately, watch a named test
   fail, restore. A decision no test defends is a comment.
4. **The reference repo is never modified.** Every copy step ends with a guard that fails
   if it was.
5. **Reviews are scoped to the diff** and asked design questions. Transcription errors are
   the compiler's job, and the compiler is 500× faster at it.
