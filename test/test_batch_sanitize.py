# -*- coding: utf-8 -*-
"""验证 R12 JSON-RPC batch 与 R8 孤立代理字符净化。"""
import io, json, os, sys, urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

URL = "http://127.0.0.1:18766/mcp"


def _read_token():
    p = os.path.join(os.environ.get("LOCALAPPDATA", ""), "xc-mcp", "token")
    if p and os.path.exists(p):
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip()
    return os.environ.get("XC_MCP_TOKEN", "")


TOK = _read_token()
OK = []
FAIL = []


def chk(name, cond, detail=""):
    if cond:
        OK.append(name)
        print("  PASS  %s  %s" % (name, detail))
    else:
        FAIL.append(name)
        print("  FAIL  %s  %s" % (name, detail))


def raw(body, extra=None, timeout=30):
    h = {"Content-Type": "application/json", "X-XC-Token": TOK}
    if extra:
        h.update(extra)
    req = urllib.request.Request(URL, data=body.encode("utf-8"), headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def rpc(obj, timeout=30):
    st, bd = raw(json.dumps(obj))
    return st, (json.loads(bd) if bd else None)


print("=== 1. batch：两个 call 一次发 ===")
batch = [
    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "xc_status", "arguments": {}}},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
]
st, res = rpc(batch)
chk("batch 返回 200", st == 200, "status=%s" % st)
chk("batch 返回数组", isinstance(res, list), type(res).__name__)
if isinstance(res, list):
    chk("batch 长度 2", len(res) == 2, str(len(res)))
    ids = [r.get("id") for r in res]
    chk("batch id 保序", ids == [1, 2], str(ids))
    txt = res[0]["result"]["content"][0]["text"]
    chk("batch 第1项是 xc_status", "toolCount" in txt, txt[:60])
    chk("batch 第2项是 tools 列表", "tools" in res[1]["result"], str(list(res[1]["result"].keys())))

print("=== 2. batch：混合通知（无 id 的项不出响应）===")
mix = [
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 7, "method": "ping"},
]
st, res = rpc(mix)
chk("混合 batch 200", st == 200, "status=%s" % st)
chk("混合 batch 只 1 项", isinstance(res, list) and len(res) == 1, str(res)[:80])
if isinstance(res, list) and res:
    chk("混合 batch id=7", res[0].get("id") == 7, str(res[0].get("id")))

print("=== 3. batch：全是通知 → 空响应 ===")
alls = [{"jsonrpc": "2.0", "method": "notifications/initialized"}]
st, bd = raw(json.dumps(alls))
chk("全通知 batch 回 202", st == 202, "status=%s" % st)
chk("全通知 batch 体为空", bd == b"", repr(bd[:40]))

print("=== 4. batch：空数组 → -32600 ===")
st, res = rpc([])
chk("空数组 200", st == 200, "status=%s" % st)
chk("空数组报 -32600", isinstance(res, dict) and res.get("error", {}).get("code") == -32600, str(res)[:80])

print("=== 5. batch：单项非法 JSON-RPC 不影响其它项 ===")
mixed = [
    {"jsonrpc": "2.0", "id": 10, "method": "no/such/method"},
    {"jsonrpc": "2.0", "id": 11, "method": "ping"},
]
st, res = rpc(mixed)
chk("错误+正常 batch 200", st == 200, "status=%s" % st)
if isinstance(res, list) and len(res) == 2:
    chk("第1项 -32601", res[0].get("error", {}).get("code") == -32601, str(res[0].get("error"))[:60])
    chk("第2项 ping 成功", res[1].get("result") == {}, str(res[1].get("result")))
else:
    chk("错误+正常 batch 长度", False, str(res)[:80])

print("=== 6. R8 孤立代理字符净化 ===")
# 通过 xc_ide_log 把孤立代理写进输出窗格，再读回来看是否被替换
bad = "PROBE-\ud800-END"
st, res = rpc({"jsonrpc": "2.0", "id": 20, "method": "tools/call",
               "params": {"name": "xc_ide_log", "arguments": {"text": bad}}})
chk("写孤立代理 200", st == 200, "status=%s" % st)
if isinstance(res, dict):
    chk("写孤立代理 ok", res.get("result", {}).get("isError") is not True, str(res.get("result"))[:60])

st, res = rpc({"jsonrpc": "2.0", "id": 21, "method": "tools/call",
               "params": {"name": "xc_get_output", "arguments": {"tailLines": 10}}})
chk("读输出 200", st == 200, "status=%s" % st)
if isinstance(res, dict):
    txt = res["result"]["content"][0]["text"]
    obj = json.loads(txt)
    got = obj.get("text", "")
    chk("响应不含孤立代理字节", b"\xed\xa0\x80" not in txt.encode("utf-8", "surrogatepass"), "len=%d" % len(txt))
    chk("PROBE 标记存在", "PROBE-" in got, got[-40:].replace("\n", " "))

print("=== 7. 单条请求仍正常 ===")
st, res = rpc({"jsonrpc": "2.0", "id": 30, "method": "tools/call",
               "params": {"name": "xc_status", "arguments": {}}})
chk("单条 200", st == 200, "status=%s" % st)
chk("单条不是数组", isinstance(res, dict), type(res).__name__)

print()
print("汇总: 成功 %d / 失败 %d / 共 %d" % (len(OK), len(FAIL), len(OK) + len(FAIL)))
if FAIL:
    for f in FAIL:
        print("  FAILED: %s" % f)
sys.exit(0 if not FAIL else 1)
