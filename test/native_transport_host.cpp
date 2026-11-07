// Standalone test host: real HTTP/identity/MCP implementation, fake IDE ABI.
// It never loads XCGUI or opens a real IDE/project. All state belongs to the test.
#include "http.hpp"
#include "instance.hpp"
#include "mcp.hpp"
#include "plug_abi.h"
#include "tools.hpp"
#include <cstdarg>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <atomic>

namespace {
std::filesystem::path statePath;
std::atomic<int> readsBeforeSwitch{-1};
std::atomic<int> activeTools{0}, maxActiveTools{0};
struct ActiveTool {
    ActiveTool() {
        int n = ++activeTools, old = maxActiveTools.load();
        while (n > old && !maxActiveTools.compare_exchange_weak(old, n)) {}
    }
    ~ActiveTool() { --activeTools; }
};

js::Value status() {
    js::Value v = js::Value::obj();
    v.set("ok", true);
    v.set("pid", (int)GetCurrentProcessId());
    v.set("instanceId", http::instanceId());
    v.set("port", http::port());
    v.set("httpRunning", http::running());
    v.set("project_file", instance::currentProjectFile());
    v.set("maxActiveTools", maxActiveTools.load());
    return v;
}
}

namespace xi {
HWND g_hMainFrame = nullptr;
HWND mainHwnd() { return nullptr; }

int getProjectRaw(void** p1, void** p2, void** p3) {
    // Stable per-thread return storage matches the IDE ABI's pointer results.
    thread_local std::wstring name, file, dir;
    std::ifstream input(statePath, std::ios::binary);
    std::string text;
    std::getline(input, text);
    int left = readsBeforeSwitch.load();
    if (left == 0) {
        std::string second;
        if (std::getline(input, second) && !second.empty()) text = second;
    } else if (left > 0) {
        --readsBeforeSwitch;
    }
    file = instance::wide(text);
    auto path = std::filesystem::path(file);
    name = path.stem().wstring();
    dir = path.parent_path().wstring();
    if (p1) *p1 = name.empty() ? nullptr : (void*)name.c_str();
    if (p2) *p2 = file.empty() ? nullptr : (void*)file.c_str();
    if (p3) *p3 = dir.empty() ? nullptr : (void*)dir.c_str();
    return 0;
}

void logLine(const wchar_t* fmt, ...) {
    va_list args;
    va_start(args, fmt);
    vfwprintf(stderr, fmt, args);
    va_end(args);
    fputwc(L'\n', stderr);
}
}

namespace tools {
js::Value listCached() {
    js::Value list = js::Value::arr();
    for (const char* name : {"xc_status", "xc_get_project", "xc_server"}) {
        js::Value item = js::Value::obj();
        item.set("name", std::string(name));
        item.set("inputSchema", js::Value::obj());
        list.push(item);
    }
    return list;
}

std::string call(const std::string& name, const js::Value& args, bool& isError) {
    ActiveTool active;
    isError = false;
    if (name == "test_throw") throw std::runtime_error("test exception");
    if (name == "xc_server" && args.find("action")) {
        if (args.find("action")->asStr() == "stop") http::stop(true);
        if (args.find("action")->asStr() == "start") http::start(http::port(), nullptr);
    }
    if (name == "test_pause") {
        std::ofstream(std::filesystem::path(statePath.wstring() + L".pause")) << "paused";
        Sleep(600);
    }
    return status().dump();
}
}

int wmain(int argc, wchar_t** argv) {
    std::wstring gate = instance::environment(L"XCMCP_TEST_GATE");
    while (!gate.empty() && !std::filesystem::exists(gate)) Sleep(2);
    if (argc < 2) return 2;
    statePath = argv[1];
    int preferred = argc > 2 ? _wtoi(argv[2]) : 18766;
    if (!http::start(preferred, mcp::handle)) { http::stop(); return 3; }
    std::cout << status().dump() << std::endl;
    std::string line;
    while (std::getline(std::cin, line)) {
        if (line == "quit") break;
        if (line == "arm-health-switch") {
            std::string reply = status().dump();
            readsBeforeSwitch = 1;
            std::cout << reply << std::endl;
            continue;
        }
        if (line == "stop" || line == "restart") http::stop();
        if (line == "start" || line == "restart") {
            if (!http::start(preferred, nullptr)) return 4;
        }
        std::cout << status().dump() << std::endl;
    }
    http::stop();
    return 0;
}
