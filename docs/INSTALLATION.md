# Installation

## On an mFruit OS device

Requirements: mFruit OS 1.4.0 or newer, Python 3.9+, Pillow (both come with
mFruit OS), `alsa-utils` for voice, `git` for version history (optional).

From the Fruit Store once it is listed there, or sideload:

```bash
# on the device, from a copy of this repository or a release archive
mfruitctl sideload ~/mCode            # or: mfruitctl sideload mcode-0.1.3.tar.gz
mfruitctl jobs                        # wait until the install job is done
mfruitctl launch mcode                # or open it from Home
```

The install hook checks Python and Pillow, warns about missing `arecord` /
`aplay` and `git`, warns if your user is not in the `dialout` group, and
creates mCode's `.env` (owner-only). It never uses sudo and changes nothing
outside mCode's data folder. The smoke test (`test.sh`) must pass before the
new version becomes active.

Once, if mCode says your user cannot open serial ports:

```bash
sudo usermod -aG dialout $USER        # then log out and in (or reboot)
```

Then install development tools from **Settings › Development tools** (see
[Toolchains](TOOLCHAINS.md)) and connect Claude (see
[Claude Code](CLAUDE_CODE.md)).

Optional, voice requests: put an OpenAI key in
`~/.whisplay-os/apps/mcode/data/.env` (`OPENAI_API_KEY=...`), or set a local
speech command in the desktop IDE's Settings › Voice. Typing always works.

## On a Linux computer (no mFruit OS)

```bash
git clone https://github.com/Mengkungkao/mCode ~/mCode    # or copy the folder
cd ~/mCode
python3 -c 'import PIL' || sudo apt install python3-pil
./run.sh --desktop            # prints the address and opens your browser
```

Data goes to `~/.local/share/mcode`, projects to `~/mcode-projects`.
`./run.sh --diagnose` reports tools, boards, Claude and audio.
Options: `--port N`, `--lan` (allow computers on the network, then pair with
the printed code), `--no-browser`.

## Updating and removing

mFruit OS updates mCode from its GitHub releases (Settings › Apps on the
device) and keeps the data folder (settings, backups, logs, mCode's tools)
across updates; projects are outside it and never touched.

Removing mCode deletes its code and data folder (including the Arduino CLI
and PlatformIO environment mCode installed in `tools/`). It does **not**
delete your projects (`~/mcode-projects`), Arduino cores and libraries
(`~/.arduino15`, `~/Arduino`), PlatformIO's packages (`~/.platformio`) or
Claude Code and its login.

## What changes on the system

Nothing outside your home folder. Inside it: the data folder, the project
folder, and whatever tools you choose to install (Arduino cores in
`~/.arduino15`, PlatformIO packages in `~/.platformio`, Claude Code in
`~/.local`, ESP-IDF where you put it).
