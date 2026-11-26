"""mCode's tools for Claude, served to the MCP server over a private socket.

Claude Code starts ``app/ai/mcp_server.py`` (stdio MCP). That small process
forwards each tool call over a Unix socket (owner-only, in the data
folder's ``run/``) to this bridge inside the running mCode, which carries
it out with mCode's own services: a build is an ordinary mCode build job,
visible on the LCD and in the desktop IDE.

Each AI turn registers a random turn token naming its project and mode; the
MCP server can only use the tools that mode allows, for that project, and
only while the turn runs. There is no tool that uploads, erases, changes
fuses or installs anything.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import threading
from typing import Dict, Optional

log = logging.getLogger("mcode.bridge")

READ_TOOLS = {
    "project_info": "The project's board, MCU, toolchain, framework, files, libraries and the "
                    "connected board.",
    "last_build": "The last build's result: state, problems with file and line, and the end of "
                  "the compiler output.",
    "serial_log": "The most recent lines from mCode's serial monitor (read-only).",
    "list_devices": "Development boards connected to this computer over USB (read-only).",
}
BUILD_TOOLS = {
    "build": "Compile the project with its real toolchain (Arduino CLI, PlatformIO, ESP-IDF or "
             "make) and return the result and compiler problems. Does not upload.",
    "run_tests": "Run the project's tests that need no hardware (PlatformIO native tests, make "
                 "test, or a compilation check) and return the result.",
}
ADVANCED_TOOLS = {
    "static_check": "Run static analysis (pio check or cppcheck) and return the findings.",
    "clean": "Delete the project's build output.",
}
MODE_TOOLS = {"ask": dict(READ_TOOLS), "assisted": dict(READ_TOOLS, **BUILD_TOOLS),
              "advanced": dict(READ_TOOLS, **BUILD_TOOLS, **ADVANCED_TOOLS)}
SCHEMAS = {"serial_log": {"type": "object", "properties": {
    "lines": {"type": "integer", "description": "how many recent lines (max 400)"}}}}


class ToolBridge:
    def __init__(self, studio, socket_path: str):
        self.studio = studio
        self.socket_path = socket_path
        self._turns: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass
        os.makedirs(os.path.dirname(self.socket_path), mode=0o700, exist_ok=True)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            sock.bind(self.socket_path)
        finally:
            os.umask(old)
        os.chmod(self.socket_path, 0o600)
        sock.listen(8)
        self._sock = sock
        self._running = True
        self._thread = threading.Thread(target=self._serve, name="ai-bridge", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._sock.close()
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass

    def open_turn(self, project_id: str, mode: str, on_tool=None) -> str:
        token = secrets.token_hex(16)
        with self._lock:
            self._turns[token] = {"project_id": project_id, "mode": mode, "on_tool": on_tool}
        return token

    def close_turn(self, token: str) -> None:
        with self._lock:
            self._turns.pop(token, None)

    def tools_for(self, mode: str) -> list:
        return [{"name": name, "description": text,
                 "inputSchema": SCHEMAS.get(name, {"type": "object", "properties": {}})}
                for name, text in MODE_TOOLS.get(mode, READ_TOOLS).items()]

    # ---------------------------------------------------------------- serving
    def _serve(self) -> None:
        while self._running:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), name="ai-bridge-call",
                             daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            try:
                reader = conn.makefile("r", encoding="utf-8")
                line = reader.readline(1_000_000)
                request = json.loads(line or "{}")
                reply = self.call(request.get("turn", ""), request.get("tool", ""),
                                  request.get("args") or {})
            except Exception as exc:    # isolation boundary: answer the call, never crash
                log.exception("bridge call failed")
                reply = {"ok": False, "error": f"mCode could not run the tool: {exc}"}
            try:
                conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
            except OSError:
                pass

    def call(self, token: str, tool: str, args: dict) -> dict:
        with self._lock:
            turn = self._turns.get(token)
        if turn is None:
            return {"ok": False, "error": "This AI request has ended."}
        if tool == "_list":
            return {"ok": True, "tools": self.tools_for(turn["mode"])}
        allowed = MODE_TOOLS.get(turn["mode"], READ_TOOLS)
        if tool not in allowed:
            return {"ok": False, "error": f"The {tool} tool is not available in this mode."}
        if turn.get("on_tool"):
            turn["on_tool"](tool)
        studio = self.studio
        project = studio.projects.find(turn["project_id"])
        if tool == "project_info":
            info = project.summary()
            info.pop("path", None)
            device = studio.devices.selected(project) if studio.devices else None
            info["connected_board"] = device.to_dict() if device else None
            return {"ok": True, "text": json.dumps(info, indent=2)}
        if tool == "last_build":
            last = studio.builds.last(project.id)
            return {"ok": True, "text": _format_build(last) if last else "No build yet."}
        if tool == "serial_log":
            count = max(1, min(400, int(args.get("lines") or 120)))
            monitor = studio.monitor
            if monitor is None or not monitor.lines:
                return {"ok": True, "text": "The serial monitor has no output."}
            state = monitor.status()
            return {"ok": True, "text": f"Port {state['port']} at {state['baud']} baud "
                                        f"({'connected' if state['connected'] else 'disconnected'})\n"
                                        + monitor.text(count, timestamps=True)}
        if tool == "list_devices":
            devices = [d.to_dict() for d in studio.devices.list()] if studio.devices else []
            slim = [{k: d.get(k) for k in ("port", "description", "usb_id", "bridge", "chip",
                                           "problem")} for d in devices]
            return {"ok": True, "text": json.dumps(slim, indent=2) if slim else
                    "No boards are connected."}
        action = {"build": "build", "run_tests": "test", "static_check": "check",
                  "clean": "clean"}[tool]
        try:
            job = studio.builds.run(project, action, source="ai")
        except Exception as exc:
            return {"ok": True, "text": f"{tool} could not start: {exc}"}
        studio.jobs.wait(job.id, job.spec.timeout + 30)
        last = studio.builds.last(project.id) or {}
        return {"ok": True, "text": _format_build(last if last.get("job") == job.id else
                                                  {"action": action, "state": job.state,
                                                   "headline": job.error,
                                                   "output_tail": job.tail(60)})}


def _format_build(last: dict) -> str:
    lines = [f"{last.get('action', 'build')}: {last.get('state')}"
             + (f" — {last.get('headline')}" if last.get("headline") else "")]
    size = last.get("size") or {}
    for key in ("flash", "ram"):
        if key in size:
            lines.append(f"{key}: {size[key].get('used')} bytes ({size[key].get('percent')}%)")
    tests = last.get("tests")
    if tests:
        lines.append(f"tests: {tests.get('summary')}")
    for diag in (last.get("diagnostics") or [])[:30]:
        where = f"{diag.get('file')}:{diag.get('line')}:{diag.get('column')}" if diag.get("file") else ""
        lines.append(f"{diag.get('severity')}: {where} {diag.get('message')}")
    tail = last.get("output_tail") or []
    if tail:
        lines.append("output (end):")
        lines.extend(tail[-60:])
    return "\n".join(lines)
