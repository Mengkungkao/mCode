"""Board settings of a project, like the Arduino IDE's Tools menu.

What can be set comes from the tools themselves, never invented:

* **Board**: the catalogue boards the project's toolchain and framework
  support (projects.boards); an ESP32 that esptool identified on a connected
  port is offered first.
* **USB CDC on boot** (ESP32 with built-in USB, Arduino framework) and
  **Upload speed** (esptool boards): the project's own fields, which every
  toolchain uses (Arduino CLI options, platformio.ini, idf.py -b).
* **The board's menus** (Arduino CLI): ``arduino-cli board details --fqbn F
  --format json`` from the installed core: CPU frequency, flash size,
  partition scheme, debug level... Stored as ``board_options`` and added to
  the FQBN. "Erase All Flash Before Sketch Upload" is not offered: erasing is
  mCode's separate, confirmed action (docs/HARDWARE.md).

Changes go through ProjectManager.update, which also edits sketch.yaml and
platformio.ini. Firmware built before a change no longer counts as current
(hardware.upload: built_for).
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from ..hardware import usbids
from ..projects import boards, buildfiles
from ..projects.model import Project, ProjectError
from . import detect

log = logging.getLogger("mcode.boardconfig")

HANDLED = {"CDCOnBoot", "UploadSpeed"}           # the project's own fields
HIDDEN = {"EraseFlash"}                          # erasing needs mCode's confirmed action


@dataclass
class Choice:
    value: str
    label: str


@dataclass
class Setting:
    id: str                       # "board" | "usb_cdc_on_boot" | "upload_speed" | "menu:<Option>"
    label: str
    value: str
    choices: List[Choice] = field(default_factory=list)
    kind: str = "choice"          # choice | toggle
    note: str = ""

    def value_label(self) -> str:
        if self.kind == "toggle":
            return "On" if self.value == "1" else "Off"
        return next((c.label for c in self.choices if c.value == self.value), self.value)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["value_label"] = self.value_label()
        return data


class BoardConfig:
    def __init__(self, studio):
        self.studio = studio
        self._menus: Dict[str, List[dict]] = {}
        self._lock = threading.Lock()

    # ----------------------------------------------------------- choices
    def connected_board(self, project: Optional[Project] = None) -> Optional[boards.Board]:
        """The catalogue board of the ESP32 esptool identified on the project's
        port (or the only identified one)."""
        devices = self.studio.devices
        if devices is None:
            return None
        chosen = devices.selected(project)
        candidates = [chosen] if chosen is not None else devices.list()
        found = [boards.for_esp_family(usbids.chip_family(d.chip)) for d in candidates if d.chip]
        found = [b for b in found if b is not None]
        return found[0] if len(found) == 1 or chosen is not None and found else None

    def board_choices(self, project: Project) -> List[Choice]:
        connected = self.connected_board(project)
        out = []
        for board in boards.BOARDS:
            if project.toolchain not in board.toolchains or \
                    project.framework not in board.frameworks(project.toolchain):
                continue
            label = board.name + (" (connected)" if connected and board.id == connected.id
                                  else "")
            out.append(Choice(board.id, label))
        if connected:
            out.sort(key=lambda c: c.value != connected.id)
        return out

    def settings(self, project: Project, menus: bool = True) -> List[Setting]:
        board = project.board
        out = [Setting("board", "Board", project.board_id, self.board_choices(project),
                       note=board.notes if board else "")]
        if board is None:
            return out
        if board.platform == "esp32" and board.native_usb and project.framework == "arduino" \
                and project.toolchain in (boards.ARDUINO_CLI, boards.PLATFORMIO):
            out.append(Setting("usb_cdc_on_boot", "USB CDC on boot",
                               "1" if project.usb_cdc_on_boot else "0", kind="toggle",
                               note="On: Serial prints on the chip's own USB port "
                                    "(USB-Serial/JTAG). Off: Serial uses the UART pins / "
                                    "the board's USB-UART chip."))
        if board.flasher == "esptool" and board.upload_speeds:
            speeds = [Choice("0", f"Default ({board.default_upload_speed})")]
            speeds += [Choice(str(s), str(s)) for s in board.upload_speeds]
            out.append(Setting("upload_speed", "Upload speed", str(project.upload_speed or 0),
                               speeds, note="Lower it if uploads fail with long cables."))
        if project.toolchain == boards.ESP_IDF:
            minimal = buildfiles.minimal_build(project.path)
            if minimal is not None:
                out.append(Setting("fast_build", "Fast build", "1" if minimal else "0",
                                   kind="toggle",
                                   note="On: build only main and the components it needs, as "
                                        "Espressif's examples do (about half the work). If a "
                                        "header such as esp_wifi.h is not found, add the "
                                        "component to REQUIRES in main/CMakeLists.txt, or "
                                        "turn this off."))
        if menus and project.toolchain == boards.ARDUINO_CLI:
            for menu in self.arduino_menus(project):
                option = menu["option"]
                values = [Choice(v["value"], v.get("value_label") or v["value"])
                          for v in menu.get("values", [])]
                default = next((v["value"] for v in menu.get("values", []) if v.get("selected")),
                               values[0].value if values else "")
                out.append(Setting(f"menu:{option}", menu.get("option_label") or option,
                                   project.board_options.get(option, default), values))
        return out

    # -------------------------------------------------- Arduino core menus
    def arduino_menus(self, project: Project) -> List[dict]:
        """The installed core's menus for the project's board (cached per board)."""
        board = project.board
        if board is None or not board.fqbn:
            return []
        base = ":".join(board.fqbn.split(":")[:3])
        with self._lock:
            if base in self._menus:
                return self._menus[base]
        tool = self.studio.tools.get(detect.ARDUINO_CLI)
        if not tool.found:
            return []
        try:
            result = subprocess.run([tool.path, "board", "details", "--fqbn", base, "--format",
                                     "json"], capture_output=True, text=True, timeout=60,
                                    stdin=subprocess.DEVNULL)
            data = json.loads(result.stdout or "{}") if result.returncode == 0 else {}
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            log.warning("board details for %s failed: %s", base, exc)
            return []
        menus = [m for m in data.get("config_options", []) or []
                 if m.get("option") and m["option"] not in HANDLED | HIDDEN]
        if result.returncode == 0:            # a missing core is not cached: install it later
            with self._lock:
                self._menus[base] = menus
        return menus

    # --------------------------------------------------------------- apply
    def apply(self, project: Project, setting_id: str, value: str) -> Project:
        """Change one setting after checking the value is one the tools offer."""
        value = str(value)
        current = {s.id: s for s in self.settings(project)}
        setting = current.get(setting_id)
        if setting is None:
            raise ProjectError(f"{project.board_name} has no setting {setting_id!r} here.")
        if setting.kind == "toggle":
            if value not in ("0", "1"):
                raise ProjectError("Choose on or off.")
        elif value not in {c.value for c in setting.choices}:
            raise ProjectError(f"{value!r} is not a choice for {setting.label}.")
        projects = self.studio.projects
        if setting_id == "board":
            return projects.update(project, board_id=value)
        if setting_id == "usb_cdc_on_boot":
            return projects.update(project, usb_cdc_on_boot=value == "1")
        if setting_id == "upload_speed":
            return projects.update(project, upload_speed=int(value))
        if setting_id == "fast_build":
            if buildfiles.set_minimal_build(project.path, value == "1"):
                log.info("%s: fast build %s", project.name, "on" if value == "1" else "off")
                projects.publish("projects", {"updated": project.summary()})
            return project
        option = setting_id.split(":", 1)[1]
        options = dict(project.board_options)
        default = next((m for m in self.arduino_menus(project) if m["option"] == option), {})
        default_value = next((v["value"] for v in default.get("values", []) if v.get("selected")),
                             None)
        if value == default_value:
            options.pop(option, None)          # keep the FQBN short: defaults are implied
        else:
            options[option] = value
        return projects.update(project, board_options=options)
