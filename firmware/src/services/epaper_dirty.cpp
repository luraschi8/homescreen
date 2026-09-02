#include "epaper_dirty.h"

#include <stdio.h>
#include <string.h>

namespace epaper {

DirtyPlan parseDirty(const char* header, int16_t panel_w, int16_t panel_h) {
  DirtyPlan plan;
  if (header == nullptr) {
    return plan;                  // absent: the server wants a full draw
  }
  // Present but empty is MEANINGFUL and different from absent: the server
  // diffed the two frames and nothing moved.
  plan.known = true;
  const char* at = header;
  while (*at != '\0') {
    if (plan.count >= kMaxDirtyRects) {
      // More rectangles than we agreed to accept. Refusing the whole plan
      // beats drawing part of the frame and leaving the rest stale.
      return DirtyPlan{};
    }
    int x = 0, y = 0, w = 0, h = 0, used = 0;
    if (sscanf(at, "%d,%d,%d,%d%n", &x, &y, &w, &h, &used) != 4) {
      return DirtyPlan{};         // malformed: fall back to a full draw
    }
    // Trust nothing off the wire: these coordinates reach GxEPD2, and one
    // outside the panel writes past its framebuffer.
    if (x < 0 || y < 0 || w <= 0 || h <= 0 ||
        x > panel_w - w || y > panel_h - h) {
      return DirtyPlan{};
    }
    plan.rects[plan.count++] = Rect{(int16_t)x, (int16_t)y,
                                    (int16_t)w, (int16_t)h};
    at += used;
    if (*at == ';') {
      ++at;
      if (*at == '\0') {
        return DirtyPlan{};       // trailing separator: malformed
      }
    } else if (*at != '\0') {
      return DirtyPlan{};         // junk between rectangles
    }
  }
  return plan;
}

}  // namespace epaper
