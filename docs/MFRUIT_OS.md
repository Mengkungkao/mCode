# mFruit OS integration

mCode is an ordinary mFruit OS app package; mFruit OS itself is unchanged.
It follows `MFruitOS/docs/apps/APP_CONTRACT.md` (copy in
`.claude/rules/mfruit-os-app.md`, checked by `tools/check.sh`).

| Contract item | mCode |
|---|---|
| Manifest | `id: mcode`, `exit_gesture: none`, `disable_esc_exit_key: true`, `min_os_version: 1.4.0`, `test: test.sh`, `background: false` |
| Registration order | `own_escape_key()` before taking the screen, then focus → framebuffer (`app/lcd/ui.py`, `app/lcd/whisplay.py`) |
| First frame | drawn at once after focus (measured 0.87 s after launch under the real daemon on the dev machine) |
| Input | only through the SDK `InputController` (button and key hub); acts only while mCode has the screen; keys pressed elsewhere ignored |
| Controls | tap next, 2× previous, hold-and-release select, 4× back (leaves from Home), 3× screen extra; the AI Engineer is a talk screen (hold talks, 350 ms) |
| Chrome | SDK status bar (page name, Wi-Fi, battery, state dot), content 40–248, footer hints from the same table as the handlers |
| Exit | `app_exit_requested` → stops jobs, the AI request, the monitor, the microphone and the desktop server; measured 0.51 s |
| Data | `WHISPLAY_OS_APP_DATA`; projects outside it (never deleted with the app) |
| Hooks | `install.sh` (checks, creates `.env`; no sudo, nothing outside the data folder), `test.sh` (selftest) |
| SDK | vendored `mfruit_sdk/` 1.5.0, unmodified (`sdk-sync.sh --check` in CI) |

## Decision: the button model

The brief proposed "short press = select, long press = back". mCode uses the
mFruit OS controls instead: the contract binds every app, the owner chose
the same controls for every converted app, and one button must mean the same
everywhere on the device. The footer of each screen says what each gesture
does; on the AI screen a hold is push-to-talk.

## HDMI

mFruit OS has no HDMI display service; apps that use HDMI find the desktop
session themselves (as mDisplay does). mCode does not draw on HDMI: it
serves its IDE to a browser. When a desktop session runs on the device, mCode
opens the IDE there (`web/desktop.py`, using this user's session variables);
otherwise the IDE is reached from a computer after pairing on the LCD. The
LCD keeps working at the same time; both show the same state.

## Audio

The microphone and speaker are opened through ALSA's shared `capture`
(dsnoop) and `playback` (dmix) devices of the Whisplay driver, so mCode does
not block other audio users. The microphone is open only while the AI screen
or the name step of the wizard is shown; sound plays only while mCode has the
screen.

## Leaving mCode

mFruit OS stops the app when you leave it (`background: false`). A running
build, upload or AI request is cancelled; partially applied AI changes can be
reviewed and undone next time (the snapshot is kept). The desktop IDE stops
with it. Run `./run.sh --desktop` on a computer if you want an IDE that keeps
running.

## Release

Bumping `manifest.json`'s `version` on `main` publishes `v<version>` through
`.github/workflows/release.yml` (checks, preflight, smoke test, archive with
SHA256SUMS), like the owner's other apps. `tools/build-release.sh` builds the
same archive locally. The Fruit Store entry is mFruit OS's
`config/catalog.json` (not added yet).
