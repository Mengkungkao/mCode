"""The desktop IDE: a local web server that serves the IDE page and its API.

Security model (docs/SECURITY.md):

* Binds to 127.0.0.1 unless "Use from a computer" (``web.lan``) is switched
  on, which can only be done on the device's LCD (or in a browser that is
  already signed in).
* Every request except the static files and the sign-in page needs the
  access token as an HttpOnly, SameSite=Strict cookie. The token lives in
  the data folder (owner-only). mCode opens the local browser with a one-time
  link that sets the cookie; a computer on the network uses the pairing link
  (QR on the LCD) or the 6-digit pairing code shown on the LCD, which is
  rate-limited.
* The Host header must name this device (stops DNS-rebinding attacks); every
  write needs the ``X-mCode: 1`` header (stops cross-site form posts).
* Uploads still need the confirmation token from ``/api/upload/prepare``,
  and the browser asks the user first: the same two-step rule as the LCD.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import logging
import mimetypes
import os
import queue
import re
import secrets
import socket
import threading
import time
import urllib.parse
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import List, Optional, Tuple

from ..core import storage
from ..projects import boards, files as pfiles, gitutil
from ..projects.model import ProjectError
from ..toolchains import detect
from . import desktop
from .terminal import Terminals

log = logging.getLogger("mcode.web")
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
COOKIE = "mcode_session"
MAX_BODY = 8 * 1024 * 1024
MAX_ZIP = 64 * 1024 * 1024
MAX_SSE = 12
EVENT_TOPICS = None    # all


class ApiError(Exception):
    def __init__(self, status: int, message: str, fix: str = ""):
        super().__init__(message)
        self.status = status
        self.fix = fix


def local_addresses() -> List[str]:
    out = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))         # TEST-NET: nothing is sent
            out.append(sock.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address not in out and not address.startswith("127."):
                out.append(address)
    except OSError:
        pass
    return out


class WebServer:
    def __init__(self, studio, host: Optional[str] = None, port: Optional[int] = None):
        self.studio = studio
        self.requested_host = host
        self.requested_port = port
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None
        self.port = 0
        self.host = ""
        self.running = False
        self.terminals = Terminals(studio.events.publish)
        self._sse = 0
        self._sse_lock = threading.Lock()
        self._pair_attempts: List[float] = []
        self.token = self._load_token()

    # ----------------------------------------------------------------- token
    def _load_token(self) -> str:
        path = self.studio.paths.web_token_file
        try:
            with open(path, encoding="utf-8") as handle:
                token = handle.read().strip()
            if re.fullmatch(r"[A-Za-z0-9_\-]{32,128}", token):
                return token
        except OSError:
            pass
        token = secrets.token_urlsafe(32)
        storage.atomic_write_text(path, token + "\n", mode=0o600)
        return token

    def rotate_token(self) -> None:
        token = secrets.token_urlsafe(32)
        storage.atomic_write_text(self.studio.paths.web_token_file, token + "\n", mode=0o600)
        self.token = token
        self.studio.events.publish("web", {"rotated": True})

    def pair_code(self) -> str:
        digest = hmac.new(self.token.encode(), b"pair", hashlib.sha256).digest()
        return f"{int.from_bytes(digest[:4], 'big') % 1000000:06d}"

    # -------------------------------------------------------------- lifetime
    def start(self) -> bool:
        lan = bool(self.studio.settings.get("web.lan"))
        self.host = self.requested_host or ("0.0.0.0" if lan else "127.0.0.1")
        first = self.requested_port if self.requested_port is not None else \
            int(self.studio.settings.get("web.port"))
        handler = _make_handler(self)
        last_error = None
        candidates = [first] if first == 0 else range(first, first + 10)
        for port in candidates:
            try:
                httpd = ThreadingHTTPServer((self.host, port), handler)
                break
            except OSError as exc:
                last_error = exc
        else:
            log.warning("desktop IDE not started: %s", last_error)
            return False
        httpd.daemon_threads = True
        self.httpd = httpd
        self.port = httpd.server_address[1]
        self.running = True
        self.thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.5},
                                       name="web", daemon=True)
        self.thread.start()
        log.info("desktop IDE at %s", ", ".join(self.urls(with_token=False)))
        self.studio.events.publish("web", {"running": True, "port": self.port})
        return True

    def stop(self) -> None:
        self.running = False
        self.terminals.close_all()
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    def restart(self) -> None:
        self.stop()
        self.start()

    def urls(self, with_token: bool = True, lan_only: bool = False) -> List[str]:
        if not self.running:
            return []
        hosts = [] if lan_only else ["127.0.0.1"]
        if self.host in ("0.0.0.0", "::"):
            hosts = local_addresses() + hosts
        suffix = f"/?t={self.token}" if with_token else "/"
        return [f"http://{h}:{self.port}{suffix}" for h in hosts]

    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    def desktop_available(self) -> bool:
        return desktop.find_session() is not None

    def open_browser(self) -> Tuple[bool, str]:
        return desktop.open_url(self.local_url())

    def allowed_hosts(self) -> set:
        port = self.port
        names = {"127.0.0.1", "localhost", "[::1]"}
        if self.host in ("0.0.0.0", "::"):
            names.update(local_addresses())
            hostname = socket.gethostname()
            names.update({hostname, f"{hostname}.local", f"{hostname}.lan"})
        return {f"{n}:{port}" for n in names} | ({n for n in names} if port == 80 else set())

    def pair_allowed(self) -> bool:
        now = time.time()
        self._pair_attempts = [t for t in self._pair_attempts if now - t < 60]
        if len(self._pair_attempts) >= 5:
            return False
        self._pair_attempts.append(now)
        return True


def _make_handler(server: WebServer):
    studio = server.studio

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "mCode"
        web = server

        def log_message(self, fmt, *args):      # quiet: requests are not logged by default
            log.debug("%s %s", self.address_string(), fmt % args)

        # ------------------------------------------------------- helpers
        def _cookie(self) -> str:
            raw = self.headers.get("Cookie", "")
            for part in raw.split(";"):
                name, _, value = part.strip().partition("=")
                if name == COOKIE:
                    return value
            return ""

        def _authed(self) -> bool:
            return hmac.compare_digest(self._cookie(), server.token)

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in server.allowed_hosts()

        def _send(self, status: int, body: bytes, ctype: str, headers: Optional[dict] = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data:; style-src 'self' "
                             "'unsafe-inline'; script-src 'self'; connect-src 'self'; "
                             "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, data, status: int = 200, headers: Optional[dict] = None):
            self._send(status, json.dumps(data, default=_default).encode(), "application/json",
                       headers)

        def _error(self, status: int, message: str, fix: str = ""):
            self._json({"error": message, "fix": fix}, status)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ApiError(413, "Request too large.")
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return {}
            try:
                data = json.loads(raw.decode("utf-8"))
            except ValueError:
                raise ApiError(400, "Invalid JSON.") from None
            if not isinstance(data, dict):
                raise ApiError(400, "Expected a JSON object.")
            return data

        # ------------------------------------------------------ dispatch
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PUT(self):
            self._dispatch("POST")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def _dispatch(self, method: str):
            if not self._host_ok():
                self._send(403, b"Unknown host name", "text/plain")
                return
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            query = dict(urllib.parse.parse_qsl(parsed.query))
            try:
                if path.startswith("/static/"):
                    return self._static(path[len("/static/"):])
                if path in ("/", "/index.html"):
                    return self._index(query)
                if path == "/api/pair" and method == "POST":
                    return self._pair()
                if not path.startswith("/api/"):
                    return self._send(404, b"Not found", "text/plain")
                if not self._authed():
                    return self._error(401, "Sign in again: open the link shown on the device.")
                if method != "GET" and self.headers.get("X-mCode") != "1":
                    return self._error(403, "Missing request header.")
                if path == "/api/events":
                    return self._events()
                result = route(method, path, query, self)
                if isinstance(result, tuple) and result and result[0] == "__raw__":
                    _, body, ctype, headers = result
                    return self._send(200, body, ctype, headers)
                return self._json(result if result is not None else {"ok": True})
            except ApiError as exc:
                return self._error(exc.status, str(exc), exc.fix)
            except ProjectError as exc:
                return self._error(400, str(exc))
            except Exception as exc:
                fix = getattr(exc, "fix", "")
                if exc.__class__.__name__ in ("BackendError", "AIError", "SerialError",
                                              "PortBusy", "SpeechError", "ValueError"):
                    return self._error(400, str(exc), fix)
                log.exception("API %s %s failed", method, path)
                return self._error(500, f"{exc.__class__.__name__}: {exc}")

        # ------------------------------------------------------- pages
        def _static(self, rel: str):
            target = os.path.realpath(os.path.join(STATIC, rel))
            if not target.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(target):
                return self._send(404, b"Not found", "text/plain")
            ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            with open(target, "rb") as handle:
                body = handle.read()
            self._send(200, body, ctype, {"Cache-Control": "max-age=300"})

        def _index(self, query: dict):
            token = query.get("t", "")
            if token and hmac.compare_digest(token, server.token):
                # One-time link: set the cookie, then drop the token from the address bar.
                return self._send(303, b"", "text/plain", {
                    "Location": "/", "Set-Cookie": f"{COOKIE}={server.token}; HttpOnly; "
                                                   f"SameSite=Strict; Path=/; Max-Age=31536000"})
            page = "index.html" if self._authed() else "pair.html"
            with open(os.path.join(STATIC, page), "rb") as handle:
                body = handle.read()
            self._send(200, body, "text/html; charset=utf-8")

        def _pair(self):
            if self.headers.get("X-mCode") != "1":
                return self._error(403, "Missing request header.")
            if not server.pair_allowed():
                return self._error(429, "Too many tries. Wait a minute.")
            code = str(self._body().get("code", "")).strip()
            if not hmac.compare_digest(code, server.pair_code()):
                return self._error(403, "That code is not right. Look at the device's screen.")
            self._json({"ok": True}, headers={
                "Set-Cookie": f"{COOKIE}={server.token}; HttpOnly; SameSite=Strict; Path=/; "
                              f"Max-Age=31536000"})

        def _events(self):
            with server._sse_lock:
                if server._sse >= MAX_SSE:
                    return self._error(503, "Too many open windows.")
                server._sse += 1
            token, q = studio.events.subscribe_queue()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                self.wfile.write(b"retry: 2000\n\n")
                self.wfile.flush()
                while server.running:
                    try:
                        topic, data, stamp = q.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    payload = json.dumps({"topic": topic, "data": data, "t": stamp},
                                         default=_default)
                    self.wfile.write(f"data: {payload}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                studio.events.unsubscribe(token)
                with server._sse_lock:
                    server._sse -= 1
                self.close_connection = True

    return Handler


def _default(value):
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, (set, tuple)):
        return list(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    return str(value)


# =========================================================================== API

def route(method: str, path: str, q: dict, handler):
    srv = handler.web
    studio = srv.studio
    body = handler._body() if method in ("POST", "DELETE") else {}
    parts = [p for p in path.split("/") if p][1:]        # drop "api"
    head = parts[0] if parts else ""

    def project(key=None):
        return studio.project(key or body.get("project_id") or q.get("project") or None)

    # ------------------------------------------------------------ state
    if head == "state" and method == "GET":
        summary = studio.summary()
        summary["tools"] = [t.to_dict() for t in studio.tools.all()]
        summary["recent_builds"] = studio.builds.recent(8)
        summary["recent_uploads"] = studio.uploads.history(8) if studio.uploads else []
        summary["recent"] = [p.summary() for p in studio.projects.recent(6)]
        summary["web"] = {"lan": studio.settings.get("web.lan"), "urls": srv.urls(False),
                          "desktop": srv.desktop_available()}
        summary["voice"] = studio.voice.status() if studio.voice else None
        return summary
    if head == "settings":
        if method == "GET":
            return {"fields": studio.settings.describe()}
        errors = studio.settings.update(body.get("values") or {})
        if "web.lan" in (body.get("values") or {}) and not errors.get("web.lan"):
            threading.Thread(target=srv.restart, daemon=True).start()
        return {"errors": errors, "fields": studio.settings.describe()}
    # ------------------------------------------------------------ tools
    if head == "tools":
        if method == "GET":
            return {"tools": [t.to_dict() for t in studio.tools.all()],
                    "machine": detect.machine(), "catalogue": [c.__dict__ for c in
                                                               boards.check_against_cores()]}
        if parts[1:2] == ["refresh"]:
            studio.tools.refresh(wait=True)
            return {"tools": [t.to_dict() for t in studio.tools.all()]}
        if parts[1:2] == ["install"]:
            return studio.builds.install_tool(body.get("tool", "")).to_dict()
        if parts[1:2] == ["core"]:
            return studio.builds.install_core(body.get("core", "")).to_dict()
    if head == "boards":
        return {"platforms": boards.PLATFORMS, "toolchains": boards.TOOLCHAIN_NAMES,
                "boards": [dict(b.to_dict(), options=studio.projects.options_for(b.id))
                           for b in boards.BOARDS]}
    # --------------------------------------------------------- projects
    if head == "projects":
        if method == "GET":
            return {"root": studio.projects.root,
                    "projects": [p.summary() for p in studio.projects.search(q.get("q", ""))],
                    "current": (studio.projects.current().summary()
                                if studio.projects.current() else None)}
        action = parts[1] if len(parts) > 1 else "create"
        if action == "create":
            created = studio.projects.create(
                body.get("name", ""), body.get("board_id", ""), body.get("toolchain", ""),
                body.get("framework", ""), baud=int(body.get("baud") or 0),
                usb_cdc_on_boot=bool(body.get("usb_cdc_on_boot")),
                upload_speed=int(body.get("upload_speed") or 0),
                description=body.get("description", ""), location=body.get("location", ""))
            return {"project": created.summary(),
                    "requirements": studio.builds.requirements(created)}
        if action == "open":
            return {"project": studio.projects.open(body.get("path", "")).summary()}
        if action == "import":
            return {"project": studio.projects.import_folder(body.get("path", ""),
                                                             copy=bool(body.get("copy", True))
                                                             ).summary()}
        if action == "clone":
            return _clone(studio, body.get("url", ""))
        if action == "upload":
            raise ApiError(400, "Use /api/projects/zip.")
    if head == "zip" and method == "POST":
        return _import_zip(studio, handler)
    if head == "project" and len(parts) >= 2:
        p = project(parts[1])
        sub = parts[2] if len(parts) > 2 else ""
        if sub == "" and method == "GET":
            data = p.summary()
            data["tree"] = pfiles.tree(p.path)
            data["git"] = gitutil.status(p.path)
            data["backups"] = studio.projects.backups(p)
            data["requirements"] = studio.builds.requirements(p)
            data["last_build"] = studio.builds.last(p.id)
            data["firmware"] = studio.builds.artifact(p)
            data["up_to_date"] = studio.builds.up_to_date(p)
            data["options"] = studio.projects.options_for(p.board_id)
            return data
        if sub == "current":
            return {"project": studio.projects.set_current(p).summary()}
        if sub == "update":
            fields = body.get("fields") or {}
            return {"project": studio.projects.update(p, **fields).summary()}
        if sub == "rename":
            return {"project": studio.projects.rename(p, body.get("name", "")).summary()}
        if sub == "duplicate":
            return {"project": studio.projects.duplicate(p, body.get("name", "")).summary()}
        if sub == "delete":
            if body.get("confirm") != p.name:
                raise ApiError(400, "Type the project's name to confirm.")
            return {"trash": studio.projects.delete(p)}
        if sub == "backup":
            return {"backup": os.path.basename(studio.projects.backup(p, "manual",
                                                                      body.get("note", "")))}
        if sub == "restore":
            return {"old": studio.projects.restore(p, body.get("name", ""))}
        if sub == "file":
            if method == "GET":
                return pfiles.read_text(p.path, q.get("path", ""))
            return pfiles.write_text(p.path, body.get("path", ""), body.get("text", ""),
                                     body.get("sha") or None)
        if sub == "fs":
            op = body.get("op")
            if op == "mkdir":
                return {"path": pfiles.make_folder(p.path, body.get("path", ""))}
            if op == "create":
                return pfiles.write_text(p.path, body.get("path", ""), body.get("text", ""),
                                         create=True)
            if op == "move":
                return {"path": pfiles.move(p.path, body.get("path", ""), body.get("target", ""))}
            if op == "delete":
                return {"trash": pfiles.remove(p.path, body.get("path", ""), studio.projects.trash)}
            raise ApiError(400, "Unknown file operation.")
        if sub == "search":
            return {"hits": _search(p.path, q.get("q", ""), q.get("regex") == "1")}
        if sub == "build":
            return studio.builds.run(p, body.get("action", "build"), source="web").to_dict()
        if sub == "jobs":
            return {"jobs": [j.to_dict() for j in studio.jobs.recent(project_id=p.id, limit=30)]}
        if sub == "libraries":
            libs = studio.libraries
            act = parts[3] if len(parts) > 3 else ""
            if act == "" and method == "GET":
                return {"installed": libs.installed(p)}
            if act == "search":
                return {"results": libs.search(p, q.get("q", ""))}
            if act == "missing":
                return libs.missing(p)
            if act == "deps":
                return {"dependencies": libs.dependencies(p, q.get("name", ""))}
            if act == "conflicts":
                return {"conflicts": libs.conflicts(p)}
            if act == "install":
                return libs.install(p, body.get("name", ""), body.get("version", "")).to_dict()
            if act == "uninstall":
                return libs.uninstall(p, body.get("name", "")).to_dict()
            if act == "update":
                return libs.update(p, body.get("name", "")).to_dict()
        if sub == "git":
            act = parts[3] if len(parts) > 3 else ""
            if act == "commit":
                return {"message": gitutil.commit(p.path, body.get("message", ""))}
            if act == "log":
                return {"log": gitutil.log(p.path)}
            if act == "diff":
                return {"diff": gitutil.diff(p.path)}
            if act == "init":
                return {"ok": gitutil.init(p.path)}
            return gitutil.status(p.path)
        if sub == "export":
            data = io.BytesIO()
            with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
                for rel in pfiles.walk_sources(p.path, include_hidden_meta=True):
                    archive.write(os.path.join(p.path, rel), f"{p.slug}/{rel}")
            return ("__raw__", data.getvalue(), "application/zip",
                    {"Content-Disposition": f'attachment; filename="{p.slug}.zip"'})
    # -------------------------------------------------------------- jobs
    if head == "jobs":
        return {"jobs": [j.to_dict() for j in studio.jobs.recent(limit=40)]}
    if head == "job" and len(parts) >= 2:
        job = studio.jobs.get(parts[1])
        if job is None:
            raise ApiError(404, "No such task.")
        if parts[2:3] == ["cancel"]:
            return {"cancelled": studio.jobs.cancel(job.id)}
        return job.to_dict(lines=int(q.get("lines") or 400))
    # ----------------------------------------------------------- devices
    if head == "devices":
        devices = studio.devices
        if devices is None:
            raise ApiError(400, "Hardware support is off.")
        if parts[1:2] == ["refresh"]:
            devices.refresh()
        if parts[1:2] == ["select"]:
            device = devices.get(body.get("port", ""))
            if device is None:
                raise ApiError(400, "That port is not connected.")
            devices.select(device, project() if (body.get("project_id") or
                                                  studio.projects.current()) else None)
        if parts[1:2] == ["identify"]:
            p = studio.projects.current()
            from ..projects.model import Project
            target = p or Project(path="", name="(no project)", platform="esp32",
                                  board_id="esp32-devkit", toolchain="arduino-cli")
            plan = studio.uploads.prepare(target, body.get("port", ""), action="identify")
            if not plan.ok:
                raise ApiError(400, plan.problem())
            return studio.uploads.start(plan.token, confirmed=True).to_dict()
        current = studio.projects.current()
        chosen = devices.selected(current)
        listed = devices.list()
        out = {"devices": [d.to_dict() for d in listed], "hotplug": devices.hotplug,
               "selected": chosen.port if chosen else "",
               "history": studio.uploads.history(20) if studio.uploads else []}
        if not listed:
            short, detail = devices.no_board_help()
            out["help"] = {"short": short, "detail": detail}
        return out
    # ------------------------------------------------------------ upload
    if head == "upload":
        if parts[1:2] == ["prepare"]:
            plan = studio.uploads.prepare(project(), body.get("port", ""),
                                          body.get("action", "upload"),
                                          int(body.get("speed") or 0))
            return plan.to_dict()
        if parts[1:2] == ["start"]:
            if body.get("confirmed") is not True:
                raise ApiError(400, "Confirm the upload first.")
            return studio.uploads.start(body.get("token", ""), confirmed=True).to_dict()
    # ----------------------------------------------------------- monitor
    if head == "monitor":
        monitor = studio.monitor
        if monitor is None:
            raise ApiError(400, "Hardware support is off.")
        act = parts[1] if len(parts) > 1 else ""
        if act == "connect":
            monitor.connect(body.get("port", ""), int(body.get("baud") or 115200))
        elif act == "disconnect":
            monitor.disconnect()
        elif act == "send":
            monitor.send(body.get("text", ""), body.get("ending", "lf"), bool(body.get("hex")))
        elif act == "pause":
            monitor.set_paused(bool(body.get("paused")))
        elif act == "clear":
            monitor.clear()
        elif act == "reset":
            monitor.reset_board()
        elif act == "save":
            target = None
            if body.get("to_project"):
                p = project()
                folder = os.path.join(p.path, "logs")
                os.makedirs(folder, exist_ok=True)
                target = os.path.join(folder, time.strftime("serial-%Y%m%d-%H%M%S.log"))
            return {"path": monitor.save(target, bool(body.get("timestamps", True)))}
        elif act == "search":
            return {"hits": monitor.search(q.get("q", ""), q.get("regex") == "1")}
        elif act == "hex":
            return {"lines": monitor.hexdump(int(q.get("bytes") or 4096))}
        data = monitor.status()
        data["lines"] = monitor.recent(int(q.get("count") or 500), int(q.get("since") or 0))
        return data
    # ---------------------------------------------------------------- AI
    if head == "ai":
        ai = studio.ai
        if ai is None:
            raise ApiError(400, "AI is not available.")
        act = parts[1] if len(parts) > 1 else ""
        if act == "" and method == "GET":
            return ai.status()
        if act == "refresh":
            return ai.refresh_status(wait=True)
        if act == "login":
            sub = parts[2] if len(parts) > 2 else ""
            if sub == "code":
                return ai.login_code(body.get("code", ""))
            if sub == "cancel":
                ai.login_cancel()
                return ai.status()
            return ai.login_start(console=bool(body.get("console")))
        if act == "logout":
            return {"ok": ai.logout()}
        if act == "cancel":
            return {"cancelled": ai.cancel()}
        if act == "actions":
            return {"actions": ai.actions(q.get("project"), int(q.get("limit") or 100))}
        p = project(act)
        sub = parts[2] if len(parts) > 2 else ""
        if sub == "conversation":
            return {"conversation": ai.conversation(p), "review": ai.review(p),
                    "turn": ai.turn.to_dict() if ai.turn else None}
        if sub == "ask":
            kind = body.get("kind", "chat")
            text = body.get("text", "")
            if kind == "fix-build":
                turn = ai.fix_build(p, "web")
            elif kind == "analyze-serial":
                turn = ai.analyze_serial(p, text, "web")
            else:
                turn = ai.ask(p, text, kind=kind, attachments=body.get("attachments") or [],
                              include_serial=bool(body.get("include_serial")), source="web",
                              mode=body.get("mode") or None)
            return turn.to_dict()
        if sub == "review":
            return {"review": ai.review(p)}
        if sub == "approve":
            return ai.approve(p, body.get("paths"), body.get("rebuild"))
        if sub == "reject":
            return ai.reject(p)
        if sub == "undo":
            return ai.undo(p)
        if sub == "clear":
            ai.clear_conversation(p)
            return {"ok": True}
    # ------------------------------------------------------------- voice
    if head == "voice":
        return studio.voice.status() if studio.voice else {"state": "unavailable"}
    # ---------------------------------------------------------- terminal
    if head == "term":
        act = parts[1] if len(parts) > 1 else ""
        if act == "open":
            p = project()
            session = srv.terminals.open(p.path)
            return {"id": session.id}
        session = srv.terminals.get(act)
        if session is None:
            raise ApiError(404, "That terminal is closed.")
        sub = parts[2] if len(parts) > 2 else ""
        if sub == "input":
            session.write(str(body.get("data", "")).encode("utf-8"))
        elif sub == "resize":
            session.resize(body.get("cols", 80), body.get("rows", 24))
        elif sub == "close":
            srv.terminals.close(act)
        elif sub == "buffer":
            return {"data": base64.b64encode(bytes(session.buffer)).decode()}
        return {"ok": True, "alive": session.alive}
    # -------------------------------------------------------------- web
    if head == "web":
        if parts[1:2] == ["open-desktop"]:
            ok, why = srv.open_browser()
            return {"ok": ok, "error": why}
        return {"urls": srv.urls(False), "lan": studio.settings.get("web.lan"),
                "desktop": srv.desktop_available()}
    raise ApiError(404, f"Unknown API {method} {path}")


def _search(root: str, query: str, regex: bool) -> list:
    if not query:
        return []
    try:
        pattern = re.compile(query if regex else re.escape(query), re.I)
    except re.error as exc:
        raise ApiError(400, f"Invalid pattern: {exc}") from None
    hits = []
    for rel in pfiles.walk_sources(root):
        full = os.path.join(root, rel)
        try:
            if os.path.getsize(full) > pfiles.MAX_EDIT_BYTES or not pfiles.is_text_file(full):
                continue
            with open(full, encoding="utf-8", errors="replace") as handle:
                for number, line in enumerate(handle, 1):
                    if pattern.search(line):
                        hits.append({"path": rel, "line": number, "text": line.rstrip()[:240]})
                        if len(hits) >= 500:
                            return hits
        except OSError:
            continue
    return hits


def _clone(studio, url: str) -> dict:
    from ..core.jobs import JobSpec
    url = (url or "").strip()
    name = re.sub(r"\.git$", "", url.rstrip("/").split("/")[-1]) or "project"
    from ..projects.model import slugify
    dest = os.path.join(studio.projects.ensure_root(), slugify(name))
    if os.path.exists(dest):
        raise ApiError(400, f"A folder called {slugify(name)} already exists.")
    argv = gitutil.clone_argv(url, dest)

    def work(job):
        job.set_phase("Cloning")
        job.run(argv, env=gitutil.GIT_ENV, timeout=900, what="git clone")
        project = studio.projects.open(dest)
        return {"project": project.summary()}

    return studio.jobs.submit(JobSpec(kind="clone", title=f"Cloning {name}", func=work,
                                      lane="tool", timeout=960)).to_dict()


def _import_zip(studio, handler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0 or length > MAX_ZIP:
        raise ApiError(413, "The zip file is empty or larger than 64 MB.")
    data = handler.rfile.read(length)
    name = os.path.splitext(os.path.basename(handler.headers.get("X-Filename") or "import.zip"))[0]
    from ..projects.model import slugify
    root = studio.projects.ensure_root()
    dest = os.path.join(root, slugify(name))
    if os.path.exists(dest):
        raise ApiError(400, f"A folder called {slugify(name)} already exists.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ApiError(400, "That is not a zip file.") from None
    names = [n for n in archive.namelist() if not n.endswith("/")]
    prefix = os.path.commonprefix(names).rsplit("/", 1)[0] + "/" if names and "/" in names[0] \
        else ""
    total = 0
    tmp = dest + ".importing"
    os.makedirs(tmp)
    try:
        for info in archive.infolist():
            if info.is_dir():
                continue
            rel = info.filename[len(prefix):] if prefix and info.filename.startswith(prefix) \
                else info.filename
            if not rel or rel.startswith("/") or ".." in rel.split("/"):
                raise ApiError(400, f"Unsafe path in zip: {info.filename}")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ApiError(400, f"The zip contains a link: {info.filename}")
            total += info.file_size
            if total > 256 * 1024 * 1024:
                raise ApiError(400, "The zip expands to more than 256 MB.")
            target = pfiles.resolve(tmp, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with archive.open(info) as source, open(target, "wb") as out:
                out.write(source.read())
        os.replace(tmp, dest)
    except Exception:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return {"project": studio.projects.open(dest).summary()}
