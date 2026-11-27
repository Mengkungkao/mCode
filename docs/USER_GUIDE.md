# User guide

mCode Studio has two faces that share everything (projects, builds, boards,
Claude's conversation): the **Whisplay screen** with its button and
microphone, and the **desktop IDE** in a browser. Use whichever is at hand;
switch any time.

## The button (same as everywhere on mFruit OS)

| Gesture | Does | Keyboard |
|---|---|---|
| tap | next item | ↓ → Tab |
| 2× | previous item | ↑ ← |
| hold, then release | open / choose (the footer says what) | Enter |
| 4× | back; from Home it leaves mCode | Esc |
| 3× | the screen's extra (menu, stop, ask AI) | — |
| hold (AI Engineer) | **talk while held**, release to send | Space (held) |

A hold never acts while the button is down: the footer changes to
"release to …" and the action happens when you let go. The footer of every
screen lists exactly the gestures that screen handles.

## Home

Three lines say where you are: **AI** (Connected / Not signed in / Not
installed), **Project** (the current one) and **Board** (the chosen USB
board and port). Four tiles: **AI Engineer**, **My Projects**, **Connect
Board**, **Settings**. A small light in the status bar shows Claude's state
next to Wi-Fi and battery.

## AI Engineer

1. **Hold** the button and speak: “Create an ESP32 project that drives eight
   servos with a PCA9685.” The screen says *Listening…*.
2. **Release.** *Processing…* turns your words into text.
3. mCode shows **I heard “…”** with *Send to Claude*, *Talk again*, *Cancel*
   (Settings › *Send voice at once* skips this step).
4. *Claude is working* shows each step (“Editing src/main.cpp”, “Building the
   firmware”). Tap for all steps; 3× stops Claude.
5. The result:
   * **N files changed** (Ask mode): *Apply changes*, *View changes* (the diff
     per file), *Discard*. Applying backs the project up first.
   * **Needs your OK**: a change to the partition table, bootloader/flash/fuse
     settings or the dependency list is held until you keep it.
   * **Done** with Claude's one-line summary: *Build now*, *Read the answer*,
     *Undo changes* (Assisted/Advanced), *Ask something else*.
   * **Not finished** with the reason (usage limit, no network, login
     expired): *Try again*, *Details*.

No microphone or no speech service? Type on a USB/Bluetooth keyboard (any key
starts a typed request; Enter sends), or use the desktop IDE.

3× opens the **AI menu**: type a request, *Fix the last build*, *Explain
serial output*, *Review my code*, *Wiring document*, the **mode** (Ask /
Assisted / Advanced), *Undo Claude's last change*, *New conversation*,
*Claude account*.

### The three modes

| Mode | Claude may | You |
|---|---|---|
| Ask before changes (default) | read and edit a **copy** of the project | approve the diff before anything changes |
| Assisted | edit the project, **build** and run host **tests** through mCode | review afterwards, *Undo* any time |
| Advanced | as Assisted, plus static analysis, clean and `git` (status/diff/log/add/commit); web search if allowed in Settings | review afterwards, *Undo* any time |

In every mode Claude **cannot** flash, erase, change fuses, install software
or libraries, or touch files outside the project. See
[Claude Code in mCode](CLAUDE_CODE.md).

## Projects

**My Projects** lists your projects (current one marked). Choose one for
*Ask AI*, *Build*, *Flash*, *Board settings*, *Serial monitor*, *Project info* and *More*
(*Run tests*, *Rebuild*, *Clean*, *Back up now*, *Duplicate*, *Delete
project*: deleting moves it to the project trash).

**New project** has four steps: **Platform** (ESP32, Arduino, Raspberry Pi
Pico, Generic C/C++) → **Board** → **Tool** (Arduino, PlatformIO with the
Arduino or ESP-IDF framework, ESP-IDF; "not installed yet" marks tools to
install) → **Name** (hold and say it, type it, or take the suggestion).
*Creating project…* generates the layout the tool expects and says what is
still missing (for example "Needs: ESP32 core (Espressif)" with an *Install*
row).

**Board settings** works like the Arduino IDE's Tools menu. The top line
says what is on the port ("ttyACM0: ESP32-C3 (not this board)" after
*Identify chip*), and *Use ESP32-C3* switches the project to it. Then:
*Board* (the boards the project's tool supports), *USB CDC on boot* (ESP32
with built-in USB, Arduino framework: on = `Serial` prints on the chip's own
USB port), *Upload speed*, *Port*, and for Arduino CLI projects the board's
own menus from the installed core (CPU frequency, flash size, partition
scheme, debug level…). ESP-IDF projects have *Fast build* (on for new
projects): only `main` and the components it lists in `REQUIRES` are built,
about half the work. Changes are saved at once and `sketch.yaml` /
`platformio.ini` follow; build again before flashing (mCode refuses
firmware built for the old settings). *Erase all flash before upload* is not
offered: erasing is a separate, confirmed action. The desktop IDE has the
same settings under *Project settings*.

Projects live in `~/mcode-projects` (Settings › Project folder). Each has a
`.mcode/project.json` with its board and settings; the folder works with the
tools directly too (`pio run`, `arduino-cli compile`, `idf.py build`).

## Build and flash

**Build** shows *Building…* with the phase and time, then **Build
successful** with flash and RAM use, or **Build failed** with the first
problem ("Missing library: ServoDriver") and *Ask AI to fix*, *View
details*. Asking the AI to fix sends the compiler output to Claude; in Ask
mode you approve the fix and mCode rebuilds.

**Flash** never starts on its own. mCode first checks:

* the firmware exists and is newer than every source file;
* the chosen board is connected and you may open its port;
* no other program has the port open (the serial monitor pauses itself);
* the board fits the project (an Uno's USB ID on an ESP32 project, an ESP32
  on an AVR project or a different ESP32 chip stops the flash; a plain
  USB-UART adapter cannot tell, so mCode says so). When the chip was
  identified, **Can't flash yet** offers *Make it ESP32-C5* (the project's
  board changes and it is rebuilt) or, when the project's tool has no such
  board, *New ESP32-C5 project*. The new-project wizard lists an identified,
  connected chip first ("connected now").

Then **Flash firmware?** shows board, project and port: hold on *Confirm
flash*. *Flashing…* shows progress; **Flash successful** says whether the
written data was verified. Failures explain the usual fixes (hold BOOT and
press RESET on ESP32 boards, the old-bootloader Nano, cable, speed).

## Connect Board

Lists USB serial devices as mCode identifies them: "ESP32 with built-in USB",
"Arduino Uno (by its USB ID)", "CP210x adapter: the board behind it is
unknown". Choose one for its details (USB ID, driver, status) and *Use for
<project>*, *Identify chip* (ESP32 boards: esptool reads the chip; the board
restarts) or *Serial monitor*. Plugging and unplugging updates the list by
itself. When the list is empty, *What to check* says what mCode sees on USB
(nothing, a power/OTG port that works only as a device, or a device without a
serial port).

## Serial monitor

*Connect* at the project's baud rate (change it with *Baud rate*). Connected:
tap pauses/resumes, 2× clears, 3× asks Claude what the output means, hold
disconnects, 4× goes back (the monitor keeps running). If the board resets
or re-plugs (native USB after flashing), the monitor reconnects by itself.

## Settings

Claude Code account, AI mode, *Send voice at once*, Sound (spoken, tones,
off), Wi-Fi (managed by mFruit OS), Desktop IDE, Development tools (install
Claude Code, the Arduino CLI, PlatformIO, esptool, Arduino cores), About.
Everything else is in the desktop IDE's Settings and
[Configuration](CONFIGURATION.md).

## The desktop IDE

* **On the device's HDMI desktop**: mCode opens it in the browser when a
  desktop session is running (Settings › *Open the IDE on the HDMI desktop*).
* **On this computer** (mCode run with `./run.sh --desktop`): it opens itself.
* **From a laptop** (headless Pi Zero / Orange Pi): on the LCD, Settings ›
  Desktop IDE › *Use from a computer* (confirm), then scan the pairing QR
  code or open the address and type the 6-digit code.

Layout: activity bar (Dashboard, Explorer, Hardware, Libraries, Version
history), editor tabs in the middle, AI Engineer on the right (or left, in
Settings › Editor layout), bottom panel (Build output, Problems, Terminal,
Serial monitor, Tests). Drag the edges to resize; ✦ and ▁ hide the AI and
bottom panels.

Keys: Ctrl+S save (files also save themselves 1.5 s after you stop typing),
Ctrl+B build, Ctrl+Space complete, Ctrl+F find, Ctrl+H replace, Ctrl+G go to
line, Ctrl+/ comment, Ctrl+Q fold, Ctrl+` terminal, Ctrl+Enter send to
Claude.

The AI panel shows the conversation, Claude's steps while it works (Stop),
and proposed changes as diffs with a checkbox per file (*Apply selected*,
*Discard all*). Attach the open file (+ file) or the serial output
(+ serial) to a request. Quick actions: Fix build, Explain serial, Review
code, Wiring doc.

## Walk-throughs

**ESP32-S3 with PlatformIO, by voice (Scenario A).** Settings › Development
tools: PlatformIO installed. AI Engineer: Claude account connected. My
Projects › New project › ESP32 › ESP32-S3 › PlatformIO (Arduino) › say
"Servo Arm" › Create. Ask AI › hold: "Write firmware for eight servos on a
PCA9685 over I2C, with a serial command to set each angle." Review the
changes › Apply. Build. Connect the board by USB › Connect Board › choose it ›
Use for Servo Arm. Flash › Confirm flash. Serial monitor › Connect.

**Arduino Uno robot (Scenario B).** New project › Arduino › Arduino Uno ›
Arduino › "Rover". Ask: "Motor control for an L298N: left motor on pins 5/6,
right on 9/10, a serial command language." Ask for the wiring document; check
docs/WIRING.md (Claude marks anything not from a datasheet as an
assumption). Build; if it fails, *Ask AI to fix*. Flash. Serial monitor.

**Existing project (Scenario C).** Desktop IDE › project menu › *Open a
folder…* (or *Import*, *Clone from Git…*, *Import a zip file…*). Ask "Review
this project", Build, *Fix build* on failure, review and apply, rebuild, Test.

**Board troubleshooting (Scenario D).** Serial monitor › Connect › reproduce
the problem › 3× (*Ask AI*) or Explain serial in the IDE. Review Claude's
diagnosis and fix › Build › Flash.
