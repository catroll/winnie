"""Winnie API host: compose framework and capability packages."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from winnie_core.auth.capability import capability as auth_capability
from winnie_core.capability import WinnieFramework
from winnie_core.jobs.queue import ArqJobQueue
from winnie_gateway.capability import capability as gateway_capability
from winnie_meetings.capability import capability as meetings_capability

from winnie_api import __version__
from winnie_api.runtime import AppRuntime, build_runtime
from winnie_api.settings import Settings, load_settings

logger = logging.getLogger(__name__)


def create_app(
    *,
    settings: Settings | None = None,
    http: httpx.AsyncClient | None = None,
) -> FastAPI:
    """Build the application from independently registered capabilities."""
    resolved = settings or load_settings()
    owns_http = http is None
    runtime: AppRuntime | None = build_runtime(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.gateway = resolved.gateway_config()
        app.state.routes_table = app.state.gateway.route_table()
        app.state.log_root = resolved.resolve_log_root()
        app.state.recorders = {}
        app.state.default_user_id = resolved.meetings.default_user_id
        app.state.http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=30.0)
        )
        app.state.runtime = runtime
        if runtime is not None:
            await runtime.startup()
            app.state.auth_service = runtime.auth_service
            app.state.meeting_service = runtime.meeting_service
        try:
            yield
        finally:
            if runtime is not None:
                await runtime.shutdown()
                app.state.runtime = None
            if owns_http:
                await app.state.http.aclose()

    app = FastAPI(title="Winnie", version=__version__, lifespan=lifespan)
    framework = WinnieFramework(app)
    framework.register(gateway_capability())
    if resolved.auth.enabled:
        framework.register(auth_capability())
    if resolved.meetings.enabled:
        framework.register(meetings_capability())
    app.state.capability_names = framework.capability_names

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        runtime = getattr(app.state, "runtime", None)
        if runtime is None:
            return {
                "status": "ok",
                "database": "disabled",
                "queue": "disabled",
            }
        try:
            async with runtime.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            logger.exception("readiness database check failed")
            raise HTTPException(
                status_code=503, detail="database unavailable"
            ) from exc

        queue = resolved.jobs.backend
        if queue == "arq":
            try:
                await asyncio.wait_for(
                    ArqJobQueue(resolved.jobs.redis_url).ping(),
                    timeout=2.0,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("readiness queue check failed")
                raise HTTPException(
                    status_code=503, detail="queue unavailable"
                ) from exc
        return {"status": "ok", "database": "ok", "queue": queue}

    return app


app = create_app()
