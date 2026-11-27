"""Shared test helpers: temporary studios, fake tools, a fake clock."""

from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def write_script(path: str, body: str) -> str:
    """An executable Python script (uses this interpreter)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(f"#!{sys.executable}\n" + textwrap.dedent(body))
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mcode-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)

    def studio(self, hardware=True, ai=True, voice=False, devices=True):
        from app.preview import make_sysfs
        from app.studio import Studio
        sysfs = make_sysfs(os.path.join(self.tmp, "sys")) if devices else os.path.join(self.tmp, "nosys")
        devroot = os.path.join(self.tmp, "dev")
        os.makedirs(devroot, exist_ok=True)
        for name in ("ttyUSB0", "ttyACM0"):
            open(os.path.join(devroot, name), "a").close()
        studio = Studio(data_dir=os.path.join(self.tmp, "data"), home=self.home, hardware=hardware,
                        ai=ai, voice=voice, sysfs=sysfs, devroot=devroot)
        studio.settings.set("dev.projects_dir", os.path.join(self.tmp, "projects"), save=False)
        studio.web = None
        self.addCleanup(studio.stop, 0.5)
        if studio.devices is not None:
            studio.devices.proc = os.path.join(self.tmp, "noproc")
            studio.devices._core_ids = lambda: {}
            studio.devices._core_cache = None
            studio.devices.scan()
        return studio


FAKE_ARDUINO_CLI = r'''
import json, os, sys
args = sys.argv[1:]
log = os.environ.get("FAKE_TOOL_LOG")
if log:
    with open(log, "a") as handle:
        handle.write(json.dumps(args) + "\n")
if args[:1] == ["version"]:
    print(json.dumps({"VersionString": "1.6.0"}))
    sys.exit(0)
if args[:1] == ["compile"]:
    build = args[args.index("--build-path") + 1]
    sketch = args[-1]
    name = os.path.basename(sketch.rstrip("/"))
    if os.environ.get("FAKE_COMPILE_SLOW"):
        import time
        os.makedirs(os.path.join(build, "core"), exist_ok=True)
        open(os.path.join(build, "core", "core.a"), "wb").close()     # half-written
        time.sleep(float(os.environ["FAKE_COMPILE_SLOW"]))
    if os.environ.get("FAKE_COMPILE_FAIL"):
        print(f"{sketch}/{name}.ino:12:5: error: 'servo' was not declared in this scope")
        print(f"{sketch}/{name}.ino:3:10: fatal error: ServoDriver.h: No such file or directory")
        print("Error during build: exit status 1")
        sys.exit(1)
    os.makedirs(build, exist_ok=True)
    with open(os.path.join(build, name + ".ino.bin"), "wb") as handle:
        handle.write(b"\x00" * 4096)
    print("Sketch uses 301922 bytes (23%) of program storage space. Maximum is 1310720 bytes.")
    print("Global variables use 22136 bytes (6%) of dynamic memory, leaving 305544 bytes for local variables. Maximum is 327680 bytes.")
    sys.exit(0)
if args[:1] == ["upload"]:
    port = args[args.index("-p") + 1]
    print(f"Connecting to {port}...")
    print("Chip type: ESP32-S3 (QFN56) (revision v0.2)")
    for pct in ("10.0", "55.5", "100.0"):
        sys.stdout.write(f"Writing at 0x00010000 [=====>   ] {pct}% 1000/2000 bytes...\r")
        sys.stdout.flush()
    print()
    print("Hash of data verified.")
    print("Hard resetting via RTS pin...")
    sys.exit(int(os.environ.get("FAKE_UPLOAD_EXIT", "0")))
print("unknown command", args)
sys.exit(2)
'''


FAKE_CLAUDE = r'''
import json, os, sys, time
args = sys.argv[1:]
if os.environ.get("FAKE_CLAUDE_ARGS"):
    with open(os.environ["FAKE_CLAUDE_ARGS"], "a") as handle:
        handle.write(json.dumps({"args": args, "cwd": os.getcwd()}) + "\n")
if args == ["--version"]:
    print("9.9.9 (Claude Code)")
    sys.exit(0)
if args[:2] == ["auth", "status"]:
    logged = os.environ.get("FAKE_CLAUDE_LOGGED", "1") == "1"
    print(json.dumps({"loggedIn": logged, "authMethod": "claude.ai" if logged else "none",
                      "subscriptionType": "pro" if logged else None,
                      "email": "owner@example.com", "orgName": "Owner's Organization"}))
    sys.exit(0 if logged else 1)
if args[:2] == ["auth", "logout"]:
    sys.exit(0)
if args[:2] == ["auth", "login"]:
    print("Opening browser to sign in…")
    print("If the browser didn't open, visit:")
    print("\x1b]8;;https://claude.ai/oauth/authorize?fake123\x07https://claude.ai/oauth/authorize?fake123\x1b]8;;\x07")
    sys.stdout.write("Paste code here if prompted > ")
    sys.stdout.flush()
    code = sys.stdin.readline().strip()
    if code == "GOOD":
        print("Login successful.")
        sys.exit(0)
    print("OAuth error: invalid code")
    sys.exit(1)
if "-p" not in args:
    sys.exit(2)
scenario = os.environ.get("FAKE_CLAUDE_SCENARIO", "edit")
session = ""
if "--session-id" in args:
    session = args[args.index("--session-id") + 1]
if "--resume" in args:
    session = args[args.index("--resume") + 1]
    if scenario == "nosession":
        print(f"No conversation found with session ID: {session}", file=sys.stderr)
        sys.exit(1)
prompt = args[args.index("--") + 1] if "--" in args else ""
cwd = os.getcwd()
def emit(obj):
    print(json.dumps(obj), flush=True)
emit({"type": "system", "subtype": "init", "session_id": session, "model": "fake",
      "permissionMode": "acceptEdits", "apiKeySource": "none", "cwd": cwd})
if scenario == "fail":
    print("API Error: usage limit reached", file=sys.stderr)
    sys.exit(1)
if scenario == "slow":
    time.sleep(30)
candidates = []
for root, dirs, files in os.walk(cwd):
    rel = os.path.relpath(root, cwd)
    for name in files:
        if name.endswith((".ino", ".cpp", ".c")) and not rel.startswith("test"):
            candidates.append(os.path.join(root, name))
candidates.sort(key=lambda p: (not os.path.basename(p).startswith("main"), p))
target = candidates[0] if candidates else None
text = "Nothing to change."
if scenario in ("edit", "risky", "nosession") and target:
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Edit",
          "input": {"file_path": target}}]}})
    with open(target, "a") as handle:
        handle.write("\n// changed by fake Claude\n")
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1"}]}})
    text = "Added a comment.\nSUMMARY: Added a comment to the main file."
if scenario == "risky":
    with open(os.path.join(cwd, "partitions.csv"), "w") as handle:
        handle.write("nvs, data, nvs, 0x9000, 0x5000\n")
    text = "Changed the partition table.\nSUMMARY: New partition table."
if scenario == "mcp":
    import subprocess
    config = json.loads(args[args.index("--mcp-config") + 1])["mcpServers"]["mcode"]
    env = dict(os.environ, **config.get("env", {}))
    proc = subprocess.Popen([config["command"]] + config["args"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, env=env, text=True)
    def rpc(i, method, params=None):
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}}) + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())
    init = rpc(1, "initialize", {"protocolVersion": "2025-06-18"})
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    listed = rpc(2, "tools/list")
    names = [t["name"] for t in listed["result"]["tools"]]
    info = rpc(3, "tools/call", {"name": "project_info", "arguments": {}})
    build = rpc(4, "tools/call", {"name": "build", "arguments": {}})
    proc.stdin.close()
    proc.wait(5)
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "m1",
          "name": "mcp__mcode__build", "input": {}}]}})
    text = json.dumps({"server": init["result"]["serverInfo"]["name"], "tools": names,
                       "info_error": info["result"]["isError"],
                       "build": build["result"]["content"][0]["text"][:200],
                       "build_error": build["result"]["isError"]}) + "\nSUMMARY: mcp checked"
emit({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})
emit({"type": "result", "subtype": "success", "is_error": False, "result": text, "num_turns": 2,
      "duration_ms": 10, "total_cost_usd": 0.001, "session_id": session or "s-1",
      "permission_denials": []})
'''


FAKE_ARECORD = r'''
import struct, sys, time, math
# 48 kHz stereo: a 440 Hz tone on the left channel only, silence on the right.
# One 40 ms chunk is computed once and paced against the clock, so the fake
# delivers real-time audio even on a slow board.
rate = 48000
chunk = b"".join(struct.pack("<hh", int(8000 * math.sin(2 * math.pi * 440 * i / rate)), 0)
                 for i in range(1920))
out = sys.stdout.buffer
start = time.monotonic()
sent = 0
while True:
    try:
        out.write(chunk)
        out.flush()
    except BrokenPipeError:
        break
    sent += 1
    delay = start + sent * 0.04 - time.monotonic()
    if delay > 0:
        time.sleep(delay)
'''
