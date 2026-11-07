#include "mcp_log.hpp"
#include "plug_abi.h"
#include "xcgui_min.h"

#include <atomic>
#include <cstdio>
#include <deque>
#include <mutex>
#include <sstream>
#include <utility>
#include <vector>

namespace mcp_log {
namespace {

constexpr UINT kFlushMessage = WM_APP + 0x4D43; // Private "MC" message, below WM_USER range limits.
constexpr int kPaneId = 1876602;
constexpr size_t kMaxQueuedLines = 4096;
constexpr int kMaxEditorRows = 2000;
constexpr size_t kMaxDetailBytes = 360;
constexpr int kMaxUiBatch = 128;

struct Exchange {
    unsigned long long id = 0;
    ULONGLONG started = 0;
    SYSTEMTIME local = {};
    std::string method, path, outcome;
    size_t bodyBytes = 0;
    std::vector<std::string> calls;
};

std::mutex g_mutex;
std::deque<std::string> g_pending;
std::atomic<HELE> g_pane{nullptr};
std::atomic<HELE> g_edit{nullptr};
std::atomic<bool> g_closing{true};
std::atomic<bool> g_postPending{false};
std::atomic<bool> g_visible{false};
std::atomic<int> g_rows{0};
bool g_showPane = false; // UI thread only: show after the host registers the pane.
std::atomic<unsigned long long> g_exchanges{0};
std::atomic<unsigned long long> g_lines{0};
std::atomic<unsigned long long> g_dropped{0};
std::atomic<unsigned long long> g_nextId{0};
thread_local Exchange g_exchange;
thread_local bool g_active = false;

std::string oneLine(const std::string& input, size_t maxBytes) {
    std::string out;
    out.reserve((std::min)(input.size(), maxBytes));
    for (unsigned char c : input) {
        if (c == '\r' || c == '\n' || c == '\t') out += ' ';
        else if (c >= 0x20) out += (char)c;
        if (out.size() >= maxBytes) break;
    }
    if (input.size() > maxBytes) out += "...";
    return out;
}

std::wstring wide(const std::string& input) {
    if (input.empty()) return {};
    int flags = 0; // Invalid input must not prevent an MCP response.
    int n = MultiByteToWideChar(CP_UTF8, flags, input.data(), (int)input.size(), nullptr, 0);
    if (n <= 0) return L"[MCP log: invalid UTF-8]";
    std::wstring out((size_t)n, L'\0');
    MultiByteToWideChar(CP_UTF8, flags, input.data(), (int)input.size(), &out[0], n);
    return out;
}

std::string timestamp(const SYSTEMTIME& t) {
    char text[32];
    sprintf_s(text, "%02u:%02u:%02u.%03u", t.wHour, t.wMinute, t.wSecond, t.wMilliseconds);
    return text;
}

void enqueue(std::string line) {
    // Logging must never affect request execution or bring down the host IDE.
    try {
        std::lock_guard<std::mutex> lock(g_mutex);
        if (g_closing) return;
        if (g_pending.size() >= kMaxQueuedLines) {
            g_pending.pop_front();
            ++g_dropped;
        }
        g_pending.push_back(std::move(line));
    } catch (...) {
    }
}

void schedule() {
    if (g_closing || !g_pane.load() || !xi::g_hMainFrame) return;
    bool expected = false;
    if (!g_postPending.compare_exchange_strong(expected, true)) return;
    // XC_PostMessage does not wait for the UI thread. A queue swap in the window
    // callback means there are no pointers to worker-owned data in the message.
    if (!XC_PostMessage(xi::g_hMainFrame, kFlushMessage, 0, 0)) {
        g_postPending = false;
        ++g_dropped; // The line stays queued; this counter also covers dropped posts.
    }
}

void flushOnUi() {
    g_postPending = false;
    if (g_closing) return;
    HELE edit = g_edit.load();
    if (!edit) return;
    if (g_showPane && g_pane.load()) {
        XPane_ShowPane(g_pane.load(), FALSE); // Do not steal the active editor tab.
        g_showPane = false;
    }
    std::deque<std::string> lines;
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        size_t count = (std::min)(g_pending.size(), (size_t)kMaxUiBatch);
        for (size_t i = 0; i < count; ++i) {
            lines.push_back(std::move(g_pending.front()));
            g_pending.pop_front();
        }
    }
    try {
        for (const auto& line : lines) {
            std::wstring text = wide(line);
            text += L"\r\n";
            XEdit_AddTextUser(edit, text.c_str());
            ++g_lines;
        }
        int rows = XEdit_GetRowCount(edit);
        // Bound both memory and expensive future UI updates. All log records are
        // single-line summaries, so old rows can be deleted without losing JSON.
        while (rows > kMaxEditorRows) {
            XEdit_DeleteRow(edit, 0);
            --rows;
        }
        XEdit_MoveEnd(edit);
        g_rows = XEdit_GetRowCount(edit);
        g_visible = g_pane.load() && XPane_IsShowPane(g_pane.load());
    } catch (...) {
    }
    if (g_closing) return;
    bool more = false;
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        more = !g_pending.empty();
    }
    if (more) schedule();
}

int CALLBACK onWindowMessage(UINT message, WPARAM, LPARAM, BOOL* handled) {
    if (message != kFlushMessage) {
        if (handled) *handled = FALSE;
        return 0; // Preserve all other IDE window messages and other plugins.
    }
    if (handled) *handled = TRUE;
    flushOnUi();
    return 1;
}

void createPane(xi::Data& event) {
    if (g_pane.load() || g_closing) return;
    HWINDOW main = event.get(0) ? (HWINDOW)event.get(0) : (HWINDOW)xi::g_hMainFrame;
    if (!main) return;
    try {
        HELE pane = XPane_Create(L"MCP 输出", 620, 220, main);
        if (!pane) return;
        HELE edit = XEdit_Create(0, 0, 600, 180, pane);
        if (!edit) return;
        XEdit_EnableMultiLine(edit, TRUE);
        XEdit_EnableReadOnly(edit, TRUE);
        XEdit_EnableAutoWrap(edit, TRUE);
        XWidget_LayoutItem_SetWidth(edit, layout_size_weight, 1);
        XWidget_LayoutItem_SetHeight(edit, layout_size_weight, 1);
        XPane_SetView(pane, edit);
        XWidget_SetID(pane, kPaneId);
        g_edit = edit;
        g_pane = pane;
        g_showPane = true;
        // Current x64 Data ABI: v[0] is hMainFrame, v[1] is arrayPanes[0].
        // Writing v[0] corrupts the host window handle (verified in isolation).
        event.put(1, pane);
        XWnd_RegEventC(main, XWM_WINDPROC, (void*)&onWindowMessage);
        flushOnUi();
    } catch (...) {
        g_pane = nullptr;
        g_edit = nullptr;
    }
}

int onPlugEvent(xi::Data* event) {
    if (event && event->type == xi::EVENT_CREATE_VIEW) createPane(*event);
    return 0; // Do not intercept the IDE's or other plugins' view creation.
}

std::string detail(const Exchange& exchange) {
    std::string text;
    for (const auto& call : exchange.calls) {
        if (!text.empty()) text += " | ";
        text += call;
        if (text.size() >= kMaxDetailBytes) break;
    }
    return oneLine(text, kMaxDetailBytes);
}

} // namespace

void install() {
    g_closing = false;
    xi::Data data;
    data.clear();
    data.type = xi::SET_BIND_EVENT;
    data.put(0, (void*)&onPlugEvent);
    xi::call(data);
}

void shutdown() {
    if (g_closing.exchange(true)) return;
    // A main-window close may already have destroyed the XCGUI handle. Do not
    // call back into it during teardown; its queued messages are gone as well.
    xi::logLine(L"mcp_log: shutdown");
    if (xi::mainHwnd())
        XWnd_RemoveEventC(xi::g_hMainFrame, XWM_WINDPROC, (void*)&onWindowMessage);
    g_edit = nullptr;
    g_pane = nullptr;
    g_postPending = false;
    std::lock_guard<std::mutex> lock(g_mutex);
    g_pending.clear();
}

void begin(const std::string& method, const std::string& path, size_t bodyBytes) {
    try {
        g_exchange = Exchange();
        g_exchange.id = ++g_nextId;
        g_exchange.started = GetTickCount64();
        GetLocalTime(&g_exchange.local);
        g_exchange.method = oneLine(method, 24);
        g_exchange.path = oneLine(path, 160);
        g_exchange.bodyBytes = bodyBytes;
        g_active = true;
    } catch (...) {
        g_active = false;
    }
}

void rpc(const std::string& method, const std::string& id, const std::string& tool) {
    if (!g_active) return;
    try {
        if (g_exchange.calls.size() >= 8) return; // Bound batch display, not JSON-RPC work.
        std::string text = method + " id=" + (id.empty() ? std::string("notification") : id);
        if (!tool.empty()) text += " tool=" + tool;
        g_exchange.calls.push_back(oneLine(text, 180));
    } catch (...) {
    }
}

void outcome(const std::string& summary) {
    if (g_active) g_exchange.outcome = oneLine(summary, 100);
}

void respond(int httpStatus, size_t bodyBytes) {
    try {
        Exchange* exchange = nullptr;
        if (g_active) exchange = &g_exchange;
        else {
            // A framing error may be answered before handleRequest has run.
            g_exchange = Exchange();
            g_exchange.id = ++g_nextId;
            g_exchange.started = GetTickCount64();
            GetLocalTime(&g_exchange.local);
            g_exchange.method = "HTTP";
            g_exchange.path = "(request not accepted)";
            exchange = &g_exchange;
        }
        const unsigned long long elapsed = GetTickCount64() - exchange->started;
        std::string number = "#" + std::to_string(exchange->id) + " <- " +
                             exchange->method + " " + exchange->path +
                             "  body=" + std::to_string(exchange->bodyBytes) + "B";
        enqueue("[" + timestamp(exchange->local) + "] " + number +
                (exchange->calls.empty() ? "" : "  " + detail(*exchange)));
        std::string line = "[" + timestamp(exchange->local) + "] #" +
                           std::to_string(exchange->id) + " -> HTTP " + std::to_string(httpStatus) +
                           "  body=" + std::to_string(bodyBytes) + "B  " +
                           std::to_string(elapsed) + "ms";
        if (!exchange->outcome.empty()) line += "  " + exchange->outcome;
        enqueue(std::move(line));
        ++g_exchanges;
        g_active = false;
        schedule();
    } catch (...) {
        g_active = false;
    }
}

void notice(const std::string& message) {
    try {
        SYSTEMTIME now;
        GetLocalTime(&now);
        enqueue("[" + timestamp(now) + "] [server] " + oneLine(message, 320));
        schedule();
    } catch (...) {
    }
}

Stats stats() {
    Stats result;
    result.pane = g_pane.load() != nullptr;
    result.visible = g_visible.load();
    result.rows = g_rows.load();
    result.exchanges = g_exchanges.load();
    result.lines = g_lines.load();
    result.dropped = g_dropped.load();
    std::lock_guard<std::mutex> lock(g_mutex);
    result.pending = g_pending.size();
    return result;
}

} // namespace mcp_log
