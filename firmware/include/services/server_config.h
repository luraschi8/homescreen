#pragma once

#include <cstdint>

namespace services::server {

/** Read the stored address into memory. Call once at boot. */
void load();

const char* host();
uint16_t port();
/** "http://host:port" -- no trailing slash. */
const char* baseUrl();

/**
 * Parse, persist, and apply immediately. Accepts `host`, `host:port`, and a
 * pasted `http://host:port/path`. Refuses `https://` outright: this firmware
 * has no TLS, and accepting the string then connecting in the clear would be a
 * lie told to whoever typed it. Returns false and leaves the stored AND live
 * values untouched on anything unusable, so a typo in the portal cannot strand
 * a working device.
 */
bool saveFromString(const char* text);

}  // namespace services::server
