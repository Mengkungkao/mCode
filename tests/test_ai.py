"""The AI Engineer with a fake Claude Code CLI (no network, no account)."""

from __future__ import annotations

import json
import os
import threading
import time
import unittest

from app.ai import context
from app.ai.claude import auth_summary, describe_tool, translate
from app.ai.service import AIError
from app.ai.workspace import classify

from .helpers import FAKE_ARDUINO_CLI, FAKE_CLAUDE, TempCase, write_script


class AITestCase(TempCase):
    def setUp(self):
        super().setUp()
        self.claude = write_script(os.path.join(self.tmp, "tools", "claude"), FAKE_CLAUDE)
        self.args_log = os.path.join(self.tmp, "claude-args.jsonl")
        self.env = {"FAKE_CLAUDE_ARGS": self.args_log, "FAKE_CLAUDE_SCENARIO": "edit",
                    "FAKE_CLAUDE_LOGGED": "1"}
        for key, value in self.env.items():
            os.environ[key] = value
            self.addCleanup(os.environ.pop, key, None)

    def scenario(self, name):
        os.environ["FAKE_CLAUDE_SCENARIO"] = name

    def make(self, mode="ask", toolchain="make", board="generic-host"):
        studio = self.studio(hardware=True, ai=True)
        studio.settings.set("ai.claude_path", self.claude, save=False)
        studio.settings.set("ai.mode", mode, save=False)
        studio.tools.refresh(wait=True, only=["claude"])
        studio.ai.start()
        studio.ai.refresh_status(wait=True)
        project = studio.projects.create("Robot", board, toolchain, init_git=False)
        return studio, project

    def run_turn(self, studio, project, text="Add a comment", **kw):
        turn = studio.ai.ask(project, text, **kw)
        studio.jobs.wait(turn.job_id, 30)
        deadline = time.time() + 5
        while turn.state == "working" and time.time() < deadline:
            time.sleep(0.02)
        return turn

    def calls(self):
        with open(self.args_log) as handle:
            return [json.loads(line) for line in handle if line.strip()]


class AskModeTests(AITestCase):
    def test_changes_wait_for_approval_then_apply_with_a_backup(self):
        studio, project = self.make("ask")
        main = os.path.join(project.path, "src", "main.c")
        before = open(main).read()
        turn = self.run_turn(studio, project)
        self.assertEqual(turn.state, "review", turn.error)
        self.assertEqual(open(main).read(), before, "the project changed before approval")
        review = studio.ai.review(project)
        self.assertEqual([c["path"] for c in review["changes"]], ["src/main.c"])
        self.assertIn("+// changed by fake Claude", review["changes"][0]["diff"])
        self.assertEqual(turn.summary, "Added a comment to the main file.")
        with self.assertRaises(AIError):                       # one review at a time
            studio.ai.ask(project, "again")
        result = studio.ai.approve(project)
        self.assertEqual(result["applied"], ["src/main.c"])
        self.assertIn("changed by fake Claude", open(main).read())
        self.assertIn("before-ai-apply", [b["reason"] for b in studio.projects.backups(project)])
        kinds = [r["kind"] for r in studio.ai.actions(project.id)]
        for kind in ("request", "tool", "result", "approve"):
            self.assertIn(kind, kinds)

    def test_reject_keeps_the_project(self):
        studio, project = self.make("ask")
        main = os.path.join(project.path, "src", "main.c")
        before = open(main).read()
        self.run_turn(studio, project)
        studio.ai.reject(project)
        self.assertEqual(open(main).read(), before)
        self.assertIsNone(studio.ai.review(project))

    def test_restricted_cli_arguments(self):
        studio, project = self.make("ask")
        self.run_turn(studio, project)
        call = [c for c in self.calls() if "-p" in c["args"]][-1]
        args = call["args"]
        self.assertIn("--restricted", args)
        self.assertEqual(args[args.index("--tools") + 1], "Read,Edit,Write,Glob,Grep")
        self.assertEqual(args[args.index("--permission-mode") + 1], "acceptEdits")
        self.assertEqual(args[args.index("--permission-prompts") + 1], "none")
        self.assertIn("--strict-mcp-config", args)
        allowed = args[args.index("--allowedTools") + 1].split(",")
        self.assertNotIn("mcp__mcode__build", allowed)          # Ask mode cannot build
        self.assertFalse([a for a in allowed if "upload" in a or "erase" in a or "flash" in a])
        self.assertTrue(call["cwd"].startswith(studio.paths.workspaces))
        prompt = args[args.index("--") + 1]
        self.assertIn("Board: Generic C/C++ (Make)", prompt)
        self.assertIn("Request: Add a comment", prompt)

    def test_session_continues_and_recovers_when_lost(self):
        studio, project = self.make("ask")
        self.run_turn(studio, project)
        studio.ai.reject(project)
        self.run_turn(studio, project)
        studio.ai.reject(project)
        calls = [c["args"] for c in self.calls() if "-p" in c["args"]]
        self.assertIn("--session-id", calls[0])
        self.assertIn("--resume", calls[1])
        self.scenario("nosession")
        turn = self.run_turn(studio, project)
        self.assertEqual(turn.state, "review", turn.error)
        calls = [c["args"] for c in self.calls() if "-p" in c["args"]]
        self.assertIn("--resume", calls[-2])
        self.assertIn("--session-id", calls[-1])


class InPlaceModeTests(AITestCase):
    def test_assisted_applies_and_undo_restores(self):
        studio, project = self.make("assisted")
        main = os.path.join(project.path, "src", "main.c")
        before = open(main).read()
        turn = self.run_turn(studio, project)
        self.assertEqual(turn.state, "done", turn.error)
        self.assertEqual(turn.applied, ["src/main.c"])
        self.assertIn("changed by fake Claude", open(main).read())
        self.assertIn("before-ai", [b["reason"] for b in studio.projects.backups(project)])
        studio.ai.undo(project)
        self.assertEqual(open(main).read(), before)
        call = [c for c in self.calls() if "-p" in c["args"]][-1]
        self.assertEqual(os.path.realpath(call["cwd"]), os.path.realpath(project.path))
        self.assertIn("mcp__mcode__build", call["args"][call["args"].index("--allowedTools") + 1])

    def test_risky_changes_are_held_until_kept(self):
        studio, project = self.make("assisted")
        self.scenario("risky")
        turn = self.run_turn(studio, project)
        self.assertEqual(turn.state, "review")
        partitions = os.path.join(project.path, "partitions.csv")
        self.assertFalse(os.path.exists(partitions), "a partition change reached the project "
                                                     "without approval")
        review = studio.ai.review(project)
        self.assertEqual(review["held"], ["partitions.csv"])
        self.assertIn("partition table", review["changes"][0]["risky"])
        studio.ai.approve(project)
        self.assertTrue(os.path.exists(partitions))

    def test_advanced_gets_git_through_mcode_and_no_shell(self):
        """A-05: Bash(git …) honoured the repository's config (fsmonitor, filters)."""
        studio, project = self.make("advanced")
        self.run_turn(studio, project)
        args = [c for c in self.calls() if "-p" in c["args"]][-1]["args"]
        self.assertEqual(args[args.index("--tools") + 1], "Read,Edit,Write,Glob,Grep")
        allowed = args[args.index("--allowedTools") + 1].split(",")
        self.assertFalse([a for a in allowed if a.startswith("Bash")], allowed)
        for tool in ("git_status", "git_diff", "git_log", "git_show", "git_commit"):
            self.assertIn(f"mcp__mcode__{tool}", allowed)

    def test_git_settings_changed_by_claude_are_put_back(self):
        studio, project = self.make("assisted")
        from app.projects import gitutil
        self.assertTrue(gitutil.init(project.path))
        config = os.path.join(project.path, ".git", "config")
        with open(config) as handle:
            before = handle.read()
        self.scenario("gitconfig")
        turn = self.run_turn(studio, project)
        with open(config) as handle:
            self.assertEqual(handle.read(), before)
        self.assertTrue(turn.notices)
        self.assertIn(".git", turn.notices[0])


class FailureAndAuthTests(AITestCase):
    def test_failure_is_explained(self):
        studio, project = self.make("ask")
        self.scenario("fail")
        turn = self.run_turn(studio, project)
        self.assertEqual(turn.state, "error")
        self.assertIn("usage limit", turn.error)

    def test_cancel(self):
        studio, project = self.make("assisted")
        self.scenario("slow")
        turn = studio.ai.ask(project, "slow one")
        time.sleep(0.5)
        self.assertTrue(studio.ai.cancel())
        studio.jobs.wait(turn.job_id, 10)
        deadline = time.time() + 5
        while turn.state == "working" and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(turn.state, "cancelled")

    def test_error_during_execution_is_explained_and_not_resumed(self):
        """2026-11-27, owner: "Not finished, error_during_execution". The reason is in
        the result's "errors"; a session that failed before its first turn has no
        conversation to continue, so it must not be resumed next time."""
        studio, project = self.make("ask")
        self.scenario("execerror")
        turn = self.run_turn(studio, project)
        self.assertEqual(turn.state, "error")
        self.assertIn("API Error: 500", turn.error)
        self.assertNotIn("error_during_execution", turn.error)
        self.assertFalse(studio.ai.project_ai(project.id).sessions)
        self.scenario("edit")
        turn = self.run_turn(studio, project)
        self.assertEqual(turn.state, "review", turn.error)
        call = [c["args"] for c in self.calls() if "-p" in c["args"]][-1]
        self.assertIn("--session-id", call)

    def test_max_turns_is_explained(self):
        studio, project = self.make("ask")
        self.scenario("maxturns")
        turn = self.run_turn(studio, project)
        self.assertIn("used up its steps", turn.error)

    def test_signed_out_refuses_with_login_fix(self):
        os.environ["FAKE_CLAUDE_LOGGED"] = "0"
        studio, project = self.make("ask")
        self.assertEqual(studio.ai.status()["state"], "signed-out")
        with self.assertRaises(AIError) as caught:
            studio.ai.ask(project, "hello")
        self.assertEqual(caught.exception.fix, "login")

    def test_status_never_carries_personal_details(self):
        studio, _project = self.make("ask")
        text = json.dumps(studio.ai.status())
        self.assertNotIn("owner@example.com", text)
        self.assertNotIn("Organization", text)
        self.assertIn("Claude Pro", text)

    def test_login_flow_with_code(self):
        studio, _project = self.make("ask")
        session = studio.ai.login_start()
        deadline = time.time() + 5
        while studio.ai.login_session.state != "waiting" and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(studio.ai.login_session.url, "https://claude.ai/oauth/authorize?fake123")
        studio.ai.login_code("GOOD")
        deadline = time.time() + 5
        while studio.ai.login_session.state not in ("done", "failed") and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(studio.ai.login_session.state, "done", session)

    def test_login_code_from_a_phone_through_the_code_page(self):
        import urllib.parse
        import urllib.request
        studio, _project = self.make("ask")
        studio.ai.login_start()
        deadline = time.time() + 5
        while studio.ai.code_drop is None and time.time() < deadline:
            time.sleep(0.02)                            # opened by the "waiting" change event
        drop = studio.ai.code_drop
        self.assertIsNotNone(drop)
        code_url = studio.ai.status()["login"]["code_url"]
        if code_url:                                   # empty when this machine has no network
            self.assertTrue(code_url.endswith(f":{drop.port}/c/{drop.token}"), code_url)
        url = f"http://127.0.0.1:{drop.port}/c/{drop.token}"
        data = urllib.parse.urlencode({"code": "GOOD"}).encode()
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=5) as resp:
            self.assertEqual(resp.status, 200)
        deadline = time.time() + 5
        while studio.ai.login_session.state not in ("done", "failed") and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(studio.ai.login_session.state, "done")
        self.assertIsNone(studio.ai.code_drop)          # closed once the sign-in stopped waiting
        self.assertEqual(studio.ai.status()["login"]["code_url"], "")

    def test_code_page_closes_on_cancel_and_can_be_switched_off(self):
        studio, _project = self.make("ask")
        studio.ai.login_start()
        deadline = time.time() + 5
        while studio.ai.code_drop is None and time.time() < deadline:
            time.sleep(0.02)
        drop = studio.ai.code_drop
        self.assertTrue(drop is not None and drop.running)
        studio.ai.login_cancel()
        self.assertIsNone(studio.ai.code_drop)
        self.assertFalse(drop.running)
        studio.settings.set("ai.login_code_page", False, save=False)
        studio.ai._open_code_drop()                     # what the "waiting" event calls
        self.assertIsNone(studio.ai.code_drop)

    def test_login_wrong_code_fails_without_leaking_the_link_query(self):
        studio, _project = self.make("ask")
        studio.ai.login_start()
        deadline = time.time() + 5
        while studio.ai.login_session.state != "waiting" and time.time() < deadline:
            time.sleep(0.02)
        studio.ai.login_code("BAD")
        deadline = time.time() + 5
        while studio.ai.login_session.state not in ("done", "failed") and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(studio.ai.login_session.state, "failed")
        self.assertNotIn("fake123", studio.ai.login_session.error)


class McpTests(AITestCase):
    def test_mcp_server_exposes_mode_tools_and_builds_through_mcode(self):
        cli = write_script(os.path.join(self.tmp, "tools", "arduino-cli"), FAKE_ARDUINO_CLI)
        core = os.path.join(self.home, ".arduino15", "packages", "esp32", "hardware", "esp32", "3.3.12")
        os.makedirs(core)
        open(os.path.join(core, "boards.txt"), "w").close()
        studio, project = self.make("assisted", toolchain="arduino-cli", board="esp32-s3")
        studio.settings.set("dev.arduino_cli", cli, save=False)
        studio.tools.refresh(wait=True, only=["arduino-cli"])
        self.scenario("mcp")
        turn = self.run_turn(studio, project)
        self.assertEqual(turn.state, "done", turn.error)
        data = json.loads(turn.result["text"].splitlines()[0])
        self.assertEqual(data["server"], "mcode")
        self.assertIn("build", data["tools"])
        self.assertNotIn("upload", " ".join(data["tools"]))
        self.assertFalse(data["build_error"])
        self.assertIn("build: succeeded", data["build"])
        self.assertEqual(studio.builds.last(project.id)["source"], "ai")
        self.assertIn("Building the firmware", [a["label"] for a in turn.activities])

    def test_claude_cannot_build_its_own_unapproved_partition_change(self):
        """A-05 end to end: the held change used to reach a build inside the same turn."""
        cli = write_script(os.path.join(self.tmp, "tools", "arduino-cli"), FAKE_ARDUINO_CLI)
        core = os.path.join(self.home, ".arduino15", "packages", "esp32", "hardware", "esp32", "3.3.12")
        os.makedirs(core)
        open(os.path.join(core, "boards.txt"), "w").close()
        studio, project = self.make("assisted", toolchain="arduino-cli", board="esp32-s3")
        studio.settings.set("dev.arduino_cli", cli, save=False)
        studio.tools.refresh(wait=True, only=["arduino-cli"])
        self.scenario("mcp-risky")
        turn = self.run_turn(studio, project)
        data = json.loads(turn.result["text"].splitlines()[0])
        self.assertIn("did not run build", data["build"])
        self.assertEqual(studio.jobs.recent(kind="build"), [])
        self.assertEqual(turn.state, "review")
        self.assertFalse(os.path.exists(os.path.join(project.path, "partitions.csv")))

    def test_bridge_refuses_closed_turns_and_other_modes_tools(self):
        studio, project = self.make("ask")
        bridge = studio.ai.bridge
        token = bridge.open_turn(project.id, "ask")
        self.assertFalse(bridge.call(token, "build", {})["ok"])
        self.assertTrue(bridge.call(token, "project_info", {})["ok"])
        bridge.close_turn(token)
        self.assertFalse(bridge.call(token, "project_info", {})["ok"])


class BridgeSafetyTests(AITestCase):
    """A-05/A-06: risky edits reached builds during the turn; a stopped turn kept building."""

    def arduino(self):
        cli = write_script(os.path.join(self.tmp, "tools", "arduino-cli"), FAKE_ARDUINO_CLI)
        core = os.path.join(self.home, ".arduino15", "packages", "esp32", "hardware", "esp32",
                            "3.3.12")
        os.makedirs(core)
        open(os.path.join(core, "boards.txt"), "w").close()
        studio, project = self.make("assisted", toolchain="arduino-cli", board="esp32-s3")
        studio.settings.set("dev.arduino_cli", cli, save=False)
        studio.tools.refresh(wait=True, only=["arduino-cli"])
        return studio, project

    def turn_token(self, studio, project):
        workspace = studio.ai._workspace(project)
        workspace.snapshot()
        token = studio.ai.bridge.open_turn(project.id, "assisted",
                                           guard=workspace.pending_reason)
        self.addCleanup(studio.ai.bridge.close_turn, token)
        return token

    def test_build_refused_while_a_risky_change_waits(self):
        studio, project = self.arduino()
        token = self.turn_token(studio, project)
        with open(os.path.join(project.path, "partitions.csv"), "w") as handle:
            handle.write("nvs, data, nvs, 0x9000, 0x5000\n")
        reply = studio.ai.bridge.call(token, "build", {})
        self.assertIn("did not run build", reply["text"])
        self.assertIn("partitions.csv", reply["text"])
        self.assertEqual(studio.jobs.recent(kind="build"), [])

    def test_build_script_change_blocks_builds_unless_switched_off(self):
        studio, project = self.arduino()
        token = self.turn_token(studio, project)
        with open(os.path.join(project.path, "extra_script.py"), "w") as handle:
            handle.write("import os\n")
        self.assertIn("extra_script.py", studio.ai.bridge.call(token, "run_tests", {})["text"])
        studio.settings.set("ai.hold_build_scripts", False, save=False)
        token = self.turn_token(studio, project)
        reply = studio.ai.bridge.call(token, "build", {})
        self.assertIn("build: succeeded", reply["text"])

    def test_closing_a_turn_cancels_its_build(self):
        studio, project = self.arduino()
        os.environ["FAKE_COMPILE_SLOW"] = "20"
        self.addCleanup(os.environ.pop, "FAKE_COMPILE_SLOW", None)
        token = self.turn_token(studio, project)
        replies = []
        caller = threading.Thread(target=lambda: replies.append(
            studio.ai.bridge.call(token, "build", {})))
        caller.start()
        deadline = time.time() + 10
        while not studio.jobs.recent(kind="build") and time.time() < deadline:
            time.sleep(0.02)
        job = studio.jobs.recent(kind="build")[0]
        studio.ai.bridge.close_turn(token)
        studio.jobs.wait(job.id, 10)
        self.assertEqual(job.state, "cancelled")
        caller.join(10)
        self.assertFalse(caller.is_alive())

    def test_git_tools_and_argument_checks(self):
        studio, project = self.make("advanced")
        from app.projects import gitutil
        self.assertTrue(gitutil.init(project.path))
        token = studio.ai.bridge.open_turn(project.id, "advanced")
        self.addCleanup(studio.ai.bridge.close_turn, token)
        bridge = studio.ai.bridge
        self.assertIn('"repo": true', bridge.call(token, "git_status", {})["text"])
        self.assertIn("created by mCode", bridge.call(token, "git_log", {})["text"])
        with open(os.path.join(project.path, "notes.txt"), "w") as handle:
            handle.write("x\n")
        self.assertIn("Committed", bridge.call(token, "git_commit", {"message": "notes"})["text"])
        self.assertIn("failed", bridge.call(token, "git_show", {"rev": "--output=/tmp/x"})["text"])
        self.assertFalse(bridge.call(token, "git_commit", {"message": " "})["ok"])
        self.assertFalse(bridge.call(token, "serial_log", {"lines": "many"})["ok"])
        self.assertFalse(bridge.call(token, "project_info", ["not", "a", "dict"])["ok"])


class ContextTests(TempCase):
    def test_prompt_contains_context_and_caps_attachments(self):
        studio = self.studio(hardware=False, ai=False)
        project = studio.projects.create("Ctx", "esp32-s3", "platformio", init_git=False)
        last = {"action": "build", "state": "failed", "headline": "Missing library: X",
                "diagnostics": [{"severity": "error", "file": "src/main.cpp", "line": 3,
                                 "message": "X.h: No such file"}],
                "missing_headers": ["X.h"], "output_tail": ["line"] * 10}
        prompt = context.build_prompt(project, "fix it", mode="assisted", kind="fix-build",
                                      device={"description": "ESP32-S3 (by esptool)",
                                              "port": "/dev/ttyACM0", "usb_id": "303a:1001"},
                                      last_build=last, serial_text="x" * 50000,
                                      attachments=["platformio.ini", "../etc/passwd"])
        self.assertIn("MCU ESP32-S3", prompt)
        self.assertIn("src/main.cpp:3", prompt)
        self.assertIn("Missing headers: X.h", prompt)
        self.assertIn("/dev/ttyACM0", prompt)
        self.assertIn("Attached file platformio.ini", prompt)
        self.assertIn("not readable", prompt)                  # outside the project
        self.assertLess(len(prompt), 60000)
        self.assertIn("Find the root cause", prompt)

    def test_summary_line(self):
        self.assertEqual(context.summary_line("Long answer.\n\n**SUMMARY:** Fixed it."), "Fixed it.")
        self.assertEqual(context.strip_summary("A\nSUMMARY: B"), "A")


class StreamTranslationTests(unittest.TestCase):
    def test_translate_events(self):
        out = translate({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "1", "name": "Edit", "input": {"file_path": "/p/src/main.cpp"}},
            {"type": "text", "text": "Done"}]}}, "/p")
        self.assertEqual(out[0]["label"], "Editing src/main.cpp")
        self.assertEqual(out[1]["text"], "Done")
        self.assertEqual(describe_tool("mcp__mcode__build", {}), "Building the firmware")
        result = translate({"type": "result", "subtype": "success", "is_error": False,
                            "result": "ok", "permission_denials": [{"tool_name": "Bash",
                                                                    "tool_input": {"command": "rm"}}]})
        self.assertTrue(result[0]["ok"])
        self.assertEqual(result[0]["denials"], ["Running rm"])

    def test_auth_summary_api_key_note(self):
        summary = auth_summary({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max",
                                "email": "a@b"}, {"ANTHROPIC_API_KEY": "x"})
        self.assertEqual(summary["label"], "Claude Max")
        self.assertIn("API key", summary["note"])
        self.assertNotIn("email", summary)

    def test_risky_classification(self):
        self.assertTrue(classify("partitions.csv", None, "x"))
        self.assertTrue(classify("sdkconfig", "a", "b"))
        old = "[env:x]\nboard = y\n"
        self.assertEqual(classify("platformio.ini", old, old + "monitor_speed = 9600\n"), [])
        self.assertTrue(classify("platformio.ini", old, old + "board_build.partitions = huge.csv\n"))
        self.assertTrue(classify("platformio.ini", old + "lib_deps =\n", old + "lib_deps =\n    a/b\n"))
        self.assertEqual(classify("src/main.cpp", "a", "b"), [])
        for name in ("CMakeLists.txt", "main/CMakeLists.txt", "Makefile", "rules.mk",
                     "extra_script.py", "tools.cmake", ".gitattributes"):
            self.assertTrue(classify(name, "a", "b"), name)
        self.assertEqual(classify("Makefile", "a", "b", build_scripts=False), [])
        self.assertTrue(classify(".gitattributes", "a", "b", build_scripts=False))


if __name__ == "__main__":
    unittest.main()
