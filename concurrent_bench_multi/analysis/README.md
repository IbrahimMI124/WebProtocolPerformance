# Sweep aggregation and plotting

This folder contains scripts that flatten the multi-run sweep outputs into a
single CSV and generate summary plots.

## Input data layout (from sweep runs)

The sweep runners create:

out_sweeps/
  rest/
    rest_<timestamp>/
      payload_64/
        concurrency_1/
          <run_timestamp>/
            rest/results.json
            rest/latency.csv
      payload_256/
      ...
  websocket/
  webrtc/

Each run folder contains a per-protocol results.json and latency.csv.

## 1) Build a master flat CSV

This script walks the sweep tree and writes one row per
(protocol, payload, concurrency, run):

protocol,payload,concurrency,run,latency_avg_ms,p95_ms,throughput,startup_ms

Run it from the repo root:

```bash
python3 bench_multiple_machines/analysis/aggregate_sweeps.py \
  --root bench_multiple_machines/out_sweeps \
  --out bench_multiple_machines/analysis/aggregated.csv
```

## 2) Plot summary graphs

This script reads the aggregated CSV, averages runs per payload, and produces
plots (latency vs payload, throughput vs payload, startup bar chart).

```bash
python3 bench_multiple_machines/analysis/plot_sweeps.py \
  --in-csv bench_multiple_machines/analysis/aggregated.csv \
  --out-dir bench_multiple_machines/analysis/plots
```

Optional: plot latency CDF for a specific payload (uses latency.csv samples):

```bash
python3 bench_multiple_machines/analysis/plot_sweeps.py \
  --in-csv bench_multiple_machines/analysis/aggregated.csv \
  --out-dir bench_multiple_machines/analysis/plots \
  --root bench_multiple_machines/out_sweeps \
  --latency-cdf 1024
```

## Notes

- The plots use run averages per (protocol, payload). You can add error bars
  later using the grouped statistics CSV written by the plotting script.
- Requirements: matplotlib (already in bench_multiple_machines/requirements.txt).
