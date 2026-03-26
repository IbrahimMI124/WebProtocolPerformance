#include "bench.hpp"

#include <App.h>

#include <iostream>
#include <string>

struct PerSocketData {};

static void usage() {
  std::cerr << "ws_server --host 0.0.0.0 --port 18081 --path /ws\n";
}

int main(int argc, char **argv) {
  const std::string host = bench::get_str(argc, argv, "--host", "127.0.0.1");
  const int port = bench::get_int(argc, argv, "--port", 18081);
  const std::string path = bench::get_str(argc, argv, "--path", "/ws");

  if (path.empty() || path[0] != '/') {
    usage();
    return 2;
  }

  uWS::App app;

  app.get("/ready", [](auto *res, auto */*req*/) {
    res->writeStatus("200 OK")->end("ok");
  });

  app.ws<PerSocketData>(path, {
    .open = [](auto */*ws*/) {},
    .message = [](auto *ws, std::string_view message, uWS::OpCode opCode) {
      ws->send(message, opCode);
    },
  });

  app.listen(host, port, [host, port](auto *listen_socket) {
    if (listen_socket) {
      std::cout << "READY " << host << " " << port << std::endl;
    } else {
      std::cerr << "Failed to listen\n";
      std::exit(2);
    }
  });

  app.run();
  return 0;
}
