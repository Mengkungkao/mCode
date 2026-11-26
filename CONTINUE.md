# CONTINUE — mCode Studio hand-off

Started 2026-11-27 at the owner's request: build **mCode Studio**, an AI
embedded development studio, in `/home/meng/mCode` as an mFruit OS app (id
`mcode`), Whisplay-first (second brief: "Whisplay HAT First UI/UX Redesign"),
with a desktop IDE for HDMI. mFruit OS itself is not changed. Nothing is
committed yet: the owner commits and pushes; commit bodies are dot points
with **no co-author or agent lines**. No GitHub repository exists yet (the
manifest names `github.com/Mengkungkao/mCode`).

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
| ESP-IDF projects | IMPLEMENTED | SOFTWARE TESTED (command generation only) | BLOCKED (ESP-IDF not installed here) |
| Host unit tests | IMPLEMENTED | real `pio test -e native` 2/2 | N/A |
| USB detection | IMPLEMENTED | SOFTWARE TESTED (simulated sysfs; netlink bind checked) | NOT VERIFIED (no board connected) |
| Arduino flashing | IMPLEMENTED | SOFTWARE TESTED (fake uploader; checks, tokens, monitor pause) | NOT VERIFIED |
| ESP32 flashing | IMPLEMENTED | SOFTWARE TESTED (fake uploader) | NOT VERIFIED |
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

## Copied to the .130 board (2026-11-27)

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
