#pragma once

// What actually changed on the glass since the last frame.
//
// Ghosting is what a partial waveform LEAVES. It is shorter and weaker than a
// full one, so the pigment does not quite arrive; and it is not
// charge-balanced, so residue accumulates in the microcapsule and each update
// moves the particles a little less than the last. Refreshing the FULL window
// in partial mode applies that waveform to every pixel on every update --
// including the ~95% that did not change -- which is why the whole screen
// fogged when only the clock was moving.
//
// The server diffs consecutive frames and names the rectangles in an `X-Dirty`
// header; this parses it. The parsing lives here, apart from `main_epaper.cpp`,
// because it reads UNTRUSTED input off the wire into coordinates that are
// handed straight to GxEPD2 -- an out-of-range rectangle writes past its
// buffer -- and that deserves a test.

#include <stddef.h>
#include <stdint.h>

namespace epaper {

constexpr size_t kMaxDirtyRects = 4;

struct Rect {
  int16_t x, y, w, h;
};

struct DirtyPlan {
  //: False means "refresh everything": the header was absent, or malformed, or
  //: named a rectangle off the panel. A full refresh is always correct -- only
  //: slower, and it is the safe way to fail.
  bool known = false;
  size_t count = 0;
  Rect rects[kMaxDirtyRects];
};

/** Parse `x,y,w,h;x,y,w,h`. `header == nullptr` means the header was absent. */
DirtyPlan parseDirty(const char* header, int16_t panel_w, int16_t panel_h);

}  // namespace epaper
