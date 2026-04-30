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

Outputs (by default in `bench_multiple_machines/out/`):
- `results.json`: full nested results and parameters
- `results.csv`: one row per framework + concurrency (easy to plot)
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


def _run_json_parallel(cmds: list[list[str]], timeout_s: float = 120.0) -> list[dict]:
    # Run multiple client commands in parallel and parse JSON output from each.
    procs: list[subprocess.Popen] = []
    try:
        for cmd in cmds:
            procs.append(
                subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True,
                )
            )
        results: list[dict] = []
        for p in procs:
            out, _ = p.communicate(timeout=timeout_s)
            if p.returncode != 0:
                raise RuntimeError(f"client failed: {p.returncode}\n{out}")
            last = out.strip().splitlines()[-1]
            results.append(json.loads(last))
        return results
    finally:
        for p in procs:
            if p.poll() is None:
                try:
                    p.kill()
                except Exception:
                    pass


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


def _parse_concurrency_list(value: str) -> list[int]:
    items = [v.strip() for v in value.split(",") if v.strip()]
    out: list[int] = []
    for it in items:
        try:
            n = int(it)
        except Exception:
            raise ValueError(f"invalid concurrency: {it}")
        if n <= 0:
            raise ValueError(f"invalid concurrency: {it}")
        out.append(n)
    # Deduplicate while preserving order.
    seen: set[int] = set()
    uniq: list[int] = []
    for n in out:
        if n not in seen:
            uniq.append(n)
            seen.add(n)
    return uniq


def _read_latency_csv(path: Path) -> list[float]:
    if not path.exists():
        return []
    lines = path.read_text().splitlines()
    if not lines:
        return []
    samples: list[float] = []
    for line in lines[1:]:
        try:
            samples.append(float(line.strip()))
        except Exception:
            continue
    return samples


def _percentile(samples: list[float], p: float) -> float:
    if not samples:
        return 0.0
    if p <= 0:
        return min(samples)
    if p >= 100:
        return max(samples)
    vals = sorted(samples)
    idx = (p / 100.0) * (len(vals) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(vals) - 1)
    frac = idx - lo
    return vals[lo] * (1.0 - frac) + vals[hi] * frac


def _summarize_samples(samples: list[float]) -> dict:
    if not samples:
        return {"n": 0, "min_ms": 0.0, "max_ms": 0.0, "avg_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0}
    return {
        "n": len(samples),
        "min_ms": min(samples),
        "max_ms": max(samples),
        "avg_ms": sum(samples) / len(samples),
        "p50_ms": _percentile(samples, 50),
        "p95_ms": _percentile(samples, 95),
    }


def main() -> int:
    # Parse CLI args.
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin-dir", required=True, help="CMake build dir containing binaries")
    ap.add_argument("--out-dir", default="bench_multiple_machines/out", help="Where to write results")
    ap.add_argument("--role", choices=["both", "server", "client"], default="both")
    ap.add_argument("--bind-host", default="127.0.0.1", help="Host interface for servers to bind")
    ap.add_argument("--server-host", default="127.0.0.1", help="Host/IP for clients to connect")
    ap.add_argument("--base-port", type=int, default=18080)
    ap.add_argument("--requests", type=int, default=200)
    ap.add_argument("--payload-bytes", type=int, default=64)
    ap.add_argument("--duration-sec", type=float, default=5.0)
    ap.add_argument("--concurrency-list", default="1,2,4,8,16,32", help="Comma-separated client counts")
    args = ap.parse_args()

    bin_dir = Path(args.bin_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rest_port = args.base_port
    ws_port = args.base_port + 1
    webrtc_port = args.base_port + 2
    concurrency_list = _parse_concurrency_list(args.concurrency_list)

    results = {
        # ISO-ish timestamp for labeling runs.
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # Parameters captured so you can compare runs later.
        "params": {
            "requests": args.requests,
            "payload_bytes": args.payload_bytes,
            "duration_sec": args.duration_sec,
            "concurrency_list": concurrency_list,
            "role": args.role,
            "bind_host": args.bind_host,
            "server_host": args.server_host,
        },
        # Each protocol run appends one entry here.
        "runs": [],
    }

    if args.role == "server":
        procs: list[subprocess.Popen] = []
        try:
            rest_server = bin_dir / "rest_server"
            ws_server = bin_dir / "ws_server"
            webrtc_server = bin_dir / "webrtc_server"
            if rest_server.exists():
                procs.append(subprocess.Popen([str(rest_server), "--host", args.bind_host, "--port", str(rest_port)]))
            if ws_server.exists():
                procs.append(subprocess.Popen([str(ws_server), "--host", args.bind_host, "--port", str(ws_port), "--path", "/ws"]))
            if webrtc_server.exists():
                procs.append(subprocess.Popen([str(webrtc_server), "--port", str(webrtc_port)]))

            print("Servers running. Press Ctrl+C to stop.")
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        finally:
            for p in procs:
                _terminate(p)
        return 0

    # REST
    # ----- REST benchmark -----
    # Server: `rest_server`
    # Client: `rest_client`
    # Signaling/setup: none (plain HTTP)
    rest_server = bin_dir / "rest_server"
    rest_client = bin_dir / "rest_client"
    if rest_server.exists() and rest_client.exists():
        for conc in concurrency_list:
            t0 = time.time()
            if args.role == "both":
                p, info = _spawn_server([str(rest_server), "--host", args.bind_host, "--port", str(rest_port)])
            else:
                p = None
                info = {"server_ready_ms": 0.0}
            try:
                latency_cmds: list[list[str]] = []
                latency_csvs: list[Path] = []
                for i in range(conc):
                    csv_path = out_dir / f"rest_latency_c{conc}_i{i:03d}.csv"
                    latency_csvs.append(csv_path)
                    latency_cmds.append([
                        str(rest_client),
                        "--url", f"http://{args.server_host}:{rest_port}",
                        "--mode", "latency",
                        "--requests", str(args.requests),
                        "--payload-bytes", str(args.payload_bytes),
                        "--out-latency-csv", str(csv_path),
                    ])
                _run_json_parallel(latency_cmds)
                samples: list[float] = []
                for pth in latency_csvs:
                    samples.extend(_read_latency_csv(pth))
                latency = _summarize_samples(samples)

                thr_cmds = [
                    [
                        str(rest_client),
                        "--url", f"http://{args.server_host}:{rest_port}",
                        "--mode", "throughput",
                        "--duration-sec", str(args.duration_sec),
                        "--payload-bytes", str(args.payload_bytes),
                    ]
                    for _ in range(conc)
                ]
                thr_outs = _run_json_parallel(thr_cmds)
                total_bytes = sum(int(o.get("bytes", 0)) for o in thr_outs)
                total_msgs = sum(int(o.get("messages", 0)) for o in thr_outs)
                thr = {
                    "duration_sec": args.duration_sec,
                    "messages": total_msgs,
                    "bytes": total_bytes,
                    "bytes_per_sec": (total_bytes / args.duration_sec) if args.duration_sec > 0 else 0.0,
                }

                results["runs"].append({
                    "framework": "rest",
                    "concurrency": conc,
                    "server_ready_ms": info["server_ready_ms"],
                    "end_to_end_startup_ms": (time.time() - t0) * 1000.0,
                    "latency": latency,
                    "throughput": thr,
                })
            finally:
                if p is not None:
                    _terminate(p)

    # WebSocket
    # ----- WebSocket benchmark -----
    # Server: `ws_server` (uWebSockets)
    # Client: `ws_client` (custom minimal RFC6455 client)
    ws_server = bin_dir / "ws_server"
    ws_client = bin_dir / "ws_client"
    if ws_server.exists() and ws_client.exists():
        for conc in concurrency_list:
            t0 = time.time()
            if args.role == "both":
                p, info = _spawn_server([str(ws_server), "--host", args.bind_host, "--port", str(ws_port), "--path", "/ws"])
            else:
                p = None
                info = {"server_ready_ms": 0.0}
            try:
                latency_cmds = []
                latency_csvs: list[Path] = []
                for i in range(conc):
                    csv_path = out_dir / f"ws_latency_c{conc}_i{i:03d}.csv"
                    latency_csvs.append(csv_path)
                    latency_cmds.append([
                        str(ws_client),
                        "--host", args.server_host,
                        "--port", str(ws_port),
                        "--path", "/ws",
                        "--mode", "latency",
                        "--requests", str(args.requests),
                        "--payload-bytes", str(args.payload_bytes),
                        "--out-latency-csv", str(csv_path),
                    ])
                _run_json_parallel(latency_cmds)
                samples = []
                for pth in latency_csvs:
                    samples.extend(_read_latency_csv(pth))
                latency = _summarize_samples(samples)

                thr_cmds = [
                    [
                        str(ws_client),
                        "--host", args.server_host,
                        "--port", str(ws_port),
                        "--path", "/ws",
                        "--mode", "throughput",
                        "--duration-sec", str(args.duration_sec),
                        "--payload-bytes", str(args.payload_bytes),
                    ]
                    for _ in range(conc)
                ]
                thr_outs = _run_json_parallel(thr_cmds)
                total_bytes = sum(int(o.get("bytes", 0)) for o in thr_outs)
                total_msgs = sum(int(o.get("messages", 0)) for o in thr_outs)
                thr = {
                    "duration_sec": args.duration_sec,
                    "messages": total_msgs,
                    "bytes": total_bytes,
                    "bytes_per_sec": (total_bytes / args.duration_sec) if args.duration_sec > 0 else 0.0,
                }

                results["runs"].append({
                    "framework": "websocket",
                    "concurrency": conc,
                    "server_ready_ms": info["server_ready_ms"],
                    "end_to_end_startup_ms": (time.time() - t0) * 1000.0,
                    "latency": latency,
                    "throughput": thr,
                })
            finally:
                if p is not None:
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
        def webrtc_one(
            mode: str,
            port: int,
            extras_list: list[list[str]],
            concurrency: int,
        ) -> tuple[dict, list[dict]]:
            t0 = time.time()
            if args.role == "both":
                p, info = _spawn_server([str(webrtc_server), "--port", str(port)])
            else:
                p = None
                info = {"server_ready_ms": 0.0}
            try:
                if extras_list and len(extras_list) != concurrency:
                    raise ValueError("extras_list length must match concurrency")
                cmds = []
                for i in range(concurrency):
                    extra = extras_list[i] if extras_list else []
                    cmds.append([
                        str(webrtc_client),
                        "--signaling", f"ws://{args.server_host}:{port}",
                        "--mode", mode,
                        *extra,
                    ])
                outs = _run_json_parallel(cmds, timeout_s=120.0)
                info["end_to_end_startup_ms"] = (time.time() - t0) * 1000.0
                return info, outs
            finally:
                if p is not None:
                    _terminate(p)

        for conc in concurrency_list:
            # libdatachannel WebSocketServer tends to fail subsequent handshakes in the
            # same process; isolate each measurement in a fresh signaling server.
            setup_info, setup_list = webrtc_one("setup", webrtc_port, [], conc)
            setup_ms_vals = [float(o.get("setup_ms", 0.0)) for o in setup_list]
            setup_result = {
                "setup_ms": sum(setup_ms_vals) / len(setup_ms_vals) if setup_ms_vals else 0.0,
            }

            latency_csvs: list[Path] = []
            latency_extras: list[list[str]] = []
            for i in range(conc):
                csv_path = out_dir / f"webrtc_latency_c{conc}_i{i:03d}.csv"
                latency_csvs.append(csv_path)
                latency_extras.append([
                    "--requests", str(args.requests),
                    "--payload-bytes", str(args.payload_bytes),
                    "--out-latency-csv", str(csv_path),
                ])
            latency_info, _ = webrtc_one(
                "latency",
                webrtc_port + 10,
                latency_extras,
                conc,
            )
            samples = []
            for pth in latency_csvs:
                samples.extend(_read_latency_csv(pth))
            latency = _summarize_samples(samples)
            latency["setup_ms"] = setup_result["setup_ms"]

            thr_info, thr_list = webrtc_one(
                "throughput",
                webrtc_port + 20,
                [
                    [
                        "--duration-sec", str(args.duration_sec),
                        "--payload-bytes", str(args.payload_bytes),
                    ]
                    for _ in range(conc)
                ],
                conc,
            )
            total_bytes = sum(int(o.get("bytes", 0)) for o in thr_list)
            total_msgs = sum(int(o.get("messages", 0)) for o in thr_list)
            thr = {
                "duration_sec": args.duration_sec,
                "messages": total_msgs,
                "bytes": total_bytes,
                "bytes_per_sec": (total_bytes / args.duration_sec) if args.duration_sec > 0 else 0.0,
                "setup_ms": setup_result["setup_ms"],
            }

            results["runs"].append({
                "framework": "webrtc",
                "concurrency": conc,
                "server_ready_ms": latency_info.get("server_ready_ms", 0.0),
                "end_to_end_startup_ms": latency_info.get("end_to_end_startup_ms", 0.0),
                "setup": {"info": setup_info, "result": setup_result},
                "latency": latency,
                "throughput": thr,
                "throughput_info": thr_info,
            })

    (out_dir / "results.json").write_text(json.dumps(results, indent=2))

    # Flatten to CSV
    # `results.csv` is intentionally a small, simple table for plotting.
    # Raw latency samples are written by the clients into `*_latency.csv`.
    rows = [
        "framework,concurrency,server_ready_ms,end_to_end_startup_ms,latency_avg_ms,latency_p95_ms,throughput_bytes_per_sec",
    ]
    for r in results["runs"]:
        lat = r.get("latency", {})
        thr = r.get("throughput", {})
        rows.append(
            f"{r.get('framework','')},{r.get('concurrency',1)},{r.get('server_ready_ms',0):.3f},{r.get('end_to_end_startup_ms',0):.3f},{lat.get('avg_ms',0):.6f},{lat.get('p95_ms',0):.6f},{thr.get('bytes_per_sec',0):.3f}"
        )

    (out_dir / "results.csv").write_text("\n".join(rows) + "\n")

    print(f"Wrote {out_dir/'results.json'} and {out_dir/'results.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
