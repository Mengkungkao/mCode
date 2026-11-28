# mCode Studio

An AI embedded development studio for **mFruit OS**: talk to Claude about
firmware, let it write and fix ESP32 and Arduino code in a real project, build
it with the real toolchains, and flash it to your board, from the Whisplay
screen with one button and your voice, from a full IDE on an HDMI desktop or
a laptop, or from an iPhone.

```text
Press and hold → “Create an ESP32 project for eight servos with a PCA9685”
→ Claude writes the firmware → Build → Flash firmware? → hold to confirm → done
```

App id `mcode` · mFruit OS 1.4 or newer · Python 3.9+ · Pillow · standard
library otherwise.

## What it does

| | |
|---|---|
| **AI Engineer** | Claude Code with your own Claude account works on the project's files: creates firmware, explains code, fixes build errors, reviews code, writes wiring docs, analyses serial output. The chat works like Claude Code's in VS Code: each step as it happens, the mode, the model and your plan's usage at hand. Four modes: *Manual* (review a diff, then apply), *Auto* (edits and builds, with Undo), *Auto + git*, *Plan* (a plan, no changes). Claude can never flash, erase or install. |
| **Projects** | ESP32, ESP32-S2/S3/C3/C5/C6/H2, Arduino Uno/Mega/Nano/Leonardo/Micro/Due/Nano ESP32, Raspberry Pi Pico, generic C/C++. Arduino CLI, PlatformIO and ESP-IDF layouts, backups before every risky change, git, import/clone/zip. |
| **Build & Test** | Real compilers through the Arduino CLI, PlatformIO, ESP-IDF or make; problems with file and line; flash/RAM use; host unit tests (`pio test -e native`, `make test`), compile checks, static analysis. |
| **Hardware** | USB serial boards found from the kernel's own description (VID/PID, driver, serial number), hot-plug without polling, CP210x/CH340/CH9102/FTDI/native USB, esptool chip detection. |
| **Flashing** | Always two steps: mCode's checks (firmware built and current, board connected, port free, the board matches the project), then your confirmation. |
| **Serial monitor** | Baud 300–2 000 000, ASCII/hex, timestamps, pause, search, save, send, board reset; it steps aside during uploads. |
| **Voice** | Hold to talk on the Whisplay microphone, spoken or tone feedback on its speaker (speech service of your choice; typing always works). |
| **Desktop IDE** | A browser IDE (CodeMirror editor, explorer, AI panel with diffs, build output, problems, terminal, serial monitor, hardware, libraries, git, settings), served by mCode itself. |
| **iPhone and tablets** | The same IDE with a phone layout (tab bar, full-screen editor and AI chat, build view, serial monitor, bottom sheets) and a tablet layout; Add to Home Screen. Pair a phone by scanning the LCD's QR code. |
| **Security** | One session per paired browser with Viewer, Developer or Full access; single-use links and pairing codes; sign browsers out one by one; the terminal and device settings need full access. |

## Quick start

On an mFruit OS device: install from the Fruit Store, or sideload this folder
(`mfruitctl sideload ~/mCode`). Open **mCode Studio**, then:

1. **Settings › Development tools**: install the Arduino CLI and/or
   PlatformIO (and Claude Code if it is not there).
2. **AI Engineer**: connect your Claude account (scan the QR code, sign in,
   type or paste the code).
3. **My Projects › New project**: platform, board, tool, name.
4. Hold the button and say what the firmware should do.

On a Linux computer: `./run.sh --desktop` opens the IDE in your browser.

```bash
./run.sh --desktop            # the desktop IDE
./run.sh --diagnose           # what mCode finds: tools, Claude, boards, audio
./run.sh cli --help           # the command line (projects, build, upload, monitor, ai)
./run.sh --preview DIR        # every LCD screen as PNG
bash tools/check.sh           # everything CI runs
python3 tools/benchmark.py    # start-up, memory, CPU, latency, page weight
```

## Documentation

| For | Read |
|---|---|
| Using mCode | [User guide](docs/USER_GUIDE.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) |
| Installing it and its tools | [Installation](docs/INSTALLATION.md) · [Toolchains and boards](docs/TOOLCHAINS.md) · [Hardware](docs/HARDWARE.md) |
| Claude Code | [Claude Code in mCode](docs/CLAUDE_CODE.md) |
| Settings | [Configuration](docs/CONFIGURATION.md) |
| Safety | [Security](docs/SECURITY.md) |
| Phones and tablets | [iPhone and mobile](docs/IOS_MOBILE.md) |
| Developers | [Architecture](docs/ARCHITECTURE.md) · [mFruit OS integration](docs/MFRUIT_OS.md) · [Testing](docs/TESTING.md) · [Performance](docs/PERFORMANCE_BENCHMARK.md) · [Release checklist](docs/RELEASE_CHECKLIST.md) · [Implementation report](docs/IMPLEMENTATION_REPORT.md) · [Hand-off](CONTINUE.md) |
| 2.0 work | [Audit](docs/AUDIT_2.0.md) · [Roadmap](docs/ROADMAP_2.0.md) · [Security audit](docs/SECURITY_AUDIT_2.0.md) |

## Status

Version 0.2.0. What has been verified, and how, is in [CONTINUE.md](CONTINUE.md)
and the dated records in [docs/records/](docs/records/). An ESP32-C5 flash
write was verified by esptool on an Orange Pi Zero 2W (0.1.1); the Whisplay
LCD itself, the other boards and a real iPhone have **not** been verified
yet.

Claude Code is Anthropic's product; mCode uses your own installation and
login and never handles your password or tokens.
# mCode
