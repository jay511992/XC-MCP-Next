# -*- coding: utf-8 -*-
"""Offline multi-instance tests. No real IDE is started, enumerated, or closed.

Run: python -I -B test\test_instances.py
All temporary files/cache directories are confined to the session scratch root.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import ntpath
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock
import urllib.error
import urllib.parse

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path(os.environ.get("PI_SCRATCH_DIR") or
               r"C:\Users\p\.pi-desktop\scratch\b8ab41d6-4e5f-4ecf-996c-aee68b00b7b7")
for name in ("TEMP", "TMP", "TMPDIR", "XDG_CACHE_HOME", "PYTHONPYCACHEPREFIX"):
    os.environ[name] = str(SCRATCH)
tempfile.tempdir = str(SCRATCH)
sys.path.insert(0, str(ROOT / "bridge"))
import instances


def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = load_script("test_stdio_bridge", ROOT / "bridge" / "stdio_bridge.py")
wait_ready = load_script("test_wait_ready", ROOT / "wait_ready.py")
xcbuild = load_script("test_xcbuild", ROOT / "xcbuild" / "xcbuild.py")


class Endpoint:
    """Authenticated loopback fixture implementing the fixed native wire contract."""
    def __init__(self, registry, pid, project, exe, *, register=True, legacy=False):
        self.registry = registry
        self.token = "Private-Test-Token-%d" % pid
        self.requests = []
        self.legacy = legacy
        self.health_override = None
        self.before_request = None
        self.after_post = None
        self.post_status = None
        self.redirect = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def send(self, status, value=None, headers=None):
                data = b"" if value is None else json.dumps(value, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                for key, val in (headers or {}).items():
                    self.send_header(key, val)
                self.end_headers()
                if data:
                    self.wfile.write(data)

            def handle_request(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                owner.requests.append((self.command, self.path, dict(self.headers), raw))
                if owner.before_request:
                    owner.before_request(self.command)
                if self.headers.get("X-XC-Token") != owner.token:
                    self.send(401, {"error": "authentication required"})
                    return
                if not owner.legacy:
                    expected = self.headers.get("X-XC-Instance")
                    project = self.headers.get("X-XC-Project")
                    if expected and expected != owner.info["instanceId"]:
                        self.send(409, {"error": "instance mismatch"})
                        return
                    if project and (not owner.info["projectFile"] or
                                    instances.normalize_project(urllib.parse.unquote(project)) !=
                                    instances.normalize_project(owner.info["projectFile"])):
                        self.send(409, {"error": "project mismatch"})
                        return
                if owner.redirect:
                    self.send(302, headers={"Location": owner.redirect})
                    return
                if self.command == "GET" and self.path == "/health":
                    self.send(200, owner.health_override if owner.health_override is not None else
                              ({"status": "ok"} if owner.legacy else owner.info))
                    return
                if self.command != "POST" or self.path != "/mcp":
                    self.send(404)
                    return
                if owner.post_status:
                    self.send(owner.post_status, {"error": "test rejection"})
                    return
                body = json.loads(raw)

                def reply(request):
                    if not isinstance(request, dict) or not isinstance(request.get("method"), str):
                        return {"jsonrpc": "2.0", "id": None,
                                "error": {"code": -32600, "message": "Invalid Request"}}
                    if "id" not in request:
                        return None
                    result = {"instanceId": owner.info["instanceId"],
                              "projectFile": owner.info["projectFile"], "echo": request.get("params")}
                    if request["method"] == "tools/call":
                        result = {"content": [{"type": "text", "text": json.dumps(
                            {"ok": True, "toolCount": 29, "ver": "offline", **result})}]}
                    return {"jsonrpc": "2.0", "id": request["id"], "result": result}

                if isinstance(body, list) and body:
                    result = [item for item in (reply(value) for value in body) if item is not None]
                else:
                    result = reply(body)
                self.send(200 if result else 202, result or None)
                if owner.after_post:
                    owner.after_post()

            do_GET = handle_request
            do_POST = handle_request

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        port = self.server.server_port
        stamp = "%016x" % (0x01da000000000000 + pid)
        instance = "%d-%s" % (pid, stamp)
        self.info = {"schemaVersion": 1, "server": "xc-mcp-next", "status": "ok",
                     "instanceId": instance, "pid": pid, "startedAt": stamp,
                     "port": port, "url": "http://127.0.0.1:%d/mcp" % port,
                     "ideExe": str(exe), "hwnd": pid + 50000,
                     "projectFile": project, "projectDir": ntpath.dirname(project),
                     "projectName": ntpath.basename(project),
                     "tokenFile": str(registry / (instance + ".token"))}
        self.token_path = Path(self.info["tokenFile"])
        self.token_path.write_text(self.token, encoding="utf-8")
        self.record = registry / (instance + ".json")
        if register:
            self.write_record()
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.stopped = False

    @property
    def url(self):
        return self.info["url"]

    @property
    def instance_id(self):
        return self.info["instanceId"]

    def write_record(self, value=None):
        self.record.write_text(json.dumps(self.info if value is None else value), encoding="utf-8")

    def switch_project(self, project):
        self.info.update(projectFile=project, projectDir=ntpath.dirname(project), projectName=ntpath.basename(project))

    def posts(self):
        return [request for request in self.requests if request[0] == "POST"]

    def stop(self):
        if not self.stopped:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
            self.stopped = True


class LocalFixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mcp-instances-", dir=SCRATCH)
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.registry = self.tmp / "instances"
        self.registry.mkdir()
        self.exe = self.tmp / "IDE" / "炫彩IDE.exe"
        self.exe.parent.mkdir()
        self.exe.touch()  # inert file, never executed
        self.project = str(self.tmp / "中文工程 A.xcproj")
        self.other_project = str(self.tmp / "另一个 B.xcproj")
        Path(self.project).touch()
        Path(self.other_project).touch()
        self.legacy_token = self.tmp / "xc-mcp" / "token"
        self.legacy_token.parent.mkdir()
        self.legacy_token.write_text("Legacy-Test-Token", encoding="utf-8")
        env = mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.tmp),
                "XC_MCP_INSTANCE_DIR": str(self.registry), "XC_MCP_TOKEN": "",
                "XC_MCP_URL": "", "XC_MCP_INSTANCE": "", "XC_MCP_PROJECT": ""})
        env.start()
        self.addCleanup(env.stop)
        self.next_pid = 12000

    def endpoint(self, project=None, **kwargs):
        self.next_pid += 1
        server = Endpoint(self.registry, self.next_pid, project or self.project, self.exe, **kwargs)
        self.addCleanup(server.stop)
        return server

    def run_script(self, relative, args=(), requests=None, env=None):
        return subprocess.run([sys.executable, "-I", "-B", str(ROOT / relative), *args],
                              input=requests, encoding="utf-8", capture_output=True,
                              env=dict(os.environ, **(env or {})), timeout=20)


class DiscoveryTests(LocalFixture):
    def test_windows_unicode_path_normalization_is_full_path_only(self):
        self.assertEqual(instances.normalize_project(r"C:\工程\子目录\..\应用.XCPROJ"),
                         instances.normalize_project("c:/工程/./应用.xcproj"))
        self.assertEqual(instances.normalize_project(r"\\?\C:\工程\应用.xcproj"),
                         instances.normalize_project(r"C:\工程\应用.xcproj"))
        self.assertEqual(instances.normalize_project(r"\\?\UNC\PC\共享\项目.xcproj"),
                         instances.normalize_project(r"\\pc\共享\项目.xcproj"))
        for path in ("应用.xcproj", r"C:应用.xcproj", r"\工程\应用.xcproj", "", r"\\.\C:\a.xcproj"):
            with self.subTest(path=path), self.assertRaises(instances.ConfigurationError):
                instances.normalize_project(path)

    def test_unsafe_urls_rejected_before_network_or_token_access(self):
        bad = ("https://127.0.0.1/mcp", "http://example.com/mcp", "file:///C:/token",
               "http://127.0.0.1@evil.test/mcp", "http://user:secret@localhost/mcp",
               "http://127.0.0.1.evil.test/mcp", "http://localhost:0/mcp", "http://localhost:65536/mcp",
               "http://localhost/mcp?x=1", "http://localhost/mcp#x", "http://localhost/mcp?",
               "http://localhost:/mcp", "http://localhost\\@example.com/mcp", "http://localhost/mcp\n")
        with mock.patch.object(instances.http.client, "HTTPConnection") as network:
            for url in bad:
                with self.subTest(url=url), self.assertRaises(instances.ConfigurationError):
                    instances.resolve(url=url)
            network.assert_not_called()

    def test_zero_singleton_and_multiple_live_instances(self):
        with self.assertRaises(instances.NoMatchError):
            instances.resolve()
        first = self.endpoint()
        self.assertEqual(instances.resolve().instance_id, first.instance_id)
        second = self.endpoint()
        with self.assertRaises(instances.AmbiguousMatchError):
            instances.resolve()
        with self.assertRaises(instances.AmbiguousMatchError):
            instances.resolve(project=self.project)
        self.assertEqual(instances.resolve(project=self.project, instance=second.instance_id).instance_id,
                         second.instance_id)
        with self.assertRaises(instances.NoMatchError):
            instances.resolve(project=self.other_project, instance=first.instance_id)

    def test_discovery_uses_live_project_not_registry_snapshot(self):
        server = self.endpoint()
        server.switch_project(self.other_project)
        targets = instances.discover()
        self.assertEqual(targets[0].info["projectFile"], self.other_project)
        with self.assertRaises(instances.NoMatchError):
            instances.resolve(project=self.project)
        target = instances.resolve(project=self.other_project.upper().replace("\\", "/"))
        self.assertEqual(target.instance_id, server.instance_id)
        guard = server.requests[-1][2]["X-XC-Project"]
        self.assertEqual(guard, urllib.parse.quote(instances.normalize_project(self.other_project), safe=""))
        self.assertNotIn("另", guard)

    def test_malformed_dead_and_invalid_schema_records_are_excluded(self):
        server = self.endpoint()
        dead = self.endpoint(self.other_project)
        dead.stop()
        (self.registry / "broken.json").write_text("{broken", encoding="utf-8")
        variants = ({"schemaVersion": 2}, {"pid": True}, {"port": 0}, {"startedAt": "yesterday"},
                    {"url": "http://remote.example/mcp"}, {"tokenFile": "relative.token"},
                    {"ideExe": "relative.exe"}, {"status": "starting"}, {"hwnd": -1},
                    {"tokenFile": str(self.legacy_token)})
        original = dict(server.info)
        for update in variants:
            with self.subTest(update=update):
                server.write_record(dict(original, **update))
                self.assertEqual(instances.discover(timeout=0.03), [])
        server.write_record()
        self.assertEqual([target.instance_id for target in instances.discover(timeout=0.03)], [server.instance_id])

    def test_live_identity_must_match_all_registered_identity_fields(self):
        server = self.endpoint()
        for update in ({"instanceId": "another-process"}, {"pid": 42},
                       {"startedAt": "0000000000000001"},
                       {"url": "http://127.0.0.1:1/mcp", "port": 1},
                       {"url": server.url.replace("127.0.0.1", "localhost")},
                       {"ideExe": str(self.tmp / "other.exe")}, {"schemaVersion": 9}):
            with self.subTest(update=update):
                server.health_override = dict(server.info, **update)
                self.assertEqual(instances.discover(), [])
        server.health_override = {"status": "ok"}
        self.assertEqual(instances.discover(), [])

    def test_unauthenticated_record_is_excluded(self):
        server = self.endpoint()
        server.token_path.write_text("wrong-token", encoding="utf-8")
        self.assertEqual(instances.discover(), [])
        self.assertEqual(len(server.requests), 2)

    def test_token_precedence_and_fresh_file_reads(self):
        server = self.endpoint()
        self.assertEqual(instances.read_token(server.token_path), server.token)
        with mock.patch.dict(os.environ, {"XC_MCP_TOKEN": " Env-Token "}):
            self.assertEqual(instances.read_token(server.token_path), "Env-Token")
            self.assertEqual(bridge._read_token(), "Env-Token")
            self.assertEqual(xcbuild.read_token(), "Env-Token")
        target = instances.resolve()
        for token in ("new-first", "new-second"):
            server.token = token
            server.token_path.write_text(token, encoding="utf-8")
            self.assertEqual(json.loads(target.post({"id": 1, "method": "ping"}))["id"], 1)
        server.token_path.unlink()
        self.assertEqual(instances.read_token(server.token_path), "Legacy-Test-Token")
        self.legacy_token.write_text("new-legacy", encoding="utf-8")
        self.assertEqual(bridge._read_token(), "new-legacy")
        self.assertEqual(xcbuild.read_token(), "new-legacy")

    def test_401_rereads_and_retries_exactly_once(self):
        server = self.endpoint()
        target = instances.resolve()
        def rotate(method):
            if method == "POST":
                server.before_request = None
                server.token = "rotated-token"
                server.token_path.write_text(server.token, encoding="utf-8")
        server.before_request = rotate
        target.post({"id": 1, "method": "ping"})
        self.assertEqual(len(server.posts()), 2)
        with mock.patch.dict(os.environ, {"XC_MCP_TOKEN": "wrong-env"}):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                target.post({"id": 1, "method": "ping"})
        self.assertEqual(caught.exception.code, 401)
        self.assertEqual(len(server.posts()), 4)

    def test_409_is_not_retried(self):
        server = self.endpoint()
        target = instances.resolve()
        server.post_status = 409
        with self.assertRaises(urllib.error.HTTPError) as caught:
            target.post({"id": 1, "method": "ping"})
        self.assertEqual(caught.exception.code, 409)
        self.assertEqual(len(server.posts()), 1)

    def test_explicit_url_preserves_401_retry_limit_and_project_409(self):
        server = self.endpoint()
        with mock.patch.dict(os.environ, {"XC_MCP_TOKEN": "wrong"}):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                instances.resolve(url=server.url)
        self.assertEqual(caught.exception.code, 401)
        self.assertEqual(len(server.requests), 2)
        server.requests.clear()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            instances.resolve(url=server.url, project=self.other_project)
        self.assertEqual(caught.exception.code, 409)
        self.assertEqual(len(server.requests), 1)

    def test_environment_and_cli_values_intersect_without_silent_conflicts(self):
        first = self.endpoint()
        second = self.endpoint(self.other_project)
        with mock.patch.dict(os.environ, {"XC_MCP_INSTANCE": first.instance_id,
                                         "XC_MCP_PROJECT": self.project, "XC_MCP_URL": first.url}):
            self.assertEqual(instances.resolve().instance_id, first.instance_id)
            chosen = instances.resolve(instance=second.instance_id, project=self.other_project, url=second.url)
            self.assertEqual(chosen.instance_id, second.instance_id)
            with self.assertRaises(instances.IdentityError):
                instances.resolve(instance=second.instance_id)
        with self.assertRaises((instances.InstanceError, urllib.error.HTTPError)):
            instances.resolve(url=first.url, project=self.other_project)

    def test_explicit_url_without_registration_binds_new_identity(self):
        server = self.endpoint(register=False)
        with mock.patch.dict(os.environ, {"XC_MCP_URL": server.url, "XC_MCP_TOKEN": server.token}):
            target = instances.resolve()
            self.assertEqual(target.instance_id, server.instance_id)
            target.post({"id": 1, "method": "ping"})
        self.assertEqual(server.posts()[-1][2]["X-XC-Instance"], server.instance_id)

    def test_explicit_legacy_url_and_token_remain_compatible(self):
        server = self.endpoint(register=False, legacy=True)
        self.legacy_token.write_text(server.token, encoding="utf-8")
        with mock.patch.dict(os.environ, {"XC_MCP_URL": server.url}):
            target = instances.resolve()
            self.assertIsNone(target.instance_id)
            self.assertEqual(json.loads(target.post({"id": 8, "method": "ping"}))["id"], 8)
            with self.assertRaises(instances.IdentityError):
                instances.resolve(project=self.project)
        with self.assertRaises(instances.NoMatchError):
            instances.resolve()  # no implicit localhost:18766 fallback

    def test_localhost_ignores_proxies_and_redirects_are_never_followed(self):
        server = self.endpoint()
        trap = self.endpoint(self.other_project)
        with mock.patch.dict(os.environ, {"HTTP_PROXY": trap.url, "http_proxy": trap.url,
                                         "ALL_PROXY": trap.url, "NO_PROXY": ""}):
            target = instances.resolve(url=server.url.replace("127.0.0.1", "localhost"))
            target.post({"id": 1, "method": "ping"})
            server.redirect = trap.url
            with self.assertRaises(urllib.error.HTTPError) as caught:
                target.post({"id": 2, "method": "ping"})
        self.assertEqual(caught.exception.code, 302)
        self.assertEqual(trap.requests, [])

    def test_cli_list_and_resolve_json_never_output_token_contents(self):
        server = self.endpoint()
        server.info["token"] = server.token  # health extras must not be published
        listing = self.run_script("bridge/instances.py", ["list", "--json"])
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertEqual(json.loads(listing.stdout)[0]["instanceId"], server.instance_id)
        self.assertNotIn(server.token, listing.stdout + listing.stderr)
        resolved = self.run_script("bridge/instances.py", ["resolve", "--project", self.project, "--json"])
        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        self.assertEqual(json.loads(resolved.stdout)["pid"], server.info["pid"])
        self.assertNotIn(server.token, resolved.stdout + resolved.stderr)

    def test_two_targets_with_same_rpc_id_are_isolated_for_50_requests_each(self):
        first = self.endpoint()
        second = self.endpoint(self.other_project)
        left = instances.resolve(project=self.project)
        right = instances.resolve(project=self.other_project)
        def check(target, expected):
            for _ in range(50):
                response = json.loads(target.post({"jsonrpc": "2.0", "id": 7, "method": "ping"}))
                self.assertEqual(response["id"], 7)
                self.assertEqual(response["result"]["instanceId"], expected)
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(check, left, first.instance_id), pool.submit(check, right, second.instance_id)]
            for job in jobs:
                job.result(timeout=15)
        self.assertEqual(len(first.posts()), 50)
        self.assertEqual(len(second.posts()), 50)

    def test_fixed_binding_does_not_follow_project_to_another_instance(self):
        first = self.endpoint()
        second = self.endpoint(self.other_project)
        target = instances.resolve(project=self.project)
        first.switch_project(self.other_project)
        second.switch_project(self.project)
        for call in (target.health, lambda: target.post({"id": 1, "method": "ping"})):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                call()
            self.assertEqual(caught.exception.code, 409)
        self.assertEqual(second.posts(), [])
        self.assertEqual(target.instance_id, first.instance_id)

    def test_fixed_binding_does_not_rediscover_after_exit_or_port_reuse(self):
        first = self.endpoint()
        target = instances.resolve(project=self.project)
        second = self.endpoint()
        first.info["instanceId"] = "new-owner-of-the-port"
        with self.assertRaises(urllib.error.HTTPError) as caught:
            target.post({"id": 1, "method": "ping"})
        self.assertEqual(caught.exception.code, 409)
        first.stop()
        with self.assertRaises((OSError, http.client.HTTPException)):
            target.post({"id": 1, "method": "ping"}, timeout=0.1)
        self.assertEqual(second.posts(), [])

    def test_mutating_public_metadata_cannot_change_the_binding(self):
        server = self.endpoint()
        target = instances.resolve()
        public = target.info
        public["instanceId"] = "wrong"
        public["url"] = "http://remote.example/mcp"
        target.post({"id": 1, "method": "ping"})
        self.assertEqual(server.posts()[0][2]["X-XC-Instance"], server.instance_id)


class BridgeAndWaitTests(LocalFixture):
    def test_bridge_utf8_notifications_batches_and_scalars(self):
        server = self.endpoint()
        messages = [{"id": 1, "method": "ping", "params": {"text": "中文输入"}},
                    {"method": "notifications/initialized"},
                    [{"id": 2, "method": "ping"}, {"method": "notifications/initialized"}],
                    [{"method": "notifications/initialized"}], [], 123]
        result = self.run_script("bridge/stdio_bridge.py", ["--project", self.project],
                                 "\n".join(json.dumps(x, ensure_ascii=False) for x in messages) + "\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["result"]["echo"]["text"], "中文输入")
        self.assertEqual(rows[1][0]["id"], 2)
        self.assertEqual(len(rows[1]), 1)
        self.assertEqual(rows[2]["error"]["code"], -32600)
        self.assertEqual(rows[3]["id"], None)
        self.assertEqual(len(server.posts()), 6)

    def test_bridge_lazy_start_without_instances_keeps_error_shapes(self):
        messages = [[], 123, {"id": 1, "method": "ping"},
                    [{"id": 2, "method": "ping"}, {"method": "notifications/initialized"}]]
        result = self.run_script("bridge/stdio_bridge.py", requests="\n".join(map(json.dumps, messages)) + "\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[2]["id"], 1)
        self.assertEqual([item["id"] for item in rows[3]], [2])

    def test_stdio_binding_is_not_replaced_after_project_switch(self):
        first = self.endpoint()
        second = self.endpoint(self.other_project)
        def switch():
            first.switch_project(self.other_project)
            second.switch_project(self.project)
        first.after_post = switch
        text = json.dumps({"id": 3, "method": "ping"}) + "\n"
        result = self.run_script("bridge/stdio_bridge.py", ["--project", self.project], text * 2)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(rows[0]["result"]["instanceId"], first.instance_id)
        self.assertIn("409", rows[1]["error"]["message"])
        self.assertEqual(second.posts(), [])

    def test_wait_ready_does_not_accept_neighbour_project_or_id(self):
        neighbour = self.endpoint(self.other_project)
        for options in ({"project": self.project}, {"instance": "missing-instance"}):
            with self.subTest(options=options), self.assertRaises(TimeoutError):
                wait_ready.wait_for_target(0.04, poll_interval=0.01, **options)
        self.assertEqual(neighbour.posts(), [])

    def test_wait_ready_unique_success_and_ambiguity_is_immediate(self):
        first = self.endpoint()
        second = self.endpoint()
        with self.assertRaises(instances.AmbiguousMatchError):
            wait_ready.wait_for_target(1, project=self.project)
        target = wait_ready.wait_for_target(0.5, instance=first.instance_id, project=self.project)
        self.assertEqual(target.instance_id, first.instance_id)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(wait_ready.main(["0", "--instance", second.instance_id]), 0)
            self.assertEqual(wait_ready.main(["0", "--project", self.other_project]), 1)
            self.assertEqual(wait_ready.main(["0", "--project", self.project]), 2)

    def test_zero_wait_is_one_bounded_probe_without_retry(self):
        with mock.patch.object(time, "monotonic", return_value=100.0), \
                mock.patch.object(instances, "resolve", side_effect=instances.NoMatchError("offline")) as probe, \
                mock.patch.object(time, "sleep") as sleep:
            with self.assertRaisesRegex(TimeoutError, "single probe"):
                wait_ready.wait_for_target(0, project=self.project)
        probe.assert_called_once()
        self.assertEqual(probe.call_args.kwargs["deadline"], 102.0)
        self.assertEqual(probe.call_args.kwargs["timeout"], 2.0)
        sleep.assert_not_called()

    def test_xcbuild_isolated_stderr_is_utf8(self):
        missing = str(self.tmp / "不存在的工程.xcproj")
        result = self.run_script("xcbuild/xcbuild.py", ["build", "--project", missing, "--json"],
                                 env={"XC_IDE_ROOT": str(self.exe.parent)})
        self.assertEqual(result.returncode, 2)
        self.assertIn("错误", result.stderr)
        self.assertIn("不存在的工程", result.stderr)

    def test_wait_rejects_nonfinite_timeouts_and_wrong_explicit_target(self):
        for seconds in (float("nan"), float("inf"), -1):
            with self.subTest(seconds=seconds), self.assertRaises(instances.ConfigurationError):
                wait_ready.wait_for_target(seconds)
        server = self.endpoint(self.other_project)
        with self.assertRaises(instances.IdentityError):
            wait_ready.wait_for_target(0.5, url=server.url, project=self.project)

    def test_discovery_deadline_cannot_turn_a_partial_scan_into_unique_success(self):
        self.endpoint()
        self.endpoint()
        clock = [10.0]
        def delayed_health(target, **kwargs):
            clock[0] += 0.04
            return target.info
        with mock.patch.object(instances.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(instances.Target, "health", delayed_health):
            with self.assertRaises(TimeoutError):
                instances.resolve(deadline=10.05)

    def test_wait_ready_import_has_no_network_or_wait_side_effect(self):
        with mock.patch.object(instances, "resolve") as resolve, mock.patch.object(time, "sleep") as sleep:
            module = load_script("wait_import_only", ROOT / "wait_ready.py")
        resolve.assert_not_called()
        sleep.assert_not_called()
        self.assertTrue(callable(module.main))


def fake_target(info, project):
    return SimpleNamespace(info=dict(info), instance_id=info["instanceId"], url=info["url"],
                           project=instances.normalize_project(project),
                           health=mock.Mock(return_value=dict(info)), post=mock.Mock())


class ProcessTests(LocalFixture):
    def setUp(self):
        super().setUp()
        guard = mock.patch.object(xcbuild, "WindowsProcessApi", side_effect=AssertionError("real process API forbidden"))
        guard.start()
        self.addCleanup(guard.stop)
        self.info = {"pid": 21001, "startedAt": "01da000000005209", "ideExe": str(self.exe),
                     "hwnd": 71001, "instanceId": "21001-01da000000005209", "projectFile": self.project,
                     "url": "http://127.0.0.1:32345/mcp"}
        self.api = mock.Mock()
        self.api.open.side_effect = lambda pid: pid
        self.api.identity.side_effect = lambda handle: dict(self.info, pid=handle)
        self.api.running.return_value = True
        self.api.window_pid.return_value = self.info["pid"]
        self.api.wait_exit.return_value = True
        self.child = SimpleNamespace(pid=self.info["pid"], _handle=self.info["pid"], poll=mock.Mock(return_value=None))
        self.factory = mock.Mock(return_value=self.child)
        self.proc = xcbuild.IdeProcess(str(self.exe), str(self.exe.parent), api=self.api,
                                      popen_factory=self.factory)
        self.target = fake_target(self.info, self.project)

    def test_close_uses_exact_pid_handle_and_window_only(self):
        self.proc.close_target(self.target, timeout=3)
        self.api.open.assert_called_once_with(self.info["pid"])
        self.api.post_close.assert_called_once_with(self.info["hwnd"])
        self.api.wait_exit.assert_called_once_with(self.info["pid"], 3)
        self.api.close.assert_called_once_with(self.info["pid"])
        self.assertEqual(self.target.health.call_count, 2)
        self.factory.assert_not_called()

    def test_close_rejects_pid_reuse_executable_and_window_mismatches(self):
        for update in ({"pid": 22000}, {"startedAt": "01da000000000001"},
                       {"ideExe": str(self.tmp / "other-install" / "炫彩IDE.exe")}):
            with self.subTest(update=update):
                self.api.identity.side_effect = lambda handle, update=update: dict(self.info, **update)
                with self.assertRaises(instances.IdentityError):
                    self.proc.close_target(self.target)
        self.api.identity.side_effect = lambda handle: dict(self.info)
        for hwnd, owner in ((0, self.info["pid"]), (True, self.info["pid"]), (71001, 99999)):
            with self.subTest(hwnd=hwnd, owner=owner):
                self.target.health.return_value = dict(self.info, hwnd=hwnd)
                self.api.window_pid.return_value = owner
                with self.assertRaises(instances.IdentityError):
                    self.proc.close_target(self.target)
        self.api.post_close.assert_not_called()

    def test_close_rechecks_project_before_posting_close(self):
        self.target.health.side_effect = [dict(self.info), instances.IdentityError("project changed")]
        with self.assertRaises(instances.IdentityError):
            self.proc.close_target(self.target)
        self.api.post_close.assert_not_called()
        self.api.close.assert_called_once()

    def test_save_dialog_timeout_does_not_force_kill_or_spawn(self):
        self.api.wait_exit.return_value = False
        with mock.patch.object(instances, "resolve", return_value=self.target):
            with self.assertRaisesRegex(RuntimeError, "未强制终止"):
                self.proc.restart(self.project, mock.Mock(), settle=0)
        self.factory.assert_not_called()
        self.api.post_close.assert_called_once_with(self.info["hwnd"])
        self.assertFalse(hasattr(xcbuild.IdeProcess, "kill"))
        self.assertFalse(hasattr(xcbuild.IdeProcess, "find_pid"))

    def test_spawn_uses_port_zero_and_binds_only_owned_pid_creation_and_project(self):
        client = mock.Mock()
        with mock.patch.object(instances, "resolve", side_effect=instances.NoMatchError("none")), \
                mock.patch.object(instances, "discover", return_value=[self.target]) as discover, \
                mock.patch.object(xcbuild.time, "sleep"):
            self.proc.restart(self.project, client, settle=0)
        args, kwargs = self.factory.call_args
        self.assertEqual(args[0], [str(self.exe), self.project])
        self.assertEqual(kwargs["env"]["XC_MCP_PORT"], "0")
        self.assertEqual(kwargs["env"]["XC_MCP_INSTANCE_DIR"], str(self.registry))
        self.assertNotIn("XC_MCP_URL", kwargs["env"])
        self.assertEqual(discover.call_args.kwargs["pid"], self.child.pid)
        self.assertEqual(discover.call_args.kwargs["project"], instances.normalize_project(self.project))
        client.bind.assert_called_once_with(self.target)
        self.assertIs(self.proc.popen, self.child)
        self.proc.cleanup()
        self.api.post_close.assert_called_once_with(self.info["hwnd"])
        self.assertIsNone(self.proc.popen)

    def test_explicit_unmatched_id_and_ambiguity_do_not_launch(self):
        explicit = xcbuild.IdeProcess(str(self.exe), str(self.exe.parent), instance="missing-id",
                                     api=self.api, popen_factory=self.factory)
        with mock.patch.object(instances, "resolve", side_effect=instances.NoMatchError("none")):
            with self.assertRaises(instances.NoMatchError):
                explicit.restart(self.project, mock.Mock(), settle=0)
        with mock.patch.object(instances, "resolve", side_effect=instances.AmbiguousMatchError("two")):
            with self.assertRaises(instances.AmbiguousMatchError):
                self.proc.restart(self.project, mock.Mock(), settle=0)
        self.factory.assert_not_called()
        self.api.post_close.assert_not_called()

    def test_owned_readiness_rejects_other_pid_time_exe_or_project(self):
        self.proc.start(self.project)
        for update in ({"pid": 99999}, {"startedAt": "0000000000000001"},
                       {"ideExe": str(self.tmp / "other.exe")}, {"projectFile": self.other_project}):
            with self.subTest(update=update):
                wrong = fake_target(dict(self.info, **update), self.project)
                with mock.patch.object(instances, "discover", return_value=[wrong]):
                    with self.assertRaises(instances.IdentityError):
                        self.proc._owned_targets()
        self.api.post_close.assert_not_called()

    def test_redirected_or_exited_popen_is_never_a_successful_start(self):
        self.child.poll.return_value = 0
        with mock.patch.object(instances, "resolve", side_effect=instances.NoMatchError("none")), \
                mock.patch.object(instances, "discover", return_value=[self.target]) as discover:
            with self.assertRaisesRegex(RuntimeError, "转交其他进程"):
                self.proc.restart(self.project, mock.Mock(), settle=0)
        discover.assert_not_called()
        self.api.open.assert_not_called()
        self.api.post_close.assert_not_called()
        self.proc.cleanup()

    def test_bound_session_does_not_rediscover_on_target_exit(self):
        self.proc._attempted = True
        self.proc.project = instances.normalize_project(self.project)
        self.proc.target = self.target
        self.target.health.side_effect = OSError("target exited")
        with mock.patch.object(instances, "resolve") as resolve, self.assertRaises(OSError):
            self.proc.restart(self.project, mock.Mock(), settle=0)
        resolve.assert_not_called()
        self.factory.assert_not_called()
        self.api.post_close.assert_not_called()

    def test_owned_startup_keeps_first_binding_while_window_is_loading(self):
        self.target.info["hwnd"] = 0
        def window_ready(**kwargs):
            self.target.info["hwnd"] = self.info["hwnd"]
            return dict(self.target.info)
        self.target.health.side_effect = window_ready
        client = mock.Mock()
        with mock.patch.object(instances, "resolve", side_effect=instances.NoMatchError("none")), \
                mock.patch.object(instances, "discover", return_value=[self.target]) as discover, \
                mock.patch.object(xcbuild.time, "sleep"):
            self.proc.restart(self.project, client, settle=0)
        discover.assert_called_once()
        client.bind.assert_called_once_with(self.target)
        self.assertIs(self.proc.target, self.target)

    def test_popen_original_handle_identity_is_cross_checked(self):
        self.api.identity.side_effect = [dict(self.info), dict(self.info, startedAt="0000000000000001")]
        with self.assertRaises(instances.IdentityError):
            self.proc.start(self.project)
        self.assertIsNone(self.proc._owned_identity)
        with self.assertRaisesRegex(RuntimeError, "no verified"):
            self.proc.cleanup()
        self.api.post_close.assert_not_called()

    def test_watch_reuses_one_process_and_cleans_it_on_ctrl_c(self):
        args = argparse.Namespace(project=self.project, instance=None, url=None, instance_dir=None, interval=0)
        proc = mock.Mock()
        with mock.patch.object(xcbuild, "IdeProcess", return_value=proc) as constructor, \
                mock.patch.object(xcbuild, "scan_sources", side_effect=[{}, {"x.xc": 1}]), \
                mock.patch.object(xcbuild, "build", return_value={"ok": True, "totalSeconds": 1}) as build, \
                mock.patch.object(xcbuild.time, "sleep", side_effect=KeyboardInterrupt), redirect_stderr(io.StringIO()):
            self.assertEqual(xcbuild.cmd_watch(args), 0)
        constructor.assert_called_once()
        build.assert_called_once_with(self.project, process=proc)
        proc.cleanup.assert_called_once()

    def test_watch_transport_failure_stops_instead_of_leaking_new_children(self):
        args = argparse.Namespace(project=self.project, instance=None, url=None, instance_dir=None, interval=0)
        proc = mock.Mock()
        with mock.patch.object(xcbuild, "IdeProcess", return_value=proc), \
                mock.patch.object(xcbuild, "scan_sources", side_effect=[{}, {"x.xc": 1}]), \
                mock.patch.object(xcbuild, "build", side_effect=OSError("target exited")) as build, \
                redirect_stderr(io.StringIO()):
            self.assertEqual(xcbuild.cmd_watch(args), 2)
        self.assertEqual(build.call_count, 1)
        proc.cleanup.assert_called_once()

    def test_one_shot_build_cleans_own_child_on_success_and_failure(self):
        for error in (None, OSError("disconnected")):
            with self.subTest(error=error):
                proc = mock.Mock()
                with mock.patch.object(xcbuild, "IdeProcess", return_value=proc), \
                        mock.patch.object(xcbuild, "_build_once", return_value={"ok": True}, side_effect=error):
                    if error:
                        with self.assertRaises(OSError):
                            xcbuild.build(self.project)
                    else:
                        self.assertEqual(xcbuild.build(self.project), {"ok": True})
                proc.cleanup.assert_called_once()

    def test_doctor_is_read_only_and_never_uses_process_management(self):
        server = self.endpoint()
        args = argparse.Namespace(project=self.project, instance=server.instance_id, url=None,
                                  instance_dir=str(self.registry))
        with mock.patch.object(xcbuild, "IdeProcess") as process, redirect_stdout(io.StringIO()):
            xcbuild.cmd_doctor(args)
        process.assert_not_called()
        self.assertTrue(server.posts())
        for _, _, _, raw in server.posts():
            self.assertEqual(json.loads(raw)["params"]["name"], "xc_status")

    def test_every_command_accepts_all_selectors(self):
        for command in ("build", "run", "watch", "doctor"):
            with self.subTest(command=command), \
                    mock.patch.object(xcbuild, "cmd_" + command, return_value=0) as handler, \
                    redirect_stdout(io.StringIO()):
                result = xcbuild.main([command, "--project", self.project, "--instance", "selected-id",
                                       "--url", self.info["url"], "--instance-dir", str(self.registry)])
                self.assertEqual(result, 0)
                args = handler.call_args.args[0]
                self.assertEqual(args.instance, "selected-id")
                self.assertEqual(args.project, self.project)
                self.assertEqual(args.url, self.info["url"])
                self.assertEqual(args.instance_dir, str(self.registry))

    def test_find_project_refuses_multiple_project_files(self):
        with self.assertRaisesRegex(RuntimeError, "多个"):
            xcbuild.find_project(str(self.tmp))


if __name__ == "__main__":
    unittest.main(verbosity=2)
