#include "services/scene_client.h"

#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <esp_task_wdt.h>

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
 * TWO clocks, because a 304 means different things to different consumers.
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
/** Bumped on every install; the render loop watches it. */
uint32_t s_generation = 0;
bool s_was_link_up = false;
/** Result of the last tick, so the task can pick its delay. */
bool s_last_poll_ok = false;
/** collectHeaders reallocates its array per call; register the list once. */
bool s_headers_registered = false;

char s_etag[40] = {0};
char s_scene[24] = {0};
char s_component[16] = {0};
char s_message[80] = {0};
/** Verbatim `draw` array for a component that ships one. */
char s_draw[768] = {0};
bool s_assigned = false;
bool s_feed_ok = false;
float s_feed_age_s = -1.0f;
float s_radius_km = 0.0f;
unsigned long s_poll_ms = config::kPollDefaultMs;

constexpr unsigned long kRequestTimeoutMs = 8000;
constexpr int kConnectAttemptMs = 400;
/**
 * Plain HTTP: no mbedTLS handshake on this stack, so far less than the
 * reference's 8192 (which measured 3,636 B free WITH TLS). 6144 is also a
 * fragmentation improvement: xTaskCreate needs its stack as ONE contiguous heap
 * block, and 8192 against a ~9 KB largest free block was close to the wall.
 * Confirm with pollTaskStackFree() on hardware before trusting it.
 */
constexpr uint32_t kPollTaskStackBytes = 6144;
/**
 * The watchdog is fed once per tick, so a tick must never be longer than the
 * watchdog period. The server may legitimately ask for a 10-minute cadence, so
 * the sleep is chunked and each chunk feeds. Without this, one
 * `PATCH {"poll_seconds": 120}` turns the display into a panic-reboot loop that
 * survives the reboot, because the server hands out the same cadence again.
 */
constexpr unsigned long kWatchdogChunkMs = 5000;

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
  char draw[768] = {0};
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
 * radar_display reads the aircraft list AND the clocks inside one lock and
 * multiplies them together. A clock set outside means a frame can draw the NEW
 * positions against the OLD content time -- up to a poll interval of extra dead
 * reckoning, ~1 km at 400 kt, as a jump-and-snap once per poll, plus a
 * whole-picture grey flash because the staleness test reads the stale number
 * too. The scene metadata goes in for the same reason: copyTrimmed writes
 * out[0] = '\0' before its memcpy, so a frame landing mid-parse would render
 * the unassigned screen with an EMPTY message -- a blank round panel, which is
 * exactly what that screen exists to prevent.
 */
void install(uint8_t back, const Parsed& p, unsigned long now) {
  s_counts[back] = p.count;           // back buffer: no reader can see it yet
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
  memcpy(s_draw, p.draw, sizeof(s_draw));
  s_content_ms = now;
  s_contact_ms = now;
  s_ever_received = true;
  ++s_generation;
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
  // silently drops our capability declaration and the server sends no radar.
  const int n = snprintf(
      out, len,
      "%s/api/device/%s/scene?w=%d&h=%d&depth=%d&max_items=%u"
      "&components=%s&fw=%s&uptime=%lu&rssi=%d",
      server::baseUrl(), deviceId(), config::kDisplayWidth,
      config::kDisplayHeight, config::kDisplayDepth,
      static_cast<unsigned>(kMaxAircraft), config::kDeclaredComponents,
      config::kFirmwareVersion,
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
  const long clamped = std::min<long>(
      std::max<long>(seconds, 0),
      static_cast<long>(config::kPollMaxMs / 1000));
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
  s_headers_registered = false;
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
  if (!s_headers_registered) {
    // collectHeaders new[]s and delete[]s its array on every call; sendRequest
    // clears only the collected VALUES, so registering once per connection is
    // enough and saves an allocation per poll.
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
    // server that vanished without a FIN leaves connected() true forever --
    // every later request then writes into a dead socket and times out after
    // 8 s, with no recovery. The reference forced a fresh session here for
    // exactly this reason.
    DEBUG_LOG("poll: HTTP %d", code);
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
    if (!comp["c"].is<const char*>()) {
      continue;
    }
    // A component that ships an instruction list needs no per-component code
    // here: keep the list, and ui::drawlist executes it. That is the whole
    // point of the vocabulary -- a new component is a server-side file, not a
    // reflash.
    if (comp["draw"].is<JsonArrayConst>()) {
      copyTrimmed(comp, "c", p.component, sizeof(p.component));
      const size_t wrote = serializeJson(comp["draw"], p.draw, sizeof(p.draw));
      if (wrote == 0 || wrote >= sizeof(p.draw)) {
        // Truncated JSON parses as garbage, and half a screen is worse than an
        // honest empty one.
        DEBUG_LOG("poll: draw list too large (%u B)",
                  static_cast<unsigned>(wrote));
        p.draw[0] = '\0';
        p.component[0] = '\0';
      }
      break;
    }
    if (strcmp(comp["c"].as<const char*>(), "radar") != 0) {
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

  install(back, p, millis());

  // Headers read before end(). It would also be safe after: HTTPClient::clear()
  // resets _returnCode, _size and _headers and does NOT touch
  // _currentHeaders[i].value, which survives until the next sendRequest().
  // Noted because it looks like a use-after-free either way, and someone will
  // eventually "fix" it in the wrong direction.
  const String etag = s_http.header("ETag");
  strncpy(s_etag, etag.c_str(), sizeof(s_etag) - 1);
  s_etag[sizeof(s_etag) - 1] = '\0';
  applyPollHeader(s_http.header("X-Poll-Seconds"));
  s_http.end();                         // keep the connection; it is healthy
  DEBUG_LOG("poll: scene=%s %u items", s_scene, static_cast<unsigned>(p.count));
  return true;
}

void pollTick(bool link_up) {
  esp_task_wdt_reset();
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

unsigned long nextDelayMs() {
  return s_last_poll_ok ? s_poll_ms : config::kPollErrorMs;
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

float contactExpirySec() {
  const float agreed = static_cast<float>(s_poll_ms) / 1000.0f;
  const float missed = agreed * kContactExpiryPolls;
  return missed > kContactExpirySec ? missed : kContactExpirySec;
}

bool contentExpired() {
  if (!s_ever_received) {
    return false;                       // nothing to expire yet
  }
  if (secondsSince(s_contact_ms) >= contactExpirySec()) {
    return true;                        // the server itself is gone
  }
  // The server is answering, but its feed stopped moving. feed_age_s is the
  // only number that grows in every way that fails -- daemon stopped, hung,
  // exited 78, upstream down -- because all of them leave fetched_at frozen.
  // It also does not twitch on a single transient, which is exactly why
  // feed_ok is not tested here. Compared bare: the ETag folds feed_age_s in at
  // AGE_BUCKET_S, so a 304 certifies the age is within one bucket of what the
  // last 200 reported -- there is nothing to extrapolate.
  return s_feed_age_s >= 0.0f && s_feed_age_s >= kFeedExpirySec;
}

unsigned long pollIntervalMs() { return s_poll_ms; }
bool assigned() { return s_assigned; }
const char* sceneName() { return s_scene; }
const char* componentName() { return s_component; }
const char* drawJson() { return s_draw; }
const char* message() { return s_message; }
float radiusKm() { return s_radius_km; }
bool feedOk() { return s_feed_ok; }
float feedAgeS() { return s_feed_age_s; }
bool everReceived() { return s_ever_received; }
uint32_t contentGeneration() { return s_generation; }

unsigned pollTaskStackFree() {
  return s_task == nullptr
             ? 0
             : static_cast<unsigned>(uxTaskGetStackHighWaterMark(s_task));
}

namespace {

void pollTaskBody(void*) {
  // Nothing else can reset this board: Arduino subscribes loopTask only if you
  // call enableLoopWDT(), and the idle-task check is off in this SDK config
  // (CONFIG_ESP_TASK_WDT=y, PANIC=y, TIMEOUT_S=5, idle check unset). The poll
  // task is the one that can block for 8 s on a dead socket.
  esp_task_wdt_init(kWatchdogTimeoutSec, /*panic=*/true);
  esp_task_wdt_add(nullptr);
  for (;;) {
    // pollTick, NOT pollOnce: the watchdog feed and the link-down teardown both
    // live there, and a task calling pollOnce directly reaches neither.
    pollTick(WiFi.status() == WL_CONNECTED);
    // Chunked, because the server may legitimately ask for a 10-minute cadence
    // and the watchdog fires at 60 s. Each chunk feeds.
    unsigned long remaining = nextDelayMs();
    while (remaining > 0) {
      const unsigned long slice = std::min(remaining, kWatchdogChunkMs);
      vTaskDelay(pdMS_TO_TICKS(slice));
      esp_task_wdt_reset();
      remaining -= slice;
    }
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
  s_generation = 0;
  s_was_link_up = false;
  s_last_poll_ok = false;
  s_headers_registered = false;
  s_etag[0] = s_scene[0] = s_component[0] = s_message[0] = '\0';
  s_draw[0] = '\0';
  s_assigned = false;
  s_feed_ok = false;
  s_feed_age_s = -1.0f;
  s_radius_km = 0.0f;
  s_poll_ms = config::kPollDefaultMs;
}
#endif

}  // namespace services::scene
