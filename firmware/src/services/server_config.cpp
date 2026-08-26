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
