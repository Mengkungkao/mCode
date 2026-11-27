# CONTINUE — mCode Studio hand-off

Started 2026-11-27 at the owner's request: build **mCode Studio**, an AI
embedded development studio, in `/home/meng/mCode` as an mFruit OS app (id
`mcode`), Whisplay-first (second brief: "Whisplay HAT First UI/UX Redesign"),
with a desktop IDE for HDMI. mFruit OS itself is not changed. The owner
commits and pushes; commit bodies are dot points with **no co-author or agent
lines**. Repository `github.com/Mengkungkao/mCode`; release v0.1.0 is
published (tag at `3eaf245`). 0.1.1 is prepared in the working tree, not
pushed (see "Session 2026-11-27 (later)").

Read first: [docs/IMPLEMENTATION_REPORT.md](docs/IMPLEMENTATION_REPORT.md)
(inspection, decisions, plan), then [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Evidence: [docs/records/2026-11-27-dev-machine.md](docs/records/2026-11-27-dev-machine.md).

## Decisions taken (owner may revisit)

1. **Button model = mFruit OS controls** (tap next, 2× previous, hold-release
   select, 4× back, 3× extra; hold = talk on the AI screen), not the brief's
   "short = select, long = back": the app contract binds every app and the
   owner chose these controls for every converted app.
2. **Desktop IDE = a local web app** served by mCode (127.0.0.1 by default;
   LAN only after pairing on the LCD); opened on the HDMI desktop when one runs.
3. **Projects in `~/mcode-projects`**, outside the app data (never snapshotted
   or deleted with the app).
4. **Claude Code via its CLI in `--restricted` mode** with per-mode tools and
   mCode's MCP tools; no upload/erase/install tool for Claude; Ask mode works
   on a staging copy; partition/bootloader/dependency changes are held.
5. **Voice**: OpenAI transcription with the owner's key in mCode's `.env`, or
   a local command; nothing heavy installed by default.

## Verification table

Statuses: NOT IMPLEMENTED · IMPLEMENTED · SOFTWARE TESTED · HARDWARE VERIFIED · BLOCKED

| Feature | Implementation | Software test | Hardware test |
|---|---|---|---|
| App launch/exit under mFruit OS | IMPLEMENTED | SOFTWARE TESTED (real whisplay-daemon code, simulated board) | NOT VERIFIED (no Whisplay device reachable) |
| Whisplay LCD interface (39 screens) | IMPLEMENTED | SOFTWARE TESTED (SDK InputController, renders, previews) | NOT VERIFIED |
| Project creation | IMPLEMENTED | SOFTWARE TESTED (all boards/toolchains) | N/A |
| Code editor (desktop IDE) | IMPLEMENTED | SOFTWARE TESTED (headless Chromium: edit, complete, save, save as) | N/A |
| Claude Code | IMPLEMENTED | SOFTWARE TESTED (fake CLI) + real turns on the dev machine (Assisted + MCP build; Ask-mode fix-build loop) | N/A |
| Claude sign-in through mCode | IMPLEMENTED | SOFTWARE TESTED (fake CLI); real CLI's link output observed | NOT VERIFIED end to end (would replace the owner's login) |
| Arduino compilation | IMPLEMENTED | SOFTWARE TESTED + real Uno builds (Arduino CLI 1.6.0 + AVR 1.8.8; PlatformIO 6.2.0) | N/A |
| ESP32 compilation | IMPLEMENTED | SOFTWARE TESTED + real ESP32-S3 builds (Arduino CLI + Espressif core 3.3.12; PlatformIO espressif32) | N/A |
| ESP-IDF projects | IMPLEMENTED | Real builds: ESP32-C5 and ESP32-S3 with ESP-IDF 6.1.1 (dev machine); C5 build on the .130 | see ESP32 flashing |
| Host unit tests | IMPLEMENTED | real `pio test -e native` 2/2 | N/A |
| USB detection | IMPLEMENTED | SOFTWARE TESTED (simulated sysfs; netlink bind checked; "no board" help from sysfs) | HARDWARE VERIFIED for an ESP32-C5 on its own USB (303a:1001, ttyACM0) on the .130 Orange Pi Zero 2W, through `cli devices` (LCD list not looked at) |
| ESP32-C5 projects | IMPLEMENTED (0.1.1) | SOFTWARE TESTED + real Arduino CLI build (`esp32c5:CDCOnBoot=cdc`, core 3.3.12) | flashing NOT VERIFIED |
| Arduino flashing | IMPLEMENTED | SOFTWARE TESTED (fake uploader; checks, tokens, monitor pause) | NOT VERIFIED |
| ESP32 flashing | IMPLEMENTED | SOFTWARE TESTED (fake uploader) | Flash write verified by esptool ("Hash of data verified") on the .130: ESP32-C5HF4 rev v1.2, ESP-IDF 6.1.1 project, mCode 0.1.1, 2026-11-27 11:12 (owner's run, job log). Firmware behaviour not observed by me |
| Chip detection / erase (esptool) | IMPLEMENTED | SOFTWARE TESTED (parsing, command names) | NOT VERIFIED |
| Serial monitor | IMPLEMENTED | SOFTWARE TESTED (real PTYs: exclusivity, 74880 baud, hex, search, save, suspend/resume) | NOT VERIFIED |
| Libraries | IMPLEMENTED | SOFTWARE TESTED (partly); real index use NOT VERIFIED | N/A |
| Voice (microphone, speech, feedback) | IMPLEMENTED | SOFTWARE TESTED (fake arecord, fake speech server) | NOT VERIFIED |
| Desktop IDE security (token, host, CSRF, pairing) | IMPLEMENTED | SOFTWARE TESTED | N/A |
| Release package | IMPLEMENTED | built and checked locally (560 KB) | release workflow NOT RUN (no repo) |

`bash tools/check.sh` = CI (suite, smoke test, mFruit OS preflight, SDK and
contract copies, JS syntax, links, generated configuration doc).

## Next (owner)

1. Read the report and decide on the two big decisions (button model, web IDE)
   if you want them different.
2. Commit (suggested subject: `feat: mCode Studio 0.1.0`), create the GitHub
   repository `Mengkungkao/mCode`, push; the release workflow publishes v0.1.0.
3. On a Whisplay device: `mfruitctl sideload ~/mCode` (copy the folder over
   first), then the device checklist in [docs/TESTING.md](docs/TESTING.md#device-checklist-not-done-yet).
4. Connect an ESP32 and an Uno to the device (or to this Jetson with
   `./run.sh --desktop`) and run Build → Flash → Serial monitor; record it in
   `docs/records/`.
5. Optional: put `OPENAI_API_KEY` in mCode's `.env` for voice; install ESP-IDF
   if you want ESP-IDF projects.
6. Fruit Store: add `mcode` to mFruit OS's `config/catalog.json` after the
   first release.

## Session 2026-11-27 (later)

- The commit `eadb164` had deleted the app (a Claude Code rewind removed the
  files the first session wrote); the owner reverted it (`aba6357`).
- .130 board: installed with `mfruitctl sideload`; it ran. After an unclean
  restart (02:08, cause unknown; root fs has `commit=600`) 213 installed files
  were empty and reinstall failed at "backup": a stale Unix socket in the
  data folder broke mFruit OS's `copytree`. Fixed (`c6499fc`, sockets now in
  XDG_RUNTIME_DIR); reinstalled and launched there. The owner then installed
  Claude Code from mCode on that board (02:22, job succeeded).
- ESP32-C5 "not found": the board's power/OTG USB-C port is gadget-only
  (`dr_mode=peripheral`, `/sys/class/udc/musb-hdrc.5.auto`); on the other
  port the C5 enumerated as 303a:1001 → ttyACM0 and mCode listed it. 0.1.1
  adds the C5 to the catalogue and a "What to check" explanation for an empty
  board list ([HARDWARE.md](docs/HARDWARE.md#when-no-board-is-found)).
- Fruit Store (owner's request): needs the 0.1.1 release first. Owner: commit
  and push (manifest 0.1.1 → release workflow publishes v0.1.1). Then add a
  native `mcode` entry to mFruit OS `config/catalog.json` with the release
  asset's `url`, `sha256`, `ref` and `version` (like `mdisplay`); after that
  `catalog-sync` moves the pin by itself on each release.
- Not verified: flashing the C5, Identify chip on the C5 (no esptool on the
  board yet), the LCD screens on the real display.

## Session 2026-11-27 (0.1.6, board settings)

- Owner: an ESP32-C3 would not take an upload "even after changing the
  board", and asked for Arduino-IDE style board configuration. On the .130
  the project (BlinkLED) was still an ESP32-C5 Arduino project while esptool
  identified an ESP32-C3 on ttyACM0, so the match check refused.
- 0.1.6: `toolchains/boardconfig.py` (board, USB CDC on boot, upload speed,
  core menus from `arduino-cli board details`; EraseFlash hidden), LCD
  Project › Board settings, desktop IDE Project settings › Board, API
  `project/<id>/board`. `projects/buildfiles.py` keeps platformio.ini and
  sketch.yaml in step (before, a PlatformIO board change did not reach
  platformio.ini). Real C3 build with CDC on, CPU 80 MHz, 460800 on the dev
  machine; the board's core 4.0.0 lists the same menus. Not flashed.

## Session 2026-11-27 (0.1.5, build speed)

- Owner: compiling is slow. Cause: automatic jobs were 1 on every board
  under 1.1 GB. Measured on the .130 with mCode open: clean ESP32-C5 Arduino
  build 143 s (1 job), 97 s (2), 83 s (4); clean ESP-IDF 6.1.1 build with 4
  jobs 563 s, free memory down to 131 MB + 227 MB zram, no OOM. 0.1.5: jobs
  = min(cores, (MemAvailable - 150) / 160) at build start (3 on the .130).
  PlatformIO's ESP-IDF build took 27.7 min there vs 8.6 min with ESP-IDF.

## Session 2026-11-27 (0.1.4, "Can't flash yet")

- Owner: "Can't flash yet" on the .130. Cause: TestLED (Arduino) and the new
  BlinkLED (PlatformIO/ESP-IDF) were made for *ESP32 DevKit* while the board
  is an ESP32-C5 (esptool: ESP32-C5HF4); mCode's match check refused, as
  designed. The earlier ESP-IDF ESP32-C5 BlinkLED had flashed fine (11:12).
- 0.1.4: on a chip mismatch the LCD offers *Make it ESP32-C5* (same
  toolchain) or *New ESP32-C5 project* (PlatformIO has no C5); the board
  problem shows before "build first"; the wizard lists the connected chip
  first; firmware built for another board/toolchain is refused
  (`BuildService.built_for`, negative control done).

## Session 2026-11-27 (0.1.3, ESP-IDF installer)

- Owner: ESP-IDF could not be installed on the dev machine or the device.
  mCode had no installer for it (docs only). 0.1.3: Settings › Development
  tools › ESP-IDF › Install (`installer.install_esp_idf`): newest stable tag
  by `git ls-remote` (5.x on Python 3.9), shallow recursive clone through
  `esp-idf.mcode-partial` into `~/esp/v<tag>/esp-idf`, Espressif's
  `install.sh all`, `idf_tools.py install cmake ninja`, `idf.py --version`;
  sets `dev.idf_path`. Never touches a folder it did not make. Builds run
  without mCode's venv on PATH and with `IDF_PY_BUILD_JOBS`.
- Dev machine: installed v6.1.1 through mCode (5.9 GB); real builds for
  ESP32-C5 (122 s) and ESP32-S3 (110 s) succeeded.
- Board (.130): the earlier ESP32-C5 Arduino build failures ("undefined
  reference to millis/app_main") came from a build cancelled after 130 s
  that left a partial core.a (Arduino core 4.0.0). Fixed: an interrupted
  Arduino CLI build makes the next one use `--clean`
  (`test_build_after_a_cancelled_one_starts_clean`, negative control done).

## Session 2026-11-27 (0.1.2, sign-in from a phone)

- Problem (owner): on the device, Claude's long sign-in code had to be typed
  on a USB keyboard; "paste it in the desktop IDE" did not say where.
- 0.1.2: the LCD sign-in has two QR codes (tap switches): 1 = Claude's
  sign-in page, 2 = a one-time page on the device (`app/web/codedrop.py`,
  owned by `AIService` login) where the phone pastes the code. Only while the
  sign-in waits; one code; never logged; setting `ai.login_code_page`.
  [Security](docs/SECURITY.md#the-sign-in-code-page).
- Checked on the .130 with the real Claude Code 2.1.332 (throw-away
  CLAUDE_CONFIG_DIR, owner's login untouched): the page opened from another
  computer on the LAN, the pasted (fake) code reached `claude auth login`,
  Claude refused it, the page closed. A real sign-in with the owner's phone
  is NOT verified.
- Fruit Store: a native `mcode` entry pinned to v0.1.1 (asset SHA-256
  checked, preflight passed) is in mFruit OS `config/catalog.json`,
  uncommitted; after it is pushed, `catalog-sync` follows v0.1.2.
- Not committed. Owner: commit + push → release v0.1.2.

## Copied to the .130 board (2026-11-27, first session)

`orangepi@192.168.0.130` (Orange Pi Zero 2W, 981 MB, mFruit OS 1.6.2, Python
3.10.12, Pillow 9.0.1): the folder is in `~/mCode` (rsync, no `.git`). Not
installed into mFruit OS yet. There: `sh test.sh` passed (5.6 s), the suite
passed (127 tests, 4 skipped: real-daemon tests disabled, no OpenCV), and
`--diagnose` found the Whisplay microphone and speaker (`capture`/`playback`),
dialout membership, make/gcc/git, and no Claude Code, Arduino CLI,
PlatformIO, esptool or USB board. Install: `mfruitctl sideload ~/mCode`
(check `mfruitctl jobs` is idle first). The board run exposed a fake-microphone
pacing problem in the tests and an unclosed microphone pipe; both fixed.

## Environment notes

- Dev machine: Jetson (aarch64), Python 3.12. mCode's data folder here is
  `~/.local/share/mcode`: it holds the Arduino CLI 1.6.0 (`tools/bin`) and a
  PlatformIO 6.2.0 venv (`tools/venv`) installed during verification.
  `~/.arduino15` now also has `arduino:avr` 1.8.8; `~/.platformio` has the
  `atmelavr`, `espressif32` and `native` platforms (about 5.6 GB).
- Claude Code 2.1.332 is only inside the VS Code extension; mCode finds it
  there. Real checks used about $0.006 of the Pro plan.
- Test boards opi2w (.248) and .130 were unreachable on 2026-11-27.
- Scratch outputs of this session (previews, browser screenshots, real-tool
  logs) are in the session scratchpad, not in the repository.
