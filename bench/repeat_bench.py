#!/usr/bin/env python3

"""Run `run_bench.py` multiple times and summarize variance across runs.

Why this exists
---------------
`bench/run_bench.py` runs the suite once and writes:
- `results.json` (nested, rich)
- `results.csv`  (flat, one row per framework)

For reporting, you typically want *repeatability*:
- Run the benchmark multiple times
- Compute mean / variance / standard deviation for each metric

What this script does
---------------------
1) Runs `bench/run_bench.py` N times into per-run output directories:
     <out-root>/run_000/
     <out-root>/run_001/
     ...
2) Reads each run's `results.csv` and aggregates per-framework metrics.
3) Optionally extracts WebRTC `setup_ms` from `results.json` (if present).
4) Writes:
   - `<out-root>/all_runs_flat.csv`   (one row per framework per run)
   - `<out-root>/summary_stats.csv`   (mean/stdev/variance/min/max/CV)
   - `<out-root>/summary_stats.json`  (same data as JSON)

Notes
-----
- This is still a *single-machine* benchmark unless you run servers/clients on
  different devices. It does, however, quantify run-to-run noise.
- Ports: to avoid TIME_WAIT / reuse issues, each run offsets `--base-port`.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class Stats:
    n: int
    mean: float
    variance: float
    stdev: float
    min_v: float
    max_v: float
    cv_percent: float


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _variance(values: List[float]) -> float:
    """Population variance (divide by N).

    For benchmarking we usually care about dispersion, and N is typically not huge;
    you can switch to sample variance if you prefer.
    """

    if not values:
        return 0.0
    m = _mean(values)
    return sum((x - m) ** 2 for x in values) / len(values)


def _stats(values: List[float]) -> Stats:
    if not values:
        return Stats(n=0, mean=0.0, variance=0.0, stdev=0.0, min_v=0.0, max_v=0.0, cv_percent=0.0)
    m = _mean(values)
    var = _variance(values)
    sd = math.sqrt(var)
    cv = (sd / m * 100.0) if m != 0.0 else 0.0
    return Stats(
        n=len(values),
        mean=m,
        variance=var,
        stdev=sd,
        min_v=min(values),
        max_v=max(values),
        cv_percent=cv,
    )


def _read_results_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _try_read_webrtc_setup_ms(results_json: Path) -> Optional[float]:
    """Extract webrtc setup_ms from results.json if present."""

    try:
        obj = json.loads(results_json.read_text())
    except Exception:
        return None

    for run in obj.get("runs", []):
        if run.get("framework") != "webrtc":
            continue
        setup = run.get("setup")
        if not isinstance(setup, dict):
            continue
        setup_result = setup.get("result")
        if not isinstance(setup_result, dict):
            continue
        v = setup_result.get("setup_ms")
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _write_csv(path: Path, header: List[str], rows: Iterable[List[Any]]) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def main() -> int:
    ap = argparse.ArgumentParser(description="Repeat run_bench.py and compute variance across runs")
    ap.add_argument("--bin-dir", required=True, help="CMake build dir containing binaries")
    ap.add_argument("--out-root", default="bench/out_repeated", help="Root directory for repeated run outputs")
    ap.add_argument("--runs", type=int, default=10, help="Number of runs")

    # Pass-through knobs to run_bench.py
    ap.add_argument("--requests", type=int, default=200)
    ap.add_argument("--payload-bytes", type=int, default=64)
    ap.add_argument("--duration-sec", type=float, default=5.0)
    ap.add_argument("--base-port", type=int, default=18080)

    # Run hygiene
    ap.add_argument("--port-step", type=int, default=50, help="Base-port increment per run")
    ap.add_argument("--sleep-sec", type=float, default=0.2, help="Sleep between runs (helps port reuse)")
    ap.add_argument("--keep-going", action="store_true", help="Continue even if a run fails")
    ap.add_argument("--timeout-sec", type=float, default=300.0, help="Timeout for one run_bench.py invocation")

    args = ap.parse_args()

    if args.runs <= 0:
        raise SystemExit("--runs must be > 0")

    repo_root = Path(__file__).resolve().parent.parent
    run_bench = repo_root / "bench" / "run_bench.py"
    if not run_bench.exists():
        print(f"ERROR: missing {run_bench}")
        return 2

    bin_dir = Path(args.bin_dir)
    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    # --- Step 1: run N times ---
    failures: List[Tuple[int, int]] = []  # (run_index, returncode)

    for i in range(args.runs):
        run_out = out_root / f"run_{i:03d}"
        run_out.mkdir(parents=True, exist_ok=True)

        base_port_i = args.base_port + i * args.port_step

        cmd = [
            sys.executable,
            str(run_bench),
            "--bin-dir",
            str(bin_dir),
            "--out-dir",
            str(run_out),
            "--base-port",
            str(base_port_i),
            "--requests",
            str(args.requests),
            "--payload-bytes",
            str(args.payload_bytes),
            "--duration-sec",
            str(args.duration_sec),
        ]

        print(f"[{i+1}/{args.runs}] running: {' '.join(cmd)}")
        t0 = time.time()
        p = subprocess.run(cmd, text=True)
        dt = time.time() - t0
        print(f"[{i+1}/{args.runs}] exit={p.returncode} elapsed={dt:.2f}s out={run_out}")

        if p.returncode != 0:
            failures.append((i, p.returncode))
            if not args.keep_going:
                print("Stopping on first failure (use --keep-going to continue).")
                break

        if args.sleep_sec > 0:
            time.sleep(args.sleep_sec)

    # --- Step 2: aggregate outputs ---
    flat_rows: List[Dict[str, Any]] = []
    series: Dict[str, Dict[str, List[float]]] = {}  # framework -> metric -> [values]

    def add_value(framework: str, metric: str, value: float) -> None:
        series.setdefault(framework, {}).setdefault(metric, []).append(value)

    run_dirs = sorted([p for p in out_root.iterdir() if p.is_dir() and p.name.startswith("run_")])
    for run_dir in run_dirs:
        results_csv = run_dir / "results.csv"
        results_json = run_dir / "results.json"
        if not results_csv.exists():
            continue

        rows = _read_results_csv(results_csv)
        for r in rows:
            fw = r.get("framework", "")
            if not fw:
                continue

            def f(col: str) -> float:
                try:
                    return float(r.get(col, "0") or 0)
                except Exception:
                    return 0.0

            record = {
                "run": run_dir.name,
                "framework": fw,
                "server_ready_ms": f("server_ready_ms"),
                "end_to_end_startup_ms": f("end_to_end_startup_ms"),
                "latency_avg_ms": f("latency_avg_ms"),
                "latency_p95_ms": f("latency_p95_ms"),
                "throughput_bytes_per_sec": f("throughput_bytes_per_sec"),
            }
            flat_rows.append(record)

            for k, v in record.items():
                if k in ("run", "framework"):
                    continue
                add_value(fw, k, float(v))

        # Add WebRTC setup_ms if present (this is not in results.csv)
        setup_ms = _try_read_webrtc_setup_ms(results_json) if results_json.exists() else None
        if setup_ms is not None:
            add_value("webrtc", "setup_ms", float(setup_ms))

    # Write per-run flat table
    flat_path = out_root / "all_runs_flat.csv"
    _write_csv(
        flat_path,
        [
            "run",
            "framework",
            "server_ready_ms",
            "end_to_end_startup_ms",
            "latency_avg_ms",
            "latency_p95_ms",
            "throughput_bytes_per_sec",
        ],
        (
            [
                r["run"],
                r["framework"],
                f"{r['server_ready_ms']:.6f}",
                f"{r['end_to_end_startup_ms']:.6f}",
                f"{r['latency_avg_ms']:.6f}",
                f"{r['latency_p95_ms']:.6f}",
                f"{r['throughput_bytes_per_sec']:.6f}",
            ]
            for r in flat_rows
        ),
    )

    # Write summary stats
    summary_rows: List[List[Any]] = []
    summary_obj: Dict[str, Dict[str, Any]] = {}

    for fw in sorted(series.keys()):
        summary_obj[fw] = {}
        for metric in sorted(series[fw].keys()):
            st = _stats(series[fw][metric])
            summary_rows.append(
                [
                    fw,
                    metric,
                    st.n,
                    st.mean,
                    st.stdev,
                    st.variance,
                    st.min_v,
                    st.max_v,
                    st.cv_percent,
                ]
            )
            summary_obj[fw][metric] = {
                "n": st.n,
                "mean": st.mean,
                "stdev": st.stdev,
                "variance": st.variance,
                "min": st.min_v,
                "max": st.max_v,
                "cv_percent": st.cv_percent,
            }

    summary_csv = out_root / "summary_stats.csv"
    _write_csv(
        summary_csv,
        ["framework", "metric", "n", "mean", "stdev", "variance", "min", "max", "cv_percent"],
        (
            [
                r[0],
                r[1],
                r[2],
                f"{float(r[3]):.6f}",
                f"{float(r[4]):.6f}",
                f"{float(r[5]):.6f}",
                f"{float(r[6]):.6f}",
                f"{float(r[7]):.6f}",
                f"{float(r[8]):.3f}",
            ]
            for r in summary_rows
        ),
    )

    summary_json = out_root / "summary_stats.json"
    summary_json.write_text(json.dumps(summary_obj, indent=2))

    print("\nWrote:")
    print(f"- {flat_path}")
    print(f"- {summary_csv}")
    print(f"- {summary_json}")
    if failures:
        print("\nFailures:")
        for idx, rc in failures:
            print(f"- run_{idx:03d}: exit {rc}")

    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
