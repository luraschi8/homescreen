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
