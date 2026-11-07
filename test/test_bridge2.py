# -*- coding: utf-8 -*-
import os, sys, json, time, subprocess, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
P = lambda *a: (print(*a), sys.stdout.flush())

IDE = r"I:\炫彩ide"
EXE = os.path.join(IDE, "炫彩IDE.exe")
BRIDGE = os.path.join(IDE, r"xc-plugin\XC-MCP-Next\bridge\stdio_bridge.py")
BASE = "http://127.0.0.1:18766"

def pids():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq 炫彩IDE.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, encoding="gbk", errors="replace").stdout
    return [l.split(",")[0].strip('"') for l in out.splitlines() if l.startswith('"')]

if not pids():
    subprocess.Popen([EXE], cwd=IDE)
    P("启动 IDE ...")

for i in range(30):
    time.sleep(1)
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=2) as r:
            if r.status == 200:
                P(f"+{i+1}s 服务就绪")
                break
    except Exception:
        if i % 5 == 4:
            P(f"  +{i+1}s 等待中…")
else:
    P("!! 30 秒内服务未就绪")
    sys.exit(1)

msgs = [
    {"jsonrpc":"2.0","id":1,"method":"initialize",
     "params":{"protocolVersion":"2024-11-05","capabilities":{},
               "clientInfo":{"name":"bridge-test","version":"1"}}},
    {"jsonrpc":"2.0","method":"notifications/initialized"},
    {"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}},
    {"jsonrpc":"2.0","id":3,"method":"tools/call",
     "params":{"name":"xc_get_project","arguments":{}}},
]

payload = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in msgs)
r = subprocess.run([sys.executable, BRIDGE], input=payload.encode("utf-8"),
                   capture_output=True, timeout=120)

P(f"\nbridge exit={r.returncode}")
lines = [l for l in r.stdout.decode("utf-8", "replace").splitlines() if l.strip()]
P(f"响应 {len(lines)} 行（期望 3，notification 不响应）")
ok = 0
for l in lines:
    o = json.loads(l)
    rid = o.get("id")
    if "result" not in o:
        P(f"  [{rid}] ERROR {o.get('error')}")
        continue
    if rid == 1:
        P(f"  [1] initialize -> {o['result']['serverInfo']}")
        ok += 1
    elif rid == 2:
        P(f"  [2] tools/list -> {len(o['result']['tools'])} 个工具")
        ok += 1
    elif rid == 3:
        P(f"  [3] tools/call -> {o['result']['content'][0]['text'][:160]}")
        ok += 1
P(f"\n桥接验证: {ok}/3 通过")