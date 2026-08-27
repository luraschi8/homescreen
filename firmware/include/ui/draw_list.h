#pragma once

#include <cstddef>
#include <cstdint>

namespace ui::drawlist {

/** One resolved drawable: where it goes, how big, and in what tone. */
struct Placement {
  int x;
  int y;
  int px;
  uint8_t tone;          // index into Tone
  char text[40];
};

enum Tone : uint8_t { kNormal = 0, kDim, kGood, kBad };

/** Room for one screenful. A component sending more is truncated, not honoured. */
constexpr size_t kMaxPlacements = 12;

/**
 * Resolve a component's instruction list for this panel.
 *
 * This is the C++ half of `homescreen/draw.py`. Both halves must produce
 * identical placements for identical input, because one draws the preview an
 * operator judges by and the other draws the glass -- and a disagreement is a
 * bug you can only see by holding the two side by side. `test_draw_parity`
 * pins both against one golden fixture generated from the Python.
 *
 * Deliberately dull arithmetic. Everything clever here is a thing to get wrong
 * twice, once in each language.
 */
size_t resolve(const char* draw_json, int w, int h, Placement* out,
               size_t out_len);

/** Slot -> y, as a fraction of panel height. */
int slotY(const char* slot, int h);
/** Size token -> pixels, as a fraction of the panel's SHORT side. */
int sizePx(const char* token, int w, int h);

/** Smallest legible type on these panels (CLAUDE.md's floor). */
constexpr int kMinTextPx = 10;

}  // namespace ui::drawlist
