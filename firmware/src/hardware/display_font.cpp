#include "hardware/display_font.h"

#include "hardware/display.h"

extern "C" {
extern const uint8_t _binary_data_ui_font_vlw_start[] asm(
    "_binary_data_ui_font_vlw_start");
extern const uint8_t _binary_data_ui_font_vlw_end[] asm("_binary_data_ui_font_vlw_end");
}

namespace {

bool s_vlw_loaded = false;

const uint8_t* vlwData() { return _binary_data_ui_font_vlw_start; }

size_t vlwDataLen() {
  return static_cast<size_t>(_binary_data_ui_font_vlw_end -
                               _binary_data_ui_font_vlw_start);
}

/**
 * Faces large type can start from, smallest first.
 *
 * A VLW is a FIXED-SIZE bitmap face -- ours is Noto Sans Bold at 15px -- and
 * LovyanGFX enlarges it by repeating pixels. Asking it for the resolver's xl
 * (62px on this panel) is a 4.1x blow-up, so every glyph pixel becomes a 4x4
 * block: that is exactly what "the time looks pixelated" was. Nothing was
 * wrong with the size, only with the face it was stretched from.
 *
 * These FreeSans faces are compiled into LovyanGFX already, so keeping a
 * ladder of them costs no flash we were not paying anyway, and it lets 62px
 * start from a 56px face instead of a 15px one.
 */
constexpr const lgfx::GFXfont* kBitmapFaces[] = {
    &fonts::FreeSansBold9pt7b,
    &fonts::FreeSansBold12pt7b,
    &fonts::FreeSansBold18pt7b,
    &fonts::FreeSansBold24pt7b,
};

/**
 * How far the VLW may be stretched before a bitmap face is the better trade.
 *
 * The VLW is the only ANTIALIASED face we have; the FreeSans ones are 1-bit.
 * Under a modest stretch smooth edges beat a closer size, which is why small
 * type stays on the VLW. Past it the blocks are the first thing you see.
 */
constexpr float kSmoothMaxScale = 1.6f;

/**
 * How far ABOVE the requested height a face may sit and still be taken.
 *
 * The resolver's lg is 41px on this panel and the 18pt face is 42, so an exact
 * "must not exceed" rule rejects it by one pixel and falls back to the 29px
 * face at 1.41x. Shrinking a face 2% costs nothing visible; stretching the next
 * one down by 41% does.
 */
constexpr float kFitTolerance = 1.05f;

struct FaceMetric {
  const lgfx::GFXfont* gfx;  //!< nullptr means the embedded VLW.
  int natural;               //!< Height at 1:1, measured rather than assumed.
};

FaceMetric s_faces[1 + sizeof(kBitmapFaces) / sizeof(kBitmapFaces[0])];
int s_face_count = 0;

int naturalHeight(lgfx::LGFXBase& gfx) {
  gfx.setTextSize(1.0f);
  return gfx.fontHeight();
}

/** Measure every face once. Heights are a property of the font files, not
 *  constants worth hardcoding -- swapping the embedded VLW would otherwise
 *  silently change every size on the panel. */
void measureFaces(lgfx::LGFXBase& gfx) {
  if (s_face_count > 0) {
    return;
  }
  if (displayFontEnsureLoaded(gfx)) {
    const int h = naturalHeight(gfx);
    if (h > 0) {
      s_faces[s_face_count++] = FaceMetric{nullptr, h};
    }
  }
  for (const lgfx::GFXfont* face : kBitmapFaces) {
    gfx.setFont(face);
    const int h = naturalHeight(gfx);
    if (h > 0) {
      s_faces[s_face_count++] = FaceMetric{face, h};
    }
  }
}

bool vlwActiveOn(const lgfx::LGFXBase& gfx) {
  const lgfx::IFont* font = gfx.getFont();
  return font != nullptr && font->getType() == lgfx::IFont::font_type_t::ft_vlw;
}

}  // namespace

bool displayFontInit() {
  s_vlw_loaded = vlwDataLen() > 0 &&
                 tft.loadFont(vlwData(), lgfx::IFont::font_type_t::ft_vlw);
  if (!s_vlw_loaded) {
    Serial.println("Smooth font load failed — using bitmap fallback");
  }
  return s_vlw_loaded;
}

bool displayFontIsSmooth() { return s_vlw_loaded; }

bool displayFontEnsureLoaded(lgfx::LGFXBase& gfx) {
  if (!s_vlw_loaded) {
    return false;
  }
  if (vlwActiveOn(gfx)) {
    return true;
  }
  return gfx.loadFont(vlwData(), lgfx::IFont::font_type_t::ft_vlw);
}

void displayFontSetSmoothSize(lgfx::LGFXBase& gfx, float size) {
  gfx.setTextSize(size);
}

bool asciiOnly(const char* text) {
  if (text == nullptr) return true;
  for (const unsigned char* p = reinterpret_cast<const unsigned char*>(text);
       *p != 0; ++p) {
    if (*p > 0x7E) return false;
  }
  return true;
}

void displayFontSetPixelHeight(lgfx::LGFXBase& gfx, int px, const char* text) {
  if (px <= 0) {
    return;
  }
  // The FreeSans faces cover 0x20-0x7E and nothing else; the embedded VLW is
  // the only one carrying a degree sign. Picking the closest-sized face
  // without asking whether it can DRAW the string put a blank box where "32°C"
  // should be -- while "31° / 33°" a few pixels away was fine, because at that
  // size the ladder had already chosen the VLW. Text that needs a glyph only
  // the smooth face has, gets the smooth face, blockiness and all.
  const bool ascii = asciiOnly(text);
  measureFaces(gfx);
  if (s_face_count == 0) {
    return;
  }

  // The face that reaches px with the least enlargement, preferring the
  // antialiased one while it is only mildly stretched.
  const FaceMetric* chosen = nullptr;
  for (int i = 0; i < s_face_count; ++i) {
    const FaceMetric& face = s_faces[i];
    if (!ascii && face.gfx != nullptr) {
      continue;                          // this face cannot draw the string
    }
    if (face.gfx == nullptr &&
        (!ascii ||
         static_cast<float>(px) <= static_cast<float>(face.natural) * kSmoothMaxScale)) {
      chosen = &face;
      break;
    }
    if (static_cast<float>(face.natural) <= static_cast<float>(px) * kFitTolerance &&
        (chosen == nullptr || face.natural > chosen->natural)) {
      chosen = &face;
    }
  }
  if (chosen == nullptr) {
    // Smaller than every face we have: take the smallest and shrink that.
    chosen = &s_faces[0];
    for (int i = 1; i < s_face_count; ++i) {
      if (s_faces[i].natural < chosen->natural) {
        chosen = &s_faces[i];
      }
    }
  }

  if (chosen->gfx == nullptr) {
    displayFontEnsureLoaded(gfx);
  } else {
    gfx.setFont(chosen->gfx);
  }
  gfx.setTextSize(static_cast<float>(px) / static_cast<float>(chosen->natural));
}

void displayFontSetBitmap(lgfx::LGFXBase& gfx, const lgfx::GFXfont* font) {
  gfx.setFont(font);
  gfx.setTextSize(1);
}
