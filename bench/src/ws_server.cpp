#include "bench.hpp"

#include <App.h>

#include <iostream>
#include <string>

// uWebSockets requires you to define a per-connection data type.
// We don't need any state, so it's an empty struct.
struct PerSocketData {};

static void usage() {
  std::cerr << "ws_server --host 0.0.0.0 --port 18081 --path /ws\n";
}

int main(int argc, char **argv) {
  // This executable is the *WebSocket echo server*.
  //
  // What it does:
  // - Listens on `host:port`.
  // - Exposes a WebSocket endpoint at `path` (e.g. `/ws`).
  // - Echoes every received message back to the same client.
  //
  // The benchmark client (`ws_client`) measures latency and throughput by
  // sending binary frames and waiting for the echoed response.

  const std::string host = bench::get_str(argc, argv, "--host", "127.0.0.1");
  const int port = bench::get_int(argc, argv, "--port", 18081);
  const std::string path = bench::get_str(argc, argv, "--path", "/ws");

  if (path.empty() || path[0] != '/') {
    usage();
    return 2;
  }

  // uWS::App is the main uWebSockets application object.
  uWS::App app;

  // Simple HTTP readiness endpoint (not WebSocket).
  app.get("/ready", [](auto *res, auto */*req*/) {
    res->writeStatus("200 OK")->end("ok");
  });

  // Register a WebSocket route at `path`.
  // uWebSockets takes a config struct with callbacks.
  app.ws<PerSocketData>(path, {
    .open = [](auto */*ws*/) {},
    .message = [](auto *ws, std::string_view message, uWS::OpCode opCode) {
      // Echo back exactly what we received.
      // `opCode` preserves whether it was text or binary.
      ws->send(message, opCode);
    },
  });

  // Start listening. The callback tells us whether it succeeded.
  app.listen(host, port, [host, port](auto *listen_socket) {
    if (listen_socket) {
      // IMPORTANT: The Python runner looks for this exact READY prefix.
      std::cout << "READY " << host << " " << port << std::endl;
    } else {
      std::cerr << "Failed to listen\n";
      std::exit(2);
    }
  });

  // Enter the event loop.
  app.run();
  return 0;
}
