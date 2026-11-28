# iPhone, tablet and mobile browsers

The desktop IDE is one web app with three layouts. They share the same API,
state and render code (`app/web/static/app.js`); `mobile.js` only arranges
them for small touch screens. Nothing is a separate product.

| Layout | When | What it looks like |
|---|---|---|
| Phone | width ≤ 760 px, or height ≤ 500 px (a phone held sideways) | one screen at a time, bottom tab bar, bottom sheets |
| Tablet | 761–1100 px wide and taller than 500 px | the desktop layout with touch-sized controls and narrower panels |
| Desktop | wider | activity bar, explorer, editor, AI panel, bottom panel (unchanged, refined) |

The layout follows the window: rotate the phone or resize the browser and it
switches. Status: IMPLEMENTED; checked in headless Chromium with iPhone 13,
iPad-sized and desktop viewports ([record](records/2026-11-28-dev-machine-2.0.md)).
**Real iPhone Safari: NOT VERIFIED.** Chromium's emulation does not
reproduce Safari's keyboard, safe areas or home-screen mode.

## Opening mCode on a phone

1. On the device: Settings › Desktop IDE › *Use from a computer* (once).
2. *Pair a computer* shows a QR code and a 6-digit code.
3. Scan the QR code with the iPhone's camera, or open the address shown and
   type the code. The phone is now paired with the access shown on the
   screen (3× switches it: Developer, Full access, Viewer).
4. Optional: Safari › Share › *Add to Home Screen*. mCode then opens
   full-screen with its own icon (the web app manifest and
   `apple-mobile-web-app-capable`).

The phone and the device must be on the same network. Plain HTTP on a home
network is the default; see [Security](SECURITY.md#the-desktop-ide) for
tunnels when the network is not trusted.

## Phone layout

Bottom tab bar: **Home · Projects · AI Engineer · Devices · Settings**. The
header shows where you are, a back button in sub-screens and the screen's
actions; a bar under it shows a running build, upload or install (tap: build
view; *Stop* cancels).

| Screen | Contents |
|---|---|
| Home | current project (board, toolchain, last build with flash/RAM use, Build · Flash · Code or *Fix with AI*), quick actions (New Project, Open Project, Ask AI, Build Firmware, Connect Board, Serial Monitor), board and Claude status, recent builds and uploads |
| Projects | search, the open project's files (tap a file to edit; ⋯ or a long press for rename, move, delete, attach to AI), every project as a card (platform, board, toolchain, last build, last edit) with ⋯ for Open, Project settings, Rename, Duplicate, Backups, Export as zip, Delete; New project, Import a zip file, Clone from Git (and Open a folder with full access) |
| Editor | full screen, line wrapping, 16 px code so iOS does not zoom; header: back to Files, file name (• when unsaved), Save, ⋯ (Build, Ask AI about this file, Find, Find and replace, Undo, Redo, switch file, close); a row of code keys above the keyboard (Tab, braces, brackets, `;`, `=`, quotes, `<>`, `&`, `|`, `#`, `/`, `_`, Undo, Redo, Find) that keeps the keyboard open |
| AI Engineer | the conversation in chat bubbles, suggested actions (Create firmware, Explain code, Fix build errors, Improve performance, Review security, Wiring doc, Explain serial output), what the mode allows (tap to change the mode), Claude's steps while it works with Stop, diff review with Apply/Discard (or Keep/Put back), *Try again* after a failure, a composer that grows and stays above the keyboard |
| Build | build output, problems (tap opens the file at the line), tests; Build or Stop in the header |
| Devices | serial monitor, Flash firmware, every connected board (port, USB ID, chipset, driver; Use, Identify chip, Monitor), upload speed and verification, upload history |
| Serial monitor | port, baud, connect, hex, timestamps, auto-scroll, pause, clear, save, reset board, ask AI, search, send with line ending |
| Settings | Claude Code, AI mode, appearance (Dark, Light, Automatic), development tools (install with full access), all settings, Security and paired browsers, Terminal (full access), this board, this browser (sign out), version |

Dialogs become bottom sheets with a grabber; menus become action sheets;
toasts appear under the header.

## iOS details

* `viewport-fit=cover` with `env(safe-area-inset-*)` paddings on the header,
  tab bar, sheets, composer and lists: nothing under the notch or the home
  indicator.
* Height from `visualViewport` (CSS `--vvh`, falling back to `100dvh`): when
  the keyboard opens, the app shrinks to what is visible, the tab bar hides,
  and the composer or the code keys sit on the keyboard.
* Every text field is at least 16 px, so Safari does not zoom on focus.
* `overscroll-behavior: none` and a fixed body: no rubber-band or
  pull-to-refresh while you work.
* System font stack (`-apple-system`, SF Pro on Apple devices), iOS system
  colours, grouped inset lists, 44 px minimum touch targets, a switch for
  on/off settings, reduced motion respected.
* CodeMirror uses its `contenteditable` input on touch screens (native
  selection handles); suggestions while typing are off by default on touch
  screens (Settings › This browser).
* Light and dark: *Automatic* follows the phone (`prefers-color-scheme`).

## Progressive web app

Implemented: `manifest.webmanifest` (name, standalone display, icons 180 and
512 px, colours), Apple home-screen meta tags.

Not implemented, on purpose: a **service worker**. Browsers only allow one
in a secure context (HTTPS or localhost), and plain HTTP on the LAN is not
one. Caching the app shell would not help much either (the app is useless
without the device), and the directive forbids caching private project data
or terminal output in an uncontrolled cache. When mCode is reached through
an HTTPS tunnel, a service worker could cache only the static files; that is
PLANNED and needs the tunnel set-up verified first.

## Performance on phones

* Static files are gzip-compressed with an ETag (first load 242 KB instead
  of 1.3 MB; later loads are `304 Not Modified`).
* xterm.js (480 KB) loads only when a terminal is opened.
* Build output is appended once per animation frame, not per line.
* The browser keeps at most 60 jobs, 4000 output lines and 5000 serial lines.
* When the event stream comes back (the phone woke up, the Wi-Fi changed) or
  the page becomes visible after 20 s, the IDE reloads its state instead of
  showing a build that ended meanwhile as running; the server drops event
  streams of phones that vanished.

## Not verified

Real iPhone Safari (portrait, landscape, keyboard, safe areas, home-screen
mode), an Android phone, an iPad, touch text selection in the editor, and
reconnection after a real Wi-Fi change. The owner's checklist is in
[Testing](TESTING.md#phone-checklist-not-done-yet).
