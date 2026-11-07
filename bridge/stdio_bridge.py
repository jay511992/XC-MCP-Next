#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stdio -> HTTP 桥：把只支持 stdio 的 MCP 客户端接到运行在 IDE 内的插件上。

配置：
{
  "mcpServers": {
    "xc-ide": {
      "type": "stdio",
      "command": "python",
      "args": ["<IDE根>\\\\xc-plugin\\\\XC-MCP-Next\\\\bridge\\\\stdio_bridge.py"]
    }
  }
}
"""
import sys
import os
import json
import argparse

# -I omits the script directory from sys.path; load only our sibling module.
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import instances

BASE = os.environ.get("XC_MCP_URL", "")  # compatibility: no implicit 18766 routing
_selection = {}
_target = None


def _read_token():
    """Keep the legacy no-argument, environment-first, fresh-file API."""
    return instances.read_token()


def _bound_target():
    global _target
    if _target is None:
        _target = instances.resolve(**_selection)
    return _target


def _iter_stdin_lines():
    buf = getattr(sys.stdin, "buffer", None)
    if buf is not None:
        for chunk in buf:
            yield chunk.decode("utf-8", errors="replace")
        return
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    for chunk in sys.stdin:
        yield chunk


def forward(req_body: str) -> str:
    raw = _bound_target().post(req_body, timeout=120)
    raw = raw.strip()
    if raw.startswith("event:") or "\ndata:" in raw or raw.startswith("data:"):
        parts = []
        for line in raw.splitlines():
            if line.startswith("data:"):
                parts.append(line[5:].strip())
        raw = "".join(parts)
    return raw


def main(argv=None):
    global _selection, _target
    parser = argparse.ArgumentParser(description="stdio bridge to one local xc-mcp IDE instance")
    instances.add_selection_arguments(parser)
    args = parser.parse_args(argv)
    _selection = instances.selection_options(**instances.arguments_options(args))
    _selection["use_env"] = False
    _target = None  # bind lazily on the first request, not at process startup
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.stderr.write("xc-mcp bridge: lazy binding %s\n" %
                     (_selection["url"] or _selection["instance"] or _selection["project"] or "<unique live instance>"))

    for raw in _iter_stdin_lines():
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "Parse error"}}) + "\n")
            sys.stdout.flush()
            continue

        is_notification = (isinstance(req, dict) and "id" not in req
                           and isinstance(req.get("method"), str))

        try:
            body = forward(line)
        except Exception as e:
            if not is_notification:
                requests = req if isinstance(req, list) and req else [req]
                errors = []
                for item in requests:
                    if isinstance(item, dict) and "id" not in item and isinstance(item.get("method"), str):
                        continue
                    errors.append({
                        "jsonrpc": "2.0", "id": item.get("id") if isinstance(item, dict) else None,
                        "error": {"code": -32603,
                                  "message": "bridge error: %s（请检查目标 IDE 与 --project/--instance/--url；不会切换实例）" % e}})
                if errors:
                    sys.stdout.write(json.dumps(errors if isinstance(req, list) and req else errors[0],
                                                ensure_ascii=False) + "\n")
                sys.stdout.flush()
            continue

        if body and not is_notification:
            sys.stdout.write(body + "\n")
            sys.stdout.flush()

    sys.stderr.write("xc-mcp bridge 退出\n")


if __name__ == "__main__":
    main()