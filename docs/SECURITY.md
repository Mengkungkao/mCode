# Security and safety

mCode runs AI-written code through real compilers and writes firmware to real
boards. These are the rules it enforces and where.

## The AI

| Risk | Protection | Where |
|---|---|---|
| Claude changes files outside the project | Claude Code `--restricted` confines file tools to the working folder; in Ask mode that folder is a staging copy | `app/ai/service.py` |
| Claude runs arbitrary commands | no shell in Ask/Assisted; in Advanced only `git status/diff/log/add/commit/show`; anything else is refused (`--permission-prompts none`) | `app/ai/service.py` |
| Claude flashes or erases a board, changes fuses, installs software | no such tool exists; builds and tests go through mCode's job runner | `app/ai/bridge.py` |
| Risky edits slip through | partition, bootloader, flash/fuse and dependency changes are held for an explicit decision in every mode | `app/ai/workspace.py` |
| A change breaks the project | Ask mode: nothing changes before approval; other modes: snapshot + backup before every request, Undo | `app/ai/workspace.py`, `app/projects/backups.py` |
| The MCP server is misused | it can only call tools of its own request's mode, for that project, while the request runs (random per-request token over an owner-only socket) | `app/ai/bridge.py` |
| Secrets reach logs or the AI | context and logs pass through a redactor (API keys, tokens, OAuth codes, passwords in URLs) | `app/core/redact.py` |

Claude's login is Claude Code's own (`~/.claude`); mCode never reads it. mCode
keeps only "signed in", the method and the plan.

## Flashing

See [Hardware › Flashing safety](HARDWARE.md#flashing-safety): two-step
flow, one-time confirmation token, re-checks at confirmation, refusal on
mismatched boards, no automatic erase.

## The desktop IDE

* Listens on **127.0.0.1** unless *Use from a computer* is switched on (on the
  LCD, or by a browser that is already signed in).
* Every API request needs the access token as an `HttpOnly; SameSite=Strict`
  cookie; the token (32 random bytes) is stored owner-only in the data folder.
  The local browser gets it through a one-time link; a computer on the
  network through the pairing QR code or the 6-digit pairing code shown on
  the LCD (5 tries per minute).
* The `Host` header must name this device (DNS rebinding); every write needs
  `X-mCode: 1` (cross-site forms cannot set it); strict Content-Security-Policy
  (no inline script), `X-Frame-Options: DENY`, `no-referrer`.
* *New pairing code* (LCD) rotates the token: every paired browser must pair
  again.
* Anyone you pair can edit projects, build and use the **terminal** (a shell
  as the device user, like SSH). Flashing still requires a confirmation in the
  browser and runs mCode's checks. Only pair computers you trust, on networks
  you trust: the IDE uses plain HTTP on the LAN.

## Installing tools

Installers run only when you confirm them, as your user (never sudo), from
official sources: the Arduino CLI release archive with SHA-256 verification
against the release's checksum file, PyPI for PlatformIO and esptool (in
mCode's own venv), Anthropic's installer script for Claude Code (downloaded
over HTTPS, refused unless it is a script), the official Arduino board index
for cores. Libraries come from the Arduino Library Manager, the PlatformIO
Registry or the Espressif Component Registry, after confirmation.

## Files

* File paths from the IDE and the AI are resolved inside the project after
  following symlinks; `..`, absolute paths and links pointing out are refused.
* Saves are atomic and refuse to overwrite a file that changed on disk since
  it was opened (unless you choose to).
* Deleting moves to a trash folder; restoring a backup backs up the current
  state first and refuses archives with links, devices or paths that escape.
* ZIP imports refuse links and escaping paths and are size-limited.

## Not a sandbox

mCode limits what Claude is *given*; it does not isolate code you build and
run. Firmware runs on your board; host tests (`pio test -e native`,
`make test`) run on the device as your user. Review what you approve.
