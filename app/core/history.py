"""What happened, kept on disk: builds, uploads, tests and AI actions.

Two append-only JSON-lines logs in the data folder:

* ``logs/history.jsonl``: builds, uploads, erases, tests (the dashboard's
  "recent builds" and "recent uploads", the Hardware Manager's upload
  history);
* ``logs/ai-actions.jsonl``: every AI request, tool use, file change,
  approval, rejection and undo (the AI activity log the brief requires).

Text is passed through :func:`redact` before it is written.
"""

from __future__ import annotations

import threading
import time
from typing import List, Optional

from . import storage
from .redact import redact


def _clean(value):
    if isinstance(value, str):
        return redact(value)[:4000]
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value][:200]
    return value


class History:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def add(self, _event: str, **fields) -> dict:
        record = _clean(fields)
        record.update(time=time.time(), kind=_event)
        with self._lock:
            storage.append_jsonl(self.path, record)
        return record

    def recent(self, kinds: Optional[List[str]] = None, project_id: Optional[str] = None,
               limit: int = 20) -> List[dict]:
        with self._lock:
            records = storage.read_jsonl(self.path, limit=2000)
        out = []
        for record in reversed(records):
            if kinds and record.get("kind") not in kinds:
                continue
            if project_id and record.get("project_id") != project_id:
                continue
            out.append(record)
            if len(out) >= limit:
                break
        return out
