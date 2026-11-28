"""The AI Engineer: Claude Code working on the current project.

One request ("turn") at a time. A turn:

1. checks Claude Code is installed and signed in;
2. prepares the workspace for the mode (staging copy, or a snapshot plus a
   backup);
3. builds the prompt from mCode's context and the request;
4. runs ``claude -p`` as a job (cancellable, time-limited, logged), with
   restricted tools for the mode and mCode's MCP tools;
5. turns the stream into activity ("Editing src/main.cpp", "Building the
   firmware") for both interfaces;
6. ends in **review** (changes waiting for approval, or risky changes held),
   **done** (with Undo for in-place changes) or **error**.

Modes (``ai.mode``): Manual (``ask``) and Plan (``plan``) run on a staging
copy; Plan has no editing tools at all and ends with a plan, never a change.
Auto (``assisted``) and Auto + git (``advanced``) edit the project in place.

While a turn runs, its ordered steps (text, thinking, each tool with its
state) and token usage are published so the interfaces can show what is
running; the plan's usage limits that Claude Code reports are kept in
``ai/usage.json`` (numbers and states only).

Every request, tool use, change, approval, rejection and undo goes to the AI
action log (``logs/ai-actions.jsonl``, redacted).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import threading
import time
import uuid
from typing import Dict, List, Optional

from ..core import storage
from ..core.config import AI_MODE_SHORT, AI_MODES
from ..core.history import History
from ..core.redact import redact
from ..core.jobs import Job, JobCancelled, JobFailed, JobSpec
from ..projects.model import Project, ProjectError
from ..toolchains import detect
from . import context
from .bridge import MODE_TOOLS, ToolBridge
from .claude import MODEL_CHOICES, TOOL_DETAIL_CHARS, ClaudeCli, ClaudeUnavailable, \
    LoginSession, translate
from .workspace import FileChange, Workspace

log = logging.getLogger("mcode.ai")

MCP_SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")
# No shell in any mode: Advanced mode's git is mCode's (bridge.py git_* tools), which never
# runs programs named in the repository's configuration (gitutil).
BUILTIN_TOOLS = {"ask": "Read,Edit,Write,Glob,Grep", "assisted": "Read,Edit,Write,Glob,Grep",
                 "advanced": "Read,Edit,Write,Glob,Grep", "plan": "Read,Glob,Grep"}
STAGED_MODES = ("ask", "plan")          # Claude works on the staging copy
CONVERSATION_MAX = 80
STEPS_SENT = 30                         # steps in each progress event
STEP_TEXT_CHARS = 1500
THINKING_CHARS = 600
STEPS_KEPT = 40                         # tool and thinking steps kept with an answer


class AIError(Exception):
    def __init__(self, message: str, fix: str = ""):
        super().__init__(message)
        self.fix = fix


class Turn:
    def __init__(self, project: Project, request: str, mode: str, kind: str, source: str):
        self.id = uuid.uuid4().hex[:10]
        self.project_id = project.id
        self.project_name = project.name
        self.request = request
        self.mode = mode
        self.kind = kind
        self.source = source
        self.state = "working"          # working | review | done | error | cancelled
        self.phase = "Starting Claude Code"
        self.activities: List[dict] = []
        self.texts: List[str] = []
        self.result: dict = {}
        self.error = ""
        self.started = time.time()
        self.ended = 0.0
        self.job_id = ""
        self.changes: List[dict] = []    # all files changed by the turn
        self.held: List[dict] = []       # changes waiting for an explicit decision
        self.applied: List[str] = []     # in-place changes still in the project (Undo)
        self.session_id = ""
        self.rebuild_after = kind == "fix-build"
        self.notices: List[str] = []    # what mCode did to protect the project
        self.steps: List[dict] = []     # text, thinking and tools, in order (tools shared
        #                                 with activities)
        self.model = ""
        self.context_tokens = 0         # what the model read last: the conversation's size
        self.context_window = 0
        self._usage: Dict[str, dict] = {}   # per message id

    @property
    def slot(self) -> str:
        """Which Claude conversation of the project the turn continues."""
        return "stage" if self.mode in STAGED_MODES else "project"

    def add_usage(self, item: dict) -> None:
        key = item.get("message_id") or f"m{len(self._usage)}"
        self._usage[key] = item
        self.model = item.get("model") or self.model
        if item.get("context"):
            self.context_tokens = item["context"]

    def usage(self) -> dict:
        """Token totals: Claude Code's own for a finished turn, else the messages so far."""
        final = self.result.get("usage") if self.result else None
        if final:
            return dict(final)
        total = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        for item in list(self._usage.values()):     # the job thread may add one
            for key in total:
                total[key] += item.get(key, 0)
        total["context"] = self.context_tokens
        return total

    def wire_steps(self) -> List[dict]:
        out = []
        for step in self.steps[-STEPS_SENT:]:
            if step["type"] == "text":
                text = step["text"]
                out.append({"type": "text", "text": text if len(text) <= STEP_TEXT_CHARS
                            else "…" + text[-STEP_TEXT_CHARS:]})
            elif step["type"] == "thinking":
                out.append({"type": "thinking", "text": step["text"][:THINKING_CHARS]})
            else:
                out.append(dict(step))
        return out

    def kept_steps(self) -> List[dict]:
        """What the conversation keeps with the answer: tools and thinking, not the text."""
        out = []
        for step in self.steps:
            if step["type"] == "tool":
                out.append({"type": "tool", "label": step["label"], "state": step.get("state", ""),
                            "detail": step.get("detail", "")})
            elif step["type"] == "thinking":
                out.append({"type": "thinking", "text": step["text"][:THINKING_CHARS]})
        return out[-STEPS_KEPT:]

    @property
    def text(self) -> str:
        final = self.result.get("text") or ""
        return final or "\n\n".join(self.texts)

    @property
    def summary(self) -> str:
        return context.summary_line(self.text) if self.text else ""

    def to_dict(self) -> dict:
        return {"id": self.id, "project_id": self.project_id, "project": self.project_name,
                "request": self.request, "mode": self.mode, "kind": self.kind,
                "source": self.source, "state": self.state, "phase": self.phase,
                "activities": self.activities[-30:], "text": context.strip_summary(self.text),
                "summary": self.summary, "error": self.error, "started": self.started,
                "ended": self.ended, "elapsed": round((self.ended or time.time()) - self.started, 1),
                "changes": self.changes, "held": self.held, "applied": self.applied,
                "cost_usd": self.result.get("cost_usd"), "turns": self.result.get("turns"),
                "denials": self.result.get("denials", []), "job_id": self.job_id,
                "rebuild_after": self.rebuild_after, "notices": self.notices,
                "steps": self.wire_steps(), "model": self.model, "usage": self.usage(),
                "context": self.context_tokens, "context_window": self.context_window,
                "slot": self.slot}


class ProjectAI:
    """Conversation, Claude sessions and the pending review of one project."""

    def __init__(self, folder: str):
        self.folder = folder
        self.path = os.path.join(folder, "state.json")
        data = storage.read_json(self.path, {}, dict)
        self.sessions: Dict[str, str] = data.get("sessions", {})
        self.conversation: List[dict] = data.get("conversation", [])
        self.review: Optional[dict] = data.get("review")
        self.last_turn: Optional[dict] = data.get("last_turn")
        # per conversation slot: {"tokens", "window", "model", "time"}
        context = data.get("context")
        self.context: Dict[str, dict] = context if isinstance(context, dict) else {}

    def save(self) -> None:
        os.makedirs(self.folder, mode=0o700, exist_ok=True)
        storage.atomic_write_json(self.path, {
            "sessions": self.sessions, "conversation": self.conversation[-CONVERSATION_MAX:],
            "review": self.review, "last_turn": self.last_turn, "context": self.context})

    def add(self, role: str, text: str, **extra) -> dict:
        entry = {"role": role, "text": text, "time": time.time()}
        entry.update(extra)
        self.conversation.append(entry)
        self.conversation = self.conversation[-CONVERSATION_MAX:]
        return entry


class AIService:
    def __init__(self, studio):
        self.studio = studio
        self.cli = ClaudeCli(lambda: studio.tools.path(detect.CLAUDE))
        self.bridge = ToolBridge(studio, socket_path(studio.paths.run, "ai-bridge.sock"))
        self.log = History(studio.paths.ai_log)
        self._lock = threading.RLock()
        self._auth: dict = {"logged_in": False, "label": "Not checked", "checked": 0.0}
        self._checking = False
        self.login_session: Optional[LoginSession] = None
        self.code_drop = None               # the sign-in code page (web/codedrop.py)
        self.turn: Optional[Turn] = None
        self._projects: Dict[str, ProjectAI] = {}
        self._bridge_started = False
        # What Claude Code last reported about the plan's limits, the model it used and the
        # models' context windows (ai/usage.json: numbers and states only).
        self._usage_path = os.path.join(studio.paths.data, "ai", "usage.json")
        saved = storage.read_json(self._usage_path, {}, dict)
        self.limits: dict = saved.get("limits") if isinstance(saved.get("limits"), dict) else {}
        self.last_model = str(saved.get("last_model") or "")[:100]
        windows = saved.get("windows")
        self.windows: Dict[str, int] = {str(k)[:100]: v for k, v in (
            windows.items() if isinstance(windows, dict) else []) if isinstance(v, int)}

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        try:
            self.bridge.start()
            self._bridge_started = True
        except OSError as exc:
            log.warning("AI tool bridge unavailable: %s", exc)
        self.refresh_status()

    def stop(self) -> None:
        turn = self.turn
        if turn is not None and turn.state == "working" and turn.job_id:
            self.studio.jobs.cancel(turn.job_id)
        if self.login_session is not None and self.login_session.state in ("starting", "waiting"):
            self.login_session.cancel()
        self._close_code_drop()
        if self._bridge_started:
            self.bridge.stop()

    def _publish(self, what: str, **data) -> None:
        payload = {"what": what, "status": self.status()}
        payload.update(data)
        self.studio.events.publish("ai", payload)

    def project_ai(self, project_id: str) -> ProjectAI:
        with self._lock:
            if project_id not in self._projects:
                folder = os.path.join(self.studio.paths.data, "ai", project_id)
                self._projects[project_id] = ProjectAI(folder)
            return self._projects[project_id]

    # ---------------------------------------------------------------- status
    def status(self) -> dict:
        tool = self.studio.tools.get(detect.CLAUDE)
        auth = dict(self._auth)
        mode = self.studio.settings.get("ai.mode")
        turn = self.turn
        if not tool.found:
            state = "not-installed"
        elif not auth.get("logged_in"):
            state = "signed-out" if auth.get("checked") else "checking"
        elif turn is not None and turn.state == "working":
            state = "working"
        else:
            state = "ready"
        machine = detect.machine()
        warning = ""
        if machine.get("memory_mb") and machine["memory_mb"] < 1000:
            warning = (f"This board has {machine['memory_mb']} MB of memory. Claude Code needs "
                       "several hundred MB and may be slow or stop; a Raspberry Pi 4/5 or a "
                       "computer works better.")
        return {"state": state, "installed": tool.found, "version": tool.version,
                "source": tool.source, "problem": tool.problem if not tool.found else "",
                "hint": tool.hint if not tool.found else "", "auth": auth, "mode": mode,
                "mode_label": AI_MODES.get(mode, mode), "mode_short": AI_MODE_SHORT.get(mode, mode),
                "modes": [{"id": key, "label": label, "short": AI_MODE_SHORT[key]}
                          for key, label in AI_MODES.items()],
                "model": self.studio.settings.get("ai.model"),
                "models": [{"id": key, "label": label} for key, label in MODEL_CHOICES],
                "last_model": self.last_model, "limits": self.limits,
                "memory_warning": warning, "login": self._login_dict(),
                "turn": turn.to_dict() if turn else None}

    def refresh_status(self, wait: bool = False) -> dict:
        def work():
            try:
                auth = self.cli.status()
            except ClaudeUnavailable as exc:
                installed = self.studio.tools.get(detect.CLAUDE).found
                auth = {"logged_in": False, "label": "Does not run" if installed else "Not installed",
                        "error": str(exc)}
            auth["checked"] = time.time()
            with self._lock:
                self._auth = auth
                self._checking = False
            self._publish("status")
        with self._lock:
            if self._checking and not wait:
                return self.status()
            self._checking = True
        if wait:
            work()
        else:
            threading.Thread(target=work, name="claude-status", daemon=True).start()
        return self.status()

    # ----------------------------------------------------------------- login
    def login_start(self, console: bool = False) -> dict:
        if self.login_session is not None and self.login_session.state in ("starting", "waiting",
                                                                            "checking"):
            return self.login_session.to_dict()

        def changed(session: LoginSession) -> None:
            if session is not self.login_session and self.login_session is not None:
                return                          # an older sign-in finishing
            if session.state == "waiting":
                self._open_code_drop()
            else:
                self._close_code_drop()
            if session.state == "done":
                self.log.add("login", ok=True)
                self.refresh_status(wait=True)
            elif session.state == "failed":
                self.log.add("login", ok=False, error=session.error)
            self._publish("login")

        try:
            self.login_session = self.cli.login(changed, console=console)
        except ClaudeUnavailable as exc:
            raise AIError(str(exc), fix="install:claude") from None
        self._publish("login")
        return self.login_session.to_dict()

    def login_code(self, code: str) -> dict:
        if self.login_session is None:
            raise AIError("Start signing in first.")
        try:
            self.login_session.submit_code(code)
        except ValueError as exc:
            raise AIError(str(exc)) from None
        return self.login_session.to_dict()

    def login_cancel(self) -> None:
        self._close_code_drop()
        if self.login_session is not None:
            self.login_session.cancel()
            self._publish("login")

    def _login_dict(self) -> Optional[dict]:
        if self.login_session is None:
            return None
        data = self.login_session.to_dict()
        drop = self.code_drop
        data["code_url"] = drop.url() if drop is not None and self.login_session.state == \
            "waiting" else ""
        return data

    # The sign-in code page: lets a phone on the same network paste Claude's
    # login code while (and only while) the sign-in waits for it.
    def _open_code_drop(self) -> None:
        from ..web.codedrop import CodeDrop
        with self._lock:
            if self.code_drop is not None or not self.studio.settings.get("ai.login_code_page"):
                return
            drop = CodeDrop(self._code_from_phone)
            if not drop.start():
                return
            self.code_drop = drop

    def _close_code_drop(self) -> None:
        with self._lock:
            drop, self.code_drop = self.code_drop, None
        if drop is not None:
            drop.stop()

    def _code_from_phone(self, code: str) -> Optional[str]:
        try:
            self.login_code(code)
        except AIError as exc:
            return str(exc)
        self.log.add("login-code", source="phone")
        return None

    def logout(self) -> bool:
        try:
            ok = self.cli.logout()
        except (ClaudeUnavailable, OSError) as exc:
            raise AIError(str(exc)) from None
        self.log.add("logout", ok=ok)
        self.refresh_status(wait=True)
        return ok

    # ------------------------------------------------------------------ asking
    def ask(self, project: Project, request: str, kind: str = "chat",
            attachments: Optional[List[str]] = None, include_serial: bool = False,
            source: str = "user", mode: Optional[str] = None) -> Turn:
        request = (request or "").strip()
        if not request and kind == "chat":
            raise AIError("Say or type what you want Claude to do.")
        if len(request) > 20000:
            raise AIError("That request is too long.")
        status = self.status()
        if not status["installed"]:
            raise AIError("Claude Code is not installed.", fix="install:claude")
        if not status["auth"].get("logged_in"):
            self.refresh_status(wait=True)
            if not self._auth.get("logged_in"):
                raise AIError("Claude Code is not signed in. Connect your Claude account first.",
                              fix="login")
        with self._lock:
            if self.turn is not None and self.turn.state == "working":
                raise AIError("Claude is still working on the last request.")
            pai = self.project_ai(project.id)
            if pai.review:
                raise AIError("Approve or discard Claude's previous changes first.", fix="review")
            mode = mode or self.studio.settings.get("ai.mode")
            if mode not in AI_MODES:
                mode = "ask"
            turn = Turn(project, request or context.KINDS.get(kind, kind), mode, kind, source)
            self.turn = turn
        workspace = self._workspace(project)
        try:
            if mode in STAGED_MODES:
                workdir = workspace.prepare_stage()
            else:
                workspace.snapshot()
                self.studio.projects.backup(project, "before-ai", note=request[:200])
                workdir = project.path
        except (OSError, ProjectError) as exc:
            with self._lock:
                self.turn = None
            raise AIError(f"Could not prepare the project for Claude: {exc}") from None
        device = self.studio.devices.selected(project) if self.studio.devices else None
        serial_text = ""
        if include_serial or kind == "analyze-serial":
            serial_text = self.studio.monitor.text(300, timestamps=True) if self.studio.monitor else ""
        prompt = context.build_prompt(
            project, request, mode=mode, kind=kind,
            device=device.to_dict() if device else None,
            last_build=self.studio.builds.last(project.id), serial_text=serial_text,
            attachments=attachments, workspace_root=workdir)
        pai.add("user", request or context.KINDS.get(kind, ""), kind=kind, source=source,
                turn=turn.id, mode=mode)
        pai.save()
        self.log.add("request", project_id=project.id, project=project.name, mode=mode,
                     request_kind=kind,
                     source=source, text=request, turn=turn.id,
                     attachments=list(attachments or []), serial=bool(serial_text))

        def work(job: Job) -> dict:
            return self._run(job, turn, project, workdir, prompt)

        timeout = float(self.studio.settings.get("ai.turn_timeout_s"))
        job = self.studio.jobs.submit(JobSpec(
            kind="ai", title=f"Claude: {request[:60] or kind}", func=work, lane="ai",
            timeout=timeout + 60, project_id=project.id,
            parser=lambda job, line: self._on_line(turn, line, workdir),
            on_done=lambda job: self._finished(job, turn, project),
            meta={"project": project.name, "mode": mode, "source": source}))
        turn.job_id = job.id
        self._publish("turn", turn=turn.to_dict())
        return turn

    def fix_build(self, project: Project, source: str = "user") -> Turn:
        last = self.studio.builds.last(project.id)
        if not last or last.get("state") == "succeeded":
            raise AIError("There is no failed build to fix.")
        return self.ask(project, "Fix the build errors.", kind="fix-build", source=source)

    def analyze_serial(self, project: Project, request: str = "", source: str = "user") -> Turn:
        monitor = self.studio.monitor
        if monitor is None or not monitor.lines:
            raise AIError("The serial monitor has no output to analyse yet.")
        return self.ask(project, request or "What is happening on the board?",
                        kind="analyze-serial", source=source)

    def cancel(self) -> bool:
        turn = self.turn
        if turn is None or turn.state != "working":
            return False
        self.log.add("cancel", project_id=turn.project_id, turn=turn.id)
        return self.studio.jobs.cancel(turn.job_id)

    def _workspace(self, project: Project) -> Workspace:
        return Workspace(os.path.join(self.studio.paths.workspaces, project.id), project.path,
                         hold_build_scripts=bool(self.studio.settings.get("ai.hold_build_scripts")))

    # --------------------------------------------------------------- running
    def _argv(self, turn: Turn, prompt: str, session_args: List[str], bridge_token: str) -> List[str]:
        claude = self.cli.path()
        mode = turn.mode
        mcp = {"mcpServers": {"mcode": {
            "command": sys.executable, "args": ["-I", MCP_SERVER],
            "env": {"MCODE_BRIDGE": self.bridge.socket_path, "MCODE_TURN": bridge_token,
                    "MCODE_TOOL_TIMEOUT": str(int(self.studio.settings.get("dev.build_timeout_s")) + 120)}}}}
        tools = BUILTIN_TOOLS[mode]
        allowed = [f"mcp__mcode__{name}" for name in MODE_TOOLS[mode]]
        if mode == "advanced":
            if self.studio.settings.get("ai.allow_web"):
                tools += ",WebSearch,WebFetch"
                allowed += ["WebSearch", "WebFetch"]
        argv = [claude, "-p", "--output-format", "stream-json", "--verbose", "--restricted",
                "--tools", tools, "--permission-mode", "acceptEdits", "--permission-prompts",
                "none", "--strict-mcp-config", "--mcp-config", json.dumps(mcp),
                "--allowedTools", ",".join(allowed),
                "--append-system-prompt", context.SYSTEM_RULES]
        model = self.studio.settings.get("ai.model")
        if model:
            argv += ["--model", model]
        return argv + session_args + ["--", prompt]

    def _run(self, job: Job, turn: Turn, project: Project, workdir: str, prompt: str) -> dict:
        pai = self.project_ai(project.id)
        slot = turn.slot
        workspace = self._workspace(project)
        # In place, the turn's own risky edits must not reach a build before the user
        # decides (the hold rule otherwise only runs after the turn).
        guard = workspace.pending_reason if turn.mode not in STAGED_MODES else None
        token = self.bridge.open_turn(project.id, turn.mode,
                                      on_tool=lambda tool: self._tool_called(turn, tool),
                                      guard=guard)
        timeout = float(self.studio.settings.get("ai.turn_timeout_s"))
        env = self.cli.env({"MCP_TOOL_TIMEOUT": str(int(self.studio.settings.get(
            "dev.build_timeout_s")) * 1000 + 120000), "MCP_TIMEOUT": "30000"})
        try:
            for attempt in (1, 2):
                existing = pai.sessions.get(slot)
                if existing and attempt == 1:
                    session_args = ["--resume", existing]
                else:
                    existing = str(uuid.uuid4())
                    session_args = ["--session-id", existing]
                turn.session_id = existing
                job.set_phase("Claude is working")
                code = job.run(self._argv(turn, prompt, session_args, token), cwd=workdir,
                               env=env, timeout=timeout, check=False, show=False,
                               what="Claude Code")
                # Claude Code cannot find the conversation to continue: before 2.1 it
                # only exited; 2.1 also reports a result "error_during_execution"
                # with the reason in "errors" (checked with 2.1.332). Either way:
                # start a new conversation once instead of failing every request.
                lost = (not turn.result or not turn.result.get("ok")) and \
                    _no_session(job.output() + "\n" + "\n".join(turn.result.get("errors", [])))
                if attempt == 1 and "--resume" in session_args and lost:
                    job.say("mcode: the previous conversation is gone; starting a new one")
                    log.info("%s: Claude conversation %s not found; starting a new one",
                             project.name, existing)
                    pai.sessions.pop(slot, None)
                    pai.save()
                    turn.result = {}
                    continue
                break
            # Remember the conversation only when Claude really had one: a session
            # that failed before its first turn has no transcript to resume.
            if turn.result.get("session_id") and (turn.result.get("ok") or
                                                  turn.result.get("turns")):
                pai.sessions[slot] = turn.result["session_id"]
                pai.save()
            if not turn.result:
                tail = [l for l in job.tail(30) if not l.startswith("{")]
                raise JobFailed(_explain_failure("\n".join(tail)) or
                                f"Claude Code stopped (exit code {code})")
            if not turn.result.get("ok"):
                log.warning("%s: Claude Code finished with %s after %s turn(s): %s", project.name,
                            turn.result.get("subtype") or "an error", turn.result.get("turns"),
                            redact("; ".join(turn.result.get("errors") or [])
                                   or turn.result.get("text") or "")[:500])
                raise JobFailed(_result_failure(turn.result, job))
            return {"ok": True}
        finally:
            self.bridge.close_turn(token)

    def _on_line(self, turn: Turn, line: str, root: str) -> None:
        if not line.startswith("{"):
            return
        try:
            event = json.loads(line)
        except ValueError:
            return
        for item in translate(event, root):
            kind = item["kind"]
            if kind == "text":
                turn.texts.append(item["text"])
                turn.steps.append({"type": "text", "text": item["text"]})
                turn.phase = "Writing the answer"
            elif kind == "thinking":
                turn.steps.append({"type": "thinking", "text": item["text"][:THINKING_CHARS * 4]})
                turn.phase = "Thinking"
            elif kind == "tool":
                entry = {"type": "tool", "label": item["label"], "name": item["name"],
                         "file": item.get("file", ""), "time": time.time(),
                         "id": item.get("id", ""), "error": False, "state": "running",
                         "ended": 0.0, "detail": ""}
                turn.activities.append(entry)
                turn.steps.append(entry)
                turn.phase = item["label"]
                self.log.add("tool", project_id=turn.project_id, turn=turn.id, tool=item["name"],
                             label=item["label"])
            elif kind == "tool_result":
                for entry in reversed(turn.activities):
                    if entry.get("id") == item["id"]:
                        entry["error"] = item["error"]
                        entry["state"] = "error" if item["error"] else "done"
                        entry["ended"] = time.time()
                        # mCode's own tools say what happened (build result); other tools'
                        # output (file contents) stays out of the events.
                        if item["error"] or str(entry.get("name", "")).startswith("mcp__mcode__"):
                            entry["detail"] = redact(item.get("text") or "")[:TOOL_DETAIL_CHARS]
                        break
            elif kind == "usage":
                turn.add_usage(item)
                if not turn.context_window and turn.model in self.windows:
                    turn.context_window = self.windows[turn.model]
            elif kind == "limits":
                self._limits_reported(item)
            elif kind == "result":
                turn.result = item
                for name, data in (item.get("models") or {}).items():
                    if data.get("context_window"):
                        self.windows[name] = data["context_window"]
                if turn.model in self.windows:
                    turn.context_window = self.windows[turn.model]
            elif kind == "init":
                turn.session_id = item.get("session_id") or turn.session_id
                turn.model = item.get("model") or turn.model
                if turn.model in self.windows:
                    turn.context_window = self.windows[turn.model]
        self._publish("progress", turn=turn.to_dict())

    def _limits_reported(self, item: dict) -> None:
        limits = {k: v for k, v in item.items() if k != "kind"}
        changed = limits != {k: v for k, v in self.limits.items() if k != "time"}
        limits["time"] = time.time()
        self.limits = limits
        if changed:
            self._publish("usage")

    def _save_usage(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._usage_path), mode=0o700, exist_ok=True)
            storage.atomic_write_json(self._usage_path, {
                "limits": self.limits, "last_model": self.last_model,
                "windows": dict(list(self.windows.items())[-20:])})
        except OSError as exc:
            log.warning("could not save AI usage: %s", exc)

    def _tool_called(self, turn: Turn, tool: str) -> None:
        turn.phase = {"build": "Building the firmware", "run_tests": "Running the tests",
                      "static_check": "Checking the code"}.get(tool, turn.phase)
        self._publish("progress", turn=turn.to_dict())

    def _finished(self, job: Job, turn: Turn, project: Project) -> None:
        pai = self.project_ai(project.id)
        workspace = self._workspace(project)
        turn.ended = time.time()
        try:
            if turn.mode in STAGED_MODES:
                changes = workspace.staged_changes() if job.state in ("succeeded",) else []
                if turn.mode == "plan" and changes:
                    # Plan mode has no editing tool; should a file differ anyway, the plan
                    # still changes nothing (the staging copy is prepared again next time).
                    turn.notices.append(f"Plan mode changes nothing: mCode left out "
                                        f"{len(changes)} file(s) that differed in Claude's copy.")
                    log.warning("%s: plan turn %s left %d changed file(s) in the stage",
                                project.name, turn.id, len(changes))
                    changes = []
                turn.changes = [c.to_dict() for c in changes]
                if changes:
                    pai.review = {"turn": turn.id, "mode": "ask", "created": time.time(),
                                  "paths": [c.path for c in changes],
                                  "held": [c.path for c in changes if c.risky],
                                  "rebuild_after": turn.rebuild_after,
                                  "summary": turn.summary}
            else:
                if workspace.restore_git():
                    turn.notices.append("Claude changed the project's git settings (.git); "
                                        "mCode put them back.")
                    log.warning("%s: AI turn %s changed .git settings; restored", project.name,
                                turn.id)
                changes = workspace.applied_changes()
                turn.changes = [c.to_dict() for c in changes]
                held = [c for c in changes if c.risky]
                if held:
                    self._hold(workspace, held)
                    pai.review = {"turn": turn.id, "mode": turn.mode, "created": time.time(),
                                  "paths": [c.path for c in held], "held": [c.path for c in held],
                                  "rebuild_after": False, "summary": turn.summary}
                turn.held = [c.to_dict() for c in held]
                turn.applied = [c.path for c in changes if not c.risky]
        except Exception as exc:        # isolation boundary: still report the turn
            log.exception("comparing AI changes failed")
            turn.error = f"Could not compare the changes: {exc}"
        if job.state == "succeeded":
            turn.state = "review" if pai.review else "done"
        elif job.state == "cancelled":
            turn.state = "cancelled"
            turn.error = "Stopped."
        else:
            turn.state = "error"
            turn.error = turn.error or job.error or "Claude could not finish."
        text = turn.text
        if turn.model:
            self.last_model = turn.model
        if turn.context_tokens:
            pai.context[turn.slot] = {"tokens": turn.context_tokens,
                                      "window": turn.context_window, "model": turn.model,
                                      "time": turn.ended}
        pai.add("assistant", context.strip_summary(text) or turn.error, summary=turn.summary,
                turn=turn.id, state=turn.state, changes=[c["path"] for c in turn.changes],
                activities=[a["label"] for a in turn.activities][-20:],
                cost_usd=turn.result.get("cost_usd"), notices=turn.notices, mode=turn.mode,
                steps=turn.kept_steps(), model=turn.model, usage=turn.usage(),
                context=turn.context_tokens, context_window=turn.context_window,
                duration=round(turn.ended - turn.started, 1))
        pai.last_turn = turn.to_dict()
        pai.save()
        self._save_usage()
        self.log.add("result", project_id=project.id, turn=turn.id, state=turn.state,
                     notices=turn.notices,
                     summary=turn.summary, error=turn.error,
                     changes=[c["path"] for c in turn.changes],
                     held=[c["path"] for c in turn.held] if turn.held else
                     (pai.review or {}).get("held", []),
                     cost_usd=turn.result.get("cost_usd"), turns=turn.result.get("turns"),
                     denials=turn.result.get("denials", []))
        self._publish("turn", turn=turn.to_dict())

    def _hold(self, workspace: Workspace, held: List[FileChange]) -> None:
        """Move risky in-place edits out of the project until the user decides."""
        held_dir = os.path.join(workspace.root, "held")
        shutil.rmtree(held_dir, ignore_errors=True)
        for change in held:
            from .workspace import copy_file
            copy_file(workspace.project_path, held_dir, change.path)
        workspace.revert([c.path for c in held])

    # ------------------------------------------------------------- reviewing
    def review(self, project: Project) -> Optional[dict]:
        pai = self.project_ai(project.id)
        if not pai.review:
            return None
        workspace = self._workspace(project)
        source = workspace.stage if pai.review["mode"] == "ask" else \
            os.path.join(workspace.root, "held")
        from .workspace import compare
        changes = compare(project.path, source, pai.review["paths"],
                          build_scripts=workspace.hold_build_scripts)
        data = dict(pai.review)
        data["changes"] = [c.to_dict() for c in changes]
        return data

    def approve(self, project: Project, paths: Optional[List[str]] = None,
                rebuild: Optional[bool] = None) -> dict:
        pai = self.project_ai(project.id)
        review = pai.review
        if not review:
            raise AIError("There are no changes waiting.")
        chosen = [p for p in (paths if paths is not None else review["paths"])
                  if p in review["paths"]]
        workspace = self._workspace(project)
        self.studio.projects.backup(project, "before-ai-apply", note=review.get("summary", ""))
        if review["mode"] == "ask":
            applied = workspace.apply(chosen)
        else:
            held_dir = os.path.join(workspace.root, "held")
            from .workspace import copy_file
            applied = []
            for rel in chosen:
                copy_file(held_dir, project.path, rel)
                applied.append(rel)
        rejected = [p for p in review["paths"] if p not in chosen]
        pai.review = None
        pai.save()
        if self.turn is not None and self.turn.id == review.get("turn"):
            self.turn.state = "done"
            self.turn.applied = sorted(set(self.turn.applied + applied))
        self.log.add("approve", project_id=project.id, turn=review.get("turn"), applied=applied,
                     rejected=rejected)
        result = {"applied": applied, "rejected": rejected}
        if (review.get("rebuild_after") if rebuild is None else rebuild) and applied:
            try:
                job = self.studio.builds.run(project, "build", source="ai-fix")
                result["build_job"] = job.id
            except Exception as exc:
                result["build_error"] = str(exc)
        self._publish("review", result=result)
        return result

    def reject(self, project: Project) -> dict:
        pai = self.project_ai(project.id)
        review = pai.review
        if not review:
            raise AIError("There are no changes waiting.")
        pai.review = None
        pai.save()
        if self.turn is not None and self.turn.id == review.get("turn"):
            self.turn.state = "done"
        self.log.add("reject", project_id=project.id, turn=review.get("turn"),
                     paths=review["paths"])
        self._publish("review", result={"rejected": review["paths"]})
        return {"rejected": review["paths"]}

    def undo(self, project: Project) -> dict:
        """Put back the files the last in-place turn changed."""
        turn = self.turn if self.turn and self.turn.project_id == project.id else None
        pai = self.project_ai(project.id)
        last = turn.to_dict() if turn else (pai.last_turn or {})
        paths = list(last.get("applied") or [])
        if not paths:
            raise AIError("There is no AI change to undo.")
        self.studio.projects.backup(project, "before-ai-undo")
        reverted = self._workspace(project).revert(paths)
        if turn is not None:
            turn.applied = []
        if pai.last_turn:
            pai.last_turn["applied"] = []
            pai.save()
        self.log.add("undo", project_id=project.id, turn=last.get("id"), paths=reverted)
        self._publish("undo", paths=reverted)
        return {"reverted": reverted}

    def conversation(self, project: Project) -> List[dict]:
        return list(self.project_ai(project.id).conversation)

    def context_of(self, project: Project) -> Dict[str, dict]:
        """How full each of the project's Claude conversations is (per slot)."""
        return dict(self.project_ai(project.id).context)

    def clear_conversation(self, project: Project) -> None:
        pai = self.project_ai(project.id)
        pai.conversation = []
        pai.sessions = {}
        pai.context = {}
        pai.save()
        self.log.add("new-conversation", project_id=project.id)
        self._publish("conversation")

    def actions(self, project_id: Optional[str] = None, limit: int = 100) -> List[dict]:
        return self.log.recent(project_id=project_id, limit=limit)


def socket_path(folder: str, name: str) -> str:
    """A Unix socket path that fits the 108-byte limit: ``folder`` (Paths.run, which
    is outside the data folder) when short enough, else a private folder under /tmp."""
    path = os.path.join(folder, name)
    if len(path.encode()) < 100:
        return path
    import hashlib
    digest = hashlib.sha256(folder.encode()).hexdigest()[:12]
    base = f"/tmp/mcode-{os.getuid()}"
    os.makedirs(base, mode=0o700, exist_ok=True)
    if os.stat(base).st_uid != os.getuid():
        raise OSError(f"{base} belongs to another user")
    os.chmod(base, 0o700)
    return os.path.join(base, f"mcode-{digest}-{name}")


def _no_session(output: str) -> bool:
    text = output.lower()
    return "no conversation found" in text or "session not found" in text or \
        ("--resume" in text and "not found" in text)


def _result_failure(result: dict, job: Job) -> str:
    """A readable reason for a result that is not a success (never the bare
    subtype such as "error_during_execution")."""
    details = "\n".join([result.get("text") or ""] + list(result.get("errors") or []))
    tail = "\n".join(l for l in job.tail(30) if not l.startswith("{"))
    explained = _explain_failure(details) or _explain_failure(tail)
    subtype = result.get("subtype") or ""
    if subtype == "error_max_turns":
        return "Claude used up its steps for this request before finishing. Ask it to " \
               "continue, or split the request."
    if explained:
        return explained
    if subtype == "error_during_execution":
        return "Claude Code stopped with an internal error. Try again; if it happens " \
               "again, start a new conversation (AI Engineer menu). Details are in the " \
               "job log."
    return "Claude could not finish."


def _explain_failure(text: str) -> str:
    lower = text.lower()
    if "not logged in" in lower or "please run /login" in lower or "invalid api key" in lower \
            or "authentication" in lower or "oauth" in lower and "expired" in lower:
        return "Claude Code is not signed in or the login expired. Connect your account again."
    if "rate limit" in lower or "usage limit" in lower or "limit reached" in lower:
        return "Your Claude usage limit is reached for now. Try again later."
    if "enotfound" in lower or "network" in lower or "econnrefused" in lower or \
            "getaddrinfo" in lower or "fetch failed" in lower:
        return "Claude cannot be reached: check the Wi-Fi connection."
    if "killed" in lower or "out of memory" in lower or "heap" in lower:
        return "Claude Code ran out of memory on this board."
    lines = [l for l in text.strip().splitlines() if l.strip()]
    return lines[-1][:300] if lines else ""
