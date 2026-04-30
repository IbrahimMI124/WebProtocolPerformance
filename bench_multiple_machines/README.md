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

Build (from repo root):

```bash
cmake -S bench_multiple_machines -B bench_multiple_machines/build -DCMAKE_BUILD_TYPE=Release
cmake --build bench_multiple_machines/build -j
```

You can also use the helper script in this folder (builds both benches):

```bash
./bench_multiple_machines/build_all.sh --release
```

If you get "permission denied", make the script executable:

```bash
chmod +x bench_multiple_machines/build_all.sh
```

If CMake reports a cache mismatch (source/build dirs don't match), remove the
build folder and reconfigure:

```bash
rm -rf bench_multiple_machines/build
cmake -S bench_multiple_machines -B bench_multiple_machines/build -DCMAKE_BUILD_TYPE=Release
cmake --build bench_multiple_machines/build -j
```

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
```

### What run_bench.py does

`run_bench.py` orchestrates one benchmark pass and writes a single results set:

1) Starts protocol servers and waits for their `READY` line
2) Launches clients in two modes
 - `latency` (fixed number of round-trips)
 - `throughput` (fixed time window)
3) Parses the final JSON line printed by each client
4) Writes outputs under `--out-dir`

Outputs:
- `results.json`: nested results and parameters
- `results.csv`: one row per framework + concurrency
- `*_latency_c<concurrency>_i*.csv`: raw latency samples per client

The `--concurrency-list` flag runs multiple clients in parallel for each protocol.

### Concurrency

You can run multiple clients per protocol with:

```bash
python3 bench_multiple_machines/run_bench.py \
 --bin-dir bench_multiple_machines/build \
 --out-dir bench_multiple_machines/out \
 --concurrency-list 1,2,4,8,16,32
```

The output CSV includes a `concurrency` column, and `results.json` stores per-concurrency runs.

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

### What repeat_bench.py does

`repeat_bench.py` runs `run_bench.py` N times, then aggregates all runs:

1) Executes `run_bench.py` for each run, writing `run_XXX/results.json` and `run_XXX/results.csv`
2) Flattens per-run rows into `all_runs_flat.csv`
3) Computes mean/stdev/variance/min/max/CV per framework + concurrency
4) Writes summary stats to `summary_stats.csv` and `summary_stats.json`

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

## Helper scripts (this folder)

These scripts live under `bench_multiple_machines/`:

- `build_all.sh`: builds `bench` and `bench_multiple_machines`
- `run_all_tests.sh`: runs local single-machine tests plus multi-machine runs

Examples:

```bash
./bench_multiple_machines/run_all_tests.sh
```

Server-only (machine A):

```bash
./bench_multiple_machines/run_all_tests.sh --role server --bind-host 0.0.0.0
```

Client-only (machine B):

```bash
./bench_multiple_machines/run_all_tests.sh --role client --server-host <SERVER_IP>
```
