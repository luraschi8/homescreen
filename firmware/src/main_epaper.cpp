// The e-paper client: an ESP32-C3 that shows whatever the Pi renders for it.
//
// Pixel push, per CLAUDE.md's rule -- "text layout -> server renders; geometry
// -> device renders". An 800x480 composed dashboard is almost entirely text
// layout, so this binary draws no layout of its own. It fetches a finished
// framebuffer and puts it on the glass.
//
// Except for one thing, which is not a compromise: a panel that cannot reach
// the Pi, or has not been let into the fleet, must say so ON ITS OWN GLASS.
// That is a line of text, and drawing it here is what stops a board that spent
// a year in a drawer from being a white rectangle nobody can diagnose.

#include <Arduino.h>
#include <ArduinoJson.h>
#include <GxEPD2_BW.h>
#include <HTTPClient.h>
#include <WiFi.h>

#include "config.h"
#include "config_epaper.h"
#include "epaper_dirty.h"
#include "epaper_ui.h"
#include "services/device_id.h"
#include "services/server_config.h"
#include "services/wifi_setup.h"

namespace {

using config::epaper::kFrameBytes;
using config::epaper::kHeight;
using config::epaper::kWidth;

// A FULL buffer, not paged. 48000 bytes for GxEPD2 plus 48000 for the fetch
// is 96 KB against 215 KB free with WiFi up -- measured, not assumed -- and it
// buys the thing paging cannot give: an explicit choice of refresh mode per
// draw, which is what clears ghosting.
GxEPD2_BW<GxEPD2_750_T7, GxEPD2_750_T7::HEIGHT> display(
    GxEPD2_750_T7(config::epaper::kPinCs, config::epaper::kPinDc,
                  config::epaper::kPinRst, config::epaper::kPinBusy));

char s_etag[64] = {0};
char s_message[96] = {0};
bool s_assigned = false;
bool s_ever_reached = false;
//: Until the server tells us otherwise -- which it does on every response.
//: CLAUDE.md makes `config.yaml` the source of truth for cadence and
//: `X-Poll-Seconds` its projection, "so cadence changes without reflashing".
//: This client never read the header, so the number below WAS the cadence and
//: no schedule change could reach this panel without a flash.
unsigned long s_poll_ms = 300000UL;

//: The panel is a slow device driving slower glass: a full refresh is 3.68s,
//: Chromium renders 800x480 behind every frame, and e-paper has a finite
//: number of refreshes in it. The clock on this panel shows HH:MM, so nothing
//: it displays can change faster than once a minute -- and the composed page
//: asks to be woken at the next minute BOUNDARY, which is as little as one
//: second away. Refuse to be driven faster than the glass can say anything
//: new, however eager the server is.
constexpr unsigned long kPollMinMs = 60000UL;
constexpr unsigned long kPollMaxMs = 3600000UL;

//: What the server says actually changed. Ghosting is what a partial waveform
//: LEAVES: it is shorter and weaker than a full one so the pigment does not
//: quite arrive, and it is not charge-balanced so residue builds in the
//: capsule and each update moves the particles a little less than the last.
//: Refreshing the full window in partial mode applied that waveform to all
//: 384,000 pixels every minute, including the ~95% that had not changed --
//: which is why the WHOLE screen fogged when only the clock was moving.
//:
//: Parsing lives in `epaper_dirty.cpp` because it reads untrusted input into
//: coordinates handed straight to GxEPD2, and that is worth a test.
epaper::DirtyPlan s_dirty;
unsigned long s_next_at = 0;

/** Measured on this panel: full 3.68s, partial 1.58s. */
constexpr unsigned long kRefreshBudgetMs = 8000;

//: How many partial refreshes before a full one. Partial is 1.58s against
//: 3.68s and does not flash, so it is what you want almost always -- but each
//: one leaves a little of the previous image behind, and the residue
//: accumulates until thin type stops being legible. A full refresh drives
//: every pixel to both extremes and clears it.
//:
//: How many partial refreshes before a full one clears the residue.
//:
//: This used to be the only defence against fogging, and it had to be small
//: because every partial drove the whole panel. Now a partial drives only the
//: rectangles that changed -- typically the clock, about 5% of the glass -- so
//: the residue is confined to that small area and builds far more slowly
//: everywhere else. Fifteen minutes between flashes rather than four.
constexpr uint32_t kPartialsBeforeFull = 15;

uint32_t s_partials = 0;
uint32_t s_last_draw_ms = 0;

void sleepPanel() {
  // On every path, including the ones that go wrong. Omitting this is the
  // most common cause of a dead panel.
  display.hibernate();
}

}  // namespace

/** One line of text for everything this panel has to say for itself. */
void epaperSay(const char* headline, const char* detail) {
  display.setFullWindow();
  display.setTextColor(GxEPD_BLACK);
  display.firstPage();
  do {
    display.fillScreen(GxEPD_WHITE);
    display.setTextSize(3);
    display.setCursor(40, 200);
    display.print(headline);
    display.setTextSize(2);
    display.setCursor(40, 250);
    display.print(detail);
    display.setTextSize(1);
    display.setCursor(40, 300);
    display.print(services::deviceId());
  } while (display.nextPage());
  display.hibernate();
}

namespace {

/** Declare geometry and learn whether we are in the fleet. */
/** Take the server's cadence, or keep ours if the header is not a number. */
void applyPollHeader(const String& value) {
  if (value.length() == 0) {
    return;
  }
  char* end = nullptr;
  const long seconds = strtol(value.c_str(), &end, 10);
  if (end == value.c_str() || *end != '\0' || seconds <= 0) {
    return;
  }
  // Clamp BEFORE multiplying: a large number times 1000 overflows unsigned
  // long on this 32-bit target.
  long clamped = seconds;
  if (clamped > static_cast<long>(kPollMaxMs / 1000)) {
    clamped = static_cast<long>(kPollMaxMs / 1000);
  }
  unsigned long ms = static_cast<unsigned long>(clamped) * 1000UL;
  if (ms < kPollMinMs) {
    ms = kPollMinMs;
  }
  s_poll_ms = ms;
}

bool poll() {
  HTTPClient http;
  char url[256];
  snprintf(url, sizeof(url),
           "%s/api/devices/%s/scene?w=%d&h=%d&depth=1",
           services::server::baseUrl(), services::deviceId(), kWidth, kHeight);
  if (!http.begin(url)) {
    return false;
  }
  http.setTimeout(8000);
  const char* kPollKeys[] = {"X-Poll-Seconds"};
  http.collectHeaders(kPollKeys, 1);
  const int code = http.GET();
  if (code != 200) {
    http.end();
    return false;
  }
  applyPollHeader(http.header("X-Poll-Seconds"));
  JsonDocument doc;
  const DeserializationError err = deserializeJson(doc, http.getStream());
  http.end();
  if (err) {
    return false;
  }
  s_ever_reached = true;
  s_assigned = doc["assigned"].is<bool>() && doc["assigned"].as<bool>();
  const char* message = doc["message"].is<const char*>()
                            ? doc["message"].as<const char*>() : "";
  snprintf(s_message, sizeof(s_message), "%s", message ? message : "");
  return true;
}

/** Fetch the frame and put it on the glass. Returns false if nothing changed. */
bool drawFrame() {
  HTTPClient http;
  char url[256];
  snprintf(url, sizeof(url), "%s/api/devices/%s/frame?w=%d&h=%d",
           services::server::baseUrl(), services::deviceId(), kWidth, kHeight);
  if (!http.begin(url)) {
    return false;
  }
  http.setTimeout(20000);
  // The server answers 304 when the pixels have not changed, which costs one
  // round trip instead of 48 KB and, more to the point, saves a refresh: this
  // panel takes 3.7 seconds and a visible flash to redraw the same image.
  const char* keys[] = {"ETag", "X-Poll-Seconds", "X-Dirty"};
  http.collectHeaders(keys, 3);
  if (s_etag[0]) {
    http.addHeader("If-None-Match", s_etag);
  }
  const int code = http.GET();
  applyPollHeader(http.header("X-Poll-Seconds"));
  if (code == 304) {
    http.end();
    return false;
  }
  if (code != 200) {
    Serial.printf("[epaper] frame returned %d\n", code);
    http.end();
    return false;
  }
  const int length = http.getSize();
  if (length != (int)kFrameBytes) {
    // A short frame is a torn screen rather than an error the panel can
    // detect, so it is refused here instead of drawn.
    Serial.printf("[epaper] refusing a frame of %d bytes, wanted %u\n",
                  length, (unsigned)kFrameBytes);
    http.end();
    return false;
  }
  uint8_t* frame = (uint8_t*)malloc(kFrameBytes);
  if (frame == nullptr) {
    Serial.println("[epaper] no room for a frame");
    http.end();
    return false;
  }
  const size_t got = http.getStream().readBytes(frame, kFrameBytes);
  snprintf(s_etag, sizeof(s_etag), "%s", http.header("ETag").c_str());
  const String dirty = http.header("X-Dirty");
  s_dirty = epaper::parseDirty(
      http.hasHeader("X-Dirty") ? dirty.c_str() : nullptr, kWidth, kHeight);
  http.end();
  if (got != kFrameBytes) {
    Serial.printf("[epaper] short read: %u of %u\n", (unsigned)got,
                  (unsigned)kFrameBytes);
    free(frame);
    return false;
  }

  // INVERTED, and this was measured rather than assumed. CLAUDE.md fixes the
  // wire format at 1 = black; the bring-up spike drew a buffer with its left
  // half set and the panel rendered that half WHITE. So the panel's own
  // convention is the opposite of ours, and `invert` is where that is
  // reconciled -- once, here, rather than by XORing 48000 bytes.
  const uint32_t started = millis();

  // The first draw after a boot is always full: whatever was on the glass came
  // from a previous life of this panel and none of it is ours to keep. After
  // that a full refresh every so often, to clear the residue the partials in
  // between have left behind.
  const uint32_t at = millis();
  const bool full = (s_partials == 0) || (s_partials >= kPartialsBeforeFull) ||
                    !s_dirty.known;
  if (full) {
    display.setFullWindow();
    display.writeImage(frame, 0, 0, kWidth, kHeight, true, false, false);
    display.refresh(false);
    s_partials = 1;
  } else if (s_dirty.count == 0) {
    // The server diffed the frames and nothing moved. Drawing would spend a
    // waveform, and a little more ghosting, to reach the picture already on
    // the glass.
  } else {
    // Only what changed. Each rectangle is its own window, so the pixels
    // between them are never driven and never accumulate residue -- which is
    // the whole reason the screen used to fog everywhere at once.
    for (size_t i = 0; i < s_dirty.count; ++i) {
      const epaper::Rect& r = s_dirty.rects[i];
      display.setPartialWindow(r.x, r.y, r.w, r.h);
      display.writeImagePart(frame, r.x, r.y, kWidth, kHeight,
                             r.x, r.y, r.w, r.h, true, false, false);
      display.refresh(true);
    }
    s_partials = s_partials + 1;
  }
  free(frame);
  s_last_draw_ms = at;

  sleepPanel();
  Serial.printf("[epaper] drew a frame in %lu ms (%s), heap %u B\n",
                (unsigned long)(millis() - started),
                full ? "full" : "partial", (unsigned)ESP.getFreeHeap());
  return true;
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.printf("\n[epaper] HomeScreen e-paper client %s\n",
                config::kFirmwareVersion);

  pinMode(config::epaper::kPinPwr, OUTPUT);
  digitalWrite(config::epaper::kPinPwr, HIGH);
  SPI.begin(config::epaper::kPinClk, -1, config::epaper::kPinDin,
            config::epaper::kPinCs);
  display.init(115200, true, 2, false);

  epaperSay("Conectando", "buscando la red");

  services::server::load();
  if (!wifiSetupConnect()) {
    epaperSay("Sin red", "abre el portal wifi");
    return;
  }
  Serial.printf("[epaper] wifi up, ip %s, heap %u B\n",
                WiFi.localIP().toString().c_str(),
                (unsigned)ESP.getFreeHeap());
  Serial.printf("[epaper] server %s, id %s\n", services::server::baseUrl(),
                services::deviceId());
}

void loop() {
  wifiLoop();
  const unsigned long now = millis();
  if (s_next_at && (long)(now - s_next_at) < 0) {
    delay(200);
    return;
  }
  s_next_at = now + s_poll_ms;

  if (!poll()) {
    if (!s_ever_reached) {
      // Never reached the Pi at all. The address is the actionable detail,
      // so it goes on the glass rather than into a log nobody can read.
      epaperSay("Sin servidor", services::server::baseUrl());
    }
    return;
  }
  if (!s_assigned) {
    epaperSay("Sin asignar",
                 s_message[0] ? s_message : "esperando aprobacion");
    return;
  }
  drawFrame();
}
