#include "dialogs.hpp"
#include "plug_abi.h"
#include <windows.h>
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>
#include <cstring>

namespace dialogs {
#ifdef XCMCP_DIALOG_TEST_WORKER_DELAY
// Only the isolated acceptance build defines this. Never present in build.bat.
DWORD g_testWorkerDelayMs = 0;
#endif
namespace {
constexpr wchar_t kReaderClass[] = L"XC-MCP-Next.DialogReader";
constexpr UINT kReadMessage = WM_APP + 0x4D44;
constexpr DWORD kWaitMs = 1800;
constexpr size_t kMaxWindows = 32;
constexpr size_t kMaxNodes = 512;

struct Window {
    HWND hwnd = nullptr;
    DWORD thread = 0;
    std::wstring className;
};
struct Inspection {
    bool matched = false;
    bool truncated = false;
    std::wstring title, text;
    std::string error;
};
struct Work {
    Work() : event(CreateEventW(nullptr, TRUE, FALSE, nullptr)) {}
    ~Work() { if (event) CloseHandle(event); }
    HANDLE event;
    std::atomic<bool> cancelled{false};
    std::mutex mutex;
    js::Value result;
    std::vector<Window> windows;
    int maxChars = 16000;
    ULONGLONG deadline = 0;
    std::string discoveryError;
};

std::mutex g_mutex;
HWND g_reader = nullptr, g_main = nullptr;
HMODULE g_module = nullptr;
DWORD g_thread = 0;
bool g_closing = true;
uintptr_t g_nextId = 0;
std::map<uintptr_t, std::shared_ptr<Work>> g_work;

std::string utf8(const std::wstring& text) {
    if (text.empty()) return {};
    int count = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, text.data(),
                                  (int)text.size(), nullptr, 0, nullptr, nullptr);
    if (!count) throw std::runtime_error("invalid Unicode text");
    std::string out((size_t)count, '\0');
    WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, text.data(), (int)text.size(),
                        &out[0], count, nullptr, nullptr);
    return out;
}
std::string handleText(HWND hwnd) {
    char buffer[32];
    sprintf_s(buffer, "0x%llX", (unsigned long long)(uintptr_t)hwnd);
    return buffer;
}
std::wstring windowClass(HWND hwnd) {
    wchar_t buffer[256] = {};
    int count = GetClassNameW(hwnd, buffer, 256);
    return std::wstring(buffer, count > 0 ? (size_t)count : 0);
}
bool sameWindow(const Window& window) {
    DWORD pid = 0;
    return IsWindow(window.hwnd) && IsWindowVisible(window.hwnd) &&
           GetWindowThreadProcessId(window.hwnd, &pid) == window.thread &&
           pid == GetCurrentProcessId() && windowClass(window.hwnd) == window.className;
}

// Limit Unicode code points, never split a UTF-16 surrogate pair/UTF-8 character.
void trimText(std::wstring& text, int limit, bool& truncated) {
    size_t at = 0;
    int points = 0;
    while (at < text.size() && points < limit) {
        wchar_t ch = text[at++];
        if (ch >= 0xD800 && ch <= 0xDBFF && at < text.size() &&
            text[at] >= 0xDC00 && text[at] <= 0xDFFF) ++at;
        ++points;
    }
    if (at < text.size()) { text.resize(at); truncated = true; }
    if (!text.empty() && text.back() >= 0xD800 && text.back() <= 0xDBFF) {
        text.pop_back();
        truncated = true;
    }
}
std::wstring copyText(const wchar_t* text, int limit, bool& truncated) {
    if (!text) return {};
    size_t cap = (size_t)limit * 2 + 2;
    size_t count = 0;
    while (count < cap && text[count]) ++count;
    if (count == cap) truncated = true;
    std::wstring out(text, count);
    trimText(out, limit, truncated);
    return out;
}
void appendBody(Inspection& out, const std::wstring& text, int limit) {
    if (text.empty()) return;
    if (!out.text.empty()) out.text += L"\n";
    out.text += text;
    trimText(out.text, limit, out.truncated);
}

// Called ONLY on the HWND's own UI thread. In particular, a worker never passes
// a stack/text buffer to SendMessageTimeout: a timed-out WndProc could still use
// that pointer. Here all buffers live in the UI callback until its actual return.
bool nativeText(HWND hwnd, int limit, std::wstring& out, bool& truncated) {
    DWORD pid = 0;
    if (!IsWindow(hwnd) || GetWindowThreadProcessId(hwnd, &pid) != GetCurrentThreadId() ||
        pid != GetCurrentProcessId()) return false;
    LRESULT length = SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0);
    if (length < 0 || !IsWindow(hwnd)) return false;
    if (!length) { out.clear(); return true; }
    size_t capacity = (size_t)(std::min)(length, (LRESULT)limit * 2 + 2) + 1;
    std::wstring buffer(capacity, L'\0');
    LRESULT count = SendMessageW(hwnd, WM_GETTEXT, capacity, (LPARAM)&buffer[0]);
    if (count <= 0 || count >= (LRESULT)capacity || !IsWindow(hwnd)) return false;
    if (count < length) truncated = true;
    buffer.resize((size_t)count);
    trimText(buffer, limit, truncated);
    out = std::move(buffer);
    return true;
}

struct NativeChildren {
    std::vector<HWND> text;
    size_t nodes = 0;
    int buttons = 0;
    bool interactive = false, limited = false;
};
BOOL CALLBACK enumNativeChild(HWND hwnd, LPARAM param) {
    NativeChildren& out = *(NativeChildren*)param;
    if (++out.nodes > kMaxNodes) { out.limited = true; return FALSE; }
    if (!IsWindowVisible(hwnd)) return TRUE;
    std::wstring cls = windowClass(hwnd);
    LONG_PTR style = GetWindowLongPtrW(hwnd, GWL_STYLE);
    if (cls == L"Static") {
        LONG_PTR kind = style & SS_TYPEMASK;
        if (kind != SS_ICON && kind != SS_BITMAP && kind != SS_OWNERDRAW &&
            kind != SS_BLACKRECT && kind != SS_GRAYRECT && kind != SS_WHITERECT &&
            kind != SS_BLACKFRAME && kind != SS_GRAYFRAME && kind != SS_WHITEFRAME &&
            kind != SS_ETCHEDHORZ && kind != SS_ETCHEDVERT && kind != SS_ETCHEDFRAME)
            out.text.push_back(hwnd);
    } else if (cls == L"Edit" && (style & ES_READONLY)) {
        out.text.push_back(hwnd);
    } else if (cls == L"Button" && ((style & BS_TYPEMASK) == BS_PUSHBUTTON ||
                                    (style & BS_TYPEMASK) == BS_DEFPUSHBUTTON)) {
        ++out.buttons;
    } else if (cls == L"Button" && (style & BS_TYPEMASK) == BS_GROUPBOX) {
        // Decoration, not an editable input. EnumChildWindows still visits its children.
    } else {
        // Do not report file pickers, property pages or other editing dialogs as
        // error prompts. No heuristic depends on the title containing "错误".
        out.interactive = true;
    }
    return TRUE;
}
Inspection inspectNative(const Window& window, int limit, ULONGLONG deadline) {
    NativeChildren children;
    EnumChildWindows(window.hwnd, enumNativeChild, (LPARAM)&children);
    Inspection out;
    if (children.limited) { out.matched = true; out.error = "node_limit"; return out; }
    if (children.interactive || !children.buttons || children.text.empty()) return out;
    out.matched = true;
    if (!nativeText(window.hwnd, 1024, out.title, out.truncated)) {
        out.error = "title_read_failed";
        return out;
    }
    for (HWND child : children.text) {
        if (GetTickCount64() >= deadline) { out.error = "read_deadline"; break; }
        std::wstring text;
        if (!nativeText(child, limit, text, out.truncated)) {
            out.error = "body_read_failed";
            break;
        }
        appendBody(out, text, limit);
    }
    if (out.error.empty() && out.text.empty()) out.error = "body_unavailable";
    return out;
}

// Verified against module_xcgui.h (XC_OBJECT_TYPE, XObj_*, XWnd_*, XEle_*,
// XShapeText_* and XC_hWindowFromHWnd) and the IDE's XCGUI.dll exports.
// Resolve at runtime so an unsupported DLL reports a diagnostic, not a load error.
struct XcgApi {
    using Unary = int (WINAPI*)(void*);
    using Index = void* (WINAPI*)(void*, int);
    using Text = const wchar_t* (WINAPI*)(void*);
    void* (WINAPI* fromHwnd)(HWND) = nullptr;
    Unary type = nullptr, base = nullptr, extended = nullptr;
    Unary windowCount = nullptr, elementCount = nullptr;
    Index windowChild = nullptr, elementChild = nullptr;
    BOOL (WINAPI* shown)(void*) = nullptr;
    BOOL (WINAPI* readOnly)(void*) = nullptr;
    Text title = nullptr, shapeText = nullptr, editText = nullptr;
    XcgApi() {
        HMODULE module = GetModuleHandleW(L"XCGUI.dll");
#define XC_READ_API(field, name) field = (decltype(field))GetProcAddress(module, name)
        XC_READ_API(fromHwnd, "XC_hWindowFromHWnd");
        XC_READ_API(type, "XObj_GetType");
        XC_READ_API(base, "XObj_GetTypeBase");
        XC_READ_API(extended, "XObj_GetTypeEx");
        XC_READ_API(windowCount, "XWnd_GetChildCount");
        XC_READ_API(windowChild, "XWnd_GetChildByIndex");
        XC_READ_API(elementCount, "XEle_GetChildCount");
        XC_READ_API(elementChild, "XEle_GetChildByIndex");
        XC_READ_API(shown, "XWidget_IsShow");
        XC_READ_API(title, "XWnd_GetTitle");
        XC_READ_API(shapeText, "XShapeText_GetText");
        XC_READ_API(readOnly, "XEdit_IsReadOnly");
        XC_READ_API(editText, "XEdit_GetText_Temp");
#undef XC_READ_API
    }
    bool available() const {
        return fromHwnd && type && base && extended && windowCount && windowChild &&
               elementCount && elementChild && shown && title && shapeText;
    }
};

// XMsg_Create has no public body getter and paints its std::wstring directly in
// WM_PAINT (CMessageBox::OnWndDrawWindow). Standard shape traversal therefore
// cannot read that body. This guarded, read-only adapter is for the one verified
// XCGUI build. It validates the exported type lookup's exact instructions, PE
// image metadata, HWND/object identity and string bounds on the owning UI thread.
// Never derefence an arbitrary "HWINDOW" as CMessageBox: XC_hWindowFromHWnd is
// an index. The registry object is CMessageBox itself; +200 is its own index.
struct XcgMessageAdapter {
    void*** objects = nullptr;
    DWORD minimum = 0, maximum = 0;
    uintptr_t moduleStart = 0;
    bool available = false;
    XcgMessageAdapter(const XcgApi& api) {
        HMODULE module = GetModuleHandleW(L"XCGUI.dll");
        if (!module || !api.type || !api.fromHwnd) return;
        const IMAGE_DOS_HEADER* dos = (const IMAGE_DOS_HEADER*)module;
        if (dos->e_magic != IMAGE_DOS_SIGNATURE) return;
        const IMAGE_NT_HEADERS64* nt = (const IMAGE_NT_HEADERS64*)((const unsigned char*)module + dos->e_lfanew);
        if (nt->Signature != IMAGE_NT_SIGNATURE || nt->FileHeader.TimeDateStamp != 0x69DD049C ||
            nt->OptionalHeader.SizeOfImage != 0x3A3000) return;
        const unsigned char* code = (const unsigned char*)api.type;
        // XObj_GetType: minimum at 0x33A5D0, maximum at 0x33A5D4, registry at 0x33A5C8.
        static const unsigned char expected[] = {
            0x48,0x63,0x05,0xA9,0x16,0x32,0x00,0x8B,0xD1,0x48,0x3B,0xD0,0x7C,0x26,
            0x48,0x63,0x05,0x9F,0x16,0x32,0x00,0x48,0x3B,0xD0,0x7D,0x1A,
            0x48,0x8B,0x05,0x87,0x16,0x32,0x00,0x4C,0x8B,0x04,0xD0,0x49,0x8D,0x48,0xFF
        };
        if (memcmp(code, expected, sizeof(expected)) != 0) return;
        DWORD tableMin = *(const DWORD*)((unsigned char*)module + 0x33A5D0);
        DWORD tableMax = *(const DWORD*)((unsigned char*)module + 0x33A5D4);
        if (tableMin > tableMax || tableMax > 0x100000) return;
        objects = *(void****)((unsigned char*)module + 0x33A5C8);
        if (!objects) return;
        minimum = tableMin; maximum = tableMax;
        moduleStart = (uintptr_t)module;
        available = true;
    }
    bool read(const XcgApi& api, const Window& window, void* root, int limit,
              std::wstring& out, bool& truncated) const {
        if (!available) return false;
        uintptr_t index = (uintptr_t)root;
        if (index < minimum || index >= maximum) return false;
        __try {
            // The registry object is CMessageBox itself; +200 is its own index,
            // not a pointer to another CMessageBox.
            unsigned char* object = (unsigned char*)objects[index];
            if (!object || *(HWND*)(object + 208) != window.hwnd ||
                *(uintptr_t*)(object + 200) != index || api.type(root) != 2)
                return false;
            const int* types = *(const int**)(object + 32);
            const int* end = *(const int**)(object + 40);
            if (!types || end != types + 4 || types[0] != 19 || types[1] != 1 ||
                types[2] != 124 || types[3] != 2) return false;
            // CMessageBox::`vftable' = module+0x2DBA68, verified from the
            // constructor's LEA at 0x13862E. A plain CModalWindow is not XMsg.
            if (*(const uintptr_t*)object != moduleStart + 0x2DBA68) return false;
            const unsigned char* text = object + 848;
            unsigned long long length = *(const unsigned long long*)(text + 16);
            unsigned long long capacity = *(const unsigned long long*)(text + 24);
            if (length > 131072 || capacity > 131072 || length > capacity) return false;
            const wchar_t* body = capacity >= 8 ? *(const wchar_t* const*)text : (const wchar_t*)text;
            out.assign(body, (size_t)length);
            trimText(out, limit, truncated);
            return true;
        } __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
    }
};
Inspection inspectXcg(const Window& window, int limit, ULONGLONG deadline) {
    static const XcgApi api; // First construction, and all uses, on the UI thread.
    static const XcgMessageAdapter messageAdapter(api);
    Inspection out;
    if (!api.available()) { out.matched = true; out.error = "xcgui_api_unavailable"; return out; }
    void* root = api.fromHwnd(window.hwnd);
    if (!root) return out; // Not an XCGUI window (a different native popup).
    int windowType = api.type(root);
    HWND owner = GetWindow(window.hwnd, GW_OWNER);
    // XC_MODALWINDOW=2; also accommodate owned XC_WINDOW=1 prompts which disable
    // their owner. Never inspect menu (12/13), combobox (11), or floating panes.
    if (windowType != 2 && !(windowType == 1 && owner && !IsWindowEnabled(owner))) return out;
    out.matched = true;
    out.title = copyText(api.title(root), 1024, out.truncated);
    std::vector<void*> nodes;
    std::set<void*> visited;
    int count = api.windowCount(root);
    if (count < 0) { out.error = "child_read_failed"; return out; }
    if ((size_t)count > kMaxNodes) { out.error = "node_limit"; return out; }
    for (int i = count - 1; i >= 0; --i) nodes.push_back(api.windowChild(root, i));
    int buttons = 0;
    bool interactive = false;
    while (!nodes.empty()) {
        if (GetTickCount64() >= deadline) { out.error = "read_deadline"; return out; }
        void* node = nodes.back(); nodes.pop_back();
        if (!node) { out.error = "child_read_failed"; return out; }
        if (!visited.insert(node).second) continue;
        if (visited.size() > kMaxNodes) { out.error = "node_limit"; return out; }
        if (!api.shown(node)) continue; // Hidden body/hidden ancestors are not current text.
        int kind = api.type(node);
        if (kind == 62) { // XC_SHAPE_TEXT: genuine body text, NOT button labels.
            appendBody(out, copyText(api.shapeText(node), limit, out.truncated), limit);
        } else if (kind == 45) { // XC_EDIT: only readonly diagnostic text is allowed.
            if (!api.readOnly || !api.editText) { out.error = "xcgui_api_unavailable"; return out; }
            if (!api.readOnly(node)) interactive = true;
            else appendBody(out, copyText(api.editText(node), limit, out.truncated), limit);
        } else if (kind == 22) { // XC_BUTTON; default=0, radio=1, check=2, caption close=3.
            int extended = api.extended(node);
            if (extended == 0) ++buttons;
            else if (extended == 1 || extended == 2) interactive = true;
        } else if ((kind >= 24 && kind <= 40 && kind != 25 && kind != 26 && kind != 33) ||
                   kind == 46 || kind == 47 || kind == 50 || kind == 51) {
            interactive = true; // Lists, editable inputs, tabs, file pickers, etc.
        }
        // XObj_GetTypeBase==XC_ELE (21), including layout elements/scroll views.
        // Text shapes have XC_SHAPE base and are never sent to an element API.
        if (api.base(node) == 21 && kind != 22 && kind != 45) {
            int children = api.elementCount(node);
            if (children < 0) { out.error = "child_read_failed"; return out; }
            if ((size_t)children + visited.size() + nodes.size() > kMaxNodes) {
                out.error = "node_limit"; return out;
            }
            for (int i = children - 1; i >= 0; --i) nodes.push_back(api.elementChild(node, i));
        }
    }
    if (interactive || !buttons) { out.matched = false; return out; }
    if (out.text.empty() && buttons) {
        std::wstring body;
        bool read = messageAdapter.read(api, window, root, limit, body, out.truncated);
        if (read) out.text = std::move(body);
        else out.error = "xcgui_layout_unverified";
    }
    if (out.text.empty() && out.error.empty()) out.error = "body_unavailable";
    return out;
}

std::string message(const std::string& code) {
    if (code == "ui_timeout") return "IDE UI 未在 1800ms 内完成读取；没有返回过期正文";
    if (code == "window_disappeared") return "弹窗在读取过程中关闭、隐藏或句柄失效";
    if (code == "ui_thread_unavailable") return "弹窗不属于已注册的 IDE UI 线程，未跨线程访问控件";
    if (code == "reader_unavailable") return "IDE UI 读取窗口未初始化或已关闭";
    if (code == "invalid_arguments") return "maxChars 必须是 128..65536 的整数；不接受其他参数";
    if (code == "node_limit") return "控件节点超过安全上限，快照不完整";
    if (code == "window_limit") return "候选弹窗超过 32 个，快照不完整";
    if (code == "read_deadline") return "读取超过时间预算，快照不完整";
    if (code == "xcgui_api_unavailable") return "当前 XCGUI 缺少已验证的正文读取 API";
    if (code == "xcgui_layout_unverified") return "XMsg 对象结构与已验证版本不符；拒绝读取私有正文";
    if (code == "body_unavailable") return "识别到提示弹窗但取不到正文；未以标题冒充正文";
    return "弹窗读取失败: " + code;
}
js::Value rowFor(const Window& window) {
    js::Value row = js::Value::obj();
    row.set("hwnd", handleText(window.hwnd));
    row.set("pid", (int)GetCurrentProcessId());
    row.set("className", utf8(window.className));
    row.set("source", std::string(window.className == L"#32770" ? "win32" : "xcgui"));
    row.set("title", std::string());
    row.set("text", std::string());
    row.set("readable", false);
    row.set("truncated", false);
    return row;
}
js::Value diagnostic(const std::string& code, HWND hwnd = nullptr) {
    js::Value out = js::Value::obj();
    out.set("code", code);
    out.set("message", message(code));
    if (hwnd) out.set("hwnd", handleText(hwnd));
    return out;
}
js::Value resultFor(js::Value rows, js::Value errors) {
    bool truncated = false;
    for (const auto& row : rows.items())
        if (const js::Value* value = row.find("truncated")) truncated |= value->asBool();
    js::Value result = js::Value::obj();
    result.set("ok", errors.items().empty());
    result.set("complete", errors.items().empty() && !truncated);
    result.set("pid", (int)GetCurrentProcessId());
    result.set("count", (int)rows.items().size());
    result.set("truncated", truncated);
    result.set("dialogs", std::move(rows));
    result.set("errors", std::move(errors));
    return result;
}
js::Value failure(const std::vector<Window>& windows, const std::string& code) {
    js::Value rows = js::Value::arr(), errors = js::Value::arr();
    errors.push(diagnostic(code));
    for (const auto& window : windows) {
        js::Value row = rowFor(window);
        row.set("candidate", true); // Recognition could not finish; NOT old text.
        row.set("error", code);
        rows.push(row);
    }
    return resultFor(std::move(rows), std::move(errors));
}
js::Value capture(const Work& work) {
    js::Value rows = js::Value::arr(), errors = js::Value::arr();
    if (!work.discoveryError.empty()) errors.push(diagnostic(work.discoveryError));
    for (const auto& window : work.windows) {
        if (work.cancelled) break;
        js::Value row = rowFor(window);
        Inspection found;
        if (GetTickCount64() >= work.deadline) {
            found.matched = true; found.error = "read_deadline";
        } else if (!sameWindow(window)) {
            found.matched = true; found.error = "window_disappeared";
        } else if (window.thread != GetCurrentThreadId()) {
            found.matched = true; found.error = "ui_thread_unavailable";
        } else {
            try {
                found = window.className == L"#32770"
                    ? inspectNative(window, work.maxChars, work.deadline)
                    : inspectXcg(window, work.maxChars, work.deadline);
                if (found.matched && !sameWindow(window)) found.error = "window_disappeared";
                if (found.matched && found.error.empty()) {
                    row.set("title", utf8(found.title));
                    row.set("text", utf8(found.text));
                }
            } catch (...) {
                // /EHa also contains stale XCGUI pointers/SEH; nothing may escape
                // this private Win32 WndProc into the IDE's message loop.
                found.matched = true;
                found.error = "text_read_failed";
            }
        }
        if (!found.matched) continue;
        row.set("readable", found.error.empty());
        row.set("truncated", found.truncated);
        if (!found.error.empty()) {
            row.set("error", found.error);
            errors.push(diagnostic(found.error, window.hwnd));
        }
        rows.push(row);
    }
    return resultFor(std::move(rows), std::move(errors));
}
LRESULT CALLBACK readerProc(HWND hwnd, UINT msg, WPARAM id, LPARAM param) {
    if (msg != kReadMessage) return DefWindowProcW(hwnd, msg, id, param);
    std::shared_ptr<Work> work;
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        auto entry = g_work.find((uintptr_t)id);
        if (entry == g_work.end()) return 0; // A timed-out/cancelled queued job.
        work = entry->second;
        g_work.erase(entry);
    }
    if (work->cancelled) return 0;
    js::Value result;
    try { result = capture(*work); }
    catch (...) { result = failure(work->windows, "text_read_failed"); }
    {
        std::lock_guard<std::mutex> lock(work->mutex);
        if (work->cancelled) return 0;
        if (GetTickCount64() >= work->deadline)
            result = failure(work->windows, "read_deadline");
        work->result = std::move(result);
        SetEvent(work->event);
    }
    return 0;
}
struct Discovery {
    HWND main;
    ULONGLONG deadline;
    std::vector<Window> windows;
    std::string error;
};
BOOL CALLBACK enumWindow(HWND hwnd, LPARAM param) {
    Discovery& out = *(Discovery*)param;
    if (GetTickCount64() >= out.deadline) { out.error = "read_deadline"; return FALSE; }
    DWORD pid = 0;
    DWORD thread = GetWindowThreadProcessId(hwnd, &pid);
    if (pid != GetCurrentProcessId() || hwnd == out.main || !IsWindowVisible(hwnd)) return TRUE;
    LONG_PTR style = GetWindowLongPtrW(hwnd, GWL_STYLE);
    if (style & WS_CHILD) return TRUE;
    std::wstring cls = windowClass(hwnd);
    if (cls == L"#32768" || cls == L"tooltips_class32") return TRUE;
    if (cls != L"#32770" && !(style & WS_POPUP)) return TRUE;
    if (out.windows.size() == kMaxWindows) { out.error = "window_limit"; return FALSE; }
    out.windows.push_back({hwnd, thread, std::move(cls)});
    return TRUE;
}
} // namespace

void install() {
    std::lock_guard<std::mutex> lock(g_mutex);
    if (g_reader && IsWindow(g_reader)) return;
    HWND main = xi::mainHwnd(); // XCGUI handle conversion runs on this UI thread.
    DWORD pid = 0;
    if (!main || GetWindowThreadProcessId(main, &pid) != GetCurrentThreadId() ||
        pid != GetCurrentProcessId()) return;
    HMODULE module = xi::g_hSelf ? xi::g_hSelf : GetModuleHandleW(nullptr);
    WNDCLASSW cls = {};
    cls.lpfnWndProc = readerProc;
    cls.hInstance = module;
    cls.lpszClassName = kReaderClass;
    if (!RegisterClassW(&cls) && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) return;
    HWND reader = CreateWindowExW(0, kReaderClass, L"", 0, 0, 0, 0, 0,
                                  HWND_MESSAGE, nullptr, module, nullptr);
    if (!reader) { UnregisterClassW(kReaderClass, module); return; }
    g_reader = reader; g_main = main; g_module = module;
    g_thread = GetCurrentThreadId(); g_closing = false;
}
void shutdown() {
    HWND reader;
    HMODULE module;
    std::map<uintptr_t, std::shared_ptr<Work>> pending;
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        g_closing = true;
        reader = g_reader; module = g_module;
        g_reader = g_main = nullptr; g_thread = 0; g_module = nullptr;
        pending.swap(g_work);
    }
    for (auto& item : pending) {
        std::lock_guard<std::mutex> lock(item.second->mutex);
        item.second->result = failure(item.second->windows, "reader_unavailable");
        SetEvent(item.second->event);
    }
    if (reader) DestroyWindow(reader); // Contract: shutdown is on the installing UI thread.
    if (module) UnregisterClassW(kReaderClass, module);
}
js::Value read(const js::Value& args) {
    int maxChars = 16000;
    if (!args.isObj() && !args.isNull()) return failure({}, "invalid_arguments");
    for (const auto& field : args.fields())
        if (field.first != "maxChars") return failure({}, "invalid_arguments");
    if (const js::Value* value = args.find("maxChars")) {
        double number = value->asNum(-1);
        if (value->type() != js::NUM || !std::isfinite(number) || number < 128 ||
            number > 65536 || std::floor(number) != number) return failure({}, "invalid_arguments");
        maxChars = (int)number;
    }
    HWND reader, main;
    DWORD thread;
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        if (g_closing || !g_reader) return failure({}, "reader_unavailable");
        reader = g_reader; main = g_main; thread = g_thread;
    }
    // Never wait for our own message loop. MCP clients always call from workers.
    if (GetCurrentThreadId() == thread) return failure({}, "ui_thread_unavailable");
    DWORD pid = 0;
    if (!IsWindow(main) || !IsWindow(reader) ||
        GetWindowThreadProcessId(reader, &pid) != thread || pid != GetCurrentProcessId())
        return failure({}, "reader_unavailable");
    Discovery discovery{main, GetTickCount64() + kWaitMs, {}, {}};
    SetLastError(ERROR_SUCCESS);
    if (!EnumWindows(enumWindow, (LPARAM)&discovery) && discovery.error.empty())
        discovery.error = "window_enumeration_failed";
    if (discovery.windows.empty()) {
        js::Value errors = js::Value::arr();
        if (!discovery.error.empty()) errors.push(diagnostic(discovery.error));
        return resultFor(js::Value::arr(), std::move(errors));
    }
    auto work = std::make_shared<Work>();
    work->windows = std::move(discovery.windows);
    work->deadline = discovery.deadline; work->maxChars = maxChars;
    work->discoveryError = discovery.error;
    if (!work->event) return failure(work->windows, "reader_unavailable");
    uintptr_t id;
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        if (g_closing || g_reader != reader) return failure(work->windows, "reader_unavailable");
        do { id = ++g_nextId; } while (!id || g_work.count(id));
        g_work.emplace(id, work);
    }
    DWORD wait = WAIT_FAILED;
    if (PostMessageW(reader, kReadMessage, (WPARAM)id, 0)) {
#ifdef XCMCP_DIALOG_TEST_WORKER_DELAY
        // Deterministic regression: UI publishes quickly, worker resumes late.
        if (g_testWorkerDelayMs) {
            DWORD delay = g_testWorkerDelayMs;
            g_testWorkerDelayMs = 0;
            Sleep(delay);
        }
#endif
        ULONGLONG now = GetTickCount64();
        wait = WaitForSingleObject(work->event, now < work->deadline ? (DWORD)(work->deadline - now) : 0);
    }
    js::Value result;
    {
        std::lock_guard<std::mutex> lock(work->mutex);
        if (wait == WAIT_OBJECT_0 && GetTickCount64() < work->deadline) result = work->result;
        else {
            work->cancelled = true;
            result = failure(work->windows, wait == WAIT_TIMEOUT ? "ui_timeout" :
                             wait == WAIT_OBJECT_0 ? "read_deadline" : "reader_unavailable");
        }
    }
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        g_work.erase(id);
    }
    return result;
}
} // namespace dialogs
