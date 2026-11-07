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
LOG = os.path.join(PLUG, "xc_mcp_next.log")
BASE = "http://127.0.0.1:18766"

def ide_pids():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq 炫彩IDE.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, encoding="gbk", errors="replace").stdout
    return [l.split(",")[0].strip('"') for l in out.splitlines() if l.startswith('"')]

if ide_pids():
    subprocess.run(["taskkill", "/F", "/IM", "炫彩IDE.exe"], capture_output=True)
    time.sleep(2)
if os.path.exists(LOG):
    os.remove(LOG)
shutil.copy2(SRC, DST)
subprocess.Popen([EXE], cwd=IDE)
P("启动 IDE，等 14 秒 ...")
time.sleep(14)

def post(body, timeout=25):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = mkreq(BASE + "/mcp", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

def call(name, args=None):
    st, raw = post({"jsonrpc":"2.0","id":9,"method":"tools/call",
                    "params":{"name":name,"arguments":args or {}}})
    if st != 200:
        return f"HTTP {st} {raw[:200]}"
    obj = json.loads(raw)
    r = obj.get("result") or {}
    return (r.get("content") or [{}])[0].get("text", "")

P("\n--- 连通性 ---")
P(call("xc_status")[:200])
P(call("xc_get_project")[:200])

tests = [
    ("type16 getCodeEditor(无参)", {"type":16}),
    ("type16 getCodeEditor(main.xc)", {"type":16,"v0":"main.xc"}),
    ("type3 getSymbol(架构文本)", {"type":3,"v0":"架构文本"}),
    ("type4 getSymbolEx(main.xc,架构文本,0)", {"type":4,"v0":"main.xc","v1":"架构文本","id":0}),
    ("type17 module_info", {"type":17}),
    ("type7 getCurClassName", {"type":7}),
]

for title, args in tests:
    P("\n=== " + title + " ===")
    P(call("xc_debug_dump", args)[:900])

P("\n--- 插件日志尾部 ---")
if os.path.exists(LOG):
    P(open(LOG, encoding="utf-8", errors="replace").read()[-800:])