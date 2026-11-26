# Architecture

One process, one set of services, two interfaces. Every responsibility has
one owner; the interfaces and the AI only call services.

```text
run.sh → app/main.py
  ├─ Studio (app/studio.py) ─ owns every service
  │    core/      settings · state · events · jobs · history · redaction · storage
  │    projects/  boards (catalogue) · model · templates · manager · files · backups · git
  │    toolchains/ detect · installer · backends · builds · diagnostics · libraries
  │    hardware/  devices (USB, hot-plug) · ports · serialport · monitor · upload
  │    ai/        claude (CLI) · service (turns, reviews) · workspace · context · bridge + mcp_server
  │    voice/     devices · audio (push-to-talk, player) · speech (STT/TTS) · service
  ├─ lcd/   the Whisplay interface: ui.py (input, navigation, drawing), screens.py, draw.py, qr.py,
  │         whisplay.py (the only whisplay-daemon client besides the SDK)
  ├─ web/   the desktop IDE: server.py (HTTP + SSE API, access control), terminal.py, desktop.py,
  │         static/ (index.html, app.js, app.css, vendored CodeMirror and xterm.js)
  └─ cli.py the command line
```

## Owners

| Responsibility | Owner | Notes |
|---|---|---|
| Settings and their validation | `core/config.py` | one schema (`FIELDS`); docs/CONFIGURATION.md is generated from it |
| Long operations (build, upload, install, AI turn) | `core/jobs.py` `JobManager` | lanes: one build, one upload, one install, one AI turn, two tool jobs; resources (ports) never overlap; cancel kills the process group; time limits |
| Projects: create, open, rename, duplicate, delete, import, recent, current | `projects/manager.py` | metadata in each project's `.mcode/project.json` |
| File access inside a project | `projects/files.py` | the project is a hard boundary (`resolve`) |
| Which boards exist | `projects/boards.py` | only tool-defined IDs |
| Finding tools | `toolchains/detect.py` | read-only; cached |
| Commands for build/clean/test/check/upload | `toolchains/backends.py` | one class per toolchain |
| Running builds | `toolchains/builds.py` | the AI's builds come here too |
| USB boards | `hardware/devices.py` | sysfs + netlink |
| Who uses a port | `hardware/ports.py` | monitor, uploads and chip detection acquire it |
| Writing to boards | `hardware/upload.py` | prepare → confirm token → start; no other code path |
| Talking to Claude | `ai/claude.py` | the only place the CLI is run |
| AI requests, reviews, undo, action log | `ai/service.py` | one request at a time |
| Claude's tools | `ai/bridge.py` | per-request token, per-mode tool set |
| Microphone, speaker, speech | `voice/` | push-to-talk only while the AI screen is open |
| LCD and button | `lcd/` | through the mFruit SDK and whisplay-daemon |

## Data flow of a voice request

```text
button hold ─ whisplay-daemon ─ lcd/whisplay.py ─ SDK InputController ─ AIScreen
  → voice.start_talk (arecord already open: pre-roll) … release → voice.end_talk
  → thread: 48k stereo → 16k mono WAV → speech.transcribe → event "voice: heard"
  → AIScreen "I heard …" → hold on Send → ai.ask(project, text)
  → workspace (stage or snapshot+backup) → context.build_prompt
  → job "ai": claude -p … (stream-json) → events "ai: progress" (steps on both interfaces)
      ↳ Claude calls mcp__mcode__build → mcp_server.py → bridge socket → builds.run → job "build"
  → end: compare → review / done / error → event "ai: turn" → LCD + IDE + spoken "Done."
```

## Threads

* The interfaces' input threads (button, keyboard, HTTP handlers) only change
  state and call services; long work is always a job.
* Jobs run in their own threads; programs in their own process groups.
* Events (`core/events.py`) are delivered on the publisher's thread;
  listeners only record and wake their loop. The desktop IDE receives them
  as Server-Sent Events through a bounded queue per browser.
* The LCD draws on one render thread, only when something changed or while a
  screen animates (4 fps), and only while mCode has the screen.
* USB hot-plug, the serial monitor reader, the microphone reader, the AI tool
  bridge and the desktop server each have one thread.

## Files and folders

| What | Where |
|---|---|
| Code | the version folder mFruit OS installs (`WHISPLAY_OS_APP_DIR`) |
| Settings, state, tokens, logs, backups, AI workspaces, voice cache, mCode's tools | data folder (`WHISPLAY_OS_APP_DATA`, or `~/.local/share/mcode`) |
| Projects | `~/mcode-projects` (setting); trash in `<projects>/.trash` |
| Arduino cores and libraries | `~/.arduino15`, `~/Arduino/libraries` (shared with the Arduino IDE) |
| PlatformIO platforms and packages | `~/.platformio` |
| Claude Code and its login | `~/.local/bin/claude`, `~/.claude` (Claude Code's own) |

## Dependencies

Python standard library and Pillow (already required by the mFruit SDK).
Vendored, static, MIT: CodeMirror 5.65.21 and xterm.js 6.0.0 for the browser.
Everything else (Claude Code, compilers, PlatformIO, ESP-IDF) is an external
tool mCode finds or installs on request. No NumPy (the microphone code is pure
Python), no web framework, no database.
