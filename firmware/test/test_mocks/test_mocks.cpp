// The mocks are the ground the whole suite stands on. A mock that silently does
// nothing turns every test above it green for the wrong reason.
#include <Arduino.h>
#include <unity.h>
#include <Preferences.h>
#include <HTTPClient.h>
#include <WiFiManager.h>
#include <freertos/semphr.h>
#include <esp_task_wdt.h>
#include <cstring>

#include "../mocks/mock_globals.h"

void setUp(void) {
  g_nvs.reset(); g_http.reset(); g_gfx.reset(); g_wc.reset(); g_wdt.reset();
  g_mutex_on_give = nullptr;
}
void tearDown(void) { g_mutex_on_give = nullptr; }

void test_preferences_round_trips_strings_and_ushorts(void) {
  Preferences p;
  TEST_ASSERT_TRUE(p.begin("ns", false));
  p.putString("host", "192.168.1.116");
  p.putUShort("port", 8080);
  p.end();
  // The read-only open is the one that mattered: a separate map would be
  // invisible to namespaceExists() and this begin() would return false.
  Preferences r;
  TEST_ASSERT_TRUE(r.begin("ns", true));
  TEST_ASSERT_EQUAL_STRING("192.168.1.116", r.getString("host", "").c_str());
  TEST_ASSERT_EQUAL_UINT16(8080, r.getUShort("port", 0));
  TEST_ASSERT_EQUAL_STRING("fallback", r.getString("absent", "fallback").c_str());
  r.end();
}

void test_a_string_is_never_misread_as_a_number(void) {
  Preferences p; p.begin("ns", false);
  p.putString("k", "not a number");
  TEST_ASSERT_EQUAL_UINT16(77, p.getUShort("k", 77));
}

void test_a_read_only_open_refuses_writes(void) {
  Preferences w; w.begin("ns", false); w.putString("host", "first"); w.end();
  Preferences r; r.begin("ns", true); r.putString("host", "second"); r.end();
  Preferences c; c.begin("ns", true);
  TEST_ASSERT_EQUAL_STRING("first", c.getString("host", "").c_str());
}

void test_the_http_mock_serves_headers_and_records_the_conditional(void) {
  g_http.response_headers["ETag"] = "\"abc\"";
  g_http.response_headers["X-Poll-Seconds"] = "30";
  WiFiClient c;
  HTTPClient h;
  h.begin(c, "http://x/y");
  h.addHeader("If-None-Match", "\"prev\"");
  h.GET();
  TEST_ASSERT_EQUAL_STRING("\"abc\"", h.header("ETag").c_str());
  TEST_ASSERT_EQUAL_STRING("30", h.header("X-Poll-Seconds").c_str());
  TEST_ASSERT_EQUAL_STRING("", h.header("Absent").c_str());
  TEST_ASSERT_EQUAL_STRING("\"prev\"", g_http.last_if_none_match.c_str());
}

void test_the_socket_teardown_counter_survives_an_http_reset(void) {
  // g_http.reset() is the poll helper's first statement. A counter cleared by
  // it would be zeroed between a test's sample and its assertion.
  WiFiClient c;
  c.stop();
  TEST_ASSERT_EQUAL_INT(1, g_wc.stop_calls);
  g_http.reset();
  TEST_ASSERT_EQUAL_INT(1, g_wc.stop_calls);
}

void test_wifi_reports_a_mac_and_an_rssi(void) {
  uint8_t mac[6] = {0};
  WiFi.macAddress(mac);
  TEST_ASSERT_EQUAL_UINT8(0xAA, mac[0]);
  TEST_ASSERT_EQUAL_UINT8(0x33, mac[5]);
  TEST_ASSERT_EQUAL_INT(-58, WiFi.RSSI());
}

void test_the_parameter_registry_survives_a_stats_reset(void) {
  // attachPortalParams() runs once per binary; setUp resets g_wm every test.
  // A registry on g_wm would be empty by the time any test looked.
  WiFiManagerParameter p("server", "Server", "", 64);
  WiFiManager wm;
  wm.addParameter(&p);
  g_wm = MockWmStats();
  TEST_ASSERT_TRUE(wmHasParameter("server"));
  TEST_ASSERT_FALSE(wmHasParameter("nope"));
  wmSetParameterValue("server", "192.168.1.116:8080");
  TEST_ASSERT_EQUAL_STRING("192.168.1.116:8080", p.getValue());
  g_wm_params.clear();
}

void test_the_give_hook_sees_the_lock_still_held(void) {
  // Firing after the decrement would always read 0, and the test that depends
  // on it would pass against a clock stamped outside the lock.
  int seen = -1;
  SemaphoreHandle_t m = xSemaphoreCreateMutex();
  g_mutex_on_give = [&seen]() { seen = g_mutex_outstanding; };
  xSemaphoreTake(m, 0);
  xSemaphoreGive(m);
  g_mutex_on_give = nullptr;
  TEST_ASSERT_EQUAL_INT(1, seen);
  TEST_ASSERT_EQUAL_INT(0, g_mutex_outstanding);
  vSemaphoreDelete(m);
}

void test_the_gfx_recorder_can_be_asked_what_was_drawn(void) {
  DrawOp op;
  op.kind = DrawOp::Text;
  op.text = "SIN SERVIDOR";
  op.x = 120;
  g_gfx.ops.push_back(op);
  TEST_ASSERT_TRUE(g_gfx.textContains("SERVIDOR"));
  TEST_ASSERT_FALSE(g_gfx.textContains("nope"));
  TEST_ASSERT_FALSE(g_gfx.textContains(nullptr));
  TEST_ASSERT_EQUAL_INT(120, g_gfx.lastX(DrawOp::Text));
  TEST_ASSERT_EQUAL_INT(-1, g_gfx.lastX(DrawOp::Circle));
}

void test_the_watchdog_mock_records_what_it_was_told(void) {
  esp_task_wdt_init(60, true);
  esp_task_wdt_add(nullptr);
  esp_task_wdt_reset();
  esp_task_wdt_reset();
  TEST_ASSERT_EQUAL_INT(1, g_wdt.inits);
  TEST_ASSERT_EQUAL_INT(1, g_wdt.adds);
  TEST_ASSERT_EQUAL_INT(2, g_wdt.resets);
  TEST_ASSERT_EQUAL_UINT(60, g_wdt.timeout_s);
  TEST_ASSERT_TRUE(g_wdt.panic);
}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_preferences_round_trips_strings_and_ushorts);
  RUN_TEST(test_a_string_is_never_misread_as_a_number);
  RUN_TEST(test_a_read_only_open_refuses_writes);
  RUN_TEST(test_the_http_mock_serves_headers_and_records_the_conditional);
  RUN_TEST(test_the_socket_teardown_counter_survives_an_http_reset);
  RUN_TEST(test_wifi_reports_a_mac_and_an_rssi);
  RUN_TEST(test_the_parameter_registry_survives_a_stats_reset);
  RUN_TEST(test_the_give_hook_sees_the_lock_still_held);
  RUN_TEST(test_the_gfx_recorder_can_be_asked_what_was_drawn);
  RUN_TEST(test_the_watchdog_mock_records_what_it_was_told);
  return UNITY_END();
}
