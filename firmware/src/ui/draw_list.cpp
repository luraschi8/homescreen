#include "ui/draw_list.h"

#include <ArduinoJson.h>

#include <cmath>
#include <cstring>

namespace ui::drawlist {
namespace {

struct SlotFrac { const char* name; float frac; };
// Same table as homescreen/draw.py SLOTS, same order, same values. On a circle
// the usable width narrows towards the edges, which is why rim_* sit at
// 0.12/0.88 rather than hard against them.
constexpr SlotFrac kSlots[] = {
    {"rim_top", 0.12f}, {"above", 0.34f},      {"center", 0.50f},
    {"below", 0.66f},   {"rim_bottom", 0.88f},
};

struct SizeFrac { const char* name; float frac; };
// Same table as homescreen/draw.py SIZES.
constexpr SizeFrac kSizes[] = {
    {"xl", 0.26f}, {"lg", 0.17f}, {"md", 0.11f},
    {"sm", 0.075f}, {"xs", 0.055f},
};

uint8_t toneFromName(const char* t) {
  if (t == nullptr) return kNormal;
  if (strcmp(t, "dim") == 0) return kDim;
  if (strcmp(t, "good") == 0) return kGood;
  if (strcmp(t, "bad") == 0) return kBad;
  if (strcmp(t, "accent") == 0) return kAccent;
  if (strcmp(t, "warn") == 0) return kWarn;
  if (strcmp(t, "cool") == 0) return kCool;
  if (strcmp(t, "hot") == 0) return kHot;
  return kNormal;                       // unknown tones are not invented
}

/** Python's round() is banker's rounding; C's roundf() is half-away-from-zero.
 *  Every fraction here lands well clear of .5 for the panel sizes in use, but
 *  the parity fixture is what proves that rather than this comment. */
int scaled(float frac, int extent) {
  return static_cast<int>(roundf(frac * static_cast<float>(extent)));
}

}  // namespace

int slotY(const char* slot, int h) {
  for (const SlotFrac& s : kSlots) {
    if (slot != nullptr && strcmp(slot, s.name) == 0) {
      return scaled(s.frac, h);
    }
  }
  return scaled(0.50f, h);              // unknown slot -> centre, never vanish
}

int sizePx(const char* token, int w, int h) {
  const int shortest = w < h ? w : h;
  for (const SizeFrac& s : kSizes) {
    if (token != nullptr && strcmp(token, s.name) == 0) {
      const int px = scaled(s.frac, shortest);
      return px < kMinTextPx ? kMinTextPx : px;
    }
  }
  const int px = scaled(0.11f, shortest);   // unknown size -> md
  return px < kMinTextPx ? kMinTextPx : px;
}

size_t resolve(const char* draw_json, int w, int h, Placement* out,
               size_t out_len) {
  if (draw_json == nullptr || out == nullptr || out_len == 0) {
    return 0;
  }
  JsonDocument doc;
  if (deserializeJson(doc, draw_json) != DeserializationError::Ok) {
    return 0;
  }
  if (!doc.is<JsonArrayConst>()) {
    return 0;
  }
  size_t n = 0;
  for (JsonObjectConst item : doc.as<JsonArrayConst>()) {
    if (n >= out_len || n >= kMaxPlacements) {
      break;
    }
    // Anything we do not recognise is dropped rather than guessed at: a device
    // that invents an instruction and a preview that does not are the same bug
    // seen from two sides.
    if (!item["t"].is<const char*>() ||
        strcmp(item["t"].as<const char*>(), "text") != 0) {
      continue;
    }
    if (!item["v"].is<const char*>()) {
      continue;
    }
    const char* text = item["v"].as<const char*>();
    if (text[0] == '\0') {
      continue;
    }
    Placement& p = out[n];
    p = Placement{};
    p.x = w / 2;
    p.y = slotY(item["slot"].is<const char*>() ? item["slot"].as<const char*>()
                                               : "center", h);
    p.px = sizePx(item["size"].is<const char*>() ? item["size"].as<const char*>()
                                                 : "md", w, h);
    p.tone = toneFromName(item["tone"].is<const char*>()
                              ? item["tone"].as<const char*>()
                              : nullptr);
    strncpy(p.text, text, sizeof(p.text) - 1);
    p.text[sizeof(p.text) - 1] = '\0';
    ++n;
  }
  return n;
}

}  // namespace ui::drawlist
