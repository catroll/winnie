"""Anthropic Messages API 透明转发（密钥透传）。"""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse

from winnie_api.forward import forward_json_post, streaming_sse_response
from winnie_api.record import InteractionRecorder, client_request_url
from winnie_api.routes import Route


def anthropic_upstream_headers(request: Request, *, default_version: str) -> dict[str, str]:
    """只透传客户端鉴权头，服务端不注入上游密钥。"""
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": request.headers.get("accept", "application/json"),
    }
    if key := request.headers.get("x-api-key"):
        headers["x-api-key"] = key
    if auth := request.headers.get("authorization"):
        headers["Authorization"] = auth

    version = request.headers.get("anthropic-version") or default_version
    headers["anthropic-version"] = version
    if beta := request.headers.get("anthropic-beta"):
        headers["anthropic-beta"] = beta
    return headers


def _extract_anthropic_stream_text(chunk: dict[str, Any]) -> str | None:
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
    route: Route,
    recorder: InteractionRecorder,
    request: Request,
    body: dict[str, Any],
    default_version: str,
) -> Response:
    stream = bool(body.get("stream"))
    upstream_url = f"{route.api_base()}/messages"
    headers = anthropic_upstream_headers(request, default_version=default_version)
    prefix = recorder.begin(
        client_url=client_request_url(request),
        upstream_url=upstream_url,
        method="POST",
        body=body,
        headers_meta={
            "route": route.name,
            "protocol": route.protocol,
            "stream": stream,
            "client_host": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
            "anthropic_version": headers.get("anthropic-version"),
            "has_x_api_key": "x-api-key" in request.headers,
        },
    )

    if stream:
        return streaming_sse_response(
            client=client,
            url=upstream_url,
            headers=headers,
            body=body,
            recorder=recorder,
            prefix=prefix,
            extract_text=_extract_anthropic_stream_text,
        )

    return await forward_json_post(
        client=client,
        url=upstream_url,
        headers=headers,
        body=body,
        recorder=recorder,
        prefix=prefix,
        error_envelope=_anthropic_error,
    )


async def proxy_anthropic_models(
    *,
    client: httpx.AsyncClient,
    route: Route,
    request: Request,
    default_version: str,
) -> Response:
    url = f"{route.api_base()}/models"
    headers = anthropic_upstream_headers(request, default_version=default_version)
    try:
        upstream = await client.get(url, headers=headers)
        data = upstream.json()
        return JSONResponse(content=data, status_code=upstream.status_code)
    except (httpx.HTTPError, json.JSONDecodeError):
        return JSONResponse(
            content={
                "data": [],
                "has_more": False,
                "error": {
                    "type": "api_error",
                    "message": f"upstream models unavailable for route {route.name}",
                },
            },
            status_code=502,
        )
