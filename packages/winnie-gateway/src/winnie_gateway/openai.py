"""OpenAI 兼容上游转发（密钥透传）。"""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse

from winnie_core.usage.ledger import UsageSink
from winnie_gateway.forward import forward_json_post, streaming_sse_response
from winnie_gateway.record import InteractionRecorder, client_request_url
from winnie_gateway.routes import Route
from winnie_gateway.usage import (
    extract_json_usage,
    extract_stream_usage,
    usage_observer,
)


def openai_upstream_headers(request: Request) -> dict[str, str]:
    """只透传客户端鉴权头，服务端不注入上游密钥。"""
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": request.headers.get("accept", "application/json"),
    }
    if auth := request.headers.get("authorization"):
        headers["Authorization"] = auth
    if org := request.headers.get("openai-organization"):
        headers["OpenAI-Organization"] = org
    if project := request.headers.get("openai-project"):
        headers["OpenAI-Project"] = project
    return headers


def _extract_openai_stream_text(chunk: dict[str, Any]) -> str | None:
    parts: list[str] = []
    for choice in chunk.get("choices") or []:
        delta = choice.get("delta") or {}
        if content := delta.get("content"):
            parts.append(content)
        if reasoning := delta.get("reasoning_content"):
            parts.append(reasoning)
    return "".join(parts) or None


async def proxy_chat_completions(
    *,
    client: httpx.AsyncClient,
    route: Route,
    recorder: InteractionRecorder,
    request: Request,
    body: dict[str, Any],
    usage: UsageSink | None = None,
) -> Response:
    stream = bool(body.get("stream"))
    upstream_url = f"{route.api_base()}/chat/completions"
    headers = openai_upstream_headers(request)
    observer = usage_observer(
        usage=usage,
        route=route,
        model=str(body.get("model") or ""),
        protocol="openai",
        stream=stream,
        endpoint="/v1/chat/completions",
    )
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
            "has_authorization": "authorization" in request.headers,
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
            extract_text=_extract_openai_stream_text,
            extract_tokens=extract_stream_usage,
            on_outcome=observer,
        )

    return await forward_json_post(
        client=client,
        url=upstream_url,
        headers=headers,
        body=body,
        recorder=recorder,
        prefix=prefix,
        error_envelope=lambda msg: {
            "error": {"message": msg, "type": "proxy_error"},
        },
        extract_tokens=extract_json_usage,
        on_outcome=observer,
    )


async def proxy_models(
    *,
    client: httpx.AsyncClient,
    route: Route,
    request: Request,
) -> Response:
    url = f"{route.api_base()}/models"
    headers = openai_upstream_headers(request)
    try:
        upstream = await client.get(url, headers=headers)
        data = upstream.json()
        return JSONResponse(content=data, status_code=upstream.status_code)
    except (httpx.HTTPError, json.JSONDecodeError):
        return JSONResponse(
            content={
                "object": "list",
                "data": [],
                "error": {
                    "message": f"upstream models unavailable for route {route.name}",
                    "type": "proxy_error",
                },
            },
            status_code=502,
        )
