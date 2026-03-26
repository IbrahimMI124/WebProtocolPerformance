#include "bench.hpp"

#include <nlohmann/json.hpp>
#include <rtc/rtc.hpp>

#include <chrono>
#include <iostream>
#include <memory>
#include <string>
#include <thread>

using json = nlohmann::json;

static void usage() {
  std::cerr << "webrtc_server --port 18082\n";
}

struct ClientState {
  std::shared_ptr<rtc::WebSocket> ws;
  std::shared_ptr<rtc::PeerConnection> pc;
  std::shared_ptr<rtc::DataChannel> dc;
};

int main(int argc, char **argv) {
  const int port = bench::get_int(argc, argv, "--port", 18082);
  if (port <= 0 || port > 65535) {
    usage();
    return 2;
  }

  rtc::InitLogger(rtc::LogLevel::Warning);

  rtc::WebSocketServer::Configuration wsc;
  wsc.port = static_cast<uint16_t>(port);
  wsc.enableTls = false;

  rtc::WebSocketServer server(wsc);

  std::cout << "READY 127.0.0.1 " << port << std::endl;

  server.onClient([&](std::shared_ptr<rtc::WebSocket> ws) {
    auto st = std::make_shared<ClientState>();
    st->ws = ws;

    rtc::Configuration pcConfig;
    pcConfig.disableAutoNegotiation = true;

    st->pc = std::make_shared<rtc::PeerConnection>(pcConfig);

    st->pc->onDataChannel([st](std::shared_ptr<rtc::DataChannel> incoming) {
      st->dc = incoming;
      st->dc->onMessage([st](std::variant<rtc::binary, rtc::string> msg) {
        // Echo back.
        if (std::holds_alternative<rtc::binary>(msg)) {
          if (st->dc) st->dc->send(std::get<rtc::binary>(msg));
        } else {
          if (st->dc) st->dc->send(std::get<rtc::string>(msg));
        }
      });
    });

    st->pc->onLocalDescription([st](rtc::Description desc) {
      json j;
      j["type"] = std::string(desc.typeString());
      j["sdp"] = std::string(desc);
      if (st->ws) st->ws->send(j.dump());
    });

    st->pc->onLocalCandidate([st](rtc::Candidate cand) {
      json j;
      j["type"] = "candidate";
      j["cand"] = cand.candidate();
      j["mid"] = cand.mid();
      if (st->ws) st->ws->send(j.dump());
    });

    ws->onMessage([st](std::variant<rtc::binary, rtc::string> data) {
      if (!std::holds_alternative<rtc::string>(data)) return;
      auto j = json::parse(std::get<rtc::string>(data), nullptr, false);
      if (j.is_discarded()) return;
      const std::string type = j.value("type", "");

      if (type == "offer" || type == "answer") {
        const std::string sdp = j.value("sdp", "");
        if (!sdp.empty()) {
          st->pc->setRemoteDescription(sdp);
          if (type == "offer") {
            st->pc->setLocalDescription();
          }
        }
      } else if (type == "candidate") {
        const std::string cand = j.value("cand", "");
        const std::string mid = j.value("mid", "");
        if (!cand.empty()) {
          if (!mid.empty()) {
            st->pc->addRemoteCandidate(rtc::Candidate(cand, mid));
          } else {
            st->pc->addRemoteCandidate(cand);
          }
        }
      }
    });
  });

  // Block forever.
  while (true) std::this_thread::sleep_for(std::chrono::seconds(3600));
}
