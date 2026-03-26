cmake_minimum_required(VERSION 3.16)
project(proto_bench LANGUAGES C CXX)

# Build definition for the benchmark harness under `bench/`.
#
# This CMake project produces several small executables:
# - REST: `rest_server`, `rest_client` (cpp-httplib)
# - WebSocket: `ws_server` (uWebSockets/uSockets), `ws_client` (custom minimal client)
# - WebRTC: `webrtc_server`, `webrtc_client` (libdatachannel)
#
# Dependencies are expected as git submodules at the repo root:
# - `../cpp-httplib`
# - `../uWebSockets`
# - `../libdatachannel`

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Optional targets.
option(BENCH_ENABLE_UWEBSOCKETS "Build uWebSockets echo server" ON)
option(BENCH_ENABLE_WEBRTC "Build libdatachannel WebRTC echo" ON)

find_package(Threads REQUIRED)

add_library(bench_common INTERFACE)

# `bench_common` is an INTERFACE library: it doesn't build a binary itself.
# It just carries shared include paths / compile features for all executables.
target_include_directories(bench_common INTERFACE
  ${CMAKE_CURRENT_SOURCE_DIR}/include
)
target_compile_features(bench_common INTERFACE cxx_std_17)

# --- REST (cpp-httplib) ---
# cpp-httplib is header-only; we just add its include directory.
add_executable(rest_server src/rest_server.cpp)
target_include_directories(rest_server PRIVATE ${CMAKE_CURRENT_LIST_DIR}/../cpp-httplib)
target_link_libraries(rest_server PRIVATE bench_common Threads::Threads)

add_executable(rest_client src/rest_client.cpp)
target_include_directories(rest_client PRIVATE ${CMAKE_CURRENT_LIST_DIR}/../cpp-httplib)
target_link_libraries(rest_client PRIVATE bench_common Threads::Threads)

# --- WebSocket (uWebSockets server + minimal RFC6455 client) ---
# `ws_client` is self-contained (POSIX sockets + framing code), so it doesn't
# need uWebSockets.
add_executable(ws_client src/ws_client.cpp)
target_link_libraries(ws_client PRIVATE bench_common Threads::Threads)

if (BENCH_ENABLE_UWEBSOCKETS)
  # uWebSockets depends on uSockets. We build uSockets from source to keep the
  # benchmark easy to build on fresh machines.
  set(UWS_ROOT ${CMAKE_CURRENT_LIST_DIR}/../uWebSockets)
  set(USOCKETS_ROOT ${UWS_ROOT}/uSockets)

  # Build uSockets as a static library.
  add_library(usockets STATIC
    ${USOCKETS_ROOT}/src/bsd.c
    ${USOCKETS_ROOT}/src/context.c
    ${USOCKETS_ROOT}/src/loop.c
    ${USOCKETS_ROOT}/src/quic.c
    ${USOCKETS_ROOT}/src/socket.c
    ${USOCKETS_ROOT}/src/udp.c

    ${USOCKETS_ROOT}/src/eventing/epoll_kqueue.c
    ${USOCKETS_ROOT}/src/eventing/gcd.c
    ${USOCKETS_ROOT}/src/eventing/libuv.c
    ${USOCKETS_ROOT}/src/eventing/asio.cpp

    ${USOCKETS_ROOT}/src/crypto/openssl.c
    ${USOCKETS_ROOT}/src/crypto/sni_tree.cpp

    ${USOCKETS_ROOT}/src/io_uring/io_context.c
    ${USOCKETS_ROOT}/src/io_uring/io_loop.c
    ${USOCKETS_ROOT}/src/io_uring/io_socket.c
  )
  target_include_directories(usockets PUBLIC
    ${USOCKETS_ROOT}/src
  )
  # No TLS for this benchmark; we only need ws:// not wss://.
  target_compile_definitions(usockets PUBLIC LIBUS_NO_SSL)
  target_compile_options(usockets PRIVATE -O3)

  # WebSocket echo server.
  add_executable(ws_server src/ws_server.cpp)
  target_include_directories(ws_server PRIVATE
    ${UWS_ROOT}/src
    ${USOCKETS_ROOT}/src
    ${UWS_ROOT}
  )
  target_compile_definitions(ws_server PRIVATE UWS_NO_ZLIB)
  target_link_libraries(ws_server PRIVATE bench_common usockets Threads::Threads)
endif()

# --- WebRTC (libdatachannel) ---
if (BENCH_ENABLE_WEBRTC)
  set(LIBDATACHANNEL_ROOT ${CMAKE_CURRENT_LIST_DIR}/../libdatachannel)

  # Reduce build surface area.
  # These cache variables are read by libdatachannel's CMake.
  set(NO_EXAMPLES ON CACHE BOOL "" FORCE)
  set(NO_TESTS ON CACHE BOOL "" FORCE)
  set(NO_MEDIA ON CACHE BOOL "" FORCE)

  # Build libdatachannel as a subproject.
  add_subdirectory(${LIBDATACHANNEL_ROOT} ${CMAKE_BINARY_DIR}/_libdatachannel EXCLUDE_FROM_ALL)

  # WebRTC signaling + echo server.
  add_executable(webrtc_server src/webrtc_server.cpp)
  target_include_directories(webrtc_server PRIVATE
    ${LIBDATACHANNEL_ROOT}/include
    ${LIBDATACHANNEL_ROOT}/deps/json/include
  )
  target_link_libraries(webrtc_server PRIVATE bench_common datachannel Threads::Threads)

  # WebRTC client (setup + latency + throughput).
  add_executable(webrtc_client src/webrtc_client.cpp)
  target_include_directories(webrtc_client PRIVATE
    ${LIBDATACHANNEL_ROOT}/include
    ${LIBDATACHANNEL_ROOT}/deps/json/include
  )
  target_link_libraries(webrtc_client PRIVATE bench_common datachannel Threads::Threads)
endif()
