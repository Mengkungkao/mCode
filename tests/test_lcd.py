"""The LCD interface driven through the real SDK InputController.

Gestures go in exactly as the Whisplay button produces them (a fake clock
drives the SDK's gesture timing); keys go in as KeyEvents. Every screen's
footer comes from the same table as its handlers.
"""

from __future__ import annotations

import os
import unittest

from mfruit_sdk.keys import DOWN, UP, KeyEvent
from mfruit_sdk.ui.theme import SCREEN_H, SCREEN_W

from app.lcd import screens as s
from app.lcd.ui import DeviceUI
from app.preview import FakeBoard, FakeStatusMonitor, FakeVoice, build_demo, scenes

from .helpers import FakeClock, TempCase


class LcdCase(TempCase):
    def setUp(self):
        super().setUp()
        self.studio = build_demo(os.path.join(self.tmp, "demo"))
        self.addCleanup(self.studio.stop, 0.5)
        self.clock = FakeClock()
        self.board = FakeBoard()
        self.ui = DeviceUI(self.studio, board=self.board, keyboard=False, threaded=False,
                           clock=self.clock, status=FakeStatusMonitor())
        self.ui.attach_for_tests()
        self.addCleanup(self.ui.studio.events.unsubscribe, self.ui._token)

    # -- gestures exactly as the button makes them
    def step(self, seconds):
        self.clock.advance(seconds)
        self.ui.input.gestures.poll()

    def tap(self, times=1):
        for _ in range(times):
            self.ui.input.press()
            self.step(0.06)
            self.ui.input.release()
            self.step(0.1)
        self.step(0.6)

    def next(self, count=1):
        """Separate single taps (not a multi-click gesture)."""
        for _ in range(count):
            self.tap(1)

    def hold(self, seconds=0.8):
        self.ui.input.press()
        self.step(seconds)
        self.ui.input.release()
        self.step(0.6)

    def key(self, name, kind="key"):
        code = hash(name) % 1000 + 1
        self.ui.input.key_event(KeyEvent(kind, name, DOWN, code))
        self.ui.input.key_event(KeyEvent(kind, name, UP, code))

    def top(self):
        return type(self.ui.top()).__name__


class NavigationTests(LcdCase):
    def test_home_controls_open_each_tile_and_exit(self):
        self.assertEqual(self.top(), "HomeScreen")
        self.hold()
        self.assertEqual(self.top(), "AIScreen")
        self.tap(4)                                       # 4x back
        self.assertEqual(self.top(), "HomeScreen")
        self.tap()                                        # next tile
        self.hold()
        self.assertEqual(self.top(), "ProjectsScreen")
        self.key("escape")
        self.tap(2)                                       # 2x: previous tile
        self.tap(3)                                       # 3x does nothing on Home
        self.assertEqual(self.top(), "HomeScreen")
        self.key("down")
        self.key("down")
        self.key("enter")
        self.assertEqual(self.top(), "BoardsScreen")
        self.key("escape")
        self.key("escape")                                # Back from Home leaves the app
        self.assertTrue(self.ui.exit_requested.is_set())

    def test_hold_only_arms_then_acts_on_release(self):
        self.ui.input.press()
        self.step(0.9)
        self.assertEqual(self.top(), "HomeScreen")       # nothing opened while held
        self.assertTrue(self.ui.armed)
        self.assertEqual(self.ui.top().hints(True), [("release", "to open")])
        self.ui.input.release()
        self.step(0.1)
        self.assertEqual(self.top(), "AIScreen")

    def test_nothing_happens_while_another_app_has_the_screen(self):
        self.board.has_focus = False
        self.key("enter")
        self.hold()
        self.assertEqual(self.top(), "HomeScreen")

    def test_new_project_wizard_by_button(self):
        self.ui.push(s.ProjectsScreen(self.ui))
        projects = len(self.studio.projects.list())
        self.next(projects)                               # to "New project"
        self.hold()
        self.assertEqual(self.top(), "NewPlatformScreen")
        self.hold()                                       # ESP32
        self.assertEqual(self.top(), "NewBoardScreen")
        self.next(2)
        self.hold()                                       # ESP32-S3
        self.assertEqual(self.top(), "NewFrameworkScreen")
        self.hold()                                       # first option
        self.assertEqual(self.top(), "NewNameScreen")
        self.ui.top().create()
        self.assertEqual(self.top(), "CreatingScreen")


class FeedbackTests(LcdCase):
    def test_results_are_announced_only_while_in_front(self):
        voice = self.studio.voice
        self.studio.events.publish("build", {"action": "build", "state": "succeeded", "source": "lcd"})
        self.studio.events.publish("upload", {"action": "upload", "state": "failed"})
        self.studio.events.publish("build", {"action": "build", "state": "failed", "source": "ai"})
        self.studio.events.publish("ai", {"what": "turn", "turn": {"state": "review"}, "status": {}})
        self.assertEqual(voice.said, ["build_ok", "error", "review"])
        self.board.has_focus = False
        self.studio.events.publish("build", {"action": "build", "state": "succeeded", "source": "lcd"})
        self.assertEqual(voice.said, ["build_ok", "error", "review"])


class AIScreenTests(LcdCase):
    def setUp(self):
        super().setUp()
        self.ui.push(s.AIScreen(self.ui))
        self.voice = self.studio.voice

    def test_hold_talks_and_release_sends_for_transcription(self):
        self.assertTrue(self.voice.armed, "the microphone should open with the screen")
        self.ui.input.press()
        self.step(0.4)                                    # talk starts after 350 ms
        self.assertEqual(self.voice.state, "listening")
        self.ui.input.release()
        self.step(0.1)
        self.assertEqual(self.voice.state, "processing")

    def test_heard_text_is_shown_with_a_send_choice(self):
        self.voice.state = "heard"
        self.voice.text = "Blink the LED faster"
        screen = self.ui.top()
        self.assertFalse(screen.talk())
        labels = [i.label for i in screen.items()]
        self.assertEqual(labels, ["Send to Claude", "Talk again", "Cancel"])
        sent = []
        self.studio.ai.ask = lambda project, text, **kw: sent.append(text)
        self.hold()
        self.assertEqual(sent, ["Blink the LED faster"])

    def test_typing_on_a_keyboard_sends_a_request(self):
        sent = []
        self.studio.ai.ask = lambda project, text, **kw: sent.append((text, kw.get("source")))
        for ch in "hi":                                   # the key hub sends characters on key-down
            self.ui.input.key_event(KeyEvent("char", ch, DOWN, ord(ch)))
        self.assertTrue(self.ui.top().typing())
        self.key("enter")
        self.assertEqual(sent, [("hi", "keyboard")])

    def test_menu_on_triple_and_leaving_closes_the_microphone(self):
        self.tap(3)
        self.assertEqual(self.top(), "AIMenuScreen")
        self.key("escape")
        self.key("escape")
        self.assertEqual(self.top(), "HomeScreen")
        self.assertFalse(self.voice.armed)


class FooterTableTests(LcdCase):
    def test_every_screen_footer_matches_its_handlers(self):
        for name, setup in scenes(self.studio, self.ui):
            setup()
            screen = self.ui.top()
            controls = screen.controls()
            handled = {c.action for c in controls}
            for control in controls:
                self.assertTrue(callable(control.run), name)
            shown = [label for _gesture, label in screen.hints(False)]
            for control in controls:
                if control.label:
                    self.assertIn(control.label, shown, f"{name}: {control.label} not in footer")
            if isinstance(screen, s.ListScreen) and screen.items():
                self.assertIn("select", handled, name)
            image = self.ui.render()
            self.assertEqual(image.size, (SCREEN_W, SCREEN_H), name)

    def test_flash_needs_a_deliberate_hold_on_confirm(self):
        from app.hardware.upload import Check, UploadPlan
        project = self.studio.projects.current()
        screen = s.FlashConfirmScreen(self.ui, project.id)
        screen.enter = lambda: None
        screen.plan = UploadPlan("upload", project.id, project.name, project.board_name,
                                 "/dev/ttyACM0", "", "firmware.bin",
                                 [Check("board", True, "ok", "info")], token="t")
        started = []
        self.studio.uploads.start = lambda token, confirmed: started.append((token, confirmed))
        self.ui.push(screen)
        self.tap()                                        # moves to Cancel: nothing flashed
        self.assertEqual(started, [])
        self.tap(2)                                       # back to Confirm
        self.hold()
        self.assertEqual(started, [("t", True)])


if __name__ == "__main__":
    unittest.main()
