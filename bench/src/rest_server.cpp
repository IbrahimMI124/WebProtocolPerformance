#include <httplib.h>

#include "bench.hpp"

#include <iostream>
#include <string>

int main(int argc, char **argv) {
  const std::string host = bench::get_str(argc, argv, "--host", "127.0.0.1");
  const int port = bench::get_int(argc, argv, "--port", 18080);

  httplib::Server svr;

  svr.Get("/ready", [](const httplib::Request &, httplib::Response &res) {
    res.set_content("ok", "text/plain");
  });

  svr.Post("/echo", [](const httplib::Request &req, httplib::Response &res) {
    res.set_content(req.body, "application/octet-stream");
  });

  if (!svr.bind_to_port(host, port)) {
    std::cerr << "Failed to bind " << host << ":" << port << "\n";
    return 2;
  }

  std::cout << "READY " << host << " " << port << std::endl;
  svr.listen_after_bind();
  return 0;
}
