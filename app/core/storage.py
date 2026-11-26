"""Small, safe file helpers: atomic JSON writes and tolerant reads.

A write goes to a temporary file in the same folder, is flushed to disk and
then renamed over the target, so a power cut leaves either the old or the new
file, never half of one. A file that cannot be parsed is kept as evidence
(``<name>.corrupt-<time>``) and the caller gets its default.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from typing import Any

log = logging.getLogger("mcode.storage")


def atomic_write_text(path: str, text: str, mode: int = 0o600) -> None:
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: str, data: Any, mode: int = 0o600) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n", mode)


def preserve_corrupt(path: str) -> str:
    """Move an unreadable file aside (kept as evidence); returns its new name."""
    target = f"{path}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        os.replace(path, target)
    except OSError as exc:
        log.warning("could not move %s aside: %s", path, exc)
        return ""
    return target


def read_json(path: str, default: Any, expect: type = dict) -> Any:
    """The file's JSON value, or ``default`` if it is missing or malformed."""
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        kept = preserve_corrupt(path)
        log.warning("%s is unreadable (%s); kept as %s, using defaults", path, exc, kept or "-")
        return default
    if not isinstance(value, expect):
        kept = preserve_corrupt(path)
        log.warning("%s does not hold a %s; kept as %s, using defaults", path, expect.__name__,
                    kept or "-")
        return default
    return value


def append_jsonl(path: str, record: dict, max_bytes: int = 4 * 1024 * 1024) -> None:
    """Append one JSON line; the file is rotated once (``.1``) past ``max_bytes``."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    try:
        if os.path.getsize(path) > max_bytes:
            os.replace(path, path + ".1")
    except OSError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def read_jsonl(path: str, limit: int = 200) -> list:
    """The last ``limit`` records (malformed lines are skipped)."""
    lines: list = []
    for name in (path + ".1", path):
        try:
            with open(name, encoding="utf-8") as handle:
                lines.extend(handle.readlines())
        except OSError:
            continue
    records = []
    for line in lines[-limit:]:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records
