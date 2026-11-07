#pragma once

#include <cstddef>
#include <string>

// Transport metadata only: never log HTTP headers, arguments or response bodies.
// Worker threads enqueue lines; the IDE UI thread owns every XCGUI operation.
namespace mcp_log {

void install();       // Register the IDE create-view callback on the UI thread.
void shutdown();      // Idempotent; call before http::stop() on the UI thread.

void begin(const std::string& method, const std::string& path, size_t bodyBytes);
void rpc(const std::string& method, const std::string& id, const std::string& tool);
void outcome(const std::string& summary);
void respond(int httpStatus, size_t bodyBytes);
void notice(const std::string& message);

struct Stats {
    bool pane = false;
    bool visible = false;
    int rows = 0;
    size_t pending = 0;
    unsigned long long exchanges = 0;
    unsigned long long lines = 0;      // Lines actually appended by the UI thread.
    unsigned long long dropped = 0;
};
Stats stats();

}
