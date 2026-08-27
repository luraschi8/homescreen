#pragma once

namespace ui {

/**
 * What this firmware can draw.
 *
 * `kDeclaredComponents` is sent to the server on every poll as `components=`.
 * The server drops anything a scene asks for that is not in this list and
 * records the omission in the fleet view (spec §5.5). So this string and the
 * switch in renderScene() must never disagree: declaring more than we draw puts
 * a hole on the glass and nothing in the fleet view, which is the silent
 * failure that rule exists to prevent. A test asserts the declared string is
 * what the client actually sends.
 */
enum class ComponentKind { kNone, kRadar, kUnknown };

inline constexpr char kDeclaredComponents[] = "radar";

ComponentKind componentKindFromName(const char* c);

/**
 * Draw whatever the scene client currently holds. Returns false if the frame
 * could not be composed -- the aircraft list was locked -- so the caller
 * retries rather than latching, because a skipped clearing frame strands the
 * last targets on the panel.
 */
bool renderScene();

}  // namespace ui
