"""In-process fan-out for the open WebSocket connections.

A published event is written to the database first (the durable part) and then
handed to every queue subscribed for that user. A queue that is full drops the
event instead of blocking the publisher: its client will see the event in the
backlog on its next connection.

This hub is per process, which is the right default for a single-user
deployment. A multi-worker deployment would need Redis pub/sub; that is
recorded in docs/push.md rather than guessed at here.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


class PushHub:
    def __init__(self, *, max_queue: int = 100) -> None:
        self.max_queue = max_queue
        self._subscriptions: dict[str, set[asyncio.Queue[dict[str, Any]]]] = (
            defaultdict(set)
        )

    @asynccontextmanager
    async def subscribe(self, user_id: str) -> AsyncIterator[asyncio.Queue]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=self.max_queue
        )
        self._subscriptions[user_id].add(queue)
        try:
            yield queue
        finally:
            self._subscriptions[user_id].discard(queue)
            if not self._subscriptions[user_id]:
                self._subscriptions.pop(user_id, None)

    def publish(self, user_id: str, event: dict[str, Any]) -> int:
        """Fan out one event; returns how many connections accepted it."""
        delivered = 0
        for queue in tuple(self._subscriptions.get(user_id, ())):
            try:
                queue.put_nowait(event)
                delivered += 1
            except asyncio.QueueFull:
                # The client is slower than the stream. It will catch up from
                # the backlog on reconnect; blocking the publisher is worse.
                continue
        return delivered

    def connections(self, user_id: str | None = None) -> int:
        if user_id is not None:
            return len(self._subscriptions.get(user_id, ()))
        return sum(len(queues) for queues in self._subscriptions.values())
