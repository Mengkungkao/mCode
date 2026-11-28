"""Every LCD screen of mCode Studio.

Design rules for the 240x280 Whisplay screen: one idea per screen, large
type, short words, colours with meaning (green done, yellow working or
waiting, red failed, blue selected), a footer that always says what the
gestures do, and a way back from everywhere. Directory trees, compiler
flags and long logs stay on the desktop IDE; the LCD shows the headline and
offers "Ask AI to fix" or "Details".
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, List, Optional

from mfruit_sdk.input import (BACK, CHAR, ERASE, EXTRA, NEXT, PREVIOUS, SELECT, TALK_END,
                              TALK_START, Action)
from mfruit_sdk.ui import Canvas, draw_list, message, text_field
from mfruit_sdk.ui.theme import CONTENT_BOTTOM, CONTENT_TOP, MARGIN, SCREEN_W

from ..core.config import AI_MODE_SHORT, AI_MODES, FIELD_BY_KEY
from ..projects import boards
from ..projects.model import ProjectError
from ..toolchains import detect
from . import draw
from .ui import Control, Item, ListScreen, Screen

log = logging.getLogger("mcode.lcd")


def _project(ui):
    try:
        return ui.studio.projects.current()
    except ProjectError:
        return None


def _elapsed(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds >= 60 else f"{seconds} s"


def _err(exc: Exception) -> str:
    return str(exc) or exc.__class__.__name__


# ===================================================================== generic

class ReaderScreen(Screen):
    """Scrollable text: tap down, 2x up, hold or 4x back."""

    def __init__(self, ui, title: str, text: str, mono: bool = False):
        super().__init__(ui)
        self.title = title
        self.text = text
        self.mono = mono
        self.first = 0
        self._lines: List[str] = []

    def controls(self):
        return [Control(NEXT, "down", lambda a: self.scroll(4)),
                Control(SELECT, "back", lambda a: self.ui.pop()),
                Control(BACK, "back", lambda a: self.ui.pop(), gesture="4×"),
                Control(PREVIOUS, "up", lambda a: self.scroll(-4))]

    def hints(self, armed):
        if armed:
            return [("release", "to go back")]
        return [("tap", "down"), ("2×", "up"), ("4×", "back")]

    def scroll(self, step: int) -> None:
        self.first = max(0, min(self.first + step, max(0, len(self._lines) - 8)))

    def render(self, c: Canvas) -> None:
        size = 12 if self.mono else 14
        width = SCREEN_W - 2 * MARGIN - 6
        lines = []
        for para in self.text.split("\n"):
            lines.extend(c.wrap(para, size, "regular", width) or [""])
        self._lines = lines
        y = CONTENT_TOP + 4
        step = size + 5
        visible = (CONTENT_BOTTOM - y) // step
        self.first = max(0, min(self.first, max(0, len(lines) - visible)))
        for line in lines[self.first:self.first + visible]:
            c.text(MARGIN, y, line, size, "regular", c.theme.text)
            y += step
        if len(lines) > visible:
            total = len(lines)
            bar_h = max(16, int((CONTENT_BOTTOM - CONTENT_TOP) * visible / total))
            span = CONTENT_BOTTOM - CONTENT_TOP - bar_h
            bar_y = CONTENT_TOP + int(span * self.first / max(1, total - visible))
            c.rounded((SCREEN_W - 5, bar_y, SCREEN_W - 3, bar_y + bar_h), 1, fill=c.theme.text_muted)


class MenuScreen(ListScreen):
    """A list built from a callable; for small menus."""

    def __init__(self, ui, title: str, build: Callable[[], List[Item]]):
        super().__init__(ui)
        self.title = title
        self._build = build

    def items(self):
        return self._build()


class ConfirmScreen(ListScreen):
    header_height = 120

    def __init__(self, ui, title: str, heading: str, body: str, confirm: str,
                 on_confirm: Callable[[], None], danger: bool = False, cancel: str = "Cancel",
                 icon: str = "warning"):
        super().__init__(ui)
        self.title = title
        self.heading = heading
        self.body = body
        self.confirm_label = confirm
        self.on_confirm = on_confirm
        self.danger = danger
        self.cancel_label = cancel
        self.icon = icon

    def items(self):
        return [Item(self.confirm_label, self._confirm, kind="danger" if self.danger else "action",
                     select="confirm"),
                Item(self.cancel_label, self.ui.pop, kind="back", select="cancel")]

    def _confirm(self):
        self.ui.pop()
        self.on_confirm()

    def render_header(self, c, top):
        t = c.theme
        draw.icon(c, self.icon, SCREEN_W // 2, top + 18, 30,
                  t.error if self.danger else t.warning)
        c.text(SCREEN_W // 2, top + 38, self.heading, 19, "bold", t.text, anchor="ma",
               max_width=SCREEN_W - 2 * MARGIN)
        draw.wrapped(c, self.body, MARGIN + 2, top + 64, SCREEN_W - 2 * MARGIN - 4, 13, "regular",
                     t.text_muted, max_lines=3, anchor_center=True)


class JobScreen(ListScreen):
    """Progress of one job, then its result with next steps."""

    animated = True
    header_height = 112
    success_title = "Done"
    failure_title = "Failed"
    running_title = "Working…"

    def __init__(self, ui, job_id: str, title: str = "mCode"):
        super().__init__(ui)
        self.job_id = job_id
        self.title = title
        self.announced = False

    @property
    def job(self):
        return self.studio.jobs.get(self.job_id)

    def animating(self):
        job = self.job
        return job is not None and not job.finished

    def controls(self):
        job = self.job
        if job is not None and not job.finished:
            return [Control(EXTRA, "stop", lambda a: self.ask_cancel()),
                    Control(BACK, "back", lambda a: self.ui.pop())]
        return super().controls()

    def ask_cancel(self):
        job = self.job
        if job is None or job.finished:
            return
        self.ui.push(ConfirmScreen(self.ui, self.title, "Stop it?",
                                   f"{job.title} will stop.", "Stop",
                                   lambda: self.studio.jobs.cancel(self.job_id), danger=True,
                                   cancel="Keep going"))

    def items(self):
        job = self.job
        if job is None or not job.finished:
            return []
        if job.ok:
            return self.success_items(job)
        return self.failure_items(job)

    def success_items(self, job):
        return [Item("Done", self.ui.pop, select="close")]

    def failure_items(self, job):
        return [Item("Details", lambda: self.details(job), kind="nav"),
                Item("Back", self.ui.pop, kind="back")]

    def details(self, job):
        self.ui.push(ReaderScreen(self.ui, "Details", "\n".join(job.tail(80)) or job.error,
                                  mono=True))

    def success_body(self, job) -> str:
        return ""

    def failure_body(self, job) -> str:
        return job.error or "It did not finish."

    def render_header(self, c, top):
        job = self.job
        t = c.theme
        if job is None:
            message(c, "Not found", "This task is no longer known.", top=top,
                    bottom=top + self.header_height)
            return
        if not job.finished:
            draw.spinner_title(c, self.running_title, job.phase, self.ui.phase, top=top + 4)
            y = top + 112
            if job.progress is not None:
                c.progress((MARGIN + 6, y, SCREEN_W - MARGIN - 6, y + 10), job.progress)
                c.text(SCREEN_W // 2, y + 16, f"{int(job.progress * 100)} %  ·  "
                       f"{_elapsed(job.elapsed)}", 13, "medium", t.text_muted, anchor="ma")
            else:
                c.text(SCREEN_W // 2, y + 4, _elapsed(job.elapsed), 13, "medium", t.text_muted,
                       anchor="ma")
            return
        if job.ok:
            draw.big_status(c, "check", self.success_title, self.success_body(job), top=top,
                            compact=True)
        elif job.state == "cancelled":
            draw.big_status(c, "warning", "Stopped", "Nothing more was done.", top=top,
                            compact=True)
        else:
            draw.big_status(c, "cross", self.failure_title, self.failure_body(job), top=top,
                            compact=True)

    def render(self, c):
        job = self.job
        if job is None or not job.finished:
            self.render_header(c, CONTENT_TOP)
            return
        self.header_height = 112
        super().render(c)


# ======================================================================== home

TILES = (("ai", "AI Engineer", "mic"), ("projects", "My Projects", "folder"),
         ("board", "Connect Board", "chip"), ("settings", "Settings", "gear"))


class HomeScreen(Screen):
    title = "mCode"

    def __init__(self, ui):
        super().__init__(ui)
        self.index = 0

    def controls(self):
        return [Control(NEXT, "next", lambda a: self._move(1)),
                Control(SELECT, "open", lambda a: self.open()),
                Control(BACK, "exit", lambda a: self.ui.pop()),
                Control(PREVIOUS, "previous", lambda a: self._move(-1))]

    def _move(self, step):
        self.index = (self.index + step) % len(TILES)

    def open(self):
        key = TILES[self.index][0]
        if key == "ai":
            open_ai(self.ui)
        elif key == "projects":
            self.ui.push(ProjectsScreen(self.ui))
        elif key == "board":
            self.ui.push(BoardsScreen(self.ui))
        else:
            self.ui.push(SettingsScreen(self.ui))

    def dot(self):
        ai = self.studio.ai
        if ai is None:
            return None
        state = ai.status()["state"]
        return {"ready": (52, 199, 110), "working": (255, 176, 32)}.get(state, (150, 158, 170))

    def render(self, c):
        t = c.theme
        studio = self.studio
        project = _project(self.ui)
        ai = studio.ai.status() if studio.ai else {"state": "unavailable"}
        ai_text, ai_tone = {
            "ready": ("Connected", t.success), "working": ("Working…", t.warning),
            "signed-out": ("Not signed in", t.warning), "not-installed": ("Not installed", t.warning),
            "checking": ("Checking…", t.text_muted)}.get(ai["state"], ("Unavailable", t.text_muted))
        device = studio.devices.selected(project) if studio.devices else None
        if device is not None:
            name = device.chip or (boards.get(device.board_id).name if device.board_id else "") \
                or (device.bridge or "USB serial")
            board_text, board_tone = f"{name} · {device.short_port}", t.success
        else:
            count = len(studio.devices.list()) if studio.devices else 0
            board_text = f"{count} found · choose one" if count else "Not connected"
            board_tone = t.text_muted
        y = CONTENT_TOP + 2
        c.rounded((MARGIN - 4, y, SCREEN_W - MARGIN + 4, y + 82), 12, fill=t.surface)
        rows = (("AI", ai_text, ai_tone), ("Project", project.name if project else "None yet",
                                           t.text if project else t.text_muted),
                ("Board", board_text, board_tone))
        for i, (label, value, tone) in enumerate(rows):
            ry = y + 8 + i * 25
            c.text(MARGIN + 6, ry + 2, label, 12, "medium", t.text_faint)
            c.text(MARGIN + 64, ry, value, 15, "semibold", tone,
                   max_width=SCREEN_W - 2 * MARGIN - 70)
        top = y + 90
        gap = 6
        width = (SCREEN_W - 2 * (MARGIN - 4) - gap) // 2
        height = (CONTENT_BOTTOM - top - gap) // 2
        for i, (_key, label, icon_name) in enumerate(TILES):
            col, row = i % 2, i // 2
            x0 = MARGIN - 4 + col * (width + gap)
            y0 = top + row * (height + gap)
            selected = i == self.index
            c.rounded((x0, y0, x0 + width, y0 + height), 14,
                      fill=t.accent_dim if selected else t.surface,
                      outline=t.accent if selected else None, width=2)
            draw.icon(c, icon_name, x0 + width // 2, y0 + 22, 26,
                      t.accent if selected else t.text_muted,
                      bg=t.accent_dim if selected else t.surface)
            c.text(x0 + width // 2, y0 + height - 21, label, 13, "semibold",
                   t.text if selected else t.text_muted, anchor="ma", max_width=width - 4)


# ====================================================================== AI

def open_ai(ui) -> None:
    studio = ui.studio
    if studio.ai is None:
        ui.toast("AI is not available", "error")
        return
    status = studio.ai.status()
    if status["state"] in ("not-installed", "signed-out"):
        ui.push(ClaudeSetupScreen(ui))
        return
    if _project(ui) is None:
        ui.push(MenuScreen(ui, "AI Engineer", lambda: [
            Item("Create a project", lambda: ui.replace(NewPlatformScreen(ui)), kind="nav",
                 subtitle="Claude works inside a project"),
            Item("My Projects", lambda: ui.replace(ProjectsScreen(ui)), kind="nav"),
            Item("Back", ui.pop, kind="back")]))
        return
    ui.push(AIScreen(ui))


class AIScreen(ListScreen):
    """Voice-first: hold to talk, release to send. Short status, big words."""

    title = "AI Engineer"

    def __init__(self, ui):
        super().__init__(ui)
        self.typed: Optional[str] = None
        self.dismissed = ""
        self.level = -90.0

    # ------------------------------------------------------------ state
    def project(self):
        return _project(self.ui)

    def view(self) -> str:
        studio = self.studio
        voice = studio.voice
        if self.typed is not None:
            return "typing"
        vstate = voice.state if voice is not None else "idle"
        if vstate in ("listening", "processing"):
            return vstate
        if vstate == "heard":
            return "heard"
        if vstate == "error":
            return "voice-error"
        project = self.project()
        ai = studio.ai
        turn = ai.turn if ai.turn is not None and project is not None and \
            ai.turn.project_id == project.id else None
        if turn is not None and turn.state == "working":
            return "working"
        if project is not None and ai.project_ai(project.id).review:
            return "review"
        if turn is not None and turn.id != self.dismissed and turn.state in ("done", "error",
                                                                            "cancelled", "review"):
            return "done" if turn.state in ("done", "review") else "failed"
        return "ready"

    def talk(self):
        return self.view() in ("ready", "listening", "voice-error")

    def typing(self):
        return self.typed is not None

    def animating(self):
        return self.view() in ("listening", "processing", "working")

    def enter(self):
        voice = self.studio.voice
        if voice is not None and voice.status()["stt_ready"]:
            voice.arm()

    def leave(self):
        voice = self.studio.voice
        if voice is not None:
            voice.disarm()
            if voice.state in ("heard", "error"):
                voice.clear()

    def on_event(self, topic, data):
        if topic == "voice.level":
            self.level = data.get("level", -90.0)
            return True
        if topic == "voice" and data.get("state") == "heard" and \
                self.studio.settings.get("ai.voice_autosend"):
            self.send(data.get("text", ""), "voice")
            return True
        return topic in ("ai", "voice")

    # --------------------------------------------------------- controls
    def controls(self):
        view = self.view()
        if view == "typing":
            return [Control(SELECT, "send", lambda a: self._send_typed(), gesture="Enter"),
                    Control(BACK, "cancel", lambda a: self._cancel_typing(), gesture="Esc"),
                    Control(CHAR, "", lambda a: self._type(a.char)),
                    Control(ERASE, "", lambda a: self._erase())]
        if view == "listening":
            return [Control(TALK_END, "send", lambda a: self.studio.voice.end_talk(),
                            gesture="release")]
        if view == "processing":
            return [Control(BACK, "cancel", lambda a: self.studio.voice.cancel())]
        if view == "working":
            return [Control(NEXT, "details", lambda a: self.ui.push(ActivityScreen(self.ui))),
                    Control(EXTRA, "stop", lambda a: self._ask_stop()),
                    Control(BACK, "back", lambda a: self.ui.pop())]
        if view in ("ready", "voice-error"):
            controls = [Control(TALK_START, "talk", lambda a: self._talk()),
                        Control(TALK_END, "", lambda a: self.studio.voice.end_talk()
                                if self.studio.voice else None),
                        Control(EXTRA, "menu", lambda a: self.ui.push(AIMenuScreen(self.ui))),
                        Control(BACK, "back", lambda a: self.ui.pop()),
                        Control(SELECT, "", lambda a: self._start_typing("")),
                        Control(CHAR, "", lambda a: self._start_typing(a.char))]
            if self._last_answer():
                controls.insert(3, Control(NEXT, "answer", lambda a: self._read_answer()))
            return controls
        return super().controls()

    def hints(self, armed):
        if armed:
            return super().hints(armed)
        view = self.view()
        if view == "typing":
            return [("Enter", "send"), ("Esc", "cancel")]
        return super().hints(armed)

    def items(self):
        view = self.view()
        ui = self.ui
        studio = self.studio
        project = self.project()
        if view == "heard":
            return [Item("Send to Claude", lambda: self.send(studio.voice.text, "voice"),
                         select="send"),
                    Item("Talk again", self._again, select="retry"),
                    Item("Cancel", self._again, kind="back", select="cancel")]
        if view == "review" and project is not None:
            review = studio.ai.project_ai(project.id).review or {}
            held_only = review.get("mode") != "ask"
            return [Item("Keep these changes" if held_only else "Apply changes",
                         lambda: self._approve(project), select="apply",
                         tone="warning" if review.get("held") else ""),
                    Item("View changes", lambda: ui.push(ChangesScreen(ui, project.id)),
                         kind="nav"),
                    Item("Put them back" if held_only else "Discard",
                         lambda: self._reject(project), kind="danger", select="discard")]
        turn = studio.ai.turn
        if view == "done" and turn is not None:
            items = [Item("Build now", lambda: start_build(ui, project, "build"), select="build"),
                     Item("Read the answer", lambda: ui.push(ReaderScreen(
                         ui, "Answer", turn.to_dict()["text"] or turn.summary)), kind="nav")]
            if turn.applied:
                items.append(Item("Undo changes", lambda: self._undo(project), kind="danger",
                                  select="undo"))
            items.append(Item("Ask something else", self._dismiss, kind="back", select="close"))
            return items
        if view == "failed" and turn is not None:
            return [Item("Try again", lambda: self._retry(turn), select="retry"),
                    Item("Details", lambda: ui.push(ReaderScreen(
                        ui, "Details", turn.error + "\n\n" + (turn.to_dict()["text"] or ""))),
                         kind="nav"),
                    Item("Back", self._dismiss, kind="back")]
        return []

    # ----------------------------------------------------------- actions
    def _talk(self):
        voice = self.studio.voice
        if voice is None:
            self.ui.toast("No microphone support", "error")
            return
        if not voice.status()["stt_ready"]:
            self.ui.push(ReaderScreen(self.ui, "Voice", VOICE_HELP))
            return
        if voice.start_talk():
            voice.cue("listen")

    def _again(self):
        if self.studio.voice:
            self.studio.voice.clear()

    def send(self, text: str, source: str):
        project = self.project()
        if self.studio.voice:
            self.studio.voice.clear()
        if not text or project is None:
            return
        try:
            self.studio.ai.ask(project, text, source=source)
        except Exception as exc:
            fix = getattr(exc, "fix", "")
            if fix == "login" or fix == "install:claude":
                self.ui.push(ClaudeSetupScreen(self.ui))
            self.ui.toast(_err(exc)[:60], "error")
            return
        self.ui.say("working")

    def _start_typing(self, first: str):
        self.typed = first or ""

    def _type(self, char: str):
        if self.typed is not None and len(self.typed) < 2000:
            self.typed += char

    def _erase(self):
        if self.typed:
            self.typed = self.typed[:-1]

    def _send_typed(self):
        text, self.typed = (self.typed or "").strip(), None
        if text:
            self.send(text, "keyboard")

    def _cancel_typing(self):
        self.typed = None

    def _ask_stop(self):
        self.ui.push(ConfirmScreen(self.ui, "AI Engineer", "Stop Claude?",
                                   "Changes made so far can be reviewed or undone.", "Stop",
                                   self.studio.ai.cancel, danger=True, cancel="Keep working"))

    def _approve(self, project):
        try:
            result = self.studio.ai.approve(project)
        except Exception as exc:
            self.ui.toast(_err(exc)[:60], "error")
            return
        count = len(result["applied"])
        self.ui.toast(f"Applied {count} file{'s' if count != 1 else ''}", "success")
        if result.get("build_job"):
            self.ui.push(BuildScreen(self.ui, result["build_job"], project.id, "build"))

    def _reject(self, project):
        try:
            self.studio.ai.reject(project)
        except Exception as exc:
            self.ui.toast(_err(exc)[:60], "error")
            return
        self.ui.toast("Changes discarded")

    def _undo(self, project):
        def undo():
            try:
                result = self.studio.ai.undo(project)
                self.ui.toast(f"Put back {len(result['reverted'])} file(s)", "success")
            except Exception as exc:
                self.ui.toast(_err(exc)[:60], "error")
        self.ui.push(ConfirmScreen(self.ui, "AI Engineer", "Undo Claude's changes?",
                                   "The files go back to how they were before.", "Undo", undo,
                                   danger=True))

    def _dismiss(self):
        turn = self.studio.ai.turn
        self.dismissed = turn.id if turn is not None else ""

    def _retry(self, turn):
        self._dismiss()
        project = self.project()
        if project is None:
            return
        try:
            self.studio.ai.ask(project, turn.request, kind=turn.kind, source=turn.source)
        except Exception as exc:
            self.ui.toast(_err(exc)[:60], "error")

    def _last_answer(self) -> str:
        project = self.project()
        if project is None:
            return ""
        for entry in reversed(self.studio.ai.conversation(project)):
            if entry.get("role") == "assistant" and entry.get("text"):
                return entry["text"]
        return ""

    def _read_answer(self):
        self.ui.push(ReaderScreen(self.ui, "Last answer", self._last_answer()))

    # ------------------------------------------------------------ drawing
    def dot(self):
        return {"working": (255, 176, 32), "listening": (255, 77, 79)}.get(self.view())

    def render(self, c):
        t = c.theme
        view = self.view()
        studio = self.studio
        project = self.project()
        self.header_height = 0
        if view == "typing":
            c.text(MARGIN, CONTENT_TOP + 8, "Type your request", 16, "bold", t.text)
            draw.wrapped(c, self.typed or "", MARGIN, CONTENT_TOP + 40, SCREEN_W - 2 * MARGIN, 15,
                         "medium", t.text, max_lines=8)
            text_field(c, (self.typed or "")[-40:], CONTENT_BOTTOM - 40, placeholder="Type…")
            return
        if view == "listening":
            draw.icon(c, "mic", SCREEN_W // 2, CONTENT_TOP + 40, 44, t.error)
            c.text(SCREEN_W // 2, CONTENT_TOP + 76, "Listening…", 22, "bold", t.text, anchor="ma")
            draw.level_bars(c, SCREEN_W // 2, CONTENT_TOP + 150, self.level, self.ui.phase,
                            t.accent)
            return
        if view == "processing":
            draw.spinner_title(c, "Processing…", "Turning your words into text", self.ui.phase)
            return
        if view == "heard":
            self.header_height = 118
            c.text(MARGIN, CONTENT_TOP + 4, "I heard", 13, "medium", t.text_faint)
            draw.wrapped(c, f"“{studio.voice.text}”", MARGIN, CONTENT_TOP + 24,
                         SCREEN_W - 2 * MARGIN, 16, "semibold", t.text, max_lines=4)
            draw_list(c, [i.row() for i in self.items()], self.index,
                      top=CONTENT_TOP + self.header_height)
            return
        if view == "voice-error":
            draw.big_status(c, "warning", "Didn't work", studio.voice.error, body_lines=4)
            c.text(SCREEN_W // 2, CONTENT_BOTTOM - 18, "Hold to try again", 13, "medium",
                   t.text_muted, anchor="ma")
            return
        if view == "working":
            turn = studio.ai.turn
            draw.spinner_title(c, "Claude is working", turn.phase if turn else "", self.ui.phase)
            if turn is not None:
                y = CONTENT_TOP + 138
                steps = [a for a in turn.activities if a.get("label")][-3:]
                for step in steps:
                    color = t.error if step.get("error") else t.text_faint
                    c.text(MARGIN + 4, y, "· " + step["label"], 12, "regular", color,
                           max_width=SCREEN_W - 2 * MARGIN - 8)
                    y += 17
                c.text(SCREEN_W - MARGIN, CONTENT_TOP + 2, _elapsed(time.time() - turn.started),
                       12, "medium", t.text_faint, anchor="ra")
            return
        if view == "review" and project is not None:
            review = studio.ai.project_ai(project.id).review or {}
            self.header_height = 110
            count = len(review.get("paths", []))
            held = review.get("held", [])
            if review.get("mode") != "ask":
                draw.icon(c, "warning", SCREEN_W // 2, CONTENT_TOP + 20, 30, t.warning)
                c.text(SCREEN_W // 2, CONTENT_TOP + 40, "Needs your OK", 19, "bold", t.text,
                       anchor="ma")
                body = "Claude changed settings that can stop the board from starting or add " \
                       "libraries. They are on hold."
            else:
                draw.icon(c, "spark", SCREEN_W // 2, CONTENT_TOP + 20, 30, t.accent)
                c.text(SCREEN_W // 2, CONTENT_TOP + 40, f"{count} file{'s' if count != 1 else ''}"
                       " changed", 19, "bold", t.text, anchor="ma")
                body = review.get("summary") or "Review before applying."
                if held:
                    body = f"Includes {len(held)} risky change(s). " + body
            draw.wrapped(c, body, MARGIN, CONTENT_TOP + 66, SCREEN_W - 2 * MARGIN, 12, "regular",
                         t.warning if held else t.text_muted, max_lines=2, anchor_center=True)
            draw_list(c, [i.row() for i in self.items()], self.index,
                      top=CONTENT_TOP + self.header_height)
            return
        if view in ("done", "failed"):
            turn = studio.ai.turn
            self.header_height = 112
            if view == "done":
                draw.big_status(c, "check", "Done", turn.summary if turn else "", compact=True)
            else:
                draw.big_status(c, "cross" if turn and turn.state == "error" else "warning",
                                "Not finished", turn.error if turn else "", compact=True)
            draw_list(c, [i.row() for i in self.items()], self.index,
                      top=CONTENT_TOP + self.header_height)
            return
        # ready
        voice = studio.voice
        ready_voice = voice is not None and voice.status()["stt_ready"]
        draw.icon(c, "mic", SCREEN_W // 2, CONTENT_TOP + 44, 56,
                  t.accent if ready_voice else t.text_faint)
        c.text(SCREEN_W // 2, CONTENT_TOP + 88, "Hold to talk" if ready_voice else "Voice is off",
               22, "bold", t.text, anchor="ma")
        sub = project.name if project else ""
        if not ready_voice:
            sub = "Type on a keyboard, or 3× for the menu"
        c.text(SCREEN_W // 2, CONTENT_TOP + 120, sub, 14, "medium", t.text_muted, anchor="ma",
               max_width=SCREEN_W - 2 * MARGIN)
        mode = AI_MODES.get(studio.settings.get("ai.mode"), "")
        c.text(SCREEN_W // 2, CONTENT_TOP + 148, mode, 12, "medium", t.text_faint, anchor="ma")
        last = self._last_answer()
        if last:
            from ..ai.context import summary_line
            c.text(SCREEN_W // 2, CONTENT_BOTTOM - 22, "Last: " + summary_line(last), 12,
                   "regular", t.text_faint, anchor="ma", max_width=SCREEN_W - 2 * MARGIN)


VOICE_HELP = ("Voice requests need speech recognition, which is not set up.\n\n"
              "Option 1: put your OpenAI key in mCode's .env file "
              "(~/.whisplay-os/apps/mcode/data/.env): OPENAI_API_KEY=…\n\n"
              "Option 2: Settings > Voice in the desktop IDE: a local speech command "
              "such as whisper.cpp.\n\nYou can always type on a USB or Bluetooth keyboard, or "
              "use the desktop IDE.")


class AIMenuScreen(ListScreen):
    title = "AI menu"

    def items(self):
        ui = self.ui
        studio = self.studio
        project = _project(ui)
        items = []
        keyboard = ui.input.keyboard_connected
        items.append(Item("Type a request", self._type, kind="nav",
                          subtitle=None if keyboard else "Needs a keyboard", enabled=keyboard))
        if project is not None:
            last = studio.builds.last(project.id)
            if last and last.get("state") not in ("succeeded", None) and \
                    last.get("action") in ("build", "rebuild"):
                items.append(Item("Fix the last build", lambda: self._run(
                    lambda: studio.ai.fix_build(project, "lcd")), subtitle=(
                    last.get("headline") or "")[:40] or None, tone="warning"))
            if studio.monitor is not None and studio.monitor.lines:
                items.append(Item("Explain serial output", lambda: self._run(
                    lambda: studio.ai.analyze_serial(project, source="lcd"))))
            items.append(Item("Review my code", lambda: self._run(
                lambda: studio.ai.ask(project, "Review this project.", kind="review",
                                      source="lcd"))))
            items.append(Item("Wiring document", lambda: self._run(
                lambda: studio.ai.ask(project, "Write the wiring document.", kind="wiring",
                                      source="lcd"))))
            last_turn = studio.ai.project_ai(project.id).last_turn or {}
            if last_turn.get("applied"):
                items.append(Item("Undo Claude's last change", lambda: self._undo(project),
                                  kind="danger", select="undo"))
        mode = studio.settings.get("ai.mode")
        items.append(Item("Mode", lambda: studio.settings.cycle("ai.mode"),
                          value=AI_MODE_SHORT[mode], select="change"))
        if project is not None:
            items.append(Item("New conversation", lambda: ui.push(ConfirmScreen(
                ui, "AI menu", "Start over?", "Claude forgets this project's conversation.",
                "Start over", lambda: studio.ai.clear_conversation(project))), kind="nav"))
        items.append(Item("Claude account", lambda: ui.push(ClaudeSetupScreen(ui)), kind="nav"))
        items.append(Item("Back", ui.pop, kind="back"))
        return items

    def _type(self):
        self.ui.pop()
        screen = self.ui.top()
        if isinstance(screen, AIScreen):
            screen._start_typing("")

    def _run(self, start):
        self.ui.pop()
        try:
            start()
        except Exception as exc:
            self.ui.toast(_err(exc)[:60], "error")
            return
        self.ui.say("working")

    def _undo(self, project):
        try:
            result = self.studio.ai.undo(project)
            self.ui.pop()
            self.ui.toast(f"Put back {len(result['reverted'])} file(s)", "success")
        except Exception as exc:
            self.ui.toast(_err(exc)[:60], "error")


class ActivityScreen(Screen):
    title = "Claude's steps"
    animated = True

    def controls(self):
        return [Control(BACK, "back", lambda a: self.ui.pop())]

    def render(self, c):
        t = c.theme
        turn = self.studio.ai.turn
        if turn is None:
            message(c, "Nothing running")
            return
        steps = turn.activities[-11:]
        y = CONTENT_TOP + 4
        if not steps:
            c.text(MARGIN, y, "Starting…", 14, "medium", t.text_muted)
        for step in steps:
            color = t.error if step.get("error") else t.text
            c.text(MARGIN, y, "· " + step["label"], 13, "regular", color,
                   max_width=SCREEN_W - 2 * MARGIN)
            y += 18


class ChangesScreen(ListScreen):
    title = "Changes"

    def __init__(self, ui, project_id: str):
        super().__init__(ui)
        self.project_id = project_id

    def items(self):
        project = self.studio.projects.find(self.project_id)
        review = self.studio.ai.review(project) or {}
        out = []
        for change in review.get("changes", []):
            sub = f"{change['status']} · +{change['added']} −{change['removed']}"
            if change.get("risky"):
                sub = "⚠ " + change["risky"][0]
            out.append(Item(os.path.basename(change["path"]), lambda ch=change: self.ui.push(
                ReaderScreen(self.ui, os.path.basename(ch["path"]), ch["diff"] or "(binary)",
                             mono=True)), subtitle=sub, kind="nav",
                tone="warning" if change.get("risky") else ""))
        out.append(Item("Back", self.ui.pop, kind="back"))
        return out


# ============================================================ Claude account

class ClaudeSetupScreen(ListScreen):
    title = "Claude Code"
    header_height = 92

    def enter(self):
        if self.studio.ai is not None:
            self.studio.ai.refresh_status()

    def on_event(self, topic, data):
        return topic in ("ai", "tools")

    def items(self):
        ui = self.ui
        ai = self.studio.ai
        status = ai.status()
        items = []
        if not status["installed"]:
            items.append(Item("Install Claude Code", lambda: confirm_install(ui, detect.CLAUDE),
                              select="install"))
        elif not status["auth"].get("logged_in"):
            items.append(Item("Connect Claude account", lambda: ui.push(LoginScreen(ui)),
                              select="sign in"))
        else:
            items.append(Item("Open AI Engineer", lambda: (ui.pop(), open_ai(ui)), kind="nav"))
            items.append(Item("Sign out", lambda: ui.push(ConfirmScreen(
                ui, "Claude Code", "Sign out?", "mCode and Claude Code on this device will need "
                "a new sign-in.", "Sign out", self._logout, danger=True)), kind="danger",
                select="sign out"))
        items.append(Item("Check again", lambda: ai.refresh_status(), select="check"))
        items.append(Item("Back", ui.pop, kind="back"))
        return items

    def _logout(self):
        try:
            self.studio.ai.logout()
            self.ui.toast("Signed out")
        except Exception as exc:
            self.ui.toast(_err(exc)[:60], "error")

    def render_header(self, c, top):
        t = c.theme
        status = self.studio.ai.status()
        auth = status["auth"]
        if not status["installed"]:
            state, tone = "Not installed", t.warning
        elif auth.get("logged_in"):
            state, tone = auth.get("label", "Signed in"), t.success
        elif status["state"] == "checking":
            state, tone = "Checking…", t.text_muted
        else:
            state, tone = "Not signed in", t.warning
        rows = [("Account", state, tone),
                ("Version", status["version"] or "—")]
        if status.get("memory_warning"):
            rows.append(("Memory", "Low for Claude", t.warning))
        elif auth.get("api_key_env"):
            rows.append(("Billing", "API key", t.warning))
        draw.facts(c, rows, top + 6)


class LoginScreen(Screen):
    """Claude sign-in in two steps, each a QR code for the phone:

    1. Claude's sign-in page (``claude auth login``'s link);
    2. mCode's sign-in code page on this device (web/codedrop.py), where the
       phone pastes the code Claude shows. A USB keyboard can type it instead.
    """
    title = "Sign in"
    animated = True

    def __init__(self, ui):
        super().__init__(ui)
        self.code = ""
        self.error = ""
        self.step = 1

    def get_title(self):
        if self.state() == "waiting":
            return "1 · Sign in" if self.step == 1 else "2 · Send code"
        return self.title

    def _code_url(self) -> str:
        login = self.studio.ai.status().get("login") or {}
        return login.get("code_url", "")

    def enter(self):
        session = self.studio.ai.login_session
        if session is None or session.state in ("done", "failed", "cancelled"):
            try:
                self.studio.ai.login_start()
            except Exception as exc:
                self.error = _err(exc)

    def state(self) -> str:
        session = self.studio.ai.login_session
        if self.error:
            return "failed"
        return session.state if session is not None else "starting"

    def typing(self):
        return self.state() == "waiting"

    def animating(self):
        return self.state() in ("starting", "checking")

    def controls(self):
        state = self.state()
        if state == "waiting":
            other = "step 2" if self.step == 1 else "step 1"
            controls = [Control(NEXT, other, lambda a: self._other_step())]
            if self.code:
                controls.append(Control(SELECT, "send code", lambda a: self._submit()))
            else:
                controls.append(Control(SELECT, "", lambda a: self._other_step()))
            return controls + [Control(BACK, "cancel", lambda a: self._cancel()),
                               Control(CHAR, "", lambda a: self._type(a.char)),
                               Control(ERASE, "", lambda a: self._erase())]
        if state == "done":
            return [Control(SELECT, "done", lambda a: self._finish()),
                    Control(BACK, "done", lambda a: self._finish())]
        if state == "failed":
            return [Control(SELECT, "try again", lambda a: self._retry()),
                    Control(BACK, "back", lambda a: self._cancel())]
        return [Control(BACK, "cancel", lambda a: self._cancel())]

    def _other_step(self):
        self.step = 2 if self.step == 1 else 1

    def _type(self, char):
        self.step = 2
        if len(self.code) < 1000:
            self.code += char

    def _erase(self):
        self.code = self.code[:-1]

    def _submit(self):
        try:
            self.studio.ai.login_code(self.code)
            self.code = ""
        except Exception as exc:
            self.ui.toast(_err(exc)[:60], "error")

    def _cancel(self):
        self.studio.ai.login_cancel()
        self.ui.pop()

    def _retry(self):
        self.error = ""
        self.code = ""
        self.step = 1
        self.studio.ai.login_session = None
        self.enter()

    def _finish(self):
        self.ui.pop()
        self.ui.toast("Claude connected", "success")

    def on_event(self, topic, data):
        if topic == "ai" and data.get("what") == "login":
            session = self.studio.ai.login_session
            if session is not None and session.state == "done":
                self.ui.say("done")
            return True
        return False

    def render(self, c):
        t = c.theme
        state = self.state()
        session = self.studio.ai.login_session
        if state == "starting":
            draw.spinner_title(c, "Starting sign-in…", "Opening Claude Code's login",
                               self.ui.phase)
        elif state == "waiting" and session is not None and self.step == 1:
            size = draw.qr_code(c, session.url, SCREEN_W // 2, CONTENT_TOP + 2, 168)
            y = CONTENT_TOP + size + 8
            c.text(SCREEN_W // 2, y, "Scan with your phone, sign in", 14, "semibold", t.text,
                   anchor="ma")
            c.text(SCREEN_W // 2, y + 20, "copy the code, then tap: step 2", 13, "regular",
                   t.text_muted, anchor="ma")
        elif state == "waiting" and session is not None:
            self._render_send(c)
        elif state == "checking":
            draw.spinner_title(c, "Checking…", "Finishing the sign-in", self.ui.phase)
        elif state == "done":
            draw.big_status(c, "check", "Connected", "Your Claude account is ready in mCode.")
        else:
            error = self.error or (session.error if session else "") or "Sign-in did not finish."
            draw.big_status(c, "cross", "Not signed in", error, body_lines=4)

    def _render_send(self, c):
        t = c.theme
        url = self._code_url()
        if self.code:
            draw.wrapped(c, "Typed code (hold to send):", MARGIN, CONTENT_TOP + 8,
                         SCREEN_W - 2 * MARGIN, 13, "semibold", t.text_muted, max_lines=1)
            text_field(c, self.code[-40:], CONTENT_TOP + 30, height=28)
            return
        if not url:
            draw.big_status(c, "warning", "Type the code", LOGIN_TYPE_HELP +
                            " Or paste it in the desktop IDE (Claude sign-in).", body_lines=4)
            return
        size = draw.qr_code(c, url, SCREEN_W // 2, CONTENT_TOP + 2, 168)
        y = CONTENT_TOP + size + 8
        c.text(SCREEN_W // 2, y, "Scan, paste the code, Send", 14, "semibold", t.text,
               anchor="ma")
        c.text(SCREEN_W // 2, y + 20, f"Same Wi-Fi · {_short_address(url)}", 12, "regular",
               t.text_muted, anchor="ma")


LOGIN_TYPE_HELP = "Type the code on a USB keyboard, then press Enter."


def _short_address(url: str) -> str:
    """"http://192.168.0.130:41234/c/token" -> "192.168.0.130:41234" (never the token)."""
    rest = url.split("://", 1)[-1]
    return rest.split("/", 1)[0]


# ================================================================== projects

class ProjectsScreen(ListScreen):
    title = "My Projects"

    def on_event(self, topic, data):
        return topic == "projects"

    def items(self):
        ui = self.ui
        current = _project(ui)
        out = []
        for project in self.studio.projects.list()[:30]:
            mark = "success" if current is not None and current.id == project.id else None
            out.append(Item(project.name, lambda p=project: self._open(p),
                            subtitle=f"{project.board_name}", kind="nav", mark=mark))
        out.append(Item("New project", lambda: ui.push(NewPlatformScreen(ui)), kind="action",
                        tone="accent", value="+", select="create"))
        out.append(Item("Back", ui.pop, kind="back"))
        return out

    def _open(self, project):
        self.studio.projects.set_current(project)
        self.ui.push(ProjectScreen(self.ui, project.id))


class ProjectScreen(ListScreen):
    header_height = 62

    def __init__(self, ui, project_id: str):
        super().__init__(ui)
        self.project_id = project_id

    def project(self):
        try:
            return self.studio.projects.find(self.project_id)
        except ProjectError:
            return None

    def get_title(self):
        project = self.project()
        return project.name if project else "Project"

    def on_event(self, topic, data):
        return topic in ("build", "upload", "projects")

    def items(self):
        ui = self.ui
        project = self.project()
        if project is None:
            return [Item("Back", ui.pop, kind="back")]
        return [
            Item("Ask AI", lambda: open_ai(ui), kind="nav"),
            Item("Build", lambda: start_build(ui, project, "build"), select="build"),
            Item("Flash", lambda: ui.push(FlashConfirmScreen(ui, project.id)), kind="nav",
                 select="flash"),
            Item("Board settings", lambda: ui.push(BoardSettingsScreen(ui, project.id)),
                 kind="nav", value=project.board_name),
            Item("Serial monitor", lambda: ui.push(SerialScreen(ui)), kind="nav"),
            Item("Project info", lambda: ui.push(ReaderScreen(ui, "Project info",
                                                              project_text(self.studio, project))),
                 kind="nav"),
            Item("More", lambda: ui.push(ProjectMoreScreen(ui, project.id)), kind="nav"),
            Item("Back", ui.pop, kind="back"),
        ]

    def render_header(self, c, top):
        t = c.theme
        project = self.project()
        if project is None:
            return
        c.text(MARGIN, top + 4, f"{project.board_name}", 15, "semibold", t.text,
               max_width=SCREEN_W - 2 * MARGIN)
        last = self.studio.builds.last(project.id)
        if last:
            ok = last.get("state") == "succeeded"
            text = ("Last build OK" if ok else "Last build failed") + \
                (f" · {int(last['size']['flash']['percent'])}% flash" if ok and
                 (last.get("size") or {}).get("flash") else "")
            tone = t.success if ok else t.error
        else:
            text, tone = "Not built yet", t.text_muted
        c.text(MARGIN, top + 28, text, 13, "medium", tone, max_width=SCREEN_W - 2 * MARGIN)


def project_text(studio, project) -> str:
    board = project.board
    lines = [project.name, "",
             f"Board: {project.board_name}", f"MCU: {project.mcu}",
             f"Tool: {boards.TOOLCHAIN_NAMES.get(project.toolchain, project.toolchain)} "
             f"({project.framework})"]
    if project.fqbn():
        lines.append(f"FQBN: {project.fqbn()}")
    if project.pio_env:
        lines.append(f"Environment: {project.pio_env}")
    lines.append(f"Serial: {project.baud} baud")
    lines.append(f"Folder: {project.path.replace(os.path.expanduser('~'), '~')}")
    if project.libraries:
        lines.append("Libraries: " + ", ".join(project.libraries))
    if board and board.notes:
        lines += ["", board.notes]
    last = studio.builds.last(project.id)
    if last:
        lines += ["", f"Last {last.get('action')}: {last.get('state')}",
                  last.get("headline") or ""]
    return "\n".join(lines)


class ProjectMoreScreen(ListScreen):
    title = "More"

    def __init__(self, ui, project_id: str):
        super().__init__(ui)
        self.project_id = project_id

    def items(self):
        ui = self.ui
        try:
            project = self.studio.projects.find(self.project_id)
        except ProjectError:
            return [Item("Back", ui.pop, kind="back")]
        return [
            Item("Run tests", lambda: start_build(ui, project, "test"), select="test"),
            Item("Rebuild", lambda: start_build(ui, project, "rebuild"), select="rebuild"),
            Item("Clean", lambda: start_build(ui, project, "clean"), select="clean"),
            Item("Back up now", lambda: self._backup(project), select="back up"),
            Item("Duplicate", lambda: self._duplicate(project), select="copy"),
            Item("Delete project", lambda: ui.push(ConfirmScreen(
                ui, project.name, "Delete project?", "It moves to the project trash and can be "
                "restored from the desktop IDE.", "Delete", lambda: self._delete(project),
                danger=True)), kind="danger", select="delete"),
            Item("Back", ui.pop, kind="back"),
        ]

    def _backup(self, project):
        self.studio.projects.backup(project, "manual")
        self.ui.toast("Backup saved", "success")

    def _duplicate(self, project):
        try:
            copy = self.studio.projects.duplicate(project)
            self.ui.toast(f"Created {copy.name}"[:40], "success")
        except Exception as exc:
            self.ui.toast(_err(exc)[:60], "error")

    def _delete(self, project):
        self.studio.projects.delete(project)
        self.ui.home()
        self.ui.toast("Project moved to trash")


# ==================================================================== wizard

class BoardSettingsScreen(ListScreen):
    """The project's board settings, like the Arduino IDE's Tools menu
    (toolchains/boardconfig.py): board, USB CDC on boot, upload speed, port and
    the board's own menus from the installed Arduino core."""

    title = "Board"
    header_height = 50

    def __init__(self, ui, project_id: str):
        super().__init__(ui)
        self.project_id = project_id
        self.settings = None            # with the core's menus, once loaded
        self.loading = False

    def project(self):
        try:
            return self.studio.projects.find(self.project_id)
        except ProjectError:
            return None

    def enter(self):
        project = self.project()
        if project is None or self.loading:
            return
        self.loading = True
        threading.Thread(target=self._load, name="board-settings", daemon=True).start()

    def _load(self):
        project = self.project()
        try:
            self.settings = self.studio.boardconfig.settings(project) if project else []
        except Exception:                 # isolation: show the basic settings instead
            log.exception("board settings for %s", self.project_id)
            self.settings = None
        self.loading = False
        self.ui.wake()

    def on_event(self, topic, data):
        if topic == "projects":
            self.settings = None
            self.enter()
            return True
        return topic == "devices"

    def _current(self, project):
        if self.settings is not None:
            return self.settings
        return self.studio.boardconfig.settings(project, menus=False)

    def items(self):
        ui = self.ui
        project = self.project()
        if project is None:
            return [Item("Back", ui.pop, kind="back")]
        config = self.studio.boardconfig
        items = []
        connected = config.connected_board(project)
        settings = self._current(project)
        board_setting = settings[0]
        if connected is not None and connected.id != project.board_id and \
                connected.id in {c.value for c in board_setting.choices}:
            items.append(Item(f"Use {connected.name}", lambda: self._apply(
                project, "board", connected.id), subtitle="the board on the port", tone="accent",
                select="use"))
        for setting in settings:
            if setting.kind == "toggle":
                items.append(Item(setting.label, lambda st=setting: self._apply(
                    project, st.id, "0" if st.value == "1" else "1"), kind="toggle",
                    value=setting.value == "1", select="switch"))
            else:
                value = "Default" if setting.id == "upload_speed" and setting.value == "0" \
                    else setting.value_label()
                items.append(Item(setting.label, lambda st=setting: self._choose(project, st),
                                  kind="nav", value=value))
            if setting.id == "upload_speed" or (setting.id == "board" and len(settings) == 1):
                items.append(self._port_item(project))
        if not any(i.label == "Port" for i in items):
            items.append(self._port_item(project))
        if self.settings is None and project.toolchain == "arduino-cli":
            items.append(Item("Loading board options…", None, kind="info", enabled=False))
        items.append(Item("Back", ui.pop, kind="back"))
        return items

    def _port_item(self, project):
        devices = self.studio.devices
        device = devices.selected(project) if devices else None
        value = (device.short_port if device else "none")
        return Item("Port", lambda: self.ui.push(BoardsScreen(self.ui)), kind="nav", value=value)

    def _choose(self, project, setting):
        ui = self.ui

        def build():
            out = [Item(c.label, lambda c=c: (ui.pop(), self._apply(project, setting.id, c.value)),
                        mark="success" if c.value == setting.value else None, select="choose")
                   for c in setting.choices]
            out.append(Item("Back", ui.pop, kind="back"))
            return out
        ui.push(MenuScreen(ui, setting.label, build))

    def _apply(self, project, setting_id, value):
        try:
            self.studio.boardconfig.apply(project, setting_id, value)
        except Exception as exc:
            self.ui.push(ReaderScreen(self.ui, "Can't change", _err(exc)))
            return
        self.ui.toast("Saved · build before flashing", "success")

    def render_header(self, c, top):
        t = c.theme
        project = self.project()
        if project is None:
            return
        connected = self.studio.boardconfig.connected_board(project)
        devices = self.studio.devices
        device = devices.selected(project) if devices else None
        if device is None:
            line, tone = "No board chosen · Connect Board", t.warning
        elif connected is not None:
            match = connected.id == project.board_id
            line = f"{device.short_port}: {connected.name}" + ("" if match else " (not this board)")
            tone = t.success if match else t.warning
        else:
            line, tone = f"{device.short_port}: {device.describe()}", t.text_muted
        c.text(MARGIN, top + 4, project.board_name, 15, "semibold", t.text,
               max_width=SCREEN_W - 2 * MARGIN)
        c.text(MARGIN, top + 26, line, 12, "medium", tone, max_width=SCREEN_W - 2 * MARGIN)


class NewPlatformScreen(ListScreen):
    title = "1 · Platform"

    def items(self):
        ui = self.ui
        out = []
        for key, name in boards.PLATFORMS.items():
            count = len(boards.for_platform(key))
            out.append(Item(name, lambda k=key: ui.push(NewBoardScreen(ui, k)), kind="nav",
                            value=str(count) if count > 1 else None))
        out.append(Item("Back", ui.pop, kind="back"))
        return out


class NewBoardScreen(ListScreen):
    title = "2 · Board"

    def __init__(self, ui, platform: str):
        super().__init__(ui)
        self.platform = platform

    def _connected(self) -> set:
        """Board ids of the ESP32 chips esptool identified on connected boards."""
        from ..hardware import usbids
        found = set()
        for device in (self.studio.devices.list() if self.studio.devices else []):
            board = boards.for_esp_family(usbids.chip_family(device.chip)) if device.chip \
                else None
            if board is not None:
                found.add(board.id)
        return found

    def items(self):
        ui = self.ui
        connected = self._connected()
        listed = sorted(boards.for_platform(self.platform), key=lambda b: b.id not in connected)
        out = [Item(b.name, lambda b=b: ui.push(NewFrameworkScreen(ui, b.id)), kind="nav",
                    subtitle="connected now" if b.id in connected else
                    (b.mcu if b.mcu != b.name else None),
                    tone="success" if b.id in connected else "")
               for b in listed]
        out.append(Item("Back", ui.pop, kind="back"))
        return out


class NewFrameworkScreen(ListScreen):
    title = "3 · Tool"

    def __init__(self, ui, board_id: str):
        super().__init__(ui)
        self.board_id = board_id

    def items(self):
        ui = self.ui
        studio = self.studio
        tool_of = {"arduino-cli": detect.ARDUINO_CLI, "platformio": detect.PLATFORMIO,
                   "esp-idf": detect.ESP_IDF, "make": detect.MAKE}
        out = []
        for option in studio.projects.options_for(self.board_id):
            tool = studio.tools.get(tool_of[option["toolchain"]])
            out.append(Item(option["label"], lambda o=option: ui.push(NewNameScreen(
                ui, self.board_id, o["toolchain"], o["framework"])), kind="nav",
                subtitle="ready" if tool.found else "not installed yet",
                tone="success" if tool.found else "warning"))
        out.append(Item("Back", ui.pop, kind="back"))
        return out


class NewNameScreen(ListScreen):
    """Name the project: say it (hold), type it, or take the suggestion."""

    title = "4 · Name"
    header_height = 70

    def __init__(self, ui, board_id: str, toolchain: str, framework: str):
        super().__init__(ui)
        self.board_id = board_id
        self.toolchain = toolchain
        self.framework = framework
        self.name = self.studio.projects.suggest_name(board_id)
        self.typed: Optional[str] = None

    def talk(self):
        voice = self.studio.voice
        return self.typed is None and voice is not None and voice.status()["stt_ready"]

    def typing(self):
        return self.typed is not None

    def enter(self):
        if self.talk():
            self.studio.voice.arm()

    def leave(self):
        if self.studio.voice is not None:
            self.studio.voice.disarm()

    def animating(self):
        voice = self.studio.voice
        return voice is not None and voice.state in ("listening", "processing")

    def on_event(self, topic, data):
        if topic == "voice" and data.get("state") == "heard":
            heard = " ".join(data.get("text", "").strip(" .!?\"'").split())
            if heard:
                self.name = heard[:60].title() if heard.islower() else heard[:60]
            self.studio.voice.clear()
            return True
        return topic == "voice"

    def items(self):
        ui = self.ui
        items = [Item(f"Create “{self.name}”", self.create, select="create")]
        if ui.input.keyboard_connected:
            items.append(Item("Type a name", lambda: setattr(self, "typed", ""), kind="nav"))
        items.append(Item("Back", ui.pop, kind="back"))
        return items

    def controls(self):
        if self.typed is not None:
            return [Control(SELECT, "use name", lambda a: self._use_typed(), gesture="Enter"),
                    Control(BACK, "cancel", lambda a: setattr(self, "typed", None), gesture="Esc"),
                    Control(CHAR, "", lambda a: self._type(a.char)),
                    Control(ERASE, "", lambda a: self._erase())]
        if self.talk():
            voice = self.studio.voice
            return [Control(TALK_START, "say name", lambda a: voice.start_talk() and voice.cue("listen")),
                    Control(TALK_END, "", lambda a: voice.end_talk()),
                    Control(EXTRA, "choose", lambda a: self.activate()),
                    Control(NEXT, "next", lambda a: self.move(1)),
                    Control(BACK, "back", lambda a: self.ui.pop()),
                    Control(SELECT, "", lambda a: self.activate())]
        return super().controls()

    def _type(self, char):
        if self.typed is not None and len(self.typed) < 60:
            self.typed += char

    def _erase(self):
        if self.typed:
            self.typed = self.typed[:-1]

    def _use_typed(self):
        if self.typed and self.typed.strip():
            self.name = self.typed.strip()
        self.typed = None

    def create(self):
        self.ui.replace(CreatingScreen(self.ui, self.name, self.board_id, self.toolchain,
                                       self.framework))

    def render_header(self, c, top):
        t = c.theme
        voice = self.studio.voice
        board = boards.get(self.board_id)
        c.text(MARGIN, top + 2, f"{board.name if board else ''} · "
               f"{boards.TOOLCHAIN_NAMES.get(self.toolchain, '')}", 13, "medium", t.text_faint,
               max_width=SCREEN_W - 2 * MARGIN)
        if self.typed is not None:
            text_field(c, self.typed, top + 24, placeholder="Project name")
        elif voice is not None and voice.state == "listening":
            c.text(MARGIN, top + 28, "Listening… say the name", 16, "bold", t.error)
        elif voice is not None and voice.state == "processing":
            c.text(MARGIN, top + 28, "Processing…", 16, "bold", t.warning)
        else:
            hint = "Hold and say a name, or choose" if self.talk() else "Choose or type a name"
            c.text(MARGIN, top + 28, hint, 14, "medium", t.text_muted,
                   max_width=SCREEN_W - 2 * MARGIN)


class CreatingScreen(ListScreen):
    title = "New project"
    header_height = 160
    animated = True

    def __init__(self, ui, name, board_id, toolchain, framework):
        super().__init__(ui)
        self.args = (name, board_id, toolchain, framework)
        self.steps: List[tuple] = []        # (label, state ok|warn|run)
        self.project = None
        self.error = ""
        self.done = False

    def enter(self):
        if self.steps or self.done:
            return
        self.ui.say("creating")
        import threading
        threading.Thread(target=self._create, name="create-project", daemon=True).start()

    def animating(self):
        return not self.done

    def _step(self, label):
        if self.steps and self.steps[-1][1] == "run":
            self.steps[-1] = (self.steps[-1][0], "ok")
        self.steps.append((label, "run"))
        self.ui.wake()

    def _create(self):
        name, board_id, toolchain, framework = self.args
        try:
            self._step("Generating files")
            project = self.studio.projects.create(name, board_id, toolchain, framework,
                                                  on_step=lambda s: None)
            self._step("Checking tools")
            needs = self.studio.builds.requirements(project)
            self.steps[-1] = ("Checking tools", "ok")
            for need in needs:
                self.steps.append((f"Needs: {need['label']}", "warn"))
            self.project = project
        except Exception as exc:
            self.error = _err(exc)
            if self.steps:
                self.steps[-1] = (self.steps[-1][0], "fail")
        self.done = True
        if self.project is not None:
            self.ui.say("project_ready")
        self.ui.wake()

    def items(self):
        ui = self.ui
        if not self.done:
            return []
        if self.project is None:
            return [Item("Back", ui.pop, kind="back")]
        project = self.project
        items = []
        needs = self.studio.builds.requirements(project)
        for need in needs:
            fix = need.get("fix", "")
            if fix.startswith("install:"):
                items.append(Item(f"Install {need['label']}", lambda f=fix: confirm_install(
                    ui, f.split(":", 1)[1]), select="install", tone="warning"))
            elif fix.startswith("install-core:"):
                items.append(Item(f"Install {need['label']}", lambda f=fix: confirm_core(
                    ui, f.split(":", 1)[1]), select="install", tone="warning"))
        items += [Item("Ask AI", lambda: (ui.home(), open_ai(ui)), kind="nav"),
                  Item("Build", lambda: start_build(ui, project, "build"), select="build"),
                  Item("Done", lambda: (ui.home(), ui.push(ProjectsScreen(ui)),
                                        ui.push(ProjectScreen(ui, project.id))), kind="back",
                       select="close")]
        return items

    def render_header(self, c, top):
        t = c.theme
        if self.done and self.project is not None:
            c.text(SCREEN_W // 2, top + 4, "Project ready!", 20, "bold", t.success, anchor="ma")
        elif self.done:
            c.text(SCREEN_W // 2, top + 4, "Could not create", 20, "bold", t.error, anchor="ma")
        else:
            c.text(SCREEN_W // 2, top + 4, "Creating project…", 20, "bold", t.text, anchor="ma")
        y = top + 40
        for label, state in self.steps[-5:]:
            color = {"ok": t.success, "warn": t.warning, "fail": t.error}.get(state, t.accent)
            if state == "run":
                c.spinner(MARGIN + 10, y + 8, 7, self.ui.phase)
            else:
                draw.icon(c, {"ok": "check", "warn": "warning", "fail": "cross"}[state],
                          MARGIN + 10, y + 8, 16, color)
            c.text(MARGIN + 26, y, label, 14, "medium", t.text, max_width=SCREEN_W - 2 * MARGIN - 30)
            y += 24
        if self.error:
            draw.wrapped(c, self.error, MARGIN, y + 2, SCREEN_W - 2 * MARGIN, 12, "regular",
                         t.error, max_lines=3)

    def render(self, c):
        self.header_height = 160 if self.done else 200
        super().render(c)


# =================================================================== building

def start_build(ui, project, action: str) -> None:
    if project is None:
        ui.toast("Open a project first", "error")
        return
    studio = ui.studio
    try:
        job = studio.builds.run(project, action, source="lcd")
    except Exception as exc:
        fix = getattr(exc, "fix", "")
        if fix.startswith("install:"):
            confirm_install(ui, fix.split(":", 1)[1], reason=_err(exc))
        elif fix.startswith("install-core:"):
            confirm_core(ui, fix.split(":", 1)[1], reason=_err(exc))
        else:
            ui.push(ReaderScreen(ui, "Can't build", _err(exc)))
        return
    if action in ("build", "rebuild"):
        ui.say("building")
    ui.push(BuildScreen(ui, job.id, project.id, action))


class BuildScreen(JobScreen):
    running_title = "Building…"
    success_title = "Build successful"
    failure_title = "Build failed"

    def __init__(self, ui, job_id, project_id, action):
        super().__init__(ui, job_id, {"build": "Build", "rebuild": "Rebuild", "clean": "Clean",
                                      "test": "Tests", "check": "Check"}.get(action, "Build"))
        self.project_id = project_id
        self.action = action
        if action == "test":
            self.running_title, self.success_title, self.failure_title = \
                "Testing…", "Tests passed", "Tests failed"
        elif action == "clean":
            self.running_title, self.success_title, self.failure_title = \
                "Cleaning…", "Clean", "Clean failed"

    def project(self):
        try:
            return self.studio.projects.find(self.project_id)
        except ProjectError:
            return None

    def success_body(self, job):
        report = job.result.get("report") or {}
        size = report.get("size") or {}
        if self.action == "test":
            tests = job.result.get("tests") or {}
            return (tests.get("summary") or "") + "\nNo hardware test was run."
        if self.action == "clean":
            return "Build output removed."
        parts = []
        if "flash" in size:
            parts.append(f"Flash {size['flash']['percent']:.0f}%")
        if "ram" in size:
            parts.append(f"RAM {size['ram']['percent']:.0f}%")
        return "Firmware is ready. " + " · ".join(parts)

    def failure_body(self, job):
        report = job.result.get("report") or {}
        return report.get("headline") or job.error

    def success_items(self, job):
        ui = self.ui
        project = self.project()
        items = []
        if self.action in ("build", "rebuild") and project is not None:
            items.append(Item("Flash board", lambda: ui.replace(FlashConfirmScreen(ui, project.id)),
                              kind="nav", select="flash"))
        items.append(Item("Done", ui.pop, kind="back", select="close"))
        return items

    def failure_items(self, job):
        ui = self.ui
        project = self.project()
        items = []
        if project is not None and self.studio.ai is not None and self.action != "clean":
            items.append(Item("Ask AI to fix", lambda: self._fix(project), select="ask AI",
                              tone="accent"))
        report = job.result.get("report") or {}
        if report.get("missing_headers"):
            idf = project is not None and project.toolchain == "esp-idf"
            text = (IDF_MISSING_HELP if idf else MISSING_HELP).format(
                ", ".join(report["missing_headers"]))
            items.append(Item("Missing header help", lambda: ui.push(ReaderScreen(
                ui, "Missing header", text)), kind="nav"))
            if idf:
                items.append(Item("Board settings", lambda: ui.push(BoardSettingsScreen(
                    ui, project.id)), kind="nav"))
        items += [Item("View details", lambda: self.details(job), kind="nav"),
                  Item("Back", ui.pop, kind="back")]
        return items

    def _fix(self, project):
        try:
            self.studio.ai.fix_build(project, "lcd")
        except Exception as exc:
            fix = getattr(exc, "fix", "")
            if fix in ("login", "install:claude"):
                self.ui.push(ClaudeSetupScreen(self.ui))
                return
            self.ui.toast(_err(exc)[:60], "error")
            return
        self.ui.say("working")
        self.ui.replace(AIScreen(self.ui))


IDF_MISSING_HELP = ("The code includes {} but that ESP-IDF component is not part of the "
                    "build.\n\nWith Fast build on, ESP-IDF builds only main and the components "
                    "main/CMakeLists.txt asks for: add the component to REQUIRES there (for "
                    "esp_wifi.h: REQUIRES esp_wifi), or ask the AI Engineer to do it. Or turn "
                    "Fast build off in Board settings (slower builds).")

MISSING_HELP = ("The code includes {} but no installed library provides it.\n\n"
                "Install the library in the desktop IDE (Libraries), or ask the AI Engineer "
                "which library it is: Claude can name it, you approve the install.")


# =================================================================== hardware

class BoardsScreen(ListScreen):
    title = "Boards"

    def items(self):
        ui = self.ui
        devices = self.studio.devices.list() if self.studio.devices else []
        project = _project(ui)
        chosen = self.studio.devices.selected(project) if self.studio.devices else None
        out = []
        for device in devices:
            label = device.chip or (boards.get(device.board_id).name if device.board_id else
                                    device.name)
            out.append(Item(label, lambda d=device: ui.push(DeviceScreen(ui, d.port)), kind="nav",
                            subtitle=f"{device.short_port} · " + (
                                "no permission" if not device.readable else
                                (device.bridge or "USB")),
                            tone="error" if not device.readable else "",
                            mark="success" if chosen is not None and chosen.port == device.port
                            else None))
        out.append(Item("Refresh", self.enter, select="refresh"))
        if not devices and self.studio.devices is not None:
            out.append(Item("What to check", self._why, kind="nav"))
        out.append(Item("Back", ui.pop, kind="back"))
        return out

    def _help(self):
        if self.studio.devices is None:
            return "Connect a board by USB", ""
        if self._cached_help is None:
            self._cached_help = self.studio.devices.no_board_help()
        return self._cached_help

    _cached_help = None

    def enter(self):
        self._cached_help = None
        if self.studio.devices is not None:
            self.studio.devices.refresh()

    def on_event(self, topic, data):
        if topic == "devices":
            self._cached_help = None
            return True
        return False

    def _why(self):
        short, detail = self._help()
        self.ui.push(ReaderScreen(self.ui, "What to check", detail or short))

    def render(self, c):
        devices = self.studio.devices.list() if self.studio.devices else []
        if not devices:
            t = c.theme
            short, _detail = self._help()
            draw.icon(c, "plug", SCREEN_W // 2, CONTENT_TOP + 22, 36, t.text_faint)
            c.text(SCREEN_W // 2, CONTENT_TOP + 48, "No board found", 19, "bold", t.text,
                   anchor="ma")
            c.text(SCREEN_W // 2, CONTENT_TOP + 74, short, 14, "regular",
                   t.text_muted, anchor="ma")
            draw_list(c, [i.row() for i in self.items()], self.index, top=CONTENT_TOP + 102)
            return
        super().render(c)


class DeviceScreen(ListScreen):
    header_height = 112

    def __init__(self, ui, port: str):
        super().__init__(ui)
        self.port = port

    def device(self):
        return self.studio.devices.get(self.port) if self.studio.devices else None

    def get_title(self):
        return os.path.basename(self.port)

    def on_event(self, topic, data):
        return topic == "devices"

    def items(self):
        ui = self.ui
        device = self.device()
        if device is None:
            return [Item("Back", ui.pop, kind="back")]
        project = _project(ui)
        items = []
        if project is not None:
            items.append(Item(f"Use for {project.name}", lambda: self._use(device, project),
                              select="use"))
        if device.kind in ("bridge", "native-esp") or device.family == "esp32":
            items.append(Item("Identify chip", lambda: self._identify(device, project),
                              select="identify", subtitle="Restarts the board"))
        items.append(Item("Serial monitor", lambda: self._monitor(device), kind="nav"))
        items.append(Item("Back", ui.pop, kind="back"))
        return items

    def _use(self, device, project):
        self.studio.devices.select(device, project)
        self.ui.toast(f"{device.short_port} chosen", "success")
        self.ui.pop()

    def _identify(self, device, project):
        uploads = self.studio.uploads
        from ..projects.model import Project
        target = project or Project(path="", name="(no project)", platform="esp32",
                                    board_id="esp32-devkit", toolchain="arduino-cli")
        plan = uploads.prepare(target, device.port, action="identify")
        if not plan.ok:
            self.ui.push(ReaderScreen(self.ui, "Can't identify", plan.problem()))
            return
        job = uploads.start(plan.token, confirmed=True)
        self.ui.push(JobScreen(self.ui, job.id, "Identify"))

    def _monitor(self, device):
        self.ui.push(SerialScreen(self.ui, device.port))

    def render_header(self, c, top):
        t = c.theme
        device = self.device()
        if device is None:
            message(c, "Disconnected", "The board was unplugged.", tone="warning", top=top,
                    bottom=top + self.header_height)
            return
        rows = [("Board", device.describe()[:40], t.success if device.chip or device.board_id
                 else t.text),
                ("USB", f"{device.vid}:{device.pid}" + (f" · {device.bridge}" if device.bridge
                                                       else "")),
                ("Driver", device.driver or "—"),
                ("Status", "Ready" if device.readable else "No permission",
                 t.success if device.readable else t.error)]
        draw.facts(c, rows, top + 4, label_w=56, size=13)


class FlashConfirmScreen(ListScreen):
    title = "Flash"
    header_height = 128

    def __init__(self, ui, project_id: str):
        super().__init__(ui)
        self.project_id = project_id
        self.plan = None

    def project(self):
        try:
            return self.studio.projects.find(self.project_id)
        except ProjectError:
            return None

    def enter(self):
        project = self.project()
        if project is None or self.studio.uploads is None:
            return
        self.plan = self.studio.uploads.prepare(project)
        if self.plan.ok:
            self.ui.say("confirm")

    def on_event(self, topic, data):
        if topic == "devices":
            self.enter()
            return True
        return False

    def items(self):
        ui = self.ui
        plan = self.plan
        project = self.project()
        if plan is None or project is None:
            return [Item("Back", ui.pop, kind="back")]
        if plan.ok:
            return [Item("Confirm flash", self._flash, select="flash", tone="accent"),
                    Item("Cancel", ui.pop, kind="back", select="cancel")]
        failed = {c.id for c in plan.checks if not c.ok and c.level == "error"}
        items = []
        if "match" in failed:                 # building for the wrong chip would not help
            items += self._other_board_items(project)
        elif "firmware" in failed:
            items.append(Item("Build now", lambda: (ui.pop(), start_build(ui, project, "build")),
                              select="build"))
        if failed & {"device", "match", "port", "permission"}:
            items.append(Item("Connect Board", lambda: ui.push(BoardsScreen(ui)), kind="nav"))
        if failed & {"match", "board", "firmware"}:
            items.append(Item("Board settings", lambda: ui.push(BoardSettingsScreen(
                ui, project.id)), kind="nav"))
        items.append(Item("Check again", self.enter, select="check"))
        items.append(Item("Back", ui.pop, kind="back"))
        return items

    def _connected_board(self):
        """The catalogue board of the chip esptool found on the plan's port."""
        from ..hardware import usbids
        device = self.studio.devices.get(self.plan.port) if self.studio.devices else None
        if device is None or not device.chip:
            return None
        return boards.for_esp_family(usbids.chip_family(device.chip))

    def _other_board_items(self, project):
        """The board is a different ESP32 than the project's: offer to switch the
        project to it (same toolchain) or to start a project for it."""
        ui = self.ui
        board = self._connected_board()
        if board is None or board.id == project.board_id:
            return []
        if project.toolchain in board.toolchains and \
                project.framework in board.frameworks(project.toolchain):
            return [Item(f"Make it {board.name}", lambda: self._switch(project, board),
                         select="switch", tone="accent")]
        tool = boards.TOOLCHAIN_NAMES.get(project.toolchain, project.toolchain)
        return [Item(f"New {board.name} project", lambda: (ui.home(), ui.push(
            NewFrameworkScreen(ui, board.id))), kind="nav", subtitle=f"{tool} has no {board.name}")]

    def _switch(self, project, board):
        ui = self.ui
        note = (" ESP-IDF makes its sdkconfig again for the new chip (sdkconfig.defaults "
                "stays)." if project.toolchain == "esp-idf" else "")

        def go():
            try:
                updated = self.studio.projects.update(project, board_id=board.id)
            except Exception as exc:
                ui.push(ReaderScreen(ui, "Can't change", _err(exc)))
                return
            ui.pop()
            start_build(ui, updated, "rebuild")
        ui.push(ConfirmScreen(ui, "Flash", f"Make it {board.name}?",
                              f"The project's board becomes {board.name} (the chip on "
                              f"{os.path.basename(self.plan.port)}) and mCode builds it again. "
                              "Flash afterwards." + note, "Change and build", go,
                              icon="chip"))

    def _flash(self):
        plan = self.plan
        try:
            job = self.studio.uploads.start(plan.token, confirmed=True)
        except Exception as exc:
            self.ui.toast(_err(exc)[:60], "error")
            self.enter()
            return
        self.ui.say("flashing")
        self.ui.replace(FlashScreen(self.ui, job.id, self.project_id))

    def render_header(self, c, top):
        t = c.theme
        plan = self.plan
        if plan is None:
            return
        if plan.ok:
            c.text(SCREEN_W // 2, top + 2, "Flash firmware?", 20, "bold", t.text, anchor="ma")
            rows = [("Board", plan.board), ("Project", plan.project),
                    ("Port", os.path.basename(plan.port))]
            y = draw.facts(c, rows, top + 34, label_w=62, size=14)
            if plan.warnings:
                draw.wrapped(c, "⚠ " + plan.warnings[0].message, MARGIN, y - 4,
                             SCREEN_W - 2 * MARGIN, 11, "regular", t.warning, max_lines=2)
        else:
            draw.icon(c, "warning", SCREEN_W // 2, top + 16, 28, t.warning)
            c.text(SCREEN_W // 2, top + 34, "Can't flash yet", 19, "bold", t.text, anchor="ma")
            draw.wrapped(c, plan.problem(), MARGIN, top + 62, SCREEN_W - 2 * MARGIN, 13,
                         "regular", t.text_muted, max_lines=4, anchor_center=True)


class FlashScreen(JobScreen):
    running_title = "Flashing…"
    success_title = "Flash successful"
    failure_title = "Flash failed"

    def __init__(self, ui, job_id, project_id):
        super().__init__(ui, job_id, "Flash")
        self.project_id = project_id

    def success_body(self, job):
        verified = job.result.get("verified")
        return "Your board is ready." + (" Written data verified." if verified else "")

    def success_items(self, job):
        ui = self.ui
        return [Item("Serial monitor", lambda: ui.replace(SerialScreen(ui)), kind="nav"),
                Item("Done", ui.pop, kind="back", select="close")]

    def failure_items(self, job):
        ui = self.ui
        return [Item("Try again", lambda: ui.replace(FlashConfirmScreen(ui, self.project_id)),
                     select="retry"),
                Item("Details", lambda: self.details(job), kind="nav"),
                Item("Back", ui.pop, kind="back")]


class SerialScreen(ListScreen):
    title = "Serial"
    animated = False
    wants_lines = True

    def __init__(self, ui, port: str = ""):
        super().__init__(ui)
        self.port = port

    def monitor(self):
        return self.studio.monitor

    def target(self) -> str:
        if self.port:
            return self.port
        project = _project(self.ui)
        device = self.studio.devices.selected(project) if self.studio.devices else None
        return device.port if device else ""

    def baud(self) -> int:
        project = _project(self.ui)
        return project.baud if project else int(self.studio.settings.get("hw.default_baud"))

    def on_event(self, topic, data):
        return topic in ("monitor", "monitor.state", "devices", "monitor.cleared")

    def get_title(self):
        monitor = self.monitor()
        if monitor is not None and monitor.connected:
            return os.path.basename(monitor.port)
        return "Serial"

    def dot(self):
        monitor = self.monitor()
        if monitor is None:
            return None
        if monitor.connected and not monitor.paused:
            return (52, 199, 110)
        if monitor.paused or monitor.suspended:
            return (255, 176, 32)
        return None

    def connected(self) -> bool:
        monitor = self.monitor()
        return monitor is not None and (monitor.connected or monitor.suspended)

    def controls(self):
        if not self.connected():
            return super().controls()
        monitor = self.monitor()
        return [Control(NEXT, "resume" if monitor.paused else "pause",
                        lambda a: monitor.set_paused(not monitor.paused)),
                Control(EXTRA, "ask AI", lambda a: self._ask_ai()),
                Control(BACK, "back", lambda a: self.ui.pop()),
                Control(SELECT, "disconnect", lambda a: monitor.disconnect()),
                Control(PREVIOUS, "clear", lambda a: monitor.clear())]

    def items(self):
        ui = self.ui
        port = self.target()
        items = []
        if port:
            items.append(Item(f"Connect {os.path.basename(port)}", self._connect,
                              select="connect", value=f"{self.baud()}"))
        else:
            items.append(Item("Choose a board", lambda: ui.push(BoardsScreen(ui)), kind="nav"))
        items.append(Item("Baud rate", self._cycle_baud, value=str(self.baud()), select="change"))
        items.append(Item("Back", ui.pop, kind="back"))
        return items

    def _connect(self):
        try:
            self.monitor().connect(self.target(), self.baud())
        except Exception as exc:
            self.ui.toast(_err(exc)[:60], "error")

    def _cycle_baud(self):
        project = _project(self.ui)
        rates = [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]
        current = self.baud()
        nxt = rates[(rates.index(current) + 1) % len(rates)] if current in rates else 115200
        if project is not None:
            self.studio.projects.update(project, baud=nxt)
        else:
            self.studio.settings.set("hw.default_baud", nxt)

    def _ask_ai(self):
        project = _project(self.ui)
        if project is None:
            self.ui.toast("Open a project first", "error")
            return
        try:
            self.studio.ai.analyze_serial(project, source="lcd")
        except Exception as exc:
            fix = getattr(exc, "fix", "")
            if fix in ("login", "install:claude"):
                self.ui.push(ClaudeSetupScreen(self.ui))
                return
            self.ui.toast(_err(exc)[:60], "error")
            return
        self.ui.say("working")
        self.ui.push(AIScreen(self.ui))

    def render(self, c):
        if not self.connected():
            super().render(c)
            return
        t = c.theme
        monitor = self.monitor()
        lines = monitor.recent(40)
        y = CONTENT_BOTTOM - 4
        size = 12
        width = SCREEN_W - 2 * MARGIN + 6
        rows = []
        for line in reversed(lines):
            wrapped = c.wrap(line["text"], size, "regular", width) or [""]
            for part in reversed(wrapped):
                rows.append((part, line["dir"]))
            if len(rows) > 14:
                break
        for text, direction in rows[:13]:
            y -= size + 4
            if y < CONTENT_TOP:
                break
            color = {"tx": t.accent, "info": t.text_faint}.get(direction, t.text)
            c.text(MARGIN - 6, y, text, size, "regular", color)
        if monitor.paused:
            c.pill(SCREEN_W - MARGIN, CONTENT_TOP + 2, "Paused", t.bg, t.warning,
                   align_right=True)
        elif monitor.suspended:
            c.pill(SCREEN_W - MARGIN, CONTENT_TOP + 2, "Waiting", t.bg, t.warning,
                   align_right=True)
        else:
            c.pill(SCREEN_W - MARGIN, CONTENT_TOP + 2, f"{monitor.baud}", t.text_muted,
                   t.surface_hi, align_right=True)


# =================================================================== settings

class SettingsScreen(ListScreen):
    title = "Settings"

    def on_event(self, topic, data):
        return topic in ("settings", "ai", "tools")

    def items(self):
        ui = self.ui
        studio = self.studio
        s = studio.settings
        ai = studio.ai.status() if studio.ai else {"auth": {}, "state": "unavailable"}
        status = ui.status.sample()
        wifi = getattr(status, "wifi_level", None)
        web = getattr(studio, "web", None)
        return [
            Item("Claude Code", lambda: ui.push(ClaudeSetupScreen(ui)), kind="nav",
                 value=ai["auth"].get("label") if ai.get("installed") else "Not installed",
                 tone="success" if ai["auth"].get("logged_in") else "warning"),
            Item("AI mode", lambda: s.cycle("ai.mode"),
                 value=AI_MODE_SHORT[s.get("ai.mode")], select="change"),
            Item("Send voice at once", lambda: s.cycle("ai.voice_autosend"), kind="toggle",
                 value=s.get("ai.voice_autosend"), select="switch"),
            Item("Sound", lambda: s.cycle("voice.feedback"),
                 value=FIELD_BY_KEY["voice.feedback"].label_of(s.get("voice.feedback")),
                 select="change"),
            Item("Wi-Fi", lambda: ui.push(ReaderScreen(ui, "Wi-Fi", WIFI_HELP)), kind="nav",
                 value="Connected" if wifi else "Off",
                 tone="success" if wifi else "warning"),
            Item("Desktop IDE", lambda: ui.push(DesktopScreen(ui)), kind="nav",
                 value="On" if web is not None and web.running else "Off"),
            Item("Development tools", lambda: ui.push(ToolsScreen(ui)), kind="nav"),
            Item("About mCode", lambda: ui.push(ReaderScreen(ui, "About", about_text(studio))),
                 kind="nav"),
            Item("Back", ui.pop, kind="back"),
        ]


WIFI_HELP = ("Wi-Fi belongs to mFruit OS: leave mCode (4× from Home), then Settings > Wi-Fi.\n\n"
             "Claude Code, speech recognition and tool downloads need the internet. Building "
             "and flashing with installed tools work offline.")


def about_text(studio) -> str:
    from .. import VERSION
    from ..toolchains.detect import machine
    info = machine()
    return (f"mCode Studio {VERSION}\n\nAn AI embedded development studio for mFruit OS.\n\n"
            f"Device: {info.get('model') or info.get('arch')}\n"
            f"Memory: {info.get('memory_mb')} MB · Python {info.get('python')}\n\n"
            f"Projects: {studio.projects.root.replace(os.path.expanduser('~'), '~')}\n"
            f"Data: {studio.paths.data.replace(os.path.expanduser('~'), '~')}\n\n"
            "Claude Code is Anthropic's tool; mCode uses your own Claude Code login.")


class ToolsScreen(ListScreen):
    title = "Dev tools"

    def enter(self):
        self.studio.tools.refresh()

    def on_event(self, topic, data):
        return topic in ("tools", "tools.installed")

    def items(self):
        ui = self.ui
        studio = self.studio
        out = []
        for tool_id in (detect.CLAUDE, detect.ARDUINO_CLI, detect.PLATFORMIO, detect.ESPTOOL,
                        detect.ESP_IDF, detect.GIT):
            tool = studio.tools.get(tool_id)
            value = tool.version or ("found" if tool.found else "missing")
            run = (lambda t=tool_id: confirm_install(ui, t)) if not tool.found and \
                tool_id in detect.INSTALLABLE else (lambda t=tool: ui.push(ReaderScreen(
                    ui, t.name, tool_text(t))))
            out.append(Item(tool.name, run, value=value, tone="" if tool.found else "warning",
                            select="install" if not tool.found and tool_id in detect.INSTALLABLE
                            else "open"))
        if studio.tools.get(detect.ARDUINO_CLI).found:
            from ..toolchains.backends import ArduinoCliBackend, CORE_NAMES
            backend = ArduinoCliBackend(studio.tools, studio.settings)
            for core in ("esp32:esp32", "arduino:avr"):
                installed = backend.core_installed(core)
                out.append(Item(CORE_NAMES[core], (lambda c=core: confirm_core(ui, c))
                                if not installed else (lambda: None),
                                value="installed" if installed else "missing",
                                tone="" if installed else "warning",
                                select="install" if not installed else "open"))
        out.append(Item("Back", ui.pop, kind="back"))
        return out


def tool_text(tool) -> str:
    if tool.found:
        return f"{tool.name} {tool.version}\n\nFound: {tool.source}\n{tool.path}"
    return f"{tool.name} is not installed.\n\n{tool.problem}\n\n{tool.hint}"


def confirm_install(ui, tool_id: str, reason: str = "") -> None:
    from ..toolchains.installer import explain_install
    info = explain_install(tool_id)
    if info is None:
        ui.push(ReaderScreen(ui, "Install", detect.HINTS.get(tool_id, "")))
        return

    def go():
        try:
            job = ui.studio.builds.install_tool(tool_id)
        except Exception as exc:
            ui.toast(_err(exc)[:60], "error")
            return
        ui.push(JobScreen(ui, job.id, "Install"))

    ui.push(ConfirmScreen(ui, "Install", info["title"], (reason + " " if reason else "") +
                          info["body"], "Install", go, icon="plug"))


def confirm_core(ui, core: str, reason: str = "") -> None:
    from ..toolchains.backends import CORE_NAMES

    def go():
        try:
            job = ui.studio.builds.install_core(core)
        except Exception as exc:
            ui.toast(_err(exc)[:60], "error")
            return
        ui.push(JobScreen(ui, job.id, "Install"))

    ui.push(ConfirmScreen(ui, "Install", f"Install the {CORE_NAMES.get(core, core)}?",
                          (reason + " " if reason else "") + "Downloads it with the Arduino CLI "
                          "from the official board index (can be several hundred MB).",
                          "Install", go, icon="plug"))


class DesktopScreen(ListScreen):
    title = "Desktop IDE"
    header_height = 0

    def on_event(self, topic, data):
        return topic in ("web", "settings")

    def web(self):
        return getattr(self.studio, "web", None)

    def items(self):
        ui = self.ui
        s = self.studio.settings
        web = self.web()
        items = []
        if web is not None and web.running and web.desktop_available():
            items.append(Item("Open on HDMI screen", self._open, select="open"))
        items.append(Item("Use from a computer", lambda: self._lan(), kind="toggle",
                          value=s.get("web.lan"), select="switch"))
        if web is not None and web.running and s.get("web.lan"):
            items.append(Item("Pair a computer", lambda: ui.push(PairScreen(ui)), kind="nav"))
        if web is not None and web.running:
            count = len(web.sessions.list())
            items.append(Item("Sign out all computers", self._rotate, kind="danger",
                              subtitle=f"{count} paired" if count else None, select="sign out"))
        items.append(Item("Back", ui.pop, kind="back"))
        return items

    def _open(self):
        ok, why = self.web().open_browser()
        self.ui.toast("Opening on HDMI" if ok else why[:60], "success" if ok else "error")

    def _lan(self):
        s = self.studio.settings
        if s.get("web.lan"):
            s.set("web.lan", False)
            web = self.web()
            if web is not None:
                web.restart()
            return

        def enable():
            s.set("web.lan", True)
            web = self.web()
            if web is not None:
                web.restart()
            self.ui.push(PairScreen(self.ui))

        self.ui.push(ConfirmScreen(self.ui, "Desktop IDE", "Allow computers?",
                                   "Computers and phones on this network can pair while "
                                   "Pair a computer is open here. Flashing still asks first.",
                                   "Allow", enable))

    def _rotate(self):
        def go():
            web = self.web()
            if web is not None:
                web.rotate_token()
                self.ui.toast("All computers signed out", "success")

        self.ui.push(ConfirmScreen(self.ui, "Desktop IDE", "Sign out all?",
                                   "Every paired computer and phone must pair again.",
                                   "Sign out", go))

    def render(self, c):
        t = c.theme
        web = self.web()
        y = CONTENT_TOP + 6
        if web is None or not web.running:
            c.text(MARGIN, y, "The desktop IDE is off.", 14, "medium", t.text_muted)
            y += 24
        else:
            for url in web.urls()[:2]:
                c.text(MARGIN, y, url, 13, "semibold", t.text, max_width=SCREEN_W - 2 * MARGIN)
                y += 20
        draw_list(c, [i.row() for i in self.items()], self.index, top=y + 6)


class PairScreen(Screen):
    """Pairing exists only while this screen is open: a single-use QR link and
    6-digit code (web/sessions.py). 3× switches what the next pairing may do."""

    title = "Pairing"
    animated = True                 # the code renews after each pairing; keep it fresh
    ROLE_CYCLE = ("developer", "admin", "viewer")
    ROLE_SHORT = {"developer": "Developer", "admin": "Full access", "viewer": "Viewer"}

    def __init__(self, ui):
        super().__init__(ui)
        self.role = self.studio.settings.get("web.pair_role")

    def web(self):
        web = getattr(self.studio, "web", None)
        return web if web is not None and web.running else None

    def enter(self):
        super().enter()
        web = self.web()
        if web is not None:
            web.open_pairing(self.role)

    def leave(self):
        super().leave()
        web = self.web()
        if web is not None:
            web.close_pairing()

    def on_event(self, topic, data):
        return topic in ("web", "security")

    def _switch(self):
        index = self.ROLE_CYCLE.index(self.role) if self.role in self.ROLE_CYCLE else 0
        self.role = self.ROLE_CYCLE[(index + 1) % len(self.ROLE_CYCLE)]
        web = self.web()
        if web is not None:
            web.open_pairing(self.role)
        self.ui.toast(self.ROLE_SHORT[self.role])

    def controls(self):
        return [Control(EXTRA, "access", lambda a: self._switch()),
                Control(BACK, "back", lambda a: self.ui.pop())]

    def render(self, c):
        t = c.theme
        web = self.web()
        window = web.sessions.pairing() if web is not None else None
        if web is not None and (window is None or window.expires - window.clock() < 60):
            window = web.open_pairing(self.role)        # keep it open while shown
        url = web.pair_url() if web is not None else ""
        if not url or window is None:
            message(c, "No network address", "Connect Wi-Fi, then try again.", tone="warning")
            return
        size = draw.qr_code(c, url, SCREEN_W // 2, CONTENT_TOP + 2, 150)
        y = CONTENT_TOP + size + 4
        code = window.code
        c.text(SCREEN_W // 2, y, f"{code[:3]} {code[3:]}", 22, "bold", t.text, anchor="mt")
        note = f"Paired: {window.paired[-1]}" if window.paired else \
            f"{self.ROLE_SHORT.get(window.role, window.role)} · scan, or type the code"
        draw.wrapped(c, note, MARGIN, y + 28, SCREEN_W - 2 * MARGIN, 12, "medium",
                     t.success if window.paired else t.text_muted, max_lines=2,
                     anchor_center=True)
