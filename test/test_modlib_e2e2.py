# -*- coding: utf-8 -*-
"""v2：纯 C++ 符号 + 编译前校验模块已入工程。"""
import hashlib, json, os, shutil, subprocess, sys, time, urllib.request
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
PROJ = r"E:\AIWork\gw2\插件启动器\启动器"
XCPROJ = os.path.join(PROJ, "启动器.xcproj")
MAINXC = os.path.join(PROJ, "main.xc")
BUILD = r"I:\炫彩ide\data\VC\VC2022\14.41.34120\bin\Hostx64\x64"
WS = r"C:\Users\p\Documents\deepseek-harness\default-workspace"

def md5(p): return hashlib.md5(open(p, "rb").read()).hexdigest()

def rpc(method, params=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None: body["params"] = params
    req = mkreq(URL, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))

def call(tool, **kw):
    r = rpc("tools/call", {"name": tool, "arguments": kw})
    txt = r["result"]["content"][0]["text"]
    try: return json.loads(txt)
    except Exception: return {"_raw": txt}

ok = fail = 0
def chk(c, label, detail=""):
    global ok, fail
    if c: ok += 1; print("  PASS", label)
    else: fail += 1; print("  FAIL", label, detail)

def restart():
    subprocess.run([sys.executable, os.path.join(WS, "ide_start.py"), "kill"], capture_output=True)
    subprocess.run([sys.executable, os.path.join(WS, "ide_start.py"), "start"], capture_output=True)
    time.sleep(9)

def wait_output(timeout=200):
    t0 = time.time(); last = ""
    while time.time() - t0 < timeout:
        d = call("xc_get_output", tailLines=250)
        t = d.get("text", "") if isinstance(d, dict) else ""
        if any(m in t for m in ("编译完成", "编译失败", "跳过编译")) and "错误列表" in t: return t
        last = t; time.sleep(1)
    return last

orig_md5, orig_size = md5(MAINXC), os.path.getsize(MAINXC)
orig_xcp = md5(XCPROJ)
print("main.xc", orig_size, orig_md5)

# 1. 造 C++ 静态库
src_dir = os.path.join(PROJ, "_probe_src"); shutil.rmtree(src_dir, ignore_errors=True); os.makedirs(src_dir)
cpp = os.path.join(src_dir, "probe_lib.cpp")
open(cpp, "w", encoding="utf-8").write("int xcl_probe_lib_value() { return 42; }\n")
env = dict(os.environ); env["PATH"] = BUILD + ";" + env["PATH"]
obj = os.path.join(src_dir, "probe_lib.obj")
r = subprocess.run([os.path.join(BUILD, "cl.exe"), "/nologo", "/c", "/EHsc", "/Fo" + obj, cpp],
                   capture_output=True, env=env)
chk(r.returncode == 0, "编译 probe_lib.obj", r.stdout.decode("gbk", "replace")[-200:])
x64dir = os.path.join(PROJ, "x64"); os.makedirs(x64dir, exist_ok=True)
libpath = os.path.join(x64dir, "probe_lib.lib")
r = subprocess.run([os.path.join(BUILD, "lib.exe"), "/nologo", "/OUT:" + libpath, obj],
                   capture_output=True, env=env)
chk(r.returncode == 0 and os.path.exists(libpath), "生成 probe_lib.lib",
    os.path.getsize(libpath) if os.path.exists(libpath) else "missing")
dump = subprocess.run([os.path.join(BUILD, "dumpbin.exe"), "/symbols", libpath],
                      capture_output=True, env=env).stdout.decode("gbk", "replace")
print("  lib 符号:", [l.strip() for l in dump.splitlines() if "xcl_probe_lib_value" in l][:2])
open(os.path.join(x64dir, "probe_lib.dll"), "wb").write(b"MZ" + b"\0" * 200)
incdir = os.path.join(PROJ, "native", "include"); os.makedirs(incdir, exist_ok=True)
open(os.path.join(incdir, "probe_api.h"), "w", encoding="utf-8").write("int xcl_probe_lib_value();\n")

# 2. 建模块入工程
print("\n=== 建模块 ===")
d = call("xc_module_create", name="probelib", alias="探针库", remark="e2e 预编译库验证",
         dir=PROJ, libs=r"x64\probe_lib.lib", dlls=r"x64\probe_lib.dll",
         includes=r"native\include", addToProject=True)
chk(d.get("ok") is True and d.get("addedToProject") is True, "create + addToProject", d.get("addError"))
print("  missing:", d.get("missing"), " warnings:", d.get("warnings"))
hpath, cpath = d["header"], d["source"]

roc = open(XCPROJ, "rb").read().decode("utf-8-sig")
chk("module_probelib.h" in roc, "xcproj 含 module_probelib.h")

# 3. 头文件补别名（纯 C++ 声明，与骨架同风格）
htxt = open(hpath, "rb").read().decode("utf-8-sig").replace("\r\n", "\n")
add = "//@别名  探针库_取值()\n//@返回  固定返回 42\nint xcl_probe_lib_value();\n"
anchor = "//@分组}\n"
chk(anchor in htxt, "找到 //@分组} 锚点")
htxt = htxt.replace(anchor, add + anchor, 1)
open(hpath, "wb").write(b"\xef\xbb\xbf" + htxt.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
print("  头文件注解区:")
for line in htxt.splitlines():
    if line.startswith(("//@", "#ifdef", "#else", "#endif", "int ", "extern ")):
        print("    ", line)

# 4. cpp 里调用 lib 符号
ctxt = open(cpath, "rb").read().decode("utf-8-sig").replace("\r\n", "\n")
old = "int xcl_probelib_init() {\n    return 1;\n}"
chk(old in ctxt, "找到 .cpp init 函数")
ctxt = ctxt.replace(old, "int xcl_probelib_init() {\n    return xcl_probe_lib_value();\n}")
open(cpath, "wb").write(b"\xef\xbb\xbf" + ctxt.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))

# 5. main.xc 调用别名
body = open(MAINXC, "rb").read().decode("utf-8-sig")
open(MAINXC, "wb").write(b"\xef\xbb\xbf" + (body + "\r\n函数 整型 探针库验证()\r\n\t返回 探针库_取值()\r\n").encode("utf-8"))
print("  main.xc ->", os.path.getsize(MAINXC), "B")

# 6. 编译
print("\n=== 编译 ===")
restart()
print("  health:", call("xc_status").get("httpRunning"))
b = call("xc_build")
print("  xc_build ->", json.dumps(b, ensure_ascii=False)[:200])
time.sleep(2)
t = wait_output()
tail = t[-1800:]
print(tail)
chk("错误(0)" in t and "未定义" not in t, "编译错误 0")
chk("未定义:探针库_取值" not in t, "别名已被识别")

# 7. //@复制文件
copied = os.path.join(PROJ, "Debug64", "probe_lib.dll")
print("\n=== //@复制文件 ===")
print("  ", copied, "exists:", os.path.exists(copied))
chk(os.path.exists(copied), "probe_lib.dll 复制到 exe 目录")

# 8. //@lib 真被链接：obj 里应引用该符号
cache = os.path.join(PROJ, "Debug64", "obj", "cache")
probe_obj = os.path.join(PROJ, "Debug64", "obj", "probelib__.obj")
found = None
for root, _, files in os.walk(os.path.join(PROJ, "Debug64", "obj")):
    for f in files:
        if "probelib" in f.lower() and f.endswith(".obj"):
            found = os.path.join(root, f)
print("  probelib obj:", found)
if found:
    syms = subprocess.run([os.path.join(BUILD, "dumpbin.exe"), "/symbols", found],
                          capture_output=True, env=env).stdout.decode("gbk", "replace")
    chk("xcl_probe_lib_value" in syms, "obj 引用 xcl_probe_lib_value（说明链接需从 lib 取）")

# 9. 清理
print("\n=== 清理 ===")
call("xc_module_remove", file="module_probelib.h")
open(MAINXC, "wb").write(b"\xef\xbb\xbf" + body.encode("utf-8"))
for p in (hpath, cpath):
    try: os.remove(p)
    except Exception: pass
shutil.rmtree(src_dir, ignore_errors=True)
shutil.rmtree(os.path.join(PROJ, "x64"), ignore_errors=True)
shutil.rmtree(os.path.join(PROJ, "native"), ignore_errors=True)
print("  main.xc", os.path.getsize(MAINXC), "same:", md5(MAINXC) == orig_md5)
chk(os.path.getsize(MAINXC) == orig_size and md5(MAINXC) == orig_md5, "main.xc 复原")
chk(md5(XCPROJ) == orig_xcp, "xcproj 复原")

print("\n汇总: 成功 %d / 失败 %d" % (ok, fail))
sys.exit(0 if fail == 0 else 1)