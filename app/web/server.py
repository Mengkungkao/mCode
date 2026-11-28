"""The desktop IDE: a local web server that serves the IDE page and its API.

Security model (docs/SECURITY.md, docs/SECURITY_AUDIT_2.0.md):

* Binds to 127.0.0.1 unless "Use from a computer" (``web.lan``) is switched
  on, on the device's LCD or by a browser with full access.
* Every request except the static files and the pairing page needs a
  **session** cookie (HttpOnly, SameSite=Strict; ``sessions.py``): one per
  browser, revocable, expiring when unused. The device's own browser gets one
  through a single-use link; other computers and phones pair while a pairing
  screen is open (random single-use code or QR token, rate-limited).
* Each session has a **role**: viewer (look only), developer (projects,
  builds, flashing with confirmation, AI) or admin (also the terminal,
  folders outside the project folder, tools, device and security settings).
  :func:`required_role` is the one table of what needs which role.
* The Host header must name this device (DNS rebinding); every write needs
  ``X-mCode: 1`` and, when the browser sends one, a same-site ``Origin``
  (cross-site posts).
* Uploads still need the confirmation token from ``/api/upload/prepare``,
  and the browser asks the user first: the same two-step rule as the LCD.
* Connections time out when idle; event streams drop clients that vanished
  (a phone that slept or left the Wi-Fi) instead of holding their slot.
"""

from __future__ import annotations

import base64
import gzip
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
import shutil
import socket
import threading
import time
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional, Tuple

from ..core import storage
from ..core.config import FIELD_BY_KEY
from ..core.net import local_addresses
from ..projects import boards, files as pfiles, gitutil
from ..projects.model import ProjectError
from ..toolchains import detect
from . import desktop
from .sessions import RANK, ROLE_LABELS, ROLES, SessionStore
from .terminal import Terminals

log = logging.getLogger("mcode.web")
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
COOKIE = "mcode_session"
MAX_BODY = 8 * 1024 * 1024
MAX_ZIP = 64 * 1024 * 1024
MAX_ZIP_EXPANDED = 256 * 1024 * 1024
MAX_SSE = 12
IDLE_TIMEOUT = 60.0                 # seconds a connection may sit without a request
STREAM_USER_TIMEOUT_MS = 30000      # unacknowledged event-stream data drops the client
COMPRESSIBLE = ("text/", "application/javascript", "application/json", "image/svg+xml",
                "application/manifest+json")


class ApiError(Exception):
    def __init__(self, status: int, message: str, fix: str = ""):
        super().__init__(message)
        self.status = status
        self.fix = fix


def tune_stream_socket(sock: socket.socket) -> None:
    """Keep-alive probes and a TCP user time-out: a browser that vanished without
    closing (a phone asleep, out of Wi-Fi) is dropped within about 45 s."""
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, "TCP_KEEPIDLE"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 20)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
        if hasattr(socket, "TCP_USER_TIMEOUT"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT, STREAM_USER_TIMEOUT_MS)
    except OSError:
        pass


def required_role(method: str, parts: List[str], body: dict) -> str:
    """The one table of what each API call needs (A-16). ``parts`` excludes "api"."""
    head = parts[0] if parts else ""
    sub = parts[1] if len(parts) > 1 else ""
    if head in ("term", "security"):
        return "admin"
    if head == "session":
        return "viewer"                 # see and end one's own session
    if method == "GET":
        return "viewer"
    if head == "settings":
        values = body.get("values") or {}
        needs = [k for k in values if k in FIELD_BY_KEY and FIELD_BY_KEY[k].needs_admin]
        return "admin" if needs else "developer"
    if head == "projects" and sub in ("open", "import"):
        return "admin"                  # any folder on the device
    if head == "tools" and sub == "install":
        return "admin"
    if head == "ai" and sub in ("login", "logout"):
        return "admin"                  # the device's Claude account
    if head == "web":
        return "admin"
    return "developer"


class _Static:
    """Static files with an ETag, gzip-compressed once when the browser accepts it."""

    def __init__(self, root: str):
        self.root = os.path.realpath(root)
        self._cache: Dict[str, tuple] = {}
        self._lock = threading.Lock()

    def get(self, rel: str) -> Optional[tuple]:
        target = os.path.realpath(os.path.join(self.root, rel))
        if not target.startswith(self.root + os.sep) or not os.path.isfile(target):
            return None
        stat = os.stat(target)
        with self._lock:
            cached = self._cache.get(target)
            if cached and cached[0] == (stat.st_mtime_ns, stat.st_size):
                return cached[1:]
        with open(target, "rb") as handle:
            body = handle.read()
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if target.endswith(".webmanifest"):
            ctype = "application/manifest+json"
        if ctype.startswith("text/") or ctype in ("application/javascript",
                                                  "application/manifest+json"):
            ctype += "; charset=utf-8"
        packed = gzip.compress(body, 6, mtime=0) if ctype.startswith(COMPRESSIBLE) and \
            len(body) > 1024 else None
        etag = '"' + hashlib.sha256(body).hexdigest()[:20] + '"'
        entry = (body, packed, ctype, etag)
        with self._lock:
            self._cache[target] = ((stat.st_mtime_ns, stat.st_size),) + entry
        return entry


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
        self.idle_timeout = IDLE_TIMEOUT
        self.terminals = Terminals(studio.events.publish)
        self.static = _Static(STATIC)
        self._sse = 0
        self._sse_lock = threading.Lock()
        self._pair_attempts: List[float] = []
        self._hosts: Tuple[float, set] = (0.0, set())
        self.sessions = SessionStore(studio.paths.web_sessions_file,
                                     days=lambda: studio.settings.get("web.session_days"),
                                     log_path=studio.paths.security_log,
                                     publish=studio.events.publish)
        self.token = self._load_token()

    # ------------------------------------------------- legacy token (0.1.x)
    def _load_token(self) -> str:
        """The shared token mCode 0.1.x put in every browser's cookie. A request that
        still carries it is exchanged once for its own session (admin, as before)."""
        path = self.studio.paths.web_token_file
        try:
            with open(path, encoding="utf-8") as handle:
                token = handle.read().strip()
            if re.fullmatch(r"[A-Za-z0-9_\-]{32,128}", token):
                return token
        except OSError:
            pass
        token = secrets_token()
        storage.atomic_write_text(path, token + "\n", mode=0o600)
        return token

    def rotate_token(self) -> None:
        """Sign out every browser: all sessions, links, the pairing window and the old
        shared token. (The LCD's *Sign out all computers*.)"""
        token = secrets_token()
        storage.atomic_write_text(self.studio.paths.web_token_file, token + "\n", mode=0o600)
        self.token = token
        self.sessions.revoke_all(by="device")
        self.studio.events.publish("web", {"rotated": True})

    # ---------------------------------------------------------------- pairing
    def open_pairing(self, role: Optional[str] = None):
        return self.sessions.open_pairing(role or self.studio.settings.get("web.pair_role"))

    def close_pairing(self) -> None:
        self.sessions.close_pairing()

    def pair_code(self) -> str:
        """The open pairing window's code, or "" (pairing is closed)."""
        window = self.sessions.pairing()
        return window.code if window is not None else ""

    def pair_url(self) -> str:
        """The QR link of the open pairing window (first network address), or ""."""
        window = self.sessions.pairing()
        urls = self.urls(lan_only=True)
        if window is None or not urls:
            return ""
        return urls[0].rstrip("/") + "/?p=" + window.qr_token

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
        self._hosts = (0.0, set())
        self.thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.5},
                                       name="web", daemon=True)
        self.thread.start()
        log.info("desktop IDE at %s", ", ".join(self.urls()))
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

    def urls(self, with_token: bool = False, lan_only: bool = False) -> List[str]:
        """Addresses of the IDE (never with a secret; ``with_token`` is ignored since
        0.2.0: use :meth:`local_url` or :meth:`pair_url`)."""
        if not self.running:
            return []
        hosts = [] if lan_only else ["127.0.0.1"]
        if self.host in ("0.0.0.0", "::"):
            hosts = local_addresses() + hosts
        return [f"http://{h}:{self.port}/" for h in hosts]

    def local_url(self) -> str:
        """A single-use link (10 minutes) that opens the IDE with full access: for this
        device's own browser, or printed by ``run.sh --desktop``."""
        return f"http://127.0.0.1:{self.port}/?t={self.sessions.new_link('admin')}"

    def desktop_available(self) -> bool:
        return desktop.find_session() is not None

    def open_browser(self) -> Tuple[bool, str]:
        return desktop.open_url(self.local_url())

    def extra_hosts(self) -> List[str]:
        raw = str(self.studio.settings.get("web.extra_hosts") or "")
        return [h.strip().lower() for h in raw.split(",") if re.fullmatch(r"[A-Za-z0-9.\-]{1,253}",
                                                                          h.strip())]

    def allowed_hosts(self) -> set:
        now = time.monotonic()
        stamp, cached = self._hosts
        if cached and now - stamp < 10:
            return cached
        port = self.port
        names = {"127.0.0.1", "localhost", "[::1]"}
        if self.host in ("0.0.0.0", "::"):
            names.update(local_addresses())
            hostname = socket.gethostname()
            names.update({hostname, f"{hostname}.local", f"{hostname}.lan"})
        allowed = {f"{n}:{port}" for n in names} | ({n for n in names} if port == 80 else set())
        for extra in self.extra_hosts():           # a TLS tunnel or proxy: any port
            allowed.update({extra, f"{extra}:{port}", f"{extra}:443"})
        self._hosts = (now, allowed)
        return allowed

    def pair_allowed(self) -> bool:
        now = time.time()
        self._pair_attempts = [t for t in self._pair_attempts if now - t < 60]
        if len(self._pair_attempts) >= 5:
            return False
        self._pair_attempts.append(now)
        return True


def secrets_token() -> str:
    return secrets.token_urlsafe(32)


def _make_handler(server: WebServer):
    studio = server.studio

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "mCode"
        web = server
        session = None
        _parsed_body = None             # this request's JSON body, once read
        _body_read = True               # False while a request's body is unread

        @property
        def timeout(self):                     # read by StreamRequestHandler.setup()
            return server.idle_timeout

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

        def _address(self) -> str:
            return self.client_address[0] if self.client_address else ""

        def _secure(self) -> bool:
            """Behind a TLS tunnel or proxy on this device (it says https)."""
            return self.headers.get("X-Forwarded-Proto", "") == "https" and \
                self._address() in ("127.0.0.1", "::1")

        def _set_cookie(self, value: str) -> str:
            return (f"{COOKIE}={value}; HttpOnly; SameSite=Strict; Path=/; "
                    f"Max-Age={server.sessions.max_age()}" + ("; Secure" if self._secure() else ""))

        def _authed(self):
            """This request's session, or None. A 0.1.x cookie becomes a session once."""
            value = self._cookie()
            session = server.sessions.check(value, self._address())
            if session is None and value and hmac.compare_digest(value.encode(),
                                                                 server.token.encode()):
                new, session = server.sessions.create(
                    "admin", self.headers.get("User-Agent", ""), self._address(), "legacy")
                self._pending_cookie = self._set_cookie(new)
            self.session = session
            return session

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "").lower() in server.allowed_hosts()

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return True                    # same-origin GET/HEAD, curl, old browsers
            parsed = urllib.parse.urlsplit(origin)
            return parsed.scheme in ("http", "https") and \
                parsed.netloc.lower() in server.allowed_hosts()

        def _send(self, status: int, body: bytes, ctype: str, headers: Optional[dict] = None):
            headers = dict(headers or {})
            if not self._body_read and int(self.headers.get("Content-Length") or 0) > 0:
                # Answered without reading the request's body (refused early): the body
                # would be taken for the next request on this connection, so close it.
                self.close_connection = True
                headers["Connection"] = "close"
            pending = getattr(self, "_pending_cookie", None)
            if pending and "Set-Cookie" not in headers:
                headers["Set-Cookie"] = pending
                self._pending_cookie = None
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cache-Control", headers.pop("Cache-Control", "no-store"))
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data:; style-src 'self' "
                             "'unsafe-inline'; script-src 'self'; connect-src 'self'; "
                             "manifest-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                             "form-action 'self'")
            for key, value in headers.items():
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
            # One handler object serves every request of a kept-alive connection:
            # _dispatch clears this cache for each request.
            if self._parsed_body is not None:
                return self._parsed_body
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ApiError(413, "Request too large.")
            raw = self.rfile.read(length) if length else b""
            self._body_read = True
            data = {}
            if raw:
                try:
                    data = json.loads(raw.decode("utf-8"))
                except ValueError:
                    raise ApiError(400, "Invalid JSON.") from None
                if not isinstance(data, dict):
                    raise ApiError(400, "Expected a JSON object.")
            self._parsed_body = data
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
            self._pending_cookie = None
            self._parsed_body = None
            self._body_read = False
            if not self._host_ok():
                self._send(403, b"Unknown host name", "text/plain")
                return
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            query = dict(urllib.parse.parse_qsl(parsed.query))
            try:
                if path.startswith("/static/"):
                    return self._static(path[len("/static/"):])
                if path == "/manifest.webmanifest":
                    return self._static("manifest.webmanifest")
                if path in ("/", "/index.html"):
                    return self._index(query)
                if method != "GET" and not self._origin_ok():
                    return self._error(403, "This request came from another site.")
                if path == "/api/pair" and method == "POST":
                    return self._pair()
                if not path.startswith("/api/"):
                    return self._send(404, b"Not found", "text/plain")
                session = self._authed()
                if session is None:
                    return self._error(401, "This browser is not paired (or was signed out). "
                                            "Pair it again from the device's screen.")
                if method != "GET" and self.headers.get("X-mCode") != "1":
                    return self._error(403, "Missing request header.")
                parts = [p for p in path.split("/") if p][1:]
                raw_upload = parts[:1] == ["zip"]          # the body is the zip itself
                body = self._body() if method in ("POST", "DELETE") and not raw_upload else {}
                need = required_role(method, parts, body)
                if RANK[session.role] < RANK[need]:
                    return self._error(403, f"This browser has {ROLE_LABELS[session.role]} "
                                            f"access; this needs "
                                            f"{'full access' if need == 'admin' else need}. "
                                            "Pair it again with more access from the device, "
                                            "or change it on the Security page of a browser "
                                            "with full access.")
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
                                              "PortBusy", "SpeechError", "ValueError",
                                              "RuntimeError"):
                    return self._error(400, str(exc), fix)
                log.exception("API %s %s failed", method, path)
                return self._error(500, "mCode could not do that because of an internal "
                                        "error. Nothing else was changed. The details are in "
                                        "mCode's log.")

        # ------------------------------------------------------- pages
        def _static(self, rel: str):
            entry = server.static.get(rel)
            if entry is None:
                return self._send(404, b"Not found", "text/plain")
            body, packed, ctype, etag = entry
            headers = {"Cache-Control": "no-cache", "ETag": etag, "Vary": "Accept-Encoding"}
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            if packed is not None and "gzip" in self.headers.get("Accept-Encoding", ""):
                headers["Content-Encoding"] = "gzip"
                body = packed
            return self._send(200, body, ctype, headers)

        def _login(self, value: str) -> None:
            self._send(303, b"", "text/plain", {"Location": "/", "Set-Cookie":
                                                 self._set_cookie(value)})

        def _index(self, query: dict):
            agent = self.headers.get("User-Agent", "")
            if query.get("t"):
                # Single-use link (this device's browser, or the address run.sh printed).
                role = server.sessions.use_link(query["t"])
                if role is not None:
                    value, _ = server.sessions.create(role, agent, self._address(), "link")
                    return self._login(value)
                server.sessions.event("link-refused", address=self._address())
                return self._send(303, b"", "text/plain", {"Location": "/?used=1"})
            if query.get("p"):
                if not server.pair_allowed():
                    return self._send(303, b"", "text/plain", {"Location": "/?wait=1"})
                paired = server.sessions.pair_with_qr(query["p"], agent, self._address())
                if paired is not None:
                    return self._login(paired[0])
                return self._send(303, b"", "text/plain", {"Location": "/?used=1"})
            page = "index.html" if self._authed() else "pair.html"
            with open(os.path.join(STATIC, page), "rb") as handle:
                body = handle.read()
            self._send(200, body, "text/html; charset=utf-8")

        def _pair(self):
            if self.headers.get("X-mCode") != "1":
                return self._error(403, "Missing request header.")
            if not server.pair_allowed():
                server.sessions.event("pairing-rate-limited", address=self._address())
                return self._error(429, "Too many tries. Wait a minute.")
            if server.sessions.pairing() is None:
                return self._error(403, "Pairing is closed. On the device open mCode › "
                                        "Settings › Desktop IDE › Pair a computer, then type "
                                        "the code it shows.")
            code = str(self._body().get("code", "")).strip()
            paired = server.sessions.pair_with_code(code, self.headers.get("User-Agent", ""),
                                                    self._address())
            if paired is None:
                return self._error(403, "That code is not right. Look at the device's screen.")
            self._json({"ok": True, "role": paired[1].role},
                       headers={"Set-Cookie": self._set_cookie(paired[0])})

        def _events(self):
            with server._sse_lock:
                if server._sse >= MAX_SSE:
                    return self._error(503, "Too many open windows.")
                server._sse += 1
            token, q = studio.events.subscribe_queue()
            try:
                tune_stream_socket(self.connection)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                pending = getattr(self, "_pending_cookie", None)
                if pending:
                    self.send_header("Set-Cookie", pending)
                self.end_headers()
                self.wfile.write(b"retry: 2000\n\n")
                self.wfile.flush()
                while server.running:
                    if self.session is not None and server.sessions.get(self.session.id) is None:
                        break                   # signed out elsewhere: stop streaming
                    try:
                        topic, data, stamp = q.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    if topic == "terminal" and RANK[self.session.role] < RANK["admin"]:
                        continue                # terminal output is for full access only
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
    session = handler.session
    admin = session is not None and session.role == "admin"
    parts = [p for p in path.split("/") if p][1:]        # drop "api"
    head = parts[0] if parts else ""
    # A zip import's body is the zip itself (reading it as JSON broke every import: A-18).
    body = handler._body() if method in ("POST", "DELETE") and head != "zip" else {}

    def project(key=None):
        key = key or body.get("project_id") or q.get("project") or None
        if key and ("/" in key or key.startswith("~")) and not admin:
            # A folder path reaches anything on the device: full access only (A-16).
            raise ApiError(403, "Opening a folder by its path needs full access.")
        return studio.project(key)

    # ---------------------------------------------------------- session
    if head == "session":
        if parts[1:2] == ["logout"] and method != "GET":
            srv.sessions.revoke(session.id, by="itself")
            return ("__raw__", b'{"ok": true}', "application/json",
                    {"Set-Cookie": f"{COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
        return {"session": session.to_dict(), "roles": {r: ROLE_LABELS[r] for r in ROLES}}
    if head == "security":
        return _security(srv, session, method, parts, body)
    # ------------------------------------------------------------ state
    if head == "state" and method == "GET":
        summary = studio.summary()
        summary["session"] = session.to_dict() if session is not None else None
        summary["tools"] = [t.to_dict() for t in studio.tools.all()]
        summary["recent_builds"] = studio.builds.recent(8)
        summary["recent_uploads"] = studio.uploads.history(8) if studio.uploads else []
        summary["recent"] = [p.summary() for p in studio.projects.recent(6)]
        summary["web"] = {"lan": studio.settings.get("web.lan"), "urls": srv.urls(),
                          "desktop": srv.desktop_available()}
        summary["voice"] = studio.voice.status() if studio.voice else None
        return summary
    if head == "settings":
        if method == "GET":
            return {"fields": studio.settings.describe()}
        errors = studio.settings.update(body.get("values") or {})
        srv._hosts = (0.0, set())
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
            builds = {}
            for record in studio.builds.recent(300):        # newest first: one per project
                if record.get("action") != "clean" and record.get("project_id") not in builds:
                    builds[record.get("project_id")] = {"state": record.get("state"),
                                                        "time": record.get("time"),
                                                        "action": record.get("action")}
            return {"root": studio.projects.root, "builds": builds,
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
        if sub == "board":
            config = studio.boardconfig
            if method != "GET":
                p = config.apply(p, str(body.get("id", "")), str(body.get("value", "")))
            connected = config.connected_board(p)
            return {"project": p.summary(),
                    "settings": [s.to_dict() for s in config.settings(p)],
                    "connected": connected.to_dict() if connected else None}
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
                    "context": ai.context_of(p),
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
        return {"urls": srv.urls(), "lan": studio.settings.get("web.lan"),
                "desktop": srv.desktop_available()}
    raise ApiError(404, f"Unknown API {method} {path}")


def _security(srv: WebServer, session, method: str, parts: List[str], body: dict) -> dict:
    """The Security page: sessions, pairing, network access, recent events (admin)."""
    store = srv.sessions
    act = parts[1] if len(parts) > 1 else ""
    if method != "GET":
        if act == "revoke":
            target = str(body.get("id", ""))
            if not store.revoke(target, by=session.id):
                raise ApiError(404, "That browser is not paired any more.")
        elif act == "revoke-others":
            store.revoke_all(keep=session.id, by=session.id)
        elif act == "role":
            target, role = str(body.get("id", "")), str(body.get("role", ""))
            if target == session.id and role != "admin":
                raise ApiError(400, "Lower your own access from another browser with full "
                                    "access, or sign this browser out.")
            if role not in ROLES or not store.set_role(target, role, by=session.id):
                raise ApiError(400, "Unknown browser or access level.")
        elif act == "pair":
            if parts[2:3] == ["close"]:
                store.close_pairing()
            else:
                if not srv.studio.settings.get("web.lan"):
                    raise ApiError(400, "Switch on Use from a computer first (Settings › "
                                        "Interface › Allow computers on the network).")
                srv.open_pairing(str(body.get("role") or "") or None)
        else:
            raise ApiError(404, "Unknown security action.")
    window = store.pairing()
    pair = window.to_dict() if window is not None else {"open": False}
    if window is not None:
        url = srv.pair_url()
        from ..lcd import qr
        pair.update(url=url, qr=["".join("1" if cell else "0" for cell in row)
                                 for row in qr.matrix(url)] if url else [])
    return {"sessions": store.list(), "me": session.id, "events": store.events(40),
            "pairing": pair, "roles": {r: ROLE_LABELS[r] for r in ROLES},
            "network": {"lan": bool(srv.studio.settings.get("web.lan")), "urls": srv.urls(),
                        "extra_hosts": srv.extra_hosts(), "https": False,
                        "session_days": srv.studio.settings.get("web.session_days"),
                        "pair_role": srv.studio.settings.get("web.pair_role")}}


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
    handler._body_read = True
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
    if os.path.isdir(tmp) and not os.path.islink(tmp):
        shutil.rmtree(tmp)                  # left by an import that was interrupted (A-11)
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
            target = pfiles.resolve(tmp, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with archive.open(info) as source, open(target, "wb") as out:
                while True:                 # streamed; the size is counted, not trusted
                    block = source.read(1 << 16)
                    if not block:
                        break
                    total += len(block)
                    if total > MAX_ZIP_EXPANDED:
                        raise ApiError(400, "The zip expands to more than 256 MB.")
                    out.write(block)
        os.replace(tmp, dest)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return {"project": studio.projects.open(dest).summary()}
