"""Git, where it is installed: init on creation, status, commit, log, clone.

Commands run with a timeout, without a terminal and without prompting for
credentials (``GIT_TERMINAL_PROMPT=0``): a private repository that needs a
password fails with a clear message instead of hanging.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import List, Optional

GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false", "LC_ALL": "C"}
FALLBACK_IDENTITY = ["-c", "user.name=mCode Studio", "-c", "user.email=mcode@localhost"]


def git_path() -> Optional[str]:
    return shutil.which("git")


def _run(args: List[str], cwd: str, timeout: float = 20) -> subprocess.CompletedProcess:
    git = git_path()
    if not git:
        raise RuntimeError("git is not installed (sudo apt install git)")
    env = dict(os.environ, **GIT_ENV)
    return subprocess.run([git] + args, cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=timeout)


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
    result = _run(["status", "--porcelain=v1", "-b"], path)
    if result.returncode != 0:
        return {"available": True, "repo": True, "error": result.stderr.strip()}
    lines = result.stdout.splitlines()
    branch = lines[0][3:] if lines and lines[0].startswith("## ") else ""
    changes = [{"status": line[:2].strip() or "?", "path": line[3:]} for line in lines[1:]]
    return {"available": True, "repo": True, "branch": branch, "changes": changes}


def commit(path: str, message: str) -> str:
    message = (message or "").strip() or "Update"
    if not is_repo(path):
        init(path)
    _run(["add", "-A"], path)
    identity = [] if _has_identity(path) else FALLBACK_IDENTITY
    result = _run(identity + ["commit", "-q", "-m", message[:500]], path)
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
    result = _run(["diff", "HEAD", "--stat", "--patch"], path)
    return result.stdout[:max_chars]


def clone_argv(url: str, dest: str) -> List[str]:
    if not (url.startswith("https://") or url.startswith("git@") or url.startswith("ssh://")):
        raise ValueError("Use an https:// or git@ repository address.")
    return [git_path() or "git", "clone", "--depth", "50", "--", url, dest]
