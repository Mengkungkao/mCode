"""The job runner: every long operation (build, upload, test, install, chip
detection, library work) is a job.

A job is either a program (``argv``) or a Python function (``func``). Jobs
run off the interface threads; their output is streamed line by line to a
log file, an in-memory tail and the event bus ("job", "job.line"), so the
LCD and the desktop IDE show the same progress.

Rules this module enforces:

* **Lanes** bound resource use: one build at a time (compilers are heavy on a
  Pi), one upload at a time, one installer, two small tool jobs.
* **Resources** never overlap: two jobs naming the same resource (a serial
  port) never run together; the second waits in the queue.
* Programs run in their own process group, at lower CPU priority, with
  stdin closed. **Cancel** sends SIGTERM to the whole group, SIGKILL after
  3 s. A **timeout** does the same and marks the job "timeout".
* Output lines ending in a carriage return (progress bars) replace the
  previous carriage-return line instead of piling up.
"""

from __future__ import annotations

import codecs
import itertools
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("mcode.jobs")

QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED, TIMEOUT = (
    "queued", "running", "succeeded", "failed", "cancelled", "timeout")
FINISHED = (SUCCEEDED, FAILED, CANCELLED, TIMEOUT)
LANE_LIMITS = {"build": 1, "upload": 1, "install": 1, "tool": 2, "ai": 1}
KILL_GRACE = 3.0
TAIL_LINES = 4000


class JobCancelled(Exception):
    """Raised inside a Python job when it notices it was cancelled."""


class JobFailed(Exception):
    """Raised inside a Python job to fail it with a user-facing message."""


def _quote(arg: str) -> str:
    if arg and all(c.isalnum() or c in "-_./:=@,+%" for c in arg):
        return arg
    return "'" + arg.replace("'", "'\\''") + "'"


@dataclass
class JobSpec:
    kind: str                                   # build | clean | upload | test | install | ...
    title: str
    argv: Optional[List[str]] = None
    func: Optional[Callable[["Job"], Any]] = None
    cwd: Optional[str] = None
    env: Dict[str, str] = field(default_factory=dict)
    timeout: float = 600.0
    lane: str = "build"
    resource: str = ""
    project_id: str = ""
    parser: Optional[Callable[["Job", str], None]] = None
    on_done: Optional[Callable[["Job"], None]] = None
    nice: int = 10
    meta: Dict[str, Any] = field(default_factory=dict)


class Job:
    def __init__(self, job_id: str, spec: JobSpec, log_path: str):
        self.id = job_id
        self.spec = spec
        self.kind = spec.kind
        self.title = spec.title
        self.state = QUEUED
        self.created = time.time()
        self.started = 0.0
        self.ended = 0.0
        self.exit_code: Optional[int] = None
        self.progress: Optional[float] = None   # 0..1 when the tool reports it
        self.phase = "Waiting"
        self.error = ""
        self.result: Dict[str, Any] = {}
        self.lines: deque = deque(maxlen=TAIL_LINES)
        self.log_path = log_path
        self.cancel_event = threading.Event()
        self.done_event = threading.Event()
        self._process: Optional[subprocess.Popen] = None
        self._cr = False                        # last line ended with a carriage return
        self._log = None

    # ---------------------------------------------------------------- views
    @property
    def finished(self) -> bool:
        return self.state in FINISHED

    @property
    def ok(self) -> bool:
        return self.state == SUCCEEDED

    @property
    def elapsed(self) -> float:
        if not self.started:
            return 0.0
        return (self.ended or time.time()) - self.started

    def tail(self, count: int = 40) -> List[str]:
        lines = list(self.lines)
        return lines[-count:] if count else lines

    def output(self) -> str:
        return "\n".join(self.lines)

    def to_dict(self, lines: int = 0) -> dict:
        data = {"id": self.id, "kind": self.kind, "title": self.title, "state": self.state,
                "created": self.created, "started": self.started, "ended": self.ended,
                "elapsed": round(self.elapsed, 1), "exit_code": self.exit_code,
                "progress": self.progress, "phase": self.phase, "error": self.error,
                "project_id": self.spec.project_id, "resource": self.spec.resource,
                "result": self.result, "meta": self.spec.meta}
        if lines:
            data["lines"] = self.tail(lines)
        return data

    # ------------------------------------------------- used by job functions
    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise JobCancelled()

    def set_phase(self, phase: str, progress: Optional[float] = None) -> None:
        self.phase = phase
        if progress is not None:
            self.progress = max(0.0, min(1.0, progress))
        if self._manager is not None:
            self._manager._changed(self, force=True)

    def say(self, line: str) -> None:
        """Add an output line (Python jobs)."""
        self._add_line(line, cr=False)

    def run(self, argv: List[str], cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None,
            timeout: Optional[float] = None, check: bool = True, what: str = "",
            show: bool = True) -> int:
        """Run a program as one step of this (Python) job; streams its output.
        ``show=False`` keeps the command line out of the output (it may hold a long prompt)."""
        try:
            code, timed_out = self._manager.execute(self, argv, cwd, env, timeout, show=show)
        except OSError as exc:
            raise JobFailed(f"cannot start {os.path.basename(argv[0])}: {exc.strerror or exc}") \
                from None
        if self.cancel_event.is_set():
            raise JobCancelled()
        if timed_out:
            raise JobFailed(f"{what or os.path.basename(argv[0])} took longer than "
                            f"{int(timeout or self.spec.timeout)} s and was stopped")
        if check and code != 0:
            raise JobFailed(f"{what or os.path.basename(argv[0])} failed (exit code {code})")
        return code

    _manager: Optional["JobManager"] = None

    def _add_line(self, line: str, cr: bool) -> None:
        if self._cr and self.lines:
            self.lines.pop()
        self.lines.append(line)
        self._cr = cr
        if self._log is not None and not cr:
            try:
                self._log.write(line + "\n")
            except (OSError, ValueError):
                pass
        if self.spec.parser is not None:
            try:
                self.spec.parser(self, line)
            except Exception:       # a parser bug must not kill the job
                log.exception("output parser failed for %s", self.id)
        if self._manager is not None:
            self._manager._line(self, line, cr)


class JobManager:
    def __init__(self, log_dir: str, publish: Callable[[str, Any], None] = lambda t, d: None,
                 base_env: Optional[Dict[str, str]] = None, extra_path: Optional[List[str]] = None,
                 lane_limits: Optional[Dict[str, int]] = None, history: int = 200):
        self.log_dir = log_dir
        self.publish = publish
        self.base_env = base_env
        self.extra_path = list(extra_path or [])
        self.lane_limits = dict(LANE_LIMITS, **(lane_limits or {}))
        self._lock = threading.RLock()
        self._jobs: Dict[str, Job] = {}
        self._order: deque = deque(maxlen=history)
        self._queue: List[Job] = []
        self._ids = itertools.count(1)
        self._last_publish: Dict[str, float] = {}
        self._closed = False
        os.makedirs(log_dir, mode=0o700, exist_ok=True)

    # ------------------------------------------------------------ public api
    def submit(self, spec: JobSpec) -> Job:
        if (spec.argv is None) == (spec.func is None):
            raise ValueError("a job needs exactly one of argv or func")
        with self._lock:
            if self._closed:
                raise RuntimeError("mCode is shutting down")
            job_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{next(self._ids)}"
            job = Job(job_id, spec, os.path.join(self.log_dir, f"{job_id}-{spec.kind}.log"))
            job._manager = self
            self._jobs[job_id] = job
            self._order.append(job_id)
            self._queue.append(job)
        log.info("job %s queued: %s", job.id, spec.title)
        self._changed(job, force=True)
        self._schedule()
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, kind: Optional[str] = None, project_id: Optional[str] = None,
               limit: int = 50) -> List[Job]:
        with self._lock:
            jobs = [self._jobs[i] for i in reversed(self._order) if i in self._jobs]
        if kind:
            jobs = [j for j in jobs if j.kind == kind]
        if project_id:
            jobs = [j for j in jobs if j.spec.project_id == project_id]
        return jobs[:limit]

    def active(self) -> List[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if not j.finished]

    def busy(self, resource: str) -> Optional[Job]:
        with self._lock:
            for job in self._jobs.values():
                if not job.finished and resource and job.spec.resource == resource:
                    return job
        return None

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None or job.finished:
            return False
        job.cancel_event.set()
        with self._lock:
            if job in self._queue:
                self._queue.remove(job)
                self._finish(job, CANCELLED, None, "cancelled before it started")
                return True
            process = job._process
        if process is not None:
            self._kill_group(process)
        log.info("job %s cancel requested", job.id)
        return True

    def wait(self, job_id: str, timeout: Optional[float] = None) -> Optional[Job]:
        job = self.get(job_id)
        if job is None:
            return None
        job.done_event.wait(timeout)
        return job

    def shutdown(self, timeout: float = 2.0) -> None:
        """Cancel everything (mCode is leaving); waits up to ``timeout``."""
        with self._lock:
            self._closed = True
            jobs = [j for j in self._jobs.values() if not j.finished]
        for job in jobs:
            self.cancel(job.id)
        deadline = time.monotonic() + timeout
        for job in jobs:
            job.done_event.wait(max(0.0, deadline - time.monotonic()))

    # ------------------------------------------------------------- scheduling
    def _schedule(self) -> None:
        to_start = []
        with self._lock:
            running = [j for j in self._jobs.values() if j.state == RUNNING]
            for job in list(self._queue):
                lane = job.spec.lane
                in_lane = sum(1 for j in running if j.spec.lane == lane)
                if in_lane >= self.lane_limits.get(lane, 1):
                    continue
                if job.spec.resource and any(j.spec.resource == job.spec.resource
                                             for j in running):
                    continue
                self._queue.remove(job)
                job.state = RUNNING
                job.started = time.time()
                job.phase = "Starting"
                running.append(job)
                to_start.append(job)
        for job in to_start:
            threading.Thread(target=self._run, args=(job,), name=f"job-{job.kind}",
                             daemon=True).start()

    def _env(self, spec: JobSpec) -> Dict[str, str]:
        env = dict(os.environ if self.base_env is None else self.base_env)
        if self.extra_path:
            env["PATH"] = os.pathsep.join(self.extra_path + [env.get("PATH", "")])
        env.update(spec.env)
        env.setdefault("PYTHONUNBUFFERED", "1")
        return env

    def _run(self, job: Job) -> None:
        self._changed(job, force=True)
        try:
            job._log = open(job.log_path, "a", encoding="utf-8")
            os.chmod(job.log_path, 0o600)
        except OSError as exc:
            log.warning("cannot write job log %s: %s", job.log_path, exc)
            job._log = None
        if job._log is not None:
            job._log.write(f"# {job.title}\n# started {time.ctime(job.started)}\n")
            if job.spec.argv:
                job._log.write("# $ " + " ".join(job.spec.argv) + "\n")
        try:
            if job.spec.func is not None:
                self._run_func(job)
            else:
                self._run_process(job)
        except Exception as exc:    # isolation boundary: report, never lose the job
            log.exception("job %s crashed", job.id)
            self._finish(job, FAILED, None, f"{exc.__class__.__name__}: {exc}")
        self._schedule()

    def _run_func(self, job: Job) -> None:
        try:
            value = job.spec.func(job)
        except JobCancelled:
            self._finish(job, CANCELLED, None, "cancelled")
            return
        except Exception as exc:
            if job.cancel_event.is_set():
                self._finish(job, CANCELLED, None, "cancelled")
                return
            log.warning("job %s failed: %s", job.id, exc)
            job.say(f"error: {exc}")
            self._finish(job, FAILED, None, str(exc))
            return
        if isinstance(value, dict):
            job.result.update(value)
        if job.cancel_event.is_set():
            self._finish(job, CANCELLED, None, "cancelled")
        else:
            self._finish(job, SUCCEEDED, 0, "")

    def _run_process(self, job: Job) -> None:
        if job.cancel_event.is_set():
            self._finish(job, CANCELLED, None, "cancelled")
            return
        try:
            code, timed_out = self.execute(job, list(job.spec.argv or []), job.spec.cwd,
                                           job.spec.env, job.spec.timeout)
        except OSError as exc:
            self._finish(job, FAILED, None, f"cannot start {os.path.basename(job.spec.argv[0])}: "
                                            f"{exc.strerror or exc}")
            return
        if timed_out:
            self._finish(job, TIMEOUT, code, f"stopped after {int(job.spec.timeout)} s")
        elif job.cancel_event.is_set():
            self._finish(job, CANCELLED, code, "cancelled")
        elif code == 0:
            self._finish(job, SUCCEEDED, code, "")
        else:
            self._finish(job, FAILED, code, job.error or f"exit code {code}")

    def execute(self, job: Job, argv: List[str], cwd: Optional[str] = None,
                env: Optional[Dict[str, str]] = None, timeout: Optional[float] = None,
                show: bool = True) -> tuple:
        """Run one program inside ``job``, streaming its output into the job.

        Returns (exit code, timed out). Raises OSError if it cannot start and
        JobCancelled if the job was cancelled before it started.
        """
        job.check_cancelled()
        program = argv[0] if argv else ""
        found = (os.path.isfile(program) and os.access(program, os.X_OK)) if os.sep in program \
            else bool(shutil.which(program, path=self._env(JobSpec(kind="", title="",
                                                                    env=dict(env or {}))).get("PATH")))
        if not found:
            job.say(f"cannot start {program}: not found")
            raise FileNotFoundError(2, "No such file or directory", program)
        if job.spec.nice and shutil.which("nice"):
            argv = ["nice", "-n", str(job.spec.nice)] + argv
        spec_env = JobSpec(kind="", title="", env=dict(env or {}))
        if show:
            job.say("$ " + " ".join(_quote(a) for a in (argv[3:] if argv[:1] == ["nice"] else argv)))
        try:
            process = subprocess.Popen(argv, cwd=cwd or None, env=self._env(spec_env),
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, start_new_session=True,
                                       bufsize=0)
        except OSError as exc:
            job.say(f"cannot start {argv[0]}: {exc.strerror or exc}")
            raise
        with self._lock:
            job._process = process
        if job.cancel_event.is_set():
            self._kill_group(process)
        if job.phase in ("Starting", "Waiting"):
            job.phase = "Running"
        self._changed(job, force=True)
        timed_out = threading.Event()
        finished = threading.Event()
        limit = timeout or job.spec.timeout

        def watchdog():
            if not finished.wait(limit):
                if process.poll() is None:
                    timed_out.set()
                    job.say(f"mcode: stopped after the {int(limit)} s time limit")
                    self._kill_group(process)

        threading.Thread(target=watchdog, name="job-watchdog", daemon=True).start()
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        pending = ""
        while True:
            try:
                chunk = os.read(process.stdout.fileno(), 4096)
            except OSError:
                chunk = b""
            if not chunk:
                break
            pending += decoder.decode(chunk)
            pending = self._split(job, pending)
        pending += decoder.decode(b"", final=True)
        if pending:
            job._add_line(pending.rstrip("\r\n"), cr=False)
        code = process.wait()
        finished.set()
        try:
            process.stdout.close()
        except OSError:
            pass
        with self._lock:
            job._process = None
        return code, timed_out.is_set()

    @staticmethod
    def _split(job: Job, text: str) -> str:
        start = 0
        index = 0
        length = len(text)
        while index < length:
            ch = text[index]
            if ch == "\n":
                job._add_line(text[start:index].rstrip("\r"), cr=False)
                start = index + 1
            elif ch == "\r":
                if index + 1 < length and text[index + 1] == "\n":
                    index += 1
                    continue
                if index + 1 >= length:
                    break           # wait: a "\n" may follow in the next chunk
                job._add_line(text[start:index], cr=True)
                start = index + 1
            index += 1
        return text[start:]

    def _kill_group(self, process: subprocess.Popen) -> None:
        def term():
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(process.pid, sig)
                except (ProcessLookupError, PermissionError, OSError):
                    return
                try:
                    process.wait(timeout=KILL_GRACE)
                    return
                except subprocess.TimeoutExpired:
                    continue
        threading.Thread(target=term, name="job-kill", daemon=True).start()

    def _finish(self, job: Job, state: str, code: Optional[int], error: str) -> None:
        with self._lock:
            if job.finished:
                return
            job.state = state
            job.exit_code = code
            job.ended = time.time()
            if error and not job.error:
                job.error = error
            job.phase = {SUCCEEDED: "Done", FAILED: "Failed", CANCELLED: "Cancelled",
                         TIMEOUT: "Timed out"}[state]
            if state == SUCCEEDED and job.progress is not None:
                job.progress = 1.0
            job._process = None
        if job.spec.on_done is not None:
            try:
                job.spec.on_done(job)
            except Exception:       # isolation boundary
                log.exception("job %s completion handler failed", job.id)
        if job._log is not None:
            try:
                job._log.write(f"# {job.state} after {job.elapsed:.1f} s"
                               f"{f' (exit {code})' if code is not None else ''}\n")
                job._log.close()
            except (OSError, ValueError):
                pass
            job._log = None
        log.info("job %s %s (%s)%s", job.id, job.state, job.title,
                 f": {job.error}" if job.error and state != SUCCEEDED else "")
        job.done_event.set()
        self._changed(job, force=True)

    # ---------------------------------------------------------------- events
    def _changed(self, job: Job, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_publish.get(job.id, 0.0) < 0.2:
            return
        self._last_publish[job.id] = now
        self.publish("job", job.to_dict())

    def _line(self, job: Job, line: str, cr: bool) -> None:
        self.publish("job.line", {"id": job.id, "line": line, "cr": cr})
        self._changed(job)
