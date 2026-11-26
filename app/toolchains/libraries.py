"""Libraries and components, through each tool's own trusted source.

* Arduino CLI: the Arduino Library Manager index (``arduino-cli lib
  search/list/install/uninstall/upgrade/deps``).
* PlatformIO: the PlatformIO Registry (search through its public API, the
  same one ``pio`` uses; installs with ``pio pkg install --library``, which
  records the library in ``platformio.ini``).
* ESP-IDF: the Espressif Component Registry (``idf.py add-dependency``,
  recorded in ``main/idf_component.yml``).

Installing, removing and updating are jobs that the user starts; the AI can
recommend a library but cannot install one. "Missing" compares the
``#include``s in the project with what the installed libraries and the
board's core provide; "conflicts" come from the build's "Multiple libraries
were found" notes.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import urllib.parse
import urllib.request
from typing import List, Optional

from ..core.jobs import Job, JobFailed, JobSpec
from ..projects.boards import ARDUINO_CLI, ESP_IDF, PLATFORMIO
from ..projects.files import walk_sources
from ..projects.model import Project, ProjectError
from . import backends, detect

log = logging.getLogger("mcode.libraries")
PIO_REGISTRY = "https://api.registry.platformio.org/v3/search"
_INCLUDE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.M)
_NAME_OK = re.compile(r"^[A-Za-z0-9 _.\-+/@^~<>=,()]{1,120}$")


def project_includes(project: Project) -> List[str]:
    """Headers the project's own sources include, minus its own headers."""
    own = set()
    found = []
    for rel in walk_sources(project.path):
        own.add(os.path.basename(rel))
        if not rel.endswith((".c", ".cpp", ".cc", ".h", ".hpp", ".ino")):
            continue
        try:
            with open(os.path.join(project.path, rel), encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        for header in _INCLUDE.findall(text):
            if header not in found:
                found.append(header)
    return [h for h in found if os.path.basename(h) not in own]


class LibraryService:
    def __init__(self, studio):
        self.studio = studio

    def _cli(self) -> str:
        tool = self.studio.tools.get(detect.ARDUINO_CLI)
        if not tool.found:
            raise ProjectError("The Arduino CLI is not installed (Settings > Development tools).")
        return tool.path

    def _pio(self) -> str:
        tool = self.studio.tools.get(detect.PLATFORMIO)
        if not tool.found:
            raise ProjectError("PlatformIO is not installed (Settings > Development tools).")
        return tool.path

    def _json(self, argv: List[str], timeout: float = 120) -> object:
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join(self.studio.tools.env_path() + [env.get("PATH", "")])
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                                    stdin=subprocess.DEVNULL, env=env)
        except subprocess.TimeoutExpired:
            raise ProjectError("The library index did not answer in time.") from None
        except OSError as exc:
            raise ProjectError(f"Cannot run {os.path.basename(argv[0])}: {exc.strerror}") from None
        if result.returncode != 0:
            raise ProjectError((result.stderr or result.stdout).strip()[-400:] or "command failed")
        try:
            return json.loads(result.stdout or "null")
        except ValueError:
            raise ProjectError("Unexpected answer from the library tool.") from None

    # ------------------------------------------------------------------ search
    def search(self, project: Project, query: str) -> List[dict]:
        query = (query or "").strip()
        if not query or not _NAME_OK.match(query):
            raise ProjectError("Type a library name to search for.")
        if project.toolchain == ARDUINO_CLI:
            data = self._json([self._cli(), "lib", "search", query, "--format", "json"])
            out = []
            for lib in (data or {}).get("libraries", [])[:40]:
                latest = lib.get("latest") or {}
                releases = lib.get("releases") or {}
                if not latest and releases:
                    latest = releases[sorted(releases)[-1]]
                out.append({"name": lib.get("name", ""), "version": latest.get("version", ""),
                            "description": latest.get("sentence", ""),
                            "author": latest.get("author", ""),
                            "includes": latest.get("provides_includes", []),
                            "source": "Arduino Library Manager"})
            return out
        if project.toolchain == PLATFORMIO:
            url = PIO_REGISTRY + "?" + urllib.parse.urlencode({"query": query, "page": 1})
            request = urllib.request.Request(url, headers={"User-Agent": "mCode-Studio"})
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    data = json.loads(response.read().decode("utf-8"))
            except (OSError, ValueError) as exc:
                raise ProjectError(f"The PlatformIO Registry is not reachable: {exc}") from None
            out = []
            for item in data.get("items", [])[:40]:
                if item.get("type") not in (None, "library"):
                    continue
                owner = (item.get("owner") or {}).get("username", "")
                out.append({"name": f"{owner}/{item.get('name')}" if owner else item.get("name"),
                            "version": (item.get("version") or {}).get("name", ""),
                            "description": item.get("description", ""), "author": owner,
                            "source": "PlatformIO Registry"})
            return out
        raise ProjectError("Search the Espressif Component Registry at components.espressif.com, "
                           "then add the component by name.")

    # ------------------------------------------------------------------ listing
    def installed(self, project: Project) -> List[dict]:
        if project.toolchain == ARDUINO_CLI:
            data = self._json([self._cli(), "lib", "list", "--format", "json"])
            items = (data or {}).get("installed_libraries", []) if isinstance(data, dict) else data
            out = []
            for entry in items or []:
                lib = entry.get("library", entry)
                out.append({"name": lib.get("name", ""), "version": lib.get("version", ""),
                            "description": lib.get("sentence", ""),
                            "includes": lib.get("provides_includes", []),
                            "location": lib.get("location", "")})
            return out
        if project.toolchain == PLATFORMIO:
            from ..projects.model import _pio_envs
            envs = [e for e in _pio_envs(os.path.join(project.path, "platformio.ini"))
                    if e.get("platform") != "native"]
            deps = (envs[0].get("lib_deps", "") if envs else "")
            return [{"name": d.strip(), "version": "", "description": "from platformio.ini"}
                    for d in re.split(r"[\n,]", deps) if d.strip()]
        if project.toolchain == ESP_IDF:
            path = os.path.join(project.path, "main", "idf_component.yml")
            out = []
            try:
                with open(path, encoding="utf-8") as handle:
                    in_deps = False
                    for line in handle:
                        if line.startswith("dependencies:"):
                            in_deps = True
                            continue
                        if in_deps and re.match(r"^\s{2}[\w./-]+:", line):
                            name, _, version = line.strip().partition(":")
                            out.append({"name": name.strip("'\""),
                                        "version": version.strip().strip("'\""),
                                        "description": "from idf_component.yml"})
            except OSError:
                pass
            return out
        return []

    def missing(self, project: Project) -> dict:
        """Headers no installed library or core provides (Arduino CLI projects)."""
        includes = project_includes(project)
        if project.toolchain != ARDUINO_CLI:
            return {"includes": includes, "missing": [], "checked": False}
        fqbn = project.fqbn()
        data = self._json([self._cli(), "lib", "list", "--all", "--fqbn", fqbn, "--format",
                           "json"])
        items = (data or {}).get("installed_libraries", []) if isinstance(data, dict) else data
        provided = set()
        for entry in items or []:
            lib = entry.get("library", entry)
            provided.update(lib.get("provides_includes", []) or [])
        core_headers = _core_headers(project)
        missing = [h for h in includes if h not in provided and os.path.basename(h) not in
                   core_headers and not h.startswith(("freertos/", "esp_", "driver/", "soc/",
                                                      "hal/", "sys/", "avr/", "util/"))
                   and h not in _STD_HEADERS]
        return {"includes": includes, "missing": missing, "checked": True}

    # -------------------------------------------------------------- changing
    def install(self, project: Project, name: str, version: str = "") -> Job:
        name = name.strip()
        if not _NAME_OK.match(name) or name.startswith("-"):
            raise ProjectError("Invalid library name.")
        spec = f"{name}@{version}" if version else name
        if project.toolchain == ARDUINO_CLI:
            argv = [self._cli(), "lib", "install", spec]
            record = True
        elif project.toolchain == PLATFORMIO:
            backend = backends.PlatformIOBackend(self.studio.tools, self.studio.settings)
            argv = [self._pio(), "pkg", "install", "-d", project.path, "-e", backend.env(project),
                    "--library", spec if not version else f"{name}@{version}"]
            record = False
        elif project.toolchain == ESP_IDF:
            idf = backends.EspIdfBackend(self.studio.tools, self.studio.settings)
            argv = idf._shell(idf._idf(), project,
                              [["idf.py", "-C", project.path, "add-dependency", spec]])
            record = False
        else:
            raise ProjectError("This project type has no library manager.")
        return self._job(project, f"Installing {name}", argv, "install-lib", name, record)

    def uninstall(self, project: Project, name: str) -> Job:
        if not _NAME_OK.match(name or "") or name.startswith("-"):
            raise ProjectError("Invalid library name.")
        if project.toolchain == ARDUINO_CLI:
            argv = [self._cli(), "lib", "uninstall", name]
        elif project.toolchain == PLATFORMIO:
            backend = backends.PlatformIOBackend(self.studio.tools, self.studio.settings)
            argv = [self._pio(), "pkg", "uninstall", "-d", project.path, "-e",
                    backend.env(project), "--library", name]
        else:
            raise ProjectError("Remove ESP-IDF components from main/idf_component.yml.")
        return self._job(project, f"Removing {name}", argv, "uninstall-lib", name, False,
                         remove=True)

    def update(self, project: Project, name: str = "") -> Job:
        if project.toolchain == ARDUINO_CLI:
            argv = [self._cli(), "lib", "upgrade"] + ([name] if name else [])
        elif project.toolchain == PLATFORMIO:
            argv = [self._pio(), "pkg", "update", "-d", project.path]
        else:
            raise ProjectError("Update ESP-IDF components with idf.py update-dependencies.")
        return self._job(project, f"Updating {name or 'libraries'}", argv, "update-lib", name,
                         False)

    def dependencies(self, project: Project, name: str) -> List[dict]:
        if project.toolchain != ARDUINO_CLI:
            raise ProjectError("Dependency details are shown for Arduino libraries.")
        data = self._json([self._cli(), "lib", "deps", name, "--format", "json"])
        out = []
        for dep in (data or {}).get("dependencies", []):
            out.append({"name": dep.get("name"), "required": dep.get("version_required", ""),
                        "installed": dep.get("version_installed", "")})
        return out

    def conflicts(self, project: Project) -> List[dict]:
        last = self.studio.builds.last(project.id) or {}
        return last.get("conflicts", [])

    def _job(self, project: Project, title: str, argv: List[str], kind: str, name: str,
             record: bool, remove: bool = False) -> Job:
        studio = self.studio

        def work(job: Job) -> dict:
            job.set_phase(title)
            job.run(argv, timeout=1800, what=title)
            if record:
                libs = [l for l in project.libraries if l.split("@")[0] != name]
                if not remove:
                    libs.append(name)
                studio.projects.update(project, libraries=libs)
            return {"library": name}

        def done(job: Job) -> None:
            studio.history.add("library", project_id=project.id, action=kind, name=name,
                               state=job.state)
            studio.events.publish("libraries", {"project_id": project.id, "state": job.state,
                                                "name": name})

        return studio.jobs.submit(JobSpec(kind=kind, title=title, func=work, lane="install",
                                          project_id=project.id, timeout=1900, on_done=done))


def _core_headers(project: Project) -> set:
    """Header names shipped inside the board's installed Arduino core."""
    board = project.board
    if not board or not board.core:
        return set()
    vendor, _, arch = board.core.partition(":")
    base = os.path.join(os.path.expanduser("~"), ".arduino15", "packages", vendor, "hardware",
                        arch)
    headers = set()
    if not os.path.isdir(base):
        return headers
    for version in os.listdir(base):
        for sub in ("cores", "libraries"):
            root = os.path.join(base, version, sub)
            for folder, _dirs, names in os.walk(root):
                for name in names:
                    if name.endswith((".h", ".hpp")):
                        headers.add(name)
    return headers


_STD_HEADERS = {"stdio.h", "stdlib.h", "string.h", "stdint.h", "stdbool.h", "math.h", "stddef.h",
                "limits.h", "ctype.h", "time.h", "assert.h", "errno.h", "float.h", "inttypes.h",
                "stdarg.h", "signal.h", "setjmp.h", "locale.h", "wchar.h", "algorithm", "array",
                "vector", "string", "map", "memory", "functional", "utility", "cstdint", "cstdio",
                "cstdlib", "cstring", "cmath", "unity.h", "Arduino.h"}
