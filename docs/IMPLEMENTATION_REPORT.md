# mCode Studio — implementation report and plan

Written 2026-11-27, before Phase 1, from an inspection of mFruit OS
(`~/MFruitOS`, HEAD `e71663f`, mFruit OS 1.6.x, SDK 1.5.0), its sibling app
repositories (mLearning, mDisplay) and this development machine. Status words
follow mFruit OS's quality language: IMPLEMENTED, AUTOMATED (software tested),
DEVICE / HARDWARE VERIFIED, NOT VERIFIED, PLANNED, BLOCKED.

## 1. What mFruit OS is, as far as an app is concerned

| Area | Finding | Consequence for mCode |
|---|---|---|
| App architecture | Apps are separate packages with a `manifest.json`; mFruit OS's `ApplicationManager` is the only launch authority; whisplay-daemon starts the app through `mfruit-run` | mCode is its own repository and package (id `mcode`), like mLearning and mDisplay. **mFruit OS itself is not changed.** |
| Registration and lifecycle | `exit_gesture: "none"`, `disable_esc_exit_key: true`, `own_escape_key()` before taking the screen, focus → framebuffer, stop within 3 s of `app_exit_requested`, exit completely when the user leaves | Same daemon client as mLearning (`app/lcd/whisplay.py`); leaving mCode stops its builds, uploads, AI turns and the desktop server |
| Display | Whisplay LCD **240 × 280**, rounded corners, RGB565 shared framebuffer owned by whisplay-daemon; SDK geometry: status bar to y 40, content 40–248, footer at 256 | The LCD interface is designed for 208 px of content: one idea per screen, large type, 2 × 2 home tiles |
| Input | **One button** plus optional USB/Bluetooth keyboard, interpreted only by the SDK's `InputController`: tap = next, 2× = previous, hold then release = select, 4× = back, 3× = screen extra; on talk screens a hold talks while held | See the decision in §5: mCode uses these controls, not the brief's "short = select, long = back" |
| Audio | WM8960 codec; shared ALSA devices `capture` (dsnoop) and `playback` (dmix), 48 kHz stereo; on opi2w the microphone is on the left channel; the ADC needs ~0.7 s to power up | Microphone and speaker code adapted from mLearning (`devices.resolve`, `aplay`/`arecord`), push-to-talk with the stream opened when the AI screen opens |
| HDMI | No platform display service for HDMI; apps that use HDMI (mDisplay) find the desktop session themselves | The desktop IDE is a local web page served by mCode; on a desktop session mCode opens it in the browser; on a headless board it is reached from a computer on the LAN after pairing on the LCD |
| Host abstraction | Hardware is behind whisplay-daemon; apps must not drive GPIO, the display or keyboards themselves; battery/Wi-Fi via `mfruit_sdk.status` | mCode uses the SDK for status, the daemon for the LCD and button; USB serial ports of development boards are ordinary Linux devices outside the Whisplay host |
| Settings and storage | Code in `WHISPLAY_OS_APP_DIR` (replaced on update); data in `WHISPLAY_OS_APP_DATA` (snapshotted before each update) | Settings, tokens, logs and AI workspaces in the data folder; **projects in `~/mcode-projects`** (configurable) so updates never snapshot large build folders and uninstalling never deletes projects |
| Updater | Fruit Store / GitHub releases: a new `manifest.json` version publishes a release; packages must stay under 100 MB | mCode's package stays small: Python + vendored web editor; toolchains are installed on demand into the data folder or the user's home |
| Permissions | Apps run as the login user; hooks must never use `sudo` | Serial ports need the `dialout` group (checked and explained, not changed); tool installers are user-level |

Relevant files read: `docs/apps/APP_CONTRACT.md`, `SDK.md`, `MANIFEST.md`,
`UI_GUIDELINES.md`, `TESTING.md`, `mfruitos/sdk/*`, the app template,
`tests/real_daemon/runner.py`, mLearning's `whisplay.py`, `device.py`,
`audio/*`, `envfile.py`, `tools/check.sh`, mDisplay's `display/session.py`.

## 2. Development machine and tools found

| Tool | Found | Notes |
|---|---|---|
| Python | 3.12.3, Pillow 10.2, pyserial 3.5, Tk 8.6 | Boards: 3.10 + Pillow 9.0 (Orange Pi), 3.13 (Pi Zero) |
| Claude Code CLI | 2.1.332 (inside the VS Code extension, not on PATH) | `claude auth status --json`, `auth login/logout`, `-p --output-format stream-json`, `--restricted`, `--mcp-config`, `--permission-mode` |
| Arduino | ESP32 core 3.3.12 in `~/.arduino15` (with esptool 5.3.1, xtensa/riscv compilers); Arduino IDE 1.8.15 | No `arduino-cli`, no AVR core in `~/.arduino15` |
| PlatformIO, ESP-IDF | not installed | |
| Development boards | none connected (no `/dev/ttyUSB*`, `/dev/ttyACM*`) | Flashing cannot be hardware verified here |
| Test boards | opi2w (.248) and .130: **unreachable** (no route to host) | No Whisplay device test this session |

Claude Code's headless login was checked with a throw-away config folder (the
real login was not touched): the CLI prints a 79-character
`https://claude.ai/oauth/authorize?…` URL and waits at "Paste code here if
prompted". mCode drives this flow; it never sees a password or token.

## 3. Dependencies

Reused: Python standard library, Pillow (already required by the SDK), the
vendored mFruit SDK, `arecord`/`aplay` (alsa-utils), `git` when present.

Not added to the platform: nothing. mCode needs no change to mFruit OS.

Optional, installed on demand by the user (Settings → Development tools), all
user-level, from official sources:

| Tool | Source | Used for |
|---|---|---|
| Claude Code | official installer `https://claude.ai/install.sh` | AI Engineer |
| arduino-cli | GitHub release archive of arduino/arduino-cli, SHA-256 checked against the release's checksum file | Arduino and ESP32 (Arduino core) build/upload, cores, libraries |
| PlatformIO Core | PyPI `platformio` in a venv in mCode's data folder | PlatformIO projects, unit tests (`pio test`), static analysis (`pio check`) |
| esptool | PyPI `esptool` in the same venv, or the copy inside the Arduino ESP32 core | chip detection, erase, flash verification |
| ESP-IDF | an existing installation (`IDF_PATH`); installing it is documented, not automated (≈2–3 GB) | ESP-IDF projects |
| Speech to text | OpenAI transcription with the owner's key in mCode's `.env`, or any local command (e.g. whisper.cpp) | voice requests; no local model by default (Pi Zero 2 W) |
| Web editor | CodeMirror 5.65.21 and xterm.js 6.0.0 (MIT), vendored as static files | desktop IDE; no network needed at run time |

## 4. Proposed structure

One process, one set of services, two interfaces:

```text
run.sh → app.main
  ├─ Studio (app/studio.py): the single owner of every service below
  │    settings · events · jobs · projects · boards · toolchains · builds
  │    devices (USB) · ports · serial monitor · uploads · libraries
  │    ai (Claude Code adapter, sessions, approvals, action log) · voice
  ├─ LCD interface   (app/lcd/)  — Whisplay: screens, button, voice
  └─ Desktop IDE     (app/web/)  — local HTTP server + single-page IDE
```

| Package | Responsibility |
|---|---|
| `app/core` | settings schema and atomic storage, event bus, job runner (subprocess, streaming, cancel, timeout, lanes), action log with redaction |
| `app/projects` | board catalogue, project model and templates, create/open/import/rename/duplicate/delete (to trash), search, recent, backups, git |
| `app/toolchains` | detection, installers, backends: arduino-cli, PlatformIO, ESP-IDF, esptool, make; compiler diagnostics parser |
| `app/hardware` | USB serial discovery from sysfs, hot-plug from kernel uevents (no polling), identification by VID/PID/driver, termios serial port, port ownership, serial monitor, upload preflight |
| `app/ai` | Claude Code CLI adapter (auth, login, turns, cancel), modes and tool permissions, staging workspace and diffs, MCP server exposing mCode's build/test tools to Claude, context builder |
| `app/voice` | push-to-talk capture, speech-to-text providers, cues and cached spoken phrases |
| `app/lcd` | the Whisplay interface (SDK controls and chrome) |
| `app/web` | the desktop IDE server (token, host and CSRF checks) and static files |

The LCD and the desktop IDE call the same `Studio` methods; neither owns
builds, uploads, files or AI sessions.

## 5. Decisions taken (with reasons)

1. **Button model: mFruit OS controls, not "short = select, long = back".**
   The brief asks for one consistent model; the platform contract
   (`APP_CONTRACT.md` §1, binding for every app) and the owner's earlier
   decision for all app conversions fix it as tap = next, 2× = previous,
   hold-and-release = select, 4× = back. Using another model in one app would
   make the device inconsistent. Every screen shows its gestures in the footer.
   On the AI Engineer screen a hold talks while held (push-to-talk), which is
   the brief's "press the button, speak".
2. **The desktop IDE is a local web application**, not a native window. One
   implementation serves an HDMI desktop on a Raspberry Pi 4/5 (opened in the
   browser), a headless Pi Zero 2 W / Orange Pi (reached from a laptop), and a
   Linux desktop. It binds to 127.0.0.1 by default; LAN access must be switched
   on from the LCD, which shows a pairing link.
3. **Projects live outside the app's data folder** (`~/mcode-projects`), so
   app updates and uninstalls never touch them; project metadata lives inside
   each project (`.mcode/project.json`).
4. **AI changes go through mCode's own approval.** In *Ask before changes*
   Claude works on a staging copy and mCode shows the diff; nothing reaches the
   project until approved. In *Assisted* and *Advanced* mCode backs the project
   up before each turn, so every AI change can be undone. Claude never gets a
   tool that uploads, erases or changes boot/partition settings; those are
   mCode actions that require the user's confirmation on screen.
5. **Claude Code runs in restricted mode** (`--restricted`): file tools
   confined to the project, no shell unless mCode names it, user/project
   settings not loaded, `--permission-prompts none` so anything not allowed is
   denied rather than waiting.
6. **Voice needs a speech service.** No heavy model is installed by default;
   OpenAI transcription (owner's key) or a local command are the providers;
   keyboard input always works.

## 6. Compatibility risks

- Claude Code is a large binary (~200 MB) and needs ~300+ MB RAM: on a Pi Zero
  2 W (512 MB) it may be too heavy; mCode reports memory and recommends a
  bigger host instead of claiming support. Same for ESP32 compiles.
- Serial access needs the `dialout` group; mCode explains it, it does not
  change groups (that needs root).
- whisplay-daemon redraws on `app.register`; mCode follows the documented order.
- mFruit OS stops the app 3 s after the user leaves: long builds stop too.
- The OAuth login on the device needs the code typed on a keyboard or pasted
  in the desktop IDE.
- Board definitions differ between PlatformIO's official `espressif32`
  platform and the Arduino ESP32 core (e.g. C6/H2 have no Arduino framework in
  the official PlatformIO platform); the catalogue lists only combinations the
  tools define.

## 7. Phased plan

| Phase | Deliverable | Evidence expected |
|---|---|---|
| 1 Foundation | package, manifest, hooks, vendored SDK, settings, LCD shell (home, exit), selftest, previews, check script | unit tests, `check-app.py`, previews |
| 2 Projects and editor | board catalogue, templates, project manager, backups; desktop IDE with editor and file explorer | unit tests; web API tests |
| 3 Toolchains and builds | detection, installers, job runner, arduino-cli / PlatformIO / ESP-IDF / make backends, diagnostics | real compiles here (ESP32 via arduino-cli, AVR, PlatformIO) |
| 4 Hardware | USB discovery and hot-plug, identification, preflight, upload, esptool chip detection | fake sysfs/PTY tests; flashing NOT VERIFIED without a board |
| 5 Claude Code | auth/login/logout, turns, modes, staging and approvals, MCP tools, action log, build-fix loop | fake-CLI tests; one small real turn |
| 6 Serial and testing | serial monitor (ASCII/hex, timestamps, search, save), test runners, AI log analysis, voice | PTY tests, fake audio tests |
| 7 Polish | LCD flows end to end, desktop IDE panels, docs, real-daemon contract test | previews, real-daemon run, docs link check |

The running verification table is in [CONTINUE.md](../CONTINUE.md).
