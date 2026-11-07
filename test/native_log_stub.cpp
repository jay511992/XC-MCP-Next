// Test-only sink for the real HTTP/MCP code. The production mcp_log.cpp is UI-only
// and is not linked into this console test host.
#include "mcp_log.hpp"
#include <fstream>
#include <mutex>
#include <string>
#include <vector>

namespace mcp_log {
namespace {
struct Current {
    std::string request, detail, result;
    size_t bytes = 0;
};
thread_local Current current;
thread_local bool active = false;
std::mutex outputMutex;
unsigned long long sequence = 0;
}

void install() {}
void shutdown() {}
void begin(const std::string& method, const std::string& path, size_t bytes) {
    current = Current();
    current.request = method + " " + path;
    current.bytes = bytes;
    active = true;
}
void rpc(const std::string& method, const std::string& id, const std::string& tool) {
    if (!active) return;
    if (!current.detail.empty()) current.detail += " | ";
    current.detail += method + " id=" + (id.empty() ? "notification" : id);
    if (!tool.empty()) current.detail += " tool=" + tool;
}
void outcome(const std::string& summary) { if (active) current.result = summary; }
void respond(int status, size_t responseBytes) {
    std::lock_guard<std::mutex> lock(outputMutex);
    const char* path = getenv("XCMCP_NATIVE_LOG");
    if (path) {
        std::ofstream out(path, std::ios::app | std::ios::binary);
        out << "#" << ++sequence << " <- " << current.request << " body=" << current.bytes
            << "B " << current.detail << " -> HTTP " << status << " body=" << responseBytes
            << "B " << current.result << "\n";
    }
    active = false;
}
void notice(const std::string&) {}
Stats stats() { return {}; }
}
