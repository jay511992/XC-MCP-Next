#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local xc-mcp instance discovery, strict selection and permanently bound targets.

Only the standard library is used. No request uses a system proxy or follows a
redirect. Registry project names are snapshots: selection always probes /health.
"""
import argparse
import http.client
import io
import json
import ntpath
import os
import re
import sys
import time
import urllib.error
import urllib.parse

sys.dont_write_bytecode = True

_FIELDS = ("schemaVersion", "server", "status", "instanceId", "pid", "startedAt",
           "port", "url", "ideExe", "hwnd", "projectFile", "projectDir",
           "projectName", "tokenFile")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\Z")
_FILETIME = re.compile(r"[0-9a-fA-F]{16}\Z")
_MAX_RECORD = 1024 * 1024


class InstanceError(RuntimeError):
    """A target could not be selected or safely used."""


class ConfigurationError(InstanceError):
    pass


class NoMatchError(InstanceError):
    pass


class AmbiguousMatchError(InstanceError):
    pass


class IdentityError(InstanceError):
    pass


def _remaining_timeout(timeout, deadline):
    if deadline is None:
        return timeout
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("MCP discovery/request deadline expired")
    return min(timeout, remaining)


def normalize_project(path):
    """Normalize a fully qualified Windows path; never match by basename."""
    if not isinstance(path, str) or not path or any(ord(c) < 32 for c in path):
        raise ConfigurationError("project/executable path must be a full Windows path")
    path = path.replace("/", "\\")
    if path.lower().startswith("\\\\?\\unc\\"):
        path = "\\\\" + path[8:]
    elif path.startswith("\\\\?\\"):
        path = path[4:]
    if path.startswith("\\\\.\\"):
        raise ConfigurationError("device paths are not project paths")
    drive, tail = ntpath.splitdrive(path)
    if not drive or not ntpath.isabs(path) or (not tail.startswith("\\") and tail):
        raise ConfigurationError("use a full Windows project/executable path: %s" % path)
    if not (re.fullmatch(r"[A-Za-z]:", drive) or drive.startswith("\\\\")):
        raise ConfigurationError("invalid Windows path: %s" % path)
    return ntpath.normcase(ntpath.normpath(path))


def local_url(url):
    """Return a canonical /mcp URL, rejecting credentials and non-loopback URLs."""
    if not isinstance(url, str) or not url or any(c.isspace() or ord(c) < 32 for c in url):
        raise ConfigurationError("MCP URL must be a local http://127.0.0.1:PORT/mcp URL")
    try:
        p = urllib.parse.urlsplit(url)
        port = p.port or 80
    except ValueError as exc:
        raise ConfigurationError("invalid MCP URL") from exc
    if (p.scheme.lower() != "http" or p.hostname not in ("127.0.0.1", "localhost")
            or p.username is not None or p.password is not None or p.query or p.fragment
            or "?" in url or "#" in url or "\\" in url
            or p.path not in ("", "/", "/mcp") or not 1 <= port <= 65535
            or p.netloc.endswith(":")):
        raise ConfigurationError("only local http://127.0.0.1 or localhost /mcp URLs are allowed")
    # Explicit port 0 is not a reachable endpoint (unlike XC_MCP_PORT=0 on servers).
    if p.port == 0:
        raise ConfigurationError("MCP URL requires a nonzero port")
    return "http://%s:%d/mcp" % (p.hostname, port)


def _same_endpoint(left, right):
    # localhost is connected to literal 127.0.0.1, not resolved through DNS.
    return urllib.parse.urlsplit(local_url(left)).port == urllib.parse.urlsplit(local_url(right)).port


def instance_directory(path=None):
    if path is None:
        path = os.environ.get("XC_MCP_INSTANCE_DIR", "")
    return os.path.abspath(path or os.path.join(os.environ.get("LOCALAPPDATA", ""),
                                               "xc-mcp", "instances"))


def read_token(token_file=None):
    """Read on every call: environment, selected instance file, legacy token file."""
    token = os.environ.get("XC_MCP_TOKEN", "").strip()
    if token:
        return token
    legacy = os.path.join(os.environ.get("LOCALAPPDATA", ""), "xc-mcp", "token")
    for path in (token_file, legacy):
        if not path:
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as stream:
                token = stream.read(65536).strip()
            if token:
                return token
        except OSError:
            pass
    return os.environ.get("XC_MCP_TOKEN", "")


def selection_options(*, project=None, instance=None, url=None, instance_dir=None, use_env=True):
    """Resolve each CLI option before its environment equivalent; selectors intersect."""
    def option(value, name):
        return (os.environ.get(name, "") if use_env and value is None else value) or ""
    project = option(project, "XC_MCP_PROJECT")
    instance = option(instance, "XC_MCP_INSTANCE")
    url = option(url, "XC_MCP_URL")
    directory = option(instance_dir, "XC_MCP_INSTANCE_DIR")
    if project:
        project = normalize_project(project)
    if instance and (not isinstance(instance, str) or not _ID.fullmatch(instance)):
        raise ConfigurationError("invalid instance ID")
    if url:
        url = local_url(url)
    return {"project": project, "instance": instance, "url": url,
            "instance_dir": instance_directory(directory)}


def _validate_info(value):
    if not isinstance(value, dict) or any(key not in value for key in _FIELDS):
        raise IdentityError("incomplete instance information")
    info = {key: value[key] for key in _FIELDS}
    if (type(info["schemaVersion"]) is not int or info["schemaVersion"] != 1
            or info["server"] != "xc-mcp-next" or info["status"] != "ok"):
        raise IdentityError("unsupported or unready instance record")
    if not isinstance(info["instanceId"], str) or not _ID.fullmatch(info["instanceId"]):
        raise IdentityError("invalid instance ID in health/registry")
    if not isinstance(info["startedAt"], str) or not _FILETIME.fullmatch(info["startedAt"]):
        raise IdentityError("invalid process creation FILETIME")
    for key in ("pid", "port", "hwnd"):
        if type(info[key]) is not int or info[key] < (0 if key == "hwnd" else 1):
            raise IdentityError("invalid instance %s" % key)
    for key in ("ideExe", "projectFile", "projectDir", "projectName", "tokenFile"):
        if not isinstance(info[key], str):
            raise IdentityError("invalid instance %s" % key)
    normalize_project(info["ideExe"])
    if info["projectFile"]:
        normalize_project(info["projectFile"])
    if info["projectDir"]:
        normalize_project(info["projectDir"])
    if not os.path.isabs(info["tokenFile"]):
        raise IdentityError("tokenFile must be an absolute path")
    info["url"] = local_url(info["url"])
    if urllib.parse.urlsplit(info["url"]).port != info["port"]:
        raise IdentityError("registry port and URL disagree")
    info["startedAt"] = info["startedAt"].lower()
    return info


def _records(directory):
    try:
        with os.scandir(directory) as entries:
            paths = sorted(entry.path for entry in entries
                           if entry.name.endswith(".json") and entry.is_file())
    except OSError:
        return
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8-sig") as stream:
                raw = stream.read(_MAX_RECORD + 1)
            if len(raw) > _MAX_RECORD:
                continue
            info = _validate_info(json.loads(raw))
            if os.path.basename(path) != info["instanceId"] + ".json":
                continue
            expected_token = os.path.join(directory, info["instanceId"] + ".token")
            if os.path.normcase(os.path.abspath(info["tokenFile"])) != os.path.normcase(expected_token):
                continue
            yield info
        except (OSError, ValueError, TypeError, InstanceError):
            continue


class Target:
    """An endpoint with immutable instance/process identity and optional project guard.

    health() returns live public metadata; post() returns UTF-8 response text.
    Neither method discovers another target. A 401 rereads the token once; a 409
    or disconnect is surfaced to the caller, never retried against another IDE.
    """
    def __init__(self, url, *, info=None, token_file=None, project=None, instance=None,
                 allow_legacy=False):
        self._url = local_url(url)
        self._project = normalize_project(project) if project else ""
        self._instance = instance or ""
        if self._instance and not _ID.fullmatch(self._instance):
            raise ConfigurationError("invalid instance ID")
        self._identity = None
        self._info = {}
        self._token_file = token_file
        self._allow_legacy = allow_legacy
        self._checked = False
        if info is not None:
            info = _validate_info(info)
            if not _same_endpoint(info["url"], self._url):
                raise IdentityError("target URL and instance URL disagree")
            if self._instance and self._instance != info["instanceId"]:
                raise IdentityError("URL and instance selectors disagree")
            self._bind(info)
            self._token_file = token_file or info["tokenFile"]

    @property
    def url(self):
        return self._url

    @property
    def instance_id(self):
        return self._instance or None

    @property
    def project(self):
        return self._project or None

    @property
    def info(self):
        # Never expose a mutable copy of the actual binding, or token contents.
        return dict(self._info)

    def _bind(self, info):
        self._instance = info["instanceId"]
        self._identity = {key: info[key] for key in
                          ("instanceId", "pid", "startedAt", "url", "ideExe")}
        self._info = dict(info)

    def _exchange(self, method, path, data=None, timeout=2.0, max_bytes=None, deadline=None):
        endpoint = urllib.parse.urlsplit(self._url)
        own_deadline = time.monotonic() + timeout
        deadline = own_deadline if deadline is None else min(deadline, own_deadline)
        for attempt in (0, 1):
            headers = {"Host": endpoint.netloc, "Accept": "application/json, text/event-stream"}
            token = read_token(self._token_file)
            if token:
                headers["X-XC-Token"] = token
            if self._instance:
                headers["X-XC-Instance"] = self._instance
            if self._project:
                headers["X-XC-Project"] = urllib.parse.quote(self._project, safe="")
            if data is not None:
                headers["Content-Type"] = "application/json"
            connection = http.client.HTTPConnection("127.0.0.1", endpoint.port,
                                                     timeout=_remaining_timeout(timeout, deadline))
            try:
                connection.request(method, path, body=data, headers=headers)
                response = connection.getresponse()
                body = response.read(None if max_bytes is None else max_bytes + 1)
                _remaining_timeout(timeout, deadline)
                if max_bytes is not None and len(body) > max_bytes:
                    raise IdentityError("oversized health response")
                if response.status == 401 and attempt == 0:
                    continue
                if not 200 <= response.status < 300:
                    # No redirects and no live response stream/resource in errors.
                    error = urllib.error.HTTPError(self._url, response.status, response.reason,
                                                   response.headers, io.BytesIO(body))
                    error.response_body = body
                    error.close()
                    raise error
                return body.decode("utf-8", "strict")
            finally:
                connection.close()

    def health(self, timeout=2.0, *, deadline=None):
        raw = self._exchange("GET", "/health", timeout=timeout, max_bytes=_MAX_RECORD,
                             deadline=deadline)
        try:
            value = json.loads(raw)
        except ValueError as exc:
            raise IdentityError("invalid JSON from /health") from exc
        if not isinstance(value, dict) or value.get("status") != "ok":
            raise IdentityError("/health did not report status ok")
        if not any(key in value for key in ("instanceId", "pid", "startedAt", "schemaVersion")):
            if self._identity or self._instance or self._project or not self._allow_legacy:
                raise IdentityError("legacy /health cannot verify the selected instance/project")
            self._info = {key: value[key] for key in ("status", "server") if key in value}
            self._info["url"] = self.url
        else:
            info = _validate_info(value)
            if not _same_endpoint(info["url"], self._url):
                raise IdentityError("/health returned a different endpoint")
            if self._instance and info["instanceId"] != self._instance:
                raise IdentityError("bound instance ID changed; reconnect explicitly")
            if self._identity:
                for key, expected in self._identity.items():
                    actual = info[key]
                    if key == "ideExe":
                        actual, expected = normalize_project(actual), normalize_project(expected)
                    if actual != expected:
                        raise IdentityError("bound instance %s changed; reconnect explicitly" % key)
            if self._project and (not info["projectFile"] or
                                  normalize_project(info["projectFile"]) != self._project):
                raise IdentityError("bound instance changed project; reconnect explicitly")
            if self._identity is None:
                self._bind(info)
                self._token_file = self._token_file or info["tokenFile"]
            self._info = info
        self._checked = True
        return self.info

    def post(self, body, timeout=120.0):
        if not self._checked or self._identity is None:
            # Legacy explicit URLs still work; upgrade to an identity binding as
            # soon as that endpoint supplies the new authenticated health schema.
            self.health(timeout=min(timeout, 2.0))
        if isinstance(body, str):
            body = body.encode("utf-8")
        elif not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        return self._exchange("POST", "/mcp", data=body, timeout=timeout)


def discover(instance_dir=None, *, timeout=1.0, project=None, instance=None, pid=None, deadline=None):
    """Return live, authenticated Targets, excluding malformed/stale registrations.

    A project filter is sent on every health probe, not applied to the snapshot.
    pid is for an already owned process, never for guessing a process by name.
    """
    project = normalize_project(project) if project else ""
    targets = []
    for info in _records(instance_directory(instance_dir)):
        if instance and info["instanceId"] != instance:
            continue
        if pid is not None and info["pid"] != pid:
            continue
        _remaining_timeout(timeout, deadline)
        try:
            target = Target(info["url"], info=info, project=project)
            target.health(timeout=timeout, deadline=deadline)
            targets.append(target)
        except (OSError, ValueError, http.client.HTTPException, InstanceError):
            continue
    _remaining_timeout(timeout, deadline)  # never return a partial scan as a unique match
    return targets


def _unique(targets, description):
    if not targets:
        raise NoMatchError("no live authenticated MCP instance matches %s" % description)
    if len(targets) > 1:
        choices = "; ".join("%s (pid=%s, %s, %s)" %
                            (t.instance_id, t.info["pid"], t.url, t.info["projectFile"])
                            for t in targets)
        raise AmbiguousMatchError("multiple MCP instances match %s: %s; use --instance" %
                                  (description, choices))
    return targets[0]


def resolve(*, project=None, instance=None, url=None, instance_dir=None, timeout=2.0,
            use_env=True, deadline=None):
    """Select exactly one live Target. Explicit URL also supports legacy servers.

    CLI/argument values win over the corresponding environment values. Combined
    URL, ID and project selectors must all agree; there is no default-port fallback.
    """
    options = selection_options(project=project, instance=instance, url=url,
                                instance_dir=instance_dir, use_env=use_env)
    project, instance, url = (options[key] for key in ("project", "instance", "url"))
    directory = options["instance_dir"]
    if url:
        # Find the selected URL's per-instance token without trusting its snapshot.
        last_http_error = None
        for info in _records(directory):
            if not _same_endpoint(info["url"], url):
                continue
            _remaining_timeout(timeout, deadline)
            try:
                candidate = Target(url, info=info, project=project)
                candidate.health(timeout=timeout, deadline=deadline)
            except urllib.error.HTTPError as exc:
                last_http_error = exc
                continue
            except (OSError, ValueError, http.client.HTTPException, InstanceError):
                continue
            if instance and candidate.instance_id != instance:
                raise IdentityError("URL and instance selectors disagree")
            return candidate
        if last_http_error is not None:
            raise last_http_error  # do not hide a 409 or add another pair of 401 retries
        target = Target(url, project=project, instance=instance, allow_legacy=True)
        target.health(timeout=timeout, deadline=deadline)
        return target
    description = "instance=%s project=%s" % (instance or "<any>", project or "<any>")
    return _unique(discover(directory, timeout=timeout, project=project, instance=instance,
                            deadline=deadline), description)


def add_selection_arguments(parser, *, project_short=False):
    parser.add_argument("--project", *(["-p"] if project_short else []),
                        help="full project path (XC_MCP_PROJECT)")
    parser.add_argument("--instance", help="instance ID (XC_MCP_INSTANCE)")
    parser.add_argument("--url", help="explicit local MCP URL (XC_MCP_URL)")
    parser.add_argument("--instance-dir", help="registry directory (XC_MCP_INSTANCE_DIR)")


def arguments_options(args):
    return {key: getattr(args, key, None) for key in ("project", "instance", "url", "instance_dir")}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="list all live authenticated instances")
    listing.add_argument("--instance-dir")
    listing.add_argument("--json", action="store_true")
    resolving = commands.add_parser("resolve", help="resolve one instance; selectors intersect")
    add_selection_arguments(resolving)
    resolving.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            records = [target.info for target in discover(args.instance_dir)]
            result = records
        else:
            result = resolve(**arguments_options(args)).info
            records = [result]
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            for info in records:
                print("%s\tpid=%s\t%s\t%s" % (info.get("instanceId", "<legacy URL>"),
                      info.get("pid", "?"), info.get("url", ""), info.get("projectFile", "")))
        return 0
    except (InstanceError, OSError, ValueError, http.client.HTTPException) as exc:
        if args.json:
            print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        else:
            print("xc-mcp: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
