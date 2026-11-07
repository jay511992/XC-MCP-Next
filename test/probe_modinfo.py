# -*- coding: utf-8 -*-
import sys, json, urllib.request
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
BASE = "http://127.0.0.1:18766"

def call(name, args=None):
    body = {"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":name,"arguments":args or {}}}
    req = mkreq(BASE + "/mcp", data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            o = json.loads(r.read().decode("utf-8","replace"))
            return (o.get("result") or {}).get("content",[{}])[0].get("text","")
    except Exception as e:
        return f"{type(e).__name__}: {e}"

MOD = r"I:\炫彩ide\data\XC-Module\核心[官方]\炫彩界面库\module_xcgui.h"

print("=== type17 v0=模块全路径 ===")
print(call("xc_debug_dump", {"type":17, "v0": MOD})[:1200])

print("\n=== type17 v1=模块文件名 ===")
print(call("xc_debug_dump", {"type":17, "v1": "module_xcgui.h"})[:1200])

print("\n=== type17 v2=模块名 ===")
print(call("xc_debug_dump", {"type":17, "v2": "炫彩界面库"})[:1200])