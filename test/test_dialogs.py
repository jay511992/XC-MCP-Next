# -*- coding: utf-8 -*-
"""Real MessageBoxW/XCGUI acceptance tests for xc_get_dialogs.

Run: python -I -B test/test_dialogs.py
The host links the production tools/HTTP/MCP/dialog code. No IDE is started and
no production plugin is deployed. Build products and all evidence stay below
PI_SCRATCH_DIR (or the permitted project Temp/tests fallback).
"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = Path(os.environ.get("PI_SCRATCH_DIR", ROOT / "Temp" / "tests"))
SCHEMA_SHA256 = "e0f0ef7e6ff672d2a31702bf7e9096458d856062528b2052ade985e03132ad8e"


def build_host(out):
    ide = Path(os.environ.get("XC_IDE_ROOT", ROOT.parents[1]))
    vc = ide / "data" / "VC" / "VC2022" / "14.41.34120"
    sdk = ide / "data" / "VC" / "Windows Kits" / "10"
    compiler = vc / "bin" / "Hostx64" / "x64" / "cl.exe"
    source_dll = ide / "XCGUI.dll"
    if not compiler.is_file() or not source_dll.is_file():
        raise RuntimeError("required native compiler or real XCGUI.dll is missing")
    if source_dll.stat().st_size != 3755008:
        raise RuntimeError("unexpected real XCGUI.dll size: %d" % source_dll.stat().st_size)
    dll = out / "XCGUI.dll"
    shutil.copy2(source_dll, dll)
    exe = out / "dialogs_host.exe"
    includes = [ROOT / "src", vc / "include"] + [
        sdk / "Include" / "10.0.19041.0" / p for p in ("ucrt", "um", "shared")]
    libraries = [vc / "lib" / "x64"] + [
        sdk / "Lib" / "10.0.19041.0" / p / "x64" for p in ("ucrt", "um")]
    command = [str(compiler), "/nologo", "/std:c++17", "/EHa", "/utf-8", "/W3", "/MT", "/O2",
               "/DXCMCP_DIALOG_TEST_WORKER_DELAY"]
    command += ["/I" + str(p) for p in includes]
    command += [str(ROOT / "test" / "dialogs_host.cpp")]
    command += [str(ROOT / "src" / p) for p in
                ("json.cpp", "instance.cpp", "http.cpp", "mcp.cpp", "plug_abi.cpp",
                 "tools.cpp", "dialogs.cpp")]
    command += [str(ROOT / "test" / "native_log_stub.cpp")]
    command += ["/Fe:" + str(exe), "/link"]
    command += ["/LIBPATH:" + str(p) for p in libraries]
    command += ["/LIBPATH:" + str(ROOT / "lib"), "kernel32.lib", "user32.lib",
                "ws2_32.lib", "comctl32.lib", "XCGUI.lib"]
    env = dict(os.environ, TEMP=str(out), TMP=str(out), TMPDIR=str(out),
               PYTHONPYCACHEPREFIX=str(out / "pycache"))
    result = subprocess.run(command, cwd=out, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
                            timeout=180)
    (out / "build.log").write_text(result.stdout, encoding="utf-8")
    (out / "build-command.json").write_text(json.dumps(command, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    if result.returncode:
        raise RuntimeError(result.stdout)
    return exe, dll


class NativeHost:
    def __init__(self, exe, root, name):
        self.root = root
        self.name = name
        self.marker = "DIALOG-%s-弹窗😀" % name
        self.project = str(root / (name + "工程") / (name + ".xcproj"))
        self.registry = root / "instances"
        self.temp = root / (name + "-temp")
        self.temp.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, LOCALAPPDATA=str(root / "local"),
                   XC_MCP_INSTANCE_DIR=str(self.registry), TEMP=str(self.temp),
                   TMP=str(self.temp), TMPDIR=str(self.temp),
                   PYTHONPYCACHEPREFIX=str(root / "pycache"),
                   XC_MCP_PORT="0", XC_MCP_TOKEN="CaseSensitive-Dialog-" + name,
                   XCMCP_NATIVE_LOG=str(root / (name + "-transport.log")))
        for key in ("XC_MCP_URL", "XC_MCP_INSTANCE", "XC_MCP_PROJECT", "XCMCP_TEST_GATE"):
            env.pop(key, None)
        self.env = env
        self.log = (root / (name + "-host.log")).open("w", encoding="utf-8")
        self.fixture_log = (root / (name + "-fixture.jsonl")).open("a", encoding="utf-8")
        self.proc = subprocess.Popen(
            [str(exe), self.marker, "0", self.project], env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
            text=True, encoding="utf-8", errors="strict", bufsize=1)
        self.rows = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.sequence = 0
        self.info = None

    def _read(self):
        try:
            for row in self.proc.stdout:
                self.rows.put(row)
        finally:
            self.rows.put(None)

    def ready(self):
        row = self.rows.get(timeout=25)
        if row is None:
            self.proc.wait(timeout=5)
            raise RuntimeError("dialog host exited %s; see %s" %
                               (self.proc.returncode, self.root / (self.name + "-host.log")))
        self.info = json.loads(row)
        if self.info["pid"] != self.proc.pid:
            raise RuntimeError("host PID mismatch")
        self.record = self.registry / (self.info["instanceId"] + ".json")
        self.metadata = json.loads(self.record.read_text(encoding="utf-8"))
        self.token = Path(self.metadata["tokenFile"]).read_text(encoding="utf-8")
        return self.info

    def command(self, command, timeout=20, **fields):
        self.sequence += 1
        request = dict(fields, id=self.sequence, command=command)
        self.proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        row = self.rows.get(timeout=timeout)
        if row is None:
            raise RuntimeError("dialog host exited during fixture command " + command)
        response = json.loads(row)
        self.fixture_log.write(json.dumps({"request": request, "response": response},
                                          ensure_ascii=False) + "\n")
        self.fixture_log.flush()
        if not response.get("ok"):
            raise RuntimeError("fixture command failed: " + json.dumps(response, ensure_ascii=False))
        return response

    def connection(self, timeout=8):
        return http.client.HTTPConnection("127.0.0.1", self.info["port"], timeout=timeout)

    def rpc(self, method, params=None, rid=1, timeout=8):
        body = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            body["params"] = params
        conn = self.connection(timeout)
        try:
            conn.request("POST", "/mcp", json.dumps(body, ensure_ascii=False),
                         headers={"X-XC-Token": self.token,
                                  "X-XC-Instance": self.info["instanceId"],
                                  "Content-Type": "application/json"})
            response = conn.getresponse()
            raw = response.read()
            return response.status, raw
        finally:
            conn.close()

    def close(self):
        if self.proc.poll() is None:
            try:
                self.command("quit", timeout=8)
                self.proc.wait(timeout=10)
            except (OSError, RuntimeError, queue.Empty, subprocess.TimeoutExpired):
                # Only the GUI host created by this test may be killed.
                self.proc.kill()
                self.proc.wait(timeout=5)
        if self.proc.stdin:
            self.proc.stdin.close()
        self.reader.join(timeout=2)
        self.proc.stdout.close()
        self.fixture_log.close()
        self.log.close()


class StdioBridge:
    def __init__(self, host):
        self.host = host
        self.root = host.root
        self.sequence = 1000
        self.log = (host.root / (host.name + "-bridge.log")).open("w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, "-I", "-B", str(ROOT / "bridge" / "stdio_bridge.py"),
             "--instance", host.info["instanceId"], "--instance-dir", str(host.registry)],
            env=host.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
            text=True, encoding="utf-8", errors="strict", bufsize=1)
        self.rows = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            for row in self.proc.stdout:
                self.rows.put(row)
        finally:
            self.rows.put(None)

    def rpc(self, method, params=None, timeout=15):
        self.sequence += 1
        request = {"jsonrpc": "2.0", "id": self.sequence, "method": method}
        if params is not None:
            request["params"] = params
        self.proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        row = self.rows.get(timeout=timeout)
        if row is None:
            raise RuntimeError("stdio bridge exited; see " + self.host.name + "-bridge.log")
        response = json.loads(row)
        if response.get("id") != self.sequence or "error" in response:
            raise RuntimeError("stdio bridge RPC failed: " + row)
        return response

    def close(self):
        if self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.reader.join(timeout=2)
        self.proc.stdout.close()
        self.log.close()


@unittest.skipUnless(os.name == "nt", "real Windows GUI acceptance test")
class DialogAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        cls.run_root = Path(tempfile.mkdtemp(prefix="dialogs-acceptance-", dir=TEMP_ROOT))
        os.environ["PYTHONPYCACHEPREFIX"] = str(cls.run_root / "pycache")
        cls.build_root = cls.run_root / "build"
        cls.build_root.mkdir()
        cls.exe, cls.dll = build_host(cls.build_root)
        print("DIALOG_TEST_ARTIFACTS=" + str(cls.run_root), flush=True)

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="case-", dir=self.run_root))
        self.hosts = []
        self.bridges = []
        self.evidence = (self.root / "mcp-evidence.jsonl").open("a", encoding="utf-8")

    def tearDown(self):
        for bridge in reversed(self.bridges):
            bridge.close()
        for host in reversed(self.hosts):
            host.close()
        self.evidence.close()

    def host(self, name="A"):
        host = NativeHost(self.exe, self.root, name)
        self.hosts.append(host)
        host.ready()
        self.assertEqual(Path(host.info["xcgui"]), self.dll)
        return host

    def bridge(self, host):
        bridge = StdioBridge(host)
        self.bridges.append(bridge)
        return bridge

    def rpc(self, host, method, params=None, bridge=None):
        if bridge:
            return bridge.rpc(method, params)
        status, raw = host.rpc(method, params)
        self.assertEqual(status, 200, raw)
        return json.loads(raw)

    def tool(self, host, name, arguments=None, bridge=None):
        params = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        envelope = self.rpc(host, "tools/call", params, bridge)
        result = envelope["result"]
        value = json.loads(result["content"][0]["text"])
        self.evidence.write(json.dumps({"host": host.name, "pid": host.proc.pid,
                                        "transport": "stdio" if bridge else "http",
                                        "tool": name, "arguments": arguments,
                                        "isError": result.get("isError", False), "value": value},
                                       ensure_ascii=False) + "\n")
        self.evidence.flush()
        return value, result.get("isError", False)

    def dialogs(self, host, arguments=None, bridge=None):
        value, is_error = self.tool(host, "xc_get_dialogs", arguments, bridge)
        self.assertEqual(value["pid"], host.proc.pid)
        return value, is_error

    def assertEmpty(self, host, bridge=None):
        value, is_error = self.dialogs(host, bridge=bridge)
        self.assertFalse(is_error)
        self.assertTrue(value["ok"])
        self.assertTrue(value["complete"])
        self.assertFalse(value["truncated"])
        self.assertEqual(value["count"], 0)
        self.assertEqual(value["dialogs"], [])
        self.assertEqual(value["errors"], [])
        return value

    def assertOne(self, value, fixture, title, text, source):
        self.assertTrue(value["ok"], value)
        self.assertTrue(value["complete"], value)
        self.assertEqual(value["errors"], [])
        self.assertEqual(value["count"], 1, value)
        row = value["dialogs"][0]
        self.assertEqual(int(row["hwnd"], 16), int(fixture["hwnd"], 16))
        self.assertEqual(row["pid"], fixture["pid"])
        self.assertEqual(row["source"], source)
        self.assertEqual(row["title"], title)
        self.assertEqual(row["text"], text)
        self.assertNotEqual(row["title"], row["text"])
        self.assertTrue(row["readable"])
        self.assertFalse(row["truncated"])
        return row

    def assertDiagnostic(self, value, code, fixture=None):
        self.assertFalse(value["ok"], value)
        self.assertFalse(value["complete"], value)
        self.assertGreaterEqual(value["count"], 1, value)
        self.assertIn(code, [error.get("code") for error in value["errors"]], value)
        matching = [row for row in value["dialogs"] if row.get("error") == code]
        self.assertTrue(matching, value)
        if fixture:
            self.assertTrue(any(int(row["hwnd"], 16) == int(fixture["hwnd"], 16)
                                for row in matching), value)
        for row in matching:
            self.assertEqual(row.get("text", ""), "")
        return matching

    def test_01_status_schema_hash_and_http_call(self):
        host = self.host()
        status, _ = self.tool(host, "xc_status")
        self.assertEqual(status["toolCount"], 30)  # Deliberately before tools/list.
        self.assertEqual(status["ver"], 339)
        self.assertEqual(status["pid"], host.proc.pid)
        envelope = self.rpc(host, "tools/list")
        tools = envelope["result"]["tools"]
        self.assertEqual(len(tools), 30)
        names = [tool["name"] for tool in tools]
        self.assertEqual(len(set(names)), 30)
        self.assertIn("xc_get_dialogs", names)
        schemas = {tool["name"]: tool["inputSchema"] for tool in tools
                   if tool["name"] != "xc_get_dialogs"}
        self.assertEqual(len(schemas), 29)
        canonical = json.dumps(schemas, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"))
        self.assertEqual(hashlib.sha256(canonical.encode("utf-8")).hexdigest(), SCHEMA_SHA256)
        self.assertEmpty(host)

    def test_02_real_messagebox_unicode_repeat_and_close(self):
        host = self.host()
        self.assertEmpty(host)
        title = host.marker + " 原生标题🚨"
        text = host.marker + " 第一行错误\n第二行中文多行😀\n第三行：不得混入标题"
        fixture = host.command("create_native", title=title, text=text)
        self.assertEqual(fixture["className"], "#32770")
        first, is_error = self.dialogs(host)
        self.assertFalse(is_error)
        self.assertOne(first, fixture, title, text, "win32")
        second, _ = self.dialogs(host)
        self.assertEqual(second, first)  # Reads neither close nor mutate the real MessageBox.
        self.assertTrue(host.command("status")["nativeVisible"])
        host.command("close_native")
        self.assertEmpty(host)

    def test_03_real_xcg_message_unicode_repeat_and_close(self):
        host = self.host()
        title = host.marker + " XCGUI标题🚫"
        text = host.marker + " 炫彩第一行\n第二行错误详情🧯\n第三行正文"
        fixture = host.command("create_xmsg", title=title, text=text)
        self.assertTrue(host.command("status")["xmsgVisible"])
        self.assertNotEqual(fixture["className"], "#32770")
        self.assertTrue(host.command("status")["xmsgVisible"])
        first, is_error = self.dialogs(host)
        self.assertFalse(is_error)
        self.assertOne(first, fixture, title, text, "xcgui")
        second, _ = self.dialogs(host)
        self.assertEqual(second, first)
        self.assertTrue(host.command("status")["xmsgVisible"])
        host.command("close_xmsg")
        self.assertEmpty(host)

    def test_04_two_hosts_http_and_stdio_instance_isolation(self):
        a, b = self.host("A"), self.host("B")
        self.assertNotEqual(a.proc.pid, b.proc.pid)
        self.assertNotEqual(a.info["port"], b.info["port"])
        title_a, title_b = a.marker + " A专属标题", b.marker + " B专属标题"
        text_a = a.marker + " A进程错误\nA第二行🍎"
        text_b = b.marker + " B进程错误\nB第二行🍊"
        fixture_a = a.command("create_native", title=title_a, text=text_a)
        fixture_b = b.command("create_native", title=title_b, text=text_b)
        for host, fixture, title, text, forbidden in (
                (a, fixture_a, title_a, text_a, b.marker),
                (b, fixture_b, title_b, text_b, a.marker)):
            value, _ = self.dialogs(host)
            self.assertOne(value, fixture, title, text, "win32")
            self.assertNotIn(forbidden, json.dumps(value, ensure_ascii=False))
        bridge_a, bridge_b = self.bridge(a), self.bridge(b)
        for host, bridge, fixture, title, text, forbidden in (
                (a, bridge_a, fixture_a, title_a, text_a, b.marker),
                (b, bridge_b, fixture_b, title_b, text_b, a.marker)):
            value, is_error = self.dialogs(host, bridge=bridge)
            self.assertFalse(is_error)
            self.assertOne(value, fixture, title, text, "win32")
            self.assertNotIn(forbidden, json.dumps(value, ensure_ascii=False))
        a.command("close_native")
        self.assertEmpty(a)
        value, _ = self.dialogs(b, bridge=bridge_b)
        self.assertOne(value, fixture_b, title_b, text_b, "win32")
        b.command("close_native")
        self.assertEmpty(b, bridge=bridge_b)

    def test_05_real_menu_tooltip_and_main_output_excluded(self):
        host = self.host()
        self.assertEmpty(host)
        menu = host.command("create_menu", text=host.marker + " 标准菜单错误项")
        self.assertEqual(menu["className"], "#32768")
        self.assertTrue(menu["visible"])
        value, _ = self.dialogs(host)
        self.assertEqual((value["ok"], value["count"], value["dialogs"], value["errors"]),
                         (True, 0, [], []))
        host.command("close_menu")
        tooltip = host.command("create_tooltip", text=host.marker + " 工具提示错误\n不是弹窗")
        self.assertEqual(tooltip["className"], "tooltips_class32")
        self.assertTrue(tooltip["visible"])
        value, _ = self.dialogs(host)
        self.assertEqual((value["ok"], value["count"], value["dialogs"], value["errors"]),
                         (True, 0, [], []))
        self.assertNotIn("主窗输出错误", json.dumps(value, ensure_ascii=False))
        host.command("close_tooltip")
        self.assertEmpty(host)

    def test_06_truncation_utf8_and_invalid_arguments(self):
        host = self.host()
        body = "界" * 127 + "😀" + "尾" * 80 + "\n第二行🚀"
        self.assertEqual(body[:128][-1], "😀")
        for kind, close, source in (("create_native", "close_native", "win32"),
                                    ("create_xmsg", "close_xmsg", "xcgui")):
            with self.subTest(source=source):
                title = host.marker + " 截断标题" + source
                fixture = host.command(kind, title=title, text=body)
                value, is_error = self.dialogs(host, {"maxChars": 128})
                self.assertFalse(is_error)
                self.assertTrue(value["ok"])
                self.assertFalse(value["complete"])
                self.assertTrue(value["truncated"])
                row = self.assertOne({**value, "complete": True, "dialogs": [{
                    **value["dialogs"][0], "truncated": False}]}, fixture, title, body[:128], source)
                self.assertTrue(value["dialogs"][0]["truncated"])
                self.assertEqual(len(value["dialogs"][0]["text"]), 128)
                value["dialogs"][0]["text"].encode("utf-8", "strict")
                full, _ = self.dialogs(host)
                self.assertOne(full, fixture, title, body, source)
                host.command(close)
                self.assertEmpty(host)
        invalid = ({"maxChars": 127}, {"maxChars": 65537}, {"maxChars": 128.5},
                   {"maxChars": "128"}, {"maxChars": True}, {"maxChars": None},
                   {"unknown": 1}, {"maxChars": 128, "unknown": 1})
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                value, is_error = self.dialogs(host, arguments)
                self.assertTrue(is_error)
                self.assertFalse(value["ok"])
                self.assertEqual(value["count"], 0)
                self.assertEqual(value["dialogs"], [])
                self.assertEqual(value["errors"][0]["code"], "invalid_arguments")
                self.assertTrue(value["errors"][0]["message"])

    def test_07_native_static_gettext_failure_is_diagnostic(self):
        host = self.host()
        title = host.marker + " GETTEXT故障标题"
        fixture = host.command("create_native", title=title,
                               text=host.marker + " Static正文读取故障", fault="fail")
        value, is_error = self.dialogs(host)
        self.assertTrue(is_error)
        rows = self.assertDiagnostic(value, "body_read_failed", fixture)
        self.assertEqual(rows[0]["source"], "win32")
        self.assertGreaterEqual(host.command("status")["gettextCalls"], 1)
        host.command("close_native")
        self.assertEmpty(host)

    def test_08_blocked_ui_and_slow_gettext_timeout_recover(self):
        host = self.host()
        title = host.marker + " UI阻塞标题"
        text = host.marker + " UI阻塞正文\n不得返回旧文本"
        fixture = host.command("create_native", title=title, text=text)
        host.command("block_ui", duration=2600)
        started = time.monotonic()
        value, is_error = self.dialogs(host)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 5.0)
        self.assertTrue(is_error)
        rows = self.assertDiagnostic(value, "ui_timeout", fixture)
        self.assertTrue(rows[0].get("candidate"))
        host.command("status")  # Queued until the controlled UI sleep has recovered.
        host.command("close_native")
        self.assertEmpty(host)

        slow_title = host.marker + " GETTEXT慢读取标题"
        slow_text = host.marker + " GETTEXT正在执行2600ms\n新正文🚀"
        slow = host.command("create_native", title=slow_title, text=slow_text, fault="slow")
        before = host.command("status")["gettextCalls"]
        started = time.monotonic()
        timed_out, is_error = self.dialogs(host)
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertTrue(is_error)
        rows = self.assertDiagnostic(timed_out, "ui_timeout", slow)
        self.assertTrue(rows[0].get("candidate"))
        self.assertEqual(rows[0]["title"], "")
        recovered = host.command("status", timeout=8)
        self.assertGreater(recovered["gettextCalls"], before)
        current, current_error = self.dialogs(host)
        self.assertFalse(current_error)
        self.assertOne(current, slow, slow_title, slow_text, "win32")
        self.assertIsNone(host.proc.poll())
        host.command("close_native")
        self.assertEmpty(host)

    def test_09_controlled_close_race_is_window_disappeared(self):
        host = self.host()
        fixture = host.command("create_native", title=host.marker + " 关闭竞态标题",
                               text=host.marker + " 读取排队期间关闭")
        host.command("race_close")  # UI sleeps 300ms, then closes only its own MessageBox.
        value, is_error = self.dialogs(host)
        self.assertTrue(is_error)
        self.assertDiagnostic(value, "window_disappeared", fixture)
        self.assertEmpty(host)


    def test_10_groupbox_decoration_is_not_interactive(self):
        host = self.host()
        title = host.marker + " GroupBox提示标题"
        text = host.marker + " GroupBox正文\n第二行🌟"
        fixture = host.command("create_group", title=title, text=text)
        self.assertTrue(fixture["visible"])
        value, is_error = self.dialogs(host)
        self.assertFalse(is_error)
        self.assertOne(value, fixture, title, text, "win32")
        self.assertTrue(host.command("status")["groupVisible"])
        host.command("close_group")
        self.assertEmpty(host)

    def test_11_worker_resumes_after_ui_publication_deadline(self):
        host = self.host()
        title = host.marker + " 后台超期醒来标题"
        text = host.marker + " 不得发布的超期正文"
        fixture = host.command("create_native", title=title, text=text)
        host.command("delay_worker", duration=1900)
        started = time.monotonic()
        value, is_error = self.dialogs(host)
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertTrue(is_error)
        # UI reads quickly and signals, but the deliberately paused worker wakes
        # after its absolute deadline; it must not publish that completed value.
        self.assertDiagnostic(value, "read_deadline", fixture)
        current, current_error = self.dialogs(host)
        self.assertFalse(current_error)
        self.assertOne(current, fixture, title, text, "win32")
        host.command("close_native")
        self.assertEmpty(host)

    def test_12_xcg_version_guard_and_recovery(self):
        host = self.host()
        title = host.marker + " 版本门禁标题"
        text = host.marker + " 版本不符绝不读私有布局"
        host.command("set_xcg_timestamp", duration=0xBAD)
        fixture = host.command("create_xmsg", title=title, text=text)
        value, is_error = self.dialogs(host)
        self.assertTrue(is_error)
        self.assertDiagnostic(value, "xcgui_layout_unverified", fixture)
        host.command("close_xmsg")
        self.assertEmpty(host)
        host.command("set_xcg_timestamp", duration=0x69DD049C)
        # The adapter is cached for one loaded DLL's lifetime. A restored DLL is
        # a fresh load scenario, so use a fresh isolated host rather than expect
        # an in-process DLL replacement to be supported.
        recovered = self.host("B")
        fixture = recovered.command("create_xmsg", title=title, text=text)
        current, current_error = self.dialogs(recovered)
        self.assertFalse(current_error)
        self.assertOne(current, fixture, title, text, "xcgui")
        recovered.command("close_xmsg")
        self.assertEmpty(recovered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
