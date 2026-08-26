#pragma once

namespace services {

/**
 * This device's hardware id: 12 lowercase hex characters of the station MAC.
 * The server keys the operator's scene assignment on it, so it has to survive
 * reboots and reflashes -- anything derived from a random seed or from NVS
 * makes a device come back as "sin asignar" after a flash.
 */
const char* deviceId();

}  // namespace services
