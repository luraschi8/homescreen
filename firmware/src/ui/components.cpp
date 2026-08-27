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

  // Order matters: each branch tells a human a different thing, and the wrong
  // order tells them the least useful one. "No server" outranks "unassigned"
  // because a device that cannot reach the Pi has not been assigned anything
  // either, and the address is the actionable detail.
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
      // declare. If it does, say so rather than leaving a hole on the glass.
      statusScreenUnassigned(services::deviceId(), "escena no soportada");
      return true;
  }
}

}  // namespace ui
