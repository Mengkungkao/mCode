"""Who may use the desktop IDE: browser sessions, one-time links and pairing.

* A **session** is one paired browser. Its cookie is a random 256-bit value;
  only its SHA-256 is stored (``web-sessions.json`` in the data folder,
  owner-only), so the file cannot be replayed as a cookie. Each session has
  a **role** (viewer < developer < admin), a label from the browser's
  User-Agent, when it was created and last used, and from which address. A
  session not used for ``web.session_days`` expires. Sessions are revoked one
  by one (Security page) or all at once (LCD: *Sign out all computers*).
* A **one-time link** (``/?t=…``) opens the IDE on this device's own screen
  or from the address mCode prints: single use, 10 minutes, admin role.
* **Pairing** exists only while a pairing screen is open (the LCD's *Pair a
  computer*, or an admin browser's *Pair a phone*): a random 6-digit code
  and a random QR token (``/?p=…``), each single use, for 5 minutes; 10
  wrong codes close the window. A new pairing gets the window's role.
* Browsers paired before 0.2.0 hold the old shared token as their cookie;
  it is exchanged once for an admin session (their access is unchanged).

Security-relevant events go to ``logs/security.jsonl`` (never a secret).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from ..core import storage

log = logging.getLogger("mcode.sessions")

ROLES = ("viewer", "developer", "admin")
RANK = {role: index for index, role in enumerate(ROLES)}
ROLE_LABELS = {"viewer": "Viewer (look only)", "developer": "Developer (projects, builds, AI)",
               "admin": "Full access (also terminal and device settings)"}
LINK_TTL = 600.0
PAIR_TTL = 300.0
PAIR_MAX_WRONG = 10
SEEN_SAVE_EVERY = 300.0             # last-use times are written at most every 5 minutes


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def browser_label(user_agent: str) -> str:
    """'iPhone · Safari', 'Linux · Chrome' … from a User-Agent (nothing else is kept)."""
    ua = user_agent or ""
    device = ("iPhone" if "iPhone" in ua else "iPad" if "iPad" in ua else
              "Android" if "Android" in ua else "Mac" if "Macintosh" in ua else
              "Windows" if "Windows" in ua else "Linux" if "Linux" in ua or "X11" in ua else
              "Browser")
    if "Edg/" in ua:
        app = "Edge"
    elif "Firefox/" in ua or "FxiOS" in ua:
        app = "Firefox"
    elif "Chrome/" in ua or "CriOS" in ua:
        app = "Chrome"
    elif "Safari/" in ua:
        app = "Safari"
    else:
        app = ""
    standalone = " (home screen)" if device in ("iPhone", "iPad") and "Safari/" not in ua and \
        "CriOS" not in ua and "FxiOS" not in ua else ""
    return f"{device} · {app}{standalone}" if app else f"{device}{standalone}"


class Session:
    __slots__ = ("key", "role", "label", "created", "last_seen", "address", "source", "_saved")

    def __init__(self, key: str, role: str, label: str, created: float, last_seen: float,
                 address: str, source: str):
        self.key = key                  # SHA-256 of the cookie value
        self.role = role
        self.label = label
        self.created = created
        self.last_seen = last_seen
        self.address = address
        self.source = source
        self._saved = last_seen

    @property
    def id(self) -> str:
        """A public name for the session (not usable as a cookie)."""
        return self.key[:12]

    def to_dict(self) -> dict:
        return {"id": self.id, "role": self.role, "role_label": ROLE_LABELS.get(self.role, ""),
                "label": self.label, "created": self.created, "last_seen": self.last_seen,
                "address": self.address, "source": self.source}

    def to_json(self) -> dict:
        return {"role": self.role, "label": self.label, "created": self.created,
                "last_seen": self.last_seen, "address": self.address, "source": self.source}


class PairingWindow:
    """The secrets for pairing while a pairing screen is open."""

    def __init__(self, role: str, ttl: float, clock: Callable[[], float]):
        self.role = role
        self.clock = clock
        self.opened = clock()
        self.expires = self.opened + ttl
        self.wrong = 0
        self.paired: List[str] = []         # labels of browsers paired in this window
        self.renew()

    def renew(self) -> None:
        self.code = f"{secrets.randbelow(1000000):06d}"
        self.qr_token = secrets.token_urlsafe(16)

    @property
    def open(self) -> bool:
        return self.clock() < self.expires and self.wrong < PAIR_MAX_WRONG

    def to_dict(self) -> dict:
        return {"open": self.open, "role": self.role, "role_label": ROLE_LABELS[self.role],
                "code": self.code if self.open else "", "expires": self.expires,
                "seconds_left": max(0, int(self.expires - self.clock())), "paired": self.paired}


class SessionStore:
    def __init__(self, path: str, days: Callable[[], int], log_path: str = "",
                 clock: Callable[[], float] = time.time,
                 publish: Callable[[str, object], None] = lambda t, d: None):
        self.path = path
        self.days = days
        self.log_path = log_path
        self.clock = clock
        self.publish = publish
        self._lock = threading.RLock()
        self._sessions: Dict[str, Session] = {}
        self._links: Dict[str, Tuple[float, str]] = {}      # digest -> (expires, role)
        self.window: Optional[PairingWindow] = None
        self._load()

    # ------------------------------------------------------------ storage
    def _load(self) -> None:
        data = storage.read_json(self.path, {}, dict)
        for key, value in (data.get("sessions") or {}).items():
            if not isinstance(value, dict) or not re.fullmatch(r"[0-9a-f]{64}", key):
                continue
            role = value.get("role") if value.get("role") in RANK else "viewer"
            try:
                self._sessions[key] = Session(key, role, str(value.get("label") or "Browser"),
                                              float(value.get("created") or 0),
                                              float(value.get("last_seen") or 0),
                                              str(value.get("address") or ""),
                                              str(value.get("source") or ""))
            except (TypeError, ValueError):
                continue
        self.expire()

    def _save(self) -> None:
        with self._lock:
            data = {"sessions": {k: s.to_json() for k, s in self._sessions.items()}}
            for session in self._sessions.values():
                session._saved = session.last_seen
        storage.atomic_write_json(self.path, data, mode=0o600)

    def event(self, what: str, **details) -> None:
        """Record a security event (no secrets) and tell the interfaces."""
        record = {"time": self.clock(), "event": what}
        record.update(details)
        if self.log_path:
            try:
                storage.append_jsonl(self.log_path, record, max_bytes=512 * 1024)
            except OSError as exc:
                log.warning("cannot write the security log: %s", exc)
        log.info("security: %s %s", what, " ".join(f"{k}={v}" for k, v in details.items()))
        self.publish("security", {"event": what})

    def events(self, limit: int = 50) -> List[dict]:
        if not self.log_path:
            return []
        return list(reversed(storage.read_jsonl(self.log_path, limit)))

    # ----------------------------------------------------------- sessions
    def max_age(self) -> int:
        return int(self.days()) * 86400

    def expire(self) -> int:
        limit = self.clock() - self.max_age()
        with self._lock:
            old = [k for k, s in self._sessions.items() if max(s.last_seen, s.created) < limit]
            for key in old:
                del self._sessions[key]
        if old:
            self._save()
            self.event("sessions-expired", count=len(old))
        return len(old)

    def create(self, role: str, user_agent: str = "", address: str = "",
               source: str = "paired") -> Tuple[str, Session]:
        if role not in RANK:
            raise ValueError(f"unknown role {role}")
        value = secrets.token_urlsafe(32)
        now = self.clock()
        session = Session(digest(value), role, browser_label(user_agent), now, now, address,
                          source)
        with self._lock:
            self._sessions[session.key] = session
        self._save()
        self.event("session-created", session=session.id, role=role, source=source,
                   address=address, browser=session.label)
        return value, session

    def check(self, value: str, address: str = "") -> Optional[Session]:
        if not value or len(value) > 200:
            return None
        key = digest(value)
        now = self.clock()
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                return None
            if max(session.last_seen, session.created) < now - self.max_age():
                del self._sessions[key]
                expired = True
            else:
                expired = False
                session.last_seen = now
                if address:
                    session.address = address
                save = now - session._saved > SEEN_SAVE_EVERY
        if expired:
            self._save()
            self.event("session-expired", session=key[:12])
            return None
        if save:
            self._save()
        return session

    def get(self, session_id: str) -> Optional[Session]:
        with self._lock:
            for session in self._sessions.values():
                if session.id == session_id:
                    return session
        return None

    def list(self) -> List[dict]:
        with self._lock:
            sessions = sorted(self._sessions.values(), key=lambda s: -s.last_seen)
            return [s.to_dict() for s in sessions]

    def revoke(self, session_id: str, by: str = "") -> bool:
        with self._lock:
            found = [k for k, s in self._sessions.items() if s.id == session_id]
            for key in found:
                del self._sessions[key]
        if found:
            self._save()
            self.event("session-revoked", session=session_id, by=by)
        return bool(found)

    def revoke_all(self, keep: str = "", by: str = "") -> int:
        with self._lock:
            gone = [k for k, s in self._sessions.items() if s.id != keep]
            for key in gone:
                del self._sessions[key]
            self._links.clear()
        self._save()
        self.close_pairing()
        self.event("sessions-revoked-all", count=len(gone), by=by)
        return len(gone)

    def set_role(self, session_id: str, role: str, by: str = "") -> bool:
        if role not in RANK:
            raise ValueError("Unknown role.")
        session = self.get(session_id)
        if session is None:
            return False
        with self._lock:
            session.role = role
        self._save()
        self.event("session-role", session=session_id, role=role, by=by)
        return True

    # ------------------------------------------------------ one-time links
    def new_link(self, role: str = "admin", ttl: float = LINK_TTL) -> str:
        value = secrets.token_urlsafe(24)
        now = self.clock()
        with self._lock:
            self._links = {k: v for k, v in self._links.items() if v[0] > now}
            self._links[digest(value)] = (now + ttl, role)
        return value

    def use_link(self, value: str) -> Optional[str]:
        """The link's role, once; None if unknown, used or expired."""
        if not value or len(value) > 200:
            return None
        with self._lock:
            found = self._links.pop(digest(value), None)
        if found is None or found[0] < self.clock():
            return None
        return found[1]

    # ------------------------------------------------------------- pairing
    def open_pairing(self, role: str, ttl: float = PAIR_TTL) -> PairingWindow:
        if role not in RANK:
            role = "developer"
        with self._lock:
            window = self.window
            if window is None or not window.open:
                window = self.window = PairingWindow(role, ttl, self.clock)
                self.event("pairing-opened", role=role)
            else:
                window.role = role
                window.expires = max(window.expires, self.clock() + ttl)
        self.publish("web", {"pairing": True})
        return window

    def close_pairing(self) -> None:
        with self._lock:
            window, self.window = self.window, None
        if window is not None:
            self.event("pairing-closed", paired=len(window.paired))
            self.publish("web", {"pairing": False})

    def pairing(self) -> Optional[PairingWindow]:
        with self._lock:
            window = self.window
            if window is not None and not window.open:
                self.window = None
                window = None
        return window

    def pair_with_code(self, code: str, user_agent: str, address: str) -> Optional[Tuple[str, Session]]:
        return self._pair("code", code, user_agent, address)

    def pair_with_qr(self, token: str, user_agent: str, address: str) -> Optional[Tuple[str, Session]]:
        return self._pair("qr", token, user_agent, address)

    def _pair(self, method: str, secret: str, user_agent: str, address: str):
        secret = (secret or "").strip()
        with self._lock:
            window = self.pairing()
            if window is None:
                return None
            expected = window.code if method == "code" else window.qr_token
            if not secret or not hmac.compare_digest(secret.encode(), expected.encode()):
                window.wrong += 1
                wrong, still_open = window.wrong, window.open
                role = None
            else:
                role = window.role
                window.renew()                  # each secret works once
        if role is None:
            self.event("pairing-failed", method=method, address=address, wrong=wrong)
            if not still_open:
                self.close_pairing()
            return None
        value, session = self.create(role, user_agent, address, source=f"paired-{method}")
        with self._lock:
            if self.window is not None:
                self.window.paired.append(session.label)
        self.publish("web", {"paired": session.label})
        return value, session
