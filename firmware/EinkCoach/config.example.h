// Copy this file to config.h (gitignored) and fill it in.
#pragma once

// The phone hotspot: the laptop, this display and the camera all join it.
// iPhone: Settings > Personal Hotspot > "Maximize Compatibility" ON (the ESP32 only does 2.4 GHz).
#define WIFI_SSID "your-hotspot"
#define WIFI_PASSWORD "your-password"

// The laptop running `python -m coach serve`. Either its IP on the hotspot (the server prints it
// at startup) or its Bonjour name ending in .local (macOS: `scutil --get LocalHostName` + ".local").
#define COACH_HOST "my-laptop.local"
#define COACH_PORT 8000

// How often to ask the server whether the screen changed.
#define POLL_MS 3000

// Partial refreshes are fast and don't flash; every Nth update is a full refresh to clear ghosting.
#define FULL_REFRESH_EVERY 8
