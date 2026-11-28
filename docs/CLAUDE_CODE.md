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

* **LCD** (*AI Engineer* while signed out, or *Settings › Claude Code* ›
  *Connect Claude account*): two steps, each a QR code for your phone (tap
  switches between them):
  1. **Sign in**: Claude's sign-in page. Sign in to your Claude account; the
     page then shows an *Authentication Code*: copy it.
  2. **Send code**: a page on the device itself
     (`http://<device>:<port>/c/<one-time link>`). Paste the code and tap
     *Send to mCode*; the device finishes signing in by itself. The phone
     must be on the same Wi-Fi as the device.

  Without a network, or with *Paste the sign-in code from a phone* switched
  off, step 2 asks you to type the code on a USB keyboard (Enter sends it)
  or paste it in the desktop IDE.
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
| `--tools Read,Edit,Write,Glob,Grep` | the built-in tools (no shell in any mode since 0.2.0); Plan mode: `Read,Glob,Grep` only |
| `--permission-mode acceptEdits` | edits inside the working folder need no prompt |
| `--permission-prompts none` | anything else is refused, not waited for |
| `--strict-mcp-config --mcp-config …` | only mCode's own tools (below) |
| `--allowedTools …` | mCode's tools for the mode (and web search in Auto + git when allowed) |
| `--model …` | only when a model is chosen (Settings › *Claude model*, or the model button in the AI Engineer); empty = Claude Code's default |
| `--append-system-prompt …` | mCode's rules: stay in the project, never claim hardware tests, cite pin sources or mark assumptions, keep docs/WIRING.md current, end with a SUMMARY line |

mCode's tools (an MCP server started for the request, talking to mCode over a
private socket):

Modes (setting `ai.mode`; the ids never change, the names follow Claude
Code's own picker since 0.3.0): Manual = `ask`, Auto = `assisted`,
Auto + git = `advanced`, Plan = `plan`.

| Tool | Manual | Auto | Auto + git | Plan |
|---|---|---|---|---|
| `project_info`, `last_build`, `serial_log`, `list_devices` (read-only) | ✓ | ✓ | ✓ | ✓ |
| `build`, `run_tests` (host-side only) | | ✓ | ✓ | |
| `static_check`, `clean` | | | ✓ | |
| `git_status`, `git_diff`, `git_log`, `git_show`, `git_commit` | | | ✓ | |

There is **no tool to upload, erase, change fuses or install** anything.
Those are mCode actions that need your confirmation on screen.

Git in Auto + git mode is mCode's own (`app/projects/gitutil.py`): it never
runs programs named in the repository's configuration, and Claude's commits
run without hooks. Before 0.2.0 that mode used `Bash(git …)`, which
honoured the repository's configuration.

`build`, `run_tests`, `static_check` and `clean` refuse to run while the
request has changes waiting for your decision (below) or has changed the
project's git settings: Claude is told why and ends its answer, you review,
then build. Stopping a request (or its time limit) also stops the build it
started.

## Changes and approvals

* **Manual**: Claude edits a staging copy
  (`<data>/workspaces/<project>/stage`). mCode shows the diff; *Apply*
  copies the chosen files into the project after a backup; *Discard* drops
  them.
* **Plan**: Claude reads the same staging copy and has no editing tool; the
  answer is a plan and never a change (should a file in the copy differ
  anyway, mCode leaves it out and says so). Plan and Manual continue the
  same Claude conversation. *Carry out this plan* sends the plan back as a
  request in Manual or Auto mode.
* **Auto / Auto + git**: before the request mCode snapshots and backs up
  the project; Claude edits in place; afterwards *Undo* puts the files back.
* **Held in every mode**: partition tables, `sdkconfig*`, bootloader and
  linker files, `platformio.ini` flash/bootloader/fuse/upload/extra-script
  settings, dependency lists (`lib_deps`, `platform_packages`,
  `idf_component.yml`, `library.json`, `sketch.yaml`), git attributes and
  submodules, and (while Settings › *Hold Claude's build-script changes* is
  on, the default) build scripts that run on this device when the project is
  built: `CMakeLists.txt`, `*.cmake`, Makefiles, `*.mk`, Python and shell
  scripts. In Auto mode they are moved out of the project until
  you *Keep* them.
* **Git settings**: in Auto and Auto + git mode `.git/config`, `.git/info` and
  `.git/hooks` are put back after the request if Claude changed them; the
  answer says so.
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

## What the chat shows: steps, model and usage

mCode reads Claude Code's `stream-json` events (shapes checked with Claude
Code 2.1.332 on 2026-11-28) and shows, while the request runs:

* each **step** in order: thinking, every tool with its state (running, done,
  failed) and time, and Claude's text between the steps. mCode's own tools
  also show their result line (for example the build's result, redacted);
  other tools' output (file contents) is not passed to the browser.
* the **model** Claude Code reports (`init`), and **tokens**: per message
  (`usage`), the request's totals (`result.usage`) and the model's context
  window (`result.modelUsage`). *Context* is what the model read for the
  last message: how full the conversation is.
* your **plan's limits** from Claude Code's `rate_limit_event`
  (`unifiedWindows`: the current 5-hour session and the week, each with the
  part used and when it resets). They arrive with requests; mCode never asks
  Anthropic itself and never reads Claude Code's login.

`<data>/ai/usage.json` keeps the last limits, the last model and the models'
context windows: numbers and states only. The context of each project's
conversations is kept with the conversation (`<data>/ai/<project>/state.json`)
and forgotten with *New conversation*. The cost Claude Code reports
(`total_cost_usd`) is shown as cost at API prices when you use a Claude
subscription (your plan's limits apply instead) and as the API key's cost
with an API key.
