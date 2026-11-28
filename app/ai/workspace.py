"""Where Claude works, and what it changed.

* **Ask before changes**: Claude edits a *staging copy* of the project's
  source (``<data>/workspaces/<project id>/stage``). After the turn mCode
  compares the copy with the project and shows the changes; nothing reaches
  the project until the user approves (all files or some of them). Before
  applying, mCode backs the project up.
* **Assisted / Advanced**: Claude edits the project itself. Before the turn
  mCode copies the source to ``<data>/workspaces/<project id>/before`` (and
  takes a backup); after it, the diff against that copy is the review, and
  *Undo* puts the old files back.

In every mode some changes are held for an explicit decision because they
can stop a board from booting or pull in new code: partition tables,
bootloader/flash/fuse settings (sdkconfig, partitions.csv, platformio.ini
``board_build.*``/``board_fuses.*``/``upload_flags``, linker scripts) and
dependency lists (platformio.ini ``lib_deps``, ``idf_component.yml``,
``sketch.yaml``), git attributes (they can make git run programs) and, while
``ai.hold_build_scripts`` is on, build scripts that run on this device when
the project is built (``CMakeLists.txt``, ``*.cmake``, ``Makefile``, ``*.mk``,
Python and shell scripts such as PlatformIO's ``extra_scripts``). In
Assisted/Advanced mode those files are put back to their old content unless
the user keeps them, and mCode's build tools refuse to run for Claude while
such a change is waiting (:meth:`Workspace.pending_reason`).

``.git`` is not part of the source tree, but its settings can name programs:
in Assisted/Advanced mode ``.git/config``, ``.git/info`` and ``.git/hooks``
are copied before the turn and put back if Claude changed them
(:meth:`Workspace.restore_git`).
"""

from __future__ import annotations

import difflib
import fnmatch
import os
import re
import shutil
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from ..projects import files as pfiles

MAX_DIFF_CHARS = 120000

RISKY_FILES = [
    ("partitions*.csv", "partition table"), ("*partition*.csv", "partition table"),
    ("sdkconfig", "ESP-IDF configuration (flash, bootloader, partitions)"),
    ("sdkconfig.*", "ESP-IDF configuration (flash, bootloader, partitions)"),
    ("*bootloader*", "bootloader"), ("*.ld", "linker script (memory layout)"),
    ("boards.local.txt", "board definition"), ("boards.txt", "board definition"),
]
BUILD_SCRIPTS = ["CMakeLists.txt", "*.cmake", "Makefile", "makefile", "GNUmakefile", "*.mk",
                 "*.py", "*.sh"]
BUILD_SCRIPT_REASON = "build script (runs on this device when the project is built)"
GIT_FILES = [(".gitattributes", "git attributes (can make git run programs)"),
             (".gitmodules", "git submodules (fetch code from elsewhere)")]
GIT_STATE = ("config", "info", "hooks")         # inside .git: snapshotted, put back
DEPENDENCY_FILES = [("idf_component.yml", "ESP-IDF components"),
                    ("dependencies.lock", "ESP-IDF component lock"),
                    ("library.json", "library manifest"), ("sketch.yaml", "Arduino profile")]
RISKY_INI = re.compile(r"^\s*(board_build\.(partitions|flash_mode|f_flash|flash_size|bootloader|"
                       r"arduino\.memory_type)|board_fuses\.|board_bootloader\.|upload_flags|"
                       r"board_upload\.|extra_scripts)", re.I)
DEPS_INI = re.compile(r"^\s*(lib_deps|platform_packages|platform\s*=|lib_extra_dirs)", re.I)


@dataclass
class FileChange:
    path: str
    status: str                      # added | modified | deleted
    old: Optional[str] = None
    new: Optional[str] = None
    binary: bool = False
    risky: List[str] = field(default_factory=list)       # reasons that need a decision
    added_lines: int = 0
    removed_lines: int = 0
    diff: str = ""

    def to_dict(self, with_text: bool = False) -> dict:
        data = {"path": self.path, "status": self.status, "binary": self.binary,
                "risky": self.risky, "added": self.added_lines, "removed": self.removed_lines,
                "diff": self.diff}
        if with_text:
            data.update(old=self.old, new=self.new)
        return data


def _read(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def _text(data: Optional[bytes]) -> Optional[str]:
    if data is None or b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def classify(path: str, old: Optional[str], new: Optional[str],
             build_scripts: bool = True) -> List[str]:
    name = os.path.basename(path)
    reasons = []
    for pattern, reason in RISKY_FILES + GIT_FILES:
        if fnmatch.fnmatch(name, pattern) and reason not in reasons:
            reasons.append(reason)
    if build_scripts and any(fnmatch.fnmatch(name, pattern) for pattern in BUILD_SCRIPTS):
        reasons.append(BUILD_SCRIPT_REASON)
    for pattern, reason in DEPENDENCY_FILES:
        if fnmatch.fnmatch(name, pattern):
            reasons.append(f"dependencies ({reason})")
    if name == "platformio.ini":
        old_lines = set((old or "").splitlines())
        new_lines = set((new or "").splitlines())
        changed = old_lines.symmetric_difference(new_lines)
        if any(RISKY_INI.match(line) for line in changed):
            reasons.append("flash, bootloader or fuse settings in platformio.ini")
        if any(DEPS_INI.match(line) for line in changed) or _section_changed(old, new, "lib_deps"):
            reasons.append("dependencies (platformio.ini lib_deps)")
    return reasons


def _section_changed(old: Optional[str], new: Optional[str], key: str) -> bool:
    def deps(text):
        out, inside = [], False
        for line in (text or "").splitlines():
            if re.match(rf"^\s*{key}\s*=", line):
                inside = True
                out.append(line.split("=", 1)[1].strip())
                continue
            if inside and line.startswith((" ", "\t")) and line.strip():
                out.append(line.strip())
                continue
            inside = False
        return out
    return deps(old) != deps(new)


def compare(before_root: str, after_root: str, paths: Optional[Iterable[str]] = None,
            build_scripts: bool = True) -> List[FileChange]:
    """Source files that differ between two trees."""
    before = set(pfiles.walk_sources(before_root)) if os.path.isdir(before_root) else set()
    after = set(pfiles.walk_sources(after_root)) if os.path.isdir(after_root) else set()
    names = sorted(before | after) if paths is None else sorted(set(paths))
    changes = []
    for rel in names:
        a = _read(os.path.join(before_root, rel)) if rel in before else None
        b = _read(os.path.join(after_root, rel)) if rel in after else None
        if a == b:
            continue
        status = "added" if a is None else "deleted" if b is None else "modified"
        old_text, new_text = _text(a), _text(b)
        change = FileChange(rel, status, old_text, new_text,
                            binary=(a is not None and old_text is None) or
                            (b is not None and new_text is None))
        if not change.binary:
            old_lines = (old_text or "").splitlines(keepends=True)
            new_lines = (new_text or "").splitlines(keepends=True)
            diff = list(difflib.unified_diff(old_lines, new_lines, f"a/{rel}", f"b/{rel}", n=3))
            change.added_lines = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
            change.removed_lines = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
            text = "".join(l if l.endswith("\n") else l + "\n" for l in diff)
            change.diff = text[:MAX_DIFF_CHARS] + ("\n… (diff truncated)\n" if len(text) >
                                                   MAX_DIFF_CHARS else "")
        change.risky = classify(rel, old_text, new_text, build_scripts)
        changes.append(change)
    return changes


def copy_file(src_root: str, dst_root: str, rel: str) -> None:
    """Make ``dst_root/rel`` equal to ``src_root/rel`` (deleting it if src has none)."""
    src = pfiles.resolve(src_root, rel)
    dst = pfiles.resolve(dst_root, rel)
    if os.path.exists(src):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".mcode-apply"
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    elif os.path.exists(dst):
        os.unlink(dst)


class Workspace:
    """The staging copy and the before-turn snapshot of one project."""

    def __init__(self, root: str, project_path: str, hold_build_scripts: bool = True):
        self.root = root
        self.project_path = project_path
        self.hold_build_scripts = hold_build_scripts
        self.stage = os.path.join(root, "stage")
        self.before = os.path.join(root, "before")
        self.before_git = os.path.join(root, "before-git")

    def prepare_stage(self) -> str:
        """Reset the staging copy to the project's current source."""
        pfiles.copy_sources(self.project_path, self.stage)
        return self.stage

    def snapshot(self) -> str:
        pfiles.copy_sources(self.project_path, self.before)
        self.snapshot_git()
        return self.before

    def staged_changes(self) -> List[FileChange]:
        return compare(self.project_path, self.stage, build_scripts=self.hold_build_scripts)

    def applied_changes(self) -> List[FileChange]:
        return compare(self.before, self.project_path, build_scripts=self.hold_build_scripts)

    def pending_reason(self) -> str:
        """Why a build must wait for the user, or "" (in-place modes, during the turn)."""
        held = sorted({c.path for c in self.applied_changes() if c.risky})
        reasons = []
        if held:
            reasons.append("these changes need the user's approval first: " + ", ".join(held[:8])
                           + (" …" if len(held) > 8 else ""))
        if self.git_changed():
            reasons.append("the project's git settings (.git) were changed")
        if not reasons:
            return ""
        return "; ".join(reasons) + ". Finish your answer; the user reviews the changes in " \
                                    "mCode and builds afterwards."

    # ------------------------------------------------------------ .git state
    def _git_files(self, root: str) -> Dict[str, bytes]:
        out: Dict[str, bytes] = {}
        for name in GIT_STATE:
            base = os.path.join(root, name)
            if os.path.isfile(base) and not os.path.islink(base):
                out[name] = _read(base) or b""
            elif os.path.isdir(base) and not os.path.islink(base):
                for folder, _dirs, files in os.walk(base):
                    for item in files:
                        full = os.path.join(folder, item)
                        if os.path.islink(full) or not os.path.isfile(full):
                            continue
                        rel = os.path.relpath(full, root)
                        mode = os.stat(full).st_mode & 0o777
                        out[rel] = (_read(full) or b"") + f"\0mode={mode:o}".encode()
        return out

    def snapshot_git(self) -> None:
        shutil.rmtree(self.before_git, ignore_errors=True)
        git_dir = os.path.join(self.project_path, ".git")
        if not os.path.isdir(git_dir) or os.path.islink(git_dir):
            return
        os.makedirs(self.before_git, mode=0o700)
        for name in GIT_STATE:
            source = os.path.join(git_dir, name)
            target = os.path.join(self.before_git, name)
            if os.path.islink(source):
                continue
            if os.path.isdir(source):
                shutil.copytree(source, target, symlinks=True)
            elif os.path.isfile(source):
                shutil.copy2(source, target)
        with open(os.path.join(self.before_git, ".taken"), "w") as handle:
            handle.write("1\n")

    def git_changed(self) -> bool:
        git_dir = os.path.join(self.project_path, ".git")
        if not os.path.exists(os.path.join(self.before_git, ".taken")):
            return False            # no repository before the turn: nothing to compare
        return self._git_files(git_dir) != self._git_files(self.before_git)

    def restore_git(self) -> bool:
        """Put ``.git/config``, ``info`` and ``hooks`` back; True if they had changed."""
        if not self.git_changed():
            return False
        git_dir = os.path.join(self.project_path, ".git")
        for name in GIT_STATE:
            current = os.path.join(git_dir, name)
            saved = os.path.join(self.before_git, name)
            if os.path.islink(current) or os.path.isfile(current):
                os.unlink(current)
            elif os.path.isdir(current):
                shutil.rmtree(current)
            if os.path.isdir(saved):
                shutil.copytree(saved, current, symlinks=True)
            elif os.path.isfile(saved):
                shutil.copy2(saved, current)
        return True

    def apply(self, paths: Iterable[str]) -> List[str]:
        """Copy approved staged files into the project."""
        done = []
        for rel in paths:
            copy_file(self.stage, self.project_path, rel)
            done.append(rel)
        return done

    def revert(self, paths: Iterable[str]) -> List[str]:
        """Put files back to their before-turn content (Undo)."""
        done = []
        for rel in paths:
            copy_file(self.before, self.project_path, rel)
            done.append(rel)
        return done

    def discard_stage(self) -> None:
        shutil.rmtree(self.stage, ignore_errors=True)
