# Hardware: boards, ports and flashing

## How mCode finds boards

For every serial port the kernel knows (`/sys/class/tty/*`), mCode follows
`device` up to the USB device and reads its vendor and product IDs,
manufacturer, product and serial-number strings, and the driver bound to it
(`cp210x`, `ch341`, `ftdi_sio`, `pl2303`, `cdc_acm`). Plugging and unplugging
arrives as kernel uevents over netlink (no polling); without netlink the list
refreshes when a screen asks.

A port's **name never identifies a board**. What each kind of ID tells:

| Seen | mCode says | Why |
|---|---|---|
| CP210x `10c4:ea60`, CH340 `1a86:7523`, CH9102 `1a86:55d4`, CH343, FTDI `0403:*`, PL2303 | "<chip> adapter: the board behind it is unknown" | a bridge chip; ESP32 DevKits, Uno clones and GPS modules all use them |
| Espressif `303a:1001` | "ESP32 with built-in USB" | the chip's own USB-Serial/JTAG (S3, C3, C6, H2): which ESP32 is unknown until identified |
| Arduino `2341:0043` and the other official IDs | "Arduino Uno (by its USB ID)" | the ID names one board |
| Raspberry Pi `2e8a:0005` / `000a` | "Raspberry Pi Pico" | |
| anything else | the product string | |

Board names from installed Arduino cores' `boards.txt` (vid/pid entries) are
shown as "Cores say" in the desktop IDE.

**Identify chip** (ESP32 boards and unknown adapters) runs
`esptool --port P chip-id`: read-only, but it resets the board. The answer
("ESP32-S3 (QFN56)", MAC, features) is remembered for that board's USB
identity and used by the upload checks.

## Permissions

Serial ports belong to the `dialout` group. If mCode shows *No permission*,
run once and log in again (mCode never changes groups itself):

```bash
sudo usermod -aG dialout $USER
```

## Flashing safety

Nothing is written to a board without your confirmation, and Claude has no
way to start an upload. Before the confirmation screen and again when you
confirm, mCode checks:

1. the project's board has an upload method mCode supports;
2. the firmware exists and is newer than every source file (otherwise *Build
   now*); a failed last build also stops it;
3. the chosen device is still connected and readable;
4. no other program has the port open (it names it); the serial monitor
   pauses and resumes by itself;
5. the device fits the project: a board USB ID for a different board, an
   ESP32 (native USB or identified chip) on an AVR project, a different ESP32
   chip, or a native-USB ESP32 on a project for the original ESP32 (no USB of
   its own) **stop** the upload; an unidentified adapter is a **warning** you
   accept by confirming;
6. the upload tool is installed.

Confirming uses a one-time token valid for five minutes; if the firmware
changed in between, mCode asks again. Erasing the whole flash (ESP32) is a
separate action that requires typing ERASE in the desktop IDE. Flash is
never erased as part of a normal upload.

Upload speed: the board's default, or Settings › Upload speed, or per
project in the Hardware view (ESP32 boards through the Arduino CLI's
`UploadSpeed` option; PlatformIO uses `upload_speed` in `platformio.ini`).
Verification: avrdude and bossac verify with `--verify`; esptool checks a
hash of every write ("Hash of data verified").

## Serial monitor

Exclusive access (`TIOCEXCL`): while mCode's monitor has a port, other
programs cannot open it, and the monitor closes it for uploads. Baud rates
from 300 to 2 000 000 including 74880 (the ESP32 boot ROM) through the
kernel's custom-rate interface. Opening a port asserts DTR/RTS as every
terminal does, which resets Arduino boards with auto-reset; *Reset board*
pulses RTS the way esptool does.

## Whisplay HAT

mCode uses the Whisplay HAT through whisplay-daemon only (LCD framebuffer,
button) and ALSA (`capture`/`playback` on the WM8960). It never touches GPIO,
SPI or I2C on the Raspberry Pi or Orange Pi itself. Development boards are
connected by USB.
