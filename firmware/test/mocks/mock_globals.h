#include <Arduino.h>
#include <Preferences.h>
uint32_t g_mock_millis = 0;
std::vector<std::string> g_events;
MockSerial Serial;
MockNvs g_nvs;
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
MockEsp ESP;
MockWiFi WiFi;
MockTlsStats g_tls;
MockWiFiClientStats g_wc;
MockHttp g_http;
int g_mutex_alloc_fail = 0;
int g_mutex_take_fails = 0;
int g_mutex_outstanding = 0;
int g_mutex_live = 0;
int g_task_create_fail = 0;
#include <esp_task_wdt.h>
MockWdt g_wdt;
#include <WiFiManager.h>
std::map<std::string, WiFiManagerParameter*> g_wm_params;
#include <freertos/semphr.h>
std::function<void()> g_mutex_on_give;
#include <LovyanGFX.hpp>
GfxRecorder g_gfx;
bool g_font_is_smooth = false;
#include <esp_wifi.h>
#include <esp_system.h>
#include <ESPmDNS.h>
#include <WiFiManager.h>
MockGpio g_gpio;
MockEspWifi g_espwifi;
MockRestart g_restart;
MockMdns MDNS;
MockWmStats g_wm;
