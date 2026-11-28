# Changelog

## [0.2.0] — 2026-11-28

First step of the "mCode Studio 2.0" work: an audit, the fixes it found, a
phone and tablet interface, and per-browser security. Details:
[audit](docs/AUDIT_2.0.md), [roadmap](docs/ROADMAP_2.0.md).

- **iPhone and tablets.** The IDE has a phone layout (bottom tab bar: Home,
  Projects, AI Engineer, Devices, Settings; full-screen editor with code
  keys above the keyboard; chat-style AI Engineer; build view; mobile serial
  monitor; bottom sheets), a tablet layout and the desktop layout, all from
  the same page. iOS design conventions (system font and colours, grouped
  lists, 44 px targets, safe areas, keyboard-aware height, no zoom on focus),
  Dark / Light / Automatic theme, *Add to Home Screen* with its own icon.
  [iPhone and mobile](docs/IOS_MOBILE.md).
- **Paired browsers, one by one.** Each browser now has its own session
  (only a hash is stored), with an access level: Viewer, Developer (default
  for new pairings) or Full access (terminal, folders outside the project
  folder, tools, device and security settings). Pairing works only while
  the LCD's *Pair a computer* screen (or a full-access browser's Security
  page) is open, with a single-use QR code and 6-digit code. The link that
  opens the IDE on the device works once. Settings › Security lists browsers,
  signs one or all others out and shows security events; the LCD's *Sign out
  all computers* signs out every browser. Browsers paired with 0.1.x keep
  full access and are moved to a session on their next visit.
- **Safer AI.** mCode's build, test, check and clean tools refuse to run for
  Claude while its own risky edits (partition table, dependencies, build
  scripts, git settings) wait for your decision; build scripts
  (`CMakeLists.txt`, Makefiles, `*.cmake`, `*.mk`, Python and shell scripts)
  are held by default (*Hold Claude's build-script changes*); `.git`
  settings Claude changed are put back. Advanced mode no longer has a shell:
  git goes through mCode's tools. Stopping a request also stops the build it
  started.
- **Safer flashing.** A build and an upload of the same project never run
  at the same time, and the firmware's SHA-256 is checked again right before
  writing: a rebuild after you confirmed is refused instead of flashed.
- Fix: opening or diffing a project ran programs named in its own
  `.git/config` (file-system monitor, filter and diff drivers).
- Fix: importing a zip file never worked ("Invalid JSON."); imports are now
  streamed and a leftover from an interrupted import no longer blocks them.
- Fix: serial lines received while the monitor was paused never reached the
  browser; a failed port hand-over could leave the monitor suspended.
- Fix: every finished job stayed in memory with its output.
- Fix: idle connections and event streams of phones that slept stayed open,
  until "Too many open windows"; the page now reloads its state when the
  connection comes back.
- Faster pages: static files compressed and cached (first load 242 KB instead
  of 1.3 MB on every visit); the terminal library loads when needed; build
  output drawn once per frame.
- AI Engineer: suggested actions (Create firmware, Explain code, Fix build
  errors, Improve performance, Review security, Wiring doc, Explain serial
  output), the mode's permissions shown above the text box, *Try again*.
- New settings: `ai.hold_build_scripts`, `web.pair_role`,
  `web.session_days`, `web.extra_hosts` (HTTPS tunnel or proxy names);
  `ui.theme` gains `auto`. Settings that name programs or open the device to
  the network need full access.
- `tools/benchmark.py` measures start-up, memory, CPU, latency and page
  weight; [results](docs/PERFORMANCE_BENCHMARK.md).
- Tests: 178 → 211, each fix with a regression test that failed without it.
- NOT VERIFIED: anything on an iPhone, Android phone or iPad (checked in
  headless Chromium only); the new LCD pairing screen on a real Whisplay
  display; pairing a real phone; flashing a board; a real Claude request with
  the new Advanced-mode git tools; HTTPS through a tunnel; upgrading a board
  that has browsers paired with 0.1.x.

## [0.1.8] — 2026-11-27

- Fix: the AI Engineer could fail every request with "Not finished ·
  error_during_execution". When the conversation mCode continues
  (`claude -p --resume`) cannot be found, Claude Code 2.1 reports a result
  `error_during_execution` (with "No conversation found…" in `errors`)
  instead of only exiting, so mCode's recovery never ran. mCode now starts a
  new conversation once in that case, and only remembers a conversation
  that really started (a request that failed before its first turn left a
  session id with nothing to continue, so every later request failed too).
- Claude failures are explained with Claude Code's own reason (its `errors`)
  instead of the bare subtype, and logged in `mcode.log` (redacted);
  "error_max_turns" says Claude used up its steps.

## [0.1.7] — 2026-11-27

- Faster ESP-IDF builds:
  - New ESP-IDF projects build only `main` and the components it needs
    (`idf_build_set_property(MINIMAL_BUILD ON)`, as Espressif's own examples
    do): the ESP32-C5 starter project needs 571 build steps instead of 1120.
    *Board settings › Fast build* switches it for any ESP-IDF project. When a
    header such as `esp_wifi.h` is then missing, the build screen says to add
    the component to `REQUIRES` in `main/CMakeLists.txt` (Claude is told too).
  - The first build gives the target to the first configure
    (`idf.py -DIDF_TARGET=…`) instead of a separate `set-target`.
  - With ccache installed (`sudo apt install ccache`) ESP-IDF compiles through
    it (`IDF_CCACHE_ENABLE=1`), so *Rebuild*, a board change or a second
    project reuse what was compiled. `--diagnose` lists ccache.
  - Measured on the dev machine (4 jobs, ESP32-C5, ESP-IDF 6.1.1): first build
    128 s → 60 s; a build after one change 6 s.

## [0.1.6] — 2026-11-27

- **Board settings** for every project (LCD: Project › Board settings;
  desktop IDE: Project settings), like the Arduino IDE's Tools menu: board,
  USB CDC on boot, upload speed, port, and for Arduino CLI projects the
  board's own menus read from the installed core (`arduino-cli board
  details`: CPU frequency, flash size, partition scheme, debug level…). An
  ESP32 identified on the port is offered first (*Use ESP32-C3*). Only values
  the tools define are accepted; "Erase all flash before upload" is left
  out (erasing stays a separate, confirmed action).
- Fix: changing a PlatformIO project's board (or upload speed, USB CDC on
  boot) only changed mCode's record; `platformio.ini` kept building for the
  old board. `platformio.ini` and `sketch.yaml` now follow the settings,
  edited line by line inside the project's environment only.

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
