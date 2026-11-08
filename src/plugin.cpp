#include "plug_abi.h"
#include "http.hpp"
#include "mcp.hpp"
#include "mcp_log.hpp"
#include "tools.hpp"
#include "dialogs.hpp"

#include <windows.h>
#include <string>

BOOL WINAPI DllMain(HMODULE hModule, DWORD reason, LPVOID reserved) {
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        xi::g_hSelf = hModule;
        DisableThreadLibraryCalls(hModule);
    }
    return TRUE;
}

namespace {

const int kPort = 18766;

// UTF-8 -> UTF-16（实例 ID 为 ASCII，这里只做通用转换）。
std::wstring widen(const std::string& s) {
    if (s.empty()) return std::wstring();
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), nullptr, 0);
    if (n <= 0) return std::wstring();
    std::wstring w((size_t)n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), &w[0], n);
    return w;
}

// debugAddString 不会自动换行，这里统一补 CRLF，保证每条横幅各占一行、便于复制。
void addLine(const std::wstring& text) {
    std::wstring line = text;
    line += L"\r\n";
    xi::debugAddString(line.c_str());
}

DWORD WINAPI bootThread(LPVOID) {
    Sleep(6000);

    xi::HELEx h = nullptr;
    if (!xi::getHele(xi::PANE_OUTPUT_DEBUG_EDIT, &h)) {
        xi::logLine(L"boot: 调试输出窗格句柄取不到，IDE 可能尚未就绪");
    } else {
        xi::logLine(L"boot: 调试输出句柄=%p", h);
    }

    bool started = http::start(kPort, [](const std::string& body) {
        return mcp::handle(body);
    });

    if (started) {
        std::wstring banner = L"[XC-MCP-Next] MCP 服务已启动  http://127.0.0.1:";
        banner += std::to_wstring(http::port());
        banner += L"/mcp  instance=";
        banner += widen(http::instanceId());
        addLine(banner);
    } else {
        addLine(L"[XC-MCP-Next] MCP 服务启动失败(端口可能被占用)，详见 xc_mcp_next.log");
    }

    mcp_log::notice(started ? "MCP output pane is active" : "MCP output pane: service start failed");
    return 0;
}

BOOL initEntry(void* funSet, HWND hMainFrame, int ver, int rsv, const wchar_t* entry) {
    xi::g_funSet     = (xi::FunSet)funSet;
    xi::g_hMainFrame = hMainFrame;
    dialogs::install();
    mcp_log::install();
    xi::g_ver        = ver;
    xi::logLine(L"==== %s funSet=%p hMain=%p ver=%d rsv=%d ====",
                entry, funSet, hMainFrame, ver, rsv);
    CloseHandle(CreateThread(nullptr, 0, bootThread, nullptr, 0, nullptr));
    return TRUE;
}

}

extern "C" __declspec(dllexport) BOOL OnInitPlug(void* funSet, HWND hMainFrame, int ver, int rsv) {
    return initEntry(funSet, hMainFrame, ver, rsv, L"OnInitPlug");
}

extern "C" __declspec(dllexport) BOOL OnPlugInit(void* funSet, HWND hMainFrame, int ver, int rsv) {
    return initEntry(funSet, hMainFrame, ver, rsv, L"OnPlugInit");
}

extern "C" __declspec(dllexport) BOOL OnExitPlug() {
    dialogs::shutdown();
    mcp_log::shutdown();
    http::stop();
    xi::logLine(L"OnExitPlug");
    return TRUE;
}

extern "C" __declspec(dllexport) BOOL OnPlugExit() {
    dialogs::shutdown();
    mcp_log::shutdown();
    http::stop();
    xi::logLine(L"OnPlugExit");
    return TRUE;
}
