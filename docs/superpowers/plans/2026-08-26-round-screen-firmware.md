# Round-Screen Firmware Implementation Plan (Phase 3a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A server-driven firmware for the ESP32-C3 + GC9A01 round display that fetches
whatever scene the Pi has assigned it and renders it — starting with the radar — so that
changing what the screen shows never again requires a reflash.

**Architecture:** A new PlatformIO project at `HomeScreen/firmware/`. It polls
`GET /api/device/<hw>/scene` over plain HTTP (no TLS), parses the scene envelope, and
dispatches each component to a renderer. The `radar` component is rendered by the
existing GC9A01 code ported from `ESP32-Plane-Radar`, which is **read-only reference and
is never modified**. A component dispatcher is in place from the start so that adding
`text`, `spark` and the rest later is a new file plus a `case`.

**Tech Stack:** PlatformIO 6.1.19, `espressif32@6.5.0`, Arduino framework, C++17,
LovyanGFX 1.2.7, ArduinoJson 7.4.2, WiFiManager 2.0.17, Unity (host tests, `-e native`)
with AddressSanitizer.

**Spec:** `docs/superpowers/specs/2026-08-26-server-driven-displays-design.md` §7
(PROVISIONAL by agreement), plus `docs/PLAN.md` §3's three firmware requirements and
`docs/ADDENDUM-01-multi-display.md` §2.

**Revision 2**, after two independent reviews (embedded correctness; plan quality). The
changes are listed in "What review changed" at the foot of this document, including three
defects that would have reached hardware and one server bug the review found, which is
already fixed in commit `afbf339`.

---

## Global Constraints

- **`/Users/matias/Documents/repos/ESP32-Plane-Radar` is never modified.** Files are
  copied out of it. Every copy step ends with a guard that fails if it was touched.
- **PlatformIO is not on `PATH`.** Every task that runs `pio` must first:
  `export PATH="$HOME/.platformio/penv/bin:$PATH"`. Verified: `pio --version` → 6.1.19.
- **No TLS.** No `WiFiClientSecure` in the firmware, no root certs, no mbedTLS. The
  reference pins **~35 KB once** for mbedTLS — two ~16.4 KB content buffers plus a
  ~2.5 KB context, claimed once and held while the session is reused, **not** per fetch.
  Free heap should go from 22–28 KB to roughly 55–63 KB. (Revision 1 said "~33 KB plus
  two ~16.4 KB per-fetch blocks", double-counting the same allocation. Task 11 measures
  the truth.)
- **Every device call sends `w`, `h` and `depth`.** The server only reads a capability
  *list* (`components=`) when the same request also declares `w` and `h`.
- **The device declares `max_items=64`.** The server sends the smaller of that and the
  operator's `max_aircraft`. Without it an operator raising `max_aircraft` to 200 would
  send a body whose parse peaks near 88 KB against ~55 KB of free heap.
- **`/frame` is not used by this device.** It is pixel-push, for the e-paper.
- **Spanish on the glass, English in logs.**
- **Do not raise Wi-Fi TX power.** `OPS.md` §7: the Super Mini's regulator browns out at
  full power; `WIFI_POWER_8_5dBm` is set in both the AP and STA paths and must stay.
- **No `Serial.printf` inside the poll loop.** `OPS.md` §3.4: `HWCDC::write` blocks up to
  100 ms with no host draining, and `Print::printf` mallocs past 64 characters.
- **Host tests must pass before any hardware step:** `pio test -e native`.
- **Firmware version string:** `fw=hs-0.1`.

---

## The wire contract

```
GET /api/device/aabb00112233/scene?w=240&h=240&depth=16&max_items=64
    &components=radar&fw=hs-0.1&uptime=1234&rssi=-58
If-None-Match: "3d3f072e75c16427"

200 OK
ETag: "3d3f072e75c16427"          <- INCLUDING the quotes; echo them verbatim
X-Poll-Seconds: 5
{"assigned":true,"components":[{"c":"radar","feed_age_s":2.4,"feed_ok":true,
  "items":[{"age":5.5,"alt":"3675 ft","cs":"IBE3221","dst":7.4,"gs":400.0,
            "lat":40.5,"lon":-3.6,"nose":90.0,"trk":91.0,"ve":0.13,"vn":-0.17}],
  "radius_km":60.0}],"hw":"aabb00112233","layout":"fill","name":"radar",
  "scene":"planes"}

304 Not Modified
ETag: "3d3f072e75c16427"
X-Poll-Seconds: 5
(no body)
```

Keys are sorted — Flask sorts them — so the body is byte-deterministic and safe to pin.

| wire | `Aircraft` field | note |
|---|---|---|
| `lat` `lon` | `lat` `lon` | degrees |
| `nose` | `nose_deg` | glyph rotation |
| `trk` | `track_deg` | direction of travel |
| `gs` | `gs_knots` | |
| `ve` `vn` | `vel_e_km_s` `vel_n_km_s` | **km/s**, precomputed server-side |
| `age` | `pos_age_s` | **includes the server's cache dwell** (fixed in `afbf339`) |
| `dst` | `dst_nm` | nautical miles; `-1` if absent |
| `cs` `ty` `alt` | `callsign[9]` `type[5]` `alt[12]` | |

`feed_age_s` is the **third** staleness cause and stays separate from `age`: `PLAN.md` §3
records that summing them made targets blink once per cycle. A device can be receiving
fresh scenes while the feed behind them is dead, and only this number says so.

---

## File Structure

```
HomeScreen/firmware/
  platformio.ini                     envs: c3, c3-debug, native
  partitions/display_client.csv      copied
  data/ui_font.vlw                   copied asset
  include/
    config.h                         NEW  pins, timings, geometry. No feed URLs.
    debug_log.h                      copied, one guard renamed
    data/large_airports.h            copied
    hardware/display.h               copied
    hardware/display_font.h          copied
    hardware/lgfx_config.hpp         copied
    services/device_id.h             NEW
    services/server_config.h         NEW
    services/scene_client.h          NEW  poll, parse, two clocks, three expiries
    services/radar_location.h        copied
    services/wifi_setup.h            copied + one portal field
    ui/components.h                  NEW  dispatcher
    ui/radar_display.h  radar_geo.h  radar_range.h  radar_theme.h
    ui/render_policy.h  runway_overlay.h  status_screens.h    copied
  src/                               mirrors include/, plus main.cpp
  test/
    mocks/                           copied, extended in Task 0
    fixtures_wire.h                  GENERATED from the server
    test_mocks/ test_smoke/ test_device_id/ test_server_config/
    test_scene_client/ test_components/ test_display/ test_geo/
    test_render_policy/ test_settings/ test_wifi/ test_runway_cap/
    test_main/ test_debug_log/
HomeScreen/
  scripts/dump_wire_fixture.py       NEW
  tests/test_wire_contract.py        NEW
```

**Task order and why.** Task 0 extends every mock before anything consumes them.
Tasks 1–2 are foundations. Task 3 is the scene client. Task 4 is the contract fixture
(Python side). Task 5 ports the renderer **with** the runway overlay and airport data,
because `test_display` includes them. Task 6 rewrites `test_display` for the new wire
format — its own task, because it is a 1,300-line rewrite and not a `sed`. Task 7 is the
dispatcher **with** the status screens it draws. Task 8 is provisioning and `main.cpp`.
Task 9 is the watchdog. Tasks 10–11 are hardware.

---

## Task 0: Extend the mocks

Everything downstream compiles against these. Revision 1 assumed five APIs that do not
exist, and each one blocked a whole task.

**Files:**
- Create: `firmware/test/mocks/` (copied whole, then extended)
- Modify: `mocks/Preferences.h`, `mocks/WiFi.h`, `mocks/HTTPClient.h`,
  `mocks/WiFiManager.h`, `mocks/LovyanGFX.hpp`
- Test: `firmware/test/test_mocks/test_mocks.cpp`

**Interfaces produced:**
- `Preferences::putString/getString/putUShort/getUShort`
- `MockWiFi::rssi_`, `WiFi.RSSI()`, `WiFi.macAddress(uint8_t*)`
- `MockHttp::response_headers`, `last_if_none_match`, `content_length_override`,
  `HTTPClient::addHeader/collectHeaders/header`, `HTTP_CODE_NOT_MODIFIED`
- `MockWiFiClientStats g_wc` — so the socket-teardown fix in Task 3 is testable
- `g_mutex_on_give` — a `std::function<void()>` the semaphore mock calls just before
  releasing, so a test can observe what was true while the lock was held
- `g_wm_params` registry + `wmHasParameter()` / `wmSetParameterValue()`
- `GfxRecorder::textContains()`, `lastX()` — queries over the existing `ops` log

- [ ] **Step 1: Copy the mocks and assets**

```bash
cd /Users/matias/Documents/repos/HomeScreen
REF=/Users/matias/Documents/repos/ESP32-Plane-Radar
mkdir -p firmware/{include/{hardware,services,ui,data},src/{hardware,services,ui,data},test/mocks,partitions,data} scripts
cp -R $REF/test/mocks/. firmware/test/mocks/
cp $REF/include/debug_log.h firmware/include/
cp $REF/data/ui_font.vlw firmware/data/
cp $REF/partitions/plane_radar.csv firmware/partitions/display_client.csv
test "$(git -C $REF status --porcelain | wc -l | tr -d ' ')" = "0" || \
  { echo "REFERENCE REPO MODIFIED -- STOP"; exit 1; }
```

- [ ] **Step 2: Preferences — strings and 16-bit ints**

`server_config` stores a host string and a port. The mock has only
`putUChar/getUChar/putBool/getBool/putDouble/getDouble`. Add to `class Preferences`,
following the existing `open_` / `read_only_` no-op discipline exactly:

**Into the existing `g_nvs.store`, via the existing `key()`/`put()`/`get<T>()`.** A
separate map would be invisible to `MockNvs::namespaceExists()`, which scans `store` only
— and `begin(ns, true)` returns false when the namespace does not exist, so *every*
read-only open would fail and `server_config::load()` would silently always fall back to
the compiled default. Add these as public members of `class Preferences`:

```cpp
  void putString(const char* k, const char* v) {
    // Same no-op discipline as put(): a handle that failed to open, or was
    // opened read-only, silently drops the write -- which is how an unchecked
    // begin() loses data on real hardware.
    if (!open_ || read_only_ || v == nullptr) return;
    g_nvs.store[key(k)] = std::string(v);
  }
  String getString(const char* k, const char* d = "") {
    auto it = g_nvs.store.find(key(k));
    return it == g_nvs.store.end() ? String(d) : String(it->second.c_str());
  }
  void putUShort(const char* k, uint16_t v) { put(k, &v, sizeof(v)); }
  uint16_t getUShort(const char* k, uint16_t d = 0) { return get<uint16_t>(k, d); }
```

Nothing is added to `MockNvs`: `store`, `namespaceExists()` and `reset()` all keep
working unchanged. `get<T>()` compares `size() != sizeof(T)`, so a string stored under a
key can never be misread as a number.

- [ ] **Step 3: WiFi — MAC and RSSI**

Both are sent on every request. `grep -rn RSSI` over the reference returns nothing, so
this is genuinely absent. Add to `class MockWiFi`:

```cpp
  uint8_t mac_[6] = {0xAA, 0xBB, 0x00, 0x11, 0x22, 0x33};
  int rssi_ = -58;
  void macAddress(uint8_t* out) { if (out) memcpy(out, mac_, 6); }
  int RSSI() const { return rssi_; }
```
and reset `rssi_` to `-58` in `MockWiFi::reset()` if it has one.

- [ ] **Step 4: HTTPClient — response headers, conditional GETs, 304**

Add `#include <map>` and `#include <vector>`, then `HTTP_CODE_NOT_MODIFIED = 304` to the
enum. In `struct MockHttp`:

```cpp
  std::map<std::string, std::string> response_headers;
  std::vector<std::string> collected_header_keys;
  std::string last_if_none_match;
```
(`reset()` is `*this = MockHttp()`, so these clear for free.)

In `class HTTPClient`:

```cpp
  void addHeader(const String& name, const String& value) {
    // strcmp, not ==: the mock String has no operator==.
    if (strcmp(name.c_str(), "If-None-Match") == 0) {
      g_http.last_if_none_match = value.c_str();
    }
  }
  void collectHeaders(const char** keys, size_t count) {
    for (size_t i = 0; i < count; ++i) {
      g_http.collected_header_keys.push_back(keys[i]);
    }
  }
  String header(const char* name) {
    auto it = g_http.response_headers.find(name);
    return it == g_http.response_headers.end() ? String("")
                                               : String(it->second.c_str());
  }
```

- [ ] **Step 5: WiFiClient — count `stop()`**

`MockTlsStats` models TLS teardown and becomes dead instrumentation once TLS is gone —
at exactly the moment the firmware reintroduces a socket-teardown bug (see Task 3's
`s_client.stop()`). Add to the plain `WiFiClient` in `mocks/WiFiClientSecure.h`:

`class WiFiClient` has only `body` and `pos` — there is no `connected_` to clear, and no
member for a test to read. Add a **separate global**, because the obvious alternative
does not work: a counter reset by `g_http.reset()` is zeroed by the `poll()` helper's own
first statement, so `const int before = g_wc.stop_calls;` then `poll(...)` compares
against a counter that just went back to 0.

In `mocks/WiFiClientSecure.h`, above `class WiFiClient`:

```cpp
/** Plain-TCP teardown counter. MockTlsStats models the TLS one; this firmware
 *  has no TLS, and without this the host suite loses its only way to see a
 *  socket-teardown bug at the moment the firmware reintroduces one. */
struct MockWiFiClientStats { int stop_calls = 0; void reset() { stop_calls = 0; } };
extern MockWiFiClientStats g_wc;
```
and inside `class WiFiClient`:
```cpp
  void stop() { ++g_wc.stop_calls; pos = 0; }
```
`mock_globals.h` holds definitions, not `extern`s, so add `MockWiFiClientStats g_wc;`
there next to `MockTlsStats g_tls;`. Reset it in each suite's `setUp()`, never from
`g_http.reset()`.

**Do not delete `mocks/WiFiClientSecure.h`.** `class WiFiClient` is defined inside it and
`HTTPClient.h` includes it.

**Do not delete `mocks/WiFiClientSecure.h`.** `class WiFiClient` is defined *inside* it
and `HTTPClient.h` includes it; deleting it breaks the whole suite.

- [ ] **Step 6: WiFiManager — record parameters**

`addParameter` currently records nothing, so a test cannot ask whether the portal offers
a field. **Not on `MockWmStats`, and not by rewriting `getValue()`.** Three reasons, each fatal:
`WiFiManagerParameter` has no `getID()`; `g_wm` is declared *after* the parameter class,
so an inline `getValue()` referencing it does not compile; and `test_wifi.cpp`'s `setUp`
does `g_wm = MockWmStats()` every test while `attachPortalParams()` runs **once** per
binary behind a file-static `s_wm_configured` guard — so anything stored on `g_wm` is
wiped before the appended tests run.

A separate registry of *pointers*, which `setUp` does not touch, and which sets the value
through the parameter's own API exactly as a real form POST does. In `WiFiManager.h`,
after `class WiFiManagerParameter`:

```cpp
/** Registered portal parameters, by id. Deliberately NOT part of MockWmStats:
 *  test_wifi.cpp resets that every test, while attachPortalParams() runs once
 *  per binary behind a file-static guard. */
extern std::map<std::string, WiFiManagerParameter*> g_wm_params;
inline bool wmHasParameter(const char* id) { return g_wm_params.count(id) != 0; }
inline void wmSetParameterValue(const char* id, const char* v) {
  auto it = g_wm_params.find(id);
  if (it != g_wm_params.end()) it->second->setValue(v, 64);
}
```
`addParameter` becomes `void addParameter(WiFiManagerParameter* p) { if (p) g_wm_params[p->id_] = p; }`
— `id_` is already public. Define `std::map<std::string, WiFiManagerParameter*> g_wm_params;`
in `mock_globals.h`, and add `#include <map>` to `WiFiManager.h`.

The appended tests in Task 8 use `wmHasParameter("server")` and
`wmSetParameterValue("server", ...)`. Leaving `getValue()` alone matters: preferring a
registry value inside it would make the existing
`test_saving_params_applies_a_valid_location` read a stale registered `"0"` instead of
the value it just set.

- [ ] **Step 7: GfxRecorder — two queries over the log it already keeps**

The recorder already logs every primitive as a `DrawOp` with a `kind`, coordinates and,
for `Text`, the string. Add **queries**, not parallel state — a second copy of the same
information is a second thing to keep in sync, and `reset()` clears `ops` only on
purpose (scripted behaviour like `sprite_alloc_fails` deliberately survives it).

```cpp
  /** Did any drawString contain this? Case-sensitive, substring. */
  bool textContains(const char* needle) const {
    if (needle == nullptr) return false;
    for (const auto& o : ops) {
      if (o.kind == DrawOp::Text && o.text.find(needle) != std::string::npos) {
        return true;
      }
    }
    return false;
  }
  /** The x of the last primitive of this kind, or -1. For "did it move?". */
  int lastX(DrawOp::Kind k) const {
    for (auto it = ops.rbegin(); it != ops.rend(); ++it) {
      if (it->kind == k) return it->x;
    }
    return -1;
  }
```

`drew_circle` is not needed: `g_gfx.count(DrawOp::Circle) > 0` already answers it, and
the plan's tests use that form.

- [ ] **Step 8: A hook the semaphore mock does not have**

`test_the_content_clock_is_stamped_with_the_swap_under_one_lock` needs to observe what was
true *while* the lock was held. The host mutex never blocks, so the only way to see that
is a callback fired inside `xSemaphoreGive`, before the counter drops. In
`mocks/freertos/semphr.h`:

```cpp
#include <functional>
/** Fired inside xSemaphoreGive BEFORE g_mutex_outstanding drops, so a test can
 *  assert on state that was only true while the lock was held. Null by default;
 *  a test sets it, then clears it. */
extern std::function<void()> g_mutex_on_give;
```
and in `xSemaphoreGive`, as the first statement of the `if (m)` branch:
```cpp
    if (g_mutex_on_give) g_mutex_on_give();
```
Define `std::function<void()> g_mutex_on_give;` in `mock_globals.h` beside the other
counters. Clear it in every `setUp()` that sets it — a lambda capturing a dead stack
frame fires on the next test otherwise.

- [ ] **Step 9: Test the mocks themselves**

```cpp
// firmware/test/test_mocks/test_mocks.cpp
// The mocks are the ground the whole suite stands on. A mock that silently
// does nothing turns every test above it green for the wrong reason.
#include <Arduino.h>
#include <unity.h>
#include <Preferences.h>
#include <HTTPClient.h>
#include <cstring>

#include "../mocks/mock_globals.h"

void setUp(void) {
  g_nvs.reset(); g_http.reset(); g_gfx.reset(); g_wc.reset();
  g_mutex_on_give = nullptr;
}
void tearDown(void) {}

void test_preferences_round_trips_strings_and_ushorts(void) {
  Preferences p;
  TEST_ASSERT_TRUE(p.begin("ns", false));
  p.putString("host", "192.168.1.116");
  p.putUShort("port", 8080);
  p.end();
  Preferences r;
  TEST_ASSERT_TRUE(r.begin("ns", true));
  TEST_ASSERT_EQUAL_STRING("192.168.1.116", r.getString("host", "").c_str());
  TEST_ASSERT_EQUAL_UINT16(8080, r.getUShort("port", 0));
  TEST_ASSERT_EQUAL_STRING("fallback", r.getString("absent", "fallback").c_str());
  r.end();
}

void test_a_read_only_open_refuses_writes(void) {
  Preferences w; w.begin("ns", false); w.putString("host", "first"); w.end();
  Preferences r; r.begin("ns", true); r.putString("host", "second"); r.end();
  Preferences c; c.begin("ns", true);
  TEST_ASSERT_EQUAL_STRING("first", c.getString("host", "").c_str());
}

void test_the_http_mock_serves_headers_and_records_the_conditional(void) {
  g_http.response_headers["ETag"] = "\"abc\"";
  g_http.response_headers["X-Poll-Seconds"] = "30";
  WiFiClient c;
  HTTPClient h;
  h.begin(c, "http://x/y");
  h.addHeader("If-None-Match", "\"prev\"");
  h.GET();
  TEST_ASSERT_EQUAL_STRING("\"abc\"", h.header("ETag").c_str());
  TEST_ASSERT_EQUAL_STRING("30", h.header("X-Poll-Seconds").c_str());
  TEST_ASSERT_EQUAL_STRING("", h.header("Absent").c_str());
  TEST_ASSERT_EQUAL_STRING("\"prev\"", g_http.last_if_none_match.c_str());
}

void test_wifi_reports_a_mac_and_an_rssi(void) {
  uint8_t mac[6] = {0};
  WiFi.macAddress(mac);
  TEST_ASSERT_EQUAL_UINT8(0xAA, mac[0]);
  TEST_ASSERT_EQUAL_UINT8(0x33, mac[5]);
  TEST_ASSERT_EQUAL_INT(-58, WiFi.RSSI());
}

void test_the_give_hook_sees_the_lock_still_held(void) {
  // If it fired after the decrement it would always read 0 and the test that
  // depends on it would pass against a clock stamped outside the lock.
  int seen = -1;
  SemaphoreHandle_t m = xSemaphoreCreateMutex();
  g_mutex_on_give = [&]() { seen = g_mutex_outstanding; };
  xSemaphoreTake(m, 0);
  xSemaphoreGive(m);
  g_mutex_on_give = nullptr;
  TEST_ASSERT_EQUAL_INT(1, seen);
  TEST_ASSERT_EQUAL_INT(0, g_mutex_outstanding);
  vSemaphoreDelete(m);
}

void test_the_gfx_recorder_can_be_asked_what_was_drawn(void) {
  DrawOp op;
  op.kind = DrawOp::Text;
  op.text = "SIN SERVIDOR";
  op.x = 120;
  g_gfx.ops.push_back(op);
  TEST_ASSERT_TRUE(g_gfx.textContains("SERVIDOR"));
  TEST_ASSERT_FALSE(g_gfx.textContains("nope"));
  TEST_ASSERT_FALSE(g_gfx.textContains(nullptr));
  TEST_ASSERT_EQUAL_INT(120, g_gfx.lastX(DrawOp::Text));
  TEST_ASSERT_EQUAL_INT(-1, g_gfx.lastX(DrawOp::Circle));
}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_preferences_round_trips_strings_and_ushorts);
  RUN_TEST(test_a_read_only_open_refuses_writes);
  RUN_TEST(test_the_http_mock_serves_headers_and_records_the_conditional);
  RUN_TEST(test_wifi_reports_a_mac_and_an_rssi);
  RUN_TEST(test_the_give_hook_sees_the_lock_still_held);
  RUN_TEST(test_the_gfx_recorder_can_be_asked_what_was_drawn);
  return UNITY_END();
}
```

- [ ] **Step 10: Commit** (the suite cannot run until Task 1 writes `platformio.ini`)

```bash
git add firmware/ && git commit -m "test(firmware): extend the copied mocks for the scene client"
```

---

## Task 1: Project skeleton that builds and host-tests

**Files:** Create `firmware/platformio.ini`, `firmware/include/config.h`,
`firmware/src/main.cpp`, `firmware/test/test_smoke/test_smoke.cpp`. Modify
`firmware/include/debug_log.h`.

**Interfaces produced:** a `native` env that runs Task 0's tests, a `c3` env that builds,
and `config::` constants consumed by every later task.

- [ ] **Step 1: Write `firmware/platformio.ini`**

```ini
; ESP32-C3 Super Mini + 1.28" round GC9A01 (240x240), server-driven.
; NOT a copy of ESP32-Plane-Radar's: no TLS, and the partition table is renamed.
[platformio]
default_envs = c3

[env:c3]
platform = espressif32@6.5.0
board = esp32-c3-devkitm-1
framework = arduino
monitor_speed = 115200
board_build.partitions = partitions/display_client.csv
board_build.embed_files = data/ui_font.vlw
build_flags =
  -std=gnu++17
  -DARDUINO_USB_MODE=1
  -DARDUINO_USB_CDC_ON_BOOT=1
  -DWM_NODEBUG
  -DWM_MDNS
; The framework ships -Wno-format and project flags are emitted first, so a bare
; -Wformat would be overridden. Unflag it, then scope the errors to our sources:
; build_src_flags does not reach the libraries, which have their own noise.
build_unflags = -Wno-format
build_src_flags = -Wformat -Werror=format -Wall -Wextra
lib_deps =
  lovyan03/LovyanGFX@^1.2.7
  tzapu/WiFiManager@^2.0.17
  bblanchon/ArduinoJson@^7.4.2

[env:c3-debug]
extends = env:c3
build_flags =
  ${env:c3.build_flags}
  -DPLANE_RADAR_DEBUG=1

[env:native]
platform = native
test_framework = unity
build_flags =
  -std=gnu++17
  -I test/mocks
  -I test
  -I include
  -DUNIT_TEST
  ; Turns a static-buffer overrun into a clean abort naming the line instead of
  ; undefined behaviour that may or may not show as a failure. Host-only.
  -fsanitize=address,undefined
  -fno-omit-frame-pointer
  -DUNITY_INCLUDE_DOUBLE
  -DUNITY_DOUBLE_PRECISION=1e-9
build_type = debug
lib_deps =
  bblanchon/ArduinoJson@^7.4.2
```

**`-DPLANE_RADAR_DEBUG=1`, not `DISPLAY_CLIENT_DEBUG`.** The copied `debug_log.h` guards
on that name; renaming the flag without renaming the guard compiles the logging out and
leaves Task 11's heap measurement printing nothing. Either keep this name or rename both.
This plan keeps the name and says why in `debug_log.h`:

```bash
cd firmware
python3 - <<'PY'
p='include/debug_log.h'; s=open(p).read()
s = s.replace('#pragma once', '''#pragma once

// The guard name is PLANE_RADAR_DEBUG and stays that way even though this is no
// longer the plane radar: platformio.ini's `-D` and this `#if` have to agree,
// and a rename that touched only one of them would compile every DEBUG_LOG out
// while still looking enabled.''', 1)
open(p,'w').write(s)
PY
```

- [ ] **Step 2: Write `firmware/include/config.h`**

```cpp
#pragma once

#include <cstdint>

#include <driver/gpio.h>

namespace config {

/** Sent as `fw` on every request; shown in the fleet view. */
constexpr char kFirmwareVersion[] = "hs-0.1";

// --- Wi-Fi portal ---
constexpr char kPortalApName[] = "HomeScreen-Setup";
constexpr char kPortalIp[] = "192.168.4.1";
constexpr char kPortalHostname[] = "homescreen-display";
constexpr char kPortalHostUrl[] = "homescreen-display.local";
constexpr unsigned long kWifiConnectAttemptMs = 15000;
constexpr uint8_t kWifiConnectAttempts = 3;
constexpr unsigned long kWifiPortalTimeoutSec = 0;
constexpr unsigned long kWifiConnectingFrameMs = 50;
constexpr unsigned long kWifiDownGraceMs = 4000;
constexpr unsigned long kWifiReconnectIntervalMs = 15000;

// --- BOOT button (ESP32-C3 Super Mini, active LOW) ---
constexpr gpio_num_t kBootPin = GPIO_NUM_9;
constexpr unsigned long kBootResetHoldMs = 3000UL;
constexpr unsigned long kBootTapMinMs = 40UL;

// --- Display: GC9A01 1.28" round 240x240 (SPI) ---
constexpr gpio_num_t kDisplayPinRst = GPIO_NUM_0;
constexpr gpio_num_t kDisplayPinCs = GPIO_NUM_1;
constexpr gpio_num_t kDisplayPinDc = GPIO_NUM_10;
constexpr gpio_num_t kDisplayPinMosi = GPIO_NUM_3;
constexpr gpio_num_t kDisplayPinSclk = GPIO_NUM_4;
constexpr int kDisplayWidth = 240;
constexpr int kDisplayHeight = 240;
constexpr int kDisplayDepth = 16;
constexpr uint32_t kDisplaySpiWriteHz = 80000000;
constexpr bool kDisplayInvert = true;
constexpr bool kDisplayRgbOrder = true;

// --- Radar centre defaults (overridden via the setup portal) ---
constexpr double kDefaultRadarLat = 52.3676;
constexpr double kDefaultRadarLon = 4.9041;

// --- Server ---
/** Fallback only: the real address is set in the portal and kept in NVS. */
constexpr char kDefaultServerHost[] = "dashboard.local";
constexpr uint16_t kDefaultServerPort = 8080;
/**
 * Cadence floor and ceiling for whatever `X-Poll-Seconds` says. The server is
 * trusted but not obeyed blindly: 0 would be a flood and 86400 a brick.
 */
constexpr unsigned long kPollMinMs = 1000;
constexpr unsigned long kPollMaxMs = 600000;
constexpr unsigned long kPollDefaultMs = 5000;
/** Retry sooner than the poll cadence when the last exchange failed. */
constexpr unsigned long kPollErrorMs = 3000;
constexpr unsigned long kPollTaskRetryMs = 10000;
constexpr unsigned long kRenderIntervalMs = 100;
constexpr unsigned long kDebugFrameReportMs = 1000;

/**
 * Largest scene body we will parse. ArduinoJson 7 peaks around 4.6x the body
 * size for this shape (measured: 9,628 B -> 30,575 B at 64 items). At
 * max_items=64 the server cannot exceed ~9.6 KB, so 8 KB bounds the peak near
 * 37 KB and still fits every body we ask for -- 12 KB would have allowed a
 * ~56 KB peak, which is the entire post-TLS budget with no margin, and the pool
 * needs contiguity rather than just total free bytes. Anything larger is a
 * misconfiguration or a chunked response, and both must be refused rather than
 * attempted: a NoMemory mid-parse looks exactly like a dead server on the glass.
 */
constexpr int kMaxBodyBytes = 8192;

// --- UI colours (RGB565) — status screens ---
constexpr uint16_t kColorBlack = 0x0000;
constexpr uint16_t kColorYellow = 0xFFE0;
constexpr uint16_t kTextOnYellow = kColorBlack;
constexpr uint16_t kTextOnBlack = 0xFFFF;

}  // namespace config
```

- [ ] **Step 3: Write the smoke test**

```cpp
// firmware/test/test_smoke/test_smoke.cpp
#include <Arduino.h>
#include <unity.h>

#include "config.h"
#include "../mocks/mock_globals.h"

void test_the_geometry_matches_what_the_server_is_told(void) {
  TEST_ASSERT_EQUAL_INT(240, config::kDisplayWidth);
  TEST_ASSERT_EQUAL_INT(240, config::kDisplayHeight);
  TEST_ASSERT_EQUAL_INT(16, config::kDisplayDepth);
}

void test_no_feed_url_is_compiled_into_this_firmware(void) {
  // The point of the phase: the device knows a server, not a data source.
  TEST_ASSERT_EQUAL_STRING("dashboard.local", config::kDefaultServerHost);
}

void test_the_body_cap_leaves_room_for_the_parse_to_peak(void) {
  // Measured: ArduinoJson 7.4 peaks ~4.6x body for this shape, and the server
  // cannot send more than ~9.6 KB at max_items=64. 8 KB bounds the peak near
  // 37 KB with real margin.
  TEST_ASSERT_EQUAL_INT(8192, config::kMaxBodyBytes);
}

void setUp(void) {}
void tearDown(void) {}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_the_geometry_matches_what_the_server_is_told);
  RUN_TEST(test_no_feed_url_is_compiled_into_this_firmware);
  RUN_TEST(test_the_body_cap_leaves_room_for_the_parse_to_peak);
  return UNITY_END();
}
```

- [ ] **Step 4: Write a `main.cpp` that links but does nothing yet**

```cpp
/** HomeScreen display client — server-driven round display. */
#include <Arduino.h>

#include "config.h"

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.printf("\nHomeScreen display client %s\n", config::kFirmwareVersion);
}

void loop() { delay(1000); }
```

- [ ] **Step 5: Run both builds**

```bash
export PATH="$HOME/.platformio/penv/bin:$PATH"
cd firmware && pio test -e native && pio run -e c3
```
Expected: Task 0's 5 mock tests and these 3 pass; the image builds. Record the flash and
RAM figures in the commit message — the reference is 1,247,850 B flash / 55,012 B RAM.

- [ ] **Step 6: Commit**

```bash
git add firmware/ && git commit -m "feat(firmware): skeleton that builds for the C3 and tests on the host"
```

---

## Task 2: Device identity and the server address

**Files:** Create `firmware/{include,src}/services/device_id.{h,cpp}`,
`firmware/{include,src}/services/server_config.{h,cpp}`,
`firmware/test/test_device_id/test_device_id.cpp`,
`firmware/test/test_server_config/test_server_config.cpp`.

**Interfaces:**
- Consumes: `config::kDefaultServerHost`, `config::kDefaultServerPort`; Task 0's
  Preferences and WiFi mocks.
- Produces: `const char* services::deviceId()`; `services::server::load()`, `host()`,
  `port()`, `baseUrl()`, `saveFromString(const char*)`.

- [ ] **Step 1: Write the identity test**

```cpp
// firmware/test/test_device_id/test_device_id.cpp
#include <Arduino.h>
#include <unity.h>
#include <cstring>

#include "../mocks/mock_globals.h"
#include "../../src/services/device_id.cpp"

void test_the_id_is_the_mac_as_lowercase_hex(void) {
  // The server stores the operator's scene assignment against this string, so
  // it must survive reboots and reflashes -- anything derived from a random
  // seed or from NVS makes a device come back as "sin asignar" after a flash.
  TEST_ASSERT_EQUAL_STRING("aabb00112233", services::deviceId());
}

void test_the_id_is_computed_once(void) {
  const char* first = services::deviceId();
  WiFi.mac_[0] = 0xFF;                       // a MAC cannot really change
  TEST_ASSERT_EQUAL_STRING(first, services::deviceId());
  TEST_ASSERT_EQUAL_UINT(12, strlen(services::deviceId()));
}

void setUp(void) {}
void tearDown(void) {}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_the_id_is_the_mac_as_lowercase_hex);
  RUN_TEST(test_the_id_is_computed_once);
  return UNITY_END();
}
```

- [ ] **Step 2: Run it to see it fail**

```bash
export PATH="$HOME/.platformio/penv/bin:$PATH"
cd firmware && pio test -e native -f test_device_id
```
Expected: FAIL — no such file.

- [ ] **Step 3: Implement `device_id`**

```cpp
// firmware/include/services/device_id.h
#pragma once

namespace services {

/**
 * This device's hardware id: 12 lowercase hex characters of the station MAC.
 * The server keys the operator's scene assignment on it, so it has to survive
 * reboots and reflashes.
 */
const char* deviceId();

}  // namespace services
```

```cpp
// firmware/src/services/device_id.cpp
#include "services/device_id.h"

#include <WiFi.h>

#include <cstdio>

namespace services {

const char* deviceId() {
  static char id[13] = {0};
  if (id[0] == '\0') {
    uint8_t mac[6] = {0};
    WiFi.macAddress(mac);
    snprintf(id, sizeof(id), "%02x%02x%02x%02x%02x%02x", mac[0], mac[1], mac[2],
             mac[3], mac[4], mac[5]);
  }
  return id;
}

}  // namespace services
```

- [ ] **Step 4: Run green**

```bash
cd firmware && pio test -e native -f test_device_id
```
Expected: 2 pass.

- [ ] **Step 5: Write the server-address test**

```cpp
// firmware/test/test_server_config/test_server_config.cpp
#include <Arduino.h>
#include <unity.h>
#include <cstring>

#include "../mocks/mock_globals.h"
#include "../../src/services/server_config.cpp"

using namespace services::server;

void setUp(void) { g_nvs.reset(); }
void tearDown(void) {}

void test_a_bare_host_takes_the_default_port(void) {
  TEST_ASSERT_TRUE(saveFromString("192.168.1.116"));
  load();
  TEST_ASSERT_EQUAL_STRING("192.168.1.116", host());
  TEST_ASSERT_EQUAL_UINT16(8080, port());
  TEST_ASSERT_EQUAL_STRING("http://192.168.1.116:8080", baseUrl());
}

void test_an_explicit_port_is_kept(void) {
  TEST_ASSERT_TRUE(saveFromString("dashboard.local:9000"));
  load();
  TEST_ASSERT_EQUAL_UINT16(9000, port());
  TEST_ASSERT_EQUAL_STRING("http://dashboard.local:9000", baseUrl());
}

void test_a_pasted_url_is_accepted_because_that_is_what_people_paste(void) {
  TEST_ASSERT_TRUE(saveFromString("http://192.168.1.116:8080/home"));
  load();
  TEST_ASSERT_EQUAL_STRING("192.168.1.116", host());
  TEST_ASSERT_EQUAL_UINT16(8080, port());
}

void test_https_is_refused_rather_than_silently_downgraded(void) {
  // This image has no TLS. Accepting the string and then connecting in the
  // clear would be a lie told to whoever typed it.
  TEST_ASSERT_FALSE(saveFromString("https://dashboard.local"));
}

void test_junk_is_refused_and_the_previous_value_survives(void) {
  TEST_ASSERT_TRUE(saveFromString("192.168.1.116"));
  for (const char* bad : {"", "   ", "host:99999", "host:0", "host:abc", ":8080"}) {
    TEST_ASSERT_FALSE(saveFromString(bad));
  }
  TEST_ASSERT_FALSE(saveFromString(nullptr));
  load();
  TEST_ASSERT_EQUAL_STRING("192.168.1.116", host());
}

void test_an_unconfigured_device_falls_back_to_the_compiled_default(void) {
  load();
  TEST_ASSERT_EQUAL_STRING("dashboard.local", host());
  TEST_ASSERT_EQUAL_UINT16(8080, port());
}

void test_saving_takes_effect_without_a_reboot(void) {
  // The portal save callback does not get to reboot the device, so a save that
  // only reaches NVS leaves the running firmware pointed at the old server
  // until someone power-cycles it.
  load();
  TEST_ASSERT_TRUE(saveFromString("10.0.0.9:1234"));
  TEST_ASSERT_EQUAL_STRING("10.0.0.9", host());
  TEST_ASSERT_EQUAL_STRING("http://10.0.0.9:1234", baseUrl());
}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_a_bare_host_takes_the_default_port);
  RUN_TEST(test_an_explicit_port_is_kept);
  RUN_TEST(test_a_pasted_url_is_accepted_because_that_is_what_people_paste);
  RUN_TEST(test_https_is_refused_rather_than_silently_downgraded);
  RUN_TEST(test_junk_is_refused_and_the_previous_value_survives);
  RUN_TEST(test_an_unconfigured_device_falls_back_to_the_compiled_default);
  RUN_TEST(test_saving_takes_effect_without_a_reboot);
  return UNITY_END();
}
```

- [ ] **Step 6: Run it to see it fail, then implement**

```cpp
// firmware/include/services/server_config.h
#pragma once

#include <cstdint>

namespace services::server {

/** Read the stored address into memory. Call once at boot. */
void load();

const char* host();
uint16_t port();
/** "http://host:port" — no trailing slash. */
const char* baseUrl();

/**
 * Parse, persist, and apply immediately. Accepts `host`, `host:port`, and a
 * pasted `http://host:port/path`. Refuses `https://` outright: this firmware
 * has no TLS. Returns false and leaves the stored AND live values untouched on
 * anything unusable, so a typo in the portal cannot strand a working device.
 */
bool saveFromString(const char* text);

}  // namespace services::server
```

```cpp
// firmware/src/services/server_config.cpp
#include "services/server_config.h"

#include <Preferences.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <strings.h>          // strncasecmp

#include "config.h"
#include "debug_log.h"

namespace services::server {
namespace {

constexpr char kPrefsNamespace[] = "hsdisplay";
constexpr char kKeyHost[] = "host";
constexpr char kKeyPort[] = "port";

char s_host[64] = {0};
uint16_t s_port = 0;
char s_base[96] = {0};

bool parse(const char* text, char* out_host, size_t host_len,
           uint16_t* out_port) {
  if (text == nullptr) {
    return false;
  }
  while (*text == ' ' || *text == '\t') {
    ++text;
  }
  if (strncasecmp(text, "https://", 8) == 0) {
    return false;                     // no TLS in this image; say so
  }
  if (strncasecmp(text, "http://", 7) == 0) {
    text += 7;
  }
  char buf[96] = {0};
  size_t n = 0;
  while (text[n] != '\0' && text[n] != '/' && n + 1 < sizeof(buf)) {
    ++n;
  }
  memcpy(buf, text, n);
  buf[n] = '\0';
  while (n > 0 && (buf[n - 1] == ' ' || buf[n - 1] == '\t')) {
    buf[--n] = '\0';
  }
  if (n == 0) {
    return false;
  }
  uint16_t parsed_port = config::kDefaultServerPort;
  char* colon = strrchr(buf, ':');
  if (colon != nullptr) {
    *colon = '\0';
    char* end = nullptr;
    const long value = strtol(colon + 1, &end, 10);
    if (end == colon + 1 || *end != '\0' || value < 1 || value > 65535) {
      return false;
    }
    parsed_port = static_cast<uint16_t>(value);
  }
  if (buf[0] == '\0' || strlen(buf) >= host_len) {
    return false;
  }
  strncpy(out_host, buf, host_len - 1);
  out_host[host_len - 1] = '\0';
  *out_port = parsed_port;
  return true;
}

void apply(const char* new_host, uint16_t new_port) {
  strncpy(s_host, new_host, sizeof(s_host) - 1);
  s_host[sizeof(s_host) - 1] = '\0';
  s_port = new_port;
  snprintf(s_base, sizeof(s_base), "http://%s:%u", s_host,
           static_cast<unsigned>(s_port));
}

}  // namespace

void load() {
  Preferences prefs;
  // Read-only open: on a device that has never been configured the framework
  // logs "nvs_open failed: NOT_FOUND" at [E]. That is the expected steady state
  // for a fresh board, not a fault.
  if (prefs.begin(kPrefsNamespace, true)) {
    String stored = prefs.getString(kKeyHost, "");
    const uint16_t stored_port =
        prefs.getUShort(kKeyPort, config::kDefaultServerPort);
    prefs.end();
    if (stored.length() > 0 && stored.length() < sizeof(s_host)) {
      apply(stored.c_str(), stored_port);
      DEBUG_LOG("server: %s", s_base);
      return;
    }
  }
  apply(config::kDefaultServerHost, config::kDefaultServerPort);
}

const char* host() { return s_host; }
uint16_t port() { return s_port; }
const char* baseUrl() { return s_base; }

bool saveFromString(const char* text) {
  char parsed_host[64] = {0};
  uint16_t parsed_port = 0;
  if (!parse(text, parsed_host, sizeof(parsed_host), &parsed_port)) {
    return false;
  }
  Preferences prefs;
  if (!prefs.begin(kPrefsNamespace, false)) {
    return false;
  }
  prefs.putString(kKeyHost, parsed_host);
  prefs.putUShort(kKeyPort, parsed_port);
  prefs.end();
  // Apply now, not at the next boot: the portal save callback cannot reboot.
  apply(parsed_host, parsed_port);
  return true;
}

}  // namespace services::server
```

- [ ] **Step 7: Run everything green and commit**

```bash
cd firmware && pio test -e native
git add firmware/ && git commit -m "feat(firmware): device identity from the MAC, server address in NVS"
```
Expected: 5 mock + 3 smoke + 2 identity + 7 server = 17 tests pass.

---

## Task 3: The scene client

The heart of the phase. It replaces `adsb_client.cpp` entirely: no TLS, no adsb.fi URL
building, no field-fallback chains (the server did that), a real 304 path, and three
distinct expiry conditions instead of one.

**Files:** Create `firmware/{include,src}/services/scene_client.{h,cpp}`,
`firmware/test/test_scene_client/test_scene_client.cpp`.

**Depends on Task 4**, whose generator produces the `fixtures_wire.h` these tests parse.
The tasks are numbered in dependency order everywhere else; this one pair is inverted
only because it reads better. **Execute Task 4 first.** Revision 2 claimed "tasks were
reordered so nothing depends on a later one" — that claim was false for this pair, and
following the numbering literally leaves the tree red at Task 3's commit.

**Interfaces:**
- Consumes: `services::deviceId()`, `services::server::baseUrl()`, `config::*`,
  Task 0's HTTP/WiFi mocks, Task 4's `fixtures_wire.h`.
- Produces: `services::scene::Aircraft`, `kMaxAircraft`, `kExtrapolationHorizonSec`,
  `pollOnce()`, `pollTick(bool link_up)`, `startPollTask()`, `pollTaskStackFree()`,
  `aircraftCount/List/Lock/Unlock()`, `hasTraffic()`, `secondsSinceContent()`,
  `secondsSinceContentRaw()`, `contentExpired()`, `pollIntervalMs()`, `assigned()`,
  `sceneName()`, `componentName()`, `message()`, `radiusKm()`, `feedOk()`,
  `feedAgeS()`, `everReceived()`, `resetForTest()`.

- [ ] **Step 1: Write the header**

```cpp
// firmware/include/services/scene_client.h
#pragma once

#include <cstddef>
#include <cstdint>

namespace services::scene {

/**
 * One target on the radar. Field-for-field the reference firmware's `Aircraft`,
 * so radar_display.cpp ports across unmodified -- the values now arrive already
 * resolved from the server instead of being derived here.
 */
struct Aircraft {
  float lat;
  float lon;
  float nose_deg;
  float track_deg;
  float gs_knots;
  /** East/north ground velocity, km per second. Computed by the server. */
  float vel_e_km_s;
  float vel_n_km_s;
  /**
   * Age of this position in seconds as the server serves it: upstream's
   * seen_pos PLUS the time the record sat in the server's cache. Dead reckoning
   * runs from when the fix was taken, not from when we fetched it.
   */
  float pos_age_s;
  /** Distance from the radar centre (NM); < 0 if absent. */
  float dst_nm;
  char callsign[9];
  char type[5];
  char alt[12];
};

/** Also declared to the server as `max_items`, so it never sends more. */
constexpr size_t kMaxAircraft = 64;

/**
 * Dead-reckoning horizon. Shared so the drawn position (clamped to it) and the
 * stale flag (tested against it) can never be judged by different numbers.
 */
constexpr float kExtrapolationHorizonSec = 12.0f;

/** Silence from the SERVER this long and the picture must go. */
constexpr float kContactExpirySec = 60.0f;
/**
 * The server's own feed cache this stale and the picture must go, even though
 * the server is answering us perfectly.
 *
 * This is the number that matters, and it took two reviews to find. `feed_ok`
 * cannot carry this: `cache.write_failure` only runs when a fetch RUNS and
 * fails, so a fetch daemon that was stopped, hung, or exited 78 on a bad config
 * leaves `ok: true` on disk forever while the data rots. `feed_age_s` grows in
 * every one of those cases, because `fetched_at` stops advancing.
 */
constexpr float kFeedExpirySec = 60.0f;

/** One HTTP exchange. True on 200 or 304; false leaves the picture untouched. */
bool pollOnce();

/**
 * One iteration of the poll loop, including the link-down transition. Split out
 * of the task so the transition is reachable from a host test -- the mocked
 * xTaskCreate never runs the task body.
 */
void pollTick(bool link_up);

bool startPollTask();
unsigned pollTaskStackFree();

size_t aircraftCount();
const Aircraft* aircraftList();
bool aircraftLock(uint32_t timeout_ms);
void aircraftUnlock();
bool hasTraffic();

/**
 * Seconds since the last 200 that carried content, clamped to the horizon.
 * A 304 does NOT refresh it: the fix really is that old.
 */
float secondsSinceContent();
/** The same, unclamped, for the 12 s staleness test. */
float secondsSinceContentRaw();

/**
 * True once the picture must not be shown at all. TWO conditions, one for each
 * thing that can die:
 *
 *   1. we have not heard from the SERVER for kContactExpirySec
 *   2. the server's own FEED has not moved for kFeedExpirySec
 *
 * The second is the one the reference could not have: there, the device WAS the
 * feed client, so a dead feed and a failed fetch were the same event. Here the
 * Pi serves from cache and keeps answering 200/304 forever after its fetcher
 * dies, so a contact test alone can never fire and the panel would present
 * hours-old traffic as live.
 *
 * `feed_ok` is deliberately NOT a condition. `cache.write_failure` keeps the
 * last good aircraft and flips only the flag, so a single upstream timeout --
 * one of many, on a 3-second fetch cycle -- would blank the whole radar for a
 * poll and restore it, which is the once-per-cycle blink that radar_display.cpp
 * was written to eliminate. It is a rendering hint (draw the "sin señal" pill),
 * not an expiry.
 */
bool contentExpired();

unsigned long pollIntervalMs();
bool assigned();
const char* sceneName();
/** The `c` of the first component we recognise -- what the dispatcher switches on. */
const char* componentName();
/** Server-supplied Spanish text for an unassigned or failed scene. */
const char* message();
float radiusKm();
bool feedOk();
/** Age of the server's own feed cache, seconds. < 0 if the server did not say. */
float feedAgeS();
/** True once at least one 200 has been parsed since boot. */
bool everReceived();

#ifdef UNIT_TEST
/** Host tests only: forget everything between cases, and make the mutex. */
void resetForTest();
#endif

}  // namespace services::scene
```

- [ ] **Step 2: Write the tests** (they will not compile until Step 4)

```cpp
// firmware/test/test_scene_client/test_scene_client.cpp
// Exercises the real scene_client.cpp against the exact bytes the server emits
// (fixtures_wire.h is generated from it by scripts/dump_wire_fixture.py).
#include <Arduino.h>
#include <unity.h>
#include <cmath>
#include <cstring>
#include <string>

#include "fixtures_wire.h"
#include "../mocks/mock_globals.h"
#include "../../src/services/device_id.cpp"
#include "../../src/services/server_config.cpp"
#include "../../src/services/scene_client.cpp"

using namespace services::scene;

static bool poll(const char* body, int code = HTTP_CODE_OK,
                 const char* etag = "\"abc\"", const char* poll_s = "5") {
  g_http.reset();
  g_http.body = body ? body : "";
  g_http.code = code;
  if (etag) g_http.response_headers["ETag"] = etag;
  if (poll_s) g_http.response_headers["X-Poll-Seconds"] = poll_s;
  return pollOnce();
}

void setUp(void) {
  g_nvs.reset();
  mockSetMs(100000);
  WiFi.status_ = WL_CONNECTED;
  services::server::saveFromString("192.168.1.116:8080");
  resetForTest();
}
void tearDown(void) {}

// --- the request ------------------------------------------------------------

void test_the_request_declares_everything_the_server_needs(void) {
  // The server only reads a capability LIST when the same request carries w and
  // h; omitting them silently drops our component declaration and the radar
  // comes back as `unsupported` with an empty component list. max_items is what
  // stops an operator's max_aircraft from sending a body we cannot parse.
  poll(kWireAssigned);
  const std::string& url = g_http.last_url;
  for (const char* needle : {"/api/device/", "/scene?", "w=240", "h=240",
                             "depth=16", "max_items=64", "components=radar",
                             "fw=hs-0.1"}) {
    TEST_ASSERT_NOT_EQUAL_MESSAGE(std::string::npos, url.find(needle), needle);
  }
  TEST_ASSERT_EQUAL(std::string::npos, url.find("https://"));
  TEST_ASSERT_NOT_EQUAL(std::string::npos, url.find("aabb00112233"));
}

// --- parsing a real body ----------------------------------------------------

void test_a_real_server_body_becomes_aircraft(void) {
  TEST_ASSERT_TRUE(poll(kWireAssigned));
  TEST_ASSERT_EQUAL_UINT(2, aircraftCount());
  const Aircraft& a = aircraftList()[0];
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 40.5f, a.lat);
  TEST_ASSERT_FLOAT_WITHIN(0.001f, -3.6f, a.lon);
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 90.0f, a.nose_deg);
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 91.0f, a.track_deg);
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 400.0f, a.gs_knots);
  TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.13f, a.vel_e_km_s);
  TEST_ASSERT_FLOAT_WITHIN(0.0001f, -0.17f, a.vel_n_km_s);
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 7.4f, a.dst_nm);
  TEST_ASSERT_EQUAL_STRING("IBE3221", a.callsign);
  TEST_ASSERT_EQUAL_STRING("A320", a.type);
  TEST_ASSERT_EQUAL_STRING("3675 ft", a.alt);
}

void test_velocities_come_from_the_server_and_are_not_recomputed(void) {
  // The fixture's ve/vn are deliberately NOT consistent with gs=400kt/trk=91:
  // gs and track imply ve~0.206, vn~-0.004. A recompute-from-gs implementation
  // fails this assertion, which is the point.
  poll(kWireAssigned);
  TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.13f, aircraftList()[0].vel_e_km_s);
  TEST_ASSERT_FLOAT_WITHIN(0.0001f, -0.17f, aircraftList()[0].vel_n_km_s);
}

void test_the_scene_metadata_is_available_to_the_dispatcher(void) {
  poll(kWireAssigned);
  TEST_ASSERT_TRUE(assigned());
  TEST_ASSERT_EQUAL_STRING("planes", sceneName());
  TEST_ASSERT_EQUAL_STRING("radar", componentName());
  TEST_ASSERT_FLOAT_WITHIN(0.01f, 60.0f, radiusKm());
  TEST_ASSERT_TRUE(feedOk());
  TEST_ASSERT_TRUE(feedAgeS() >= 0.0f);
}

void test_an_unassigned_device_says_what_to_do_rather_than_showing_nothing(void) {
  TEST_ASSERT_TRUE(poll(kWireUnassigned));
  TEST_ASSERT_FALSE(assigned());
  TEST_ASSERT_EQUAL_UINT(0, aircraftCount());
  TEST_ASSERT_TRUE(strlen(message()) > 0);
  TEST_ASSERT_EQUAL_STRING("", componentName());
}

// --- the cadence ------------------------------------------------------------

void test_the_poll_cadence_comes_from_the_server(void) {
  poll(kWireAssigned, HTTP_CODE_OK, "\"x\"", "30");
  TEST_ASSERT_EQUAL_UINT32(30000, pollIntervalMs());
}

void test_an_absurd_cadence_is_clamped_not_obeyed(void) {
  poll(kWireAssigned, HTTP_CODE_OK, "\"a\"", "0");
  TEST_ASSERT_EQUAL_UINT32(config::kPollMinMs, pollIntervalMs());
  poll(kWireAssigned, HTTP_CODE_OK, "\"b\"", "999999");
  TEST_ASSERT_EQUAL_UINT32(config::kPollMaxMs, pollIntervalMs());
  poll(kWireAssigned, HTTP_CODE_OK, "\"c\"", "-1");
  TEST_ASSERT_EQUAL_UINT32(config::kPollMinMs, pollIntervalMs());
  poll(kWireAssigned, HTTP_CODE_OK, "\"d\"", "not-a-number");
  TEST_ASSERT_EQUAL_UINT32(config::kPollDefaultMs, pollIntervalMs());
}

// --- 304 --------------------------------------------------------------------

void test_the_etag_is_echoed_verbatim_including_its_quotes(void) {
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  TEST_ASSERT_EQUAL_STRING(kWireAssignedEtag, g_http.last_if_none_match.c_str());
  TEST_ASSERT_EQUAL_CHAR('"', g_http.last_if_none_match[0]);
}

void test_a_304_keeps_the_list_and_is_not_a_failure(void) {
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  TEST_ASSERT_EQUAL_UINT(2, aircraftCount());
  TEST_ASSERT_TRUE(poll("", HTTP_CODE_NOT_MODIFIED, kWireAssignedEtag));
  TEST_ASSERT_EQUAL_UINT(2, aircraftCount());
  TEST_ASSERT_EQUAL_STRING("IBE3221", aircraftList()[0].callsign);
  TEST_ASSERT_EQUAL_STRING("radar", componentName());
}

void test_a_304_freezes_the_content_clock_and_refreshes_the_contact_clock(void) {
  // PLAN.md section 3, the requirement that survived the protocol change. The
  // fix really is as old as the last 200, so extrapolation and the 12s dim test
  // must keep ageing -- but we DID just hear from the server, so the contact
  // bound must not fire.
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  mockAdvanceMs(30000);
  poll("", HTTP_CODE_NOT_MODIFIED, kWireAssignedEtag);
  mockAdvanceMs(40000);
  poll("", HTTP_CODE_NOT_MODIFIED, kWireAssignedEtag);

  TEST_ASSERT_FLOAT_WITHIN(0.5f, 70.0f, secondsSinceContentRaw());
  TEST_ASSERT_FLOAT_WITHIN(0.01f, kExtrapolationHorizonSec, secondsSinceContent());
  TEST_ASSERT_FALSE(contentExpired());
}

void test_a_304_does_not_disturb_the_parsed_picture(void) {
  // A 304 has no body. Parsing "" would clear the list and blank the screen.
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  const float lat_before = aircraftList()[0].lat;
  poll("", HTTP_CODE_NOT_MODIFIED, kWireAssignedEtag);
  TEST_ASSERT_EQUAL_UINT(2, aircraftCount());
  TEST_ASSERT_EQUAL_FLOAT(lat_before, aircraftList()[0].lat);
  TEST_ASSERT_EQUAL_INT(1, g_http.get_calls);   // the helper resets the counter
}

// --- the three expiries -----------------------------------------------------

void test_silence_from_the_server_expires_the_picture(void) {
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  mockAdvanceMs(61000);
  TEST_ASSERT_TRUE(contentExpired());
}

void test_a_server_that_answers_forever_with_a_dead_feed_still_expires(void) {
  // THE failure the reference could not have, and the one two reviews were
  // needed to state correctly. serve.py serves from cache and never fetches on
  // a device request, so a STOPPED fetch daemon leaves ok:true on disk forever
  // -- cache.write_failure only runs when a fetch RUNS and fails. Nothing flips
  // feed_ok, the server keeps answering, and only feed_age_s grows.
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  for (int i = 0; i < 12; ++i) {                 // two minutes of 304s
    mockAdvanceMs(10000);
    TEST_ASSERT_TRUE(poll("", HTTP_CODE_NOT_MODIFIED, kWireAssignedEtag));
  }
  TEST_ASSERT_TRUE(contentExpired());
}

void test_one_upstream_hiccup_does_not_blank_the_radar(void) {
  // write_failure KEEPS the last good aircraft and flips only the flag, on a
  // 3-second fetch cycle. Expiring on feed_ok would blank the whole picture for
  // one poll and restore it -- the once-per-cycle blink radar_display.cpp was
  // written to eliminate.
  TEST_ASSERT_TRUE(poll(kWireAssigned, HTTP_CODE_OK, "\"a\""));
  TEST_ASSERT_FALSE(contentExpired());
  TEST_ASSERT_TRUE(poll(kWireFeedDown, HTTP_CODE_OK, "\"b\""));
  TEST_ASSERT_FALSE_MESSAGE(contentExpired(),
                            "a transient must not blank the panel");
  TEST_ASSERT_FALSE(feedOk());          // ...but the renderer may show a pill
}

void test_a_feed_that_stays_down_does_expire(void) {
  // The other half: not blinking is not the same as never noticing.
  poll(kWireAssigned, HTTP_CODE_OK, "\"a\"");
  for (int i = 0; i < 12; ++i) {
    mockAdvanceMs(10000);
    poll("", HTTP_CODE_NOT_MODIFIED, "\"a\"");
  }
  TEST_ASSERT_TRUE(contentExpired());
}

void test_nothing_is_expired_before_the_first_reply(void) {
  // The reference guards both accessors on `last_update != 0`. Without it,
  // twelve seconds of uptime marks every target stale and sixty blanks the
  // panel -- before the device has even spoken to the server.
  mockAdvanceMs(120000);
  TEST_ASSERT_FALSE(contentExpired());
  TEST_ASSERT_EQUAL_FLOAT(0.0f, secondsSinceContentRaw());
  TEST_ASSERT_EQUAL_FLOAT(0.0f, secondsSinceContent());
  TEST_ASSERT_FALSE(everReceived());
}

// --- failure paths ----------------------------------------------------------

void test_an_http_error_keeps_the_last_good_scene(void) {
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  TEST_ASSERT_FALSE(poll("", 500));
  TEST_ASSERT_EQUAL_UINT(2, aircraftCount());
  TEST_ASSERT_FALSE(poll("", HTTPC_ERROR_CONNECTION_REFUSED));
  TEST_ASSERT_EQUAL_UINT(2, aircraftCount());
}

void test_every_failure_path_drops_the_socket(void) {
  // HTTPClient keeps a keep-alive connection across end(). When the Pi is power
  // cycled there is no FIN, so `connected()` stays true, the next request is
  // written into a dead socket, and handleHeaderResponse times out after 8 s --
  // forever, because _canReuse was never cleared. The reference forced a fresh
  // session on every failure for exactly this reason; without it the device
  // stops updating and never self-heals.
  poll(kWireAssigned);
  // One case per failure path, each with the status code that actually reaches
  // it -- sending a malformed BODY with a 500 exits at the status check and
  // never touches the shape guard, so the guard's teardown would go untested.
  struct Case { const char* body; int code; } cases[] = {
      {"", 500},                       // HTTP error
      {"", HTTPC_ERROR_CONNECTION_REFUSED},
      {"<html>nope</html>", HTTP_CODE_OK},    // parse error
      {"{\"a\":1}", HTTP_CODE_OK},            // shape guard
  };
  for (const auto& c : cases) {
    const int before = g_wc.stop_calls;
    poll(c.body, c.code);
    TEST_ASSERT_GREATER_THAN_MESSAGE(before, g_wc.stop_calls, c.body);
  }
}

void test_a_successful_poll_keeps_the_connection(void) {
  // The other half: dropping the socket on success would pay a fresh TCP
  // handshake every cadence for nothing.
  poll(kWireAssigned);
  const int before = g_wc.stop_calls;
  poll(kWireAssigned);
  poll("", HTTP_CODE_NOT_MODIFIED);
  TEST_ASSERT_EQUAL_INT(before, g_wc.stop_calls);
}

void test_a_body_that_is_not_a_scene_is_rejected_rather_than_believed(void) {
  // An HTML captive-portal page, a bare null, or a chunk-size line must not
  // read as "empty sky": that wipes real traffic AND refreshes the clocks, so
  // no expiry ever fires and the screen sits there reporting itself healthy.
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  for (const char* junk : {"<html>nope</html>", "null", "42", "500",
                           "{\"a\":1}", "{\"components\":5}", ""}) {
    TEST_ASSERT_FALSE_MESSAGE(poll(junk), junk);
    TEST_ASSERT_EQUAL_UINT(2, aircraftCount());
  }
}

void test_an_oversized_body_is_refused_before_it_is_parsed(void) {
  // Not truncated after parsing -- refused before. ArduinoJson peaks ~4.6x the
  // body, so a 30 KB body is ~88 KB of peak against ~55 KB of heap: NoMemory,
  // every cycle, at exactly the busiest time of day.
  poll(kWireAssigned);
  g_http.reset();
  g_http.body = kWireAssigned;
  g_http.content_length_override = config::kMaxBodyBytes + 1;
  TEST_ASSERT_FALSE(pollOnce());
  TEST_ASSERT_EQUAL_UINT(2, aircraftCount());
}

void test_a_chunked_response_is_refused_not_misparsed(void) {
  // getStreamPtr() does not decode chunk framing, so a chunked body starts with
  // a hex length. `500\r\n{...}` parses as the NUMBER 500 and the shape guard
  // rejects it -- but silently, forever. getSize() < 0 catches it up front.
  poll(kWireAssigned);
  g_http.reset();
  g_http.body = kWireAssigned;
  g_http.content_length_override = -1;
  TEST_ASSERT_FALSE(pollOnce());
}

void test_a_scene_with_no_radar_component_clears_the_sky(void) {
  // Not the same as a bad body: the server said, in a well-formed scene, that
  // this device is showing something else now.
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  TEST_ASSERT_TRUE(poll(kWireUnassigned));
  TEST_ASSERT_EQUAL_UINT(0, aircraftCount());
}

// --- hostile and malformed items --------------------------------------------

void test_more_items_than_we_declared_are_truncated_not_overflowed(void) {
  std::string big = "{\"scene\":\"planes\",\"assigned\":true,\"layout\":\"fill\","
                    "\"components\":[{\"c\":\"radar\",\"items\":[";
  for (size_t i = 0; i < kMaxAircraft + 20; ++i) {
    if (i) big += ",";
    big += "{\"lat\":40.5,\"lon\":-3.6,\"cs\":\"X\"}";
  }
  big += "]}]}";
  TEST_ASSERT_TRUE(poll(big.c_str()));
  TEST_ASSERT_EQUAL_UINT(kMaxAircraft, aircraftCount());
}

void test_an_item_without_a_position_is_skipped(void) {
  TEST_ASSERT_TRUE(poll("{\"scene\":\"planes\",\"assigned\":true,"
                        "\"layout\":\"fill\",\"components\":[{\"c\":\"radar\","
                        "\"items\":[{\"cs\":\"NOPOS\"},"
                        "{\"lat\":1.0,\"lon\":2.0,\"cs\":\"OK\"}]}]}"));
  TEST_ASSERT_EQUAL_UINT(1, aircraftCount());
  TEST_ASSERT_EQUAL_STRING("OK", aircraftList()[0].callsign);
}

void test_an_overlong_tag_cannot_overrun_its_buffer(void) {
  // The callsign originates at a third-party feed. ASan turns a mistake here
  // into an abort naming the line rather than silent corruption.
  TEST_ASSERT_TRUE(poll("{\"scene\":\"planes\",\"assigned\":true,"
                        "\"layout\":\"fill\",\"components\":[{\"c\":\"radar\","
                        "\"items\":[{\"lat\":1.0,\"lon\":2.0,"
                        "\"cs\":\"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\","
                        "\"ty\":\"BBBBBBBBBB\",\"alt\":\"CCCCCCCCCCCCCCCCCC\"}]}]}"));
  TEST_ASSERT_EQUAL_UINT(8, strlen(aircraftList()[0].callsign));
  TEST_ASSERT_EQUAL_UINT(4, strlen(aircraftList()[0].type));
  TEST_ASSERT_EQUAL_UINT(11, strlen(aircraftList()[0].alt));
}

void test_a_non_finite_number_never_reaches_the_renderer(void) {
  // inf/nan through the projection gives a coordinate that is neither on screen
  // nor off it, and clipPointToOuterRing does not converge.
  TEST_ASSERT_TRUE(poll("{\"scene\":\"planes\",\"assigned\":true,"
                        "\"layout\":\"fill\",\"components\":[{\"c\":\"radar\","
                        "\"items\":[{\"lat\":1e400,\"lon\":2.0,\"cs\":\"BAD\"},"
                        "{\"lat\":1.0,\"lon\":2.0,\"ve\":1e400,\"cs\":\"V\"},"
                        "{\"lat\":3.0,\"lon\":4.0,\"cs\":\"OK\"}]}]}"));
  for (size_t i = 0; i < aircraftCount(); ++i) {
    TEST_ASSERT_TRUE(std::isfinite(aircraftList()[i].lat));
    TEST_ASSERT_TRUE(std::isfinite(aircraftList()[i].lon));
    TEST_ASSERT_TRUE(std::isfinite(aircraftList()[i].vel_e_km_s));
    TEST_ASSERT_TRUE(std::isfinite(aircraftList()[i].pos_age_s));
  }
}

// --- concurrency ------------------------------------------------------------

void test_the_reader_lock_is_taken_and_released_around_every_publish(void) {
  // resetForTest() creates the mutex; without it aircraftLock() takes the
  // `s_mutex == nullptr -> true` path and this test passes against a publish()
  // that never locks at all.
  TEST_ASSERT_EQUAL_INT(1, g_mutex_live);
  poll(kWireAssigned);
  TEST_ASSERT_TRUE(aircraftLock(10));
  aircraftUnlock();
  poll("", 500);
  TEST_ASSERT_TRUE(aircraftLock(10));
  aircraftUnlock();
  TEST_ASSERT_EQUAL_INT(0, g_mutex_outstanding);
}

void test_the_content_clock_is_stamped_with_the_swap_under_one_lock(void) {
  // The host is single-threaded, so no test here can make a frame interleave.
  // What CAN be asserted is that the stamp happens while the mutex is held --
  // which is the property that makes interleaving safe on the device. Set
  // outside the lock, a frame draws the NEW positions against the OLD content
  // time: up to a poll interval of extra dead reckoning, ~1 km at 400 kt, as a
  // jump-and-snap once per poll.
  int held_during_stamp = -1;
  g_mutex_on_give = [&]() { held_during_stamp = g_mutex_outstanding; };
  poll(kWireAssigned);
  g_mutex_on_give = nullptr;
  TEST_ASSERT_EQUAL_INT_MESSAGE(1, held_during_stamp,
                                "the clock was stamped outside the lock");
  TEST_ASSERT_FLOAT_WITHIN(0.01f, 0.0f, secondsSinceContentRaw());
  TEST_ASSERT_EQUAL_UINT(2, aircraftCount());
}

// --- the link-down transition ----------------------------------------------

void test_losing_the_link_drops_the_socket(void) {
  // A socket that survives a Wi-Fi drop is a socket that will time out for 8 s
  // on every poll after the link returns.
  poll(kWireAssigned);
  const int before = g_wc.stop_calls;
  pollTick(false);
  TEST_ASSERT_GREATER_THAN(before, g_wc.stop_calls);
}

void test_the_link_down_teardown_happens_once_not_every_tick(void) {
  poll(kWireAssigned);
  pollTick(false);
  const int after_first = g_wc.stop_calls;
  pollTick(false);
  pollTick(false);
  TEST_ASSERT_EQUAL_INT(after_first, g_wc.stop_calls);
}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_the_request_declares_everything_the_server_needs);
  RUN_TEST(test_a_real_server_body_becomes_aircraft);
  RUN_TEST(test_velocities_come_from_the_server_and_are_not_recomputed);
  RUN_TEST(test_the_scene_metadata_is_available_to_the_dispatcher);
  RUN_TEST(test_an_unassigned_device_says_what_to_do_rather_than_showing_nothing);
  RUN_TEST(test_the_poll_cadence_comes_from_the_server);
  RUN_TEST(test_an_absurd_cadence_is_clamped_not_obeyed);
  RUN_TEST(test_the_etag_is_echoed_verbatim_including_its_quotes);
  RUN_TEST(test_a_304_keeps_the_list_and_is_not_a_failure);
  RUN_TEST(test_a_304_freezes_the_content_clock_and_refreshes_the_contact_clock);
  RUN_TEST(test_a_304_does_not_disturb_the_parsed_picture);
  RUN_TEST(test_silence_from_the_server_expires_the_picture);
  RUN_TEST(test_a_server_that_answers_forever_with_a_dead_feed_still_expires);
  RUN_TEST(test_one_upstream_hiccup_does_not_blank_the_radar);
  RUN_TEST(test_a_feed_that_stays_down_does_expire);
  RUN_TEST(test_nothing_is_expired_before_the_first_reply);
  RUN_TEST(test_an_http_error_keeps_the_last_good_scene);
  RUN_TEST(test_every_failure_path_drops_the_socket);
  RUN_TEST(test_a_successful_poll_keeps_the_connection);
  RUN_TEST(test_a_body_that_is_not_a_scene_is_rejected_rather_than_believed);
  RUN_TEST(test_an_oversized_body_is_refused_before_it_is_parsed);
  RUN_TEST(test_a_chunked_response_is_refused_not_misparsed);
  RUN_TEST(test_a_scene_with_no_radar_component_clears_the_sky);
  RUN_TEST(test_more_items_than_we_declared_are_truncated_not_overflowed);
  RUN_TEST(test_an_item_without_a_position_is_skipped);
  RUN_TEST(test_an_overlong_tag_cannot_overrun_its_buffer);
  RUN_TEST(test_a_non_finite_number_never_reaches_the_renderer);
  RUN_TEST(test_the_reader_lock_is_taken_and_released_around_every_publish);
  RUN_TEST(test_the_content_clock_is_stamped_with_the_swap_under_one_lock);
  RUN_TEST(test_losing_the_link_drops_the_socket);
  RUN_TEST(test_the_link_down_teardown_happens_once_not_every_tick);
  return UNITY_END();
}
```

- [ ] **Step 3: Run to see it fail**

```bash
export PATH="$HOME/.platformio/penv/bin:$PATH"
cd firmware && pio test -e native -f test_scene_client
```
Expected: FAIL — `scene_client.cpp` does not exist.

- [ ] **Step 4: Implement `scene_client.cpp` in full**

```cpp
#include "services/scene_client.h"

#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFi.h>

#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "config.h"
#include "debug_log.h"
#include "services/device_id.h"
#include "services/server_config.h"

namespace services::scene {
namespace {

/** Double buffer: parse into the back one, swap under the mutex. */
Aircraft s_buffers[2][kMaxAircraft];
size_t s_counts[2] = {0, 0};
uint8_t s_front = 0;
SemaphoreHandle_t s_mutex = nullptr;
TaskHandle_t s_task = nullptr;

/**
 * TWO clocks, because a 304 means different things to different consumers
 * (PLAN.md section 3, spec section 7.4):
 *
 *   s_content_ms  last 200 that carried content. FROZEN by a 304. Drives
 *                 extrapolation and the 12 s dim test -- the fix really is as
 *                 old as the last 200.
 *   s_contact_ms  last successful exchange, 200 OR 304. Drives the 60 s
 *                 "server is gone" bound -- we just heard from it.
 *
 * Collapsing these into one field is the bug this comment exists to prevent:
 * one way dims every target at 12 s with perfectly current data, the other
 * shows minutes-old traffic as live.
 */
unsigned long s_content_ms = 0;
unsigned long s_contact_ms = 0;
bool s_ever_received = false;
bool s_was_link_up = false;
/** Result of the last pollTick, so the task can pick its delay. */
bool s_last_poll_ok = false;
/** collectHeaders reallocates per call; register the list once. */
bool s_headers_registered = false;

char s_etag[40] = {0};
char s_scene[24] = {0};
char s_component[16] = {0};
char s_message[80] = {0};
bool s_assigned = false;
bool s_feed_ok = false;
float s_feed_age_s = -1.0f;
float s_radius_km = 0.0f;
unsigned long s_poll_ms = config::kPollDefaultMs;

constexpr unsigned long kRequestTimeoutMs = 8000;
constexpr int kConnectAttemptMs = 400;
/**
 * Plain HTTP: no mbedTLS handshake on this stack, so far less than the
 * reference's 8192 (which measured 3,636 B free with TLS). 6144 is also a
 * fragmentation improvement: xTaskCreate needs its stack as ONE contiguous heap
 * block, and 8192 against a ~9 KB largest free block was close to the wall.
 * Confirm with pollTaskStackFree() on hardware before trusting it.
 */
constexpr uint32_t kPollTaskStackBytes = 6144;

WiFiClient s_client;
HTTPClient s_http;

/** Everything the parse produced, so it can be installed under one lock. */
struct Parsed {
  size_t count = 0;
  bool assigned = false;
  bool feed_ok = false;
  float feed_age_s = -1.0f;
  float radius_km = 0.0f;
  char scene[24] = {0};
  char component[16] = {0};
  char message[80] = {0};
};

void copyTrimmed(JsonObjectConst obj, const char* key, char* out, size_t len) {
  out[0] = '\0';
  if (len == 0 || !obj[key].is<const char*>()) {
    return;
  }
  const char* s = obj[key].as<const char*>();
  size_t n = strlen(s);
  while (n > 0 && s[n - 1] == ' ') {
    --n;
  }
  if (n >= len) {
    n = len - 1;
  }
  memcpy(out, s, n);
  out[n] = '\0';
}

bool readFloat(JsonObjectConst obj, const char* key, float* out) {
  if (!obj[key].is<float>()) {
    return false;              // is<float>() already covers integers in AJ7
  }
  const float v = obj[key].as<float>();
  if (!std::isfinite(v)) {
    return false;              // inf/nan through the projection never converges
  }
  *out = v;
  return true;
}

/**
 * Install a parsed scene and stamp both clocks, ALL under the mutex.
 *
 * The reference sets its timestamp inside the lock with the swap, and that is
 * not incidental: radar_display reads the clock and the aircraft list inside
 * one lock and multiplies them together. A clock set outside means a frame can
 * draw the NEW positions against the OLD content time -- up to a poll interval
 * of extra dead reckoning, a jump-and-snap once per poll, plus a whole-picture
 * grey flash because the staleness test reads the stale number too.
 *
 * The scene metadata goes in here for the same reason: copyTrimmed() writes
 * out[0] = '\0' before its memcpy, so a frame landing mid-parse would render
 * the unassigned screen with an EMPTY message -- a blank round panel, which is
 * exactly what the unassigned screen exists to prevent.
 */
void install(uint8_t back, const Parsed& p, unsigned long now,
             bool content_changed) {
  s_counts[back] = p.count;             // back buffer: no reader can see it yet
  if (s_mutex != nullptr) {
    xSemaphoreTake(s_mutex, portMAX_DELAY);
  }
  s_front = back;
  s_assigned = p.assigned;
  s_feed_ok = p.feed_ok;
  s_feed_age_s = p.feed_age_s;
  s_radius_km = p.radius_km;
  memcpy(s_scene, p.scene, sizeof(s_scene));
  memcpy(s_component, p.component, sizeof(s_component));
  memcpy(s_message, p.message, sizeof(s_message));
  if (content_changed) {
    s_content_ms = now;
  }
  s_contact_ms = now;
  s_ever_received = true;
  if (s_mutex != nullptr) {
    xSemaphoreGive(s_mutex);
  }
}

/** A 304: refresh only the contact clock, still under the lock. */
void noteContact(unsigned long now) {
  if (s_mutex != nullptr) {
    xSemaphoreTake(s_mutex, portMAX_DELAY);
  }
  s_contact_ms = now;
  if (s_mutex != nullptr) {
    xSemaphoreGive(s_mutex);
  }
}

void buildUrl(char* out, size_t len) {
  // Field order matters: snprintf truncates, and the tail is the least
  // important part. components= must precede the telemetry, because losing it
  // silently drops our capability declaration.
  const int n = snprintf(
      out, len,
      "%s/api/device/%s/scene?w=%d&h=%d&depth=%d&max_items=%u"
      "&components=radar&fw=%s&uptime=%lu&rssi=%d",
      server::baseUrl(), deviceId(), config::kDisplayWidth,
      config::kDisplayHeight, config::kDisplayDepth,
      static_cast<unsigned>(kMaxAircraft), config::kFirmwareVersion,
      millis() / 1000UL, WiFi.RSSI());
  if (n < 0 || static_cast<size_t>(n) >= len) {
    DEBUG_LOG("poll: URL truncated at %u bytes -- host too long?",
              static_cast<unsigned>(len));
  }
}

void applyPollHeader(const String& value) {
  char* end = nullptr;
  const long seconds = strtol(value.c_str(), &end, 10);
  if (end == value.c_str() || *end != '\0') {
    s_poll_ms = config::kPollDefaultMs;
    return;
  }
  // Clamp BEFORE multiplying: 99999999 * 1000 overflows unsigned long on a
  // 32-bit target, and the host tests would never catch it.
  const long clamped =
      std::min<long>(std::max<long>(seconds, 0), config::kPollMaxMs / 1000);
  unsigned long ms = static_cast<unsigned long>(clamped) * 1000UL;
  if (ms < config::kPollMinMs) {
    ms = config::kPollMinMs;
  }
  s_poll_ms = ms;
}

/** Drop the TCP connection so the next poll opens a fresh one. */
void dropSocket() {
  s_http.end();
  s_client.stop();
}

float secondsSince(unsigned long stamp) {
  if (stamp == 0) {
    return 0.0f;               // before the first reply, nothing is old
  }
  return (millis() - stamp) / 1000.0f;
}

}  // namespace

bool pollOnce() {
  if (WiFi.status() != WL_CONNECTED) {
    return false;
  }
  char url[224];
  buildUrl(url, sizeof(url));

  if (!s_http.begin(s_client, url)) {
    DEBUG_LOG("poll: begin failed");
    dropSocket();
    return false;
  }
  s_http.setConnectTimeout(kConnectAttemptMs);
  s_http.setTimeout(kRequestTimeoutMs);
  // collectHeaders new[]s and delete[]s its array on every call, so this is
  // hoisted to once per connection rather than once per poll. sendRequest()
  // clears the collected VALUES per request, so the registration persists.
  if (!s_headers_registered) {
    static const char* kWanted[] = {"ETag", "X-Poll-Seconds"};
    s_http.collectHeaders(kWanted, 2);
    s_headers_registered = true;
  }
  if (s_etag[0] != '\0') {
    s_http.addHeader("If-None-Match", s_etag);
  }

  const int code = s_http.GET();

  // 304 FIRST, and off the failure path. In the reference this fell into the
  // `code != HTTP_CODE_OK` branch and was counted as an error; here it is the
  // normal case for a quiet sky. It must touch neither the buffers nor the
  // content clock, and must not reparse -- there is no body to parse.
  if (code == HTTP_CODE_NOT_MODIFIED) {
    noteContact(millis());
    applyPollHeader(s_http.header("X-Poll-Seconds"));
    s_http.end();                       // keep the connection; it is healthy
    return true;
  }
  if (code != HTTP_CODE_OK) {
    // Not just end(): HTTPClient keeps a keep-alive socket across end(), and a
    // server that vanished without a FIN leaves `connected()` true forever --
    // every later request then writes into a dead socket and times out after
    // 8 s, with no recovery. The reference forced a fresh session here for
    // exactly this reason.
    dropSocket();
    return false;
  }

  // Refuse before parsing, not after. getSize() < 0 means chunked (the stream
  // pointer does NOT decode chunk framing, so the body would start with a hex
  // length); too large means the parse peaks past the heap.
  const int body_len = s_http.getSize();
  if (body_len < 0 || body_len > config::kMaxBodyBytes) {
    DEBUG_LOG("poll: unusable body length %d", body_len);
    dropSocket();
    return false;
  }

  WiFiClient* body = s_http.getStreamPtr();
  if (body == nullptr) {
    dropSocket();
    return false;
  }
  JsonDocument doc;
  const DeserializationError err = deserializeJson(doc, *body);
  if (err) {
    DEBUG_LOG("poll: JSON parse error: %s", err.c_str());
    dropSocket();
    return false;
  }

  // Insist on the shape. A captive-portal HTML page, a bare `null`, a plain
  // number and a chunk-size line all deserialize "Ok" into a document whose
  // ["components"] is null. Treating that as an empty scene would wipe real
  // traffic AND refresh the clocks, so no expiry would ever fire -- the screen
  // would sit there showing nothing and reporting itself healthy.
  if (!doc["components"].is<JsonArrayConst>()) {
    DEBUG_LOG("poll: rejected -- body is not a scene");
    dropSocket();
    return false;
  }

  JsonObjectConst root = doc.as<JsonObjectConst>();
  Parsed p;
  p.assigned = root["assigned"].is<bool>() && root["assigned"].as<bool>();
  copyTrimmed(root, "scene", p.scene, sizeof(p.scene));
  copyTrimmed(root, "message", p.message, sizeof(p.message));

  const uint8_t back = s_front ^ 1;
  Aircraft* out = s_buffers[back];

  for (JsonObjectConst comp : doc["components"].as<JsonArrayConst>()) {
    if (!comp["c"].is<const char*>() ||
        strcmp(comp["c"].as<const char*>(), "radar") != 0) {
      continue;                         // a component this build cannot draw
    }
    copyTrimmed(comp, "c", p.component, sizeof(p.component));
    readFloat(comp, "radius_km", &p.radius_km);
    readFloat(comp, "feed_age_s", &p.feed_age_s);
    p.feed_ok = comp["feed_ok"].is<bool>() && comp["feed_ok"].as<bool>();
    if (!comp["items"].is<JsonArrayConst>()) {
      break;
    }
    for (JsonObjectConst item : comp["items"].as<JsonArrayConst>()) {
      if (p.count >= kMaxAircraft) {
        break;                          // truncate; never overrun
      }
      float lat = 0.0f;
      float lon = 0.0f;
      if (!readFloat(item, "lat", &lat) || !readFloat(item, "lon", &lon)) {
        continue;                       // no position, nothing to plot
      }
      Aircraft& a = out[p.count];
      a = Aircraft{};
      a.lat = lat;
      a.lon = lon;
      readFloat(item, "nose", &a.nose_deg);
      readFloat(item, "trk", &a.track_deg);
      readFloat(item, "gs", &a.gs_knots);
      // ve/vn arrive already resolved into km/s east/north: the server does it
      // once per fetch so the render loop needs no trig per frame. Recomputing
      // here would diverge silently from the server's mapping.
      readFloat(item, "ve", &a.vel_e_km_s);
      readFloat(item, "vn", &a.vel_n_km_s);
      readFloat(item, "age", &a.pos_age_s);
      a.dst_nm = -1.0f;
      readFloat(item, "dst", &a.dst_nm);
      copyTrimmed(item, "cs", a.callsign, sizeof(a.callsign));
      copyTrimmed(item, "ty", a.type, sizeof(a.type));
      copyTrimmed(item, "alt", a.alt, sizeof(a.alt));
      ++p.count;
    }
    break;                              // one radar component per scene
  }

  install(back, p, millis(), /*content_changed=*/true);

  // Headers are read BEFORE end() here, which needs no justification -- but it
  // would also be safe after it: HTTPClient::clear() resets _returnCode, _size
  // and _headers and does NOT touch _currentHeaders[i].value, which survives
  // until the next sendRequest(). Noted because it looks like a use-after-free
  // either way, and someone will eventually "fix" it in the wrong direction.
  const String etag = s_http.header("ETag");
  strncpy(s_etag, etag.c_str(), sizeof(s_etag) - 1);
  s_etag[sizeof(s_etag) - 1] = '\0';
  applyPollHeader(s_http.header("X-Poll-Seconds"));
  s_http.end();                         // keep the connection; it is healthy
  DEBUG_LOG("poll: scene=%s %u items", s_scene, static_cast<unsigned>(p.count));
  return true;
}

void pollTick(bool link_up) {
  feedWatchdog();
  if (!link_up) {
    if (s_was_link_up) {
      // Once, on the transition. A socket that survives a Wi-Fi drop times out
      // for 8 s on every poll after the link returns.
      dropSocket();
      s_was_link_up = false;
    }
    s_last_poll_ok = false;
    return;
  }
  s_was_link_up = true;
  s_last_poll_ok = pollOnce();
}

size_t aircraftCount() { return s_counts[s_front]; }
const Aircraft* aircraftList() { return s_buffers[s_front]; }

bool aircraftLock(uint32_t timeout_ms) {
  if (s_mutex == nullptr) {
    return true;
  }
  return xSemaphoreTake(s_mutex, pdMS_TO_TICKS(timeout_ms)) == pdTRUE;
}

void aircraftUnlock() {
  if (s_mutex != nullptr) {
    xSemaphoreGive(s_mutex);
  }
}

bool hasTraffic() { return s_counts[s_front] > 0 && !contentExpired(); }

float secondsSinceContent() {
  return std::min(secondsSince(s_content_ms), kExtrapolationHorizonSec);
}

float secondsSinceContentRaw() { return secondsSince(s_content_ms); }

bool contentExpired() {
  if (!s_ever_received) {
    return false;                       // nothing to expire yet
  }
  if (secondsSince(s_contact_ms) >= kContactExpirySec) {
    return true;                        // the server itself is gone
  }
  // The server is answering, but its feed stopped moving. feed_age_s is the
  // only number that grows in every way that fails -- daemon stopped, daemon
  // hung, daemon exited 78, upstream down -- because all of them leave
  // fetched_at frozen. It also does not twitch on a single transient, which is
  // exactly why feed_ok is not tested here.
  if (s_feed_age_s >= 0.0f && s_feed_age_s + secondsSince(s_content_ms)
                                  >= kFeedExpirySec) {
    return true;
  }
  return false;
}

unsigned long pollIntervalMs() { return s_poll_ms; }
bool assigned() { return s_assigned; }
const char* sceneName() { return s_scene; }
const char* componentName() { return s_component; }
const char* message() { return s_message; }
float radiusKm() { return s_radius_km; }
bool feedOk() { return s_feed_ok; }
float feedAgeS() { return s_feed_age_s; }
bool everReceived() { return s_ever_received; }

unsigned pollTaskStackFree() {
  return s_task == nullptr
             ? 0
             : static_cast<unsigned>(uxTaskGetStackHighWaterMark(s_task));
}

namespace {

void pollTaskBody(void*) {
  for (;;) {
    // pollTick, NOT pollOnce: the link-down teardown and the watchdog feed both
    // live in pollTick, and a task that calls pollOnce directly reaches neither.
    // That is not a style point -- it is the difference between a socket that
    // survives a Wi-Fi drop and times out for 8 s on every later poll, and one
    // that does not.
    pollTick(WiFi.status() == WL_CONNECTED);
    vTaskDelay(pdMS_TO_TICKS(s_last_poll_ok ? s_poll_ms
                                            : config::kPollErrorMs));
  }
}

}  // namespace

bool startPollTask() {
  if (s_mutex == nullptr) {
    s_mutex = xSemaphoreCreateMutex();
    if (s_mutex == nullptr) {
      return false;
    }
  }
  if (s_task != nullptr) {
    return true;
  }
  return xTaskCreate(pollTaskBody, "scene", kPollTaskStackBytes, nullptr, 1,
                     &s_task) == pdPASS;
}

#ifdef UNIT_TEST
void resetForTest() {
  if (s_mutex == nullptr) {
    s_mutex = xSemaphoreCreateMutex();
  }
  s_counts[0] = s_counts[1] = 0;
  s_front = 0;
  s_content_ms = s_contact_ms = 0;
  s_ever_received = false;
  s_was_link_up = false;
  s_last_poll_ok = false;
  s_headers_registered = false;
  s_etag[0] = s_scene[0] = s_component[0] = s_message[0] = '\0';
  s_assigned = false;
  s_feed_ok = false;
  s_feed_age_s = -1.0f;
  s_radius_km = 0.0f;
  s_poll_ms = config::kPollDefaultMs;
}
#endif

}  // namespace services::scene
```

- [ ] **Step 5: Run the tests green**

```bash
cd firmware && pio test -e native -f test_scene_client
```
Expected: 30 tests pass.

- [ ] **Step 6: Prove the two clocks are really two**

The mutation must apply to a literal that exists. Check first, then mutate:

```bash
cd firmware
BEFORE=$(grep -c "s_contact_ms = now;" src/services/scene_client.cpp)
echo "$BEFORE"        # expect 2: install() and noteContact() both stamp it
cp src/services/scene_client.cpp /tmp/sc.bak
# Collapse them, as the reference has them. Both sites are mutated, which is
# what we want -- either one alone would still freeze the content clock.
sed -i '' 's/^  s_contact_ms = now;$/  s_contact_ms = now; s_content_ms = now;/' \
  src/services/scene_client.cpp
test "$(grep -c 's_content_ms = now;' src/services/scene_client.cpp)" = "3" || \
  { echo "mutation did NOT apply -- fix the pattern before trusting this"; exit 1; }
export PATH="$HOME/.platformio/penv/bin:$PATH"
pio test -e native -f test_scene_client                        # MUST fail
cp /tmp/sc.bak src/services/scene_client.cpp
pio test -e native -f test_scene_client                        # green again
```
Expected: the mutated build fails
`test_a_304_freezes_the_content_clock_and_refreshes_the_contact_clock`. The guard above
exits non-zero if the mutation did not apply, so a green run after a failed `sed` is not
possible.

- [ ] **Step 7: Prove the socket teardown is really tested**

```bash
cd firmware
cp src/services/scene_client.cpp /tmp/sc.bak
sed -i '' 's/^  s_client.stop();$/  \/\/ s_client.stop();/' src/services/scene_client.cpp
pio test -e native -f test_scene_client                        # MUST fail
cp /tmp/sc.bak src/services/scene_client.cpp
```
Expected: `test_every_failure_path_drops_the_socket` and
`test_losing_the_link_drops_the_socket` both fail.

- [ ] **Step 8: Commit**

```bash
cd /Users/matias/Documents/repos/HomeScreen
git add firmware/ && git commit -m "feat(firmware): scene client -- plain HTTP, 304, two clocks, three expiries"
```

---

## Task 4: The wire contract, generated from the server

**Files:** Create `HomeScreen/scripts/dump_wire_fixture.py`,
`HomeScreen/tests/test_wire_contract.py`, `firmware/test/fixtures_wire.h` (generated).

**Interfaces produced:** `kWireAssigned`, `kWireUnassigned`, `kWireDropped`,
`kWireFeedDown`, `kWireAssignedEtag`, `kWireAssignedPollSeconds`.

- [ ] **Step 1: Write the generator**

```python
# HomeScreen/scripts/dump_wire_fixture.py
"""Emit the firmware's wire fixture from THIS server's own routes.

The firmware parses bytes this server produces. A fixture hand-written on the
firmware side drifts the moment a field is renamed, and the failure shows up as
a blank screen on hardware rather than a red test. So it is generated, checked
in, and pinned from both sides.
"""
import argparse
import json
import pathlib
import sys
import tempfile
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from homescreen.serve import create_app          # noqa: E402

CFG = {"location": {"name": "Madrid", "timezone": "Europe/Madrid"},
       "feeds": {"adsb": {"source": "api", "endpoint": "https://x"}},
       "devices": []}
HW = "aabb00112233"
QUERY = "w=240&h=240&depth=16&max_items=64&components=radar&fw=hs-0.1"
NOW = 1_787_000_000.0
AIRCRAFT = [
    # ve/vn are deliberately NOT consistent with gs/trk: a firmware that
    # recomputes them from gs and track instead of reading them fails a test.
    {"lat": 40.5, "lon": -3.6, "nose": 90.0, "trk": 91.0, "gs": 400.0,
     "ve": 0.13, "vn": -0.17, "age": 3.1, "dst": 7.4,
     "cs": "IBE3221", "ty": "A320", "alt": "3675 ft"},
    {"lat": 40.6, "lon": -3.7, "nose": 270.0, "trk": 271.0, "gs": 250.0,
     "ve": -0.09, "vn": 0.02, "age": 0.4, "dst": 12.9,
     "cs": "RYR44BQ", "ty": "B738", "alt": "12000 ft"},
]


def _write_feed(cache_dir: pathlib.Path, ok: bool = True) -> None:
    path = cache_dir / "feed" / "adsb.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "fetched_at": datetime.fromtimestamp(NOW, timezone.utc).isoformat(),
        "ok": ok, "error": None, "data": {"aircraft": AIRCRAFT}}))


def build(out_path: pathlib.Path) -> None:
    cache_dir = pathlib.Path(tempfile.mkdtemp())
    app = create_app(CFG, cache_dir, version="fixture", clock=lambda: NOW)
    client = app.test_client()
    _write_feed(cache_dir)

    unassigned = client.get(f"/api/device/{HW}/scene?{QUERY}")
    client.patch(f"/api/devices/{HW}", json={"name": "radar", "scene": "planes"})
    assigned = client.get(f"/api/device/{HW}/scene?{QUERY}")
    dropped = client.get(
        f"/api/device/{HW}/scene?w=240&h=240&depth=16&components=text")

    _write_feed(cache_dir, ok=False)
    client.get(f"/api/device/{HW}/scene?{QUERY}")     # re-declare radar
    feed_down = client.get(f"/api/device/{HW}/scene?{QUERY}")

    def body(resp) -> str:
        return json.dumps(resp.get_json(), separators=(",", ":"),
                          ensure_ascii=False, sort_keys=True)

    lines = [
        "// GENERATED by HomeScreen/scripts/dump_wire_fixture.py -- do not edit.",
        "//",
        "// The exact bytes the server emits. Regenerate with:",
        "//   venv/bin/python scripts/dump_wire_fixture.py",
        "// tests/test_wire_contract.py fails if this drifts from the server.",
        "#pragma once",
        "",
        f"inline constexpr char kWireAssigned[] = R\"JSON({body(assigned)})JSON\";",
        f"inline constexpr char kWireUnassigned[] = R\"JSON({body(unassigned)})JSON\";",
        f"inline constexpr char kWireDropped[] = R\"JSON({body(dropped)})JSON\";",
        f"inline constexpr char kWireFeedDown[] = R\"JSON({body(feed_down)})JSON\";",
        "",
        "// The ETag is echoed verbatim in If-None-Match, quotes included.",
        f"inline constexpr char kWireAssignedEtag[] = "
        f"{json.dumps(assigned.headers['ETag'])};",
        f"inline constexpr int kWireAssignedPollSeconds = "
        f"{assigned.headers['X-Poll-Seconds']};",
        "",
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path,
                    default=ROOT / "firmware" / "test" / "fixtures_wire.h")
    args = ap.parse_args()
    build(args.out)
    print(f"wrote {args.out}")
```

**`json.dumps` on the ETag, not an f-string.** The server's ETag already contains its
HTTP quotes (`serve.py`: `etag = '"%s"' % ...`), so interpolating it raw emits
`char kWireAssignedEtag[] = ""045ba…"";` — every suite that includes the fixture fails to
compile. `json.dumps` escapes them, and the quotes must stay: the firmware echoes them
verbatim.

- [ ] **Step 2: Generate and eyeball it**

```bash
cd /Users/matias/Documents/repos/HomeScreen
venv/bin/python scripts/dump_wire_fixture.py
grep -c '"c":"radar"' firmware/test/fixtures_wire.h     # expect 2: kWireAssigned
                                                        # and kWireFeedDown.
                                                        # kWireDropped comes back
                                                        # as "components":[] with
                                                        # "unsupported":["radar"]
grep 'kWireAssignedEtag' firmware/test/fixtures_wire.h  # must be \"...\" escaped
```

- [ ] **Step 3: Pin it from the server side**

```python
# HomeScreen/tests/test_wire_contract.py
"""The firmware parses these bytes. Renaming a field here is a blank screen on
hardware, so both sides pin the same fixture."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from scripts.dump_wire_fixture import build          # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "firmware" / "test" / "fixtures_wire.h"


def test_the_checked_in_fixture_still_matches_this_server(tmp_path):
    # Generated into tmp_path, NOT over the checked-in file. Regenerating in
    # place would mean run 1 fails, leaves the new bytes on disk, and run 2
    # passes -- with the firmware still parsing the old format. A guard that
    # heals itself on the second run is not a guard.
    fresh = tmp_path / "fixtures_wire.h"
    build(fresh)
    assert fresh.read_text() == FIXTURE.read_text(), (
        "the wire format changed and firmware/ still parses the old one. "
        "Regenerate: venv/bin/python scripts/dump_wire_fixture.py -- then "
        "update firmware/src/services/scene_client.cpp and re-run "
        "`pio test -e native` BEFORE committing.")


@pytest.mark.parametrize("field", ["lat", "lon", "nose", "trk", "gs", "ve", "vn",
                                   "age", "dst", "cs", "ty", "alt"])
def test_every_item_field_the_firmware_reads_is_present(field):
    assert f'"{field}"' in FIXTURE.read_text(), \
        f"scene_client.cpp reads {field!r} out of every item"


@pytest.mark.parametrize("key", ['"c":"radar"', '"components"', '"layout"',
                                 '"assigned"', '"radius_km"', '"feed_ok"',
                                 '"feed_age_s"', '"items"', '"scene"'])
def test_every_envelope_key_the_firmware_switches_on_is_present(key):
    assert key in FIXTURE.read_text()


def test_the_etag_fixture_is_valid_cpp_and_keeps_its_quotes():
    line = [l for l in FIXTURE.read_text().splitlines()
            if "kWireAssignedEtag" in l][0]
    assert '\\"' in line, "the HTTP quotes must be escaped, not doubled"
    assert not line.count('""'), "an unescaped ETag does not compile"


def test_the_fixture_covers_a_feed_the_server_knows_is_dead():
    # The firmware expires its picture immediately on this, so it has to be a
    # real server response and not a hand-written guess.
    text = FIXTURE.read_text()
    assert "kWireFeedDown" in text
    assert '"feed_ok":false' in text
```

- [ ] **Step 4: Run it, then break it deliberately**

```bash
cd /Users/matias/Documents/repos/HomeScreen
venv/bin/pytest tests/test_wire_contract.py -q       # expect green
# Prove the guard bites: rename a field the firmware reads.
python3 - <<'PY'
p='homescreen/scenes/planes.py'; s=open(p).read()
open(p+'.bak','w').write(s)
open(p,'w').write(s.replace('"radius_km": radius_km', '"range_km": radius_km'))
PY
venv/bin/pytest tests/test_wire_contract.py -q       # MUST fail
mv homescreen/scenes/planes.py.bak homescreen/scenes/planes.py
venv/bin/pytest tests/test_wire_contract.py -q       # green again
git status --porcelain                               # fixture must be UNCHANGED
```
Expected: the middle run fails; `git status` shows no modification to
`firmware/test/fixtures_wire.h`. If the fixture changed, the test regenerated in place
and the guard is worthless.

- [ ] **Step 5: Commit**

```bash
git add scripts/dump_wire_fixture.py tests/test_wire_contract.py \
        firmware/test/fixtures_wire.h
git commit -m "test: generate the firmware wire fixture from the server, and pin it"
```

---

## Task 5: Port the renderer, the geometry, and the runway overlay

Ported **together** because `test_display` includes all of them; splitting the airport
data into a later task leaves this one unable to compile.

**Files:** copy 8 headers, 6 sources and 5 test suites out of the reference; apply a
6-line substitution to `radar_display.cpp` only.

- [ ] **Step 1: Copy everything the renderer needs, in one go**

```bash
cd /Users/matias/Documents/repos/HomeScreen
REF=/Users/matias/Documents/repos/ESP32-Plane-Radar
mkdir -p firmware/include/{ui,hardware,services,data} firmware/src/{ui,hardware,services,data}
for f in ui/radar_display.h ui/radar_geo.h ui/radar_range.h ui/radar_theme.h \
         ui/render_policy.h ui/runway_overlay.h \
         hardware/display.h hardware/display_font.h hardware/lgfx_config.hpp \
         services/radar_location.h data/large_airports.h; do
  cp "$REF/include/$f" "firmware/include/$f"
done
for f in ui/radar_display.cpp ui/radar_geo.cpp ui/radar_range.cpp \
         ui/runway_overlay.cpp hardware/display.cpp hardware/display_font.cpp \
         services/radar_location.cpp data/large_airports_data.cpp; do
  cp "$REF/src/$f" "firmware/src/$f"
done
cp $REF/test/fixtures_geo.h firmware/test/
cp -R $REF/test/test_geo $REF/test/test_render_policy $REF/test/test_settings \
      $REF/test/test_runway_cap $REF/test/test_debug_log firmware/test/
test "$(git -C $REF status --porcelain | wc -l | tr -d ' ')" = "0" || \
  { echo "REFERENCE REPO MODIFIED -- STOP"; exit 1; }
```

`include/hardware/display_font.h` and `include/data/large_airports.h` are easy to miss:
the native build masks the first with `test/mocks/hardware/display_font.h`, so its
absence only detonates at the first `pio run -e c3`, several tasks later.

- [ ] **Step 2: Repoint the renderer at the scene client**

Only `radar_display.cpp`, and only these six substitutions. Order matters: the `Raw`
form must be rewritten before the shorter one.

```bash
cd firmware
sed -i '' \
  -e 's|#include "services/adsb_client.h"|#include "services/scene_client.h"|' \
  -e 's|secondsSinceUpdateRaw()|secondsSinceContentRaw()|g' \
  -e 's|secondsSinceUpdate()|secondsSinceContent()|g' \
  -e 's|dataExpired()|contentExpired()|g' \
  -e 's|services::adsb::|services::scene::|g' \
  src/ui/radar_display.cpp
test -z "$(grep -n adsb src/ui/radar_display.cpp)" || \
  { echo "adsb references remain"; grep -n adsb src/ui/radar_display.cpp; exit 1; }
```

Verified against the reference: the file's only `adsb` references are the include, six
`services::adsb::` qualifications (`Aircraft`, `kMaxAircraft`,
`kExtrapolationHorizonSec`), four lock/list calls, and the three clock calls — all
present in `scene_client.h` under the same names. The only `config::` constants it uses
are `kDisplayRgbOrder` and `kDebugFrameReportMs`, both in the new `config.h`.

- [ ] **Step 3: Point the copied suites at the new sources**

`test_geo`, `test_render_policy`, `test_settings`, `test_runway_cap` and `test_debug_log`
contain **no** `adsb` references — verified. They compile as copied. Run them:

```bash
export PATH="$HOME/.platformio/penv/bin:$PATH"
cd firmware && pio test -e native
```
Expected: everything from Tasks 0–4 plus `test_geo`, `test_render_policy`,
`test_settings`, `test_runway_cap` pass. `test_display` is **not** copied yet — it is
Task 6, because it needs a rewrite rather than a copy.

- [ ] **Step 4: Add the third staleness cause the firmware requirements ask for**

`PLAN.md` §3: *"Note `radar_display.cpp` tests the two staleness causes separately and
deliberately — the comment there records that summing them made targets blink once per
cycle. Preserve that structure and add `feed.age_s` as a THIRD separately-tested cause.
Do not merge them."* Revision 2 declared `feedAgeS()` and consumed it nowhere.

At `radar_display.cpp:532`, the existing test is two `||` terms. Add the third:

```cpp
    const bool stale = planes[i].pos_age_s >= services::scene::kExtrapolationHorizonSec ||
                       fetch_age_raw >= services::scene::kExtrapolationHorizonSec ||
                       services::scene::feedAgeS() >= services::scene::kExtrapolationHorizonSec;
```

Separately tested, not summed: a device receiving fresh scenes from a server whose feed
has stalled has a healthy content clock and a healthy contact clock, and only this term
can dim its targets.

Add to `test_display.cpp`, with a `RUN_TEST` line:

```cpp
void test_a_stalled_server_feed_dims_targets_even_when_scenes_are_fresh(void) {
  Target t[1] = {{5.0f, 0.0f, 400.0f, 90.0f, 0.0f, "STALE"}};
  publishTargets(t, 1);                       // feed_age_s 1.0 in the helper
  g_gfx.reset();
  ui::radarDisplayRefreshAircraft();
  const uint16_t fresh = g_gfx.of(DrawOp::Triangle).back().color;

  const std::string p = payloadFor(t, 1);
  std::string stalled = p;
  const std::string from = "\"feed_age_s\":1.0";
  stalled.replace(stalled.find(from), from.size(), "\"feed_age_s\":30.0");
  g_http.reset(); g_http.body = stalled; g_http.code = HTTP_CODE_OK;
  TEST_ASSERT_TRUE(services::scene::pollOnce());
  g_gfx.reset();
  ui::radarDisplayRefreshAircraft();
  TEST_ASSERT_NOT_EQUAL_MESSAGE(fresh, g_gfx.of(DrawOp::Triangle).back().color,
                                "a stalled feed must dim the targets");
}
```

- [ ] **Step 5: Note what still calls `fetchRadiusKm()`**

`runway_overlay.cpp` calls `radar::fetchRadiusKm()` to bound which airports it draws, so
`radar_range.cpp` must keep the function even though it no longer sizes any request. Add
the note so a later cleanup does not delete it:

```bash
cd firmware
python3 - <<'PY'
p='include/ui/radar_range.h'; s=open(p).read()
s = s.replace('float fetchRadiusKm();', '''/**
 * No longer sizes any request -- the server decides the feed radius now. Still
 * live: runway_overlay.cpp bounds which airports it draws by this. Do not
 * delete it on the grounds that the fetch is gone.
 */
float fetchRadiusKm();''')
open(p,'w').write(s)
PY
```

- [ ] **Step 6: Build for the target and commit**

```bash
cd firmware && pio run -e c3
```
Expected: builds. The image should be **smaller** than the reference's 1,247,850 B —
mbedTLS is gone and the airport table is the same size. Record the figure.

```bash
cd /Users/matias/Documents/repos/HomeScreen
git add firmware/ && git commit -m "feat(firmware): port the renderer, geometry and runway overlay"
```

---

## Task 6: Rewrite `test_display` for the scene wire format

Its own task. Revision 1 called this "apply the same substitutions" — a 5-minute `sed`.
It is a rewrite of ~1,300 lines, and doing it carelessly produces a suite that stays
**green while testing nothing**: the payload helper emits adsb.fi shapes, so after a
naive port every aircraft parses with `ve = vn = 0` and every dead-reckoning assertion
passes against a stationary target.

**Files:** Copy `firmware/test/test_display/`, then rewrite its helpers.

- [ ] **Step 1: Copy it**

```bash
cd /Users/matias/Documents/repos/HomeScreen
REF=/Users/matias/Documents/repos/ESP32-Plane-Radar
cp -R $REF/test/test_display firmware/test/
test "$(git -C $REF status --porcelain | wc -l | tr -d ' ')" = "0" || exit 1
```

- [ ] **Step 2: Replace the includes**

```bash
cd firmware
sed -i '' \
  -e 's|#include "../../src/services/adsb_client.cpp"|#include "../../src/services/device_id.cpp"\n#include "../../src/services/server_config.cpp"\n#include "../../src/services/scene_client.cpp"|' \
  -e 's|services::adsb::|services::scene::|g' \
  -e 's|kDataExpirySec|kContactExpirySec|g' \
  test/test_display/test_display.cpp
```

`kDataExpirySec` is used at `test_display.cpp:889` and has no counterpart in
`scene_client.h` -- the substitution above maps it onto `kContactExpirySec`, which is the
bound it was testing.

- [ ] **Step 3: Rewrite `payloadFor()` to emit a scene**

Replace the existing helper. `ve`/`vn` are computed **in the test helper** from `gs` and
`track`, because the geometry cases were written against those values and must keep
their meaning — the server does this same arithmetic in `adsb_map.py`.

```cpp
// firmware/test/test_display/test_display.cpp  -- replaces payloadFor() only
static constexpr float kKnotsToKmPerSec = 1.852f / 3600.0f;
static constexpr float kDegToRad = 0.01745329252f;

/** A scene envelope carrying these targets, shaped exactly like the server's. */
static std::string payloadFor(const Target* t, int n) {
  // KEEP the projection. All ~54 call sites specify targets as offsets in km
  // from the radar centre; without this they land at lat = east_km, lon =
  // north_km -- off the dial -- and every geometry assertion in this file
  // quietly changes meaning.
  const double cos_lat = cos(kLat * M_PI / 180.0);
  std::string s =
      "{\"assigned\":true,\"layout\":\"fill\",\"scene\":\"planes\","
      "\"components\":[{\"c\":\"radar\",\"feed_ok\":true,"
      "\"feed_age_s\":1.0,\"radius_km\":60.0,\"items\":[";
  for (int i = 0; i < n; ++i) {
    const double lat = kLat + t[i].north_km / 111.0;
    const double lon = kLon + t[i].east_km / (111.0 * cos_lat);
    // The server resolves track+gs into east/north km/s once per fetch; the
    // firmware reads ve/vn and never recomputes. Do the same arithmetic here or
    // every extrapolation test silently runs against a stationary target.
    const float gs_km_s = t[i].gs_kt * kKnotsToKmPerSec;
    const float trk_rad = t[i].track_deg * kDegToRad;
    char b[400];
    snprintf(b, sizeof(b),
             "%s{\"lat\":%.6f,\"lon\":%.6f,\"nose\":%.1f,\"trk\":%.1f,"
             "\"gs\":%.1f,\"ve\":%.6f,\"vn\":%.6f,\"age\":%.2f,"
             "\"dst\":-1.0,\"cs\":\"%s\",\"ty\":\"B738\","
             "\"alt\":\"3675 ft\"}",
             i ? "," : "", lat, lon, t[i].track_deg, t[i].track_deg,
             t[i].gs_kt, gs_km_s * sinf(trk_rad), gs_km_s * cosf(trk_rad),
             t[i].seen_pos, t[i].callsign);
    s += b;
  }
  return s + "]}]}";
}
```

The real struct is `struct Target { float east_km, north_km, gs_kt, track_deg,
seen_pos; const char* callsign; }` — six members, offsets in km, and no `heading` field
(the reference sends `track_deg` for both `track` and `true_heading`). Do not invent
`lat`/`lon`/`gs`/`flight` members; the aggregate initialisers at every call site are
positional and would silently mean something else.

- [ ] **Step 4: Rewrite `publishTargets()` to drive the scene client**

```cpp
static void publishTargets(const Target* t, int n) {
  const std::string payload = payloadFor(t, n);
  g_http.reset();
  g_http.body = payload;
  g_http.code = HTTP_CODE_OK;
  g_http.response_headers["ETag"] = "\"t\"";
  g_http.response_headers["X-Poll-Seconds"] = "5";
  TEST_ASSERT_TRUE(services::scene::pollOnce());
}
```

Then replace every remaining `services::scene::fetchUpdate(...)` call — there is no such
function — and both `services::scene::startFetchTask()` calls (lines ~655 and ~1143 in
the copied file) with `services::scene::startPollTask()`. Find them with:

```bash
grep -n "fetchUpdate\|startFetchTask" test/test_display/test_display.cpp
```
Every hit must be gone before Step 6.

- [ ] **Step 5: Add the test that would have caught a stationary port**

```cpp
void test_a_moving_target_actually_moves_between_frames(void) {
  // The port's failure mode: after a naive rename, every item parses with
  // ve = vn = 0 and every dead-reckoning assertion in this file passes against
  // a stationary aeroplane. This is the test that fails when that happens.
  // east_km, north_km, gs_kt, track_deg, seen_pos, callsign -- six, in km.
  Target t[1] = {{5.0f, 0.0f, 400.0f, 90.0f, 0.0f, "MOVER"}};
  publishTargets(t, 1);
  TEST_ASSERT_TRUE(services::scene::aircraftList()[0].vel_e_km_s > 0.15f);

  g_gfx.reset();
  ui::radarDisplayRefreshAircraft();
  const int x_first = g_gfx.lastX(DrawOp::Triangle);   // the aircraft glyph
  TEST_ASSERT_NOT_EQUAL_MESSAGE(-1, x_first, "nothing drew the target at all");

  mockAdvanceMs(6000);
  g_gfx.reset();
  ui::radarDisplayRefreshAircraft();
  TEST_ASSERT_NOT_EQUAL_MESSAGE(x_first, g_gfx.lastX(DrawOp::Triangle),
                                "the target did not dead-reckon");
}
```

The glyph is a `Triangle` in the recorder's vocabulary — confirm with
`grep -n "fillTriangle" src/ui/radar_display.cpp` before relying on it; if the symbol is
drawn with a different primitive, assert on that kind instead. The property being tested
is that the drawn position changes with time, not which primitive draws it.

- [ ] **Step 6: Reset the scene client between tests**

`test_display`'s `setUp()` resets the gfx recorder, the clock and the fonts, but the
scene client is new to this suite and carries file-static state — both clocks, the
buffers, the etag. Combined with a `mockSetMs()` that moves time *backwards* after a test
that advanced it, `secondsSince()` computes an unsigned underflow. Add to `setUp()`:

```cpp
  services::scene::resetForTest();
```

- [ ] **Step 7: Register every appended test**

Unity only runs what the runner lists. Appended `void test_...` functions with no
`RUN_TEST` line compile and are silently skipped — and the step still reports "all pass".

```bash
cd firmware
# Every test function must have a RUN_TEST. The copied suites declare tests as
# `static void test_x(void)`, so a pattern anchored on a bare `void` matches
# NOTHING and the check can never fail for the reason it exists.
check_runners() {
  diff <(grep -oE '^(static )?void +test_[a-z0-9_]+ *\(' "$1" \
         | sed -E 's/^(static )?void +//; s/ *\($//' | sort) \
       <(grep -oE 'RUN_TEST\( *test_[a-z0-9_]+ *\)' "$1" \
         | sed -E 's/RUN_TEST\( *//; s/ *\)//' | sort)
}
check_runners test/test_display/test_display.cpp
```
Expected: no output. A line prefixed `<` is a test that compiles and never runs; `>` is a
`RUN_TEST` for a function that no longer exists. Verify the check itself works by
deleting one `RUN_TEST` line and re-running before you trust a clean result.

- [ ] **Step 8: Run green and commit**

```bash
export PATH="$HOME/.platformio/penv/bin:$PATH"
cd firmware && pio test -e native
git add firmware/ && git commit -m "test(firmware): rewrite test_display for the scene wire format"
```

---

## Task 7: Status screens and the component dispatcher

Ported **together** because the dispatcher draws them. Revision 1 split them and left the
"SIN SERVIDOR" screen written by nobody while a test asserted on it.

**Files:** copy `status_screens.{h,cpp}`, add one screen, create `ui/components.{h,cpp}`
and `test/test_components/`.

**Interfaces:**
- Consumes: `services::scene::*`, `services::deviceId()`, `ui::radarDisplayRefreshAircraft()`.
- Produces: `ui::ComponentKind`, `ui::componentKindFromName()`, `ui::kDeclaredComponents`,
  `ui::renderScene()`, `statusScreenNoServer()`, `statusScreenUnassigned()`.

- [ ] **Step 1: Copy the status screens**

```bash
cd /Users/matias/Documents/repos/HomeScreen
REF=/Users/matias/Documents/repos/ESP32-Plane-Radar
cp $REF/include/ui/status_screens.h firmware/include/ui/
cp $REF/src/ui/status_screens.cpp firmware/src/ui/
test "$(git -C $REF status --porcelain | wc -l | tr -d ' ')" = "0" || exit 1
```

- [ ] **Step 2: Add the two screens this firmware needs**

Append to `firmware/include/ui/status_screens.h`, inside its existing declarations:

```cpp
/**
 * Never reached the server since boot. A blank round panel is indistinguishable
 * from a dead one, so say which it is -- and show the address we are trying,
 * because "wrong server in the portal" is the likeliest cause.
 */
void statusScreenNoServer(const char* base_url);

/**
 * Registered but no scene assigned. Shows the hardware id, because that is the
 * string the operator types into the fleet view (spec section 6.1), plus
 * whatever the server said to do about it.
 */
void statusScreenUnassigned(const char* hw_id, const char* message);
```

And implement them in `firmware/src/ui/status_screens.cpp` following the file's existing
screens exactly — same `tft.fillScreen`, `setTextDatum`, `drawString` idiom, and the same
black/`kTextOnBlack` palette the connecting screen uses (the portal screen is the yellow
one; these are not it).

**Both must fit the glass.** The server's message is
`"sin asignar · elige una escena en el panel"` — 42 characters, ~290 px at the mock's
metrics, on a 240 px round panel where the usable chord at that height is narrower still.
The file already solves this: `kConnectingTextMaxWidthPx = 220` and the truncate-with-`…`
loop at `status_screens.cpp:109-125`. Reuse it rather than drawing the string raw. Factor
that loop into a small `fitToWidth(const char* in, char* out, size_t n, int max_px)` and
call it from both new screens and from the existing SSID path, so there is one
implementation:

```cpp
void statusScreenNoServer(const char* base_url) {
  tft.fillScreen(config::kColorBlack);
  tft.setTextColor(config::kTextOnBlack, config::kColorBlack);
  tft.setTextDatum(middle_center);
  tft.drawString("SIN SERVIDOR", config::kDisplayWidth / 2,
                 config::kDisplayHeight / 2 - 24);
  char fitted[48];
  fitToWidth(base_url == nullptr ? "" : base_url, fitted, sizeof(fitted),
             kConnectingTextMaxWidthPx);
  tft.drawString(fitted, config::kDisplayWidth / 2,
                 config::kDisplayHeight / 2 + 4);
  tft.drawString("revisa el portal", config::kDisplayWidth / 2,
                 config::kDisplayHeight / 2 + 30);
}

void statusScreenUnassigned(const char* hw_id, const char* message) {
  tft.fillScreen(config::kColorBlack);
  tft.setTextColor(config::kTextOnBlack, config::kColorBlack);
  tft.setTextDatum(middle_center);
  tft.drawString("SIN ASIGNAR", config::kDisplayWidth / 2,
                 config::kDisplayHeight / 2 - 30);
  tft.drawString(hw_id == nullptr ? "" : hw_id, config::kDisplayWidth / 2,
                 config::kDisplayHeight / 2);
  // The server's own words, if it sent any -- it knows what the fleet view
  // says. Fitted: the real message is 42 characters and the panel is 240 px.
  if (message != nullptr && message[0] != '\0') {
    char fitted[48];
    fitToWidth(message, fitted, sizeof(fitted), kConnectingTextMaxWidthPx);
    tft.drawString(fitted, config::kDisplayWidth / 2,
                   config::kDisplayHeight / 2 + 30);
  }
}
```

- [ ] **Step 3: Write the dispatcher test**

```cpp
// firmware/test/test_components/test_components.cpp
#include <Arduino.h>
#include <unity.h>
#include <cstring>

#include "fixtures_wire.h"
#include "../mocks/mock_globals.h"
#include "../../src/services/device_id.cpp"
#include "../../src/services/server_config.cpp"
#include "../../src/services/scene_client.cpp"
#include "../../src/services/radar_location.cpp"
#include "../../src/ui/radar_range.cpp"
#include "../../src/ui/radar_geo.cpp"
#include "../../src/ui/runway_overlay.cpp"
#include "../../src/data/large_airports_data.cpp"
// display.cpp is the ONLY definition of `LGFX tft` -- display.h declares it
// extern, and both radar_display.cpp and status_screens.cpp use it. Every other
// drawing suite includes it for the same reason.
#include "../../src/hardware/display.cpp"
#include "../../src/ui/radar_display.cpp"
#include "../../src/ui/status_screens.cpp"
#include "../../src/ui/components.cpp"

static bool poll(const char* body, int code = HTTP_CODE_OK) {
  g_http.reset();
  g_http.body = body;
  g_http.code = code;
  g_http.response_headers["ETag"] = "\"t\"";
  g_http.response_headers["X-Poll-Seconds"] = "5";
  return services::scene::pollOnce();
}

void setUp(void) {
  g_nvs.reset(); mockSetMs(100000); g_gfx.reset();
  WiFi.status_ = WL_CONNECTED;
  services::server::saveFromString("192.168.1.116:8080");
  services::scene::resetForTest();
  services::location::clear();
  ui::radar::rangeInit();
}
void tearDown(void) {}

void test_the_declared_list_matches_what_we_can_actually_draw(void) {
  // The server DROPS components we did not declare and reports the omission on
  // /home. Declaring something we cannot draw puts a hole on the glass and
  // nothing in the fleet view -- the silent failure spec 5.5 exists to prevent.
  TEST_ASSERT_EQUAL_STRING("radar", ui::kDeclaredComponents);
  TEST_ASSERT_EQUAL(ui::ComponentKind::kRadar,
                    ui::componentKindFromName("radar"));
  TEST_ASSERT_EQUAL(ui::ComponentKind::kUnknown,
                    ui::componentKindFromName("text"));
  TEST_ASSERT_EQUAL(ui::ComponentKind::kNone, ui::componentKindFromName(""));
  TEST_ASSERT_EQUAL(ui::ComponentKind::kNone,
                    ui::componentKindFromName(nullptr));
}

void test_the_declared_list_is_what_the_client_actually_sends(void) {
  // Two strings that must never disagree: one is compiled into the URL, the
  // other into the dispatcher.
  poll(kWireAssigned);
  TEST_ASSERT_NOT_EQUAL(std::string::npos,
                        g_http.last_url.find(std::string("components=") +
                                             ui::kDeclaredComponents));
}

void test_an_assigned_radar_scene_draws_the_radar(void) {
  poll(kWireAssigned);
  TEST_ASSERT_TRUE(ui::renderScene());
  TEST_ASSERT_TRUE(g_gfx.count(DrawOp::Circle) > 0);
  TEST_ASSERT_FALSE(g_gfx.textContains("SIN"));
}

void test_a_device_that_never_reached_the_server_says_so_with_the_address(void) {
  // Before any reply. A blank round screen looks exactly like a dead one, and
  // the likeliest cause is the wrong address in the portal -- so show it.
  TEST_ASSERT_TRUE(ui::renderScene());
  TEST_ASSERT_TRUE(g_gfx.textContains("SIN SERVIDOR"));
  TEST_ASSERT_TRUE(g_gfx.textContains("192.168.1.116"));
}

void test_an_unassigned_device_shows_its_id_and_the_servers_message(void) {
  // Spec 6.1: a newly flashed board tells you what to type into the fleet view.
  poll(kWireUnassigned);
  TEST_ASSERT_TRUE(ui::renderScene());
  TEST_ASSERT_TRUE(g_gfx.textContains("SIN ASIGNAR"));
  TEST_ASSERT_TRUE(g_gfx.textContains("aabb00112233"));
  TEST_ASSERT_TRUE(g_gfx.textContains("sin asignar"));   // the server's words
}

void test_nothing_drawn_on_a_status_screen_runs_off_the_glass(void) {
  // The server's real message is 42 characters, ~290 px at the mock's metrics,
  // on a 240 px round panel. Every drawn string must be fitted.
  poll(kWireUnassigned);
  ui::renderScene();
  for (const auto& op : g_gfx.ops) {
    if (op.kind != DrawOp::Text) continue;
    TEST_ASSERT_LESS_OR_EQUAL_MESSAGE(
        220, static_cast<int>(op.text.size()) * g_gfx.char_width,
        op.text.c_str());
  }
}

void test_an_expired_picture_is_dropped_rather_than_shown_as_live(void) {
  poll(kWireAssigned);
  mockAdvanceMs(61000);                  // past the contact bound
  g_gfx.reset();
  ui::renderScene();
  TEST_ASSERT_FALSE(g_gfx.textContains("IBE3221"));
}

void test_a_feed_that_stays_dead_drops_the_picture(void) {
  // The server keeps answering; only feed_age_s grows. One poll of feed_ok
  // false must NOT blank it -- that is a transient -- but a minute must.
  poll(kWireAssigned);
  poll(kWireFeedDown);
  g_gfx.reset();
  ui::renderScene();
  TEST_ASSERT_TRUE(g_gfx.textContains("IBE3221"));   // still there: transient
  for (int i = 0; i < 12; ++i) { mockAdvanceMs(10000); poll(kWireFeedDown); }
  g_gfx.reset();
  ui::renderScene();
  TEST_ASSERT_FALSE(g_gfx.textContains("IBE3221"));
}

void test_a_scene_whose_component_we_cannot_draw_does_not_blank_the_screen(void) {
  // The server should never send one -- it drops undeclared components -- but a
  // hole on the glass is the worst possible response to a server that does.
  poll("{\"assigned\":true,\"layout\":\"fill\",\"scene\":\"ticker\","
       "\"components\":[{\"c\":\"text\",\"slot\":\"center\",\"text\":\"x\"}]}");
  TEST_ASSERT_TRUE(ui::renderScene());
  TEST_ASSERT_TRUE(g_gfx.textContains("SIN"));
}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_the_declared_list_matches_what_we_can_actually_draw);
  RUN_TEST(test_the_declared_list_is_what_the_client_actually_sends);
  RUN_TEST(test_an_assigned_radar_scene_draws_the_radar);
  RUN_TEST(test_a_device_that_never_reached_the_server_says_so_with_the_address);
  RUN_TEST(test_an_unassigned_device_shows_its_id_and_the_servers_message);
  RUN_TEST(test_nothing_drawn_on_a_status_screen_runs_off_the_glass);
  RUN_TEST(test_an_expired_picture_is_dropped_rather_than_shown_as_live);
  RUN_TEST(test_a_feed_that_stays_dead_drops_the_picture);
  RUN_TEST(test_a_scene_whose_component_we_cannot_draw_does_not_blank_the_screen);
  return UNITY_END();
}
```

- [ ] **Step 4: Run to see it fail, then implement the dispatcher**

```cpp
// firmware/include/ui/components.h
#pragma once

namespace ui {

/**
 * What this firmware can draw. `kDeclaredComponents` is sent to the server on
 * every poll as `components=`; the server drops anything a scene asks for that
 * is not in this list and records the omission in the fleet view (spec 5.5).
 * This string and the switch in renderScene() must never disagree: declaring
 * more than we draw puts a hole on the glass and nothing in the fleet view.
 */
enum class ComponentKind { kNone, kRadar, kUnknown };

inline constexpr char kDeclaredComponents[] = "radar";

ComponentKind componentKindFromName(const char* c);

/**
 * Draw whatever the scene client currently holds. False if the frame could not
 * be composed (the aircraft list was locked); the caller retries rather than
 * latching, or a skipped clearing frame strands the last targets on the panel.
 */
bool renderScene();

}  // namespace ui
```

```cpp
// firmware/src/ui/components.cpp
#include "ui/components.h"

#include <cstring>

#include "config.h"
#include "services/device_id.h"
#include "services/scene_client.h"
#include "services/server_config.h"
#include "ui/radar_display.h"
#include "ui/status_screens.h"

namespace ui {

ComponentKind componentKindFromName(const char* c) {
  if (c == nullptr || c[0] == '\0') {
    return ComponentKind::kNone;
  }
  if (strcmp(c, "radar") == 0) {
    return ComponentKind::kRadar;
  }
  return ComponentKind::kUnknown;
}

bool renderScene() {
  namespace scene = services::scene;

  // Order matters: each of these is a different thing to tell a human, and the
  // wrong order tells them the least useful one.
  if (!scene::everReceived()) {
    statusScreenNoServer(services::server::baseUrl());
    return true;
  }
  if (!scene::assigned()) {
    statusScreenUnassigned(services::deviceId(), scene::message());
    return true;
  }
  switch (componentKindFromName(scene::componentName())) {
    case ComponentKind::kRadar:
      // radarDisplayRefreshAircraft() handles contentExpired() itself: it draws
      // the rings and drops the targets, which is a better answer than a blank
      // screen -- the panel still shows it is alive and oriented.
      return radarDisplayRefreshAircraft();
    case ComponentKind::kNone:
    case ComponentKind::kUnknown:
    default:
      // The server should never send this: it drops components we did not
      // declare. If it does, say so rather than leaving a hole.
      statusScreenUnassigned(services::deviceId(),
                             "escena no soportada");
      return true;
  }
}

}  // namespace ui
```

- [ ] **Step 5: Run green, verify the dispatcher is really reached**

```bash
export PATH="$HOME/.platformio/penv/bin:$PATH"
cd firmware && pio test -e native -f test_components
cp src/ui/components.cpp /tmp/comp.bak
sed -i '' 's|return radarDisplayRefreshAircraft();|return true;|' src/ui/components.cpp
pio test -e native -f test_components         # MUST fail
cp /tmp/comp.bak src/ui/components.cpp
```
Expected: 8 pass, then `test_an_assigned_radar_scene_draws_the_radar` fails under the
mutation.

- [ ] **Step 6: Commit**

```bash
git add firmware/ && git commit -m "feat(firmware): status screens and the component dispatcher"
```

---

## Task 8: Provisioning and the real loop

**Files:** copy `wifi_setup.{h,cpp}`, add one portal field, write `main.cpp`, adapt
`test_main`.

- [ ] **Step 1: Copy provisioning**

```bash
cd /Users/matias/Documents/repos/HomeScreen
REF=/Users/matias/Documents/repos/ESP32-Plane-Radar
cp $REF/include/services/wifi_setup.h firmware/include/services/
cp $REF/src/services/wifi_setup.cpp firmware/src/services/
cp -R $REF/test/test_wifi firmware/test/
test "$(git -C $REF status --porcelain | wc -l | tr -d ' ')" = "0" || exit 1
# The brownout guard rides on a file we are about to hand-edit. OPS.md section 7:
# the Super Mini's regulator is marginal at full TX power.
test "$(grep -c WIFI_POWER_8_5dBm firmware/src/services/wifi_setup.cpp)" = "2" || \
  { echo "TX power cap missing -- do not flash this"; exit 1; }
```

- [ ] **Step 2: Add the server field and a save hook the tests can call**

In `firmware/src/services/wifi_setup.cpp`, alongside `s_param_lat`:

```cpp
WiFiManagerParameter s_param_server(
    "server", "HomeScreen server (host or host:port)", "", 64,
    " placeholder=\"192.168.1.116:8080\"");
```

Register it **first** in `attachPortalParams()` (`wifi_setup.cpp:122` — not
"setupParameters", which does not exist) — it is the field a new board most needs:

```cpp
  wm.addParameter(&s_param_server);
  wm.addParameter(&s_param_lat);
  ...
```

`wifi_setup.cpp` must also gain `#include "services/server_config.h"`; nothing else in it
includes that header. Then, in the existing named save callback `onPortalParamsSaved()`
(`wifi_setup.cpp:103` — a file-static function, not a lambda), before the location
fields:

```cpp
  // A refused value leaves the stored one alone: a typo in the portal must not
  // strand a working device with no server.
  if (!services::server::saveFromString(s_param_server.getValue())) {
    Serial.println("wifi: server address rejected; keeping the previous one");
  }
```

`test_wifi.cpp` reaches the callback through the mock's existing
`fireSaveParamsCallback()`, which the reference suite already uses -- no `ForTest` symbol
is needed or wanted. The suite must also gain
`#include "../../src/services/server_config.cpp"`, or the three appended tests fail to
link on `services::server::host()`.

- [ ] **Step 3: Append the portal tests, and register them**

```cpp
// append to firmware/test/test_wifi/test_wifi.cpp
void test_the_portal_offers_a_server_address_field(void) {
  // How a freshly flashed board learns where the Pi is. Without it, pointing a
  // device at a server means a recompile -- the thing this phase exists to stop.
  wifiSetupConnect();
  TEST_ASSERT_TRUE(wmHasParameter("server"));
}

void test_a_saved_server_address_is_persisted_and_applied(void) {
  wifiSetupConnect();
  wmSetParameterValue("server", "192.168.1.116:8080");
  g_wm.fireSaveParamsCallback();
  TEST_ASSERT_EQUAL_STRING("192.168.1.116", services::server::host());
  TEST_ASSERT_EQUAL_UINT16(8080, services::server::port());
}

void test_a_rejected_server_address_does_not_wipe_the_working_one(void) {
  services::server::saveFromString("192.168.1.116");
  wifiSetupConnect();
  wmSetParameterValue("server", "https://nope");
  g_wm.fireSaveParamsCallback();
  TEST_ASSERT_EQUAL_STRING("192.168.1.116", services::server::host());
}
```

Add the three `RUN_TEST` lines, then `check_runners test/test_wifi/test_wifi.cpp`.

- [ ] **Step 4: Write `main.cpp`**

```cpp
/**
 * HomeScreen display client — server-driven round display.
 *
 * The loop is the reference firmware's, with three changes: the server address
 * is loaded before Wi-Fi, the poll task replaces the ADS-B fetch task, and every
 * frame goes through ui::renderScene() rather than straight to the radar.
 * Everything else -- the frame-reserve ordering, the render policy, the BOOT
 * button, the reconnect grace -- is unchanged, and the comments explaining why
 * are unchanged with it.
 */
#include <Arduino.h>
#include <WiFi.h>

#include "config.h"
#include "debug_log.h"
#include "hardware/display.h"
#include "services/radar_location.h"
#include "services/scene_client.h"
#include "services/server_config.h"
#include "services/wifi_setup.h"
#include "ui/components.h"
#include "ui/radar_display.h"
#include "ui/radar_range.h"
#include "ui/render_policy.h"
#include "ui/status_screens.h"

namespace {

bool g_scene_visible = false;
unsigned long g_wifi_down_since = 0;
unsigned long g_last_reconnect_ms = 0;
unsigned long g_last_render_ms = 0;
ui::RenderPolicy g_render;
bool g_poll_task_ok = false;
unsigned long g_last_task_retry_ms = 0;

void showSceneIfConnected() {
  if (WiFi.status() != WL_CONNECTED) {
    g_scene_visible = false;
    return;
  }
  // Sample before the blit: pushSprite takes ~11.5 ms, and a publish landing in
  // that window would latch "no traffic" for a frame that is showing some.
  const bool traffic = services::scene::hasTraffic();
  const bool blitted = ui::renderScene();
  g_render.onFrameDrawn(traffic, blitted);
  if (!blitted) {
    return;  // loop() retries; never latch over a status screen
  }
  g_scene_visible = true;
}

void onRangeTap() {
  ui::radar::rangeNext();
  char range_label[12];
  ui::radar::formatCurrentRing3Label(range_label, sizeof(range_label));
  Serial.printf("Range: %s (outer ~%.0f km)\n", range_label,
                ui::radar::rangeCurrent().outer_km);
  if (g_scene_visible && WiFi.status() == WL_CONNECTED) {
    const bool traffic = services::scene::hasTraffic();
    g_render.onFrameDrawn(traffic, ui::renderScene());
  }
}

void handleBootButton() {
  bootButtonPollLongPress();
  if (bootButtonConsumeTap()) {
    onRangeTap();
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println();
  Serial.printf("HomeScreen display client %s\n", config::kFirmwareVersion);

  DEBUG_LOG_HEAP("at boot");
  bootButtonInit();
  displayInit();
  DEBUG_LOG_HEAP("after display");
  // Before WiFi: the frame buffer needs 115 KB contiguous, and the network
  // stack leaves a much smaller largest free block. Claimed here it always
  // succeeds; claimed later it may never succeed again.
  if (!ui::radarDisplayReserveFrame()) {
    Serial.println("display: frame buffer unavailable — falling back to direct draw");
  }
  DEBUG_LOG_HEAP("after sprite");

  services::server::load();
  if (wifiShowsSetupScreenOnBoot()) {
    statusScreenPortal();
  }
  services::location::init();
  ui::radar::rangeInit();

  if (wifiSetupConnect()) {
    showSceneIfConnected();
  }

  g_poll_task_ok = services::scene::startPollTask();
  DEBUG_LOG("setup: poll task %s", g_poll_task_ok ? "started" : "FAILED");
  DEBUG_LOG_HEAP("after setup");
}

void loop() {
  handleBootButton();
  wifiLoop();

  if (WiFi.status() != WL_CONNECTED) {
    if (g_scene_visible) {
      Serial.println("WiFi lost — will reconnect");
      DEBUG_LOG_HEAP("on wifi loss");
      g_scene_visible = false;
      g_render.reset();
    }
    if (g_wifi_down_since == 0) {
      g_wifi_down_since = millis();
    }
    const unsigned long down_ms = millis() - g_wifi_down_since;
    if (down_ms >= config::kWifiDownGraceMs &&
        millis() - g_last_reconnect_ms >= config::kWifiReconnectIntervalMs) {
      g_last_reconnect_ms = millis();
      if (wifiReconnect()) {
        g_wifi_down_since = 0;
        showSceneIfConnected();
      }
    }
  } else {
    g_wifi_down_since = 0;
    // Task creation can fail under heap pressure right after the 115 KB sprite;
    // without it nothing ever polls, so keep retrying slowly.
    if (!g_poll_task_ok &&
        millis() - g_last_task_retry_ms >= config::kPollTaskRetryMs) {
      g_last_task_retry_ms = millis();
      g_poll_task_ok = services::scene::startPollTask();
    }
    if (wifiConsumeSettingsChanged()) {
      g_render.requestRedraw();
    }
    if (!g_scene_visible) {
      if (millis() - g_last_render_ms >= config::kRenderIntervalMs) {
        g_last_render_ms = millis();
        showSceneIfConnected();
      }
    } else if (millis() - g_last_render_ms >= config::kRenderIntervalMs) {
      // Polling happens on its own task; loop() animates the last list forward
      // by dead reckoning. Idle when there is nothing to animate -- but the
      // frame AFTER the last aircraft leaves must still be drawn, or its symbol
      // stays burned on the panel until the next redraw.
      const bool traffic = services::scene::hasTraffic();
      if (g_render.shouldRender(traffic)) {
        g_last_render_ms = millis();
        g_render.onFrameDrawn(traffic, ui::renderScene());
      }
    }
  }

  delay(10);
}
```

- [ ] **Step 5: Adapt `test_main`**

The copied `test_main.cpp` includes `../../src/services/adsb_client.cpp` (never copied),
calls `fetchUpdate` seven times, and pokes `services::adsb::s_task` directly. Copy it,
then:

```bash
cd /Users/matias/Documents/repos/HomeScreen
cp -R /Users/matias/Documents/repos/ESP32-Plane-Radar/test/test_main firmware/test/
cd firmware
sed -i '' \
  -e 's|#include "../../src/services/adsb_client.cpp"|#include "../../src/services/device_id.cpp"\n#include "../../src/services/server_config.cpp"\n#include "../../src/services/scene_client.cpp"\n#include "../../src/ui/components.cpp"|' \
  -e 's|services::adsb::|services::scene::|g' \
  -e 's|startFetchTask|startPollTask|g' \
  -e 's|kFetchTaskRetryMs|kPollTaskRetryMs|g' \
  test/test_main/test_main.cpp
# kFetchTaskRetryMs is renamed in the new config.h and the grep below cannot
# see it -- without the substitution above this fails to compile mid-task with
# no clue why.
grep -n "fetchUpdate\|adsb\|kFetchTaskRetryMs" test/test_main/test_main.cpp
```
Every remaining hit needs a hand edit: replace `fetchUpdate(...)` calls with the
`g_http`-scripted `pollOnce()` helper from Task 6 Step 4, and `s_task` pokes with
`startPollTask()` / `pollTaskStackFree()`. Then run `check_runners test/test_main/test_main.cpp`.

- [ ] **Step 6: Build, test, commit**

```bash
export PATH="$HOME/.platformio/penv/bin:$PATH"
cd firmware && pio test -e native && pio run -e c3
git add firmware/ && git commit -m "feat(firmware): provisioning with a server field, and the real loop"
```

---

## Task 9: The watchdog

Unbundled from OTA. Spec §7.6 and ADDENDUM §4 ask for both "from day one"; OTA needs a
partition change and a server endpoint that does not exist, and the watchdog needs five
lines. They are not the same size of decision.

The SDK this builds against has `CONFIG_ESP_TASK_WDT=y`, `CONFIG_ESP_TASK_WDT_PANIC=y`,
`CONFIG_ESP_TASK_WDT_TIMEOUT_S=5`, and the idle-task check **off** — so today nothing can
reset this board. Task 3's socket handling is the hang it would rescue.

**Files:** modify `firmware/src/services/scene_client.cpp`, `firmware/src/main.cpp`,
`firmware/test/test_scene_client/test_scene_client.cpp`.

- [ ] **Step 1: Mock the watchdog so the calls stay compiled and visible**

Create `firmware/test/mocks/esp_task_wdt.h`:

```cpp
#pragma once
// The host has no task watchdog. Mocked rather than #ifdef'd out, so the calls
// stay in the code where a reader can see them -- and so a test can assert the
// loop actually feeds the thing.
struct MockWdt {
  int inits = 0;
  int adds = 0;
  int resets = 0;
  unsigned timeout_s = 0;
  bool panic = false;
  void reset() { *this = MockWdt(); }
};
extern MockWdt g_wdt;

inline int esp_task_wdt_init(unsigned timeout_s, bool panic) {
  ++g_wdt.inits; g_wdt.timeout_s = timeout_s; g_wdt.panic = panic; return 0;
}
inline int esp_task_wdt_add(void*) { ++g_wdt.adds; return 0; }
inline int esp_task_wdt_reset() { ++g_wdt.resets; return 0; }
```
Define `MockWdt g_wdt;` in `mock_globals.h`. `MockWdt::reset()` matters: the test calls it
and the plan's revision-2 sketch declared a struct without one.

- [ ] **Step 2: Subscribe in the task, feed in the tick**

In `scene_client.cpp`, add `#include <esp_task_wdt.h>` and:

```cpp
/**
 * 60 s, not the SDK's 5. WiFiClient::connect() resolves the host BEFORE
 * applying the connect timeout, and hostByName can block ~31 s on its own DNS
 * waits which setConnectTimeout() does not bound; add the 8 s header timeout
 * and a legitimately slow poll can exceed 30 s. Long enough never to fire on a
 * device that is merely slow, far shorter than a human noticing a frozen panel.
 */
constexpr uint32_t kWatchdogTimeoutSec = 60;

void feedWatchdog() { esp_task_wdt_reset(); }
```

`feedWatchdog()` is called at the top of `pollTick()` — already written into the
implementation in Task 3 — and the subscription happens once, in the task body:

```cpp
void pollTaskBody(void*) {
  // Nothing else can reset this board: Arduino subscribes loopTask only if you
  // call enableLoopWDT(), and the idle-task check is off in this SDK config
  // (CONFIG_ESP_TASK_WDT=y, PANIC=y, TIMEOUT_S=5, idle check unset). The poll
  // task is the one that can block for 8 s on a dead socket.
  esp_task_wdt_init(kWatchdogTimeoutSec, /*panic=*/true);
  esp_task_wdt_add(nullptr);
  for (;;) {
    pollTick(WiFi.status() == WL_CONNECTED);
    vTaskDelay(pdMS_TO_TICKS(s_last_poll_ok ? s_poll_ms
                                            : config::kPollErrorMs));
  }
}
```

**The feed must be in `pollTick`, and the task must call `pollTick`.** Revision 2 had the
task calling `pollOnce()` directly while the feed lived in `pollTick` — so on hardware the
subscribed task would never have fed the watchdog, and a 60 s panic reboot loop would
have been the first thing the board did. `pio test -e native` and `pio run -e c3` both
pass in that state; only this pairing catches it.

- [ ] **Step 3: Test that the loop really feeds it**

```cpp
// append to test_scene_client.cpp, with a RUN_TEST line
void test_the_poll_loop_feeds_the_watchdog(void) {
  // A watchdog nobody feeds is a reboot loop; one nobody subscribes to is
  // decoration. Both halves, and the feed must survive a link-down tick --
  // that is precisely when the device is doing nothing and looks hung.
  g_wdt.reset();
  services::scene::pollTick(true);
  TEST_ASSERT_EQUAL_INT(1, g_wdt.resets);
  services::scene::pollTick(false);
  TEST_ASSERT_EQUAL_INT_MESSAGE(2, g_wdt.resets,
                                "a link-down tick must still feed it");
}

void test_the_watchdog_is_configured_longer_than_a_slow_poll(void) {
  // 8 s header timeout plus up to ~31 s of DNS is more than the SDK's 5 s
  // default and more than 30.
  TEST_ASSERT_GREATER_OR_EQUAL(45u, services::scene::kWatchdogTimeoutSec);
}
```
`kWatchdogTimeoutSec` must therefore be declared in `scene_client.h`, not in the anonymous
namespace.

- [ ] **Step 3: Run green and commit**

```bash
cd firmware && pio test -e native && pio run -e c3
git add firmware/ && git commit -m "feat(firmware): watchdog on the poll task"
```

---

## Task 10: Flash and bring up

The first task that needs the board. Everything above is verifiable on the Mac.

- [ ] **Step 1: Establish which board is attached**

A device is present at `/dev/cu.usbmodem2101`. **It may be the working radar.** Flashing
it destroys a running device, so identify it first:

```bash
export PATH="$HOME/.platformio/penv/bin:$PATH"
pio device monitor -p /dev/cu.usbmodem2101 -b 115200
```
A banner of `Plane Radar` is the reference firmware — **stop and ask which board to use.**
A silent or unknown port is a spare and is safe to flash.

- [ ] **Step 2: Flash**

```bash
cd firmware && pio run -e c3 -t upload --upload-port /dev/cu.usbmodem2101
pio device monitor -p /dev/cu.usbmodem2101 -b 115200
```
Expected: `HomeScreen display client hs-0.1`, then the setup portal
(`HomeScreen-Setup`), because a fresh NVS has no credentials.

- [ ] **Step 3: Provision**

Join `HomeScreen-Setup`, set Wi-Fi, and set **server** to `192.168.1.116:8080`.
Expected on the glass: `SIN ASIGNAR`, the hardware id, and the server's Spanish message.

- [ ] **Step 4: Confirm the server saw it, from the Mac**

```bash
curl -s http://192.168.1.116:8080/api/devices | python3 -m json.tool
```
Expected: a new device, `online: true`, `caps` showing `w:240 h:240 depth:16
max_items:64 components:["radar"]`, `fw: "hs-0.1"`, `poll_seconds: 5`.

- [ ] **Step 5: Assign the radar from the Mac and watch the glass**

```bash
HW=<the id from step 4>
curl -s -X PATCH http://192.168.1.116:8080/api/devices/$HW \
  -H 'content-type: application/json' -d '{"name":"radar","scene":"planes"}'
```
Expected: within one poll the display switches from `SIN ASIGNAR` to the radar with live
traffic. **This is the moment the phase is proving:** the screen changed without a
reflash.

- [ ] **Step 6: Switch it back and forth**

```bash
curl -s -X PATCH http://192.168.1.116:8080/api/devices/$HW \
  -H 'content-type: application/json' -d '{"scene":"unassigned"}'
curl -s -X PATCH http://192.168.1.116:8080/api/devices/$HW \
  -H 'content-type: application/json' -d '{"scene":"planes"}'
```
Expected: the panel follows, each within one poll.

---

## Task 11: Measure, soak, and document

- [ ] **Step 1: Measure what dropping TLS actually bought**

```bash
cd firmware && pio run -e c3-debug -t upload --upload-port /dev/cu.usbmodem2101
pio device monitor -p /dev/cu.usbmodem2101 -b 115200 | tee /tmp/heap.log
```
Record `dbg: heap <stage> free N largest N` at boot, after the display, after the 115 KB
sprite, after setup, and in steady state. Compare against the reference's measured
baseline: **~22–28 KB free, ~9 KB largest block**, and the static figures
**1,247,850 B flash / 55,012 B RAM**.

Expected: materially more free heap and a much larger largest-free-block. **If it is
not, say so plainly in `firmware/OPS.md`.** The ~35 KB figure is the reference's own
comment, not a measurement of this firmware, and this step is what turns it into one.

Two documented reference symptoms are the real test, because both were caused by the
tight budget:
- WiFiManager's `/wifi` page blanking at ~16 APs (needs ~9.2 KB contiguous).
- `IncompleteInput` roughly every 2–3 minutes.

Check both. If the portal page now renders at a busy site and the parse errors are gone,
that is the phase's benefit in a form a human can see.

- [ ] **Step 2: Check the poll task's stack headroom**

`pollTaskStackFree()` on the serial log. `kPollTaskStackBytes` is 6144, down from the
reference's 8192, on the reasoning that no mbedTLS handshake runs on this stack.
Expected: comfortably above 1 KB free. If it is under 512 B, raise it and record why.

- [ ] **Step 3: Soak for an hour, then read the fleet**

```bash
curl -s http://192.168.1.116:8080/api/status | python3 -c \
  'import json,sys; d=json.load(sys.stdin); print(d["memory"]); print(d["fleet"][0])'
```
Expected: `online: true`, `last_seen` within one poll, telemetry with a growing `uptime`,
and the server's in-memory maps unchanged in size.

- [ ] **Step 4: Pull the plug on the Pi**

The failure the two clocks and three expiries exist for. With the radar showing traffic,
stop the server:

```bash
ssh pi@192.168.1.116 'sudo systemctl stop homescreen-serve'
```
Expected on the glass: targets keep dead-reckoning briefly, dim at 12 s, and the picture
drops to rings-only at 60 s. Then:

```bash
ssh pi@192.168.1.116 'sudo systemctl start homescreen-serve'
```
Expected: the radar returns within one or two polls **without a reboot**. If it does not,
the socket teardown from Task 3 Step 7 is not working on hardware — that is exactly the
hang it was written for, and the host test cannot prove it.

- [ ] **Step 5: Stop only the fetcher**

The failure a contact clock alone cannot see, and the one whose expected result revision
2 got wrong. Stopping the daemon does **not** set `feed_ok: false` — `write_failure` only
runs when a fetch runs and fails, so nothing writes at all and `ok: true` stays on disk.
The only thing that moves is `feed_age_s`.

```bash
ssh pi@192.168.1.116 'sudo systemctl stop homescreen-fetch'
watch -n5 "curl -s http://192.168.1.116:8080/api/device/$HW/scene\
?w=240\&h=240\&depth=16\&max_items=64\&components=radar \
  | python3 -c 'import json,sys; c=json.load(sys.stdin)[\"components\"][0]; \
    print(c[\"feed_ok\"], c[\"feed_age_s\"])'"
```
Expected: `feed_ok` stays **True** while `feed_age_s` climbs. On the glass: targets keep
dead-reckoning, dim past 12 s, and the picture drops to rings-only once `feed_age_s`
passes `kFeedExpirySec` (60 s). If the panel blanks in the first few seconds, the
firmware is expiring on `feed_ok` and will blink on every upstream hiccup. Restart the
fetcher and confirm the radar returns within one poll.

- [ ] **Step 6: Write the docs and commit**

`firmware/README.md`: what it is, how to build, flash and provision, and the one-line
answer to "how do I change what it shows" (`PATCH /api/devices/<hw>`).
`firmware/OPS.md`: the measured heap figures against the reference's, the poll cadence,
what each status screen means, and the three expiry conditions with the failure each one
catches.

```bash
git add firmware/ CLAUDE.md
git commit -m "docs(firmware): bring-up notes and measured heap figures"
```

---

## What this plan deliberately does not do

- **No `text`, `spark` or `icon` components.** The dispatcher is built for them; adding
  them is the next piece of work, once the radar proves the loop on hardware.
- **No OTA.** Spec §7.6 wants it from day one. It needs a partition change and a server
  endpoint that does not exist, and it is not on the critical path to proving the
  protocol. The watchdog, which spec §7.6 bundles with it, **is** in scope (Task 9).
- **No `X-Feed-Age` / `X-Feed-Ok` response headers on `/scene`.** `PLAN.md` §3 asks for
  them on the `/data` route and they are implemented there. On this route the same
  information rides in the body as `feed_ok` and `feed_age_s`, which a 304 does not
  carry — but a 304 also means the content did not change, so the device already has the
  last values. The gap is real only if the feed dies *during* a run of 304s with
  byte-identical content, which cannot happen: `feed_ok` is part of the body, so it
  changing changes the ETag.
- **No `grid` layout.** Deferred in the spec (§5.4); every scene is `fill`.
- **No e-paper.** Phase B, different device, pixel-push.
- **The reference firmware is not retired.** It keeps working, on its own repo and its
  own board, until this one has run long enough to trust.

---

## What review changed (revision 1 → 2)

Two independent reviews. Three findings would have reached hardware:

1. **`contentExpired()` read the contact clock only.** `serve.py` serves from cache and
   never fetches on a device request, so when the Pi's fetcher dies the server keeps
   answering 200/304 forever. A contact-clock test can never fire, and the panel would
   present hours-old traffic as live — the exact failure `dataExpired()` exists to
   prevent. Now three conditions, and `feed_ok` — which revision 1 parsed and never
   read — is the one that fires first.
2. **No `s_client.stop()` anywhere.** `HTTPClient` keeps a keep-alive socket across
   `end()`, and a server that vanishes without a FIN leaves `connected()` true forever:
   every later request writes into a dead socket and times out after 8 s, permanently,
   with no watchdog to break it. The reference forced a fresh session on every failure
   for exactly this reason. Now on every failure path and on the link-down transition,
   with a mock counter so it is tested.
3. **Nothing bounded the item count before the parse.** ArduinoJson peaks ~4.6× the body;
   an operator raising `max_aircraft` to 200 would produce ~88 KB of peak against ~55 KB
   of heap — `NoMemory` every cycle at the busiest time of day. Now bounded at both ends:
   the device declares `max_items=64` (server support committed in `afbf339`) and refuses
   any body over `kMaxBodyBytes` before parsing, which also catches chunked responses.

One server bug, found by review and fixed in `afbf339` before this revision: **`/scene`
never added the cache dwell to `age`**, so a 20-second-old record reached the device
still claiming 3.1 s. VALIDATION F4, second door. The wire table in revision 1 asserted
the opposite.

Structural fixes: a new Task 0 extends five mocks that revision 1 assumed existed;
`publish()`, the accessors, `resetForTest()`, `startPollTask()`, `hasTraffic()`,
`renderScene()` and `main.cpp` are written out instead of described; the clocks and all
scene metadata moved inside the mutex (outside it, a frame could draw new positions
against an old content time — a visible jump-and-snap once per poll); `componentName()`
was added before the header is committed, because the dispatcher had nothing to dispatch
on; tasks were reordered so nothing depends on a later one; `test_display` got its own
task, because a naive port leaves every dead-reckoning test green against a stationary
target; the ETag fixture is escaped so it compiles; the contract test generates into
`tmp_path` instead of healing itself on the second run; and the TLS saving is stated as
~35 KB once rather than ~66 KB.
