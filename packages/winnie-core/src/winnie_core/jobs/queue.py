"""Job queue port and implementations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID


class JobQueue(Protocol):
    async def enqueue(self, meeting_id: UUID) -> None: ...


class InlineJobQueue:
    """Run the processor in the current process.

    Useful for local development and deterministic tests. A request that
    enqueues work will wait for it to finish.
    """

    def __init__(
        self,
        processor: Callable[[UUID], Awaitable[None]],
    ) -> None:
        self._processor = processor

    async def enqueue(self, meeting_id: UUID) -> None:
        await self._processor(meeting_id)


class ArqJobQueue:
    """Enqueue processing to a separate arq worker."""

    def __init__(self, redis_url: str) -> None:
        self.redis_url = redis_url or "redis://localhost:6379"

    async def enqueue(self, meeting_id: UUID) -> None:
        from arq import create_pool
        from arq.connections import RedisSettings

        pool = await create_pool(RedisSettings.from_dsn(self.redis_url))
        try:
            await pool.enqueue_job("process_meeting_job", str(meeting_id))
        finally:
            await pool.aclose()

    async def ping(self) -> None:
        """Check that Redis is reachable; used by the readiness probe."""
        from arq import create_pool
        from arq.connections import RedisSettings

        pool = await create_pool(RedisSettings.from_dsn(self.redis_url))
        try:
            await pool.ping()
        finally:
            await pool.aclose()
