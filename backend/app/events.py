"""In-process pub/sub that fans pipeline, metric and log events out to WebSocket clients."""
import asyncio
from collections import defaultdict
from typing import Any, Optional


class Hub:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, app_name: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs[app_name].add(q)
        return q

    def unsubscribe(self, app_name: str, q: asyncio.Queue) -> None:
        self._subs[app_name].discard(q)

    def publish(self, app_name: str, message: dict[str, Any]) -> None:
        """Safe to call from the event loop or from worker threads."""
        if self._loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            self._fanout(app_name, message)
        else:
            self._loop.call_soon_threadsafe(self._fanout, app_name, message)

    def _fanout(self, app_name: str, message: dict[str, Any]) -> None:
        for q in list(self._subs.get(app_name, ())):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                pass  # slow client; drop rather than block the pipeline
