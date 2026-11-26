# Toolchains and boards

mCode drives the official tools; it does not bundle compilers. Install what
your boards need from **Settings › Development tools** (LCD or desktop IDE)
or with the commands below. Everything installs as your user, never with
sudo.

| Tool | mCode installs it | Where | For |
|---|---|---|---|
| Arduino CLI | yes: GitHub release, SHA-256 checked against the release's checksum file | `<data>/tools/bin/arduino-cli` | Arduino-framework projects for every board in the catalogue |
| Arduino cores | yes: `arduino-cli core install` from the official index | `~/.arduino15` (shared with the Arduino IDE) | the board definitions and compilers |
| PlatformIO Core | yes: `pip install platformio` from PyPI into mCode's own venv | `<data>/tools/venv` (platforms and toolchains in `~/.platformio`) | PlatformIO projects, `pio test`, `pio check` |
| esptool | yes: from PyPI into the same venv; the Arduino ESP32 core also ships one | `<data>/tools/venv/bin/esptool` | chip detection, erase, ESP-IDF flashing |
| ESP-IDF | no (2–3 GB): see below | `~/esp/esp-idf` or Settings › ESP-IDF folder | ESP-IDF projects |
| make, gcc | no: `sudo apt install build-essential` | system | Generic C/C++ projects, PlatformIO native tests |
| cppcheck | no: `sudo apt install cppcheck` | system | static analysis of Arduino sketches |

`<data>` is `~/.whisplay-os/apps/mcode/data` under mFruit OS and
`~/.local/share/mcode` otherwise. `./run.sh cli tools` lists what mCode
finds; `./run.sh --diagnose` says what is missing and how to get it.

## By hand

```bash
# Arduino CLI (official script; mCode's own installer does the same with a checksum check)
curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | BINDIR=~/.local/bin sh
arduino-cli core update-index --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core install esp32:esp32 --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core install arduino:avr          # Uno, Mega, Nano, Leonardo, Micro
arduino-cli core install arduino:sam          # Due
arduino-cli core install arduino:esp32        # Nano ESP32
arduino-cli core install arduino:mbed_rp2040  # Raspberry Pi Pico

# PlatformIO Core (any Python venv works; tell mCode where pio is in Settings)
python3 -m venv ~/.local/share/mcode/tools/venv
~/.local/share/mcode/tools/venv/bin/pip install platformio esptool

# ESP-IDF (a stable release tag; several GB; takes a long time on a Pi)
mkdir -p ~/esp && cd ~/esp
git clone -b v6.1.1 --recursive --depth 1 https://github.com/espressif/esp-idf.git
./esp-idf/install.sh esp32,esp32s2,esp32s3,esp32c3,esp32c6,esp32h2
# then Settings > ESP-IDF folder = ~/esp/esp-idf (or export IDF_PATH)
```

v6.1.1 was the newest stable tag on 2026-11-27. If Espressif's
[Get Started guide](https://docs.espressif.com/projects/esp-idf/) gives
different installation steps for your release, follow it; mCode needs a
folder with `tools/idf.py` and `export.sh`, and the tools installed by
`install.sh`. mCode's ESP-IDF support (set-target, build, fullclean,
flash with `flash_args`) has **not** been run against a real ESP-IDF yet.

## Boards

The catalogue lists only combinations the tools themselves define. ESP32
board IDs were checked against the Espressif Arduino core 3.3.12 and the AVR
IDs against the Arduino AVR core mCode installed on 2026-11-27; the Due,
Nano ESP32 and Pico IDs come from Arduino's documentation and have not been
checked against an installed core
(`tests/test_projects.py` compares every catalogue FQBN with any installed
core's `boards.txt`).

| Board | MCU | Arduino CLI FQBN | PlatformIO board (frameworks) | ESP-IDF target |
|---|---|---|---|---|
| ESP32 DevKit | ESP32 | `esp32:esp32:esp32` | `esp32dev` (arduino, espidf) | `esp32` |
| ESP32-S2 | ESP32-S2 | `esp32:esp32:esp32s2` | `esp32-s2-saola-1` (arduino, espidf) | `esp32s2` |
| ESP32-S3 | ESP32-S3 | `esp32:esp32:esp32s3` | `esp32-s3-devkitc-1` (arduino, espidf) | `esp32s3` |
| ESP32-C3 | ESP32-C3 | `esp32:esp32:esp32c3` | `esp32-c3-devkitm-1` (arduino, espidf) | `esp32c3` |
| ESP32-C6 | ESP32-C6 | `esp32:esp32:esp32c6` | `esp32-c6-devkitc-1` (espidf only) | `esp32c6` |
| ESP32-H2 | ESP32-H2 | `esp32:esp32:esp32h2` | `esp32-h2-devkitm-1` (espidf only) | `esp32h2` |
| Arduino Uno | ATmega328P | `arduino:avr:uno` | `uno` | — |
| Arduino Mega 2560 | ATmega2560 | `arduino:avr:mega:cpu=atmega2560` | `megaatmega2560` | — |
| Arduino Nano | ATmega328P | `arduino:avr:nano:cpu=atmega328` | `nanoatmega328new` | — |
| Arduino Nano (old bootloader) | ATmega328P | `arduino:avr:nano:cpu=atmega328old` | `nanoatmega328` | — |
| Arduino Leonardo | ATmega32U4 | `arduino:avr:leonardo` | `leonardo` | — |
| Arduino Micro | ATmega32U4 | `arduino:avr:micro` | `micro` | — |
| Arduino Due (programming port) | AT91SAM3X8E | `arduino:sam:arduino_due_x_dbg` | `due` | — |
| Arduino Nano ESP32 | ESP32-S3 | `arduino:esp32:nano_nora` | `arduino_nano_esp32` | — |
| Raspberry Pi Pico | RP2040 | `arduino:mbed_rp2040:pico` | `pico` | — |
| Generic C/C++ | any | — | — | — (make) |

Not claimed: every clone and third-party board. A clone that behaves like one
of these usually works with that entry (Nano clones often need the old
bootloader). Other boards supported by an installed core can be used by
opening an existing project folder; they are not offered in the wizard.

The official PlatformIO `espressif32` platform has no Arduino framework for
the C6 and H2, so mCode offers the Arduino CLI (Espressif core 3.x) or ESP-IDF
for those. Uploading to the Raspberry Pi Pico (UF2) and to the Nano ESP32
(DFU) goes through the Arduino CLI's own upload tools; neither has been
tested on hardware.

## What each action runs

| Action | Arduino CLI | PlatformIO | ESP-IDF | Make |
|---|---|---|---|---|
| Build | `compile --fqbn F --build-path .mcode/build` | `pio run -e ENV` | `idf.py set-target T` (first time), `idf.py build` | `make` |
| Rebuild | `compile --clean` | `-t clean`, then build | `fullclean`, then build | `make clean`, `make` |
| Clean | removes `.mcode/build` | `pio run -t clean` | `idf.py fullclean` | `make clean` |
| Test | compile check with all warnings (+ cppcheck if installed) | `pio test -e native` when the project has a native env, else a test build | compile check | `make test` |
| Static analysis | cppcheck | `pio check` | — | — |
| Upload | `upload -p PORT --input-dir .mcode/build` (`--verify` for AVR/SAM) | `pio run -t nobuild -t upload --upload-port PORT` | `esptool write-flash @flash_args` | — |

Uploads write the firmware that was built and checked; they never rebuild
first.
