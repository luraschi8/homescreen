// Bring-up spike for the e-paper panel. Throwaway by intent: it proves the
// wiring and settles the one question that must not be guessed at, and then
// the real client is written against what it found.
//
// It answers, in order:
//
//   1. Is the panel wired correctly at all? BUSY moving is the first sign of
//      life -- it is the only line the panel drives, so a BUSY that never
//      changes means the panel is not powered or not reset.
//   2. Which way round is the data? CLAUDE.md fixes the WIRE format at
//      1 = black, corrected against the driver source after the addendum got
//      it backwards. What the panel's own RAM wants is a separate question,
//      and getting it wrong renders a photographic negative -- which is
//      obvious on glass and invisible in a test. So this draws a shape whose
//      handedness cannot be mistaken and says which polarity produced it.
//   3. How long a full refresh actually takes, measured rather than quoted.

#include <Arduino.h>

#include <GxEPD2_BW.h>

#include "config_epaper.h"

namespace {

using config::epaper::kFrameBytes;
using config::epaper::kHeight;
using config::epaper::kWidth;

// Paged: a full-screen buffer is 48000 bytes against roughly 55 KB of free
// heap, which leaves nothing for the WiFi stack the real client needs. The
// spike does not need the network, but it should not prove the panel works in
// a configuration the client cannot use.
GxEPD2_BW<GxEPD2_750_T7, GxEPD2_750_T7::HEIGHT / 8> display(
    GxEPD2_750_T7(config::epaper::kPinCs, config::epaper::kPinDc,
                  config::epaper::kPinRst, config::epaper::kPinBusy));

void report(const char* what, uint32_t started) {
  Serial.printf("[epaper] %-26s %6lu ms  BUSY=%d\n", what,
                (unsigned long)(millis() - started),
                digitalRead(config::epaper::kPinBusy));
}

// A shape with no symmetry: an L in the top-left corner. If the panel is
// mirrored, rotated or inverted, an L says so and a rectangle does not.
void drawProbe(bool inverted) {
  const uint16_t ink = inverted ? GxEPD_WHITE : GxEPD_BLACK;
  const uint16_t paper = inverted ? GxEPD_BLACK : GxEPD_WHITE;
  display.setFullWindow();
  display.firstPage();
  do {
    display.fillScreen(paper);
    display.fillRect(20, 20, 240, 40, ink);   // the long arm, along the top
    display.fillRect(20, 20, 40, 200, ink);   // the short arm, down the side
    display.setTextColor(ink);
    display.setCursor(300, 60);
    display.print(inverted ? "INVERTED" : "NORMAL");
    display.setCursor(300, 100);
    display.print("800x480 7.5in V2");
  } while (display.nextPage());
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(2000);                       // USB CDC needs a moment to enumerate
  Serial.println("\n[epaper] bring-up spike");
  Serial.printf("[epaper] free heap %u B, frame is %u B\n",
                (unsigned)ESP.getFreeHeap(), (unsigned)kFrameBytes);

  // PWR first and always. Without it the panel never powers up and gives no
  // indication why -- which is the failure CLAUDE.md calls out by name.
  pinMode(config::epaper::kPinPwr, OUTPUT);
  digitalWrite(config::epaper::kPinPwr, HIGH);
  pinMode(config::epaper::kPinBusy, INPUT);
  delay(20);
  Serial.printf("[epaper] PWR high, BUSY reads %d\n",
                digitalRead(config::epaper::kPinBusy));

  SPI.begin(config::epaper::kPinClk, -1, config::epaper::kPinDin,
            config::epaper::kPinCs);

  uint32_t started = millis();
  display.init(115200, true, 2, false);
  report("init", started);

  if (display.width() != kWidth || display.height() != kHeight) {
    Serial.printf("[epaper] WRONG GEOMETRY: driver says %dx%d, expected %dx%d\n",
                  display.width(), display.height(), kWidth, kHeight);
  }

  started = millis();
  drawProbe(false);
  report("full refresh, normal", started);
  Serial.println("[epaper] LOOK AT THE PANEL. Expect a black L on white,");
  Serial.println("[epaper] its long arm along the TOP and short arm down the");
  Serial.println("[epaper] LEFT, with the word NORMAL beside it.");

  delay(6000);

  started = millis();
  drawProbe(true);
  report("full refresh, inverted", started);
  Serial.println("[epaper] Now the negative. Whichever of the two read");
  Serial.println("[epaper] correctly tells us the panel's polarity.");

  // On every path, including the ones that go wrong. Omitting it is the most
  // common cause of a dead panel.
  display.hibernate();
  Serial.println("[epaper] hibernated. Spike done.");
}

void loop() {
  delay(10000);
  Serial.printf("[epaper] idle, BUSY=%d, heap %u B\n",
                digitalRead(config::epaper::kPinBusy),
                (unsigned)ESP.getFreeHeap());
}
