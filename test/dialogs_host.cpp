// Isolated acceptance-test host for the production HTTP/MCP/dialog reader.
// All GUI calls run on the main XCGUI thread. The stdin controller only parses
// JSON and posts commands to a private message-only window on that thread.
#include "dialogs.hpp"
#include "http.hpp"
#include "instance.hpp"
#include "json.hpp"
#include "mcp.hpp"
#include "plug_abi.h"
#include "tools.hpp"
#include "xcgui_min.h"

#include <atomic>
#include <commctrl.h>
#include <cstdio>
#include <filesystem>
#include <iostream>
#include <memory>
#include <string>
#include <thread>
#include <vector>

namespace dialogs { extern DWORD g_testWorkerDelayMs; }
namespace {
constexpr UINT kCommandMessage = WM_APP + 0x711;
constexpr UINT_PTR kMenuTimer = 41;
constexpr UINT_PTR kCreateTimer = 42;
constexpr int kModalStyle = 0x0001 | 0x0002 | 0x0004 | 0x0040 | 0x0080 | 0x0400;
constexpr int kDefaultStyle = kModalStyle | 0x0008 | 0x0020 | 0x0100 | 0x0200;

using XInit = BOOL (WINAPI*)(BOOL);
using XExit = void (WINAPI*)();
using XRun = void (WINAPI*)();
using XWndCreate = void* (WINAPI*)(int, int, int, int, const wchar_t*, HWND, int);
using XMsgCreate = void* (WINAPI*)(const wchar_t*, const wchar_t*, int, HWND, int);
using XDoModal = int (WINAPI*)(void*);
using XEndModal = void (WINAPI*)(void*, int);
using XShow = BOOL (WINAPI*)(void*, int);
using XClose = void (WINAPI*)(void*);

HMODULE g_xcg = nullptr;
XInit pXInit = nullptr;
XExit pXExit = nullptr;
XRun pXRun = nullptr;
XWndCreate pXWndCreate = nullptr;
XMsgCreate pXMsgCreate = nullptr;
XDoModal pXDoModal = nullptr;
XEndModal pXEndModal = nullptr;
XShow pXShow = nullptr;
XClose pXClose = nullptr;

void* g_main = nullptr;
HWND g_mainHwnd = nullptr;
HWND g_control = nullptr;
HWND g_native = nullptr;
HWND g_groupDialog = nullptr;
HWND g_xmsgHwnd = nullptr;
HWND g_tooltip = nullptr;
HWND g_menuHwnd = nullptr;
void* g_xmsg = nullptr;
HMENU g_menu = nullptr;
bool g_menuActive = false;
HCURSOR g_cursor = nullptr;
HHOOK g_hook = nullptr;
std::wstring g_faultMode;
std::atomic<int> g_gettextCalls{0};
DWORD g_uiThread = 0;

std::wstring g_projectName, g_projectFile, g_projectDir;

struct Command {
    HANDLE event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    js::Value id;
    std::string name, title, text, fault;
    int duration = 0;
    std::string reply;
    ~Command() { if (event) CloseHandle(event); }
};
using CommandPtr = std::shared_ptr<Command>;
CommandPtr g_activeCreate, g_pendingMenu;

std::string hwndText(HWND hwnd) {
    char text[32];
    sprintf_s(text, "0x%llX", (unsigned long long)(uintptr_t)hwnd);
    return text;
}

std::wstring className(HWND hwnd) {
    wchar_t name[256] = {};
    int count = GetClassNameW(hwnd, name, 256);
    return std::wstring(name, count > 0 ? count : 0);
}

bool ownWindow(HWND hwnd) {
    DWORD pid = 0;
    return hwnd && IsWindow(hwnd) && GetWindowThreadProcessId(hwnd, &pid) &&
           pid == GetCurrentProcessId();
}
js::Value debugXcgTree() {
    // Do not keep calling XCGUI with a possibly released message handle. The
    // fixture records HWND and original inputs; production must retrieve text
    // independently, not obtain it from this expected-text fixture record.
    js::Value rows = js::Value::arr();
    if (!ownWindow(g_xmsgHwnd) || !IsWindowVisible(g_xmsgHwnd)) return rows;
    js::Value root = js::Value::obj();
    root.set("hwnd", hwndText(g_xmsgHwnd));
    rows.push(root);
    return rows;
}


js::Value status() {
    js::Value value = js::Value::obj();
    value.set("ok", true);
    value.set("pid", (int)GetCurrentProcessId());
    value.set("instanceId", http::instanceId());
    value.set("port", http::port());
    value.set("httpRunning", http::running());
    value.set("projectFile", instance::currentProjectFile());
    value.set("projectName", instance::utf8(g_projectName.c_str()));
    value.set("projectDir", instance::utf8(g_projectDir.c_str()));
    value.set("mainHwnd", hwndText(g_mainHwnd));
    wchar_t modulePath[MAX_PATH] = {};
    DWORD moduleLength = GetModuleFileNameW(g_xcg, modulePath, MAX_PATH);
    value.set("xcgui", instance::utf8(moduleLength ? modulePath : L""));
    value.set("nativeVisible", ownWindow(g_native) && IsWindowVisible(g_native));
    value.set("groupVisible", ownWindow(g_groupDialog) && IsWindowVisible(g_groupDialog));
    value.set("xmsgVisible", ownWindow(g_xmsgHwnd) && IsWindowVisible(g_xmsgHwnd));
    value.set("tooltipVisible", ownWindow(g_tooltip) && IsWindowVisible(g_tooltip));
    value.set("menuActive", g_menuActive);
    value.set("gettextCalls", g_gettextCalls.load());
    value.set("xcgTree", debugXcgTree());
    return value;
}

void finish(const CommandPtr& command, bool ok = true, HWND hwnd = nullptr,
            const std::string& error = std::string()) {
    if (!command || !command->reply.empty()) return;
    js::Value value = js::Value::obj();
    value.set("id", command->id);
    value.set("command", command->name);
    value.set("ok", ok);
    value.set("pid", (int)GetCurrentProcessId());
    if (hwnd) {
        value.set("hwnd", hwndText(hwnd));
        value.set("className", instance::utf8(className(hwnd).c_str()));
        value.set("visible", IsWindowVisible(hwnd) != 0);
    }
    if (!error.empty()) value.set("error", error);
    command->reply = value.dump();
    SetEvent(command->event);
}

int WINAPI fakeAbi(xi::Data* data) {
    if (!data) return -1;
    if (data->type == xi::SET_GET_PROJECT) {
        // Stable UTF-16 pointers, just like the real IDE's project ABI results.
        data->put(0, (void*)g_projectName.c_str());
        data->put(1, (void*)g_projectFile.c_str());
        data->put(2, (void*)g_projectDir.c_str());
        return 0;
    }
    return 0;
}

LRESULT CALLBACK staticProc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam) {
    WNDPROC old = (WNDPROC)GetWindowLongPtrW(hwnd, GWLP_USERDATA);
    if (message == WM_GETTEXT && !g_faultMode.empty()) {
        ++g_gettextCalls;
        if (g_faultMode == L"slow") { g_faultMode.clear(); Sleep(2600); }
        if (g_faultMode == L"fail") {
            if (wparam && lparam) ((wchar_t*)lparam)[0] = 0;
            return 0;
        }
    }
    return old ? CallWindowProcW(old, hwnd, message, wparam, lparam)
               : DefWindowProcW(hwnd, message, wparam, lparam);
}

BOOL CALLBACK subclassStatic(HWND hwnd, LPARAM) {
    if (className(hwnd) != L"Static") return TRUE;
    LONG_PTR style = GetWindowLongPtrW(hwnd, GWL_STYLE);
    if ((style & SS_TYPEMASK) == SS_ICON || (style & SS_TYPEMASK) == SS_BITMAP) return TRUE;
    if (GetWindowTextLengthW(hwnd) <= 0) return TRUE;
    WNDPROC old = (WNDPROC)GetWindowLongPtrW(hwnd, GWLP_WNDPROC);
    SetWindowLongPtrW(hwnd, GWLP_USERDATA, (LONG_PTR)old);
    SetWindowLongPtrW(hwnd, GWLP_WNDPROC, (LONG_PTR)staticProc);
    return FALSE;
}

void closeNative() {
    if (!ownWindow(g_native)) { g_native = nullptr; return; }
    SendMessageW(g_native, WM_CLOSE, 0, 0);
    // MessageBox teardown can otherwise wait for its modal loop to be re-entered.
    if (IsWindow(g_native) && IsWindowVisible(g_native)) ShowWindow(g_native, SW_HIDE);
    g_native = nullptr;
}

void closeXmsg() {
    // EndModal closes the native window and may destroy g_xmsg during its
    // nested message dispatch. Never hide/close that released handle again.
    if (g_xmsg) pXEndModal(g_xmsg, 0);
    g_xmsg = nullptr;
    g_xmsgHwnd = nullptr;
}

void closeTooltip() {
    if (ownWindow(g_tooltip)) DestroyWindow(g_tooltip);
    g_tooltip = nullptr;
}

LRESULT CALLBACK hookProc(int code, WPARAM wparam, LPARAM lparam) {
    if (code == HCBT_ACTIVATE && g_activeCreate) {
        HWND hwnd = (HWND)wparam;
        if (ownWindow(hwnd)) {
            bool match = false;
            if (g_activeCreate->name == "create_native") {
                wchar_t title[1024] = {};
                GetWindowTextW(hwnd, title, 1024);
                match = className(hwnd) == L"#32770" &&
                        g_activeCreate->title == instance::utf8(title);
                if (match) {
                    g_native = hwnd;
                    g_faultMode = instance::wide(g_activeCreate->fault);
                    if (!g_faultMode.empty()) EnumChildWindows(hwnd, subclassStatic, 0);
                }
            } else if (g_activeCreate->name == "create_xmsg") {
                HWND expected = g_xmsg ? XWnd_GetHWND((HWINDOW)g_xmsg) : nullptr;
                match = hwnd == expected;
                if (match) g_xmsgHwnd = hwnd;
            }
            if (match) SetTimer(g_control, kCreateTimer, 10, nullptr);
        }
    }
    return CallNextHookEx(g_hook, code, wparam, lparam);
}

BOOL CALLBACK findMenu(HWND hwnd, LPARAM result) {
    if (ownWindow(hwnd) && IsWindowVisible(hwnd) && className(hwnd) == L"#32768") {
        *(HWND*)result = hwnd;
        return FALSE;
    }
    return TRUE;
}

void createTooltip(const CommandPtr& command) {
    closeTooltip();
    INITCOMMONCONTROLSEX controls = {sizeof(controls), ICC_WIN95_CLASSES};
    InitCommonControlsEx(&controls);
    g_tooltip = CreateWindowExW(WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
                                TOOLTIPS_CLASSW, nullptr,
                                WS_POPUP | TTS_ALWAYSTIP | TTS_NOPREFIX,
                                0, 0, 0, 0, g_mainHwnd, nullptr,
                                GetModuleHandleW(nullptr), nullptr);
    if (!g_tooltip) { finish(command, false, nullptr, "tooltip_create_failed"); return; }
    std::wstring text = instance::wide(command->text);
    TOOLINFOW info = {};
    info.cbSize = sizeof(info);
    info.uFlags = TTF_IDISHWND | TTF_TRACK | TTF_ABSOLUTE;
    info.hwnd = g_mainHwnd;
    info.uId = (UINT_PTR)g_mainHwnd;
    info.lpszText = &text[0];
    SetLastError(ERROR_SUCCESS);
    LRESULT added = SendMessageW(g_tooltip, TTM_ADDTOOLW, 0, (LPARAM)&info);
    if (!added) {
        info.cbSize = sizeof(info) - sizeof(void*);
        added = SendMessageW(g_tooltip, TTM_ADDTOOLW, 0, (LPARAM)&info);
    }
    if (!added) {
        closeTooltip();
        finish(command, false, nullptr, "tooltip_add_failed_" + std::to_string(GetLastError()));
        return;
    }
    SendMessageW(g_tooltip, TTM_TRACKPOSITION, 0, MAKELPARAM(180, 180));
    SendMessageW(g_tooltip, TTM_TRACKACTIVATE, TRUE, (LPARAM)&info);
    ShowWindow(g_tooltip, SW_SHOWNOACTIVATE); // Fixture visibility, not production action.
    finish(command, ownWindow(g_tooltip) && IsWindowVisible(g_tooltip), g_tooltip,
           "tooltip_not_visible");
}

void dispatch(const CommandPtr& command) {
    const std::string& name = command->name;
    if (name == "status") {
        command->reply = status().dump();
        js::Value parsed;
        if (js::parse(command->reply, parsed)) parsed.set("id", command->id);
        command->reply = parsed.dump();
        SetEvent(command->event);
    } else if (name == "create_native") {
        if (ownWindow(g_native) && IsWindowVisible(g_native)) {
            finish(command, false, nullptr, "native_already_exists");
            return;
        }
        g_activeCreate = command;
        std::wstring text = instance::wide(command->text);
        std::wstring title = instance::wide(command->title);
        MessageBoxW(g_mainHwnd, text.c_str(), title.c_str(), MB_OK | MB_ICONERROR);
        KillTimer(g_control, kCreateTimer);
        g_native = nullptr;
        g_faultMode.clear();
        g_activeCreate.reset();
    } else if (name == "create_xmsg") {
        if (g_xmsg) { finish(command, false, nullptr, "xmsg_already_exists"); return; }
        g_activeCreate = command;
        std::wstring text = instance::wide(command->text);
        std::wstring title = instance::wide(command->title);
        g_xmsg = pXMsgCreate(title.c_str(), text.c_str(), 0x0001 | 0x8000,
                             g_mainHwnd, kModalStyle);
        if (!g_xmsg) {
            g_activeCreate.reset();
            finish(command, false, nullptr, "xmsg_create_failed");
            return;
        }
        void* local = g_xmsg;
        pXDoModal(local);
        KillTimer(g_control, kCreateTimer);
        if (g_xmsg == local) { g_xmsg = nullptr; g_xmsgHwnd = nullptr; }
        g_activeCreate.reset();
    } else if (name == "close_native" || name == "race_close") {
        if (name == "race_close") {
            finish(command);
            Sleep(300); // Let the HTTP worker discover and queue its read first.
        }
        closeNative();
        if (name != "race_close") finish(command, true);
    } else if (name == "close_xmsg") {
        closeXmsg();
        finish(command, true);
    } else if (name == "block_ui") {
        finish(command);
        Sleep((DWORD)command->duration);
    } else if (name == "create_menu") {
        if (g_menuActive) { finish(command, false, nullptr, "menu_already_exists"); return; }
        g_menu = CreatePopupMenu();
        if (!g_menu) { finish(command, false, nullptr, "menu_create_failed"); return; }
        std::wstring text = instance::wide(command->text);
        AppendMenuW(g_menu, MF_STRING, 1, text.c_str());
        AppendMenuW(g_menu, MF_SEPARATOR, 0, nullptr);
        AppendMenuW(g_menu, MF_STRING, 2, L"fixture-only menu item");
        g_pendingMenu = command;
        g_menuActive = true;
        g_menuHwnd = nullptr;
        SetTimer(g_control, kMenuTimer, 20, nullptr);
        TrackPopupMenu(g_menu, TPM_LEFTALIGN | TPM_TOPALIGN | TPM_RETURNCMD | TPM_NONOTIFY,
                       180, 180, 0, g_mainHwnd, nullptr);
        KillTimer(g_control, kMenuTimer);
        g_menuActive = false;
        g_menuHwnd = nullptr;
        g_pendingMenu.reset();
        DestroyMenu(g_menu);
        g_menu = nullptr;
    } else if (name == "close_menu") {
        if (g_menuActive) EndMenu(); // Only this host UI thread's own tracked menu.
        finish(command, true);
    } else if (name == "create_tooltip") {
        createTooltip(command);
    } else if (name == "close_tooltip") {
        closeTooltip();
        finish(command, true);
    } else if (name == "create_group") {
        if (ownWindow(g_groupDialog)) { finish(command, false, nullptr, "group_exists"); return; }
        std::wstring title = instance::wide(command->title);
        std::wstring text = instance::wide(command->text);
        g_groupDialog = CreateWindowExW(WS_EX_DLGMODALFRAME, L"#32770", title.c_str(),
            WS_POPUP | WS_VISIBLE | WS_CAPTION | WS_SYSMENU,
            320, 220, 440, 230, g_mainHwnd, nullptr, xi::g_hSelf, nullptr);
        if (!g_groupDialog) { finish(command, false, nullptr, "group_create_failed"); return; }
        CreateWindowExW(0, L"Static", text.c_str(), WS_CHILD | WS_VISIBLE | SS_LEFT | SS_NOPREFIX,
            18, 18, 390, 55, g_groupDialog, nullptr, xi::g_hSelf, nullptr);
        CreateWindowExW(0, L"Button", L"装饰分组（非输入）",
            WS_CHILD | WS_VISIBLE | BS_GROUPBOX, 14, 75, 398, 66,
            g_groupDialog, nullptr, xi::g_hSelf, nullptr);
        CreateWindowExW(0, L"Button", L"确定", WS_CHILD | WS_VISIBLE | BS_DEFPUSHBUTTON,
            158, 158, 100, 30, g_groupDialog, nullptr, xi::g_hSelf, nullptr);
        finish(command, true, g_groupDialog);
    } else if (name == "close_group") {
        if (ownWindow(g_groupDialog)) DestroyWindow(g_groupDialog);
        g_groupDialog = nullptr;
        finish(command, true);
    } else if (name == "delay_worker") {
        dialogs::g_testWorkerDelayMs = (DWORD)command->duration;
        finish(command, true);
    } else if (name == "set_xcg_timestamp") {
        // Fixture-only version guard test: modify our private DLL copy's header,
        // never an IDE, never the on-disk DLL and never executable code.
        auto* dos = (IMAGE_DOS_HEADER*)g_xcg;
        auto* nt = (IMAGE_NT_HEADERS64*)((unsigned char*)g_xcg + dos->e_lfanew);
        DWORD old = 0;
        bool ok = dos->e_magic == IMAGE_DOS_SIGNATURE && nt->Signature == IMAGE_NT_SIGNATURE &&
                  VirtualProtect(&nt->FileHeader.TimeDateStamp, sizeof(DWORD), PAGE_READWRITE, &old);
        if (ok) {
            nt->FileHeader.TimeDateStamp = (DWORD)command->duration;
            VirtualProtect(&nt->FileHeader.TimeDateStamp, sizeof(DWORD), old, &old);
        }
        finish(command, ok, nullptr, ok ? "" : "timestamp_guard_failed");
    } else if (name == "quit") {
        if (ownWindow(g_groupDialog)) DestroyWindow(g_groupDialog);
        g_groupDialog = nullptr;
        closeNative();
        closeXmsg();
        closeTooltip();
        if (g_menuActive) EndMenu();
        finish(command, true);
        PostQuitMessage(0);
    } else {
        finish(command, false, nullptr, "unknown_fixture_command");
    }
}

LRESULT CALLBACK controlProc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam) {
    if (message == kCommandMessage) {
        CommandPtr* posted = (CommandPtr*)lparam;
        CommandPtr command = *posted;
        delete posted;
        dispatch(command);
        return 0;
    }
    if (message == WM_TIMER && wparam == kMenuTimer) {
        HWND menu = nullptr;
        EnumWindows(findMenu, (LPARAM)&menu);
        if (menu && g_pendingMenu) {
            g_menuHwnd = menu;
            finish(g_pendingMenu, true, menu);
        }
        return 0;
    }
    if (message == WM_TIMER && wparam == kCreateTimer) {
        HWND created = g_activeCreate && g_activeCreate->name == "create_xmsg" ? g_xmsgHwnd : g_native;
        if (g_activeCreate && ownWindow(created) && IsWindowVisible(created)) {
            KillTimer(g_control, kCreateTimer);
            finish(g_activeCreate, true, created);
        }
        return 0;
    }
    return DefWindowProcW(hwnd, message, wparam, lparam);
}

void controller() {
    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) continue;
        js::Value request;
        std::string error;
        if (!js::parse(line, request, &error) || !request.isObj()) {
            js::Value value = js::Value::obj();
            value.set("ok", false);
            value.set("error", "invalid fixture JSON: " + error);
            std::cout << value.dump() << std::endl;
            continue;
        }
        CommandPtr command = std::make_shared<Command>();
        const js::Value* id = request.find("id");
        if (id) command->id = *id;
        const js::Value* field = request.find("command");
        command->name = field ? field->asStr() : "";
        field = request.find("title");
        command->title = field ? field->asStr() : "";
        field = request.find("text");
        command->text = field ? field->asStr() : "";
        field = request.find("fault");
        command->fault = field ? field->asStr() : "";
        field = request.find("duration");
        command->duration = field ? field->asInt() : 0;
        CommandPtr* posted = new CommandPtr(command);
        if (!PostMessageW(g_control, kCommandMessage, 0, (LPARAM)posted)) {
            delete posted;
            finish(command, false, nullptr, "control_post_failed");
        }
        DWORD wait = WaitForSingleObject(command->event, 15000);
        if (wait != WAIT_OBJECT_0 && command->reply.empty()) {
            finish(command, false, nullptr, "fixture_command_timeout");
        }
        std::cout << command->reply << std::endl;
        if (command->name == "quit") break;
    }
}

bool loadXcg() {
    g_xcg = GetModuleHandleW(L"XCGUI.dll");
    if (!g_xcg) g_xcg = LoadLibraryW(L"XCGUI.dll");
    if (!g_xcg) return false;
#define LOAD_XCG(field, name) field = (decltype(field))GetProcAddress(g_xcg, name); if (!field) return false
    LOAD_XCG(pXInit, "XInitXCGUI");
    LOAD_XCG(pXExit, "XExitXCGUI");
    LOAD_XCG(pXRun, "XRunXCGUI");
    LOAD_XCG(pXWndCreate, "XWnd_Create");
    LOAD_XCG(pXMsgCreate, "XMsg_Create");
    LOAD_XCG(pXDoModal, "XModalWnd_DoModal");
    LOAD_XCG(pXEndModal, "XModalWnd_EndModal");
    LOAD_XCG(pXShow, "XWnd_ShowWindow");
    LOAD_XCG(pXClose, "XWnd_CloseWindow");
#undef LOAD_XCG
    return true;
}
} // namespace

int wmain(int argc, wchar_t** argv) {
    if (argc < 4) return 2;
    g_uiThread = GetCurrentThreadId();
    xi::g_hSelf = GetModuleHandleW(nullptr);
    xi::g_funSet = fakeAbi;
    xi::g_ver = 339;
    g_projectFile = argv[3];
    std::filesystem::path project(g_projectFile);
    g_projectName = project.stem().wstring();
    g_projectDir = project.parent_path().wstring();

    if (!loadXcg() || !pXInit(FALSE)) return 3;
    int preferred = _wtoi(argv[2]);
    g_main = pXWndCreate(80, 80, 780, 500, argv[1], nullptr, kDefaultStyle);
    if (!g_main) { pXExit(); return 4; }
    xi::g_hMainFrame = (HWND)g_main;
    g_mainHwnd = XWnd_GetHWND((HWINDOW)g_main);
    if (!ownWindow(g_mainHwnd)) { pXExit(); return 5; }

    HELE output = XEdit_Create(12, 12, 744, 420, (HELE)g_main);
    if (!output) { pXExit(); return 6; }
    XEdit_EnableMultiLine(output, TRUE);
    XEdit_EnableReadOnly(output, TRUE);
    std::wstring outputText = std::wstring(argv[1]) +
        L"\r\n主窗输出错误：这不是弹窗正文。\r\nOutput error only 😀";
    XEdit_SetText(output, outputText.c_str());
    pXShow(g_main, SW_SHOW);

    WNDCLASSW cls = {};
    cls.lpfnWndProc = controlProc;
    cls.hInstance = xi::g_hSelf;
    cls.lpszClassName = L"XC-MCP-Next.DialogFixtureControl";
    if (!RegisterClassW(&cls)) { pXExit(); return 7; }
    g_control = CreateWindowExW(0, cls.lpszClassName, L"", 0, 0, 0, 0, 0,
                                HWND_MESSAGE, nullptr, cls.hInstance, nullptr);
    if (!g_control) { pXExit(); return 8; }
    g_hook = SetWindowsHookExW(WH_CBT, hookProc, nullptr, g_uiThread);
    if (!g_hook) { pXExit(); return 9; }

    dialogs::install();
    if (!http::start(preferred, mcp::handle)) {
        dialogs::shutdown();
        http::stop();
        pXExit();
        return 10;
    }
    std::cout << status().dump() << std::endl;
    std::thread input(controller);
    pXRun();

    if (input.joinable()) input.join();
    // Required order: both calls on the installing UI thread would deadlock if
    // HTTP workers were joined while the private reader window still existed.
    dialogs::shutdown();
    http::stop();
    if (g_hook) UnhookWindowsHookEx(g_hook);
    if (g_control) DestroyWindow(g_control);
    UnregisterClassW(cls.lpszClassName, cls.hInstance);
    pXClose(g_main);
    pXExit();
    return 0;
}
