"""The AI Engineer with a fake Claude Code CLI (no network, no account)."""

from __future__ import annotations

import json
import os
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

    def test_advanced_allows_git_only(self):
        studio, project = self.make("advanced")
        self.run_turn(studio, project)
        args = [c for c in self.calls() if "-p" in c["args"]][-1]["args"]
        self.assertEqual(args[args.index("--tools") + 1], "Read,Edit,Write,Glob,Grep,Bash")
        allowed = args[args.index("--allowedTools") + 1].split(",")
        bash = [a for a in allowed if a.startswith("Bash(")]
        self.assertTrue(bash and all(a.startswith("Bash(git ") for a in bash), bash)


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

    def test_bridge_refuses_closed_turns_and_other_modes_tools(self):
        studio, project = self.make("ask")
        bridge = studio.ai.bridge
        token = bridge.open_turn(project.id, "ask")
        self.assertFalse(bridge.call(token, "build", {})["ok"])
        self.assertTrue(bridge.call(token, "project_info", {})["ok"])
        bridge.close_turn(token)
        self.assertFalse(bridge.call(token, "project_info", {})["ok"])


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


if __name__ == "__main__":
    unittest.main()
