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

// For this benchmark we keep per-client state in one struct.
// Each signaling WebSocket connection corresponds to one PeerConnection.
struct ClientState {
  std::shared_ptr<rtc::WebSocket> ws;
  std::shared_ptr<rtc::PeerConnection> pc;
  std::shared_ptr<rtc::DataChannel> dc;
};

int main(int argc, char **argv) {
  // This executable is the *WebRTC echo server*.
  //
  // Important concept: WebRTC needs signaling.
  // - WebRTC peers must exchange SDP (offer/answer) and ICE candidates before a
  //   DataChannel can open.
  // - That exchange is not part of the WebRTC transport itself; you must provide
  //   a signaling channel.
  //
  // In this benchmark we use libdatachannel's `rtc::WebSocketServer` as a simple
  // signaling mechanism.
  // - Client connects to `ws://127.0.0.1:PORT`.
  // - Signaling messages are JSON strings, with a simple schema:
  //   {"type":"offer"|"answer", "sdp":"..."}
  //   {"type":"candidate", "cand":"...", "mid":"..."}

  const int port = bench::get_int(argc, argv, "--port", 18082);
  if (port <= 0 || port > 65535) {
    usage();
    return 2;
  }

  // Keep logs quiet by default.
  rtc::InitLogger(rtc::LogLevel::Warning);

  // Configure the signaling WebSocket server.
  rtc::WebSocketServer::Configuration wsc;
  wsc.port = static_cast<uint16_t>(port);
  wsc.enableTls = false;

  rtc::WebSocketServer server(wsc);

  // IMPORTANT: The Python runner waits for this line before running clients.
  std::cout << "READY 127.0.0.1 " << port << std::endl;

  // Called once per signaling client connection.
  server.onClient([&](std::shared_ptr<rtc::WebSocket> ws) {
    auto st = std::make_shared<ClientState>();
    st->ws = ws;

    // PeerConnection configuration.
    // `disableAutoNegotiation = true` means we control when offer/answer is
    // created (we do it explicitly when we receive an offer).
    rtc::Configuration pcConfig;
    pcConfig.disableAutoNegotiation = true;

    st->pc = std::make_shared<rtc::PeerConnection>(pcConfig);

    // When the remote peer creates a DataChannel, libdatachannel will notify us
    // here and hand us the channel object.
    st->pc->onDataChannel([st](std::shared_ptr<rtc::DataChannel> incoming) {
      st->dc = incoming;

      // Echo server behavior: any message received on the DataChannel is sent back.
      // `onMessage` can deliver either binary or string.
      st->dc->onMessage([st](std::variant<rtc::binary, rtc::string> msg) {
        // Echo back.
        if (std::holds_alternative<rtc::binary>(msg)) {
          if (st->dc) st->dc->send(std::get<rtc::binary>(msg));
        } else {
          if (st->dc) st->dc->send(std::get<rtc::string>(msg));
        }
      });
    });

    // When *we* generate our local SDP (answer), send it to the client.
    st->pc->onLocalDescription([st](rtc::Description desc) {
      json j;
      j["type"] = std::string(desc.typeString());
      j["sdp"] = std::string(desc);
      if (st->ws) st->ws->send(j.dump());
    });

    // When ICE gathers a candidate, send it across signaling.
    st->pc->onLocalCandidate([st](rtc::Candidate cand) {
      json j;
      j["type"] = "candidate";
      j["cand"] = cand.candidate();
      j["mid"] = cand.mid();
      if (st->ws) st->ws->send(j.dump());
    });

    // Receive signaling messages from the client.
    ws->onMessage([st](std::variant<rtc::binary, rtc::string> data) {
      if (!std::holds_alternative<rtc::string>(data)) return;
      auto j = json::parse(std::get<rtc::string>(data), nullptr, false);
      if (j.is_discarded()) return;
      const std::string type = j.value("type", "");

      if (type == "offer" || type == "answer") {
        // SDP offer/answer from the client.
        const std::string sdp = j.value("sdp", "");
        if (!sdp.empty()) {
          // Setting the remote description tells the PeerConnection what the
          // other side supports.
          st->pc->setRemoteDescription(sdp);
          if (type == "offer") {
            // If the client sent an offer, we generate and send an answer.
            st->pc->setLocalDescription();
          }
        }
      } else if (type == "candidate") {
        // ICE candidate from the client.
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
