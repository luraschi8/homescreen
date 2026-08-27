// The C++ resolver, and its agreement with the Python one.
//
// Two implementations of one vocabulary is the price of previewing a screen the
// server never draws. The price is only worth paying if they actually agree, so
// the parity suite below is the point of this file -- the rest is the usual
// hostile-input work.
#include <Arduino.h>
#include <unity.h>
#include <ArduinoJson.h>
#include <cstring>
#include <string>

#include "fixtures_draw.h"
#include "../mocks/mock_globals.h"
#include "../../src/ui/draw_list.cpp"

using namespace ui::drawlist;

void setUp(void) {}
void tearDown(void) {}

// --- parity with homescreen/draw.py -----------------------------------------

void test_every_golden_case_resolves_exactly_as_python_does(void) {
  JsonDocument doc;
  TEST_ASSERT_EQUAL(DeserializationError::Ok,
                    deserializeJson(doc, kParityCases).code());
  TEST_ASSERT_TRUE(doc.is<JsonArrayConst>());

  size_t cases = 0;
  for (JsonObjectConst c : doc.as<JsonArrayConst>()) {
    ++cases;
    const char* name = c["name"].as<const char*>();
    const int w = c["w"].as<int>();
    const int h = c["h"].as<int>();

    std::string draw_json;
    serializeJson(c["draw"], draw_json);

    Placement got[kMaxPlacements];
    const size_t n = resolve(draw_json.c_str(), w, h, got, kMaxPlacements);

    JsonArrayConst expect = c["expect"].as<JsonArrayConst>();
    char msg[160];
    snprintf(msg, sizeof(msg), "%s: placement COUNT", name);
    TEST_ASSERT_EQUAL_UINT_MESSAGE(expect.size(), n, msg);

    size_t i = 0;
    for (JsonObjectConst e : expect) {
      snprintf(msg, sizeof(msg), "%s: item %u x", name, (unsigned)i);
      TEST_ASSERT_EQUAL_INT_MESSAGE(e["x"].as<int>(), got[i].x, msg);
      snprintf(msg, sizeof(msg), "%s: item %u y", name, (unsigned)i);
      TEST_ASSERT_EQUAL_INT_MESSAGE(e["y"].as<int>(), got[i].y, msg);
      snprintf(msg, sizeof(msg), "%s: item %u px", name, (unsigned)i);
      TEST_ASSERT_EQUAL_INT_MESSAGE(e["px"].as<int>(), got[i].px, msg);
      snprintf(msg, sizeof(msg), "%s: item %u text", name, (unsigned)i);
      TEST_ASSERT_EQUAL_STRING_MESSAGE(e["text"].as<const char*>(),
                                       got[i].text, msg);

      const char* want_tone = e["tone"].as<const char*>();
      const uint8_t want = strcmp(want_tone, "dim") == 0    ? kDim
                           : strcmp(want_tone, "good") == 0 ? kGood
                           : strcmp(want_tone, "bad") == 0  ? kBad
                                                            : kNormal;
      snprintf(msg, sizeof(msg), "%s: item %u tone", name, (unsigned)i);
      TEST_ASSERT_EQUAL_UINT8_MESSAGE(want, got[i].tone, msg);
      ++i;
    }
  }
  TEST_ASSERT_GREATER_OR_EQUAL_MESSAGE(
      12, cases, "the fixture shrank -- regenerate it, do not weaken this");
}

// --- the tables themselves --------------------------------------------------

void test_slots_are_ordered_top_to_bottom(void) {
  const char* order[] = {"rim_top", "above", "center", "below", "rim_bottom"};
  int last = -1;
  for (const char* s : order) {
    const int y = slotY(s, 240);
    TEST_ASSERT_GREATER_THAN_MESSAGE(last, y, s);
    last = y;
  }
  TEST_ASSERT_GREATER_THAN(0, slotY("rim_top", 240));
  TEST_ASSERT_LESS_THAN(240, slotY("rim_bottom", 240));
}

void test_size_scales_on_the_short_side(void) {
  // A wide panel must not grow type past what its height allows.
  TEST_ASSERT_EQUAL_INT(sizePx("xl", 480, 480), sizePx("xl", 800, 480));
  TEST_ASSERT_LESS_THAN(sizePx("xl", 800, 480), sizePx("xl", 240, 240));
}

void test_type_never_falls_below_the_legibility_floor(void) {
  TEST_ASSERT_EQUAL_INT(kMinTextPx, sizePx("xs", 40, 40));
}

void test_an_unknown_slot_or_size_falls_back_rather_than_vanishing(void) {
  TEST_ASSERT_EQUAL_INT(slotY("center", 240), slotY("nowhere", 240));
  TEST_ASSERT_EQUAL_INT(sizePx("md", 240, 240), sizePx("enormous", 240, 240));
  TEST_ASSERT_EQUAL_INT(slotY("center", 240), slotY(nullptr, 240));
  TEST_ASSERT_EQUAL_INT(sizePx("md", 240, 240), sizePx(nullptr, 240, 240));
}

// --- hostile and malformed input --------------------------------------------

void test_a_malformed_list_resolves_to_nothing_rather_than_crashing(void) {
  Placement out[kMaxPlacements];
  const char* junk[] = {"", "null", "42", "{\"not\":\"an array\"}",
                        "[", "[{\"t\":\"text\"}]", "[null]", "[5]",
                        "[{\"t\":\"text\",\"v\":5}]",
                        "[{\"t\":\"text\",\"v\":\"\"}]"};
  for (const char* j : junk) {
    TEST_ASSERT_EQUAL_UINT_MESSAGE(0, resolve(j, 240, 240, out, kMaxPlacements),
                                   j);
  }
  TEST_ASSERT_EQUAL_UINT(0, resolve(nullptr, 240, 240, out, kMaxPlacements));
  TEST_ASSERT_EQUAL_UINT(0, resolve("[]", 240, 240, nullptr, kMaxPlacements));
  TEST_ASSERT_EQUAL_UINT(0, resolve("[]", 240, 240, out, 0));
}

void test_more_instructions_than_fit_are_truncated_not_overflowed(void) {
  // The list arrives from the network. ASan turns a mistake here into an abort
  // naming the line rather than silent corruption.
  std::string big = "[";
  for (size_t i = 0; i < kMaxPlacements + 8; ++i) {
    if (i) big += ",";
    big += "{\"t\":\"text\",\"slot\":\"center\",\"v\":\"x\",\"size\":\"sm\"}";
  }
  big += "]";
  Placement out[kMaxPlacements];
  TEST_ASSERT_EQUAL_UINT(kMaxPlacements,
                         resolve(big.c_str(), 240, 240, out, kMaxPlacements));
}

void test_an_overlong_string_cannot_overrun_its_buffer(void) {
  std::string s = "[{\"t\":\"text\",\"slot\":\"center\",\"v\":\"";
  s += std::string(200, 'A');
  s += "\"}]";
  Placement out[kMaxPlacements];
  TEST_ASSERT_EQUAL_UINT(1, resolve(s.c_str(), 240, 240, out, kMaxPlacements));
  TEST_ASSERT_EQUAL_UINT(sizeof(out[0].text) - 1, strlen(out[0].text));
}

void test_a_smaller_output_buffer_is_respected(void) {
  const char* three = "[{\"t\":\"text\",\"v\":\"a\"},{\"t\":\"text\",\"v\":\"b\"},"
                      "{\"t\":\"text\",\"v\":\"c\"}]";
  Placement out[2];
  TEST_ASSERT_EQUAL_UINT(2, resolve(three, 240, 240, out, 2));
  TEST_ASSERT_EQUAL_STRING("a", out[0].text);
  TEST_ASSERT_EQUAL_STRING("b", out[1].text);
}

void test_a_zero_sized_panel_does_not_divide_by_anything(void) {
  Placement out[kMaxPlacements];
  TEST_ASSERT_EQUAL_UINT(1, resolve("[{\"t\":\"text\",\"v\":\"x\"}]", 0, 0, out,
                                    kMaxPlacements));
  TEST_ASSERT_EQUAL_INT(kMinTextPx, out[0].px);
}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_every_golden_case_resolves_exactly_as_python_does);
  RUN_TEST(test_slots_are_ordered_top_to_bottom);
  RUN_TEST(test_size_scales_on_the_short_side);
  RUN_TEST(test_type_never_falls_below_the_legibility_floor);
  RUN_TEST(test_an_unknown_slot_or_size_falls_back_rather_than_vanishing);
  RUN_TEST(test_a_malformed_list_resolves_to_nothing_rather_than_crashing);
  RUN_TEST(test_more_instructions_than_fit_are_truncated_not_overflowed);
  RUN_TEST(test_an_overlong_string_cannot_overrun_its_buffer);
  RUN_TEST(test_a_smaller_output_buffer_is_respected);
  RUN_TEST(test_a_zero_sized_panel_does_not_divide_by_anything);
  return UNITY_END();
}
