"""What Claude is told: mCode's rules (system prompt) and the project context.

The context is built fresh for every request from mCode's own services, so
Claude knows the current project, board, MCU, toolchain, libraries, the
connected board, the last build's problems and (when attached) serial
output and files. Everything is size-capped and passed through the redactor.
"""

from __future__ import annotations

import os
from typing import List, Optional

from ..core.redact import redact
from ..projects import files as pfiles
from ..projects.model import Project

MAX_ATTACH_CHARS = 24000
MAX_FILES_LISTED = 80

SYSTEM_RULES = """\
You are the AI Engineer inside mCode Studio, an embedded development app on a small Linux \
device (mFruit OS). You work on one firmware project at a time, as a careful senior embedded \
engineer.

Rules you must follow:
- Work only inside the current project folder. Do not read or change anything outside it.
- You cannot upload, flash or erase boards, change fuses, or install software or libraries. \
mCode does those only after the user confirms on screen. Never say firmware was flashed, or \
that anything was tested on hardware, unless the user told you so.
- If a library is needed, name it and its source (Arduino Library Manager, PlatformIO Registry \
or ESP-IDF Component Registry) and tell the user to install it from Libraries. If you add it to \
platformio.ini lib_deps or idf_component.yml, mCode will ask the user before keeping that.
- Changes to partition tables, bootloader, flash or fuse settings, build scripts (CMakeLists.txt, \
Makefile, *.cmake, *.mk, Python or shell scripts) and git settings are held for the user's \
explicit approval, and mCode's build tools refuse to run until the user decides. Only make them \
when the request needs them, say why, and end your answer so the user can review them.
- Pins and electrical facts: give the source (the board's documentation or a datasheet) or \
mark them clearly as ASSUMPTION. Never invent voltage or current ratings. Note 3.3 V versus \
5 V logic. Keep docs/WIRING.md (Signal | Board pin | Connected to | Voltage | Source) current \
when you assign pins.
- Prefer small, readable changes that fit the project's existing structure and toolchain.
- When mCode's build tool is available, build after changing code and fix what fails.
- The user may be reading your answer on a 240x280 screen. Keep the answer short and plain. \
End with one last line: "SUMMARY: <what you did or found, at most 90 characters>".
"""

MODE_NOTES = {
    "ask": "Mode: Manual (ask before changes). You are editing a staging copy; the user reviews "
           "and approves your changes before they reach the project. You cannot build in this "
           "mode.",
    "assisted": "Mode: Auto. You edit the project directly and may build and run tests with "
                "mCode's tools (mcp__mcode__build, mcp__mcode__run_tests). The user can undo "
                "your changes.",
    "plan": "Mode: Plan. You cannot change files or build in this mode. Read the project and "
            "answer with a concrete, numbered plan: what to change in which files and why, how "
            "to build and test it, and the hardware risks (pins, voltages, partitions). Ask "
            "questions you need answered first. The user carries the plan out in another mode.",
    "advanced": "Mode: Auto + git. You edit the project directly, may build, test and check with "
                "mCode's tools, and may use git through mCode's tools (mcp__mcode__git_status, "
                "git_diff, git_log, git_show, git_commit); there is no shell. The user can undo "
                "your changes.",
}

KINDS = {
    "fix-build": "The last build failed. Find the root cause from the compiler output below, fix "
                 "it in the source, and explain the cause in one or two sentences.",
    "analyze-serial": "Analyse the serial output below. Explain what the firmware is doing, what "
                      "looks wrong, likely causes, and concrete debugging steps or code changes.",
    "review": "Review this project's code: correctness, robustness, hardware use and structure. "
              "List the most important findings first; change code only if asked.",
    "wiring": "Produce or update docs/WIRING.md for this project: GPIO assignments, wiring, "
              "power, voltage compatibility and bus (I2C/SPI/UART) details, each with a source or "
              "marked ASSUMPTION.",
}


def _cap(text: str, limit: int) -> str:
    text = redact(text or "")
    if len(text) <= limit:
        return text
    return "… (earlier part cut)\n" + text[-limit:]


def build_prompt(project: Project, request: str, *, mode: str, kind: str = "chat",
                 device: Optional[dict] = None, last_build: Optional[dict] = None,
                 serial_text: str = "", attachments: Optional[List[str]] = None,
                 libraries: Optional[List[dict]] = None, workspace_root: str = "") -> str:
    board = project.board
    lines = ["<mcode-context>"]
    lines.append(f"Project: {project.name}")
    lines.append(f"Board: {project.board_name}" + (f" — MCU {board.mcu} ({board.arch})" if board else ""))
    tool = {"arduino-cli": "Arduino CLI", "platformio": "PlatformIO", "esp-idf": "ESP-IDF",
            "make": "Make"}.get(project.toolchain, project.toolchain)
    lines.append(f"Toolchain: {tool}, framework {project.framework}, language {project.language}")
    if project.fqbn():
        lines.append(f"Arduino FQBN: {project.fqbn()}")
    if project.pio_env:
        lines.append(f"PlatformIO environment: {project.pio_env}")
    if board and board.notes:
        lines.append(f"Board notes (from mCode's catalogue): {board.notes}")
    if project.toolchain == "esp-idf":
        from ..projects import buildfiles
        if buildfiles.minimal_build(project.path):
            lines.append("ESP-IDF fast build is on (idf_build_set_property(MINIMAL_BUILD ON) in "
                         "CMakeLists.txt): only main and the components it lists are built. When "
                         "the code uses another ESP-IDF component (esp_wifi, nvs_flash, "
                         "esp_driver_gpio, ...), add it to REQUIRES in main/CMakeLists.txt.")
    lines.append(f"Serial baud rate: {project.baud}")
    if device:
        lines.append(f"Connected board: {device.get('description')} on {device.get('port')} "
                     f"(USB {device.get('usb_id') or 'unknown'})")
    else:
        lines.append("Connected board: none selected")
    if libraries:
        names = ", ".join(f"{l.get('name')} {l.get('version', '')}".strip() for l in libraries[:40])
        lines.append(f"Libraries: {names}")
    elif project.libraries:
        lines.append(f"Libraries: {', '.join(project.libraries)}")
    root = workspace_root or project.path
    listing = list(pfiles.walk_sources(root))
    lines.append(f"Files ({len(listing)}): " + ", ".join(listing[:MAX_FILES_LISTED]) +
                 (" …" if len(listing) > MAX_FILES_LISTED else ""))
    lines.append(MODE_NOTES.get(mode, ""))
    if last_build and (kind == "fix-build" or last_build.get("state") not in (None, "succeeded")):
        lines.append(f"Last {last_build.get('action', 'build')}: {last_build.get('state')}"
                     f" — {last_build.get('headline', '')}")
        diags = last_build.get("diagnostics") or []
        for diag in diags[:25]:
            where = f"{diag.get('file')}:{diag.get('line')}" if diag.get("file") else ""
            lines.append(f"  {diag.get('severity')}: {where} {diag.get('message')}")
        if last_build.get("missing_headers"):
            lines.append("  Missing headers: " + ", ".join(last_build["missing_headers"]))
        tail = "\n".join(last_build.get("output_tail") or [])
        if tail:
            lines.append("Build output (end):\n```\n" + _cap(tail, 12000) + "\n```")
    elif last_build:
        lines.append(f"Last {last_build.get('action', 'build')}: {last_build.get('state')}")
    if serial_text:
        lines.append("Serial monitor output (most recent last):\n```\n" +
                     _cap(serial_text, MAX_ATTACH_CHARS) + "\n```")
    for rel in attachments or []:
        try:
            data = pfiles.read_text(root, rel)
        except Exception as exc:       # an attachment that vanished: say so, continue
            lines.append(f"Attached file {rel}: not readable ({exc})")
            continue
        lines.append(f"Attached file {data['path']}:\n```\n" + _cap(data["text"], MAX_ATTACH_CHARS)
                     + "\n```")
    lines.append("</mcode-context>")
    task = KINDS.get(kind, "")
    body = "\n".join(l for l in lines if l)
    request = (request or "").strip()
    return f"{body}\n\n{task}\n\nRequest: {request}".strip() if task else \
        f"{body}\n\nRequest: {request}"


def summary_line(text: str) -> str:
    """The 'SUMMARY:' line Claude ends with, or the first sentence."""
    for line in reversed((text or "").strip().splitlines()):
        stripped = line.strip().strip("*_ ")
        if stripped.upper().startswith("SUMMARY:"):
            return stripped[8:].strip().strip("*_ ").strip()[:120]
    first = (text or "").strip().split("\n", 1)[0]
    return first[:120]


def strip_summary(text: str) -> str:
    out = [l for l in (text or "").splitlines() if not l.strip().strip("*_ ").upper()
           .startswith("SUMMARY:")]
    return "\n".join(out).strip()
