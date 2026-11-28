# mCode Studio 2.0 roadmap

From the "mCode Studio 2.0" directive and the [audit](AUDIT_2.0.md). Work is
incremental: each step leaves `bash tools/check.sh` passing and
[CONTINUE.md](../CONTINUE.md) current. Statuses: PLANNED · IMPLEMENTED ·
SOFTWARE TESTED · HARDWARE VERIFIED · NOT VERIFIED · BLOCKED.

Release naming: the next release is **0.2.0**, the first step towards "2.0".
The 2.0 acceptance criteria include real iPhone Safari, Whisplay and
board-flashing evidence, which do not exist yet; calling a release 2.0.0
before that would claim more than was verified.

## Milestone 1 — audit and baseline

| Item | Status |
|---|---|
| Repository audit, confirmed defects vs risks | IMPLEMENTED ([audit](AUDIT_2.0.md)) |
| Baseline test run (178 tests, 10 checks) | SOFTWARE TESTED (dev machine, 2026-11-28; after the work: 211 tests) |
| Threat model | IMPLEMENTED ([security audit](SECURITY_AUDIT_2.0.md)) |

## Milestone 2 — critical fixes (A-01, A-03 … A-11)

| Item | Owner | Status |
|---|---|---|
| Git calls ignore command-running repository settings (A-01) | `projects/gitutil.py` | SOFTWARE TESTED |
| Build and upload of one project never overlap (A-03) | `core/jobs.py` resources | SOFTWARE TESTED |
| Firmware checksum re-checked before flashing (A-04) | `hardware/upload.py` | SOFTWARE TESTED (fake uploader); HARDWARE NOT VERIFIED |
| AI tools refuse while risky edits are pending; git through mCode tools (A-05) | `ai/bridge.py`, `ai/workspace.py` | SOFTWARE TESTED (fake Claude CLI, real MCP server) |
| Ending an AI turn cancels its builds (A-06) | `ai/bridge.py` | SOFTWARE TESTED |
| Job history bounded (A-07) | `core/jobs.py` | SOFTWARE TESTED |
| HTTP time-outs, vanished event streams dropped (A-08) | `web/server.py` | SOFTWARE TESTED (idle time-out; socket options); a real sleeping phone NOT VERIFIED |
| Serial pause keeps lines (A-09); monitor resume (A-10) | `hardware/monitor.py`, `upload.py` | SOFTWARE TESTED (real PTYs) |
| Zip import streamed (A-11); zip import works at all (A-18) | `web/server.py` | SOFTWARE TESTED |

## Milestone 3 — iOS design system and responsive layout

| Item | Status |
|---|---|
| Design tokens (system font, iOS colours, radii, 44 px targets), dark/light/automatic | IMPLEMENTED; checked in headless Chromium |
| Phone layout: bottom tab bar (Home, Projects, AI Engineer, Devices, Settings), full-screen views, bottom sheets | IMPLEMENTED; headless Chromium (iPhone 13 profile); real iPhone NOT VERIFIED |
| Tablet layout: side navigation, split project/AI, touch resizing | IMPLEMENTED (desktop layout with touch sizes, pointer-event resizing); real tablet NOT VERIFIED |
| Desktop layout preserved (activity bar, explorer, editor, AI, terminal, output, problems, serial, hardware) | IMPLEMENTED; headless Chromium 1440×900 |
| Safe areas, `viewport-fit=cover`, `100dvh`, 16 px inputs, no pull-to-refresh, keyboard-aware views | IMPLEMENTED; iOS Safari NOT VERIFIED |
| Web app manifest and home-screen icon (no service worker: plain HTTP is not a secure context) | IMPLEMENTED; Add to Home Screen NOT VERIFIED |

## Milestone 4 — mobile functionality

| Item | Status |
|---|---|
| Home dashboard: current project, board, build, AI, activity, quick actions | IMPLEMENTED |
| Project cards and actions sheet (open, rename, duplicate, export, import, delete) | IMPLEMENTED |
| Archive a project | PLANNED (not in the backend yet) |
| AI Engineer full screen: suggested actions, mode explanation, stop, retry, diff review | IMPLEMENTED (attachments: open file and serial output, as before; photos PLANNED) |
| Mobile editor: full screen, line wrapping, accessory keys, save/build/AI in the header | IMPLEMENTED; touch selection on iOS NOT VERIFIED |
| Build view: progress, duration, problems, sizes, tests, ask AI | IMPLEMENTED |
| Mobile serial monitor | IMPLEMENTED |
| Reconnect refresh (A-12), bounded browser state, batched output (A-13) | IMPLEMENTED; reconnect checked in headless Chromium (server restart) |

## Milestone 5 — security

| Item | Status |
|---|---|
| Per-browser sessions, revocation, idle expiry (A-02) | SOFTWARE TESTED |
| One-time links; pairing window with random single-use code and QR token (A-02) | SOFTWARE TESTED; LCD screen on a real display NOT VERIFIED |
| Roles viewer / developer / admin; terminal and device settings admin-only (A-16) | SOFTWARE TESTED |
| Security page: sessions, network access, recent security events | IMPLEMENTED (API tested; page checked in headless Chromium) |
| `Origin` check, plain error messages (A-17) | SOFTWARE TESTED |
| HTTPS through a tunnel or reverse proxy (`web.extra_hosts`, `Secure` cookie) | IMPLEMENTED (host names tested); a real tunnel NOT VERIFIED |
| Vendored library checksums in CI (R-05) | IMPLEMENTED (`vendor/SHA256SUMS`, checked by `tools/check.sh`) |
| OS-level sandbox for host tests (bubblewrap where installed) | PLANNED (later) |
| OS keyring for the OpenAI key | PLANNED (later; headless boards have none) |

## Milestone 6 — performance

| Item | Status |
|---|---|
| gzip + ETag for static files (A-15) | SOFTWARE TESTED |
| Benchmark report: start-up, idle RAM/CPU, API latency, page weight | IMPLEMENTED (`tools/benchmark.py`, [report](PERFORMANCE_BENCHMARK.md)); dev machine measured; boards and iPhone NOT VERIFIED |

## Milestone 7 — verification (needs the owner's hardware)

| Item | Status |
|---|---|
| iPhone Safari portrait/landscape, keyboard, safe areas, home-screen app | NOT VERIFIED |
| Whisplay HAT, button, microphone, speaker on a board | NOT VERIFIED |
| Flashing Uno, Nano, ESP32-S3, ESP32-C3, ESP32-C5 from the phone | NOT VERIFIED |

## Milestone 8 — release

| Item | Status |
|---|---|
| CHANGELOG, README, docs, manifest 0.2.0 | IMPLEMENTED (not committed: the owner commits) |
| Release checklist | IMPLEMENTED ([checklist](RELEASE_CHECKLIST.md)) |
| Upgrade from 0.1.8 keeps projects, settings, paired browsers | SOFTWARE TESTED (legacy cookie migration); on a board NOT VERIFIED |
