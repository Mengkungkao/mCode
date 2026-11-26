"""Small persistent state that is not a setting: recent projects, the current
project, the last selected board. Stored atomically in ``state.json``."""

from __future__ import annotations

import threading
from typing import Any, Dict

from . import storage


class StateStore:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = storage.read_json(path, {}, dict) if path else {}

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            value = self._data.get(key, default)
            if isinstance(value, (list, dict)):
                return type(value)(value)
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if self._data.get(key) == value:
                return
            self._data[key] = value
            self._save()

    def update(self, values: Dict[str, Any]) -> None:
        with self._lock:
            self._data.update(values)
            self._save()

    def _save(self) -> None:
        if self.path:
            storage.atomic_write_json(self.path, self._data)
