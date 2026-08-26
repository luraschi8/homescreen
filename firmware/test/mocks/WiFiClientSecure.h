#pragma once
#include <Arduino.h>
#include <Stream.h>

/**
 * Plain-TCP teardown counter. MockTlsStats models the TLS one, and this
 * firmware has no TLS -- without this the host suite loses its only way to see
 * a socket-teardown bug at the moment the firmware reintroduces one.
 * Deliberately NOT reset by g_http.reset(): the poll helper calls that as its
 * first statement, which would zero the counter a test just sampled.
 */
struct MockWiFiClientStats { int stop_calls = 0; void reset() { stop_calls = 0; } };
extern MockWiFiClientStats g_wc;

/** Serves a scripted body and records how it was torn down. */
class WiFiClient : public Stream {
 public:
  std::string body;
  size_t pos = 0;
  int available() override { return (int)(body.size() - pos); }
  int read() override { return pos < body.size() ? (unsigned char)body[pos++] : -1; }
  int peek() override { return pos < body.size() ? (unsigned char)body[pos] : -1; }
  void stop() { ++g_wc.stop_calls; pos = 0; }
};

struct MockTlsStats {
  int setInsecure = 0;
  int stop = 0;
  /**
   * ssl_client's teardown memsets its context, leaving socket == 0 rather than
   * -1, so a SECOND stop() on an already-closed client evaluates 0 >= 0 and
   * calls close(0) -- the console descriptor. Modelled so tests can catch it.
   */
  int close_of_fd0 = 0;
};
extern MockTlsStats g_tls;

class WiFiClientSecure : public WiFiClient {
 public:
  void setInsecure() { ++g_tls.setInsecure; }
  void connectSocket() { socket_ = 7; }   // a real fd is well above 0
  void stop() {
    ++g_tls.stop;
    if (socket_ == 0) ++g_tls.close_of_fd0;
    socket_ = 0;                          // NOT -1: this is the real behaviour
  }
  int socket_ = -1;                       // fresh client: nothing open
};
