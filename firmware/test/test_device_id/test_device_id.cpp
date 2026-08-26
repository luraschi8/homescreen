#include <Arduino.h>
#include <unity.h>
#include <cstring>

#include "../mocks/mock_globals.h"
#include "../../src/services/device_id.cpp"

void test_the_id_is_the_mac_as_lowercase_hex(void) {
  // The server stores the operator's scene assignment against this string, so
  // it must survive reboots and reflashes.
  TEST_ASSERT_EQUAL_STRING("aabb00112233", services::deviceId());
}

void test_the_id_is_computed_once(void) {
  const char* first = services::deviceId();
  WiFi.mac_[0] = 0xFF;                       // a MAC cannot really change
  TEST_ASSERT_EQUAL_STRING(first, services::deviceId());
  TEST_ASSERT_EQUAL_UINT(12, strlen(services::deviceId()));
}

void setUp(void) {}
void tearDown(void) {}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_the_id_is_the_mac_as_lowercase_hex);
  RUN_TEST(test_the_id_is_computed_once);
  return UNITY_END();
}
