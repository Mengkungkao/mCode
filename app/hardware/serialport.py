"""A serial port with the Linux termios interface (standard library only).

Raw 8N1, no flow control, exclusive (TIOCEXCL: other programs cannot open
the port while mCode has it). Standard baud rates use termios; others
(74880, the ESP8266/ESP32 boot ROM rate, 1500000, ...) use the kernel's
termios2 interface with BOTHER, as pyserial does. DTR/RTS can be set for a
board reset.
"""

from __future__ import annotations

import array
import errno
import fcntl
import os
import select
import struct
import termios
from typing import Optional

TIOCEXCL = getattr(termios, "TIOCEXCL", 0x540C)
TIOCNXCL = getattr(termios, "TIOCNXCL", 0x540D)
TIOCMGET = getattr(termios, "TIOCMGET", 0x5415)
TIOCMBIS = getattr(termios, "TIOCMBIS", 0x5416)
TIOCMBIC = getattr(termios, "TIOCMBIC", 0x5417)
TIOCM_DTR = getattr(termios, "TIOCM_DTR", 0x002)
TIOCM_RTS = getattr(termios, "TIOCM_RTS", 0x004)
TCGETS2 = 0x802C542A
TCSETS2 = 0x402C542B
BOTHER = 0o010000
CBAUD = 0o010017

STANDARD = {rate: getattr(termios, f"B{rate}") for rate in
            (300, 600, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800,
             500000, 576000, 921600, 1000000, 1152000, 1500000, 2000000, 2500000, 3000000)
            if hasattr(termios, f"B{rate}")}


class SerialError(Exception):
    pass


class SerialPort:
    def __init__(self, path: str, baud: int = 115200, exclusive: bool = True):
        self.path = path
        self.baud = int(baud)
        self.exclusive = exclusive
        self.fd: Optional[int] = None

    def open(self) -> "SerialPort":
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EPERM):
                raise SerialError(f"No permission to open {self.path}. Add your user to the "
                                  "'dialout' group and log in again.") from None
            if exc.errno == errno.EBUSY:
                raise SerialError(f"{self.path} is in use by another program.") from None
            if exc.errno == errno.ENOENT:
                raise SerialError(f"{self.path} is not connected.") from None
            raise SerialError(f"Cannot open {self.path}: {exc.strerror}") from None
        try:
            if self.exclusive:
                try:
                    fcntl.ioctl(fd, TIOCEXCL)
                except OSError:
                    pass                    # a pty or a driver without it: still usable
            self._configure(fd)
        except Exception:
            os.close(fd)
            raise
        self.fd = fd
        return self

    def _configure(self, fd: int) -> None:
        try:
            attrs = termios.tcgetattr(fd)
        except termios.error as exc:
            raise SerialError(f"{self.path} is not a serial port ({exc})") from None
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = attrs
        iflag &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK | termios.ISTRIP |
                   termios.INLCR | termios.IGNCR | termios.ICRNL | termios.IXON | termios.IXOFF |
                   termios.IXANY)
        oflag &= ~termios.OPOST
        lflag &= ~(termios.ECHO | termios.ECHONL | termios.ICANON | termios.ISIG |
                   termios.IEXTEN)
        cflag &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB)
        if hasattr(termios, "CRTSCTS"):
            cflag &= ~termios.CRTSCTS
        cflag |= termios.CS8 | termios.CREAD | termios.CLOCAL
        cc[termios.VMIN] = 0
        cc[termios.VTIME] = 0
        speed = STANDARD.get(self.baud)
        if speed is not None:
            termios.tcsetattr(fd, termios.TCSANOW, [iflag, oflag, cflag, lflag, speed, speed, cc])
            return
        termios.tcsetattr(fd, termios.TCSANOW, [iflag, oflag, cflag, lflag, ispeed, ospeed, cc])
        self._custom_baud(fd)

    def _custom_baud(self, fd: int) -> None:
        buf = array.array("i", [0] * 64)
        try:
            fcntl.ioctl(fd, TCGETS2, buf)
            buf[2] &= ~CBAUD
            buf[2] |= BOTHER
            buf[9] = buf[10] = self.baud
            fcntl.ioctl(fd, TCSETS2, buf)
        except OSError as exc:
            raise SerialError(f"{self.baud} baud is not supported by this port ({exc.strerror})") \
                from None

    # ------------------------------------------------------------------ io
    def read(self, timeout: Optional[float] = 0.2, wake_fd: Optional[int] = None) -> bytes:
        """Bytes available within ``timeout`` (b"" if none). Raises SerialError when the
        device went away."""
        if self.fd is None:
            raise SerialError("port is closed")
        fds = [self.fd] + ([wake_fd] if wake_fd is not None else [])
        try:
            ready, _, _ = select.select(fds, [], [], timeout)
        except (OSError, ValueError) as exc:
            raise SerialError(f"port error: {exc}") from None
        if self.fd not in ready:
            return b""
        try:
            data = os.read(self.fd, 4096)
        except BlockingIOError:
            return b""
        except OSError as exc:
            raise SerialError(f"{self.path} disconnected ({exc.strerror})") from None
        if not data:
            raise SerialError(f"{self.path} disconnected")
        return data

    def write(self, data: bytes, timeout: float = 2.0) -> int:
        if self.fd is None:
            raise SerialError("port is closed")
        sent = 0
        view = memoryview(data)
        while sent < len(data):
            _, ready, _ = select.select([], [self.fd], [], timeout)
            if not ready:
                raise SerialError("write timed out (is flow control holding the line?)")
            try:
                sent += os.write(self.fd, view[sent:])
            except BlockingIOError:
                continue
            except OSError as exc:
                raise SerialError(f"write failed: {exc.strerror}") from None
        return sent

    def set_lines(self, dtr: Optional[bool] = None, rts: Optional[bool] = None) -> None:
        if self.fd is None:
            return
        for flag, value in ((TIOCM_DTR, dtr), (TIOCM_RTS, rts)):
            if value is None:
                continue
            try:
                fcntl.ioctl(self.fd, TIOCMBIS if value else TIOCMBIC, struct.pack("I", flag))
            except OSError:
                pass

    def close(self) -> None:
        fd, self.fd = self.fd, None
        if fd is not None:
            try:
                fcntl.ioctl(fd, TIOCNXCL)
            except OSError:
                pass
            os.close(fd)

    @property
    def is_open(self) -> bool:
        return self.fd is not None
