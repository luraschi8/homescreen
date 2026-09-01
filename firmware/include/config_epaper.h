// Pins and geometry for the e-paper client: an ESP32-C3 wired to a Waveshare
// e-Paper Driver HAT carrying a 7.5" V2 panel (800x480, 1-bit).
//
// GPIO 2, 8 and 9 are deliberately unused: they are strapping pins on the C3,
// and a signal held low at reset changes the boot mode. 18 and 19 are USB.
#pragma once

#include <driver/gpio.h>

namespace config {
namespace epaper {

// Matches the HAT's own labels. DIN is the panel's data in, so it is the C3's
// MOSI; there is no MISO -- the panel never talks back except through BUSY.
constexpr gpio_num_t kPinDin = GPIO_NUM_6;
constexpr gpio_num_t kPinClk = GPIO_NUM_4;
constexpr gpio_num_t kPinCs = GPIO_NUM_7;
constexpr gpio_num_t kPinDc = GPIO_NUM_5;
constexpr gpio_num_t kPinRst = GPIO_NUM_3;
constexpr gpio_num_t kPinBusy = GPIO_NUM_10;

// CLAUDE.md: "Wire the PWR pin -- BCM 18, physical 12. module_init() asserts
// it and SPEC SS2's wiring table omits it; without it the panel never powers up
// and says nothing about why."
constexpr gpio_num_t kPinPwr = GPIO_NUM_1;

constexpr int kWidth = 800;
constexpr int kHeight = 480;

// 800 * 480 / 8. The frame endpoint serves exactly this many bytes, and a
// mismatch is a torn image rather than an error, so both sides state it.
constexpr size_t kFrameBytes = 48000;

}  // namespace epaper
}  // namespace config
