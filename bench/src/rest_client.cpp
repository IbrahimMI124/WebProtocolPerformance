#include <httplib.h>

#include "bench.hpp"

#include <chrono>
#include <cstdint>
#include <iostream>
#include <string>
#include <vector>

static void usage() {
  std::cerr << "rest_client --url http://127.0.0.1:PORT --mode latency|throughput --requests N --payload-bytes B --duration-sec S --out-latency-csv PATH\n";
}

int main(int argc, char **argv) {
  const std::string url = bench::get_str(argc, argv, "--url", "http://127.0.0.1:18080");
  const std::string mode = bench::get_str(argc, argv, "--mode", "latency");
  const int requests = bench::get_int(argc, argv, "--requests", 100);
  const size_t payload_bytes = bench::get_size(argc, argv, "--payload-bytes", 4);
  const double duration_sec = bench::get_double(argc, argv, "--duration-sec", 5.0);
  const std::string out_csv = bench::get_str(argc, argv, "--out-latency-csv", "");

  if (mode != "latency" && mode != "throughput") {
    usage();
    return 2;
  }

  httplib::Client cli(url);
  cli.set_keep_alive(true);

  std::string payload(reinterpret_cast<const char *>(bench::make_payload(payload_bytes).data()), payload_bytes);

  if (mode == "latency") {
    std::vector<double> samples;
    samples.reserve(static_cast<size_t>(requests));

    for (int i = 0; i < requests; i++) {
      const auto t0 = bench::Clock::now();
      auto res = cli.Post("/echo", payload, "application/octet-stream");
      const auto t1 = bench::Clock::now();

      if (!res || res->status != 200) {
        std::cerr << "request failed\n";
        return 1;
      }
      samples.push_back(std::chrono::duration<double, std::milli>(t1 - t0).count());
    }

    if (!out_csv.empty()) {
      bench::write_latency_csv(out_csv, samples);
    }

    const auto s = bench::summarize_ms(samples);
    std::cout << "{";
    bench::print_json_kv(std::cout, "framework", std::string("rest"));
    std::cout << ",";
    bench::print_json_kv(std::cout, "metric", std::string("latency"));
    std::cout << ",";
    bench::print_json_kv(std::cout, "n", static_cast<std::uint64_t>(s.n));
    std::cout << ",";
    bench::print_json_kv(std::cout, "min_ms", s.min_ms);
    std::cout << ",";
    bench::print_json_kv(std::cout, "max_ms", s.max_ms);
    std::cout << ",";
    bench::print_json_kv(std::cout, "avg_ms", s.avg_ms);
    std::cout << ",";
    bench::print_json_kv(std::cout, "p50_ms", s.p50_ms);
    std::cout << ",";
    bench::print_json_kv(std::cout, "p95_ms", s.p95_ms);
    std::cout << "}" << std::endl;
    return 0;
  }

  // throughput
  std::uint64_t bytes = 0;
  std::uint64_t msgs = 0;
  const auto end_t = bench::Clock::now() + std::chrono::duration_cast<bench::Clock::duration>(std::chrono::duration<double>(duration_sec));
  while (bench::Clock::now() < end_t) {
    auto res = cli.Post("/echo", payload, "application/octet-stream");
    if (!res || res->status != 200) {
      std::cerr << "request failed\n";
      return 1;
    }
    bytes += static_cast<std::uint64_t>(res->body.size());
    msgs++;
  }

  const double bps = bytes / duration_sec;
  std::cout << "{";
  bench::print_json_kv(std::cout, "framework", std::string("rest"));
  std::cout << ",";
  bench::print_json_kv(std::cout, "metric", std::string("throughput"));
  std::cout << ",";
  bench::print_json_kv(std::cout, "duration_sec", duration_sec);
  std::cout << ",";
  bench::print_json_kv(std::cout, "messages", static_cast<std::uint64_t>(msgs));
  std::cout << ",";
  bench::print_json_kv(std::cout, "bytes", static_cast<std::uint64_t>(bytes));
  std::cout << ",";
  bench::print_json_kv(std::cout, "bytes_per_sec", bps);
  std::cout << "}" << std::endl;

  return 0;
}
