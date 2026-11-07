#pragma once

#include <string>
#include <functional>

namespace http {

using Handler = std::function<std::string(const std::string& body)>;

// XC_MCP_PORT unset: prefer port, fall back to a free port when occupied.
// XC_MCP_PORT=0: ask the OS for a port; explicit nonzero: fail on conflict.
bool start(int port, Handler handler);
// Handler-initiated stop preserves its response; final stop joins all workers.
void stop(bool fromHandler = false);
bool running();
int port();
std::string instanceId();
// Legacy compatibility metadata only; token authentication is disabled and this
// value is never compared with request headers.
std::string token();

}
