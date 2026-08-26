#pragma once
#include <Arduino.h>
#include <WiFiClientSecure.h>
#include <map>
#include <vector>
#include <cstring>

enum {
  HTTP_CODE_OK = 200,
  HTTP_CODE_NOT_MODIFIED = 304,
  HTTPC_ERROR_CONNECTION_REFUSED = -1,
  HTTPC_ERROR_NOT_CONNECTED = -4,
  HTTPC_ERROR_SEND_HEADER_FAILED = -2,
  HTTPC_ERROR_SEND_PAYLOAD_FAILED = -3,
  HTTPC_ERROR_CONNECTION_LOST = -5,
  HTTPC_ERROR_READ_TIMEOUT = -11,
};

/** Script the network for a test: what each GET returns, and what body follows. */
struct MockHttp {
  std::string body;
  int code = HTTP_CODE_OK;
  int fail_first_n_gets = 0;      // return CONNECTION_REFUSED this many times
  int content_length_override = 0;  // 0 = use body.size()
  int get_calls = 0;
  /** Return a null stream from a 200, as a torn-down connection can. */
  bool null_stream = false;
  int begin_calls = 0;
  int end_calls = 0;
  std::string last_url;
  // The scene client is conditional: it sends If-None-Match and reads ETag and
  // X-Poll-Seconds back. None of that existed for the adsb.fi client.
  std::map<std::string, std::string> response_headers;
  std::vector<std::string> collected_header_keys;
  std::string last_if_none_match;
  void reset() {
    null_stream = false; *this = MockHttp(); }
};
extern MockHttp g_http;

class HTTPClient {
 public:
  bool begin(WiFiClient& c, const String& url) {
    ++g_http.begin_calls; g_http.last_url = url.c_str(); client_ = &c;
    static_cast<WiFiClient*>(client_)->body = g_http.body;
    static_cast<WiFiClient*>(client_)->pos = 0;
    return true;
  }
  void setTimeout(unsigned long) {}
  void addHeader(const String& name, const String& value) {
    // strcmp, not ==: the mock String has no operator==.
    if (strcmp(name.c_str(), "If-None-Match") == 0) {
      g_http.last_if_none_match = value.c_str();
    }
  }
  void collectHeaders(const char** keys, size_t count) {
    for (size_t i = 0; i < count; ++i) g_http.collected_header_keys.push_back(keys[i]);
  }
  String header(const char* name) {
    auto it = g_http.response_headers.find(name);
    return it == g_http.response_headers.end() ? String("")
                                               : String(it->second.c_str());
  }
  void setConnectTimeout(int) {}
  int GET() {
    ++g_http.get_calls;
    if (g_http.fail_first_n_gets > 0) {
      --g_http.fail_first_n_gets;
      return HTTPC_ERROR_CONNECTION_REFUSED;   // no socket was ever opened
    }
    if (auto* s = dynamic_cast<WiFiClientSecure*>(client_)) s->connectSocket();
    return g_http.code;
  }
  int getSize() const {
    return g_http.content_length_override ? g_http.content_length_override : (int)g_http.body.size();
  }
  WiFiClient* getStreamPtr() {
    if (g_http.null_stream) return nullptr;
    return static_cast<WiFiClient*>(client_);
  }
  void end() { ++g_http.end_calls; }
 private:
  WiFiClient* client_ = nullptr;
};
