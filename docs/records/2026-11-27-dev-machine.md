# Validation record — 2026-11-27, development machine

| | |
|---|---|
| Date | 2026-11-27 |
| Revision | mCode Studio 0.1.0, working tree (not committed yet) |
| Machine | NVIDIA Jetson Orin Nano developer kit, aarch64, Ubuntu (kernel 6.8.12-tegra), 7.5 GB RAM, 6 CPUs |
| Python / Pillow | 3.12.3 / 10.2.0 |
| mFruit OS checkout | `~/MFruitOS` at `e71663f` (bundled whisplay-daemon, SDK 1.5.0) |
| Claude Code | 2.1.332 (the VS Code extension's binary), signed in with a Claude Pro account |
| Whisplay device | **none** (opi2w .248 and .130 unreachable: "No route to host") |
| Development board | **none connected** |

## Automated

| Check | Result |
|---|---|
| `bash tools/check.sh` (Python 3.9 syntax, shells, whitespace, JS syntax, suite, smoke test, mFruit OS preflight, SDK/contract parity, links, configuration doc) | all passed |
| Unit and integration suite | 127 tests, OK (about 31 s) |
| Real-daemon contract (`tests/test_real_daemon.py`, bundled daemon, simulated board) | launch → focus and first frame 0.87 s; a button tap reaches mCode; 4× from Home exits; `app.exit.request` → gone in 0.51 s |
| `tools/build-release.sh` | `mcode-0.1.0.tar.gz`, 573 207 bytes, 194 files; preflight and smoke test inside the package passed |
| `install.sh` twice on a fresh data folder | exit 0 both times; `.env` 0600, folder 0700 |
| LCD previews (`--preview`) | 39 screens rendered and inspected (layout evidence only) |

## Real tools, run by hand on this machine

| Step | Result |
|---|---|
| Install Arduino CLI through mCode | 1.6.0 from GitHub, SHA-256 matched `1.6.0-checksums.txt` (a wrong checksum-file name, found here, was fixed first) |
| Arduino CLI build, ESP32-S3 template (Espressif core 3.3.12) | succeeded, 27 s; flash 301 922 B (23 %), RAM 22 136 B (6 %) |
| Install `arduino:avr` core through mCode | 1.8.8, succeeded (38 s) |
| Arduino CLI build + compile check, Uno template | succeeded; flash 2 318 B (7 %), RAM 196 B (9 %) |
| Catalogue vs installed cores | all 6 ESP32 and 6 AVR board IDs found; Due, Nano ESP32, Pico: core not installed (not checked) |
| Install PlatformIO through mCode | 6.2.0 into mCode's venv (16 s) |
| PlatformIO build, Uno | succeeded (35 s, first run including toolchain download) |
| PlatformIO `pio test -e native` (ESP32-S3 template's host tests) | 2 test cases, 2 succeeded |
| PlatformIO build, ESP32-S3 | succeeded (265 s first run); flash 298 146 B of 3 342 336 (8.9 %) |
| Claude: one-off flags check (`--restricted`, `acceptEdits`, `--permission-prompts none`) in a scratch folder | Claude edited the file; `apiKeySource: none` (subscription login); $0.0012 |
| Claude, Assisted mode, "print the uptime… build with the mcode build tool" | done in 31 s: read, edit, **MCP build tool → real arduino-cli build** (`source: ai`), summary with flash use; $0.0024 |
| Claude, Ask mode, real build failure (2 errors) → *Fix the build* | review in 6.4 s, project unchanged before approval; approve → backup → apply → automatic rebuild **succeeded**; $0.0026 |
| `claude auth login` headless output (throw-away config folder) | prints a 79-character `https://claude.ai/oauth/authorize?…` link and waits for a code; the real login was not touched |
| Desktop IDE in headless Chromium (Playwright) | dashboard, explorer, editor, completion (`digitalWrite`), save, Save as, wizard, flash checks (blocked without firmware), hardware view, terminal (`echo $((6*7))` → 42), settings, Claude dialog, AI in the bottom panel; no console or page errors, no server tracebacks |

Bugs found by these runs and fixed: the Arduino CLI checksum-file name; the
`boards.txt` reader merging `upload_port.N` IDs; a missing program reported as
"exit 127" instead of "cannot start"; concurrent saves (autosave + Ctrl+S)
colliding on one temporary file (HTTP 500 in the browser) — now a unique
temporary file under a lock, with a regression test (its negative control was
**not** re-run against the old code); editor colours overridden by
CodeMirror's default theme; board-name guesses for Espressif's shared USB ID.

Total Claude usage for these checks: about $0.006 of the Pro plan.

## Not verified

* Anything on a Whisplay HAT: LCD readability, the physical button, keyboard
  through mFruit OS's key hub, microphone and speaker, spoken feedback,
  launch from mFruit OS's Home (ApplicationManager / `mfruit-run`), the
  memory and speed on a Pi Zero 2 W or Orange Pi.
* Flashing, erasing, chip detection and the serial monitor with a real board
  (only PTYs and simulated sysfs).
* ESP-IDF (not installed here), the Arduino SAM / ESP32 (Arduino) / Mbed
  RP2040 cores, uploads to the Pico (UF2) and Nano ESP32 (DFU).
* Completing a Claude sign-in through mCode (would replace the owner's login;
  covered with a fake CLI only), and Claude Code's installer run by mCode.
* Speech recognition and spoken feedback with a real OpenAI key or a local
  recogniser (fake server and fake command only).
* The desktop IDE from another computer over the LAN, and opening it on a
  real HDMI desktop session.
* The GitHub release workflow (no GitHub repository yet).
