"""Project backups: compressed snapshots of the source, restorable at any time.

mCode takes one before every risky change (an AI turn in Assisted/Advanced
mode, applying AI changes, a restore, a rename) and on request. A backup
holds the source files only (``walk_sources``), so it stays small. Restore
first backs up the current state, extracts into a new folder next to the
project after checking every member (regular files and folders only, no
absolute paths, no ``..``), then swaps the folders; the replaced tree goes to
the project trash, never straight to deletion.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import tarfile
import time
from typing import List, Optional

from .files import walk_sources
from .model import META_FILE, ProjectError

MAX_RESTORE_BYTES = 512 * 1024 * 1024
_NAME = re.compile(r"^(\d{8}-\d{6})(?:-(\d+))?-([a-z0-9-]+)\.tar\.gz$")


def _folder(backups_dir: str, project_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", project_id or ""):
        raise ProjectError("Invalid project id.")
    return os.path.join(backups_dir, project_id)


def create(project_path: str, backups_dir: str, project_id: str, reason: str = "manual",
           keep: int = 10, note: str = "") -> str:
    reason = re.sub(r"[^a-z0-9-]+", "-", reason.lower()).strip("-") or "manual"
    folder = _folder(backups_dir, project_id)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"{stamp}-{reason}.tar.gz"
    serial = 1
    while os.path.exists(os.path.join(folder, name)):
        serial += 1
        name = f"{stamp}-{serial}-{reason}.tar.gz"
    path = os.path.join(folder, name)
    tmp = path + ".part"
    files = list(walk_sources(project_path))
    meta = os.path.join(project_path, META_FILE)
    with tarfile.open(tmp, "w:gz") as archive:
        info = {"project": os.path.basename(project_path), "reason": reason, "note": note,
                "time": time.time(), "files": len(files)}
        data = json.dumps(info).encode()
        member = tarfile.TarInfo(".mcode-backup.json")
        member.size = len(data)
        member.mtime = int(time.time())
        archive.addfile(member, io.BytesIO(data))
        for rel in files:
            archive.add(os.path.join(project_path, rel), arcname=rel, recursive=False)
        if os.path.exists(meta):
            archive.add(meta, arcname=META_FILE.replace(os.sep, "/"), recursive=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    prune(folder, keep)
    return path


def prune(folder: str, keep: int) -> None:
    entries = sorted(n for n in os.listdir(folder) if n.endswith(".tar.gz"))
    for name in entries[:-keep] if keep > 0 else []:
        try:
            os.unlink(os.path.join(folder, name))
        except OSError:
            pass


def list_backups(backups_dir: str, project_id: str) -> List[dict]:
    folder = _folder(backups_dir, project_id)
    if not os.path.isdir(folder):
        return []
    out = []
    for name in sorted(os.listdir(folder), reverse=True):
        match = _NAME.match(name)
        if not match:
            continue
        path = os.path.join(folder, name)
        out.append({"name": name, "reason": match.group(3),
                    "time": time.mktime(time.strptime(match.group(1), "%Y%m%d-%H%M%S")),
                    "size": os.path.getsize(path)})
    return out


def path_of(backups_dir: str, project_id: str, name: str) -> str:
    if not _NAME.match(name or ""):
        raise ProjectError("Unknown backup.")
    path = os.path.join(_folder(backups_dir, project_id), name)
    if not os.path.exists(path):
        raise ProjectError("That backup no longer exists.")
    return path


def safe_extract(archive_path: str, dest: str) -> int:
    """Extract only regular files and folders that stay inside ``dest``."""
    total = 0
    count = 0
    dest_real = os.path.realpath(dest)
    with tarfile.open(archive_path, "r:*") as archive:
        for member in archive.getmembers():
            name = member.name
            if name == ".mcode-backup.json":
                continue
            if os.path.isabs(name) or ".." in name.replace("\\", "/").split("/"):
                raise ProjectError(f"Unsafe path in backup: {name}")
            if not (member.isfile() or member.isdir()):
                raise ProjectError(f"Backup contains a link or device: {name}")
            target = os.path.realpath(os.path.join(dest_real, name))
            if target != dest_real and not target.startswith(dest_real + os.sep):
                raise ProjectError(f"Unsafe path in backup: {name}")
            total += member.size
            if total > MAX_RESTORE_BYTES:
                raise ProjectError("Backup is too large to restore.")
            if member.isdir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                continue
            with source, open(target, "wb") as out:
                shutil.copyfileobj(source, out)
            os.chmod(target, (member.mode & 0o777) | 0o600)
            count += 1
    return count


def restore(archive_path: str, project_path: str, trash_dir: str,
            keep_generated: bool = True) -> Optional[str]:
    """Replace the project's source with the backup. Returns where the old tree went."""
    parent = os.path.dirname(os.path.abspath(project_path))
    staging = os.path.join(parent, f".{os.path.basename(project_path)}.restore-{os.getpid()}")
    if os.path.exists(staging):
        shutil.rmtree(staging)
    os.makedirs(staging)
    try:
        safe_extract(archive_path, staging)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if keep_generated:
        # Build output and git history are not in backups: carry them over.
        for name in (".pio", "build", ".git", "managed_components", os.path.join(".mcode", "build"),
                     "sdkconfig"):
            src = os.path.join(project_path, name)
            if os.path.exists(src) and not os.path.exists(os.path.join(staging, name)):
                os.makedirs(os.path.dirname(os.path.join(staging, name)), exist_ok=True)
                shutil.move(src, os.path.join(staging, name))
    os.makedirs(trash_dir, exist_ok=True)
    old = os.path.join(trash_dir, f"{time.strftime('%Y%m%d-%H%M%S')}-before-restore-"
                                  f"{os.path.basename(project_path)}")
    os.replace(project_path, old) if _same_fs(project_path, trash_dir) else \
        shutil.move(project_path, old)
    os.replace(staging, project_path)
    return old


def _same_fs(a: str, b: str) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False
