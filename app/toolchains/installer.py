"""Install development tools for the user, from their official sources only.

Every installer runs as a job (progress, log, cancel), as the current user,
never with sudo, and only after the user confirmed it on screen:

* **Arduino CLI**: the release archive from github.com/arduino/arduino-cli,
  checked against the SHA-256 in that release's checksum file before
  anything is unpacked; the single ``arduino-cli`` binary goes to mCode's
  tools folder.
* **PlatformIO Core** and **esptool**: ``pip install`` from PyPI into a
  Python virtual environment in mCode's data folder (``tools/venv``), so the
  system Python is never changed.
* **Claude Code**: Anthropic's official installer script
  (https://claude.ai/install.sh), downloaded over HTTPS and run with bash; it
  installs into ``~/.local``. mCode never handles the login itself.
* **Arduino cores** (ESP32, AVR, ...): ``arduino-cli core install`` (see
  backends.ArduinoCliBackend.install_core).

ESP-IDF (2–3 GB) is not installed automatically: docs/TOOLCHAINS.md gives
the commands.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import platform
import re
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
from typing import Callable, Optional

from ..core.jobs import Job, JobFailed

log = logging.getLogger("mcode.installer")

ARDUINO_RELEASES = "https://github.com/arduino/arduino-cli/releases"
CLAUDE_INSTALLER = "https://claude.ai/install.sh"
USER_AGENT = "mCode-Studio-installer"
ARCHES = {"aarch64": "Linux_ARM64", "arm64": "Linux_ARM64", "armv7l": "Linux_ARMv7",
          "armv6l": "Linux_ARMv6", "x86_64": "Linux_64bit", "amd64": "Linux_64bit",
          "i686": "Linux_32bit", "i386": "Linux_32bit"}
MAX_DOWNLOAD = 200 * 1024 * 1024


def _open(url: str, timeout: float = 30):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=timeout)


def latest_arduino_cli(opener: Callable = _open) -> str:
    """The latest release tag, from the redirect of /releases/latest (no API quota)."""
    with opener(ARDUINO_RELEASES + "/latest") as response:
        final = response.geturl()
    match = re.search(r"/tag/v?(\d+\.\d+\.\d+)$", final)
    if not match:
        raise JobFailed(f"could not find the latest Arduino CLI release ({final})")
    return match.group(1)


def download(job: Job, url: str, opener: Callable = _open, limit: int = MAX_DOWNLOAD,
             label: str = "Downloading") -> bytes:
    job.say(f"downloading {url}")
    with opener(url, timeout=60) as response:
        total = int(response.headers.get("Content-Length") or 0)
        if total > limit:
            raise JobFailed(f"download too large ({total // 1048576} MB)")
        chunks = []
        received = 0
        while True:
            job.check_cancelled()
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            received += len(chunk)
            if received > limit:
                raise JobFailed("download too large")
            if total:
                job.set_phase(label, received / total)
    return b"".join(chunks)


def install_arduino_cli(job: Job, tools_bin: str, version: str = "",
                        opener: Callable = _open, machine: str = "") -> dict:
    arch = ARCHES.get((machine or platform.machine()).lower())
    if not arch:
        raise JobFailed(f"no Arduino CLI release for {machine or platform.machine()}")
    job.set_phase("Finding the latest release", 0.0)
    version = version or latest_arduino_cli(opener)
    name = f"arduino-cli_{version}_{arch}.tar.gz"
    base = f"{ARDUINO_RELEASES}/download/v{version}"
    checksums = ""
    # Recent releases name it "<version>-checksums.txt" (checked on v1.6.0); older ones
    # "arduino-cli_<version>_checksums.txt".
    for candidate in (f"{version}-checksums.txt", f"arduino-cli_{version}_checksums.txt"):
        try:
            checksums = download(job, f"{base}/{candidate}", opener, limit=1024 * 1024,
                                 label="Downloading checksums").decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
    if not checksums:
        raise JobFailed(f"Arduino CLI {version} has no checksum file: refusing to install")
    expected = ""
    for line in checksums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            expected = parts[0].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise JobFailed(f"{name} is not listed in the release checksums")
    archive = download(job, f"{base}/{name}", opener, label="Downloading Arduino CLI")
    actual = hashlib.sha256(archive).hexdigest()
    if actual != expected:
        raise JobFailed(f"checksum mismatch for {name}: refusing to install")
    job.say(f"sha256 verified: {actual}")
    job.set_phase("Installing", 0.95)
    os.makedirs(tools_bin, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        member = next((m for m in tar.getmembers() if m.name == "arduino-cli" and m.isfile()), None)
        if member is None:
            raise JobFailed("the archive has no arduino-cli program")
        source = tar.extractfile(member)
        target = os.path.join(tools_bin, "arduino-cli")
        tmp = target + ".new"
        with source, open(tmp, "wb") as out:
            shutil.copyfileobj(source, out)
        os.chmod(tmp, 0o755)
        os.replace(tmp, target)
    job.say(f"installed Arduino CLI {version} to {target}")
    return {"version": version, "path": target}


def ensure_venv(job: Job, venv: str, python: str = "") -> str:
    """mCode's tool environment; created once with the system Python."""
    py = os.path.join(venv, "bin", "python")
    if os.path.exists(py):
        return py
    base = python or shutil.which("python3") or "python3"
    job.set_phase("Creating the Python environment")
    code = job.run([base, "-m", "venv", venv], check=False, what="python3 -m venv")
    if code != 0 or not os.path.exists(py):
        shutil.rmtree(venv, ignore_errors=True)
        raise JobFailed("could not create a Python environment; install python3-venv "
                        "(sudo apt install python3-venv) and try again")
    job.run([py, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--upgrade",
             "pip"], check=False, what="pip upgrade")
    return py


def install_pip_package(job: Job, venv: str, package: str, python: str = "") -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+(==[0-9A-Za-z.\-]+)?", package):
        raise JobFailed("invalid package name")
    py = ensure_venv(job, venv, python)
    job.set_phase(f"Installing {package} from PyPI")
    job.run([py, "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", package],
            what=f"pip install {package}", timeout=1800)
    return {"venv": venv, "package": package}


def install_claude_code(job: Job, opener: Callable = _open) -> dict:
    script = download(job, CLAUDE_INSTALLER, opener, limit=1024 * 1024,
                      label="Downloading the official installer").decode("utf-8", "replace")
    if not script.startswith("#!"):
        raise JobFailed("the installer download does not look like a script; nothing was run")
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as handle:
        handle.write(script)
        path = handle.name
    try:
        job.set_phase("Running Anthropic's installer")
        job.run(["bash", path], what="the Claude Code installer", timeout=1800)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return {"installed": True}


def explain_install(tool_id: str) -> Optional[dict]:
    """What an install will do, shown before the user confirms."""
    return {
        "arduino-cli": {"title": "Install the Arduino CLI",
                        "body": "Downloads the official release from GitHub (about 30 MB), "
                                "checks its SHA-256 and installs it for mCode only."},
        "platformio": {"title": "Install PlatformIO",
                       "body": "Installs PlatformIO Core from PyPI into mCode's own Python "
                               "environment (about 60 MB; board tools download on first build)."},
        "esptool": {"title": "Install esptool",
                    "body": "Installs Espressif's esptool from PyPI into mCode's own Python "
                            "environment."},
        "claude": {"title": "Install Claude Code",
                   "body": "Runs Anthropic's official installer (claude.ai/install.sh). It "
                           "installs into ~/.local and needs about 200 MB. You then sign in with "
                           "your Claude account."},
    }.get(tool_id)
