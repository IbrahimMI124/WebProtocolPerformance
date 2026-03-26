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
