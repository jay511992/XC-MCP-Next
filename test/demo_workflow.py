# -*- coding: utf-8 -*-
"""用真实工作流验证插件（只读 + 编译，不改任何源码）。"""
import sys, json, time, urllib.request
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
BASE = "http://127.0.0.1:18766"

def call(name, args=None, timeout=90):
    body = {"jsonrpc":"2.0","id":9,"method":"tools/call",
            "params":{"name":name,"arguments":args or {}}}
    req = mkreq(BASE + "/mcp",
                                 data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        o = json.loads(r.read().decode("utf-8", "replace"))
    res = o.get("result") or {}
    txt = (res.get("content") or [{}])[0].get("text", "")
    try:
        return json.loads(txt)
    except Exception:
        return {"_raw": txt}

P("=" * 70)
P("1) 工程上下文")
P("=" * 70)
proj = call("xc_get_project")
P(f"  工程: {proj.get('name')}")
P(f"  目录: {proj.get('dir')}")
P(f"  项目文件: {proj.get('project_file')}")

st = call("xc_parse_status")
P(f"  解析状态: parsing={st.get('parsing')} count={st.get('count')}")

P()
P("=" * 70)
P("2) 代码结构（静态解析）")
P("=" * 70)
fns = call("xc_list_functions")
funcs = fns.get("functions", [])
P(f"  共 {fns.get('count')} 个函数，扫描 {fns.get('files_scanned')} 个文件")
for f in funcs[:6]:
    P(f"    L{f['line']:<4} {f['name']}")
if len(funcs) > 6:
    P(f"    ... 还有 {len(funcs)-6} 个")

P()
P("=" * 70)
P("3) 符号定位（IDE 索引）")
P("=" * 70)
for nm in ("取宿主路径", "启动器窗口类"):
    s = call("xc_symbol", {"name": nm})
    kind = "函数" if s.get("symbol_type") == 1002 else ("类" if s.get("symbol_type") == 1001 else s.get("symbol_type"))
    P(f"  {nm}: {kind} @ L{s.get('lineno')} 列{s.get('col1')}")

P()
P("=" * 70)
P("4) 编辑器读取当前文件")
P("=" * 70)
ed = call("xc_editor_get")
P(f"  文件: {ed.get('file')}")
P(f"  {ed.get('length')} 字符 / {ed.get('rows')} 行")
first = ed.get("text", "").splitlines()[:3]
for i, ln in enumerate(first, 1):
    P(f"    {i}: {ln}")

P()
P("=" * 70)
P("5) 编译并读取结果")
P("=" * 70)
b = call("xc_build")
P(f"  投递: {b.get('channel')} key={b.get('action')} hwnd={b.get('hwnd')}")
P("  等待 10 秒 ...")
time.sleep(10)
out = call("xc_get_output", {"tailLines": 8})
tail = out.get("text", "")
for ln in tail.splitlines()[-6:]:
    P(f"    {ln}")
err_lines = [l for l in tail.splitlines() if "错误列表" in l]
if err_lines:
    P(f"  -> {err_lines[-1]}")

P()
P("=" * 70)
P("6) 模块元信息")
P("=" * 70)
m = call("xc_module_info", {"path": r"I:\炫彩ide\data\XC-Module\核心[官方]\炫彩界面库\module_xcgui.h"})
P(f"  模块: {m.get('module')}  作者: {m.get('author')}  版本: {m.get('version')}")
P(f"  日期: {m.get('date')}  MD5: {m.get('extra')}")