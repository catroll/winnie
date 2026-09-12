"""OpenAI 兼容上游转发。"""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse

from winnie_api.config import Settings
from winnie_api.forward import forward_json_post, streaming_sse_response
from winnie_api.record import InteractionRecorder


def openai_upstream_headers(settings: Settings, request: Request) -> dict[str, str]:
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": request.headers.get("accept", "application/json"),
    }
    if settings.upstream_api_key:
        headers["Authorization"] = f"Bearer {settings.upstream_api_key}"
    elif auth := request.headers.get("authorization"):
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
    settings: Settings,
    recorder: InteractionRecorder,
    request: Request,
    body: dict[str, Any],
) -> Response:
    stream = bool(body.get("stream"))
    url = f"{settings.normalized_upstream_base()}/chat/completions"
    headers = openai_upstream_headers(settings, request)
    prefix = recorder.begin(
        path="/v1/chat/completions",
        body=body,
        headers_meta={
            "stream": stream,
            "client_host": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
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
            extract_text=_extract_openai_stream_text,
        )

    return await forward_json_post(
        client=client,
        url=url,
        headers=headers,
        body=body,
        recorder=recorder,
        prefix=prefix,
        error_envelope=lambda msg: {
            "error": {"message": msg, "type": "proxy_error"},
        },
    )


async def proxy_models(
    *,
    client: httpx.AsyncClient,
    settings: Settings,
    request: Request,
) -> Response:
    url = f"{settings.normalized_upstream_base()}/models"
    headers = openai_upstream_headers(settings, request)
    try:
        upstream = await client.get(url, headers=headers)
        data = upstream.json()
        return JSONResponse(content=data, status_code=upstream.status_code)
    except (httpx.HTTPError, json.JSONDecodeError):
        model = settings.upstream_default_model
        return JSONResponse(
            content={
                "object": "list",
                "data": [
                    {
                        "id": model,
                        "object": "model",
                        "created": 0,
                        "owned_by": "winnie-proxy",
                    }
                ],
            }
        )
