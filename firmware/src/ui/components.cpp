#include "ui/components.h"

#include <cstring>

#include "config.h"
#include "hardware/display.h"
#include "hardware/display_font.h"
#include "services/device_id.h"
#include "services/scene_client.h"
#include "services/server_config.h"
#include "ui/draw_list.h"
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
  // Anything else is drawn from its instruction list if it shipped one. This
  // firmware deliberately does not know what a "clock" or a "weather" is.
  if (services::scene::drawJson()[0] != '\0') {
    return ComponentKind::kDrawList;
  }
  return ComponentKind::kUnknown;
}

namespace {

/** Tone -> pen. 1-bit panels collapse these; this one has colour to spend. */
uint16_t penFor(uint8_t tone) {
  switch (tone) {
    case ui::drawlist::kDim:  return 0x8410;   // mid grey
    case ui::drawlist::kGood: return 0x2E68;   // green
    case ui::drawlist::kBad:  return 0xF9A6;   // red
    default:                  return config::kTextOnBlack;
  }
}

/** Execute an instruction list onto the panel. */
bool drawInstructions(const char* draw_json) {
  ui::drawlist::Placement placed[ui::drawlist::kMaxPlacements];
  const size_t n = ui::drawlist::resolve(draw_json, config::kDisplayWidth,
                                         config::kDisplayHeight, placed,
                                         ui::drawlist::kMaxPlacements);
  if (n == 0) {
    return false;   // nothing drawable: the caller shows a status screen
  }
  displayFontEnsureLoaded(tft);
  tft.fillScreen(config::kColorBlack);
  tft.setTextDatum(textdatum_t::middle_center);
  for (size_t i = 0; i < n; ++i) {
    // The resolver decided WHERE and HOW BIG; this only chooses the pen and
    // the face. Any layout thinking here would be a second opinion, and the
    // preview would stop matching the glass.
    // Pixels, not a scale factor. displayFontSetSmoothSize takes a SCALE and
    // reads as if it took a size; passing 62 there rendered the 15px face at
    // 62x and the panel showed one letter.
    displayFontSetPixelHeight(tft, placed[i].px);
    tft.setTextColor(penFor(placed[i].tone), config::kColorBlack);
    tft.drawString(placed[i].text, placed[i].x, placed[i].y);
  }
  return true;
}

}  // namespace

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
    case ComponentKind::kDrawList:
      if (drawInstructions(services::scene::drawJson())) {
        return true;
      }
      statusScreenUnassigned(services::deviceId(), "escena vacia");
      return true;
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
