# mCode Studio 2.0 audit

Audit of mCode Studio **0.1.8** (`4a8e012`, 2026-11-28) against the "mCode
Studio 2.0" directive: correctness, concurrency, resources, security, small
screens and tests. Plan and status of the fixes: [Roadmap 2.0](ROADMAP_2.0.md).
Security findings in threat-model form: [Security audit 2.0](SECURITY_AUDIT_2.0.md).

## Method

* Read every service module (`app/core`, `app/projects`, `app/toolchains`,
  `app/hardware`, `app/ai`, `app/web`, the LCD event and render loop), the
  desktop IDE (`index.html`, `app.js`, `app.css`, `pair.*`), the tests, the
  CI script and the documents listed in the directive.
* Baseline on the dev machine (Jetson, aarch64, Python 3.12.3):
  `bash tools/check.sh` all 10 checks passed (43 s); suite 178 tests OK
  (38 s), with 15 `ResourceWarning`s and 3 `forkpty` deprecation warnings.
* Probes: a repository config command (reproduced, A-01); terminal shells
  after close and after `exit` (no zombies: not a finding).
* Not possible this session: the Whisplay boards (.130 and .248 did not answer
  SSH), an iPhone, a connected development board.

Words: **Confirmed** = reproduced or certain from the code (with the line of
reasoning); **Suspected** = a plausible risk not reproduced; **Improvement** =
not a defect. Verification: *regression test* names the test that fails
without the fix (negative control done) once fixed.

## Confirmed defects

| ID | Severity | Module | Problem |
|---|---|---|---|
| A-01 | High (security) | `projects/gitutil.py` | Opening a project runs commands from its `.git/config` |
| A-02 | High (security) | `web/server.py`, `lcd/screens.py` | Pairing secrets are permanent; one token for every browser |
| A-03 | High (safety) | `toolchains/builds.py`, `hardware/upload.py` | A build of the project can run during its upload |
| A-04 | Medium (safety) | `hardware/upload.py` | The confirmed firmware is identified by its path only |
| A-05 | High (AI security) | `ai/bridge.py`, `ai/workspace.py` | Held risky changes take effect during the AI turn |
| A-06 | Medium (resources) | `ai/bridge.py` | Stopping an AI request leaves its build running |
| A-07 | Medium (memory) | `core/jobs.py` | Finished jobs are never forgotten |
| A-08 | Medium (reliability) | `web/server.py` | HTTP connections never time out; phones fill the event-stream slots |
| A-09 | Low | `hardware/monitor.py` | Serial lines received while paused never reach the browser |
| A-10 | Low | `hardware/upload.py` | A failed port hand-over leaves the serial monitor suspended |
| A-11 | Low (memory) | `web/server.py` | Zip import holds whole entries in memory; a leftover folder blocks imports |
| A-12 | Medium (mobile) | `web/static/app.js` | After the event stream reconnects the IDE shows stale state |
| A-13 | Low (performance) | `web/static/app.js` | Unbounded job map; one forced layout per output line |
| A-14 | Medium (UX) | `web/static/*` | The IDE is unusable on a phone |
| A-15 | Low (performance) | `web/server.py` | 1.3 MB of static files, uncompressed, re-fetched every 5 minutes |
| A-16 | Medium (security) | `web/server.py`, `projects/manager.py` | Every paired browser has full device-user access |
| A-17 | Low (security) | `web/server.py` | `Origin` is not checked; unexpected errors return exception text |
| A-18 | Medium | `web/server.py` | Importing a zip file never worked |
| A-19 | High (regression in 0.2.0) | `web/server.py` | A second POST on a kept-alive connection gets the first one's body |
| A-20 | Low | `web/server.py` | A POST refused before its body is read corrupts the next request on the connection |

### A-01 Opening a project runs commands from its `.git/config`

* **Root cause:** `gitutil.status()` runs `git status` with the repository's
  own configuration. Git runs `core.fsmonitor` on `status`, and `git diff`
  runs `diff.external` and textconv drivers. mCode calls `gitutil.status` for
  every project view (`GET /api/project/<id>`), so merely opening a project
  imported from a zip, opened from a folder, or whose `.git/config` Claude
  edited in Assisted mode runs that command as the device user.
* **Reproduction (done):** `git init`; `git config core.fsmonitor "touch X"`;
  `python3 -c "from app.projects import gitutil; gitutil.status(repo)"` → `X`
  exists.
* **Fix:** mCode's read-only git calls pass `-c core.fsmonitor=false`,
  `--no-ext-diff`, `--no-textconv`; commits made for the AI run without
  hooks; Claude gets git through mCode tools instead of `Bash(git …)` (see
  A-05); `.git` configuration changed during an AI turn is put back.
* **Regression test:** `test_projects.GitSafetyTests`.

### A-02 Pairing secrets are permanent; one token for every browser

* **Root cause:** the LCD's pairing QR code and the "one-time link" both carry
  the single access token (`/?t=<token>`); it stays valid until *New pairing
  code* and is kept in browser history and in any photo of the screen. The
  6-digit code is `HMAC(token, "pair")`: it never changes, is accepted
  whenever LAN access is on (not only while the pairing screen is shown) and
  has no lock-out beyond 5 tries a minute. Every browser holds the same
  cookie, so one lost phone can only be revoked by signing out all of them;
  there is no list of paired browsers.
* **Fix:** per-browser sessions (random cookie, only its hash stored, idle
  expiry, revocation one by one), real one-time links (single use, 10
  minutes), a pairing window that exists only while a pairing screen is open
  (random code and QR token, single use, 5 minutes, 10 wrong codes close it),
  a security page listing sessions and events. Browsers paired before keep
  working: their old cookie is exchanged for a session once.
* **Regression test:** `test_web.SessionTests`, `test_web.PairingTests`.

### A-03 A build of the project can run during its upload

* **Root cause:** builds run on the `build` lane with no resource; uploads on
  the `upload` lane with the port as resource. Nothing stops *Rebuild* or
  *Clean* (from the IDE, the LCD or the AI) while esptool or avrdude reads
  the firmware file, so a half-written or deleted image can be flashed
  ("Hash of data verified" checks what was sent, not what was built).
* **Fix:** jobs can hold several resources; builds and uploads both hold
  `project:<id>`, so one waits for the other; `prepare` reports a running
  build.
* **Regression test:** `test_hardware.UploadTests.test_build_waits_for_the_upload_of_its_project`.

### A-04 The confirmed firmware is identified by its path only

* **Root cause:** `UploadService.start` compares the relative firmware path of
  the confirmed plan with a fresh plan. A rebuild in between keeps the path,
  so different bytes than the ones confirmed are flashed.
* **Fix:** the plan records the firmware's SHA-256 and size; the upload job
  checks them again right before the first upload step.
* **Regression test:** `test_hardware.UploadTests.test_firmware_rebuilt_after_confirmation_is_refused`.

### A-05 Held risky changes take effect during the AI turn

* **Root cause:** in Assisted and Advanced mode Claude edits the project in
  place and may call mCode's `build`/`run_tests` tools in the same turn. The
  rule that holds partition, bootloader, `extra_scripts` and dependency
  changes runs only after the turn, so a build in the turn already uses them.
  Build files that run programs on the device (`CMakeLists.txt`, `*.cmake`,
  `Makefile`, `*.mk`, PlatformIO extra scripts, `.gitattributes`) were not
  classified at all, and `.git/` is not compared. Advanced mode gave Claude
  `Bash(git …)`, which honours the repository configuration (A-01).
* **Fix:** mCode's build, test, check and clean tools refuse while the live
  project has risky changes from this turn; build scripts and git attributes
  are risky; `.git/config`, `.git/hooks` and `.git/info` are snapshotted and
  put back; Advanced mode's git goes through mCode tools (`git_status`,
  `git_diff`, `git_log`, `git_commit`) instead of `Bash`.
* **Remaining (documented):** host tests (`pio test -e native`, `make test`)
  compile and *run* project code on the device as the user. Assisted mode can
  therefore run code Claude wrote. This is stated in the mode description and
  [Security](SECURITY.md); it is not sandboxed.
* **Regression tests:** `test_ai.BridgeSafetyTests`,
  `test_ai.McpTests.test_claude_cannot_build_its_own_unapproved_partition_change`,
  `test_ai.InPlaceModeTests.test_git_settings_changed_by_claude_are_put_back`,
  `test_ai.InPlaceModeTests.test_advanced_gets_git_through_mcode_and_no_shell`,
  `test_ai.StreamTranslationTests.test_risky_classification`.

### A-06 Stopping an AI request leaves its build running

* **Root cause:** a bridge call waits on the build job it started; cancelling
  the AI job kills Claude Code but nobody cancels that build, which keeps the
  single build lane busy for up to `dev.build_timeout_s` (40 minutes).
* **Fix:** the bridge records the jobs each turn started; closing the turn
  cancels those still running.
* **Regression test:** `test_ai.BridgeSafetyTests.test_closing_a_turn_cancels_its_build`.

### A-07 Finished jobs are never forgotten

* **Root cause:** `JobManager._jobs` gains one entry per job and loses none
  (`_order`, which is bounded, is only used for listing). Each job keeps up to
  4000 output lines; AI turns keep Claude's stream-json lines. On a 512 MB
  board a long session grows without bound.
* **Fix:** jobs that fall out of the bounded history are dropped (never a
  running or queued one), with their publish timestamps.
* **Regression test:** `test_core.JobTests.test_finished_jobs_are_forgotten`.

### A-08 HTTP connections never time out

* **Root cause:** `BaseHTTPRequestHandler.timeout` is `None`. An idle client
  holds a server thread forever. The event stream (at most 12) of a phone
  that sleeps or leaves the Wi-Fi stays open until TCP gives up (about 15
  minutes on Linux); a phone reconnecting after each wake can fill the 12
  slots, and every new window then gets "Too many open windows".
* **Fix:** a request time-out (60 s) and, for event streams,
  `TCP_USER_TIMEOUT` + keep-alive so unacknowledged data drops a vanished
  client within about 45 s.
* **Regression test:** `test_web.ConnectionTests`.

### A-09 Serial lines received while paused never reach the browser

* **Root cause:** `_maybe_publish` takes the pending batch and drops it while
  paused; *Resume* publishes nothing, the browser never re-reads the buffer.
* **Fix:** pending lines are kept while paused (bounded) and published on
  resume.
* **Regression test:** `test_hardware.SerialTests.test_lines_received_while_paused_arrive_on_resume`.

### A-10 A failed port hand-over leaves the serial monitor suspended

* **Root cause:** the upload job suspends the monitor, then acquires the port
  *outside* the `try` whose `finally` resumes it.
* **Fix:** acquire inside the `try`.
* **Regression test:** `test_hardware.UploadTests.test_monitor_resumes_when_the_port_is_busy`.

### A-11 Zip import memory and leftovers

* **Root cause:** each entry is read with `source.read()` (up to the 256 MB
  expansion limit in memory); a crash leaves `<name>.importing`, and
  `os.makedirs` on it then fails with an internal error for every retry.
* **Fix:** stream entries with a running size check; remove a stale
  `.importing` folder that mCode made.
* **Regression test:** `test_web.ZipImportTests`.

### A-12 Stale state after the event stream reconnects

* **Root cause:** `EventSource` reconnects by itself but the IDE does not
  reload state, so a build or AI request that ended while a phone slept still
  shows as running.
* **Fix:** on reconnect the IDE reloads state, the project and the serial
  view.
* **Verification:** headless browser check (records); no automated browser
  test in CI.

### A-13 Browser memory and layout work

* **Root cause:** `S.jobs` keeps every job event's job; each output line sets
  `scrollTop` (one forced layout per line, thousands per build).
* **Fix:** keep only recent jobs; append output in one batch per animation
  frame.

### A-14 The IDE is unusable on a phone

* **Root cause:** a fixed desktop layout (activity bar 44 px + side 260 px +
  AI 380 px + editor) on a 390 px screen; no `viewport-fit`, safe areas or
  dynamic viewport height; 13 px inputs (iOS zooms on focus); file actions
  only on right-click; panel resizing by mouse only.
* **Fix:** phone, tablet and desktop layouts on one code base (bottom tab bar,
  full-screen views, bottom sheets, keyboard-aware composer and editor),
  pointer-event resizing. See [iOS and mobile](IOS_MOBILE.md).

### A-15 Static files

* **Root cause:** the IDE loads 1.3 MB (290 KB gzipped), served uncompressed.
  `_send` always wrote `Cache-Control: no-store` and the static handler added
  a second `Cache-Control: max-age=300`; browsers honour `no-store`, so every
  page load downloaded all of it. No validator either.
* **Fix:** one `Cache-Control: no-cache`, an `ETag` with `304 Not Modified`,
  gzip when the browser accepts it (compressed once, kept in memory).
* **Regression test:** `test_web.AccessTests.test_static_files_are_compressed_and_revalidated`.

### A-16 Every paired browser has full access

* **Root cause:** one role for all: the terminal (a shell as the device user),
  `projects/open` and `projects/import` take any folder (and `find()` accepts
  a path, so the file API reads outside the project folder), and settings
  name programs mCode runs (`ai.claude_path`, `dev.*` paths,
  `voice.stt_command`).
* **Fix:** session roles *viewer* < *developer* < *admin*. The terminal,
  folders outside the project folder, device and security settings, Claude
  sign-in/out and session management need *admin*; the device's own browser
  and browsers paired before 0.2.0 are admin; new pairings default to
  developer (`web.pair_role`; the LCD's pairing screen can switch it).
* **Regression test:** `test_web.RoleTests`.

### A-17 Origin header and error text

* **Root cause:** cross-site writes are stopped by `SameSite=Strict` and the
  `X-mCode` header; `Origin` is ignored. An unexpected exception's class and
  text are returned to the browser.
* **Fix:** a write with an `Origin` of another site is refused; unexpected
  errors return a plain message and a reference to the log.
* **Regression test:** `test_web.AccessTests.test_dns_rebinding_and_cross_site_posts_refused`,
  `test_web.AccessTests.test_unexpected_errors_do_not_return_internals`.

### A-18 Importing a zip file never worked

* **Found while fixing A-11.** `route()` parsed every POST body as JSON
  before dispatching, so the zip upload (`POST /api/zip`) always ended in
  "Invalid JSON." No test covered it.
* **Fix:** the zip route reads its own body; the access check skips it too.
* **Regression test:** `test_web.ZipImportTests` (negative control done).

### A-19 A second POST on a kept-alive connection gets the first one's body

* **Found 2026-11-28 (0.3.0 work)** in a browser check: sending a request
  to Claude right after switching the mode answered "Say or type what you
  want Claude to do." although the text was sent (the request body was
  seen in the browser's network log).
* **Root cause:** 0.2.0 caches the parsed JSON body on the request handler
  (`_parsed_body`, so the access check and the route read it once). One
  handler object serves every request of an HTTP/1.1 kept-alive connection,
  and the cache was never cleared: every later POST on the connection got
  the first POST's body, and its own body stayed unread in the stream.
  Browsers reuse connections, so this hit any second action within the
  idle time-out (settings, AI requests, approvals, serial input).
* **Fix:** `_dispatch` clears the cache for every request.
* **Regression test:**
  `test_web.ConnectionTests.test_each_request_on_a_kept_alive_connection_has_its_own_body`
  (two settings POSTs on one connection; failed with `15 != 17` before the
  fix, negative control done).
* **Verification:** SOFTWARE TESTED; headless Chromium (mode change, then
  send) on the dev machine.

### A-20 A POST refused before its body is read corrupts the next request

* **Root cause:** answers sent before the body is read (missing `X-mCode`
  header, wrong `Origin`, not paired, unknown host) left the body in the
  stream, where it was read as the start of the next request on the
  connection (present since 0.1.x).
* **Fix:** such answers close the connection (`Connection: close`).
* **Regression test:**
  `test_web.ConnectionTests.test_a_refused_post_does_not_leave_its_body_on_the_connection`
  (negative control done).

## Suspected risks (not reproduced)

| ID | Area | Risk | Action |
|---|---|---|---|
| R-01 | AI | Project files, compiler and serial output are prompt-injection input. Claude's tools are limited per mode and mCode's tools validate arguments, but approving a malicious diff is still possible. | Documented; risky files held; diffs shown before Ask-mode changes |
| R-02 | Processes | `pty.fork()` in a threaded process (Python 3.12 warns): a child could deadlock before `exec`. The child only calls `chdir`/`execvpe`. | Watch; switch to `subprocess` with `start_new_session` + `pty.openpty` if seen |
| R-03 | Memory | Claude Code (~300 MB) plus an ESP-IDF build on a 512 MB Pi Zero 2 W may be killed by the OOM killer. | Existing memory warning; build jobs sized from free memory (0.1.5) |
| R-04 | LCD | The render loop renders a frame for every wake even when the frame is identical (only sending is skipped). | Measure on a board before changing |
| R-05 | Supply chain | Vendored CodeMirror/xterm had no recorded checksums; installers trust PyPI and Anthropic's installer over HTTPS. | Done: `vendor/SHA256SUMS`, checked by `tools/check.sh` |
| R-06 | Remote access | Plain HTTP on the LAN: cookies and code travel unencrypted on Wi-Fi. | Documented; HTTPS through a tunnel (`web.extra_hosts`) supported, not verified |

## Not findings

* Terminal shells are reaped after *close* and after `exit` (probed).
* File paths from the IDE and the AI are resolved inside the project after
  following links (`projects/files.resolve`, existing tests).
* Upload confirmation tokens are single-use, expire after 5 minutes and are
  re-checked at start (existing tests).
* Secrets from `.env` are not put into the process environment
  (`core/envfile.py`), so builds, terminals and Claude Code do not inherit
  them.

## Test gaps found

* No test for git configuration, job retention, concurrent build/upload,
  firmware identity, AI tool refusal, connection time-outs, roles or sessions
  (all added with the fixes).
* No automated browser test (no browser in CI); layout checks are recorded
  runs with headless Chromium. Real iPhone Safari is **not verified**.
