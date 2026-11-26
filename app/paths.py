"""Where mCode Studio keeps its files.

* Code: the version folder mFruit OS replaces on every update
  (``WHISPLAY_OS_APP_DIR``, the working directory).
* Data: ``WHISPLAY_OS_APP_DATA`` (snapshotted by mFruit OS before each
  update), or ``~/.local/share/mcode`` when run on its own. Settings, the
  desktop access token, logs, AI workspaces, backups, the voice cache and
  tools installed by mCode live here. Sockets do not (see ``Paths.run``).
* Projects: ``~/mcode-projects`` by default (Settings > Development >
  Project folder). Deliberately *outside* the data folder: projects are the
  user's work, they can be large (build folders), and removing or updating
  the app must never touch them.
"""

from __future__ import annotations

import hashlib
import logging
import os
import stat
from typing import Mapping, Optional

from . import APP_ID

CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PROJECTS_DIR = "~/mcode-projects"

log = logging.getLogger("mcode.paths")


def default_data_dir(environ: Optional[Mapping[str, str]] = None) -> str:
    env = os.environ if environ is None else environ
    base = env.get("WHISPLAY_OS_APP_DATA")
    if base:
        return base
    xdg = env.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(xdg, APP_ID)


def under_mfruit_os(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True when mFruit OS (mfruit-run) started this process."""
    env = os.environ if environ is None else environ
    return bool(env.get("WHISPLAY_OS_APP_DATA") or env.get("MFRUIT_SESSION"))


def expand(path: str) -> str:
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path))) if path else ""


def runtime_base(environ: Optional[Mapping[str, str]] = None) -> str:
    """A private per-user folder for sockets: XDG_RUNTIME_DIR, else /tmp/mcode-<uid>."""
    env = os.environ if environ is None else environ
    base = env.get("XDG_RUNTIME_DIR") or ""
    if base and os.path.isdir(base):
        return base
    base = f"/tmp/mcode-{os.getuid()}"
    os.makedirs(base, mode=0o700, exist_ok=True)
    if os.stat(base).st_uid != os.getuid():
        raise OSError(f"{base} belongs to another user")
    os.chmod(base, 0o700)
    return base


class Paths:
    """Every file and folder mCode writes outside projects."""

    def __init__(self, data: Optional[str] = None, environ: Optional[Mapping[str, str]] = None):
        self.data = os.path.abspath(data or default_data_dir(environ))

    def _p(self, *parts: str) -> str:
        return os.path.join(self.data, *parts)

    @property
    def settings_file(self) -> str:
        return self._p("settings.json")

    @property
    def state_file(self) -> str:
        return self._p("state.json")

    @property
    def env_file(self) -> str:
        return self._p(".env")

    @property
    def web_token_file(self) -> str:
        return self._p("web-token")

    @property
    def logs(self) -> str:
        return self._p("logs")

    @property
    def job_logs(self) -> str:
        return self._p("logs", "jobs")

    @property
    def history_file(self) -> str:
        return self._p("logs", "history.jsonl")

    @property
    def ai_log(self) -> str:
        return self._p("logs", "ai-actions.jsonl")

    @property
    def serial_logs(self) -> str:
        return self._p("logs", "serial")

    @property
    def backups(self) -> str:
        return self._p("backups")

    @property
    def workspaces(self) -> str:
        return self._p("workspaces")

    @property
    def trash(self) -> str:
        return self._p("trash")

    @property
    def voice_cache(self) -> str:
        return self._p("voice-cache")

    @property
    def tools(self) -> str:
        return self._p("tools")

    @property
    def tools_bin(self) -> str:
        return self._p("tools", "bin")

    @property
    def venv(self) -> str:
        return self._p("tools", "venv")

    @property
    def run(self) -> str:
        """Sockets and other per-run files (owner-only).

        Deliberately *outside* the data folder: mFruit OS copies the data folder
        before every install and update, and ``shutil.copytree`` cannot copy a
        Unix socket. A socket left behind by an unclean exit therefore made every
        reinstall fail at "backup" (2026-11-27, .130 board).
        """
        digest = hashlib.sha256(self.data.encode()).hexdigest()[:12]
        return os.path.join(runtime_base(), f"mcode-{digest}")

    @property
    def legacy_run(self) -> str:
        """Where 0.1.0 kept its sockets (inside the data folder)."""
        return self._p("run")

    def remove_legacy_run(self) -> None:
        """Delete sockets 0.1.0 left in ``data/run`` and the folder when empty.

        Only sockets and an empty folder are removed, never other files.
        """
        folder = self.legacy_run
        try:
            names = os.listdir(folder)
        except OSError:
            return
        for name in names:
            path = os.path.join(folder, name)
            try:
                if stat.S_ISSOCK(os.lstat(path).st_mode):
                    os.unlink(path)
            except OSError as exc:
                log.warning("could not remove stale socket %s: %s", path, exc)
        try:
            os.rmdir(folder)
        except OSError:
            pass

    def ensure(self) -> "Paths":
        os.makedirs(self.data, mode=0o700, exist_ok=True)
        try:
            os.chmod(self.data, 0o700)
        except OSError:
            pass
        for folder in (self.logs, self.job_logs, self.serial_logs, self.backups, self.workspaces,
                       self.trash, self.voice_cache, self.tools_bin):
            os.makedirs(folder, mode=0o700, exist_ok=True)
        self.remove_legacy_run()
        os.makedirs(self.run, mode=0o700, exist_ok=True)
        return self
