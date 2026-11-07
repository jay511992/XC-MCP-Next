# -*- coding: utf-8 -*-
import os, sys, json, time, shutil, subprocess, urllib.request
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
DISK = r"E:\AIWork\gw2\插件启动器\启动器\main.xc"

def pids():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq 炫彩IDE.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, encoding="gbk", errors="replace").stdout
    return [l.split(",")[0].strip('"') for l in out.splitlines() if l.startswith('"')]

def stat(tag):
    d = open(DISK, "rb").read()
    crlf = d.count(b"\r\n")
    lf = d.count(b"\n")
    P(f"  {tag}: {len(d)} B  BOM={d[:3]==b'\xef\xbb\xbf'}  CRLF={crlf}  总LF={lf}")
    return d

if pids():
    subprocess.run(["taskkill", "/F", "/IM", "炫彩IDE.exe"], capture_output=True)
    time.sleep(2)
shutil.copy2(SRC, DST)
subprocess.Popen([EXE], cwd=IDE)
P("等 15 秒 ...")
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

P("\n=== 写前 ===")
d0 = stat("disk")

P("\n=== editor_set 写回相同内容 ===")
cur = raw("xc_editor_get")
text = cur.get("text", "")
P(f"  编辑器读到 {len(text)} 字符")
r = raw("xc_editor_set", {"path": DISK, "text": text})
P(f"  {r.get('ok')} channel={r.get('channel')}")

P("\n=== 写后 ===")
d1 = stat("disk")
P(f"  大小变化 {len(d1) - len(d0)} B  (期望 0)")

P("\n=== 编译验证 ===")
raw("xc_build")
time.sleep(9)
out = raw("xc_get_output", {"tailLines": 6})
P(out.get("text", "")[-350:])