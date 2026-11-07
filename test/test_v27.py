# -*- coding: utf-8 -*-
"""27 工具版全量验证：toolCount / 隐藏编译 / 错误捕获 / 调试 / 窗口控制。"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import shutil
import subprocess
import time
import urllib.request
def _read_token():
    import os as _os
    p = _os.path.join(_os.environ.get("LOCALAPPDATA", ""), "xc-mcp", "token")
    if p and _os.path.exists(p):
        with open(p, "r", encoding="utf-8", errors="ignore") as _f:
            return _f.read().strip()
    return _os.environ.get("XC_MCP_TOKEN", "")


_TOKEN = _read_token()


def mkreq(url, data=None, headers=None, method=None):
    if headers is None:
        headers = {}
    if _TOKEN:
        headers.setdefault("X-XC-Token", _TOKEN)
    return urllib.request.Request(url, data=data, headers=headers, method=method)


DLL_SRC = r"I:\炫彩ide\xc-plugin\XC-MCP-Next\out\plug_xcmcp.dll"
DLL_DST = r"I:\炫彩ide\data\plug\plug_xcmcp.dll"
EXE = r"I:\炫彩ide\炫彩IDE.exe"
PROJ = r"E:\AIWork\gw2\插件启动器\启动器\启动器.xcproj"
SRC = r"E:\AIWork\gw2\插件启动器\启动器\main.xc"
URL = "http://127.0.0.1:18766/mcp"

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowW.restype = wt.HWND
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL


EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]


def ide_pids():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq 炫彩IDE.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, shell=True)
    txt = out.stdout.decode("gbk", "replace")
    pids = set()
    for line in txt.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower().startswith("炫彩ide.exe"):
            try:
                pids.add(int(parts[1]))
            except ValueError:
                pass
    return pids


def ide_windows():
    pids = ide_pids()
    found = []

    def cb(hwnd, lparam):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.GetWindowTextLengthW(hwnd) > 0:
            found.append((hwnd, bool(user32.IsWindowVisible(hwnd))))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return found


def ide_visible():
    wins = ide_windows()
    if not wins:
        return None
    return any(v for _, v in wins)


def rpc(method, params=None, timeout=240):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params or {}}).encode("utf-8")
    req = mkreq(URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def call(name, args=None, timeout=240):
    res = rpc("tools/call", {"name": name, "arguments": args or {}}, timeout)
    txt = ""
    for c in res.get("result", {}).get("content", []):
        if c.get("type") == "text":
            txt += c.get("text", "")
    try:
        return json.loads(txt)
    except Exception:
        return {"_raw": txt}


def health(timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen("http://127.0.0.1:18766/health", timeout=2) as r:
                if r.status == 200:
                    return round(time.time() - t0, 2)
        except Exception:
            pass
        time.sleep(0.5)
    return None


def restart_ide():
    subprocess.run(["taskkill", "/F", "/IM", "炫彩IDE.exe"], capture_output=True, shell=True)
    subprocess.run(["taskkill", "/F", "/IM", "启动器.exe"], capture_output=True, shell=True)
    time.sleep(1.5)
    shutil.copy2(DLL_SRC, DLL_DST)
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    subprocess.Popen([EXE, PROJ], cwd=r"I:\炫彩ide", startupinfo=si,
                     creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    h = health()
    time.sleep(10)
    return h


def alive(name):
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + name],
                         capture_output=True, shell=True)
    return name.lower() in out.stdout.decode("gbk", "replace").lower()


def md5(p):
    import hashlib
    return hashlib.md5(open(p, "rb").read()).hexdigest()


results = []


def check(label, ok, detail=""):
    results.append((label, ok, detail))
    print(("  PASS  " if ok else "  FAIL  ") + label + ("  " + detail if detail else ""))


orig_md5 = md5(SRC)
print("源码 md5:", orig_md5)

print("\n=== A. 工具清单 ===")
h = restart_ide()
st = call("xc_status")
check("health 就绪", h is not None, "%ss" % h)
check("toolCount == 27", st.get("toolCount") == 27, "实际 %s" % st.get("toolCount"))
names = [t.get("name") for t in call("xc_list_tools").get("tools", [])] if False else None
tl = rpc("tools/list", {})
tools = [t["name"] for t in tl["result"]["tools"]]
check("tools/list 长度 27", len(tools) == 27, "实际 %d" % len(tools))
for t in ("xc_ide_window", "xc_build_sync", "xc_debug_run"):
    check("存在 " + t, t in tools)
check("xc_debug_dump 已移除", "xc_debug_dump" not in tools)

print("\n=== B. 隐藏窗口无界面编译 ===")
print("  IDE 窗口可见(调用前):", ide_visible())
r = call("xc_build_sync", {"timeout": 120, "hide": True})
print("  IDE 窗口可见(调用后):", ide_visible())
check("编译完成", r.get("finished") is True, json.dumps(
    {k: r.get(k) for k in ("finished", "skipped", "compiled", "seconds")}, ensure_ascii=False))
check("编译成功 compiled", r.get("compiled") is True)
check("错误数 0", r.get("errorCount") == 0, "errorCount=%s" % r.get("errorCount"))
check("耗时合理(<60s)", (r.get("seconds") or 999) < 60, "%ss" % r.get("seconds"))
check("hidden 标记", r.get("hidden") is True)
check("窗口被隐藏", ide_visible() is False, str(ide_visible()))

print("\n=== C. 二次调用应 skipped ===")
r2 = call("xc_build_sync", {"timeout": 60, "hide": True})
check("skipped=true", r2.get("skipped") is True, json.dumps(
    {k: r2.get(k) for k in ("finished", "skipped", "seconds")}, ensure_ascii=False))

print("\n=== D. 语法错误捕获 ===")
raw = open(SRC, "rb").read()
try:
    bad = raw.decode("utf-8-sig")
    bad = bad.replace("\r\n", "\n")
    bad += "\n函数 整型 XCMCP_BAD(整型 a) {\n\t返回 a + \n}\n"
    bad = bad.replace("\n", "\r\n")
    open(SRC, "wb").write(b"\xef\xbb\xbf" + bad.encode("utf-8"))
    print("  写入临时错误代码:", os.path.getsize(SRC), "B")
    restart_ide()
    r3 = call("xc_build_sync", {"timeout": 120, "hide": True})
    print("  errors:", json.dumps(r3.get("errors", [])[:3], ensure_ascii=False))
    check("识别为失败", r3.get("compiled") is False or (r3.get("errorCount") or 0) > 0,
          "compiled=%s errorCount=%s failCode=%s" % (
              r3.get("compiled"), r3.get("errorCount"), r3.get("failCode")))
    check("错误定位到 XCMCP_BAD 附近行", any(
        "main.xc" in e.get("file", "") and int(e.get("line") or 0) > 300
        for e in r3.get("errors", [])), json.dumps(r3.get("errors", [])[:2], ensure_ascii=False))
finally:
    open(SRC, "wb").write(raw)
    print("  源码已恢复:", os.path.getsize(SRC), "B md5", md5(SRC))
    check("源码 md5 复原", md5(SRC) == orig_md5)

print("\n=== E. 恢复后重新编译 ===")
restart_ide()
r4 = call("xc_build_sync", {"timeout": 120, "hide": True})
check("恢复后编译成功", r4.get("compiled") is True and r4.get("errorCount") == 0,
      "compiled=%s errors=%s" % (r4.get("compiled"), r4.get("errorCount")))

print("\n=== F. 调试运行 ===")
restart_ide()
r5 = call("xc_debug_run", {"timeout": 180, "hide": True})
time.sleep(3)
print("  debugging:", r5.get("debugging"), "errors:", r5.get("errorCount"))
check("debugging=true", r5.get("debugging") is True)
check("调试进程存活", alive("启动器.exe"))
subprocess.run(["taskkill", "/F", "/IM", "启动器.exe"], capture_output=True, shell=True)

print("\n=== G. 窗口显隐 ===")
w1 = call("xc_ide_window", {"show": True})
time.sleep(0.8)
check("show=true 后可见", ide_visible() is True, json.dumps(w1, ensure_ascii=False))
w2 = call("xc_ide_window", {"show": False})
time.sleep(0.8)
check("show=false 后隐藏", ide_visible() is False, json.dumps(w2, ensure_ascii=False))

print("\n=== H. 收尾 ===")
call("xc_ide_window", {"show": False})
subprocess.run(["taskkill", "/F", "/IM", "启动器.exe"], capture_output=True, shell=True)
check("源码最终 md5 一致", md5(SRC) == orig_md5)

ok = sum(1 for _, o, _ in results if o)
print("\n" + "=" * 60)
print("汇总: 成功 %d / 失败 %d / 共 %d" % (ok, len(results) - ok, len(results)))
for lbl, o, d in results:
    if not o:
        print("  FAILED:", lbl, d)