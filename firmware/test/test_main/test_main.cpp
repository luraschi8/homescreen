// The loop nobody tested.
//
// Three shipped bugs lived in this file's seams, and the host suite could not
// see any of them, for one reason: every other test calls ui::renderScene()
// directly, while the DEVICE reaches it through the scheduling below. The
// giant letter, the clock frozen on SIN SERVIDOR, and the panel that had the
// right content and never drew it were all found by flashing hardware and
// looking at it. That is an expensive way to run a test suite.
//
// So these drive loop() itself and assert what reached the glass.
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
#include "../../src/hardware/display.cpp"
#include "../../src/ui/radar_display.cpp"
#include "../../src/ui/status_screens.cpp"
#include "../../src/ui/draw_list.cpp"
#include "../../src/ui/components.cpp"
#include "../../src/services/wifi_setup.cpp"
#include "../../src/main.cpp"

static const char* kClockScene =
    "{\"assigned\":true,\"layout\":\"fill\",\"scene\":\"clock\","
    "\"components\":[{\"c\":\"clock\",\"draw\":["
    "{\"t\":\"text\",\"slot\":\"center\",\"v\":\"14:05\",\"size\":\"xl\"},"
    "{\"t\":\"text\",\"slot\":\"below\",\"v\":\"Madrid\",\"size\":\"sm\"}]}]}";

/** One poll, as the poll task would deliver it. */
static bool poll(const char* body, int code = HTTP_CODE_OK,
                 const char* etag = "\"a\"") {
  g_http.reset();
  g_http.body = body ? body : "";
  g_http.code = code;
  if (etag) g_http.response_headers["ETag"] = etag;
  g_http.response_headers["X-Poll-Seconds"] = "5";
  return services::scene::pollOnce();
}

/**
 * Frames composited since the recorder was last cleared.
 *
 * Counted by the screen CLEAR, not by pushSprite: only the radar composites
 * through a sprite, while an instruction list draws straight to the panel. My
 * first version counted Push, so it counted zero for every clock -- and the
 * "an unchanged clock must not repaint" test passed on 0 == 0 while proving
 * nothing at all. Every frame begins by clearing, whichever path drew it.
 */
static int framesDrawn() {
  int n = 0;
  for (const auto& op : g_gfx.ops) {
    if (op.kind == DrawOp::FillScreen) ++n;
  }
  return n;
}

static void runLoop(int times = 1) {
  for (int i = 0; i < times; ++i) {
    // The loop is rate-limited by kRenderIntervalMs, so time must move or
    // every call after the first declines to composite.
    mockAdvanceMs(config::kRenderIntervalMs + 1);
    loop();
  }
}

void setUp(void) {
  g_nvs.reset(); g_http.reset(); g_gfx.resetAll(); g_wc.reset(); g_wdt.reset();
  g_events.clear();
  g_mutex_on_give = nullptr;
  g_task_create_fail = 0;
  mockSetMs(100000);
  WiFi.status_ = WL_CONNECTED;
  services::server::saveFromString("192.168.1.116:8080");
  services::scene::resetForTest();
  g_font_is_smooth = false;
  // main.cpp's own state is file-static; reset what the tests depend on.
  g_scene_visible = false;
  g_render.reset();
  g_drawn_generation = 0;
  g_last_render_ms = 0;
  g_wifi_down_since = 0;
  g_poll_task_ok = true;             // do not spawn a task in the host build
}

void tearDown(void) {}


void test_a_component_with_nothing_moving_is_still_drawn(void) {
  // THE BUG, as the operator reported it: "the display shows no server" while
  // the poll log said `scene=clock 0 items`. The device had the scene and
  // never drew it again -- shouldRender() keys on AIRCRAFT, and a clock has
  // none, so after the boot frame all three terms were false forever.
  runLoop();                          // boot frame: no content yet
  g_gfx.reset();

  poll(kClockScene);
  runLoop();
  TEST_ASSERT_GREATER_THAN_INT_MESSAGE(0, framesDrawn(),
      "new content must reach the panel even with nothing moving");
  TEST_ASSERT_TRUE_MESSAGE(g_gfx.textContains("14:05"),
      "and it must be the content that just arrived");
}


void test_the_same_content_is_not_redrawn_forever(void) {
  // The other half. Redrawing on every pass would keep a 44ms composite
  // running at 10Hz for a picture that changes once a minute.
  poll(kClockScene);
  runLoop(2);
  g_gfx.reset();
  runLoop(5);
  TEST_ASSERT_EQUAL_INT_MESSAGE(0, framesDrawn(),
      "an unchanged clock must not repaint");
}


void test_a_304_does_not_repaint(void) {
  // A quiet sky answers 304 all day. That must cost nothing.
  poll(kClockScene);
  runLoop(2);
  g_gfx.reset();
  poll("", HTTP_CODE_NOT_MODIFIED, "\"a\"");
  runLoop(3);
  TEST_ASSERT_EQUAL_INT(0, framesDrawn());
}


void test_the_next_minute_is_drawn(void) {
  // The case that matters for a clock: same component, same item count, new
  // text. Anything keying on "did the list change size" would miss it.
  poll(kClockScene);
  runLoop(2);
  g_gfx.reset();
  poll("{\"assigned\":true,\"layout\":\"fill\",\"scene\":\"clock\","
       "\"components\":[{\"c\":\"clock\",\"draw\":["
       "{\"t\":\"text\",\"slot\":\"center\",\"v\":\"14:06\",\"size\":\"xl\"}]}]}",
       HTTP_CODE_OK, "\"b\"");
  runLoop();
  TEST_ASSERT_TRUE_MESSAGE(g_gfx.textContains("14:06"), "the minute rolled");
}


void test_losing_wifi_stops_claiming_the_picture_is_current(void) {
  // Whatever is on the glass came from a server we can no longer reach.
  poll(kClockScene);
  runLoop(2);
  WiFi.status_ = WL_DISCONNECTED;
  runLoop();
  TEST_ASSERT_FALSE_MESSAGE(g_scene_visible,
      "a disconnected device must not report a live scene");
}


void test_reconnecting_draws_again_rather_than_waiting_for_new_content(void) {
  // After a reconnect the generation has NOT changed -- the content is the
  // same one we already had. Something else has to ask for the frame, or the
  // panel stays on whatever the disconnect left there.
  poll(kClockScene);
  runLoop(2);
  WiFi.status_ = WL_DISCONNECTED;
  runLoop();
  g_gfx.reset();

  WiFi.status_ = WL_CONNECTED;
  mockAdvanceMs(config::kWifiDownGraceMs + config::kWifiReconnectIntervalMs + 1);
  runLoop(3);
  TEST_ASSERT_GREATER_THAN_INT_MESSAGE(0, framesDrawn(),
      "coming back must repaint; the content did not change but the panel did");
}


void test_a_poll_task_that_failed_to_start_is_retried(void) {
  // Task creation can fail under heap pressure right after the 115KB sprite.
  // Without a retry nothing ever polls again and the panel is dead quiet.
  g_poll_task_ok = false;
  g_last_task_retry_ms = 0;
  mockAdvanceMs(config::kPollTaskRetryMs + 1);
  runLoop();
  TEST_ASSERT_TRUE_MESSAGE(g_poll_task_ok, "the poll task must be retried");
}


int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_a_component_with_nothing_moving_is_still_drawn);
  RUN_TEST(test_the_same_content_is_not_redrawn_forever);
  RUN_TEST(test_a_304_does_not_repaint);
  RUN_TEST(test_the_next_minute_is_drawn);
  RUN_TEST(test_losing_wifi_stops_claiming_the_picture_is_current);
  RUN_TEST(test_reconnecting_draws_again_rather_than_waiting_for_new_content);
  RUN_TEST(test_a_poll_task_that_failed_to_start_is_retried);
  return UNITY_END();
}
