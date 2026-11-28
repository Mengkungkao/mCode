"""Settings: one documented schema, validation, defaults and safe storage.

Every setting mCode reads is declared in ``FIELDS`` (key, type, default,
allowed values, label, help). ``settings.json`` in the data folder holds the
saved values. On load:

* a missing file means defaults;
* a malformed file is kept as ``settings.json.corrupt-<time>`` and defaults
  are used (the user's file is never silently overwritten);
* a value of the wrong type or outside its range falls back to the default
  for that key only, with a warning;
* unknown keys are kept (a newer mCode may have written them) but ignored.

The canonical description is docs/CONFIGURATION.md.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import storage

log = logging.getLogger("mcode.config")

BAUD_RATES = (300, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 74880, 115200, 230400, 460800,
              921600, 1500000, 2000000)
UPLOAD_SPEEDS = (0, 57600, 115200, 230400, 460800, 921600, 1500000, 2000000)


@dataclass(frozen=True)
class Field:
    key: str
    kind: str                       # bool | int | str | path | choice
    default: Any
    label: str
    help: str = ""
    choices: Tuple = ()
    choice_labels: Dict[str, str] = field(default_factory=dict)
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    lcd: bool = False               # offered on the small screen (simple settings)
    secret: bool = False
    admin: bool = False             # only browsers with full access may change it

    @property
    def needs_admin(self) -> bool:
        """Programs and folders mCode uses, network and device settings."""
        return self.admin or self.kind == "path"

    @property
    def section(self) -> str:
        return self.key.split(".", 1)[0]

    def label_of(self, value: Any) -> str:
        if self.kind == "bool":
            return "On" if value else "Off"
        if self.choice_labels and str(value) in self.choice_labels:
            return self.choice_labels[str(value)]
        if self.key in ("hw.upload_speed",) and value == 0:
            return "Board default"
        if self.kind == "int" and self.key == "dev.build_jobs" and value == 0:
            return "Automatic"
        return str(value) if value not in ("", None) else "Automatic"

    def validate(self, value: Any) -> Any:
        """The value in canonical form; raises ValueError when it is not allowed."""
        if self.kind == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.lower() in ("true", "false", "on", "off", "1", "0"):
                return value.lower() in ("true", "on", "1")
            raise ValueError(f"{self.key}: expected on/off")
        if self.kind == "int":
            if isinstance(value, bool):
                raise ValueError(f"{self.key}: expected a number")
            try:
                number = int(value)
            except (TypeError, ValueError):
                raise ValueError(f"{self.key}: expected a number") from None
            if self.choices and number not in self.choices:
                raise ValueError(f"{self.key}: {number} is not one of {list(self.choices)}")
            if self.minimum is not None and number < self.minimum:
                raise ValueError(f"{self.key}: at least {self.minimum}")
            if self.maximum is not None and number > self.maximum:
                raise ValueError(f"{self.key}: at most {self.maximum}")
            return number
        if self.kind == "choice":
            if value not in self.choices:
                raise ValueError(f"{self.key}: {value!r} is not one of {list(self.choices)}")
            return value
        if self.kind in ("str", "path"):
            if not isinstance(value, str):
                raise ValueError(f"{self.key}: expected text")
            if len(value) > 1024 or "\0" in value or "\n" in value:
                raise ValueError(f"{self.key}: text too long or not one line")
            return value.strip()
        raise ValueError(f"{self.key}: unknown kind {self.kind}")


AI_MODES = {"ask": "Ask before changes", "assisted": "Assisted development",
            "advanced": "Advanced development"}

FIELDS: List[Field] = [
    # ------------------------------------------------------------------ AI
    Field("ai.mode", "choice", "ask", "AI mode",
          "Ask: Claude proposes changes and you approve them. Assisted: Claude edits files and "
          "runs builds and tests; tests that run on this device (PlatformIO native, make test) "
          "run code Claude wrote, as your user. Advanced: also git through mCode. Flashing, "
          "erasing and installing always need your confirmation.",
          choices=tuple(AI_MODES), choice_labels=AI_MODES, lcd=True),
    Field("ai.claude_path", "path", "", "Claude Code program",
          "Empty: found automatically (PATH, ~/.local/bin/claude)."),
    Field("ai.model", "str", "", "Claude model", "Empty: Claude Code's default model."),
    Field("ai.voice_autosend", "bool", False, "Send voice requests at once",
          "Off: mCode shows what it heard and waits for you to send it.", lcd=True),
    Field("ai.turn_timeout_s", "int", 1200, "AI task time limit (seconds)", minimum=60,
          maximum=14400),
    Field("ai.hold_build_scripts", "bool", True, "Hold Claude's build-script changes",
          "On: changes Claude makes to CMakeLists.txt, Makefiles, *.cmake, *.mk and Python or "
          "shell scripts wait for your OK, and Claude cannot build until you decide. These files "
          "run programs on this device when the project is built. Off: they apply like other "
          "source files (Assisted and Advanced modes).", admin=True),
    Field("ai.allow_web", "bool", False, "Let Claude search the web (Advanced mode)",
          "Datasheets and library documentation; read-only.", admin=True),
    Field("ai.login_code_page", "bool", True, "Paste the sign-in code from a phone",
          "While signing in to Claude waits for its code, mCode serves one page on the local "
          "network (second QR code) where a phone can paste it: one-time link, one code, "
          "closed when the sign-in stops waiting. Off: type the code on a keyboard or paste it "
          "in the desktop IDE.", lcd=True, admin=True),
    # ---------------------------------------------------------- development
    Field("dev.projects_dir", "path", "~/mcode-projects", "Project folder",
          "Where new projects are created. Projects are never deleted with the app."),
    Field("dev.arduino_cli", "path", "", "Arduino CLI program", "Empty: found automatically."),
    Field("dev.platformio", "path", "", "PlatformIO program", "Empty: found automatically."),
    Field("dev.idf_path", "path", "", "ESP-IDF folder",
          "Empty: IDF_PATH, ~/esp/esp-idf or the newest ~/esp/v*/esp-idf. Installing ESP-IDF "
          "from Development tools sets it."),
    Field("dev.esptool", "path", "", "esptool program", "Empty: found automatically."),
    Field("dev.python", "path", "", "Python for tools",
          "Python used to create mCode's tool environment (PlatformIO, esptool)."),
    Field("dev.build_jobs", "int", 0, "Compiler jobs",
          "0: automatic: one per CPU core while about 160 MB of free memory is left per job "
          "(3 on a 1 GB Orange Pi Zero 2W with mCode open, 1 on a 512 MB Pi Zero 2 W).",
          minimum=0, maximum=64),
    Field("dev.build_timeout_s", "int", 2400, "Build time limit (seconds)", minimum=60,
          maximum=14400),
    Field("dev.upload_timeout_s", "int", 300, "Upload time limit (seconds)", minimum=30,
          maximum=3600),
    Field("dev.warnings", "choice", "default", "Compiler warnings",
          choices=("none", "default", "more", "all")),
    # -------------------------------------------------------------- hardware
    Field("hw.default_port", "str", "", "Default serial port", "Empty: the detected board."),
    Field("hw.default_baud", "int", 115200, "Default baud rate", choices=BAUD_RATES, lcd=True),
    Field("hw.upload_speed", "int", 0, "Upload speed", "0: the board's default.",
          choices=UPLOAD_SPEEDS),
    Field("hw.verify_upload", "bool", True, "Verify after upload (where supported)"),
    # ------------------------------------------------------------- interface
    Field("ui.theme", "choice", "dark", "Theme",
          "Automatic follows the browser's or the phone's light/dark setting.",
          choices=("dark", "light", "auto"),
          choice_labels={"dark": "Dark", "light": "Light", "auto": "Automatic"}),
    Field("ui.font_size", "int", 14, "Editor font size", minimum=9, maximum=28),
    Field("ui.layout", "choice", "ai-right", "Editor layout",
          choices=("ai-right", "ai-left", "ai-bottom"),
          choice_labels={"ai-right": "AI on the right", "ai-left": "AI on the left",
                         "ai-bottom": "AI in the bottom panel"}),
    Field("ui.terminal_scrollback", "int", 5000, "Terminal scrollback lines", minimum=500,
          maximum=100000),
    Field("ui.display_mode", "choice", "auto", "Display mode",
          "Auto: the LCD under mFruit OS, the desktop IDE elsewhere.",
          choices=("auto", "lcd", "desktop"),
          choice_labels={"auto": "Automatic", "lcd": "LCD only", "desktop": "Desktop only"},
          admin=True),
    Field("ui.desktop_ide", "bool", True, "Desktop IDE",
          "Serve the full IDE to a browser on this device.", lcd=True, admin=True),
    Field("ui.open_browser", "bool", True, "Open the IDE on the HDMI desktop",
          "When a desktop session is running on this device.", admin=True),
    Field("web.port", "int", 8765, "Desktop IDE port", minimum=1024, maximum=65535, admin=True),
    Field("web.lan", "bool", False, "Allow computers on the network",
          "Off: only this device's own browser. On: computers and phones on the network can "
          "pair while a pairing screen is open (LCD: Desktop IDE › Pair a computer).", lcd=True,
          admin=True),
    Field("web.pair_role", "choice", "developer", "Access for new pairings",
          "What a newly paired browser may do. Developer: projects, builds, flashing (with "
          "confirmation) and the AI. Full access: also the terminal, folders outside the "
          "project folder, tools and device settings. The LCD's pairing screen can switch it "
          "for one pairing.",
          choices=("viewer", "developer", "admin"),
          choice_labels={"viewer": "Viewer (look only)", "developer": "Developer",
                         "admin": "Full access"}, admin=True),
    Field("web.session_days", "int", 30, "Keep paired browsers (days)",
          "A browser not used for this many days must pair again.", minimum=1, maximum=365,
          admin=True),
    Field("web.extra_hosts", "str", "", "Other names of this device",
          "Comma-separated host names a browser may use for mCode, e.g. the name of an HTTPS "
          "tunnel or reverse proxy (mydevice.tailnet.ts.net). Requests for any other name are "
          "refused (DNS rebinding).", admin=True),
    # ----------------------------------------------------------------- voice
    Field("voice.stt", "choice", "auto", "Speech recognition",
          "Auto: OpenAI when a key is set, else your command, else off.",
          choices=("auto", "openai", "command", "off"),
          choice_labels={"auto": "Automatic", "openai": "OpenAI", "command": "Local command",
                         "off": "Off"}),
    Field("voice.stt_command", "str", "", "Speech command",
          "A local recogniser: {wav} is replaced by the recording; it prints the text.",
          admin=True),
    Field("voice.stt_model", "str", "gpt-4o-mini-transcribe", "OpenAI speech model"),
    Field("voice.language", "str", "en", "Speech language", "ISO code, e.g. en, de, th."),
    Field("voice.feedback", "choice", "spoken", "Sound feedback",
          choices=("spoken", "tones", "off"),
          choice_labels={"spoken": "Spoken", "tones": "Tones only", "off": "Off"}, lcd=True),
    Field("voice.tts", "choice", "auto", "Voice for spoken feedback",
          choices=("auto", "openai", "espeak", "off"),
          choice_labels={"auto": "Automatic", "openai": "OpenAI", "espeak": "eSpeak",
                         "off": "Off"}),
    Field("voice.capture_device", "str", "auto", "Microphone", "auto or an ALSA device name.",
          admin=True),
    Field("voice.playback_device", "str", "auto", "Speaker", "auto or an ALSA device name.",
          admin=True),
    # --------------------------------------------------------------- storage
    Field("storage.backups_keep", "int", 10, "Backups kept per project", minimum=1, maximum=200),
    Field("storage.log_days", "int", 30, "Keep logs (days)", minimum=1, maximum=3650),
]

FIELD_BY_KEY: Dict[str, Field] = {f.key: f for f in FIELDS}
SECTIONS = {"ai": "AI", "dev": "Development", "hw": "Hardware", "ui": "Interface",
            "web": "Interface", "voice": "Voice", "storage": "Storage"}


class Settings:
    """Validated settings backed by a JSON file. Thread-safe."""

    def __init__(self, path: Optional[str], on_change: Optional[Callable[[str, Any], None]] = None):
        self.path = path
        self.on_change = on_change
        self._lock = threading.RLock()
        self._extra: Dict[str, Any] = {}
        self._values: Dict[str, Any] = {f.key: f.default for f in FIELDS}
        self.problems: List[str] = []
        if path:
            self._load()

    def _load(self) -> None:
        raw = storage.read_json(self.path, {}, dict)
        for key, value in raw.items():
            spec = FIELD_BY_KEY.get(key)
            if spec is None:
                self._extra[key] = value
                continue
            try:
                self._values[key] = spec.validate(value)
            except ValueError as exc:
                self.problems.append(str(exc))
                log.warning("settings: %s; using the default %r", exc, spec.default)

    def get(self, key: str) -> Any:
        with self._lock:
            if key not in self._values:
                raise KeyError(key)
            return self._values[key]

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def set(self, key: str, value: Any, save: bool = True) -> Any:
        spec = FIELD_BY_KEY.get(key)
        if spec is None:
            raise KeyError(key)
        value = spec.validate(value)
        with self._lock:
            changed = self._values.get(key) != value
            self._values[key] = value
            if save and changed:
                self.save()
        if changed and self.on_change is not None:
            self.on_change(key, value)
        return value

    def update(self, values: Dict[str, Any]) -> Dict[str, str]:
        """Set several values; returns {key: error} for the ones refused."""
        errors = {}
        for key, value in values.items():
            try:
                self.set(key, value, save=False)
            except (KeyError, ValueError) as exc:
                errors[key] = str(exc)
        self.save()
        return errors

    def reset(self, key: str) -> Any:
        return self.set(key, FIELD_BY_KEY[key].default)

    def save(self) -> None:
        if not self.path:
            return
        with self._lock:
            data = dict(self._extra)
            data.update(self._values)
            storage.atomic_write_json(self.path, data)

    def as_dict(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._values)

    def describe(self) -> list:
        """The schema with current values, for the settings screens."""
        values = self.as_dict()
        out = []
        for spec in FIELDS:
            out.append({"key": spec.key, "kind": spec.kind, "label": spec.label, "help": spec.help,
                        "section": SECTIONS.get(spec.section, spec.section),
                        "choices": list(spec.choices), "choice_labels": dict(spec.choice_labels),
                        "min": spec.minimum, "max": spec.maximum, "default": spec.default,
                        "value": values[spec.key], "shown": spec.label_of(values[spec.key]),
                        "admin": spec.needs_admin})
        return out

    def cycle(self, key: str, step: int = 1) -> Any:
        """Next allowed value (for one-button screens): booleans toggle, choices rotate."""
        spec = FIELD_BY_KEY[key]
        current = self.get(key)
        if spec.kind == "bool":
            return self.set(key, not current)
        if spec.choices:
            options = list(spec.choices)
            index = options.index(current) if current in options else 0
            return self.set(key, options[(index + step) % len(options)])
        raise ValueError(f"{key} cannot be changed with the button")
