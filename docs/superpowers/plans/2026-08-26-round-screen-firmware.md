# Round-Screen Firmware Implementation Plan (Phase 3a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A server-driven firmware for the ESP32-C3 + GC9A01 round display that fetches
whatever scene the Pi has assigned it and renders it — starting with the radar — so that
changing what the screen shows never again requires a reflash.

**Architecture:** A new PlatformIO project at `HomeScreen/firmware/`. It polls
`GET /api/device/<hw>/scene` over plain HTTP (no TLS), parses the scene envelope, and
dispatches each component to a renderer. The `radar` component is rendered by the
existing GC9A01 code ported verbatim from `ESP32-Plane-Radar`, which is **read-only
reference and is never modified**. A component dispatcher is in place from the start so
that adding `text`, `spark` and the rest later is a new file plus a `case`, not a
restructuring.

**Tech Stack:** PlatformIO, `espressif32@6.5.0`, Arduino framework, C++17, LovyanGFX
1.2.7, ArduinoJson 7.4.2, WiFiManager 2.0.17, Unity (host tests, `-e native`) with
AddressSanitizer.

**Spec:** `docs/superpowers/specs/2026-08-26-server-driven-displays-design.md` §7
(PROVISIONAL by agreement), plus `docs/PLAN.md` §3's three firmware requirements and
`docs/ADDENDUM-01-multi-display.md` §2.

---

## Global Constraints

- **`/Users/matias/Documents/repos/ESP32-Plane-Radar` is never modified.** Files are
  copied out of it. Any improvement discovered while porting is made in the copy.
- **No TLS.** No `WiFiClientSecure`, no root certs, no mbedTLS. Dropping it is the
  measured win this phase exists for: ~33 KB pinned heap plus two ~16.4 KB per-fetch
  blocks, on a device whose healthy free heap is 22–28 KB with a ~9 KB largest block.
- **Every device call sends `w`, `h` and `depth`.** The server only reads a capability
  *list* (`components=`) when the same request also declares `w` and `h` — a fragment
  cannot redefine a device. A request omitting them silently loses its component
  declaration.
- **`/frame` is not used by this device.** It is pixel-push, for the e-paper.
- **1 = black is a framebuffer concern and does not apply here.** This display is
  16-bit colour and draws its own geometry.
- **Spanish on the glass, English in logs.** Matches the server: `status.py` and
  `planes.py` render Spanish; `/home`, JSON and logs are English.
- **Host tests must pass before any hardware step:** `pio test -e native`.
- **Firmware version string:** `fw` is sent on every request. Format `hs-<major>.<minor>`,
  starting `hs-0.1`.

---

## File Structure

```
HomeScreen/firmware/
  platformio.ini                     build envs: c3, c3-debug, native
  partitions/display_client.csv      copied from ESP32-Plane-Radar
  data/ui_font.vlw                   copied asset (Noto Sans Bold 15 VLW)
  include/
    config.h                         pins, timings, geometry. NO feed URLs.
    debug_log.h                      copied verbatim
    hardware/display.h               copied verbatim
    hardware/lgfx_config.hpp         copied verbatim
    services/device_id.h             NEW  hw id from the MAC
    services/server_config.h         NEW  server address: NVS + portal field
    services/scene_client.h          NEW  poll, parse, hold-last-good, 2 clocks
    services/radar_location.h        copied verbatim
    services/wifi_setup.h            copied + one portal field
    ui/components.h                  NEW  Component model + dispatcher
    ui/radar_display.h               copied verbatim
    ui/radar_geo.h                   copied verbatim
    ui/radar_range.h                 copied verbatim
    ui/radar_theme.h                 copied verbatim
    ui/render_policy.h               copied verbatim
    ui/runway_overlay.h              copied verbatim
    ui/status_screens.h              copied + "no server" screen
  src/                               mirrors include/, plus main.cpp
    data/large_airports_data.cpp     copied verbatim (2,884 lines of data)
  test/
    mocks/                           copied, extended with response headers + 304
    fixtures_wire.h                  THE CONTRACT: bytes the server really emits
    test_scene_client/
    test_components/
    test_device_id/
    test_server_config/
    test_geo/  test_display/  test_render_policy/  test_settings/  test_wifi/
                                     copied with the ported modules
HomeScreen/
  tests/test_wire_contract.py        NEW  pins the same bytes from the server side
  scripts/dump_wire_fixture.py       NEW  regenerates fixtures_wire.h from the app
```

**Why this shape.** `scene_client` owns the network and the two clocks and knows nothing
about drawing. `components` owns dispatch and knows nothing about HTTP. `radar_display`
is untouched by either — it renders an aircraft list, exactly as it does today. That
boundary is what makes "add a component" a new file rather than an edit to the fetch
path.

---

## The wire contract, once, here

Every task below refers to this. It is what the server emits today, verified live:

```
GET /api/device/aabb00112233/scene?w=240&h=240&depth=16&components=radar
    &fw=hs-0.1&rssi=-58&uptime=1234
If-None-Match: "3d3f072e75c16427"

200 OK
ETag: "3d3f072e75c16427"
X-Poll-Seconds: 5
{"hw":"aabb00112233","name":"radar","scene":"planes","assigned":true,
 "layout":"fill",
 "components":[{"c":"radar","radius_km":60.0,"feed_ok":true,
   "items":[{"lat":40.5,"lon":-3.6,"nose":90.0,"trk":91.0,"gs":400.0,
             "ve":0.13,"vn":-0.17,"age":3.1,"dst":7.4,
             "cs":"IBE3221","ty":"A320","alt":"3675 ft"}]}]}

304 Not Modified
ETag: "3d3f072e75c16427"
X-Poll-Seconds: 5
(no body)
```

Unassigned devices get `"scene":"unassigned"`, `"assigned":false`, `"components":[]` and
`"message":"sin asignar · elige una escena en el panel"`. A device that declares
components the scene does not have gets `"unsupported":["radar"]`.

Item fields map 1:1 onto the existing `Aircraft` struct, deliberately — `ve`/`vn` are
already km/s east/north and `age` is already the position age in seconds, so the device
no longer computes them:

| wire | `Aircraft` field | note |
|---|---|---|
| `lat` `lon` | `lat` `lon` | degrees |
| `nose` | `nose_deg` | glyph rotation |
| `trk` | `track_deg` | direction of travel |
| `gs` | `gs_knots` | |
| `ve` `vn` | `vel_e_km_s` `vel_n_km_s` | **km/s**, precomputed server-side |
| `age` | `pos_age_s` | includes the server's cache dwell |
| `dst` | `dst_nm` | nautical miles; `-1` if absent |
| `cs` `ty` `alt` | `callsign[9]` `type[5]` `alt[12]` | |

---

## Task 1: Project skeleton that builds and host-tests

**Files:**
- Create: `firmware/platformio.ini`
- Create: `firmware/include/config.h`
- Create: `firmware/src/main.cpp`
- Copy: `firmware/include/debug_log.h`, `firmware/partitions/display_client.csv`,
  `firmware/data/ui_font.vlw`
- Copy: `firmware/test/mocks/` (whole directory)
- Test: `firmware/test/test_smoke/test_smoke.cpp`

**Interfaces:**
- Consumes: nothing.
- Produces: a `native` env that compiles project sources against the mocks, and a `c3`
  env that produces a flashable image. `config::kDisplayWidth = 240`,
  `config::kDisplayHeight = 240`, `config::kFirmwareVersion = "hs-0.1"`.

- [ ] **Step 1: Copy the reference assets that need no thought**

```bash
cd /Users/matias/Documents/repos/HomeScreen
REF=/Users/matias/Documents/repos/ESP32-Plane-Radar
mkdir -p firmware/{include/{hardware,services,ui},src/{hardware,services,ui,data},test/mocks,partitions,data,scripts}
cp -R $REF/test/mocks/. firmware/test/mocks/
cp $REF/include/debug_log.h firmware/include/
cp $REF/data/ui_font.vlw firmware/data/
cp $REF/partitions/plane_radar.csv firmware/partitions/display_client.csv
# Prove the reference repo is untouched:
git -C $REF status --porcelain | tee /dev/stderr | wc -l   # must print 0
```

- [ ] **Step 2: Write `firmware/platformio.ini`**

```ini
; ESP32-C3 Super Mini + 1.28" round GC9A01 (240x240), server-driven.
; Deliberately NOT a copy of ESP32-Plane-Radar's: no TLS means no
; WiFiClientSecure, and the partition table is renamed.
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
  -DDISPLAY_CLIENT_DEBUG=1

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

- [ ] **Step 3: Write `firmware/include/config.h`**

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
 * trusted but not obeyed blindly: a bad value must not turn the device into a
 * flood or into a brick that never polls again.
 */
constexpr unsigned long kPollMinMs = 1000;
constexpr unsigned long kPollMaxMs = 600000;
constexpr unsigned long kPollDefaultMs = 5000;
/** Retry sooner than the poll cadence when the last exchange failed. */
constexpr unsigned long kPollErrorMs = 3000;
constexpr unsigned long kFetchTaskRetryMs = 10000;
constexpr unsigned long kRenderIntervalMs = 100;
constexpr unsigned long kDebugFrameReportMs = 1000;

// --- UI colours (RGB565) — status screens ---
constexpr uint16_t kColorBlack = 0x0000;
constexpr uint16_t kColorYellow = 0xFFE0;
constexpr uint16_t kTextOnYellow = kColorBlack;
constexpr uint16_t kTextOnBlack = 0xFFFF;

}  // namespace config
```

- [ ] **Step 4: Write the smoke test**

```cpp
// firmware/test/test_smoke/test_smoke.cpp
// Proves the host harness can compile project headers against the mocks. If
// this fails, nothing below it can be trusted to be testing the real code.
#include <Arduino.h>
#include <unity.h>

#include "config.h"
#include "../mocks/mock_globals.h"

void test_the_display_geometry_is_what_the_server_is_told(void) {
  TEST_ASSERT_EQUAL_INT(240, config::kDisplayWidth);
  TEST_ASSERT_EQUAL_INT(240, config::kDisplayHeight);
  TEST_ASSERT_EQUAL_INT(16, config::kDisplayDepth);
}

void test_no_feed_url_is_compiled_into_this_firmware(void) {
  // The whole point of the phase: the device knows a server, not a data source.
  TEST_ASSERT_EQUAL_STRING("dashboard.local", config::kDefaultServerHost);
}

void setUp(void) {}
void tearDown(void) {}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_the_display_geometry_is_what_the_server_is_told);
  RUN_TEST(test_no_feed_url_is_compiled_into_this_firmware);
  return UNITY_END();
}
```

- [ ] **Step 5: Write a `main.cpp` that does nothing yet but links**

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

- [ ] **Step 6: Run both builds**

Run: `cd firmware && pio test -e native`
Expected: 2 tests pass.

Run: `cd firmware && pio run -e c3`
Expected: build succeeds; note the reported flash/RAM use in the commit message.

- [ ] **Step 7: Commit**

```bash
git add firmware/
git commit -m "feat(firmware): skeleton that builds for the C3 and tests on the host"
```

---

## Task 2: Device identity and the server address

**Files:**
- Create: `firmware/include/services/device_id.h`, `firmware/src/services/device_id.cpp`
- Create: `firmware/include/services/server_config.h`,
  `firmware/src/services/server_config.cpp`
- Test: `firmware/test/test_device_id/test_device_id.cpp`,
  `firmware/test/test_server_config/test_server_config.cpp`

**Interfaces:**
- Consumes: `config::kDefaultServerHost`, `config::kDefaultServerPort`.
- Produces:
  - `const char* services::deviceId()` — 12 lowercase hex chars, from the MAC.
  - `bool services::server::load()` / `const char* services::server::host()` /
    `uint16_t services::server::port()` / `const char* services::server::baseUrl()`
  - `bool services::server::saveFromString(const char* text)` — accepts
    `192.168.1.116`, `192.168.1.116:8080`, `dashboard.local`, `http://host:port/`.

- [ ] **Step 1: Write the failing identity test**

```cpp
// firmware/test/test_device_id/test_device_id.cpp
#include <Arduino.h>
#include <unity.h>
#include <cstring>

#include "../mocks/mock_globals.h"
#include "../../src/services/device_id.cpp"

void test_the_id_is_the_mac_as_lowercase_hex(void) {
  // The server keys everything on this string. It must be stable across
  // reboots and reflashes, because the operator's scene assignment is stored
  // against it -- a device that changes id comes back as "sin asignar".
  WiFi.mac_[0] = 0xAA; WiFi.mac_[1] = 0xBB; WiFi.mac_[2] = 0x00;
  WiFi.mac_[3] = 0x11; WiFi.mac_[4] = 0x22; WiFi.mac_[5] = 0x33;
  TEST_ASSERT_EQUAL_STRING("aabb00112233", services::deviceId());
}

void test_the_id_is_computed_once_and_never_changes(void) {
  const char* first = services::deviceId();
  WiFi.mac_[0] = 0xFF;                       // the MAC cannot really change
  TEST_ASSERT_EQUAL_STRING(first, services::deviceId());
}

void setUp(void) {}
void tearDown(void) {}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_the_id_is_the_mac_as_lowercase_hex);
  RUN_TEST(test_the_id_is_computed_once_and_never_changes);
  return UNITY_END();
}
```

- [ ] **Step 2: Extend the WiFi mock with a MAC**

Add to `firmware/test/mocks/WiFi.h`, inside `class MockWiFi`:

```cpp
  uint8_t mac_[6] = {0xDE, 0xAD, 0xBE, 0xEF, 0x00, 0x01};
  void macAddress(uint8_t* out) { memcpy(out, mac_, 6); }
```

- [ ] **Step 3: Run the test to see it fail**

Run: `cd firmware && pio test -e native -f test_device_id`
Expected: FAIL — `device_id.cpp` does not exist.

- [ ] **Step 4: Implement `device_id`**

```cpp
// firmware/include/services/device_id.h
#pragma once

namespace services {

/**
 * This device's hardware id: 12 lowercase hex characters of the station MAC.
 *
 * The server stores the operator's scene assignment against this string, so it
 * has to survive reboots and reflashes -- anything derived from a random seed
 * or from NVS would make a device come back as "sin asignar" after a flash.
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

- [ ] **Step 5: Run it green**

Run: `cd firmware && pio test -e native -f test_device_id`
Expected: 2 tests pass.

- [ ] **Step 6: Write the failing server-address test**

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
  // This firmware has no TLS at all. Accepting the string and then connecting
  // in the clear would be a lie told to whoever typed it.
  TEST_ASSERT_FALSE(saveFromString("https://dashboard.local"));
}

void test_junk_is_refused_and_the_previous_value_survives(void) {
  TEST_ASSERT_TRUE(saveFromString("192.168.1.116"));
  TEST_ASSERT_FALSE(saveFromString(""));
  TEST_ASSERT_FALSE(saveFromString("   "));
  TEST_ASSERT_FALSE(saveFromString("host:99999"));
  TEST_ASSERT_FALSE(saveFromString("host:0"));
  load();
  TEST_ASSERT_EQUAL_STRING("192.168.1.116", host());
}

void test_an_unconfigured_device_falls_back_to_the_compiled_default(void) {
  load();
  TEST_ASSERT_EQUAL_STRING("dashboard.local", host());
  TEST_ASSERT_EQUAL_UINT16(8080, port());
}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_a_bare_host_takes_the_default_port);
  RUN_TEST(test_an_explicit_port_is_kept);
  RUN_TEST(test_a_pasted_url_is_accepted_because_that_is_what_people_paste);
  RUN_TEST(test_https_is_refused_rather_than_silently_downgraded);
  RUN_TEST(test_junk_is_refused_and_the_previous_value_survives);
  RUN_TEST(test_an_unconfigured_device_falls_back_to_the_compiled_default);
  return UNITY_END();
}
```

- [ ] **Step 7: Run it to see it fail**

Run: `cd firmware && pio test -e native -f test_server_config`
Expected: FAIL — `server_config.cpp` does not exist.

- [ ] **Step 8: Implement `server_config`**

```cpp
// firmware/include/services/server_config.h
#pragma once

#include <cstdint>

namespace services::server {

/** Read the stored address into memory. Call once at boot. */
void load();

const char* host();
uint16_t port();
/** "http://host:port" — no trailing slash. Valid until the next load(). */
const char* baseUrl();

/**
 * Parse and persist. Accepts `host`, `host:port`, and a pasted
 * `http://host:port/path`. Refuses `https://` outright: this firmware has no
 * TLS, and accepting the string then connecting in the clear would be a lie.
 * Returns false and leaves the stored value untouched on anything unusable.
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

/** Trim, strip an http:// prefix and any /path, split off :port. */
bool parse(const char* text, char* out_host, size_t host_len, uint16_t* out_port) {
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
  uint16_t port = config::kDefaultServerPort;
  char* colon = strrchr(buf, ':');
  if (colon != nullptr) {
    *colon = '\0';
    char* end = nullptr;
    const long value = strtol(colon + 1, &end, 10);
    if (end == colon + 1 || *end != '\0' || value < 1 || value > 65535) {
      return false;
    }
    port = static_cast<uint16_t>(value);
  }
  if (buf[0] == '\0' || strlen(buf) >= host_len) {
    return false;
  }
  strncpy(out_host, buf, host_len - 1);
  out_host[host_len - 1] = '\0';
  *out_port = port;
  return true;
}

void rebuildBaseUrl() {
  snprintf(s_base, sizeof(s_base), "http://%s:%u", s_host,
           static_cast<unsigned>(s_port));
}

}  // namespace

void load() {
  Preferences prefs;
  // read-only open: on a device that has never been configured the framework
  // logs "nvs_open failed: NOT_FOUND" at [E]. That is the expected steady state
  // for a fresh board, not a fault.
  if (prefs.begin(kPrefsNamespace, true)) {
    String stored = prefs.getString(kKeyHost, "");
    s_port = prefs.getUShort(kKeyPort, config::kDefaultServerPort);
    prefs.end();
    if (stored.length() > 0 && stored.length() < sizeof(s_host)) {
      strncpy(s_host, stored.c_str(), sizeof(s_host) - 1);
      s_host[sizeof(s_host) - 1] = '\0';
      rebuildBaseUrl();
      DEBUG_LOG("server: %s", s_base);
      return;
    }
  }
  strncpy(s_host, config::kDefaultServerHost, sizeof(s_host) - 1);
  s_host[sizeof(s_host) - 1] = '\0';
  s_port = config::kDefaultServerPort;
  rebuildBaseUrl();
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
  return true;
}

}  // namespace services::server
```

- [ ] **Step 9: Run it green**

Run: `cd firmware && pio test -e native`
Expected: all tests pass (2 smoke + 2 identity + 6 server config).

- [ ] **Step 10: Commit**

```bash
git add firmware/
git commit -m "feat(firmware): device identity from the MAC, server address in NVS"
```

---

## Task 3: The scene client — polling, 304, and two clocks

This is the heart of the phase. It replaces `adsb_client.cpp` entirely: no TLS, no
adsb.fi URL building, no field-fallback chains (the server did that), and a real 304
path.

**Files:**
- Create: `firmware/include/services/scene_client.h`,
  `firmware/src/services/scene_client.cpp`
- Create: `firmware/test/fixtures_wire.h`
- Create: `HomeScreen/scripts/dump_wire_fixture.py`
- Create: `HomeScreen/tests/test_wire_contract.py`
- Modify: `firmware/test/mocks/HTTPClient.h` (response headers, 304, request headers)
- Test: `firmware/test/test_scene_client/test_scene_client.cpp`

**Interfaces:**
- Consumes: `services::deviceId()`, `services::server::baseUrl()`,
  `config::kFirmwareVersion`, `config::kDisplayWidth/Height/Depth`,
  `config::kPollMinMs/kPollMaxMs/kPollDefaultMs`.
- Produces:
  - `struct services::scene::Aircraft` — the field-for-field twin of the reference
    firmware's, so `radar_display` ports across unmodified.
  - `bool services::scene::pollOnce()` — one HTTP exchange. True on 200 or 304.
  - `size_t aircraftCount()`, `const Aircraft* aircraftList()`,
    `bool aircraftLock(uint32_t)`, `void aircraftUnlock()`, `bool hasTraffic()`
  - `float secondsSinceContent()` (clamped), `float secondsSinceContentRaw()`,
    `bool contentExpired()` — the three consumers, now off two clocks.
  - `unsigned long pollIntervalMs()`, `bool assigned()`, `const char* sceneName()`,
    `const char* message()`, `float radiusKm()`, `bool feedOk()`
  - `constexpr float kExtrapolationHorizonSec = 12.0f;`

- [ ] **Step 1: Generate the wire fixture from the real server**

```python
# HomeScreen/scripts/dump_wire_fixture.py
"""Emit the firmware's wire fixture from THIS server's own routes.

The firmware parses bytes this server produces. A fixture hand-written on the
firmware side would drift the moment a field is renamed, and the failure would
appear as a blank screen on hardware rather than a red test. So the fixture is
generated, checked in, and pinned from both sides.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from homescreen.cache import write_cache
from homescreen.serve import create_app

CFG = {"location": {"name": "Madrid", "timezone": "Europe/Madrid"},
       "feeds": {"adsb": {"source": "api", "endpoint": "https://x"}},
       "devices": []}
HW = "aabb00112233"
QUERY = f"w=240&h=240&depth=16&components=radar&fw=hs-0.1"


def dump(out_path: pathlib.Path, cache_dir: pathlib.Path) -> None:
    app = create_app(CFG, cache_dir, version="fixture")
    client = app.test_client()
    write_cache(cache_dir / "feed" / "adsb.json", {"aircraft": [
        {"lat": 40.5, "lon": -3.6, "nose": 90.0, "trk": 91.0, "gs": 400.0,
         "ve": 0.13, "vn": -0.17, "age": 3.1, "dst": 7.4,
         "cs": "IBE3221", "ty": "A320", "alt": "3675 ft"},
        {"lat": 40.6, "lon": -3.7, "nose": 270.0, "trk": 271.0, "gs": 250.0,
         "ve": -0.09, "vn": 0.02, "age": 0.4, "dst": 12.9,
         "cs": "RYR44BQ", "ty": "B738", "alt": "12000 ft"}]})

    client.get(f"/api/device/{HW}/scene?{QUERY}")
    unassigned = client.get(f"/api/device/{HW}/scene?{QUERY}")
    client.patch(f"/api/devices/{HW}", json={"name": "radar", "scene": "planes"})
    assigned = client.get(f"/api/device/{HW}/scene?{QUERY}")
    dropped = client.get(f"/api/device/{HW}/scene?w=240&h=240&components=text")

    def body(resp):
        return json.dumps(resp.get_json(), separators=(",", ":"),
                          ensure_ascii=False)

    lines = [
        "// GENERATED by HomeScreen/scripts/dump_wire_fixture.py -- do not edit.",
        "//",
        "// These are the exact bytes the server emits. Regenerate with:",
        "//   venv/bin/python scripts/dump_wire_fixture.py",
        "// tests/test_wire_contract.py fails if this file drifts from the server.",
        "#pragma once",
        "",
        f'inline constexpr char kWireAssigned[] = R"JSON({body(assigned)})JSON";',
        f'inline constexpr char kWireUnassigned[] = R"JSON({body(unassigned)})JSON";',
        f'inline constexpr char kWireDropped[] = R"JSON({body(dropped)})JSON";',
        f'inline constexpr char kWireAssignedEtag[] = '
        f'"{assigned.headers["ETag"]}";',
        f'inline constexpr int kWireAssignedPollSeconds = '
        f'{assigned.headers["X-Poll-Seconds"]};',
        "",
    ]
    out_path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    import tempfile
    root = pathlib.Path(__file__).resolve().parent.parent
    dump(root / "firmware" / "test" / "fixtures_wire.h",
         pathlib.Path(tempfile.mkdtemp()))
    print("wrote firmware/test/fixtures_wire.h")
```

Run: `cd /Users/matias/Documents/repos/HomeScreen && venv/bin/python scripts/dump_wire_fixture.py`
Expected: `firmware/test/fixtures_wire.h` exists and contains a `"c":"radar"` component.

- [ ] **Step 2: Pin the contract from the server side**

```python
# HomeScreen/tests/test_wire_contract.py
"""The firmware parses these bytes. Renaming a field here is a silent blank
screen on hardware, so both sides pin the same fixture."""
import pathlib
import subprocess
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "firmware" / "test" / "fixtures_wire.h"


def test_the_checked_in_fixture_still_matches_this_server(tmp_path):
    # Not "does it parse" -- does it still say what the firmware was built to
    # read. Regenerate with: venv/bin/python scripts/dump_wire_fixture.py
    before = FIXTURE.read_text(encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "dump_wire_fixture.py")],
                   check=True, cwd=ROOT, capture_output=True)
    after = FIXTURE.read_text(encoding="utf-8")
    assert before == after, (
        "the wire format changed; the firmware in firmware/ parses the old one. "
        "Regenerate the fixture, then update firmware/src/services/scene_client.cpp "
        "and re-run `pio test -e native` before committing.")


@pytest.mark.parametrize("field", ["lat", "lon", "nose", "trk", "gs", "ve", "vn",
                                   "age", "dst", "cs", "ty", "alt"])
def test_every_field_the_firmware_reads_is_present(field):
    assert f'"{field}"' in FIXTURE.read_text(encoding="utf-8"), \
        f"the firmware's Aircraft struct reads {field!r}"


def test_the_component_key_and_envelope_keys_are_unchanged():
    text = FIXTURE.read_text(encoding="utf-8")
    for key in ('"c":"radar"', '"components"', '"layout"', '"assigned"',
                '"radius_km"', '"feed_ok"', '"items"'):
        assert key in text, f"{key} is what the firmware switches on"
```

Run: `cd /Users/matias/Documents/repos/HomeScreen && venv/bin/pytest tests/test_wire_contract.py -q`
Expected: PASS.

- [ ] **Step 3: Teach the HTTP mock about headers and 304**

Add to `firmware/test/mocks/HTTPClient.h`:

```cpp
  // --- added for the scene client: response headers and conditional GETs ---
  std::map<std::string, std::string> response_headers;
  std::vector<std::string> collected_header_keys;
  std::string last_if_none_match;
```
(inside `struct MockHttp`, and add `#include <map>` / `#include <vector>` at the top;
extend `reset()` to clear all three.)

And inside `class HTTPClient`:

```cpp
  void addHeader(const String& name, const String& value) {
    if (String(name) == "If-None-Match") {
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

Also add `HTTP_CODE_NOT_MODIFIED = 304` to the enum.

- [ ] **Step 4: Write the failing scene-client tests**

```cpp
// firmware/test/test_scene_client/test_scene_client.cpp
// Exercises the real scene_client.cpp against the exact bytes the server
// emits (fixtures_wire.h is generated from it).
#include <Arduino.h>
#include <unity.h>
#include <cmath>
#include <cstring>

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
  services::server::load();
  resetForTest();
}
void tearDown(void) {}

void test_the_request_declares_everything_the_server_needs(void) {
  // The server only reads a capability LIST when the same request also carries
  // w and h. Omitting them silently drops our component declaration, and the
  // radar then arrives as `unsupported` with an empty component list.
  poll(kWireAssigned);
  const std::string& url = g_http.last_url;
  TEST_ASSERT_NOT_EQUAL(std::string::npos, url.find("/api/device/"));
  TEST_ASSERT_NOT_EQUAL(std::string::npos, url.find("/scene?"));
  TEST_ASSERT_NOT_EQUAL(std::string::npos, url.find("w=240"));
  TEST_ASSERT_NOT_EQUAL(std::string::npos, url.find("h=240"));
  TEST_ASSERT_NOT_EQUAL(std::string::npos, url.find("depth=16"));
  TEST_ASSERT_NOT_EQUAL(std::string::npos, url.find("components=radar"));
  TEST_ASSERT_NOT_EQUAL(std::string::npos, url.find("fw=hs-0.1"));
  TEST_ASSERT_EQUAL(std::string::npos, url.find("https://"));
}

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
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 3.1f, a.pos_age_s);
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 7.4f, a.dst_nm);
  TEST_ASSERT_EQUAL_STRING("IBE3221", a.callsign);
  TEST_ASSERT_EQUAL_STRING("A320", a.type);
  TEST_ASSERT_EQUAL_STRING("3675 ft", a.alt);
}

void test_velocities_come_from_the_server_and_are_not_recomputed(void) {
  // The server already resolved track+gs into east/north km/s. Recomputing
  // here would silently diverge the moment the server's mapping changes -- and
  // the fixture's ve/vn are deliberately NOT consistent with gs=400kt.
  poll(kWireAssigned);
  TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.13f, aircraftList()[0].vel_e_km_s);
}

void test_the_scene_and_its_metadata_are_available(void) {
  poll(kWireAssigned);
  TEST_ASSERT_TRUE(assigned());
  TEST_ASSERT_EQUAL_STRING("planes", sceneName());
  TEST_ASSERT_FLOAT_WITHIN(0.01f, 60.0f, radiusKm());
  TEST_ASSERT_TRUE(feedOk());
}

void test_an_unassigned_device_says_what_to_do_instead_of_showing_nothing(void) {
  TEST_ASSERT_TRUE(poll(kWireUnassigned));
  TEST_ASSERT_FALSE(assigned());
  TEST_ASSERT_EQUAL_UINT(0, aircraftCount());
  TEST_ASSERT_TRUE(strlen(message()) > 0);
}

void test_the_poll_cadence_comes_from_the_server(void) {
  poll(kWireAssigned, HTTP_CODE_OK, "\"x\"", "30");
  TEST_ASSERT_EQUAL_UINT32(30000, pollIntervalMs());
}

void test_an_absurd_cadence_is_clamped_not_obeyed(void) {
  // Trusted, not obeyed blindly: 0 would be a flood, 86400 a brick.
  poll(kWireAssigned, HTTP_CODE_OK, "\"x\"", "0");
  TEST_ASSERT_EQUAL_UINT32(config::kPollMinMs, pollIntervalMs());
  poll(kWireAssigned, HTTP_CODE_OK, "\"y\"", "999999");
  TEST_ASSERT_EQUAL_UINT32(config::kPollMaxMs, pollIntervalMs());
  poll(kWireAssigned, HTTP_CODE_OK, "\"z\"", "not-a-number");
  TEST_ASSERT_EQUAL_UINT32(config::kPollDefaultMs, pollIntervalMs());
}

void test_the_etag_is_sent_back_on_the_next_poll(void) {
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  TEST_ASSERT_EQUAL_STRING(kWireAssignedEtag, g_http.last_if_none_match.c_str());
}

void test_a_304_keeps_the_list_and_is_not_a_failure(void) {
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  TEST_ASSERT_EQUAL_UINT(2, aircraftCount());
  TEST_ASSERT_TRUE(poll("", HTTP_CODE_NOT_MODIFIED, kWireAssignedEtag));
  TEST_ASSERT_EQUAL_UINT(2, aircraftCount());
  TEST_ASSERT_EQUAL_STRING("IBE3221", aircraftList()[0].callsign);
}

void test_a_304_freezes_the_content_clock_and_refreshes_the_contact_clock(void) {
  // PLAN.md section 3, the requirement that survived the protocol change. The
  // fix really is as old as the last 200, so extrapolation and the 12s dim test
  // must keep ageing -- but we DID just hear from the server, so the 60s
  // "drop the picture" expiry must not fire.
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  mockAdvanceMs(30000);
  poll("", HTTP_CODE_NOT_MODIFIED, kWireAssignedEtag);
  mockAdvanceMs(40000);
  poll("", HTTP_CODE_NOT_MODIFIED, kWireAssignedEtag);

  TEST_ASSERT_FLOAT_WITHIN(0.5f, 70.0f, secondsSinceContentRaw());
  TEST_ASSERT_FLOAT_WITHIN(0.01f, kExtrapolationHorizonSec, secondsSinceContent());
  TEST_ASSERT_FALSE(contentExpired());
}

void test_silence_from_the_server_does_expire_the_picture(void) {
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  mockAdvanceMs(61000);
  TEST_ASSERT_TRUE(contentExpired());
}

void test_a_304_does_not_reparse_the_body(void) {
  // It has no body. Parsing "" would clear the list and blank the screen.
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  const int before = g_http.get_calls;
  poll("", HTTP_CODE_NOT_MODIFIED, kWireAssignedEtag);
  TEST_ASSERT_EQUAL_INT(before + 1, g_http.get_calls);
  TEST_ASSERT_EQUAL_UINT(2, aircraftCount());
}

void test_an_http_error_keeps_the_last_good_scene(void) {
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  TEST_ASSERT_FALSE(poll("", 500));
  TEST_ASSERT_EQUAL_UINT(2, aircraftCount());
  TEST_ASSERT_FALSE(poll("", HTTPC_ERROR_CONNECTION_REFUSED));
  TEST_ASSERT_EQUAL_UINT(2, aircraftCount());
}

void test_a_body_that_is_not_a_scene_is_rejected_rather_than_believed(void) {
  // An HTML captive-portal page, a bare null, or a truncated body must not read
  // as "empty sky": that wipes real traffic AND refreshes the clocks, so the
  // expiry never fires either.
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  for (const char* junk : {"<html>nope</html>", "null", "42", "{\"a\":1}",
                           "{\"components\":5}", ""}) {
    TEST_ASSERT_FALSE(poll(junk));
    TEST_ASSERT_EQUAL_UINT(2, aircraftCount());
  }
}

void test_a_scene_with_no_radar_component_clears_the_sky(void) {
  // Not the same as a bad body: the server said, in a well-formed scene, that
  // this device is showing something else now.
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  TEST_ASSERT_TRUE(poll(kWireUnassigned));
  TEST_ASSERT_EQUAL_UINT(0, aircraftCount());
}

void test_more_aircraft_than_we_have_room_for_are_truncated_not_overflowed(void) {
  std::string big = "{\"scene\":\"planes\",\"assigned\":true,\"layout\":\"fill\","
                    "\"components\":[{\"c\":\"radar\",\"items\":[";
  for (int i = 0; i < kMaxAircraft + 20; ++i) {
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

void test_an_overlong_callsign_cannot_overrun_the_tag_buffer(void) {
  // The callsign originates at a third-party feed. ASan makes this an abort
  // naming the line rather than a silent corruption.
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
  // inf/nan through the projection produces a coordinate that is neither on
  // screen nor off it, and the clip maths does not converge.
  TEST_ASSERT_TRUE(poll("{\"scene\":\"planes\",\"assigned\":true,"
                        "\"layout\":\"fill\",\"components\":[{\"c\":\"radar\","
                        "\"items\":[{\"lat\":1e400,\"lon\":2.0,\"cs\":\"BAD\"},"
                        "{\"lat\":1.0,\"lon\":2.0,\"ve\":1e400,\"cs\":\"V\"},"
                        "{\"lat\":3.0,\"lon\":4.0,\"cs\":\"OK\"}]}]}"));
  for (size_t i = 0; i < aircraftCount(); ++i) {
    TEST_ASSERT_TRUE(std::isfinite(aircraftList()[i].lat));
    TEST_ASSERT_TRUE(std::isfinite(aircraftList()[i].lon));
    TEST_ASSERT_TRUE(std::isfinite(aircraftList()[i].vel_e_km_s));
  }
}

void test_the_reader_lock_is_released_even_when_a_poll_fails(void) {
  poll(kWireAssigned);
  TEST_ASSERT_TRUE(aircraftLock(10));
  aircraftUnlock();
  poll("", 500);
  TEST_ASSERT_TRUE(aircraftLock(10));
  aircraftUnlock();
  TEST_ASSERT_EQUAL_INT(0, g_mutex_outstanding);
}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_the_request_declares_everything_the_server_needs);
  RUN_TEST(test_a_real_server_body_becomes_aircraft);
  RUN_TEST(test_velocities_come_from_the_server_and_are_not_recomputed);
  RUN_TEST(test_the_scene_and_its_metadata_are_available);
  RUN_TEST(test_an_unassigned_device_says_what_to_do_instead_of_showing_nothing);
  RUN_TEST(test_the_poll_cadence_comes_from_the_server);
  RUN_TEST(test_an_absurd_cadence_is_clamped_not_obeyed);
  RUN_TEST(test_the_etag_is_sent_back_on_the_next_poll);
  RUN_TEST(test_a_304_keeps_the_list_and_is_not_a_failure);
  RUN_TEST(test_a_304_freezes_the_content_clock_and_refreshes_the_contact_clock);
  RUN_TEST(test_silence_from_the_server_does_expire_the_picture);
  RUN_TEST(test_a_304_does_not_reparse_the_body);
  RUN_TEST(test_an_http_error_keeps_the_last_good_scene);
  RUN_TEST(test_a_body_that_is_not_a_scene_is_rejected_rather_than_believed);
  RUN_TEST(test_a_scene_with_no_radar_component_clears_the_sky);
  RUN_TEST(test_more_aircraft_than_we_have_room_for_are_truncated_not_overflowed);
  RUN_TEST(test_an_item_without_a_position_is_skipped);
  RUN_TEST(test_an_overlong_callsign_cannot_overrun_the_tag_buffer);
  RUN_TEST(test_a_non_finite_number_never_reaches_the_renderer);
  RUN_TEST(test_the_reader_lock_is_released_even_when_a_poll_fails);
  return UNITY_END();
}
```

- [ ] **Step 5: Run to see it fail**

Run: `cd firmware && pio test -e native -f test_scene_client`
Expected: FAIL — `scene_client.cpp` does not exist.

- [ ] **Step 6: Write the header**

```cpp
// firmware/include/services/scene_client.h
#pragma once

#include <cstddef>
#include <cstdint>

namespace services::scene {

/**
 * One target on the radar. Field-for-field the reference firmware's `Aircraft`,
 * so radar_display.cpp ports across unmodified -- the values now arrive
 * already resolved from the server instead of being derived here.
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
   * Age of this position in seconds, as the server serves it -- upstream's
   * seen_pos PLUS the time the record sat in the server's cache. Dead
   * reckoning runs from when the fix was taken, not from when we fetched it.
   */
  float pos_age_s;
  /** Distance from the radar centre (NM); < 0 if absent. */
  float dst_nm;
  char callsign[9];
  char type[5];
  char alt[12];
};

constexpr size_t kMaxAircraft = 64;

/**
 * Dead-reckoning horizon. Shared so the drawn position (clamped to it) and the
 * stale flag (tested against it) can never be judged by different numbers.
 */
constexpr float kExtrapolationHorizonSec = 12.0f;

/** One HTTP exchange. True on 200 or 304; false leaves everything untouched. */
bool pollOnce();

/** Start the background poll task. */
bool startPollTask();
unsigned pollTaskStackFree();

size_t aircraftCount();
const Aircraft* aircraftList();
bool aircraftLock(uint32_t timeout_ms);
void aircraftUnlock();
bool hasTraffic();

/**
 * Seconds since the last 200 that CHANGED the content, clamped to the horizon.
 * A 304 does not refresh it: the fix really is that old.
 */
float secondsSinceContent();
/** The same, unclamped, for the staleness test. */
float secondsSinceContentRaw();
/**
 * True once the content is old enough that it must not be shown at all. Driven
 * by the CONTACT clock, which a 304 does refresh -- we heard from the server,
 * so the picture is current even though the bytes did not change.
 */
bool contentExpired();

unsigned long pollIntervalMs();
bool assigned();
const char* sceneName();
/** Server-supplied Spanish text for an unassigned or failed scene. */
const char* message();
/** Coverage radius of the feed behind this component, km. 0 if unstated. */
float radiusKm();
bool feedOk();
/** True once at least one 200 has been parsed since boot. */
bool everReceived();

#ifdef UNIT_TEST
/** Host tests only: forget everything between cases. */
void resetForTest();
#endif

}  // namespace services::scene
```

- [ ] **Step 7: Implement `scene_client.cpp`**

Write `firmware/src/services/scene_client.cpp` implementing the header. The shape,
with the decisions that the tests above pin:

```cpp
#include "services/scene_client.h"

#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFi.h>

#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

#include <cmath>
#include <cstdio>
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
 *   s_content_ms  last 200 that changed the picture. FROZEN by a 304.
 *                 Drives extrapolation and the 12s dim test -- the fix really
 *                 is as old as the last 200.
 *   s_contact_ms  last successful exchange, 200 OR 304. Drives the 60s expiry
 *                 -- we did just hear from the server, so the picture is
 *                 current and must not be dropped.
 * Collapsing these into one field is the bug this comment exists to prevent:
 * one way dims every target at 12s with perfectly current data, the other
 * shows minutes-old traffic as live.
 */
unsigned long s_content_ms = 0;
unsigned long s_contact_ms = 0;
bool s_ever_received = false;

char s_etag[40] = {0};
char s_scene[24] = {0};
char s_message[80] = {0};
bool s_assigned = false;
bool s_feed_ok = false;
float s_radius_km = 0.0f;
unsigned long s_poll_ms = config::kPollDefaultMs;

constexpr float kDataExpirySec = 60.0f;
constexpr unsigned long kRequestTimeoutMs = 8000;
constexpr int kConnectAttemptMs = 400;
/** Plain HTTP: no handshake, so a far smaller stack than the TLS client needed. */
constexpr uint32_t kPollTaskStackBytes = 6144;

WiFiClient s_client;
HTTPClient s_http;

void buildUrl(char* out, size_t len) {
  snprintf(out, len,
           "%s/api/device/%s/scene?w=%d&h=%d&depth=%d&components=radar"
           "&fw=%s&uptime=%lu&rssi=%d",
           server::baseUrl(), deviceId(), config::kDisplayWidth,
           config::kDisplayHeight, config::kDisplayDepth,
           config::kFirmwareVersion, millis() / 1000UL,
           static_cast<int>(WiFi.RSSI()));
}

bool readFloat(JsonObjectConst obj, const char* key, float* out) {
  if (!obj[key].is<float>() && !obj[key].is<double>() && !obj[key].is<int>()) {
    return false;
  }
  const float v = obj[key].as<float>();
  if (!std::isfinite(v)) {
    return false;      // inf/nan through the projection does not converge
  }
  *out = v;
  return true;
}

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

void publish(uint8_t back, size_t count) { /* swap under s_mutex */ }

void applyPollHeader(const String& value) {
  // Trusted, not obeyed: 0 is a flood and 86400 is a brick.
  char* end = nullptr;
  const long seconds = strtol(value.c_str(), &end, 10);
  if (end == value.c_str() || *end != '\0') {
    s_poll_ms = config::kPollDefaultMs;
    return;
  }
  unsigned long ms = static_cast<unsigned long>(seconds) * 1000UL;
  if (ms < config::kPollMinMs) ms = config::kPollMinMs;
  if (ms > config::kPollMaxMs) ms = config::kPollMaxMs;
  s_poll_ms = ms;
}

}  // namespace

}  // namespace

bool pollOnce() {
  if (WiFi.status() != WL_CONNECTED) {
    return false;
  }
  char url[224];
  buildUrl(url, sizeof(url));

  HTTPClient& http = s_http;
  if (!http.begin(s_client, url)) {
    DEBUG_LOG("poll: begin failed");
    return false;
  }
  http.setConnectTimeout(kConnectAttemptMs);
  http.setTimeout(kRequestTimeoutMs);
  static const char* kWanted[] = {"ETag", "X-Poll-Seconds"};
  http.collectHeaders(kWanted, 2);
  if (s_etag[0] != '\0') {
    http.addHeader("If-None-Match", s_etag);
  }

  const int code = http.GET();

  // 304 FIRST, and off the failure path. In the reference firmware this fell
  // into the `code != HTTP_CODE_OK` branch and was counted as an error; here it
  // is the normal case for a quiet sky, and the single most important thing it
  // must NOT do is touch the buffers or the content clock.
  if (code == HTTP_CODE_NOT_MODIFIED) {
    s_contact_ms = millis();
    applyPollHeader(http.header("X-Poll-Seconds"));
    http.end();
    DEBUG_LOG("poll: 304, holding %u aircraft",
              static_cast<unsigned>(s_counts[s_front]));
    return true;
  }
  if (code != HTTP_CODE_OK) {
    Serial.printf("poll: HTTP %d\n", code);
    http.end();
    return false;
  }

  WiFiClient* body = http.getStreamPtr();
  if (body == nullptr) {
    http.end();
    return false;
  }
  JsonDocument doc;
  const DeserializationError err = deserializeJson(doc, *body);
  http.end();
  if (err) {
    Serial.printf("poll: JSON parse error: %s\n", err.c_str());
    return false;
  }

  // Insist on the shape. A captive-portal HTML page, a bare `null` and a plain
  // number all deserialize "Ok" into a document whose ["components"] is null.
  // Treating that as an empty scene would wipe real traffic off the panel AND
  // refresh both clocks, so the 60 s expiry would never fire either -- the
  // screen would sit there showing nothing, reporting itself healthy.
  if (!doc["components"].is<JsonArrayConst>()) {
    DEBUG_LOG("poll: rejected -- body is not a scene");
    Serial.println("poll: 'components' missing or not an array");
    return false;
  }

  s_assigned = doc["assigned"].is<bool>() && doc["assigned"].as<bool>();
  copyTrimmed(doc.as<JsonObjectConst>(), "scene", s_scene, sizeof(s_scene));
  copyTrimmed(doc.as<JsonObjectConst>(), "message", s_message, sizeof(s_message));

  const uint8_t back = s_front ^ 1;
  Aircraft* out = s_buffers[back];
  size_t n = 0;
  s_radius_km = 0.0f;
  s_feed_ok = false;

  for (JsonObjectConst comp : doc["components"].as<JsonArrayConst>()) {
    if (!comp["c"].is<const char*>() ||
        strcmp(comp["c"].as<const char*>(), "radar") != 0) {
      continue;                       // a component this build does not draw
    }
    readFloat(comp, "radius_km", &s_radius_km);
    s_feed_ok = comp["feed_ok"].is<bool>() && comp["feed_ok"].as<bool>();
    if (!comp["items"].is<JsonArrayConst>()) {
      break;
    }
    for (JsonObjectConst item : comp["items"].as<JsonArrayConst>()) {
      if (n >= kMaxAircraft) {
        break;                        // truncate; never overrun
      }
      float lat = 0.0f;
      float lon = 0.0f;
      if (!readFloat(item, "lat", &lat) || !readFloat(item, "lon", &lon)) {
        continue;                     // no position, nothing to plot
      }
      Aircraft& a = out[n];
      a = Aircraft{};
      a.lat = lat;
      a.lon = lon;
      readFloat(item, "nose", &a.nose_deg);
      readFloat(item, "trk", &a.track_deg);
      readFloat(item, "gs", &a.gs_knots);
      // ve/vn arrive already resolved into km/s east/north: the server does
      // that once per fetch so the render loop needs no trig per frame.
      // Recomputing them here would diverge silently from the server's mapping.
      readFloat(item, "ve", &a.vel_e_km_s);
      readFloat(item, "vn", &a.vel_n_km_s);
      readFloat(item, "age", &a.pos_age_s);
      a.dst_nm = -1.0f;
      readFloat(item, "dst", &a.dst_nm);
      copyTrimmed(item, "cs", a.callsign, sizeof(a.callsign));
      copyTrimmed(item, "ty", a.type, sizeof(a.type));
      copyTrimmed(item, "alt", a.alt, sizeof(a.alt));
      ++n;
    }
    break;                            // one radar component per scene
  }

  publish(back, n);
  const unsigned long now = millis();
  s_content_ms = now;                 // the picture changed...
  s_contact_ms = now;                 // ...and we heard from the server
  s_ever_received = true;
  const String etag = http.header("ETag");
  strncpy(s_etag, etag.c_str(), sizeof(s_etag) - 1);
  s_etag[sizeof(s_etag) - 1] = '\0';
  applyPollHeader(http.header("X-Poll-Seconds"));
  DEBUG_LOG("poll: scene=%s %u aircraft", s_scene, static_cast<unsigned>(n));
  return true;
}

}  // namespace services::scene
```

`resetForTest()` (compiled only under `UNIT_TEST`, which the `native` env defines) zeroes
every static above — both clocks, `s_ever_received`, `s_etag`, `s_scene`, `s_message`,
`s_counts`, `s_front`, `s_assigned`, `s_feed_ok`, `s_radius_km` — and sets `s_poll_ms`
back to `config::kPollDefaultMs`. Without it the host tests leak state between cases and
each one passes or fails depending on what ran before it.

The accessors are one-liners over the statics above. `secondsSinceContent()` returns
`min((millis() - s_content_ms) / 1000.0f, kExtrapolationHorizonSec)`,
`secondsSinceContentRaw()` the same unclamped, and `contentExpired()` is
`(millis() - s_contact_ms) / 1000.0f >= kDataExpirySec` — note it reads the **contact**
clock, which is the whole point of there being two.

`startPollTask()` mirrors the reference firmware's `startFetchTask()`: create the mutex
if absent, then `xTaskCreate` a loop that calls `pollOnce()` and then delays
`pollIntervalMs()` on success or `config::kPollErrorMs` on failure. `kPollTaskStackBytes`
is 6144 rather than the reference's 8192 because there is no TLS handshake on this
stack; confirm with `pollTaskStackFree()` on hardware in Task 8 before trusting it.

- [ ] **Step 8: Run the tests green**

Run: `cd firmware && pio test -e native -f test_scene_client`
Expected: 20 tests pass.

- [ ] **Step 9: Prove the two clocks are really two**

```bash
cd firmware
cp src/services/scene_client.cpp /tmp/sc.bak
# Collapse them, as the reference firmware has them:
sed -i '' 's/s_contact_ms = millis();/s_contact_ms = millis(); s_content_ms = millis();/' src/services/scene_client.cpp
pio test -e native -f test_scene_client    # MUST fail
cp /tmp/sc.bak src/services/scene_client.cpp
pio test -e native -f test_scene_client    # green again
```
Expected: the mutated build fails
`test_a_304_freezes_the_content_clock_and_refreshes_the_contact_clock`. If it passes,
the test is not testing what it claims and must be fixed before moving on.

- [ ] **Step 10: Commit**

```bash
cd /Users/matias/Documents/repos/HomeScreen
git add firmware/ scripts/dump_wire_fixture.py tests/test_wire_contract.py
git commit -m "feat(firmware): scene client -- plain HTTP, 304, and the two clocks"
```

---

## Task 4: Port the radar renderer and drive it from the scene

**Files:**
- Copy: `firmware/include/ui/{radar_display,radar_geo,radar_range,radar_theme,render_policy}.h`
- Copy: `firmware/src/ui/{radar_display,radar_geo,radar_range}.cpp`
- Copy: `firmware/include/hardware/{display.h,lgfx_config.hpp}`,
  `firmware/src/hardware/{display,display_font}.cpp`
- Copy: `firmware/include/services/radar_location.h`,
  `firmware/src/services/radar_location.cpp`
- Copy: `firmware/test/{test_geo,test_display,test_render_policy,test_settings}/`,
  `firmware/test/fixtures_geo.h`
- Modify: the copied `radar_display.cpp` — one include and one namespace change
- Test: `firmware/test/test_display/test_display.cpp` (copied, then extended)

**Interfaces:**
- Consumes: `services::scene::aircraftList()`, `aircraftCount()`, `aircraftLock()`,
  `secondsSinceContent()`, `secondsSinceContentRaw()`, `contentExpired()`,
  `kExtrapolationHorizonSec`.
- Produces: `ui::radarDisplayReserveFrame()`, `ui::radarDisplayDraw()`,
  `ui::radarDisplayRefreshAircraft()` — unchanged signatures.

- [ ] **Step 1: Copy the renderer and its tests verbatim**

```bash
cd /Users/matias/Documents/repos/HomeScreen
REF=/Users/matias/Documents/repos/ESP32-Plane-Radar
for f in ui/radar_display.h ui/radar_geo.h ui/radar_range.h ui/radar_theme.h \
         ui/render_policy.h hardware/display.h hardware/lgfx_config.hpp \
         services/radar_location.h; do cp $REF/include/$f firmware/include/$f; done
for f in ui/radar_display.cpp ui/radar_geo.cpp ui/radar_range.cpp \
         hardware/display.cpp hardware/display_font.cpp \
         services/radar_location.cpp; do cp $REF/src/$f firmware/src/$f; done
cp $REF/test/fixtures_geo.h firmware/test/
cp -R $REF/test/test_geo $REF/test/test_display $REF/test/test_render_policy \
      $REF/test/test_settings firmware/test/
git -C $REF status --porcelain | wc -l    # must print 0
```

- [ ] **Step 2: Repoint the copied renderer at the scene client**

In `firmware/src/ui/radar_display.cpp` only these substitutions — no other edits:

```bash
cd firmware
sed -i '' \
  -e 's|#include "services/adsb_client.h"|#include "services/scene_client.h"|' \
  -e 's|services::adsb::|services::scene::|g' \
  -e 's|secondsSinceUpdateRaw()|secondsSinceContentRaw()|g' \
  -e 's|secondsSinceUpdate()|secondsSinceContent()|g' \
  -e 's|dataExpired()|contentExpired()|g' \
  src/ui/radar_display.cpp
grep -n "adsb" src/ui/radar_display.cpp    # must print nothing
```

Apply the same substitutions to the copied `test/test_display/test_display.cpp` and
`test/test_geo/test_geo.cpp`, and change their `#include "../../src/services/adsb_client.cpp"`
to `#include "../../src/services/scene_client.cpp"`.

- [ ] **Step 3: Run the copied tests**

Run: `cd firmware && pio test -e native`
Expected: all pass. Any failure here is a porting mistake, not a design question —
the renderer is unchanged code being fed the same struct.

- [ ] **Step 4: Add the test that the range presets no longer size the request**

```cpp
// append to firmware/test/test_display/test_display.cpp
void test_the_range_preset_no_longer_changes_what_is_fetched(void) {
  // Behaviour change worth pinning: the server decides the feed radius now.
  // The BOOT button still changes the DISPLAY scale instantly and locally
  // (ADDENDUM section 2), but it no longer alters the request -- so a preset
  // wider than the server's radius_km shows an empty rim rather than more
  // traffic, and that is expected rather than a bug to chase on hardware.
  services::server::saveFromString("192.168.1.116:8080");
  services::server::load();
  ui::radar::rangeInit();
  g_http.reset();
  g_http.body = kWireAssigned;
  g_http.code = HTTP_CODE_OK;
  services::scene::pollOnce();
  const std::string first = g_http.last_url;
  ui::radar::rangeNext();
  g_http.reset();
  g_http.body = kWireAssigned;
  g_http.code = HTTP_CODE_OK;
  services::scene::pollOnce();
  TEST_ASSERT_EQUAL_STRING(first.c_str(), g_http.last_url.c_str());
}
```

- [ ] **Step 5: Run it green, then commit**

Run: `cd firmware && pio test -e native`
Expected: all pass.

```bash
cd /Users/matias/Documents/repos/HomeScreen
git add firmware/
git commit -m "feat(firmware): port the GC9A01 radar renderer, driven by the scene"
```

---

## Task 5: The component dispatcher

The radar is the only component today. The dispatcher exists now so that adding `text`
next is a new file and a `case`, not surgery on the render loop.

**Files:**
- Create: `firmware/include/ui/components.h`, `firmware/src/ui/components.cpp`
- Test: `firmware/test/test_components/test_components.cpp`

**Interfaces:**
- Consumes: `services::scene::sceneName()`, `assigned()`, `message()`, and the renderer
  entry points from Task 4.
- Produces:
  - `enum class ui::ComponentKind { kNone, kRadar, kUnknown };`
  - `ui::ComponentKind ui::componentKindFromName(const char* c);`
  - `const char* ui::kDeclaredComponents` — the exact string sent as `components=`.
  - `bool ui::renderScene()` — draws whatever the scene client currently holds.

- [ ] **Step 1: Write the failing dispatcher test**

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
#include "../../src/ui/components.cpp"

void setUp(void) {
  g_nvs.reset(); mockSetMs(100000); g_gfx.reset();
  WiFi.status_ = WL_CONNECTED;
  services::server::saveFromString("192.168.1.116:8080");
  services::server::load();
  services::scene::resetForTest();
}
void tearDown(void) {}

void test_the_declared_component_list_matches_what_we_can_actually_draw(void) {
  // The server DROPS components we did not declare, and reports the omission
  // on /home. Declaring something we cannot draw would put a hole on the glass
  // and nothing in the fleet view -- the silent failure section 5.5 exists to
  // prevent. Declaring less than we can draw wastes a component.
  TEST_ASSERT_EQUAL_STRING("radar", ui::kDeclaredComponents);
  TEST_ASSERT_EQUAL(ui::ComponentKind::kRadar,
                    ui::componentKindFromName("radar"));
  TEST_ASSERT_EQUAL(ui::ComponentKind::kUnknown,
                    ui::componentKindFromName("text"));
  TEST_ASSERT_EQUAL(ui::ComponentKind::kNone, ui::componentKindFromName(""));
  TEST_ASSERT_EQUAL(ui::ComponentKind::kNone, ui::componentKindFromName(nullptr));
}

void test_an_assigned_radar_scene_draws_the_radar(void) {
  g_http.reset(); g_http.body = kWireAssigned; g_http.code = HTTP_CODE_OK;
  services::scene::pollOnce();
  TEST_ASSERT_TRUE(ui::renderScene());
  TEST_ASSERT_TRUE(g_gfx.drew_circle);
}

void test_an_unassigned_device_shows_the_servers_message_not_a_blank_screen(void) {
  // Spec section 6.1: a newly flashed board tells you what to type into the
  // fleet view. A blank round screen is indistinguishable from a dead one.
  g_http.reset(); g_http.body = kWireUnassigned; g_http.code = HTTP_CODE_OK;
  services::scene::pollOnce();
  TEST_ASSERT_TRUE(ui::renderScene());
  TEST_ASSERT_TRUE(g_gfx.textContains("sin asignar"));
}

void test_a_device_that_has_never_reached_the_server_says_so(void) {
  TEST_ASSERT_TRUE(ui::renderScene());
  TEST_ASSERT_TRUE(g_gfx.textContains("SIN SERVIDOR"));
}

void test_an_expired_picture_is_dropped_rather_than_shown_as_live(void) {
  g_http.reset(); g_http.body = kWireAssigned; g_http.code = HTTP_CODE_OK;
  services::scene::pollOnce();
  mockAdvanceMs(61000);
  ui::renderScene();
  TEST_ASSERT_FALSE(g_gfx.textContains("IBE3221"));
}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_the_declared_component_list_matches_what_we_can_actually_draw);
  RUN_TEST(test_an_assigned_radar_scene_draws_the_radar);
  RUN_TEST(test_an_unassigned_device_shows_the_servers_message_not_a_blank_screen);
  RUN_TEST(test_a_device_that_has_never_reached_the_server_says_so);
  RUN_TEST(test_an_expired_picture_is_dropped_rather_than_shown_as_live);
  return UNITY_END();
}
```

- [ ] **Step 2: Extend the GfxRecorder mock with a text search**

Add to `firmware/test/mocks/LovyanGFX.hpp`, inside `struct GfxRecorder`:

```cpp
  std::vector<std::string> strings;
  bool drew_circle = false;
  bool textContains(const char* needle) const {
    for (const auto& s : strings) {
      if (s.find(needle) != std::string::npos) return true;
    }
    return false;
  }
```
Record into `strings` from the mock's `drawString`, and set `drew_circle` in
`drawCircle`. Extend `reset()` to clear both.

- [ ] **Step 3: Run to see it fail**

Run: `cd firmware && pio test -e native -f test_components`
Expected: FAIL — `components.cpp` does not exist.

- [ ] **Step 4: Implement the dispatcher**

```cpp
// firmware/include/ui/components.h
#pragma once

namespace ui {

/**
 * What this firmware can draw. `kDeclaredComponents` is sent to the server on
 * every poll as `components=`; the server drops anything a scene asks for that
 * is not in this list and reports the omission in the fleet view (spec 5.5).
 * So this string and the switch in renderScene() must never disagree: declaring
 * more than we draw puts a hole on the glass and nothing in the fleet view.
 */
enum class ComponentKind { kNone, kRadar, kUnknown };

inline constexpr char kDeclaredComponents[] = "radar";

ComponentKind componentKindFromName(const char* c);

/**
 * Draw whatever the scene client currently holds. Returns false if the frame
 * could not be composed (the aircraft list was locked); the caller retries
 * rather than latching, or a skipped clearing frame strands the last targets.
 */
bool renderScene();

}  // namespace ui
```

`renderScene()` decides, in this order:
1. `!services::scene::everReceived()` → the "SIN SERVIDOR" screen. A device that has
   never reached the Pi must say so; a blank round screen looks identical to a dead one.
2. `!services::scene::assigned()` → the status screen showing `deviceId()` and the
   server's `message()`.
3. `services::scene::contentExpired()` → rings only, no targets.
4. otherwise → `switch (componentKindFromName(...))`, `kRadar` →
   `ui::radarDisplayRefreshAircraft()`.

- [ ] **Step 5: Run green and commit**

Run: `cd firmware && pio test -e native`
Expected: all pass.

```bash
git add firmware/ && git commit -m "feat(firmware): component dispatcher with the radar as its first case"
```

---

## Task 6: Provisioning, status screens and the real main loop

**Files:**
- Copy: `firmware/include/ui/status_screens.h`, `firmware/src/ui/status_screens.cpp`
- Copy: `firmware/include/services/wifi_setup.h`, `firmware/src/services/wifi_setup.cpp`
- Copy: `firmware/test/test_wifi/`, `firmware/test/test_main/`
- Modify: the copied `wifi_setup.cpp` — add the server-address portal field
- Modify: `firmware/src/main.cpp` — the real loop
- Test: `firmware/test/test_wifi/test_wifi.cpp` (extended)

**Interfaces:**
- Consumes: everything from Tasks 2–5.
- Produces: a firmware that boots, provisions, polls and renders.

- [ ] **Step 1: Copy provisioning and its tests**

```bash
cd /Users/matias/Documents/repos/HomeScreen
REF=/Users/matias/Documents/repos/ESP32-Plane-Radar
cp $REF/include/ui/status_screens.h firmware/include/ui/
cp $REF/src/ui/status_screens.cpp firmware/src/ui/
cp $REF/include/services/wifi_setup.h firmware/include/services/
cp $REF/src/services/wifi_setup.cpp firmware/src/services/
cp -R $REF/test/test_wifi $REF/test/test_main firmware/test/
git -C $REF status --porcelain | wc -l    # must print 0
```

- [ ] **Step 2: Add the failing portal test**

```cpp
// append to firmware/test/test_wifi/test_wifi.cpp
void test_the_portal_offers_a_server_address_field(void) {
  // How a freshly flashed board learns where the Pi is. Without it the only
  // way to point a device at a server is a recompile -- which is the thing
  // this whole phase exists to stop.
  wifiSetupConnect();
  TEST_ASSERT_TRUE(g_wm.hasParameter("server"));
}

void test_a_saved_server_address_is_persisted_and_used(void) {
  g_wm.setParameterValue("server", "192.168.1.116:8080");
  wifiSaveParamsCallbackForTest();
  services::server::load();
  TEST_ASSERT_EQUAL_STRING("192.168.1.116", services::server::host());
  TEST_ASSERT_EQUAL_UINT16(8080, services::server::port());
}

void test_a_rejected_server_address_does_not_wipe_the_working_one(void) {
  services::server::saveFromString("192.168.1.116");
  g_wm.setParameterValue("server", "https://nope");
  wifiSaveParamsCallbackForTest();
  services::server::load();
  TEST_ASSERT_EQUAL_STRING("192.168.1.116", services::server::host());
}
```

- [ ] **Step 3: Add the portal field**

In `firmware/src/services/wifi_setup.cpp`, alongside the existing `s_param_lat` etc.:

```cpp
WiFiManagerParameter s_param_server(
    "server", "HomeScreen server (host or host:port)", "", 64,
    " placeholder=\"192.168.1.116:8080\"");
```
Register it with `wm.addParameter(&s_param_server)` before the location fields — it is
the field a new board most needs — and in the save callback:

```cpp
  // Refused values leave the stored one alone: a typo in the portal must not
  // strand a working device with no server.
  if (!services::server::saveFromString(s_param_server.getValue())) {
    Serial.println("wifi: server address rejected; keeping the previous one");
  }
```

- [ ] **Step 4: Run the portal tests green**

Run: `cd firmware && pio test -e native -f test_wifi`
Expected: all pass.

- [ ] **Step 5: Write the real `main.cpp`**

Same shape as the reference firmware's loop, with three changes: `services::server::load()`
before Wi-Fi; `services::scene::startPollTask()` instead of the ADS-B task; and the render
call goes through `ui::renderScene()` rather than straight to the radar. The
`RenderPolicy`, the BOOT-button handling, the reconnect grace and the frame-reserve
ordering are all copied unchanged — including the comment about reserving the 115 KB
sprite before Wi-Fi, which still applies.

- [ ] **Step 6: Build and run everything**

Run: `cd firmware && pio test -e native && pio run -e c3`
Expected: all tests pass; image builds. Record flash and RAM figures.

- [ ] **Step 7: Commit**

```bash
git add firmware/ && git commit -m "feat(firmware): provisioning with a server field, and the real loop"
```

---

## Task 7: Runway overlay (port verbatim)

**Files:**
- Copy: `firmware/include/ui/runway_overlay.h`, `firmware/src/ui/runway_overlay.cpp`,
  `firmware/src/data/large_airports_data.cpp`, `firmware/test/test_runway_cap/`

**Interfaces:**
- Consumes: `ui::radar::latLonToScreen()`, `ui::radar::rangeCurrent()`.
- Produces: nothing new — `radar_display.cpp` already calls it.

- [ ] **Step 1: Copy**

```bash
cd /Users/matias/Documents/repos/HomeScreen
REF=/Users/matias/Documents/repos/ESP32-Plane-Radar
cp $REF/include/ui/runway_overlay.h firmware/include/ui/
cp $REF/src/ui/runway_overlay.cpp firmware/src/ui/
cp $REF/src/data/large_airports_data.cpp firmware/src/data/
cp -R $REF/test/test_runway_cap firmware/test/
git -C $REF status --porcelain | wc -l    # must print 0
```

- [ ] **Step 2: Run and commit**

Run: `cd firmware && pio test -e native && pio run -e c3`
Expected: all pass; note the flash increase from the 2,884-line airport table.

```bash
git add firmware/ && git commit -m "feat(firmware): port the runway overlay and airport data"
```

---

## Task 8: Bring-up on real hardware

The first task that needs the board. Everything above is verifiable on the Mac.

**Files:**
- Create: `firmware/README.md`, `firmware/OPS.md`
- Modify: `CLAUDE.md` — a `firmware/` section

- [ ] **Step 1: Flash and watch the first boot**

```bash
cd firmware && pio run -e c3 -t upload && pio device monitor
```
Expected: `HomeScreen display client hs-0.1`, then the setup portal
(`HomeScreen-Setup`), because a fresh board has no credentials.

- [ ] **Step 2: Provision through the portal**

Join `HomeScreen-Setup`, set Wi-Fi, and set **server** to `192.168.1.116:8080`.
Expected: the board connects and the screen shows `sin asignar` plus its hardware id.

- [ ] **Step 3: Confirm the server saw it, from the Mac**

```bash
curl -s http://192.168.1.116:8080/api/devices | python3 -m json.tool
```
Expected: one new device, `online: true`, `caps` showing `240x240` `depth 16`
`components: ["radar"]`, `fw: "hs-0.1"`.

- [ ] **Step 4: Assign the radar scene from the Mac and watch the glass**

```bash
HW=<the id from step 3>
curl -s -X PATCH http://192.168.1.116:8080/api/devices/$HW \
  -H 'content-type: application/json' \
  -d '{"name":"radar","scene":"planes"}'
```
Expected: within one poll the display switches from `sin asignar` to the radar with
live traffic. **This is the moment the phase is proving:** the screen changed without
a reflash.

- [ ] **Step 5: Measure what dropping TLS bought**

With `-e c3-debug`, record from the monitor: free heap and largest free block at boot,
after the sprite, and in steady state. Compare against the reference figures in
`OPS.md` (22–28 KB free, ~9 KB largest block). Write both sets into `firmware/OPS.md`.
Expected: a materially larger largest-free-block. If it is not, say so plainly in the
document — the ~33 KB figure is an estimate from the spec, not a measurement, and this
step is what turns it into one.

- [ ] **Step 6: Soak, then check the fleet view**

Leave it running an hour. Then:
```bash
curl -s http://192.168.1.116:8080/api/status | python3 -c \
  'import json,sys; d=json.load(sys.stdin); print(d["memory"]); print(d["fleet"][0])'
```
Expected: `online: true`, a `last_seen` within one poll, telemetry showing a growing
`uptime` and `errors: 0`.

- [ ] **Step 7: Write the docs and commit**

`firmware/README.md`: what the firmware is, how to build, flash and provision, and the
one-line answer to "how do I change what it shows" (`PATCH /api/devices/<hw>`).
`firmware/OPS.md`: the measured heap figures, the poll cadence, and what each status
screen means.

```bash
git add firmware/ CLAUDE.md
git commit -m "docs(firmware): bring-up notes and measured heap figures"
```

---

## What this plan deliberately does not do

- **No `text`, `spark` or `icon` components.** The dispatcher is built for them; adding
  them is the next piece of work, once the radar proves the loop on hardware.
- **No OTA and no watchdog.** Spec §7.6 wants both from day one. They are real, and they
  are a separate plan: OTA needs a partition change and a server endpoint that does not
  exist yet, and neither is on the critical path to proving the protocol.
- **No `grid` layout.** Deferred in the spec (§5.4); every scene is `fill`.
- **No e-paper.** Phase B, different device, pixel-push.
- **The reference firmware is not retired.** It keeps working, on its own repo and its
  own board, until this one has been running for long enough to trust.

---

## Self-review notes

**Spec coverage.** §7.1 loop → Tasks 3 and 6. §7.2 what carries over → Tasks 4, 6, 7.
§7.3 memory → Task 8 step 5, which turns the spec's estimate into a measurement.
§7.4 the 304 clock split → Task 3, with a mutation check at step 9 proving the test
bites. §7.5 capabilities on every call → Task 3 step 4, including the `w`+`h` rule the
server now enforces. §7.6 watchdog and OTA → explicitly deferred above, with reasons.
§6.1 unassigned shows the hw id → Task 5. §6.3 hold the last good scene → Task 3.

**Known gap, stated rather than hidden:** the range presets no longer size the fetch
(Task 4 step 4). A preset wider than the server's `radius_km` will show an empty rim.
That is a real behaviour change from the reference firmware and it is pinned by a test
rather than left to be discovered on hardware.
