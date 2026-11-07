import json, sys, urllib.request, os, hashlib
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

BASE = "http://127.0.0.1:18766/mcp"
PROJ = r"E:\AIWork\gw2\插件启动器\启动器"
okc = failc = 0

def call(_tool, **kw):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": _tool, "arguments": kw}}).encode()
    req = mkreq(BASE, data=body,
                                headers={"Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=120).read())
    t = r["result"]["content"][0]["text"]
    try:
        return json.loads(t)
    except Exception:
        return {"_raw": t}

def chk(tag, cond, extra=""):
    global okc, failc
    if cond:
        okc += 1
        print("PASS", tag, extra)
    else:
        failc += 1
        print("FAIL", tag, extra)

# ---- B1: 同名二次 create 必须拒绝 ----
r1 = call("xc_module_create", name="verifyguard", alias="验证守卫",
          location="project", addToProject=False)
chk("B1-1 首次创建成功", r1.get("ok") is True, str(r1)[:160])
h = os.path.join(PROJ, "module_verifyguard.h")
c = os.path.join(PROJ, "module_verifyguard.cpp")
mk1 = hashlib.md5(open(h, "rb").read()).hexdigest() if os.path.exists(h) else None

r2 = call("xc_module_create", name="verifyguard", alias="验证守卫2",
          location="project", addToProject=False)
chk("B1-2 同名二次被拒", r2.get("ok") is False and r2.get("needForce") is True,
    str(r2)[:200])
mk2 = hashlib.md5(open(h, "rb").read()).hexdigest() if os.path.exists(h) else None
chk("B1-3 原文件未被改", mk1 == mk2, "%s vs %s" % (mk1, mk2))

r3 = call("xc_module_create", name="verifyguard", alias="验证守卫3",
          location="project", addToProject=False, force=True)
chk("B1-4 force=true 允许覆盖", r3.get("ok") is True, str(r3)[:160])
mk3 = hashlib.md5(open(h, "rb").read()).hexdigest() if os.path.exists(h) else None
chk("B1-5 覆盖后内容变化", mk3 != mk1, "%s vs %s" % (mk1, mk3))

# ---- B2: dir 穿越必须拒绝 ----
r4 = call("xc_module_create", name="escprobe", alias="穿越探针",
          location="dev", dir="..\\..\\..\\_escape_probe", addToProject=False)
chk("B2-1 穿越被拒", r4.get("ok") is False, str(r4)[:200])
esc = r"I:\_escape_probe"
chk("B2-2 库外目录未创建", not os.path.exists(esc) and
    not os.path.exists(r"I:\炫彩ide\data\_escape_probe"), str(os.path.exists(esc)))

r5 = call("xc_module_create", name="escprobe2", alias="带盘符探针",
          location="dev", dir="C:\\Windows", addToProject=False)
chk("B2-3 盘符被拒", r5.get("ok") is False, str(r5)[:160])

r6 = call("xc_module_create", name="devok", alias="正常子目录",
          location="dev", dir="验证分类/子目录", addToProject=False)
chk("B2-4 合法子目录仍可用", r6.get("ok") is True, str(r6)[:200])
devroot = r"I:\炫彩ide\data\XC-Module-dev\验证分类\子目录"
chk("B2-5 合法目录已建", os.path.isdir(devroot), devroot)

# ---- R7: clear 不再调用 SetText ----
r7 = call("xc_get_output", tailLines=3, clear=True)
chk("R7-1 clear 返回 cleared=false", r7.get("cleared") is False, str(r7)[:160])
chk("R7-2 有 clearNote", "clearNote" in r7, str(r7.get("clearNote", ""))[:80])

# ---- B3: 写盘走 tmp+rename，无残留 ----
r8 = call("xc_module_add", **{"file": "module_threadpool.h"})
chk("B3-1 module_add ok", r8.get("added") is True or r8.get("ok") is not False,
    str(r8)[:160])
xp = os.path.join(PROJ, "启动器.xcproj")
tmp = xp + ".xcmcp_tmp"
chk("B3-2 无 tmp 残留", not os.path.exists(tmp), tmp)
r9 = call("xc_module_remove", **{"file": "module_threadpool.h"})
chk("B3-3 module_remove ok", r9.get("removed") is True, str(r9)[:160])
md5 = hashlib.md5(open(xp, "rb").read()).hexdigest()
chk("B3-4 xcproj 复原", md5 == "e338f51c6e62ad20d095dc834c66d168" and
    os.path.getsize(xp) == 2867, "%s %d" % (md5, os.path.getsize(xp)))

# ---- B4: 根目录推导仍正确 ----
r10 = call("xc_module_list", limit=1)
chk("B4-1 ideRoot 推导正确", r10.get("ideRoot") == r"I:\炫彩ide",
    str(r10.get("ideRoot")))

# ---- 清理 ----
for p in (h, c):
    if os.path.exists(p):
        os.remove(p)
import shutil
for d in (os.path.join(devroot), os.path.join(r"I:\炫彩ide\data\XC-Module-dev\验证分类")):
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
if os.path.exists(os.path.join(r"I:\炫彩ide\data\XC-Module-dev", "module_devok.h")):
    os.remove(os.path.join(r"I:\炫彩ide\data\XC-Module-dev", "module_devok.h"))
if os.path.exists(os.path.join(r"I:\炫彩ide\data\XC-Module-dev", "module_devok.cpp")):
    os.remove(os.path.join(r"I:\炫彩ide\data\XC-Module-dev", "module_devok.cpp"))

ma = os.path.join(PROJ, "main.xc")
chk("Z1 main.xc 完好", os.path.getsize(ma) == 13925 and
    hashlib.md5(open(ma, "rb").read()).hexdigest() == "36f26227294078b1de3b2cea29326555",
    "%d B" % os.path.getsize(ma))

print()
print("汇总: 成功 %d / 失败 %d / 共 %d" % (okc, failc, okc + failc))
sys.exit(0 if failc == 0 else 1)
