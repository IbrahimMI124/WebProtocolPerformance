#!/usr/bin/env python3

"""Repeat WebRTC client runs across payload sizes.

Runs run_client.py with --protocol webrtc for multiple payload configs.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

PROTOCOL = "webrtc"


def _parse_payloads(value: str) -> list[int]:
    payloads: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        payloads.append(int(part))
    return payloads


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin-dir", required=True, help="CMake build dir containing binaries")
    ap.add_argument("--server-host", required=True, help="Server machine IP/hostname")
    ap.add_argument("--out-root", default=f"out_sweeps/{PROTOCOL}", help="Root output directory")
    ap.add_argument("--payloads", default="64,256,1024", help="Comma-separated payload sizes")
    ap.add_argument("--runs-per-payload", type=int, default=3, help="Runs per payload size")
    ap.add_argument("--base-port", type=int, default=18080)
    ap.add_argument("--requests", type=int, default=200)
    ap.add_argument("--duration-sec", type=float, default=5.0)
    ap.add_argument("--ws-path", default="/ws")
    ap.add_argument("--ready-timeout", type=float, default=10.0)
    ap.add_argument("--sleep-sec", type=float, default=1.5)
    ap.add_argument("--keep-going", action="store_true", help="Continue even if a run fails")
    args = ap.parse_args()

    payloads = _parse_payloads(args.payloads)
    if not payloads:
        raise SystemExit("--payloads must contain at least one value")
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

    failures: list[tuple[int, int, int]] = []  # (payload, run_index, returncode)
    run_total = len(payloads) * args.runs_per_payload
    run_count = 0

    for payload in payloads:
        payload_dir = sweep_dir / f"payload_{payload}"
        payload_dir.mkdir(parents=True, exist_ok=True)
        for run_idx in range(args.runs_per_payload):
            run_count += 1
            cmd = [
                sys.executable,
                str(run_client),
                "--bin-dir",
                str(args.bin_dir),
                "--server-host",
                str(args.server_host),
                "--out-dir",
                str(payload_dir),
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
            print(f"[{run_count}/{run_total}] payload={payload} run={run_idx+1}: {' '.join(cmd)}")
            t0 = time.time()
            p = subprocess.run(cmd, text=True)
            dt = time.time() - t0
            print(f"[{run_count}/{run_total}] exit={p.returncode} elapsed={dt:.2f}s")

            if p.returncode != 0:
                failures.append((payload, run_idx, p.returncode))
                if not args.keep_going:
                    print("Stopping on first failure (use --keep-going to continue).")
                    break

            if args.sleep_sec > 0:
                time.sleep(args.sleep_sec)

        if failures and not args.keep_going:
            break

    if failures:
        print("\nFailures:")
        for payload, run_idx, rc in failures:
            print(f"- payload {payload} run {run_idx+1}: exit {rc}")
        return 1

    print(f"\nSweep complete: {sweep_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
