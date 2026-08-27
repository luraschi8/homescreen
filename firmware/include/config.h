#pragma once

#include <cstdint>

#include <driver/gpio.h>

namespace config {

/** Sent as `fw` on every request; shown in the fleet view. */
constexpr char kFirmwareVersion[] = "hs-0.1";

/**
 * What this build tells the server it can draw, sent as `components=`.
 *
 * Here, not in ui/components.h, because two places need it -- the dispatcher
 * that switches on it and the URL builder that declares it -- and they were
 * allowed to disagree once already: the dispatcher grew `clock` while the URL
 * still said `radar`, so the server dropped every clock component as
 * undeclared and the panel showed nothing.
 *
 * `radar` is one bespoke renderer. Everything else arrives as an instruction
 * list the firmware executes without knowing what it means, so adding a name
 * here costs one word and no drawing code.
 */
constexpr char kDeclaredComponents[] = "radar,clock";

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
 * Largest scene body we will parse, measured rather than guessed.
 *
 * ArduinoJson 7 peaks around 4.6x the body for this shape. Against the real
 * server, with every float rounded to what a float32 can carry, a full scene
 * measures: 20 items 3,218 B, 30 items 4,746 B, 40 items 6,274 B, 64 items
 * 9,940 B. At kMaxAircraft = 40 the worst case is ~6.3 KB and the peak ~29 KB,
 * against ~55 KB of post-TLS heap. 64 items would peak at ~46 KB, which is too
 * close to the wall for a pool that needs contiguity rather than just total
 * free bytes.
 *
 * 8 KB leaves room above the 6.3 KB worst case for longer callsigns and type
 * codes. Anything larger is a misconfiguration or a chunked response, and both
 * are refused rather than attempted: a NoMemory mid-parse looks exactly like a
 * dead server on the glass.
 */
constexpr int kMaxBodyBytes = 8192;

// --- UI colours (RGB565) — status screens ---
constexpr uint16_t kColorBlack = 0x0000;
constexpr uint16_t kColorYellow = 0xFFE0;
constexpr uint16_t kTextOnYellow = kColorBlack;
constexpr uint16_t kTextOnBlack = 0xFFFF;

}  // namespace config
