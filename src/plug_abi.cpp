#include "plug_abi.h"
#include "xcgui_min.h"

#include <cstdio>
#include <cstdarg>
#include <string>

namespace xi {

FunSet  g_funSet     = nullptr;
HWND    g_hMainFrame = nullptr;
int     g_ver        = 0;
HMODULE g_hSelf      = nullptr;

void Data::clear() {
    type = 0;
    id = 0;
    for (int i = 0; i < 31; ++i) v[i] = nullptr;
}

void* Data::get(int slot) const {
    return (slot >= 0 && slot < 31) ? v[slot] : nullptr;
}

void Data::put(int slot, void* value) {
    if (slot >= 0 && slot < 31) v[slot] = value;
}

static const wchar_t* logPath() {
    static wchar_t buf[MAX_PATH] = {0};
    if (buf[0]) return buf;
    wchar_t tmp[MAX_PATH] = {0};
    if (!GetModuleFileNameW(g_hSelf, tmp, MAX_PATH)) return L"xc_mcp_next.log";
    wchar_t* slash = wcsrchr(tmp, L'\\');
    if (slash) *(slash + 1) = 0;
    wcscpy_s(buf, MAX_PATH, tmp);
    wcscat_s(buf, MAX_PATH, L"xc_mcp_next.log");
    return buf;
}

void logLine(const wchar_t* fmt, ...) {
    FILE* f = nullptr;
    if (_wfopen_s(&f, logPath(), L"a, ccs=UTF-8") != 0 || !f) return;
    SYSTEMTIME st;
    GetLocalTime(&st);
    fwprintf(f, L"[%02d:%02d:%02d.%03d] ", st.wHour, st.wMinute, st.wSecond, st.wMilliseconds);
    va_list ap;
    va_start(ap, fmt);
    vfwprintf(f, fmt, ap);
    va_end(ap);
    fwprintf(f, L"\n");
    fclose(f);
}

HWND mainHwnd() {
    if (!g_hMainFrame) return nullptr;
    HWND hwnd = nullptr;
    __try {
        hwnd = XWnd_GetHWND((HWINDOW)g_hMainFrame);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return nullptr;
    }
    DWORD pid = 0;
    if (!hwnd || !IsWindow(hwnd) || !GetWindowThreadProcessId(hwnd, &pid) ||
        pid != GetCurrentProcessId()) return nullptr;
    return hwnd;
}

bool ready() {
    return g_funSet != nullptr;
}

int call(Data& d) {
    if (!g_funSet) return -1;
    return g_funSet(&d);
}

bool getHele(int paneId, HELEx* out) {
    Data d;
    d.clear();
    d.type = SET_GET_HELE;
    d.id   = paneId;
    call(d);
    if (out) *out = d.get(0);
    return d.get(0) != nullptr;
}

bool debugAddString(const wchar_t* text) {
    if (!text) return false;
    Data d;
    d.clear();
    d.type = SET_DEBUG_ADDSTRING;
    d.put(0, (void*)text);
    call(d);
    return true;
}

int getProjectRaw(void** p1, void** p2, void** p3) {
    Data d;
    d.clear();
    d.type = SET_GET_PROJECT;
    int rc = call(d);
    if (p1) *p1 = d.get(0);
    if (p2) *p2 = d.get(1);
    if (p3) *p3 = d.get(2);
    return rc;
}

}
