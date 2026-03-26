# Minimal protocol benchmark (REST vs WebSocket vs WebRTC)

This folder contains a small benchmark harness matching your `test.cpp` intent:

- **REST (HTTP)** via `cpp-httplib`
- **WebSocket** via `uWebSockets` (server) + a tiny RFC6455 client (no external deps)
- **WebRTC DataChannel** via `libdatachannel`

It measures:

- **Startup time**: time from spawning the server process until the client handshake is ready.
- **Latency (RTT)**: ping-pong round-trip time for `N` messages (writes a CSV of samples).
- **Throughput**: bytes/sec for ping-pong echo over a fixed duration.

## Build (Linux)

First time only (dependency submodules):

```bash
cd "/home/mohammed-ibrahim/Downloads/Sem 6/SSP/Project/Code"
git submodule update --init --recursive
```

Build:

```bash
cd "/home/mohammed-ibrahim/Downloads/Sem 6/SSP/Project/Code"
cmake -S bench -B bench/build -DCMAKE_BUILD_TYPE=Release
cmake --build bench/build -j
```

## Run

Run the full benchmark suite (produces `bench/out/results.json` and `bench/out/results.csv`):

```bash
python3 bench/run_bench.py --bin-dir bench/build --out-dir bench/out
```

Note: if you copy/paste from VS Code and it turns into something like `python3 [run_bench.py](http://...)`, bash will error on the `(`. Use the plain path above, or:

```bash
./bench/run_bench.py --bin-dir bench/build --out-dir bench/out
```

Useful knobs:

```bash
python3 bench/run_bench.py --help
```

## Plot

Install plotting dependency:

```bash
python3 -m pip install -r bench/requirements.txt
```

Generate plots from CSV outputs (writes images under `bench/out/plots/` by default):

```bash
python3 bench/plot_results.py --in-dir bench/out --out-dir bench/out/plots
```
