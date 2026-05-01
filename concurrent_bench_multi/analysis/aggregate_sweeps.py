#!/usr/bin/env python3

"""Aggregate sweep outputs into a flat CSV.

Outputs one row per (protocol, payload, concurrency, run).
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def _resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base_dir / path


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text())


def _find_run_result(run_dir: Path, protocol: str) -> Optional[Dict[str, Any]]:
    per_proto = run_dir / protocol / "results.json"
    if per_proto.exists():
        return _read_json(per_proto)

    combined = run_dir / "results.json"
    if not combined.exists():
        return None

    data = _read_json(combined)
    for run in data.get("runs", []):
        if run.get("framework") == protocol:
            return run
    return None


def _iter_rows(root: Path) -> Iterable[Dict[str, Any]]:
    for protocol_dir in sorted([p for p in root.iterdir() if p.is_dir()]):
        protocol = protocol_dir.name
        for sweep_dir in sorted([p for p in protocol_dir.iterdir() if p.is_dir()]):
            for payload_dir in sorted(sweep_dir.glob("payload_*")):
                parts = payload_dir.name.split("_", 1)
                if len(parts) != 2:
                    continue
                try:
                    payload = int(parts[1])
                except ValueError:
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

                    run_dirs = sorted([p for p in concurrency_dir.iterdir() if p.is_dir()])
                    for run_idx, run_dir in enumerate(run_dirs, start=1):
                        run = _find_run_result(run_dir, protocol)
                        if not run:
                            continue

                        latency = run.get("latency", {}) or {}
                        throughput = run.get("throughput", {}) or {}
                        row = {
                            "protocol": protocol,
                            "payload": payload,
                            "concurrency": concurrency,
                            "run": run_idx,
                            "latency_avg_ms": float(latency.get("avg_ms", 0.0) or 0.0),
                            "p95_ms": float(latency.get("p95_ms", 0.0) or 0.0),
                            "throughput": float(throughput.get("bytes_per_sec", 0.0) or 0.0),
                            "startup_ms": float(run.get("end_to_end_startup_ms", 0.0) or 0.0),
                        }
                        yield row


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    fieldnames = [
        "protocol",
        "payload",
        "concurrency",
        "run",
        "latency_avg_ms",
        "p95_ms",
        "throughput",
        "startup_ms",
    ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="out_sweeps", help="Root sweep output directory")
    ap.add_argument("--out", default="analysis/aggregated.csv", help="Output CSV path")
    args = ap.parse_args()

    base_dir = Path(__file__).resolve().parents[1]
    root = _resolve_path(base_dir, args.root)
    out = _resolve_path(base_dir, args.out)

    if not root.exists():
        raise SystemExit(f"missing root directory: {root}")

    rows = list(_iter_rows(root))
    if not rows:
        raise SystemExit("no runs found under root")

    out.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(out, rows)
    print(f"Wrote {out} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
