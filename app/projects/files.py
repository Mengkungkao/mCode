"""File access inside a project, with the project as a hard boundary.

Every path from an interface or the AI goes through :func:`resolve` first:
it must stay inside the project after following symlinks, so ``../x``,
absolute paths and links that point elsewhere are refused. The explorer,
the editor's open/save, the AI workspace copy and backups all walk the tree
with :func:`walk_sources`, which skips build output and tool state.
"""

from __future__ import annotations

import filecmp
import hashlib
import os
import shutil
import stat
import tempfile
import threading
from typing import Dict, Iterator, List, Optional

from .model import GENERATED_DIRS, ProjectError

MAX_EDIT_BYTES = 2 * 1024 * 1024        # the editor opens files up to 2 MB
TEXT_EXTENSIONS = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".ino", ".pde",
                   ".py", ".json", ".yaml", ".yml", ".xml", ".md", ".txt", ".ini", ".cfg",
                   ".cmake", ".csv", ".s", ".S", ".ld", ".mk", ".sh", ".toml", ".properties",
                   ".html", ".css", ".js", ".svg", ".conf", ".defaults", ""}
LANGUAGE_BY_EXT = {".c": "c", ".h": "cpp", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp",
                   ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp", ".ino": "arduino", ".pde": "arduino",
                   ".py": "python", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
                   ".xml": "xml", ".svg": "xml", ".html": "xml", ".md": "markdown",
                   ".ini": "ini", ".cfg": "ini", ".toml": "ini", ".sh": "shell", ".cmake": "cmake",
                   ".txt": "text", ".csv": "text"}


def language_of(path: str) -> str:
    name = os.path.basename(path)
    if name in ("CMakeLists.txt",):
        return "cmake"
    if name in ("Makefile", "makefile") or name.endswith(".mk"):
        return "makefile"
    if name.startswith("sdkconfig"):
        return "ini"
    return LANGUAGE_BY_EXT.get(os.path.splitext(name)[1].lower(), "text")


def resolve(root: str, relative: str, must_exist: bool = False) -> str:
    """The absolute path of ``relative`` inside ``root``; refuses anything outside."""
    if relative is None:
        raise ProjectError("No file given.")
    relative = str(relative).replace("\\", "/").strip()
    if "\0" in relative:
        raise ProjectError("Invalid file name.")
    if os.path.isabs(relative):
        raise ProjectError("Use a path inside the project.")
    root_real = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root_real, relative))
    if target != root_real and not target.startswith(root_real + os.sep):
        raise ProjectError("That path is outside the project.")
    if must_exist and not os.path.exists(target):
        raise ProjectError(f"{relative} does not exist.")
    return target


def relative_to(root: str, path: str) -> str:
    return os.path.relpath(os.path.realpath(path), os.path.realpath(root)).replace(os.sep, "/")


def _skipped(rel_dir: str) -> bool:
    rel_dir = rel_dir.replace(os.sep, "/")
    if rel_dir in GENERATED_DIRS:
        return True
    return os.path.basename(rel_dir) in {".pio", "__pycache__", ".git", ".vscode", ".idea"}


def walk_sources(root: str, include_hidden_meta: bool = False) -> Iterator[str]:
    """Relative paths of regular files that belong to the source (no build output)."""
    root = os.path.realpath(root)
    for folder, dirs, names in os.walk(root):
        rel_folder = os.path.relpath(folder, root)
        rel_folder = "" if rel_folder == "." else rel_folder
        keep = []
        for name in sorted(dirs):
            rel = os.path.join(rel_folder, name) if rel_folder else name
            if _skipped(rel) or os.path.islink(os.path.join(folder, name)):
                continue
            if rel.replace(os.sep, "/") == ".mcode" and not include_hidden_meta:
                continue
            keep.append(name)
        dirs[:] = keep
        for name in sorted(names):
            full = os.path.join(folder, name)
            try:
                mode = os.lstat(full).st_mode
            except OSError:
                continue
            if not stat.S_ISREG(mode):
                continue
            yield (os.path.join(rel_folder, name) if rel_folder else name).replace(os.sep, "/")


def tree(root: str, limit: int = 3000) -> List[dict]:
    """The explorer's view: [{"path", "dir", "size"}], folders first."""
    entries: Dict[str, dict] = {}
    count = 0
    for rel in walk_sources(root):
        parts = rel.split("/")
        for depth in range(1, len(parts)):
            folder = "/".join(parts[:depth])
            entries.setdefault(folder, {"path": folder, "dir": True, "size": 0})
        try:
            size = os.path.getsize(os.path.join(root, rel))
        except OSError:
            size = 0
        entries[rel] = {"path": rel, "dir": False, "size": size, "language": language_of(rel)}
        count += 1
        if count >= limit:
            break
    return sorted(entries.values(), key=lambda e: e["path"].lower())


def is_text_file(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            head = handle.read(8192)
    except OSError:
        return False
    if b"\0" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        return exc.start > len(head) - 4     # only cut in the middle of a character
    return True


def read_text(root: str, relative: str) -> dict:
    path = resolve(root, relative, must_exist=True)
    if os.path.isdir(path):
        raise ProjectError(f"{relative} is a folder.")
    size = os.path.getsize(path)
    if size > MAX_EDIT_BYTES:
        raise ProjectError(f"{relative} is too large to edit here ({size // 1024} KB).")
    if not is_text_file(path):
        raise ProjectError(f"{relative} is not a text file.")
    with open(path, encoding="utf-8", errors="replace", newline="") as handle:
        text = handle.read()
    return {"path": relative_to(root, path), "text": text, "language": language_of(path),
            "size": size, "mtime": os.path.getmtime(path), "sha": digest(text.encode("utf-8"))}


_WRITE_LOCK = threading.Lock()


def write_text(root: str, relative: str, text: str, expect_sha: Optional[str] = None,
               create: bool = True) -> dict:
    """Save a file; with ``expect_sha``, refuse if it changed on disk since it was read.

    The check and the write happen under one lock and through a unique temporary file,
    so two saves of the same file (autosave and Ctrl+S) never interleave."""
    path = resolve(root, relative)
    if os.path.isdir(path):
        raise ProjectError(f"{relative} is a folder.")
    data = text.encode("utf-8")
    if len(data) > MAX_EDIT_BYTES:
        raise ProjectError("The file is too large.")
    with _WRITE_LOCK:
        exists = os.path.exists(path)
        if not exists and not create:
            raise ProjectError(f"{relative} does not exist.")
        if exists and expect_sha:
            with open(path, "rb") as handle:
                current = digest(handle.read())
            if current != expect_sha and current != digest(data):
                raise ProjectError(f"{relative} was changed on disk (by a build, the AI or another "
                                   "editor). Reload it before saving.")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = stat.S_IMODE(os.stat(path).st_mode) if exists else 0o644
        fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", suffix=".mcode-save",
                                   dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
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
    return {"path": relative_to(root, path), "sha": digest(data), "mtime": os.path.getmtime(path)}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def make_folder(root: str, relative: str) -> str:
    path = resolve(root, relative)
    os.makedirs(path, exist_ok=True)
    return relative_to(root, path)


def move(root: str, source: str, target: str) -> str:
    src = resolve(root, source, must_exist=True)
    dst = resolve(root, target)
    if os.path.exists(dst):
        raise ProjectError(f"{target} already exists.")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.replace(src, dst)
    return relative_to(root, dst)


def remove(root: str, relative: str, trash_dir: str) -> str:
    """Move a file or folder into the trash (never deleted outright)."""
    path = resolve(root, relative, must_exist=True)
    if os.path.realpath(path) == os.path.realpath(root):
        raise ProjectError("Delete the project from My Projects instead.")
    os.makedirs(trash_dir, exist_ok=True)
    import time
    target = os.path.join(trash_dir, f"{time.strftime('%Y%m%d-%H%M%S')}-{os.path.basename(path)}")
    shutil.move(path, target)
    return target


def copy_sources(src: str, dst: str) -> int:
    """Make ``dst`` hold exactly the source files of ``src`` (an AI workspace sync)."""
    wanted = set(walk_sources(src, include_hidden_meta=False))
    os.makedirs(dst, exist_ok=True)
    for rel in walk_sources(dst, include_hidden_meta=False):
        if rel not in wanted:
            os.unlink(os.path.join(dst, rel))
    count = 0
    for rel in wanted:
        a, b = os.path.join(src, rel), os.path.join(dst, rel)
        if os.path.exists(b) and filecmp.cmp(a, b, shallow=False):
            continue
        os.makedirs(os.path.dirname(b), exist_ok=True)
        shutil.copy2(a, b)
        count += 1
    # prune now-empty folders (not build output)
    for folder, dirs, names in os.walk(dst, topdown=False):
        rel = os.path.relpath(folder, dst)
        if rel != "." and not names and not dirs and not _skipped(rel):
            try:
                os.rmdir(folder)
            except OSError:
                pass
    return count
