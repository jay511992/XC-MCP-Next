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

def pids():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq 炫彩IDE.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, encoding="gbk", errors="replace").stdout
    return [l.split(",")[0].strip('"') for l in out.splitlines() if l.startswith('"')]

if pids():
    subprocess.run(["taskkill", "/F", "/IM", "炫彩IDE.exe"], capture_output=True)
    time.sleep(2)
shutil.copy2(SRC, DST)
P(f"部署 {os.path.getsize(DST):,} B")
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
        d = json.loads(txt)
        d["_err"] = res.get("isError", False)
        return d
    except Exception:
        return {"_raw": txt, "_err": res.get("isError", False)}

CASES = [
    ("xc_status", {}),
    ("xc_get_project", {}),
    ("xc_parse_status", {}),
    ("xc_reset_parse_count", {}),
    ("xc_server", {"action":"status"}),
    ("xc_ide_log", {"text":"[XC-MCP-Next] final round2"}),
    ("xc_get_output", {"tailLines":3}),
    ("xc_read_file", {"path": r"E:\AIWork\gw2\插件启动器\启动器\common.xh"}),
    ("xc_list_projects", {"root": r"E:\AIWork\gw2\插件启动器"}),
    ("xc_editor_get", {}),
    ("xc_editor_set", {"path": r"E:\AIWork\gw2\插件启动器\启动器\common.xh", "text": "//预编译头文件\r\n"}),
    ("xc_symbol", {"name":"取宿主路径"}),
    ("xc_goto_function", {"name":"架构文本"}),
    ("xc_list_functions", {"filter":"插件"}),
    ("xc_tree_probe", {"action":"dump"}),
    ("xc_module_info", {"path": r"I:\炫彩ide\data\XC-Module\核心[官方]\炫彩界面库\module_xcgui.h"}),
    ("xc_new_file", {"name":"zz_xcmcp_test"}),
    ("xc_add_file", {"path": r"E:\AIWork\gw2\插件启动器\启动器\main.xc", "open": False}),
    ("xc_add_dir_files", {"dir": r"E:\AIWork\gw2\插件启动器\启动器", "ext":"xc", "open": False}),
    ("xc_open_in_ide", {"path": r"E:\AIWork\gw2\插件启动器\启动器\main.xc"}),
    ("xc_save_all", {}),
    ("xc_build", {}),
    ("xc_menu_cmd", {"command":"compile"}),
    ("xc_probe_cmd", {"cmd_id":20}),
]

ok = 0
fail = 0
P("")
for name, args in CASES:
    r = raw(name, args)
    if r.get("_err") or r.get("ok") is False:
        fail += 1
        msg = r.get("error") or r.get("_raw", "")[:120]
        P(f"  [--] {name:<20} {str(msg)[:100]}")
    else:
        ok += 1
        keys = [k for k in r.keys() if k != "_err"]
        P(f"  [OK] {name:<20} {str({k: r[k] for k in list(r)[:3]})[:100]}")

P(f"\n汇总: 成功 {ok} / 失败 {fail} / 共 {len(CASES)}")

# 清理测试文件
tf = r"E:\AIWork\gw2\插件启动器\启动器\zz_xcmcp_test.xc"
if os.path.exists(tf):
    os.remove(tf)
    P(f"已清理 {tf}")