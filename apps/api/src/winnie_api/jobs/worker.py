"""arq worker entry point.

Run with:
    uv run arq winnie_api.jobs.worker.WorkerSettings
"""

from __future__ import annotations

from typing import Any

import httpx
from arq import cron
from arq.connections import RedisSettings

from winnie_llm import ApiKeys, ProviderRegistry

from winnie_api.ai.seed import seed_input
from winnie_api.jobs.tasks import (
    cleanup_expired_audio,
    cleanup_expired_sessions,
    process_meeting_job,
)
from winnie_api.runtime import build_runtime
from winnie_api.settings import get_settings


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    ai = (
        ProviderRegistry(
            settings.resolve_ai_path(),
            keys=ApiKeys.load(),
            seed=seed_input(settings),
        )
        if settings.ai.enabled
        else None
    )
    if ai is not None:
        ai.load()
    http = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0))
    runtime = build_runtime(settings, ai=ai, http=http)
    if runtime is None:
        await http.aclose()
        raise RuntimeError("auth.enabled or meetings.enabled must be true")
    try:
        await runtime.startup()
    except Exception:
        await http.aclose()
        raise
    ctx["runtime"] = runtime
    ctx["service"] = runtime.meeting_service
    ctx["auth"] = runtime.auth_service
    ctx["ai"] = ai
    ctx["http"] = http


async def shutdown(ctx: dict[str, Any]) -> None:
    runtime = ctx.get("runtime")
    if runtime is not None:
        await runtime.shutdown()
    http = ctx.get("http")
    if http is not None:
        await http.aclose()


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
