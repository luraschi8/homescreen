/**
 * Headless bring-up spike -- NO display code.
 *
 * Exists to test the four assumptions the rest of the phase is built on, before
 * ~800 lines of ported rendering go on top of them:
 *
 *   1. dropping TLS frees the heap the reference's comment claims (~35 KB)
 *   2. ArduinoJson parses a REAL scene body inside the REAL heap, not a Mac's
 *   3. the socket teardown recovers a Pi that went away (D4)
 *   4. the device registers and is served its scene over the real LAN
 *
 * Everything it needs is already implemented and mutation-checked on the host.
 * It reuses the Wi-Fi credentials already in NVS: the partition table is
 * byte-identical to the reference's, so an app-only flash leaves nvs at 0x9000
 * untouched, and the reference stored them with WiFi.persistent(true).
 */
#include <Arduino.h>
#include <WiFi.h>

#include "config.h"
#include "services/device_id.h"
#include "services/scene_client.h"
#include "services/server_config.h"

namespace {

unsigned long g_last_report = 0;
unsigned g_polls = 0;

void reportHeap(const char* stage) {
  Serial.printf("heap %-18s free %6u  largest %6u\n", stage,
                static_cast<unsigned>(ESP.getFreeHeap()),
                static_cast<unsigned>(ESP.getMaxAllocHeap()));
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.println();
  Serial.println("=== HomeScreen headless spike ===");
  Serial.printf("fw %s  hw ", config::kFirmwareVersion);
  reportHeap("at boot");

  WiFi.mode(WIFI_STA);
  // Cap the TX power: OPS.md section 7 records that the Super Mini's regulator
  // browns out at full power and reboot-loops on connect. Same value the
  // reference uses in both its AP and STA paths.
  WiFi.setTxPower(WIFI_POWER_8_5dBm);
  WiFi.begin();                       // credentials already in NVS
  Serial.print("wifi: connecting");
  const unsigned long deadline = millis() + 25000;
  while (WiFi.status() != WL_CONNECTED && millis() < deadline) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("wifi: FAILED -- no stored credentials reachable.");
    Serial.println("      Reflash the reference firmware to recover:");
    Serial.println("      cd ESP32-Plane-Radar && pio run -e supermini -t upload");
    return;
  }
  Serial.printf("wifi: %s  rssi %d  ip %s\n", WiFi.SSID().c_str(), WiFi.RSSI(),
                WiFi.localIP().toString().c_str());
  reportHeap("after wifi");

  services::server::load();
#ifdef HS_SPIKE_SERVER
  // The spike env pins the server so the run does not also depend on mDNS.
  if (strcmp(services::server::host(), config::kDefaultServerHost) == 0) {
    services::server::saveFromString(HS_SPIKE_SERVER);
  }
#endif
  Serial.printf("device: %s -> %s\n", services::deviceId(),
                services::server::baseUrl());

  if (!services::scene::startPollTask()) {
    Serial.println("poll task: FAILED to start");
    return;
  }
  Serial.println("poll task: started");
  reportHeap("after poll task");
  Serial.println("--- polling; stop/start the Pi to test recovery ---");
}

void loop() {
  if (millis() - g_last_report >= 2000) {
    g_last_report = millis();
    ++g_polls;
    Serial.printf(
        "[%3u] items %2u  scene %-12s comp %-6s assigned %d  feed_ok %d  "
        "feed_age %6.1f  content %5.1f  expired %d  poll %lums  stack %u  ",
        g_polls, static_cast<unsigned>(services::scene::aircraftCount()),
        services::scene::sceneName(), services::scene::componentName(),
        services::scene::assigned() ? 1 : 0,
        services::scene::feedOk() ? 1 : 0, services::scene::feedAgeS(),
        services::scene::secondsSinceContentRaw(),
        services::scene::contentExpired() ? 1 : 0,
        services::scene::pollIntervalMs(),
        services::scene::pollTaskStackFree());
    reportHeap("steady");

    // One aircraft, so a human can see the parse produced real values rather
    // than a plausible-looking count.
    if (services::scene::aircraftCount() > 0 &&
        services::scene::aircraftLock(20)) {
      const auto& a = services::scene::aircraftList()[0];
      Serial.printf("      first: %-8s %-4s %-9s lat %.5f lon %.5f "
                    "ve %.5f vn %.5f age %.1f\n",
                    a.callsign, a.type, a.alt, a.lat, a.lon, a.vel_e_km_s,
                    a.vel_n_km_s, a.pos_age_s);
      services::scene::aircraftUnlock();
    }
  }
  delay(50);
}
