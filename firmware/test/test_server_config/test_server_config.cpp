#include <Arduino.h>
#include <unity.h>
#include <cstring>

#include "../mocks/mock_globals.h"
#include "../../src/services/server_config.cpp"

using namespace services::server;

void setUp(void) { g_nvs.reset(); }
void tearDown(void) {}

void test_a_bare_host_takes_the_default_port(void) {
  TEST_ASSERT_TRUE(saveFromString("192.168.1.116"));
  load();
  TEST_ASSERT_EQUAL_STRING("192.168.1.116", host());
  TEST_ASSERT_EQUAL_UINT16(8080, port());
  TEST_ASSERT_EQUAL_STRING("http://192.168.1.116:8080", baseUrl());
}

void test_an_explicit_port_is_kept(void) {
  TEST_ASSERT_TRUE(saveFromString("dashboard.local:9000"));
  load();
  TEST_ASSERT_EQUAL_UINT16(9000, port());
  TEST_ASSERT_EQUAL_STRING("http://dashboard.local:9000", baseUrl());
}

void test_a_pasted_url_is_accepted_because_that_is_what_people_paste(void) {
  TEST_ASSERT_TRUE(saveFromString("http://192.168.1.116:8080/home"));
  load();
  TEST_ASSERT_EQUAL_STRING("192.168.1.116", host());
  TEST_ASSERT_EQUAL_UINT16(8080, port());
}

void test_https_is_refused_rather_than_silently_downgraded(void) {
  // This image has no TLS. Accepting the string and connecting in the clear
  // would be a lie told to whoever typed it.
  TEST_ASSERT_FALSE(saveFromString("https://dashboard.local"));
}

void test_junk_is_refused_and_the_previous_value_survives(void) {
  TEST_ASSERT_TRUE(saveFromString("192.168.1.116"));
  const char* bad[] = {"", "   ", "host:99999", "host:0", "host:abc", ":8080"};
  for (const char* b : bad) {
    TEST_ASSERT_FALSE_MESSAGE(saveFromString(b), b);
  }
  TEST_ASSERT_FALSE(saveFromString(nullptr));
  load();
  TEST_ASSERT_EQUAL_STRING("192.168.1.116", host());
}

void test_an_unconfigured_device_falls_back_to_the_compiled_default(void) {
  load();
  TEST_ASSERT_EQUAL_STRING("dashboard.local", host());
  TEST_ASSERT_EQUAL_UINT16(8080, port());
}

void test_saving_takes_effect_without_a_reboot(void) {
  // The portal save callback cannot reboot, so a save that only reaches NVS
  // leaves the running firmware pointed at the old server until someone
  // power-cycles it.
  load();
  TEST_ASSERT_TRUE(saveFromString("10.0.0.9:1234"));
  TEST_ASSERT_EQUAL_STRING("10.0.0.9", host());
  TEST_ASSERT_EQUAL_STRING("http://10.0.0.9:1234", baseUrl());
}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_a_bare_host_takes_the_default_port);
  RUN_TEST(test_an_explicit_port_is_kept);
  RUN_TEST(test_a_pasted_url_is_accepted_because_that_is_what_people_paste);
  RUN_TEST(test_https_is_refused_rather_than_silently_downgraded);
  RUN_TEST(test_junk_is_refused_and_the_previous_value_survives);
  RUN_TEST(test_an_unconfigured_device_falls_back_to_the_compiled_default);
  RUN_TEST(test_saving_takes_effect_without_a_reboot);
  return UNITY_END();
}
