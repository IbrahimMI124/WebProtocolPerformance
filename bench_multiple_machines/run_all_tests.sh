#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

usage() {
  cat <<'EOF'
Usage: run_all_tests.sh [options]

Options:
  --role both|server|client   Role for multi-machine runs (default: both)
  --server-host HOST          Server IP/hostname for client mode (default: 127.0.0.1)
  --bind-host HOST            Bind host for server mode (default: 0.0.0.0)
  --base-port PORT            Base port for protocols (default: 18080)
  --requests N                Number of latency requests (default: 200)
  --payload-bytes BYTES       Payload size (default: 64)
  --duration-sec SEC          Throughput duration (default: 5)
  --concurrency-list LIST     Comma list of client counts (default: 1,2,4,8,16,32)
  --out-dir PATH              Output directory for single-machine run (default: bench/out)
  --out-dir-mm PATH           Output directory for multi-machine run (default: bench_multiple_machines/out)
  -h, --help                  Show this help

Examples:
  # Single machine (runs both suites locally)
  ./run_all_tests.sh

  # Server machine
  ./run_all_tests.sh --role server --bind-host 0.0.0.0

  # Client machine
  ./run_all_tests.sh --role client --server-host 192.168.1.10
EOF
}

ROLE="both"
SERVER_HOST="127.0.0.1"
BIND_HOST="0.0.0.0"
BASE_PORT=18080
REQUESTS=200
PAYLOAD_BYTES=64
DURATION_SEC=5
CONCURRENCY_LIST="1,2,4,8,16,32"
OUT_DIR="$ROOT_DIR/bench/out"
OUT_DIR_MM="$ROOT_DIR/bench_multiple_machines/out"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --role)
      ROLE="$2"
      shift 2
      ;;
    --server-host)
      SERVER_HOST="$2"
      shift 2
      ;;
    --bind-host)
      BIND_HOST="$2"
      shift 2
      ;;
    --base-port)
      BASE_PORT="$2"
      shift 2
      ;;
    --requests)
      REQUESTS="$2"
      shift 2
      ;;
    --payload-bytes)
      PAYLOAD_BYTES="$2"
      shift 2
      ;;
    --duration-sec)
      DURATION_SEC="$2"
      shift 2
      ;;
    --concurrency-list)
      CONCURRENCY_LIST="$2"
      shift 2
      ;;
    --out-dir)
      OUT_DIR="$2"
      shift 2
      ;;
    --out-dir-mm)
      OUT_DIR_MM="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ "$ROLE" != "both" && "$ROLE" != "server" && "$ROLE" != "client" ]]; then
  echo "Invalid --role: $ROLE" >&2
  exit 2
fi

if [[ "$ROLE" == "both" ]]; then
  python3 "$ROOT_DIR/bench/run_bench.py" \
    --bin-dir "$ROOT_DIR/bench/build" \
    --out-dir "$OUT_DIR" \
    --base-port "$BASE_PORT" \
    --requests "$REQUESTS" \
    --payload-bytes "$PAYLOAD_BYTES" \
    --duration-sec "$DURATION_SEC"
fi

python3 "$ROOT_DIR/bench_multiple_machines/run_bench.py" \
  --bin-dir "$ROOT_DIR/bench_multiple_machines/build" \
  --out-dir "$OUT_DIR_MM" \
  --role "$ROLE" \
  --bind-host "$BIND_HOST" \
  --server-host "$SERVER_HOST" \
  --base-port "$BASE_PORT" \
  --requests "$REQUESTS" \
  --payload-bytes "$PAYLOAD_BYTES" \
  --duration-sec "$DURATION_SEC" \
  --concurrency-list "$CONCURRENCY_LIST"
