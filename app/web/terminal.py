"""Integrated terminal sessions for the desktop IDE (a real shell in a PTY).

The shell runs as the device user in the project folder. Output is kept in a
bounded buffer and streamed to the browser; input and window size come
from the browser. Sessions end when the shell exits, when the browser closes
them, or when mCode stops (the whole process group is killed).
"""

from __future__ import annotations

import fcntl
import itertools
import os
import pty
import select
import signal
import struct
import termios
import threading
from typing import Callable, Dict, Optional

MAX_SESSIONS = 4
BUFFER = 256 * 1024


class TerminalSession:
    def __init__(self, session_id: str, cwd: str, on_output: Callable[[str, bytes], None],
                 shell: Optional[str] = None):
        self.id = session_id
        self.cwd = cwd
        self.on_output = on_output
        self.buffer = bytearray()
        self.offset = 0
        self.alive = True
        shell = shell or os.environ.get("SHELL") or "/bin/bash"
        if not os.path.exists(shell):
            shell = "/bin/sh"
        pid, fd = pty.fork()
        if pid == 0:
            try:
                os.chdir(cwd)
            except OSError:
                pass
            env = dict(os.environ)
            env["TERM"] = "xterm-256color"
            env["MCODE_TERMINAL"] = "1"
            try:
                os.execvpe(shell, [shell, "-i"], env)
            finally:
                os._exit(127)
        self.pid = pid
        self.fd = fd
        self._thread = threading.Thread(target=self._read, name="terminal", daemon=True)
        self._thread.start()

    def _read(self) -> None:
        while self.alive:
            try:
                ready, _, _ = select.select([self.fd], [], [], 1.0)
            except (OSError, ValueError):
                break
            if not ready:
                continue
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                break
            if not data:
                break
            self.buffer.extend(data)
            if len(self.buffer) > BUFFER:
                drop = len(self.buffer) - BUFFER
                del self.buffer[:drop]
                self.offset += drop
            self.on_output(self.id, data)
        self.alive = False
        self.on_output(self.id, b"")
        try:
            os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            pass

    def write(self, data: bytes) -> None:
        if self.alive:
            os.write(self.fd, data)

    def resize(self, cols: int, rows: int) -> None:
        cols = max(10, min(500, int(cols)))
        rows = max(4, min(200, int(rows)))
        try:
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except OSError:
            pass

    def close(self) -> None:
        self.alive = False
        for sig in (signal.SIGHUP, signal.SIGKILL):
            try:
                os.killpg(os.getpgid(self.pid), sig)
            except (OSError, ProcessLookupError):
                break
        try:
            os.close(self.fd)
        except OSError:
            pass


class Terminals:
    def __init__(self, publish: Callable[[str, object], None]):
        self.publish = publish
        self._lock = threading.Lock()
        self._sessions: Dict[str, TerminalSession] = {}
        self._ids = itertools.count(1)

    def open(self, cwd: str) -> TerminalSession:
        with self._lock:
            alive = {k: s for k, s in self._sessions.items() if s.alive}
            self._sessions = alive
            if len(alive) >= MAX_SESSIONS:
                raise RuntimeError("Close a terminal first (at most four).")
            session_id = f"t{next(self._ids)}"
            session = TerminalSession(session_id, cwd, self._output)
            self._sessions[session_id] = session
            return session

    def _output(self, session_id: str, data: bytes) -> None:
        import base64
        self.publish("terminal", {"id": session_id, "data": base64.b64encode(data).decode(),
                                  "closed": not data})

    def get(self, session_id: str) -> Optional[TerminalSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def close(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is not None:
            session.close()

    def close_all(self) -> None:
        with self._lock:
            sessions, self._sessions = list(self._sessions.values()), {}
        for session in sessions:
            session.close()
