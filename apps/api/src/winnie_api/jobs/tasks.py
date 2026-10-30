"""Job functions executed by the worker."""

from __future__ import annotations

from typing import Any
from uuid import UUID


async def process_meeting_job(ctx: dict[str, Any], meeting_id: str) -> None:
    service = ctx.get("service")
    if service is None:
        raise RuntimeError("meetings are not enabled for this worker")
    await service.process(UUID(meeting_id))


async def cleanup_expired_sessions(ctx: dict[str, Any]) -> int:
    auth = ctx.get("auth")
    if auth is None:
        raise RuntimeError("accounts are not enabled for this worker")
    return await auth.cleanup_expired_sessions()


async def cleanup_expired_audio(ctx: dict[str, Any]) -> int:
    service = ctx.get("service")
    if service is None:
        raise RuntimeError("meetings are not enabled for this worker")
    return await service.purge_expired_audio()
