# Troubleshooting

Start with `./run.sh --diagnose` (in the app folder, or
`~/.whisplay-os/apps/mcode/current` on a device): it lists tools, Claude's
login, boards, audio and permissions. mCode's log under mFruit OS is
`~/.whisplay-os/logs/mcode.log`; every job's full output is in
`<data>/logs/jobs/`.

| Problem | What to do |
|---|---|
| **AI: "Claude Code is not installed"** | Settings › Development tools › Claude Code › Install, or `curl -fsSL https://claude.ai/install.sh \| bash`. If it is installed somewhere unusual, set Settings › Claude Code program. |
| **AI: "Not signed in" / login expired** | Connect again (QR code on the LCD, or the Claude pill in the IDE). On the device without a browser, type the code the sign-in page shows on a USB keyboard, or paste it in the desktop IDE. |
| **AI: "usage limit"** | Your Claude plan's limit for now; try later. The project is as it was (Ask mode) or can be undone. |
| **AI: "cannot be reached"** | Check Wi-Fi; Claude Code needs the internet. |
| **AI: "ran out of memory"** | The board is too small for Claude Code (Pi Zero 2 W). Use the desktop IDE from a bigger computer, or a Pi 4/5. |
| **"Approve or discard Claude's previous changes first"** | A review is waiting: AI Engineer › Apply / Discard, or the IDE's AI panel. |
| **Voice: "Voice is off"** | No speech service: add `OPENAI_API_KEY` to mCode's `.env` or set a local speech command. Typing works without it. |
| **Voice: "I did not catch that"** | Hold the button for the whole sentence; speak after the tone. `--diagnose` shows which microphone is used. |
| **Build: "Arduino CLI / PlatformIO is not installed"** | Settings › Development tools › Install. |
| **Build: "ESP32 core (Espressif) is needed first"** | Install the core when mCode offers it (several hundred MB). |
| **Build failed: "Missing library: X"** | Desktop IDE › Libraries › search and install, or ask the AI which library provides the header. |
| **First PlatformIO build is slow** | PlatformIO downloads the platform and compiler on first use (hundreds of MB for ESP32). Later builds are faster. |
| **Flash: "Build the project first" / "The source changed"** | Build again: mCode only flashes the firmware it checked. |
| **Flash: "is an Arduino Uno (by its USB ID), but the project is for ESP32-S3"** | Wrong board or port: choose the right one in Connect Board, or change the project's board. |
| **Flash: "<chip> adapter: mCode cannot see which board is behind it"** | Make sure it is your board, then confirm; *Identify chip* tells ESP32 chips apart. |
| **Flash: "The board did not answer"** (ESP32) | Hold BOOT, press and release RESET (or plug in while holding BOOT), release BOOT, try again; try a lower upload speed or another cable. |
| **Flash: "not in sync" / "programmer is not responding"** (AVR) | Nano clones: choose *Arduino Nano (old bootloader)*. Check the cable and the port. |
| **"No permission to open the port"** | `sudo usermod -aG dialout $USER`, then log in again. |
| **"is open in minicom (1234)"** | Close the other program first. |
| **Serial monitor shows nothing** | Wrong baud rate (the project's is in its info; ESP32 boot messages are 115200, the ROM uses 74880). ESP32-S3/C3 on their own USB port print only with *USB CDC on boot* (project settings). |
| **Desktop IDE: "Sign in again"** | Open the link mCode printed, or pair again (LCD › Settings › Desktop IDE). *New pairing code* signs every browser out. |
| **Desktop IDE not reachable from a laptop** | LCD › Settings › Desktop IDE › *Use from a computer* must be on; same network; the address shown there. |
| **"File changed on disk"** | A build, Claude or another editor changed it: load the disk version or keep yours. |
| **Something broke after an AI change** | AI menu › *Undo Claude's last change*, or the IDE's project menu › Backups › Restore. |
