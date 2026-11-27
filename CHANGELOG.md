# Changelog

## [0.1.5] — 2026-11-27

- Faster builds on small boards: the automatic number of parallel compiles
  (Settings › Compiler jobs = 0) now follows the memory that is free when
  the build starts (one job per CPU core while about 160 MB is left per job,
  3 on the .130) instead of "1 job under 1.1 GB". Measured on the 1 GB Orange
  Pi Zero 2W (.130) with mCode open: clean ESP32-C5 Arduino build 143 s with
  1 job, 97 s with 2, 83 s with 4; clean ESP-IDF build with 4 jobs 563 s,
  free memory down to 131 MB plus 227 MB in zram swap, so 3 is the safer
  default. Free memory is read when each build starts, so a running Claude
  Code session means fewer jobs. Applies to the Arduino CLI, PlatformIO,
  ESP-IDF and Make.

## [0.1.4] — 2026-11-27

- **Can't flash yet** because the connected ESP32 is a different chip than
  the project's (for example an ESP32-C5 on an *ESP32 DevKit* project) now
  offers the way out: *Make it ESP32-C5* (same toolchain: the project's board
  changes and it is rebuilt) or *New ESP32-C5 project* when the project's
  tool has no such board (PlatformIO). The board problem is shown before
  "build first".
- The new-project wizard lists an identified, connected ESP32 first
  ("connected now").
- Fix: firmware built before the project's board was changed no longer
  counts as current (the board is not in a source file); flashing asks for a
  new build.

## [0.1.3] — 2026-11-27

- ESP-IDF can be installed from Settings › Development tools (LCD and desktop
  IDE), and a build that needs it offers the install: the newest stable
  release from Espressif's GitHub with its own `install.sh`, CMake and Ninja,
  into `~/esp/v<version>/esp-idf` and `~/.espressif`, as the user (no sudo).
  It checks git, Python venv and about 7 GB of free space first, and never
  changes an ESP-IDF folder it did not make.
- ESP-IDF builds run without mCode's own Python environment on PATH and with
  the same parallel-job limit as other builds (`dev.build_jobs`; 1 on boards
  with less than 1.1 GB, where ninja's default of CPUs + 2 can run out of
  memory). Checked: real ESP-IDF v6.1.1 builds for ESP32-C5 and ESP32-S3.
- Fix: after an Arduino CLI build was cancelled or timed out, every later
  build of that project failed with "undefined reference to `millis`" /
  "`app_main`": the Arduino CLI reused the half-written core archive. The
  next build after an interrupted one now starts clean (`--clean`).

## [0.1.2] — 2026-11-27

- Claude sign-in on the device in two steps, each a QR code for the phone:
  1. Claude's sign-in page; 2. a one-time page on the device where the phone
  pastes the code Claude shows (no more typing it). Tap switches between the
  steps; a USB keyboard can still type the code. The page exists only while
  the sign-in waits for the code, takes one code and never logs it
  (`ai.login_code_page`, on by default; [Security](docs/SECURITY.md#the-sign-in-code-page)).

## [0.1.1] — 2026-11-27

- ESP32-C5 boards: new projects with the Arduino CLI (`esp32:esp32:esp32c5`,
  Espressif core 3.3 or newer) or ESP-IDF (`esp32c5`). PlatformIO's official
  platform has no C5 board, so it is not offered.
- "No board found" now says why: nothing on USB (cable, power, port), a USB
  port that works only as a device (gadget/OTG, as on the Orange Pi Zero 2W's
  power port), or a device without a serial port. LCD: *Connect Board › What
  to check*; desktop IDE: Hardware; terminal: `cli devices`.
- Fix: reinstalling or updating failed at "backup" after the app had been
  stopped uncleanly. The AI tool bridge kept its Unix socket in the data
  folder, which mFruit OS copies before every install and `shutil.copytree`
  cannot copy a socket. Sockets now live in `XDG_RUNTIME_DIR` (or
  `/tmp/mcode-<uid>`); a stale socket left in `data/run` by 0.1.0 is removed
  at start. Regression tests: `RuntimeFilesTests`.

## [0.1.0] — 2026-11-27

First version.

- Whisplay LCD interface designed for 240×280 and one button (mFruit OS
  controls): Home with AI, project, board status and four tiles; voice-first
  AI Engineer (hold to talk, release to send); projects; new-project wizard;
  Connect Board; build and flash with progress; flash confirmation; serial
  monitor; settings, tools, Claude sign-in with a QR code, desktop pairing.
- Desktop IDE in the browser: editor (syntax highlighting, completion,
  folding, search/replace, error marks), explorer, tabs, AI panel with diffs
  and approvals, build output, problems, integrated terminal, serial monitor,
  hardware manager, libraries, git, settings, resizable/collapsible panels.
- Claude Code integration through the official CLI: browser login,
  sign-out, status; Ask / Assisted / Advanced modes in restricted mode;
  staging copy and approvals; held partition, bootloader and dependency
  changes; Undo; MCP tools for build, tests and read-only context; action log.
- Toolchains: Arduino CLI (checksum-verified install, cores, libraries),
  PlatformIO (installed in mCode's own Python environment), ESP-IDF (existing
  installation), esptool, make; diagnostics, sizes, progress.
- Hardware: sysfs USB discovery with hot-plug, identification by USB ID and
  driver, esptool chip detection, upload checks and confirmation tokens,
  serial monitor with ASCII/hex, timestamps, search and save.
- Voice: push-to-talk on the Whisplay microphone, OpenAI or local-command
  speech recognition, spoken/tone feedback with a phrase cache.
- Tests: 127 automated tests (fake Claude CLI, fake arduino-cli, fake sysfs,
  PTY serial ports, fake audio), real-daemon contract tests, smoke test.
