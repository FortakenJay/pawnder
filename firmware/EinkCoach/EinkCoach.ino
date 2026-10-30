// Pawnder's e-paper display: a dumb screen for the coach server.
//
// Hardware: Waveshare 7.5" e-Paper (V2, 800x480, black/white) on the Waveshare e-Paper ESP32 Driver Board.
// Library:  GxEPD2 (Library Manager: "GxEPD2" by Jean-Marc Zingg; it pulls in Adafruit GFX).
// Board:    "ESP32 Dev Module". Copy config.example.h to config.h first.
//
// The server renders the whole screen. This sketch only:
//   1. joins the phone hotspot,
//   2. polls GET /display/version every POLL_MS,
//   3. when it changes, downloads GET /display.bmp (1-bit 800x480) and copies its pixels to the panel.

#include <WiFi.h>
#include <HTTPClient.h>
#include <ESPmDNS.h>
#include <GxEPD2_BW.h>
#include <Fonts/FreeSansBold12pt7b.h>

#if __has_include("config.h")
#include "config.h"
#else
#error "Copy config.example.h to config.h and fill in your hotspot and laptop"
#endif

// Waveshare ESP32 Driver Board wiring: BUSY 25, RST 26, DC 27, CS 15, CLK 13, DIN 14
GxEPD2_BW<GxEPD2_750_T7, GxEPD2_750_T7::HEIGHT> display(GxEPD2_750_T7(/*CS=*/15, /*DC=*/27, /*RST=*/26, /*BUSY=*/25));
SPIClass hspi(HSPI);

static const int W = 800, H = 480, ROW_BYTES = W / 8;
static uint8_t row[ROW_BYTES];

String lastVersion = "";
IPAddress coachIp;
int updates = 0;
unsigned long lastOk = 0;
bool showingError = false;

// ------------------------------------------------------------------ status screens (drawn locally)

void message(const char *line1, const String &line2) {
  display.setFullWindow();
  display.fillScreen(GxEPD_WHITE);
  display.setTextColor(GxEPD_BLACK);
  display.setFont(&FreeSansBold12pt7b);
  display.setCursor(40, 200);
  display.print(line1);
  display.setFont(nullptr);
  display.setTextSize(2);
  display.setCursor(40, 240);
  display.print(line2);
  display.display(false);
  display.powerOff();
  lastVersion = "";  // redraw the coach screen as soon as the server answers again
}

// ------------------------------------------------------------------ network

void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("Joining %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  for (int i = 0; i < 60 && WiFi.status() != WL_CONNECTED; i++) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("Wi-Fi OK, my IP %s\n", WiFi.localIP().toString().c_str());
    MDNS.begin("eink-coach");
  }
}

// COACH_HOST is an IP, or a Bonjour name ending in .local (resolved over mDNS)
bool resolveCoach() {
  String host = COACH_HOST;
  if (coachIp.fromString(host)) return true;
  if (host.endsWith(".local")) host.remove(host.length() - 6);
  coachIp = MDNS.queryHost(host.c_str(), 3000);
  if (coachIp == IPAddress()) {
    Serial.printf("Couldn't resolve %s\n", COACH_HOST);
    return false;
  }
  Serial.printf("%s is %s\n", COACH_HOST, coachIp.toString().c_str());
  return true;
}

String url(const char *path) {
  return String("http://") + coachIp.toString() + ":" + COACH_PORT + path;
}

bool readFully(WiFiClient *s, uint8_t *buf, size_t n) {
  size_t got = 0;
  unsigned long start = millis();
  while (got < n && millis() - start < 10000) {
    int r = s->read(buf + got, n - got);
    if (r > 0) got += r;
    else delay(1);
  }
  return got == n;
}

// ------------------------------------------------------------------ the screen

bool fetchVersion(String &version) {
  HTTPClient http;
  http.setTimeout(4000);
  http.begin(url("/display/version"));
  int code = http.GET();
  if (code == 200) version = http.getString();
  http.end();
  return code == 200;
}

bool drawScreen() {
  HTTPClient http;
  http.setTimeout(10000);
  http.begin(url("/display.bmp"));
  if (http.GET() != 200) {
    http.end();
    return false;
  }
  WiFiClient *s = http.getStreamPtr();
  uint8_t h[62];
  bool ok = readFully(s, h, sizeof h) && h[0] == 'B' && h[1] == 'M';
  uint32_t offset = h[10] | h[11] << 8 | h[12] << 16 | h[13] << 24;
  int32_t width = h[18] | h[19] << 8 | h[20] << 16 | h[21] << 24;
  int32_t height = h[22] | h[23] << 8 | h[24] << 16 | h[25] << 24;
  uint16_t bpp = h[28] | h[29] << 8;
  ok = ok && width == W && (height == H || height == -H) && bpp == 1 && offset >= sizeof h;
  for (uint32_t skip = offset - sizeof h; ok && skip > 0; skip--) ok = readFully(s, row, 1);
  if (!ok) {
    Serial.println("Bad BMP from the server");
    http.end();
    return false;
  }
  bool blackIsZero = h[54] == 0 && h[55] == 0 && h[56] == 0;  // palette entry 0
  display.setFullWindow();
  display.fillScreen(GxEPD_WHITE);
  for (int r = 0; r < H && ok; r++) {
    ok = readFully(s, row, ROW_BYTES);
    if (blackIsZero)
      for (int i = 0; i < ROW_BYTES; i++) row[i] = ~row[i];  // now 1 = black
    int y = height > 0 ? H - 1 - r : r;                        // BMP rows are bottom-up
    display.drawBitmap(0, y, row, W, 1, GxEPD_BLACK);
  }
  http.end();
  if (!ok) return false;
  bool full = updates % FULL_REFRESH_EVERY == 0;
  display.display(!full);  // partial refresh unless it's time for a full one
  display.powerOff();      // keeps the image; hibernate would break the next partial refresh
  updates++;
  return true;
}

// ------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  hspi.begin(13, 12, 14, 15);  // remap SPI to the driver board's pins (SCK, MISO, MOSI, SS)
  display.epd2.selectSPI(hspi, SPISettings(4000000, MSBFIRST, SPI_MODE0));
  display.init(115200, true, 2, false);  // 2 ms reset pulse for Waveshare boards
  display.setRotation(0);
  message("Pawnder", String("Joining ") + WIFI_SSID + " ...");
  connectWifi();
  if (WiFi.status() != WL_CONNECTED) {
    message("No Wi-Fi", String("Can't join ") + WIFI_SSID + " (2.4 GHz?)");
  }
  lastOk = millis();
}

void loop() {
  connectWifi();
  String version;
  bool ok = WiFi.status() == WL_CONNECTED && (coachIp != IPAddress() || resolveCoach()) && fetchVersion(version);
  if (ok) {
    lastOk = millis();
    showingError = false;
    version.trim();
    if (version != lastVersion && drawScreen()) {
      Serial.printf("Screen %s drawn\n", version.c_str());
      lastVersion = version;
    }
  } else {
    coachIp = IPAddress();  // the laptop may have a new address: resolve again
    if (!showingError && millis() - lastOk > 30000) {
      message("Coach server not found", String(COACH_HOST) + ":" + COACH_PORT + "  is `python -m coach serve` running?");
      showingError = true;
    }
  }
  delay(POLL_MS);
}
