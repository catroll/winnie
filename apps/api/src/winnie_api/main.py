"""FastAPI 入口：OpenAI 兼容透明代理。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from winnie_api import __version__
from winnie_api.config import Settings, get_settings
from winnie_api.proxy import proxy_chat_completions, proxy_models
from winnie_api.record import InteractionRecorder


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.recorder = InteractionRecorder(settings.resolve_log_dir())
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0))
    yield
    await app.state.http.aclose()


app = FastAPI(
    title="Winnie OpenAI Proxy",
    version=__version__,
    lifespan=lifespan,
)


def require_client_auth(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    expected = settings.winnie_api_key
    if not expected:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if token != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/v1/models", dependencies=[Depends(require_client_auth)])
async def list_models(request: Request):
    return await proxy_models(
        client=request.app.state.http,
        settings=request.app.state.settings,
        request=request,
    )


@app.post("/v1/chat/completions", dependencies=[Depends(require_client_auth)])
async def chat_completions(request: Request):
    try:
        body: dict[str, Any] = await request.json()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=400,
            content={"error": {"message": f"invalid json: {exc}", "type": "invalid_request_error"}},
        )
    if "messages" not in body:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "messages is required",
                    "type": "invalid_request_error",
                }
            },
        )
    return await proxy_chat_completions(
        client=request.app.state.http,
        settings=request.app.state.settings,
        recorder=request.app.state.recorder,
        request=request,
        body=body,
    )
