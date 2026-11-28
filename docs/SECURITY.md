# Security and safety

mCode runs AI-written code through real compilers and writes firmware to real
boards. These are the rules it enforces and where. The threat model behind
them is in [Security audit 2.0](SECURITY_AUDIT_2.0.md).

## The AI

| Risk | Protection | Where |
|---|---|---|
| Claude changes files outside the project | Claude Code `--restricted` confines file tools to the working folder; in Manual and Plan mode that folder is a staging copy, and Plan mode has no editing tool | `app/ai/service.py` |
| Claude runs arbitrary commands | no shell in any mode (since 0.2.0 the Auto + git mode's git is mCode's own `git_*` tools, not `Bash`); anything not allowed is refused (`--permission-prompts none`) | `app/ai/service.py`, `app/ai/bridge.py` |
| A model name smuggles an option into Claude Code's command line | `ai.model` must be an alias or a model id (letters, digits, `.` `_` `-` `:` `/` `@` `[` `]`; never starting with `-`) | `app/core/config.py` |
| Claude flashes or erases a board, changes fuses, installs software | no such tool exists; builds and tests go through mCode's job runner | `app/ai/bridge.py` |
| Risky edits slip through | partition, bootloader, flash/fuse, dependency and git-attribute changes, and build scripts (`CMakeLists.txt`, Makefiles, `*.cmake`, `*.mk`, Python and shell scripts; `ai.hold_build_scripts`) are held for an explicit decision in every mode | `app/ai/workspace.py` |
| A held edit runs before you decide | while the turn has held edits (or changed `.git` settings), mCode's build, test, check and clean tools refuse to run for Claude | `app/ai/workspace.py` `pending_reason`, `app/ai/bridge.py` |
| Claude plants a command in git's settings | `.git/config`, `.git/info` and `.git/hooks` are copied before an in-place turn and put back after it; mCode's own git never runs programs named in a repository's configuration | `app/ai/workspace.py`, `app/projects/gitutil.py` |
| A stopped request keeps building | ending a turn cancels the jobs it started | `app/ai/bridge.py` |
| A change breaks the project | Manual mode: nothing changes before approval; Plan mode: nothing changes; Auto modes: snapshot + backup before every request, Undo | `app/ai/workspace.py`, `app/projects/backups.py` |
| The MCP server is misused | it can only call tools of its own request's mode, for that project, while the request runs (random per-request token over an owner-only socket); arguments are checked | `app/ai/bridge.py` |
| Secrets reach logs or the AI | context and logs pass through a redactor (API keys, tokens, OAuth codes, passwords in URLs) | `app/core/redact.py` |

Claude's login is Claude Code's own (`~/.claude`); mCode never reads it. mCode
keeps only "signed in", the method and the plan. The usage the AI Engineer
shows comes from the events Claude Code prints for each request (numbers and
states, `<data>/ai/usage.json`); the browser gets tool output only from
mCode's own tools (redacted), never the contents of files Claude read.

**Auto and Auto + git mode run code on this device.** Host tests
(`pio test -e native`, `make test`) compile and run project code, which
Claude may have written, as your user, and every build runs the project's
build scripts. mCode holds build-script edits for your decision, but a test
is code. Use Manual or Plan mode for projects you do not trust.

## The sign-in code page

While (and only while) the Claude sign-in started on the device waits for its
code, mCode serves one page on the local network so a phone can paste the
code (`app/web/codedrop.py`; [Claude Code](CLAUDE_CODE.md#signing-in)):

* one path, `/c/<token>`, with a random 128-bit token shown only in the LCD's
  QR code; anything else is "not found"; 30 wrong requests close it;
* it takes one code (at most 4 KB, one line), hands it to the waiting
  `claude auth login` and then refuses more; it closes when the sign-in stops
  waiting (done, failed, cancelled) and after 15 minutes at most;
* the code is never logged or stored. It is a one-time OAuth code that is
  useless without the PKCE verifier only the waiting `claude auth login`
  holds, so plain HTTP on the local network does not expose the account;
* it does not open the desktop IDE to the network; switch it off with
  `ai.login_code_page` ([Configuration](CONFIGURATION.md)).

## Flashing

See [Hardware › Flashing safety](HARDWARE.md#flashing-safety): two-step
flow, one-time confirmation token, re-checks at confirmation, the firmware's
SHA-256 checked again right before writing, no build of the project while it
is flashed, refusal on mismatched boards, no automatic erase.

## The desktop IDE

* Listens on **127.0.0.1** unless *Allow computers on the network* is on
  (on the LCD, or by a browser with full access).
* **One session per browser.** Each paired browser has its own random
  cookie (`HttpOnly; SameSite=Strict`, `Secure` behind an HTTPS tunnel);
  only a hash of it is stored (`web-sessions.json`, owner-only). A browser
  not used for `web.session_days` (30) days must pair again. The Security
  page (Settings › Security) lists paired browsers with their access, last
  use and address, signs one out or all others, and shows recent security
  events (`logs/security.jsonl`). On the LCD, Settings › Desktop IDE ›
  *Sign out all computers* signs every browser out.
* **Getting a session.** The device's own browser (and `run.sh --desktop`)
  uses a **single-use link** valid for 10 minutes. Other computers and
  phones **pair**, and only while a pairing screen is open: the LCD's
  *Pair a computer*, or *Pair a phone or computer* on the Security page of a
  browser with full access. It shows a QR code and a random 6-digit code;
  each works once, the window closes after 5 minutes or 10 wrong codes, and
  tries are limited to 5 a minute. Nothing permanent is ever put in a link
  or QR code.
* **Access levels.** *Viewer* reads projects, builds and logs. *Developer*
  edits, builds, tests, flashes (with the usual confirmation), uses the AI,
  libraries, git and the serial monitor. *Full access* also gets the
  terminal (a shell as the device user), opening folders outside the
  project folder, installing tools, Claude sign-in/out, and the settings
  marked *Full* in [Configuration](CONFIGURATION.md) (programs and folders
  mCode runs, network and security). New pairings get `web.pair_role`
  (Developer); the LCD's pairing screen switches it with 3×; a browser with
  full access can change any other browser's level. The device's own browser
  and browsers paired before 0.2.0 have full access.
* The `Host` header must name this device or one of `web.extra_hosts` (DNS
  rebinding); every write needs `X-mCode: 1` and, when the browser sends
  one, an `Origin` of this device (cross-site requests); strict
  Content-Security-Policy (no inline script), `X-Frame-Options: DENY`,
  `no-referrer`.
* Idle connections close after 60 s; event streams drop a browser that
  vanished (a sleeping phone) within about 45 s.
* Flashing always requires a confirmation in the browser and runs mCode's
  checks.
* **Plain HTTP.** On the local network the IDE is not encrypted: someone who
  can watch the Wi-Fi traffic can copy a session cookie. Pair only on
  networks you trust. For anything else reach mCode through an encrypted
  tunnel instead of opening a port: for example `ssh -L 8765:127.0.0.1:8765
  <device>` then the single-use link on your computer, or an HTTPS reverse
  proxy or tunnel on the device (Tailscale `serve`, Caddy) that forwards to
  `127.0.0.1:8765` with `X-Forwarded-Proto: https`; add its host name to
  `web.extra_hosts`. (HTTPS through a tunnel is supported in the code but
  NOT VERIFIED with a real tunnel yet.)

## Git and imported projects

A project's `.git/config` can name programs that git runs (a file-system
monitor on every `git status`, filter and diff drivers, signing programs).
mCode's own git calls switch those off (`app/projects/gitutil.py`), so
opening, viewing or diffing an imported project never runs them. Building a
project you did not write still runs its build scripts: that is what
building is.

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
* ZIP imports refuse links and escaping paths, are streamed and limited to
  64 MB compressed and 256 MB expanded.

## Not a sandbox

mCode limits what Claude is *given*; it does not isolate code you build and
run. Firmware runs on your board; host tests (`pio test -e native`,
`make test`) run on the device as your user. Review what you approve.
