"""Open the desktop IDE on the HDMI desktop, when there is one.

Started by mFruit OS, mCode runs under whisplay-daemon's service and has no
DISPLAY of its own. Like mDisplay (whose session finder this follows), it
looks at this user's own processes in a graphical session for DISPLAY /
WAYLAND_DISPLAY / XAUTHORITY, verifies the display socket exists, and starts
the browser there. Nothing here needs root, and nothing is drawn by mCode
itself on HDMI: the browser does that.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Dict, Optional, Tuple

SESSION_KEYS = ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR",
                "DBUS_SESSION_BUS_ADDRESS", "XDG_SESSION_TYPE")
MAX_PROCESSES = 4000


def _x_socket(display: str) -> bool:
    if not display.startswith(":"):
        return bool(display)
    number = display[1:].split(".")[0]
    return os.path.exists(f"/tmp/.X11-unix/X{number}")


def _wayland_socket(env: Dict[str, str]) -> bool:
    name = env.get("WAYLAND_DISPLAY", "")
    runtime = env.get("XDG_RUNTIME_DIR", "")
    if not name:
        return False
    return os.path.exists(name if os.path.isabs(name) else os.path.join(runtime, name))


def find_session(environ: Optional[Dict[str, str]] = None, proc: str = "/proc") -> Optional[Dict[str, str]]:
    """Environment variables of this user's graphical session, or None."""
    env = dict(os.environ if environ is None else environ)
    if _wayland_socket(env) or (env.get("DISPLAY") and _x_socket(env["DISPLAY"])):
        return {k: env[k] for k in SESSION_KEYS if k in env}
    uid = os.getuid()
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return None
    for pid in pids[:MAX_PROCESSES]:
        base = os.path.join(proc, pid)
        try:
            if os.stat(base).st_uid != uid:
                continue
            with open(os.path.join(base, "environ"), "rb") as handle:
                raw = handle.read(256 * 1024)
        except OSError:
            continue
        values = {}
        for item in raw.split(b"\0"):
            key, sep, value = item.partition(b"=")
            name = key.decode("ascii", "replace")
            if sep and name in SESSION_KEYS:
                values[name] = value.decode("utf-8", "replace")
        if _wayland_socket(values) or (values.get("DISPLAY") and _x_socket(values["DISPLAY"])):
            return values
    return None


def browser_command(url: str) -> Optional[list]:
    for name in ("chromium-browser", "chromium", "google-chrome"):
        path = shutil.which(name)
        if path:
            return [path, f"--app={url}", "--new-window"]
    for name in ("xdg-open", "sensible-browser", "firefox"):
        path = shutil.which(name)
        if path:
            return [path, url]
    return None


def open_url(url: str, session: Optional[Dict[str, str]] = None) -> Tuple[bool, str]:
    session = session if session is not None else find_session()
    if session is None:
        return False, "No desktop is running on the HDMI screen."
    command = browser_command(url)
    if command is None:
        return False, "No web browser is installed on this device."
    env = dict(os.environ)
    env.update(session)
    try:
        subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as exc:
        return False, f"Could not start the browser: {exc.strerror}"
    return True, ""
