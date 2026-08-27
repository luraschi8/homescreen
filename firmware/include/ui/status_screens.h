#pragma once

void statusScreenPortal();
void statusScreenConnectFailed();
void statusScreenWifiReset();

/** Saved-network connect animation (call Tick until connect finishes). */
void statusScreenConnectingBegin(const char* ssid);
void statusScreenConnectingTick();

/**
 * Never reached the server since boot. Shows the address being tried, because
 * a blank round panel looks exactly like a dead one and the likeliest cause is
 * the wrong address in the setup portal.
 */
void statusScreenNoServer(const char* base_url);

/**
 * Registered but unassigned. Shows the hardware id -- the string the operator
 * types into the fleet view -- and the server's own message about it.
 */
void statusScreenUnassigned(const char* hw_id, const char* message);
