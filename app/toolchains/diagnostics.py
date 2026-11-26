"""Turn compiler and tool output into structured problems.

Understands GCC/Clang diagnostics (``file:line:col: error: message``, as
printed by avr-gcc, xtensa/riscv gcc, arm-none-eabi-gcc and host gcc), linker
errors, missing headers, Arduino CLI's library conflict notes, PlatformIO and
ESP-IDF failures and size reports. The result feeds the Problems list,
the LCD's "Build failed" screen ("Missing library: ServoDriver") and the
context sent to Claude when you ask it to fix the build.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from typing import List, Optional

_GCC = re.compile(r"^(?P<file>[^:\n]+?):(?P<line>\d+)(?::(?P<col>\d+))?:\s*"
                  r"(?P<sev>fatal error|error|warning|note):\s*(?P<msg>.+)$")
_MISSING_HEADER = re.compile(r"fatal error:\s*(?P<header>[^:\s]+):\s*No such file or directory")
_UNDEFINED = re.compile(r"undefined reference to [`'](?P<sym>[^'`]+)'")
_MULTI_LIB = re.compile(r'Multiple libraries were found for "(?P<header>[^"]+)"')
_USED_LIB = re.compile(r"^\s*Used:\s*(?P<path>.+)$")
_NOT_USED = re.compile(r"^\s*Not used:\s*(?P<path>.+)$")
_ARDUINO_SIZE = re.compile(r"Sketch uses (?P<used>\d+) bytes \((?P<pct>\d+)%\) of program storage"
                           r" space\. Maximum is (?P<max>\d+) bytes")
_ARDUINO_RAM = re.compile(r"Global variables use (?P<used>\d+) bytes \((?P<pct>\d+)%\) of dynamic"
                          r" memory")
_PIO_SIZE = re.compile(r"^(?P<kind>RAM|Flash):\s*\[[^\]]*\]\s*(?P<pct>[\d.]+)% \(used (?P<used>\d+)"
                       r" bytes from (?P<max>\d+) bytes\)")
_IDF_SIZE = re.compile(r"(?P<name>\S+\.bin) binary size (?P<size>0x[0-9a-fA-F]+) bytes\. "
                       r"Smallest app partition is (?P<part>0x[0-9a-fA-F]+) bytes\. "
                       r"(?P<free>0x[0-9a-fA-F]+) bytes \((?P<pct>\d+)%\) free")
_TOOL_ERRORS = [
    (re.compile(r"Error during build: (.+)"), "build"),
    (re.compile(r"Error during Upload: (.+)", re.I), "upload"),
    (re.compile(r"Platform '([^']+)' not found"), "core-missing"),
    (re.compile(r"Error resolving dependencies for (.+)"), "dependency"),
    (re.compile(r"UnknownPackageError: (.+)"), "dependency"),
    (re.compile(r"Library Manager: Installing (.+)"), "info"),
    (re.compile(r"A fatal error occurred: (.+)"), "esptool"),
    (re.compile(r"\*\*\* \[.+\] Error (\d+)"), "make"),
    (re.compile(r"ninja: build stopped: (.+)"), "build"),
]

SEVERITY = {"fatal error": "error", "error": "error", "warning": "warning", "note": "note"}


@dataclass
class Diagnostic:
    severity: str                  # error | warning | note
    message: str
    file: str = ""                 # relative to the project when inside it
    line: int = 0
    column: int = 0
    in_project: bool = False
    code: str = ""                 # missing-header | undefined | conflict | tool

    def where(self) -> str:
        if not self.file:
            return ""
        return f"{self.file}:{self.line}" + (f":{self.column}" if self.column else "")


@dataclass
class Report:
    diagnostics: List[Diagnostic] = field(default_factory=list)
    missing_headers: List[str] = field(default_factory=list)
    undefined: List[str] = field(default_factory=list)
    conflicts: List[dict] = field(default_factory=list)
    tool_errors: List[str] = field(default_factory=list)
    size: dict = field(default_factory=dict)

    @property
    def errors(self) -> List[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]

    @property
    def warnings(self) -> List[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "warning"]

    def headline(self) -> str:
        """One short line for the small screen."""
        if self.missing_headers:
            return f"Missing library: {os.path.splitext(self.missing_headers[0])[0]}"
        if self.errors:
            first = self.errors[0]
            text = first.message
            return f"{os.path.basename(first.file)}:{first.line} {text}" if first.file else text
        if self.undefined:
            return f"Undefined: {self.undefined[0]}"
        if self.tool_errors:
            return self.tool_errors[0]
        return ""

    def to_dict(self) -> dict:
        return {"diagnostics": [asdict(d) for d in self.diagnostics[:300]],
                "missing_headers": self.missing_headers, "undefined": self.undefined[:50],
                "conflicts": self.conflicts, "tool_errors": self.tool_errors[:20],
                "size": self.size, "errors": len(self.errors), "warnings": len(self.warnings),
                "headline": self.headline()}


def _relative(path: str, project_path: str) -> tuple:
    path = path.strip()
    if not project_path:
        return path, False
    candidates = [path]
    if not os.path.isabs(path):
        candidates.append(os.path.join(project_path, path))
    for candidate in candidates:
        try:
            real = os.path.realpath(candidate)
        except (OSError, ValueError):
            continue
        root = os.path.realpath(project_path)
        if real.startswith(root + os.sep):
            return os.path.relpath(real, root).replace(os.sep, "/"), True
    return path, False


def parse(output: str, project_path: str = "") -> Report:
    report = Report()
    seen = set()
    conflict: Optional[dict] = None
    for raw in output.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        match = _GCC.match(line)
        if match and not line.startswith(("In file included", "                 from")):
            file, in_project = _relative(match.group("file"), project_path)
            sev = SEVERITY[match.group("sev")]
            message = match.group("msg").strip()
            key = (file, match.group("line"), sev, message)
            if key not in seen:
                seen.add(key)
                diag = Diagnostic(sev, message, file, int(match.group("line")),
                                  int(match.group("col") or 0), in_project)
                header = _MISSING_HEADER.search(line)
                if header:
                    diag.code = "missing-header"
                    if header.group("header") not in report.missing_headers:
                        report.missing_headers.append(header.group("header"))
                report.diagnostics.append(diag)
            continue
        undefined = _UNDEFINED.search(line)
        if undefined:
            symbol = undefined.group("sym")
            if symbol not in report.undefined:
                report.undefined.append(symbol)
                report.diagnostics.append(Diagnostic("error", f"undefined reference to {symbol}",
                                                     code="undefined"))
            continue
        multi = _MULTI_LIB.search(line)
        if multi:
            conflict = {"header": multi.group("header"), "used": "", "not_used": []}
            report.conflicts.append(conflict)
            continue
        if conflict is not None:
            used = _USED_LIB.match(line)
            not_used = _NOT_USED.match(line)
            if used:
                conflict["used"] = used.group("path").strip()
                continue
            if not_used:
                conflict["not_used"].append(not_used.group("path").strip())
                continue
            conflict = None
        size = _ARDUINO_SIZE.search(line)
        if size:
            report.size["flash"] = {"used": int(size.group("used")), "max": int(size.group("max")),
                                    "percent": float(size.group("pct"))}
            continue
        ram = _ARDUINO_RAM.search(line)
        if ram:
            report.size["ram"] = {"used": int(ram.group("used")), "percent": float(ram.group("pct"))}
            continue
        pio = _PIO_SIZE.match(line)
        if pio:
            report.size[pio.group("kind").lower()] = {"used": int(pio.group("used")),
                                                      "max": int(pio.group("max")),
                                                      "percent": float(pio.group("pct"))}
            continue
        idf = _IDF_SIZE.search(line)
        if idf:
            used = int(idf.group("size"), 16)
            part = int(idf.group("part"), 16)
            report.size["flash"] = {"used": used, "max": part,
                                    "percent": round(100.0 - float(idf.group("pct")), 1)}
            continue
        for pattern, kind in _TOOL_ERRORS:
            found = pattern.search(line)
            if found and kind != "info":
                text = line.strip()
                if text not in report.tool_errors:
                    report.tool_errors.append(text)
                break
    return report


_PROGRESS = [
    re.compile(r"\((?P<pct>\d{1,3}) ?%\)"),                    # esptool v4: (12 %)
    re.compile(r"\]\s+(?P<pct>\d{1,3}(?:\.\d)?)%"),            # esptool v5 / bossac: ] 45.3%
    re.compile(r"\|\s*(?P<pct>\d{1,3})%\s"),                   # avrdude: | 100% 1.23s
    re.compile(r"^\[(?P<n>\d+)/(?P<total>\d+)\]"),             # ninja (ESP-IDF): [12/980]
]


def progress(line: str) -> Optional[float]:
    """A fraction 0..1 if the line reports progress."""
    for pattern in _PROGRESS:
        match = pattern.search(line)
        if not match:
            continue
        groups = match.groupdict()
        if "total" in groups and groups.get("total"):
            total = int(groups["total"])
            return int(groups["n"]) / total if total else None
        try:
            return min(1.0, float(groups["pct"]) / 100.0)
        except (TypeError, ValueError):
            return None
    return None


_PHASES = [
    (re.compile(r"Detecting libraries used", re.I), "Finding libraries"),
    (re.compile(r"Compiling sketch", re.I), "Compiling your code"),
    (re.compile(r"Compiling libraries", re.I), "Compiling libraries"),
    (re.compile(r"Compiling core", re.I), "Compiling the core"),
    (re.compile(r"Linking", re.I), "Linking"),
    (re.compile(r"^Compiling \.pio", re.I), "Compiling"),
    (re.compile(r"^Building in release mode|^Processing \S+ \(", re.I), "Preparing"),
    (re.compile(r"Tool Manager: Installing|Platform Manager: Installing|Downloading", re.I),
     "Downloading tools"),
    (re.compile(r"Library Manager: Installing", re.I), "Installing libraries"),
    (re.compile(r"Connecting\.\.\.|Connecting\s*\.", re.I), "Connecting to the board"),
    (re.compile(r"Chip is |Chip type:|Detecting chip type", re.I), "Board found"),
    (re.compile(r"Erasing flash|Flash will be erased", re.I), "Erasing"),
    (re.compile(r"Writing at 0x|Writing \||writing flash", re.I), "Writing firmware"),
    (re.compile(r"Reading \||Verifying|Hash of data verified", re.I), "Verifying"),
    (re.compile(r"Hard resetting|Leaving\.\.\.", re.I), "Restarting the board"),
    (re.compile(r"Testing\.\.\.|test/\S+ \[", re.I), "Testing"),
]


def phase(line: str) -> str:
    for pattern, name in _PHASES:
        if pattern.search(line):
            return name
    return ""
