# Performance benchmark

How fast and how heavy mCode is, measured, not assumed. Re-run on any
machine with:

```bash
python3 tools/benchmark.py --idle 60      # throw-away data and project folders
```

It starts mCode's services and the desktop IDE server (127.0.0.1) with the
real tool detection and USB discovery, waits idle, then measures the API and
what a browser downloads. Record new runs here with date, revision and
machine; one never stands in for another.

## Dev machine, 2026-11-28 (0.2.0 work tree, based on `4a8e012`)

Jetson (aarch64), Python 3.12.3, 60 s idle window, no browser open, no board
connected.

| Measure | Result |
|---|---|
| Import + construct + start + web server | 0.39 s (import 0.23 s) |
| Tool detection (Claude, Arduino CLI, PlatformIO, ESP-IDF, esptool, make, git…) | 0.72 s, in the background at start |
| Memory (RSS): Python alone / started / idle 60 s / after 100 requests | 17.9 / 29.2 / 29.4 / 31.1 MB |
| CPU while idle | 0.07 % of one core (no polling) |
| Threads while idle | 4 |
| `GET /api/state` | p50 2.2 ms, p90 2.8 ms |
| Create a project (ESP32-S3, Arduino CLI) | 46 ms |
| Open a project (`GET /api/project/<id>`: tree, git, backups, build state) | p50 11 ms |
| First page load: assets | 42 files, 864 KB raw, **242 KB gzip** (xterm.js loads only with a terminal) |

Before 0.2.0 the same page was 1.3 MB on every load: no compression, and a
`Cache-Control: no-store` that made the browser download everything each time
(A-15 in the [audit](AUDIT_2.0.md)).

Phone emulation (headless Chromium, iPhone 13 profile, same machine, local
network not involved): first load 47 responses, 283 KB transferred, home
screen drawn after about 1.35 s. This is Chromium on a Jetson, not Safari on
an iPhone.

## Memory bounds (by design, tested)

| What | Bound | Test |
|---|---|---|
| Finished jobs kept in memory | the last 200 (each up to 4000 output lines) | `test_core.JobTests.test_finished_jobs_are_forgotten` |
| Serial monitor | 5000 lines and 256 KB raw bytes | existing monitor tests |
| Event queue per browser | 2000 events, oldest dropped | `test_core` event bus test |
| Event streams | 12, vanished clients dropped in about 45 s | `test_web.ConnectionTests` |
| Browser: jobs / output lines / serial lines | 60 / 4000 / 5000 | — (browser code) |
| Zip import | streamed, 64 MB in, 256 MB out | `test_web.ZipImportTests` |

## Builds (from earlier sessions, real tools)

| Machine | Build | Time |
|---|---|---|
| .130 Orange Pi Zero 2W (1 GB), mCode open | clean ESP32-C5 Arduino, 1 / 2 / 4 jobs | 143 / 97 / 83 s |
| .130 | clean ESP-IDF 6.1.1, 4 jobs | 563 s (free memory down to 131 MB + 227 MB zram) |
| .130 | PlatformIO with the ESP-IDF framework | 27.7 min |
| Dev machine, 4 jobs | ESP32-C5 ESP-IDF starter, first / after one change | 60 s / 6 s (0.1.7 fast build) |

## NOT VERIFIED

* Orange Pi Zero 2W and Raspberry Pi Zero 2 W: start-up, idle memory and CPU
  of 0.2.0 (both boards were unreachable on 2026-11-28). Run
  `python3 tools/benchmark.py --idle 120` there.
* iPhone Safari: load time, scrolling, editor and serial monitor speed.
* Recovery after an interrupted build or upload on a board.
