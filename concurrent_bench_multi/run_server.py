#!/usr/bin/env python3

"""Launch benchmark servers for multi-machine runs.

Starts REST, WebSocket, and three WebRTC signaling servers (one per mode)
on fixed ports derived from --base-port.
"""

import argparse
import os
import signal
import socket
import subprocess
import sys
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


def _spawn(cmd: list[str], env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(cmd, env=env)


def _make_env(bin_dir: Path) -> dict[str, str]:
    env = dict(**{k: v for k, v in os.environ.items()})
    lib_path = bin_dir / "_libdatachannel"
    if lib_path.exists():
        existing = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = f"{lib_path}:{existing}" if existing else str(lib_path)
    return env


def _terminate_all(procs: list[subprocess.Popen]) -> None:
    for p in procs:
        if p.poll() is None:
            try:
                p.send_signal(signal.SIGTERM)
            except Exception:
                pass
    for p in procs:
        if p.poll() is None:
            try:
                p.wait(timeout=3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin-dir", required=True, help="CMake build dir containing binaries")
    ap.add_argument("--host", default="0.0.0.0", help="Bind host for REST/WS servers")
    ap.add_argument("--base-port", type=int, default=18080)
    ap.add_argument(
        "--protocol",
        choices=["rest", "websocket", "webrtc", "all"],
        default="all",
        help="Which protocol servers to start",
    )
    ap.add_argument("--ws-path", default="/ws")
    ap.add_argument("--ready-timeout", type=float, default=10.0)
    args = ap.parse_args()

    bin_dir = Path(args.bin_dir)
    env = _make_env(bin_dir)

    rest_port = args.base_port
    ws_port = args.base_port + 1
    webrtc_setup_port = args.base_port + 2
    webrtc_latency_port = args.base_port + 12
    webrtc_throughput_port = args.base_port + 22

    rest_server = bin_dir / "rest_server"
    ws_server = bin_dir / "ws_server"
    webrtc_server = bin_dir / "webrtc_server"

    procs: list[subprocess.Popen] = []
    selected = {"rest", "websocket", "webrtc"} if args.protocol == "all" else {args.protocol}
    try:
        if "rest" in selected and rest_server.exists():
            procs.append(_spawn([str(rest_server), "--host", args.host, "--port", str(rest_port)], env))
        if "websocket" in selected and ws_server.exists():
            procs.append(
                _spawn([
                    str(ws_server),
                    "--host", args.host,
                    "--port", str(ws_port),
                    "--path", args.ws_path,
                ], env)
            )
        if "webrtc" in selected and webrtc_server.exists():
            procs.append(_spawn([str(webrtc_server), "--host", args.host, "--port", str(webrtc_setup_port)], env))
            procs.append(_spawn([str(webrtc_server), "--host", args.host, "--port", str(webrtc_latency_port)], env))
            procs.append(_spawn([str(webrtc_server), "--host", args.host, "--port", str(webrtc_throughput_port)], env))

        if "rest" in selected and rest_server.exists():
            _wait_port(args.host, rest_port, args.ready_timeout)
        if "websocket" in selected and ws_server.exists():
            _wait_port(args.host, ws_port, args.ready_timeout)
        if "webrtc" in selected and webrtc_server.exists():
            _wait_port(args.host, webrtc_setup_port, args.ready_timeout)
            _wait_port(args.host, webrtc_latency_port, args.ready_timeout)
            _wait_port(args.host, webrtc_throughput_port, args.ready_timeout)

        print("Servers ready:")
        if "rest" in selected and rest_server.exists():
            print(f"  REST http://{args.host}:{rest_port}")
        if "websocket" in selected and ws_server.exists():
            print(f"  WS   ws://{args.host}:{ws_port}{args.ws_path}")
        if "webrtc" in selected and webrtc_server.exists():
            print(f"  WebRTC setup      ws://{args.host}:{webrtc_setup_port}")
            print(f"  WebRTC latency    ws://{args.host}:{webrtc_latency_port}")
            print(f"  WebRTC throughput ws://{args.host}:{webrtc_throughput_port}")

        for p in procs:
            p.wait()
        return 0
    except KeyboardInterrupt:
        return 130
    finally:
        _terminate_all(procs)


if __name__ == "__main__":
    raise SystemExit(main())