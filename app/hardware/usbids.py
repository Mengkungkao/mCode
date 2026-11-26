"""USB IDs mCode recognises, and what each one does and does not tell us.

Two kinds of entries:

* **Bridges** (CP210x, CH340, CH9102, FTDI, PL2303): a USB-to-UART chip.
  It says nothing about the board behind it: an ESP32 DevKit, an Uno clone
  and a GPS module can all show up as the same CH340. mCode reports the
  bridge and leaves the board to the project or to esptool's chip detection.
* **Native** IDs: the microcontroller itself (or the board's own USB chip
  with a vendor-specific ID) enumerates. Arduino's official IDs identify the
  board; Espressif's USB-Serial/JTAG ID (303a:1001) identifies "an ESP32
  with built-in USB" (S3, C3, C6, H2...) but not which chip.

Board names from the installed Arduino cores' boards.txt are added at run
time (see boards.installed_fqbns), so a newly installed core teaches mCode
its boards without a code change.
"""

from __future__ import annotations

from typing import Dict, NamedTuple, Optional


class UsbId(NamedTuple):
    name: str               # what to show
    kind: str               # bridge | native-esp | arduino | pico | cdc
    family: str = ""        # esp32 | avr | sam | rp2040 | ""
    board: str = ""         # catalogue board id when the ID is board-specific
    bridge: str = ""        # bridge chip name


BRIDGES: Dict[str, UsbId] = {
    "10c4:ea60": UsbId("CP210x USB to UART", "bridge", bridge="CP210x"),
    "10c4:ea70": UsbId("CP2105 USB to UART", "bridge", bridge="CP2105"),
    "10c4:ea71": UsbId("CP2108 USB to UART", "bridge", bridge="CP2108"),
    "1a86:7523": UsbId("CH340 USB to UART", "bridge", bridge="CH340"),
    "1a86:7522": UsbId("CH340K USB to UART", "bridge", bridge="CH340"),
    "1a86:5523": UsbId("CH341 USB to UART", "bridge", bridge="CH341"),
    "1a86:55d4": UsbId("CH9102 USB to UART", "bridge", bridge="CH9102"),
    "1a86:55d3": UsbId("CH343 USB to UART", "bridge", bridge="CH343"),
    "1a86:55d2": UsbId("CH342 USB to UART", "bridge", bridge="CH342"),
    "0403:6001": UsbId("FTDI FT232R USB to UART", "bridge", bridge="FTDI FT232R"),
    "0403:6010": UsbId("FTDI FT2232 USB to UART", "bridge", bridge="FTDI FT2232"),
    "0403:6011": UsbId("FTDI FT4232 USB to UART", "bridge", bridge="FTDI FT4232"),
    "0403:6014": UsbId("FTDI FT232H USB to UART", "bridge", bridge="FTDI FT232H"),
    "0403:6015": UsbId("FTDI FT231X USB to UART", "bridge", bridge="FTDI FT231X"),
    "067b:2303": UsbId("PL2303 USB to UART", "bridge", bridge="PL2303"),
    "067b:23a3": UsbId("PL2303GC USB to UART", "bridge", bridge="PL2303"),
}

NATIVE: Dict[str, UsbId] = {
    "303a:1001": UsbId("ESP32 with built-in USB", "native-esp", "esp32"),
    "303a:0002": UsbId("ESP32-S2 (USB)", "native-esp", "esp32", board="esp32-s2"),
    "303a:0003": UsbId("ESP32-S2 (USB)", "native-esp", "esp32", board="esp32-s2"),
    "303a:4001": UsbId("ESP32 (TinyUSB CDC)", "native-esp", "esp32"),
    "303a:1002": UsbId("ESP32 (USB DFU)", "native-esp", "esp32"),
    "2341:0043": UsbId("Arduino Uno", "arduino", "avr", board="uno"),
    "2341:0001": UsbId("Arduino Uno", "arduino", "avr", board="uno"),
    "2341:0243": UsbId("Arduino Uno", "arduino", "avr", board="uno"),
    "2a03:0043": UsbId("Arduino Uno", "arduino", "avr", board="uno"),
    "2341:0010": UsbId("Arduino Mega 2560", "arduino", "avr", board="mega2560"),
    "2341:0042": UsbId("Arduino Mega 2560", "arduino", "avr", board="mega2560"),
    "2341:0242": UsbId("Arduino Mega 2560", "arduino", "avr", board="mega2560"),
    "2a03:0010": UsbId("Arduino Mega 2560", "arduino", "avr", board="mega2560"),
    "2a03:0042": UsbId("Arduino Mega 2560", "arduino", "avr", board="mega2560"),
    "2341:8036": UsbId("Arduino Leonardo", "arduino", "avr", board="leonardo"),
    "2341:0036": UsbId("Arduino Leonardo (bootloader)", "arduino", "avr", board="leonardo"),
    "2a03:8036": UsbId("Arduino Leonardo", "arduino", "avr", board="leonardo"),
    "2341:8037": UsbId("Arduino Micro", "arduino", "avr", board="micro"),
    "2341:0037": UsbId("Arduino Micro (bootloader)", "arduino", "avr", board="micro"),
    "2341:003d": UsbId("Arduino Due (programming port)", "arduino", "sam", board="due"),
    "2341:003e": UsbId("Arduino Due (native port)", "arduino", "sam", board="due"),
    "2341:0070": UsbId("Arduino Nano ESP32", "arduino", "esp32", board="nano-esp32"),
    "2e8a:0005": UsbId("Raspberry Pi Pico", "pico", "rp2040", board="pico"),
    "2e8a:000a": UsbId("Raspberry Pi Pico", "pico", "rp2040", board="pico"),
    "2e8a:00c0": UsbId("Raspberry Pi Pico (Arduino)", "pico", "rp2040", board="pico"),
}

DRIVER_BRIDGES = {"cp210x": "CP210x", "ch341": "CH340/CH341", "ftdi_sio": "FTDI",
                  "pl2303": "PL2303", "ch343": "CH343", "usb_ch343": "CH343"}


def lookup(vid: str, pid: str) -> Optional[UsbId]:
    key = f"{(vid or '').lower()}:{(pid or '').lower()}"
    return NATIVE.get(key) or BRIDGES.get(key)


def chip_family(chip: str) -> str:
    """"ESP32-S3 (QFN56)" -> "esp32s3"; "ESP32-D0WD-V3" -> "esp32"."""
    text = (chip or "").upper().replace(" ", "")
    for name in ("ESP32-S2", "ESP32-S3", "ESP32-C2", "ESP32-C3", "ESP32-C5", "ESP32-C61",
                 "ESP32-C6", "ESP32-H2", "ESP32-P4"):
        if text.startswith(name):
            return name.lower().replace("-", "")
    if text.startswith("ESP32"):
        return "esp32"
    return ""
