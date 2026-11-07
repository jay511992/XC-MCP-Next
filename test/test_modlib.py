# -*- coding: utf-8 -*-
import json, os, shutil, sys, urllib.request
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
URL = "http://127.0.0.1:18766/mcp"
TMP = r"E:\AIWork\gw2\_modlib_test"

def rpc(method, params=None, mid=1):
    body = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        body["params"] = params
    req = mkreq(URL, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))

def call(tool, **kw):
    r = rpc("tools/call", {"name": tool, "arguments": kw})
    txt = r["result"]["content"][0]["text"]
    try:
        return json.loads(txt), txt
    except Exception:
        return {"_raw": txt}, txt

ok = 0
fail = 0
def chk(cond, label, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS", label)
    else:
        fail += 1
        print("  FAIL", label, detail)

# ---- 准备临时模块目录 ----
if os.path.isdir(TMP):
    shutil.rmtree(TMP)
os.makedirs(os.path.join(TMP, "x64"))
os.makedirs(os.path.join(TMP, "x86"))
for rel, size in [(r"x64\libcurl.lib", 1000), (r"x64\libcrypto.lib", 500),
                  (r"x64\libcurl.dll", 800), (r"x86\libcurl.lib", 900)]:
    with open(os.path.join(TMP, rel), "wb") as f:
        f.write(b"\0" * size)
print("tmp dir ready:", TMP)

print("\n=== 1. xc_module_create 带预编译库 ===")
d, txt = call("xc_module_create", name="curl", alias="CURL网络访问2",
              author="tester", remark="封装开源 CURL",
              dir=TMP, libs=r"x64\libcurl.lib;x64\libcrypto.lib",
              dlls=r"x64\libcurl.dll", includes=r"native\include",
              addToProject=False)
print(json.dumps(d, ensure_ascii=False, indent=1)[:1400])
chk(d.get("ok") is True, "ok")
chk(d.get("libsX64") == [r"x64\libcurl.lib", r"x64\libcrypto.lib"], "libsX64", d.get("libsX64"))
chk(d.get("libsX86") == [r"x86\libcurl.lib", r"x86\libcrypto.lib"], "libsX86 自动推导", d.get("libsX86"))
chk(d.get("dllsX64") == [r"x64\libcurl.dll"], "dllsX64", d.get("dllsX64"))
chk(d.get("dllsX86") == [r"x86\libcurl.dll"], "dllsX86 自动推导", d.get("dllsX86"))
chk(d.get("includes") == [r"native\include"], "includes", d.get("includes"))
miss = d.get("missing") or []
chk(r"x86\libcrypto.lib" in miss, "missing 含 x86\\libcrypto.lib", miss)
chk(r"x86\libcurl.dll" in miss, "missing 含 x86\\libcurl.dll", miss)
chk(r"x64\libcurl.lib" not in miss, "x64 lib 不该缺", miss)

hpath = d.get("header")
with open(hpath, "rb") as f:
    hraw = f.read()
htxt = hraw.decode("utf-8-sig")
chk(hraw.startswith(b"\xef\xbb\xbf"), "生成头文件带 BOM")
chk(hraw.count(b"\r\n") > 10, "生成头文件是 CRLF", hraw.count(b"\r\n"))
chk(htxt.count("//@lib  ") == 4, "4 条 //@lib (x64 2 + x86 2)", htxt.count("//@lib  "))
chk(htxt.count("//@复制文件 @当前模块路径") == 2, "2 条 //@复制文件")
chk('#ifdef _WIN64' in htxt and '#else' in htxt and '#endif' in htxt, "双分支 #ifdef/#else/#endif")
chk('//@包含目录 @当前模块路径 "native\\include"' in htxt, "包含目录注解")
chk('//@lib  "x64\\libcurl.lib"' in htxt, "x64 lib 行")
chk('//@lib  "x86\\libcurl.lib"' in htxt, "x86 lib 行")
chk('//@复制文件 @当前模块路径 "x64\\libcurl.dll"' in htxt, "x64 dll 行")
print("---- 生成的头文件注解区 ----")
for line in htxt.splitlines():
    if line.startswith("//@") or line.startswith("#ifdef") or line.startswith("#else") or line.startswith("#endif"):
        print("   ", line)

print("\n=== 2. 单平台（只给 libs，无 86 文件是否仍生成 x86 分支） ===")
d2, _ = call("xc_module_create", name="single", alias="单平台", dir=TMP,
             libs="mylib.lib", addToProject=False)
chk(d2.get("ok") is True, "ok")
chk(d2.get("libsX64") == [r"x64\mylib.lib"], "裸名自动加 x64\\", d2.get("libsX64"))
chk(d2.get("libsX86") == [r"x86\mylib.lib"], "裸名自动加 x86\\", d2.get("libsX86"))
miss2 = d2.get("missing") or []
chk(r"x64\mylib.lib" in miss2 and r"x86\mylib.lib" in miss2, "两个平台都报缺失", miss2)

print("\n=== 3. xc_module_search dependsOn 反查 ===")
d3, _ = call("xc_module_search", dependsOn="module_kyozy_std.h", limit=20)
print(json.dumps(d3, ensure_ascii=False)[:900])
chk(d3.get("ok") is True, "ok")
chk(d3.get("count", 0) >= 1, "至少 1 条依赖 module_kyozy_std.h", d3.get("count"))
for hit in (d3.get("hits") or []):
    chk("dependsOn;" in hit.get("match", ""), "命中标记 dependsOn", hit.get("match"))
    chk("module_kyozy_std.h" in (hit.get("deps") or []), "deps 含 module_kyozy_std.h", hit.get("deps"))

d3b, _ = call("xc_module_search", dependsOn="module_base.h", limit=5)
dd, _ = call("xc_module_search", dependsOn="module_base.h", limit=500)
print("  module_base.h 被依赖数量:", dd.get("count"))
chk(dd.get("count", 0) >= 130, "module_base.h 被大量模块依赖", dd.get("count"))

print("\n=== 4. query 与 deps 打分 ===")
d4, _ = call("xc_module_search", query="线程", limit=10)
chk(d4.get("ok") is True and d4.get("count", 0) > 0, "query 仍正常", d4.get("count"))
withdeps = [h for h in (d4.get("hits") or []) if h.get("deps")]
chk(len(withdeps) > 0, "结果里带 deps 字段", len(withdeps))

print("\n=== 5. xc_module_list withDeps ===")
d5, _ = call("xc_module_list", limit=5, withDeps=True)
chk(d5.get("ok") is True, "ok")
n_deps = sum(1 for m in (d5.get("modules") or []) if m.get("deps"))
chk(n_deps > 0, "部分模块带 deps", n_deps)
for m in (d5.get("modules") or []):
    print("   ", m.get("file"), "->", m.get("deps"))

print("\n=== 6. 缺参数应报错 ===")
e1, _ = call("xc_module_search")
chk("error" in e1 or e1.get("ok") is not True, "search 无参数报错", e1)

print("\n汇总: 成功 %d / 失败 %d" % (ok, fail))
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0 if fail == 0 else 1)