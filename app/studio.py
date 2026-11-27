"""Studio: the one object that owns every mCode service.

Both interfaces (the Whisplay LCD and the desktop IDE), the command line and
the AI's tool bridge use this object; none of them builds, uploads, edits
files or talks to Claude on its own. Creating a Studio has no side effects
beyond the data folder; ``start()`` begins the background work (tool
detection, USB hot-plug) and ``stop()`` ends it, cancelling running jobs.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable, List, Optional

from . import APP_NAME, VERSION
from .core.config import Settings
from .core.events import EventBus
from .core.history import History
from .core.jobs import JobManager
from .core.state import StateStore
from .paths import Paths
from .projects.manager import ProjectManager
from .projects.model import Project, ProjectError
from .toolchains import detect
from .toolchains.boardconfig import BoardConfig
from .toolchains.builds import BuildService

log = logging.getLogger("mcode.studio")


class Studio:
    def __init__(self, data_dir: Optional[str] = None, environ=None, home: Optional[str] = None,
                 hardware: bool = True, ai: bool = True, voice: bool = True,
                 sysfs: str = "/sys", devroot: str = "/dev"):
        self.version = VERSION
        self.name = APP_NAME
        self.paths = Paths(data_dir, environ).ensure()
        self.events = EventBus()
        self.settings = Settings(self.paths.settings_file,
                                 on_change=lambda k, v: self.events.publish("settings", {k: v}))
        self.state = StateStore(self.paths.state_file)
        self.history = History(self.paths.history_file)
        self.tools = detect.Toolchains(self.settings, self.paths.tools_bin, self.paths.venv,
                                       home=home, publish=self.events.publish)
        self.jobs = JobManager(self.paths.job_logs, publish=self.events.publish,
                               extra_path=[self.paths.tools_bin,
                                           os.path.join(self.paths.venv, "bin")])
        self.projects = ProjectManager(self.settings, self.state, self.paths.backups,
                                       publish=self.events.publish)
        self.builds = BuildService(self.jobs, self.tools, self.settings, self.history,
                                   publish=self.events.publish, paths=self.paths)
        self.devices = None
        self.boardconfig = BoardConfig(self)
        self.monitor = None
        self.uploads = None
        self.libraries = None
        self.ai = None
        self.voice = None
        self._started = False
        self._stop_callbacks: List[Callable[[], None]] = []
        if hardware:
            self._init_hardware(sysfs, devroot)
        self._init_libraries()
        if ai:
            self._init_ai()
        if voice:
            self._init_voice()

    # --------------------------------------------------------------- wiring
    def _init_hardware(self, sysfs: str, devroot: str) -> None:
        try:
            from .hardware.devices import DeviceManager
            from .hardware.monitor import SerialMonitor
            from .hardware.ports import PortCoordinator
            from .hardware.upload import UploadService
        except ImportError as exc:          # during early development phases
            log.debug("hardware services unavailable: %s", exc)
            return
        self.ports = PortCoordinator()
        self.devices = DeviceManager(self.state, publish=self.events.publish, sysfs=sysfs,
                                     devroot=devroot)
        self.monitor = SerialMonitor(self.ports, self.paths.serial_logs, publish=self.events.publish)
        self.uploads = UploadService(self)

    def _init_libraries(self) -> None:
        try:
            from .toolchains.libraries import LibraryService
        except ImportError as exc:
            log.debug("library service unavailable: %s", exc)
            return
        self.libraries = LibraryService(self)

    def _init_ai(self) -> None:
        try:
            from .ai.service import AIService
        except ImportError as exc:
            log.debug("AI service unavailable: %s", exc)
            return
        self.ai = AIService(self)

    def _init_voice(self) -> None:
        try:
            from .voice.service import VoiceService
        except ImportError as exc:
            log.debug("voice service unavailable: %s", exc)
            return
        self.voice = VoiceService(self)

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.prune_logs()
        self.tools.refresh()
        if self.devices is not None:
            self.devices.start()
        if self.ai is not None:
            self.ai.start()
        log.info("%s %s, data in %s, projects in %s", self.name, self.version, self.paths.data,
                 self.projects.root)

    def prune_logs(self) -> int:
        """Delete job and serial logs older than Settings > Keep logs (days)."""
        import time as _time
        limit = _time.time() - 86400 * int(self.settings.get("storage.log_days"))
        removed = 0
        for folder in (self.paths.job_logs, self.paths.serial_logs):
            try:
                names = os.listdir(folder)
            except OSError:
                continue
            for name in names:
                path = os.path.join(folder, name)
                try:
                    if os.path.isfile(path) and os.path.getmtime(path) < limit:
                        os.unlink(path)
                        removed += 1
                except OSError:
                    continue
        if removed:
            log.info("removed %d old log files", removed)
        return removed

    def stop(self, timeout: float = 2.0) -> None:
        for callback in reversed(self._stop_callbacks):
            try:
                callback()
            except Exception:       # isolation boundary: keep stopping the rest
                log.exception("stop callback failed")
        if self.ai is not None:
            self.ai.stop()
        if self.monitor is not None:
            self.monitor.close_all()
        if self.devices is not None:
            self.devices.stop()
        if self.voice is not None:
            self.voice.stop()
        self.jobs.shutdown(timeout)

    def on_stop(self, callback: Callable[[], None]) -> None:
        self._stop_callbacks.append(callback)

    def toast(self, text: str, tone: str = "") -> None:
        self.events.publish("toast", {"text": text, "tone": tone})

    # -------------------------------------------------------------- queries
    def project(self, key: Optional[str] = None) -> Project:
        """A project by id/path, or the current project."""
        if key:
            return self.projects.find(key)
        current = self.projects.current()
        if current is None:
            raise ProjectError("No project is open. Create or open one in My Projects.")
        return current

    def summary(self) -> dict:
        """What the dashboard and the LCD home screen show."""
        current = self.projects.current()
        claude = self.ai.status() if self.ai is not None else {"state": "unavailable"}
        device = None
        if self.devices is not None:
            chosen = self.devices.selected(current)
            device = chosen.to_dict() if chosen is not None else None
        last = self.builds.last(current.id) if current else None
        return {"version": self.version, "project": current.summary() if current else None,
                "claude": claude, "device": device,
                "devices": [d.to_dict() for d in self.devices.list()] if self.devices else [],
                "last_build": _brief(last), "jobs": [j.to_dict() for j in self.jobs.active()],
                "machine": detect.machine()}


def _brief(record: Optional[dict]) -> Optional[dict]:
    if not record:
        return None
    keys = ("project_id", "project", "action", "state", "elapsed", "headline", "errors",
            "warnings", "size", "time", "tests", "missing_headers")
    return {k: record.get(k) for k in keys if k in record}
