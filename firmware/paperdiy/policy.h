#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>

namespace paperhub {
constexpr std::size_t kMaxPngBytes = 1024 * 1024;
constexpr int kWidth = 960;
constexpr int kHeight = 540;

enum class View : std::uint8_t { Dashboard = 0, Strata = 1, Usage = 2 };
enum class Button : std::uint8_t { None, A, B, Both };

inline std::uint64_t pinBit(int pin) {
  return pin >= 0 && pin < 64 ? std::uint64_t(1) << pin : 0;
}

inline Button buttonFromWakeMask(std::uint64_t mask, int pinA, int pinB) {
  const bool a = (mask & pinBit(pinA)) != 0, b = (mask & pinBit(pinB)) != 0;
  return a ? (b ? Button::Both : Button::A) : (b ? Button::B : Button::None);
}

// Keep the latched wake source; promote a chord if the other input is now low.
// Ignore button levels on timer/cold boots, which have no button wake source.
inline Button resolveButton(Button wake, bool aPressed, bool bPressed) {
  if (wake == Button::None || wake == Button::Both) return wake;
  if ((wake == Button::A && bPressed) || (wake == Button::B && aPressed)) return Button::Both;
  return wake;
}

inline View sanitizeView(std::uint8_t raw) {
  return raw <= 2 ? static_cast<View>(raw) : View::Dashboard;
}

inline View nextView(View displayed, Button pressed) {
  displayed = sanitizeView(static_cast<std::uint8_t>(displayed));
  if (pressed == Button::Both) return View::Dashboard;
  if (pressed == Button::B) return static_cast<View>((static_cast<unsigned>(displayed) + 1) % 3);
  return displayed;
}

inline const char* viewQuery(View view) {
  switch (view) {
    case View::Strata: return "strata";
    case View::Usage: return "usage";
    default: return "dashboard";
  }
}

inline std::uint64_t releasedWakeMask(bool aReleased, bool bReleased, int pinA, int pinB) {
  return (aReleased ? pinBit(pinA) : 0) | (bReleased ? pinBit(pinB) : 0);
}

inline const char* ntpServer(const char* configured) {
  return configured && configured[0] ? configured : "pool.ntp.org";
}

inline const char* timezoneRule(const char* configured) {
  return configured && configured[0] ? configured : "UTC0";
}

inline unsigned sleepSeconds(bool clockValid, int hour) {
  return clockValid && hour >= 7 && hour < 24 ? 600 : 1800;
}

inline bool append(std::uint8_t* buffer, std::size_t capacity, std::size_t& used,
                   const std::uint8_t* source, std::size_t count) {
  if (used > capacity || count > capacity - used) return false;
  if (count) std::memcpy(buffer + used, source, count);
  used += count;
  return true;
}

inline std::uint32_t be32(const std::uint8_t* p) {
  return (std::uint32_t(p[0]) << 24) | (std::uint32_t(p[1]) << 16) |
         (std::uint32_t(p[2]) << 8) | p[3];
}

inline std::uint32_t crc32(const std::uint8_t* p, std::size_t length) {
  std::uint32_t crc = 0xffffffff;
  for (std::size_t i = 0; i < length; ++i) {
    crc ^= p[i];
    for (int bit = 0; bit < 8; ++bit)
      crc = (crc >> 1) ^ ((crc & 1) ? 0xedb88320U : 0U);
  }
  return crc ^ 0xffffffff;
}

// Accept the server's noninterlaced 8-bit grayscale PNG; validate every chunk
// before the decoder is allowed to touch the physical display.
inline bool validPng(const std::uint8_t* data, std::size_t size) {
  const std::uint8_t signature[] = {137, 80, 78, 71, 13, 10, 26, 10};
  if (size < 45 || size > kMaxPngBytes || std::memcmp(data, signature, 8)) return false;
  bool header = false, pixels = false;
  std::size_t pos = 8;
  while (pos <= size && size - pos >= 12) {
    const std::size_t length = be32(data + pos);
    if (length > size - pos - 12) return false;
    const auto* type = data + pos + 4;
    const auto* body = data + pos + 8;
    if (crc32(type, length + 4) != be32(body + length)) return false;
    if (!header) {
      if (std::memcmp(type, "IHDR", 4) || length != 13 ||
          be32(body) != kWidth || be32(body + 4) != kHeight ||
          body[8] != 8 || body[9] != 0 || body[10] || body[11] || body[12]) return false;
      header = true;
    } else if (!std::memcmp(type, "IHDR", 4)) {
      return false;
    }
    if (!std::memcmp(type, "IDAT", 4)) pixels = true;
    pos += length + 12;
    if (!std::memcmp(type, "IEND", 4)) return length == 0 && pixels && pos == size;
  }
  return false;
}
}  // namespace paperhub
