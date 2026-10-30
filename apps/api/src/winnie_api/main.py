"""Winnie API entry point and composition root."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from winnie_api import __version__
from winnie_api.gateway.router import router as gateway_router
from winnie_api.meetings.router import router as meetings_router
from winnie_api.meetings.wiring import MeetingRuntime, build_meeting_runtime
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
    meeting_runtime: MeetingRuntime | None = None
    if resolved.meetings.enabled:
        meeting_runtime = build_meeting_runtime(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = resolved
        app.state.routes_table = resolved.route_table()
        app.state.log_root = resolved.resolve_log_root()
        app.state.recorders = {}
        app.state.http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=30.0)
        )
        if meeting_runtime is not None:
            await meeting_runtime.startup()
            app.state.meeting_service = meeting_runtime.service
        try:
            yield
        finally:
            if meeting_runtime is not None:
                await meeting_runtime.shutdown()
            if owns_http:
                await app.state.http.aclose()

    app = FastAPI(title="Winnie", version=__version__, lifespan=lifespan)
    app.state.settings = resolved
    app.include_router(gateway_router)
    if meeting_runtime is not None:
        app.include_router(meetings_router, prefix="/api/v1")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
