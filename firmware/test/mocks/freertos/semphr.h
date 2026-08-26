#pragma once
#include <freertos/FreeRTOS.h>
#include <functional>
// Single-threaded host: a mutex that counts use so tests can assert on locking.
struct MockMutex { int taken = 0; int given = 0; bool exists = true; };
/**
 * Every successful take minus every give. A single-threaded host mutex never
 * blocks, so a path that returns without unlocking looks identical to one that
 * unlocks -- on the device it strands the render task's lock forever and the
 * fetch task never publishes again. This counter is the only way a test can
 * see it. Assert it is 0 after any path that takes the lock.
 */
extern int g_mutex_outstanding;
/** Created minus deleted: a mutex stranded by an error path shows up here. */
extern int g_mutex_live;
using SemaphoreHandle_t = MockMutex*;
extern int g_mutex_alloc_fail;
/** Force the next N takes to time out, so a caller's skip path is reachable. */
extern int g_mutex_take_fails;
/**
 * Fired inside xSemaphoreGive BEFORE g_mutex_outstanding drops, so a test can
 * assert on state that was only true while the lock was held. The host mutex
 * never blocks, so this is the only way to see it. Null by default.
 */
extern std::function<void()> g_mutex_on_give;
inline SemaphoreHandle_t xSemaphoreCreateMutex() {
  if (g_mutex_alloc_fail) { --g_mutex_alloc_fail; return nullptr; }
  ++g_mutex_live;
  return new MockMutex();
}
inline BaseType_t xSemaphoreTake(SemaphoreHandle_t m, TickType_t) {
  if (g_mutex_take_fails > 0) { --g_mutex_take_fails; return pdFALSE; }
  if (m) { ++m->taken; ++g_mutex_outstanding; }
  return pdTRUE;
}
inline BaseType_t xSemaphoreGive(SemaphoreHandle_t m) {
  if (m) {
    if (g_mutex_on_give) g_mutex_on_give();
    ++m->given; --g_mutex_outstanding;
  }
  return pdTRUE;
}
inline void vSemaphoreDelete(SemaphoreHandle_t m) {
  if (m) --g_mutex_live;
  delete m;
}
