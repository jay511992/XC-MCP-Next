# -*- coding: utf-8 -*-
import os, sys, json, time, subprocess, urllib.request
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
BASE = "http://127.0.0.1:18766"
PROJ_FILES = {
    r"E:\AIWork\gw2\插件启动器\启动器\main.xc": 13925,
    r"E:\AIWork\gw2\插件启动器\启动器\common.xh": 235,
}

P("=== 工程文件状态 ===")
for f, expect in PROJ_FILES.items():
    d = open(f, "rb").read()
    ok = len(d) == expect
    P(f"  {'OK ' if ok else 'DIFF'} {os.path.basename(f)}: {len(d)} B (期望 {expect})  BOM={d[:3]==b'\xef\xbb\xbf'}  CRLF={d.count(b'\r\n')}")

subprocess.Popen([EXE], cwd=IDE)
P("\n启动 IDE，等 15 秒 ...")
time.sleep(15)

def raw(name, args=None, timeout=60):
    body = {"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":name,"arguments":args or {}}}
    req = mkreq(BASE + "/mcp", data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        o = json.loads(r.read().decode("utf-8", "replace"))
    res = o.get("result") or {}
    txt = (res.get("content") or [{}])[0].get("text", "")
    try:
        return json.loads(txt)
    except Exception:
        return {"_raw": txt}

P("\n=== 最终编译 ===")
raw("xc_build")
time.sleep(10)
out = raw("xc_get_output", {"tailLines": 8})
P(out.get("text", "")[-450:])