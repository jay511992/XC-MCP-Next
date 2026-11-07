# -*- coding: utf-8 -*-
import os, sys, subprocess, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
P = lambda *a: (print(*a), sys.stdout.flush())

IDE = r"I:\炫彩ide"
F = r"E:\AIWork\gw2\插件启动器\启动器\common.xh"

out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq 炫彩IDE.exe", "/FO", "CSV", "/NH"],
                     capture_output=True, text=True, encoding="gbk", errors="replace").stdout
if any(l.startswith('"') for l in out.splitlines()):
    subprocess.run(["taskkill", "/F", "/IM", "炫彩IDE.exe"], capture_output=True)
    time.sleep(2)
    P("IDE 已关闭")

cur = open(F, "rb").read()
P(f"当前 {len(cur)} B: {cur[:60]!r}")

CONTENT = (
    "//预编译头文件, 提高修改代码时的编译速度\r\n"
    "//将不常修改的内容放在此文件中\r\n"
    "//包含外部头文件,库文件(lib)\r\n"
    "//申明外部函数, 变量, 宏, 枚举, 数据类型等, 不会生成实际代码\r\n"
)
data = b"\xef\xbb\xbf" + CONTENT.encode("utf-8")
open(F, "wb").write(data)
P(f"已恢复 {len(data)} B")

d2 = open(F, "rb").read()
P(f"校验: {len(d2)} B  BOM={d2[:3]==b'\xef\xbb\xbf'}  CRLF={d2.count(b'\r\n')}")
for i, ln in enumerate(d2.decode("utf-8-sig").splitlines(), 1):
    P(f"  {i}: {ln}")