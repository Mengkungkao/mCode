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

Not covered by the suite (needs hardware or accounts): a real Claude request
(done by hand, see the records), real compilers (done by hand), flashing a
board, the Whisplay LCD, button, microphone and speaker.

## Recorded runs

Dated evidence lives in [records/](records/). Each says what ran, on which
machine and revision, and what was **not** verified.

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
