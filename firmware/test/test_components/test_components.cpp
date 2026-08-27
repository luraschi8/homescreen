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
  TEST_ASSERT_EQUAL_STRING("radar", ui::kDeclaredComponents);
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

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_the_declared_list_matches_what_we_can_actually_draw);
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
