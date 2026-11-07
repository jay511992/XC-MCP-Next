# -*- coding: utf-8 -*-
"""基线/新版性能测量。用法：python -I test/bench_opt2.py <label> [N]

每个指标用配平法：工具耗时 - (调用前 ping + 调用后 ping)/2，抵消 HTTP 地板漂移。
另外测同一连接连续请求的中位数，用来量化 keep-alive。
"""
import http.client
import json
import os
import statistics
import sys
import time
import urllib.request

URL = "http://127.0.0.1:18766/mcp"


def read_token():
    tok = os.environ.get("XC_MCP_TOKEN", "").strip()
    if tok:
        return tok
    p = os.path.join(os.environ.get("LOCALAPPDATA", ""), "xc-mcp", "token")
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip()
    except OSError:
        return ""


TOK = read_token()
_rid = [0]


def request(obj, conn=None, timeout=60):
    data = json.dumps(obj).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-XC-Token": TOK}
    own = conn is None
    conn = conn or http.client.HTTPConnection("127.0.0.1", 18766, timeout=timeout)
    try:
        conn.request("POST", "/mcp", data, headers)
        res = conn.getresponse()
        raw = res.read()
        if res.status != 200:
            raise RuntimeError("HTTP %d" % res.status)
        return json.loads(raw)
    finally:
        if own:
            conn.close()


def rpc(method, params=None):
    _rid[0] += 1
    obj = {"jsonrpc": "2.0", "id": _rid[0], "method": method}
    if params is not None:
        obj["params"] = params
    return request(obj)


def call(name, args):
    _rid[0] += 1
    return request({"jsonrpc": "2.0", "id": _rid[0], "method": "tools/call",
                    "params": {"name": name, "arguments": args}})


def timed(fn):
    t = time.perf_counter()
    fn()
    return (time.perf_counter() - t) * 1000.0


def measure(label, fn, n):
    raw, net = [], []
    for _ in range(n):
        p1 = timed(lambda: rpc("ping"))
        t = timed(fn)
        p2 = timed(lambda: rpc("ping"))
        raw.append(t)
        net.append(t - (p1 + p2) / 2.0)
    return {"raw_ms": round(statistics.median(raw), 4),
            "net_ms": round(statistics.median(net), 4),
            "n": n}


def persistent(label, make_obj, n):
    conn = http.client.HTTPConnection("127.0.0.1", 18766, timeout=60)
    times = []
    try:
        for _ in range(n):
            t = timed(lambda: request(make_obj(), conn=conn))
            times.append(t)
    finally:
        conn.close()
    return {"median_ms": round(statistics.median(times), 4),
            "p90_ms": round(sorted(times)[int(len(times) * 0.9)], 4), "n": n}


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    result = {"label": label, "token": TOK[:8] + "...", "results": {}}
    result["results"]["tools/list"] = measure("tools/list", lambda: rpc("tools/list"), n)
    result["results"]["xc_status"] = measure("xc_status", lambda: call("xc_status", {}), n)
    result["results"]["xc_get_project"] = measure("xc_get_project", lambda: call("xc_get_project", {}), n)
    result["results"]["xc_read_file"] = measure(
        "xc_read_file", lambda: call("xc_read_file", {"path": r"E:\AIWork\gw2\插件启动器\启动器\main.xc"}), n)
    result["results"]["xc_get_output"] = measure(
        "xc_get_output", lambda: call("xc_get_output", {"tailLines": 200}), n)
    result["results"]["xc_module_list"] = measure(
        "xc_module_list", lambda: call("xc_module_list", {"limit": 100}), max(15, n // 3))
    result["results"]["xc_module_search"] = measure(
        "xc_module_search", lambda: call("xc_module_search", {"query": "线程"}), max(15, n // 3))

    _rid[0] += 1
    ping_obj = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    result["results"]["keepalive_ping"] = persistent("keepalive_ping", lambda: ping_obj, 200)
    _rid[0] += 1
    list_obj = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    result["results"]["keepalive_tools_list"] = persistent("keepalive_tools_list", lambda: list_obj, 200)

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "doc", "perf-opt2-%s.json" % label)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("saved:", out)


if __name__ == "__main__":
    main()
