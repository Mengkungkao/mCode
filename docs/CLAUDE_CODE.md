# Claude Code in mCode

mCode's AI Engineer is **Claude Code**, Anthropic's command-line coding
agent, running on the device with **your own** Claude Code installation and
login. mCode does not have its own AI account, API key or model.

## Installing Claude Code

Settings › Development tools › Claude Code › *Install* runs Anthropic's
official installer (`https://claude.ai/install.sh`, into `~/.local`). By hand:

```bash
curl -fsSL https://claude.ai/install.sh | bash
claude --version
```

mCode finds `claude` in Settings › Claude Code program, on `PATH`,
`~/.local/bin`, `~/.claude/local`, `~/.npm-global/bin` or a VS Code
extension's bundled copy, in that order.

Claude Code needs several hundred MB of memory. On a **Raspberry Pi Zero 2 W
(512 MB)** mCode warns that it may be slow or stop; a Raspberry Pi 4/5, an
Orange Pi with 1 GB or more, or a computer running the desktop IDE works
better. Builds and flashing do not need Claude.

## Signing in

mCode drives the official login, `claude auth login`:

* **LCD**: *Connect Claude account* shows a QR code with Claude's sign-in
  link. Open it on your phone, sign in to your Claude account, then type the
  code the page shows on a USB keyboard (or paste it in the desktop IDE).
* **Desktop IDE**: the Claude pill › *Sign in with Claude* › open the link,
  sign in, paste the code.
* **Terminal**: `./run.sh cli claude login`.

mCode never sees your password, never reads or stores tokens, and never shows
your e-mail address or organisation: the status it keeps is "signed in", the
login method and the plan (`claude auth status --json`). *Sign out* runs
`claude auth logout`. An expired login shows *Not signed in* and mCode asks
you to connect again.

**A Claude subscription is not an API key.** Signing in with your Claude
account uses your plan's usage. If `ANTHROPIC_API_KEY` is set in mCode's
environment, Claude Code bills that key instead; mCode says so on the account
screen. Usage limits of your plan apply; when one is reached the AI Engineer
says so and keeps your project as it was.

## What Claude may do

Every request runs `claude -p` with:

| Option | Why |
|---|---|
| `--restricted` | no shell or code-running tools unless named, file tools confined to the working folder, your user/project Claude settings not loaded |
| `--tools Read,Edit,Write,Glob,Grep` (+ `Bash` in Advanced) | the built-in tools of the mode |
| `--permission-mode acceptEdits` | edits inside the working folder need no prompt |
| `--permission-prompts none` | anything else is refused, not waited for |
| `--strict-mcp-config --mcp-config …` | only mCode's own tools (below) |
| `--allowedTools …` | mCode's tools for the mode; in Advanced `Bash(git status/diff/log/add/commit/show:*)` only |
| `--append-system-prompt …` | mCode's rules: stay in the project, never claim hardware tests, cite pin sources or mark assumptions, keep docs/WIRING.md current, end with a SUMMARY line |

mCode's tools (an MCP server started for the request, talking to mCode over a
private socket):

| Tool | Ask | Assisted | Advanced |
|---|---|---|---|
| `project_info`, `last_build`, `serial_log`, `list_devices` (read-only) | ✓ | ✓ | ✓ |
| `build`, `run_tests` (host-side only) | | ✓ | ✓ |
| `static_check`, `clean` | | | ✓ |

There is **no tool to upload, erase, change fuses or install** anything.
Those are mCode actions that need your confirmation on screen.

## Changes and approvals

* **Ask before changes**: Claude edits a staging copy
  (`<data>/workspaces/<project>/stage`). mCode shows the diff; *Apply*
  copies the chosen files into the project after a backup; *Discard* drops
  them.
* **Assisted / Advanced**: before the request mCode snapshots and backs up
  the project; Claude edits in place; afterwards *Undo* puts the files back.
* **Held in every mode**: partition tables, `sdkconfig*`, bootloader and
  linker files, `platformio.ini` flash/bootloader/fuse/upload settings, and
  dependency lists (`lib_deps`, `platform_packages`, `idf_component.yml`,
  `library.json`, `sketch.yaml`). In Assisted/Advanced mode they are moved
  out of the project until you *Keep* them.
* After a *Fix the build* request is approved, mCode rebuilds.

## Context Claude gets

Project name, board, MCU, toolchain, framework, FQBN or environment, board
notes from mCode's catalogue, the chosen board and port, libraries, the file
list, the last build's problems and the end of its output, and (when you
attach them) serial output and files. Text is passed through mCode's secret
redactor and size-capped.

## Conversation and records

Each project keeps its conversation and Claude session (resumed across
requests; a lost session starts a new one automatically). *New conversation*
forgets it. Every request, tool use, change, approval, rejection and undo is
written to `logs/ai-actions.jsonl` in the data folder (redacted).
