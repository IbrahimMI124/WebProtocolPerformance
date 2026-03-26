// =====================================================
// FILE 1: latency_benchmark.cpp
// Records RTT latency into CSV
// =====================================================

#include <iostream>
#include <fstream>
#include <chrono>
#include <vector>
#include <string>

using namespace std;

void run_latency_test(const string &protocol) {
    ofstream file(protocol + "_latency.csv");
    file << "latency_ms\n";

    for (int i = 0; i < 100; i++) {
        auto start = chrono::high_resolution_clock::now();

        // ---- SIMULATE SEND/RECEIVE ----
        // Replace this with actual protocol call
        this_thread::sleep_for(chrono::milliseconds(1));

        auto end = chrono::high_resolution_clock::now();

        double latency = chrono::duration<double, milli>(end - start).count();
        file << latency << "\n";
    }

    file.close();
}

int main() {
    run_latency_test("REST");
    run_latency_test("WebSocket");
    run_latency_test("WebRTC");
}


// =====================================================
// FILE 2: throughput_benchmark.cpp
// Measures bytes/sec and logs to CSV
// =====================================================

#include <iostream>
#include <fstream>
#include <chrono>
#include <thread>

using namespace std;

void run_throughput_test(const string &protocol) {
    ofstream file(protocol + "_throughput.csv");
    file << "bytes_per_sec\n";

    int bytes_sent = 0;
    auto start = chrono::high_resolution_clock::now();

    while (true) {
        auto now = chrono::high_resolution_clock::now();
        double elapsed = chrono::duration<double>(now - start).count();

        if (elapsed > 5.0) break;

        // ---- SIMULATE SEND ----
        bytes_sent += 4; // "ping"
    }

    double throughput = bytes_sent / 5.0;
    file << throughput << "\n";

    file.close();
}

int main() {
    run_throughput_test("REST");
    run_throughput_test("WebSocket");
    run_throughput_test("WebRTC");
}


// =====================================================
// FILE 3: setup_time_benchmark.cpp
// Measures connection/setup latency
// =====================================================

#include <iostream>
#include <fstream>
#include <chrono>
#include <thread>

using namespace std;

void run_setup_test(const string &protocol) {
    ofstream file(protocol + "_setup.csv");
    file << "setup_time_ms\n";

    auto start = chrono::high_resolution_clock::now();

    // ---- SIMULATE SETUP ----
    // Replace with actual connect/handshake
    if (protocol == "REST") {
        this_thread::sleep_for(chrono::milliseconds(5));
    } else if (protocol == "WebSocket") {
        this_thread::sleep_for(chrono::milliseconds(20));
    } else {
        this_thread::sleep_for(chrono::milliseconds(100));
    }

    auto end = chrono::high_resolution_clock::now();

    double setup_time = chrono::duration<double, milli>(end - start).count();
    file << setup_time << "\n";

    file.close();
}

int main() {
    run_setup_test("REST");
    run_setup_test("WebSocket");
    run_setup_test("WebRTC");
}
