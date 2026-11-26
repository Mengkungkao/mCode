"""The event bus: services announce changes; the interfaces redraw.

Topics are short strings ("job", "job.line", "devices", "ai", "monitor",
"settings", "projects", "voice", "toast", ...). Callbacks run on the
publisher's thread and must only record state and wake their own loop.
Queues serve the desktop IDE's event stream: a slow browser loses old
events (the queue is bounded), it never blocks a build.
"""

from __future__ import annotations

import itertools
import logging
import queue
import threading
import time
from typing import Any, Callable, Dict, Optional

log = logging.getLogger("mcode.events")


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._callbacks: Dict[int, Callable[[str, Any], None]] = {}
        self._queues: Dict[int, "queue.Queue"] = {}
        self._ids = itertools.count(1)

    def subscribe(self, callback: Callable[[str, Any], None]) -> int:
        with self._lock:
            token = next(self._ids)
            self._callbacks[token] = callback
            return token

    def subscribe_queue(self, maxsize: int = 2000) -> tuple:
        """(token, queue) receiving (topic, data, time) tuples."""
        q: "queue.Queue" = queue.Queue(maxsize=maxsize)
        with self._lock:
            token = next(self._ids)
            self._queues[token] = q
            return token, q

    def unsubscribe(self, token: Optional[int]) -> None:
        if token is None:
            return
        with self._lock:
            self._callbacks.pop(token, None)
            self._queues.pop(token, None)

    def publish(self, topic: str, data: Any = None) -> None:
        with self._lock:
            callbacks = list(self._callbacks.values())
            queues = list(self._queues.values())
        for callback in callbacks:
            try:
                callback(topic, data)
            except Exception:       # isolation boundary: one listener must not stop the rest
                log.exception("event listener failed for %s", topic)
        stamp = time.time()
        for q in queues:
            try:
                q.put_nowait((topic, data, stamp))
            except queue.Full:
                try:                # drop the oldest, keep the newest
                    q.get_nowait()
                    q.put_nowait((topic, data, stamp))
                except (queue.Empty, queue.Full):
                    pass
