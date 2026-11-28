# Configuration

Generated from `app/core/config.py` by `tools/gen-config-doc.py`; do not edit
by hand (`bash tools/check.sh` fails when this file is out of date).

Settings live in `settings.json` in mCode's data folder
(`~/.whisplay-os/apps/mcode/data/` under mFruit OS, `~/.local/share/mcode/`
otherwise). Change them in the desktop IDE (Settings), on the LCD (the
simple ones, marked LCD below) or with any editor while mCode is closed.
Settings marked *Full* can only be changed from a browser with full access
(the device's own browser or one paired with *Full access*): they name
programs or folders mCode uses, or open the device to the network.

Loading rules: a missing file means defaults; a file that is not valid JSON
is kept as `settings.json.corrupt-<time>` and defaults are used; a value of
the wrong type or outside its range falls back to that setting's default with
a warning in the log; unknown keys are kept and ignored. Writes are atomic
(temporary file, fsync, rename) and the file is owner-only (0600).

Other files in the data folder: `state.json` (recent projects, current
project, chosen board per project, identified chips), `.env` (optional
`OPENAI_API_KEY` for voice; never logged), `web-sessions.json` (paired
browsers: role, label, times; only hashes of their cookies), `web-token` (the
shared token of mCode 0.1.x, used once to migrate browsers paired then),
`logs/` (jobs, history, AI actions, serial logs, `security.jsonl`),
`backups/`, `workspaces/` (AI staging copies), `voice-cache/`, `tools/`
(Arduino CLI, PlatformIO venv).


## AI

| Key | Default | Allowed | LCD | Full | Meaning |
|---|---|---|---|---|---|
| `ai.mode` | `"ask"` | `ask`, `assisted`, `advanced`, `plan` | yes |  | AI mode. Manual: Claude works on a copy and you approve each change. Auto: Claude edits files and runs builds and tests; tests that run on this device (PlatformIO native, make test) run code Claude wrote, as your user. Auto + git: also static analysis and git through mCode. Plan: Claude reads the project and writes a plan; it changes nothing. Flashing, erasing and installing always need your confirmation. |
| `ai.claude_path` | empty | path |  | yes | Claude Code program. Empty: found automatically (PATH, ~/.local/bin/claude). |
| `ai.model` | empty | text |  |  | Claude model. Empty: Claude Code's default model. An alias (opus, sonnet, haiku, fable) or a model id; your Claude plan decides which models you can use. |
| `ai.voice_autosend` | `false` | `true`, `false` | yes |  | Send voice requests at once. Off: mCode shows what it heard and waits for you to send it. |
| `ai.turn_timeout_s` | `1200` | 60–14400 |  |  | AI task time limit (seconds) |
| `ai.hold_build_scripts` | `true` | `true`, `false` |  | yes | Hold Claude's build-script changes. On: changes Claude makes to CMakeLists.txt, Makefiles, *.cmake, *.mk and Python or shell scripts wait for your OK, and Claude cannot build until you decide. These files run programs on this device when the project is built. Off: they apply like other source files (Assisted and Advanced modes). |
| `ai.allow_web` | `false` | `true`, `false` |  | yes | Let Claude search the web (Advanced mode). Datasheets and library documentation; read-only. |
| `ai.login_code_page` | `true` | `true`, `false` | yes | yes | Paste the sign-in code from a phone. While signing in to Claude waits for its code, mCode serves one page on the local network (second QR code) where a phone can paste it: one-time link, one code, closed when the sign-in stops waiting. Off: type the code on a keyboard or paste it in the desktop IDE. |

## Development

| Key | Default | Allowed | LCD | Full | Meaning |
|---|---|---|---|---|---|
| `dev.projects_dir` | `"~/mcode-projects"` | path |  | yes | Project folder. Where new projects are created. Projects are never deleted with the app. |
| `dev.arduino_cli` | empty | path |  | yes | Arduino CLI program. Empty: found automatically. |
| `dev.platformio` | empty | path |  | yes | PlatformIO program. Empty: found automatically. |
| `dev.idf_path` | empty | path |  | yes | ESP-IDF folder. Empty: IDF_PATH, ~/esp/esp-idf or the newest ~/esp/v*/esp-idf. Installing ESP-IDF from Development tools sets it. |
| `dev.esptool` | empty | path |  | yes | esptool program. Empty: found automatically. |
| `dev.python` | empty | path |  | yes | Python for tools. Python used to create mCode's tool environment (PlatformIO, esptool). |
| `dev.build_jobs` | `0` | 0–64 |  |  | Compiler jobs. 0: automatic: one per CPU core while about 160 MB of free memory is left per job (3 on a 1 GB Orange Pi Zero 2W with mCode open, 1 on a 512 MB Pi Zero 2 W). |
| `dev.build_timeout_s` | `2400` | 60–14400 |  |  | Build time limit (seconds) |
| `dev.upload_timeout_s` | `300` | 30–3600 |  |  | Upload time limit (seconds) |
| `dev.warnings` | `"default"` | `none`, `default`, `more`, `all` |  |  | Compiler warnings |

## Hardware

| Key | Default | Allowed | LCD | Full | Meaning |
|---|---|---|---|---|---|
| `hw.default_port` | empty | text |  |  | Default serial port. Empty: the detected board. |
| `hw.default_baud` | `115200` | `300`, `1200`, `2400`, `4800`, `9600`, `19200`, `38400`, `57600`, `74880`, `115200`, `230400`, `460800`, `921600`, `1500000`, `2000000` | yes |  | Default baud rate |
| `hw.upload_speed` | `0` | `0`, `57600`, `115200`, `230400`, `460800`, `921600`, `1500000`, `2000000` |  |  | Upload speed. 0: the board's default. |
| `hw.verify_upload` | `true` | `true`, `false` |  |  | Verify after upload (where supported) |

## Interface

| Key | Default | Allowed | LCD | Full | Meaning |
|---|---|---|---|---|---|
| `ui.theme` | `"dark"` | `dark`, `light`, `auto` |  |  | Theme. Automatic follows the browser's or the phone's light/dark setting. |
| `ui.font_size` | `14` | 9–28 |  |  | Editor font size |
| `ui.layout` | `"ai-right"` | `ai-right`, `ai-left`, `ai-bottom` |  |  | Editor layout |
| `ui.terminal_scrollback` | `5000` | 500–100000 |  |  | Terminal scrollback lines |
| `ui.display_mode` | `"auto"` | `auto`, `lcd`, `desktop` |  | yes | Display mode. Auto: the LCD under mFruit OS, the desktop IDE elsewhere. |
| `ui.desktop_ide` | `true` | `true`, `false` | yes | yes | Desktop IDE. Serve the full IDE to a browser on this device. |
| `ui.open_browser` | `true` | `true`, `false` |  | yes | Open the IDE on the HDMI desktop. When a desktop session is running on this device. |
| `web.port` | `8765` | 1024–65535 |  | yes | Desktop IDE port |
| `web.lan` | `false` | `true`, `false` | yes | yes | Allow computers on the network. Off: only this device's own browser. On: computers and phones on the network can pair while a pairing screen is open (LCD: Desktop IDE › Pair a computer). |
| `web.pair_role` | `"developer"` | `viewer`, `developer`, `admin` |  | yes | Access for new pairings. What a newly paired browser may do. Developer: projects, builds, flashing (with confirmation) and the AI. Full access: also the terminal, folders outside the project folder, tools and device settings. The LCD's pairing screen can switch it for one pairing. |
| `web.session_days` | `30` | 1–365 |  | yes | Keep paired browsers (days). A browser not used for this many days must pair again. |
| `web.extra_hosts` | empty | text |  | yes | Other names of this device. Comma-separated host names a browser may use for mCode, e.g. the name of an HTTPS tunnel or reverse proxy (mydevice.tailnet.ts.net). Requests for any other name are refused (DNS rebinding). |

## Voice

| Key | Default | Allowed | LCD | Full | Meaning |
|---|---|---|---|---|---|
| `voice.stt` | `"auto"` | `auto`, `openai`, `command`, `off` |  |  | Speech recognition. Auto: OpenAI when a key is set, else your command, else off. |
| `voice.stt_command` | empty | text |  | yes | Speech command. A local recogniser: {wav} is replaced by the recording; it prints the text. |
| `voice.stt_model` | `"gpt-4o-mini-transcribe"` | text |  |  | OpenAI speech model |
| `voice.language` | `"en"` | text |  |  | Speech language. ISO code, e.g. en, de, th. |
| `voice.feedback` | `"spoken"` | `spoken`, `tones`, `off` | yes |  | Sound feedback |
| `voice.tts` | `"auto"` | `auto`, `openai`, `espeak`, `off` |  |  | Voice for spoken feedback |
| `voice.capture_device` | `"auto"` | text |  | yes | Microphone. auto or an ALSA device name. |
| `voice.playback_device` | `"auto"` | text |  | yes | Speaker. auto or an ALSA device name. |

## Storage

| Key | Default | Allowed | LCD | Full | Meaning |
|---|---|---|---|---|---|
| `storage.backups_keep` | `10` | 1–200 |  |  | Backups kept per project |
| `storage.log_days` | `30` | 1–3650 |  |  | Keep logs (days) |
