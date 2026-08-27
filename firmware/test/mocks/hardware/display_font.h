// The real display_font.cpp links against the VLW blob embedded by the build,
// which does not exist on the host. Tests use the bitmap-font path (the same
// fallback the firmware takes when the smooth font fails to load).
#pragma once
#include <LovyanGFX.hpp>
// Face selection names the FreeSans faces, which live here rather than in
// LovyanGFX.hpp -- exactly as they do in the real header.
#include <lgfx/v1/lgfx_fonts.hpp>
extern bool g_font_is_smooth;
inline bool displayFontInit() { return g_font_is_smooth; }
inline bool displayFontIsSmooth() { return g_font_is_smooth; }
inline bool displayFontEnsureLoaded(lgfx::LGFXBase&) { return g_font_is_smooth; }
inline void displayFontSetSmoothSize(lgfx::LGFXBase& g, float s) {
  // The VLW path scales a single face; clearing the bitmap font makes
  // fontHeight() fall back to the size-scaled base, which is what the real
  // smooth font does and what findVlwSizeForHeight() binary-searches over.
  g.setFont(nullptr);
  g.setTextSize(s);
}
inline void displayFontSetBitmap(lgfx::LGFXBase& g, const lgfx::GFXfont* f) {
  g.setFont(f); g.setTextSize(1);
}
/** Absolute pixel height, modelling the real one: measure at 1:1, then scale.
 *  Without this the mock took a pixel count where the firmware's
 *  displayFontSetSmoothSize wanted a SCALE, so passing 62 rendered the 15px
 *  face at 62x -- one letter filling the panel -- and the host saw nothing
 *  wrong. */
inline void displayFontSetPixelHeight(lgfx::LGFXBase& g, int px) {
  if (px <= 0) return;
  // Mirrors src/hardware/display_font.cpp. The natural heights differ from the
  // panel's real ones -- these are the mock's -- but the RULE is the same one,
  // so a change to face selection is visible here.
  struct Face { const lgfx::GFXfont* gfx; int natural; };
  const lgfx::GFXfont* bitmaps[] = {
      &lgfx::v1::fonts::FreeSansBold9pt7b, &lgfx::v1::fonts::FreeSansBold12pt7b,
      &lgfx::v1::fonts::FreeSansBold18pt7b, &lgfx::v1::fonts::FreeSansBold24pt7b};
  Face faces[5];
  int n = 0;
  auto natural = [&g](const lgfx::GFXfont* f) {
    g.setFont(f); g.setTextSize(1.0f); return g.fontHeight();
  };
  if (g_font_is_smooth) {
    const int h = natural(nullptr);
    if (h > 0) faces[n++] = Face{nullptr, h};
  }
  for (const lgfx::GFXfont* f : bitmaps) {
    const int h = natural(f);
    if (h > 0) faces[n++] = Face{f, h};
  }
  if (n == 0) return;

  const Face* chosen = nullptr;
  for (int i = 0; i < n; ++i) {
    if (faces[i].gfx == nullptr && (float)px <= (float)faces[i].natural * 1.6f) {
      chosen = &faces[i];
      break;
    }
    if ((float)faces[i].natural <= (float)px * 1.05f &&
        (!chosen || faces[i].natural > chosen->natural))
      chosen = &faces[i];
  }
  if (!chosen) {
    chosen = &faces[0];
    for (int i = 1; i < n; ++i)
      if (faces[i].natural < chosen->natural) chosen = &faces[i];
  }
  g.setFont(chosen->gfx);
  g.setTextSize((float)px / (float)chosen->natural);
}
