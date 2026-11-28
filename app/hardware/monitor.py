"""The serial monitor: one live connection to a board's serial port.

* Connect / disconnect at a chosen baud rate (the port is acquired from the
  PortCoordinator, so an upload cannot write to it at the same time).
* Incoming bytes are kept twice: as timestamped text lines (ASCII view,
  search, saving) and as raw bytes (hex view), both bounded.
* Pause freezes what the interfaces show while bytes keep arriving (so the
  board's output is not lost in the OS buffer); resume shows everything.
* ``suspend``/``resume`` are for uploads: the port is closed, the session
  remembered, and reopened afterwards (waiting for a board that re-appears
  after a reset, as native-USB boards do).

Interfaces get "monitor" events (new lines, batched) and read the buffers.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import deque
from typing import Callable, List, Optional

from .ports import PortBusy, PortCoordinator
from .serialport import SerialError, SerialPort

log = logging.getLogger("mcode.monitor")

MAX_LINES = 5000
MAX_RAW = 256 * 1024
LINE_ENDINGS = {"none": b"", "lf": b"\n", "cr": b"\r", "crlf": b"\r\n"}
OWNER = "the serial monitor"


class Line:
    __slots__ = ("t", "text", "direction", "n")

    def __init__(self, t: float, text: str, direction: str, n: int):
        self.t = t
        self.text = text
        self.direction = direction      # rx | tx | info
        self.n = n

    def to_dict(self) -> dict:
        return {"t": self.t, "text": self.text, "dir": self.direction, "n": self.n}


class SerialMonitor:
    def __init__(self, ports: PortCoordinator, log_dir: str,
                 publish: Callable[[str, object], None] = lambda t, d: None,
                 opener: Callable[[str, int], SerialPort] = None):
        self.ports = ports
        self.log_dir = log_dir
        self.publish = publish
        self.opener = opener or (lambda path, baud: SerialPort(path, baud).open())
        self._lock = threading.RLock()
        self.port: Optional[str] = None
        self.baud = 115200
        self.connected = False
        self.paused = False
        self.suspended = False
        self.error = ""
        self.lines: deque = deque(maxlen=MAX_LINES)
        self.raw = bytearray()
        self.raw_offset = 0                 # bytes dropped from the front of `raw`
        self.rx_bytes = 0
        self._serial: Optional[SerialPort] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._wake_r, self._wake_w = os.pipe()
        self._partial = ""
        self._partial_t = 0.0
        self._counter = 0
        self._last_publish = 0.0
        self._pending: List[dict] = []

    # ------------------------------------------------------------- connection
    def connect(self, port: str, baud: int = 115200) -> None:
        with self._lock:
            if self.connected and self.port == port and self.baud == baud:
                return
            self.disconnect()
            self.ports.acquire(port, OWNER)
            try:
                serial = self.opener(port, int(baud))
            except SerialError as exc:
                self.ports.release(port, OWNER)
                self.error = str(exc)
                self._publish_state()
                raise
            self._serial = serial
            self.port = port
            self.baud = int(baud)
            self.connected = True
            self.suspended = False
            self.error = ""
            self._stop.clear()
            self._info(f"connected to {os.path.basename(port)} at {baud} baud")
            self._thread = threading.Thread(target=self._read_loop, args=(serial,),
                                            name="serial-monitor", daemon=True)
            self._thread.start()
        self._publish_state()

    def disconnect(self, keep_session: bool = False) -> None:
        with self._lock:
            serial, self._serial = self._serial, None
            thread, self._thread = self._thread, None
            was = self.connected
            self.connected = False
            self._stop.set()
            try:
                os.write(self._wake_w, b"x")
            except OSError:
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        if serial is not None:
            serial.close()
        try:
            while os.read(self._wake_r, 64) if _readable(self._wake_r) else b"":
                pass
        except OSError:
            pass
        if self.port:
            self.ports.release(self.port, OWNER)
        if was and not keep_session:
            self._flush_partial()
            self._info("disconnected")
            self.suspended = False
        self._publish_state()

    def close_all(self) -> None:
        self.disconnect()

    def suspend(self, port: str) -> bool:
        """Close the port for an upload; True if a session was open on it."""
        with self._lock:
            if not self.connected or os.path.realpath(self.port or "") != os.path.realpath(port):
                return False
            self._info("paused for upload")
        self.disconnect(keep_session=True)
        self.suspended = True
        self._publish_state()
        return True

    def resume(self, present: Optional[Callable[[str], bool]] = None) -> bool:
        """Reopen after an upload. A native-USB board re-enumerates after its reset:
        if the port is not back yet, the session stays suspended and
        :meth:`device_added` reconnects when the hot-plug event arrives."""
        if not self.suspended or not self.port:
            return False
        if present is not None and not present(self.port):
            self._info("waiting for the board to come back")
            self._publish_state()
            return False
        try:
            self.connect(self.port, self.baud)
            return True
        except (SerialError, PortBusy) as exc:
            self.error = str(exc)
            self._info(f"could not reconnect: {self.error}")
            self._publish_state()
            return False

    def device_added(self, port: str) -> None:
        """Hot-plug: the suspended session's port is back."""
        if self.suspended and self.port and os.path.realpath(port) == os.path.realpath(self.port):
            try:
                self.connect(self.port, self.baud)
            except (SerialError, PortBusy) as exc:
                self.error = str(exc)
                self._publish_state()

    # ---------------------------------------------------------------- reading
    def _read_loop(self, serial: SerialPort) -> None:
        while not self._stop.is_set():
            try:
                data = serial.read(0.1, wake_fd=self._wake_r)
            except SerialError as exc:
                if self._stop.is_set():
                    return
                log.info("monitor: %s", exc)
                with self._lock:
                    self.error = str(exc)
                    port = self.port
                self._info(str(exc))
                threading.Thread(target=self.disconnect, kwargs={"keep_session": True},
                                 daemon=True).start()
                self.suspended = bool(port)
                return
            if data:
                self._received(data)
            elif self._partial and time.time() - self._partial_t > 0.15:
                self._flush_partial()
                self._maybe_publish(force=True)

    def _received(self, data: bytes) -> None:
        now = time.time()
        with self._lock:
            self.rx_bytes += len(data)
            self.raw.extend(data)
            if len(self.raw) > MAX_RAW:
                drop = len(self.raw) - MAX_RAW
                del self.raw[:drop]
                self.raw_offset += drop
            text = self._partial + data.decode("utf-8", "replace")
            if not self._partial:
                self._partial_t = now
            parts = re.split(r"\r\n|\n|\r", text)
            for part in parts[:-1]:
                self._add(Line(self._partial_t or now, part, "rx", 0))
                self._partial_t = now
            self._partial = parts[-1]
            if len(self._partial) > 2000:
                self._add(Line(self._partial_t, self._partial, "rx", 0))
                self._partial = ""
        self._maybe_publish()

    def _flush_partial(self) -> None:
        with self._lock:
            if self._partial:
                self._add(Line(self._partial_t, self._partial, "rx", 0))
                self._partial = ""

    def _add(self, line: Line) -> None:
        self._counter += 1
        line.n = self._counter
        self.lines.append(line)
        self._pending.append(line.to_dict())

    def _info(self, text: str) -> None:
        with self._lock:
            self._add(Line(time.time(), text, "info", 0))
        self._maybe_publish(force=True)

    def _maybe_publish(self, force: bool = False) -> None:
        now = time.monotonic()
        with self._lock:
            if self.paused:
                # Keep what arrives while the view is frozen (bounded like the buffer);
                # resume publishes it, so nothing goes missing in the interfaces.
                if len(self._pending) > MAX_LINES:
                    del self._pending[:len(self._pending) - MAX_LINES]
                return
            if not self._pending or (not force and now - self._last_publish < 0.1):
                return
            batch, self._pending = self._pending, []
            self._last_publish = now
        self.publish("monitor", {"lines": batch, "port": self.port})

    def _publish_state(self) -> None:
        self.publish("monitor.state", self.status())

    # -------------------------------------------------------------- sending
    def send(self, text: str, ending: str = "lf", hex_mode: bool = False) -> int:
        with self._lock:
            serial = self._serial
        if serial is None:
            raise SerialError("Connect to a port first.")
        if hex_mode:
            cleaned = re.sub(r"[^0-9a-fA-F]", "", text)
            if len(cleaned) % 2:
                raise SerialError("Hex needs pairs of digits, e.g. 0A FF 10.")
            data = bytes.fromhex(cleaned)
        else:
            data = text.encode("utf-8") + LINE_ENDINGS.get(ending, b"\n")
        count = serial.write(data)
        with self._lock:
            self._add(Line(time.time(), text if not hex_mode else data.hex(" "), "tx", 0))
        self._maybe_publish(force=True)
        return count

    def reset_board(self) -> None:
        """Pulse the reset line the way esptool's hard reset does (RTS = EN on
        ESP32 boards, DTR on Arduino boards with auto-reset)."""
        with self._lock:
            serial = self._serial
        if serial is None:
            raise SerialError("Connect to a port first.")
        serial.set_lines(dtr=False, rts=True)
        time.sleep(0.1)          # the reset pulse width itself, not a race fix
        serial.set_lines(dtr=False, rts=False)
        self._info("reset pulse sent")

    # ---------------------------------------------------------------- views
    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self.paused = bool(paused)
        self._publish_state()
        if not paused:
            self._maybe_publish(force=True)

    def clear(self) -> None:
        with self._lock:
            self.lines.clear()
            self.raw = bytearray()
            self.raw_offset = 0
            self._pending = []
        self.publish("monitor.cleared", {})

    def status(self) -> dict:
        return {"port": self.port, "baud": self.baud, "connected": self.connected,
                "paused": self.paused, "suspended": self.suspended, "error": self.error,
                "rx_bytes": self.rx_bytes, "lines": len(self.lines)}

    def recent(self, count: int = 200, since: int = 0) -> List[dict]:
        with self._lock:
            lines = [l.to_dict() for l in self.lines if l.n > since]
        return lines[-count:]

    def text(self, count: int = 200, timestamps: bool = False) -> str:
        out = []
        for line in self.recent(count):
            prefix = time.strftime("%H:%M:%S", time.localtime(line["t"])) + \
                f".{int(line['t'] % 1 * 1000):03d} " if timestamps else ""
            marker = {"tx": "> ", "info": "-- "}.get(line["dir"], "")
            out.append(prefix + marker + line["text"])
        return "\n".join(out)

    def hexdump(self, max_bytes: int = 4096) -> List[str]:
        with self._lock:
            data = bytes(self.raw[-max_bytes:])
            start = self.raw_offset + max(0, len(self.raw) - max_bytes)
        out = []
        for offset in range(0, len(data), 16):
            chunk = data[offset:offset + 16]
            text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            out.append(f"{start + offset:08x}  {chunk.hex(' '):<47}  {text}")
        return out

    def search(self, query: str, regex: bool = False, limit: int = 200) -> List[dict]:
        if not query:
            return []
        try:
            pattern = re.compile(query if regex else re.escape(query), re.I)
        except re.error as exc:
            raise SerialError(f"Invalid pattern: {exc}") from None
        with self._lock:
            hits = [l.to_dict() for l in self.lines if pattern.search(l.text)]
        return hits[-limit:]

    def save(self, path: Optional[str] = None, timestamps: bool = True) -> str:
        if path is None:
            os.makedirs(self.log_dir, exist_ok=True)
            name = os.path.basename(self.port or "serial")
            path = os.path.join(self.log_dir, f"{time.strftime('%Y%m%d-%H%M%S')}-{name}.log")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(self.text(MAX_LINES, timestamps=timestamps) + "\n")
        return path


def _readable(fd: int) -> bool:
    import select as _select
    ready, _, _ = _select.select([fd], [], [], 0)
    return bool(ready)
