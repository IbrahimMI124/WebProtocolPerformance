#include "bench.hpp"

#include <rtc/rtc.hpp>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <mutex>
#include <queue>
#include <sstream>
#include <string>
#include <thread>
#include <variant>
#include <vector>

namespace {

struct MsgQueue {
  // Thread-safe queue to hand WebSocket messages from callbacks to the
  // benchmark loop.
  std::mutex m;
  std::condition_variable cv;
  std::queue<std::vector<std::uint8_t>> q;

  void push(std::vector<std::uint8_t> v) {
    {
      std::lock_guard<std::mutex> lk(m);
      q.push(std::move(v));
    }
    cv.notify_one();
  }

  std::vector<std::uint8_t> pop_blocking() {
    std::unique_lock<std::mutex> lk(m);
    cv.wait(lk, [&] { return !q.empty(); });
    auto v = std::move(q.front());
    q.pop();
    return v;
  }
};

static void usage() {
  std::cerr << "ws_client --host 127.0.0.1 --port 18081 --path /ws --mode latency|throughput --requests N --payload-bytes B --duration-sec S --out-latency-csv PATH\n";
}

} // namespace

int main(int argc, char **argv) {
  // This is the *WebSocket benchmark client*.
  //
  // Like `rest_client`, it supports:
  // - latency: N echo messages, measure RTT for each.
  // - throughput: send/echo loop for duration_sec, report bytes/sec.

  const std::string host = bench::get_str(argc, argv, "--host", "127.0.0.1");
  const int port = bench::get_int(argc, argv, "--port", 18081);
  const std::string path = bench::get_str(argc, argv, "--path", "/ws");
  const std::string mode = bench::get_str(argc, argv, "--mode", "latency");
  const int requests = bench::get_int(argc, argv, "--requests", 100);
  const size_t payload_bytes = bench::get_size(argc, argv, "--payload-bytes", 4);
  const double duration_sec = bench::get_double(argc, argv, "--duration-sec", 5.0);
  const std::string out_csv = bench::get_str(argc, argv, "--out-latency-csv", "");

  if (path.empty() || path[0] != '/') {
    usage();
    return 2;
  }

  if (mode != "latency" && mode != "throughput") {
    usage();
    return 2;
  }

  rtc::InitLogger(rtc::LogLevel::Warning);

  MsgQueue mq;
  std::atomic<bool> ws_open{false};
  std::atomic<bool> ws_closed{false};
  std::atomic<bool> ws_error{false};
  std::mutex err_mutex;
  std::string err_msg;

  auto ws = std::make_shared<rtc::WebSocket>();
  ws->onOpen([&] {
    ws_open.store(true);
  });
  ws->onClosed([&] {
    ws_closed.store(true);
  });
  ws->onError([&](const std::string &err) {
    {
      std::lock_guard<std::mutex> lk(err_mutex);
      err_msg = err;
    }
    ws_error.store(true);
  });
  ws->onMessage([&](std::variant<rtc::binary, rtc::string> msg) {
    if (std::holds_alternative<rtc::binary>(msg)) {
      const auto &b = std::get<rtc::binary>(msg);
      std::vector<std::uint8_t> v;
      v.reserve(b.size());
      for (auto by : b) v.push_back(std::to_integer<std::uint8_t>(by));
      mq.push(std::move(v));
    } else {
      const auto &s = std::get<rtc::string>(msg);
      mq.push(std::vector<std::uint8_t>(s.begin(), s.end()));
    }
  });

  std::ostringstream url;
  url << "ws://" << host << ":" << port << path;
  ws->open(url.str());

  while (!ws_open.load()) {
    if (ws_error.load()) {
      std::lock_guard<std::mutex> lk(err_mutex);
      if (!err_msg.empty()) {
        std::cerr << "ws error: " << err_msg << "\n";
      } else {
        std::cerr << "ws error\n";
      }
      return 1;
    }
    if (ws_closed.load()) {
      std::cerr << "ws closed\n";
      return 1;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }

  const auto payload = bench::make_payload(payload_bytes);
  rtc::binary payload_bin;
  payload_bin.reserve(payload.size());
  for (auto u8 : payload) payload_bin.push_back(static_cast<std::byte>(u8));

  if (mode == "latency") {
    std::vector<double> samples;
    samples.reserve(static_cast<size_t>(requests));

    for (int i = 0; i < requests; i++) {
      const auto t0 = bench::Clock::now();
      ws->send(payload_bin);
      auto echoed = mq.pop_blocking();
      const auto t1 = bench::Clock::now();

      if (echoed.size() != payload.size()) {
        std::cerr << "echo mismatch\n";
        return 1;
      }

      samples.push_back(std::chrono::duration<double, std::milli>(t1 - t0).count());
    }

    if (!out_csv.empty()) {
      bench::write_latency_csv(out_csv, samples);
    }

    const auto s = bench::summarize_ms(samples);
    std::cout << "{";
    bench::print_json_kv(std::cout, "framework", std::string("ws"));
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

  std::uint64_t bytes = 0;
  std::uint64_t msgs = 0;
  const auto end_t = bench::Clock::now() + std::chrono::duration_cast<bench::Clock::duration>(std::chrono::duration<double>(duration_sec));
  while (bench::Clock::now() < end_t) {
    ws->send(payload_bin);
    auto echoed = mq.pop_blocking();
    bytes += static_cast<std::uint64_t>(echoed.size());
    msgs++;
  }

  const double bps = bytes / duration_sec;
  std::cout << "{";
  bench::print_json_kv(std::cout, "framework", std::string("ws"));
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
