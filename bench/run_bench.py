#!/usr/bin/env python3

"""Benchmark orchestrator for REST vs WebSocket vs WebRTC.

This script is the *glue* that runs the C++ binaries and collects results.

High-level flow:
1) Spawn a protocol server process (REST / WS / WebRTC signaling)
2) Wait for a single readiness line on stdout:
    READY <host> <port>
3) Run the matching client binary in different modes (latency / throughput / setup)
4) Parse the client's final line as JSON and store it
5) Write `results.json` (rich structure) and `results.csv` (flat table)

Why do servers print `READY ...`?
- It avoids guessing "how long the server needs to boot".
- The runner can start clients immediately after the server is actually listening.

Outputs (by default in `bench/out/`):
- `results.json`: full nested results and parameters
- `results.csv`: one row per framework (easy to plot)
- `*_latency.csv`: raw latency samples (used for CDF/boxplots)
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def _readline_deadline(p: subprocess.Popen, deadline_s: float) -> str:
    # Read line-by-line from the process stdout until a deadline.
    #
    # `subprocess.Popen(..., stdout=PIPE, text=True)` gives us a text stream.
    # We keep polling because `readline()` can block if the process is silent.
    while time.time() < deadline_s:
        line = p.stdout.readline()
        if line:
            return line.strip()
        if p.poll() is not None:
            raise RuntimeError(f"process exited early: {p.returncode}")
        time.sleep(0.01)
    raise TimeoutError("timeout waiting for line")


def _run_json(cmd: list[str], timeout_s: float = 120.0) -> dict:
    # Run a client command and parse its *last* stdout line as JSON.
    #
    # All our C++ clients print a single JSON object on the final line.
    # We take the last line so any debug prints above don't break parsing.
    out = subprocess.check_output(cmd, text=True, timeout=timeout_s)
    last = out.strip().splitlines()[-1]
    return json.loads(last)


def _spawn_server(cmd: list[str], ready_timeout_s: float = 10.0) -> tuple[subprocess.Popen, dict]:
    # Spawn a server process and wait for its READY line.
    #
    # Returns:
    # - the Popen handle (so we can terminate it later)
    # - an info dict containing host/port and server_ready_ms
    p = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )
    t0 = time.time()
    line = _readline_deadline(p, t0 + ready_timeout_s)
    if not line.startswith("READY "):
        raise RuntimeError(f"unexpected ready line: {line}")
    _, host, port = line.split()
    return p, {"host": host, "port": int(port), "server_ready_ms": (time.time() - t0) * 1000.0}


def _terminate(p: subprocess.Popen):
    # Best-effort process shutdown.
    #
    # We try SIGTERM first (graceful), then SIGKILL (force) if needed.
    if p.poll() is not None:
        return
    try:
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=3)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


def main() -> int:
    # Parse CLI args.
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin-dir", required=True, help="CMake build dir containing binaries")
    ap.add_argument("--out-dir", default="bench", help="Where to write results")
    ap.add_argument("--base-port", type=int, default=18080)
    ap.add_argument("--requests", type=int, default=200)
    ap.add_argument("--payload-bytes", type=int, default=64)
    ap.add_argument("--duration-sec", type=float, default=5.0)
    args = ap.parse_args()

    bin_dir = Path(args.bin_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rest_port = args.base_port
    ws_port = args.base_port + 1
    webrtc_port = args.base_port + 2

    results = {
        # ISO-ish timestamp for labeling runs.
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # Parameters captured so you can compare runs later.
        "params": {
            "requests": args.requests,
            "payload_bytes": args.payload_bytes,
            "duration_sec": args.duration_sec,
        },
        # Each protocol run appends one entry here.
        "runs": [],
    }

    # REST
    # ----- REST benchmark -----
    # Server: `rest_server`
    # Client: `rest_client`
    # Signaling/setup: none (plain HTTP)
    rest_server = bin_dir / "rest_server"
    rest_client = bin_dir / "rest_client"
    if rest_server.exists() and rest_client.exists():
        t0 = time.time()
        p, info = _spawn_server([str(rest_server), "--host", "127.0.0.1", "--port", str(rest_port)])
        try:
            latency = _run_json([
                str(rest_client),
                "--url", f"http://127.0.0.1:{rest_port}",
                "--mode", "latency",
                "--requests", str(args.requests),
                "--payload-bytes", str(args.payload_bytes),
                "--out-latency-csv", str(out_dir / "rest_latency.csv"),
            ])
            thr = _run_json([
                str(rest_client),
                "--url", f"http://127.0.0.1:{rest_port}",
                "--mode", "throughput",
                "--duration-sec", str(args.duration_sec),
                "--payload-bytes", str(args.payload_bytes),
            ])
            results["runs"].append({
                "framework": "rest",
                "server_ready_ms": info["server_ready_ms"],
                # Includes server spawn + client runs (latency + throughput).
                "end_to_end_startup_ms": (time.time() - t0) * 1000.0,
                "latency": latency,
                "throughput": thr,
            })
        finally:
            _terminate(p)

    # WebSocket
    # ----- WebSocket benchmark -----
    # Server: `ws_server` (uWebSockets)
    # Client: `ws_client` (custom minimal RFC6455 client)
    ws_server = bin_dir / "ws_server"
    ws_client = bin_dir / "ws_client"
    if ws_server.exists() and ws_client.exists():
        t0 = time.time()
        p, info = _spawn_server([str(ws_server), "--host", "127.0.0.1", "--port", str(ws_port), "--path", "/ws"])
        try:
            latency = _run_json([
                str(ws_client),
                "--host", "127.0.0.1",
                "--port", str(ws_port),
                "--path", "/ws",
                "--mode", "latency",
                "--requests", str(args.requests),
                "--payload-bytes", str(args.payload_bytes),
                "--out-latency-csv", str(out_dir / "ws_latency.csv"),
            ])
            thr = _run_json([
                str(ws_client),
                "--host", "127.0.0.1",
                "--port", str(ws_port),
                "--path", "/ws",
                "--mode", "throughput",
                "--duration-sec", str(args.duration_sec),
                "--payload-bytes", str(args.payload_bytes),
            ])
            results["runs"].append({
                "framework": "websocket",
                "server_ready_ms": info["server_ready_ms"],
                "end_to_end_startup_ms": (time.time() - t0) * 1000.0,
                "latency": latency,
                "throughput": thr,
            })
        finally:
            _terminate(p)

    # WebRTC
    # ----- WebRTC benchmark -----
    # Server: `webrtc_server` (signaling WebSocket + PeerConnection)
    # Client: `webrtc_client` (signaling + PeerConnection + DataChannel)
    #
    # This has an explicit setup phase (SDP/ICE/DataChannel open). We measure it
    # via `webrtc_client --mode setup`.
    webrtc_server = bin_dir / "webrtc_server"
    webrtc_client = bin_dir / "webrtc_client"
    if webrtc_server.exists() and webrtc_client.exists():
        def webrtc_one(mode: str, port: int, extra: list[str]) -> tuple[dict, dict]:
            # Helper: run ONE WebRTC server + ONE client mode and capture output.
            # We return both server timing info and the parsed client JSON.
            t0 = time.time()
            p, info = _spawn_server([str(webrtc_server), "--port", str(port)])
            try:
                out = _run_json([
                    str(webrtc_client),
                    "--signaling", f"ws://127.0.0.1:{port}",
                    "--mode", mode,
                    *extra,
                ], timeout_s=120.0)
                info["end_to_end_startup_ms"] = (time.time() - t0) * 1000.0
                return info, out
            finally:
                _terminate(p)

        # libdatachannel WebSocketServer tends to fail subsequent handshakes in the
        # same process; isolate each measurement in a fresh signaling server.
        #
        # Practical effect: we start a *fresh* signaling server for each mode.
        # This makes the benchmark more reliable/repeatable.
        setup_info, setup = webrtc_one("setup", webrtc_port, [])
        latency_info, latency = webrtc_one(
            "latency",
            webrtc_port + 10,
            [
                "--requests", str(args.requests),
                "--payload-bytes", str(args.payload_bytes),
                "--out-latency-csv", str(out_dir / "webrtc_latency.csv"),
            ],
        )
        thr_info, thr = webrtc_one(
            "throughput",
            webrtc_port + 20,
            [
                "--duration-sec", str(args.duration_sec),
                "--payload-bytes", str(args.payload_bytes),
            ],
        )

        results["runs"].append({
            "framework": "webrtc",
            # Use the latency run's server timing as the representative one.
            "server_ready_ms": latency_info["server_ready_ms"],
            "end_to_end_startup_ms": latency_info["end_to_end_startup_ms"],
            # Keep setup as a nested object because it has separate timing info.
            "setup": {"info": setup_info, "result": setup},
            "latency": latency,
            "throughput": thr,
            "throughput_info": thr_info,
        })

    (out_dir / "results.json").write_text(json.dumps(results, indent=2))

    # Flatten to CSV
    # `results.csv` is intentionally a small, simple table for plotting.
    # Raw latency samples are written by the clients into `*_latency.csv`.
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
