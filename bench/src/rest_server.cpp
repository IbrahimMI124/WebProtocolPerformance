#include <httplib.h>

#include "bench.hpp"

#include <iostream>
#include <string>

int main(int argc, char **argv) {
  // This executable is the *REST echo server* used by the benchmark suite.
  //
  // What it does:
  // - Exposes a tiny HTTP API.
  // - Accepts a POST body on `/echo` and responds with the exact same bytes.
  //
  // Why an echo server?
  // - It is the simplest request/response workload.
  // - Clients can measure round-trip time (latency) and sustained rate (throughput).
  //
  // How it integrates with `bench/run_bench.py`:
  // - The Python runner starts this process.
  // - It waits until it prints: `READY host port`
  // - Then it runs `rest_client` against it.

  // CLI: `--host` and `--port` are used so the runner can pick ports.
  const std::string host = bench::get_str(argc, argv, "--host", "127.0.0.1");
  const int port = bench::get_int(argc, argv, "--port", 18080);

  // cpp-httplib provides a simple embedded HTTP server.
  httplib::Server svr;

  // Health/readiness endpoint used by some manual checks.
  svr.Get("/ready", [](const httplib::Request &, httplib::Response &res) {
    res.set_content("ok", "text/plain");
  });

  // Echo endpoint: send bytes in, get identical bytes out.
  // The content-type is `application/octet-stream` because this is raw binary.
  svr.Post("/echo", [](const httplib::Request &req, httplib::Response &res) {
    res.set_content(req.body, "application/octet-stream");
  });

  // Bind first so we can print a single READY line, then enter the accept loop.
  if (!svr.bind_to_port(host, port)) {
    std::cerr << "Failed to bind " << host << ":" << port << "\n";
    return 2;
  }

  // IMPORTANT: The runner looks for this exact prefix.
  std::cout << "READY " << host << " " << port << std::endl;
  svr.listen_after_bind();
  return 0;
}
