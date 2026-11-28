# Security audit 2.0 (threat model)

mCode edits source code, runs native build tools and writes firmware to
boards. It is a privileged development application running as the device
user. This document lists what it protects, from whom, and how; the rules
users see are in [Security](SECURITY.md). Findings referenced as A-xx are in
the [audit](AUDIT_2.0.md).

Status of this document: written with the audit (2026-11-28); the
"Protection" column describes 0.2.0 once the roadmap's Milestone 5 items are
IMPLEMENTED (see [Roadmap](ROADMAP_2.0.md)).

## Assets

| Asset | Where | Why it matters |
|---|---|---|
| The device user's account | the shell, home folder, SSH keys, `~/.claude` | mCode runs as this user; a shell is full control |
| Claude login | `~/.claude` (Claude Code's own) | usage billed to the owner |
| OpenAI key | mCode's `.env` (0600) | billed to the owner |
| Projects | `~/mcode-projects` | the owner's work |
| Boards | USB serial ports | wrong firmware can brick a board or a machine it drives |
| Desktop IDE sessions | `web-sessions.json` (hashes only) | a session is remote control of the above |

## Actors

| Actor | Can reach | Trust |
|---|---|---|
| Owner at the device | LCD, HDMI desktop, 127.0.0.1 | full |
| Paired browser | the IDE API with its session's role | as paired: viewer / developer / admin |
| Someone on the same Wi-Fi | port 8765 when LAN access is on; the sign-in code page while it is open | none |
| A web page in the owner's browser | can make the browser send requests to mCode | none (CSRF, DNS rebinding) |
| Project content | files, imported zips and repositories, compiler and serial output | untrusted input |
| Claude | the tools of the request's mode, inside the project | limited; never above the user |

## Threats and protections

| # | Threat | Protection | Residual risk |
|---|---|---|---|
| T1 | A LAN stranger guesses or replays pairing | pairing only while a pairing screen is open; random 6-digit code, single use, 5 min, 10 wrong codes close it, 5 tries/min; QR token single use (A-02) | plain HTTP: a sniffer on the Wi-Fi sees cookies (R-06); use a tunnel for untrusted networks |
| T2 | A photo of the QR code or browser history gives permanent access | links and QR tokens are single use; cookies are per browser and revocable (A-02) | — |
| T3 | A lost phone keeps access | revoke one session on the Security page or the LCD (*Sign out all computers*); idle expiry `web.session_days` | — |
| T4 | Cross-site request from a web page | `SameSite=Strict` HttpOnly cookie, `X-mCode` header on writes, `Origin` check (A-17), CSP without inline script, `X-Frame-Options: DENY` | — |
| T5 | DNS rebinding | `Host` must be a name of this device or `web.extra_hosts` | — |
| T6 | A paired phone opens a shell or reads `~/.ssh` | terminal, folders outside the project folder and program-path settings need the admin role (A-16) | an admin session is a shell, by design |
| T7 | A malicious project runs code when opened | git runs without fsmonitor/external diff/textconv (A-01) | **building** a project runs its build scripts; host tests run its code: building untrusted code is running it |
| T8 | Prompt injection makes Claude flash, erase or install | no such tool exists; uploads need a confirmation token from the user's own action | — |
| T9 | Prompt injection makes Claude run code | Manual and Plan (0.3.0): staging copy, read-only tools. Auto / Auto + git: risky and build-script edits block mCode's build tools until approved (A-05); git through mCode tools; `.git` config restored | Assisted host tests (`pio test -e native`, `make test`) run Claude-written code as the user |
| T10 | Path traversal or link escape | `files.resolve` after `realpath`; zip import refuses links, `..`, absolute paths, size bombs | — |
| T11 | Wrong or stale firmware flashed | two-step flow, board match, firmware newer than sources, SHA-256 re-checked before writing, no concurrent build (A-03, A-04) | plain USB-UART adapters cannot identify the board (warning shown) |
| T12 | Secrets in logs, AI context or the browser | redaction of logs and AI context; `.env` never in the environment; cookies HttpOnly; tokens never in URLs except single-use links | redaction is pattern based |
| T13 | Supply chain | official sources; Arduino CLI SHA-256 from the release; vendored JS checksums in CI (R-05) | PyPI and Anthropic's installer are trusted over HTTPS |

## Not provided

mCode is **not a sandbox**: builds, host tests and the terminal run as the
device user. A command allowlist would not be a sandbox either, so mCode does
not pretend to have one. OS-level isolation of host tests (bubblewrap where
installed) is PLANNED.
