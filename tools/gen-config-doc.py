#!/usr/bin/env python3
"""Write docs/CONFIGURATION.md from the settings schema (app/core/config.py).

    python3 tools/gen-config-doc.py           write the file
    python3 tools/gen-config-doc.py --check   exit 1 if the file is out of date
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.core.config import FIELDS, SECTIONS  # noqa: E402

HEAD = """\
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
"""


def render() -> str:
    lines = [HEAD]
    section = None
    for field in FIELDS:
        name = SECTIONS.get(field.section, field.section)
        if name != section:
            section = name
            lines.append(f"\n## {name}\n")
            lines.append("| Key | Default | Allowed | LCD | Meaning |")
            lines.append("|---|---|---|---|---|")
        if field.choices:
            allowed = ", ".join(f"`{c}`" for c in field.choices)
        elif field.kind == "bool":
            allowed = "`true`, `false`"
        elif field.kind == "int":
            allowed = f"{field.minimum if field.minimum is not None else ''}–" \
                      f"{field.maximum if field.maximum is not None else ''}"
        else:
            allowed = "text" if field.kind == "str" else "path"
        default = f"`{json.dumps(field.default)}`" if field.default != "" else "empty"
        meaning = field.label + (f". {field.help}" if field.help else "")
        lines.append(f"| `{field.key}` | {default} | {allowed} | {'yes' if field.lcd else ''} | "
                     f"{meaning.replace('|', '/')} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    path = os.path.join(ROOT, "docs", "CONFIGURATION.md")
    text = render()
    if "--check" in sys.argv:
        try:
            with open(path, encoding="utf-8") as handle:
                current = handle.read()
        except OSError:
            current = ""
        if current != text:
            print("docs/CONFIGURATION.md is out of date: python3 tools/gen-config-doc.py")
            return 1
        return 0
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
