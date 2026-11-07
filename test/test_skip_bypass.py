# -*- coding: utf-8 -*-
"""验证：删/改 IDE 中间产物能否绕过「跳过编译」（同生命周期二次编译）。"""
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
OBJ = r"E:\AIWork\gw2\插件启动器\启动器\Debug64\obj"
CACHE = os.path.join(OBJ, "cache")
URL = "http://127.0.0.1:18766/mcp"


def rpc(method, params=None, timeout=180):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params or {}}).encode("utf-8")
    req = mkreq(URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def call(name, args=None, timeout=180):
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


def restart():
    subprocess.run(["taskkill", "/F", "/IM", "炫彩IDE.exe"], capture_output=True, shell=True)
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


def build():
    r = call("xc_build_sync", {"timeout": 90, "hide": True})
    return (r.get("skipped"), r.get("compiled"), r.get("errorCount"), r.get("seconds"))


def mtime(p):
    try:
        return round(os.path.getmtime(p), 1)
    except OSError:
        return None


print("=== 准备 ===")
print("  obj dir:", os.path.isdir(OBJ), " cache:", os.path.isdir(CACHE))
print("  cache 内容:", sorted(os.listdir(CACHE)) if os.path.isdir(CACHE) else None)
print("  obj 内容:", sorted(f for f in os.listdir(OBJ) if os.path.isfile(os.path.join(OBJ, f))) if os.path.isdir(OBJ) else None)
cpp = os.path.join(CACHE, "main__.cpp")
print("  main__.cpp mtime:", mtime(cpp))

restart()
print("\n=== B1 首次编译（基准）===")
print("  ", build())

print("\n=== B2 二次编译（应 skipped）===")
print("  ", build())

# 实验 1：删 cache\main__.cpp
bak = cpp + ".xcbak"
if os.path.exists(cpp):
    shutil.copy2(cpp, bak)
    os.remove(cpp)
    print("\n=== E1 删除 cache\\main__.cpp → 编译 ===")
    print("  ", build())
    print("  main__.cpp 是否重建:", os.path.exists(cpp), "mtime:", mtime(cpp))
else:
    print("\n=== E1 跳过：cache\\main__.cpp 不存在 ===")

# 实验 2：改 cache\main__.cpp 尾部（追加注释）
if os.path.exists(cpp):
    with open(cpp, "ab") as f:
        f.write(b"\n// XCMCP_MTIME_PROBE\n")
    print("\n=== E2 改 cache\\main__.cpp 尾部 → 编译 ===")
    print("  ", build())

# 实验 3：删 Debug64\obj\main__.obj
mainobj = os.path.join(OBJ, "main__.obj")
if os.path.exists(mainobj):
    os.remove(mainobj)
print("\n=== E3 删 Debug64\\obj\\main__.obj → 编译 ===")
print("  ", build())
print("  main__.obj 是否重建:", os.path.exists(mainobj), "mtime:", mtime(mainobj))

# 实验 4：删 cache 里全部文件
print("\n=== E4 清空 cache 目录 → 编译 ===")
for f in os.listdir(CACHE):
    p = os.path.join(CACHE, f)
    try:
        os.remove(p)
    except OSError:
        pass
print("  cache 剩余:", os.listdir(CACHE))
print("  ", build())
print("  cache 重建:", sorted(os.listdir(CACHE)))

# 实验 5：改动磁盘源文件 + 删 obj（最接近真实场景）
print("\n=== E5 改源文件 + 删 obj → 编译 ===")
SRC = r"E:\AIWork\gw2\插件启动器\启动器\main.xc"
raw = open(SRC, "rb").read()
try:
    txt = raw.decode("utf-8-sig").replace("\r\n", "\n")
    txt += "\n函数 整型 XCMCP_E5() {\n\t返回 1\n}\n"
    open(SRC, "wb").write(b"\xef\xbb\xbf" + txt.replace("\n", "\r\n").encode("utf-8"))
    if os.path.exists(mainobj):
        os.remove(mainobj)
    print("  ", build())
finally:
    open(SRC, "wb").write(raw)
    print("  源文件已恢复:", os.path.getsize(SRC), "B")