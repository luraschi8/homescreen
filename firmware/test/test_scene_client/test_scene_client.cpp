// Exercises the real scene_client.cpp against the exact bytes the server emits
// (fixtures_wire.h is generated from it by scripts/dump_wire_fixture.py).
#include <Arduino.h>
#include <unity.h>
#include <cmath>
#include <cstring>
#include <string>

#include "fixtures_wire.h"
#include "../mocks/mock_globals.h"
#include "../../src/services/device_id.cpp"
#include "../../src/services/server_config.cpp"
#include "../../src/services/scene_client.cpp"

using namespace services::scene;

static bool poll(const char* body, int code = HTTP_CODE_OK,
                 const char* etag = "\"abc\"", const char* poll_s = "5") {
  g_http.reset();
  g_http.body = body ? body : "";
  g_http.code = code;
  if (etag) g_http.response_headers["ETag"] = etag;
  if (poll_s) g_http.response_headers["X-Poll-Seconds"] = poll_s;
  return pollOnce();
}

void setUp(void) {
  g_nvs.reset();
  g_wc.reset();
  g_wdt.reset();
  g_mutex_on_give = nullptr;
  mockSetMs(100000);
  WiFi.status_ = WL_CONNECTED;
  services::server::saveFromString("192.168.1.116:8080");
  resetForTest();
}
void tearDown(void) { g_mutex_on_give = nullptr; }

// --- the request ------------------------------------------------------------

void test_the_request_declares_everything_the_server_needs(void) {
  // The server only reads a capability LIST when the same request carries w and
  // h; omitting them silently drops our component declaration and the radar
  // comes back as `unsupported` with an empty component list. max_items is what
  // stops an operator's max_aircraft from sending a body we cannot parse.
  poll(kWireAssigned);
  const std::string& url = g_http.last_url;
  const char* needles[] = {"/api/device/", "/scene?", "w=240", "h=240",
                           "depth=16", "max_items=40", "components=radar,clock",
                           "fw=hs-0.1", "aabb00112233"};
  for (const char* n : needles) {
    TEST_ASSERT_NOT_EQUAL_MESSAGE(std::string::npos, url.find(n), n);
  }
  TEST_ASSERT_EQUAL(std::string::npos, url.find("https://"));
}

// --- parsing a real body ----------------------------------------------------

void test_a_real_server_body_becomes_aircraft(void) {
  TEST_ASSERT_TRUE(poll(kWireAssigned));
  TEST_ASSERT_EQUAL_UINT(2, aircraftCount());
  const Aircraft& a = aircraftList()[0];
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 90.0f, a.nose_deg);
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 91.0f, a.track_deg);
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 400.0f, a.gs_knots);
  TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.13f, a.vel_e_km_s);
  TEST_ASSERT_FLOAT_WITHIN(0.0001f, -0.17f, a.vel_n_km_s);
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 7.4f, a.dst_nm);
  TEST_ASSERT_EQUAL_STRING("IBE3221", a.callsign);
  TEST_ASSERT_EQUAL_STRING("A320", a.type);
  TEST_ASSERT_EQUAL_STRING("3675 ft", a.alt);
}

void test_velocities_come_from_the_server_and_are_not_recomputed(void) {
  // The fixture's ve/vn are deliberately NOT consistent with gs=400kt/trk=91:
  // those imply ve~0.206, vn~-0.004. A recompute-from-gs implementation fails
  // this assertion, which is the point of choosing inconsistent numbers.
  poll(kWireAssigned);
  TEST_ASSERT_FLOAT_WITHIN(0.0001f, 0.13f, aircraftList()[0].vel_e_km_s);
  TEST_ASSERT_FLOAT_WITHIN(0.0001f, -0.17f, aircraftList()[0].vel_n_km_s);
}

void test_the_scene_metadata_is_available_to_the_dispatcher(void) {
  poll(kWireAssigned);
  TEST_ASSERT_TRUE(assigned());
  TEST_ASSERT_EQUAL_STRING("planes", sceneName());
  TEST_ASSERT_EQUAL_STRING("radar", componentName());
  TEST_ASSERT_FLOAT_WITHIN(0.01f, 60.0f, radiusKm());
  TEST_ASSERT_TRUE(feedOk());
  TEST_ASSERT_TRUE(feedAgeS() >= 0.0f);
}

void test_an_unassigned_device_says_what_to_do_rather_than_showing_nothing(void) {
  TEST_ASSERT_TRUE(poll(kWireUnassigned));
  TEST_ASSERT_FALSE(assigned());
  TEST_ASSERT_EQUAL_UINT(0, aircraftCount());
  TEST_ASSERT_TRUE(strlen(message()) > 0);
  TEST_ASSERT_EQUAL_STRING("", componentName());
}

// --- the cadence ------------------------------------------------------------

void test_the_poll_cadence_comes_from_the_server(void) {
  poll(kWireAssigned, HTTP_CODE_OK, "\"x\"", "30");
  TEST_ASSERT_EQUAL_UINT32(30000, pollIntervalMs());
}

void test_an_absurd_cadence_is_clamped_not_obeyed(void) {
  poll(kWireAssigned, HTTP_CODE_OK, "\"a\"", "0");
  TEST_ASSERT_EQUAL_UINT32(config::kPollMinMs, pollIntervalMs());
  poll(kWireAssigned, HTTP_CODE_OK, "\"b\"", "999999");
  TEST_ASSERT_EQUAL_UINT32(config::kPollMaxMs, pollIntervalMs());
  poll(kWireAssigned, HTTP_CODE_OK, "\"c\"", "-1");
  TEST_ASSERT_EQUAL_UINT32(config::kPollMinMs, pollIntervalMs());
  poll(kWireAssigned, HTTP_CODE_OK, "\"d\"", "not-a-number");
  TEST_ASSERT_EQUAL_UINT32(config::kPollDefaultMs, pollIntervalMs());
}

// --- 304 --------------------------------------------------------------------

void test_the_etag_is_echoed_verbatim_including_its_quotes(void) {
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  TEST_ASSERT_EQUAL_STRING(kWireAssignedEtag, g_http.last_if_none_match.c_str());
  TEST_ASSERT_EQUAL_CHAR('"', g_http.last_if_none_match[0]);
}

void test_a_304_keeps_the_list_and_is_not_a_failure(void) {
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  TEST_ASSERT_EQUAL_UINT(2, aircraftCount());
  TEST_ASSERT_TRUE(poll("", HTTP_CODE_NOT_MODIFIED, kWireAssignedEtag));
  TEST_ASSERT_EQUAL_UINT(2, aircraftCount());
  TEST_ASSERT_EQUAL_STRING("IBE3221", aircraftList()[0].callsign);
  TEST_ASSERT_EQUAL_STRING("radar", componentName());
}

void test_a_304_freezes_the_content_clock_and_refreshes_the_contact_clock(void) {
  // The requirement that survived the protocol change. The fix really is as old
  // as the last 200, so extrapolation and the 12s dim test must keep ageing --
  // but we DID just hear from the server, so the contact bound must not fire.
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  mockAdvanceMs(30000);
  poll("", HTTP_CODE_NOT_MODIFIED, kWireAssignedEtag);
  mockAdvanceMs(20000);
  poll("", HTTP_CODE_NOT_MODIFIED, kWireAssignedEtag);

  TEST_ASSERT_FLOAT_WITHIN(0.5f, 50.0f, secondsSinceContentRaw());
  TEST_ASSERT_FLOAT_WITHIN(0.01f, kExtrapolationHorizonSec, secondsSinceContent());
  TEST_ASSERT_FALSE(contentExpired());
}

void test_a_304_does_not_disturb_the_parsed_picture(void) {
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  const float lat_before = aircraftList()[0].lat;
  poll("", HTTP_CODE_NOT_MODIFIED, kWireAssignedEtag);
  TEST_ASSERT_EQUAL_UINT(2, aircraftCount());
  TEST_ASSERT_EQUAL_FLOAT(lat_before, aircraftList()[0].lat);
}

// --- the two expiries -------------------------------------------------------

void test_silence_from_the_server_expires_the_picture(void) {
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  mockAdvanceMs(61000);
  TEST_ASSERT_TRUE(contentExpired());
}

void test_a_server_that_answers_with_a_stale_feed_expires(void) {
  // THE failure the reference could not have. A STOPPED fetch daemon leaves
  // ok:true on disk forever -- write_failure only runs when a fetch RUNS and
  // fails. Nothing flips feed_ok, the server keeps answering, and only
  // feed_age_s grows. This fixture is that state, taken from the real server.
  TEST_ASSERT_TRUE(poll(kWireAssigned, HTTP_CODE_OK, "\"a\""));
  TEST_ASSERT_FALSE(contentExpired());
  TEST_ASSERT_TRUE(poll(kWireFeedStale, HTTP_CODE_OK, "\"b\""));
  TEST_ASSERT_TRUE(feedOk());                  // the server still says it is ok
  TEST_ASSERT_TRUE(feedAgeS() >= 60.0f);       // ...and only this shows it is not
  TEST_ASSERT_TRUE(contentExpired());
}

void test_one_upstream_hiccup_does_not_blank_the_radar(void) {
  // write_failure KEEPS the last good aircraft and flips only the flag, on a
  // 3-second fetch cycle. Expiring on feed_ok would blank the whole picture for
  // one poll and restore it -- the once-per-cycle blink radar_display.cpp was
  // written to eliminate.
  TEST_ASSERT_TRUE(poll(kWireAssigned, HTTP_CODE_OK, "\"a\""));
  TEST_ASSERT_TRUE(poll(kWireFeedDown, HTTP_CODE_OK, "\"b\""));
  TEST_ASSERT_FALSE(feedOk());
  TEST_ASSERT_FALSE_MESSAGE(contentExpired(),
                            "a transient must not blank the panel");
}

void test_nothing_is_expired_before_the_first_reply(void) {
  // Without the zero guards, twelve seconds of uptime marks every target stale
  // and sixty blanks the panel -- before the device has spoken to the server.
  mockAdvanceMs(120000);
  TEST_ASSERT_FALSE(contentExpired());
  TEST_ASSERT_EQUAL_FLOAT(0.0f, secondsSinceContentRaw());
  TEST_ASSERT_EQUAL_FLOAT(0.0f, secondsSinceContent());
  TEST_ASSERT_FALSE(everReceived());
}

// --- failure paths ----------------------------------------------------------

void test_an_http_error_keeps_the_last_good_scene(void) {
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  TEST_ASSERT_FALSE(poll("", 500));
  TEST_ASSERT_EQUAL_UINT(2, aircraftCount());
  TEST_ASSERT_FALSE(poll("", HTTPC_ERROR_CONNECTION_REFUSED));
  TEST_ASSERT_EQUAL_UINT(2, aircraftCount());
}

void test_every_failure_path_drops_the_socket(void) {
  // HTTPClient keeps a keep-alive connection across end(). When the Pi is power
  // cycled there is no FIN, so connected() stays true, the next request is
  // written into a dead socket, and handleHeaderResponse times out after 8 s --
  // forever, because _canReuse was never cleared.
  //
  // One case per failure path, each with the status that actually reaches it: a
  // malformed BODY sent with a 500 exits at the status check and never touches
  // the shape guard, so the guard's teardown would go untested.
  poll(kWireAssigned);
  struct Case { const char* body; int code; const char* what; };
  const Case cases[] = {
      {"", 500, "http error"},
      {"", HTTPC_ERROR_CONNECTION_REFUSED, "refused"},
      {"<html>nope</html>", HTTP_CODE_OK, "parse error"},
      {"{\"a\":1}", HTTP_CODE_OK, "shape guard"},
  };
  for (const Case& c : cases) {
    const int before = g_wc.stop_calls;
    poll(c.body, c.code);
    TEST_ASSERT_GREATER_THAN_MESSAGE(before, g_wc.stop_calls, c.what);
  }
}

void test_a_successful_poll_keeps_the_connection(void) {
  // The other half: dropping the socket on success pays a fresh TCP handshake
  // every cadence for nothing.
  poll(kWireAssigned);
  const int before = g_wc.stop_calls;
  poll(kWireAssigned);
  poll("", HTTP_CODE_NOT_MODIFIED);
  TEST_ASSERT_EQUAL_INT(before, g_wc.stop_calls);
}

void test_a_body_that_is_not_a_scene_is_rejected_rather_than_believed(void) {
  // An HTML captive-portal page, a bare null, or a chunk-size line must not
  // read as "empty sky": that wipes real traffic AND refreshes the clocks, so
  // no expiry fires and the screen reports itself healthy while showing nothing.
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  const char* junk[] = {"<html>nope</html>", "null", "42", "500",
                        "{\"a\":1}", "{\"components\":5}", ""};
  for (const char* j : junk) {
    TEST_ASSERT_FALSE_MESSAGE(poll(j), j);
    TEST_ASSERT_EQUAL_UINT(2, aircraftCount());
  }
}

void test_an_oversized_body_is_refused_before_it_is_parsed(void) {
  // Refused before, not truncated after. ArduinoJson peaks ~4.6x the body, so
  // an over-cap body is a NoMemory every cycle at the busiest time of day.
  poll(kWireAssigned);
  g_http.reset();
  g_http.body = kWireAssigned;
  g_http.content_length_override = config::kMaxBodyBytes + 1;
  TEST_ASSERT_FALSE(pollOnce());
  TEST_ASSERT_EQUAL_UINT(2, aircraftCount());
}

void test_a_chunked_response_is_refused_not_misparsed(void) {
  // getStreamPtr() does not decode chunk framing, so a chunked body starts with
  // a hex length: `500\r\n{...}` parses as the NUMBER 500 and the shape guard
  // rejects it -- silently, forever. getSize() < 0 catches it up front.
  poll(kWireAssigned);
  g_http.reset();
  g_http.body = kWireAssigned;
  g_http.content_length_override = -1;
  TEST_ASSERT_FALSE(pollOnce());
}

void test_a_scene_with_no_radar_component_clears_the_sky(void) {
  // Not the same as a bad body: the server said, in a well-formed scene, that
  // this device is showing something else now.
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  TEST_ASSERT_TRUE(poll(kWireUnassigned));
  TEST_ASSERT_EQUAL_UINT(0, aircraftCount());
}

// --- hostile and malformed items --------------------------------------------

void test_more_items_than_we_declared_are_truncated_not_overflowed(void) {
  std::string big = "{\"scene\":\"planes\",\"assigned\":true,\"layout\":\"fill\","
                    "\"components\":[{\"c\":\"radar\",\"items\":[";
  for (size_t i = 0; i < kMaxAircraft + 20; ++i) {
    if (i) big += ",";
    big += "{\"lat\":40.5,\"lon\":-3.6,\"cs\":\"X\"}";
  }
  big += "]}]}";
  g_http.reset();
  g_http.body = big;
  g_http.code = HTTP_CODE_OK;
  g_http.content_length_override = 1;   // not the size guard's business here
  TEST_ASSERT_TRUE(pollOnce());
  TEST_ASSERT_EQUAL_UINT(kMaxAircraft, aircraftCount());
}

void test_an_item_without_a_position_is_skipped(void) {
  TEST_ASSERT_TRUE(poll("{\"scene\":\"planes\",\"assigned\":true,"
                        "\"layout\":\"fill\",\"components\":[{\"c\":\"radar\","
                        "\"items\":[{\"cs\":\"NOPOS\"},"
                        "{\"lat\":1.0,\"lon\":2.0,\"cs\":\"OK\"}]}]}"));
  TEST_ASSERT_EQUAL_UINT(1, aircraftCount());
  TEST_ASSERT_EQUAL_STRING("OK", aircraftList()[0].callsign);
}

void test_an_overlong_tag_cannot_overrun_its_buffer(void) {
  // The callsign originates at a third-party feed. ASan turns a mistake here
  // into an abort naming the line rather than silent corruption.
  TEST_ASSERT_TRUE(poll("{\"scene\":\"planes\",\"assigned\":true,"
                        "\"layout\":\"fill\",\"components\":[{\"c\":\"radar\","
                        "\"items\":[{\"lat\":1.0,\"lon\":2.0,"
                        "\"cs\":\"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\","
                        "\"ty\":\"BBBBBBBBBB\",\"alt\":\"CCCCCCCCCCCCCCCCCC\"}]}]}"));
  TEST_ASSERT_EQUAL_UINT(8, strlen(aircraftList()[0].callsign));
  TEST_ASSERT_EQUAL_UINT(4, strlen(aircraftList()[0].type));
  TEST_ASSERT_EQUAL_UINT(11, strlen(aircraftList()[0].alt));
}

void test_a_non_finite_number_never_reaches_the_renderer(void) {
  // inf/nan through the projection gives a coordinate that is neither on screen
  // nor off it, and clipPointToOuterRing does not converge.
  TEST_ASSERT_TRUE(poll("{\"scene\":\"planes\",\"assigned\":true,"
                        "\"layout\":\"fill\",\"components\":[{\"c\":\"radar\","
                        "\"items\":[{\"lat\":1e400,\"lon\":2.0,\"cs\":\"BAD\"},"
                        "{\"lat\":1.0,\"lon\":2.0,\"ve\":1e400,\"cs\":\"V\"},"
                        "{\"lat\":3.0,\"lon\":4.0,\"cs\":\"OK\"}]}]}"));
  for (size_t i = 0; i < aircraftCount(); ++i) {
    TEST_ASSERT_TRUE(std::isfinite(aircraftList()[i].lat));
    TEST_ASSERT_TRUE(std::isfinite(aircraftList()[i].lon));
    TEST_ASSERT_TRUE(std::isfinite(aircraftList()[i].vel_e_km_s));
    TEST_ASSERT_TRUE(std::isfinite(aircraftList()[i].pos_age_s));
  }
}

// --- concurrency ------------------------------------------------------------

void test_the_reader_lock_is_taken_and_released_around_every_publish(void) {
  // resetForTest() creates the mutex; without it aircraftLock() takes the
  // `s_mutex == nullptr -> true` path and this passes against a publish that
  // never locks at all.
  TEST_ASSERT_EQUAL_INT(1, g_mutex_live);
  poll(kWireAssigned);
  TEST_ASSERT_TRUE(aircraftLock(10));
  aircraftUnlock();
  poll("", 500);
  TEST_ASSERT_TRUE(aircraftLock(10));
  aircraftUnlock();
  TEST_ASSERT_EQUAL_INT(0, g_mutex_outstanding);
}

void test_the_content_clock_is_stamped_with_the_swap_under_one_lock(void) {
  // The host is single-threaded, so no test can make a frame interleave. What
  // CAN be asserted is that the stamp happens while the mutex is held -- the
  // property that makes interleaving safe on the device. Set outside the lock, a
  // frame draws the NEW positions against the OLD content time: ~1 km at 400 kt
  // as a jump-and-snap once per poll.
  int held_during_stamp = -1;
  unsigned long content_at_give = 0;
  unsigned long contact_at_give = 0;
  // The raw static, not secondsSinceContentRaw(): that has a zero guard which
  // returns 0.0 both when the clock is unset AND when it was just set this
  // millisecond, so it cannot tell the two apart. Reading the field can.
  g_mutex_on_give = [&]() {
    held_during_stamp = g_mutex_outstanding;
    content_at_give = s_content_ms;
    contact_at_give = s_contact_ms;
  };
  poll(kWireAssigned);
  g_mutex_on_give = nullptr;
  TEST_ASSERT_EQUAL_INT_MESSAGE(1, held_during_stamp,
                                "nothing happened inside the lock at all");
  TEST_ASSERT_NOT_EQUAL_MESSAGE(0, content_at_give,
                                "the content clock was set after the release");
  TEST_ASSERT_NOT_EQUAL_MESSAGE(0, contact_at_give,
                                "the contact clock was set after the release");
  TEST_ASSERT_EQUAL_UINT(2, aircraftCount());
}

// --- the tick ---------------------------------------------------------------

void test_losing_the_link_drops_the_socket(void) {
  // Through the TICK, as the task does: s_was_link_up is only set there, so a
  // test that drives pollOnce directly never establishes the up-state and the
  // transition it means to exercise cannot happen.
  g_http.reset(); g_http.body = kWireAssigned; g_http.code = HTTP_CODE_OK;
  pollTick(true);
  const int before = g_wc.stop_calls;
  pollTick(false);
  TEST_ASSERT_GREATER_THAN(before, g_wc.stop_calls);
}

void test_the_link_down_teardown_happens_once_not_every_tick(void) {
  g_http.reset(); g_http.body = kWireAssigned; g_http.code = HTTP_CODE_OK;
  pollTick(true);
  pollTick(false);
  const int after_first = g_wc.stop_calls;
  pollTick(false);
  pollTick(false);
  TEST_ASSERT_EQUAL_INT(after_first, g_wc.stop_calls);
}

void test_every_tick_feeds_the_watchdog(void) {
  // A watchdog nobody feeds is a reboot loop. The link-down tick must feed it
  // too -- that is precisely when the device is doing nothing and looks hung.
  g_wdt.reset();
  g_http.reset(); g_http.body = kWireAssigned; g_http.code = HTTP_CODE_OK;
  pollTick(true);
  TEST_ASSERT_EQUAL_INT(1, g_wdt.resets);
  pollTick(false);
  TEST_ASSERT_EQUAL_INT_MESSAGE(2, g_wdt.resets,
                                "a link-down tick must still feed it");
}

void test_the_tick_reports_the_delay_the_task_should_use(void) {
  g_http.reset(); g_http.body = kWireAssigned; g_http.code = HTTP_CODE_OK;
  g_http.response_headers["X-Poll-Seconds"] = "30";
  pollTick(true);
  TEST_ASSERT_EQUAL_UINT32(30000, nextDelayMs());
  pollTick(false);
  TEST_ASSERT_EQUAL_UINT32(config::kPollErrorMs, nextDelayMs());
}

void test_the_watchdog_period_outlasts_a_slow_poll(void) {
  // 8 s of header timeout plus up to ~31 s of DNS is more than the SDK's 5 s
  // default and more than 30.
  TEST_ASSERT_GREATER_OR_EQUAL(45u, kWatchdogTimeoutSec);
}


void test_new_content_bumps_the_generation_the_render_loop_watches(void) {
  // The loop's other redraw trigger counts AIRCRAFT, so a component with none
  // -- a clock -- never redrew and sat on the frame drawn before its first poll
  // landed. Content changing is a reason to redraw whatever the component is.
  const uint32_t start = contentGeneration();
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  const uint32_t after_first = contentGeneration();
  TEST_ASSERT_GREATER_THAN_UINT32(start, after_first);

  poll(kWireUnassigned, HTTP_CODE_OK, "\"other\"");
  TEST_ASSERT_GREATER_THAN_UINT32(after_first, contentGeneration());
}

void test_a_304_does_not_bump_the_generation(void) {
  // Nothing changed, so nothing needs redrawing -- and redrawing anyway would
  // composite a 44ms frame every poll for no reason.
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  const uint32_t before = contentGeneration();
  poll("", HTTP_CODE_NOT_MODIFIED, kWireAssignedEtag);
  poll("", HTTP_CODE_NOT_MODIFIED, kWireAssignedEtag);
  TEST_ASSERT_EQUAL_UINT32(before, contentGeneration());
}

void test_a_failed_poll_does_not_bump_the_generation(void) {
  poll(kWireAssigned, HTTP_CODE_OK, kWireAssignedEtag);
  const uint32_t before = contentGeneration();
  poll("", 500);
  poll("<html>nope</html>");
  TEST_ASSERT_EQUAL_UINT32(before, contentGeneration());
}

void test_a_component_with_no_items_still_bumps_the_generation(void) {
  // The exact case that froze the panel: a clock installs content and zero
  // aircraft, so `hasTraffic()` is false and only the generation can say
  // anything happened.
  poll("{\"assigned\":true,\"layout\":\"fill\",\"scene\":\"clock\","
       "\"components\":[{\"c\":\"clock\",\"draw\":["
       "{\"t\":\"text\",\"slot\":\"center\",\"v\":\"13:40\"}]}]}");
  TEST_ASSERT_EQUAL_UINT(0, aircraftCount());
  TEST_ASSERT_FALSE(hasTraffic());
  const uint32_t before = contentGeneration();
  poll("{\"assigned\":true,\"layout\":\"fill\",\"scene\":\"clock\","
       "\"components\":[{\"c\":\"clock\",\"draw\":["
       "{\"t\":\"text\",\"slot\":\"center\",\"v\":\"13:41\"}]}]}",
       HTTP_CODE_OK, "\"minute-later\"");
  TEST_ASSERT_GREATER_THAN_UINT32_MESSAGE(
      before, contentGeneration(),
      "a ticking clock must be able to tell the loop to redraw");
}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_the_request_declares_everything_the_server_needs);
  RUN_TEST(test_a_real_server_body_becomes_aircraft);
  RUN_TEST(test_velocities_come_from_the_server_and_are_not_recomputed);
  RUN_TEST(test_the_scene_metadata_is_available_to_the_dispatcher);
  RUN_TEST(test_an_unassigned_device_says_what_to_do_rather_than_showing_nothing);
  RUN_TEST(test_the_poll_cadence_comes_from_the_server);
  RUN_TEST(test_an_absurd_cadence_is_clamped_not_obeyed);
  RUN_TEST(test_the_etag_is_echoed_verbatim_including_its_quotes);
  RUN_TEST(test_a_304_keeps_the_list_and_is_not_a_failure);
  RUN_TEST(test_a_304_freezes_the_content_clock_and_refreshes_the_contact_clock);
  RUN_TEST(test_a_304_does_not_disturb_the_parsed_picture);
  RUN_TEST(test_silence_from_the_server_expires_the_picture);
  RUN_TEST(test_a_server_that_answers_with_a_stale_feed_expires);
  RUN_TEST(test_one_upstream_hiccup_does_not_blank_the_radar);
  RUN_TEST(test_nothing_is_expired_before_the_first_reply);
  RUN_TEST(test_an_http_error_keeps_the_last_good_scene);
  RUN_TEST(test_every_failure_path_drops_the_socket);
  RUN_TEST(test_a_successful_poll_keeps_the_connection);
  RUN_TEST(test_a_body_that_is_not_a_scene_is_rejected_rather_than_believed);
  RUN_TEST(test_an_oversized_body_is_refused_before_it_is_parsed);
  RUN_TEST(test_a_chunked_response_is_refused_not_misparsed);
  RUN_TEST(test_a_scene_with_no_radar_component_clears_the_sky);
  RUN_TEST(test_more_items_than_we_declared_are_truncated_not_overflowed);
  RUN_TEST(test_an_item_without_a_position_is_skipped);
  RUN_TEST(test_an_overlong_tag_cannot_overrun_its_buffer);
  RUN_TEST(test_a_non_finite_number_never_reaches_the_renderer);
  RUN_TEST(test_the_reader_lock_is_taken_and_released_around_every_publish);
  RUN_TEST(test_the_content_clock_is_stamped_with_the_swap_under_one_lock);
  RUN_TEST(test_losing_the_link_drops_the_socket);
  RUN_TEST(test_the_link_down_teardown_happens_once_not_every_tick);
  RUN_TEST(test_every_tick_feeds_the_watchdog);
  RUN_TEST(test_the_tick_reports_the_delay_the_task_should_use);
  RUN_TEST(test_the_watchdog_period_outlasts_a_slow_poll);
  RUN_TEST(test_new_content_bumps_the_generation_the_render_loop_watches);
  RUN_TEST(test_a_304_does_not_bump_the_generation);
  RUN_TEST(test_a_failed_poll_does_not_bump_the_generation);
  RUN_TEST(test_a_component_with_no_items_still_bumps_the_generation);
  return UNITY_END();
}
