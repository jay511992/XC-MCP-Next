#pragma once

#include <windows.h>

typedef void* HELE;
typedef void* HWINDOW;

extern "C" {

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
