#pragma once

#include <cstddef>
#include <cstdint>

namespace services::scene {

/**
 * One target on the radar. Field-for-field the reference firmware's `Aircraft`,
 * so radar_display.cpp ports across unmodified -- the values now arrive already
 * resolved from the server instead of being derived here.
 */
struct Aircraft {
  float lat;
  float lon;
  float nose_deg;
  float track_deg;
  float gs_knots;
  /** East/north ground velocity, km per second. Computed by the server. */
  float vel_e_km_s;
  float vel_n_km_s;
  /**
   * Age of this position in seconds as the server serves it: upstream's
   * seen_pos PLUS the time the record sat in the server's cache. Dead reckoning
   * runs from when the fix was taken, not from when we fetched it.
   */
  float pos_age_s;
  /** Distance from the radar centre (NM); < 0 if absent. */
  float dst_nm;
  char callsign[9];
  char type[5];
  char alt[12];
};

/**
 * Also declared to the server as `max_items`, so it never sends more.
 *
 * 40, not 64. Measured against the real server with every float rounded to
 * what a float32 can carry: 40 items is a 6,274-byte body and ArduinoJson
 * peaks around 4.6x that (~29 KB) against ~55 KB of post-TLS heap. 64 items
 * would peak near 46 KB, too close for a pool that needs contiguity rather
 * than just total free bytes.
 */
constexpr size_t kMaxAircraft = 40;

/**
 * Dead-reckoning horizon. Shared so the drawn position (clamped to it) and the
 * stale flag (tested against it) can never be judged by different numbers.
 */
constexpr float kExtrapolationHorizonSec = 12.0f;

/**
 * Silence from the SERVER this long and the picture must go -- but never
 * sooner than we were told to come back.
 *
 * This was a bare 60s, which was safe only while every scene polled at the
 * radar's 5s. Now that a component declares its own cadence, a clock asking to
 * be woken on the minute boundary polls at up to 60s, and a fixed 60s expiry
 * would condemn it for obeying us: the picture would blank moments before the
 * poll that refreshes it, every single minute.
 *
 * So the floor stays 60s and the real threshold is whichever is longer: this,
 * or kContactExpiryPolls consecutive missed polls at the agreed cadence.
 * Expiry means "the server is gone", and the only honest evidence for that is
 * polls that were due and did not land.
 */
constexpr float kContactExpirySec = 60.0f;

/** Missed polls at the AGREED cadence before the server counts as gone. */
constexpr float kContactExpiryPolls = 3.0f;

/** The threshold actually applied: the floor, or three missed polls. */
float contactExpirySec();
/**
 * The server's own feed cache this stale and the picture must go, even though
 * the server is answering us perfectly.
 *
 * This is the number that matters. `feed_ok` cannot carry it: the server's
 * `cache.write_failure` only runs when a fetch RUNS and fails, so a fetch
 * daemon that was stopped, hung, or exited 78 on a bad config leaves `ok: true`
 * on disk forever while the data rots. `feed_age_s` grows in every one of those
 * cases, because `fetched_at` stops advancing.
 */
constexpr float kFeedExpirySec = 60.0f;

/**
 * Task watchdog period. 60 s, not the SDK's 5: WiFiClient::connect() resolves
 * the host BEFORE applying the connect timeout, and hostByName can block ~31 s
 * on its own DNS waits, which setConnectTimeout() does not bound. Add the 8 s
 * header timeout and a legitimately slow poll can pass 30 s. Long enough never
 * to fire on a device that is merely slow, far shorter than a human noticing a
 * frozen panel.
 */
constexpr uint32_t kWatchdogTimeoutSec = 60;

/** One HTTP exchange. True on 200 or 304; false leaves the picture untouched. */
bool pollOnce();

/**
 * One iteration of the poll loop: feeds the watchdog, handles the link-down
 * transition, then polls. The task calls THIS, never pollOnce directly -- a
 * task that skips it feeds no watchdog and never tears down a socket that
 * outlived its Wi-Fi link.
 */
void pollTick(bool link_up);

bool startPollTask();
unsigned pollTaskStackFree();
/** How long the task should sleep before the next tick. */
unsigned long nextDelayMs();

size_t aircraftCount();
const Aircraft* aircraftList();
bool aircraftLock(uint32_t timeout_ms);
void aircraftUnlock();
bool hasTraffic();

/**
 * Seconds since the last 200 that carried content, clamped to the horizon.
 * A 304 does NOT refresh it: the fix really is that old.
 */
float secondsSinceContent();
/** The same, unclamped, for the 12 s staleness test. */
float secondsSinceContentRaw();

/**
 * True once the picture must not be shown at all. Two conditions, one for each
 * thing that can die:
 *
 *   1. we have not heard from the SERVER for kContactExpirySec
 *   2. the server's own FEED has not moved for kFeedExpirySec
 *
 * The second is the one the reference firmware could not have: there, the
 * device WAS the feed client, so a dead feed and a failed fetch were the same
 * event. Here the Pi serves from cache and keeps answering 200/304 forever
 * after its fetcher dies, so a contact test alone can never fire and the panel
 * would present hours-old traffic as live.
 *
 * `feed_ok` is deliberately NOT a condition. The server keeps the last good
 * aircraft and flips only the flag, so a single upstream timeout -- one of
 * many, on a 3-second fetch cycle -- would blank the whole radar for one poll
 * and restore it. That is the once-per-cycle blink radar_display.cpp was
 * written to eliminate. It is a rendering hint, not an expiry.
 */
bool contentExpired();

unsigned long pollIntervalMs();
bool assigned();
const char* sceneName();
/** The `c` of the component we recognised -- what the dispatcher switches on. */
const char* componentName();

/**
 * The component's instruction list, verbatim JSON, or "" if it carried none.
 *
 * Kept as text rather than parsed here on purpose: `ui::drawlist` owns the
 * vocabulary and is pinned against the server's Python resolver, so this module
 * has no opinion about what an instruction means. It only has to not lose it.
 */
const char* drawJson();
/** Server-supplied Spanish text for an unassigned or failed scene. */
const char* message();
float radiusKm();
bool feedOk();
/** Age of the server's own feed cache, seconds. < 0 if the server did not say. */
float feedAgeS();
/** True once at least one 200 has been parsed since boot. */
bool everReceived();

/**
 * Bumped every time a 200 installs new content. The render loop redraws when
 * this changes.
 *
 * Needed because the loop's other trigger is `hasTraffic()`, which counts
 * AIRCRAFT -- a policy written when the only reason to redraw was a target
 * moving. A clock has no aircraft, so after the boot frame the policy computed
 * "nothing to animate" and the panel sat on whatever had been drawn before the
 * first poll landed, forever. Content changing is a reason to redraw whatever
 * the component is.
 */
uint32_t contentGeneration();

#ifdef UNIT_TEST
/** Host tests only: forget everything between cases, and make the mutex. */
void resetForTest();
#endif

}  // namespace services::scene
