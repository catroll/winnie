"""Winnie API entry point and composition root."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from winnie_api import __version__
from winnie_api.auth.router import router as auth_router
from winnie_api.gateway.router import router as gateway_router
from winnie_api.meetings.router import router as meetings_router
from winnie_api.runtime import AppRuntime, build_runtime
from winnie_api.settings import Settings, load_settings


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
        if runtime is not None:
            await runtime.startup()
            app.state.auth_service = runtime.auth_service
            app.state.meeting_service = runtime.meeting_service
        try:
            yield
        finally:
            if runtime is not None:
                await runtime.shutdown()
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

    return app


app = create_app()
