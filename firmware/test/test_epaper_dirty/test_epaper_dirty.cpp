// Which rectangles the panel is told to refresh.
//
// This parses UNTRUSTED input off the wire into coordinates handed straight to
// GxEPD2, where an out-of-range rectangle writes past its framebuffer. Every
// rejection below is a real way the panel could be made to do that, or to draw
// part of a frame and leave the rest stale.
#include <Arduino.h>
#include <unity.h>

#include "../../src/services/epaper_dirty.cpp"

using namespace epaper;

constexpr int16_t W = 800;
constexpr int16_t H = 480;

void setUp(void) {}
void tearDown(void) {}

void test_an_absent_header_means_refresh_everything(void) {
  const DirtyPlan plan = parseDirty(nullptr, W, H);
  TEST_ASSERT_FALSE(plan.known);
  TEST_ASSERT_EQUAL_UINT32(0, plan.count);
}

void test_an_empty_header_means_nothing_moved(void) {
  // Meaningful, and different from absent: the server diffed the frames and
  // found no change. Drawing would spend a waveform, and more ghosting, to
  // reach the picture already on the glass.
  const DirtyPlan plan = parseDirty("", W, H);
  TEST_ASSERT_TRUE(plan.known);
  TEST_ASSERT_EQUAL_UINT32(0, plan.count);
}

void test_one_rectangle(void) {
  const DirtyPlan plan = parseDirty("24,10,8,1", W, H);
  TEST_ASSERT_TRUE(plan.known);
  TEST_ASSERT_EQUAL_UINT32(1, plan.count);
  TEST_ASSERT_EQUAL_INT16(24, plan.rects[0].x);
  TEST_ASSERT_EQUAL_INT16(10, plan.rects[0].y);
  TEST_ASSERT_EQUAL_INT16(8, plan.rects[0].w);
  TEST_ASSERT_EQUAL_INT16(1, plan.rects[0].h);
}

void test_several_rectangles(void) {
  const DirtyPlan plan = parseDirty("128,82,304,59;456,431,144,24", W, H);
  TEST_ASSERT_TRUE(plan.known);
  TEST_ASSERT_EQUAL_UINT32(2, plan.count);
  TEST_ASSERT_EQUAL_INT16(456, plan.rects[1].x);
  TEST_ASSERT_EQUAL_INT16(24, plan.rects[1].h);
}

void test_a_rectangle_off_the_panel_is_refused(void) {
  // These coordinates reach GxEPD2. One past the edge writes past its buffer.
  TEST_ASSERT_FALSE(parseDirty("792,0,16,1", W, H).known);
  TEST_ASSERT_FALSE(parseDirty("0,479,8,2", W, H).known);
  TEST_ASSERT_FALSE(parseDirty("-8,0,8,1", W, H).known);
  TEST_ASSERT_FALSE(parseDirty("0,-1,8,1", W, H).known);
  TEST_ASSERT_FALSE(parseDirty("0,0,0,1", W, H).known);
  TEST_ASSERT_FALSE(parseDirty("0,0,8,0", W, H).known);
}

void test_an_enormous_coordinate_does_not_wrap_into_range(void) {
  // int16 truncation would turn 65544 into 8.
  TEST_ASSERT_FALSE(parseDirty("65544,0,8,1", W, H).known);
  TEST_ASSERT_FALSE(parseDirty("0,0,65544,1", W, H).known);
}

void test_the_exact_edge_is_allowed(void) {
  const DirtyPlan plan = parseDirty("792,479,8,1", W, H);
  TEST_ASSERT_TRUE(plan.known);
  TEST_ASSERT_EQUAL_UINT32(1, plan.count);
}

void test_malformed_input_falls_back_to_a_full_refresh(void) {
  TEST_ASSERT_FALSE(parseDirty("garbage", W, H).known);
  TEST_ASSERT_FALSE(parseDirty("1,2,3", W, H).known);
  TEST_ASSERT_FALSE(parseDirty("1,2,3,4;", W, H).known);
  TEST_ASSERT_FALSE(parseDirty("1,2,3,4;;5,6,7,8", W, H).known);
  TEST_ASSERT_FALSE(parseDirty("1,2,3,4 5,6,7,8", W, H).known);
}

void test_more_rectangles_than_agreed_is_refused_not_truncated(void) {
  // Drawing the first four and dropping the fifth would leave part of the
  // frame stale with nothing to say so.
  const DirtyPlan plan = parseDirty(
      "0,0,8,1;0,10,8,1;0,20,8,1;0,30,8,1;0,40,8,1", W, H);
  TEST_ASSERT_FALSE(plan.known);
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_an_absent_header_means_refresh_everything);
  RUN_TEST(test_an_empty_header_means_nothing_moved);
  RUN_TEST(test_one_rectangle);
  RUN_TEST(test_several_rectangles);
  RUN_TEST(test_a_rectangle_off_the_panel_is_refused);
  RUN_TEST(test_an_enormous_coordinate_does_not_wrap_into_range);
  RUN_TEST(test_the_exact_edge_is_allowed);
  RUN_TEST(test_malformed_input_falls_back_to_a_full_refresh);
  RUN_TEST(test_more_rectangles_than_agreed_is_refused_not_truncated);
  return UNITY_END();
}
