#include "bench.hpp"

#include <nlohmann/json.hpp>
#include <rtc/rtc.hpp>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <mutex>
#include <optional>
#include <queue>
#include <string>
#include <thread>
#include <vector>

using json = nlohmann::json;

static void usage() {
  std::cerr << "webrtc_client --signaling ws://127.0.0.1:18082 --mode setup|latency|throughput --requests N --payload-bytes B --duration-sec S --out-latency-csv PATH [--timeout-sec T] [--verbose]\n";
}

struct MsgQueue {
  // Thread-safe queue used to hand DataChannel messages from the libdatachannel
  // callback thread to the benchmark loop.
  //
  // Why do we need this?
  // - `dc->onMessage(...)` runs asynchronously.
  // - Our benchmark loop wants to "send, then wait for the echo".
  // - So we push received payloads into a queue and pop them in order.
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
    // Wait until at least one message arrives.
    std::unique_lock<std::mutex> lk(m);
    cv.wait(lk, [&] { return !q.empty(); });
    auto v = std::move(q.front());
    q.pop();
    return v;
  }
};

int main(int argc, char **argv) {
  // This executable is the *WebRTC benchmark client*.
  //
  // WebRTC is different from REST/WebSocket because it has a "setup" phase:
  // - Signaling connection opens
  // - Offer/answer SDP exchange
  // - ICE candidate exchange
  // - DataChannel opens
  //
  // After the DataChannel is open, we treat it like a message channel and run
  // the same latency/throughput loops.

  const std::string signaling = bench::get_str(argc, argv, "--signaling", "ws://127.0.0.1:18082");
  const std::string mode = bench::get_str(argc, argv, "--mode", "latency");
  const int requests = bench::get_int(argc, argv, "--requests", 100);
  const size_t payload_bytes = bench::get_size(argc, argv, "--payload-bytes", 4);
  const double duration_sec = bench::get_double(argc, argv, "--duration-sec", 5.0);
  const std::string out_csv = bench::get_str(argc, argv, "--out-latency-csv", "");
  const double timeout_sec = bench::get_double(argc, argv, "--timeout-sec", 30.0);
  const bool verbose = bench::has_flag(argc, argv, "--verbose");

  if (mode != "setup" && mode != "latency" && mode != "throughput") {
    usage();
    return 2;
  }

  rtc::InitLogger(rtc::LogLevel::Warning);

  // Create the PeerConnection (the main WebRTC object).
  rtc::Configuration pcConfig;
  pcConfig.disableAutoNegotiation = true;

  auto pc = std::make_shared<rtc::PeerConnection>(pcConfig);

  // Create a DataChannel proactively.
  // On the server, `onDataChannel` will fire when this arrives.
  auto dc = pc->createDataChannel("bench");

  MsgQueue mq;
  std::atomic<bool> dc_open{false};
  std::atomic<bool> ws_error{false};

  // DataChannel open event: this means setup is complete.
  dc->onOpen([&] {
    dc_open.store(true);
    if (verbose) std::cerr << "dc open\n";
  });

  // DataChannel message event: push bytes into our blocking queue.
  dc->onMessage([&](std::variant<rtc::binary, rtc::string> msg) {
    if (std::holds_alternative<rtc::binary>(msg)) {
      const auto &b = std::get<rtc::binary>(msg);
      std::vector<std::uint8_t> v;
      v.reserve(b.size());
      // `rtc::binary` is a vector of `std::byte`, so convert to uint8_t.
      for (auto by : b) v.push_back(std::to_integer<std::uint8_t>(by));
      mq.push(std::move(v));
    } else {
      const auto &s = std::get<rtc::string>(msg);
      mq.push(std::vector<std::uint8_t>(s.begin(), s.end()));
    }
  });

  // Signaling WebSocket. This is separate from the WebRTC transport.
  auto ws = std::make_shared<rtc::WebSocket>();

  // Receive signaling messages from the server.
  ws->onMessage([&](std::variant<rtc::binary, rtc::string> data) {
    if (!std::holds_alternative<rtc::string>(data)) return;
    auto j = json::parse(std::get<rtc::string>(data), nullptr, false);
    if (j.is_discarded()) return;

    const std::string type = j.value("type", "");
    if (verbose) std::cerr << "signal: " << type << "\n";
    if (type == "answer" || type == "offer") {
      // Server SDP. For our flow we expect an "answer".
      const std::string sdp = j.value("sdp", "");
      if (!sdp.empty()) pc->setRemoteDescription(sdp);
    } else if (type == "candidate") {
      // ICE candidate discovered by the server.
      const std::string cand = j.value("cand", "");
      const std::string mid = j.value("mid", "");
      if (!cand.empty()) {
        if (!mid.empty()) pc->addRemoteCandidate(rtc::Candidate(cand, mid));
        else pc->addRemoteCandidate(cand);
      }
    }
  });

  ws->onError([&](const std::string &err) {
    std::cerr << "ws error: " << err << "\n";
    ws_error.store(true);
  });

  // When we create local SDP (offer), send it to the signaling server.
  pc->onLocalDescription([ws](rtc::Description desc) {
    json j;
    j["type"] = std::string(desc.typeString());
    j["sdp"] = std::string(desc);
    ws->send(j.dump());
  });

  // When we discover a local ICE candidate, send it to the signaling server.
  pc->onLocalCandidate([ws](rtc::Candidate cand) {
    json j;
    j["type"] = "candidate";
    j["cand"] = cand.candidate();
    j["mid"] = cand.mid();
    ws->send(j.dump());
  });

  // ---- Setup phase timing ----
  // We start timing before opening the signaling socket and stop after the
  // DataChannel is open.
  const auto t_setup0 = bench::Clock::now();
  ws->open(signaling);

  // Wait for the signaling WebSocket to connect (with timeout).
  const auto ws_deadline = bench::Clock::now() + std::chrono::duration_cast<bench::Clock::duration>(std::chrono::duration<double>(timeout_sec));
  while (!ws->isOpen()) {
    if (ws->isClosed()) {
      std::cerr << "signaling closed\n";
      return 1;
    }
    if (ws_error.load()) {
      std::cerr << "signaling error\n";
      return 1;
    }
    if (bench::Clock::now() > ws_deadline) {
      std::cerr << "timeout waiting for signaling open\n";
      return 1;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }

  // Kick off negotiation by creating an offer.
  pc->setLocalDescription(); // create offer

  // Wait for the DataChannel to open (meaning SDP+ICE succeeded).
  const auto dc_deadline = bench::Clock::now() + std::chrono::duration_cast<bench::Clock::duration>(std::chrono::duration<double>(timeout_sec));
  while (!dc_open.load()) {
    if (ws->isClosed()) {
      std::cerr << "signaling closed\n";
      return 1;
    }
    if (ws_error.load()) {
      std::cerr << "signaling error\n";
      return 1;
    }
    if (bench::Clock::now() > dc_deadline) {
      std::cerr << "timeout waiting for datachannel open\n";
      return 1;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  const auto t_setup1 = bench::Clock::now();

  // Setup time includes signaling connect + negotiation + ICE + DataChannel open.
  const double setup_ms = std::chrono::duration<double, std::milli>(t_setup1 - t_setup0).count();
  if (mode == "setup") {
    // Setup-only mode: print just the setup timing.
    std::cout << "{";
    bench::print_json_kv(std::cout, "framework", std::string("webrtc"));
    std::cout << ",";
    bench::print_json_kv(std::cout, "metric", std::string("setup"));
    std::cout << ",";
    bench::print_json_kv(std::cout, "setup_ms", setup_ms);
    std::cout << "}" << std::endl;
    return 0;
  }

  // Payload for the message benchmark.
  const auto payload = bench::make_payload(payload_bytes);

  // libdatachannel uses `rtc::binary` (vector<std::byte>) for binary payloads.
  // Convert from uint8_t to std::byte.
  rtc::binary payload_bin;
  payload_bin.reserve(payload.size());
  for (auto u8 : payload) payload_bin.push_back(static_cast<std::byte>(u8));

  if (mode == "latency") {
    // Latency: send one message, wait for echo, record RTT.
    std::vector<double> samples;
    samples.reserve(static_cast<size_t>(requests));

    for (int i = 0; i < requests; i++) {
      const auto t0 = bench::Clock::now();
      dc->send(payload_bin);
      auto echoed = mq.pop_blocking();
      const auto t1 = bench::Clock::now();
      if (echoed.size() != payload.size()) {
        std::cerr << "echo mismatch\n";
        return 1;
      }
      samples.push_back(std::chrono::duration<double, std::milli>(t1 - t0).count());
    }

    if (!out_csv.empty()) bench::write_latency_csv(out_csv, samples);

    const auto s = bench::summarize_ms(samples);
    std::cout << "{";
    bench::print_json_kv(std::cout, "framework", std::string("webrtc"));
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
    std::cout << ",";
    bench::print_json_kv(std::cout, "setup_ms", setup_ms);
    std::cout << "}" << std::endl;
    return 0;
  }

  // throughput
  // Throughput: loop for `duration_sec`, send and wait for echo each time.
  // This measures "application-level throughput" under an echo workload.
  std::uint64_t bytes = 0;
  std::uint64_t msgs = 0;
  const auto end_t = bench::Clock::now() + std::chrono::duration_cast<bench::Clock::duration>(std::chrono::duration<double>(duration_sec));
  while (bench::Clock::now() < end_t) {
    dc->send(payload_bin);
    auto echoed = mq.pop_blocking();
    bytes += static_cast<std::uint64_t>(echoed.size());
    msgs++;
  }

  const double bps = bytes / duration_sec;
  std::cout << "{";
  bench::print_json_kv(std::cout, "framework", std::string("webrtc"));
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
  std::cout << ",";
  bench::print_json_kv(std::cout, "setup_ms", setup_ms);
  std::cout << "}" << std::endl;

  return 0;
}
