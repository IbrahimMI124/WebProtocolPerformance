#!/usr/bin/env python3

"""Plot graphs from `bench_multiple_machines/run_bench.py` outputs.

This script reads:
- `results.csv` (one row per framework + concurrency)
- `*_latency_c<concurrency>_i*.csv` (raw RTT samples per client)

And produces 3 comparison plots:
1) `startup_throughput.*`: bar charts for end-to-end startup and throughput
2) `latency_cdf.*`: CDF curves of raw latency samples (RTT)
3) `latency_boxplot.*`: boxplot of latency distributions

All plots are written under `--out-dir`.
"""

import argparse
import csv
import math
from pathlib import Path
from typing import Dict, List, Tuple


def read_results_csv(path: Path) -> List[dict]:
    # Read the flattened summary table produced by `run_bench.py`.
    rows: List[dict] = []
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows


def read_latency_csv(path: Path) -> List[float]:
    # Read raw latency samples (milliseconds) from a 1-column CSV.
    # These are written by each client when `--out-latency-csv` is set.
    values: List[float] = []
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or "latency_ms" not in reader.fieldnames:
            raise ValueError(f"{path} missing latency_ms column")
        for r in reader:
            v = r.get("latency_ms", "")
            if not v:
                continue
            values.append(float(v))
    return values


def read_latency_glob(paths: List[Path]) -> List[float]:
    samples: List[float] = []
    for p in paths:
        if not p.exists():
            continue
        samples.extend(read_latency_csv(p))
    return samples


def cdf(points: List[float]) -> Tuple[List[float], List[float]]:
    # Convert raw samples into a CDF curve.
    #
    # If xs is sorted samples, then y_i = (i+1)/n gives an empirical CDF.
    if not points:
        return [], []
    xs = sorted(points)
    n = len(xs)
    ys = [(i + 1) / n for i in range(n)]
    return xs, ys


def nice_framework_order(names: List[str]) -> List[str]:
    # Force a consistent visual order in plots.
    # If a framework is missing (e.g. you didn't build it), we skip it.
    pref = ["rest", "websocket", "webrtc"]
    rest = [n for n in names if n not in pref]
    return [n for n in pref if n in names] + sorted(rest)


def main() -> int:
    # CLI options.
    ap = argparse.ArgumentParser(description="Plot benchmark CSV outputs")
    ap.add_argument("--in-dir", default="bench_multiple_machines/out", help="Directory containing results.csv and *_latency.csv")
    ap.add_argument("--out-dir", default="bench_multiple_machines/out/plots", help="Directory to write plots")
    ap.add_argument("--format", default="png", choices=["png", "svg"], help="Output image format")
    ap.add_argument("--show", action="store_true", help="Show plots interactively")
    ap.add_argument("--concurrency", type=int, default=1, help="Concurrency level to plot")
    args = ap.parse_args()

    in_dir = Path(args.in_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        import matplotlib
        # Non-interactive mode (Agg) is best for CI and headless runs.
        # If `--show` is set, we let matplotlib pick a GUI backend.
        matplotlib.use("Agg" if not args.show else None)  # type: ignore
        import matplotlib.pyplot as plt
    except Exception as e:
        print("ERROR: matplotlib is required to plot graphs.")
        print("Install with: python3 -m pip install -r bench_multiple_machines/requirements.txt")
        print(f"Import error: {e}")
        return 2

    results_csv = in_dir / "results.csv"
    if not results_csv.exists():
        print(f"ERROR: missing {results_csv}")
        return 2

    rows = read_results_csv(results_csv)
    if not rows:
        print(f"ERROR: no rows in {results_csv}")
        return 2

    # Parse numeric columns.
    # `csv.DictReader` gives strings, so we convert to float.
    parsed = []
    for r in rows:
        try:
            conc = int(r.get("concurrency", "1") or 1)
            if conc != args.concurrency:
                continue
            parsed.append(
                {
                    "framework": r["framework"],
                    "server_ready_ms": float(r["server_ready_ms"]),
                    "end_to_end_startup_ms": float(r["end_to_end_startup_ms"]),
                    "latency_avg_ms": float(r["latency_avg_ms"]),
                    "latency_p95_ms": float(r["latency_p95_ms"]),
                    "throughput_bytes_per_sec": float(r["throughput_bytes_per_sec"]),
                }
            )
        except Exception as e:
            raise ValueError(f"Failed parsing row: {r}") from e

    frameworks = nice_framework_order([p["framework"] for p in parsed])
    by_fw: Dict[str, dict] = {p["framework"]: p for p in parsed}

    # --- Plot 1: Startup and throughput bars ---
    # Uses only the flattened `results.csv` summary.
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), dpi=140)

    ax = axes[0]
    x = list(range(len(frameworks)))
    startup = [by_fw[f]["end_to_end_startup_ms"] for f in frameworks]
    ax.bar(x, startup)
    ax.set_xticks(x, frameworks, rotation=0)
    ax.set_title("End-to-end startup time")
    ax.set_ylabel("ms")
    ax.grid(axis="y", alpha=0.3)

    ax = axes[1]
    thr = [by_fw[f]["throughput_bytes_per_sec"] for f in frameworks]
    ax.bar(x, thr)
    ax.set_xticks(x, frameworks, rotation=0)
    ax.set_title("Throughput")
    ax.set_ylabel("bytes/sec")
    ax.grid(axis="y", alpha=0.3)

    fig.tight_layout()
    p1 = out_dir / f"startup_throughput.{args.format}"
    fig.savefig(p1)

    # --- Plot 2: Latency CDF from samples ---
    # Reads the raw per-message RTT samples written by each client.
    latency_globs = {
        "rest": f"rest_latency_c{args.concurrency}_i*.csv",
        "websocket": f"ws_latency_c{args.concurrency}_i*.csv",
        "webrtc": f"webrtc_latency_c{args.concurrency}_i*.csv",
    }

    fig2, ax2 = plt.subplots(figsize=(7.5, 5.0), dpi=140)
    any_latency = False
    for fw in frameworks:
        pat = latency_globs.get(fw)
        if not pat:
            continue
        files = list(in_dir.glob(pat))
        if not files:
            continue
        samples = read_latency_glob(files)
        xs, ys = cdf(samples)
        if not xs:
            continue
        any_latency = True
        ax2.plot(xs, ys, label=f"{fw} (n={len(samples)})")

    ax2.set_title("Latency CDF (RTT)")
    ax2.set_xlabel("latency (ms)")
    ax2.set_ylabel("CDF")
    ax2.set_ylim(0, 1.0)
    ax2.grid(alpha=0.3)
    if any_latency:
        ax2.legend()
    fig2.tight_layout()
    p2 = out_dir / f"latency_cdf.{args.format}"
    fig2.savefig(p2)

    # --- Plot 3: Latency boxplot from samples ---
    # A boxplot makes it easy to compare spread/outliers across frameworks.
    fig3, ax3 = plt.subplots(figsize=(7.5, 5.0), dpi=140)
    box_data = []
    box_labels = []
    for fw in frameworks:
        pat = latency_globs.get(fw)
        if not pat:
            continue
        files = list(in_dir.glob(pat))
        if not files:
            continue
        samples = read_latency_glob(files)
        if not samples:
            continue
        box_data.append(samples)
        box_labels.append(fw)

    if box_data:
        ax3.boxplot(box_data, tick_labels=box_labels, showfliers=False)
    ax3.set_title("Latency distribution (RTT)")
    ax3.set_ylabel("latency (ms)")
    ax3.grid(axis="y", alpha=0.3)
    fig3.tight_layout()
    p3 = out_dir / f"latency_boxplot.{args.format}"
    fig3.savefig(p3)

    print(f"Wrote:\n- {p1}\n- {p2}\n- {p3}")

    if args.show:
        plt.show()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
