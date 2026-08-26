#include <Arduino.h>
#include <unity.h>

#include "config.h"
#include "../mocks/mock_globals.h"

void test_the_geometry_matches_what_the_server_is_told(void) {
  TEST_ASSERT_EQUAL_INT(240, config::kDisplayWidth);
  TEST_ASSERT_EQUAL_INT(240, config::kDisplayHeight);
  TEST_ASSERT_EQUAL_INT(16, config::kDisplayDepth);
}

void test_no_feed_url_is_compiled_into_this_firmware(void) {
  // The point of the phase: the device knows a server, not a data source.
  TEST_ASSERT_EQUAL_STRING("dashboard.local", config::kDefaultServerHost);
}

void test_the_body_cap_leaves_room_for_the_parse_to_peak(void) {
  // Measured against the real server: 40 items is 6,274 B and peaks ~29 KB.
  TEST_ASSERT_EQUAL_INT(8192, config::kMaxBodyBytes);
}

void setUp(void) {}
void tearDown(void) {}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_the_geometry_matches_what_the_server_is_told);
  RUN_TEST(test_no_feed_url_is_compiled_into_this_firmware);
  RUN_TEST(test_the_body_cap_leaves_room_for_the_parse_to_peak);
  return UNITY_END();
}
