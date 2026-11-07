# -*- coding: utf-8 -*-
"""优化回归：只在临时目录写文件，不修改当前工程或现有模块。
用法：python -I test/test_opt2.py [--offline]
"""
import ctypes
import http.client
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[1]


def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = load_script("bridge", ROOT / "bridge" / "stdio_bridge.py")
xcbuild = load_script("xcbuild", ROOT / "xcbuild" / "xcbuild.py")


def rpc(method, params=None, conn=None):
    own = conn is None
    conn = conn or http.client.HTTPConnection("127.0.0.1", 18766, timeout=20)
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    try:
        conn.request("POST", "/mcp", json.dumps(body), {
            "Content-Type": "application/json", "X-XC-Token": bridge._read_token()})
        res = conn.getresponse()
        data = res.read()
        if res.status != 200:
            raise RuntimeError("HTTP %d: %r" % (res.status, data[:100]))
        return json.loads(data)
    finally:
        if own:
            conn.close()


def tool(name, **args):
    res = rpc("tools/call", {"name": name, "arguments": args})["result"]
    return json.loads(res["content"][0]["text"])


class OfflineTests(unittest.TestCase):
    def test_token_env_precedes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "xc-mcp"
            p.mkdir()
            (p / "token").write_text("file-token", encoding="utf-8")
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": tmp, "XC_MCP_TOKEN": " MixedCase-Token "}):
                self.assertEqual(bridge._read_token(), "MixedCase-Token")
                self.assertEqual(xcbuild.read_token(), "MixedCase-Token")
                os.environ["XC_MCP_TOKEN"] = ""
                self.assertEqual(bridge._read_token(), "file-token")
                self.assertEqual(xcbuild.read_token(), "file-token")
                (p / "token").write_text("changed-token", encoding="utf-8")
                self.assertEqual(bridge._read_token(), "changed-token")
                self.assertEqual(xcbuild.read_token(), "changed-token")

    def test_build_output_requires_completion(self):
        for text in ("", "正在编译", "错误列表: 错误(0)", "跳过编译", "编译完成, 耗时 10ms\n错误列表: 共有(2)个错误"):
            with self.subTest(text=text):
                self.assertFalse(xcbuild.parse_output(text)["ok"])
        self.assertTrue(xcbuild.parse_output("编译完成, 耗时 10ms\n错误列表: 错误(0)")["ok"])
        self.assertFalse(xcbuild.parse_output("编译完成, 耗时 10ms\n🐞*编译失败, 错误码: 2")["ok"])

    def test_ide_root_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "炫彩IDE.exe").touch()
            with mock.patch.dict(os.environ, {"XC_IDE_ROOT": tmp}):
                self.assertEqual(xcbuild._find_ide_root(), tmp)

    def test_bridge_errors_do_not_crash_on_batch_or_scalar(self):
        requests = [[], 123, {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    [{"id": 2, "method": "ping"}, {"method": "notifications/initialized"}]]
        env = dict(os.environ, XC_MCP_URL="http://127.0.0.1:1/mcp", PYTHONIOENCODING="utf-8")
        res = subprocess.run([sys.executable, "-I", str(ROOT / "bridge" / "stdio_bridge.py")],
                             input="\n".join(json.dumps(v) for v in requests) + "\n",
                             encoding="utf-8", capture_output=True, env=env, timeout=20)
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = [json.loads(line) for line in res.stdout.splitlines()]
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[2]["id"], 1)
        self.assertEqual(rows[3][0]["id"], 2)
        self.assertEqual(len(rows[3]), 1)


class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.status = tool("xc_status")
        cls.modules = tool("xc_module_list", all=True, limit=500)

    def test_keep_alive_uses_same_socket(self):
        conn = http.client.HTTPConnection("127.0.0.1", 18766, timeout=20)
        try:
            self.assertEqual(rpc("ping", conn=conn)["result"], {})
            sock = conn.sock
            self.assertIsNotNone(sock)
            for _ in range(5):
                self.assertEqual(rpc("ping", conn=conn)["result"], {})
                self.assertIs(conn.sock, sock)
        finally:
            conn.close()

    def test_two_pipelined_requests(self):
        body = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}).encode()
        head = ("POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:18766\r\nX-XC-Token: %s\r\n"
                "Content-Type: application/json\r\nContent-Length: %d\r\n\r\n" %
                (bridge._read_token(), len(body))).encode()
        ids = []
        buf = b""
        with socket.create_connection(("127.0.0.1", 18766), timeout=20) as sock:
            sock.sendall((head + body) * 2)
            while len(ids) < 2:
                buf += sock.recv(65536)
                while True:
                    sep = buf.find(b"\r\n\r\n")
                    if sep < 0:
                        break
                    raw_head = buf[:sep].decode("latin-1")
                    clen = 0
                    for line in raw_head.split("\r\n"):
                        if line.lower().startswith("content-length:"):
                            clen = int(line.split(":", 1)[1])
                    if len(buf) < sep + 4 + clen:
                        break
                    payload = buf[sep + 4:sep + 4 + clen]
                    buf = buf[sep + 4 + clen:]
                    ids.append(json.loads(payload)["id"])
        self.assertEqual(ids, [7, 7])

    def test_large_fragmented_request(self):
        body = json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping", "params": {"pad": "x" * 700000}}).encode()
        head = ("POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:18766\r\nX-XC-Token: %s\r\n"
                "Content-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" %
                (bridge._read_token(), len(body))).encode()
        with socket.create_connection(("127.0.0.1", 18766), timeout=20) as sock:
            sock.sendall(head)
            for i in range(0, len(body), 4096):
                sock.sendall(body[i:i + 4096])
            res = http.client.HTTPResponse(sock)
            res.begin()
            self.assertEqual(res.status, 200)
            self.assertEqual(json.loads(res.read())["id"], 9)
            res.close()

    def test_token_optional_origin_and_host(self):
        for headers, expected in [({"Origin": ""}, 403), ({"Origin": "http://example.test"}, 403),
                                  ({"Host": "evil.test:18766"}, 403), ({"X-XC-Token": "bad-token"}, 200),
                                  ({"X-XC-Token": "", "Authorization": "Bearer " + bridge._read_token()}, 200)]:
            with self.subTest(headers=list(headers), expected=expected):
                conn = http.client.HTTPConnection("127.0.0.1", 18766, timeout=20)
                try:
                    hdrs = {"X-XC-Token": bridge._read_token()}
                    hdrs.update(headers)
                    conn.request("GET", "/health", headers=hdrs)
                    res = conn.getresponse()
                    self.assertEqual(res.status, expected, res.read())
                    res.read()
                finally:
                    conn.close()

    def test_oversized_content_length(self):
        with socket.create_connection(("127.0.0.1", 18766), timeout=20) as sock:
            sock.sendall(b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:18766\r\nContent-Length: 4194305\r\n\r\n")
            res = http.client.HTTPResponse(sock)
            res.begin()
            self.assertEqual(res.status, 413)
            res.read()
            res.close()

    def test_bom_crlf_roundtrip(self):
        with tempfile.TemporaryDirectory(prefix="_opt2_", dir=self.status["pluginDir"]) as tmp:
            for name, raw in [("bom.xc", b"\xef\xbb\xbf" + "中文\r\n第二行\r\n".encode()),
                              ("plain.xc", "中文\n第二行\n".encode()), ("empty.xc", b"")]:
                p = Path(tmp) / name
                p.write_bytes(raw)
                got = tool("xc_read_file", path=str(p))
                self.assertTrue(got["ok"])
                self.assertEqual(got["bom"], raw.startswith(b"\xef\xbb\xbf"))
                self.assertEqual(got["rawSize"], len(raw))
                self.assertEqual(got["size"], len(got["content"].encode()))
                self.assertTrue(tool("xc_editor_set", path=str(p), text=got["content"])["ok"])
                self.assertEqual(p.read_bytes(), raw)
            p = Path(tmp) / "override.xc"
            self.assertTrue(tool("xc_editor_set", path=str(p), text="test\n", bom="false")["ok"])
            self.assertFalse(p.read_bytes().startswith(b"\xef\xbb\xbf"))
            self.assertTrue(tool("xc_editor_set", path=str(p), text="test\n", bom="true")["ok"])
            self.assertTrue(p.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_cache_add_edit_delete(self):
        dev = self.modules["devDir"]
        os.makedirs(dev, exist_ok=True)
        key = uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix="_opt2_", dir=dev) as tmp:
            p = Path(tmp) / ("module_" + key + ".h")
            def write_header(name):
                p.write_bytes(("﻿//@模块名称  " + name + "\r\n//@版本  1.0\r\n//@模块信息结束\r\n").encode())
            def search():
                return tool("xc_module_search", query=key, all=True, limit=500)["hits"]
            write_header("before")
            self.assertEqual(search()[0]["name"], "before")
            self.assertEqual(search()[0]["name"], "before")
            write_header("after!")  # 相同大小，但 mtime 改变
            stamp = time.time_ns() + 1_000_000_000
            os.utime(p, ns=(stamp, stamp))
            self.assertEqual(search()[0]["name"], "after!")
            p.unlink()
            self.assertEqual(search(), [])

    def test_bridge_live_initialize_batch(self):
        requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                    [{"jsonrpc": "2.0", "id": 2, "method": "ping"},
                     {"jsonrpc": "2.0", "method": "notifications/initialized"}],
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}]
        res = subprocess.run([sys.executable, "-I", str(ROOT / "bridge" / "stdio_bridge.py")],
                             input="\n".join(json.dumps(v) for v in requests) + "\n",
                             encoding="utf-8", capture_output=True, timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = [json.loads(line) for line in res.stdout.splitlines()]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["result"]["serverInfo"]["name"], "xcide-mcp")
        self.assertEqual(len(rows[1]), 1)
        self.assertEqual(len(rows[2]["result"]["tools"]), 29)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    offline = "--offline" in sys.argv
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(OfflineTests)
    if not offline:
        suite.addTests(loader.loadTestsFromTestCase(LiveTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
