"""The sign-in code page: paste Claude's login code from a phone.

Signing in to Claude Code on the device is ``claude auth login``: the user
opens Claude's sign-in page (the first QR code on the LCD), signs in on a
phone, and Claude shows a long one-time code that the waiting
``claude auth login`` needs. Typing it on the device is impractical, so while
(and only while) the sign-in waits for that code, mCode serves this one
page on the local network (the second QR code):

* one address, ``http://<device>:<port>/c/<token>``: a random 128-bit token in
  the path; any other path, or a wrong token, gets 404;
* it accepts exactly one code (a form post, at most 4 KB) and then refuses
  further ones; it stops when the sign-in stops waiting, or after 15 minutes,
  or after 30 wrong requests;
* the code is handed to the waiting ``claude auth login`` and never logged or
  stored. The code is useless without the PKCE verifier that only the
  waiting ``claude auth login`` holds, which is why plain HTTP on the local
  network is acceptable for it.

The desktop IDE (``server.py``) is not involved: it can stay on 127.0.0.1.
"""

from __future__ import annotations

import hmac
import html
import logging
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, List, Optional

from ..core.net import local_addresses

log = logging.getLogger("mcode.codedrop")

MAX_BODY = 4096
LIFETIME = 15 * 60
MAX_WRONG = 30


class CodeDrop:
    def __init__(self, submit: Callable[[str], Optional[str]], port: int = 0,
                 host: str = "0.0.0.0", addresses: Callable[[], List[str]] = local_addresses):
        """``submit(code)`` returns None when the code was handed over, else an error."""
        self.submit = submit
        self.requested_port = port
        self.host = host
        self.addresses = addresses
        self.token = secrets.token_urlsafe(16)
        self.port = 0
        self.used = False
        self.wrong = 0
        self.started = 0.0
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self) -> bool:
        try:
            httpd = ThreadingHTTPServer((self.host, self.requested_port), _handler(self))
        except OSError as exc:
            log.warning("sign-in code page not started: %s", exc)
            return False
        httpd.daemon_threads = True
        self._httpd = httpd
        self.port = httpd.server_address[1]
        self.started = time.time()
        threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.5},
                         name="codedrop", daemon=True).start()
        self._timer = threading.Timer(LIFETIME, self.stop)
        self._timer.daemon = True
        self._timer.start()
        log.info("sign-in code page on port %d", self.port)
        return True

    def stop(self) -> None:
        with self._lock:
            httpd, self._httpd = self._httpd, None
            timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()
        if httpd is not None:
            # shutdown() waits for serve_forever, which may be this thread's caller.
            threading.Thread(target=lambda: (httpd.shutdown(), httpd.server_close()),
                             name="codedrop-stop", daemon=True).start()
            log.info("sign-in code page closed")

    def urls(self) -> List[str]:
        """Addresses a phone on the same network can open (best first)."""
        if not self.running:
            return []
        if self.host not in ("0.0.0.0", "::", ""):
            hosts = [self.host]
        else:
            hosts = self.addresses()
        return [f"http://{h}:{self.port}/c/{self.token}" for h in hosts]

    def url(self) -> str:
        urls = self.urls()
        return urls[0] if urls else ""

    # ------------------------------------------------------------- requests
    def _check(self, path: str) -> bool:
        prefix = "/c/"
        ok = path.startswith(prefix) and hmac.compare_digest(
            path[len(prefix):].encode("utf-8", "replace"), self.token.encode())
        if not ok:
            with self._lock:
                self.wrong += 1
                too_many = self.wrong >= MAX_WRONG
            if too_many:
                log.warning("sign-in code page closed after %d wrong requests", self.wrong)
                self.stop()
        return ok

    def _take(self, code: str) -> str:
        """Returns "" when the code was handed over, else the reason it was not."""
        code = (code or "").strip()
        if not code:
            return "Paste the code first."
        if len(code) > 2000 or any(ch in code for ch in "\r\n\0"):
            return "That does not look like a sign-in code."
        with self._lock:
            if self.used:
                return "A code was already sent. Look at the device."
            self.used = True
        try:
            error = self.submit(code)
        except Exception as exc:               # isolation: report, never crash the page
            log.warning("handing over the sign-in code failed: %s", exc.__class__.__name__)
            error = "The device could not take the code."
        if error:
            with self._lock:
                self.used = False
            return error
        return ""


def _handler(drop: CodeDrop):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "mCode"

        def log_message(self, fmt, *args):       # never log paths (they hold the token)
            pass

        def _send(self, status: int, body: str):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                             "frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if not drop._check(path):
                return self._send(404, _page("Not found", "<p>This link is not valid.</p>"))
            if drop.used:
                return self._send(200, _page("Code sent", "<p>The code was sent. "
                                                          "Look at the device.</p>"))
            return self._send(200, _form())

        def do_POST(self):
            path = urllib.parse.urlsplit(self.path).path
            if not drop._check(path):
                return self._send(404, _page("Not found", "<p>This link is not valid.</p>"))
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                self.close_connection = True
                return self._send(413, _page("Too long", "<p>That is too long for a code.</p>"))
            raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
            code = urllib.parse.parse_qs(raw).get("code", [""])[0]
            error = drop._take(code)
            if error:
                return self._send(400, _form(error))
            return self._send(200, _page("Code sent", "<p><b>Sent.</b> Look at the device: it "
                                                      "finishes signing in by itself.</p><p>You "
                                                      "can close this page.</p>"))

    return Handler


_STYLE = ("body{font-family:system-ui,sans-serif;background:#0b0f14;color:#e8edf3;margin:0;"
          "padding:24px 16px;max-width:520px}h1{font-size:22px;margin:0 0 12px}"
          "p{line-height:1.45;color:#b8c2cc}b{color:#e8edf3}textarea{width:100%;box-sizing:"
          "border-box;min-height:110px;font:16px ui-monospace,monospace;padding:10px;border-radius:"
          "10px;border:1px solid #3a4654;background:#121a23;color:#e8edf3}button{margin-top:14px;"
          "width:100%;padding:14px;font-size:18px;border:0;border-radius:10px;background:#3b82f6;"
          "color:#fff}.err{color:#f87171}")


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport "
            f"content='width=device-width,initial-scale=1'><title>mCode · {html.escape(title)}"
            f"</title><style>{_STYLE}</style></head><body><h1>{html.escape(title)}</h1>{body}"
            "</body></html>")


def _form(error: str = "") -> str:
    message = f"<p class=err>{html.escape(error)}</p>" if error else ""
    return _page("Send the sign-in code", (
        "<p>After signing in, Claude shows an <b>Authentication Code</b>. Copy it, paste it "
        "here and tap <b>Send to mCode</b>.</p>" + message +
        "<form method=post><textarea name=code autocomplete=off autocapitalize=off "
        "autocorrect=off spellcheck=false placeholder='Paste the code here' required></textarea>"
        "<button type=submit>Send to mCode</button></form>"
        "<p>This page works only on the same Wi-Fi as the device, only while the device waits "
        "for the code, and only once.</p>"))
