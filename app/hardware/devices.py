"""Connected development boards: discovery, identification and hot-plug.

Discovery reads the kernel's own description of each serial port in sysfs
(``/sys/class/tty/<name>/device``) and walks up to its USB device for the
vendor and product IDs, manufacturer, product and serial-number strings and
the driver (cp210x, ch341, ftdi_sio, cdc_acm, ...). A port is never
identified by its name alone: ``ttyUSB0`` says nothing about what is
connected.

Hot-plug: a netlink socket receives the kernel's (and udev's) uevents; any
tty or USB change triggers a rescan. No polling. Without netlink (some
containers) the list refreshes when a screen asks for it.
"""

from __future__ import annotations

import errno
import glob
import logging
import os
import select
import socket
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional

from ..projects import boards
from . import usbids
from .ports import external_holders

log = logging.getLogger("mcode.devices")

NETLINK_KOBJECT_UEVENT = 15


@dataclass
class Device:
    port: str
    name: str = ""
    vid: str = ""
    pid: str = ""
    manufacturer: str = ""
    product: str = ""
    serial: str = ""
    driver: str = ""
    bridge: str = ""
    kind: str = "cdc"                   # bridge | native-esp | arduino | pico | cdc
    family: str = ""                    # esp32 | avr | sam | rp2040 | ""
    board_id: str = ""                  # only when the USB ID names one board
    board_names: List[str] = field(default_factory=list)   # from installed cores
    chip: str = ""                      # from esptool, when the user ran "Identify"
    chip_info: Dict[str, str] = field(default_factory=dict)
    by_id: str = ""
    location: str = ""
    interface: str = ""
    readable: bool = True
    problem: str = ""
    holders: List[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Stable identity across re-plugs (serial number when the device has one)."""
        if self.serial:
            return f"{self.vid}:{self.pid}:{self.serial}"
        return f"{self.vid}:{self.pid}@{self.location or self.port}"

    @property
    def short_port(self) -> str:
        return os.path.basename(self.port)

    def describe(self) -> str:
        """What we know, worded honestly."""
        if self.chip:
            return f"{self.chip} (identified by esptool)"
        if self.board_id:
            board = boards.get(self.board_id)
            return f"{board.name if board else self.board_id} (by its USB ID)"
        if self.kind == "native-esp":
            return "ESP32 with built-in USB (chip not identified yet)"
        if self.kind == "bridge":
            return f"{self.bridge} adapter: the board behind it is unknown"
        return self.product or "USB serial device"

    def to_dict(self) -> dict:
        data = asdict(self)
        data.update(key=self.key, short_port=self.short_port, description=self.describe(),
                    usb_id=f"{self.vid}:{self.pid}" if self.vid else "")
        return data


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return ""


class DeviceManager:
    def __init__(self, state, publish: Callable[[str, object], None] = lambda t, d: None,
                 sysfs: str = "/sys", devroot: str = "/dev", proc: str = "/proc",
                 core_ids: Optional[Callable[[], Dict[str, List[str]]]] = None):
        self.state = state
        self.publish = publish
        self.sysfs = sysfs
        self.devroot = devroot
        self.proc = proc
        self._core_ids = core_ids or _core_usb_names
        self._lock = threading.RLock()
        self._devices: Dict[str, Device] = {}
        self._chips: Dict[str, dict] = dict(state.get("chips", {}) or {})
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._sock: Optional[socket.socket] = None
        self._wake_r, self._wake_w = os.pipe()
        self.hotplug = False
        self.listeners: List[Callable[[str, Device], None]] = []
        self._core_cache: Optional[Dict[str, List[str]]] = None

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self.scan()
        try:
            sock = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, NETLINK_KOBJECT_UEVENT)
            sock.bind((0, 1 | 2))
            self._sock = sock
            self.hotplug = True
        except (OSError, AttributeError) as exc:
            log.info("no hot-plug events (%s); devices refresh on request", exc)
            return
        self._thread = threading.Thread(target=self._listen, name="usb-hotplug", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            os.write(self._wake_w, b"x")
        except OSError:
            pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._sock is not None:
            self._sock.close()

    def _listen(self) -> None:
        pending = False
        while not self._stop.is_set():
            # Coalesce a burst of uevents (one plug-in sends a dozen) into one rescan.
            timeout = 0.25 if pending else None
            try:
                ready, _, _ = select.select([self._sock, self._wake_r], [], [], timeout)
            except (OSError, ValueError):
                return
            if self._wake_r in ready:
                return
            if not ready:
                pending = False
                self.scan()
                continue
            try:
                data = self._sock.recv(65536)
            except OSError:
                continue
            if _relevant(data):
                pending = True

    # -------------------------------------------------------------- scanning
    def scan(self) -> List[Device]:
        found: Dict[str, Device] = {}
        for tty in sorted(glob.glob(os.path.join(self.sysfs, "class", "tty", "*"))):
            device = self._describe(tty)
            if device is not None:
                found[device.port] = device
        with self._lock:
            before = dict(self._devices)
            self._devices = found
        added = [d for p, d in found.items() if p not in before]
        removed = [d for p, d in before.items() if p not in found]
        for device in added:
            log.info("connected: %s %s:%s %s", device.port, device.vid, device.pid,
                     device.describe())
        for device in removed:
            log.info("disconnected: %s", device.port)
        if added or removed or before.keys() != found.keys():
            self.publish("devices", {"devices": [d.to_dict() for d in found.values()],
                                     "added": [d.port for d in added],
                                     "removed": [d.port for d in removed]})
        for listener in list(self.listeners):
            for device in added:
                listener("added", device)
            for device in removed:
                listener("removed", device)
        return list(found.values())

    def _describe(self, tty_dir: str) -> Optional[Device]:
        name = os.path.basename(tty_dir)
        link = os.path.join(tty_dir, "device")
        if not os.path.exists(link):
            return None
        node = os.path.realpath(link)
        usb = node
        interface = ""
        for _ in range(8):
            if os.path.exists(os.path.join(usb, "idVendor")):
                break
            if os.path.exists(os.path.join(usb, "bInterfaceNumber")):
                interface = usb
            parent = os.path.dirname(usb)
            if parent == usb:
                return None
            usb = parent
        else:
            return None
        if not os.path.exists(os.path.join(usb, "idVendor")):
            return None
        port = os.path.join(self.devroot, name)
        device = Device(port=port)
        device.vid = _read(os.path.join(usb, "idVendor")).lower()
        device.pid = _read(os.path.join(usb, "idProduct")).lower()
        device.manufacturer = _read(os.path.join(usb, "manufacturer"))
        device.product = _read(os.path.join(usb, "product"))
        device.serial = _read(os.path.join(usb, "serial"))
        device.location = os.path.basename(usb)
        device.interface = _read(os.path.join(interface, "bInterfaceNumber")) if interface else ""
        driver_link = os.path.join(node, "driver")
        if not os.path.exists(driver_link) and interface:
            driver_link = os.path.join(interface, "driver")
        device.driver = os.path.basename(os.path.realpath(driver_link)) \
            if os.path.exists(driver_link) else ""
        self._identify(device)
        device.by_id = self._by_id(port)
        if not os.path.exists(port):
            device.readable = False
            device.problem = f"{port} does not exist (udev has not created it)"
        elif not os.access(port, os.R_OK | os.W_OK):
            device.readable = False
            device.problem = ("No permission to open the port. Add your user to the 'dialout' "
                              "group (sudo usermod -aG dialout $USER) and log in again.")
        device.holders = external_holders(port, self.proc)
        return device

    def _identify(self, device: Device) -> None:
        known = usbids.lookup(device.vid, device.pid)
        if known is not None:
            device.name = known.name
            device.kind = known.kind
            device.family = known.family
            device.board_id = known.board
            device.bridge = known.bridge
        elif device.driver in usbids.DRIVER_BRIDGES:
            device.kind = "bridge"
            device.bridge = usbids.DRIVER_BRIDGES[device.driver]
            device.name = f"{device.bridge} USB to UART"
        else:
            device.kind = "cdc"
            device.name = device.product or "USB serial device"
        names = self.core_names().get(f"{device.vid}:{device.pid}", [])
        # Bridges and Espressif's USB-Serial/JTAG (303a:1001) are shared by many boards:
        # the cores' names for those IDs would be guesses, so they are not shown.
        if names and device.kind not in ("bridge", "native-esp"):
            device.board_names = names[:4]
        chip = self._chips.get(self._chip_key(device))
        if chip:
            device.chip = chip.get("chip", "")
            device.chip_info = chip
            device.family = "esp32"
            device.name = f"{device.chip} ({device.bridge or 'USB'})"

    def core_names(self) -> Dict[str, List[str]]:
        if self._core_cache is None:
            try:
                self._core_cache = self._core_ids()
            except Exception:       # isolation: a malformed boards.txt must not break scans
                log.exception("reading board USB IDs from installed cores failed")
                self._core_cache = {}
        return self._core_cache

    def _by_id(self, port: str) -> str:
        folder = os.path.join(self.devroot, "serial", "by-id")
        try:
            for name in os.listdir(folder):
                path = os.path.join(folder, name)
                if os.path.realpath(path) == os.path.realpath(port):
                    return path
        except OSError:
            pass
        return ""

    @staticmethod
    def _chip_key(device: Device) -> str:
        return device.key

    # --------------------------------------------------------------- queries
    def list(self) -> List[Device]:
        with self._lock:
            return list(self._devices.values())

    def get(self, port: str) -> Optional[Device]:
        with self._lock:
            return self._devices.get(port)

    def refresh(self) -> List[Device]:
        return self.scan()

    def present(self, port: str) -> Optional[Device]:
        """Rescan and return the device if it is still connected."""
        self.scan()
        return self.get(port)

    def selected(self, project=None) -> Optional[Device]:
        """The board chosen for the project (by identity first, then port), or the
        only connected one."""
        devices = self.list()
        choice = (self.state.get("device_choice", {}) or {}).get(project.id if project else "_")
        if choice:
            for device in devices:
                if choice.get("key") and device.key == choice["key"]:
                    return device
            for device in devices:
                if device.port == choice.get("port"):
                    return device
        if len(devices) == 1:
            return devices[0]
        return None

    def select(self, device: Device, project=None) -> None:
        choices = dict(self.state.get("device_choice", {}) or {})
        choices[project.id if project else "_"] = {"key": device.key, "port": device.port}
        self.state.set("device_choice", choices)
        self.publish("devices", {"devices": [d.to_dict() for d in self.list()],
                                 "selected": device.port})

    def remember_chip(self, device: Device, info: dict) -> None:
        with self._lock:
            self._chips[self._chip_key(device)] = dict(info, time=time.time())
            chips = dict(self._chips)
        self.state.set("chips", chips)
        self.scan()
        self.publish("devices", {"devices": [d.to_dict() for d in self.list()],
                                 "identified": device.port})


def _relevant(data: bytes) -> bool:
    """Is this uevent about a serial port or USB device?"""
    # Kernel messages are "action@devpath\0KEY=value\0..."; udev's carry a binary
    # header and then the same KEY=value strings. Either way a substring test works.
    return (b"SUBSYSTEM=tty" in data or b"SUBSYSTEM=usb-serial" in data
            or b"SUBSYSTEM=usb" in data or b"/tty" in data)


def _core_usb_names() -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for fqbn, info in boards.installed_fqbns().items():
        for usb in info.get("usb", []):
            out.setdefault(usb, []).append(info.get("name", fqbn))
    return out


def is_permission_error(exc: OSError) -> bool:
    return exc.errno in (errno.EACCES, errno.EPERM)
