# Troubleshooting

Start with `./run.sh --diagnose` (in the app folder, or
`~/.whisplay-os/apps/mcode/current` on a device): it lists tools, Claude's
login, boards, audio and permissions. mCode's log under mFruit OS is
`~/.whisplay-os/logs/mcode.log`; every job's full output is in
`<data>/logs/jobs/`.

| Problem | What to do |
|---|---|
| **AI: "Claude Code is not installed"** | Settings › Development tools › Claude Code › Install, or `curl -fsSL https://claude.ai/install.sh \| bash`. If it is installed somewhere unusual, set Settings › Claude Code program. |
| **AI: "Not signed in" / login expired** | Connect again (LCD: AI Engineer or Settings › Claude Code › *Connect Claude account*; IDE: the Claude pill). On the LCD, step 1's QR code opens Claude's sign-in; copy the code it shows, tap, and scan step 2's QR code to paste it from the same phone. |
| **Sign-in step 2 page does not open on the phone** | The phone must be on the same Wi-Fi as the device (not mobile data). The link works once and only while the device shows step 1/2; *try again* makes a new one. Or type the code on a USB keyboard. |
| **AI: "usage limit"** | Your Claude plan's limit for now; try later. The project is as it was (Ask mode) or can be undone. |
| **AI: "cannot be reached"** | Check Wi-Fi; Claude Code needs the internet. |
| **AI: "ran out of memory"** | The board is too small for Claude Code (Pi Zero 2 W). Use the desktop IDE from a bigger computer, or a Pi 4/5. |
| **"Approve or discard Claude's previous changes first"** | A review is waiting: AI Engineer › Apply / Discard, or the IDE's AI panel. |
| **Voice: "Voice is off"** | No speech service: add `OPENAI_API_KEY` to mCode's `.env` or set a local speech command. Typing works without it. |
| **Voice: "I did not catch that"** | Hold the button for the whole sentence; speak after the tone. `--diagnose` shows which microphone is used. |
| **Build: "Arduino CLI / PlatformIO is not installed"** | Settings › Development tools › Install. |
| **Build: "ESP32 core (Espressif) is needed first"** | Install the core when mCode offers it (several hundred MB). |
| **Build failed: "Missing library: X"** | Desktop IDE › Libraries › search and install, or ask the AI which library provides the header. |
| **Build: "undefined reference to `millis`" / "`app_main`" / "`digitalWrite`"** (Arduino) | An earlier build was stopped part-way and left a half-built core. 0.1.3 and later start the next build clean by themselves; on older versions use *Rebuild* (or delete the project's `.mcode/build` folder). |
| **ESP-IDF install fails** | The job log (Build output / `<data>/logs/jobs/`) names the step. "needs Python 3 with venv": `sudo apt install python3-venv`. "free space": ESP-IDF needs about 7 GB free while installing. A network failure leaves only `~/esp/v<version>/esp-idf.mcode-partial`, which the next attempt removes; install again. |
| **First PlatformIO build is slow** | PlatformIO downloads the platform and compiler on first use (hundreds of MB for ESP32). Later builds are faster. |
| **Builds are slow on the device** | The first build of a project compiles the whole framework; later builds only what changed, so use *Build*, not *Rebuild*, unless something is wrong. Since 0.1.5 mCode runs one compiler per CPU core while about 160 MB of free memory per job allows (3 on a 1 GB Orange Pi Zero 2W; Settings › Compiler jobs, 0 = automatic; a higher number is faster but may swap). On a 1 GB board, ESP-IDF itself is much faster than PlatformIO with the ESP-IDF framework (measured on the .130: 8.6 min vs 27.7 min for a first build). |
| **Flash: "Build the project first" / "The source changed"** | Build again: mCode only flashes the firmware it checked. |
| **Flash: "is an Arduino Uno (by its USB ID), but the project is for ESP32-S3"** | Wrong board or port: choose the right one in Connect Board, or change the project's board. |
| **Flash: "esptool found an ESP32-C5… but the project is for ESP32"** | The project was made for another ESP32 than the one connected (*ESP32 DevKit* is the original ESP32). Choose *Make it ESP32-C5* on that screen (Arduino CLI or ESP-IDF projects), or *New ESP32-C5 project* (PlatformIO has no ESP32-C5). |
| **Flash: "The firmware was built for ESP32 DevKit; the project is now for ESP32-C5"** | The project's board changed after the last build: build again. |
| **The board is not the project's board** (an ESP32-C3 on an ESP32-C5 project) | Project › *Board settings* › *Use ESP32-C3* (shown after *Identify chip*), or choose *Board*; then *Build* and *Flash*. Nothing prints on the chip's own USB port? Turn on *USB CDC on boot* (Arduino framework). |
| **Flash: "<chip> adapter: mCode cannot see which board is behind it"** | Make sure it is your board, then confirm; *Identify chip* tells ESP32 chips apart. |
| **Flash: "The board did not answer"** (ESP32) | Hold BOOT, press and release RESET (or plug in while holding BOOT), release BOOT, try again; try a lower upload speed or another cable. |
| **Flash: "not in sync" / "programmer is not responding"** (AVR) | Nano clones: choose *Arduino Nano (old bootloader)*. Check the cable and the port. |
| **Connect Board: "No board found"** | *What to check* says what mCode sees on USB: nothing at all (charge-only cable, unpowered board, or the board is on a power/OTG port that works only as a device: use another port; [details](HARDWARE.md#when-no-board-is-found)), or a device without a serial port (download mode, or a missing driver). |
| **"No permission to open the port"** | `sudo usermod -aG dialout $USER`, then log in again. |
| **"is open in minicom (1234)"** | Close the other program first. |
| **Serial monitor shows nothing** | Wrong baud rate (the project's is in its info; ESP32 boot messages are 115200, the ROM uses 74880). ESP32-S3/C3/C5/C6 on their own USB port print only with *USB CDC on boot* (project settings). |
| **Desktop IDE: "Sign in again"** | Open the link mCode printed, or pair again (LCD › Settings › Desktop IDE). *New pairing code* signs every browser out. |
| **Desktop IDE not reachable from a laptop** | LCD › Settings › Desktop IDE › *Use from a computer* must be on; same network; the address shown there. |
| **"File changed on disk"** | A build, Claude or another editor changed it: load the disk version or keep yours. |
| **Something broke after an AI change** | AI menu › *Undo Claude's last change*, or the IDE's project menu › Backups › Restore. |
