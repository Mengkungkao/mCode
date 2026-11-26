"""Who is using a serial port.

Inside mCode, the PortCoordinator is the single owner of port access: the
serial monitor, uploads, erases and chip detection all acquire the port
first, so two of them can never talk to a board at the same time (an upload
pauses the monitor and resumes it afterwards). Outside mCode,
:func:`external_holders` finds other programs of this user that have the
port open (a terminal program, another IDE) so an upload can refuse with a
useful message instead of failing halfway.
"""

from __future__ import annotations

import os
import threading
from typing import Dict, List, Optional


class PortBusy(Exception):
    def __init__(self, port: str, owner: str):
        super().__init__(f"{os.path.basename(port)} is in use by {owner}.")
        self.port = port
        self.owner = owner


class PortCoordinator:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owners: Dict[str, str] = {}

    def acquire(self, port: str, owner: str) -> None:
        port = os.path.realpath(port)
        with self._lock:
            current = self._owners.get(port)
            if current and current != owner:
                raise PortBusy(port, current)
            self._owners[port] = owner

    def release(self, port: str, owner: str) -> None:
        port = os.path.realpath(port)
        with self._lock:
            if self._owners.get(port) == owner:
                del self._owners[port]

    def owner(self, port: str) -> Optional[str]:
        with self._lock:
            return self._owners.get(os.path.realpath(port))


def external_holders(port: str, proc: str = "/proc") -> List[str]:
    """["minicom (1234)", ...]: this user's other processes with ``port`` open."""
    try:
        target = os.path.realpath(port)
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return []
    me = os.getpid()
    found = []
    for pid in pids:
        if int(pid) == me:
            continue
        fd_dir = os.path.join(proc, pid, "fd")
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                if os.readlink(os.path.join(fd_dir, fd)) != target:
                    continue
            except OSError:
                continue
            try:
                with open(os.path.join(proc, pid, "comm")) as handle:
                    name = handle.read().strip()
            except OSError:
                name = "a program"
            found.append(f"{name} ({pid})")
            break
    return found
