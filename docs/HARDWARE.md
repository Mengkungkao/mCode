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
| Espressif `303a:1001` | "ESP32 with built-in USB" | the chip's own USB-Serial/JTAG (S3, C3, C5, C6, H2): which ESP32 is unknown until identified |
| Arduino `2341:0043` and the other official IDs | "Arduino Uno (by its USB ID)" | the ID names one board |
| Raspberry Pi `2e8a:0005` / `000a` | "Raspberry Pi Pico" | |
| anything else | the product string | |

Board names from installed Arduino cores' `boards.txt` (vid/pid entries) are
shown as "Cores say" in the desktop IDE.

**Identify chip** (ESP32 boards and unknown adapters) runs
`esptool --port P chip-id`: read-only, but it resets the board. The answer
("ESP32-S3 (QFN56)", MAC, features) is remembered for that board's USB
identity and used by the upload checks.

## When no board is found

An empty list means the kernel created no serial port for a board. mCode then
reads `/sys/bus/usb/devices` and `/sys/class/udc` and says which case it is
(LCD: *Connect Board › What to check*; desktop IDE: Hardware; terminal:
`./run.sh cli devices`):

- **Nothing on USB**: no device that could be a board (a known ID, or a CDC
  or vendor-specific interface) is connected. Usual causes: a charge-only
  cable, an unpowered board, or the wrong USB port (next point).
- **A USB port that works only as a device** (`/sys/class/udc/*`): the
  computer's power/OTG port is in gadget mode, so a board plugged in there is
  never seen. Use another port.
- **"<device>: no serial port"**: the device is on USB but no driver gave it a
  serial port: a board in UF2/DFU download mode, or an adapter whose driver
  is missing (`dmesg` after plugging it in says which).

### Orange Pi Zero 2W

Checked on the .130 board (Orange Pi 1.0.2 Jammy, kernel 6.1.31,
2026-11-27): the USB-C port wired to USB0 (`usb@5100000`, MUSB) is set to
`dr_mode = "peripheral"` in the device tree and its host controllers
(`ehci0`/`ohci0`) are disabled, so it is a gadget-only port
(`/sys/class/udc/musb-hdrc.5.auto`). The other USB-C port is a host port: an
ESP32-C5 plugged in there enumerated at once as `303a:1001` on `ttyACM0`, and
mCode listed it. According to the
[DietPi forum](https://dietpi.com/forum/t/orange-pi-zero-2-w-enable-usb0-host-mode/23661),
the host port is the one further from the board edge.

Orange Pi's image ships an overlay, `sun50i-h616-usb0-host.dtbo`, that sets
USB0 to host mode (`overlays=... usb0-host` in `/boot/orangepiEnv.txt`, then a
reboot). That is a system change needing root, mCode never makes it, and it
has not been tried: the power port then also has to work as a host while the
board is powered through it or through the PiSugar.

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
   now*); a failed last build also stops it, and so does firmware built for
   another board or toolchain than the project has now (the board is in
   `.mcode/project.json`, which is not a source file);
3. the chosen device is still connected and readable;
4. no other program has the port open (it names it); the serial monitor
   pauses and resumes by itself;
5. the device fits the project: a board USB ID for a different board, an
   ESP32 (native USB or identified chip) on an AVR project, a different ESP32
   chip, or a native-USB ESP32 on a project for the original ESP32 (no USB of
   its own) **stop** the upload; an unidentified adapter is a **warning** you
   accept by confirming. When esptool identified the chip (*Identify chip*)
   and the catalogue has that ESP32, the LCD offers *Make it <board>* (same
   toolchain: the project's board changes and it is rebuilt) or *New <board>
   project* (PlatformIO has no ESP32-C5, for example);
6. the upload tool is installed;
7. no build, rebuild, clean or test of the project is running (since 0.2.0:
   a build and an upload of one project never run at the same time; one
   waits for the other).

Confirming uses a one-time token valid for five minutes. The plan records
the firmware's SHA-256 and size; when you confirm, and again right before the
first byte is written, mCode checks that the file is still exactly what you
confirmed. If it was built again in between, nothing is written and mCode
asks again (the job log shows the SHA-256 it flashed). Erasing the whole flash (ESP32) is a
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
