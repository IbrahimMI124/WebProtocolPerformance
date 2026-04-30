#pragma once

// `bench.hpp`
// ------------
// This header holds small, reusable helpers used by all benchmark binaries.
// The goal is to keep the individual `*_client.cpp` and `*_server.cpp` files
// focused on protocol logic, while common tasks live here:
//
// - Timing helpers (steady clock, ISO timestamp)
// - Payload generation (random bytes)
// - Simple summary statistics (min/max/avg/p50/p95)
// - Tiny CLI argument parsing (`--key value` and flags)
// - CSV writing for latency samples
//
// The implementations are intentionally straightforward and dependency-free.

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <numeric>
#include <optional>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace bench {

// We use a monotonic clock so elapsed-time measurements don't jump if the system
// clock changes (NTP adjustments, manual time changes, etc.).
using Clock = std::chrono::steady_clock;

// Return an ISO-8601 UTC timestamp like "2026-03-26T05:12:20Z".
// This is mainly for labeling outputs.
inline std::string now_iso8601_utc() {
  using std::chrono::system_clock;
  auto t = system_clock::to_time_t(system_clock::now());
  std::tm tm{};
  gmtime_r(&t, &tm);
  char buf[32];
  std::snprintf(buf, sizeof(buf), "%04d-%02d-%02dT%02d:%02d:%02dZ",
                tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday,
                tm.tm_hour, tm.tm_min, tm.tm_sec);
  return std::string(buf);
}

// Create a byte payload of size `bytes`.
//
// Why random? If we always send identical bytes, some layers can optimize
// (compression, deduplication, caches). Random payloads make the benchmark
// closer to "real" data.
inline std::vector<std::uint8_t> make_payload(size_t bytes) {
  std::vector<std::uint8_t> p(bytes);
  static thread_local std::mt19937 rng{std::random_device{}()};
  std::uniform_int_distribution<int> dist(0, 255);
  for (auto &b : p) b = static_cast<std::uint8_t>(dist(rng));
  return p;
}

// Compute a percentile from a set of latency samples.
//
// Example: p=50 gives the median, p=95 gives "p95".
// We do a simple sort and linear interpolation.
inline double percentile_ms(std::vector<double> samples_ms, double p) {
  if (samples_ms.empty()) return 0.0;
  if (p <= 0) {
    return *std::min_element(samples_ms.begin(), samples_ms.end());
  }
  if (p >= 100) {
    return *std::max_element(samples_ms.begin(), samples_ms.end());
  }
  std::sort(samples_ms.begin(), samples_ms.end());
  const double idx = (p / 100.0) * (samples_ms.size() - 1);
  const auto lo = static_cast<size_t>(idx);
  const auto hi = std::min(lo + 1, samples_ms.size() - 1);
  const double frac = idx - lo;
  return samples_ms[lo] * (1.0 - frac) + samples_ms[hi] * frac;
}

// A small summary block for a latency distribution.
// All units are milliseconds.
struct Summary {
  size_t n = 0;
  double min_ms = 0;
  double max_ms = 0;
  double avg_ms = 0;
  double p50_ms = 0;
  double p95_ms = 0;
};

// Summarize a list of milliseconds: min/max/average/p50/p95.
inline Summary summarize_ms(const std::vector<double> &samples_ms) {
  Summary s;
  s.n = samples_ms.size();
  if (samples_ms.empty()) return s;
  s.min_ms = *std::min_element(samples_ms.begin(), samples_ms.end());
  s.max_ms = *std::max_element(samples_ms.begin(), samples_ms.end());
  s.avg_ms = std::accumulate(samples_ms.begin(), samples_ms.end(), 0.0) / samples_ms.size();
  s.p50_ms = percentile_ms(samples_ms, 50);
  s.p95_ms = percentile_ms(samples_ms, 95);
  return s;
}

// Write latency samples to a 1-column CSV.
//
// This is useful because you can:
// - plot a histogram/CDF later
// - compute additional percentiles
// - compare runs visually
inline void write_latency_csv(const std::string &path, const std::vector<double> &samples_ms) {
  std::ofstream f(path);
  if (!f) throw std::runtime_error("failed to open CSV: " + path);
  f << "latency_ms\n";
  for (auto v : samples_ms) f << v << "\n";
}

// ---- Tiny CLI parsing helpers ----
//
// These functions support a minimal convention used across all binaries:
//
//   --key value
//   --flag
//
// We intentionally don't depend on a full CLI library to keep the project small.
inline std::optional<std::string> get_arg(int argc, char **argv, std::string_view key) {
  const std::string k(key);
  for (int i = 1; i + 1 < argc; i++) {
    if (argv[i] == k) return std::string(argv[i + 1]);
  }
  return std::nullopt;
}

// Return true if `--key` appears anywhere in argv.
inline bool has_flag(int argc, char **argv, std::string_view key) {
  const std::string k(key);
  for (int i = 1; i < argc; i++) {
    if (argv[i] == k) return true;
  }
  return false;
}

// Parse an int from `--key value`, or return `def`.
inline int get_int(int argc, char **argv, std::string_view key, int def) {
  auto v = get_arg(argc, argv, key);
  if (!v) return def;
  return std::stoi(*v);
}

// Parse a size from `--key value`, or return `def`.
inline size_t get_size(int argc, char **argv, std::string_view key, size_t def) {
  auto v = get_arg(argc, argv, key);
  if (!v) return def;
  return static_cast<size_t>(std::stoull(*v));
}

// Parse a string from `--key value`, or return `def`.
inline std::string get_str(int argc, char **argv, std::string_view key, const std::string &def) {
  auto v = get_arg(argc, argv, key);
  if (!v) return def;
  return *v;
}

// Parse a floating-point number from `--key value`, or return `def`.
inline double get_double(int argc, char **argv, std::string_view key, double def) {
  auto v = get_arg(argc, argv, key);
  if (!v) return def;
  return std::stod(*v);
}

// ---- Minimal JSON printing ----
//
// Our binaries print one-line JSON objects on stdout.
// We keep it dependency-free by implementing only what we need.
inline void print_json_kv(std::ostream &os, const char *k, const std::string &v) {
  os << "\"" << k << "\":\"";
  for (char c : v) {
    if (c == '"' || c == '\\') os << '\\';
    os << c;
  }
  os << "\"";
}

inline void print_json_kv(std::ostream &os, const char *k, double v) {
  os << "\"" << k << "\":" << v;
}

inline void print_json_kv(std::ostream &os, const char *k, std::uint64_t v) {
  os << "\"" << k << "\":" << v;
}

} // namespace bench
