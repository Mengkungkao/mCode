"""Git, where it is installed: init on creation, status, commit, log, clone.

Commands run with a timeout, without a terminal and without prompting for
credentials (``GIT_TERMINAL_PROMPT=0``): a private repository that needs a
password fails with a clear message instead of hanging.

A project's own ``.git/config`` is untrusted input (a zip import, a folder
from elsewhere, or a file Claude edited): git runs programs named there
(``core.fsmonitor`` on every ``status``, filter drivers when it compares
files, external diff and textconv drivers, ``gpg.program`` when signing).
Opening a project must never run them, so every command mCode runs
switches those off (:func:`_safety_args`); commits made for the AI also run
without hooks. The user's own global configuration is left alone.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import List, Optional

GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false", "LC_ALL": "C",
           "GIT_PAGER": "cat", "GIT_EDITOR": "true"}
FALLBACK_IDENTITY = ["-c", "user.name=mCode Studio", "-c", "user.email=mcode@localhost"]
# Always: no file-system monitor program, no signature checks in log.
ALWAYS_SAFE = ["-c", "core.fsmonitor=false", "-c", "log.showSignature=false"]
# Repository-local keys that name programs git would run.
_PROGRAM_KEYS = re.compile(r"^(filter\..+\.(clean|smudge|process)|gpg\.(.+\.)?program|"
                           r"core\.hookspath)$", re.I)


def git_path() -> Optional[str]:
    return shutil.which("git")


def _raw(git: str, args: List[str], cwd: str, timeout: float) -> subprocess.CompletedProcess:
    env = dict(os.environ, **GIT_ENV)
    return subprocess.run([git, "--no-pager"] + args, cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=timeout, stdin=subprocess.DEVNULL)


def _safety_args(git: str, cwd: str, hooks: bool = True) -> List[str]:
    """``-c`` options that neutralise programs named in the repository's own config.

    Reading the configuration runs nothing; the overrides then empty every
    filter driver, reset ``gpg.program`` and turn signing off, and (for
    commits made for the AI, ``hooks=False``) point hooks at nothing."""
    args = list(ALWAYS_SAFE)
    if not hooks:
        args += ["-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false"]
    if not is_repo(cwd):
        return args
    found = _raw(git, ["config", "--local", "--includes", "--name-only", "--get-regexp",
                       r"^(filter|gpg|core)\."], cwd, 10)
    for key in found.stdout.split():
        if not _PROGRAM_KEYS.match(key):
            continue
        lower = key.lower()
        if lower.startswith("filter."):
            args += ["-c", f"{key}="]
        elif lower == "core.hookspath":
            if hooks:
                args += ["-c", f"core.hooksPath={os.path.join(cwd, '.git', 'hooks')}"]
        else:
            program = {"gpg.program": "gpg", "gpg.ssh.program": "ssh-keygen",
                       "gpg.x509.program": "gpgsm", "gpg.openpgp.program": "gpg"}.get(lower,
                                                                                     "false")
            args += ["-c", f"{key}={program}", "-c", "commit.gpgSign=false"]
    return args


def _run(args: List[str], cwd: str, timeout: float = 20,
         hooks: bool = True) -> subprocess.CompletedProcess:
    git = git_path()
    if not git:
        raise RuntimeError("git is not installed (sudo apt install git)")
    return _raw(git, _safety_args(git, cwd, hooks) + args, cwd, timeout)


def is_repo(path: str) -> bool:
    return os.path.isdir(os.path.join(path, ".git"))


def init(path: str) -> bool:
    if not git_path() or is_repo(path):
        return False
    result = _run(["init", "-q"], path)
    if result.returncode != 0:
        return False
    _run(["add", "-A"], path)
    identity = [] if _has_identity(path) else FALLBACK_IDENTITY
    _run(identity + ["commit", "-q", "-m", "Project created by mCode Studio"], path)
    return True


def _has_identity(path: str) -> bool:
    result = _run(["config", "user.email"], path)
    return result.returncode == 0 and bool(result.stdout.strip())


def status(path: str) -> dict:
    if not git_path():
        return {"available": False, "repo": False}
    if not is_repo(path):
        return {"available": True, "repo": False}
    result = _run(["status", "--porcelain=v1", "-b", "--ignore-submodules=all"], path)
    if result.returncode != 0:
        return {"available": True, "repo": True, "error": result.stderr.strip()}
    lines = result.stdout.splitlines()
    branch = lines[0][3:] if lines and lines[0].startswith("## ") else ""
    changes = [{"status": line[:2].strip() or "?", "path": line[3:]} for line in lines[1:]]
    return {"available": True, "repo": True, "branch": branch, "changes": changes}


def commit(path: str, message: str, hooks: bool = True) -> str:
    """Stage everything and commit. ``hooks=False`` for commits made for the AI."""
    message = (message or "").strip() or "Update"
    if not is_repo(path):
        init(path)
    _run(["add", "-A"], path, hooks=hooks)
    identity = [] if _has_identity(path) else FALLBACK_IDENTITY
    result = _run(identity + ["commit", "-q", "-m", message[:500]], path, hooks=hooks)
    if result.returncode != 0:
        text = (result.stdout + result.stderr).strip()
        if "nothing to commit" in text:
            return "Nothing to commit."
        raise RuntimeError(text or "git commit failed")
    head = _run(["rev-parse", "--short", "HEAD"], path)
    return f"Committed {head.stdout.strip()}"


def log(path: str, count: int = 20) -> List[dict]:
    if not is_repo(path):
        return []
    result = _run(["log", f"-{count}", "--pretty=format:%h%x09%ct%x09%s"], path)
    out = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            out.append({"hash": parts[0], "time": int(parts[1]), "subject": parts[2]})
    return out


def diff(path: str, max_chars: int = 200000) -> str:
    if not is_repo(path):
        return ""
    result = _run(["diff", "--no-ext-diff", "--no-textconv", "HEAD", "--stat", "--patch"], path)
    return result.stdout[:max_chars]


def show(path: str, rev: str = "HEAD", max_chars: int = 120000) -> str:
    """One commit (message and patch); ``rev`` must be a plain revision name."""
    if not is_repo(path):
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_./~^-]{1,80}", rev or "") or rev.startswith("-"):
        raise ValueError("Give a commit such as HEAD, HEAD~1 or a commit hash.")
    result = _run(["show", "--no-ext-diff", "--no-textconv", "--stat", "--patch", rev, "--"],
                  path)
    return (result.stdout or result.stderr)[:max_chars]


def clone_argv(url: str, dest: str) -> List[str]:
    if not (url.startswith("https://") or url.startswith("git@") or url.startswith("ssh://")):
        raise ValueError("Use an https:// or git@ repository address.")
    return [git_path() or "git", "clone", "--depth", "50", "--", url, dest]
