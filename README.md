# WebProtocolPerformance

## Overview
WebProtocolPerformance is a C++ and Python-based benchmarking suite designed to evaluate and compare the performance characteristics of modern web communication protocols. The project focuses on measuring **Startup Time**, **Latency (RTT)**, and **Throughput** across three distinct protocols:

1. **REST (HTTP)** - Implemented via [`cpp-httplib`](https://github.com/yhirose/cpp-httplib)
2. **WebSocket** - Implemented via [`uWebSockets`](https://github.com/uNetworking/uWebSockets) (server) and a minimal RFC6455 client
3. **WebRTC DataChannel** - Implemented via [`libdatachannel`](https://github.com/paullouisageneau/libdatachannel)

This project includes automated benchmarking scripts, multi-run variance analysis, and data visualization tools, alongside project papers detailing the methodology and findings.

## Folder Structure

- **`bench/`**: The core directory containing all source code, build scripts, and benchmarking harnesses.
  - **`src/`**: Contains the C++ server and client implementations for each protocol (`rest_client.cpp`, `webrtc_server.cpp`, `ws_server.cpp`, etc.).
  - **`CMakeLists.txt`**: CMake configuration for building the benchmark binaries.
  - **`run_bench.py`**: Python script to execute the benchmark suite and generate JSON/CSV results.
  - **`repeat_bench.py`**: Script to run benchmarks multiple times to measure statistical variance and consistency.
  - **`plot_results.py`**: Generates visual plots (using matplotlib/pandas) from the benchmark CSV outputs.
  - **`README.md`**: Detailed instructions for building and running the benchmark scripts.
- **`cpp-httplib/`**: Git submodule for the HTTP/REST library.
- **`libdatachannel/`**: Git submodule for the WebRTC DataChannel library.
- **`uWebSockets/`**: Git submodule for the WebSocket server library.
- **`papers/`**: Contains reference academic papers and related literature.
- **`ssp_project_paper_v1.pdf` / `ssp_project_final.pdf`**: The project's academic research reports detailing the benchmarking methodology and final findings.
- **`test.cpp`**: A preliminary mock script demonstrating the structure of latency and throughput measurements (simulated).

## What's Happening Under the Hood?

The benchmarking process is divided into C++ binary execution and Python orchestration:

1. **Protocol Implementation**: For each protocol, a dedicated client and server binary is built from the `bench/src` directory. They are designed to be minimal to reduce framework overhead and focus strictly on protocol performance.
2. **Measurement Metrics**:
    - **Startup Time**: Measures the duration from spawning the server process to the client successfully completing its connection/handshake.
    - **Latency (RTT)**: The client sends a payload and waits for the server to echo it back. The round-trip time is recorded over `N` iterations to generate a distribution of latencies.
    - **Throughput**: Measures how much data (bytes per second) can be reliably transmitted over a fixed duration using a continuous ping-pong echo.
3. **Automation & Aggregation**: The Python scripts (`run_bench.py`, `repeat_bench.py`) orchestrate starting the servers, launching the clients with specific parameters (payload size, request count), capturing standard output, and parsing it into structured CSV/JSON formats.
4. **Analysis**: Once data is collected, `plot_results.py` consumes the CSV files to generate visual graphs, making it easy to compare the overhead and efficiency of REST vs WebSocket vs WebRTC.

## Getting Started

To build and run the benchmarks, you will need `cmake`, a C++ compiler, and Python 3.

### 1. Initialize Submodules
Since the project relies on external libraries, initialize the submodules first:
```bash
git submodule update --init --recursive
```

### 2. Build the Binaries
Use CMake to compile the client and server binaries:
```bash
cmake -S bench -B bench/build -DCMAKE_BUILD_TYPE=Release
cmake --build bench/build -j
```

### 3. Run the Benchmarks
Run the automated Python harness to collect data (this produces `bench/out/results.json` and `bench/out/results.csv`):
```bash
python3 bench/run_bench.py --bin-dir bench/build --out-dir bench/out
```

### 4. Plot Results
Ensure you have the required Python dependencies installed (`python3 -m pip install -r bench/requirements.txt`), then generate plots:
```bash
python3 bench/plot_results.py --in-dir bench/out --out-dir bench/out/plots
```

*For more detailed execution instructions, including running multi-pass variance tests, refer to [bench/README.md](bench/README.md).*
