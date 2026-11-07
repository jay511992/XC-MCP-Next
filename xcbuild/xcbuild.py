#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""xcbuild — 无界面编译炫语言工程（后台隐藏 IDE 充当编译器）

原理：炫彩IDE 的 .xc -> .cpp 转译器内置于 炫彩IDE.exe（无 CLI、无导出），
但 IDE 的「跳过无变化编译」判断依据是进程内「上次编译快照」标志，
启动后首次 F7 必定真实编译。因此每次编译前重启 IDE（隐藏窗口）即可稳定编译。

用法：
    xcbuild build   [--project X.xcproj] [--json] [-v]
    xcbuild watch   [--project X.xcproj] [--interval 1.0]
    xcbuild run     [--project X.xcproj] [--json]      编译 + 调试运行
    xcbuild doctor                                     自检环境
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "bridge")))
import instances


def _find_ide_root():
    env = os.environ.get("XC_IDE_ROOT")
    if env and os.path.isfile(os.path.join(env, "炫彩IDE.exe")):
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    # <IDE 根>\xc-plugin\XC-MCP-Next\xcbuild\xcbuild.py
    cand = os.path.abspath(os.path.join(here, "..", "..", ".."))
    if os.path.isfile(os.path.join(cand, "炫彩IDE.exe")):
        return cand
    for c in (r"I:\炫彩ide", r"I:\xcide"):
        if os.path.isfile(os.path.join(c, "炫彩IDE.exe")):
            return c
    return cand


def read_token():
    """Compatible no-argument API: environment first, token file read afresh."""
    return instances.read_token()


IDE_ROOT = _find_ide_root()
IDE_EXE = os.path.join(IDE_ROOT, "炫彩IDE.exe")
HEALTH_TIMEOUT = 90
SETTLE = 9.0
BUILD_TIMEOUT = 180


def log(msg):
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


class IdeClient(object):
    def __init__(self, base=None, *, target=None, **selectors):
        self._target = target
        self._selectors = dict(selectors)
        if base is not None:
            self._selectors["url"] = base
        self._id = 0

    @property
    def target(self):
        if self._target is None:
            self._target = instances.resolve(**self._selectors)
        return self._target

    def bind(self, target):
        """Only an explicit, verified process restart may replace the binding."""
        self._target = target

    def _post(self, payload, timeout):
        return self.target.post(payload, timeout=timeout)

    def call(self, name, args=None, timeout=BUILD_TIMEOUT):
        self._id += 1
        payload = {
            "jsonrpc": "2.0", "id": self._id, "method": "tools/call",
            "params": {"name": name, "arguments": args or {}},
        }
        raw = self._post(payload, timeout)
        try:
            env = json.loads(raw)
            txt = env["result"]["content"][0]["text"]
            return json.loads(txt)
        except Exception:
            return {"_raw": raw[:400]}

    def health(self, timeout=2):
        try:
            self.target.health(timeout=timeout)
            return True
        except Exception:
            return False

    def output(self, tail=200):
        d = self.call("xc_get_output", {"tailLines": tail}, timeout=30)
        return d.get("text", "")


class WindowsProcessApi:
    """Minimal Win32 API: query/wait handles and a PID-verified WM_CLOSE only."""
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("IDE process management requires Windows")
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.w = ctypes, wintypes
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        signatures = (
            (self.k.OpenProcess, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            (self.k.CloseHandle, [wintypes.HANDLE], wintypes.BOOL),
            (self.k.GetProcessId, [wintypes.HANDLE], wintypes.DWORD),
            (self.k.GetProcessTimes, [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4, wintypes.BOOL),
            (self.k.QueryFullProcessImageNameW, [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            (self.k.WaitForSingleObject, [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            (self.u.GetWindowThreadProcessId, [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            (self.u.PostMessageW, [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
        )
        for function, arguments, result in signatures:
            function.argtypes, function.restype = arguments, result

    def _error(self):
        return self.ctypes.WinError(self.ctypes.get_last_error())

    def open(self, pid):
        if type(pid) is not int or not 0 < pid <= 0xffffffff:
            raise instances.IdentityError("invalid process PID")
        handle = self.k.OpenProcess(0x1000 | 0x00100000, False, pid)
        if not handle:
            raise self._error()
        return handle

    def close(self, handle):
        self.k.CloseHandle(handle)

    def identity(self, handle):
        c, w = self.ctypes, self.w
        times = [w.FILETIME() for _ in range(4)]
        if not self.k.GetProcessTimes(handle, *(c.byref(t) for t in times)):
            raise self._error()
        size = w.DWORD(32768)
        image = c.create_unicode_buffer(size.value)
        if not self.k.QueryFullProcessImageNameW(handle, 0, image, c.byref(size)):
            raise self._error()
        pid = self.k.GetProcessId(handle)
        if not pid:
            raise self._error()
        return {"pid": int(pid), "startedAt": "%016x" %
                ((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime), "ideExe": image.value}

    def wait_exit(self, handle, timeout):
        result = self.k.WaitForSingleObject(handle, min(0xfffffffe, max(0, int(timeout * 1000))))
        if result == 0:
            return True
        if result == 258:
            return False
        raise self._error()

    def running(self, handle):
        return not self.wait_exit(handle, 0)

    def window_pid(self, hwnd):
        pid = self.w.DWORD()
        if not self.u.GetWindowThreadProcessId(hwnd, self.ctypes.byref(pid)):
            raise self._error()
        return int(pid.value)

    def post_close(self, hwnd):
        if not self.u.PostMessageW(hwnd, 0x0010, 0, 0):  # WM_CLOSE, never a desktop-wide shortcut
            raise self._error()


def validate_process_identity(info, observed, expected_exe):
    """Pure identity check, shared by existing-target close and owned-PID startup."""
    if type(info.get("pid")) is not int or info["pid"] <= 0 or info["pid"] != observed.get("pid"):
        raise instances.IdentityError("PID does not match the verified process handle")
    stamp = info.get("startedAt", "")
    if (not isinstance(stamp, str) or not re.fullmatch(r"[0-9a-fA-F]{16}", stamp)
            or stamp.lower() != str(observed.get("startedAt", "")).lower()):
        raise instances.IdentityError("process creation time changed (possible PID reuse)")
    expected = instances.normalize_project(expected_exe)
    if (instances.normalize_project(info.get("ideExe", "")) != expected
            or instances.normalize_project(observed.get("ideExe", "")) != expected):
        raise instances.IdentityError("IDE executable differs from the selected installation; refusing to close it")


class IdeProcess:
    """One selected IDE plus at most one child created by this command.

    No process-name lookup and no forced termination are used. Existing IDEs
    must close gracefully; a save dialog/timeout prevents launching a replacement.
    """
    def __init__(self, exe=IDE_EXE, root=IDE_ROOT, *, instance=None, url=None,
                 instance_dir=None, api=None, popen_factory=None):
        self.exe, self.root = os.path.abspath(exe), os.path.abspath(root)
        self.options = instances.selection_options(project="", instance=instance, url=url,
                                                   instance_dir=instance_dir)
        self._api = api
        self._popen_factory = popen_factory
        self.target = None
        self.popen = None
        self._owned_handle = None
        self._owned_identity = None
        self._attempted = False
        self.project = None

    @property
    def api(self):
        if self._api is None:
            self._api = WindowsProcessApi()
        return self._api

    def _verify_window(self, info, handle):
        validate_process_identity(info, self.api.identity(handle), self.exe)
        if not self.api.running(handle):
            raise instances.IdentityError("selected IDE has exited")
        hwnd = info.get("hwnd")
        if type(hwnd) is not int or hwnd <= 0 or self.api.window_pid(hwnd) != info["pid"]:
            raise instances.IdentityError("main window is missing or belongs to a different PID")

    def close_target(self, target, timeout=30):
        info = target.health(timeout=2)
        if not target.instance_id:
            raise instances.IdentityError("legacy URL has no process identity; refusing to restart it")
        handle = self.api.open(info["pid"])
        try:
            self._verify_window(info, handle)
            # Recheck current project immediately before a potentially destructive close.
            info = target.health(timeout=2)
            self._verify_window(info, handle)
            self.api.post_close(info["hwnd"])
            if not self.api.wait_exit(handle, timeout):
                raise RuntimeError("IDE PID %d 未关闭（可能有未保存编辑/保存对话框）；未强制终止，请手动处理" % info["pid"])
        finally:
            self.api.close(handle)

    def _check_child(self):
        if self.popen is None or self.popen.poll() is not None:
            raise RuntimeError("自启 IDE PID %s 已退出或将工程转交其他进程；不会冒认其他实例" %
                               (self.popen.pid if self.popen else "?"))
        if self._owned_handle is not None and not self.api.running(self._owned_handle):
            raise instances.IdentityError("owned IDE process handle has exited")

    def start(self, project):
        if self.popen is not None or self._owned_handle is not None:
            raise RuntimeError("previous owned IDE has not been safely cleaned up")
        if os.name != "nt" and self._popen_factory is None:
            raise RuntimeError("IDE process management requires Windows")
        env = dict(os.environ, XC_MCP_PORT="0", XC_MCP_INSTANCE_DIR=self.options["instance_dir"])
        for name in ("XC_MCP_URL", "XC_MCP_INSTANCE", "XC_MCP_PROJECT"):
            env.pop(name, None)
        kwargs = {"cwd": self.root, "stdout": subprocess.DEVNULL,
                  "stderr": subprocess.DEVNULL, "env": env}
        if os.name == "nt":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            kwargs["startupinfo"] = si
        self.popen = (self._popen_factory or subprocess.Popen)([self.exe, project], **kwargs)
        self.project = instances.normalize_project(project)
        self._check_child()
        self._owned_handle = self.api.open(self.popen.pid)
        observed = self.api.identity(self._owned_handle)
        # Popen's original Windows handle pins the exact process it created.
        original = getattr(self.popen, "_handle", self._owned_handle)
        validate_process_identity(observed, self.api.identity(original), self.exe)
        if observed["pid"] != self.popen.pid:
            raise instances.IdentityError("Popen PID and process handle disagree")
        self._check_child()
        # Failed verification must never grant cleanup authority over this PID.
        self._owned_identity = observed

    def _owned_targets(self, timeout=1, deadline=None):
        targets = instances.discover(self.options["instance_dir"], timeout=timeout,
                                     project=self.project, pid=self.popen.pid, deadline=deadline)
        for target in targets:
            validate_process_identity(target.info, self._owned_identity, self.exe)
            if not target.project or instances.normalize_project(target.info["projectFile"]) != self.project:
                raise instances.IdentityError("owned IDE has not opened the requested project")
        if len(targets) > 1:
            raise instances.AmbiguousMatchError("multiple registrations claim the owned PID")
        return targets

    def restart(self, project, client, settle=SETTLE):
        project = os.path.abspath(project)
        normalized = instances.normalize_project(project)
        if self.project and self.project != normalized:
            raise instances.IdentityError("cannot change a bound build session's project")
        if not os.path.isfile(self.exe):
            raise RuntimeError("找不到 IDE: %s" % self.exe)
        if self._attempted and self.target is None:
            raise RuntimeError("previous binding/startup failed; reconnect explicitly instead of rediscovering")
        if not self._attempted:
            self._attempted = True
            options = dict(self.options, project=project)
            try:
                self.target = instances.resolve(**options, use_env=False)
            except instances.NoMatchError:
                if options["instance"] or options["url"]:
                    raise  # an explicit ID/URL cannot silently become a new process
        self.project = normalized
        if self.target is not None:
            self.close_target(self.target)
            self.detach()
            self.target = None
        started = time.monotonic()
        self.start(project)
        deadline = started + HEALTH_TIMEOUT
        while time.monotonic() < deadline:
            self._check_child()
            probe_timeout = min(1, max(0.01, deadline - time.monotonic()))
            if self.target is None:
                targets = self._owned_targets(timeout=probe_timeout, deadline=deadline)
                if targets:
                    self.target = targets[0]
            else:
                self.target.health(timeout=probe_timeout, deadline=deadline)
            self._check_child()
            if self.target is not None and self.target.info["hwnd"]:
                self._verify_window(self.target.info, self._owned_handle)
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("自启 IDE PID %d 未在 %ds 内打开目标工程并登记就绪" %
                               (self.popen.pid, HEALTH_TIMEOUT))
        boot = time.monotonic() - started
        time.sleep(settle)
        self._check_child()
        self.target.health(timeout=2)
        client.bind(self.target)  # intentional replacement by the exact new child only
        return boot

    def detach(self):
        """Release handles without closing an IDE intentionally retained by `run`."""
        if self._owned_handle is not None:
            self.api.close(self._owned_handle)
        self._owned_handle = None
        self._owned_identity = None
        self.popen = None

    def cleanup(self):
        """Gracefully close only our own child; never another discovered process."""
        if self.popen is None:
            return
        if self.popen.poll() is not None:
            self.detach()
            return
        pid = self.popen.pid
        try:
            if self._owned_identity is None:
                raise RuntimeError("no verified creation-time/executable identity")
            if self.target is None:
                targets = self._owned_targets()
                if not targets:
                    raise RuntimeError("no authenticated window for the owned PID/project")
                self.target = targets[0]
            validate_process_identity(self.target.info, self._owned_identity, self.exe)
            self.close_target(self.target)
            self.detach()
            self.target = None
        except Exception as exc:
            raise RuntimeError("无法安全清理自启 IDE PID %d（未强杀/未操作其他进程）: %s" % (pid, exc)) from exc


# ---------------------------------------------------------------- 错误解析

RE_PARSE_ERR = re.compile(r"🐛解析错误:\s*(.+?)\((\d+)\),\s*列\((\d+)-(\d+)\)")
RE_CHECK_ERR = re.compile(r"🐛检查错误:\s*(.+?)\((\d+)\),\s*列\((\d+)-(\d+)\)")
RE_CPP_DIAG = re.compile(r"^(.+?)\((\d+)\):\s*(错误|警告)\s+(C\d+):\s*(.*)$")
RE_CPP_HINT = re.compile(r"^(.+?)\((\d+)\):\s*提示:\s*(.*)$")
RE_FAIL = re.compile(r"🐞\*编译失败,\s*错误码:\s*(-?\d+)")
RE_TOTAL = re.compile(r"错误列表:\s*共有\((\d+)\)个错误")
RE_ZERO = re.compile(r"错误列表:\s*错误\(0\)")
RE_SKIP = re.compile(r"跳过编译")
RE_OK = re.compile(r"编译完成,\s*耗时")


def parse_output(text):
    """把 IDE 输出解析成结构化结果。"""
    lines = text.splitlines()
    errors = []
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        m = RE_PARSE_ERR.search(ln)
        if m:
            reason = lines[i + 1].strip() if i + 1 < n else ""
            errors.append({
                "kind": "parse", "file": m.group(1), "line": int(m.group(2)),
                "col1": int(m.group(3)), "col2": int(m.group(4)),
                "message": reason,
            })
            i += 2
            continue
        m = RE_CHECK_ERR.search(ln)
        if m:
            reason = lines[i + 1].strip() if i + 1 < n else ""
            errors.append({
                "kind": "check", "file": m.group(1), "line": int(m.group(2)),
                "col1": int(m.group(3)), "col2": int(m.group(4)),
                "message": reason,
            })
            i += 2
            continue
        m = RE_CPP_DIAG.match(ln.strip())
        if m:
            sev = "error" if m.group(3) == "错误" else "warning"
            errors.append({
                "kind": "cpp", "severity": sev, "file": m.group(1),
                "line": int(m.group(2)), "code": m.group(4),
                "message": m.group(5),
            })
            i += 1
            continue
        m = RE_CPP_HINT.match(ln.strip())
        if m:
            if errors and errors[-1]["kind"] == "cpp":
                errors[-1].setdefault("hints", []).append(
                    "%s(%s): %s" % (m.group(1), m.group(2), m.group(3)))
            i += 1
            continue
        i += 1

    fail_code = None
    m = RE_FAIL.search(text)
    if m:
        fail_code = int(m.group(1))

    total = 0
    m = RE_TOTAL.search(text)
    if m:
        total = int(m.group(1))

    skipped = bool(RE_SKIP.search(text))
    compiled = bool(RE_OK.search(text))

    err_cnt = sum(1 for e in errors
                  if e["kind"] != "cpp" or e.get("severity") == "error")
    ok = compiled and (not skipped) and fail_code is None and err_cnt == 0 and total == 0

    return {
        "ok": ok,
        "skipped": skipped,
        "compiled": compiled,
        "failCode": fail_code,
        "reportedTotal": total,
        "errorCount": err_cnt,
        "errors": errors,
    }


def fmt_errors(res, only_errors=True):
    out = []
    for e in res["errors"]:
        if only_errors and e.get("severity") == "warning":
            continue
        loc = "%s(%d)" % (e["file"], e["line"])
        if e["kind"] in ("parse", "check"):
            tag = "解析" if e["kind"] == "parse" else "检查"
            c1, c2 = e.get("col1") or 0, e.get("col2") or 0
            span = " [%s 列%d-%d]" % (tag, c1, c2) if (c1 or c2) else " [%s]" % tag
            out.append("%s:%s %s" % (loc, span, e["message"]))
        else:
            out.append("%s: [%s] %s" % (loc, e.get("code", ""), e["message"]))
            for h in e.get("hints", []):
                out.append("    " + h)
    return out


# ---------------------------------------------------------------- 构建编排

def find_project(start=None):
    cur = os.path.abspath(start or os.getcwd())
    while True:
        try:
            projects = sorted(f for f in os.listdir(cur) if f.lower().endswith(".xcproj")
                              and os.path.isfile(os.path.join(cur, f)))
            if len(projects) > 1:
                raise RuntimeError("目录中有多个 .xcproj，请用 --project 指定完整路径: %s" % cur)
            if projects:
                return os.path.join(cur, projects[0])
        except OSError:
            pass
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def build(project, verbose=False, timeout=BUILD_TIMEOUT, *, instance=None, url=None,
          instance_dir=None, process=None):
    """Compile with the original IDE toolchain; single builds clean up their child."""
    proc = process or IdeProcess(instance=instance, url=url, instance_dir=instance_dir)
    try:
        result = _build_once(os.path.abspath(project), proc, verbose, timeout)
    except BaseException:
        if process is None:
            _cleanup_after_error(proc)
        raise
    if process is None:
        proc.cleanup()
    return result


def _cleanup_after_error(proc):
    try:
        proc.cleanup()
    except Exception as exc:
        log("清理失败: %s" % exc)


def _build_once(project, proc, verbose, timeout):
    client = IdeClient()

    if not os.path.isfile(project):
        raise RuntimeError("找不到工程文件: %s" % project)

    t0 = time.time()
    boot = proc.restart(project, client)
    if verbose:
        log("  IDE 就绪 %.1fs (+settle %.0fs)" % (boot, SETTLE))

    proj = client.call("xc_get_project", timeout=30)
    if verbose:
        log("  工程: %s" % proj.get("name"))

    before = client.output(tail=200)
    tb = time.time()
    client.call("xc_build", timeout=60)

    text = before
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1.0)
        text = client.output(tail=200)
        if text != before and (RE_TOTAL.search(text) or RE_ZERO.search(text)
                               or RE_FAIL.search(text) or RE_SKIP.search(text)):
            break

    build_s = time.time() - tb
    res = parse_output(text)
    res["project"] = proj.get("name") or ""
    res["projectFile"] = project
    res["bootSeconds"] = round(boot, 1)
    res["buildSeconds"] = round(build_s, 1)
    res["totalSeconds"] = round(time.time() - t0, 1)
    res["raw"] = text
    res.update({key: client.target.info[key] for key in ("instanceId", "pid", "url")})
    return res


def _project_argument(args):
    value = args.project if args.project is not None else os.environ.get("XC_MCP_PROJECT", "")
    return os.path.abspath(value) if value else None


def _process_options(args):
    return {key: getattr(args, key, None) for key in ("instance", "url", "instance_dir")}


def cmd_build(args):
    project = _project_argument(args) or find_project()
    if not project:
        log("错误: 当前目录及上级找不到 .xcproj，请用 --project 指定")
        return 2

    log("xcbuild: 编译 %s" % os.path.basename(project))
    try:
        res = build(project, verbose=args.verbose, **_process_options(args))
    except Exception as e:
        log("错误: %s" % e)
        return 2

    if args.json:
        d = dict(res)
        d.pop("raw", None)
        print(json.dumps(d, ensure_ascii=False, indent=2))
    else:
        if res["skipped"]:
            print("跳过编译（IDE 未重启，标志未重置）")
        elif res["ok"]:
            print("编译成功  耗时 %.1fs (启动 %.1fs / 编译 %.1fs)"
                  % (res["totalSeconds"], res["bootSeconds"], res["buildSeconds"]))
        else:
            print("编译失败  错误 %d 个  耗时 %.1fs"
                  % (res["errorCount"], res["totalSeconds"]))
            for l in fmt_errors(res):
                print("  " + l)
    return 0 if res["ok"] else 1


def cmd_run(args):
    project = _project_argument(args) or find_project()
    if not project:
        log("错误: 找不到 .xcproj")
        return 2
    if not os.path.isfile(project):
        raise RuntimeError("找不到工程文件: %s" % project)
    proc = IdeProcess(**_process_options(args))
    log("xcbuild: 编译并调试运行 %s" % os.path.basename(project))
    try:
        res, text = _run_once(project, proc)
    except BaseException:
        _cleanup_after_error(proc)
        raise
    if res["ok"]:
        log("  调试 IDE 保留运行: instance=%s pid=%s %s" %
            (res["instanceId"], res["pid"], res["url"]))
        proc.detach()
    else:
        proc.cleanup()
    if args.json:
        d = dict(res)
        d.pop("raw", None)
        print(json.dumps(d, ensure_ascii=False, indent=2))
    else:
        if res["ok"]:
            print("编译成功并已启动调试  耗时 %.1fs" % res["totalSeconds"])
            for l in [x for x in text.splitlines() if "开始调试" in x][:2]:
                print("  " + l.strip())
        else:
            print("失败  错误 %d 个" % res["errorCount"])
            for l in fmt_errors(res):
                print("  " + l)
    return 0 if res["ok"] else 1


def _run_once(project, proc):
    client = IdeClient()
    t0 = time.time()
    boot = proc.restart(project, client)
    before = client.output(tail=200)
    client.call("xc_menu_cmd", {"command": "compile_all"}, timeout=60)
    text = before
    deadline = time.time() + BUILD_TIMEOUT
    while time.time() < deadline:
        time.sleep(1.0)
        text = client.output(tail=200)
        if text != before and (RE_TOTAL.search(text) or RE_ZERO.search(text)
                               or RE_FAIL.search(text) or "开始调试" in text):
            if RE_TOTAL.search(text) or RE_ZERO.search(text) or RE_FAIL.search(text):
                break
    res = parse_output(text)
    res["bootSeconds"] = round(boot, 1)
    res["totalSeconds"] = round(time.time() - t0, 1)
    res.update({key: client.target.info[key] for key in ("instanceId", "pid", "url")})
    return res, text


WATCH_EXT = (".xc", ".xh", ".xcs")


def scan_sources(project_dir):
    out = {}
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d.lower() not in
                   ("debug64", "debug", "release", "release64", "obj", ".git")]
        for f in files:
            if f.lower().endswith(WATCH_EXT):
                p = os.path.join(root, f)
                try:
                    out[p] = os.path.getmtime(p)
                except OSError:
                    pass
    return out


def cmd_watch(args):
    project = _project_argument(args) or find_project()
    if not project:
        log("错误: 找不到 .xcproj")
        return 2
    if args.interval < 0:
        raise RuntimeError("--interval must be nonnegative")
    proc = IdeProcess(**_process_options(args))
    try:
        return _watch_loop(project, args, proc)
    finally:
        proc.cleanup()  # one persistent binding; no accumulation of hidden IDEs


def _watch_loop(project, args, proc):
    pdir = os.path.dirname(os.path.abspath(project))
    log("xcbuild watch: %s   (Ctrl+C 退出)" % pdir)
    snap = scan_sources(pdir)
    last = None
    pending = False
    dirty_at = 0.0
    while True:
        try:
            cur = scan_sources(pdir)
            changed = [p for p in cur if snap.get(p) != cur[p]]
            for p in snap:
                if p not in cur:
                    changed.append(p)
            snap = cur
            if changed:
                pending = True
                dirty_at = time.time()
                log("  变更: %s" % ", ".join(os.path.basename(p) for p in changed[:5]))
            if pending and time.time() - dirty_at >= args.interval:
                pending = False
                try:
                    res = build(project, process=proc)
                except Exception as e:
                    log("  编译异常（停止 watch，不重新发现其他实例）: %s" % e)
                    return 2
                stamp = time.strftime("%H:%M:%S")
                if res["ok"]:
                    log("  [%s] 编译成功 %.1fs" % (stamp, res["totalSeconds"]))
                else:
                    log("  [%s] 编译失败 %d 个错误" % (stamp, res["errorCount"]))
                    for l in fmt_errors(res)[:12]:
                        log("      " + l)
                last = res
            time.sleep(0.4)
        except KeyboardInterrupt:
            log("\n退出 watch")
            return 0 if (last is None or last["ok"]) else 1


def cmd_doctor(args):
    print("xcbuild doctor")
    ok = True

    def chk(name, cond, detail=""):
        nonlocal ok
        print("  [%s] %-26s %s" % ("OK" if cond else "!!", name, detail))
        if not cond:
            ok = False

    chk("IDE 可执行文件", os.path.isfile(IDE_EXE),
        "%d B" % os.path.getsize(IDE_EXE) if os.path.isfile(IDE_EXE) else "缺失")

    options = instances.arguments_options(args)
    options["project"] = _project_argument(args)
    try:
        live = instances.discover(args.instance_dir)
        for target in live:
            info = target.info
            print("  instance=%s pid=%s %s %s" %
                  (target.instance_id, info["pid"], target.url, info["projectFile"]))
        target = instances.resolve(**options)
        chk("插件服务 /health", True, target.url)
        client = IdeClient(target=target)
        st = client.call("xc_status", timeout=20)  # read-only; never start/close an IDE
        chk("插件握手", st.get("ok") is True, "ver=%s" % st.get("ver", st.get("_raw", "")[:40]))
        cnt = st.get("toolCount")
        chk("工具数", cnt == 29, str(cnt))
    except Exception as exc:
        chk("实例选择/插件服务", False, str(exc))

    p = _project_argument(args) or find_project()
    chk("当前目录可定位工程", bool(p), p or "请用 --project 指定")
    if p:
        d = os.path.dirname(p)
        chk("工程目录可读", os.path.isdir(d), d)
        chk("obj 目录存在", os.path.isdir(os.path.join(d, "Debug64", "obj")),
            "首次编译会自动创建" )

    print("\n结论:", "环境就绪" if ok else "存在问题，见上方 !! 项")
    return 0 if ok else 1


def main(argv=None):
    # -I ignores PYTHONIOENCODING. Both machine output and diagnostics are UTF-8,
    # including argparse failures before command dispatch.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(
        prog="xcbuild",
        description="无界面编译炫语言(.xc)工程 — 后台隐藏 IDE 充当编译器",
    )
    sub = ap.add_subparsers(dest="cmd")

    b = sub.add_parser("build", help="编译一次")
    instances.add_selection_arguments(b, project_short=True)
    b.add_argument("--json", action="store_true")
    b.add_argument("--verbose", "-v", action="store_true")
    b.set_defaults(func=cmd_build)

    w = sub.add_parser("watch", help="监视源码变更并自动编译")
    instances.add_selection_arguments(w, project_short=True)
    w.add_argument("--interval", type=float, default=1.0)
    w.set_defaults(func=cmd_watch)

    r = sub.add_parser("run", help="编译并启动调试")
    instances.add_selection_arguments(r, project_short=True)
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_run)

    d = sub.add_parser("doctor", help="自检环境")
    instances.add_selection_arguments(d, project_short=True)
    d.set_defaults(func=cmd_doctor)

    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 2
    try:
        return args.func(args)
    except Exception as exc:
        log("错误: %s" % exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())