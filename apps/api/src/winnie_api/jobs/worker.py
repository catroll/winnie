"""arq worker entry point.

Run with:
    uv run arq winnie_api.jobs.worker.WorkerSettings
"""

from __future__ import annotations

from typing import Any

from arq.connections import RedisSettings

from winnie_api.jobs.tasks import process_meeting_job
from winnie_api.meetings.wiring import build_meeting_runtime
from winnie_api.settings import get_settings


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    runtime = build_meeting_runtime(settings)
    await runtime.startup()
    ctx["runtime"] = runtime
    ctx["service"] = runtime.service


async def shutdown(ctx: dict[str, Any]) -> None:
    runtime = ctx.get("runtime")
    if runtime is not None:
        await runtime.shutdown()


class WorkerSettings:
    functions = [process_meeting_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(
        get_settings().jobs.redis_url or "redis://localhost:6379"
    )
