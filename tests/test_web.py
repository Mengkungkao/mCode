"""The desktop IDE server: access control, API, events, terminal."""

from __future__ import annotations

import base64
import http.client
import json
import io
import os
import threading
import time
import unittest
import zipfile

from app.lcd import qr
from app.web.server import WebServer

from .helpers import FAKE_ARDUINO_CLI, TempCase, write_script


class WebCase(TempCase):
    def setUp(self):
        super().setUp()
        self.studio_ = self.studio(hardware=True, ai=True)
        self.studio_.settings.set("ai.claude_path", os.path.join(self.tmp, "no-claude"), save=False)
        self.web = WebServer(self.studio_, port=0)
        self.assertTrue(self.web.start())
        self.addCleanup(self.web.stop)
        self.studio_.web = self.web
        self.cookie, _ = self.web.sessions.create("admin", "test", "127.0.0.1", "test")

    def request(self, method, path, body=None, headers=None, cookie=True, host=None):
        """``cookie``: True = this test's admin session, a string = that cookie value."""
        conn = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=10)
        all_headers = {"Host": host or f"127.0.0.1:{self.web.port}"}
        if cookie:
            value = self.cookie if cookie is True else cookie
            all_headers["Cookie"] = f"mcode_session={value}"
        if method != "GET":
            all_headers["X-mCode"] = "1"
        all_headers.update(headers or {})
        payload = json.dumps(body).encode() if body is not None else None
        if payload is not None:
            all_headers["Content-Type"] = "application/json"
        conn.request(method, path, body=payload, headers=all_headers)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        try:
            parsed = json.loads(data.decode() or "null")
        except ValueError:
            parsed = data
        return response.status, parsed, response


def cookie_of(response):
    header = response.getheader("Set-Cookie") or ""
    return header.split(";", 1)[0].partition("=")[2]


class AccessTests(WebCase):
    def test_single_use_link_sets_a_strict_session_cookie(self):
        link = self.web.local_url().split("?t=", 1)[1]
        status, _, response = self.request("GET", f"/?t={link}", cookie=False)
        self.assertEqual(status, 303)
        cookie = response.getheader("Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn(link, cookie)
        self.assertEqual(response.getheader("Location"), "/")
        value = cookie_of(response)
        self.assertEqual(self.request("GET", "/api/state", cookie=value)[0], 200)
        # A-02: the link works once (it used to be the permanent shared token).
        status, _, response = self.request("GET", f"/?t={link}", cookie=False)
        self.assertEqual(response.getheader("Location"), "/?used=1")
        self.assertIsNone(response.getheader("Set-Cookie"))

    def test_without_cookie_only_the_pairing_page(self):
        status, body, _ = self.request("GET", "/", cookie=False)
        self.assertEqual(status, 200)
        self.assertIn(b"Connect this browser", body)
        self.assertEqual(self.request("GET", "/api/state", cookie=False)[0], 401)
        self.assertEqual(self.request("GET", "/api/state", cookie="forged")[0], 401)
        self.assertEqual(self.request("GET", "/?t=wrong", cookie=False)[0], 303)

    def test_dns_rebinding_and_cross_site_posts_refused(self):
        self.assertEqual(self.request("GET", "/api/state", host="evil.example:80")[0], 403)
        status, _, _ = self.request("POST", "/api/projects/create", {"name": "x"},
                                    headers={"X-mCode": "0"})
        self.assertEqual(status, 403)
        status, err, _ = self.request("POST", "/api/projects/create", {"name": "x"},
                                      headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        self.assertIn("another site", err["error"])
        status, _, _ = self.request("POST", "/api/monitor/clear", {},
                                    headers={"Origin": f"http://127.0.0.1:{self.web.port}"})
        self.assertNotEqual(status, 403)

    def test_extra_host_names_are_accepted(self):
        self.studio_.settings.set("web.extra_hosts", "dev.tailnet.ts.net", save=False)
        self.web._hosts = (0.0, set())
        self.assertEqual(self.request("GET", "/api/state", host="dev.tailnet.ts.net")[0], 200)
        self.assertEqual(self.request("GET", "/api/state", host="other.ts.net")[0], 403)

    def test_security_headers_and_static_files(self):
        status, body, response = self.request("GET", "/static/app.js", cookie=False)
        self.assertEqual(status, 200)
        self.assertIn("script-src 'self'", response.getheader("Content-Security-Policy"))
        self.assertEqual(response.getheader("X-Frame-Options"), "DENY")
        self.assertEqual(self.request("GET", "/static/../server.py", cookie=False)[0], 404)

    def test_static_files_are_compressed_and_revalidated(self):
        """A-15: no-store was sent next to max-age, so nothing was ever cached."""
        status, body, response = self.request("GET", "/static/app.js", cookie=False,
                                              headers={"Accept-Encoding": "gzip"})
        self.assertEqual(response.getheader("Content-Encoding"), "gzip")
        self.assertEqual(response.getheader("Cache-Control"), "no-cache")
        etag = response.getheader("ETag")
        self.assertTrue(etag)
        import gzip as _gzip
        with open(os.path.join(os.path.dirname(__file__), "..", "app", "web", "static",
                               "app.js"), "rb") as handle:
            self.assertEqual(_gzip.decompress(body), handle.read())
        status, _, _ = self.request("GET", "/static/app.js", cookie=False,
                                    headers={"If-None-Match": etag})
        self.assertEqual(status, 304)
        self.assertEqual(self.request("GET", "/manifest.webmanifest", cookie=False)[0], 200)

    def test_unexpected_errors_do_not_return_internals(self):
        def boom(*a, **k):
            raise KeyError("secret-internal-name")
        self.studio_.summary = boom
        status, err, _ = self.request("GET", "/api/state")
        self.assertEqual(status, 500)
        self.assertNotIn("secret-internal-name", err["error"])
        self.assertIn("log", err["error"])

    def test_token_file_is_private(self):
        self.assertEqual(os.stat(self.studio_.paths.web_token_file).st_mode & 0o777, 0o600)


class SessionTests(WebCase):
    """A-02: one permanent shared token for every browser; no list, no revocation."""

    def test_sessions_are_listed_and_revoked_one_by_one(self):
        other, other_session = self.web.sessions.create(
            "developer", "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Safari/604.1",
            "192.168.0.50")
        status, data, _ = self.request("GET", "/api/security")
        self.assertEqual(status, 200)
        labels = {s["id"]: s["label"] for s in data["sessions"]}
        self.assertEqual(labels[other_session.id], "iPhone · Safari")
        self.assertEqual(self.request("GET", "/api/state", cookie=other)[0], 200)
        self.assertEqual(self.request("POST", "/api/security/revoke",
                                      {"id": other_session.id})[0], 200)
        self.assertEqual(self.request("GET", "/api/state", cookie=other)[0], 401)
        self.assertEqual(self.request("GET", "/api/state")[0], 200)   # this one stays
        events = [e["event"] for e in self.request("GET", "/api/security")[1]["events"]]
        self.assertIn("session-revoked", events)

    def test_only_hashes_are_stored(self):
        with open(self.studio_.paths.web_sessions_file) as handle:
            text = handle.read()
        self.assertNotIn(self.cookie, text)
        self.assertEqual(os.stat(self.studio_.paths.web_sessions_file).st_mode & 0o777, 0o600)

    def test_idle_sessions_expire(self):
        store = self.web.sessions
        value, session = store.create("developer", "x")
        session.last_seen = session.created = time.time() - 31 * 86400
        self.assertIsNone(store.check(value))

    def test_own_logout_and_sign_out_everywhere(self):
        other, _ = self.web.sessions.create("developer", "x")
        status, _, response = self.request("POST", "/api/session/logout", {}, cookie=other)
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", response.getheader("Set-Cookie"))
        self.assertEqual(self.request("GET", "/api/state", cookie=other)[0], 401)
        third, _ = self.web.sessions.create("developer", "x")
        self.web.rotate_token()                         # LCD: Sign out all computers
        self.assertEqual(self.request("GET", "/api/state", cookie=third)[0], 401)
        self.assertEqual(self.request("GET", "/api/state")[0], 401)

    def test_browsers_paired_before_0_2_keep_working_once_migrated(self):
        status, _, response = self.request("GET", "/api/state", cookie=self.web.token)
        self.assertEqual(status, 200)
        migrated = cookie_of(response)
        self.assertTrue(migrated and migrated != self.web.token)
        self.assertEqual(self.request("GET", "/api/state", cookie=migrated)[0], 200)
        sources = [s["source"] for s in self.web.sessions.list()]
        self.assertIn("legacy", sources)


class PairingTests(WebCase):
    def test_pairing_only_while_a_pairing_screen_is_open(self):
        status, err, _ = self.request("POST", "/api/pair", {"code": "123456"}, cookie=False)
        self.assertEqual(status, 403)
        self.assertIn("Pairing is closed", err["error"])
        self.studio_.settings.set("web.lan", True, save=False)
        window = self.web.open_pairing("developer")
        code = window.code
        status, data, response = self.request("POST", "/api/pair", {"code": code}, cookie=False)
        self.assertEqual(status, 200)
        self.assertEqual(data["role"], "developer")
        value = cookie_of(response)
        self.assertEqual(self.request("GET", "/api/state", cookie=value)[0], 200)
        self.assertNotEqual(self.web.pair_code(), code)          # single use
        self.assertEqual(self.request("POST", "/api/pair", {"code": code}, cookie=False)[0], 403)
        self.web.close_pairing()
        self.assertEqual(self.web.pair_code(), "")

    def test_qr_link_is_single_use(self):
        window = self.web.open_pairing("viewer")
        token = window.qr_token
        status, _, response = self.request("GET", f"/?p={token}", cookie=False)
        self.assertEqual(status, 303)
        value = cookie_of(response)
        state = self.request("GET", "/api/state", cookie=value)[1]
        self.assertEqual(state["session"]["role"], "viewer")
        status, _, response = self.request("GET", f"/?p={token}", cookie=False)
        self.assertEqual(response.getheader("Location"), "/?used=1")

    def test_wrong_codes_close_the_window_and_tries_are_rate_limited(self):
        window = self.web.open_pairing("developer")
        wrong = "000000" if window.code != "000000" else "111111"
        for _ in range(4):
            self.assertEqual(self.request("POST", "/api/pair", {"code": wrong},
                                          cookie=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/pair", {"code": window.code},
                                      cookie=False)[0], 200)
        self.assertEqual(self.request("POST", "/api/pair", {"code": wrong},
                                      cookie=False)[0], 429)
        self.web._pair_attempts = []
        window = self.web.open_pairing("developer")
        for _ in range(10):
            window.wrong += 1
        self.assertIsNone(self.web.sessions.pairing())

    def test_an_admin_browser_can_show_a_pairing_qr(self):
        self.studio_.settings.set("web.lan", True, save=False)
        self.web.restart()
        self.cookie, _ = self.web.sessions.create("admin", "test")
        status, data, _ = self.request("POST", "/api/security/pair", {"role": "developer"})
        self.assertEqual(status, 200, data)
        pairing = data["pairing"]
        self.assertTrue(pairing["open"])
        self.assertEqual(len(pairing["code"]), 6)
        if pairing["url"]:                                # needs a network address
            self.assertIn("/?p=", pairing["url"])
            self.assertTrue(pairing["qr"] and set(pairing["qr"][0]) <= {"0", "1"})


class RoleTests(WebCase):
    """A-16: every paired browser had the terminal and any folder on the device."""

    def make_project(self):
        _, data, _ = self.request("POST", "/api/projects/create",
                                  {"name": "Roles", "board_id": "generic-host", "toolchain": "make"})
        return data["project"]["id"]

    def test_developer_cannot_use_the_terminal_or_device_settings(self):
        pid = self.make_project()
        dev, _ = self.web.sessions.create("developer", "phone")
        self.assertEqual(self.request("POST", "/api/term/open", {"project_id": pid},
                                      cookie=dev)[0], 403)
        self.assertEqual(self.request("POST", "/api/settings",
                                      {"values": {"ai.claude_path": "/tmp/evil"}},
                                      cookie=dev)[0], 403)
        self.assertEqual(self.request("POST", "/api/settings", {"values": {"web.lan": True}},
                                      cookie=dev)[0], 403)
        self.assertEqual(self.request("POST", "/api/settings", {"values": {"ui.font_size": 16}},
                                      cookie=dev)[0], 200)
        self.assertEqual(self.request("POST", "/api/projects/open", {"path": self.home},
                                      cookie=dev)[0], 403)
        status, _, _ = self.request("GET", f"/api/project/{pid}/file?path=src/main.c&project=x",
                                    cookie=dev)
        self.assertEqual(status, 200)
        status, err, _ = self.request("POST", "/api/upload/prepare",
                                      {"project_id": self.home}, cookie=dev)
        self.assertEqual(status, 403)
        self.assertIn("full access", err["error"])
        self.assertEqual(self.request("GET", "/api/security", cookie=dev)[0], 403)
        self.assertEqual(self.request("POST", "/api/monitor/clear", {}, cookie=dev)[0], 200)

    def test_viewer_only_reads(self):
        pid = self.make_project()
        viewer, _ = self.web.sessions.create("viewer", "tablet")
        self.assertEqual(self.request("GET", f"/api/project/{pid}", cookie=viewer)[0], 200)
        self.assertEqual(self.request("POST", f"/api/project/{pid}/build", {"action": "build"},
                                      cookie=viewer)[0], 403)
        self.assertEqual(self.request("POST", f"/api/project/{pid}/file",
                                      {"path": "x.c", "text": ""}, cookie=viewer)[0], 403)

    def test_admin_changes_roles_but_not_its_own_downwards(self):
        _, dev = self.web.sessions.create("developer", "phone")
        me = self.request("GET", "/api/session")[1]["session"]["id"]
        self.assertEqual(self.request("POST", "/api/security/role",
                                      {"id": dev.id, "role": "admin"})[0], 200)
        self.assertEqual(self.web.sessions.get(dev.id).role, "admin")
        self.assertEqual(self.request("POST", "/api/security/role",
                                      {"id": me, "role": "viewer"})[0], 400)


class ConnectionTests(WebCase):
    """A-08: idle and vanished clients held server threads and event-stream slots."""

    def test_idle_connections_are_closed(self):
        import socket as _socket
        self.web.idle_timeout = 0.5
        sock = _socket.create_connection(("127.0.0.1", self.web.port), timeout=5)
        self.addCleanup(sock.close)
        start = time.time()
        self.assertEqual(sock.recv(10), b"")            # the server hung up
        self.assertLess(time.time() - start, 4)

    def test_event_stream_sockets_drop_vanished_clients(self):
        import socket as _socket
        from app.web.server import STREAM_USER_TIMEOUT_MS, tune_stream_socket
        listener = _socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        client = _socket.create_connection(listener.getsockname())
        self.addCleanup(client.close)
        server_side, _ = listener.accept()
        self.addCleanup(server_side.close)
        tune_stream_socket(server_side)
        self.assertEqual(server_side.getsockopt(_socket.SOL_SOCKET, _socket.SO_KEEPALIVE), 1)
        if hasattr(_socket, "TCP_USER_TIMEOUT"):
            self.assertEqual(server_side.getsockopt(_socket.IPPROTO_TCP,
                                                    _socket.TCP_USER_TIMEOUT),
                             STREAM_USER_TIMEOUT_MS)


class ZipImportTests(WebCase):
    """A-18: every zip import failed ("Invalid JSON."); A-11: memory and leftovers."""

    def zip_bytes(self, files):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            for name, text in files.items():
                archive.writestr(name, text)
        return data.getvalue()

    def post_zip(self, payload, name="blink.zip"):
        conn = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=10)
        conn.request("POST", "/api/zip", body=payload, headers={
            "Host": f"127.0.0.1:{self.web.port}", "Cookie": f"mcode_session={self.cookie}",
            "X-mCode": "1", "X-Filename": name, "Content-Type": "application/zip"})
        response = conn.getresponse()
        data = json.loads(response.read().decode() or "null")
        conn.close()
        return response.status, data

    def test_zip_import_works_and_refuses_escapes(self):
        root = self.studio_.projects.ensure_root()
        os.makedirs(os.path.join(root, "blink.importing", "old"))       # an interrupted import
        status, data = self.post_zip(self.zip_bytes({"blink/Makefile": "all:\n",
                                                     "blink/src/main.c": "int main(){}\n"}))
        self.assertEqual(status, 200, data)
        path = data["project"]["path"]
        self.assertTrue(os.path.isfile(os.path.join(path, "src", "main.c")))
        self.assertFalse(os.path.exists(os.path.join(root, "blink.importing")))
        status, data = self.post_zip(self.zip_bytes({"ok/Makefile": "all:\n",
                                                     "../evil.txt": "x"}), "evil.zip")
        self.assertEqual(status, 400)
        self.assertIn("Unsafe path", data["error"])


class ApiTests(WebCase):
    def test_projects_files_and_boundaries(self):
        status, data, _ = self.request("POST", "/api/projects/create",
                                       {"name": "Web Robot", "board_id": "generic-host",
                                        "toolchain": "make"})
        self.assertEqual(status, 200, data)
        pid = data["project"]["id"]
        status, data, _ = self.request("GET", f"/api/project/{pid}")
        self.assertIn("src/main.c", [e["path"] for e in data["tree"]])
        status, data, _ = self.request("GET", f"/api/project/{pid}/file?path=src/main.c")
        sha = data["sha"]
        status, saved, _ = self.request("POST", f"/api/project/{pid}/file",
                                        {"path": "src/main.c", "text": "int main(void){return 0;}\n",
                                         "sha": sha})
        self.assertEqual(status, 200, saved)
        status, err, _ = self.request("POST", f"/api/project/{pid}/file",
                                      {"path": "src/main.c", "text": "x", "sha": sha})
        self.assertEqual(status, 400)
        self.assertIn("changed on disk", err["error"])
        for bad in ("../../etc/passwd", "/etc/passwd"):
            status, err, _ = self.request("GET", f"/api/project/{pid}/file?path={bad}")
            self.assertEqual(status, 400, bad)
        status, hits, _ = self.request("GET", f"/api/project/{pid}/search?q=return")
        self.assertIn("src/main.c", [h["path"] for h in hits["hits"]])
        status, exported, response = self.request("GET", f"/api/project/{pid}/export")
        self.assertEqual(response.getheader("Content-Type"), "application/zip")

    def test_delete_needs_the_name(self):
        _, data, _ = self.request("POST", "/api/projects/create", {"name": "Gone", "board_id": "uno"})
        pid = data["project"]["id"]
        self.assertEqual(self.request("POST", f"/api/project/{pid}/delete", {"confirm": "no"})[0], 400)
        self.assertEqual(self.request("POST", f"/api/project/{pid}/delete", {"confirm": "Gone"})[0], 200)

    def test_build_through_the_api_and_events(self):
        cli = write_script(os.path.join(self.tmp, "tools", "arduino-cli"), FAKE_ARDUINO_CLI)
        core = os.path.join(self.home, ".arduino15", "packages", "esp32", "hardware", "esp32", "3.3.12")
        os.makedirs(core)
        open(os.path.join(core, "boards.txt"), "w").close()
        self.studio_.settings.set("dev.arduino_cli", cli, save=False)
        self.studio_.tools.refresh(wait=True, only=["arduino-cli"])
        _, data, _ = self.request("POST", "/api/projects/create",
                                  {"name": "Events", "board_id": "esp32-s3", "toolchain": "arduino-cli"})
        pid = data["project"]["id"]
        seen = []
        conn = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=10)
        conn.request("GET", "/api/events", headers={"Host": f"127.0.0.1:{self.web.port}",
                                                    "Cookie": f"mcode_session={self.web.token}"})
        stream = conn.getresponse()
        self.assertEqual(stream.getheader("Content-Type"), "text/event-stream")

        def read():
            for raw in stream:
                if raw.startswith(b"data: "):
                    seen.append(json.loads(raw[6:])["topic"])
                    if "build" in seen:
                        return
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        time.sleep(0.2)
        status, job, _ = self.request("POST", f"/api/project/{pid}/build", {"action": "build"})
        self.assertEqual(status, 200, job)
        reader.join(15)
        conn.close()
        self.assertIn("job.line", seen)
        self.assertIn("build", seen)
        status, job, _ = self.request("GET", f"/api/job/{job['id']}?lines=50")
        self.assertEqual(job["state"], "succeeded")

    def test_upload_start_needs_explicit_confirmation(self):
        status, err, _ = self.request("POST", "/api/upload/start", {"token": "x"})
        self.assertEqual(status, 400)
        status, err, _ = self.request("POST", "/api/upload/start", {"token": "x", "confirmed": True})
        self.assertEqual(status, 400)
        self.assertIn("expired", err["error"])

    def test_settings_validation(self):
        status, data, _ = self.request("POST", "/api/settings", {"values": {"ai.mode": "bad",
                                                                           "hw.default_baud": 9600}})
        self.assertIn("ai.mode", data["errors"])
        self.assertEqual(self.studio_.settings.get("hw.default_baud"), 9600)

    def test_devices_and_monitor_status(self):
        status, data, _ = self.request("GET", "/api/devices")
        self.assertEqual(len(data["devices"]), 2)
        status, data, _ = self.request("GET", "/api/monitor")
        self.assertFalse(data["connected"])

    def test_terminal_runs_a_shell_in_the_project(self):
        _, data, _ = self.request("POST", "/api/projects/create", {"name": "Term", "board_id": "uno"})
        pid = data["project"]["id"]
        status, term, _ = self.request("POST", "/api/term/open", {"project_id": pid})
        self.assertEqual(status, 200, term)
        self.request("POST", f"/api/term/{term['id']}/input", {"data": "echo MCODE_$((40+2))\n"})
        deadline = time.time() + 5
        text = ""
        while time.time() < deadline and "MCODE_42" not in text:
            time.sleep(0.1)
            _, buf, _ = self.request("POST", f"/api/term/{term['id']}/buffer", {})
            text = base64.b64decode(buf["data"]).decode(errors="replace")
        self.assertIn("MCODE_42", text)
        self.request("POST", f"/api/term/{term['id']}/close", {})


class QrTests(unittest.TestCase):
    def test_structure(self):
        code = qr.QrCode(b"https://claude.ai/oauth/authorize?z-abc", "M")
        modules = code.modules
        for x, y in ((0, 0), (code.size - 7, 0), (0, code.size - 7)):
            self.assertTrue(all(modules[y][x + i] for i in range(7)))       # finder top edges
        self.assertTrue(modules[code.size - 8][8])                           # dark module

    def test_decodes_with_opencv(self):
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("OpenCV is not installed here")
        detector = cv2.QRCodeDetector()
        for text in ("https://claude.ai/oauth/authorize?z-vn_BVEXIc7Qe0Ssvu6j6aV4M4sofEB4mWverS5t3O0Y",
                     "http://192.168.0.248:8765/?t=" + "x" * 43, "hi"):
            code = qr.QrCode(text.encode(), "M")
            scale, quiet = 8, 4
            n = code.size + 2 * quiet
            image = np.full((n * scale, n * scale), 255, np.uint8)
            for y, row in enumerate(code.modules):
                for x, dark in enumerate(row):
                    if dark:
                        image[(y + quiet) * scale:(y + quiet + 1) * scale,
                              (x + quiet) * scale:(x + quiet + 1) * scale] = 0
            value, _points, _ = detector.detectAndDecode(image)
            self.assertEqual(value, text)


if __name__ == "__main__":
    unittest.main()


class BoardSettingsApiTests(WebCase):
    def test_get_and_change_board_settings(self):
        status, data, _ = self.request("POST", "/api/projects/create",
                                       {"name": "Board API", "board_id": "esp32-c5",
                                        "toolchain": "esp-idf", "framework": "espidf"})
        self.assertEqual(status, 200, data)
        pid = data["project"]["id"]
        status, data, _ = self.request("GET", f"/api/project/{pid}/board")
        self.assertEqual(status, 200, data)
        self.assertEqual([s["id"] for s in data["settings"]], ["board", "upload_speed", "fast_build"])
        status, data, _ = self.request("POST", f"/api/project/{pid}/board",
                                       {"id": "board", "value": "esp32-c3"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["project"]["board_id"], "esp32-c3")
        status, data, _ = self.request("POST", f"/api/project/{pid}/board",
                                       {"id": "upload_speed", "value": "1"})
        self.assertEqual(status, 400, data)
