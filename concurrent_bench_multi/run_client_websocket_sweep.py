#!/usr/bin/env python3

"""Repeat WebSocket client runs across payload sizes.

Runs run_client.py with --protocol websocket for multiple payload configs.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

PROTOCOL = "websocket"


def _parse_int_list(value: str) -> list[int]:
    values: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        values.append(int(part))
    return values


def _merge_run_csvs(worker_dirs: list[Path], out_csv: Path, concurrency: int) -> None:
    rows: list[dict[str, str]] = []
    header: str | None = None
    for idx, worker_dir in enumerate(worker_dirs):
        subdirs = [d for d in worker_dir.iterdir() if d.is_dir()]
        if len(subdirs) != 1:
            print(f"  warning: worker_{idx} has {len(subdirs)} run dirs, skipping")
            continue
        csv_path = subdirs[0] / "results.csv"
        if not csv_path.exists():
            print(f"  warning: no results.csv in {subdirs[0]}, skipping")
            continue
        lines = csv_path.read_text().splitlines()
        if len(lines) < 2:
            continue
        header = header or lines[0]
        for line in lines[1:]:
            rows.append({"worker": str(idx), "data": line})

    if not rows or header is None:
        return

    with out_csv.open("w") as f:
        f.write(f"worker,concurrency,{header}\n")
        for row in rows:
            f.write(f"{row['worker']},{concurrency},{row['data']}\n")

    print(f"  merged {len(rows)} rows -> {out_csv}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin-dir", required=True, help="CMake build dir containing binaries")
    ap.add_argument("--server-host", required=True, help="Server machine IP/hostname")
    ap.add_argument("--out-root", default=f"out_sweeps/{PROTOCOL}", help="Root output directory")
    ap.add_argument("--payloads", default="64,256,1024", help="Comma-separated payload sizes")
    ap.add_argument(
        "--concurrency-values",
        default="1,2,4,8,16",
        help="Comma-separated client concurrency levels",
    )
    ap.add_argument("--runs-per-payload", type=int, default=3, help="Runs per payload size")
    ap.add_argument("--base-port", type=int, default=18080)
    ap.add_argument("--requests", type=int, default=200)
    ap.add_argument("--duration-sec", type=float, default=5.0)
    ap.add_argument("--ws-path", default="/ws")
    ap.add_argument("--ready-timeout", type=float, default=10.0)
    ap.add_argument("--sleep-sec", type=float, default=0.2)
    ap.add_argument("--keep-going", action="store_true", help="Continue even if a run fails")
    args = ap.parse_args()

    payloads = _parse_int_list(args.payloads)
    if not payloads:
        raise SystemExit("--payloads must contain at least one value")
    concurrency_values = _parse_int_list(args.concurrency_values)
    if not concurrency_values:
        raise SystemExit("--concurrency-values must contain at least one value")
    if any(v <= 0 for v in concurrency_values):
        raise SystemExit("--concurrency-values must be > 0")
    if args.runs_per_payload <= 0:
        raise SystemExit("--runs-per-payload must be > 0")

    script_dir = Path(__file__).resolve().parent
    run_client = script_dir / "run_client.py"
    if not run_client.exists():
        raise SystemExit(f"missing {run_client}")

    sweep_ts = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
    sweep_dir = Path(args.out_root) / f"{PROTOCOL}_{sweep_ts}"
    sweep_dir.mkdir(parents=True, exist_ok=False)

    config = {"protocol": PROTOCOL, "args": vars(args)}
    (sweep_dir / "sweep_config.json").write_text(json.dumps(config, indent=2))

    failures: list[tuple[int, int, int, int]] = []  # (payload, concurrency, run_index, returncode)
    run_total = len(payloads) * len(concurrency_values) * args.runs_per_payload
    run_count = 0

    for payload in payloads:
        payload_dir = sweep_dir / f"payload_{payload}"
        payload_dir.mkdir(parents=True, exist_ok=True)
        for concurrency in concurrency_values:
            concurrency_dir = payload_dir / f"concurrency_{concurrency}"
            concurrency_dir.mkdir(parents=True, exist_ok=True)
            for run_idx in range(args.runs_per_payload):
                run_count += 1
                worker_dirs = [concurrency_dir / f"worker_{i}" for i in range(concurrency)]
                for worker_dir in worker_dirs:
                    worker_dir.mkdir(parents=True, exist_ok=True)

                cmds = [
                    [
                        sys.executable,
                        str(run_client),
                        "--bin-dir",
                        str(args.bin_dir),
                        "--server-host",
                        str(args.server_host),
                        "--out-dir",
                        str(worker_dir),
                        "--protocol",
                        PROTOCOL,
                        "--base-port",
                        str(args.base_port),
                        "--requests",
                        str(args.requests),
                        "--payload-bytes",
                        str(payload),
                        "--duration-sec",
                        str(args.duration_sec),
                        "--ws-path",
                        str(args.ws_path),
                        "--ready-timeout",
                        str(args.ready_timeout),
                    ]
                    for worker_dir in worker_dirs
                ]

                print(
                    f"[{run_count}/{run_total}] payload={payload} concurrency={concurrency} run={run_idx+1}"
                )
                t0 = time.time()
                procs: list[subprocess.Popen[str]] = [
                    subprocess.Popen(cmd, text=True) for cmd in cmds
                ]
                return_codes = [p.wait() for p in procs]
                dt = time.time() - t0
                print(
                    f"[{run_count}/{run_total}] exit={return_codes} elapsed={dt:.2f}s"
                )

                if any(rc != 0 for rc in return_codes):
                    failures.append((payload, concurrency, run_idx, next(rc for rc in return_codes if rc != 0)))
                    if not args.keep_going:
                        print("Stopping on first failure (use --keep-going to continue).")
                        break
                else:
                    _merge_run_csvs(
                        worker_dirs,
                        concurrency_dir / f"run_{run_idx+1}_combined.csv",
                        concurrency,
                    )

                if args.sleep_sec > 0:
                    time.sleep(args.sleep_sec)

            if failures and not args.keep_going:
                break

        if failures and not args.keep_going:
            break

    if failures:
        print("\nFailures:")
        for payload, concurrency, run_idx, rc in failures:
            print(f"- payload {payload} concurrency {concurrency} run {run_idx+1}: exit {rc}")
        return 1

    print(f"\nSweep complete: {sweep_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
