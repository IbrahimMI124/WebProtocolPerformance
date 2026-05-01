#!/usr/bin/env python3

"""Plot sweep aggregates from the flat CSV.

Generates:
- latency_avg vs payload
- latency_p95 vs payload
- throughput vs payload
- startup_ms by protocol
Optional: latency CDF for a specific payload (from latency.csv samples).
"""

from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path
from typing import Dict, List, Tuple

import matplotlib.pyplot as plt


def _resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base_dir / path


def _read_aggregated(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _group(rows: List[Dict[str, str]]) -> Dict[Tuple[str, int, int], Dict[str, float]]:
    grouped: Dict[Tuple[str, int, int], Dict[str, List[float]]] = {}
    for r in rows:
        protocol = r["protocol"]
        payload = int(float(r["payload"]))
        concurrency = int(float(r.get("concurrency", "1")))
        key = (protocol, payload, concurrency)
        bucket = grouped.setdefault(key, {})
        for metric in ("latency_avg_ms", "p95_ms", "throughput", "startup_ms"):
            bucket.setdefault(metric, []).append(float(r[metric]))

    summary: Dict[Tuple[str, int, int], Dict[str, float]] = {}
    for key, metrics in grouped.items():
        summary[key] = {}
        for metric, values in metrics.items():
            summary[key][metric] = statistics.mean(values) if values else 0.0
            summary[key][f"{metric}_stdev"] = statistics.pstdev(values) if len(values) > 1 else 0.0
    return summary


def _write_grouped_csv(path: Path, summary: Dict[Tuple[str, int, int], Dict[str, float]]) -> None:
    fieldnames = [
        "protocol",
        "payload",
        "concurrency",
        "latency_avg_ms",
        "latency_avg_ms_stdev",
        "p95_ms",
        "p95_ms_stdev",
        "throughput",
        "throughput_stdev",
        "startup_ms",
        "startup_ms_stdev",
    ]
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(fieldnames)
        for (protocol, payload, concurrency), metrics in sorted(summary.items()):
            writer.writerow(
                [
                    protocol,
                    payload,
                    concurrency,
                    metrics.get("latency_avg_ms", 0.0),
                    metrics.get("latency_avg_ms_stdev", 0.0),
                    metrics.get("p95_ms", 0.0),
                    metrics.get("p95_ms_stdev", 0.0),
                    metrics.get("throughput", 0.0),
                    metrics.get("throughput_stdev", 0.0),
                    metrics.get("startup_ms", 0.0),
                    metrics.get("startup_ms_stdev", 0.0),
                ]
            )


def _plot_lines(summary: Dict[Tuple[str, int, int], Dict[str, float]], metric: str, out: Path, title: str, ylabel: str) -> None:
    series = sorted({(k[0], k[2]) for k in summary.keys()})
    for protocol, concurrency in series:
        points = sorted(
            [
                (payload, summary[(protocol, payload, concurrency)][metric])
                for (p, payload, c) in summary.keys()
                if p == protocol and c == concurrency
            ]
        )
        xs = [p for p, _ in points]
        ys = [v for _, v in points]
        plt.plot(xs, ys, marker="o", label=f"{protocol}-c{concurrency}")

    plt.xlabel("Payload (bytes)")
    plt.ylabel(ylabel)
    plt.title(title)
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(out)
    plt.close()


def _plot_startup_bar(summary: Dict[Tuple[str, int, int], Dict[str, float]], out: Path) -> None:
    labels = []
    values = []
    for protocol, concurrency in sorted({(k[0], k[2]) for k in summary.keys()}):
        vals = [v["startup_ms"] for k, v in summary.items() if k[0] == protocol and k[2] == concurrency]
        labels.append(f"{protocol}-c{concurrency}")
        values.append(statistics.mean(vals) if vals else 0.0)

    plt.bar(labels, values)
    plt.ylabel("Startup (ms)")
    plt.title("Startup time by protocol and concurrency (mean across payloads)")
    plt.grid(True, axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(out)
    plt.close()


def _read_latency_csv(path: Path) -> List[float]:
    samples: List[float] = []
    if not path.exists():
        return samples
    with path.open(newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            try:
                samples.append(float(row[0]))
            except ValueError:
                continue
    return samples


def _plot_latency_cdf(root: Path, payload: int, out: Path, protocols: List[str]) -> None:
    series_samples: Dict[Tuple[str, int], List[float]] = {}
    for protocol in protocols:
        protocol_dir = root / protocol
        if not protocol_dir.exists():
            continue
        all_samples: List[float] = []
        for sweep_dir in sorted([p for p in protocol_dir.iterdir() if p.is_dir()]):
            payload_dir = sweep_dir / f"payload_{payload}"
            if not payload_dir.exists():
                continue
            concurrency_dirs = sorted(payload_dir.glob("concurrency_*"))
            if not concurrency_dirs:
                concurrency_dirs = [payload_dir]

            for concurrency_dir in concurrency_dirs:
                concurrency = 1
                if concurrency_dir != payload_dir:
                    parts = concurrency_dir.name.split("_", 1)
                    if len(parts) != 2:
                        continue
                    try:
                        concurrency = int(parts[1])
                    except ValueError:
                        continue

                for run_dir in sorted([p for p in concurrency_dir.iterdir() if p.is_dir()]):
                    latency_path = run_dir / protocol / "latency.csv"
                    series_samples.setdefault((protocol, concurrency), []).extend(
                        _read_latency_csv(latency_path)
                    )

    for (protocol, concurrency), samples in sorted(series_samples.items()):
        if not samples:
            continue
        samples.sort()
        n = len(samples)
        ys = [(i + 1) / n for i in range(n)]
        plt.plot(samples, ys, label=f"{protocol}-c{concurrency}")

    plt.xlabel("Latency (ms)")
    plt.ylabel("CDF")
    plt.title(f"Latency CDF (payload {payload} bytes)")
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(out)
    plt.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-csv", required=True, help="Aggregated CSV path")
    ap.add_argument("--out-dir", default="analysis/plots", help="Output directory for plots")
    ap.add_argument("--root", default="out_sweeps", help="Sweep root (for CDF)")
    ap.add_argument("--latency-cdf", type=int, default=0, help="Payload size for latency CDF (0=skip)")
    ap.add_argument("--cdf-protocols", default="rest,websocket,webrtc", help="CSV list for CDF")
    args = ap.parse_args()

    base_dir = Path(__file__).resolve().parents[1]
    in_csv = _resolve_path(base_dir, args.in_csv)
    out_dir = _resolve_path(base_dir, args.out_dir)
    root = _resolve_path(base_dir, args.root)

    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _read_aggregated(in_csv)
    summary = _group(rows)

    grouped_csv = out_dir / "grouped_means.csv"
    _write_grouped_csv(grouped_csv, summary)

    _plot_lines(summary, "latency_avg_ms", out_dir / "latency_avg_vs_payload.png", "Latency (avg) vs payload", "Latency (ms)")
    _plot_lines(summary, "p95_ms", out_dir / "latency_p95_vs_payload.png", "Latency (p95) vs payload", "Latency p95 (ms)")
    _plot_lines(summary, "throughput", out_dir / "throughput_vs_payload.png", "Throughput vs payload", "Bytes per second")
    _plot_startup_bar(summary, out_dir / "startup_ms_by_protocol.png")

    if args.latency_cdf:
        protocols = [p.strip() for p in args.cdf_protocols.split(",") if p.strip()]
        _plot_latency_cdf(root, args.latency_cdf, out_dir / f"latency_cdf_payload_{args.latency_cdf}.png", protocols)

    print(f"Wrote plots to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
