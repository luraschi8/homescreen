#pragma once

#include <cstddef>
#include <cstdint>

namespace ui::drawlist {

//! What kind of thing a placement is.
//!
//! The server expands an icon into PRIMITIVES before it reaches the wire, so a
//! sun arrives as circles and lines. This binary therefore draws every icon
//! that will ever be invented, including ones that did not exist when it was
//! flashed -- the same bargain `draw_list` struck for components.
enum Shape : uint8_t { kText = 0, kCircle, kLine, kTri };

/** One resolved drawable: where it goes, how big, and in what tone. */
struct Placement {
  uint8_t shape;         //!< index into Shape
  int x;                 //!< text anchor, or circle centre, or line start
  int y;
  int px;                //!< text height, circle radius, or line width
  uint8_t tone;          //!< index into Tone
  bool fill;             //!< circles only
  int x2;                //!< lines and triangles
  int y2;
  int x3;                //!< triangles only
  int y3;
  char text[40];
};

//! What a value MEANS. The panel decides how to show it.
//!
//! Order is the wire's: `resolve()` maps the server's tone names onto these by
//! index, so appending is safe and reordering is not.
enum Tone : uint8_t {
  kNormal = 0,  //!< the thing you came to read
  kDim,         //!< its label, its units, its footnote
  kGood,        //!< up, healthy, live
  kBad,         //!< down, failing, expired
  kAccent,      //!< the identity of the thing -- a city, a symbol, a team
  kWarn,        //!< needs attention but is not wrong
  kCool,        //!< cold end of a scale
  kHot,         //!< hot end of a scale
};

/** Room for one screenful. A component sending more is truncated, not honoured. */
//! How many drawables one frame may carry.
//!
//! Was 12, from when every instruction was a line of text. One icon expands to
//! nine primitives server-side, so a weather panel with a sun and four labels
//! is thirteen -- the cap silently ate the last label. Sized for a couple of
//! icons plus a full slot vocabulary, and the buffer is static rather than a
//! local: 40 of these is ~3 KB, which is a third of the loop task's stack.
constexpr size_t kMaxPlacements = 40;

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
