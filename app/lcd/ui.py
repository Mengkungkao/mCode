"""The Whisplay LCD interface: screens, navigation, input and drawing.

mFruit OS launches mCode (ApplicationManager -> whisplay-daemon ->
mfruit-run -> run.sh). This module follows the app contract: it claims Esc,
takes focus and the framebuffer, draws its first frame at once, reads the
button and keyboards only through the SDK's InputController, stops drawing
when focus is revoked and exits on ``app_exit_requested`` or Back from Home.

Controls are the same as everywhere on the device: tap = next, 2x =
previous, hold then release = select, 4x = back, 3x = the screen's extra;
on the AI Engineer screen a hold talks while held. Each screen declares its
controls in ONE table (:class:`Control`): the footer is drawn from it and
actions are dispatched through it, so a screen cannot advertise a gesture it
does not handle.

Threads: the button, keyboard and daemon call in on their own threads and
only change state under ``self.lock``; one render thread draws when
something changed (and at a few frames per second while a screen animates).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, List, NamedTuple, Optional

from mfruit_sdk.daemon import own_escape_key
from mfruit_sdk.input import (BACK, CHAR, ERASE, EXTRA, KEY, NEXT, PREVIOUS, SELECT, TALK_END,
                              TALK_START, Action, InputController)
from mfruit_sdk.status import StatusMonitor
from mfruit_sdk.ui import Canvas, Row, draw_list, footer, status_bar, toast
from mfruit_sdk.ui.theme import CONTENT_BOTTOM, CONTENT_TOP

from .. import APP_ID
from . import whisplay

log = logging.getLogger("mcode.lcd")

GESTURE = {NEXT: "tap", PREVIOUS: "2×", SELECT: "hold", BACK: "4×", EXTRA: "3×",
           TALK_START: "hold"}
TOAST_SECONDS = 2.2


class Control(NamedTuple):
    action: str                 # an InputController action name
    label: str                  # footer text ("" = not shown, still handled)
    run: Callable[[Action], None]
    gesture: str = ""           # footer gesture label; default from GESTURE


class Item(NamedTuple):
    label: str
    run: Optional[Callable[[], None]] = None
    subtitle: Optional[str] = None
    value: object = None
    kind: str = "action"        # action | nav | toggle | info | back | danger
    tone: str = ""
    enabled: bool = True
    select: str = "open"        # what hold does, for the footer ("open", "flash", ...)
    mark: object = None

    def row(self) -> Row:
        return Row(self.label, self.subtitle, self.value, self.kind, self.tone, self.enabled,
                   self.mark)


class Screen:
    title = "mCode"
    animated = False

    def __init__(self, ui: "DeviceUI"):
        self.ui = ui
        self.studio = ui.studio

    # what the controller asks
    def talk(self) -> bool:
        return False

    def typing(self) -> bool:
        return False

    def controls(self) -> List[Control]:
        return [Control(BACK, "back", lambda a: self.ui.pop())]

    def select_label(self) -> str:
        for control in self.controls():
            if control.action == SELECT:
                return control.label or "open"
        return "open"

    def handle(self, action: Action) -> bool:
        for control in self.controls():
            if control.action == action.name:
                control.run(action)
                return True
        return False

    def hints(self, armed: bool) -> list:
        if armed:
            return [("release", f"to {self.select_label()}")]
        out = []
        for control in self.controls():
            if control.label:
                out.append((control.gesture or GESTURE.get(control.action, control.action),
                            control.label))
        return out

    def get_title(self) -> str:
        return self.title

    def dot(self):
        return None

    def render(self, c: Canvas) -> None:
        pass

    def enter(self) -> None:
        pass

    def leave(self) -> None:
        pass

    def on_event(self, topic: str, data) -> bool:
        return False

    def animating(self) -> bool:
        return self.animated


class ListScreen(Screen):
    """A list with the standard controls: tap next, 2x previous, hold open, 4x back."""

    back_label = "back"
    header_height = 0
    empty = "Nothing here"

    def __init__(self, ui: "DeviceUI"):
        super().__init__(ui)
        self.index = 0

    def items(self) -> List[Item]:
        return []

    def current(self) -> Optional[Item]:
        items = self.items()
        if not items:
            return None
        self.index = max(0, min(self.index, len(items) - 1))
        return items[self.index]

    def controls(self) -> List[Control]:
        item = self.current()
        select = item.select if item is not None else "open"
        return [Control(NEXT, "next", lambda a: self.move(1)),
                Control(SELECT, select, lambda a: self.activate()),
                Control(BACK, self.back_label, lambda a: self.back()),
                Control(PREVIOUS, "previous", lambda a: self.move(-1))]

    def move(self, step: int) -> None:
        items = self.items()
        if items:
            self.index = (self.index + step) % len(items)

    def activate(self) -> None:
        item = self.current()
        if item is not None and item.enabled and item.run is not None:
            item.run()

    def back(self) -> None:
        self.ui.pop()

    def render_header(self, c: Canvas, top: int) -> None:
        pass

    def render(self, c: Canvas) -> None:
        top = CONTENT_TOP
        if self.header_height:
            self.render_header(c, top)
            top += self.header_height
        items = self.items()
        self.index = max(0, min(self.index, len(items) - 1)) if items else 0
        draw_list(c, [i.row() for i in items], self.index, top=top, bottom=CONTENT_BOTTOM,
                  empty=self.empty)


class DeviceUI:
    def __init__(self, studio, board: Optional[whisplay.WhisplayApp] = None,
                 keyboard: bool = True, threaded: bool = True, clock=time.monotonic,
                 status: Optional[StatusMonitor] = None):
        self.studio = studio
        self.board = board or whisplay.WhisplayApp(APP_ID)
        self.clock = clock
        self.threaded = threaded
        self.lock = threading.RLock()
        self.stack: List[Screen] = []
        self.armed = False
        self.note = ""
        self.note_tone = ""
        self.note_until = 0.0
        self.phase = 0
        self.frames = 0
        self.last_frame = None
        self._last_bytes = b""
        self.running = False
        self.exit_requested = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._token = None
        self.status = status or StatusMonitor(on_change=lambda _s: self.wake())
        self.input = InputController(self._on_action, talk=self._talk, typing=self._typing,
                                     active=lambda: self.board.has_focus,
                                     on_armed=self._on_armed, app_id=self.board.app_id,
                                     keyboard=keyboard, threaded=threaded, clock=clock,
                                     talk_press_ms=350)
        from .screens import HomeScreen
        self.stack.append(HomeScreen(self))

    # ============================================================ lifecycle
    def start(self) -> bool:
        board = self.board
        board.on_press = self.input.press
        board.on_release = self.input.release
        board.on_exit_request = self.request_exit
        board.on_focus_changed = self._focus_changed
        try:
            # Before taking the screen: a registration makes the daemon redraw its desktop.
            own_escape_key(board.app_id, whisplay.SOCKET_PATH)
        except Exception as exc:
            log.info("could not claim Esc: %s", exc)
        try:
            board.start()
        except (OSError, RuntimeError) as exc:
            log.warning("whisplay-daemon unavailable: %s", exc)
            return False
        self.running = True
        self.draw()                         # first frame at once
        self._token = self.studio.events.subscribe(self._event)
        self.status.start()
        self.input.start()
        self.top().enter()
        if self.threaded:
            self._thread = threading.Thread(target=self._render_loop, name="lcd-render",
                                            daemon=True)
            self._thread.start()
        log.info("on the Whisplay LCD")
        return True

    def attach_for_tests(self) -> None:
        """Start without a daemon (tests and previews): events and the first screen."""
        self.running = True
        self._token = self.studio.events.subscribe(self._event)
        self.top().enter()

    def stop(self) -> None:
        self.running = False
        self._wake.set()
        self.studio.events.unsubscribe(self._token)
        for screen in reversed(self.stack):
            try:
                screen.leave()
            except Exception:       # isolation boundary
                log.exception("leaving a screen failed")
        self.input.stop()
        self.status.stop()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self.board.stop()

    def request_exit(self) -> None:
        self.exit_requested.set()

    def wake(self) -> None:
        self._wake.set()

    def _focus_changed(self, has_focus: bool) -> None:
        self.input.reset()                  # keys pressed elsewhere are not ours
        with self.lock:
            self.armed = False
        if self.studio.voice is not None:
            self.studio.voice.enabled_output = has_focus
            if not has_focus:
                self.studio.voice.disarm()
        if has_focus:
            self.top().enter()
        self.wake()

    def _render_loop(self) -> None:
        while self.running:
            interval = 0.25 if self._animating() else None
            self._wake.wait(timeout=interval)
            self._wake.clear()
            if not self.running:
                return
            try:
                self.draw()
            except Exception:           # isolation boundary: drawing must not kill the app
                log.exception("drawing failed")

    def _animating(self) -> bool:
        with self.lock:
            screen = self.top()
            return screen.animating() or (self.note and self.clock() < self.note_until)

    # =========================================================== navigation
    def top(self) -> Screen:
        return self.stack[-1]

    def push(self, screen: Screen) -> None:
        with self.lock:
            self.armed = False
            self.top().leave()
            self.stack.append(screen)
            screen.enter()
        self.wake()

    def pop(self) -> None:
        with self.lock:
            self.armed = False
            if len(self.stack) <= 1:
                self.toast("Exiting")
                self.draw()
                self.request_exit()
                return
            screen = self.stack.pop()
            screen.leave()
            self.top().enter()
        self.wake()

    def replace(self, screen: Screen) -> None:
        with self.lock:
            self.armed = False
            old = self.stack.pop()
            old.leave()
            self.stack.append(screen)
            screen.enter()
        self.wake()

    def home(self) -> None:
        with self.lock:
            while len(self.stack) > 1:
                self.stack.pop().leave()
            self.top().enter()
        self.wake()

    def toast(self, text: str, tone: str = "", seconds: float = TOAST_SECONDS) -> None:
        with self.lock:
            self.note = text
            self.note_tone = tone
            self.note_until = self.clock() + seconds
        self.wake()

    # ================================================================ input
    def _talk(self) -> bool:
        return self.top().talk()

    def _typing(self) -> bool:
        return self.top().typing()

    def _on_armed(self, armed: bool) -> None:
        with self.lock:
            self.armed = armed
        self.wake()

    def _on_action(self, action: Action) -> None:
        with self.lock:
            self.armed = False
            screen = self.top()
            try:
                screen.handle(action)
            except Exception as exc:    # isolation boundary: a screen bug must not stop input
                log.exception("screen %s failed on %s", type(screen).__name__, action.name)
                self.toast(f"Error: {exc}", "error")
        self.wake()

    def say(self, phrase: str) -> None:
        voice = self.studio.voice
        if voice is not None and self.board.has_focus:
            voice.say(phrase)

    # =============================================================== events
    def _event(self, topic: str, data) -> None:
        if topic in ("job.line", "voice.level") and not self._interested(topic):
            return
        with self.lock:
            if topic == "toast" and isinstance(data, dict):
                self.toast(data.get("text", ""), data.get("tone", ""))
            changed = False
            for screen in list(self.stack):
                try:
                    changed = screen.on_event(topic, data) or changed
                except Exception:       # isolation boundary
                    log.exception("screen event handler failed")
        self._feedback(topic, data)
        if changed or topic in ("devices", "projects", "ai", "build", "upload", "settings",
                                "tools", "monitor.state", "voice", "job"):
            self.wake()

    def _feedback(self, topic: str, data) -> None:
        """Short audio feedback for results (only while mCode has the screen)."""
        if not isinstance(data, dict):
            return
        if topic == "build" and data.get("source") not in ("ai",) and \
                data.get("action") in ("build", "rebuild", "test"):
            self.say("build_ok" if data.get("state") == "succeeded" else "build_failed")
        elif topic == "upload" and data.get("action") == "upload":
            self.say("flash_ok" if data.get("state") == "succeeded" else "error")
        elif topic == "ai" and data.get("what") == "turn":
            state = (data.get("turn") or {}).get("state")
            phrase = {"done": "done", "review": "review", "error": "error"}.get(state)
            if phrase:
                self.say(phrase)

    def _interested(self, topic: str) -> bool:
        screen = self.top()
        return topic == "voice.level" and screen.talk() or getattr(screen, "wants_lines", False)

    # ============================================================== drawing
    def render(self):
        with self.lock:
            self.phase += 1
            screen = self.top()
            c = Canvas()
            try:
                screen.render(c)
            except Exception as exc:    # isolation boundary: show the problem, keep running
                log.exception("rendering %s failed", type(screen).__name__)
                from mfruit_sdk.ui import message
                c = Canvas()
                message(c, "Display error", str(exc)[:120], tone="error")
            status_bar(c, screen.get_title(), self.status.sample(), dot=screen.dot())
            footer(c, screen.hints(self.armed))
            if self.note and self.clock() < self.note_until:
                toast(c, self.note, self.note_tone)
            elif self.note:
                self.note = ""
            return c.image

    def draw(self) -> None:
        image = self.render()
        self.last_frame = image
        if not self.board.has_focus:
            return
        data = image.tobytes()
        if data == self._last_bytes:
            return
        self._last_bytes = data
        self.board.show(image)
        self.frames += 1
