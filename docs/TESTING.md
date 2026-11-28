# Testing

Status words: IMPLEMENTED (in the source), SOFTWARE TESTED (an automated test
or a recorded run on a computer passed), HARDWARE VERIFIED (seen working on a
named device). One never stands in for another.

## Commands

```bash
bash tools/check.sh                                     # everything CI runs
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t .
sh test.sh                                              # the install smoke test
./run.sh --preview /tmp/mcode-preview                   # every LCD screen as PNG
python3 tools/benchmark.py --idle 60                    # start-up, memory, CPU, latency, page weight
MCODE_SKIP_REAL_DAEMON=1 python3 -m unittest …          # without the real-daemon tests
```

`tools/check.sh`: Python 3.9 syntax (the boards run 3.10–3.13), shell syntax,
trailing whitespace, JavaScript syntax (`node --check`, when node exists), the
suite, the smoke test, mFruit OS's package preflight (`MFRUIT_OS`, default
`~/MFruitOS`), the vendored SDK and contract copies, Markdown links, and the
generated configuration document.

## What the suite covers, and with what

| Level | Tests | Stand-ins |
|---|---|---|
| Unit | settings, storage, events, redaction, catalogue, templates, file boundary, diagnostics parser, backend commands, QR (decoded with OpenCV when installed), audio conversion, stream-json translation, risky-change rules | — |
| Integration | job runner (real processes: streaming, cancel kills the group, timeouts, lanes), projects, backups and restore, builds through a fake `arduino-cli`, checksum-verified installer (fake downloads), USB discovery (fake sysfs), serial port and monitor (real PTYs), upload checks and flow, AI requests (fake `claude` CLI: Ask/Assisted/Advanced, approvals, held changes, undo, sessions, failures, login), MCP round trip (fake CLI → real `mcp_server.py` → bridge → real mCode build job), voice (fake `arecord`, local fake speech server), LCD (the SDK `InputController` with a fake clock, every screen rendered, footer = handlers), desktop server (access control, API, SSE, real PTY terminal) | fake tools are small scripts in `tests/helpers.py` |
| Host contract | `tests/test_real_daemon.py`: the real whisplay-daemon code (mFruit OS's bundled copy, simulated board): launch, first frame, button, 4× exit, `app.exit.request` within 3 s | simulated board |
| Security (0.2.0) | git never runs a repository's programs (`GitSafetyTests`: fsmonitor, filters, textconv, external diff, gpg, hooks); sessions, single-use links, pairing window, roles, Origin/Host, legacy cookie migration, idle and vanished connections, zip import (`test_web`); AI tools refuse while risky edits wait, `.git` restored, turns cancel their builds, git tools (`BridgeSafetyTests`, `McpTests`) | real git, real HTTP server, fake Claude CLI |
| Flash safety (0.2.0) | build and upload of one project never overlap; firmware SHA-256 re-checked; monitor resumes after a failed hand-over (`UploadTests`) | fake uploader |

Not covered by the suite (needs hardware or accounts): a real Claude request
(done by hand, see the records), real compilers (done by hand), flashing a
board, the Whisplay LCD, button, microphone and speaker, and any browser:
CI has none. Phone, tablet and desktop layouts are checked by hand with
headless Chromium (Playwright) and recorded; that is not Safari.

Every regression test for a confirmed bug was run once against the code
without its fix (negative control) and failed there; the
[audit](AUDIT_2.0.md) names the tests.

## Recorded runs

Dated evidence lives in [records/](records/). Each says what ran, on which
machine and revision, and what was **not** verified.

## Phone checklist (not done yet)

On an iPhone with Safari, on the same Wi-Fi as the device:

1. Pair by scanning the LCD's QR code; Settings › Security lists the phone.
2. Portrait and landscape: tab bar, header, nothing under the notch or the
   home indicator; Add to Home Screen opens full-screen with the icon.
3. Editor: open a file, type with the keyboard open (code keys stay above
   it, no zoom), select text with the handles, save, build.
4. AI Engineer: send a request and watch its steps appear, Stop, review a
   diff, Apply; switch the mode and the model from the sheets under the
   message box (it stays above the keyboard); a Plan request offers *Carry
   out this plan*; the Usage sheet shows the plan's limits after a request.
5. Build view while the screen is locked for a minute: the result is shown
   after unlocking.
6. Serial monitor: connect, pause, resume (lines received while paused
   appear), send, search; Flash pauses and resumes it.
7. Sign the phone out from another browser: the phone shows the pairing
   page.

## Device checklist (not done yet)

On a Whisplay device with mFruit OS, a board connected by USB:

1. Sideload, launch from Home, first frame; every Home tile opens and 4× goes
   back; Esc and keyboard navigation.
2. Settings › Development tools: install the Arduino CLI and the ESP32 core.
3. Claude sign-in with the QR code on a phone and the code typed on a
   keyboard.
4. Voice: hold to talk in a quiet and a noisy room; spoken and tone feedback.
5. New ESP32 project by voice name; Ask AI by voice; Apply; Build.
6. Connect Board: plug/unplug; Identify chip; Flash: confirmation, progress,
   "verified"; Serial monitor shows the heartbeat; it pauses for a second
   flash and comes back.
7. A wrong board (Uno connected, ESP32 project): the flash is refused.
8. Desktop IDE from a laptop: pairing code, editor, terminal, flash from the
   browser.
9. Leave mCode during a build: the process is gone within 5 s.
10. Pi Zero 2 W: memory warning, build time of the ESP32 template.
