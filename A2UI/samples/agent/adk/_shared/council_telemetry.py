"""Process-global pubsub for live council activity events.

Used to surface what's happening inside the LLM council (which children
were picked, who finished first, the judge's verdict, etc.) to a browser
client over Server-Sent Events. Strictly read-only telemetry, fire-and-
forget on the publish side: if no subscribers are listening the event is
dropped (the ring buffer still keeps it for late subscribers).

Why a singleton BUS rather than passing the bus around: the council code
already sits behind several layers of ADK abstractions (BaseLlm, Runner,
agent_executor) that have no place to thread a context object through.
A module-level singleton keeps the patches minimal.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Last N events kept around so a freshly-connected SSE client can catch up
# on what just happened (e.g. clicked a chip then opened the stream a beat
# later). 100 covers a few back-to-back queries.
RING_SIZE = 100


class TelemetryBus:
    """asyncio fan-out: publish once, every subscriber's queue gets a copy."""

    def __init__(self) -> None:
        self._subscribers: List[asyncio.Queue[Dict[str, Any]]] = []
        self._ring: deque[Dict[str, Any]] = deque(maxlen=RING_SIZE)
        self._lock = asyncio.Lock()

    def publish(self, kind: str, **fields: Any) -> None:
        """Synchronous publish. Safe to call from anywhere in the agent code."""
        event: Dict[str, Any] = {
            "event_id": uuid.uuid4().hex[:12],
            "ts_ms": int(time.time() * 1000),
            "kind": kind,
            **fields,
        }
        self._ring.append(event)
        # put_nowait so a slow client never blocks the council.
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("telemetry subscriber queue full, dropping event")

    async def subscribe(self) -> asyncio.Queue[Dict[str, Any]]:
        """Returns a new queue; events published from this moment land in it.

        The caller MUST call unsubscribe() when done or the queue leaks.
        """
        # Reasonable bound; we don't expect more than ~20 events per query.
        q: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._subscribers.append(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[Dict[str, Any]]) -> None:
        async with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return the tail of the ring buffer (most recent first)."""
        return list(self._ring)[-limit:]


# Singleton. Importers get the same bus.
BUS = TelemetryBus()
