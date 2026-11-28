# Validation record — 2026-11-28, development machine (2.0 directive, 0.2.0)

| | |
|---|---|
| Date | 2026-11-28 |
| Revision | mCode Studio 0.2.0, working tree on top of `4a8e012` (not committed) |
| Machine | NVIDIA Jetson Orin Nano developer kit, aarch64, Ubuntu, kernel 6.8.12-tegra |
| Python / Pillow / git / node | 3.12.3 / 10.2.0 / 2.43.0 / 20.20.2 |
| Browser | Playwright's Chromium 1248 (headless) — **not Safari / WebKit** |
| Whisplay boards | **none**: .130 and .248 did not answer SSH (connection timed out) |
| Development board, iPhone | **none** |

## Baseline (0.1.8, before any change)

`bash tools/check.sh`: all 10 checks passed (43 s); suite 178 tests OK
(38 s).

## Automated, after the changes

| Check | Result |
|---|---|
| `bash tools/check.sh` | all 10 checks passed |
| Unit and integration suite | 211 tests OK (54 s) |
| Negative controls | each new regression test for a confirmed bug (A-01, A-02, A-03, A-04, A-05, A-06, A-07, A-08, A-09, A-10, A-11/A-18, A-15, A-16, A-17) failed with its fix reverted, then passed with it |
| `tools/build-release.sh` | `mcode-0.2.0.tar.gz`, 688 956 bytes; selftest and package test passed inside it |
| LCD previews | 43 screens rendered; the new *Desktop IDE* and *Pairing* screens inspected (layout evidence only) |

## Reproduced before fixing

* A-01: a repository with `core.fsmonitor = touch X` → `gitutil.status()`
  created `X`. A filter driver's `process` ran on plain `git status` and
  `git diff`; with mCode's overrides nothing ran (git 2.43.0).
* A-18: no test covered zip import; reading the code showed the body was
  parsed as JSON first. The new test failed with "Invalid JSON." against
  the old line, as predicted.
* Probed and **not** a finding: terminal shells are reaped after *close* and
  after `exit`.

## Browser checks (headless Chromium, by hand, scripts in the session scratchpad)

Disposable mCode (own data folder, fake sysfs with two boards, three
sample projects) on 127.0.0.1:18765, opened through single-use links.

| Viewport | Result |
|---|---|
| iPhone 13 profile (390×664, touch, mobile) | phone layout; no horizontal overflow; no touch target under 44 px; no input under 16 px; no JavaScript error; Home, Projects, Editor (opened by tapping a file), AI Engineer, Devices, Serial monitor, Settings, Security sheet and New project sheet rendered and inspected |
| Same, 844×390 (landscape), light scheme | phone layout, no error |
| 820×1180 touch (tablet) | tablet layout, no error |
| 1440×900 | desktop layout as before (refined colours and fonts), Security dialog from the ⋯ menu, no error |
| Phone flows | first load 47 responses, 283 KB transferred (gzip), Home after about 1.35 s; xterm.js not loaded until the terminal opened, then loaded; **server restarted while the page was open → the page reloaded its state once the event stream came back** (A-12); Build from Home → build view with "Arduino CLI is needed first."; *Create firmware* filled "Create firmware that "; mode note shown |

## Benchmark (`python3 tools/benchmark.py --idle 60`)

Start 0.39 s; RSS 29.2 MB started, 29.4 MB idle, 31.1 MB after 100
requests; idle CPU 0.07 %; 4 threads; `/api/state` p50 2.2 ms; project open
p50 11 ms; page 864 KB raw / 242 KB gzip. Details:
[Performance benchmark](../PERFORMANCE_BENCHMARK.md).

## NOT VERIFIED

* Anything on an iPhone (Safari, keyboard, safe areas, home-screen mode,
  touch selection in the editor), an Android phone or an iPad.
* Anything on the Whisplay boards: LCD *Pairing* screen on the real
  display, 3× access switch with the real button, pairing a real phone,
  upgrade from 0.1.8 with browsers paired before.
* Flashing any board; the new firmware checksum check with a real esptool
  or avrdude run.
* A real Claude request with the new Advanced-mode git tools and the
  build-tool refusal (tested with the fake Claude CLI and the real MCP
  server only).
* HTTPS through a tunnel or reverse proxy (`web.extra_hosts`, `Secure`
  cookie).
