"""The Claude Code CLI: the only way mCode talks to Claude.

mCode uses the user's own Claude Code installation and login. It never sees
a password or a token: signing in is ``claude auth login`` (the official
browser flow; on a device without a browser the CLI prints a link and asks
for the code shown after signing in), signing out is ``claude auth logout``,
and the status comes from ``claude auth status --json`` (mCode keeps only
whether you are signed in, the method and the plan; never the e-mail address,
organisation or anything secret).

A Claude **subscription login** and an **API key** are different things:
when ``ANTHROPIC_API_KEY`` is set, Claude Code bills the API key instead of
the subscription. mCode reports which one is in use and does not pretend
they are the same.

Turns run ``claude -p --output-format stream-json`` and translate its events
(see :func:`translate`).
"""

from __future__ import annotations

import json
import logging
import os
import pty
import re
import select
import signal
import subprocess
import threading
import time
from typing import Callable, Dict, List, Optional

log = logging.getLogger("mcode.claude")

_URL = re.compile(r"https://(?:claude\.ai|console\.anthropic\.com|platform\.claude\.com)/\S+?"
                  r"(?=[\s\x07\x1b\]]|$)")
_ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[ -/]*[@-~]")


class ClaudeUnavailable(Exception):
    pass


def clean_terminal(text: str) -> str:
    return _ANSI.sub("", text).replace("\r", "")


def auth_summary(data: dict, environ: Optional[dict] = None) -> dict:
    """Only what mCode may show: never e-mail, organisation, ids or tokens."""
    env = os.environ if environ is None else environ
    method = data.get("authMethod") or "none"
    logged = bool(data.get("loggedIn"))
    plan = data.get("subscriptionType") or data.get("planTier") or ""
    summary = {"logged_in": logged, "method": method, "plan": plan,
               "expires": data.get("loginExpiresAt") or "", "provider": data.get("apiProvider", "")}
    if env.get("ANTHROPIC_API_KEY"):
        summary["api_key_env"] = True
        summary["note"] = ("ANTHROPIC_API_KEY is set: Claude Code bills that API key, not your "
                           "Claude subscription.")
    if logged and method in ("claude.ai", "claudeai", "oauth"):
        summary["label"] = f"Claude {plan.capitalize()}" if plan else "Claude account"
    elif logged:
        summary["label"] = {"apiKey": "API key", "console": "Anthropic Console"}.get(method, method)
    else:
        summary["label"] = "Not signed in"
    return summary


class LoginSession:
    """``claude auth login`` in a pseudo-terminal: the link to open and the code to paste."""

    def __init__(self, argv: List[str], env: Dict[str, str],
                 on_change: Callable[["LoginSession"], None] = lambda s: None):
        self.argv = argv
        self.env = env
        self.on_change = on_change
        self.url = ""
        self.state = "starting"         # starting | waiting | checking | done | failed | cancelled
        self.error = ""
        self.output = ""
        self._pid = 0
        self._fd = -1
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.started = time.time()

    def start(self) -> "LoginSession":
        pid, fd = pty.fork()
        if pid == 0:                    # child
            try:
                os.execvpe(self.argv[0], self.argv, self.env)
            finally:
                os._exit(127)
        self._pid, self._fd = pid, fd
        self._thread = threading.Thread(target=self._read, name="claude-login", daemon=True)
        self._thread.start()
        return self

    def _read(self) -> None:
        deadline = time.time() + 900
        while time.time() < deadline:
            try:
                ready, _, _ = select.select([self._fd], [], [], 1.0)
            except (OSError, ValueError):
                break
            if not ready:
                if self._exited():
                    break
                continue
            try:
                data = os.read(self._fd, 4096)
            except OSError:
                break
            if not data:
                break
            text = data.decode("utf-8", "replace")
            with self._lock:
                self.output = (self.output + text)[-20000:]
                if not self.url:
                    found = _URL.search(self.output)
                    if found:
                        self.url = found.group(0).rstrip(".,)")
                        self.state = "waiting"
                        self.on_change(self)
        code = self._wait()
        with self._lock:
            if self.state == "cancelled":
                pass
            elif code == 0:
                self.state = "done"
            else:
                self.state = "failed"
                tail = clean_terminal(self.output).strip().splitlines()
                self.error = (tail[-1] if tail else "") or f"claude auth login exited with {code}"
                # Never surface a URL with its query in an error.
                self.error = re.sub(r"\?\S+", "?…", self.error)
        try:
            os.close(self._fd)
        except OSError:
            pass
        self.on_change(self)

    def _exited(self) -> bool:
        try:
            pid, _status = os.waitpid(self._pid, os.WNOHANG)
            if pid:
                self._status = _status
                return True
        except ChildProcessError:
            return True
        return False

    def _wait(self) -> int:
        status = getattr(self, "_status", None)
        if status is None:
            try:
                _pid, status = os.waitpid(self._pid, 0)
            except ChildProcessError:
                return 1
        return os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") \
            else (status >> 8)

    def submit_code(self, code: str) -> None:
        code = (code or "").strip()
        if not code or len(code) > 2000 or "\n" in code:
            raise ValueError("Paste the code shown after signing in.")
        if self.state != "waiting":
            raise ValueError("The sign-in is not waiting for a code.")
        os.write(self._fd, code.encode() + b"\r")
        self.state = "checking"
        self.on_change(self)

    def cancel(self) -> None:
        self.state = "cancelled"
        try:
            os.kill(self._pid, signal.SIGTERM)
        except OSError:
            pass

    def to_dict(self) -> dict:
        return {"state": self.state, "url": self.url, "error": self.error,
                "started": self.started}


class ClaudeCli:
    def __init__(self, path_lookup: Callable[[], str], environ: Optional[dict] = None):
        self.path_lookup = path_lookup
        self.environ = environ

    def path(self) -> str:
        path = self.path_lookup()
        if not path:
            raise ClaudeUnavailable("Claude Code is not installed.")
        return path

    def env(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        env = dict(os.environ if self.environ is None else self.environ)
        env.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
        env.pop("CLAUDECODE", None)              # not a nested session of this process
        env.pop("CLAUDE_CODE_ENTRYPOINT", None)
        env.update(extra or {})
        return env

    def status(self, timeout: float = 20.0) -> dict:
        path = self.path()
        try:
            result = subprocess.run([path, "auth", "status", "--json"], capture_output=True,
                                    text=True, timeout=timeout, env=self.env(),
                                    stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return {"logged_in": False, "method": "unknown", "label": "Not checked",
                    "error": "claude auth status did not answer"}
        except OSError as exc:
            raise ClaudeUnavailable(f"Claude Code does not run: {exc.strerror}") from None
        try:
            data = json.loads(result.stdout or "{}")
        except ValueError:
            return {"logged_in": False, "method": "unknown", "label": "Unknown",
                    "error": "unexpected answer from claude auth status"}
        return auth_summary(data, self.env())

    def login(self, on_change: Callable[[LoginSession], None], console: bool = False) -> LoginSession:
        argv = [self.path(), "auth", "login", "--console" if console else "--claudeai"]
        env = self.env({"BROWSER": "/bin/false"} if not os.environ.get("DISPLAY") else {})
        return LoginSession(argv, env, on_change).start()

    def logout(self, timeout: float = 20.0) -> bool:
        result = subprocess.run([self.path(), "auth", "logout"], capture_output=True, text=True,
                                timeout=timeout, env=self.env(), stdin=subprocess.DEVNULL)
        return result.returncode == 0


# ------------------------------------------------------------ stream events

TOOL_WORDS = {"Read": "Reading", "Edit": "Editing", "MultiEdit": "Editing", "Write": "Writing",
              "Glob": "Looking for files", "Grep": "Searching the code", "Bash": "Running",
              "WebSearch": "Searching the web", "WebFetch": "Reading a web page",
              "TodoWrite": "Planning", "NotebookEdit": "Editing"}
MCODE_WORDS = {"build": "Building the firmware", "run_tests": "Running the tests",
               "static_check": "Checking the code", "project_info": "Reading the project",
               "last_build": "Reading the build log", "serial_log": "Reading serial output",
               "list_devices": "Checking connected boards", "clean": "Cleaning the build",
               "git_status": "Checking git", "git_diff": "Reading the changes",
               "git_log": "Reading the history", "git_show": "Reading a commit",
               "git_commit": "Committing"}


def describe_tool(name: str, data: dict, root: str = "") -> str:
    if name.startswith("mcp__mcode__"):
        return MCODE_WORDS.get(name[len("mcp__mcode__"):], name[len("mcp__mcode__"):])
    word = TOOL_WORDS.get(name, name)
    target = data.get("file_path") or data.get("path") or data.get("pattern") or \
        data.get("command") or data.get("query") or data.get("url") or ""
    if isinstance(target, str) and root and target.startswith(root.rstrip("/") + "/"):
        target = target[len(root.rstrip("/")) + 1:]
    if name == "Bash":
        target = (data.get("description") or target)[:60]
    return f"{word} {target}".strip() if target and name != "TodoWrite" else word


def translate(event: dict, root: str = "") -> List[dict]:
    """stream-json event -> mCode AI events (init, text, tool, tool_result, result)."""
    kind = event.get("type")
    out: List[dict] = []
    if kind == "system" and event.get("subtype") == "init":
        out.append({"kind": "init", "session_id": event.get("session_id", ""),
                    "model": event.get("model", ""), "mode": event.get("permissionMode", ""),
                    "api_key_source": event.get("apiKeySource", "")})
    elif kind == "assistant":
        for block in (event.get("message") or {}).get("content") or []:
            if block.get("type") == "text" and block.get("text", "").strip():
                out.append({"kind": "text", "text": block["text"]})
            elif block.get("type") == "tool_use":
                data = block.get("input") or {}
                out.append({"kind": "tool", "id": block.get("id", ""), "name": block.get("name"),
                            "label": describe_tool(block.get("name", ""), data, root),
                            "file": _rel(data.get("file_path", ""), root)})
    elif kind == "user":
        content = (event.get("message") or {}).get("content") or []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    out.append({"kind": "tool_result", "id": block.get("tool_use_id", ""),
                                "error": bool(block.get("is_error"))})
    elif kind == "result":
        out.append({"kind": "result", "ok": not event.get("is_error") and
                    event.get("subtype") == "success",
                    "subtype": event.get("subtype", ""), "text": event.get("result") or "",
                    "turns": event.get("num_turns", 0), "duration_ms": event.get("duration_ms", 0),
                    "cost_usd": event.get("total_cost_usd"), "session_id": event.get("session_id"),
                    "errors": [str(e) for e in event.get("errors") or []][:5],
                    "denials": [_denial(d, root) for d in event.get("permission_denials") or []]})
    return out


def _rel(path: str, root: str) -> str:
    if path and root and path.startswith(root.rstrip("/") + "/"):
        return path[len(root.rstrip("/")) + 1:]
    return path


def _denial(item: dict, root: str) -> str:
    name = item.get("tool_name", "")
    data = item.get("tool_input") or {}
    return describe_tool(name, data, root)
