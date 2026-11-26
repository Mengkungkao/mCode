"""The board catalogue: boards mCode offers when you create a project.

Every identifier here is one the real tools define, and the catalogue says
which tool defines which board:

* ``fqbn``: the Arduino CLI board ID (``vendor:arch:board[:options]``).
  ESP32 entries match the Espressif Arduino core's boards.txt (checked
  against core 3.3.12, 2026-11-27); AVR/SAM/mbed entries match Arduino's
  official cores (tests check them against an installed core's boards.txt
  whenever one is present).
* ``pio``: the PlatformIO board ID and platform, with the frameworks the
  official PlatformIO platform offers for it.
* ``idf_target``: the ESP-IDF target (``idf.py set-target``).

A combination that is not listed is not offered: for instance the official
PlatformIO ``espressif32`` platform has no Arduino framework for the C6 and
H2, so those boards offer PlatformIO only with ESP-IDF.

Pin maps and electrical limits are NOT part of this catalogue. mCode never
invents them; the AI is told to state where a pin assignment comes from.
"""

from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

ARDUINO_CLI, PLATFORMIO, ESP_IDF, MAKE = "arduino-cli", "platformio", "esp-idf", "make"
TOOLCHAIN_NAMES = {ARDUINO_CLI: "Arduino CLI", PLATFORMIO: "PlatformIO", ESP_IDF: "ESP-IDF",
                   MAKE: "Make"}

PLATFORMS = {
    "esp32": "ESP32",
    "arduino": "Arduino",
    "pico": "Raspberry Pi Pico",
    "generic": "Generic C/C++",
}


@dataclass(frozen=True)
class Board:
    id: str
    name: str
    platform: str                    # esp32 | arduino | pico | generic
    mcu: str
    arch: str                        # xtensa | riscv | avr | arm | host
    fqbn: str = ""
    core: str = ""                   # Arduino CLI core to install (vendor:arch)
    pio_board: str = ""
    pio_platform: str = ""
    pio_frameworks: Tuple[str, ...] = ()
    idf_target: str = ""
    flasher: str = ""                # esptool | avrdude | bossac | uf2 | none
    upload_speeds: Tuple[int, ...] = ()
    default_upload_speed: int = 0
    native_usb: bool = False         # the MCU itself has USB (no bridge chip needed)
    usb_ids: Tuple[str, ...] = ()    # VID:PID this board itself enumerates as
    notes: str = ""

    @property
    def toolchains(self) -> List[str]:
        out = []
        if self.fqbn:
            out.append(ARDUINO_CLI)
        if self.pio_board:
            out.append(PLATFORMIO)
        if self.idf_target:
            out.append(ESP_IDF)
        if self.platform == "generic":
            out.append(MAKE)
        return out

    def frameworks(self, toolchain: str) -> List[str]:
        if toolchain == ARDUINO_CLI:
            return ["arduino"] if self.fqbn else []
        if toolchain == PLATFORMIO:
            return list(self.pio_frameworks)
        if toolchain == ESP_IDF:
            return ["espidf"] if self.idf_target else []
        if toolchain == MAKE:
            return ["none"]
        return []

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "platform": self.platform, "mcu": self.mcu,
                "arch": self.arch, "fqbn": self.fqbn, "core": self.core,
                "pio_board": self.pio_board, "pio_platform": self.pio_platform,
                "pio_frameworks": list(self.pio_frameworks), "idf_target": self.idf_target,
                "flasher": self.flasher, "toolchains": self.toolchains,
                "upload_speeds": list(self.upload_speeds),
                "default_upload_speed": self.default_upload_speed, "native_usb": self.native_usb,
                "notes": self.notes}


ESP_SPEEDS = (115200, 230400, 460800, 921600)

BOARDS: List[Board] = [
    # ---------------------------------------------------------------- ESP32
    Board("esp32-devkit", "ESP32 DevKit", "esp32", "ESP32", "xtensa",
          fqbn="esp32:esp32:esp32", core="esp32:esp32",
          pio_board="esp32dev", pio_platform="espressif32", pio_frameworks=("arduino", "espidf"),
          idf_target="esp32", flasher="esptool", upload_speeds=ESP_SPEEDS,
          default_upload_speed=921600,
          notes="ESP32-WROOM-32 / WROVER DevKits. USB through a bridge chip (CP210x, CH340, "
                "CH9102)."),
    Board("esp32-s2", "ESP32-S2", "esp32", "ESP32-S2", "xtensa",
          fqbn="esp32:esp32:esp32s2", core="esp32:esp32",
          pio_board="esp32-s2-saola-1", pio_platform="espressif32",
          pio_frameworks=("arduino", "espidf"), idf_target="esp32s2", flasher="esptool",
          upload_speeds=ESP_SPEEDS, default_upload_speed=921600, native_usb=True,
          notes="Saola-1 / DevKitM-1. Native USB (OTG) or a bridge, depending on the board."),
    Board("esp32-s3", "ESP32-S3", "esp32", "ESP32-S3", "xtensa",
          fqbn="esp32:esp32:esp32s3", core="esp32:esp32",
          pio_board="esp32-s3-devkitc-1", pio_platform="espressif32",
          pio_frameworks=("arduino", "espidf"), idf_target="esp32s3", flasher="esptool",
          upload_speeds=ESP_SPEEDS, default_upload_speed=921600, native_usb=True,
          usb_ids=("303a:1001",),
          notes="DevKitC-1 has two USB ports: UART (bridge) and USB (the chip's USB-Serial/JTAG). "
                "On the USB port, Serial needs 'USB CDC on boot'."),
    Board("esp32-c3", "ESP32-C3", "esp32", "ESP32-C3", "riscv",
          fqbn="esp32:esp32:esp32c3", core="esp32:esp32",
          pio_board="esp32-c3-devkitm-1", pio_platform="espressif32",
          pio_frameworks=("arduino", "espidf"), idf_target="esp32c3", flasher="esptool",
          upload_speeds=ESP_SPEEDS, default_upload_speed=921600, native_usb=True,
          usb_ids=("303a:1001",),
          notes="DevKitM-1 uses a bridge; many small C3 boards use the chip's USB-Serial/JTAG."),
    Board("esp32-c6", "ESP32-C6", "esp32", "ESP32-C6", "riscv",
          fqbn="esp32:esp32:esp32c6", core="esp32:esp32",
          pio_board="esp32-c6-devkitc-1", pio_platform="espressif32", pio_frameworks=("espidf",),
          idf_target="esp32c6", flasher="esptool", upload_speeds=ESP_SPEEDS,
          default_upload_speed=921600, native_usb=True, usb_ids=("303a:1001",),
          notes="Arduino framework through the Arduino CLI; PlatformIO's official platform "
                "offers ESP-IDF only."),
    Board("esp32-h2", "ESP32-H2", "esp32", "ESP32-H2", "riscv",
          fqbn="esp32:esp32:esp32h2", core="esp32:esp32",
          pio_board="esp32-h2-devkitm-1", pio_platform="espressif32", pio_frameworks=("espidf",),
          idf_target="esp32h2", flasher="esptool", upload_speeds=ESP_SPEEDS,
          default_upload_speed=921600, native_usb=True, usb_ids=("303a:1001",),
          notes="No Wi-Fi (802.15.4 and Bluetooth LE). PlatformIO's official platform offers "
                "ESP-IDF only."),
    # -------------------------------------------------------------- Arduino
    Board("uno", "Arduino Uno", "arduino", "ATmega328P", "avr",
          fqbn="arduino:avr:uno", core="arduino:avr",
          pio_board="uno", pio_platform="atmelavr", pio_frameworks=("arduino",),
          flasher="avrdude", usb_ids=("2341:0043", "2341:0001", "2a03:0043", "2341:0243"),
          notes="Clones often use a CH340 bridge instead of the 16U2."),
    Board("mega2560", "Arduino Mega 2560", "arduino", "ATmega2560", "avr",
          fqbn="arduino:avr:mega:cpu=atmega2560", core="arduino:avr",
          pio_board="megaatmega2560", pio_platform="atmelavr", pio_frameworks=("arduino",),
          flasher="avrdude", usb_ids=("2341:0010", "2341:0042", "2a03:0010", "2a03:0042",
                                      "2341:0242")),
    Board("nano", "Arduino Nano", "arduino", "ATmega328P", "avr",
          fqbn="arduino:avr:nano:cpu=atmega328", core="arduino:avr",
          pio_board="nanoatmega328new", pio_platform="atmelavr", pio_frameworks=("arduino",),
          flasher="avrdude",
          notes="Current bootloader. Older Nanos and many clones need the old bootloader "
                "variant."),
    Board("nano-old", "Arduino Nano (old bootloader)", "arduino", "ATmega328P", "avr",
          fqbn="arduino:avr:nano:cpu=atmega328old", core="arduino:avr",
          pio_board="nanoatmega328", pio_platform="atmelavr", pio_frameworks=("arduino",),
          flasher="avrdude"),
    Board("leonardo", "Arduino Leonardo", "arduino", "ATmega32U4", "avr",
          fqbn="arduino:avr:leonardo", core="arduino:avr",
          pio_board="leonardo", pio_platform="atmelavr", pio_frameworks=("arduino",),
          flasher="avrdude", native_usb=True, usb_ids=("2341:8036", "2341:0036", "2a03:8036"),
          notes="Native USB: the port disappears briefly while the bootloader runs."),
    Board("micro", "Arduino Micro", "arduino", "ATmega32U4", "avr",
          fqbn="arduino:avr:micro", core="arduino:avr",
          pio_board="micro", pio_platform="atmelavr", pio_frameworks=("arduino",),
          flasher="avrdude", native_usb=True, usb_ids=("2341:8037", "2341:0037")),
    Board("due", "Arduino Due (programming port)", "arduino", "AT91SAM3X8E", "arm",
          fqbn="arduino:sam:arduino_due_x_dbg", core="arduino:sam",
          pio_board="due", pio_platform="atmelsam", pio_frameworks=("arduino",),
          flasher="bossac", usb_ids=("2341:003d",),
          notes="3.3 V I/O. Use the programming port (next to the power jack)."),
    Board("nano-esp32", "Arduino Nano ESP32", "arduino", "ESP32-S3", "xtensa",
          fqbn="arduino:esp32:nano_nora", core="arduino:esp32",
          pio_board="arduino_nano_esp32", pio_platform="espressif32",
          pio_frameworks=("arduino",), flasher="dfu", native_usb=True, usb_ids=("2341:0070",),
          notes="Arduino's own ESP32 core (arduino:esp32); uploads through DFU."),
    # ----------------------------------------------------------------- Pico
    Board("pico", "Raspberry Pi Pico", "pico", "RP2040", "arm",
          fqbn="arduino:mbed_rp2040:pico", core="arduino:mbed_rp2040",
          pio_board="pico", pio_platform="raspberrypi", pio_frameworks=("arduino",),
          flasher="uf2", native_usb=True, usb_ids=("2e8a:0005", "2e8a:000a"),
          notes="Uploads by copying a UF2 file; hold BOOTSEL while plugging in the first time."),
    # -------------------------------------------------------------- Generic
    Board("generic-host", "Generic C/C++ (Make)", "generic", "Any", "host",
          flasher="none",
          notes="A Makefile project: build, clean and tests with make. Bring your own "
                "cross-compiler and flashing commands in the Makefile."),
]

BY_ID: Dict[str, Board] = {b.id: b for b in BOARDS}


def get(board_id: str) -> Optional[Board]:
    return BY_ID.get(board_id)


def for_platform(platform: str) -> List[Board]:
    return [b for b in BOARDS if b.platform == platform]


def find_by_fqbn(fqbn: str) -> Optional[Board]:
    base = ":".join(fqbn.split(":")[:3])
    for board in BOARDS:
        if board.fqbn == fqbn:
            return board
    for board in BOARDS:
        if board.fqbn and ":".join(board.fqbn.split(":")[:3]) == base:
            return board
    return None


def find_by_pio(pio_board: str) -> Optional[Board]:
    return next((b for b in BOARDS if b.pio_board == pio_board), None)


def find_by_idf_target(target: str) -> Optional[Board]:
    return next((b for b in BOARDS if b.idf_target == target), None)


def family_of_mcu(mcu: str) -> str:
    """A comparable chip family: "esp32s3", "atmega328p", ..."""
    return re.sub(r"[^a-z0-9]", "", (mcu or "").lower())


# ------------------------------------------------- installed core definitions

_USB_LINE = re.compile(r"^([A-Za-z0-9_\-]+)\.(?:upload_port\.(\d+)\.(vid|pid)|(vid|pid)\.(\d+)|"
                       r"(vid|pid))=(0x[0-9A-Fa-f]+)\s*$")


def core_boards_files(data_dir: str = "~/.arduino15") -> List[str]:
    base = os.path.expanduser(data_dir)
    return sorted(glob.glob(os.path.join(base, "packages", "*", "hardware", "*", "*",
                                         "boards.txt")))


def read_boards_txt(path: str) -> Dict[str, Dict[str, str]]:
    """{board id: {"name": ..., "usb": ["vid:pid", ...], "mcu": ...}} from one boards.txt."""
    names: Dict[str, Dict] = {}
    pairs: Dict[Tuple[str, str], Dict[str, str]] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                parts = key.split(".")
                if len(parts) == 2 and parts[1] == "name":
                    names.setdefault(parts[0], {"usb": []})["name"] = value
                elif len(parts) == 3 and parts[1] == "build" and parts[2] == "mcu":
                    names.setdefault(parts[0], {"usb": []})["mcu"] = value
                match = _USB_LINE.match(line)
                if match:
                    board, up_index, up_which, which, index, bare, number = match.groups()
                    key = (board, f"u{up_index}") if up_which else (board, index or "0")
                    slot = pairs.setdefault(key, {})
                    slot[up_which or which or bare] = number[2:].lower().rjust(4, "0")
    except OSError:
        return {}
    for (board, _index), slot in pairs.items():
        if "vid" in slot and "pid" in slot and board in names:
            names[board]["usb"].append(f"{slot['vid']}:{slot['pid']}")
    return names


def installed_fqbns(data_dir: str = "~/.arduino15") -> Dict[str, dict]:
    """{fqbn: {"name", "mcu", "usb"}} for every board in the installed Arduino cores."""
    out: Dict[str, dict] = {}
    for path in core_boards_files(data_dir):
        parts = path.split(os.sep)       # .../packages/<vendor>/hardware/<arch>/<version>/boards.txt
        vendor, arch = parts[-5], parts[-3]
        for board, info in read_boards_txt(path).items():
            out[f"{vendor}:{arch}:{board}"] = info
    return out


@dataclass
class CatalogueCheck:
    board_id: str
    fqbn: str
    status: str                      # ok | missing | core-not-installed
    detail: str = ""
    extra: Dict = field(default_factory=dict)


def check_against_cores(data_dir: str = "~/.arduino15") -> List[CatalogueCheck]:
    """Compare every catalogue FQBN with the installed cores' boards.txt."""
    defined = installed_fqbns(data_dir)
    cores = {":".join(f.split(":")[:2]) for f in defined}
    checks = []
    for board in BOARDS:
        if not board.fqbn:
            continue
        base = ":".join(board.fqbn.split(":")[:3])
        core = ":".join(board.fqbn.split(":")[:2])
        if core not in cores:
            checks.append(CatalogueCheck(board.id, board.fqbn, "core-not-installed"))
        elif base in defined:
            checks.append(CatalogueCheck(board.id, board.fqbn, "ok", defined[base].get("name", "")))
        else:
            checks.append(CatalogueCheck(board.id, board.fqbn, "missing"))
    return checks
