#pragma once

#define LGFX_USE_V1
#include <LovyanGFX.hpp>

#include "config.h"

/** LovyanGFX device: GC9A01 on SPI. Pin values come from config.h. */
class LGFX : public lgfx::LGFX_Device {
  lgfx::Bus_SPI _bus;
  lgfx::Panel_GC9A01 _panel;

public:
  LGFX() {
    {
      auto cfg = _bus.config();
      cfg.spi_host = SPI2_HOST;
      cfg.freq_write = config::kDisplaySpiWriteHz;
      cfg.pin_sclk = static_cast<int>(config::kDisplayPinSclk);
      cfg.pin_mosi = static_cast<int>(config::kDisplayPinMosi);
      cfg.pin_miso = -1;
      cfg.pin_dc = static_cast<int>(config::kDisplayPinDc);
      _bus.config(cfg);
      _panel.setBus(&_bus);
    }
    {
      auto cfg = _panel.config();
      cfg.pin_cs = static_cast<int>(config::kDisplayPinCs);
      cfg.pin_rst = static_cast<int>(config::kDisplayPinRst);
      cfg.invert = config::kDisplayInvert;
      cfg.rgb_order = config::kDisplayRgbOrder;
      // Panel_Device::Config::readable defaults to TRUE, and nothing overrode
      // it. With pin_miso = -1 that makes every anti-aliased primitive --
      // fillSmoothCircle, drawWideLine, VLW glyphs -- route through readRect()
      // once per scanline, issuing a real RAMRD with a clock-rate change over
      // a bus that has no MISO wire. The pixels come back as zeros, so AA edges
      // blend against black instead of the real background. The boot log says
      // it out loud: "spiAttachMISO(): SPI Does not have default pins on
      // ESP32C3!". Inherited from the reference; fixed here.
      cfg.readable = false;
      _panel.config(cfg);
    }
    setPanel(&_panel);
  }
};
