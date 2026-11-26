#!/usr/bin/env python3
"""mCode's MCP server (stdio), started by Claude Code for one AI turn.

Standard library only and no mCode imports: it forwards ``tools/list`` and
``tools/call`` to the running mCode over the socket in ``MCODE_BRIDGE``,
identified by ``MCODE_TURN``. mCode decides which tools exist for the turn
(see bridge.py); this process cannot add any.

Protocol: JSON-RPC 2.0, one message per line (MCP stdio transport).
"""

import json
import os
import socket
import sys

PROTOCOL = "2025-06-18"
BRIDGE = os.environ.get("MCODE_BRIDGE", "")
TURN = os.environ.get("MCODE_TURN", "")
TIMEOUT = float(os.environ.get("MCODE_TOOL_TIMEOUT", "3600"))


def bridge(tool, args):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(TIMEOUT)
            sock.connect(BRIDGE)
            sock.sendall((json.dumps({"turn": TURN, "tool": tool, "args": args}) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        return json.loads(data.decode("utf-8") or "{}")
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"mCode is not reachable ({exc})"}


def reply(message_id, result=None, error=None):
    body = {"jsonrpc": "2.0", "id": message_id}
    if error is not None:
        body["error"] = error
    else:
        body["result"] = result
    sys.stdout.write(json.dumps(body) + "\n")
    sys.stdout.flush()


def handle(message):
    method = message.get("method")
    message_id = message.get("id")
    params = message.get("params") or {}
    if message_id is None:          # a notification (e.g. notifications/initialized)
        return
    if method == "initialize":
        version = params.get("protocolVersion") or PROTOCOL
        reply(message_id, {"protocolVersion": version, "capabilities": {"tools": {}},
                           "serverInfo": {"name": "mcode", "version": "1"}})
    elif method == "ping":
        reply(message_id, {})
    elif method == "tools/list":
        answer = bridge("_list", {})
        reply(message_id, {"tools": answer.get("tools", []) if answer.get("ok") else []})
    elif method == "tools/call":
        answer = bridge(params.get("name", ""), params.get("arguments") or {})
        if answer.get("ok"):
            reply(message_id, {"content": [{"type": "text", "text": answer.get("text", "")}],
                               "isError": False})
        else:
            reply(message_id, {"content": [{"type": "text", "text": answer.get("error", "failed")}],
                               "isError": True})
    elif method in ("resources/list", "prompts/list"):
        reply(message_id, {method.split("/")[0]: []})
    else:
        reply(message_id, error={"code": -32601, "message": f"unknown method {method}"})


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        try:
            handle(message)
        except Exception as exc:        # answer instead of dying mid-turn
            if isinstance(message, dict) and message.get("id") is not None:
                reply(message["id"], error={"code": -32603, "message": str(exc)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
