#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

usage() {
  cat <<'EOF'
Usage: build_all.sh [--release|--debug]

Builds both bench and bench_multiple_machines with CMake.
Defaults to Release if no mode is provided.
EOF
}

BUILD_TYPE="Release"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release)
      BUILD_TYPE="Release"
      shift
      ;;
    --debug)
      BUILD_TYPE="Debug"
      shift
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

# Ensure submodules are present (no-op if already initialized).
git -C "$ROOT_DIR" submodule update --init --recursive

# Build bench
cmake -S "$ROOT_DIR/bench" -B "$ROOT_DIR/bench/build" -DCMAKE_BUILD_TYPE="$BUILD_TYPE"
cmake --build "$ROOT_DIR/bench/build" -j

# Build bench_multiple_machines
cmake -S "$ROOT_DIR/bench_multiple_machines" -B "$ROOT_DIR/bench_multiple_machines/build" -DCMAKE_BUILD_TYPE="$BUILD_TYPE"
cmake --build "$ROOT_DIR/bench_multiple_machines/build" -j

printf "\nBuild complete (%s).\n" "$BUILD_TYPE"
