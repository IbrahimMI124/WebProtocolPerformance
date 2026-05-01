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
cmake -S bench_multiple_machines -B bench_multiple_machines/build -DCMAKE_BUILD_TYPE=Release
cmake --build bench_multiple_machines/build -j
```

## Run (two machines)

You can split the benchmark across two machines:

- **Server machine** runs the protocol servers.
- **Client machine** runs the benchmark clients and writes results locally.

### Server machine

Pin the server to specific CPU cores using `taskset` for more stable results:

```bash
taskset -c 2-3 python3 bench_multiple_machines/run_server.py \
	--bin-dir bench_multiple_machines/build \
	--host 0.0.0.0 \
	--base-port 18080
```

This starts:

- REST server on `base-port`
- WebSocket server on `base-port + 1`
- WebRTC signaling servers on `base-port + 2`, `+12`, `+22`

### Client machine

Pin the client to specific CPU cores using `taskset` for more stable results:

```bash
taskset -c 4-5 python3 bench_multiple_machines/run_client.py \
	--bin-dir bench_multiple_machines/build \
	--server-host <SERVER_IP> \
	--base-port 18080 \
	--out-dir bench_multiple_machines/out
```

Useful knobs:

```bash
python3 bench_multiple_machines/run_client.py --help
```

Protocol selection (server and client):

```bash
taskset -c 2-3 python3 bench_multiple_machines/run_server.py --bin-dir bench_multiple_machines/build --protocol rest
taskset -c 4-5 python3 bench_multiple_machines/run_client.py --bin-dir bench_multiple_machines/build --server-host <SERVER_IP> --protocol rest
```

## Sweep runs (payload configs)

There are per-protocol sweep runners that run the client multiple times across
payload sizes and store outputs neatly:

- `run_client_rest_sweep.py`
- `run_client_websocket_sweep.py`
- `run_client_webrtc_sweep.py`

Each sweep script iterates through `--payloads` (comma-separated), sweeps
`--concurrency-values`, and runs `--runs-per-payload` repeats. Example:

```bash
taskset -c 1-2 python3 concurrent_bench_multi/run_client_rest_sweep.py \
	--bin-dir concurrent_bench_multi/build \
	--server-host <SERVER_IP> \
	--payloads 64,256,1024,4096 \
	--runs-per-payload 10 \
	--out-root concurrent_bench_multi/out_sweeps/rest
```

```bash
taskset -c 1-2 python3 concurrent_bench_multi/run_client_websocket_sweep.py \
	--bin-dir concurrent_bench_multi/build \
	--server-host 172.20.10.2 \
	--payloads 64,256,1024,4096 \
	--runs-per-payload 10 \
	--out-root concurrent_bench_multi/out_sweeps/websocket
```

```bash
taskset -c 1-2 python3 concurrent_bench_multi/run_client_webrtc_sweep.py \
	--bin-dir concurrent_bench_multi/build \
	--server-host 172.20.10.2 \
	--payloads 64,256,1024,4096 \
	--runs-per-payload 10 \
	--out-root concurrent_bench_multi/out_sweeps/webrtc
```

Defaults (if you do not pass flags):

- `--payloads 64,256,1024`
- `--concurrency-values 1,2,4,8,16`
- `--runs-per-payload 3`
- `--requests 200`
- `--duration-sec 5.0`

## Output layout

- Single client run (`run_client.py`) creates a timestamped folder under `--out-dir`:
	- `2026-05-01_12-30-45/`
	- `config.json`
	- `results.csv`
	- `results.json`
	- `rest/latency.csv`, `rest/results.json`
	- `websocket/latency.csv`, `websocket/results.json`
	- `webrtc/latency.csv`, `webrtc/results.json`

- Sweep run (`run_client_*_sweep.py`) creates a timestamped sweep folder under `--out-root`:
	- `rest_2026-05-01_12-30-45/`
	- `sweep_config.json`
	- `payload_64/`
		- `concurrency_1/`
			- `worker_0/` (each worker writes its own timestamped run folder)
			- `worker_1/`
			- `run_1_combined.csv` (merged results for the run across workers)
		- `concurrency_2/`
		- `concurrency_4/`
	- `payload_256/`
	- `payload_1024/`
