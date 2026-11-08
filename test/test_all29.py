# -*- coding: utf-8 -*-
"""XC-MCP-Next 30 工具回归（保留历史文件名；含业务路径，勿直接用于隔离验收）。"""
import io, json, os, sys, urllib.request
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

import xml.etree.ElementTree as ET

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

URL = "http://127.0.0.1:18766/mcp"
PROJ = r"E:\AIWork\gw2\插件启动器\启动器"
XCPROJ = os.path.join(PROJ, "启动器.xcproj")
MAIN = os.path.join(PROJ, "main.xc")

_rid = [0]
OK = []
FAIL = []


def rpc(method, params=None, timeout=200):
    _rid[0] += 1
    body = {"jsonrpc": "2.0", "id": _rid[0], "method": method}
    if params is not None:
        body["params"] = params
    req = mkreq(URL, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def tool(_tname, **kw):
    r = rpc("tools/call", {"name": _tname, "arguments": kw})
    res = r.get("result", {})
    txt = ""
    for c in res.get("content", []):
        if c.get("type") == "text":
            txt += c.get("text", "")
    try:
        val = json.loads(txt)
    except Exception:
        val = {"_raw": txt}
    return val, bool(res.get("isError"))


def check(label, cond, detail=""):
    if cond:
        OK.append(label)
        print("  OK   %s %s" % (label, detail))
    else:
        FAIL.append(label)
        print("  FAIL %s %s" % (label, detail))


def main():
    with open(MAIN, "rb") as f:
        main_before = f.read()
    source_lines = main_before.decode("utf-8-sig").splitlines()
    symbol_line = next(i for i, line in enumerate(source_lines, 1)
                       if "函数" in line and "取宿主路径(" in line)
    print("== 1. 协议与工具清单 ==")
    init = rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                              "clientInfo": {"name": "regress", "version": "1"}})
    check("initialize", init["result"]["serverInfo"]["name"] == "xcide-mcp")
    tl = rpc("tools/list")
    names = [t["name"] for t in tl["result"]["tools"]]
    check("tools/list==30", len(names) == 30, "(%d)" % len(names))
    for n in ("xc_module_list", "xc_module_search", "xc_module_add",
              "xc_module_remove", "xc_module_create", "xc_get_dialogs"):
        check("has " + n, n in names)
    for n in ("xc_ide_window", "xc_build_sync", "xc_debug_run", "xc_debug_dump"):
        check("removed " + n, n not in names)

    print("== 2. 状态与工程 ==")
    v, e = tool("xc_status")
    check("xc_status", (not e) and v.get("toolCount") == 30 and v.get("httpRunning"),
          "toolCount=%s ver=%s" % (v.get("toolCount"), v.get("ver")))
    v, e = tool("xc_get_project")
    check("xc_get_project", (not e) and v.get("dir") == PROJ, str(v.get("dir"))[:60])
    v, e = tool("xc_parse_status")
    check("xc_parse_status", (not e) and "parsing" in json.dumps(v, ensure_ascii=False))
    v, e = tool("xc_reset_parse_count")
    check("xc_reset_parse_count", not e)

    print("== 3. 文件与编辑器 ==")
    v, e = tool("xc_read_file", path=MAIN)
    check("xc_read_file", (not e) and v.get("rawSize") == len(main_before), "rawSize=%s size=%s" % (v.get("rawSize"), v.get("size")))
    v, e = tool("xc_editor_get")
    check("xc_editor_get", (not e) and v.get("length", 0) > 7000, "len=%s" % v.get("length"))
    v, e = tool("xc_list_functions")
    check("xc_list_functions", (not e) and v.get("count", 0) >= 10, "count=%s" % v.get("count"))
    v, e = tool("xc_symbol", name="取宿主路径")
    check("xc_symbol", (not e) and v.get("lineno") == symbol_line, "lineno=%s" % v.get("lineno"))
    v, e = tool("xc_goto_function", name="取宿主路径")
    check("xc_goto_function", (not e) and v.get("moved"), "moved=%s" % v.get("moved"))

    print("== 4. 模块链路 ==")
    v, e = tool("xc_module_info", name="module_base.h")
    check("xc_module_info", (not e) and v.get("module") == "基础模块", str(v.get("module")))
    v, e = tool("xc_module_list", limit=5)
    check("xc_module_list", (not e) and v.get("count") == 5 and v.get("hasProject"),
          "scanned=%s" % v.get("totalScanned"))
    v, e = tool("xc_module_search", query="线程")
    check("xc_module_search", (not e) and v.get("count", 0) >= 3, "hits=%s" % v.get("count"))
    before = os.path.getsize(XCPROJ)
    v, e = tool("xc_module_add", file="module_threadpool.h")
    check("xc_module_add", (not e) and v.get("added") and v.get("moduleCount") == 5,
          "xcproj %d -> %d" % (before, os.path.getsize(XCPROJ)))
    v, e = tool("xc_module_remove", file="module_threadpool.h")
    check("xc_module_remove", (not e) and v.get("removed") and v.get("moduleCount") == 4,
          "xcproj back to %d" % os.path.getsize(XCPROJ))
    check("xcproj restored", os.path.getsize(XCPROJ) == before, "%d B" % os.path.getsize(XCPROJ))

    tmpl_h = os.path.join(PROJ, "module_regress.h")
    tmpl_c = os.path.join(PROJ, "module_regress.cpp")
    v, e = tool("xc_module_create", name="regress", alias="回归模块", addToProject=False)
    check("xc_module_create", (not e) and os.path.exists(tmpl_h) and os.path.exists(tmpl_c))
    if os.path.exists(tmpl_h) and os.path.exists(tmpl_c):
        hd = io.open(tmpl_h, "rb").read()
        cd = io.open(tmpl_c, "rb").read()
        check("create BOM", hd[:3] == b"\xef\xbb\xbf" and cd[:3] == b"\xef\xbb\xbf")
        check("create CRLF", b"\r\n" in hd and b"\r\n" in cd)
        for p in (tmpl_h, tmpl_c):
            os.remove(p)

    print("== 5. 输出与工程扫描 ==")
    v, e = tool("xc_get_output", tailLines=5)
    check("xc_get_output", (not e) and v.get("length", 0) > 0, "len=%s" % v.get("length"))
    v, e = tool("xc_list_projects", root=r"E:\AIWork\gw2")
    check("xc_list_projects", (not e) and v.get("count", 0) >= 1, "count=%s" % v.get("count"))
    v, e = tool("xc_tree_probe", action="dump")
    check("xc_tree_probe", (not e) and len(v.get("entries", [])) >= 5,
          "entries=%s" % len(v.get("entries", [])))

    print("== 6. 其余只读/状态工具 ==")
    v, e = tool("xc_server", action="status")
    check("xc_server", (not e) and v.get("running"))
    v, e = tool("xc_ide_log", text="[regress] ping")
    check("xc_ide_log", not e)
    v, e = tool("xc_save_all")
    check("xc_save_all", not e)
    v, e = tool("xc_probe_cmd", cmd_id=20)
    check("xc_probe_cmd", not e, "rc=%s" % v.get("rc"))

    print("== 7. 工程完整性 ==")
    d = io.open(MAIN, "rb").read()
    import hashlib
    md5 = hashlib.md5(d).hexdigest()
    check("main.xc size unchanged", len(d) == len(main_before), "%d B" % len(d))
    check("main.xc bytes unchanged", d == main_before, md5)
    check("no bak residue",
          not [f for f in os.listdir(PROJ) if f.startswith("main.xc.bak-")
               or f.startswith("module_regress")],
          str([f for f in os.listdir(PROJ) if f.startswith("main.xc.bak-")]))
    ET.parse(XCPROJ)
    check("xcproj parseable", True)

    print()
    print("汇总: 成功 %d / 失败 %d / 共 %d" % (len(OK), len(FAIL), len(OK) + len(FAIL)))
    if FAIL:
        print("失败项: " + ", ".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())