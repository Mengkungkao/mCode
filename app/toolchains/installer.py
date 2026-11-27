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

* **ESP-IDF**: Espressif's own steps (Get Started, Linux): a shallow
  ``git clone --recursive`` of the newest stable release tag from
  github.com/espressif/esp-idf into ``~/esp/v<version>/esp-idf``, then its
  ``install.sh`` (compilers and its Python environment in ``~/.espressif``)
  and ``idf_tools.py install cmake ninja``. Those are the standard ESP-IDF
  places, outside mCode's data folder on purpose: mFruit OS copies the data
  folder before every update, and ESP-IDF is several GB. A folder that is
  not mCode's own half-finished download is never deleted or overwritten.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
from typing import Callable, Optional

from ..core.jobs import Job, JobFailed

log = logging.getLogger("mcode.installer")

ARDUINO_RELEASES = "https://github.com/arduino/arduino-cli/releases"
IDF_REPOSITORY = "https://github.com/espressif/esp-idf.git"
IDF_MIN_FREE = 7 * 1024 ** 3          # 6 GB measured (v6.1.1, all targets, arm64) + headroom
IDF_PARTIAL = ".mcode-partial"
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


# ================================================================ ESP-IDF

def _version_key(tag: str):
    match = re.fullmatch(r"v(\d+)\.(\d+)(?:\.(\d+))?", tag)
    return tuple(int(n or 0) for n in match.groups()) if match else None


def pick_idf_release(tags, python: tuple) -> str:
    """The newest stable ESP-IDF tag this Python can run: 6.x needs Python 3.10
    (tools/detect_python.sh), 5.x runs on 3.9. Pre-releases (-beta, -rc,
    -dev) are never chosen."""
    usable = []
    for tag in tags:
        key = _version_key(tag)
        if key is None:
            continue
        if key[0] >= 6 and tuple(python[:2]) < (3, 10):
            continue
        usable.append((key, tag))
    if not usable:
        raise JobFailed("no ESP-IDF release runs on this Python "
                        f"({'.'.join(map(str, python[:3]))}); ESP-IDF needs Python 3.9 or newer")
    return max(usable)[1]


def _capture(job: Job, argv: list, what: str, env: Optional[dict] = None, cwd: str = "",
             timeout: float = 120, check: bool = True) -> str:
    """Run a short command and return its output (stdout and stderr)."""
    job.check_cancelled()
    try:
        result = subprocess.run(argv, cwd=cwd or None, env=dict(os.environ, **(env or {})),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise JobFailed(f"{what} took longer than {int(timeout)} s") from None
    except OSError as exc:
        raise JobFailed(f"cannot run {what}: {exc.strerror or exc}") from None
    if check and result.returncode != 0:
        tail = (result.stdout or "").strip().splitlines()[-3:]
        raise JobFailed(f"{what} failed (exit code {result.returncode})"
                        + (": " + " / ".join(tail) if tail else ""))
    return result.stdout or ""


def idf_release_tags(job: Job) -> list:
    """Release tags from Espressif's repository (git ls-remote: no API quota)."""
    out = _capture(job, ["git", "ls-remote", "--tags", "--refs", IDF_REPOSITORY],
                   "git ls-remote (ESP-IDF releases)", timeout=120)
    return [line.split("refs/tags/", 1)[1] for line in out.splitlines() if "refs/tags/" in line]


def idf_folder(home: str, tag: str) -> str:
    return os.path.join(home, "esp", tag, "esp-idf")


def clean_path_env() -> dict:
    """PATH without mCode's own Python environment, so ESP-IDF builds its
    environment from the system Python (as its guide does)."""
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep)
             if p and not os.path.isfile(os.path.join(p, "pyvenv.cfg"))
             and not os.path.isfile(os.path.join(os.path.dirname(p), "pyvenv.cfg"))]
    return {"PATH": os.pathsep.join(parts)}


def install_esp_idf(job: Job, home: str, tag: str = "", python: str = "python3") -> dict:
    """Install ESP-IDF the way Espressif's Linux guide does, as the user."""
    env = clean_path_env()
    job.set_phase("Checking this computer", 0.0)
    if not shutil.which("git", path=env["PATH"]):
        raise JobFailed("git is needed to download ESP-IDF: sudo apt install git")
    probe = _capture(job, [python, "-c", "import sys, venv, ensurepip; "
                                         "print('%d.%d.%d' % sys.version_info[:3])"],
                     "python3 check", env=env, timeout=60, check=False)
    last = probe.strip().splitlines()[-1] if probe.strip() else ""
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", last)
    version = tuple(int(n) for n in match.groups()) if match else ()
    if not version:
        raise JobFailed("ESP-IDF needs Python 3 with venv: sudo apt install python3-venv")
    os.makedirs(os.path.join(home, "esp"), exist_ok=True)
    free = shutil.disk_usage(os.path.join(home, "esp")).free
    if free < IDF_MIN_FREE:
        raise JobFailed(f"ESP-IDF needs about {IDF_MIN_FREE // 1024 ** 3} GB of free space; "
                        f"{free / 1024 ** 3:.1f} GB is free in {home}")
    if not tag:
        job.set_phase("Finding the newest ESP-IDF release", 0.02)
        tag = pick_idf_release(idf_release_tags(job), version)
    if _version_key(tag) is None:
        raise JobFailed(f"{tag!r} is not an ESP-IDF release tag")
    folder = idf_folder(home, tag)
    job.say(f"ESP-IDF {tag} into {folder} (Python {'.'.join(map(str, version))})")
    if os.path.isfile(os.path.join(folder, "tools", "idf.py")):
        job.say("the ESP-IDF source is already there: installing its tools only")
    elif os.path.exists(folder):
        raise JobFailed(f"{folder} exists but is not a complete ESP-IDF; mCode does not change "
                        "folders it did not make. Move it away, then try again.")
    else:
        partial = folder + IDF_PARTIAL
        if os.path.exists(partial):
            job.say(f"removing an unfinished download from an earlier attempt: {partial}")
            shutil.rmtree(partial)
        job.set_phase(f"Downloading ESP-IDF {tag} (git, about 1 GB)", 0.05)
        job.run(["git", "clone", "--progress", "--branch", tag, "--depth", "1", "--recursive",
                 "--shallow-submodules", "--jobs", "4", IDF_REPOSITORY, partial],
                env=env, timeout=4 * 3600, what="git clone (ESP-IDF)")
        if not os.path.isfile(os.path.join(partial, "tools", "idf.py")):
            raise JobFailed("the download finished but has no tools/idf.py")
        os.replace(partial, folder)
    job.set_phase("Installing compilers and Python packages (install.sh)", 0.5)
    job.run(["bash", os.path.join(folder, "install.sh"), "all"], cwd=folder,
            env=dict(env, IDF_PATH=folder), timeout=4 * 3600, what="ESP-IDF install.sh")
    job.set_phase("Installing CMake and Ninja (idf_tools.py)", 0.9)
    job.run([python, os.path.join(folder, "tools", "idf_tools.py"), "install", "cmake", "ninja"],
            cwd=folder, env=dict(env, IDF_PATH=folder), timeout=3600,
            what="idf_tools.py install cmake ninja")
    job.set_phase("Checking idf.py", 0.97)
    out = _capture(job, ["bash", "-c", '. "$IDF_PATH/export.sh" >/dev/null 2>&1 && '
                                       'idf.py --version'],
                   "idf.py --version", cwd=folder, env=dict(env, IDF_PATH=folder), timeout=600)
    job.say(out.strip().splitlines()[-1] if out.strip() else "idf.py answered")
    return {"version": tag.lstrip("v"), "path": folder}


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
        "esp-idf": {"title": "Install ESP-IDF",
                    "body": "Downloads the newest stable ESP-IDF from Espressif's GitHub and its "
                            "compilers and Python packages from Espressif, as Espressif's Linux "
                            "guide does, into ~/esp and ~/.espressif. Needs about 6 GB of disk "
                            "and takes 20 to 60 minutes or more on a Raspberry Pi. No sudo."},
        "claude": {"title": "Install Claude Code",
                   "body": "Runs Anthropic's official installer (claude.ai/install.sh). It "
                           "installs into ~/.local and needs about 200 MB. You then sign in with "
                           "your Claude account."},
    }.get(tool_id)
