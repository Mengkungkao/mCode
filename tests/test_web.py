"""The desktop IDE server: access control, API, events, terminal."""

from __future__ import annotations

import base64
import http.client
import json
import os
import threading
import time
import unittest

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
        self.cookie = None

    def request(self, method, path, body=None, headers=None, cookie=True, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=10)
        all_headers = {"Host": host or f"127.0.0.1:{self.web.port}"}
        if cookie:
            all_headers["Cookie"] = f"mcode_session={self.web.token}"
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


class AccessTests(WebCase):
    def test_one_time_link_sets_a_strict_cookie(self):
        status, _, response = self.request("GET", f"/?t={self.web.token}", cookie=False)
        self.assertEqual(status, 303)
        cookie = response.getheader("Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertEqual(response.getheader("Location"), "/")

    def test_without_cookie_only_the_pairing_page(self):
        status, body, _ = self.request("GET", "/", cookie=False)
        self.assertEqual(status, 200)
        self.assertIn(b"Connect this browser", body)
        self.assertEqual(self.request("GET", "/api/state", cookie=False)[0], 401)
        self.assertEqual(self.request("GET", "/?t=wrong", cookie=False)[0], 200)

    def test_dns_rebinding_and_cross_site_posts_refused(self):
        self.assertEqual(self.request("GET", "/api/state", host="evil.example:80")[0], 403)
        status, _, _ = self.request("POST", "/api/projects/create", {"name": "x"},
                                    headers={"X-mCode": "0"})
        self.assertEqual(status, 403)

    def test_pairing_code_and_rate_limit(self):
        wrong = "000000" if self.web.pair_code() != "000000" else "111111"
        status, body, _ = self.request("POST", "/api/pair", {"code": wrong}, cookie=False)
        self.assertEqual(status, 403)
        status, _, response = self.request("POST", "/api/pair", {"code": self.web.pair_code()},
                                           cookie=False)
        self.assertEqual(status, 200)
        self.assertIn(self.web.token, response.getheader("Set-Cookie"))
        for _ in range(4):
            self.request("POST", "/api/pair", {"code": wrong}, cookie=False)
        self.assertEqual(self.request("POST", "/api/pair", {"code": self.web.pair_code()},
                                      cookie=False)[0], 429)

    def test_security_headers_and_static_files(self):
        status, body, response = self.request("GET", "/static/app.js", cookie=False)
        self.assertEqual(status, 200)
        self.assertIn("script-src 'self'", response.getheader("Content-Security-Policy"))
        self.assertEqual(response.getheader("X-Frame-Options"), "DENY")
        self.assertEqual(self.request("GET", "/static/../server.py", cookie=False)[0], 404)

    def test_token_file_is_private(self):
        self.assertEqual(os.stat(self.studio_.paths.web_token_file).st_mode & 0o777, 0o600)


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
        self.assertEqual([s["id"] for s in data["settings"]], ["board", "upload_speed"])
        status, data, _ = self.request("POST", f"/api/project/{pid}/board",
                                       {"id": "board", "value": "esp32-c3"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["project"]["board_id"], "esp32-c3")
        status, data, _ = self.request("POST", f"/api/project/{pid}/board",
                                       {"id": "upload_speed", "value": "1"})
        self.assertEqual(status, 400, data)
