"""Render every LCD screen, in its important states, to PNG files.

    ./run.sh --preview DIR

Uses a throw-away data folder with a demo project, two simulated USB boards
(a CP210x ESP32 DevKit and an ESP32-S3 on its own USB), simulated jobs and
AI turns. Nothing is drawn on a device, no network is used and nothing is
flashed. These images are layout evidence only: they do not show how the
physical LCD looks (brightness, corners, readability).
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from typing import Callable, List, Tuple


class FakeBoard:
    app_id = "mcode"

    def __init__(self):
        self.has_focus = True
        self.frames = []
        self.on_press = self.on_release = self.on_exit_request = lambda *a: None
        self.on_focus_changed = lambda *a: None

    def show(self, image):
        self.frames.append(image)

    def start(self):
        pass

    def stop(self):
        pass


class FakeStatusMonitor:
    class _S:
        wifi_level = 3
        battery = 78
        charging = False

    def sample(self):
        return self._S()

    def start(self):
        pass

    def stop(self):
        pass


class FakeVoice:
    """Stands in for VoiceService: no microphone or speaker."""

    def __init__(self):
        self.state = "idle"
        self.text = ""
        self.error = ""
        self.level = -30.0
        self.enabled_output = True
        self.said: List[str] = []
        self.armed = False

    def status(self):
        return {"state": self.state, "text": self.text, "error": self.error, "level": self.level,
                "stt_ready": True, "stt": "openai", "tts": "off", "microphone": "capture",
                "speaker": "playback", "feedback": "tones"}

    def arm(self):
        self.armed = True
        return True

    def disarm(self):
        self.armed = False

    def start_talk(self):
        self.state = "listening"
        return True

    def end_talk(self):
        self.state = "processing"

    def cancel(self):
        self.state = "idle"

    def clear(self):
        self.state = "idle"
        self.text = ""

    def say(self, phrase):
        self.said.append(phrase)

    def cue(self, kind):
        self.said.append("cue:" + kind)

    def stop(self):
        pass


def make_sysfs(root: str) -> str:
    """A fake /sys with two USB serial boards (the layout the kernel uses)."""
    def device(name, usb, vid, pid, product, manufacturer, serial, driver, acm):
        usb_dir = os.path.join(root, "devices", "platform", "usb1", usb)
        iface = os.path.join(usb_dir, f"{usb}:1.0")
        os.makedirs(iface, exist_ok=True)
        for key, value in (("idVendor", vid), ("idProduct", pid), ("product", product),
                           ("manufacturer", manufacturer), ("serial", serial)):
            with open(os.path.join(usb_dir, key), "w") as handle:
                handle.write(value + "\n")
        with open(os.path.join(iface, "bInterfaceNumber"), "w") as handle:
            handle.write("00\n")
        drivers = os.path.join(root, "bus", "usb", "drivers", driver)
        os.makedirs(drivers, exist_ok=True)
        if acm:
            node = iface
        else:
            node = os.path.join(iface, name)
            os.makedirs(node, exist_ok=True)
        if not os.path.exists(os.path.join(node, "driver")):
            os.symlink(drivers, os.path.join(node, "driver"))
        tty = os.path.join(root, "class", "tty", name)
        os.makedirs(tty, exist_ok=True)
        if not os.path.lexists(os.path.join(tty, "device")):
            os.symlink(node, os.path.join(tty, "device"))

    device("ttyUSB0", "1-1", "10c4", "ea60", "CP2102N USB to UART Bridge Controller",
           "Silicon Labs", "a4b1c2", "cp210x", acm=False)
    device("ttyACM0", "1-2", "303a", "1001", "USB JTAG/serial debug unit", "Espressif",
           "DC:DA:0C:11:22:33", "cdc_acm", acm=True)
    return root


def build_demo(base: str):
    """A Studio with a demo project, simulated boards and fake voice/AI state."""
    from .studio import Studio
    from .toolchains.detect import Tool
    sysfs = make_sysfs(os.path.join(base, "sys"))
    devroot = os.path.join(base, "dev")
    os.makedirs(devroot, exist_ok=True)
    for name in ("ttyUSB0", "ttyACM0"):
        open(os.path.join(devroot, name), "w").close()
    studio = Studio(data_dir=os.path.join(base, "data"), sysfs=sysfs, devroot=devroot, voice=False)
    studio.settings.set("dev.projects_dir", os.path.join(base, "projects"), save=False)
    for tool_id, name, version in (("claude", "Claude Code", "2.1.332"),
                                   ("arduino-cli", "Arduino CLI", "1.6.0"),
                                   ("platformio", "PlatformIO", "6.2.0"),
                                   ("esptool", "esptool", "5.3.1"), ("git", "Git", "2.43.0")):
        studio.tools._tools[tool_id] = Tool(tool_id, name, f"/usr/bin/{tool_id}", version, "PATH")
    for tool_id, name in (("esp-idf", "ESP-IDF"), ("make", "Make"), ("cc", "C compiler"),
                          ("cppcheck", "Cppcheck")):
        studio.tools._tools[tool_id] = Tool(tool_id, name, problem="not installed")
    studio.voice = FakeVoice()
    studio.web = None
    if studio.ai is not None:
        studio.ai._auth = {"logged_in": True, "label": "Claude Pro", "method": "claude.ai",
                           "plan": "pro", "checked": time.time()}
    studio.devices.scan()
    studio.projects.create("ESP32 Robot", "esp32-s3", "platformio", "arduino", init_git=False)
    project = studio.projects.create("Smart Farm", "esp32-devkit", "arduino-cli", init_git=False)
    studio.projects.create("Spider Robot", "uno", "arduino-cli", init_git=False)
    studio.projects.set_current(studio.projects.find(
        [p.id for p in studio.projects.list() if p.name == "ESP32 Robot"][0]))
    del project
    return studio


def _fake_job(studio, kind: str, title: str, state: str, phase: str, progress=None,
              result=None, error: str = "", lines=None, project_id: str = ""):
    from .core.jobs import Job, JobSpec
    job = Job(f"demo-{kind}-{state}", JobSpec(kind=kind, title=title, func=lambda j: None,
                                              project_id=project_id),
              os.path.join(studio.paths.job_logs, "demo.log"))
    job.state = state
    job.phase = phase
    job.progress = progress
    job.started = time.time() - 42
    job.ended = time.time() if state != "running" else 0.0
    job.result = result or {}
    job.error = error
    for line in lines or []:
        job.lines.append(line)
    studio.jobs._jobs[job.id] = job
    return job


def scenes(studio, ui) -> List[Tuple[str, Callable[[], None]]]:
    from .ai.service import Turn
    from .lcd import screens as s
    project = studio.projects.current()
    voice = studio.voice
    ai = studio.ai

    def reset():
        ui.stack[1:] = []
        ui.armed = False
        voice.clear()
        ai.turn = None
        ai.project_ai(project.id).review = None
        ai.login_session = None

    def push(screen):
        ui.stack.append(screen)

    def turn(state, **extra):
        t = Turn(project, "Create an ESP32 project for controlling eight servos", "assisted",
                 "chat", "voice")
        t.state = state
        t.activities = [{"label": "Reading src/main.cpp", "error": False},
                        {"label": "Editing src/main.cpp", "error": False},
                        {"label": "Writing docs/WIRING.md", "error": False},
                        {"label": "Building the firmware", "error": False}]
        t.phase = "Building the firmware"
        t.started = time.time() - 74
        for key, value in extra.items():
            setattr(t, key, value)
        ai.turn = t
        return t

    out = []

    def add(name, setup):
        out.append((name, setup))

    add("01-home", lambda: reset())
    add("02-home-armed", lambda: (reset(), setattr(ui, "armed", True)))
    add("03-ai-ready", lambda: (reset(), push(s.AIScreen(ui))))
    add("04-ai-listening", lambda: (reset(), push(s.AIScreen(ui)), setattr(voice, "state", "listening")))
    add("05-ai-processing", lambda: (reset(), push(s.AIScreen(ui)), setattr(voice, "state", "processing")))
    add("06-ai-heard", lambda: (reset(), push(s.AIScreen(ui)), setattr(voice, "state", "heard"),
                                setattr(voice, "text", "Create an ESP32 project for controlling "
                                                       "eight servos with a PCA9685")))
    add("07-ai-working", lambda: (reset(), push(s.AIScreen(ui)), turn("working")))

    def review():
        reset()
        push(s.AIScreen(ui))
        t = turn("review")
        t.texts = ["Added PCA9685 servo control.\nSUMMARY: Added 8-servo control with PCA9685 on I2C."]
        ai.project_ai(project.id).review = {"turn": t.id, "mode": "ask",
                                            "paths": ["src/main.cpp", "platformio.ini",
                                                      "docs/WIRING.md"],
                                            "held": ["platformio.ini"],
                                            "summary": "Added 8-servo control with PCA9685 on I2C."}
    add("08-ai-review", review)

    def done():
        reset()
        push(s.AIScreen(ui))
        t = turn("done")
        t.texts = ["SUMMARY: Fixed the missing include; the build succeeds now."]
        t.applied = ["src/main.cpp"]
    add("09-ai-done", done)

    def failed():
        reset()
        push(s.AIScreen(ui))
        turn("error", error="Your Claude usage limit is reached for now. Try again later.")
    add("10-ai-failed", failed)
    add("11-ai-menu", lambda: (reset(), push(s.AIMenuScreen(ui))))
    add("12-ai-voice-error", lambda: (reset(), push(s.AIScreen(ui)), setattr(voice, "state", "error"),
                                      setattr(voice, "error", "I did not catch that. Hold the "
                                                              "button and speak again.")))

    def setup_signed_out():
        reset()
        ai._auth = {"logged_in": False, "label": "Not signed in", "checked": time.time()}
        push(s.ClaudeSetupScreen(ui))
    add("13-claude-signed-out", setup_signed_out)

    def login():
        reset()
        from .ai.claude import LoginSession
        session = LoginSession(["true"], {})
        session.state = "waiting"
        session.url = "https://claude.ai/oauth/authorize?z-vn_BVEXIc7Qe0Ssvu6j6aV4M4sofEB4mWverS5t3O0Y"
        ai.login_session = session
        push(s.LoginScreen(ui))
    add("14-claude-login-qr", login)

    def signed_in():
        reset()
        ai._auth = {"logged_in": True, "label": "Claude Pro", "checked": time.time()}
        push(s.ClaudeSetupScreen(ui))
    add("15-claude-signed-in", signed_in)
    add("16-projects", lambda: (reset(), push(s.ProjectsScreen(ui))))
    add("17-project", lambda: (reset(), push(s.ProjectScreen(ui, project.id))))
    add("18-project-more", lambda: (reset(), push(s.ProjectMoreScreen(ui, project.id))))
    add("19-new-platform", lambda: (reset(), push(s.NewPlatformScreen(ui))))
    add("20-new-board", lambda: (reset(), push(s.NewBoardScreen(ui, "esp32"))))
    add("21-new-framework", lambda: (reset(), push(s.NewFrameworkScreen(ui, "esp32-s3"))))
    add("22-new-name", lambda: (reset(), push(s.NewNameScreen(ui, "esp32-s3", "platformio",
                                                              "arduino"))))

    def creating():
        reset()
        screen = s.CreatingScreen(ui, "Servo Arm", "esp32-s3", "platformio", "arduino")
        screen.steps = [("Generating files", "ok"), ("Checking tools", "ok")]
        screen.project = project
        screen.done = True
        push(screen)
    add("23-new-created", creating)
    add("24-boards", lambda: (reset(), push(s.BoardsScreen(ui))))
    add("25-device", lambda: (reset(), push(s.DeviceScreen(ui, studio.devices.list()[0].port))))

    def build_running():
        reset()
        job = _fake_job(studio, "build", "Building ESP32 Robot", "running", "Compiling",
                        project_id=project.id)
        push(s.BuildScreen(ui, job.id, project.id, "build"))
    add("26-build-running", build_running)

    def build_ok():
        reset()
        job = _fake_job(studio, "build", "Building ESP32 Robot", "succeeded", "Done", 1.0,
                        {"report": {"size": {"flash": {"percent": 23.0}, "ram": {"percent": 6.0}}}},
                        project_id=project.id)
        push(s.BuildScreen(ui, job.id, project.id, "build"))
    add("27-build-ok", build_ok)

    def build_failed():
        reset()
        job = _fake_job(studio, "build", "Building ESP32 Robot", "failed", "Failed",
                        result={"report": {"headline": "Missing library: ServoDriver",
                                           "missing_headers": ["ServoDriver.h"]}},
                        error="Missing library: ServoDriver", project_id=project.id)
        push(s.BuildScreen(ui, job.id, project.id, "build"))
    add("28-build-failed", build_failed)

    def flash_confirm():
        reset()
        screen = s.FlashConfirmScreen(ui, project.id)
        from .hardware.upload import Check, UploadPlan
        screen.plan = UploadPlan("upload", project.id, project.name, project.board_name,
                                 "/dev/ttyACM0", "ESP32 with built-in USB", "firmware.bin",
                                 [Check("board", True, "Board: ESP32-S3", "info")], token="demo")
        push(screen)
    add("29-flash-confirm", flash_confirm)

    def flash_blocked():
        reset()
        screen = s.FlashConfirmScreen(ui, project.id)
        from .hardware.upload import Check, UploadPlan
        screen.plan = UploadPlan("upload", project.id, project.name, project.board_name, "", "",
                                 "", [Check("firmware", False, "Build the project first: there "
                                            "is no firmware yet.")])
        push(screen)
    add("30-flash-blocked", flash_blocked)

    def flashing():
        reset()
        job = _fake_job(studio, "upload", "Flashing ESP32 Robot", "running", "Writing firmware",
                        0.62, project_id=project.id)
        push(s.FlashScreen(ui, job.id, project.id))
    add("31-flash-running", flashing)

    def flashed():
        reset()
        job = _fake_job(studio, "upload", "Flashing ESP32 Robot", "succeeded", "Done", 1.0,
                        {"verified": True}, project_id=project.id)
        push(s.FlashScreen(ui, job.id, project.id))
    add("32-flash-ok", flashed)

    def serial():
        reset()
        from .hardware.monitor import Line
        monitor = studio.monitor
        monitor.connected = True
        monitor.port = "/dev/ttyACM0"
        monitor.baud = 115200
        monitor.lines.clear()
        for i, text in enumerate(["ESP-ROM:esp32s3-20210327", "rst:0x1 (POWERON),boot:0x8 "
                                  "(SPI_FAST_FLASH_BOOT)", "ESP32 Robot: ready",
                                  "PCA9685 found at 0x40", "servo 0 -> 90 deg", "heartbeat 1",
                                  "heartbeat 2", "servo 3 -> 45 deg", "heartbeat 3"]):
            monitor.lines.append(Line(time.time() - 10 + i, text, "rx", i + 1))
        push(s.SerialScreen(ui))
    add("33-serial", serial)

    def serial_idle():
        reset()
        studio.monitor.connected = False
        studio.monitor.lines.clear()
        push(s.SerialScreen(ui))
    add("34-serial-connect", serial_idle)
    add("35-settings", lambda: (reset(), push(s.SettingsScreen(ui))))
    add("36-tools", lambda: (reset(), push(s.ToolsScreen(ui))))
    add("37-desktop", lambda: (reset(), push(s.DesktopScreen(ui))))
    add("38-confirm-stop", lambda: (reset(), push(s.ConfirmScreen(
        ui, "AI Engineer", "Stop Claude?", "Changes made so far can be reviewed or undone.",
        "Stop", lambda: None, danger=True, cancel="Keep working"))))
    add("39-about", lambda: (reset(), push(s.ReaderScreen(ui, "About", s.about_text(studio)))))
    return out


def render_all(directory: str, base: str = "") -> List[str]:
    from .lcd.ui import DeviceUI
    own = not base
    base = base or tempfile.mkdtemp(prefix="mcode-preview-")
    os.makedirs(directory, exist_ok=True)
    studio = build_demo(base)
    ui = DeviceUI(studio, board=FakeBoard(), keyboard=False, threaded=False,
                  status=FakeStatusMonitor())
    written = []
    try:
        for name, setup in scenes(studio, ui):
            setup()
            image = ui.render()
            path = os.path.join(directory, f"{name}.png")
            image.save(path)
            written.append(path)
    finally:
        studio.stop(timeout=0.5)
        if own:
            shutil.rmtree(base, ignore_errors=True)
    return written


def contact_sheet(paths: List[str], target: str, columns: int = 8) -> str:
    from PIL import Image, ImageDraw
    images = [Image.open(p) for p in paths]
    w, h = images[0].size
    rows = (len(images) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * (w + 12) + 12, rows * (h + 30) + 12), (40, 44, 52))
    draw = ImageDraw.Draw(sheet)
    for i, (image, path) in enumerate(zip(images, paths)):
        x = 12 + (i % columns) * (w + 12)
        y = 12 + (i // columns) * (h + 30)
        sheet.paste(image, (x, y))
        draw.text((x, y + h + 4), os.path.splitext(os.path.basename(path))[0], fill=(200, 200, 200))
    sheet.save(target)
    return target


def main(directory: str) -> int:
    paths = render_all(directory)
    contact_sheet(paths, os.path.join(directory, "all-screens.png"))
    print(f"{len(paths)} screens written to {directory} (and all-screens.png)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "preview"))
