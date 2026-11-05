#include <cstdlib>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#include <vector>
#include "policy.h"

int main(int argc, char** argv) {
  if (argc < 2) return 2;
  const std::string mode(argv[1]);
  if (mode == "views") {
    using paperhub::View;
    using paperhub::Button;
    for (unsigned v = 0; v < 3; ++v) {
      const View current = static_cast<View>(v);
      if (paperhub::nextView(current, Button::None) != current ||
          paperhub::nextView(current, Button::A) != current ||
          paperhub::nextView(current, Button::B) != static_cast<View>((v + 1) % 3) ||
          paperhub::nextView(current, Button::Both) != View::Dashboard) return 1;
      View cycle = current;
      for (int i = 0; i < 3; ++i) cycle = paperhub::nextView(cycle, Button::B);
      if (cycle != current || paperhub::sanitizeView(v) != current) return 1;
    }
    for (unsigned v = 3; v < 256; ++v)
      if (paperhub::sanitizeView(v) != View::Dashboard) return 1;
    if (std::string(paperhub::viewQuery(View::Dashboard)) != "dashboard" ||
        std::string(paperhub::viewQuery(View::Strata)) != "strata" ||
        std::string(paperhub::viewQuery(View::Usage)) != "usage" ||
        std::string(paperhub::viewQuery(static_cast<View>(9))) != "dashboard") return 1;
    const Button expected[] = {Button::None, Button::A, Button::B, Button::Both};
    for (unsigned mask = 0; mask < 4; ++mask) {
      const auto bits = paperhub::releasedWakeMask(mask & 1, mask & 2, 4, 3);
      if (paperhub::buttonFromWakeMask(bits | (1ULL << 50), 4, 3) != expected[mask]) return 1;
    }
    if (paperhub::buttonFromWakeMask(~0ULL, -1, 64) != Button::None) return 1;
    return 0;
  }
  if (mode == "config" && argc == 4) {
    std::cout << paperhub::ntpServer(argv[2]) << "|" << paperhub::timezoneRule(argv[3]);
    return 0;
  }
  if (mode == "sleep" && argc == 4) {
    std::cout << paperhub::sleepSeconds(std::atoi(argv[2]), std::atoi(argv[3]));
    return 0;
  }
  if (mode == "png" && argc == 3) {
    std::ifstream file(argv[2], std::ios::binary);
    std::vector<uint8_t> data((std::istreambuf_iterator<char>(file)), {});
    std::cout << paperhub::validPng(data.data(), data.size());
    return 0;
  }
  if (mode == "append") {
    uint8_t storage[6] = {9, 0, 0, 0, 0, 9};
    const uint8_t payload[] = {1, 2, 3, 4};
    size_t used = 0;
    if (!paperhub::append(storage + 1, 4, used, payload, 2)) return 1;
    if (!paperhub::append(storage + 1, 4, used, payload + 2, 2)) return 1;
    if (paperhub::append(storage + 1, 4, used, payload, 1)) return 1;
    if (used != 4 || storage[0] != 9 || storage[5] != 9) return 1;
    for (int i = 0; i < 4; ++i) if (storage[i + 1] != payload[i]) return 1;
    used = 5;
    if (paperhub::append(storage + 1, 4, used, payload, 0)) return 1;
    return 0;
  }
  return 2;
}
