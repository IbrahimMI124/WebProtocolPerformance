#!/usr/bin/env python3

"""Client-side benchmark runner for multi-machine runs.

Connects to servers started by run_server.py on another machine and
writes results/latency CSVs locally.
"""

import argparse
import os
import json
import socket
import subprocess
import time
from pathlib import Path


def _wait_port(host: str, port: int, timeout_s: float) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            try:
                sock.connect((host, port))
                return
            except OSError:
                time.sleep(0.05)
    raise TimeoutError(f"timeout waiting for {host}:{port}")


def _run_json(cmd: list[str], env: dict[str, str], timeout_s: float = 120.0) -> dict:
    out = subprocess.check_output(cmd, text=True, timeout=timeout_s, env=env)
    last = out.strip().splitlines()[-1]
    return json.loads(last)


def _make_env(bin_dir: Path) -> dict[str, str]:
    env = dict(**{k: v for k, v in os.environ.items()})
    lib_path = bin_dir / "_libdatachannel"
    if lib_path.exists():
        existing = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = f"{lib_path}:{existing}" if existing else str(lib_path)
    return env


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin-dir", required=True, help="CMake build dir containing binaries")
    ap.add_argument("--server-host", required=True, help="Server machine IP/hostname")
    ap.add_argument("--out-dir", default="bench", help="Where to write results")
    ap.add_argument("--base-port", type=int, default=18080)
    ap.add_argument("--requests", type=int, default=200)
    ap.add_argument("--payload-bytes", type=int, default=64)
    ap.add_argument("--duration-sec", type=float, default=5.0)
    ap.add_argument("--ws-path", default="/ws")
    ap.add_argument("--ready-timeout", type=float, default=10.0)
    args = ap.parse_args()

    bin_dir = Path(args.bin_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    env = _make_env(bin_dir)

    rest_port = args.base_port
    ws_port = args.base_port + 1
    webrtc_setup_port = args.base_port + 2
    webrtc_latency_port = args.base_port + 12
    webrtc_throughput_port = args.base_port + 22

    results = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "params": {
            "requests": args.requests,
            "payload_bytes": args.payload_bytes,
            "duration_sec": args.duration_sec,
        },
        "runs": [],
    }

    rest_client = bin_dir / "rest_client"
    ws_client = bin_dir / "ws_client"
    webrtc_client = bin_dir / "webrtc_client"

    # REST
    if rest_client.exists():
        _wait_port(args.server_host, rest_port, args.ready_timeout)
        t0 = time.time()
        latency = _run_json([
            str(rest_client),
            "--url", f"http://{args.server_host}:{rest_port}",
            "--mode", "latency",
            "--requests", str(args.requests),
            "--payload-bytes", str(args.payload_bytes),
            "--out-latency-csv", str(out_dir / "rest_latency.csv"),
        ], env)
        thr = _run_json([
            str(rest_client),
            "--url", f"http://{args.server_host}:{rest_port}",
            "--mode", "throughput",
            "--duration-sec", str(args.duration_sec),
            "--payload-bytes", str(args.payload_bytes),
        ], env)
        results["runs"].append({
            "framework": "rest",
            "server_ready_ms": 0.0,
            "end_to_end_startup_ms": (time.time() - t0) * 1000.0,
            "latency": latency,
            "throughput": thr,
        })

    # WebSocket
    if ws_client.exists():
        _wait_port(args.server_host, ws_port, args.ready_timeout)
        t0 = time.time()
        latency = _run_json([
            str(ws_client),
            "--host", args.server_host,
            "--port", str(ws_port),
            "--path", args.ws_path,
            "--mode", "latency",
            "--requests", str(args.requests),
            "--payload-bytes", str(args.payload_bytes),
            "--out-latency-csv", str(out_dir / "ws_latency.csv"),
        ], env)
        thr = _run_json([
            str(ws_client),
            "--host", args.server_host,
            "--port", str(ws_port),
            "--path", args.ws_path,
            "--mode", "throughput",
            "--duration-sec", str(args.duration_sec),
            "--payload-bytes", str(args.payload_bytes),
        ], env)
        results["runs"].append({
            "framework": "websocket",
            "server_ready_ms": 0.0,
            "end_to_end_startup_ms": (time.time() - t0) * 1000.0,
            "latency": latency,
            "throughput": thr,
        })

    # WebRTC (separate signaling servers per mode)
    if webrtc_client.exists():
        _wait_port(args.server_host, webrtc_setup_port, args.ready_timeout)
        _wait_port(args.server_host, webrtc_latency_port, args.ready_timeout)
        _wait_port(args.server_host, webrtc_throughput_port, args.ready_timeout)

        setup = _run_json([
            str(webrtc_client),
            "--signaling", f"ws://{args.server_host}:{webrtc_setup_port}",
            "--mode", "setup",
        ], env, timeout_s=120.0)

        latency = _run_json([
            str(webrtc_client),
            "--signaling", f"ws://{args.server_host}:{webrtc_latency_port}",
            "--mode", "latency",
            "--requests", str(args.requests),
            "--payload-bytes", str(args.payload_bytes),
            "--out-latency-csv", str(out_dir / "webrtc_latency.csv"),
        ], env, timeout_s=120.0)

        thr = _run_json([
            str(webrtc_client),
            "--signaling", f"ws://{args.server_host}:{webrtc_throughput_port}",
            "--mode", "throughput",
            "--duration-sec", str(args.duration_sec),
            "--payload-bytes", str(args.payload_bytes),
        ], env, timeout_s=120.0)

        results["runs"].append({
            "framework": "webrtc",
            "server_ready_ms": 0.0,
            "end_to_end_startup_ms": 0.0,
            "setup": {"result": setup},
            "latency": latency,
            "throughput": thr,
        })

    (out_dir / "results.json").write_text(json.dumps(results, indent=2))

    rows = [
        "framework,server_ready_ms,end_to_end_startup_ms,latency_avg_ms,latency_p95_ms,throughput_bytes_per_sec",
    ]
    for r in results["runs"]:
        lat = r.get("latency", {})
        thr = r.get("throughput", {})
        rows.append(
            f"{r.get('framework','')},{r.get('server_ready_ms',0):.3f},{r.get('end_to_end_startup_ms',0):.3f},{lat.get('avg_ms',0):.6f},{lat.get('p95_ms',0):.6f},{thr.get('bytes_per_sec',0):.3f}"
        )

    (out_dir / "results.csv").write_text("\n".join(rows) + "\n")

    print(f"Wrote {out_dir/'results.json'} and {out_dir/'results.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())