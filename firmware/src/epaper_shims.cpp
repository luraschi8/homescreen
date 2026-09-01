// What `wifi_setup` expects a display to provide, answered for e-paper.
//
// The round panel's versions animate: `statusScreenConnectingTick` redraws a
// spinner many times a second. On this glass a redraw is 3.7 seconds and a
// visible full-screen flash, so an animated "connecting" would take longer
// than connecting does and leave the panel flickering through it. The tick is
// therefore deliberately empty, and the state is drawn ONCE when it changes.
//
// The radar options exist because `wifi_setup` builds one portal for every
// board. This panel has no radar, so they answer with the defaults and store
// nothing -- a setting that cannot affect anything should not be persisted
// where somebody will later wonder what it does.

#include <Arduino.h>

#include "epaper_ui.h"
#include "ui/radar_range.h"
#include "ui/status_screens.h"

void statusScreenPortal() {
  epaperSay("Configura la red", "conecta al wifi HomeScreen");
}

void statusScreenConnectFailed() {
  epaperSay("Sin red", "no se pudo conectar");
}

void statusScreenWifiReset() {
  epaperSay("Red borrada", "reiniciando");
}

void statusScreenConnectingBegin(const char* ssid) {
  epaperSay("Conectando", ssid && ssid[0] ? ssid : "buscando la red");
}

void statusScreenConnectingTick() {
  // Deliberately nothing. See the note at the top: one tick here would cost
  // 3.7 seconds and a flash, and they arrive several times a second.
}

namespace ui {
namespace radar {

bool useMiles() { return false; }
bool showRunways() { return false; }
void saveMilesFromPortal(const char*) {}
void saveRunwaysFromPortal(const char*) {}
void unitsReset() {}

}  // namespace radar
}  // namespace ui
