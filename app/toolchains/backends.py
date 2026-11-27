"""The real tools behind Build, Clean, Rebuild, Test, Check, Upload and Erase.

Each backend turns a project and an action into the exact commands its
official tool documents, plus where the firmware ends up:

=============  ==========================================================
Arduino CLI    ``arduino-cli compile --fqbn F --build-path .mcode/build``;
               ``arduino-cli upload -p PORT --fqbn F --input-dir ...``
PlatformIO     ``pio run -e ENV``; ``pio run -t nobuild -t upload
               --upload-port PORT``; ``pio test -e native``; ``pio check``
ESP-IDF        ``idf.py set-target T`` (first build), ``idf.py build``; flash
               with esptool and the build's ``flash_args`` (no rebuild)
esptool        chip detection, erase (always confirmed), verify
Make           ``make``, ``make clean``, ``make test``
=============  ==========================================================

Uploads always use the firmware that was built and checked, never a fresh
build: what you confirmed is what gets written.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..projects.boards import ARDUINO_CLI, ESP_IDF, MAKE, PLATFORMIO
from ..projects.files import walk_sources
from ..projects.model import BUILD_DIR, Project, _pio_envs
from . import detect

ESP32_INDEX = "https://espressif.github.io/arduino-esp32/package_esp32_index.json"
CORE_INDEX_URLS = {"esp32:esp32": ESP32_INDEX}
CORE_NAMES = {"esp32:esp32": "ESP32 core (Espressif)", "arduino:avr": "Arduino AVR core",
              "arduino:sam": "Arduino SAM core (Due)", "arduino:esp32": "Arduino ESP32 core",
              "arduino:mbed_rp2040": "Arduino Mbed RP2040 core"}


INTERRUPTED = ".mcode-interrupted"


def interrupted_marker(build_dir: str) -> str:
    """Written into a build folder when a build there was cancelled or timed out."""
    return os.path.join(build_dir, INTERRUPTED)


class BackendError(Exception):
    """A user-facing reason an action cannot run; ``fix`` names an action that helps."""

    def __init__(self, message: str, fix: str = "", fix_label: str = ""):
        super().__init__(message)
        self.fix = fix
        self.fix_label = fix_label


@dataclass
class Plan:
    what: str                                   # "Building ESP32 Robot"
    steps: List[List[str]] = field(default_factory=list)
    cwd: str = ""
    env: Dict[str, str] = field(default_factory=dict)
    timeout: float = 1800.0
    remove: List[str] = field(default_factory=list)   # folders to delete (clean)
    labels: List[str] = field(default_factory=list)   # per-step description


def build_jobs(settings, machine: Optional[dict] = None) -> int:
    value = int(settings.get("dev.build_jobs") or 0)
    if value:
        return value
    info = machine or detect.machine()
    cpus = int(info.get("cpus") or 1)
    memory = int(info.get("memory_mb") or 0)
    if memory and memory < 1100:
        return 1
    if memory and memory < 2200:
        return min(2, cpus)
    return max(1, cpus)


def newest_source_mtime(project: Project) -> float:
    newest = 0.0
    for rel in walk_sources(project.path):
        try:
            newest = max(newest, os.path.getmtime(os.path.join(project.path, rel)))
        except OSError:
            continue
    return newest


class Backend:
    toolchain = ""
    tool_id = ""

    def __init__(self, tools: detect.Toolchains, settings):
        self.tools = tools
        self.settings = settings

    # ------------------------------------------------------------- tool path
    def program(self) -> str:
        tool = self.tools.get(self.tool_id)
        if not tool.found:
            raise BackendError(f"{detect.NAMES[self.tool_id]} is not installed. "
                               f"{detect.HINTS.get(self.tool_id, '')}",
                               fix=f"install:{self.tool_id}" if self.tool_id in detect.INSTALLABLE
                               else "", fix_label=f"Install {detect.NAMES[self.tool_id]}")
        return tool.path

    def timeout(self) -> float:
        return float(self.settings.get("dev.build_timeout_s"))

    def upload_timeout(self) -> float:
        return float(self.settings.get("dev.upload_timeout_s"))

    # ------------------------------------------------------------- interface
    def build(self, project: Project, clean: bool = False) -> Plan:
        raise NotImplementedError

    def clean(self, project: Project) -> Plan:
        raise NotImplementedError

    def test(self, project: Project) -> Plan:
        raise BackendError("This project type has no tests mCode can run.")

    def check(self, project: Project) -> Plan:
        raise BackendError("Static analysis is not available for this project type.")

    def upload(self, project: Project, port: str, speed: int = 0, verify: bool = True) -> Plan:
        raise BackendError("mCode cannot upload this kind of project.")

    def artifact(self, project: Project) -> str:
        return ""

    def requirements(self, project: Project) -> List[dict]:
        """Things missing before a build can work: [{"id", "label", "fix"}]."""
        return []

    def up_to_date(self, project: Project) -> bool:
        firmware = self.artifact(project)
        if not firmware or not os.path.exists(firmware):
            return False
        return os.path.getmtime(firmware) >= newest_source_mtime(project) - 1.0


# ===================================================================== Arduino

class ArduinoCliBackend(Backend):
    toolchain = ARDUINO_CLI
    tool_id = detect.ARDUINO_CLI

    def build_dir(self, project: Project) -> str:
        return os.path.join(project.path, BUILD_DIR)

    def _fqbn(self, project: Project) -> str:
        fqbn = project.fqbn()
        if not fqbn:
            raise BackendError(f"{project.board_name} has no Arduino CLI board definition.")
        return fqbn

    def core_id(self, project: Project) -> str:
        board = project.board
        return board.core if board else ":".join(self._fqbn(project).split(":")[:2])

    def _urls(self, core: str) -> List[str]:
        url = CORE_INDEX_URLS.get(core)
        return ["--additional-urls", url] if url else []

    def build(self, project: Project, clean: bool = False) -> Plan:
        cli = self.program()
        # A build that was cancelled or timed out can leave a half-written
        # core archive (core/core.a) that the Arduino CLI then reuses: every
        # later build fails with "undefined reference to millis/app_main"
        # (seen on the .130 board, ESP32-C5, 2026-11-27). Start clean once.
        clean = clean or os.path.exists(interrupted_marker(self.build_dir(project)))
        argv = [cli, "compile", "--fqbn", self._fqbn(project), "--build-path",
                self.build_dir(project), "--warnings", self.settings.get("dev.warnings"),
                "--jobs", str(build_jobs(self.settings))]
        if clean:
            argv.append("--clean")
        argv += self._urls(self.core_id(project)) + [project.path]
        return Plan(f"{'Rebuilding' if clean else 'Building'} {project.name}", [argv],
                    project.path, timeout=self.timeout(),
                    labels=["Compiling with the Arduino CLI"])

    def clean(self, project: Project) -> Plan:
        return Plan(f"Cleaning {project.name}", [], project.path,
                    remove=[self.build_dir(project), os.path.join(project.path, ".mcode", "build-test")])

    def test(self, project: Project) -> Plan:
        cli = self.program()
        steps = [[cli, "compile", "--fqbn", self._fqbn(project), "--build-path",
                  os.path.join(project.path, ".mcode", "build-test"), "--warnings", "all",
                  "--jobs", str(build_jobs(self.settings))] + self._urls(self.core_id(project))
                 + [project.path]]
        labels = ["Compilation check (all warnings)"]
        cppcheck = self.tools.get(detect.CPPCHECK)
        if cppcheck.found:
            steps.append([cppcheck.path, "--enable=warning,style,performance,portability",
                          "--inline-suppr", "--quiet", "--language=c++", "--std=c++17",
                          "--error-exitcode=1", "--suppress=missingIncludeSystem",
                          "--exclude=" + os.path.join(project.path, ".mcode"), project.path])
            labels.append("Static analysis (cppcheck)")
        return Plan(f"Testing {project.name}", steps, project.path, timeout=self.timeout(),
                    labels=labels)

    def check(self, project: Project) -> Plan:
        cppcheck = self.tools.get(detect.CPPCHECK)
        if not cppcheck.found:
            raise BackendError("Static analysis for Arduino sketches uses cppcheck, which is not "
                               "installed (sudo apt install cppcheck).")
        return Plan(f"Checking {project.name}", [[
            cppcheck.path, "--enable=warning,style,performance,portability", "--inline-suppr",
            "--language=c++", "--std=c++17", "--suppress=missingIncludeSystem",
            "--exclude=" + os.path.join(project.path, ".mcode"), project.path]], project.path,
            timeout=self.timeout(), labels=["Static analysis (cppcheck)"])

    def upload(self, project: Project, port: str, speed: int = 0, verify: bool = True) -> Plan:
        cli = self.program()
        fqbn = self._fqbn(project)
        board = project.board
        if speed and board and board.flasher == "esptool":
            fqbn = _with_option(fqbn, "UploadSpeed", str(speed))
        argv = [cli, "upload", "-p", port, "--fqbn", fqbn, "--input-dir", self.build_dir(project)]
        if verify and board and board.flasher in ("avrdude", "bossac"):
            argv.append("--verify")
        argv.append(project.path)
        return Plan(f"Uploading {project.name}", [argv], project.path,
                    timeout=self.upload_timeout(), labels=["Uploading with the Arduino CLI"])

    def artifact(self, project: Project) -> str:
        folder = self.build_dir(project)
        base = os.path.join(folder, project.slug + ".ino")
        for ext in (".bin", ".hex", ".uf2", ".elf"):
            if os.path.exists(base + ext):
                return base + ext
        return ""

    def requirements(self, project: Project) -> List[dict]:
        out = []
        tool = self.tools.get(self.tool_id)
        if not tool.found:
            return [{"id": "tool", "label": "Arduino CLI", "fix": "install:arduino-cli"}]
        core = self.core_id(project)
        if core and not self.core_installed(core):
            out.append({"id": "core", "label": CORE_NAMES.get(core, core),
                        "fix": f"install-core:{core}"})
        return out

    def core_installed(self, core: str) -> bool:
        vendor, _, arch = core.partition(":")
        base = os.path.join(self.tools.home, ".arduino15", "packages", vendor, "hardware", arch)
        return os.path.isdir(base) and any(os.path.isfile(os.path.join(base, v, "boards.txt"))
                                           for v in os.listdir(base))

    def install_core(self, core: str) -> Plan:
        if not re.fullmatch(r"[a-z0-9_]+:[a-z0-9_]+", core):
            raise BackendError("Unknown core.")
        cli = self.program()
        urls = self._urls(core)
        return Plan(f"Installing {CORE_NAMES.get(core, core)}",
                    [[cli, "core", "update-index"] + urls, [cli, "core", "install", core] + urls],
                    "", timeout=3600, labels=["Updating the board index", "Downloading the core"])


def _with_option(fqbn: str, key: str, value: str) -> str:
    parts = fqbn.split(":")
    options = {}
    if len(parts) > 3:
        options = dict(item.split("=", 1) for item in parts[3].split(",") if "=" in item)
    options[key] = value
    return ":".join(parts[:3]) + ":" + ",".join(f"{k}={v}" for k, v in options.items())


# ================================================================= PlatformIO

class PlatformIOBackend(Backend):
    toolchain = PLATFORMIO
    tool_id = detect.PLATFORMIO

    def envs(self, project: Project) -> List[dict]:
        return _pio_envs(os.path.join(project.path, "platformio.ini"))

    def env(self, project: Project) -> str:
        envs = [e for e in self.envs(project) if e.get("platform") != "native"]
        names = [e["name"] for e in envs]
        if project.pio_env and project.pio_env in names:
            return project.pio_env
        if names:
            return names[0]
        raise BackendError("platformio.ini has no [env:...] section for a board.")

    def has_native(self, project: Project) -> bool:
        return any(e.get("platform") == "native" for e in self.envs(project))

    def _base(self, project: Project) -> List[str]:
        return [self.program(), "run", "-d", project.path]

    def build(self, project: Project, clean: bool = False) -> Plan:
        env = self.env(project)
        jobs = ["-j", str(build_jobs(self.settings))]
        steps = []
        labels = []
        if clean:
            steps.append(self._base(project) + ["-e", env, "-t", "clean"])
            labels.append("Cleaning")
        steps.append(self._base(project) + ["-e", env] + jobs)
        labels.append("Compiling with PlatformIO")
        return Plan(f"{'Rebuilding' if clean else 'Building'} {project.name}", steps,
                    project.path, timeout=self.timeout(), labels=labels)

    def clean(self, project: Project) -> Plan:
        return Plan(f"Cleaning {project.name}",
                    [self._base(project) + ["-e", self.env(project), "-t", "clean"]],
                    project.path, timeout=self.timeout(), labels=["Cleaning"])

    def test(self, project: Project) -> Plan:
        pio = self.program()
        if self.has_native(project):
            if not self.tools.get(detect.CC).found:
                raise BackendError("Host tests need a C compiler: sudo apt install build-essential")
            return Plan(f"Testing {project.name}", [[pio, "test", "-d", project.path, "-e", "native"]],
                        project.path, timeout=self.timeout(),
                        labels=["Unit tests on this computer (pio test -e native)"])
        return Plan(f"Testing {project.name}",
                    [[pio, "test", "-d", project.path, "-e", self.env(project),
                      "--without-uploading", "--without-testing"]], project.path,
                    timeout=self.timeout(),
                    labels=["Building the tests (running them needs the board)"])

    def check(self, project: Project) -> Plan:
        return Plan(f"Checking {project.name}",
                    [[self.program(), "check", "-d", project.path, "-e", self.env(project),
                      "--skip-packages"]], project.path, timeout=self.timeout(),
                    labels=["Static analysis (pio check)"])

    def upload(self, project: Project, port: str, speed: int = 0, verify: bool = True) -> Plan:
        argv = self._base(project) + ["-e", self.env(project), "-t", "nobuild", "-t", "upload",
                                      "--upload-port", port]
        return Plan(f"Uploading {project.name}", [argv], project.path,
                    timeout=self.upload_timeout(), labels=["Uploading with PlatformIO"])

    def test_on_board(self, project: Project, port: str) -> Plan:
        return Plan(f"Testing {project.name} on the board",
                    [[self.program(), "test", "-d", project.path, "-e", self.env(project),
                      "--upload-port", port, "--test-port", port]], project.path,
                    timeout=self.timeout(), labels=["Uploading and running tests on the board"])

    def artifact(self, project: Project) -> str:
        try:
            env = self.env(project)
        except BackendError:
            return ""
        folder = os.path.join(project.path, ".pio", "build", env)
        for name in ("firmware.bin", "firmware.hex", "firmware.uf2", "firmware.elf"):
            if os.path.exists(os.path.join(folder, name)):
                return os.path.join(folder, name)
        return ""

    def requirements(self, project: Project) -> List[dict]:
        if not self.tools.get(self.tool_id).found:
            return [{"id": "tool", "label": "PlatformIO", "fix": "install:platformio"}]
        return []


# ==================================================================== ESP-IDF

class EspIdfBackend(Backend):
    toolchain = ESP_IDF
    tool_id = detect.ESP_IDF

    def _idf(self) -> str:
        tool = self.tools.get(detect.ESP_IDF)
        if not tool.found:
            raise BackendError("ESP-IDF is not set up. " + (tool.problem or "") + " " +
                               detect.HINTS[detect.ESP_IDF], fix="install:esp-idf",
                               fix_label="Install ESP-IDF")
        return tool.path

    def _shell(self, idf: str, project: Project, commands: List[List[str]]) -> List[str]:
        body = " && ".join(" ".join(shlex.quote(a) for a in cmd) for cmd in commands)
        script = f'. "$IDF_PATH/export.sh" >/dev/null 2>&1 || {{ echo "ESP-IDF export.sh failed"; exit 2; }}; {body}'
        return ["bash", "-c", script]

    def _env(self, idf: str) -> Dict[str, str]:
        # Without mCode's own Python environment on PATH, so export.sh picks
        # the system Python its environment was made with (installer.py).
        # IDF_PY_BUILD_JOBS: idf.py's own limit for ninja's parallel jobs (by
        # default the CPU count + 2, too many for a 1 GB board).
        from .installer import clean_path_env
        return dict(clean_path_env(), IDF_PATH=idf,
                    IDF_PY_BUILD_JOBS=str(build_jobs(self.settings)))

    def target(self, project: Project) -> str:
        board = project.board
        if not board or not board.idf_target:
            raise BackendError(f"{project.board_name} is not an ESP-IDF target.")
        return board.idf_target

    def configured_target(self, project: Project) -> str:
        try:
            with open(os.path.join(project.path, "sdkconfig"), encoding="utf-8",
                      errors="replace") as handle:
                match = re.search(r'^CONFIG_IDF_TARGET="([a-z0-9]+)"', handle.read(), re.M)
                return match.group(1) if match else ""
        except OSError:
            return ""

    def build(self, project: Project, clean: bool = False) -> Plan:
        idf = self._idf()
        target = self.target(project)
        commands = []
        if self.configured_target(project) != target:
            commands.append(["idf.py", "-C", project.path, "set-target", target])
        if clean:
            commands.append(["idf.py", "-C", project.path, "fullclean"])
        commands.append(["idf.py", "-C", project.path, "build"])
        return Plan(f"{'Rebuilding' if clean else 'Building'} {project.name}",
                    [self._shell(idf, project, commands)], project.path, env=self._env(idf),
                    timeout=self.timeout(), labels=["Building with ESP-IDF"])

    def clean(self, project: Project) -> Plan:
        idf = self._idf()
        return Plan(f"Cleaning {project.name}",
                    [self._shell(idf, project, [["idf.py", "-C", project.path, "fullclean"]])],
                    project.path, env=self._env(idf), timeout=self.timeout(), labels=["Cleaning"])

    def test(self, project: Project) -> Plan:
        plan = self.build(project)
        plan.what = f"Testing {project.name}"
        plan.labels = ["Compilation check (Unity component tests run on the board)"]
        return plan

    def upload(self, project: Project, port: str, speed: int = 0, verify: bool = True) -> Plan:
        esptool = self.tools.get(detect.ESPTOOL)
        build = os.path.join(project.path, "build")
        if not os.path.exists(os.path.join(build, "flash_args")):
            raise BackendError("Build the project first (build/flash_args is missing).")
        if esptool.found:
            argv = [esptool.path, "--chip", self.target(project), "-p", port, "-b",
                    str(speed or 460800), "--before", "default_reset", "--after", "hard_reset"]
            argv += [_cmd(esptool, "write-flash", "write_flash"), "@flash_args"]
            return Plan(f"Uploading {project.name}", [argv], build, timeout=self.upload_timeout(),
                        labels=["Writing with esptool"])
        idf = self._idf()
        return Plan(f"Uploading {project.name}",
                    [self._shell(idf, project, [["idf.py", "-C", project.path, "-p", port, "-b",
                                                str(speed or 460800), "flash"]])],
                    project.path, env=self._env(idf), timeout=self.upload_timeout(),
                    labels=["Flashing with idf.py"])

    def artifact(self, project: Project) -> str:
        build = os.path.join(project.path, "build")
        if not os.path.exists(os.path.join(build, "flash_args")):
            return ""
        bins = [n for n in os.listdir(build) if n.endswith(".bin")]
        return os.path.join(build, bins[0]) if bins else ""

    def requirements(self, project: Project) -> List[dict]:
        tool = self.tools.get(detect.ESP_IDF)
        if not tool.found:
            return [{"id": "tool", "label": "ESP-IDF", "fix": "install:esp-idf"}]
        return []


def _cmd(esptool: detect.Tool, new: str, old: str) -> str:
    """esptool 5 renamed commands to dashes; 4.x only knows underscores."""
    major = int((esptool.version or "4").split(".")[0] or 4)
    return new if major >= 5 else old


# ======================================================================= Make

class MakeBackend(Backend):
    toolchain = MAKE
    tool_id = detect.MAKE

    def build(self, project: Project, clean: bool = False) -> Plan:
        make = self.program()
        steps = []
        if clean:
            steps.append([make, "-C", project.path, "clean"])
        steps.append([make, "-C", project.path, f"-j{build_jobs(self.settings)}"])
        return Plan(f"{'Rebuilding' if clean else 'Building'} {project.name}", steps, project.path,
                    timeout=self.timeout(), labels=["make clean", "make"][-len(steps):])

    def clean(self, project: Project) -> Plan:
        return Plan(f"Cleaning {project.name}", [[self.program(), "-C", project.path, "clean"]],
                    project.path, timeout=self.timeout(), labels=["make clean"])

    def test(self, project: Project) -> Plan:
        return Plan(f"Testing {project.name}", [[self.program(), "-C", project.path, "test"]],
                    project.path, timeout=self.timeout(), labels=["make test"])

    def upload(self, project: Project, port: str, speed: int = 0, verify: bool = True) -> Plan:
        raise BackendError("Make projects have no upload step in mCode: add a flash target to "
                           "your Makefile and run it from the terminal.")

    def up_to_date(self, project: Project) -> bool:
        return False


BACKENDS = {ARDUINO_CLI: ArduinoCliBackend, PLATFORMIO: PlatformIOBackend, ESP_IDF: EspIdfBackend,
            MAKE: MakeBackend}


def for_project(project: Project, tools: detect.Toolchains, settings) -> Backend:
    cls = BACKENDS.get(project.toolchain)
    if cls is None:
        raise BackendError(f"Unknown project type {project.toolchain}.")
    return cls(tools, settings)


class Esptool:
    """Direct esptool operations: chip detection (read-only), erase, verify."""

    def __init__(self, tools: detect.Toolchains):
        self.tools = tools

    def program(self) -> detect.Tool:
        tool = self.tools.get(detect.ESPTOOL)
        if not tool.found:
            raise BackendError("esptool is not installed. " + detect.HINTS[detect.ESPTOOL],
                               fix="install:esptool", fix_label="Install esptool")
        return tool

    def chip_id(self, port: str) -> List[str]:
        tool = self.program()
        return [tool.path, "--port", port, _cmd(tool, "chip-id", "chip_id")]

    def erase(self, port: str) -> List[str]:
        tool = self.program()
        return [tool.path, "--port", port, _cmd(tool, "erase-flash", "erase_flash")]


_CHIP = re.compile(r"(?:Chip is|Chip type:|Detecting chip type\.\.\.)\s*(ESP32[-\w]*)", re.I)
_MAC = re.compile(r"MAC:\s*([0-9a-f:]{17})", re.I)
_FEATURES = re.compile(r"Features:\s*(.+)")
_CRYSTAL = re.compile(r"Crystal (?:is|frequency:)\s*(\d+\s*MHz)", re.I)


def parse_chip(output: str) -> dict:
    """What esptool said about the chip (no guesses: empty when it said nothing)."""
    info = {}
    chip = _CHIP.findall(output)
    if chip:
        info["chip"] = chip[-1].upper().replace("ESP32S", "ESP32-S").replace("ESP32C", "ESP32-C") \
            .replace("ESP32H", "ESP32-H").replace("ESP32P", "ESP32-P")
    for pattern, key in ((_MAC, "mac"), (_FEATURES, "features"), (_CRYSTAL, "crystal")):
        found = pattern.search(output)
        if found:
            info[key] = found.group(1).strip()
    return info
