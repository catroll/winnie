"""arq worker entry point.

Run with:
    uv run arq winnie_api.jobs.worker.WorkerSettings
"""

from __future__ import annotations

from typing import Any

from arq import cron
from arq.connections import RedisSettings

from winnie_api.jobs.tasks import (
    cleanup_expired_audio,
    cleanup_expired_sessions,
    process_meeting_job,
)
from winnie_api.runtime import build_runtime
from winnie_api.settings import get_settings


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    runtime = build_runtime(settings)
    if runtime is None:
        raise RuntimeError("auth.enabled or meetings.enabled must be true")
    await runtime.startup()
    ctx["runtime"] = runtime
    ctx["service"] = runtime.meeting_service
    ctx["auth"] = runtime.auth_service


async def shutdown(ctx: dict[str, Any]) -> None:
    runtime = ctx.get("runtime")
    if runtime is not None:
        await runtime.shutdown()


class WorkerSettings:
    functions = [
        process_meeting_job,
        cleanup_expired_sessions,
        cleanup_expired_audio,
    ]
    cron_jobs = [
        cron(cleanup_expired_sessions, hour=3, minute=17),
        cron(cleanup_expired_audio, hour=3, minute=47),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(
        get_settings().jobs.redis_url or "redis://localhost:6379"
    )
