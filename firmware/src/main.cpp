/** HomeScreen display client — server-driven round display. */
#include <Arduino.h>

#include "config.h"

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.printf("\nHomeScreen display client %s\n", config::kFirmwareVersion);
}

void loop() { delay(1000); }
