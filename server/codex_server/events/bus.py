from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import replace
from typing import Any

from ..models import ServerEvent


class EventBus:
    def __init__(self, *, max_events: int = 1000):
        self._max_events = max_events
        self._events: deque[ServerEvent] = deque(maxlen=max_events)
        self._subscribers: set[asyncio.Queue[ServerEvent]] = set()
        self._next_id = 1
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def publish(self, event: ServerEvent) -> ServerEvent:
        async with self._lock:
            event = replace(event, event_id=self._next_id)
            self._next_id += 1
            self._events.append(event)
            subscribers = list(self._subscribers)
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
        return event

    def publish_threadsafe(self, event: ServerEvent) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(lambda: asyncio.create_task(self.publish(event)))

    async def subscribe(self, *, last_event_id: int | None = None) -> asyncio.Queue[ServerEvent]:
        queue: asyncio.Queue[ServerEvent] = asyncio.Queue(maxsize=250)
        async with self._lock:
            self._subscribers.add(queue)
            replay = [event for event in self._events if last_event_id is not None and (event.event_id or 0) > last_event_id]
        for event in replay:
            await queue.put(event)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[ServerEvent]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)


def sse_encode(event: ServerEvent) -> str:
    import json

    data = json.dumps(event.to_json(), ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.event_id}\nevent: {event.type}\ndata: {data}\n\n"


def ping_event() -> str:
    import json
    import time

    data = json.dumps({"time": time.time()}, separators=(",", ":"))
    return f"event: ping\ndata: {data}\n\n"

