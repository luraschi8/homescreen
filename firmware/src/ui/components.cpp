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
//! RGB565 for each tone. Picked to stay distinguishable at a glance on a
//! small panel across a room, which rules out anything subtle: these are
//! saturated because a 240px circle seen from a sofa has no room for nuance.
uint16_t penFor(uint8_t tone) {
  switch (tone) {
    case ui::drawlist::kDim:    return 0x8410;   // mid grey
    case ui::drawlist::kGood:   return 0x2E68;   // green
    case ui::drawlist::kBad:    return 0xF9A6;   // red
    case ui::drawlist::kAccent: return 0x5E5C;   // cyan
    case ui::drawlist::kWarn:   return 0xE5A7;   // amber
    case ui::drawlist::kCool:   return 0x6D5E;   // cold blue
    case ui::drawlist::kHot:    return 0xF449;   // warm orange
    default:                    return config::kTextOnBlack;
  }
}

/** Execute an instruction list onto the panel. */
bool drawInstructions(const char* draw_json) {
  // Static, not a local: 40 placements is ~3 KB and the loop task's stack is
  // 8 KB. Drawing is single-threaded -- renderScene() is only ever called from
  // loop() -- so one buffer is enough and it costs the stack nothing.
  static ui::drawlist::Placement placed[ui::drawlist::kMaxPlacements];
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
    const ui::drawlist::Placement& p = placed[i];
    const uint16_t pen = penFor(p.tone);
    switch (p.shape) {
      case ui::drawlist::kCircle:
        // Drawn in the order the server emitted them, which is the only
        // stacking either side has to agree on.
        if (p.fill) {
          tft.fillCircle(p.x, p.y, p.px, pen);
        } else {
          tft.drawCircle(p.x, p.y, p.px, pen);
        }
        break;
      case ui::drawlist::kLine:
        // Width matters: a one-pixel ray on a 240px panel vanishes at the
        // distance this screen is actually read from.
        tft.drawWideLine(p.x, p.y, p.x2, p.y2, p.px / 2.0f, pen);
        break;
      case ui::drawlist::kTri:
        tft.fillTriangle(p.x, p.y, p.x2, p.y2, p.x3, p.y3, pen);
        break;
      default:
        // Pixels, not a scale factor. displayFontSetSmoothSize takes a SCALE
        // and reads as if it took a size; passing 62 there rendered the 15px
        // face at 62x and the panel showed one letter.
        displayFontSetPixelHeight(tft, p.px, p.text);
        tft.setTextColor(pen, config::kColorBlack);
        tft.drawString(p.text, p.x, p.y);
        break;
    }
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
