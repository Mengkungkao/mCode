# Configuration

Generated from `app/core/config.py` by `tools/gen-config-doc.py`; do not edit
by hand (`bash tools/check.sh` fails when this file is out of date).

Settings live in `settings.json` in mCode's data folder
(`~/.whisplay-os/apps/mcode/data/` under mFruit OS, `~/.local/share/mcode/`
otherwise). Change them in the desktop IDE (Settings), on the LCD (the
simple ones, marked LCD below) or with any editor while mCode is closed.

Loading rules: a missing file means defaults; a file that is not valid JSON
is kept as `settings.json.corrupt-<time>` and defaults are used; a value of
the wrong type or outside its range falls back to that setting's default with
a warning in the log; unknown keys are kept and ignored. Writes are atomic
(temporary file, fsync, rename) and the file is owner-only (0600).

Other files in the data folder: `state.json` (recent projects, current
project, chosen board per project, identified chips), `.env` (optional
`OPENAI_API_KEY` for voice; never logged), `web-token` (desktop IDE access),
`logs/` (jobs, history, AI actions, serial logs), `backups/`, `workspaces/`
(AI staging copies), `voice-cache/`, `tools/` (Arduino CLI, PlatformIO venv).


## AI

| Key | Default | Allowed | LCD | Meaning |
|---|---|---|---|---|
| `ai.mode` | `"ask"` | `ask`, `assisted`, `advanced` | yes | AI mode. Ask: Claude proposes changes and you approve them. Assisted: Claude edits files and runs builds and tests. Advanced: also git and approved workflows. Flashing, erasing and installing always need your confirmation. |
| `ai.claude_path` | empty | path |  | Claude Code program. Empty: found automatically (PATH, ~/.local/bin/claude). |
| `ai.model` | empty | text |  | Claude model. Empty: Claude Code's default model. |
| `ai.voice_autosend` | `false` | `true`, `false` | yes | Send voice requests at once. Off: mCode shows what it heard and waits for you to send it. |
| `ai.turn_timeout_s` | `1200` | 60–14400 |  | AI task time limit (seconds) |
| `ai.allow_web` | `false` | `true`, `false` |  | Let Claude search the web (Advanced mode). Datasheets and library documentation; read-only. |

## Development

| Key | Default | Allowed | LCD | Meaning |
|---|---|---|---|---|
| `dev.projects_dir` | `"~/mcode-projects"` | path |  | Project folder. Where new projects are created. Projects are never deleted with the app. |
| `dev.arduino_cli` | empty | path |  | Arduino CLI program. Empty: found automatically. |
| `dev.platformio` | empty | path |  | PlatformIO program. Empty: found automatically. |
| `dev.idf_path` | empty | path |  | ESP-IDF folder. Empty: IDF_PATH or ~/esp/esp-idf. |
| `dev.esptool` | empty | path |  | esptool program. Empty: found automatically. |
| `dev.python` | empty | path |  | Python for tools. Python used to create mCode's tool environment (PlatformIO, esptool). |
| `dev.build_jobs` | `0` | 0–64 |  | Compiler jobs. 0: automatic (1 on boards under 1 GB). |
| `dev.build_timeout_s` | `2400` | 60–14400 |  | Build time limit (seconds) |
| `dev.upload_timeout_s` | `300` | 30–3600 |  | Upload time limit (seconds) |
| `dev.warnings` | `"default"` | `none`, `default`, `more`, `all` |  | Compiler warnings |

## Hardware

| Key | Default | Allowed | LCD | Meaning |
|---|---|---|---|---|
| `hw.default_port` | empty | text |  | Default serial port. Empty: the detected board. |
| `hw.default_baud` | `115200` | `300`, `1200`, `2400`, `4800`, `9600`, `19200`, `38400`, `57600`, `74880`, `115200`, `230400`, `460800`, `921600`, `1500000`, `2000000` | yes | Default baud rate |
| `hw.upload_speed` | `0` | `0`, `57600`, `115200`, `230400`, `460800`, `921600`, `1500000`, `2000000` |  | Upload speed. 0: the board's default. |
| `hw.verify_upload` | `true` | `true`, `false` |  | Verify after upload (where supported) |

## Interface

| Key | Default | Allowed | LCD | Meaning |
|---|---|---|---|---|
| `ui.theme` | `"dark"` | `dark`, `light` |  | Theme |
| `ui.font_size` | `14` | 9–28 |  | Editor font size |
| `ui.layout` | `"ai-right"` | `ai-right`, `ai-left`, `ai-bottom` |  | Editor layout |
| `ui.terminal_scrollback` | `5000` | 500–100000 |  | Terminal scrollback lines |
| `ui.display_mode` | `"auto"` | `auto`, `lcd`, `desktop` |  | Display mode. Auto: the LCD under mFruit OS, the desktop IDE elsewhere. |
| `ui.desktop_ide` | `true` | `true`, `false` | yes | Desktop IDE. Serve the full IDE to a browser on this device. |
| `ui.open_browser` | `true` | `true`, `false` |  | Open the IDE on the HDMI desktop. When a desktop session is running on this device. |
| `web.port` | `8765` | 1024–65535 |  | Desktop IDE port |
| `web.lan` | `false` | `true`, `false` | yes | Allow computers on the network. Off: only this device's own browser. On: any computer that has the pairing link shown on the LCD. |

## Voice

| Key | Default | Allowed | LCD | Meaning |
|---|---|---|---|---|
| `voice.stt` | `"auto"` | `auto`, `openai`, `command`, `off` |  | Speech recognition. Auto: OpenAI when a key is set, else your command, else off. |
| `voice.stt_command` | empty | text |  | Speech command. A local recogniser: {wav} is replaced by the recording; it prints the text. |
| `voice.stt_model` | `"gpt-4o-mini-transcribe"` | text |  | OpenAI speech model |
| `voice.language` | `"en"` | text |  | Speech language. ISO code, e.g. en, de, th. |
| `voice.feedback` | `"spoken"` | `spoken`, `tones`, `off` | yes | Sound feedback |
| `voice.tts` | `"auto"` | `auto`, `openai`, `espeak`, `off` |  | Voice for spoken feedback |
| `voice.capture_device` | `"auto"` | text |  | Microphone. auto or an ALSA device name. |
| `voice.playback_device` | `"auto"` | text |  | Speaker. auto or an ALSA device name. |

## Storage

| Key | Default | Allowed | LCD | Meaning |
|---|---|---|---|---|
| `storage.backups_keep` | `10` | 1–200 |  | Backups kept per project |
| `storage.log_days` | `30` | 1–3650 |  | Keep logs (days) |
