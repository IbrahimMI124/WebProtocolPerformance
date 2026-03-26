#!/usr/bin/env python3

import argparse
import csv
import math
from pathlib import Path
from typing import Dict, List, Tuple


def read_results_csv(path: Path) -> List[dict]:
    rows: List[dict] = []
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows


def read_latency_csv(path: Path) -> List[float]:
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


def cdf(points: List[float]) -> Tuple[List[float], List[float]]:
    if not points:
        return [], []
    xs = sorted(points)
    n = len(xs)
    ys = [(i + 1) / n for i in range(n)]
    return xs, ys


def nice_framework_order(names: List[str]) -> List[str]:
    pref = ["rest", "websocket", "webrtc"]
    rest = [n for n in names if n not in pref]
    return [n for n in pref if n in names] + sorted(rest)


def main() -> int:
    ap = argparse.ArgumentParser(description="Plot benchmark CSV outputs")
    ap.add_argument("--in-dir", default="bench/out", help="Directory containing results.csv and *_latency.csv")
    ap.add_argument("--out-dir", default="bench/out/plots", help="Directory to write plots")
    ap.add_argument("--format", default="png", choices=["png", "svg"], help="Output image format")
    ap.add_argument("--show", action="store_true", help="Show plots interactively")
    args = ap.parse_args()

    in_dir = Path(args.in_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        import matplotlib
        matplotlib.use("Agg" if not args.show else None)  # type: ignore
        import matplotlib.pyplot as plt
    except Exception as e:
        print("ERROR: matplotlib is required to plot graphs.")
        print("Install with: python3 -m pip install -r bench/requirements.txt")
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
    parsed = []
    for r in rows:
        try:
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
    latency_files = {
        "rest": in_dir / "rest_latency.csv",
        "websocket": in_dir / "ws_latency.csv",
        "webrtc": in_dir / "webrtc_latency.csv",
    }

    fig2, ax2 = plt.subplots(figsize=(7.5, 5.0), dpi=140)
    any_latency = False
    for fw in frameworks:
        lf = latency_files.get(fw)
        if not lf or not lf.exists():
            continue
        samples = read_latency_csv(lf)
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
    fig3, ax3 = plt.subplots(figsize=(7.5, 5.0), dpi=140)
    box_data = []
    box_labels = []
    for fw in frameworks:
        lf = latency_files.get(fw)
        if not lf or not lf.exists():
            continue
        samples = read_latency_csv(lf)
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
