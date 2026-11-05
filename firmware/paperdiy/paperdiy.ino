#include <Arduino.h>
#include <M5Unified.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <driver/rtc_io.h>
#include <esp_heap_caps.h>
#include <esp_sleep.h>
#include <esp_sntp.h>
#include <time.h>

#include "policy.h"
#include "secrets.h"

namespace {
constexpr gpio_num_t kButtonA = GPIO_NUM_4;  // PaperDIY BtnA, active low.
constexpr gpio_num_t kButtonB = GPIO_NUM_3;  // PaperDIY BtnB, active low; used only as an input.
RTC_DATA_ATTR uint8_t displayedView = 0;
constexpr unsigned kWifiTimeoutMs = 10000;
constexpr unsigned kNtpTimeoutMs = 5000;
constexpr unsigned kReceiveTimeoutMs = 30000;
RTC_DATA_ATTR bool clockSynced = false;
volatile bool ntpSyncedThisBoot = false;

class PsramStream : public Stream {
 public:
  explicit PsramStream(size_t capacity) : capacity_(capacity), started_(millis()) {
    buffer_ = static_cast<uint8_t*>(heap_caps_malloc(capacity, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  }
  ~PsramStream() override { heap_caps_free(buffer_); }
  size_t write(uint8_t value) override { return write(&value, 1); }
  size_t write(const uint8_t* data, size_t count) override {
    if (!buffer_ || millis() - started_ >= kReceiveTimeoutMs ||
        !paperhub::append(buffer_, capacity_, used_, data, count)) {
      rejected = true;
      return 0;
    }
    return count;
  }
  int available() override { return 0; }
  int read() override { return -1; }
  int peek() override { return -1; }
  void flush() override {}
  uint8_t* data() const { return buffer_; }
  size_t size() const { return used_; }
  bool rejected = false;
 private:
  uint8_t* buffer_ = nullptr;
  size_t capacity_, used_ = 0;
  unsigned long started_;
};

bool clockValid() { return clockSynced && time(nullptr) >= 1609459200; }

void onTimeSync(struct timeval*) {
  clockSynced = true;
  ntpSyncedThisBoot = true;
}

bool connectWifi() {
  const auto started = millis();
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED && millis() - started < kWifiTimeoutMs) {
    M5.update();
    delay(20);
  }
  const bool connected = WiFi.status() == WL_CONNECTED;
  Serial.printf("[wifi] connected=%d elapsed_ms=%lu\n", connected, millis() - started);
  if (connected) Serial.printf("[wifi] rssi_dbm=%d\n", WiFi.RSSI());
  else Serial.println("[wifi] rssi_dbm=unavailable");
  return connected;
}

void syncClock() {
  sntp_set_time_sync_notification_cb(onTimeSync);
  configTzTime(paperhub::timezoneRule(TZ_RULE), paperhub::ntpServer(NTP_SERVER));
  const auto started = millis();
  while (!ntpSyncedThisBoot && millis() - started < kNtpTimeoutMs) delay(20);
  Serial.printf("[clock] source=%s elapsed_ms=%lu\n",
                ntpSyncedThisBoot ? "NTP" : (clockValid() ? "RTC" : "unknown"), millis() - started);
}

bool fetchAndDraw(paperhub::View target) {
  String url(HUB_URL);
  // Append telemetry before an optional fragment, preserving existing queries.
  const int fragment = url.indexOf('#');
  if (fragment >= 0) url.remove(fragment);
  url += url.indexOf('?') >= 0 ? '&' : '?';
  const auto battery = M5.Power.getBatteryLevel();
  if (battery >= 0 && battery <= 100) url += "bat=" + String(battery) + "&";
  url += "rssi=" + String(WiFi.RSSI()) + "&view=" + paperhub::viewQuery(target);
  if (!url.startsWith("http://")) {
    Serial.println("[http] status=0 received_bytes=0 error=expected_http_url");
    return false;
  }
  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(3000);
  http.setTimeout(10000);
  if (!http.begin(client, url)) {
    Serial.println("[http] status=0 received_bytes=0 error=begin_failed");
    return false;
  }
  const char* responseHeaders[] = {"X-Paper-View"};
  http.collectHeaders(responseHeaders, 1);
  const int status = http.GET();
  const int expected = http.getSize();
  Serial.printf("[http] status=%d content_length=%d\n", status, expected);
  if (status != HTTP_CODE_OK || expected == 0 || expected > int(paperhub::kMaxPngBytes)) {
    Serial.printf("[http] status=%d received_bytes=0 result=rejected\n", status);
    http.end();
    return false;
  }
  if (http.header("X-Paper-View") != paperhub::viewQuery(target)) {
    Serial.printf("[http] status=%d received_bytes=0 error=view_header_mismatch\n", status);
    http.end();
    return false;
  }
  // writeToStream also decodes chunked bodies; no PNG String or DRAM copy.
  PsramStream png(expected > 0 ? size_t(expected) : paperhub::kMaxPngBytes);
  if (!png.data()) {
    Serial.printf("[http] status=%d received_bytes=0 error=psram_allocation\n", status);
    http.end();
    return false;
  }
  const int received = http.writeToStream(&png);
  Serial.printf("[http] status=%d received_bytes=%u stream_result=%d\n",
                status, unsigned(png.size()), received);
  http.end();
  if (received < 0 || png.rejected || (expected >= 0 && png.size() != size_t(expected)) ||
      !paperhub::validPng(png.data(), png.size())) {
    Serial.println("[draw] result=failed reason=incomplete_or_invalid_png");
    return false;
  }
  // Preflight decode in PSRAM; malformed compressed data must not replace the
  // previous panel image. The display itself is updated only after validation.
  M5Canvas check(&M5.Display);
  check.setPsram(true);
  check.setColorDepth(8);
  if (!check.createSprite(paperhub::kWidth, paperhub::kHeight) ||
      !check.drawPng(png.data(), png.size())) {
    Serial.println("[draw] result=failed reason=decode_preflight");
    check.deleteSprite();
    return false;
  }
  check.deleteSprite();
  M5.Display.setEpdMode(epd_mode_t::epd_quality);
  const bool drawn = M5.Display.drawPng(png.data(), png.size(), 0, 0);
  if (drawn) {
    M5.Display.display();
    M5.Display.waitDisplay();
  }
  Serial.printf("[draw] result=%s\n", drawn ? "ok" : "failed");
  return drawn;
}

void markFailure() {
  if (M5.Display.width() != paperhub::kWidth || M5.Display.height() != paperhub::kHeight) {
    Serial.println("[draw] marker_skipped=display_unavailable");
    return;
  }
  constexpr int width = 128, height = 28;
  const int x = M5.Display.width() - width, y = M5.Display.height() - height;
  char timestamp[8] = "--:--";
  if (clockValid()) {
    const time_t now = time(nullptr);
    struct tm local{};
    localtime_r(&now, &local);
    strftime(timestamp, sizeof(timestamp), "%H:%M", &local);
  }
  M5.Display.setEpdMode(epd_mode_t::epd_fast);
  M5.Display.fillRect(x, y, width, height, TFT_WHITE);
  // Draw the multiplication mark as two strokes, independent of font support.
  M5.Display.drawLine(x + 5, y + 8, x + 17, y + 20, TFT_BLACK);
  M5.Display.drawLine(x + 5, y + 9, x + 17, y + 21, TFT_BLACK);
  M5.Display.drawLine(x + 5, y + 20, x + 17, y + 8, TFT_BLACK);
  M5.Display.drawLine(x + 5, y + 21, x + 17, y + 9, TFT_BLACK);
  M5.Display.setTextColor(TFT_BLACK, TFT_WHITE);
  M5.Display.setFont(&fonts::Font2);
  M5.Display.setTextSize(1);
  M5.Display.drawString(timestamp, x + 28, y + 5);
  M5.Display.display(x, y, width, height);
  M5.Display.waitDisplay();
  Serial.printf("[draw] partial_error_marker=%s\n", timestamp);
}

void sleepUntilNextUpdate() {
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  struct tm local{};
  const time_t now = time(nullptr);
  if (clockValid()) localtime_r(&now, &local);
  const unsigned seconds = paperhub::sleepSeconds(clockValid(), local.tm_hour);
  // Held buttons are excluded individually from this sleep's wake mask.
  const auto started = millis();
  while ((digitalRead(kButtonA) == LOW || digitalRead(kButtonB) == LOW) &&
         millis() - started < 2000) delay(10);
  uint64_t mask = paperhub::releasedWakeMask(digitalRead(kButtonA) != LOW,
                                            digitalRead(kButtonB) != LOW, kButtonA, kButtonB);
  esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_EXT0);
  esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_EXT1);
  // ext1 does not keep the RTC peripheral domain on automatically.
  if (esp_sleep_pd_config(ESP_PD_DOMAIN_RTC_PERIPH, ESP_PD_OPTION_ON) != ESP_OK) mask = 0;
  for (const auto pin : {kButtonA, kButtonB}) {
    if (!(mask & paperhub::pinBit(pin))) continue;
    if (rtc_gpio_init(pin) != ESP_OK ||
        rtc_gpio_set_direction(pin, RTC_GPIO_MODE_INPUT_ONLY) != ESP_OK ||
        rtc_gpio_pullup_en(pin) != ESP_OK || rtc_gpio_pulldown_dis(pin) != ESP_OK) {
      mask &= ~paperhub::pinBit(pin);
    }
  }
  if (mask && esp_sleep_enable_ext1_wakeup(mask, ESP_EXT1_WAKEUP_ANY_LOW) != ESP_OK) mask = 0;
  Serial.printf("[sleep] seconds=%u button_a_wakeup=%d button_b_wakeup=%d clock_valid=%d\n",
                seconds, bool(mask & paperhub::pinBit(kButtonA)), bool(mask & paperhub::pinBit(kButtonB)), clockValid());
  Serial.flush();
  // M5Unified 0.2.26 only changes ext0/ext1 when touch_wakeup is true.
  // false preserves our two-button mask and RTC_PERIPH=ON setting.
  M5.Power.deepSleep(uint64_t(seconds) * 1000000ULL, false);
  Serial.println("[sleep] fallback=esp_deep_sleep_start");
  Serial.flush();
  esp_sleep_enable_timer_wakeup(uint64_t(seconds) * 1000000ULL);
  esp_deep_sleep_start();
}
}  // namespace

void setup() {
  const auto cause = esp_sleep_get_wakeup_cause();
  const bool resumed = esp_reset_reason() == ESP_RST_DEEPSLEEP &&
                       (cause == ESP_SLEEP_WAKEUP_TIMER || cause == ESP_SLEEP_WAKEUP_EXT1);
  if (!resumed) displayedView = 0;
  const auto wakeButton = resumed && cause == ESP_SLEEP_WAKEUP_EXT1
      ? paperhub::buttonFromWakeMask(esp_sleep_get_ext1_wakeup_status(), kButtonA, kButtonB)
      : paperhub::Button::None;
  // Sample before the serial wait or M5.begin: a human chord may lag the
  // first ext1 edge, while the recorded wake mask contains only that first pin.
  rtc_gpio_deinit(kButtonA);
  rtc_gpio_deinit(kButtonB);
  pinMode(kButtonA, INPUT_PULLUP);
  pinMode(kButtonB, INPUT_PULLUP);
  const auto button = paperhub::resolveButton(wakeButton, digitalRead(kButtonA) == LOW,
                                             digitalRead(kButtonB) == LOW);
  Serial.begin(115200);
  const auto serialStarted = millis();
  while (!Serial && millis() - serialStarted < 1200) delay(10);
  const auto from = paperhub::sanitizeView(displayedView);
  const auto target = paperhub::nextView(from, button);
  const char* reason = !resumed ? "power_on_or_reset" :
      button == paperhub::Button::Both ? "button_ab" :
      button == paperhub::Button::B ? "button_b" :
      button == paperhub::Button::A ? "button_a" : "timer";
  Serial.printf("[boot] reason=%s wakeup_cause=%d reset_reason=%d\n",
                reason, int(cause), int(esp_reset_reason()));
  auto cfg = M5.config();
  cfg.clear_display = false;
  cfg.serial_baudrate = 115200;
  M5.begin(cfg);
  pinMode(kButtonA, INPUT_PULLUP);
  pinMode(kButtonB, INPUT_PULLUP);
  M5.Display.setRotation(1);
  if (M5.Display.width() != paperhub::kWidth) M5.Display.setRotation(0);
  M5.Display.setAutoDisplay(false);
  setenv("TZ", paperhub::timezoneRule(TZ_RULE), 1);
  tzset();
  const bool configured = WIFI_SSID[0] && HUB_URL[0];
  Serial.printf("[boot] psram_bytes=%u configured=%d\n", unsigned(ESP.getPsramSize()), configured);
  bool ok = false;
  if (configured && connectWifi()) {
    syncClock();
    ok = fetchAndDraw(target);
  } else if (!configured) {
    Serial.println("[config] missing_required_setting");
    Serial.println("[wifi] skipped=missing_configuration");
  }
  Serial.printf("[fetch] result=%s\n", ok ? "ok" : "failed");
  if (ok) displayedView = static_cast<uint8_t>(target);
  Serial.printf("[view] from=%s to=%s result=%s\n", paperhub::viewQuery(from),
                paperhub::viewQuery(target), ok ? "ok" : "kept");
  if (!ok) markFailure();
  sleepUntilNextUpdate();
}

void loop() {
  // Normally unreachable; retry sleeping if a platform call returns.
  sleepUntilNextUpdate();
  delay(20);
}
