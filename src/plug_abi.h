#pragma once

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef _WINSOCKAPI_
#define _WINSOCKAPI_
#endif

#include <windows.h>

namespace xi {

constexpr int SET_ERROR              = 0;
constexpr int SET_BIND_EVENT         = 1;
constexpr int SET_DEBUG_ADDSTRING    = 2;
constexpr int SET_GET_SYMBOL         = 3;
constexpr int SET_GET_SYMBOL_EX      = 4;
constexpr int SET_GET_VIEW           = 5;
constexpr int SET_GET_HELE           = 6;
constexpr int SET_GET_CUR_CLASSNAME  = 7;
constexpr int SET_GET_PROJECT        = 8;
constexpr int SET_CODE_UPDATE        = 9;
constexpr int SET_GET_PARENT         = 10;
constexpr int SET_GET_CLASS_INHERIT  = 11;
constexpr int SET_IMPORT_FILE        = 12;
constexpr int SET_THREADTASK_ADD     = 13;
constexpr int SET_THREADTASK_RELEASE = 14;
constexpr int SET_CREATE_EDITOR      = 15;
constexpr int SET_GET_CODE_EDITOR    = 16;
constexpr int SET_MODULE_INFO        = 17;
constexpr int SET_ENABLE_AI          = 18;
constexpr int SET_TASK_STRING        = 19;
constexpr int SET_TASK_PLUG          = 20;
constexpr int SET_TASK_PLUG_STRING   = 21;

// Current 3.3.9 create-view event. The older AI sample's enum maps 10 to a UI
// property event; do not use that stale numeric value for pane creation.
constexpr int EVENT_CREATE_VIEW      = 11;

constexpr int PANE_FILEVIEW          = 1001;
constexpr int PANE_OUTPUT_DEBUG      = 1012;
constexpr int PANE_OUTPUT_DEBUG_EDIT = 1101;
constexpr int PANE_ID_MIN            = 1000;
constexpr int PANE_ID_MAX            = 1101;

using HELEx = void*;

struct Data {
    int   type;
    int   id;
    void* v[31];

    void  clear();
    void* get(int slot) const;
    void  put(int slot, void* value);
};

using FunSet = int (*)(Data*);

extern FunSet  g_funSet;
extern HWND    g_hMainFrame; // ABI value is XCGUI HWINDOW, not a Win32 HWND.
extern int     g_ver;
extern HMODULE g_hSelf;

bool  ready();
int   call(Data& d);
bool  getHele(int paneId, HELEx* out);
bool  debugAddString(const wchar_t* text);
int   getProjectRaw(void** p1, void** p2, void** p3);
HWND  mainHwnd(); // Convert the XCGUI handle and verify ownership by this PID.

void  logLine(const wchar_t* fmt, ...);

}
