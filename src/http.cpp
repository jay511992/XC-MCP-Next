#include "http.hpp"
#include "instance.hpp"
#include "plug_abi.h"
#include "mcp_log.hpp"

#include <winsock2.h>
#include <ws2tcpip.h>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <mutex>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

#pragma comment(lib, "ws2_32.lib")

namespace http {
namespace {

struct Server {
    Server(int port, Handler fn) : identity(port), handler(std::move(fn)) {}
    instance::Identity identity;
    Handler handler;
    std::atomic<SOCKET> listener{INVALID_SOCKET};
    std::atomic<bool> run{false};
    HANDLE acceptThread = nullptr;
    std::mutex workersMutex;
    std::set<SOCKET> sockets;
    std::vector<HANDLE> workers;
};

std::mutex g_lifecycle;
std::mutex g_finalStop;
// One IDE has one execution lock, including overlapping stop/start generations.
std::mutex g_dispatchMutex;
std::shared_ptr<Server> g_current;
std::vector<std::shared_ptr<Server>> g_servers;
Handler g_handler;
bool g_wsa = false;
bool g_shuttingDown = false;
// Keep status/stop calls attached to the request's own server generation.
thread_local Server* g_requestServer = nullptr;

std::string lower(std::string s) {
    for (char& c : s) if (c >= 'A' && c <= 'Z') c = (char)(c - 'A' + 'a');
    return s;
}

std::string trim(const std::string& s) {
    size_t a = s.find_first_not_of(" \t\r\n");
    return a == std::string::npos ? std::string() : s.substr(a, s.find_last_not_of(" \t\r\n") - a + 1);
}

struct Headers {
    std::vector<std::pair<std::string, std::string>> kv;
    void parse(const std::string& head) {
        size_t p = head.find("\r\n");
        if (p == std::string::npos) return;
        p += 2;
        while (p < head.size()) {
            size_t e = head.find("\r\n", p);
            if (e == std::string::npos) e = head.size();
            size_t c = head.find(':', p);
            if (c != std::string::npos && c < e)
                kv.emplace_back(lower(head.substr(p, c - p)), trim(head.substr(c + 1, e - c - 1)));
            p = e + 2;
        }
    }
    const std::string* find(const char* name) const {
        for (const auto& entry : kv) if (entry.first == name) return &entry.second;
        return nullptr;
    }
    std::string get(const char* name) const {
        const auto* value = find(name);
        return value ? *value : std::string();
    }
    bool has(const char* name) const { return find(name) != nullptr; }
};

bool sendAll(SOCKET s, const char* data, int len) {
    for (int sent = 0; sent < len;) {
        int n = ::send(s, data + sent, len - sent, 0);
        if (n <= 0) return false;
        sent += n;
    }
    return true;
}

void sendResponse(SOCKET s, int code, const char* text, const std::string& body,
                  bool jsonBody, bool keepAlive) {
    mcp_log::respond(code, body.size());
    char head[640];
    int len = sprintf_s(head, sizeof(head),
        "HTTP/1.1 %d %s\r\n"
        "Content-Type: %s\r\n"
        "Content-Length: %d\r\n"
        "X-Content-Type-Options: nosniff\r\n"
        "Cache-Control: no-store\r\n"
        "Connection: %s\r\n%s\r\n",
        code, text, jsonBody ? "application/json; charset=utf-8" : "text/plain; charset=utf-8",
        (int)body.size(), keepAlive ? "keep-alive" : "close",
        code == 405 ? "Allow: POST, OPTIONS\r\n" : "");
    if (len <= 0) return;
    if (body.size() <= 16u * 1024u) {
        std::string out(head, (size_t)len);
        out += body;
        sendAll(s, out.data(), (int)out.size());
    } else if (sendAll(s, head, len)) {
        sendAll(s, body.data(), (int)body.size());
    }
}

struct Request {
    std::string method, target, version, body;
    Headers headers;
    bool keepAlive = false;
    bool bodyUntilEof = false;
};

// select supplies a short cancellation point without shortening the 15s idle
// timeout advertised by keep-alive. Only the worker closes its connection.
int receive(Server& server, SOCKET socket, char* buffer, int size) {
    ULONGLONG deadline = GetTickCount64() + 15000;
    while (server.run && GetTickCount64() < deadline) {
        fd_set read;
        FD_ZERO(&read);
        FD_SET(socket, &read);
        timeval wait = {0, 200000};
        int ready = select(0, &read, nullptr, nullptr, &wait);
        if (!server.run || ready < 0) return -1;
        if (ready > 0) return recv(socket, buffer, size, 0);
    }
    return -1;
}

bool readRequest(Server& server, SOCKET s, std::string& buf, Request& rq) {
    size_t headerEnd = std::string::npos, contentLen = 0;
    bool haveLen = false;
    for (;;) {
        if (headerEnd == std::string::npos) {
            size_t p = buf.find("\r\n\r\n");
            if (p != std::string::npos) {
                if (p > 64u * 1024u) {
                    sendResponse(s, 431, "Request Header Fields Too Large", "request header too large", false, false);
                    return false;
                }
                headerEnd = p + 4;
                rq.headers.parse(buf.substr(0, headerEnd));
                if (rq.headers.has("transfer-encoding")) {
                    sendResponse(s, 400, "Bad Request", "Transfer-Encoding is not supported; use Content-Length", false, false);
                    return false;
                }
                for (const auto& h : rq.headers.kv) {
                    if (h.first != "content-length") continue;
                    if (haveLen || h.second.empty() || h.second.find_first_not_of("0123456789") != std::string::npos) {
                        sendResponse(s, 400, "Bad Request", "invalid Content-Length", false, false);
                        return false;
                    }
                    haveLen = true;
                    unsigned long long n = _strtoui64(h.second.c_str(), nullptr, 10);
                    if (n > 4u * 1024u * 1024u) {
                        sendResponse(s, 413, "Payload Too Large", "payload too large (max 4MB)", false, false);
                        return false;
                    }
                    contentLen = (size_t)n;
                }
            } else if (buf.size() > 64u * 1024u) {
                sendResponse(s, 431, "Request Header Fields Too Large", "request header too large", false, false);
                return false;
            }
        }
        if (headerEnd != std::string::npos && (!haveLen || buf.size() - headerEnd >= contentLen)) break;
        char tmp[8192];
        int n = receive(server, s, tmp, (int)sizeof(tmp));
        if (n <= 0) return false;
        buf.append(tmp, n);
    }
    size_t lineEnd = buf.find("\r\n");
    std::string first = buf.substr(0, lineEnd);
    size_t a = first.find(' '), b = a == std::string::npos ? a : first.find(' ', a + 1);
    if (a == std::string::npos || b == std::string::npos) {
        sendResponse(s, 400, "Bad Request", "invalid request line", false, false);
        return false;
    }
    rq.method = first.substr(0, a);
    rq.target = first.substr(a + 1, b - a - 1);
    rq.version = first.substr(b + 1);
    size_t bodyLen = haveLen ? contentLen : 0;
    if (!haveLen && rq.method == "POST") {
        bodyLen = buf.size() - headerEnd;
        rq.bodyUntilEof = true;  // Legacy unframed POST: one response, then close.
    }
    rq.body.assign(buf, headerEnd, bodyLen);
    buf.erase(0, headerEnd + bodyLen);
    std::string connection = lower(rq.headers.get("connection"));
    rq.keepAlive = rq.version == "HTTP/1.1" ? connection != "close" : connection == "keep-alive";
    return true;
}

void handleRequest(Server& server, SOCKET s, const Request& rq, bool& closeConn) {
    std::string path = rq.target.substr(0, rq.target.find('?'));
    mcp_log::begin(rq.method, path, rq.body.size());
    closeConn = true;
    if (rq.headers.has("origin")) {
        sendResponse(s, 403, "Forbidden", "forbidden: request carries an Origin header", false, false);
        return;
    }
    std::string port = std::to_string(server.identity.port);
    std::string host = lower(rq.headers.get("host"));
    if (host != "127.0.0.1:" + port && host != "localhost:" + port) {
        sendResponse(s, 403, "Forbidden", "forbidden: Host header not allowed", false, false);
        return;
    }
    // Token authentication is disabled by design. Legacy X-XC-Token and
    // Authorization headers are accepted but ignored; loopback binding, Host,
    // Origin, instance ID and project guards remain enforced.
    if (rq.headers.has("x-xc-instance") && rq.headers.get("x-xc-instance") != server.identity.id) {
        sendResponse(s, 409, "Conflict", "target IDE instance changed; explicitly reconnect to select a new instance", false, false);
        return;
    }
    if (path != "/mcp" && path != "/health") {
        sendResponse(s, 404, "Not Found", "not found", false, false);
        return;
    }
    std::lock_guard<std::mutex> dispatch(g_dispatchMutex);
    if (!server.run) {
        sendResponse(s, 503, "Service Unavailable", "this IDE MCP instance has stopped", false, false);
        return;
    }
    if (rq.headers.has("x-xc-project") &&
        !instance::matchesProject(rq.headers.get("x-xc-project"), instance::currentProjectFile())) {
        sendResponse(s, 409, "Conflict", "target project changed or is not loaded in this IDE; request was not executed", false, false);
        return;
    }
    if (rq.method == "OPTIONS") {
        closeConn = !rq.keepAlive;
        sendResponse(s, 204, "No Content", "", false, !closeConn);
        return;
    }
    if (rq.method == "GET" && path == "/health") {
        js::Value snapshot = server.identity.info();
        const js::Value* project = snapshot.find("projectFile");
        if (rq.headers.has("x-xc-project") && (!project ||
            !instance::matchesProject(rq.headers.get("x-xc-project"), project->asStr()))) {
            sendResponse(s, 409, "Conflict", "project changed while reading health; snapshot discarded", false, false);
            return;
        }
        closeConn = !rq.keepAlive;
        sendResponse(s, 200, "OK", snapshot.dump(), true, !closeConn);
        return;
    }
    if (rq.method != "POST" || path != "/mcp") {
        // A stateless JSON-response MCP endpoint does not offer a GET SSE stream.
        sendResponse(s, 405, "Method Not Allowed", "use POST /mcp or GET /health", false, false);
        return;
    }
    if (lower(rq.headers.get("content-type")).find("application/json") == std::string::npos) {
        sendResponse(s, 415, "Unsupported Media Type", "content-type must be application/json", false, false);
        return;
    }
    std::string out;
    try {
        if (server.handler) out = server.handler(rq.body);
    } catch (const std::exception& e) {
        sendResponse(s, 500, "Internal Server Error", std::string("internal error: ") + e.what(), false, false);
        return;
    } catch (...) {
        sendResponse(s, 500, "Internal Server Error", "internal error", false, false);
        return;
    }
    // Also avoid reporting another project's data if the user changed the IDE
    // project during a call. This is routing protection, not a filesystem sandbox.
    if (rq.headers.has("x-xc-project") &&
        !instance::matchesProject(rq.headers.get("x-xc-project"), instance::currentProjectFile())) {
        sendResponse(s, 409, "Conflict", "project changed during the request; result discarded, inspect IDE before retrying", false, false);
        return;
    }
    closeConn = !rq.keepAlive || rq.bodyUntilEof || !server.run;
    sendResponse(s, out.empty() ? 202 : 200, out.empty() ? "Accepted" : "OK", out, !out.empty(), !closeConn);
}

struct Connection {
    std::shared_ptr<Server> server;
    SOCKET socket;
};

DWORD WINAPI connProc(LPVOID opaque) {
    std::unique_ptr<Connection> connection((Connection*)opaque);
    Server& server = *connection->server;
    SOCKET s = connection->socket;
    g_requestServer = &server;
    DWORD timeout = 15000;
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, (const char*)&timeout, sizeof(timeout));
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, (const char*)&timeout, sizeof(timeout));
    try {
        std::string buf;
        buf.reserve(8192);
        for (int i = 0; i < 1000 && server.run; ++i) {
            Request rq;
            if (!readRequest(server, s, buf, rq)) break;
            if (!server.run) break;
            {
                std::lock_guard<std::mutex> lock(server.workersMutex);
                if (server.sockets.size() > 64 || i == 999) rq.keepAlive = false;
            }
            bool closeConn = false;
            handleRequest(server, s, rq, closeConn);
            if (closeConn) break;
        }
    } catch (...) {
        // Never let exceptions cross the native thread entry and terminate IDE.
    }
    shutdown(s, SD_SEND);
    DWORD drainTimeout = 500;
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, (const char*)&drainTimeout, sizeof(drainTimeout));
    char drain[4096];
    for (int i = 0; i < 32 && server.run; ++i) if (recv(s, drain, sizeof(drain), 0) <= 0) break;
    {
        std::lock_guard<std::mutex> lock(server.workersMutex);
        server.sockets.erase(s);
        closesocket(s);
    }
    g_requestServer = nullptr;
    return 0;
}

void reapWorkers(Server& server) {  // Caller owns workersMutex.
    for (auto i = server.workers.begin(); i != server.workers.end();) {
        if (WaitForSingleObject(*i, 0) == WAIT_OBJECT_0) {
            CloseHandle(*i);
            i = server.workers.erase(i);
        } else ++i;
    }
}

DWORD WINAPI acceptProc(LPVOID opaque) {
    std::unique_ptr<std::shared_ptr<Server>> owner((std::shared_ptr<Server>*)opaque);
    auto server = *owner;
    while (server->run) {
        SOCKET listener = server->listener.load();
        if (listener == INVALID_SOCKET) break;
        fd_set read;
        FD_ZERO(&read);
        FD_SET(listener, &read);
        timeval wait = {0, 200000};
        int ready = select(0, &read, nullptr, nullptr, &wait);
        if (!server->run) break;
        if (ready <= 0) continue;
        SOCKET s = ::accept(listener, nullptr, nullptr);
        if (s == INVALID_SOCKET) {
            if (!server->run) break;
            Sleep(20);
            continue;
        }
        u_long blocking = 0;
        if (ioctlsocket(s, FIONBIO, &blocking) != 0) { closesocket(s); continue; }
        int nodelay = 1;
        setsockopt(s, IPPROTO_TCP, TCP_NODELAY, (const char*)&nodelay, sizeof(nodelay));
        // Hand the socket off immediately. An idle client must not block accept().
        try {
            std::lock_guard<std::mutex> lock(server->workersMutex);
            reapWorkers(*server);
            if (!server->run || server->sockets.size() >= 128) {
                closesocket(s);
                continue;
            }
            auto args = std::make_unique<Connection>();
            args->server = server;
            args->socket = s;
            server->sockets.insert(s);
            // Reserve before creating a thread: never lose a live thread handle
            // if vector growth fails after CreateThread succeeds.
            server->workers.reserve(server->workers.size() + 1);
            HANDLE thread = CreateThread(nullptr, 0, connProc, args.get(), 0, nullptr);
            if (thread) {
                args.release();
                server->workers.push_back(thread);
            } else {
                server->sockets.erase(s);
                closesocket(s);
            }
        } catch (...) {
            std::lock_guard<std::mutex> lock(server->workersMutex);
            server->sockets.erase(s);
            closesocket(s);
        }
    }
    SOCKET listener = server->listener.exchange(INVALID_SOCKET);
    if (listener != INVALID_SOCKET) closesocket(listener);
    return 0;
}

SOCKET bindPort(int requested, bool fallback, int& actual) {
    SOCKET s = ::socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s == INVALID_SOCKET) throw std::runtime_error("socket failed");
    BOOL exclusive = TRUE;
    if (setsockopt(s, SOL_SOCKET, SO_EXCLUSIVEADDRUSE, (const char*)&exclusive, sizeof(exclusive)) != 0) {
        closesocket(s);
        throw std::runtime_error("SO_EXCLUSIVEADDRUSE failed");
    }
    sockaddr_in address = {};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = htons((u_short)requested);
    if (::bind(s, (sockaddr*)&address, sizeof(address)) == SOCKET_ERROR) {
        int error = WSAGetLastError();
        closesocket(s);
        if (fallback && requested != 0 && (error == WSAEADDRINUSE || error == WSAEACCES))
            return bindPort(0, false, actual);
        throw std::runtime_error("cannot bind requested MCP port: " + std::to_string(error));
    }
    int size = sizeof(address);
    if (getsockname(s, (sockaddr*)&address, &size) != 0 || ::listen(s, SOMAXCONN) != 0) {
        closesocket(s);
        throw std::runtime_error("cannot listen on MCP port");
    }
    u_long nonblocking = 1;
    if (ioctlsocket(s, FIONBIO, &nonblocking) != 0) {
        closesocket(s);
        throw std::runtime_error("cannot configure MCP listener");
    }
    actual = ntohs(address.sin_port);
    return s;
}

void stopListening(Server& server) {  // g_lifecycle held, no waits here.
    server.run = false;
    server.identity.unpublish();
}

}

bool start(int preferredPort, Handler handler) {
    std::lock_guard<std::mutex> lock(g_lifecycle);
    if (g_shuttingDown) return false;
    if (g_current && g_current->run) return true;
    if (handler) g_handler = std::move(handler);
    if (!g_handler) return false;
    SOCKET listener = INVALID_SOCKET;
    std::shared_ptr<Server> server;
    try {
        std::wstring setting = instance::environment(L"XC_MCP_PORT");
        bool fallback = setting.empty();
        int requested = preferredPort;
        if (!setting.empty()) {
            if (setting.find_first_not_of(L"0123456789") != std::wstring::npos)
                throw std::runtime_error("XC_MCP_PORT must be an integer from 0 to 65535");
            unsigned long long value = _wcstoui64(setting.c_str(), nullptr, 10);
            if (value > 65535) throw std::runtime_error("XC_MCP_PORT must be from 0 to 65535");
            requested = (int)value;
        }
        if (requested < 0 || requested > 65535) throw std::runtime_error("invalid preferred MCP port");
        if (!g_wsa) {
            WSADATA wsa;
            if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) throw std::runtime_error("WSAStartup failed");
            g_wsa = true;
        }
        int actual = 0;
        listener = bindPort(requested, fallback, actual);
        server = std::make_shared<Server>(actual, g_handler);
        server->listener = listener;
        server->identity.publish();
        auto arg = std::make_unique<std::shared_ptr<Server>>(server);
        g_servers.push_back(server);
        server->run = true;
        server->acceptThread = CreateThread(nullptr, 0, acceptProc, arg.get(), 0, nullptr);
        if (!server->acceptThread) {
            server->run = false;
            g_servers.pop_back();
            throw std::runtime_error("cannot start MCP listener thread");
        }
        arg.release();
        g_current = server;
        xi::logLine(L"http: listening on http://127.0.0.1:%d/mcp, instance=%hs (no token auth)",
                    actual, server->identity.id.c_str());
        mcp_log::notice("MCP service started on http://127.0.0.1:" + std::to_string(actual) + "/mcp");
        return true;
    } catch (const std::exception& error) {
        if (server) { server->identity.unpublish(); server->listener = INVALID_SOCKET; }
        if (listener != INVALID_SOCKET) closesocket(listener);
        xi::logLine(L"http: start failed: %hs", error.what());
        mcp_log::notice("MCP service start failed: " + std::string(error.what()));
        return false;
    }
}

void stop(bool fromHandler) {
    if (fromHandler) {
        std::lock_guard<std::mutex> lock(g_lifecycle);
        Server* server = g_requestServer ? g_requestServer : g_current.get();
        if (server) {
            mcp_log::notice("MCP service stop requested for instance " + server->identity.id);
            stopListening(*server);
        }
        return;  // Own response must be sent before its socket closes.
    }
    std::lock_guard<std::mutex> finalStop(g_finalStop);
    std::vector<std::shared_ptr<Server>> servers;
    {
        std::lock_guard<std::mutex> lock(g_lifecycle);
        g_shuttingDown = true;
        servers = g_servers;
        for (auto& server : servers) stopListening(*server);
    }
    // Do not hold g_lifecycle while joining: a handler may be reading status or
    // stopping itself. Include generations previously stopped by xc_server.
    for (auto& server : servers) {
        if (server->acceptThread) {
            WaitForSingleObject(server->acceptThread, INFINITE);
            CloseHandle(server->acceptThread);
            server->acceptThread = nullptr;
        }
        {
            std::lock_guard<std::mutex> lock(server->workersMutex);
            for (SOCKET s : server->sockets) shutdown(s, SD_BOTH);
        }
        for (HANDLE worker : server->workers) {
            WaitForSingleObject(worker, INFINITE);
            CloseHandle(worker);
        }
        server->workers.clear();
    }
    {
        std::lock_guard<std::mutex> lock(g_lifecycle);
        g_servers.clear();
        if (g_wsa) { WSACleanup(); g_wsa = false; }
        g_shuttingDown = false;
    }
    xi::logLine(L"http: stopped this IDE instance");
}

bool running() {
    if (g_requestServer) return g_requestServer->run;
    std::lock_guard<std::mutex> lock(g_lifecycle);
    return g_current && g_current->run;
}

int port() {
    if (g_requestServer) return g_requestServer->identity.port;
    std::lock_guard<std::mutex> lock(g_lifecycle);
    return g_current ? g_current->identity.port : 18766;
}

std::string instanceId() {
    if (g_requestServer) return g_requestServer->identity.id;
    std::lock_guard<std::mutex> lock(g_lifecycle);
    return g_current ? g_current->identity.id : std::string();
}

std::string token() {
    if (g_requestServer) return g_requestServer->identity.token;
    std::lock_guard<std::mutex> lock(g_lifecycle);
    return g_current ? g_current->identity.token : std::string();
}

}
