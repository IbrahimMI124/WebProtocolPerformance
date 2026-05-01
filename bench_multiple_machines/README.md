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
git submodule update --init --recursive
```

Build:

```bash
cmake -S bench -B bench/build -DCMAKE_BUILD_TYPE=Release
cmake --build bench/build -j
```

## Run (two machines)

You can split the benchmark across two machines:

- **Server machine** runs the protocol servers.
- **Client machine** runs the benchmark clients and writes results locally.

### Server machine

```bash
python3 bench_multiple_machines/run_server.py \
	--bin-dir bench_multiple_machines/build \
	--host 0.0.0.0 \
	--base-port 18080
```

This starts:

- REST server on `base-port`
- WebSocket server on `base-port + 1`
- WebRTC signaling servers on `base-port + 2`, `+12`, `+22`

### Client machine

```bash
python3 bench_multiple_machines/run_client.py \
	--bin-dir bench_multiple_machines/build \
	--server-host <SERVER_IP> \
	--base-port 18080 \
	--out-dir bench_multiple_machines/out
```

Useful knobs:

```bash
python3 bench_multiple_machines/run_client.py --help
```

## Run (single machine)

If you want the original single-machine orchestration, use `run_bench.py`:

```bash
python3 bench_multiple_machines/run_bench.py --bin-dir bench_multiple_machines/build --out-dir bench_multiple_machines/out
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

## Repeat runs + variance

Single runs are noisy. To quantify run-to-run variance (same machine, same settings),
use `bench/repeat_bench.py`.

This will run the full suite multiple times (each run writes its own `results.json` and
`results.csv`), then it aggregates everything and writes:

- `all_runs_flat.csv`: one row per framework *per run*
- `summary_stats.csv`: mean/stdev/variance/min/max (plus coefficient of variation)
- `summary_stats.json`: same summary data as JSON

Example (2 quick runs):

```bash
python3 bench/repeat_bench.py \
	--bin-dir bench/build \
	--out-root bench/out_repeated \
	--runs 2 \
	--requests 50 \
	--payload-bytes 64 \
	--duration-sec 2
```

The per-run outputs are stored under:

- `bench/out_repeated/run_000/`
- `bench/out_repeated/run_001/`
- ...
