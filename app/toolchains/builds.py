"""Build, clean, rebuild, test and check projects with their real tools.

The single owner of build operations: the LCD, the desktop IDE and the AI
(through mCode's MCP tools) all start builds here. Each run is a job on the
"build" lane, so only one compiler runs at a time. When it ends, the output
is parsed into problems and sizes, recorded in the history and published
("build"), and the last result per project is kept for the AI context and
the dashboard.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
from typing import Callable, Dict, Optional

from ..core.history import History
from ..core.jobs import CANCELLED, SUCCEEDED, TIMEOUT, Job, JobFailed, JobManager, JobSpec
from ..projects.model import Project
from . import backends, detect, diagnostics, installer
from .backends import BackendError

log = logging.getLogger("mcode.builds")

ACTIONS = {"build": "Build", "rebuild": "Rebuild", "clean": "Clean", "test": "Test",
           "check": "Static analysis"}
_PIO_TESTS = re.compile(r"(?P<total>\d+) test cases?: (?:(?P<failed>\d+) failed, )?"
                        r"(?:(?P<ok>\d+) succeeded)?")
_UNITY = re.compile(r"(?P<total>\d+) Tests (?P<failed>\d+) Failures (?P<ignored>\d+) Ignored")


class BuildService:
    def __init__(self, jobs: JobManager, tools: detect.Toolchains, settings, history: History,
                 publish: Callable[[str, object], None] = lambda t, d: None,
                 paths=None):
        self.jobs = jobs
        self.tools = tools
        self.settings = settings
        self.history = history
        self.publish = publish
        self.paths = paths
        self._lock = threading.Lock()
        self._last: Dict[str, dict] = {}

    def backend(self, project: Project) -> backends.Backend:
        return backends.for_project(project, self.tools, self.settings)

    def requirements(self, project: Project) -> list:
        try:
            return self.backend(project).requirements(project)
        except BackendError as exc:
            return [{"id": "backend", "label": str(exc), "fix": exc.fix}]

    def up_to_date(self, project: Project) -> bool:
        try:
            return self.backend(project).up_to_date(project)
        except BackendError:
            return False

    def artifact(self, project: Project) -> str:
        try:
            return self.backend(project).artifact(project)
        except BackendError:
            return ""

    # -------------------------------------------------------------- running
    def run(self, project: Project, action: str = "build", source: str = "user") -> Job:
        """Start an action; raises BackendError (with a fix) if it cannot start."""
        if action not in ACTIONS:
            raise BackendError(f"Unknown action {action}.")
        backend = self.backend(project)
        if action in ("build", "rebuild", "test"):
            missing = backend.requirements(project)
            if missing:
                first = missing[0]
                raise BackendError(f"{first['label']} is needed first.", fix=first.get("fix", ""),
                                   fix_label=f"Install {first['label']}")
        if action == "build":
            plan = backend.build(project)
        elif action == "rebuild":
            plan = backend.build(project, clean=True)
        elif action == "clean":
            plan = backend.clean(project)
        elif action == "test":
            plan = backend.test(project)
        else:
            plan = backend.check(project)

        def work(job: Job) -> dict:
            return self._execute(job, project, action, plan)

        spec = JobSpec(kind=action, title=plan.what, func=work, cwd=plan.cwd,
                       timeout=plan.timeout, lane="build", project_id=project.id,
                       parser=_progress_parser,
                       on_done=lambda job: self._done(job, project, action, source),
                       meta={"project": project.name, "source": source,
                             "toolchain": project.toolchain})
        return self.jobs.submit(spec)

    def _execute(self, job: Job, project: Project, action: str, plan: backends.Plan) -> dict:
        for index, argv in enumerate(plan.steps):
            label = plan.labels[index] if index < len(plan.labels) else plan.what
            job.set_phase(label)
            code = job.run(argv, cwd=plan.cwd, env=plan.env, timeout=plan.timeout, check=False,
                           what=label)
            if code != 0:
                report = diagnostics.parse(job.output(), project.path)
                job.result["report"] = report.to_dict()
                job.result["tests"] = _tests(job.output(), action, failed=True)
                raise JobFailed(report.headline() or f"{label} failed (exit code {code})")
        for folder in plan.remove:
            real = os.path.realpath(folder)
            if real.startswith(os.path.realpath(project.path) + os.sep) and os.path.isdir(real):
                shutil.rmtree(real)
                job.say(f"removed {os.path.relpath(real, project.path)}")
        report = diagnostics.parse(job.output(), project.path)
        result = {"report": report.to_dict()}
        if action in ("build", "rebuild"):
            artifact = self.artifact(project)
            result["artifact"] = os.path.relpath(artifact, project.path) if artifact else ""
        if action == "test":
            result["tests"] = _tests(job.output(), action, failed=False)
        return result

    def _done(self, job: Job, project: Project, action: str, source: str) -> None:
        self._note_interruption(job, project, action)
        report = job.result.get("report") or {}
        record = {"project_id": project.id, "project": project.name, "action": action,
                  "state": job.state, "elapsed": round(job.elapsed, 1), "job": job.id,
                  "headline": report.get("headline", "") or job.error,
                  "errors": report.get("errors", 0), "warnings": report.get("warnings", 0),
                  "size": report.get("size", {}), "source": source,
                  "tests": job.result.get("tests"), "toolchain": project.toolchain,
                  "board": project.board_name}
        with self._lock:
            self._last[project.id] = dict(record, output_tail=job.tail(120),
                                          diagnostics=report.get("diagnostics", [])[:60],
                                          missing_headers=report.get("missing_headers", []),
                                          conflicts=report.get("conflicts", []))
        if action != "clean":
            self.history.add("build", **record)
        self.publish("build", record)

    def _note_interruption(self, job: Job, project: Project, action: str) -> None:
        """An Arduino CLI build that was stopped part-way makes the next one start
        clean (backends.ArduinoCliBackend.build); a finished one clears that."""
        if action not in ("build", "rebuild") or project.toolchain != backends.ARDUINO_CLI:
            return
        marker = backends.interrupted_marker(os.path.join(project.path, backends.BUILD_DIR))
        try:
            if job.state in (CANCELLED, TIMEOUT):
                os.makedirs(os.path.dirname(marker), exist_ok=True)
                with open(marker, "w") as handle:
                    handle.write(f"{job.state} {job.id}\n")
                log.info("%s: build %s; the next build starts clean", project.name, job.state)
            elif job.state == SUCCEEDED and os.path.exists(marker):
                os.unlink(marker)
        except OSError as exc:
            log.warning("%s: could not record the interrupted build: %s", project.name, exc)

    def last(self, project_id: str) -> Optional[dict]:
        with self._lock:
            found = self._last.get(project_id)
        if found:
            return dict(found)
        for record in self.history.recent(["build"], project_id=project_id, limit=1):
            return record
        return None

    def recent(self, limit: int = 10) -> list:
        return self.history.recent(["build"], limit=limit)

    # ------------------------------------------------------------ installers
    def install_tool(self, tool_id: str) -> Job:
        if tool_id not in detect.INSTALLABLE:
            raise BackendError(f"mCode cannot install {tool_id}.")
        paths = self.paths

        def work(job: Job) -> dict:
            if tool_id == detect.ARDUINO_CLI:
                result = installer.install_arduino_cli(job, paths.tools_bin)
                job.set_phase("Updating the board index")
                job.run([result["path"], "core", "update-index"], check=False,
                        what="arduino-cli core update-index")
            elif tool_id == detect.PLATFORMIO:
                result = installer.install_pip_package(job, paths.venv, "platformio",
                                                       self.settings.get("dev.python"))
            elif tool_id == detect.ESPTOOL:
                result = installer.install_pip_package(job, paths.venv, "esptool",
                                                       self.settings.get("dev.python"))
            elif tool_id == detect.ESP_IDF:
                result = installer.install_esp_idf(job, self.tools.home,
                                                   python=self.settings.get("dev.python")
                                                   or "python3")
                self.settings.set("dev.idf_path", result["path"])
            else:
                result = installer.install_claude_code(job)
            self.tools.refresh(wait=True, only=[tool_id])
            tool = self.tools.get(tool_id)
            if not tool.found:
                raise JobFailed(f"{detect.NAMES[tool_id]} was installed but cannot be found or "
                                f"run: {tool.problem}")
            job.say(f"{tool.name} {tool.version} at {tool.path}")
            result["version"] = tool.version
            return result

        info = installer.explain_install(tool_id) or {"title": f"Install {tool_id}"}
        return self.jobs.submit(JobSpec(kind="install", title=info["title"], func=work,
                                        lane="install",
                                        timeout=6 * 3600 if tool_id == detect.ESP_IDF else 3600,
                                        on_done=lambda job: self.publish("tools.installed",
                                                                         job.to_dict()),
                                        meta={"tool": tool_id}))

    def install_core(self, core: str) -> Job:
        backend = backends.ArduinoCliBackend(self.tools, self.settings)
        plan = backend.install_core(core)

        def work(job: Job) -> dict:
            for index, argv in enumerate(plan.steps):
                job.set_phase(plan.labels[index])
                job.run(argv, timeout=plan.timeout, what=plan.labels[index])
            return {"core": core}

        return self.jobs.submit(JobSpec(kind="install", title=plan.what, func=work, lane="install",
                                        timeout=plan.timeout, parser=_progress_parser,
                                        on_done=lambda job: self.publish("tools.installed",
                                                                         job.to_dict()),
                                        meta={"core": core}))


def _progress_parser(job: Job, line: str) -> None:
    fraction = diagnostics.progress(line)
    if fraction is not None:
        job.progress = fraction
    name = diagnostics.phase(line)
    if name:
        job.phase = name


def _tests(output: str, action: str, failed: bool) -> Optional[dict]:
    if action != "test":
        return None
    result = {"hardware": False, "summary": ""}
    match = None
    for match in _PIO_TESTS.finditer(output):
        pass
    if match:
        total = int(match.group("total"))
        bad = int(match.group("failed") or 0)
        result.update(total=total, failed=bad, passed=int(match.group("ok") or (total - bad)),
                      summary=match.group(0))
        return result
    unity = None
    for unity in _UNITY.finditer(output):
        pass
    if unity:
        total, bad = int(unity.group("total")), int(unity.group("failed"))
        result.update(total=total, failed=bad, passed=total - bad, summary=unity.group(0))
        return result
    if "all tests passed" in output:
        result.update(summary="all tests passed", failed=0)
    elif failed:
        result.update(summary="tests failed")
    else:
        result.update(summary="compilation check passed")
    return result
