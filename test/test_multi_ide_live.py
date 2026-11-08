# -*- coding: utf-8 -*-
"""Explicit live acceptance test against two already opened, isolated IDE projects.

python -I -B test/test_multi_ide_live.py --fixture PATH/fixture.json --baseline PATH/tools.json --build
The fixture's .xcmcp-test marker and project containment checks are mandatory.
No IDE is started here except the precisely selected xcbuild child with --build.
"""
import argparse
import concurrent.futures
import hashlib
import http.client
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "bridge"))
import instances


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rpc(target, method, params=None, rid=1):
    request = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        request["params"] = params
    response = json.loads(target.post(request, timeout=30))
    assert response.get("id") == rid, response
    assert "error" not in response, response
    return response["result"]


def tool(target, name, **arguments):
    result = rpc(target, "tools/call", {"name": name, "arguments": arguments})
    assert not result.get("isError"), result
    value = json.loads(result["content"][0]["text"])
    assert value.get("ok", True), value
    return value


class Bridge:
    def __init__(self, target, directory, root, label, env):
        self.log = (root / (label + ".stderr.log")).open("w", encoding="utf-8")
        self.proc = subprocess.Popen([sys.executable, "-I", "-B", str(ROOT / "bridge" / "stdio_bridge.py"),
                                      "--project", target.info["projectFile"], "--instance-dir", directory],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                                     env=env, text=True, encoding="utf-8")
        self.rows = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            for line in self.proc.stdout:
                self.rows.put(line)
        finally:
            self.rows.put(None)

    def request(self, method, params=None, rid=1):
        request = {"jsonrpc": "2.0", "method": method}
        if rid is not None:
            request["id"] = rid
        if params is not None:
            request["params"] = params
        self.proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        if rid is None:
            return None
        row = self.rows.get(timeout=35)
        assert row is not None, "stdio bridge exited without a response"
        response = json.loads(row)
        assert response["id"] == rid, response
        return response

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)
        self.reader.join(timeout=2)
        self.proc.stdout.close()
        self.log.close()
        assert self.proc.returncode == 0


class Acceptance:
    def __init__(self, args):
        self.args = args
        self.fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
        self.root = Path(self.fixture["root"]).resolve()
        assert self.root == Path(args.fixture).resolve().parent
        assert (self.root / ".xcmcp-test").is_file(), "isolated fixture marker required"
        for name in ("A", "B"):
            path = Path(self.fixture["projects"][name]).resolve()
            assert path.is_relative_to(self.root), "refusing to test a business project outside the fixture"
            assert path.is_file()
        self.directory = self.fixture["instanceDir"]
        assert Path(self.directory).resolve().is_relative_to(self.root)
        self.report = {"checks": [], "fixture": self.fixture, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.env = dict(os.environ, XC_MCP_INSTANCE_DIR=self.directory, XC_IDE_ROOT=self.fixture["ideA"],
                        PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        for key in ("XC_MCP_URL", "XC_MCP_INSTANCE", "XC_MCP_PROJECT", "XC_MCP_TOKEN"):
            self.env.pop(key, None)
            os.environ.pop(key, None)
        self.targets = {name: instances.resolve(project=self.fixture["projects"][name],
                                               instance_dir=self.directory, use_env=False)
                        for name in ("A", "B")}
        self.initial = {name: target.health() for name, target in self.targets.items()}
        self.report["initial"] = self.initial

    def check(self, name, condition=True, detail=None):
        assert condition, name + ": " + str(detail)
        self.report["checks"].append({"name": name, "passed": True, "detail": detail})
        self.save()
        print("PASS " + name + (": " + str(detail) if detail is not None else ""), flush=True)

    def save(self):
        (self.root / "verification-live.json").write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")

    def raw(self, target, method="GET", path="/health", body=None, headers=None, conn=None):
        own = conn is None
        conn = conn or http.client.HTTPConnection("127.0.0.1", target.info["port"], timeout=10)
        # Token auth is disabled: do not send X-XC-Token in live acceptance.
        h = {"X-XC-Instance": target.instance_id, "X-XC-Project": quote(target.project, safe=""),
             "Content-Type": "application/json"}
        for key, value in (headers or {}).items():
            if value is None:
                h.pop(key, None)
            else:
                h[key] = value
        try:
            conn.request(method, path, body, h)
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            if own:
                conn.close()

    def protocols(self):
        a, b = self.targets.values()
        self.check("distinct_real_processes_and_ports", a.info["pid"] != b.info["pid"] and a.info["port"] != b.info["port"],
                   {key: {k: v[k] for k in ("pid", "instanceId", "port", "projectFile", "hwnd")} for key, v in self.initial.items()})
        baseline = json.loads(Path(self.args.baseline).read_text(encoding="utf-8")) if self.args.baseline else None
        schemas = []
        for name, target in self.targets.items():
            init = rpc(target, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                               "clientInfo": {"name": "multi-ide-live-" + name, "version": "1"}})
            assert init["serverInfo"]["name"] == "xcide-mcp"
            assert init["protocolVersion"] == "2024-11-05"
            assert target.post({"jsonrpc": "2.0", "method": "notifications/initialized"}) == ""
            listing = rpc(target, "tools/list")["tools"]
            assert len(listing) == 30
            schema = {t["name"]: t["inputSchema"] for t in listing}
            schemas.append(schema)
            if baseline is not None:
                legacy = {t["name"]: t["inputSchema"] for t in baseline}
                assert all(schema.get(name) == params for name, params in legacy.items())
                assert (set(schema) - set(legacy)) <= {"xc_get_dialogs"}
            status = tool(target, "xc_status")
            assert status["toolCount"] == 30
            assert status["pid"] == target.info["pid"]
            assert status["port"] == target.info["port"]
            assert status["instanceId"] == target.instance_id
            project = tool(target, "xc_get_project")
            assert instances.normalize_project(project["project_file"]) == target.project
            main = Path(self.fixture["projects"][name]).parent / "main.xc"
            read = tool(target, "xc_read_file", path=str(main))
            assert read["bom"] and read["crlf"] > 0
            assert "MCP_ISOLATED_" + name in read["content"]
            for token in (None, "incorrect-token"):
                assert self.raw(target, headers={"X-XC-Token": token})[0] == 200
            for origin in ("", "http://untrusted.invalid"):
                assert self.raw(target, headers={"Origin": origin})[0] == 403
            assert self.raw(target, headers={"Host": "untrusted.invalid"})[0] == 403
            assert self.raw(target, headers={"X-XC-Instance": "wrong-instance"})[0] == 409
            other = self.targets["B" if name == "A" else "A"]
            assert self.raw(target, headers={"X-XC-Project": quote(other.project, safe="")})[0] == 409
        assert schemas[0] == schemas[1]
        self.check("protocol_30_tools_parameters_and_file_reads", detail="initialize / notification / 30 schemas / status / project / BOM+CRLF")
        self.check("token_optional_and_target_guards_on_both_instances",
                   detail="200 without/with bad token; 403 Host/Origin; 409 wrong instance/project")
        found = instances.discover(self.directory)
        assert {t.instance_id for t in found} == {a.instance_id, b.instance_id}
        for target in (a, b):
            assert instances.resolve(instance=target.instance_id, instance_dir=self.directory, use_env=False).instance_id == target.instance_id
        try:
            instances.resolve(instance_dir=self.directory, use_env=False)
            raise AssertionError("ambiguous automatic selection unexpectedly succeeded")
        except instances.AmbiguousMatchError:
            pass
        self.check("real_discovery_id_project_selection_and_ambiguity")

    def http_parallel(self):
        barrier = threading.Barrier(2)
        def client(target):
            conn = http.client.HTTPConnection("127.0.0.1", target.info["port"], timeout=20)
            try:
                barrier.wait(timeout=10)
                original = None
                for i in range(50):
                    name = "xc_status" if i % 2 == 0 else "xc_get_project"
                    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name}}
                    code, raw = self.raw(target, "POST", "/mcp", json.dumps(request), conn=conn)
                    assert code == 200, (code, raw)
                    envelope = json.loads(raw)
                    assert envelope["id"] == 1 and not envelope["result"].get("isError")
                    value = json.loads(envelope["result"]["content"][0]["text"])
                    if name == "xc_status":
                        assert value["pid"] == target.info["pid"] and value["instanceId"] == target.instance_id
                    else:
                        assert instances.normalize_project(value["project_file"]) == target.project
                    if original is None:
                        original = conn.sock
                    assert conn.sock is original and original is not None
            finally:
                conn.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(client, self.targets.values()))
        self.check("http_parallel_50_each_identical_rpc_id_keep_alive", detail=100)

    def stdio_parallel(self):
        barrier = threading.Barrier(2)
        def client(pair):
            name, target = pair
            bridge = Bridge(target, self.directory, self.root, "stdio-" + name, self.env)
            try:
                barrier.wait(timeout=10)
                init = bridge.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}}, rid=99)
                assert init["result"]["serverInfo"]["name"] == "xcide-mcp"
                bridge.request("notifications/initialized", rid=None)
                for i in range(50):
                    name = "xc_status" if i % 2 == 0 else "xc_get_project"
                    response = bridge.request("tools/call", {"name": name}, rid=1)
                    assert "error" not in response and not response["result"].get("isError"), response
                    value = json.loads(response["result"]["content"][0]["text"])
                    if name == "xc_status":
                        assert value["pid"] == target.info["pid"] and value["instanceId"] == target.instance_id
                    else:
                        assert instances.normalize_project(value["project_file"]) == target.project
            finally:
                bridge.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(client, self.targets.items()))
        self.check("stdio_parallel_50_each_identical_rpc_id", detail=100)

    def helpers_and_write(self):
        for name, target in self.targets.items():
            result = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "wait_ready.py"), "2", "--instance", target.instance_id,
                                     "--project", target.info["projectFile"], "--instance-dir", self.directory],
                                    env=self.env, capture_output=True, encoding="utf-8", timeout=10)
            assert result.returncode == 0, result.stderr
        missing = str(self.root / "not-open.xcproj")
        result = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "wait_ready.py"), "0.5", "--project", missing,
                                 "--instance-dir", self.directory], env=self.env, capture_output=True, encoding="utf-8", timeout=10)
        assert result.returncode == 1, (result.stdout, result.stderr)
        self.check("wait_ready_verifies_target_not_its_neighbour")
        main_a = Path(self.fixture["projects"]["A"]).parent / "main.xc"
        main_b = Path(self.fixture["projects"]["B"]).parent / "main.xc"
        before_b = sha(main_b)
        content = tool(self.targets["A"], "xc_read_file", path=str(main_a))["content"]
        marker = "// MCP_A_EDIT_MARKER\r\n"
        if marker not in content:
            tool(self.targets["A"], "xc_editor_set", path=str(main_a), text=content + marker)
        assert marker.encode() in main_a.read_bytes()
        assert main_a.read_bytes().startswith(b"\xef\xbb\xbf")
        assert sha(main_b) == before_b
        self.report["bSourceSha256"] = before_b
        self.check("edit_A_preserves_B_source_and_A_encoding")

    def build_a(self):
        a, b = self.targets["A"], self.targets["B"]
        main_b = Path(self.fixture["projects"]["B"]).parent / "main.xc"
        original_b = sha(main_b)
        pinned = Bridge(a, self.directory, self.root, "pinned-A", self.env)
        try:
            response = pinned.request("tools/call", {"name": "xc_status"})
            assert json.loads(response["result"]["content"][0]["text"])["pid"] == a.info["pid"]
            command = [sys.executable, "-I", "-B", str(ROOT / "xcbuild" / "xcbuild.py"), "build",
                       "--project", a.info["projectFile"], "--instance", a.instance_id,
                       "--instance-dir", self.directory, "--json", "-v"]
            heartbeats = 0
            with (self.root / "xcbuild.stdout.json").open("w", encoding="utf-8") as output, \
                    (self.root / "xcbuild.stderr.log").open("w", encoding="utf-8") as error:
                process = subprocess.Popen(command, env=self.env, stdout=output, stderr=error)
                deadline = time.monotonic() + 240
                while process.poll() is None:
                    assert time.monotonic() < deadline, "xcbuild timed out; inspect its exact owned PID before cleanup"
                    value = tool(b, "xc_status")
                    assert value["pid"] == self.initial["B"]["pid"] and value["instanceId"] == b.instance_id
                    assert instances.normalize_project(tool(b, "xc_get_project")["project_file"]) == b.project
                    assert sha(main_b) == original_b
                    heartbeats += 1
                    time.sleep(0.2)
                code = process.returncode
            stdout = (self.root / "xcbuild.stdout.json").read_text(encoding="utf-8")
            stderr = (self.root / "xcbuild.stderr.log").read_text(encoding="utf-8")
            assert code == 0, (code, stdout, stderr)
            result = json.loads(stdout)
            assert result["ok"] and not result["skipped"] and result["errorCount"] == 0, result
            assert instances.normalize_project(result["projectFile"]) == a.project
            assert result["pid"] not in (a.info["pid"], b.info["pid"])
            assert result["instanceId"] not in (a.instance_id, b.instance_id)
            assert b.health()["pid"] == self.initial["B"]["pid"] and sha(main_b) == original_b
            assert heartbeats > 0
            self.report["compile"] = result
            self.check("xcbuild_restarts_only_A_and_compiles_successfully", detail=result)
            self.check("B_unchanged_and_responsive_during_A_compile", detail={"heartbeats": heartbeats, "sourceSha256": original_b})
            rejected = pinned.request("tools/call", {"name": "xc_status"})
            assert "error" in rejected, rejected
            self.check("already_bound_stdio_A_does_not_retarget_after_exit")
            remaining = instances.discover(self.directory)
            assert {t.instance_id for t in remaining} == {b.instance_id}
            self.check("xcbuild_child_cleaned_and_stale_A_excluded")
        finally:
            pinned.close()

    def run(self):
        try:
            self.protocols()
            self.http_parallel()
            self.stdio_parallel()
            self.helpers_and_write()
            if self.args.build:
                self.build_a()
            self.report["success"] = True
        except BaseException as error:
            self.report["success"] = False
            self.report["error"] = repr(error)
            raise
        finally:
            self.report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self.save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--baseline", help="pre-upgrade tools/list array to compare names and inputSchema")
    parser.add_argument("--build", action="store_true", help="explicitly permit targeted restart/build of isolated fixture A")
    Acceptance(parser.parse_args()).run()
