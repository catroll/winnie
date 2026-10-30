"""Job functions executed by the worker."""

from __future__ import annotations

from typing import Any
from uuid import UUID


async def process_meeting_job(ctx: dict[str, Any], meeting_id: str) -> None:
    service = ctx["service"]
    await service.process(UUID(meeting_id))
