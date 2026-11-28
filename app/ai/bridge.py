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

Builds run the project's build scripts on this device, so the build, test,
check and clean tools first ask the turn's *guard* (``AIService``): while
the turn has changes waiting for the user's decision (partition, bootloader,
dependency or build-script edits, git settings) they refuse and say why.
Jobs a turn starts are cancelled when the turn ends, so stopping Claude
also stops its build. Git in Advanced mode is mCode's own (``gitutil``,
which never runs programs named in the repository's configuration), not a
shell.
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
    "git_status": "The project's git branch and changed files.",
    "git_diff": "The project's uncommitted changes as a patch.",
    "git_log": "The project's recent commits (hash, time, subject).",
    "git_show": "One commit's message and patch (rev: HEAD, HEAD~1 or a hash).",
    "git_commit": "Stage all changes and commit them with the given message.",
}
MODE_TOOLS = {"ask": dict(READ_TOOLS), "assisted": dict(READ_TOOLS, **BUILD_TOOLS),
              "advanced": dict(READ_TOOLS, **BUILD_TOOLS, **ADVANCED_TOOLS)}
JOB_TOOLS = {"build": "build", "run_tests": "test", "static_check": "check", "clean": "clean"}
SCHEMAS = {
    "serial_log": {"type": "object", "properties": {
        "lines": {"type": "integer", "description": "how many recent lines (max 400)"}}},
    "git_show": {"type": "object", "properties": {
        "rev": {"type": "string", "description": "a revision: HEAD, HEAD~1 or a commit hash"}}},
    "git_commit": {"type": "object", "properties": {
        "message": {"type": "string", "description": "the commit message"}},
        "required": ["message"]},
}


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

    def open_turn(self, project_id: str, mode: str, on_tool=None, guard=None) -> str:
        """``guard()`` returns why build-type tools must wait ("" = they may run)."""
        token = secrets.token_hex(16)
        with self._lock:
            self._turns[token] = {"project_id": project_id, "mode": mode, "on_tool": on_tool,
                                  "guard": guard, "jobs": []}
        return token

    def close_turn(self, token: str) -> None:
        """End the turn: its token stops working and its unfinished jobs are cancelled."""
        with self._lock:
            turn = self._turns.pop(token, None)
        for job_id in (turn or {}).get("jobs", []):
            if self.studio.jobs.cancel(job_id):
                log.info("AI turn ended: cancelled its job %s", job_id)

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
                with conn.makefile("r", encoding="utf-8") as reader:
                    line = reader.readline(1_000_000)
                request = json.loads(line or "{}")
                if not isinstance(request, dict):
                    raise ValueError("expected a JSON object")
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
        if not isinstance(args, dict):
            return {"ok": False, "error": "Tool arguments must be an object."}
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
            try:
                count = max(1, min(400, int(args.get("lines") or 120)))
            except (TypeError, ValueError):
                return {"ok": False, "error": "lines must be a number."}
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
        if tool.startswith("git_"):
            return self._git(project, tool, args)
        action = JOB_TOOLS[tool]
        guard = turn.get("guard")
        reason = guard() if guard is not None else ""
        if reason:
            log.info("AI %s refused: %s", tool, reason)
            return {"ok": True, "text": f"mCode did not run {tool}: {reason}"}
        try:
            job = studio.builds.run(project, action, source="ai")
        except Exception as exc:
            return {"ok": True, "text": f"{tool} could not start: {exc}"}
        with self._lock:
            if token not in self._turns:          # the turn ended while the job started
                ended = True
            else:
                ended = False
                turn["jobs"].append(job.id)
        if ended:
            studio.jobs.cancel(job.id)
            return {"ok": False, "error": "This AI request has ended."}
        studio.jobs.wait(job.id, job.spec.timeout + 30)
        last = studio.builds.last(project.id) or {}
        return {"ok": True, "text": _format_build(last if last.get("job") == job.id else
                                                  {"action": action, "state": job.state,
                                                   "headline": job.error,
                                                   "output_tail": job.tail(60)})}


    def _git(self, project, tool: str, args: dict) -> dict:
        from ..projects import gitutil
        if not gitutil.git_path():
            return {"ok": True, "text": "git is not installed on this device."}
        try:
            if tool == "git_status":
                return {"ok": True, "text": json.dumps(gitutil.status(project.path), indent=2)}
            if tool == "git_diff":
                return {"ok": True, "text": gitutil.diff(project.path) or "No changes."}
            if tool == "git_log":
                return {"ok": True, "text": "\n".join(
                    f"{c['hash']} {c['subject']}" for c in gitutil.log(project.path)) or
                    "No commits."}
            if tool == "git_show":
                return {"ok": True, "text": gitutil.show(project.path, str(args.get("rev") or
                                                                           "HEAD"))}
            message = str(args.get("message") or "").strip()
            if not message:
                return {"ok": False, "error": "Give a commit message."}
            # No hooks for the AI's commits: a hook is a program in the project.
            return {"ok": True, "text": gitutil.commit(project.path, message[:500], hooks=False)}
        except (RuntimeError, ValueError, OSError) as exc:
            return {"ok": True, "text": f"{tool} failed: {exc}"}


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
