#include "bench.hpp"

#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <unistd.h>

#include <array>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace {

// This file is a *minimal* WebSocket client implementation + benchmark loop.
//
// Why implement a client manually?
// - uWebSockets is primarily a server library; it doesn't ship a simple C++ WS
//   client API we can use here.
// - For a benchmark we want a single self-contained executable.
//
// What this client supports (enough for this benchmark):
// - TCP connect (no TLS)
// - HTTP Upgrade handshake (RFC 6455)
// - Send one *binary* frame per message (client frames are masked)
// - Receive one echoed frame per message
//
// What it intentionally does NOT support (to keep it short):
// - Fragmentation, extensions (permessage-deflate), ping/pong, close handshake
// - Large protocol compliance beyond what's needed for echo

static constexpr const char *kWsGuid = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";

// WebSocket handshake recap:
// - Client sends `Sec-WebSocket-Key: <random base64>`
// - Server replies with `Sec-WebSocket-Accept: base64( SHA1(key + GUID) )`
// The GUID is fixed by the RFC.

std::string base64_encode(std::string_view in) {
  // Small Base64 encoder used for:
  // - Encoding Sec-WebSocket-Key (random bytes)
  // - Encoding SHA1 digest in Sec-WebSocket-Accept validation
  static constexpr char tbl[] =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::string out;
  out.reserve(((in.size() + 2) / 3) * 4);
  size_t i = 0;
  while (i + 3 <= in.size()) {
    const uint32_t n = (static_cast<uint8_t>(in[i]) << 16) |
                       (static_cast<uint8_t>(in[i + 1]) << 8) |
                       (static_cast<uint8_t>(in[i + 2]));
    out.push_back(tbl[(n >> 18) & 63]);
    out.push_back(tbl[(n >> 12) & 63]);
    out.push_back(tbl[(n >> 6) & 63]);
    out.push_back(tbl[n & 63]);
    i += 3;
  }
  const size_t rem = in.size() - i;
  if (rem == 1) {
    const uint32_t n = (static_cast<uint8_t>(in[i]) << 16);
    out.push_back(tbl[(n >> 18) & 63]);
    out.push_back(tbl[(n >> 12) & 63]);
    out.push_back('=');
    out.push_back('=');
  } else if (rem == 2) {
    const uint32_t n = (static_cast<uint8_t>(in[i]) << 16) |
                       (static_cast<uint8_t>(in[i + 1]) << 8);
    out.push_back(tbl[(n >> 18) & 63]);
    out.push_back(tbl[(n >> 12) & 63]);
    out.push_back(tbl[(n >> 6) & 63]);
    out.push_back('=');
  }
  return out;
}

struct Sha1 {
  // Minimal SHA1 implementation.
  //
  // We only need SHA1 for the WebSocket handshake accept validation.
  // (This is not used for cryptographic security in the benchmark.)
  uint32_t h0 = 0x67452301;
  uint32_t h1 = 0xEFCDAB89;
  uint32_t h2 = 0x98BADCFE;
  uint32_t h3 = 0x10325476;
  uint32_t h4 = 0xC3D2E1F0;

  uint64_t total_bits = 0;
  std::vector<uint8_t> buf;

  static uint32_t rol(uint32_t v, uint32_t n) { return (v << n) | (v >> (32 - n)); }

  void update(const uint8_t *data, size_t len) {
    // Feed bytes into the hash. We buffer until we have a 64-byte block.
    total_bits += static_cast<uint64_t>(len) * 8;
    buf.insert(buf.end(), data, data + len);
    while (buf.size() >= 64) {
      uint32_t w[80];
      for (int i = 0; i < 16; i++) {
        // SHA1 operates on big-endian 32-bit words.
        w[i] = (buf[i * 4] << 24) | (buf[i * 4 + 1] << 16) | (buf[i * 4 + 2] << 8) | (buf[i * 4 + 3]);
      }
      for (int i = 16; i < 80; i++) {
        w[i] = rol(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);
      }

      uint32_t a = h0, b = h1, c = h2, d = h3, e = h4;
      for (int i = 0; i < 80; i++) {
        // SHA1 has 4 rounds (0-19, 20-39, 40-59, 60-79) with different
        // boolean functions and constants.
        uint32_t f = 0;
        uint32_t k = 0;
        if (i < 20) {
          f = (b & c) | ((~b) & d);
          k = 0x5A827999;
        } else if (i < 40) {
          f = b ^ c ^ d;
          k = 0x6ED9EBA1;
        } else if (i < 60) {
          f = (b & c) | (b & d) | (c & d);
          k = 0x8F1BBCDC;
        } else {
          f = b ^ c ^ d;
          k = 0xCA62C1D6;
        }
        const uint32_t temp = rol(a, 5) + f + e + k + w[i];
        e = d;
        d = c;
        c = rol(b, 30);
        b = a;
        a = temp;
      }

      h0 += a;
      h1 += b;
      h2 += c;
      h3 += d;
      h4 += e;

      buf.erase(buf.begin(), buf.begin() + 64);
    }
  }

  std::array<uint8_t, 20> final() {
    // Finalize with padding (1-bit + zeros) and 64-bit length.
    std::vector<uint8_t> tmp = buf;
    tmp.push_back(0x80);
    while ((tmp.size() % 64) != 56) tmp.push_back(0);

    for (int i = 7; i >= 0; i--) tmp.push_back(static_cast<uint8_t>((total_bits >> (i * 8)) & 0xFF));

    Sha1 s = *this;
    s.buf.clear();
    s.total_bits = 0;
    s.update(tmp.data(), tmp.size());

    std::array<uint8_t, 20> out{};
    auto put = [&](int idx, uint32_t v) {
      out[idx] = static_cast<uint8_t>((v >> 24) & 0xFF);
      out[idx + 1] = static_cast<uint8_t>((v >> 16) & 0xFF);
      out[idx + 2] = static_cast<uint8_t>((v >> 8) & 0xFF);
      out[idx + 3] = static_cast<uint8_t>(v & 0xFF);
    };
    put(0, s.h0);
    put(4, s.h1);
    put(8, s.h2);
    put(12, s.h3);
    put(16, s.h4);
    return out;
  }
};

std::string ws_accept_for_key(const std::string &key_b64) {
  // Compute what the server MUST return in Sec-WebSocket-Accept.
  std::string in = key_b64 + kWsGuid;
  Sha1 sha;
  sha.update(reinterpret_cast<const uint8_t *>(in.data()), in.size());
  const auto digest = sha.final();
  return base64_encode(std::string_view(reinterpret_cast<const char *>(digest.data()), digest.size()));
}

int connect_tcp(const std::string &host, int port) {
  // Resolve host/port and connect a TCP socket.
  //
  // We also set TCP_NODELAY to reduce Nagle's algorithm buffering, which can
  // otherwise add latency for small messages.
  addrinfo hints{};
  hints.ai_family = AF_UNSPEC;
  hints.ai_socktype = SOCK_STREAM;

  addrinfo *res = nullptr;
  const std::string port_str = std::to_string(port);
  if (getaddrinfo(host.c_str(), port_str.c_str(), &hints, &res) != 0) {
    throw std::runtime_error("getaddrinfo failed");
  }

  int fd = -1;
  for (auto *p = res; p; p = p->ai_next) {
    fd = socket(p->ai_family, p->ai_socktype, p->ai_protocol);
    if (fd < 0) continue;

    int yes = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &yes, sizeof(yes));

    if (connect(fd, p->ai_addr, p->ai_addrlen) == 0) {
      break;
    }
    close(fd);
    fd = -1;
  }

  freeaddrinfo(res);
  if (fd < 0) throw std::runtime_error("connect failed");
  return fd;
}

void write_all(int fd, const void *buf, size_t len) {
  // `write(2)` may write fewer bytes than requested.
  // This helper loops until everything is written or an error occurs.
  const uint8_t *p = static_cast<const uint8_t *>(buf);
  size_t off = 0;
  while (off < len) {
    const ssize_t n = ::write(fd, p + off, len - off);
    if (n <= 0) throw std::runtime_error("write failed");
    off += static_cast<size_t>(n);
  }
}

std::string read_until(int fd, const std::string &needle, size_t max_bytes = 64 * 1024) {
  // Read from the socket until we see `needle` (used for HTTP headers).
  // Safety: `max_bytes` prevents unbounded memory growth if the peer is bad.
  std::string out;
  out.reserve(1024);
  char buf[1024];
  while (out.find(needle) == std::string::npos) {
    const ssize_t n = ::read(fd, buf, sizeof(buf));
    if (n <= 0) throw std::runtime_error("read failed");
    out.append(buf, buf + n);
    if (out.size() > max_bytes) throw std::runtime_error("read too large");
  }
  return out;
}

struct WsConn {
  int fd;
};

WsConn ws_connect(const std::string &host, int port, const std::string &path) {
  // Perform the WebSocket HTTP Upgrade handshake and validate the response.
  int fd = connect_tcp(host, port);

  // Create the random Sec-WebSocket-Key (16 bytes, base64 encoded).
  std::array<uint8_t, 16> rnd{};
  static thread_local std::mt19937 rng{std::random_device{}()};
  for (auto &b : rnd) b = static_cast<uint8_t>(rng() & 0xFF);
  const std::string key_b64 = base64_encode(std::string_view(reinterpret_cast<const char *>(rnd.data()), rnd.size()));
  const std::string expected_accept = ws_accept_for_key(key_b64);

  std::ostringstream req;
  req << "GET " << path << " HTTP/1.1\r\n";
  req << "Host: " << host << ":" << port << "\r\n";
  req << "Upgrade: websocket\r\n";
  req << "Connection: Upgrade\r\n";
  req << "Sec-WebSocket-Key: " << key_b64 << "\r\n";
  req << "Sec-WebSocket-Version: 13\r\n";
  req << "\r\n";

  const auto s = req.str();
  write_all(fd, s.data(), s.size());

  // Read HTTP response headers until the blank line terminator.
  const auto resp = read_until(fd, "\r\n\r\n");
  if (resp.find("101") == std::string::npos) throw std::runtime_error("handshake failed");

  const std::string hdr = "Sec-WebSocket-Accept:";
  auto pos = resp.find(hdr);
  if (pos == std::string::npos) throw std::runtime_error("missing accept");
  pos += hdr.size();
  while (pos < resp.size() && (resp[pos] == ' ' || resp[pos] == '\t')) pos++;
  auto end = resp.find("\r\n", pos);
  if (end == std::string::npos) throw std::runtime_error("bad accept");
  const std::string accept = resp.substr(pos, end - pos);

  // Verify the server didn't accept a bogus handshake.
  if (accept != expected_accept) throw std::runtime_error("accept mismatch");

  return WsConn{fd};
}

void ws_send_binary(WsConn &c, const uint8_t *data, size_t len) {
  // Send a single unfragmented WebSocket *binary* frame.
  //
  // RFC 6455 requires that **client-to-server** frames are masked.
  // (Server-to-client frames are typically unmasked.)
  std::array<uint8_t, 4> mask{};
  static thread_local std::mt19937 rng{std::random_device{}()};
  for (auto &b : mask) b = static_cast<uint8_t>(rng() & 0xFF);

  std::vector<uint8_t> frame;
  frame.reserve(2 + 8 + 4 + len);

  // Byte 0: FIN=1 (no fragmentation) + opcode=2 (binary)
  frame.push_back(0x80 | 0x2); // FIN + binary

  // Byte 1: MASK bit + payload length encoding.
  // Length rules:
  // - 0..125: stored directly
  // - 126: next 2 bytes are length
  // - 127: next 8 bytes are length
  if (len < 126) {
    frame.push_back(static_cast<uint8_t>(0x80 | len));
  } else if (len <= 0xFFFF) {
    frame.push_back(0x80 | 126);
    frame.push_back(static_cast<uint8_t>((len >> 8) & 0xFF));
    frame.push_back(static_cast<uint8_t>(len & 0xFF));
  } else {
    frame.push_back(0x80 | 127);
    for (int i = 7; i >= 0; i--) frame.push_back(static_cast<uint8_t>((static_cast<uint64_t>(len) >> (i * 8)) & 0xFF));
  }

  // Mask key is 4 bytes, inserted before the payload.
  frame.insert(frame.end(), mask.begin(), mask.end());

  const size_t payload_off = frame.size();
  frame.insert(frame.end(), data, data + len);

  // Apply the XOR mask to the payload bytes.
  for (size_t i = 0; i < len; i++) {
    frame[payload_off + i] ^= mask[i % 4];
  }

  write_all(c.fd, frame.data(), frame.size());
}

std::vector<uint8_t> ws_recv_message(WsConn &c) {
  // Receive a single unfragmented WebSocket message.
  // This is enough for an echo server that sends one frame per message.
  uint8_t hdr[2];
  if (::read(c.fd, hdr, 2) != 2) throw std::runtime_error("read hdr failed");

  const bool fin = (hdr[0] & 0x80) != 0;
  const uint8_t opcode = hdr[0] & 0x0F;
  const bool masked = (hdr[1] & 0x80) != 0;
  uint64_t len = hdr[1] & 0x7F;

  // For simplicity we reject fragmented messages.
  if (!fin) throw std::runtime_error("fragmentation unsupported");
  if (opcode == 0x8) throw std::runtime_error("closed");
  if (opcode != 0x2 && opcode != 0x1) throw std::runtime_error("unexpected opcode");

  if (len == 126) {
    uint8_t ext[2];
    if (::read(c.fd, ext, 2) != 2) throw std::runtime_error("read ext failed");
    len = (ext[0] << 8) | ext[1];
  } else if (len == 127) {
    // 64-bit length in network byte order.
    uint8_t ext[8];
    if (::read(c.fd, ext, 8) != 8) throw std::runtime_error("read ext failed");
    len = 0;
    for (int i = 0; i < 8; i++) len = (len << 8) | ext[i];
  }

  std::array<uint8_t, 4> mask{};
  if (masked) {
    if (::read(c.fd, mask.data(), 4) != 4) throw std::runtime_error("read mask failed");
  }

  // Read the payload body.
  std::vector<uint8_t> payload(len);
  size_t off = 0;
  while (off < payload.size()) {
    const ssize_t n = ::read(c.fd, payload.data() + off, payload.size() - off);
    if (n <= 0) throw std::runtime_error("read payload failed");
    off += static_cast<size_t>(n);
  }

  // Unmask if needed (server-to-client frames are usually unmasked).
  if (masked) {
    for (size_t i = 0; i < payload.size(); i++) payload[i] ^= mask[i % 4];
  }

  return payload;
}

static void usage() {
  std::cerr << "ws_client --host 127.0.0.1 --port 18081 --path /ws --mode latency|throughput --requests N --payload-bytes B --duration-sec S --out-latency-csv PATH\n";
}

} // namespace

int main(int argc, char **argv) {
  // This is the *WebSocket benchmark client*.
  //
  // Like `rest_client`, it supports:
  // - `latency`: N echo round-trips
  // - `throughput`: echo loop for S seconds
  //
  // The major difference from REST is that once the WebSocket handshake is
  // complete, each message is a framed payload on the same connection.
  const std::string host = bench::get_str(argc, argv, "--host", "127.0.0.1");
  const int port = bench::get_int(argc, argv, "--port", 18081);
  const std::string path = bench::get_str(argc, argv, "--path", "/ws");
  const std::string mode = bench::get_str(argc, argv, "--mode", "latency");
  const int requests = bench::get_int(argc, argv, "--requests", 100);
  const size_t payload_bytes = bench::get_size(argc, argv, "--payload-bytes", 4);
  const double duration_sec = bench::get_double(argc, argv, "--duration-sec", 5.0);
  const std::string out_csv = bench::get_str(argc, argv, "--out-latency-csv", "");

  if (mode != "latency" && mode != "throughput") {
    usage();
    return 2;
  }

  try {
    // 1) TCP connect + HTTP upgrade handshake.
    auto conn = ws_connect(host, port, path);
    // 2) The payload we'll send each time.
    const auto payload = bench::make_payload(payload_bytes);

    if (mode == "latency") {
      std::vector<double> samples;
      samples.reserve(static_cast<size_t>(requests));

      for (int i = 0; i < requests; i++) {
        // Measure one echo RTT.
        const auto t0 = bench::Clock::now();
        ws_send_binary(conn, payload.data(), payload.size());
        auto echoed = ws_recv_message(conn);
        const auto t1 = bench::Clock::now();
        if (echoed.size() != payload.size()) throw std::runtime_error("echo size mismatch");
        samples.push_back(std::chrono::duration<double, std::milli>(t1 - t0).count());
      }

      if (!out_csv.empty()) bench::write_latency_csv(out_csv, samples);

      const auto s = bench::summarize_ms(samples);
      std::cout << "{";
      bench::print_json_kv(std::cout, "framework", std::string("websocket"));
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
      ws_send_binary(conn, payload.data(), payload.size());
      auto echoed = ws_recv_message(conn);
      bytes += static_cast<std::uint64_t>(echoed.size());
      msgs++;
    }

    const double bps = bytes / duration_sec;
    std::cout << "{";
    bench::print_json_kv(std::cout, "framework", std::string("websocket"));
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

  } catch (const std::exception &e) {
    std::cerr << "error: " << e.what() << "\n";
    return 1;
  }
}
