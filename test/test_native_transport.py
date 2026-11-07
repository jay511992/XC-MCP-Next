# -*- coding: utf-8 -*-
"""Real native HTTP/MCP tests with a fake IDE ABI; no running IDE is touched.

python -I -B test/test_native_transport.py
Requires the same Windows compiler as build.bat. Every artifact lives in
PI_SCRATCH_DIR (when set), otherwise project/Temp/tests.
"""
import concurrent.futures
import http.client
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = Path(os.environ.get("PI_SCRATCH_DIR", ROOT / "Temp" / "tests"))


def build_host(out):
    ide = Path(os.environ.get("XC_IDE_ROOT", ROOT.parents[1]))
    vc = ide / "data" / "VC" / "VC2022" / "14.41.34120"
    sdk = ide / "data" / "VC" / "Windows Kits" / "10"
    compiler = vc / "bin" / "Hostx64" / "x64" / "cl.exe"
    exe = out / "native_transport_host.exe"
    includes = [ROOT / "src", vc / "include"] + [sdk / "Include" / "10.0.19041.0" / p for p in ("ucrt", "um", "shared")]
    libraries = [vc / "lib" / "x64"] + [sdk / "Lib" / "10.0.19041.0" / p / "x64" for p in ("ucrt", "um")]
    command = [str(compiler), "/nologo", "/std:c++17", "/EHa", "/utf-8", "/W3", "/MT", "/O2"]
    command += ["/I" + str(p) for p in includes]
    command += [str(ROOT / "test" / "native_transport_host.cpp")]
    command += [str(ROOT / "src" / p) for p in ("json.cpp", "instance.cpp", "http.cpp", "mcp.cpp")]
    command += ["/Fe:" + str(exe), "/link"] + ["/LIBPATH:" + str(p) for p in libraries]
    command += ["kernel32.lib", "user32.lib", "ws2_32.lib"]
    env = dict(os.environ, TEMP=str(out), TMP=str(out), TMPDIR=str(out))
    result = subprocess.run(command, cwd=out, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, encoding="utf-8", errors="replace", timeout=120)
    (out / "build.log").write_text(result.stdout, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(result.stdout)
    return exe


class NativeHost:
    def __init__(self, exe, root, name, *, preferred=18766, port="0", token="CaseSensitive-TestToken", gate=None, wait=True):
        self.root = root
        self.state = root / (name + ".state")
        self.project = str(root / (name + "项目") / (name + ".xcproj"))
        self.state.write_text(self.project, encoding="utf-8")
        env = dict(os.environ, LOCALAPPDATA=str(root / "local"),
                   XC_MCP_INSTANCE_DIR=str(root / "instances"), TEMP=str(root), TMP=str(root))
        for key, value in (("XC_MCP_PORT", port), ("XC_MCP_TOKEN", token), ("XCMCP_TEST_GATE", gate)):
            env.pop(key, None)
            if value is not None:
                env[key] = str(value)
        self.env = env
        self.log = (root / (name + ".log")).open("w", encoding="utf-8")
        self.proc = subprocess.Popen([str(exe), str(self.state), str(preferred)], env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                                     text=True, encoding="utf-8")
        self.rows = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.info = None
        if wait:
            self.ready()

    def _read(self):
        try:
            for row in self.proc.stdout:
                self.rows.put(row)
        finally:
            self.rows.put(None)

    def ready(self):
        row = self.rows.get(timeout=15)
        if row is None:
            self.proc.wait(timeout=5)
            raise RuntimeError("native host exited: " + str(self.proc.returncode))
        self.info = json.loads(row)
        self.record = self.root / "instances" / (self.info["instanceId"] + ".json")
        if self.record.exists():
            self.metadata = json.loads(self.record.read_text(encoding="utf-8"))
            self.token = Path(self.metadata["tokenFile"]).read_text(encoding="utf-8")
        return self.info

    def command(self, line):
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        return self.ready()

    def connection(self, timeout=5):
        return http.client.HTTPConnection("127.0.0.1", self.info["port"], timeout=timeout)

    def request(self, method="GET", path="/health", body=None, headers=None, conn=None):
        own = conn is None
        conn = conn or self.connection()
        h = {"X-XC-Token": self.token, "Content-Type": "application/json"}
        if headers is not None:
            for k, v in headers.items():
                if v is None:
                    h.pop(k, None)
                else:
                    h[k] = v
        try:
            conn.request(method, path, body=body, headers=h)
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            if own:
                conn.close()

    def rpc(self, method="ping", params=None, headers=None, conn=None, rid=1):
        body = {"jsonrpc": "2.0", "method": method}
        if rid is not None:
            body["id"] = rid
        if params is not None:
            body["params"] = params
        return self.request("POST", "/mcp", json.dumps(body), headers, conn)

    def close(self):
        if self.proc.poll() is None:
            try:
                self.proc.stdin.write("quit\n")
                self.proc.stdin.flush()
                self.proc.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                # Only this test-created console process, never an IDE.
                self.proc.kill()
                self.proc.wait(timeout=5)
        if self.proc.stdin:
            self.proc.stdin.close()
        self.reader.join(timeout=2)
        self.proc.stdout.close()
        self.log.close()


@unittest.skipUnless(os.name == "nt", "Windows native transport")
class NativeTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        cls.build_temp = tempfile.TemporaryDirectory(prefix="native-build-", dir=TEMP_ROOT)
        cls.exe = build_host(Path(cls.build_temp.name))

    @classmethod
    def tearDownClass(cls):
        cls.build_temp.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="native-case-", dir=TEMP_ROOT)
        self.root = Path(self.temp.name)
        self.hosts = []

    def tearDown(self):
        for h in reversed(self.hosts):
            h.close()
        self.temp.cleanup()

    def host(self, name="A", **kwargs):
        # Register cleanup even when a deliberately failing host never gets ready.
        wait = kwargs.pop("wait", True)
        h = NativeHost(self.exe, self.root, name, wait=False, **kwargs)
        self.hosts.append(h)
        if wait:
            h.ready()
        return h

    def test_identity_and_fresh_project_health(self):
        h = self.host()
        status, raw = h.request()
        value = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(value["pid"], h.proc.pid)
        self.assertEqual(value["instanceId"], h.info["instanceId"])
        self.assertEqual(value["projectFile"], h.project)
        self.assertEqual(len(value["startedAt"]), 16)
        new_project = str(self.root / "另一个工程" / "新.xcproj")
        h.state.write_text(new_project, encoding="utf-8")
        self.assertEqual(json.loads(h.request()[1])["projectFile"], new_project)
        self.assertEqual(json.loads(h.record.read_text(encoding="utf-8"))["projectFile"], h.project)
        self.assertEqual(h.rpc(headers={"X-XC-Project": quote(h.project, safe="")})[0], 409)

    def test_preferred_port_fallback_and_explicit_conflict(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            preferred = probe.getsockname()[1]
        a = self.host(preferred=preferred, port=None)
        self.assertEqual(a.info["port"], preferred)
        b = self.host("B", preferred=preferred, port=None)
        self.assertNotEqual(a.info["port"], b.info["port"])
        c = self.host("Conflict", port=str(preferred), wait=False)
        self.assertEqual(c.proc.wait(timeout=10), 3)
        self.assertEqual(a.request()[0], 200)
        self.assertEqual(b.request()[0], 200)
        self.assertEqual(len(list((self.root / "instances").glob("*.json"))), 2)

    def test_concurrent_default_token_creation_and_restart(self):
        gate = self.root / "start.gate"
        hosts = [self.host(str(i), token=None, gate=gate, wait=False) for i in range(8)]
        gate.touch()
        for h in hosts:
            h.ready()
        tokens = {h.token for h in hosts}
        self.assertEqual(len(tokens), 1)
        token = next(iter(tokens))
        self.assertEqual(len(token), 64)
        self.assertEqual((self.root / "local" / "xc-mcp" / "token").read_text(), token)
        first = hosts[0]
        first.command("restart")
        self.assertEqual(first.token, token)
        for h in hosts:
            self.assertEqual(h.request()[0], 200)
        self.assertFalse(list(self.root.rglob("*.tmp")))

    def test_idle_connection_does_not_block_accept(self):
        h = self.host()
        with socket.create_connection(("127.0.0.1", h.info["port"]), timeout=3):
            time.sleep(0.05)
            started = time.monotonic()
            conn = h.connection(timeout=2)
            try:
                self.assertEqual(h.request(conn=conn)[0], 200)
                self.assertLess(time.monotonic() - started, 2)
            finally:
                conn.close();

    def test_keep_alive_pipelining_and_notifications(self):
        h = self.host()
        conn = h.connection()
        try:
            self.assertEqual(h.rpc(conn=conn)[0], 200)
            original = conn.sock
            for _ in range(5):
                self.assertEqual(json.loads(h.rpc(conn=conn)[1])["result"], {})
                self.assertIs(conn.sock, original)
            self.assertEqual(h.rpc("notifications/initialized", conn=conn, rid=None), (202, b""))
        finally:
            conn.close()
        body = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}).encode()
        header = ("POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-XC-Token: %s\r\n"
                  "Content-Type: application/json\r\nContent-Length: %d\r\n\r\n" %
                  (h.info["port"], h.token, len(body))).encode()
        with socket.create_connection(("127.0.0.1", h.info["port"]), timeout=5) as sock:
            sock.sendall((header + body) * 2)
            data = b""
            responses = []
            while len(responses) < 2:
                chunk = sock.recv(65536)
                self.assertTrue(chunk)
                data += chunk
                while b"\r\n\r\n" in data:
                    head, rest = data.split(b"\r\n\r\n", 1)
                    length = int(next(line.split(b":", 1)[1] for line in head.split(b"\r\n")
                                      if line.lower().startswith(b"content-length:")))
                    if len(rest) < length:
                        break
                    responses.append(json.loads(rest[:length]))
                    data = rest[length:]
            self.assertEqual([r["id"] for r in responses], [7, 7])

    def test_instance_and_unicode_project_guards(self):
        h = self.host()
        self.assertEqual(h.rpc(headers={"X-XC-Instance": "wrong-instance"})[0], 409)
        normalized = h.project.replace("\\", "/").upper().replace("/A.XCPROJ", "/./A.XCPROJ")
        headers = {"X-XC-Instance": h.info["instanceId"], "X-XC-Project": quote(normalized, safe="")}
        self.assertEqual(h.rpc(headers=headers)[0], 200)
        for bad in ("%FF", "%00", "%zz", "relative.xcproj", ""):
            self.assertEqual(h.rpc(headers={"X-XC-Project": bad})[0], 409)

    def test_auth_host_origin_and_bearer(self):
        h = self.host()
        for token in (None, "wrong", h.token.lower()):
            self.assertEqual(h.request(headers={"X-XC-Token": token})[0], 401)
        self.assertEqual(h.request(headers={"X-XC-Token": None, "Authorization": "Bearer " + h.token})[0], 200)
        for origin in ("", "http://evil.example"):
            self.assertEqual(h.request(headers={"Origin": origin})[0], 403)
        for host in ("", "evil.example", "127.0.0.1:1"):
            self.assertEqual(h.request(headers={"Host": host})[0], 403)
        self.assertEqual(h.request(headers={"Host": "localhost:%d" % h.info["port"]})[0], 200)

    def test_two_clients_same_ids_50_calls_each(self):
        hosts = [self.host("A"), self.host("B")]
        barrier = threading.Barrier(2)
        def client(h):
            conn = h.connection()
            try:
                barrier.wait(timeout=5)
                for i in range(50):
                    status, raw = h.rpc("tools/call", {"name": "xc_get_project" if i % 2 else "xc_status"},
                                        headers={"X-XC-Instance": h.info["instanceId"],
                                                 "X-XC-Project": quote(h.project, safe="")}, conn=conn, rid=1)
                    self.assertEqual(status, 200)
                    env = json.loads(raw)
                    self.assertEqual(env["id"], 1)
                    value = json.loads(env["result"]["content"][0]["text"])
                    self.assertEqual(value["instanceId"], h.info["instanceId"])
                    self.assertEqual(value["project_file"], h.project)
            finally:
                conn.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(client, hosts))

    def test_handler_stop_preserves_response_and_other_instance(self):
        a, b = self.host("A"), self.host("B")
        old_id = a.info["instanceId"]
        status, raw = a.rpc("tools/call", {"name": "xc_server", "arguments": {"action": "stop"}})
        self.assertEqual(status, 200)
        value = json.loads(json.loads(raw)["result"]["content"][0]["text"])
        self.assertFalse(value["httpRunning"])
        self.assertFalse(a.record.exists())
        self.assertEqual(b.request()[0], 200)
        a.command("start")
        self.assertEqual(a.info["instanceId"], old_id)
        self.assertEqual(a.request()[0], 200)
        self.assertEqual(b.request()[0], 200)

    def test_shutdown_joins_idle_workers_and_removes_only_own_record(self):
        a, b = self.host("A"), self.host("B")
        sockets = [socket.create_connection(("127.0.0.1", a.info["port"]), timeout=3) for _ in range(12)]
        try:
            started = time.monotonic()
            a.command("stop")
            self.assertLess(time.monotonic() - started, 3)
            self.assertFalse(a.record.exists())
            self.assertTrue(b.record.exists())
            self.assertEqual(b.request()[0], 200)
        finally:
            for s in sockets:
                s.close()

    def test_exception_does_not_terminate_server(self):
        h = self.host()
        self.assertEqual(h.rpc("tools/call", {"name": "test_throw"})[0], 500)
        self.assertEqual(h.request()[0], 200)
        self.assertIsNone(h.proc.poll())

    def test_framing_limits_and_protocol_errors(self):
        h = self.host()
        for extra, expected in (("Content-Length: 4194305\r\n", 413),
                                ("Content-Length: 0\r\nContent-Length: 1\r\n", 400),
                                ("Transfer-Encoding: chunked\r\n", 400)):
            with socket.create_connection(("127.0.0.1", h.info["port"]), timeout=5) as sock:
                request = ("POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-XC-Token: %s\r\n"
                           "Content-Type: application/json\r\n%s\r\n" % (h.info["port"], h.token, extra))
                sock.sendall(request.encode())
                self.assertIn(("HTTP/1.1 %d" % expected).encode(), sock.recv(4096))
        code, raw = h.request("POST", "/mcp", "{")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw)["error"]["code"], -32700)
        self.assertEqual(h.request(path="/mcp")[0], 405)
        self.assertEqual(h.request(path="/unknown")[0], 404)


    def test_health_snapshot_rechecks_project_after_abi_read(self):
        h = self.host()
        other = str(self.root / "切换后的工程" / "B.xcproj")
        h.state.write_text(h.project + "\n" + other, encoding="utf-8")
        h.command("arm-health-switch")
        code, _ = h.request(headers={"X-XC-Project": quote(h.project, safe="")})
        self.assertEqual(code, 409)
        self.assertEqual(json.loads(h.request()[1])["projectFile"], other)

    def test_restart_generations_share_one_ide_execution_lock(self):
        h = self.host()
        batch = [{"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": params}
                 for i, params in enumerate([
                     {"name": "xc_server", "arguments": {"action": "stop"}},
                     {"name": "xc_server", "arguments": {"action": "start"}},
                     {"name": "test_pause"}], 1)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(h.request, "POST", "/mcp", json.dumps(batch))
            gate = Path(str(h.state) + ".pause")
            deadline = time.monotonic() + 5
            while not gate.exists():
                self.assertLess(time.monotonic(), deadline, "batch did not restart and enter pause")
                time.sleep(0.005)
            new = json.loads(h.record.read_text(encoding="utf-8"))
            conn = http.client.HTTPConnection("127.0.0.1", new["port"], timeout=5)
            try:
                code, raw = h.rpc("tools/call", {"name": "xc_status"}, conn=conn)
                self.assertEqual(code, 200)
                value = json.loads(json.loads(raw)["result"]["content"][0]["text"])
                self.assertEqual(value["maxActiveTools"], 1)
            finally:
                conn.close()
            self.assertEqual(pending.result(timeout=5)[0], 200)
        h.command("status")


if __name__ == "__main__":
    unittest.main(verbosity=2)
