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

## Run

Run the full benchmark suite (produces `bench_multiple_machines/out/results.json` and `bench_multiple_machines/out/results.csv`):

```bash
python3 bench_multiple_machines/run_bench.py --bin-dir bench_multiple_machines/build --out-dir bench_multiple_machines/out
```

Note: if you copy/paste from VS Code and it turns into something like `python3 [run_bench.py](http://...)`, bash will error on the `(`. Use the plain path above, or:

```bash
./bench_multiple_machines/run_bench.py --bin-dir bench_multiple_machines/build --out-dir bench_multiple_machines/out
```

Useful knobs:

```bash
python3 bench_multiple_machines/run_bench.py --help

### Concurrency

You can run multiple clients per protocol with:

```bash
python3 bench_multiple_machines/run_bench.py \
	--bin-dir bench_multiple_machines/build \
	--out-dir bench_multiple_machines/out \
	--concurrency-list 1,2,4,8,16,32
```

The output CSV now includes a `concurrency` column, and results.json stores per-concurrency runs.

### Multi-machine usage

**Server machine** (run servers only):

```bash
python3 bench_multiple_machines/run_bench.py \
	--bin-dir bench_multiple_machines/build \
	--role server \
	--bind-host 0.0.0.0 \
	--base-port 18080
```

**Client machine** (run clients only):

```bash
python3 bench_multiple_machines/run_bench.py \
	--bin-dir bench_multiple_machines/build \
	--out-dir bench_multiple_machines/out \
	--role client \
	--server-host <SERVER_IP> \
	--base-port 18080 \
	--concurrency-list 1,2,4,8,16,32
```
```

## Plot

Install plotting dependency:

```bash
python3 -m pip install -r bench_multiple_machines/requirements.txt
```

Generate plots from CSV outputs (writes images under `bench/out/plots/` by default):

```bash
python3 bench_multiple_machines/plot_results.py --in-dir bench_multiple_machines/out --out-dir bench_multiple_machines/out/plots
```

## Repeat runs + variance

Single runs are noisy. To quantify run-to-run variance (same machine, same settings),
use `bench_multiple_machines/repeat_bench.py`.

This will run the full suite multiple times (each run writes its own `results.json` and
`results.csv`), then it aggregates everything and writes:

- `all_runs_flat.csv`: one row per framework *per run*
- `summary_stats.csv`: mean/stdev/variance/min/max (plus coefficient of variation)
- `summary_stats.json`: same summary data as JSON

Example (2 quick runs):

```bash
python3 bench_multiple_machines/repeat_bench.py \
	--bin-dir bench_multiple_machines/build \
	--out-root bench_multiple_machines/out_repeated \
	--runs 2 \
	--requests 50 \
	--payload-bytes 64 \
	--duration-sec 2 \
	--concurrency-list 1,2,4,8,16,32
```

The per-run outputs are stored under:

- `bench_multiple_machines/out_repeated/run_000/`
- `bench_multiple_machines/out_repeated/run_001/`
- ...
