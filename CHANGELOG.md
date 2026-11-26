# Changelog

## Unreleased

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
