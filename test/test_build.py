# -*- coding: utf-8 -*-
import os, sys, json, time, shutil, subprocess, urllib.request, urllib.error
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

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
P = lambda *a: (print(*a), sys.stdout.flush())

IDE = r"I:\炫彩ide"
EXE = os.path.join(IDE, "炫彩IDE.exe")
PLUG = os.path.join(IDE, "data", "plug")
SRC = os.path.join(IDE, r"xc-plugin\XC-MCP-Next\out\plug_xcmcp.dll")
DST = os.path.join(PLUG, "plug_xcmcp.dll")
BASE = "http://127.0.0.1:18766"

def pids():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq 炫彩IDE.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, encoding="gbk", errors="replace").stdout
    return [l.split(",")[0].strip('"') for l in out.splitlines() if l.startswith('"')]

if pids():
    subprocess.run(["taskkill", "/F", "/IM", "炫彩IDE.exe"], capture_output=True)
    time.sleep(2)
shutil.copy2(SRC, DST)
subprocess.Popen([EXE], cwd=IDE)
time.sleep(15)

def call(name, args=None, limit=500):
    body = {"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":name,"arguments":args or {}}}
    req = mkreq(BASE + "/mcp", data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            o = json.loads(r.read().decode("utf-8","replace"))
            res = o.get("result") or {}
            return ("[ERR] " if res.get("isError") else "") + (res.get("content") or [{}])[0].get("text","")[:limit]
    except Exception as e:
        return f"{type(e).__name__}: {e}"

P("=== xc_build ===")
P(call("xc_build"))

P("\n等待 8 秒让 IDE 编译 ...")
time.sleep(8)

P("\n=== xc_get_output (末尾 12 行) ===")
P(call("xc_get_output", {"tailLines": 12}, 900))

P("\n=== xc_menu_cmd(compile) ===")
P(call("xc_menu_cmd", {"command": "compile"}))