"""mCode under the REAL whisplay-daemon code (Level 3: host contract).

Uses mFruit OS's own harness (``MFruitOS/tests/real_daemon``: the bundled
daemon with a simulated board). Skipped without an mFruit OS checkout
(``MFRUIT_OS``, default ~/MFruitOS). Checks: launch -> focus and first frame,
a button tap reaches mCode, 4x from Home leaves, ``app.exit.request`` ends
mCode within the 3-second rule.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MFRUIT_OS = os.path.expanduser(os.environ.get("MFRUIT_OS", "~/MFruitOS"))


def _harness():
    """mFruit OS's harness module, loaded by path (its tests folder is not a package,
    so ``import tests...`` would find mCode's own tests instead)."""
    import importlib.util
    path = os.path.join(MFRUIT_OS, "tests", "real_daemon", "__init__.py")
    if not os.path.isfile(path):
        return None
    spec = importlib.util.spec_from_file_location("mfruit_real_daemon", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RealDaemon, module.find_whisplay_src


@unittest.skipIf(os.environ.get("MCODE_SKIP_REAL_DAEMON"), "disabled by MCODE_SKIP_REAL_DAEMON")
class RealDaemonContractTests(unittest.TestCase):
    def setUp(self):
        harness = _harness()
        if harness is None or harness[1]() is None:
            self.skipTest("no mFruit OS checkout with the real-daemon harness")
        RealDaemon, find_src = harness
        self.home = tempfile.mkdtemp(prefix="mcode-rd-", dir="/tmp")   # short socket paths
        self.addCleanup(shutil.rmtree, self.home, True)
        self.daemon = RealDaemon(self.home)
        self.log = os.path.join(self.home, "mcode.log")
        command = (f"env WHISPLAY_DAEMON_SOCKET={self.daemon.socket_path} "
                   f"XDG_DATA_HOME={self.home}/xdg MFRUIT_KEYS_SOCKET=/nonexistent "
                   f"PYTHONDONTWRITEBYTECODE=1 {ROOT}/run.sh --lcd --no-browser --port 0 "
                   f">> {self.log} 2>&1")
        with open(os.path.join(self.daemon.apps_dir, "mcode.json"), "w") as handle:
            json.dump({"app_id": "mcode", "display_name": "mCode Studio", "icon": "mC",
                       "launch_command": command, "cwd": ROOT, "priority": 5, "persist": True,
                       "exit_gesture": "none"}, handle)
        self.daemon.start(find_src())
        self.addCleanup(self._stop)

    def _stop(self):
        try:
            self.daemon._cmd("quit")
        except Exception:
            pass
        if self.daemon.proc is not None:
            try:
                self.daemon.proc.wait(5)
            except Exception:
                self.daemon.proc.kill()

    def request(self, cmd, payload):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            sock.connect(self.daemon.socket_path)
            sock.sendall((json.dumps({"version": 1, "cmd": cmd, "payload": payload}) + "\n").encode())
            return json.loads(sock.makefile().readline())

    def wait(self, predicate, limit=20.0):
        end = time.time() + limit
        while time.time() < end:
            state = self.daemon.state()
            if predicate(state):
                return state
            time.sleep(0.1)
        return self.daemon.state()

    def tap(self):
        self.daemon.press()
        time.sleep(0.06)
        self.daemon.release()

    def log_text(self):
        try:
            with open(self.log) as handle:
                return handle.read()
        except OSError:
            return ""

    def test_launch_input_and_exit(self):
        desktop = self.daemon.state()["lcd"]
        started = time.time()
        self.assertTrue(self.request("app.launch", {"app_id": "mcode"}).get("ok"))
        state = self.wait(lambda s: s["foreground"] == "mcode" and s["lcd"] != desktop)
        self.assertEqual(state["foreground"], "mcode", self.log_text())
        self.assertNotEqual(state["lcd"], desktop, "mCode drew no frame")
        self.assertLess(time.time() - started, 15)
        home_frame = state["lcd"]
        self.tap()                                         # next tile
        state = self.wait(lambda s: s["lcd"] != home_frame, 3)
        self.assertNotEqual(state["lcd"], home_frame, "the button did not reach mCode")
        for _ in range(4):                                 # 4x = back: leaves from Home
            self.tap()
            time.sleep(0.08)
        state = self.wait(lambda s: "mcode" not in s["running"], 10)
        self.assertNotIn("mcode", state["running"], self.log_text())
        self.assertNotIn("Traceback", self.log_text())

    def test_exit_request_within_three_seconds(self):
        self.request("app.launch", {"app_id": "mcode"})
        self.wait(lambda s: s["foreground"] == "mcode", 20)
        time.sleep(1.0)
        started = time.time()
        self.assertTrue(self.request("app.exit.request", {"app_id": "mcode"}).get("ok"))
        state = self.wait(lambda s: "mcode" not in s["running"], 6)
        self.assertNotIn("mcode", state["running"])
        self.assertLess(time.time() - started, 3.0)


if __name__ == "__main__":
    unittest.main()
