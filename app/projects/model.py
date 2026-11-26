"""A project: a folder of firmware source plus ``.mcode/project.json``.

The metadata lives inside the project so a project folder is self-describing
and portable (copy it to another device, open it, it still knows its board).
Projects created elsewhere (a PlatformIO folder, an Arduino sketch, an
ESP-IDF project, a Makefile tree) are recognised from their own files when
they have no metadata yet.
"""

from __future__ import annotations

import configparser
import glob
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from ..core import storage
from . import boards
from .boards import ARDUINO_CLI, ESP_IDF, MAKE, PLATFORMIO

META_DIR = ".mcode"
META_FILE = os.path.join(META_DIR, "project.json")
BUILD_DIR = os.path.join(META_DIR, "build")
# Folders that are build output or tool state, never source: skipped by
# backups, AI workspaces, duplicates and the file explorer.
GENERATED_DIRS = {".pio", ".mcode/build", "build", ".git", "managed_components", "__pycache__",
                  ".vscode", ".idea", ".cache", ".mcode/ai"}
SCHEMA = 1


class ProjectError(Exception):
    """A user-facing problem with a project (message is shown as is)."""


def slugify(name: str) -> str:
    """A folder/sketch name: letters, digits, dashes; starts with a letter or digit."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name.strip()).strip("-").lower()
    slug = re.sub(r"-{2,}", "-", slug)[:48].strip("-")
    return slug or "project"


def valid_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise ProjectError("Give the project a name.")
    if len(name) > 60:
        raise ProjectError("Project names are at most 60 characters.")
    if any(ch in name for ch in "/\\\0") or name in (".", ".."):
        raise ProjectError("A project name cannot contain / or \\.")
    return name


@dataclass
class Project:
    path: str
    name: str
    platform: str
    board_id: str
    toolchain: str
    framework: str = "arduino"
    language: str = "cpp"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    board_options: Dict[str, str] = field(default_factory=dict)
    pio_env: str = ""
    libraries: List[str] = field(default_factory=list)
    port: str = ""
    baud: int = 115200
    upload_speed: int = 0
    usb_cdc_on_boot: bool = False
    description: str = ""
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    detected: bool = False             # metadata guessed from the folder, not saved yet
    schema: int = SCHEMA

    # -------------------------------------------------------------- derived
    @property
    def board(self) -> Optional[boards.Board]:
        return boards.get(self.board_id)

    @property
    def board_name(self) -> str:
        board = self.board
        return board.name if board else (self.board_id or "Unknown board")

    @property
    def mcu(self) -> str:
        board = self.board
        return board.mcu if board else ""

    @property
    def slug(self) -> str:
        return os.path.basename(self.path.rstrip(os.sep))

    def fqbn(self) -> str:
        board = self.board
        if not board or not board.fqbn:
            return ""
        options = dict(self.board_options)
        if self.usb_cdc_on_boot and board.platform == "esp32" and board.native_usb:
            options.setdefault("CDCOnBoot", "cdc")
        if self.upload_speed and board.flasher == "esptool":
            options.setdefault("UploadSpeed", str(self.upload_speed))
        if not options:
            return board.fqbn
        base = board.fqbn
        existing = {}
        parts = base.split(":")
        if len(parts) > 3:
            existing = dict(item.split("=", 1) for item in parts[3].split(",") if "=" in item)
            base = ":".join(parts[:3])
        existing.update(options)
        return base + ":" + ",".join(f"{k}={v}" for k, v in existing.items())

    def sketch_file(self) -> str:
        return os.path.join(self.path, self.slug + ".ino")

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("detected", None)
        return data

    def summary(self) -> dict:
        board = self.board
        return {"id": self.id, "name": self.name, "path": self.path, "platform": self.platform,
                "platform_name": boards.PLATFORMS.get(self.platform, self.platform),
                "board_id": self.board_id, "board_name": self.board_name, "mcu": self.mcu,
                "toolchain": self.toolchain,
                "toolchain_name": boards.TOOLCHAIN_NAMES.get(self.toolchain, self.toolchain),
                "framework": self.framework, "language": self.language,
                "fqbn": self.fqbn(), "pio_env": self.pio_env,
                "pio_board": board.pio_board if board else "",
                "idf_target": board.idf_target if board else "",
                "libraries": list(self.libraries), "port": self.port, "baud": self.baud,
                "upload_speed": self.upload_speed, "usb_cdc_on_boot": self.usb_cdc_on_boot,
                "description": self.description, "updated": self.updated,
                "detected": self.detected}


def save(project: Project) -> None:
    project.updated = time.time()
    project.detected = False
    os.makedirs(os.path.join(project.path, META_DIR), exist_ok=True)
    data = project.to_dict()
    data.pop("path", None)
    storage.atomic_write_json(os.path.join(project.path, META_FILE), data, mode=0o644)
    ignore = os.path.join(project.path, META_DIR, ".gitignore")
    if not os.path.exists(ignore):
        storage.atomic_write_text(ignore, "build/\nai/\n", mode=0o644)


def load(path: str) -> Project:
    """The project in ``path``: its metadata, or what its files say it is."""
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise ProjectError(f"No folder at {path}.")
    meta = os.path.join(path, META_FILE)
    if os.path.exists(meta):
        data = storage.read_json(meta, None, dict)
        if data is not None:
            known = {f for f in Project.__dataclass_fields__}  # type: ignore[attr-defined]
            values = {k: v for k, v in data.items() if k in known and k != "path"}
            try:
                project = Project(path=path, **values)
            except TypeError as exc:
                raise ProjectError(f"{META_FILE} is not valid: {exc}") from None
            return project
    guessed = detect(path)
    if guessed is None:
        raise ProjectError("This folder is not a project mCode recognises (no platformio.ini, "
                           ".ino sketch, ESP-IDF CMakeLists.txt or Makefile).")
    return guessed


# ---------------------------------------------------------------- detection

def _pio_envs(ini_path: str) -> List[Dict[str, str]]:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read(ini_path, encoding="utf-8")
    except (configparser.Error, OSError):
        return []
    envs = []
    default = ""
    if parser.has_section("platformio"):
        default = (parser.get("platformio", "default_envs", fallback="") or "").split(",")[0].strip()
    for section in parser.sections():
        if section.startswith("env:"):
            values = dict(parser.items(section))
            values["name"] = section[4:]
            values["default"] = "1" if section[4:] == default else ""
            envs.append(values)
    envs.sort(key=lambda e: (not e["default"], e.get("platform") == "native"))
    return envs


def detect(path: str) -> Optional[Project]:
    name = os.path.basename(path.rstrip(os.sep))
    ini = os.path.join(path, "platformio.ini")
    if os.path.exists(ini):
        envs = [e for e in _pio_envs(ini) if e.get("platform") != "native"]
        env = envs[0] if envs else {}
        board = boards.find_by_pio(env.get("board", ""))
        framework = (env.get("framework") or "arduino").split(",")[0].strip()
        return Project(path=path, name=name, platform=board.platform if board else "esp32",
                       board_id=board.id if board else env.get("board", ""),
                       toolchain=PLATFORMIO, framework=framework,
                       pio_env=env.get("name", ""), detected=True)
    cmake = os.path.join(path, "CMakeLists.txt")
    if os.path.exists(cmake):
        try:
            with open(cmake, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            text = ""
        if "project.cmake" in text and "IDF_PATH" in text:
            target = ""
            sdkconfig = os.path.join(path, "sdkconfig")
            if os.path.exists(sdkconfig):
                with open(sdkconfig, encoding="utf-8", errors="replace") as handle:
                    match = re.search(r'^CONFIG_IDF_TARGET="([a-z0-9]+)"', handle.read(), re.M)
                    target = match.group(1) if match else ""
            board = boards.find_by_idf_target(target or "esp32")
            return Project(path=path, name=name, platform="esp32",
                           board_id=board.id if board else "esp32-devkit", toolchain=ESP_IDF,
                           framework="espidf", language="c", detected=True)
    sketch = os.path.join(path, name + ".ino")
    inos = glob.glob(os.path.join(path, "*.ino"))
    if os.path.exists(sketch) or inos:
        fqbn = ""
        yaml_path = os.path.join(path, "sketch.yaml")
        if os.path.exists(yaml_path):
            with open(yaml_path, encoding="utf-8", errors="replace") as handle:
                match = re.search(r"^default_fqbn:\s*([^\s#]+)", handle.read(), re.M)
                fqbn = match.group(1) if match else ""
        board = boards.find_by_fqbn(fqbn) if fqbn else None
        return Project(path=path, name=name, platform=board.platform if board else "arduino",
                       board_id=board.id if board else "uno", toolchain=ARDUINO_CLI,
                       framework="arduino", detected=True)
    if os.path.exists(os.path.join(path, "Makefile")):
        return Project(path=path, name=name, platform="generic", board_id="generic-host",
                       toolchain=MAKE, framework="none", detected=True)
    return None
