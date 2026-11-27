"""Find the development tools on this machine and report their versions.

Each tool is looked for in a fixed order: the path set in Settings, mCode's
own tools folder, PATH, then the places the tool's official installer uses.
Nothing is installed or changed here. Detection runs the tools'
``--version`` commands (with short timeouts) off the interface threads and
is cached until the next refresh (after an install, or on request).
"""

from __future__ import annotations

import glob
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable, Dict, List, Optional

from ..paths import expand

log = logging.getLogger("mcode.tools")

CLAUDE, ARDUINO_CLI, PLATFORMIO, ESP_IDF, ESPTOOL, MAKE, GIT = (
    "claude", "arduino-cli", "platformio", "esp-idf", "esptool", "make", "git")
ARECORD, APLAY, ESPEAK, CPPCHECK, CC = "arecord", "aplay", "espeak", "cppcheck", "cc"

NAMES = {CLAUDE: "Claude Code", ARDUINO_CLI: "Arduino CLI", PLATFORMIO: "PlatformIO",
         ESP_IDF: "ESP-IDF", ESPTOOL: "esptool", MAKE: "Make", GIT: "Git",
         ARECORD: "Microphone (arecord)", APLAY: "Speaker (aplay)", ESPEAK: "eSpeak NG",
         CPPCHECK: "Cppcheck", CC: "C compiler"}
INSTALLABLE = (CLAUDE, ARDUINO_CLI, PLATFORMIO, ESPTOOL, ESP_IDF)
HINTS = {
    CLAUDE: "Install from Settings > Development tools, or: curl -fsSL https://claude.ai/install.sh | bash",
    ARDUINO_CLI: "Install from Settings > Development tools (official release, checksum verified).",
    PLATFORMIO: "Install from Settings > Development tools (PlatformIO Core in mCode's own Python "
                "environment).",
    ESP_IDF: "Install from Settings > Development tools (Espressif's release and tools, about "
             "6 GB), or by hand as in docs/TOOLCHAINS.md and set the ESP-IDF folder in Settings.",
    ESPTOOL: "Comes with the ESP32 Arduino core and with PlatformIO, or install it from "
             "Settings > Development tools.",
    MAKE: "sudo apt install build-essential",
    GIT: "sudo apt install git",
    ARECORD: "sudo apt install alsa-utils",
    APLAY: "sudo apt install alsa-utils",
    ESPEAK: "Optional, for spoken feedback without an OpenAI key: sudo apt install espeak-ng",
    CPPCHECK: "Optional static analysis for Arduino sketches: sudo apt install cppcheck",
    CC: "sudo apt install build-essential",
}


@dataclass
class Tool:
    id: str
    name: str
    path: str = ""
    version: str = ""
    source: str = ""
    problem: str = ""
    hint: str = ""
    extra: Optional[dict] = None

    @property
    def found(self) -> bool:
        return bool(self.path) and not self.problem

    def to_dict(self) -> dict:
        data = asdict(self)
        data["found"] = self.found
        data["installable"] = self.id in INSTALLABLE
        return data


def _version(argv: List[str], pattern: str = r"(\d+\.\d+(?:\.\d+)?)", timeout: float = 8.0,
             env: Optional[dict] = None) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                                stdin=subprocess.DEVNULL, env=env)
    except (OSError, subprocess.SubprocessError):
        return ""
    text = (result.stdout or "") + " " + (result.stderr or "")
    match = re.search(pattern, text)
    return match.group(1) if match else ""


def _executable(path: str) -> bool:
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)


def _newest(paths: List[str]) -> str:
    def key(p):
        nums = re.findall(r"\d+", p)
        return [int(n) for n in nums[-6:]]
    paths = [p for p in paths if _executable(p)]
    return sorted(paths, key=key)[-1] if paths else ""


def machine() -> dict:
    """CPU architecture and memory (for warnings on small boards)."""
    total = 0
    try:
        with open("/proc/meminfo") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) // 1024
                    break
    except OSError:
        pass
    model = ""
    for path in ("/proc/device-tree/model", "/sys/firmware/devicetree/base/model"):
        try:
            with open(path, "rb") as handle:
                model = handle.read().rstrip(b"\0").decode("utf-8", "replace")
                break
        except OSError:
            continue
    return {"arch": platform.machine(), "memory_mb": total, "cpus": os.cpu_count() or 1,
            "model": model, "python": platform.python_version(), "system": platform.system()}


class Toolchains:
    def __init__(self, settings, tools_bin: str, venv: str, home: Optional[str] = None,
                 publish: Callable[[str, object], None] = lambda t, d: None):
        self.settings = settings
        self.tools_bin = tools_bin
        self.venv = venv
        self.home = home or os.path.expanduser("~")
        self.publish = publish
        self._lock = threading.Lock()
        self._tools: Dict[str, Tool] = {}
        self._detected_at = 0.0
        self._detecting = False

    # ---------------------------------------------------------------- access
    def get(self, tool_id: str, refresh: bool = False) -> Tool:
        with self._lock:
            cached = self._tools.get(tool_id)
        if cached is not None and not refresh:
            return cached
        tool = self._detect_one(tool_id)
        with self._lock:
            self._tools[tool_id] = tool
        return tool

    def path(self, tool_id: str) -> str:
        tool = self.get(tool_id)
        return tool.path if tool.found else ""

    def all(self) -> List[Tool]:
        with self._lock:
            known = dict(self._tools)
        return [known.get(t) or Tool(t, NAMES[t], problem="not checked yet")
                for t in NAMES]

    def refresh(self, wait: bool = False, only: Optional[List[str]] = None) -> None:
        def work():
            tools = {}
            for tool_id in (only or list(NAMES)):
                try:
                    tools[tool_id] = self._detect_one(tool_id)
                except Exception as exc:       # isolation boundary: one odd tool, not all
                    log.exception("detecting %s failed", tool_id)
                    tools[tool_id] = Tool(tool_id, NAMES[tool_id], problem=str(exc))
            with self._lock:
                self._tools.update(tools)
                self._detected_at = time.time()
                self._detecting = False
            self.publish("tools", [t.to_dict() for t in self.all()])
        with self._lock:
            if self._detecting and not wait:
                return
            self._detecting = True
        if wait:
            work()
        else:
            threading.Thread(target=work, name="tool-detect", daemon=True).start()

    def env_path(self) -> List[str]:
        """Folders to put in front of PATH for jobs (mCode's own tools)."""
        out = [self.tools_bin, os.path.join(self.venv, "bin")]
        return [p for p in out if os.path.isdir(p)]

    # -------------------------------------------------------------- detection
    def _setting(self, key: str) -> str:
        try:
            return expand(self.settings.get(key))
        except KeyError:
            return ""

    def _first(self, candidates: List[tuple]) -> tuple:
        for path, source in candidates:
            if path and _executable(path):
                return path, source
        return "", ""

    def _detect_one(self, tool_id: str) -> Tool:
        method = getattr(self, "_detect_" + tool_id.replace("-", "_"))
        tool = method()
        tool.name = NAMES[tool_id]
        if not tool.found and not tool.hint:
            tool.hint = HINTS.get(tool_id, "")
        if not tool.path and not tool.problem:
            tool.problem = "not installed"
        return tool

    def _detect_claude(self) -> Tool:
        home = self.home
        vscode = glob.glob(os.path.join(home, ".vscode*", "extensions", "anthropic.claude-code-*",
                                        "resources", "native-binary", "claude"))
        path, source = self._first([
            (self._setting("ai.claude_path"), "settings"),
            (shutil.which("claude") or "", "PATH"),
            (os.path.join(home, ".local", "bin", "claude"), "~/.local/bin"),
            (os.path.join(home, ".claude", "local", "claude"), "~/.claude/local"),
            (os.path.join(home, ".npm-global", "bin", "claude"), "npm"),
            (_newest(vscode), "VS Code extension"),
        ])
        tool = Tool(CLAUDE, "", path, source=source)
        if path:
            tool.version = _version([path, "--version"])
            if not tool.version:
                tool.problem = "does not run (claude --version failed)"
        return tool

    def _detect_arduino_cli(self) -> Tool:
        path, source = self._first([
            (self._setting("dev.arduino_cli"), "settings"),
            (os.path.join(self.tools_bin, "arduino-cli"), "mCode tools"),
            (shutil.which("arduino-cli") or "", "PATH"),
            (os.path.join(self.home, ".local", "bin", "arduino-cli"), "~/.local/bin"),
            (os.path.join(self.home, "bin", "arduino-cli"), "~/bin"),
        ])
        tool = Tool(ARDUINO_CLI, "", path, source=source)
        if path:
            try:
                result = subprocess.run([path, "version", "--format", "json"], capture_output=True,
                                        text=True, timeout=8, stdin=subprocess.DEVNULL)
                tool.version = json.loads(result.stdout or "{}").get("VersionString", "")
            except (OSError, ValueError, subprocess.SubprocessError):
                tool.version = _version([path, "version"])
            if not tool.version:
                tool.problem = "does not run (arduino-cli version failed)"
        return tool

    def _detect_platformio(self) -> Tool:
        path, source = self._first([
            (self._setting("dev.platformio"), "settings"),
            (os.path.join(self.venv, "bin", "pio"), "mCode tools"),
            (shutil.which("pio") or "", "PATH"),
            (shutil.which("platformio") or "", "PATH"),
            (os.path.join(self.home, ".platformio", "penv", "bin", "pio"), "~/.platformio"),
        ])
        tool = Tool(PLATFORMIO, "", path, source=source)
        if path:
            tool.version = _version([path, "--version"], timeout=20)
            if not tool.version:
                tool.problem = "does not run (pio --version failed)"
        return tool

    def idf_candidates(self) -> List[str]:
        out = [self._setting("dev.idf_path"), expand(os.environ.get("IDF_PATH", ""))]
        out += [os.path.join(self.home, "esp", "esp-idf")]
        out += sorted(glob.glob(os.path.join(self.home, "esp", "v*", "esp-idf")), reverse=True)
        out += sorted(glob.glob(os.path.join(self.home, ".espressif", "frameworks", "esp-idf-*")),
                      reverse=True)
        return [p for p in out if p]

    def _detect_esp_idf(self) -> Tool:
        tool = Tool(ESP_IDF, "")
        for folder in self.idf_candidates():
            if os.path.isfile(os.path.join(folder, "tools", "idf.py")):
                tool.path = folder
                tool.source = "settings" if folder == self._setting("dev.idf_path") else "found"
                break
        if not tool.path:
            return tool
        version_file = os.path.join(tool.path, "tools", "cmake", "version.cmake")
        try:
            with open(version_file) as handle:
                text = handle.read()
            parts = [re.search(rf"IDF_VERSION_{n}\s+(\d+)", text) for n in ("MAJOR", "MINOR", "PATCH")]
            tool.version = ".".join(m.group(1) for m in parts if m)
        except OSError:
            tool.version = ""
        if not os.path.isfile(os.path.join(tool.path, "export.sh")):
            tool.problem = "export.sh missing: not a complete ESP-IDF checkout"
        elif not glob.glob(os.path.join(self.home, ".espressif", "python_env", "*")):
            tool.problem = "ESP-IDF tools not installed: run install.sh in the ESP-IDF folder"
        return tool

    def _detect_esptool(self) -> Tool:
        core = glob.glob(os.path.join(self.home, ".arduino15", "packages", "esp32", "tools",
                                      "esptool_py", "*", "esptool"))
        path, source = self._first([
            (self._setting("dev.esptool"), "settings"),
            (os.path.join(self.venv, "bin", "esptool"), "mCode tools"),
            (os.path.join(self.venv, "bin", "esptool.py"), "mCode tools"),
            (shutil.which("esptool") or "", "PATH"),
            (shutil.which("esptool.py") or "", "PATH"),
            (_newest(core), "Arduino ESP32 core"),
        ])
        tool = Tool(ESPTOOL, "", path, source=source)
        if path:
            tool.version = _version([path, "version"])
            if not tool.version:
                tool.problem = "does not run (esptool version failed)"
        return tool

    def _simple(self, tool_id: str, names: List[str], version_args: Optional[List[str]] = None) -> Tool:
        for name in names:
            path = shutil.which(name)
            if path:
                tool = Tool(tool_id, "", path, source="PATH")
                if version_args is not None:
                    tool.version = _version([path] + version_args)
                return tool
        return Tool(tool_id, "")

    def _detect_make(self) -> Tool:
        return self._simple(MAKE, ["make"], ["--version"])

    def _detect_git(self) -> Tool:
        return self._simple(GIT, ["git"], ["--version"])

    def _detect_arecord(self) -> Tool:
        return self._simple(ARECORD, ["arecord"], ["--version"])

    def _detect_aplay(self) -> Tool:
        return self._simple(APLAY, ["aplay"], ["--version"])

    def _detect_espeak(self) -> Tool:
        return self._simple(ESPEAK, ["espeak-ng", "espeak"], ["--version"])

    def _detect_cppcheck(self) -> Tool:
        return self._simple(CPPCHECK, ["cppcheck"], ["--version"])

    def _detect_cc(self) -> Tool:
        return self._simple(CC, ["cc", "gcc", "clang"], ["--version"])
