"""Winnie API entry point and composition root."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from winnie_api import __version__
from winnie_api.gateway.router import router as gateway_router
from winnie_api.settings import Settings, load_settings


def create_app(
    *,
    settings: Settings | None = None,
    http: httpx.AsyncClient | None = None,
) -> FastAPI:
    """Build the application.

    Settings and the shared HTTP client can be injected, which keeps tests and
    alternate deployments free of process-wide globals. When omitted, settings
    are loaded from config.yaml during startup and the client is owned by the
    app.
    """
    owns_http = http is None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved = settings or load_settings()
        app.state.settings = resolved
        app.state.routes_table = resolved.route_table()
        app.state.log_root = resolved.resolve_log_root()
        app.state.recorders = {}
        app.state.http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=30.0)
        )
        try:
            yield
        finally:
            if owns_http:
                await app.state.http.aclose()

    app = FastAPI(title="Winnie", version=__version__, lifespan=lifespan)
    app.include_router(gateway_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
