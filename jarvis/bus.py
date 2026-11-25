"""In-process event bus: the core publishes, the wallpaper server forwards.

Thread-safe: audio threads call `publish` too, so events hop onto the
asyncio loop via call_soon_threadsafe.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, Callable

Listener = Callable[[dict[str, Any]], None]


class Bus:
    def __init__(self) -> None:
        self._listeners: list[Listener] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self.feed_history: deque[dict[str, Any]] = deque(maxlen=60)
        self.state = "idle"
        self.last_stats: dict[str, Any] = {}

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, fn: Listener) -> Callable[[], None]:
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn) if fn in self._listeners else None

    def publish(self, event: dict[str, Any]) -> None:
        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is not loop:
                loop.call_soon_threadsafe(self._dispatch, event)
                return
        self._dispatch(event)

    def _dispatch(self, event: dict[str, Any]) -> None:
        t = event.get("type")
        if t == "state":
            self.state = event["state"]
        elif t == "feed":
            self.feed_history.append(event)
        elif t == "stats":
            self.last_stats.update({k: v for k, v in event.items() if k != "type"})
        for fn in list(self._listeners):
            try:
                fn(event)
            except Exception:  # a broken listener must never stop the core
                pass

    # convenience
    def set_state(self, state: str) -> None:
        self.publish({"type": "state", "state": state})

    def feed(self, text: str, kind: str = "") -> None:
        stamp = time.strftime("%I:%M %p").lstrip("0")
        self.publish({"type": "feed", "text": f"[{stamp}] {text}", "kind": kind})
