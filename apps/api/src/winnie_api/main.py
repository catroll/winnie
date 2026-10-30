"""Winnie API entry point and composition root."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from winnie_api import __version__
from winnie_api.auth.router import router as auth_router
from winnie_api.gateway.router import router as gateway_router
from winnie_api.jobs.queue import ArqJobQueue
from winnie_api.meetings.router import router as meetings_router
from winnie_api.runtime import AppRuntime, build_runtime
from winnie_api.settings import Settings, load_settings

logger = logging.getLogger(__name__)


def create_app(
    *,
    settings: Settings | None = None,
    http: httpx.AsyncClient | None = None,
) -> FastAPI:
    """Build the application.

    Settings and the shared HTTP client can be injected, which keeps tests and
    alternate deployments free of process-wide globals. When omitted, settings
    are loaded from config.yaml.
    """
    resolved = settings or load_settings()
    owns_http = http is None
    runtime: AppRuntime | None = build_runtime(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = resolved
        app.state.routes_table = resolved.route_table()
        app.state.log_root = resolved.resolve_log_root()
        app.state.recorders = {}
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
    app.state.settings = resolved
    app.include_router(gateway_router)
    if resolved.auth.enabled:
        app.include_router(auth_router, prefix="/api/v1")
    if resolved.meetings.enabled:
        app.include_router(meetings_router, prefix="/api/v1")

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
