"""The project manager: the one owner of creating, opening, renaming,
duplicating, deleting, importing and listing projects, of the recent list
and of the current project. The LCD, the desktop IDE and the AI all go
through it.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import time
import uuid
from typing import Callable, List, Optional

from ..core.state import StateStore
from ..paths import expand
from . import backups, boards, buildfiles, gitutil, model, templates
from .boards import ARDUINO_CLI, ESP_IDF, MAKE, PLATFORMIO
from .files import walk_sources
from .model import Project, ProjectError

log = logging.getLogger("mcode.projects")
RECENT_MAX = 12


class ProjectManager:
    def __init__(self, settings, state: StateStore, backups_dir: str,
                 publish: Callable[[str, object], None] = lambda t, d: None):
        self.settings = settings
        self.state = state
        self.backups_dir = backups_dir
        self.publish = publish
        self._lock = threading.RLock()
        self._cache: dict = {}

    # ----------------------------------------------------------- locations
    @property
    def root(self) -> str:
        return expand(self.settings.get("dev.projects_dir")) or expand("~/mcode-projects")

    @property
    def trash(self) -> str:
        return os.path.join(self.root, ".trash")

    def ensure_root(self) -> str:
        os.makedirs(self.root, exist_ok=True)
        return self.root

    # -------------------------------------------------------------- listing
    def list(self) -> List[Project]:
        """Projects in the project folder plus recent ones opened elsewhere."""
        found = {}
        root = self.root
        if os.path.isdir(root):
            for name in sorted(os.listdir(root)):
                path = os.path.join(root, name)
                if name.startswith(".") or not os.path.isdir(path):
                    continue
                try:
                    found[os.path.realpath(path)] = model.load(path)
                except ProjectError:
                    continue
        for path in self.state.get("recent", []):
            real = os.path.realpath(path)
            if real in found or not os.path.isdir(real):
                continue
            try:
                found[real] = model.load(real)
            except ProjectError:
                continue
        recent = [os.path.realpath(p) for p in self.state.get("recent", [])]
        order = {p: i for i, p in enumerate(recent)}
        return sorted(found.values(),
                      key=lambda p: (order.get(os.path.realpath(p.path), 999), -p.updated,
                                     p.name.lower()))

    def recent(self, limit: int = 5) -> List[Project]:
        out = []
        for path in self.state.get("recent", []):
            try:
                out.append(model.load(path))
            except ProjectError:
                continue
            if len(out) >= limit:
                break
        return out

    def search(self, query: str) -> List[Project]:
        words = (query or "").lower().split()
        if not words:
            return self.list()
        out = []
        for project in self.list():
            hay = " ".join([project.name, project.board_name, project.mcu, project.toolchain,
                            project.platform, os.path.basename(project.path)]).lower()
            if all(w in hay for w in words):
                out.append(project)
        return out

    def find(self, key: str) -> Project:
        """A project by id, path or folder name."""
        if not key:
            raise ProjectError("No project given.")
        if os.path.isdir(key):
            return model.load(key)
        candidate = os.path.join(self.root, key)
        if os.path.isdir(candidate):
            return model.load(candidate)
        for project in self.list():
            if project.id == key:
                return project
        raise ProjectError(f"No project called {key}.")

    # -------------------------------------------------------------- current
    def current(self) -> Optional[Project]:
        path = self.state.get("current")
        if not path:
            return None
        try:
            return model.load(path)
        except ProjectError:
            return None

    def set_current(self, project: Project) -> Project:
        if project.detected:
            model.save(project)
        recent = [p for p in self.state.get("recent", [])
                  if os.path.realpath(p) != os.path.realpath(project.path)]
        recent.insert(0, project.path)
        self.state.update({"current": project.path, "recent": recent[:RECENT_MAX]})
        self.publish("projects", {"current": project.summary()})
        return project

    def open(self, path: str) -> Project:
        project = model.load(expand(path))
        return self.set_current(project)

    # ------------------------------------------------------------- creating
    def options_for(self, board_id: str) -> List[dict]:
        board = boards.get(board_id)
        if board is None:
            return []
        out = []
        for toolchain in board.toolchains:
            for framework in board.frameworks(toolchain):
                out.append({"toolchain": toolchain, "framework": framework,
                            "label": _option_label(toolchain, framework)})
        return out

    def unique_name(self, base: str) -> str:
        names = {p.name.lower() for p in self.list()}
        slugs = set(os.listdir(self.root)) if os.path.isdir(self.root) else set()
        if base.lower() not in names and model.slugify(base) not in slugs:
            return base
        for n in range(2, 1000):
            candidate = f"{base} {n}"
            if candidate.lower() not in names and model.slugify(candidate) not in slugs:
                return candidate
        return f"{base} {uuid.uuid4().hex[:4]}"

    def suggest_name(self, board_id: str) -> str:
        board = boards.get(board_id)
        base = f"{board.name if board else 'My'} project".replace("Arduino ", "")
        return self.unique_name(base)

    def create(self, name: str, board_id: str, toolchain: str = "", framework: str = "",
               baud: int = 0, usb_cdc_on_boot: bool = False, upload_speed: int = 0,
               description: str = "", location: str = "", init_git: bool = True,
               on_step: Callable[[str], None] = lambda s: None) -> Project:
        name = model.valid_name(name)
        board = boards.get(board_id)
        if board is None:
            raise ProjectError("Choose a board from the list.")
        toolchain = toolchain or board.toolchains[0]
        if toolchain not in board.toolchains:
            raise ProjectError(f"{board.name} is not supported by "
                               f"{boards.TOOLCHAIN_NAMES.get(toolchain, toolchain)}.")
        frameworks = board.frameworks(toolchain)
        framework = framework or frameworks[0]
        if framework not in frameworks:
            raise ProjectError(f"{boards.TOOLCHAIN_NAMES[toolchain]} has no {framework} "
                               f"framework for {board.name}.")
        parent = expand(location) if location else self.ensure_root()
        os.makedirs(parent, exist_ok=True)
        slug = model.slugify(name)
        path = os.path.join(parent, slug)
        if os.path.exists(path):
            raise ProjectError(f"A folder called {slug} already exists. Choose another name.")
        language = "c" if framework == "espidf" or toolchain == MAKE else "cpp"
        project = Project(path=path, name=name, platform=board.platform, board_id=board.id,
                          toolchain=toolchain, framework=framework, language=language,
                          baud=baud or int(self.settings.get("hw.default_baud")),
                          usb_cdc_on_boot=usb_cdc_on_boot, upload_speed=upload_speed,
                          pio_env=board.pio_board if toolchain == PLATFORMIO else "",
                          description=description)
        on_step("Generating files")
        files = templates.files_for(project)
        tmp = os.path.join(parent, f".{slug}.creating")
        if os.path.exists(tmp):
            shutil.rmtree(tmp)
        try:
            for rel, content in files.items():
                target = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(content)
            project.path = tmp
            model.save(project)
            os.replace(tmp, path)
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        project.path = path
        if init_git and gitutil.git_path():
            on_step("Starting version history")
            try:
                gitutil.init(path)
            except Exception as exc:     # git trouble must not fail the project
                log.warning("git init failed for %s: %s", path, exc)
        log.info("project created: %s (%s, %s/%s) in %s", name, board.id, toolchain, framework,
                 path)
        self.set_current(project)
        self.publish("projects", {"created": project.summary()})
        return project

    # ------------------------------------------------------------- changing
    def update(self, project: Project, **fields) -> Project:
        allowed = {"board_id", "toolchain", "framework", "port", "baud", "upload_speed",
                   "usb_cdc_on_boot", "libraries", "description", "board_options", "pio_env"}
        unknown = set(fields) - allowed
        if unknown:
            raise ProjectError(f"Cannot change {', '.join(sorted(unknown))}.")
        if "board_id" in fields:
            board = boards.get(fields["board_id"])
            if board is None:
                raise ProjectError("Unknown board.")
            toolchain = fields.get("toolchain", project.toolchain)
            if toolchain not in board.toolchains:
                raise ProjectError(f"{board.name} is not supported by "
                                   f"{boards.TOOLCHAIN_NAMES.get(toolchain, toolchain)}.")
            project.platform = board.platform
            if fields["board_id"] != project.board_id and "board_options" not in fields:
                fields["board_options"] = {}      # each board has its own menus
        if "upload_speed" in fields:
            fields["upload_speed"] = int(fields["upload_speed"] or 0)
            if fields["upload_speed"] < 0:
                raise ProjectError("Upload speed must be a positive number.")
        if "usb_cdc_on_boot" in fields:
            fields["usb_cdc_on_boot"] = bool(fields["usb_cdc_on_boot"])
        if "board_options" in fields:
            options = fields["board_options"] or {}
            if not isinstance(options, dict) or not all(
                    isinstance(k, str) and isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_]+", k)
                    and re.fullmatch(r"[A-Za-z0-9_.\-]+", v) for k, v in options.items()):
                raise ProjectError("Board options must be names and values from the board's "
                                   "menus.")
            fields["board_options"] = dict(options)
        for key, value in fields.items():
            setattr(project, key, value)
        model.save(project)
        if set(fields) & {"board_id", "upload_speed", "usb_cdc_on_boot", "board_options"}:
            try:
                for name in buildfiles.sync(project):
                    log.info("%s: updated %s for its board settings", project.name, name)
            except OSError as exc:
                raise ProjectError(f"Saved, but {exc.filename or 'a build file'} could not be "
                                   f"updated: {exc.strerror or exc}") from None
        self.publish("projects", {"updated": project.summary()})
        return project

    def rename(self, project: Project, new_name: str) -> Project:
        new_name = model.valid_name(new_name)
        old_path = project.path
        new_slug = model.slugify(new_name)
        new_path = os.path.join(os.path.dirname(old_path), new_slug)
        if new_path != old_path and os.path.exists(new_path):
            raise ProjectError(f"A folder called {new_slug} already exists.")
        backups.create(old_path, self.backups_dir, project.id, "before-rename",
                       keep=self.settings.get("storage.backups_keep"))
        if new_path != old_path:
            os.replace(old_path, new_path)
            project.path = new_path
            if project.toolchain == ARDUINO_CLI:
                old_ino = os.path.join(new_path, os.path.basename(old_path) + ".ino")
                if os.path.exists(old_ino):
                    os.replace(old_ino, os.path.join(new_path, new_slug + ".ino"))
        project.name = new_name
        model.save(project)
        recent = [new_path if os.path.realpath(p) == os.path.realpath(old_path) else p
                  for p in self.state.get("recent", [])]
        values = {"recent": recent}
        if os.path.realpath(self.state.get("current") or "") == os.path.realpath(old_path):
            values["current"] = new_path
        self.state.update(values)
        self.publish("projects", {"renamed": project.summary()})
        return project

    def duplicate(self, project: Project, new_name: str = "") -> Project:
        new_name = model.valid_name(new_name or self.unique_name(f"{project.name} copy"))
        slug = model.slugify(new_name)
        target = os.path.join(os.path.dirname(project.path), slug)
        if os.path.exists(target):
            raise ProjectError(f"A folder called {slug} already exists.")
        tmp = target + ".copying"
        try:
            for rel in walk_sources(project.path):
                dst = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(os.path.join(project.path, rel), dst)
            if project.toolchain == ARDUINO_CLI:
                old_ino = os.path.join(tmp, project.slug + ".ino")
                if os.path.exists(old_ino):
                    os.replace(old_ino, os.path.join(tmp, slug + ".ino"))
            copy = model.Project(**{**project.to_dict(), "path": tmp, "name": new_name,
                                    "id": uuid.uuid4().hex[:12], "created": time.time()})
            model.save(copy)
            os.replace(tmp, target)
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        copy.path = target
        self.publish("projects", {"created": copy.summary()})
        return copy

    def delete(self, project: Project) -> str:
        """Move the project to the project trash (``<project folder>/.trash``)."""
        os.makedirs(self.trash, exist_ok=True)
        target = os.path.join(self.trash, f"{time.strftime('%Y%m%d-%H%M%S')}-{project.slug}")
        shutil.move(project.path, target)
        recent = [p for p in self.state.get("recent", [])
                  if os.path.realpath(p) != os.path.realpath(project.path)]
        values = {"recent": recent}
        if os.path.realpath(self.state.get("current") or "") == os.path.realpath(project.path):
            values["current"] = ""
        self.state.update(values)
        log.info("project %s moved to %s", project.name, target)
        self.publish("projects", {"deleted": project.id})
        return target

    def import_folder(self, source: str, copy: bool = True) -> Project:
        source = expand(source)
        if not os.path.isdir(source):
            raise ProjectError(f"No folder at {source}.")
        project = model.load(source)          # refuses folders that are not projects
        if not copy:
            return self.set_current(project)
        slug = model.slugify(project.name if not project.detected else os.path.basename(source))
        target = os.path.join(self.ensure_root(), slug)
        if os.path.exists(target):
            raise ProjectError(f"A project folder called {slug} already exists.")
        shutil.copytree(source, target, symlinks=True,
                        ignore=shutil.ignore_patterns(".pio", "build", "__pycache__"))
        imported = model.load(target)
        imported.id = uuid.uuid4().hex[:12]
        model.save(imported)
        return self.set_current(imported)

    # --------------------------------------------------------------- backups
    def backup(self, project: Project, reason: str = "manual", note: str = "") -> str:
        return backups.create(project.path, self.backups_dir, project.id, reason,
                              keep=self.settings.get("storage.backups_keep"), note=note)

    def backups(self, project: Project) -> List[dict]:
        return backups.list_backups(self.backups_dir, project.id)

    def restore(self, project: Project, name: str) -> str:
        archive = backups.path_of(self.backups_dir, project.id, name)
        self.backup(project, "before-restore")
        old = backups.restore(archive, project.path, self.trash)
        self.publish("projects", {"restored": project.summary(), "backup": name})
        return old or ""


def _option_label(toolchain: str, framework: str) -> str:
    if toolchain == PLATFORMIO:
        return "PlatformIO (Arduino)" if framework == "arduino" else "PlatformIO (ESP-IDF)"
    if toolchain == ARDUINO_CLI:
        return "Arduino"
    if toolchain == ESP_IDF:
        return "ESP-IDF"
    return boards.TOOLCHAIN_NAMES.get(toolchain, toolchain)
