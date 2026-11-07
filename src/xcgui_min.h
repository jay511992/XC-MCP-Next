#pragma once

#ifndef XWM_WINDPROC
#define XWM_WINDPROC (0x7000 + 2)
#endif
enum layout_size_ {
    layout_size_fixed = 0, layout_size_fill, layout_size_auto,
    layout_size_weight, layout_size_percent, layout_size_disable
};
#include <windows.h>

typedef void* HELE;
typedef void* HWINDOW;

extern "C" {
HELE __stdcall XPane_Create(const wchar_t* pName, int width, int height, HWINDOW hFrameWnd);
void __stdcall XPane_SetView(HELE hPane, HELE hView);
void __stdcall XPane_ShowPane(HELE hPane, BOOL activate);
BOOL __stdcall XPane_IsShowPane(HELE hPane);
HELE __stdcall XEdit_Create(int x, int y, int width, int height, HELE hParent);
void __stdcall XEdit_EnableMultiLine(HELE hEle, BOOL enable);
void __stdcall XEdit_EnableReadOnly(HELE hEle, BOOL enable);
void __stdcall XEdit_EnableAutoWrap(HELE hEle, BOOL enable);
void __stdcall XEdit_MoveEnd(HELE hEle);
BOOL __stdcall XEdit_DeleteRow(HELE hEle, int row);
void __stdcall XWidget_LayoutItem_SetWidth(HELE hObject, layout_size_ type, int width);
void __stdcall XWidget_LayoutItem_SetHeight(HELE hObject, layout_size_ type, int height);
void __stdcall XWidget_SetID(HELE hObject, int id);
BOOL __stdcall XWnd_RegEventC(HWINDOW hWindow, int event, void* callback);
BOOL __stdcall XWnd_RemoveEventC(HWINDOW hWindow, int event, void* callback);
BOOL __stdcall XC_PostMessage(HWINDOW hWindow, UINT message, WPARAM wParam, LPARAM lParam);

HWND __stdcall XWnd_GetHWND(HWINDOW hWindow);

int  __stdcall XEdit_GetLength(HELE hEle);
int  __stdcall XEdit_GetRowCount(HELE hEle);
int  __stdcall XEdit_GetText(HELE hEle, wchar_t* pOut, int cchMax);
const wchar_t* __stdcall XEdit_GetText_Temp(HELE hEle);
int  __stdcall XEdit_GetTextRow(HELE hEle, int iRow, wchar_t* pOut, int cchMax);
int  __stdcall XEdit_SetText(HELE hEle, const wchar_t* pText);
int  __stdcall XEdit_GetCurRow(HELE hEle);
int  __stdcall XEdit_GetCurCol(HELE hEle);
BOOL __stdcall XEdit_SetCurPos(HELE hEle, int iRow, int iCol);
BOOL __stdcall XEdit_SetCurPosEx(HELE hEle, int iRow, int iCol);
int  __stdcall XEdit_GetLengthRow(HELE hEle, int iRow);
BOOL __stdcall XEdit_InsertText(HELE hEle, int iRow, int iCol, const wchar_t* pText);
BOOL __stdcall XEdit_AddText(HELE hEle, const wchar_t* pText);
BOOL __stdcall XEdit_AddTextUser(HELE hEle, const wchar_t* pText);
BOOL __stdcall XEdit_SelectAll(HELE hEle);
BOOL __stdcall XEdit_Undo(HELE hEle);
BOOL __stdcall XEdit_Redo(HELE hEle);

int  __stdcall XTree_GetCount(HELE hEle);
HELE __stdcall XTree_GetFirstChildItem(HELE hEle, HELE hParent);
HELE __stdcall XTree_GetNextSiblingItem(HELE hEle, HELE hItem);
HELE __stdcall XTree_GetParentItem(HELE hEle, HELE hItem);
const wchar_t* __stdcall XTree_GetItemText(HELE hEle, HELE hItem);
BOOL __stdcall XTree_SetSelectItem(HELE hEle, HELE hItem);
HELE __stdcall XTree_GetSelectItem(HELE hEle);
BOOL __stdcall XTree_IsExpand(HELE hEle, HELE hItem);
BOOL __stdcall XTree_ExpandItem(HELE hEle, HELE hItem, BOOL bExpand);

}
