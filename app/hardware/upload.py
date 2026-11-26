"""Firmware upload, flash erase and chip detection, with safety checks.

Nothing is ever written to a board without the user confirming it. The flow
is always two steps:

1. ``prepare(project, port)`` runs the checks and returns a plan: the
   board, the port, the firmware, every check with its result, and a
   one-time **token**. Interfaces show this as the "Flash firmware?" screen.
2. ``start(token)``, only after the user confirmed on that screen, runs the
   checks again (the board may have been unplugged in between), pauses the
   serial monitor, uploads exactly the firmware that was built, and resumes
   the monitor.

The AI has no path to this module: Claude can build and test, never upload.

Checks (any failure stops the upload and says why):

* the project's board has an upload method mCode supports;
* the firmware exists and is newer than every source file;
* the selected device is still connected and this user may open it;
* no other program has the port open (the monitor is paused instead);
* the connected device matches the project: an Arduino Uno's USB ID on an
  ESP32 project, an ESP32 chip on an AVR project, or esptool reporting a
  different ESP32 chip all stop the upload. A plain USB-UART adapter cannot
  tell which board is behind it: that is shown, and the user confirms.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from ..core.jobs import Job, JobFailed, JobSpec
from ..projects import boards
from ..projects.model import Project, ProjectError
from ..toolchains import backends, diagnostics
from ..toolchains.backends import BackendError
from . import usbids
from .devices import Device
from .ports import PortBusy, external_holders

log = logging.getLogger("mcode.upload")
TOKEN_TTL = 300.0
OWNER = "an upload"


@dataclass
class Check:
    id: str
    ok: bool
    message: str
    level: str = "error"            # error | warning | info

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class UploadPlan:
    action: str                     # upload | erase | identify | test-on-board
    project_id: str
    project: str
    board: str
    port: str
    device: str
    firmware: str
    checks: List[Check] = field(default_factory=list)
    token: str = ""
    created: float = field(default_factory=time.time)
    speed: int = 0

    @property
    def ok(self) -> bool:
        return all(c.ok or c.level != "error" for c in self.checks)

    @property
    def warnings(self) -> List[Check]:
        return [c for c in self.checks if not c.ok and c.level == "warning"]

    def problem(self) -> str:
        failed = [c for c in self.checks if not c.ok and c.level == "error"]
        return failed[0].message if failed else ""

    def to_dict(self) -> dict:
        return {"action": self.action, "project_id": self.project_id, "project": self.project,
                "board": self.board, "port": self.port, "device": self.device,
                "firmware": self.firmware, "speed": self.speed, "ok": self.ok,
                "problem": self.problem(), "token": self.token if self.ok else "",
                "checks": [c.to_dict() for c in self.checks],
                "warnings": [c.message for c in self.warnings]}


def match_device(project: Project, device: Device) -> Check:
    """Does the connected device fit the project's board? (Never a guess from a name.)"""
    board = project.board
    if board is None:
        return Check("match", False, "The project has no known board.")
    project_family = usbids.chip_family(board.mcu) if board.platform == "esp32" or \
        board.mcu.upper().startswith("ESP32") else ""
    if device.chip:
        family = usbids.chip_family(device.chip)
        if project_family and family and family != project_family:
            return Check("match", False, f"esptool found an {device.chip} on "
                         f"{device.short_port}, but the project is for {board.mcu}.")
        if not project_family:
            return Check("match", False, f"{device.short_port} is an {device.chip}; the project "
                         f"is for {board.name}.")
        return Check("match", True, f"{device.chip} matches the project (identified by esptool).",
                     "info")
    if device.board_id:
        if device.board_id == board.id:
            return Check("match", True, f"{device.short_port} is an "
                         f"{boards.get(device.board_id).name} (by its USB ID).", "info")
        other = boards.get(device.board_id)
        return Check("match", False, f"{device.short_port} is an "
                     f"{other.name if other else device.board_id} (by its USB ID), but the "
                     f"project is for {board.name}. Choose the right board or port.")
    if device.kind == "native-esp":
        if not project_family:
            return Check("match", False, f"{device.short_port} is an ESP32 with built-in USB; "
                         f"the project is for {board.name}.")
        if board.native_usb:
            return Check("match", True, "An ESP32 with built-in USB: mCode cannot tell which "
                         "ESP32 it is until you identify the chip.", "warning")
        return Check("match", False, f"{device.short_port} is an ESP32 with built-in USB, but "
                     f"the {board.mcu} has no USB of its own. Use the board's UART port.")
    if device.kind == "bridge":
        return Check("match", False, f"{device.bridge} adapter: mCode cannot see which board is "
                     f"behind it. Make sure it is your {board.name}.", "warning")
    return Check("match", False, f"Unknown USB serial device ({device.vid}:{device.pid}). Make "
                 f"sure it is your {board.name}.", "warning")


class UploadService:
    def __init__(self, studio):
        self.studio = studio
        self._lock = threading.Lock()
        self._plans: Dict[str, UploadPlan] = {}
        studio.devices.listeners.append(self._device_event)

    def _device_event(self, kind: str, device: Device) -> None:
        if kind == "added" and self.studio.monitor is not None:
            self.studio.monitor.device_added(device.port)

    # --------------------------------------------------------------- planning
    def prepare(self, project: Project, port: str = "", action: str = "upload",
                speed: int = 0) -> UploadPlan:
        devices = self.studio.devices
        device = devices.present(port) if port else devices.selected(project)
        board = project.board
        plan = UploadPlan(action, project.id, project.name, project.board_name,
                          device.port if device else (port or ""),
                          device.describe() if device else "", "",
                          speed=speed or project.upload_speed or
                          int(self.studio.settings.get("hw.upload_speed") or 0))
        checks = plan.checks
        # -- board and method
        if board is None:
            checks.append(Check("board", False, "The project has no known board."))
        elif action in ("erase", "identify") and board.flasher != "esptool":
            checks.append(Check("board", False, f"{'Erasing' if action == 'erase' else 'Chip detection'}"
                                f" works on ESP32 boards; this project is for {board.name}."))
        elif action == "upload" and board.flasher in ("", "none"):
            checks.append(Check("board", False, f"mCode has no upload method for {board.name}."))
        else:
            checks.append(Check("board", True, f"Board: {board.name}", "info"))
        # -- firmware
        backend = None
        try:
            backend = self.studio.builds.backend(project)
        except BackendError as exc:
            checks.append(Check("tool", False, str(exc)))
        if action == "upload" and backend is not None:
            artifact = backend.artifact(project)
            if not artifact:
                checks.append(Check("firmware", False, "Build the project first: there is no "
                                    "firmware yet."))
            elif not backend.up_to_date(project):
                checks.append(Check("firmware", False, "The source changed since the last build. "
                                    "Build again before uploading."))
            else:
                plan.firmware = os.path.relpath(artifact, project.path)
                size = os.path.getsize(artifact)
                checks.append(Check("firmware", True, f"Firmware: {plan.firmware} "
                                    f"({size // 1024} KB, built "
                                    f"{time.strftime('%H:%M', time.localtime(os.path.getmtime(artifact)))})",
                                    "info"))
            last = self.studio.builds.last(project.id)
            if last and last.get("action") in ("build", "rebuild") and last.get("state") != "succeeded":
                checks.append(Check("firmware", False, "The last build failed. Fix it and build "
                                    "again."))
        # -- device
        if device is None:
            checks.append(Check("device", False, "No board selected or the board is not "
                                "connected. Connect it and choose it in Connect Board."))
        else:
            checks.append(Check("device", True, f"Port: {device.port}", "info"))
            if not device.readable:
                checks.append(Check("permission", False, device.problem))
            holders = external_holders(device.port, self.studio.devices.proc)
            if holders:
                checks.append(Check("port", False, f"{device.short_port} is open in "
                                    f"{', '.join(holders)}. Close it there first."))
            owner = self.studio.ports.owner(device.port)
            if owner and owner != "the serial monitor":
                checks.append(Check("port", False, f"{device.short_port} is busy: {owner}."))
            elif owner == "the serial monitor":
                checks.append(Check("monitor", True, "The serial monitor pauses during the "
                                    "upload and reconnects afterwards.", "info"))
            if action in ("upload", "test-on-board", "erase", "identify") and board is not None:
                match = match_device(project, device) if action != "identify" else \
                    Check("match", True, "Chip detection reads the chip; it restarts the board.",
                          "info")
                checks.append(match)
        # -- upload command (tool installed, method known)
        if action == "upload" and backend is not None and device is not None:
            try:
                backend.upload(project, device.port, plan.speed,
                               bool(self.studio.settings.get("hw.verify_upload")))
            except BackendError as exc:
                checks.append(Check("tool", False, str(exc)))
        if action in ("erase", "identify"):
            try:
                backends.Esptool(self.studio.tools).program()
            except BackendError as exc:
                checks.append(Check("tool", False, str(exc)))
        if plan.ok:
            seed = f"{project.id}|{plan.port}|{plan.firmware}|{action}|{time.time()}|{os.urandom(8).hex()}"
            plan.token = hashlib.sha256(seed.encode()).hexdigest()[:24]
            with self._lock:
                self._expire()
                self._plans[plan.token] = plan
        return plan

    def _expire(self) -> None:
        now = time.time()
        for token in [t for t, p in self._plans.items() if now - p.created > TOKEN_TTL]:
            del self._plans[token]

    # ---------------------------------------------------------------- running
    def start(self, token: str, confirmed: bool) -> Job:
        """Run a prepared plan. ``confirmed`` must come from the user's own action."""
        if not confirmed:
            raise ProjectError("Uploading needs your confirmation.")
        with self._lock:
            self._expire()
            plan = self._plans.pop(token or "", None)
        if plan is None:
            raise ProjectError("This confirmation has expired. Check the board and confirm "
                               "again.")
        project = self.studio.projects.find(plan.project_id)
        fresh = self.prepare(project, plan.port, plan.action, plan.speed)
        with self._lock:
            self._plans.pop(fresh.token, None)
        if not fresh.ok:
            raise ProjectError(fresh.problem())
        if fresh.firmware != plan.firmware:
            raise ProjectError("The firmware changed after you confirmed. Confirm again.")
        return self._run(project, fresh)

    def _run(self, project: Project, plan: UploadPlan) -> Job:
        studio = self.studio
        port = plan.port
        monitor = studio.monitor

        def work(job: Job) -> dict:
            paused = monitor.suspend(port) if monitor is not None else False
            try:
                studio.ports.acquire(port, OWNER)
            except PortBusy as exc:
                raise JobFailed(str(exc))
            try:
                if plan.action == "upload":
                    backend = studio.builds.backend(project)
                    steps = backend.upload(project, port, plan.speed,
                                           bool(studio.settings.get("hw.verify_upload")))
                    for index, argv in enumerate(steps.steps):
                        job.set_phase(steps.labels[index] if index < len(steps.labels) else
                                      "Uploading")
                        code = job.run(argv, cwd=steps.cwd, env=steps.env, timeout=steps.timeout,
                                       check=False)
                        if code != 0:
                            report = diagnostics.parse(job.output(), project.path)
                            raise JobFailed(_upload_problem(job.output()) or report.headline() or
                                            f"upload failed (exit code {code})")
                    job.set_phase("Done", 1.0)
                    return {"verified": _verified(job.output())}
                if plan.action == "erase":
                    job.set_phase("Erasing the whole flash")
                    code = job.run(backends.Esptool(studio.tools).erase(port), check=False,
                                   timeout=float(studio.settings.get("dev.upload_timeout_s")))
                    if code != 0:
                        raise JobFailed(_upload_problem(job.output()) or "erase failed")
                    return {"erased": True}
                if plan.action == "identify":
                    job.set_phase("Reading the chip")
                    code = job.run(backends.Esptool(studio.tools).chip_id(port), check=False,
                                   timeout=60)
                    info = backends.parse_chip(job.output())
                    if code != 0 or not info.get("chip"):
                        raise JobFailed(_upload_problem(job.output()) or
                                        "esptool could not read the chip")
                    device = studio.devices.get(port)
                    if device is not None:
                        studio.devices.remember_chip(device, info)
                    return info
                raise JobFailed(f"unknown action {plan.action}")
            finally:
                studio.ports.release(port, OWNER)
                if paused and monitor is not None:
                    monitor.resume(present=lambda p: studio.devices.present(p) is not None)

        def done(job: Job) -> None:
            record = {"project_id": project.id, "project": project.name, "action": plan.action,
                      "state": job.state, "port": port, "device": plan.device,
                      "board": plan.board, "firmware": plan.firmware,
                      "elapsed": round(job.elapsed, 1), "error": job.error, "job": job.id,
                      "verified": job.result.get("verified"), "chip": job.result.get("chip", "")}
            studio.history.add(plan.action if plan.action != "upload" else "upload", **record)
            studio.events.publish("upload", record)

        title = {"upload": f"Flashing {project.name}", "erase": f"Erasing {os.path.basename(port)}",
                 "identify": f"Identifying {os.path.basename(port)}"}.get(plan.action, plan.action)
        return studio.jobs.submit(JobSpec(kind=plan.action, title=title, func=work, lane="upload",
                                          resource=os.path.realpath(port), project_id=project.id,
                                          timeout=float(studio.settings.get("dev.upload_timeout_s")) + 60,
                                          parser=_parser, on_done=done,
                                          meta={"project": project.name, "port": port,
                                                "board": plan.board}))

    def history(self, limit: int = 20, project_id: Optional[str] = None) -> list:
        return self.studio.history.recent(["upload", "erase", "identify"], project_id=project_id,
                                          limit=limit)


def _parser(job: Job, line: str) -> None:
    fraction = diagnostics.progress(line)
    if fraction is not None:
        job.progress = fraction
    name = diagnostics.phase(line)
    if name:
        job.phase = name


_UPLOAD_PROBLEMS = [
    ("Failed to connect to ESP32", "The board did not answer. Hold BOOT, press RESET (or replug "
                                   "while holding BOOT), then try again."),
    ("Wrong boot mode detected", "The board is not in download mode. Hold BOOT while it "
                                 "resets, then try again."),
    ("No serial data received", "The board did not answer. Hold BOOT, press RESET, then try "
                                "again."),
    ("Could not open", "The port could not be opened. Is the board still connected?"),
    ("Permission denied", "No permission to open the port: add your user to the 'dialout' group."),
    ("programmer is not responding", "The board did not answer the uploader. Check the board "
                                     "type (Nano: try the old bootloader) and the cable."),
    ("not in sync", "The board did not answer the uploader. Check the board type (Nano: try the "
                    "old bootloader) and the cable."),
    ("Device or resource busy", "The port is busy in another program."),
    ("No device found on", "No board found on that port."),
    ("Timed out waiting for packet header", "The board stopped answering. Try a lower upload "
                                            "speed or another cable."),
    ("does not match the chip", "The chip on the board is different from the project's board. "
                                "Change the project's board."),
    ("This chip is", "The chip on the board is different from the project's board. Change the "
                     "project's board."),
]


def _upload_problem(output: str) -> str:
    for needle, message in _UPLOAD_PROBLEMS:
        if needle.lower() in output.lower():
            return message
    return ""


def _verified(output: str) -> bool:
    text = output.lower()
    return ("hash of data verified" in text or "bytes of flash verified" in text
            or "verify successful" in text or "verified ok" in text)
