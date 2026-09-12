"""FastAPI 入口：OpenAI + Anthropic 透明代理。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from winnie_api import __version__
from winnie_api.anthropic_proxy import proxy_anthropic_models, proxy_messages
from winnie_api.config import Settings, get_settings
from winnie_api.proxy import proxy_chat_completions, proxy_models
from winnie_api.record import InteractionRecorder


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.recorder = InteractionRecorder(
        settings.resolve_log_dir(), service="openai-proxy"
    )
    app.state.anthropic_recorder = InteractionRecorder(
        settings.resolve_anthropic_log_dir(), service="anthropic-proxy"
    )
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0))
    yield
    await app.state.http.aclose()


app = FastAPI(
    title="Winnie LLM Proxy",
    version=__version__,
    lifespan=lifespan,
)


def require_client_auth(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
    settings: Settings = Depends(get_settings),
) -> None:
    """OpenAI 客户端常用 Bearer；Anthropic 常用 x-api-key。"""
    expected = settings.winnie_api_key
    if not expected:
        return
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
    elif x_api_key:
        token = x_api_key.strip()
    if token != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _wants_anthropic(request: Request) -> bool:
    """同一 /v1/models：带 anthropic-version 或仅 x-api-key（无 Bearer）时走 Anthropic。"""
    if request.headers.get("anthropic-version"):
        return True
    has_x = bool(request.headers.get("x-api-key"))
    has_bearer = bool(request.headers.get("authorization"))
    return has_x and not has_bearer


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/v1/models", dependencies=[Depends(require_client_auth)])
async def list_models(request: Request):
    if _wants_anthropic(request):
        return await proxy_anthropic_models(
            client=request.app.state.http,
            settings=request.app.state.settings,
            request=request,
        )
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


@app.post("/v1/messages", dependencies=[Depends(require_client_auth)])
async def anthropic_messages(request: Request):
    try:
        body: dict[str, Any] = await request.json()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=400,
            content=_anthropic_bad_request(f"invalid json: {exc}"),
        )
    missing = [k for k in ("model", "messages", "max_tokens") if k not in body]
    if missing:
        return JSONResponse(
            status_code=400,
            content=_anthropic_bad_request(f"missing required fields: {', '.join(missing)}"),
        )
    return await proxy_messages(
        client=request.app.state.http,
        settings=request.app.state.settings,
        recorder=request.app.state.anthropic_recorder,
        request=request,
        body=body,
    )


def _anthropic_bad_request(message: str) -> dict[str, Any]:
    return {
        "type": "error",
        "error": {"type": "invalid_request_error", "message": message},
    }
