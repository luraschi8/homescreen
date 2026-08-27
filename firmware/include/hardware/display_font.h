#pragma once

#include <LovyanGFX.hpp>

bool displayFontInit();
bool displayFontIsSmooth();

/** Load embedded VLW font on gfx if smooth fonts are enabled and not already active. */
bool displayFontEnsureLoaded(lgfx::LGFXBase& gfx);

/** VLW: setTextSize scale (1.0 = font point size). Bitmap: no-op — use displayFontSetBitmap. */
void displayFontSetSmoothSize(lgfx::LGFXBase& gfx, float size);

/**
 * Set type to an absolute height in PIXELS, whatever face is loaded.
 *
 * Exists because the function above takes a SCALE and reads as if it took a
 * size: passing a resolver's 62px rendered the 15px face at 62x, and the panel
 * showed one letter. Callers that think in pixels -- which is everything
 * driven by an instruction list -- must use this instead.
 */
void displayFontSetPixelHeight(lgfx::LGFXBase& gfx, int px);

/** Bitmap GFXfont fallback; clears any runtime VLW font on this instance. */
void displayFontSetBitmap(lgfx::LGFXBase& gfx, const lgfx::GFXfont* font);
