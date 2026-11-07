# -*- coding: utf-8 -*-
"""端到端验证：新模块加入工程后能否被炫语言代码正确引用并编译通过。"""
import io, json, os, shutil, subprocess, sys, time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MCP = r"G:\AItools\skill\_融合后\mcp-cli\scripts\mcp.py"
PY = sys.executable
PROJ = r"E:\AIWork\gw2\插件启动器\启动器"
MAIN = os.path.join(PROJ, "main.xc")
BAK = MAIN + ".bak-modtest"
WS = r"C:\Users\p\Documents\deepseek-harness\default-workspace"

ENV = dict(os.environ)
ENV["PYTHONIOENCODING"] = "utf-8"


def mcp(tool, *args):
    cmd = [PY, MCP, "call", "xc_ide", tool] + list(args)
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=ENV, timeout=300)
    out = (p.stdout or "").strip()
    try:
        return json.loads(out)
    except Exception:
        return {"_raw": out, "_err": (p.stderr or "").strip()[:300]}


def ide(args, timeout=200):
    p = subprocess.run([PY, "ide_start.py"] + args, capture_output=True, text=True,
                       encoding="utf-8", env=ENV, cwd=WS, timeout=timeout)
    return (p.stdout or "").strip()


def build():
    """重启 IDE 并编译；返回 (ok, errors, text)。"""
    ide(["kill"])
    time.sleep(1.5)
    r = ide(["start"])
    if "ready after" not in r:
        return False, -1, "IDE 启动失败: " + r[:200]
    time.sleep(2)
    # 通过 xc_build 的低层入口触发；单次生命周期内第一次必然真实编译
    res = mcp("xc_open_in_ide", "path=" + MAIN)
    b = mcp("xc_build")
    # 轮询输出
    deadline = time.time() + 150
    text = ""
    while time.time() < deadline:
        o = mcp("xc_get_output", "tailLines=0")
        text = o.get("text", "") or json.dumps(o, ensure_ascii=False)
        if "错误列表" in text or "编译失败" in text or "跳过编译" in text:
            break
        time.sleep(1.0)
    errs = []
    for ln in text.splitlines():
        if ".xc(" in ln or ".xh(" in ln or ".cpp(" in ln:
            errs.append(ln.strip())
    ok = ("错误(0)" in text) and ("编译失败" not in text)
    return ok, len(errs), text


def append_src(code):
    data = io.open(MAIN, "rb").read()
    bom = data[:3] == b"\xef\xbb\xbf"
    body = data[3:] if bom else data
    raw = code.encode("utf-8")
    raw = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    out = (b"\xef\xbb\xbf" if bom else b"") + body + raw
    io.open(MAIN, "wb").write(out)
    return len(out)


def main():
    shutil.copy2(MAIN, BAK)
    print("backup -> %s" % BAK)
    try:
        # ---- 阶段 1：正确调用模块函数 ----
        n = append_src("\r\n函数 整型 模块调用测试()\r\n\t返回 测试模块_初始化()\r\n")
        print("[1] main.xc -> %d B" % n)
        ok, ec, text = build()
        print("[1] ok=%s errLines=%d" % (ok, ec))
        for ln in text.splitlines()[-14:]:
            print("     | " + ln.strip())
        if not ok:
            print("[1] FAIL")
            return 1

        # ---- 阶段 2：故意多传参数，验证错误能定位到新代码 ----
        io.open(MAIN, "wb").write(io.open(BAK, "rb").read())
        n = append_src("\r\n函数 整型 模块调用测试()\r\n\t返回 测试模块_初始化(9)\r\n")
        print("[2] main.xc -> %d B" % n)
        ok, ec, text = build()
        print("[2] ok=%s errLines=%d" % (ok, ec))
        for ln in text.splitlines()[-14:]:
            print("     | " + ln.strip())
        if ok:
            print("[2] FAIL: 期望编译失败但通过了")
            return 2
        print("[2] PASS: 错误已被捕获")
        return 0
    finally:
        io.open(MAIN, "wb").write(io.open(BAK, "rb").read())
        os.remove(BAK)
        d = io.open(MAIN, "rb").read()
        print("restored: %d B bom=%s" % (len(d), d[:3] == b"\xef\xbb\xbf"))


if __name__ == "__main__":
    sys.exit(main())