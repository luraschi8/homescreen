// A wiring probe for the 7.5" panel. Not part of the product.
//
// The client reported "Busy Timeout!" on every operation: the C3 talks and
// nothing answers. That is either a disconnected signal, an unpowered HAT, or
// a dead panel, and from the client's own logs those look identical. This
// tells them apart without needing the panel to cooperate.
//
// The central trick is float detection. An input pin with nothing attached
// follows the chip's own internal pull-up or pull-down; a pin wired to
// something that drives it does not. So toggling the internal pull and
// watching whether the reading follows says whether the WIRE IS THERE, which
// no amount of staring at a ribbon can.

#include <Arduino.h>

#include "config_epaper.h"

namespace {

using config::epaper::kPinBusy;
using config::epaper::kPinCs;
using config::epaper::kPinDc;
using config::epaper::kPinPwr;
using config::epaper::kPinRst;

/** Read a pin with the internal pull-up, then the pull-down. */
void probe(const char* label, gpio_num_t pin) {
  pinMode(pin, INPUT_PULLUP);
  delay(30);
  const int up = digitalRead(pin);
  pinMode(pin, INPUT_PULLDOWN);
  delay(30);
  const int down = digitalRead(pin);
  pinMode(pin, INPUT);

  const char* verdict;
  if (up == 1 && down == 0) {
    verdict = "FLOATING -- nothing is connected to this pin";
  } else if (up == 0 && down == 0) {
    verdict = "driven LOW by something (connected)";
  } else if (up == 1 && down == 1) {
    verdict = "driven HIGH by something (connected)";
  } else {
    verdict = "unstable";
  }
  Serial.printf("  %-14s pullup=%d pulldown=%d  %s\n", label, up, down,
                verdict);
}

/** Does BUSY ever move? A live panel pulses it during a reset. */
void watchDuringReset() {
  pinMode(kPinBusy, INPUT);
  pinMode(kPinRst, OUTPUT);

  int lo = 0, hi = 0, changes = 0, last = digitalRead(kPinBusy);
  digitalWrite(kPinRst, LOW);          // hold the panel in reset
  delay(20);
  digitalWrite(kPinRst, HIGH);         // release it: init should follow
  const uint32_t until = millis() + 3000;
  while (millis() < until) {
    const int now = digitalRead(kPinBusy);
    if (now != last) {
      ++changes;
      last = now;
    }
    now ? ++hi : ++lo;
    delayMicroseconds(200);
  }
  Serial.printf("  after a reset pulse: BUSY high %d / low %d samples, "
                "%d transitions\n", hi, lo, changes);
  Serial.println(changes > 0
      ? "  -> the panel ANSWERED: BUSY moved, so the line is live"
      : "  -> BUSY never moved: the panel is not responding");
}

//: Every line the C3 drives, with the pin it should be on.
struct Wire {
  const char* name;
  gpio_num_t pin;
};

//: BUSY is deliberately NOT here. It is an INPUT from the HAT, and driving it
//: puts the C3 against the HAT's own driver. Worse, it is useless: the HAT has
//: a bidirectional level shifter on that line rather than a passthrough, so
//: with no panel attached the shifter holds whatever state it last saw --
//: driving it high made it read high afterwards, with nothing connected.
//: Which also means BUSY tells us nothing about the panel unless the panel is
//: present and actively driving.
const Wire kWires[] = {
    {"DIN ", config::epaper::kPinDin}, {"CLK ", config::epaper::kPinClk},
    {"CS  ", kPinCs},                  {"DC  ", kPinDc},
    {"RST ", kPinRst},
};

/** Drive each line in turn and see whether any OTHER line follows it.
 *
 * A jumper in the wrong header hole, a bent pin, two wires touching: all of
 * them show up as one line moving when a different one is driven. This needs
 * no panel and no cooperation from the HAT, which is why it is worth doing
 * while the ribbon is out.
 */
void shorts() {
  const size_t n = sizeof(kWires) / sizeof(kWires[0]);
  bool clean = true;
  for (size_t i = 0; i < n; ++i) {
    for (size_t j = 0; j < n; ++j) {
      if (i != j) pinMode(kWires[j].pin, INPUT_PULLDOWN);
    }
    pinMode(kWires[i].pin, OUTPUT);
    digitalWrite(kWires[i].pin, HIGH);
    delay(20);
    for (size_t j = 0; j < n; ++j) {
      if (i == j) continue;
      if (digitalRead(kWires[j].pin) == HIGH) {
        Serial.printf("  SHORT: driving %s pulls %s high too\n",
                      kWires[i].name, kWires[j].name);
        clean = false;
      }
    }
    pinMode(kWires[i].pin, INPUT);
  }
  Serial.println(clean ? "  no two lines are shorted together"
                       : "  ^^ those lines are connected to each other");
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(2500);
  Serial.println("\n=== e-paper wiring probe ===");

  Serial.println("\n[1] with the HAT unpowered (PWR low)");
  pinMode(kPinPwr, OUTPUT);
  digitalWrite(kPinPwr, LOW);
  delay(300);
  probe("BUSY/GPIO10", kPinBusy);

  Serial.println("\n[2] with the HAT powered (PWR high)");
  digitalWrite(kPinPwr, HIGH);
  delay(1200);                          // let the panel's rail come up
  probe("BUSY/GPIO10", kPinBusy);

  Serial.println("\n[3] does BUSY respond to a reset?");
  watchDuringReset();

  Serial.println("\n[4] are any two lines shorted? (needs no panel)");
  digitalWrite(kPinPwr, LOW);
  delay(200);
  shorts();

  Serial.println("\n[5] the pins we drive, for reference");
  Serial.printf("  PWR=GPIO%d CS=GPIO%d DC=GPIO%d RST=GPIO%d BUSY=GPIO%d\n",
                (int)kPinPwr, (int)kPinCs, (int)kPinDc, (int)kPinRst,
                (int)kPinBusy);
  Serial.println("\n=== done ===");
}

void loop() {
  // Live, so the ribbon can be pulled and reseated WHILE watching. That is
  // the test that separates the two readings of a LOW busy line: if BUSY
  // floats with the ribbon out and goes LOW with it in, the panel is there
  // and asserting busy. If it reads the same either way, the panel is
  // contributing nothing and the LOW is coming from this side of the FPC.
  digitalWrite(kPinPwr, HIGH);
  probe("BUSY/GPIO10", kPinBusy);
  delay(2000);
}
