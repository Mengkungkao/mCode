"""Keep the files the tools read in step with a project's board settings.

The board, upload speed and "USB CDC on boot" live in ``.mcode/project.json``;
the Arduino CLI also reads ``sketch.yaml`` (``default_fqbn``) and PlatformIO
reads ``platformio.ini``. When the settings change (projects.manager.update),
these files are edited line by line, inside the project's own environment
section only, so comments and anything else the user wrote stay as they are.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional

from . import boards
from .model import Project

CDC_FLAG = "-DARDUINO_USB_CDC_ON_BOOT=1"

# ESP-IDF "trimmed" build: only main, the common components and what main
# needs (REQUIRES / PRIV_REQUIRES). Espressif's own examples (hello_world,
# blink, 447 of them in ESP-IDF 6.1.1) start like this. Measured 2026-11-27,
# ESP32-C5 starter project, ESP-IDF 6.1.1: 571 build steps instead of 1120,
# 82 s instead of 128 s on the dev machine (4 jobs).
MINIMAL_BUILD = "idf_build_set_property(MINIMAL_BUILD ON)"
MINIMAL_BUILD_LINES = ("# Fast build: only main and the components it needs (REQUIRES in\n"
                       "# main/CMakeLists.txt). Remove the next line to build every component.\n"
                       + MINIMAL_BUILD + "\n")


def sync(project: Project) -> List[str]:
    """Update sketch.yaml / platformio.ini; returns the files changed."""
    changed = []
    if project.toolchain == boards.ARDUINO_CLI:
        if _sync_sketch_yaml(project):
            changed.append("sketch.yaml")
    elif project.toolchain == boards.PLATFORMIO:
        if _sync_platformio_ini(project):
            changed.append("platformio.ini")
    return changed


# ------------------------------------------------------------------ Arduino

def _sync_sketch_yaml(project: Project) -> bool:
    path = os.path.join(project.path, "sketch.yaml")
    fqbn = project.fqbn()
    if not fqbn or not os.path.isfile(path):
        return False
    lines = _read(path)
    out, found = [], False
    for line in lines:
        if re.match(r"^default_fqbn\s*:", line):
            out.append(f"default_fqbn: {fqbn}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"default_fqbn: {fqbn}")
    return _write(path, lines, out)


# --------------------------------------------------------------- PlatformIO

def _sync_platformio_ini(project: Project) -> bool:
    path = os.path.join(project.path, "platformio.ini")
    board = project.board
    if board is None or not board.pio_board or not os.path.isfile(path):
        return False
    lines = _read(path)
    env = _env_name(project, lines)
    if env is None:
        return False
    out = list(lines)
    out = _set_key(out, env, "board", board.pio_board)
    out = _set_key(out, env, "upload_speed",
                   str(project.upload_speed) if project.upload_speed else None)
    want_cdc = bool(project.usb_cdc_on_boot and board.native_usb and
                    project.framework == "arduino")
    out = _set_flag(out, env, CDC_FLAG, want_cdc)
    return _write(path, lines, out)


def _env_name(project: Project, lines: List[str]) -> Optional[str]:
    envs = [m.group(1) for m in (re.match(r"^\[env:([^\]]+)\]\s*$", l) for l in lines) if m]
    if project.pio_env and project.pio_env in envs:
        return project.pio_env
    for line in lines:
        m = re.match(r"^default_envs\s*=\s*([^,\s]+)", line)
        if m and m.group(1) in envs:
            return m.group(1)
    hardware = [e for e in envs if e != "native"]
    return hardware[0] if hardware else None


def _section(lines: List[str], env: str):
    """(first line inside [env:<env>], index of the next section or len)."""
    start = next((i for i, l in enumerate(lines) if l.strip() == f"[env:{env}]"), None)
    if start is None:
        return None, None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")),
               len(lines))
    # keep trailing blank lines and comments outside the edited block
    while end > start + 1 and (not lines[end - 1].strip() or
                               lines[end - 1].lstrip().startswith((";", "#"))):
        end -= 1
    return start + 1, end


def _key_span(lines: List[str], begin: int, end: int, key: str):
    """(index of "key =", index after its indented continuation lines) or (None, None)."""
    for i in range(begin, end):
        if re.match(rf"^{re.escape(key)}\s*=", lines[i]):
            j = i + 1
            while j < end and lines[j][:1] in (" ", "\t") and lines[j].strip():
                j += 1
            return i, j
    return None, None


def _set_key(lines: List[str], env: str, key: str, value: Optional[str]) -> List[str]:
    begin, end = _section(lines, env)
    if begin is None:
        return lines
    i, j = _key_span(lines, begin, end, key)
    if i is None:
        if value is None:
            return lines
        return lines[:end] + [f"{key} = {value}"] + lines[end:]
    if value is None:
        return lines[:i] + lines[j:]
    return lines[:i] + [f"{key} = {value}"] + lines[j:]


def _set_flag(lines: List[str], env: str, flag: str, on: bool) -> List[str]:
    """Add or remove one flag of ``build_flags``, one per line; other flags (which may
    hold quotes and spaces) are never re-split or rewritten."""
    begin, end = _section(lines, env)
    if begin is None:
        return lines
    i, j = _key_span(lines, begin, end, "build_flags")
    if i is None:
        return lines[:end] + ["build_flags =", f"    {flag}"] + lines[end:] if on else lines
    head, _, inline = lines[i].partition("=")
    in_head = inline.strip() == flag
    rows = [k for k in range(i + 1, j) if lines[k].strip() == flag]
    present = in_head or bool(rows)
    if on == present:
        return lines
    if on:
        return lines[:j] + [f"    {flag}"] + lines[j:]
    out = list(lines)
    for k in reversed(rows):
        del out[k]
    if in_head:
        out[i] = f"{head.rstrip()} ="
    j -= len(rows)
    if out[i].partition("=")[2].strip() == "" and j == i + 1:
        del out[i]                       # nothing left in build_flags
    return out


# ------------------------------------------------------------------ ESP-IDF

def minimal_build(project_path: str) -> Optional[bool]:
    """Whether the project's top CMakeLists.txt trims the build; None when it has
    no ``project(...)`` line mCode can work with."""
    path = os.path.join(project_path, "CMakeLists.txt")
    try:
        lines = _read(path)
    except OSError:
        return None
    if not any(re.match(r"^\s*project\s*\(", l) for l in lines):
        return None
    return any(_is_minimal(l) for l in lines)


def set_minimal_build(project_path: str, on: bool) -> bool:
    """Add or remove the MINIMAL_BUILD line (and mCode's comment above it) just
    before ``project(...)``; returns True when the file changed."""
    path = os.path.join(project_path, "CMakeLists.txt")
    lines = _read(path)
    if on:
        if any(_is_minimal(l) for l in lines):
            return False
        index = next(i for i, l in enumerate(lines) if re.match(r"^\s*project\s*\(", l))
        out = lines[:index] + MINIMAL_BUILD_LINES.rstrip("\n").split("\n") + lines[index:]
    else:
        comment = MINIMAL_BUILD_LINES.split("\n")[:2]
        out = [l for l in lines if not _is_minimal(l)]
        for text in comment:
            if text in out:
                out.remove(text)
    return _write(path, lines, out)


def _is_minimal(line: str) -> bool:
    return bool(re.match(r"^\s*idf_build_set_property\s*\(\s*MINIMAL_BUILD\s+ON\s*\)", line))


# ---------------------------------------------------------------- file i/o

def _read(path: str) -> List[str]:
    with open(path, encoding="utf-8") as handle:
        return handle.read().splitlines()


def _write(path: str, before: List[str], after: List[str]) -> bool:
    if after == before:
        return False
    tmp = path + ".mcode-tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write("\n".join(after) + "\n")
    os.replace(tmp, path)
    return True
