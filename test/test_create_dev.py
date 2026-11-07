# -*- coding: utf-8 -*-
"""验证 xc_module_create location=dev 的端到端链路：
1. 生成到 XC-Module-dev 并自动加入工程
2. 重启 IDE（全局索引重建）
3. main.xc 调用模块别名函数 -> 编译错误 0
4. 清理：删模块文件、从工程移除、恢复 main.xc
"""
import hashlib
import json
import os
import sys
import time
import urllib.request
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

MCP = "http://127.0.0.1:18766/mcp"
PROJ = r"E:\AIWork\gw2\插件启动器\启动器"
MAIN = os.path.join(PROJ, "main.xc")
XCPROJ = os.path.join(PROJ, "启动器.xcproj")
DEV = r"I:\炫彩ide\data\XC-Module-dev"
ENG = "devE2E"
ALIAS = "开发库端到端"

import subprocess  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
def _find_ide_start():
    cand = [os.environ.get("XC_IDE_START"),
            os.path.join(HERE, "ide_start.py"),
            os.path.join(HERE, "..", "..", "..", "..", "..",
                         "Users", "p", "Documents",
                         "deepseek-harness", "default-workspace", "ide_start.py")]
    for c in cand:
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    raise SystemExit("找不到 ide_start.py：请用环境变量 XC_IDE_START 指定路径")


IDE = _find_ide_start()


def ide(cmd):
    subprocess.run([sys.executable, IDE, cmd], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def md5(p):
    with open(p, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def call(name, args=None, timeout=180):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": args or {}}}).encode()
    req = mkreq(MCP, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(json.loads(r.read().decode())["result"]["content"][0]["text"])


def build_and_wait(timeout=180):
    call("xc_build")
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(1)
        out = call("xc_get_output", {"tailLines": 200})
        txt = out.get("text", "")
        if "错误列表" in txt or "编译失败" in txt or "跳过编译" in txt:
            return out
    return {"text": "", "timeout": True}


ok = 0
bad = 0


def check(label, cond, detail=""):
    global ok, bad
    if cond:
        ok += 1
        print("  OK   %s %s" % (label, detail))
    else:
        bad += 1
        print("  FAIL %s %s" % (label, detail))


main0 = md5(MAIN)
xproj0 = md5(XCPROJ)
size0 = os.path.getsize(MAIN)

print("=== 0. 前置 ===")
check("main.xc 基线", size0 == 13925, "%d B md5=%s" % (size0, main0[:8]))

print("=== 1. 生成到 dev 并加入工程 ===")
r = call("xc_module_create", {"name": ENG, "alias": ALIAS, "location": "dev",
                              "remark": "端到端验证", "addToProject": True})
check("create ok", r.get("ok") is True, json.dumps(r, ensure_ascii=False)[:160])
check("header 在 dev 库", (r.get("header") or "").startswith(DEV), r.get("header", ""))
check("location=dev", r.get("location") == "dev", str(r.get("location")))
check("moduleRoot 是 dev", r.get("moduleRoot") == DEV, str(r.get("moduleRoot")))
check("已加入工程", r.get("addedToProject") is True, str(r.get("addResult", ""))[:120])

hpath = os.path.join(DEV, "module_%s.h" % ENG)
cpath = os.path.join(DEV, "module_%s.cpp" % ENG)
check(".h 存在", os.path.isfile(hpath), "%d B" % (os.path.getsize(hpath) if os.path.isfile(hpath) else -1))
check(".cpp 存在", os.path.isfile(cpath), "%d B" % (os.path.getsize(cpath) if os.path.isfile(cpath) else -1))
with open(hpath, "rb") as f:
    raw = f.read()
check(".h 有 BOM", raw[:3] == b"\xef\xbb\xbf", "")
check(".h 用 CRLF", b"\r\n" in raw, "crlf=%d" % raw.count(b"\r\n"))

print("=== 2. 重启 IDE 让全局索引收录 ===")
ide("kill")
ide("start")
before = call("xc_module_info", {"name": "module_%s.h" % ENG})
check("索引已收录", before.get("ok") is True, json.dumps(before, ensure_ascii=False)[:120])

print("=== 3. main.xc 调用模块别名函数并编译 ===")
with open(MAIN, "rb") as f:
    raw = f.read()
base = raw.decode("utf-8-sig")
extra = ("函数 整型 开发库端到端测试()\r\n"
         "\t返回 %s_初始化()\r\n" % ALIAS)
new = base.rstrip("\r\n") + "\r\n\r\n" + extra
with open(MAIN, "wb") as f:
    f.write(b"\xef\xbb\xbf" + new.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
check("main.xc 已写入", os.path.getsize(MAIN) > size0, "%d B" % os.path.getsize(MAIN))

out = build_and_wait()
txt = out.get("text", "")
check("编译完成", "错误列表" in txt or "编译失败" in txt, "timeout=%s" % out.get("timeout"))
check("错误数 0", "错误(0)" in txt, [l for l in txt.splitlines() if "错误" in l][-1][:100] if txt else "")
check("模块 .cpp 参与编译", ("module_%s.cpp" % ENG) in txt, "")

print("=== 4. 清理 ===")
with open(MAIN, "wb") as f:
    f.write(raw)
check("main.xc 复原", md5(MAIN) == main0, md5(MAIN)[:8])
check("main.xc 大小复原", os.path.getsize(MAIN) == size0, "%d B" % os.path.getsize(MAIN))

rm = call("xc_module_remove", {"file": "module_%s.h" % ENG})
check("已从工程移除", rm.get("removed") is True, json.dumps(rm, ensure_ascii=False)[:120])
check("xcproj 复原", md5(XCPROJ) == xproj0, md5(XCPROJ)[:8])

for p in (hpath, cpath):
    if os.path.isfile(p):
        os.remove(p)
check(".h 已删除", not os.path.isfile(hpath), "")
check(".cpp 已删除", not os.path.isfile(cpath), "")

print("\n汇总: 成功 %d / 失败 %d / 共 %d" % (ok, bad, ok + bad))
sys.exit(0 if bad == 0 else 1)
