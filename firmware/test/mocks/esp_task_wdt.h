#pragma once
// The host has no task watchdog. Mocked rather than #ifdef'd out, so the calls
// stay visible in the code and a test can assert the loop actually feeds it.
struct MockWdt {
  int inits = 0;
  int adds = 0;
  int resets = 0;
  unsigned timeout_s = 0;
  bool panic = false;
  void reset() { *this = MockWdt(); }
};
extern MockWdt g_wdt;

inline int esp_task_wdt_init(unsigned timeout_s, bool panic) {
  ++g_wdt.inits; g_wdt.timeout_s = timeout_s; g_wdt.panic = panic; return 0;
}
inline int esp_task_wdt_add(void*) { ++g_wdt.adds; return 0; }
inline int esp_task_wdt_reset() { ++g_wdt.resets; return 0; }
