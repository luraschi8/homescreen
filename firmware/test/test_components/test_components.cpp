// The dispatcher: what a human sees for each state the device can be in.
#include <Arduino.h>
#include <unity.h>
#include <cstring>
#include <string>

#include "fixtures_wire.h"
#include "../mocks/mock_globals.h"
#include "../../src/services/device_id.cpp"
#include "../../src/services/server_config.cpp"
#include "../../src/services/scene_client.cpp"
#include "../../src/services/radar_location.cpp"
#include "../../src/ui/radar_range.cpp"
#include "../../src/ui/radar_geo.cpp"
#include "../../src/ui/runway_overlay.cpp"
#include "../../src/data/large_airports_data.cpp"
// display.cpp is the only definition of `LGFX tft`; display.h declares it
// extern and both radar_display.cpp and status_screens.cpp use it.
#include "../../src/hardware/display.cpp"
#include "../../src/ui/radar_display.cpp"
#include "../../src/ui/status_screens.cpp"
#include "../../src/ui/draw_list.cpp"
#include "../../src/ui/components.cpp"

static bool poll(const char* body, int code = HTTP_CODE_OK) {
  g_http.reset();
  g_http.body = body;
  g_http.code = code;
  g_http.response_headers["ETag"] = "\"t\"";
  g_http.response_headers["X-Poll-Seconds"] = "5";
  return services::scene::pollOnce();
}

void setUp(void) {
  g_nvs.reset(); g_http.reset(); g_gfx.resetAll(); g_wc.reset(); g_wdt.reset();
  g_mutex_on_give = nullptr;
  mockSetMs(100000);
  WiFi.status_ = WL_CONNECTED;
  services::server::saveFromString("192.168.1.116:8080");
  services::scene::resetForTest();
  services::location::clear();
  // The radar centre must be where the fixture traffic is. Left at the
  // compiled default (Amsterdam) with fixtures over Madrid, every target is
  // ~1,480 km out, clipped by the outer ring, never drawn -- and every drawing
  // assertion below would pass vacuously.
  char lat[24], lon[24];
  snprintf(lat, sizeof(lat), "%.6f", kWireHomeLat);
  snprintf(lon, sizeof(lon), "%.6f", kWireHomeLon);
  services::location::saveFromStrings(lat, lon);
  ui::radar::rangeInit();
}
void tearDown(void) { g_mutex_on_give = nullptr; }

// --- the declaration and the switch must never disagree ---------------------

void test_the_declared_list_matches_what_we_can_actually_draw(void) {
  // `draw_list` is a CAPABILITY, not a component. It says "send me any
  // instruction list", which is what the dispatcher has always done -- it
  // draws anything it does not recognise, provided the component ships a draw
  // list. Naming components one by one here made every new component on the
  // server a firmware release, for a device that never needed to know their
  // names.
  TEST_ASSERT_EQUAL_STRING("radar,draw_list", ui::kDeclaredComponents);
  TEST_ASSERT_EQUAL_STRING(config::kDeclaredComponents,
                           ui::kDeclaredComponents);
  TEST_ASSERT_EQUAL(ui::ComponentKind::kRadar,
                    ui::componentKindFromName("radar"));
  TEST_ASSERT_EQUAL(ui::ComponentKind::kUnknown,
                    ui::componentKindFromName("text"));
  TEST_ASSERT_EQUAL(ui::ComponentKind::kNone, ui::componentKindFromName(""));
  TEST_ASSERT_EQUAL(ui::ComponentKind::kNone,
                    ui::componentKindFromName(nullptr));
}

void test_the_declared_list_is_what_the_client_actually_sends(void) {
  // Two strings compiled into different files that must agree: one goes in the
  // URL, one drives the switch. If they drift the server drops our component
  // and the fleet view says so, but the glass just shows nothing.
  poll(kWireAssigned);
  const std::string want = std::string("components=") + ui::kDeclaredComponents;
  TEST_ASSERT_NOT_EQUAL_MESSAGE(std::string::npos, g_http.last_url.find(want),
                                want.c_str());
}

// --- each state a human can be looking at -----------------------------------

void test_a_device_that_never_reached_the_server_says_so_with_the_address(void) {
  // Before any reply. A blank round screen looks exactly like a dead one, and
  // the likeliest cause is the wrong address in the portal -- so show it.
  TEST_ASSERT_TRUE(ui::renderScene());
  TEST_ASSERT_TRUE(g_gfx.textContains("SIN SERVIDOR"));
  TEST_ASSERT_TRUE_MESSAGE(g_gfx.textContains("192.168.1.116"),
                           "the address being tried is the actionable detail");
}

void test_an_unassigned_device_shows_its_id_and_the_servers_message(void) {
  // Spec 6.1: a newly flashed board tells you what to type into the fleet view.
  poll(kWireUnassigned);
  g_gfx.reset();
  TEST_ASSERT_TRUE(ui::renderScene());
  TEST_ASSERT_TRUE(g_gfx.textContains("SIN ASIGNAR"));
  TEST_ASSERT_TRUE_MESSAGE(g_gfx.textContains("aabb00112233"),
                           "the hw id is what the operator types");
  TEST_ASSERT_TRUE_MESSAGE(g_gfx.textContains("sin asignar"),
                           "the server's own words reach the glass");
}

void test_an_assigned_radar_scene_draws_the_radar(void) {
  poll(kWireAssigned);
  g_gfx.reset();
  TEST_ASSERT_TRUE(ui::renderScene());
  TEST_ASSERT_TRUE_MESSAGE(g_gfx.count(DrawOp::Circle) > 0, "no rings drawn");
  TEST_ASSERT_TRUE_MESSAGE(g_gfx.count(DrawOp::Triangle) > 0,
                           "no aircraft drawn -- is the radar centre right?");
  TEST_ASSERT_FALSE_MESSAGE(g_gfx.textContains("SIN"),
                            "a working radar must show no status screen");
}

void test_a_component_we_cannot_draw_says_so_instead_of_leaving_a_hole(void) {
  // The server should never send one -- it drops undeclared components -- but a
  // blank panel is the worst possible response to a server that does.
  poll("{\"assigned\":true,\"layout\":\"fill\",\"scene\":\"ticker\","
       "\"components\":[{\"c\":\"text\",\"slot\":\"center\",\"text\":\"x\"}]}");
  g_gfx.reset();
  TEST_ASSERT_TRUE(ui::renderScene());
  TEST_ASSERT_TRUE(g_gfx.textContains("no soportada"));
}

// --- degradation ------------------------------------------------------------

void test_an_expired_picture_drops_the_targets_but_keeps_the_rings(void) {
  // Rings-only says "alive and oriented, but I do not trust this data". A blank
  // screen says nothing at all.
  poll(kWireAssigned);
  mockAdvanceMs(61000);                       // past the contact bound
  g_gfx.reset();
  ui::renderScene();
  TEST_ASSERT_FALSE_MESSAGE(g_gfx.textContains("IBE3221"),
                            "expired traffic must not be shown as live");
  TEST_ASSERT_TRUE_MESSAGE(g_gfx.count(DrawOp::Circle) > 0,
                           "the rings must survive so the panel is not blank");
}

void test_a_stale_server_feed_drops_the_targets_too(void) {
  // The server is answering; only its own feed is old. kWireFeedStale is that
  // exact state taken from the real server: feed_ok true, feed_age 90.
  poll(kWireAssigned);
  poll(kWireFeedStale);
  g_gfx.reset();
  ui::renderScene();
  TEST_ASSERT_FALSE(g_gfx.textContains("IBE3221"));
  TEST_ASSERT_TRUE(g_gfx.count(DrawOp::Circle) > 0);
}

// --- the glass is 240 px across ---------------------------------------------

void test_nothing_drawn_on_a_status_screen_runs_off_the_glass(void) {
  // The server's real message is 43 bytes, ~259 px unfitted, on a 240 px round
  // panel. Measured with the same textWidth() the fitter uses, under the same
  // font -- comparing against a byte count would be a different metric and
  // would pass while text overflowed.
  poll(kWireUnassigned);
  g_gfx.reset();
  ui::renderScene();
  bool saw_text = false;
  for (const auto& op : g_gfx.ops) {
    if (op.kind != DrawOp::Text) continue;
    saw_text = true;
    TEST_ASSERT_LESS_OR_EQUAL_MESSAGE(220, tft.textWidth(op.text.c_str()),
                                      op.text.c_str());
  }
  TEST_ASSERT_TRUE_MESSAGE(saw_text, "precondition: something was drawn");
}

void test_a_long_message_is_truncated_without_splitting_a_character(void) {
  // The messages are UTF-8 Spanish. A truncation that lands inside a multi-byte
  // sequence emits a partial character, which renders as a box or nothing.
  poll("{\"assigned\":false,\"layout\":\"fill\",\"scene\":\"unassigned\","
       "\"components\":[],\"message\":\"\\u00e1\\u00e9\\u00ed\\u00f3\\u00fa "
       "\\u00e1\\u00e9\\u00ed\\u00f3\\u00fa \\u00e1\\u00e9\\u00ed\\u00f3\\u00fa "
       "\\u00e1\\u00e9\\u00ed\\u00f3\\u00fa \\u00e1\\u00e9\\u00ed\\u00f3\\u00fa\"}");
  g_gfx.reset();
  ui::renderScene();
  for (const auto& op : g_gfx.ops) {
    if (op.kind != DrawOp::Text) continue;
    // Every byte must belong to a complete UTF-8 sequence.
    const std::string& t = op.text;
    for (size_t i = 0; i < t.size();) {
      const unsigned char c = static_cast<unsigned char>(t[i]);
      size_t len = c < 0x80 ? 1 : (c >> 5) == 0x6 ? 2 : (c >> 4) == 0xE ? 3 : 4;
      TEST_ASSERT_TRUE_MESSAGE(i + len <= t.size(),
                               "a multi-byte character was cut in half");
      i += len;
    }
  }
}


// --- components drawn from an instruction list -------------------------------
// The firmware does not know what a clock is. It executes what it is sent, and
// homescreen/draw.py executes the same list for the preview -- which is the
// only reason a preview is worth showing for a screen the server never draws.

static const char* kClockScene =
    "{\"assigned\":true,\"layout\":\"fill\",\"scene\":\"clock\","
    "\"components\":[{\"c\":\"clock\",\"draw\":["
    "{\"t\":\"text\",\"slot\":\"center\",\"v\":\"22:53\",\"size\":\"xl\"},"
    "{\"t\":\"text\",\"slot\":\"below\",\"v\":\"Madrid\",\"size\":\"sm\","
    "\"tone\":\"dim\"}]}]}";

void test_a_component_with_an_instruction_list_is_drawn_without_knowing_it(void) {
  poll(kClockScene);
  g_gfx.reset();
  TEST_ASSERT_TRUE(ui::renderScene());
  TEST_ASSERT_TRUE(g_gfx.textContains("22:53"));
  TEST_ASSERT_TRUE(g_gfx.textContains("Madrid"));
  TEST_ASSERT_FALSE_MESSAGE(g_gfx.textContains("SIN"),
                            "a drawable component must not show a status screen");
}

void test_the_firmware_declares_it_can_draw_instruction_lists(void) {
  // The declaration and the dispatcher must agree, or the server drops a
  // component we could have drawn -- or sends one we cannot.
  TEST_ASSERT_NOT_NULL(strstr(ui::kDeclaredComponents, "draw_list"));
  TEST_ASSERT_NOT_NULL(strstr(ui::kDeclaredComponents, "radar"));
  poll(kClockScene);
  TEST_ASSERT_EQUAL(ui::ComponentKind::kDrawList,
                    ui::componentKindFromName("clock"));
}

void test_a_component_this_firmware_has_never_heard_of_is_still_drawn(void) {
  // The point of declaring a capability instead of a list: a component that
  // did not exist when this binary was built draws anyway, because the device
  // executes instructions rather than recognising names. If this fails, every
  // new component is a reflash.
  poll("{\"assigned\":true,\"layout\":\"fill\",\"scene\":\"weather\","
       "\"components\":[{\"c\":\"weather\",\"draw\":["
       "{\"t\":\"text\",\"slot\":\"center\",\"v\":\"21\\u00b0\","
       "\"size\":\"xl\"},"
       "{\"t\":\"text\",\"slot\":\"below\",\"v\":\"Madrid\","
       "\"size\":\"sm\"}]}]}");
  g_gfx.reset();
  TEST_ASSERT_TRUE(ui::renderScene());
  TEST_ASSERT_TRUE_MESSAGE(g_gfx.textContains("Madrid"),
                           "an unknown component with a draw list must draw");
  TEST_ASSERT_EQUAL(ui::ComponentKind::kDrawList,
                    ui::componentKindFromName("weather"));
}

void test_instructions_land_where_the_resolver_says(void) {
  // The dispatcher must not do layout of its own: a second opinion here is how
  // the preview and the glass drift apart.
  poll(kClockScene);
  g_gfx.reset();
  ui::renderScene();
  ui::drawlist::Placement want[ui::drawlist::kMaxPlacements];
  const size_t n = ui::drawlist::resolve(services::scene::drawJson(),
                                         config::kDisplayWidth,
                                         config::kDisplayHeight, want,
                                         ui::drawlist::kMaxPlacements);
  TEST_ASSERT_EQUAL_UINT(2, n);
  for (size_t i = 0; i < n; ++i) {
    bool found = false;
    for (const auto& op : g_gfx.ops) {
      if (op.kind == DrawOp::Text && op.text == want[i].text &&
          op.x == want[i].x && op.y == want[i].y) {
        found = true;
        break;
      }
    }
    char m[96];
    snprintf(m, sizeof(m), "'%s' not drawn at (%d,%d)", want[i].text,
             want[i].x, want[i].y);
    TEST_ASSERT_TRUE_MESSAGE(found, m);
  }
}

void test_tones_reach_the_pen(void) {
  poll("{\"assigned\":true,\"layout\":\"fill\",\"scene\":\"x\","
       "\"components\":[{\"c\":\"x\",\"draw\":["
       "{\"t\":\"text\",\"slot\":\"above\",\"v\":\"up\",\"tone\":\"good\"},"
       "{\"t\":\"text\",\"slot\":\"below\",\"v\":\"dn\",\"tone\":\"bad\"}]}]}");
  g_gfx.reset();
  ui::renderScene();
  uint16_t up = 0, dn = 0;
  for (const auto& op : g_gfx.ops) {
    if (op.kind != DrawOp::Text) continue;
    if (op.text == "up") up = op.color;
    if (op.text == "dn") dn = op.color;
  }
  TEST_ASSERT_NOT_EQUAL_MESSAGE(up, dn, "good and bad must not share a pen");
}

void test_an_empty_instruction_list_says_so_rather_than_blanking(void) {
  poll("{\"assigned\":true,\"layout\":\"fill\",\"scene\":\"x\","
       "\"components\":[{\"c\":\"x\",\"draw\":[]}]}");
  g_gfx.reset();
  TEST_ASSERT_TRUE(ui::renderScene());
  TEST_ASSERT_TRUE(g_gfx.textContains("escena vacia"));
}

void test_the_radar_is_untouched_by_any_of_this(void) {
  // The whole point of keeping radar bespoke: it must still project and
  // dead-reckon exactly as before.
  poll(kWireAssigned);
  g_gfx.reset();
  TEST_ASSERT_TRUE(ui::renderScene());
  TEST_ASSERT_EQUAL(ui::ComponentKind::kRadar,
                    ui::componentKindFromName("radar"));
  TEST_ASSERT_TRUE_MESSAGE(g_gfx.count(DrawOp::Circle) > 0, "no rings");
  TEST_ASSERT_TRUE_MESSAGE(g_gfx.count(DrawOp::Triangle) > 0, "no aircraft");
}

void test_switching_between_a_drawlist_and_the_radar_leaves_no_residue(void) {
  // The instruction list is file-static; a stale one after switching to radar
  // would put a clock's text over the rings.
  poll(kClockScene);
  ui::renderScene();
  poll(kWireAssigned);
  g_gfx.reset();
  ui::renderScene();
  TEST_ASSERT_FALSE_MESSAGE(g_gfx.textContains("22:53"),
                            "the previous component's text survived");
  TEST_ASSERT_TRUE(g_gfx.count(DrawOp::Triangle) > 0);
}

void test_a_draw_list_too_large_to_hold_is_refused_not_truncated(void) {
  // Truncated JSON parses as garbage, and half a screen is worse than an honest
  // empty one.
  std::string big = "{\"assigned\":true,\"layout\":\"fill\",\"scene\":\"x\","
                    "\"components\":[{\"c\":\"x\",\"draw\":[";
  for (int i = 0; i < 60; ++i) {
    if (i) big += ",";
    big += "{\"t\":\"text\",\"slot\":\"center\",\"v\":\""
           "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"size\":\"sm\"}";
  }
  big += "]}]}";
  g_http.reset();
  g_http.body = big;
  g_http.code = HTTP_CODE_OK;
  g_http.content_length_override = 1;
  g_http.response_headers["ETag"] = "\"t\"";
  g_http.response_headers["X-Poll-Seconds"] = "5";
  TEST_ASSERT_TRUE(services::scene::pollOnce());
  g_gfx.reset();
  TEST_ASSERT_TRUE(ui::renderScene());
  TEST_ASSERT_FALSE_MESSAGE(g_gfx.textContains("aaaa"),
                            "a truncated list must not be drawn");
}


void test_text_is_drawn_at_the_pixel_height_the_resolver_asked_for(void) {
  // The bug this exists for: displayFontSetSmoothSize takes a SCALE FACTOR and
  // reads as if it took a size, so passing the resolver's 62px rendered the
  // 15px face at 62x and the panel showed part of one letter. Every other test
  // asserted WHERE text landed and none asserted HOW BIG, so the host was
  // perfectly happy.
  poll(kClockScene);
  g_gfx.reset();
  ui::renderScene();

  ui::drawlist::Placement want[ui::drawlist::kMaxPlacements];
  const size_t n = ui::drawlist::resolve(services::scene::drawJson(),
                                         config::kDisplayWidth,
                                         config::kDisplayHeight, want,
                                         ui::drawlist::kMaxPlacements);
  TEST_ASSERT_GREATER_THAN_UINT(0, n);
  for (size_t i = 0; i < n; ++i) {
    bool checked = false;
    for (const auto& op : g_gfx.ops) {
      if (op.kind != DrawOp::Text || op.text != want[i].text) continue;
      char m[128];
      snprintf(m, sizeof(m), "'%s' asked for %dpx, drew %dpx", want[i].text,
               want[i].px, op.h);
      // Within a pixel: the scale is a float division onto an integer face.
      TEST_ASSERT_INT_WITHIN_MESSAGE(1, want[i].px, op.h, m);
      checked = true;
      break;
    }
    TEST_ASSERT_TRUE_MESSAGE(checked, "the string was never drawn at all");
  }
}

void test_nothing_a_component_draws_overflows_the_panel(void) {
  // The symptom the operator actually reported: something far too big for the
  // glass. Whatever the cause, this is the assertion that names it.
  poll(kClockScene);
  g_gfx.reset();
  ui::renderScene();
  for (const auto& op : g_gfx.ops) {
    if (op.kind != DrawOp::Text) continue;
    char m[128];
    snprintf(m, sizeof(m), "'%s' is %dx%d on a %dx%d panel", op.text.c_str(),
             op.w, op.h, config::kDisplayWidth, config::kDisplayHeight);
    TEST_ASSERT_LESS_OR_EQUAL_MESSAGE(config::kDisplayHeight, op.h, m);
    TEST_ASSERT_LESS_OR_EQUAL_MESSAGE(config::kDisplayWidth, op.w, m);
  }
}

void test_the_largest_size_token_still_fits_the_round_panel(void) {
  // xl on 240x240 resolves to 62px. A face rendered at 62x that would be 930px.
  poll("{\"assigned\":true,\"layout\":\"fill\",\"scene\":\"x\","
       "\"components\":[{\"c\":\"x\",\"draw\":["
       "{\"t\":\"text\",\"slot\":\"center\",\"v\":\"88:88\","
       "\"size\":\"xl\"}]}]}");
  g_gfx.reset();
  ui::renderScene();
  bool seen = false;
  for (const auto& op : g_gfx.ops) {
    if (op.kind != DrawOp::Text || op.text != "88:88") continue;
    seen = true;
    TEST_ASSERT_LESS_OR_EQUAL_MESSAGE(config::kDisplayHeight, op.h,
                                      "xl must fit the height it is a fraction of");
  }
  TEST_ASSERT_TRUE(seen);
}


void test_large_type_is_not_stretched_from_the_smallest_face(void) {
  // The complaint this exists for: "the time looks very pixeled". The SIZE was
  // right the whole time -- 62px is 62px -- but it was reached by enlarging a
  // 15px face 4.1x, so every glyph pixel became a 4x4 block. Tests that assert
  // where text lands and how tall it ends up cannot see that; only how far the
  // face had to be stretched to get there can.
  g_font_is_smooth = true;
  displayFontSetPixelHeight(tft, 62);
  char m[96];
  snprintf(m, sizeof(m), "62px was reached by stretching a face %.2fx",
           tft.textSize());
  TEST_ASSERT_TRUE_MESSAGE(tft.textSize() <= 2.0f, m);
}

void test_small_type_stays_on_the_antialiased_face(void) {
  // The other half of the trade: the VLW is the only face with antialiasing,
  // and under a mild stretch smooth edges beat a closer-fitting 1-bit face.
  // Without this, "pick whatever is nearest the size" would quietly move the
  // radar's small labels onto bitmaps and make them look worse.
  g_font_is_smooth = true;
  displayFontSetPixelHeight(tft, 18);
  TEST_ASSERT_NULL_MESSAGE(tft.currentFont(),
                           "small type must stay on the smooth face");
}


void test_a_device_nobody_let_in_says_so_on_its_own_glass(void) {
  // The gate has to be visible where someone is standing. Without this a panel
  // plugged in by a guest shows the unassigned screen -- which invites them to
  // pick a scene the server will never serve them -- or worse, whatever it was
  // assigned before it was revoked.
  poll(kWirePending);
  g_gfx.reset();
  TEST_ASSERT_TRUE(ui::renderScene());
  TEST_ASSERT_TRUE_MESSAGE(g_gfx.textContains("esperando"),
                           "the server's own words must reach the glass");
  TEST_ASSERT_TRUE_MESSAGE(g_gfx.textContains("4827") ||
                           g_gfx.textContains("aabb"),
                           "and its id, so a human knows WHICH panel to admit");
}


void test_text_needing_a_glyph_only_the_smooth_face_has_gets_it(void) {
  // Reported from the sofa: "32°C" drew a blank box while "31° / 33°" a few
  // pixels away was fine. The ladder picks the face closest in SIZE, and the
  // FreeSans faces cover 0x20-0x7E only -- the embedded VLW is the one with a
  // degree sign. At xl the ladder chose a bitmap face that could not draw the
  // string; at xs it had already chosen the VLW, which is why one worked.
  g_font_is_smooth = true;
  displayFontSetPixelHeight(tft, 62, "32\u00b0C");
  TEST_ASSERT_NULL_MESSAGE(tft.currentFont(),
                           "non-ASCII must fall back to the smooth face");
  displayFontSetPixelHeight(tft, 62, "32C");
  TEST_ASSERT_NOT_NULL_MESSAGE(tft.currentFont(),
                               "plain ASCII still gets the closest-sized face");
}

void test_the_tone_vocabulary_survives_the_wire(void) {
  // Through resolve(), which is the path a tone actually travels. An unknown
  // tone falls back rather than being invented, so a tone a newer server sends
  // to an older binary is plain text and never a wrong colour.
  ui::drawlist::Placement out[ui::drawlist::kMaxPlacements];
  const size_t n = ui::drawlist::resolve(
      "[{\"t\":\"text\",\"slot\":\"above\",\"v\":\"a\",\"tone\":\"accent\"},"
      "{\"t\":\"text\",\"slot\":\"center\",\"v\":\"h\",\"tone\":\"hot\"},"
      "{\"t\":\"text\",\"slot\":\"below\",\"v\":\"x\",\"tone\":\"chartreuse\"}]",
      240, 240, out, ui::drawlist::kMaxPlacements);
  TEST_ASSERT_EQUAL_UINT(3, n);
  TEST_ASSERT_EQUAL(ui::drawlist::kAccent, out[0].tone);
  TEST_ASSERT_EQUAL(ui::drawlist::kHot, out[1].tone);
  TEST_ASSERT_EQUAL(ui::drawlist::kNormal, out[2].tone);
}


void test_an_icon_arrives_as_primitives_and_is_drawn(void) {
  // The server expands "sun" into circles and lines before it reaches the
  // wire, so this binary draws icons it has never heard of. If it did not,
  // every new icon would be a reflash -- the thing draw_list exists to avoid.
  poll("{\"assigned\":true,\"layout\":\"fill\",\"scene\":\"weather\","
       "\"components\":[{\"c\":\"weather\",\"draw\":["
       // FRACTIONS of the panel, which is what the server actually puts on
       // the wire -- `draw.circle` rounds to 4 decimals and never multiplies.
       // This test used to carry pixels, which is the resolved PREVIEW output
       // pasted onto the unresolved parser: the test agreed with the firmware
       // and both disagreed with the server.
       "{\"t\":\"circle\",\"cx\":0.5,\"cy\":0.35,\"r\":0.075,\"tone\":\"warn\"},"
       "{\"t\":\"line\",\"x1\":0.5,\"y1\":0.1667,\"x2\":0.5,\"y2\":0.2167,"
       "\"w\":0.0154,\"tone\":\"warn\"},"
       "{\"t\":\"tri\",\"p\":[0.25,0.833,0.333,0.833,0.292,0.917],"
       "\"tone\":\"good\"},"
       "{\"t\":\"text\",\"slot\":\"below\",\"v\":\"32\","
       "\"size\":\"xl\"}]}]}");
  g_gfx.reset();
  TEST_ASSERT_TRUE(ui::renderScene());
  int circles = 0, lines = 0, tris = 0;
  for (const auto& op : g_gfx.ops) {
    if (op.kind == DrawOp::Circle || op.kind == DrawOp::SmoothCircle) ++circles;
    if (op.kind == DrawOp::WideLine) ++lines;
    if (op.kind == DrawOp::Triangle) ++tris;
  }
  TEST_ASSERT_GREATER_THAN_INT_MESSAGE(0, circles, "the circle was drawn");
  TEST_ASSERT_GREATER_THAN_INT_MESSAGE(0, lines, "the ray was drawn");
  TEST_ASSERT_GREATER_THAN_INT_MESSAGE(0, tris, "the triangle was drawn");
  TEST_ASSERT_TRUE_MESSAGE(g_gfx.textContains("32"),
                           "and the text still arrived alongside them");
}


void test_a_fraction_becomes_a_pixel_rather_than_truncating_to_zero(void) {
  // The bug this pins: reading `cx` as an int truncated 0.5 to 0, so every
  // shape the server sent landed in the top-left corner with radius 1 and the
  // panel showed a speck. Both suites were green while it shipped, because
  // the firmware fixture had been written in pixels.
  poll("{\"assigned\":true,\"layout\":\"fill\",\"scene\":\"weather\","
       "\"components\":[{\"c\":\"weather\",\"draw\":["
       "{\"t\":\"circle\",\"cx\":0.5,\"cy\":0.5,\"r\":0.25,"
       "\"tone\":\"warn\"}]}]}");
  g_gfx.reset();
  TEST_ASSERT_TRUE(ui::renderScene());
  const DrawOp* circle = nullptr;
  for (const auto& op : g_gfx.ops) {
    if (op.kind == DrawOp::Circle || op.kind == DrawOp::SmoothCircle) {
      circle = &op;
    }
  }
  TEST_ASSERT_NOT_NULL_MESSAGE(circle, "a circle was drawn at all");
  // 240x240 glass: the centre is 120,120 and a quarter-panel radius is 60.
  TEST_ASSERT_INT_WITHIN_MESSAGE(1, 120, circle->x, "cx scaled off the width");
  TEST_ASSERT_INT_WITHIN_MESSAGE(1, 120, circle->y, "cy scaled off the height");
  TEST_ASSERT_INT_WITHIN_MESSAGE(1, 60, circle->r,
                                 "the radius scaled off the short side");
}

void test_a_frame_carries_enough_drawables_for_an_icon_and_its_labels(void) {
  // One icon is nine primitives. The cap was twelve, sized for when every
  // instruction was a line of text, so a sun plus four labels silently lost
  // the last label.
  TEST_ASSERT_GREATER_OR_EQUAL_UINT_MESSAGE(
      20, ui::drawlist::kMaxPlacements,
      "an icon plus a full slot vocabulary must fit");
}

void test_an_absurd_radius_cannot_fill_the_screen(void) {
  // The server is not the only thing that can put a number on this wire.
  ui::drawlist::Placement out[ui::drawlist::kMaxPlacements];
  const size_t n = ui::drawlist::resolve(
      "[{\"t\":\"circle\",\"cx\":120,\"cy\":120,\"r\":999999}]",
      240, 240, out, ui::drawlist::kMaxPlacements);
  TEST_ASSERT_EQUAL_UINT(1, n);
  TEST_ASSERT_LESS_OR_EQUAL_INT(240, out[0].px);
}

void test_a_triangle_without_three_points_is_not_drawn(void) {
  ui::drawlist::Placement out[ui::drawlist::kMaxPlacements];
  TEST_ASSERT_EQUAL_UINT(0, ui::drawlist::resolve(
      "[{\"t\":\"tri\",\"p\":[1,2,3,4]}]", 240, 240, out,
      ui::drawlist::kMaxPlacements));
}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_an_icon_arrives_as_primitives_and_is_drawn);
  RUN_TEST(test_a_fraction_becomes_a_pixel_rather_than_truncating_to_zero);
  RUN_TEST(test_a_frame_carries_enough_drawables_for_an_icon_and_its_labels);
  RUN_TEST(test_an_absurd_radius_cannot_fill_the_screen);
  RUN_TEST(test_a_triangle_without_three_points_is_not_drawn);
  RUN_TEST(test_text_needing_a_glyph_only_the_smooth_face_has_gets_it);
  RUN_TEST(test_the_tone_vocabulary_survives_the_wire);
  RUN_TEST(test_a_device_nobody_let_in_says_so_on_its_own_glass);
  RUN_TEST(test_a_component_with_an_instruction_list_is_drawn_without_knowing_it);
  RUN_TEST(test_the_firmware_declares_it_can_draw_instruction_lists);
  RUN_TEST(test_instructions_land_where_the_resolver_says);
  RUN_TEST(test_text_is_drawn_at_the_pixel_height_the_resolver_asked_for);
  RUN_TEST(test_nothing_a_component_draws_overflows_the_panel);
  RUN_TEST(test_the_largest_size_token_still_fits_the_round_panel);
  RUN_TEST(test_large_type_is_not_stretched_from_the_smallest_face);
  RUN_TEST(test_small_type_stays_on_the_antialiased_face);
  RUN_TEST(test_tones_reach_the_pen);
  RUN_TEST(test_an_empty_instruction_list_says_so_rather_than_blanking);
  RUN_TEST(test_the_radar_is_untouched_by_any_of_this);
  RUN_TEST(test_switching_between_a_drawlist_and_the_radar_leaves_no_residue);
  RUN_TEST(test_a_draw_list_too_large_to_hold_is_refused_not_truncated);
  RUN_TEST(test_the_declared_list_matches_what_we_can_actually_draw);
  RUN_TEST(test_a_component_this_firmware_has_never_heard_of_is_still_drawn);
  RUN_TEST(test_the_declared_list_is_what_the_client_actually_sends);
  RUN_TEST(test_a_device_that_never_reached_the_server_says_so_with_the_address);
  RUN_TEST(test_an_unassigned_device_shows_its_id_and_the_servers_message);
  RUN_TEST(test_an_assigned_radar_scene_draws_the_radar);
  RUN_TEST(test_a_component_we_cannot_draw_says_so_instead_of_leaving_a_hole);
  RUN_TEST(test_an_expired_picture_drops_the_targets_but_keeps_the_rings);
  RUN_TEST(test_a_stale_server_feed_drops_the_targets_too);
  RUN_TEST(test_nothing_drawn_on_a_status_screen_runs_off_the_glass);
  RUN_TEST(test_a_long_message_is_truncated_without_splitting_a_character);
  return UNITY_END();
}
