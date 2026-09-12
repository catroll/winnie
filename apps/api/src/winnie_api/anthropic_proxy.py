"""Anthropic Messages API 透明转发。"""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse

from winnie_api.config import Settings
from winnie_api.forward import forward_json_post, streaming_sse_response
from winnie_api.record import InteractionRecorder


def anthropic_upstream_headers(settings: Settings, request: Request) -> dict[str, str]:
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": request.headers.get("accept", "application/json"),
    }
    if settings.anthropic_api_key:
        headers["x-api-key"] = settings.anthropic_api_key
    elif key := request.headers.get("x-api-key"):
        headers["x-api-key"] = key
    elif auth := request.headers.get("authorization"):
        # 部分客户端用 Bearer 调 Anthropic 兼容网关
        headers["Authorization"] = auth

    version = request.headers.get("anthropic-version") or settings.anthropic_version
    headers["anthropic-version"] = version
    if beta := request.headers.get("anthropic-beta"):
        headers["anthropic-beta"] = beta
    return headers


def _extract_anthropic_stream_text(chunk: dict[str, Any]) -> str | None:
    """从 content_block_delta 等事件提取可读文本。"""
    ctype = chunk.get("type")
    if ctype == "content_block_delta":
        delta = chunk.get("delta") or {}
        dtype = delta.get("type")
        if dtype == "text_delta" and (text := delta.get("text")):
            return text
        if dtype == "thinking_delta" and (thinking := delta.get("thinking")):
            return thinking
        if dtype == "input_json_delta" and (partial := delta.get("partial_json")):
            return partial
    return None


def _anthropic_error(msg: str) -> dict[str, Any]:
    return {"type": "error", "error": {"type": "api_error", "message": msg}}


async def proxy_messages(
    *,
    client: httpx.AsyncClient,
    settings: Settings,
    recorder: InteractionRecorder,
    request: Request,
    body: dict[str, Any],
) -> Response:
    stream = bool(body.get("stream"))
    url = f"{settings.normalized_anthropic_base()}/messages"
    headers = anthropic_upstream_headers(settings, request)
    prefix = recorder.begin(
        path="/v1/messages",
        body=body,
        headers_meta={
            "stream": stream,
            "client_host": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
            "anthropic_version": headers.get("anthropic-version"),
        },
    )

    if stream:
        return streaming_sse_response(
            client=client,
            url=url,
            headers=headers,
            body=body,
            recorder=recorder,
            prefix=prefix,
            extract_text=_extract_anthropic_stream_text,
        )

    return await forward_json_post(
        client=client,
        url=url,
        headers=headers,
        body=body,
        recorder=recorder,
        prefix=prefix,
        error_envelope=_anthropic_error,
    )


async def proxy_anthropic_models(
    *,
    client: httpx.AsyncClient,
    settings: Settings,
    request: Request,
) -> Response:
    url = f"{settings.normalized_anthropic_base()}/models"
    headers = anthropic_upstream_headers(settings, request)
    try:
        upstream = await client.get(url, headers=headers)
        data = upstream.json()
        return JSONResponse(content=data, status_code=upstream.status_code)
    except (httpx.HTTPError, json.JSONDecodeError):
        model = settings.anthropic_default_model
        return JSONResponse(
            content={
                "data": [
                    {
                        "type": "model",
                        "id": model,
                        "display_name": model,
                        "created_at": "1970-01-01T00:00:00Z",
                    }
                ],
                "has_more": False,
                "first_id": model,
                "last_id": model,
            }
        )
