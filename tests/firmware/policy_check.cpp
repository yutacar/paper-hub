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
