"""上游 HTTP 转发与流式透传。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from winnie_api.config import Settings
from winnie_api.record import InteractionRecorder


def upstream_headers(settings: Settings, request: Request) -> dict[str, str]:
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": request.headers.get("accept", "application/json"),
    }
    # 优先用服务端配置的上游密钥；未配置时透传客户端 Bearer
    if settings.upstream_api_key:
        headers["Authorization"] = f"Bearer {settings.upstream_api_key}"
    elif auth := request.headers.get("authorization"):
        headers["Authorization"] = auth
    if org := request.headers.get("openai-organization"):
        headers["OpenAI-Organization"] = org
    if project := request.headers.get("openai-project"):
        headers["OpenAI-Project"] = project
    return headers


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
    headers = upstream_headers(settings, request)
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
        return await _stream_upstream(
            client=client,
            url=url,
            headers=headers,
            body=body,
            recorder=recorder,
            prefix=prefix,
        )

    try:
        upstream = await client.post(url, headers=headers, json=body)
    except httpx.HTTPError as exc:
        recorder.end_error(prefix, exc)
        return JSONResponse(
            status_code=502,
            content={"error": {"message": f"upstream unreachable: {exc}", "type": "proxy_error"}},
        )

    try:
        data = upstream.json()
    except json.JSONDecodeError:
        text = upstream.text
        recorder.end_error(prefix, f"non-json response: {text[:2000]}", status_code=upstream.status_code)
        return Response(content=text, status_code=upstream.status_code, media_type="text/plain")

    if upstream.is_success:
        recorder.end_json(prefix, data, status_code=upstream.status_code)
    else:
        recorder.end_error(prefix, data, status_code=upstream.status_code)
    return JSONResponse(content=data, status_code=upstream.status_code)


async def _stream_upstream(
    *,
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    recorder: InteractionRecorder,
    prefix: str,
) -> StreamingResponse:
    # 确保上游按 SSE 返回
    payload = dict(body)
    payload["stream"] = True

    async def event_gen() -> AsyncIterator[bytes]:
        raw_events: list[str] = []
        text_parts: list[str] = []
        status_code = 502
        finished = False
        try:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                status_code = resp.status_code
                if not resp.is_success:
                    err_body = await resp.aread()
                    try:
                        err_json = json.loads(err_body)
                    except json.JSONDecodeError:
                        err_json = err_body.decode("utf-8", errors="replace")
                    recorder.end_error(prefix, err_json, status_code=status_code)
                    finished = True
                    yield err_body
                    return

                async for line in resp.aiter_lines():
                    if line == "":
                        yield b"\n"
                        continue
                    raw_events.append(line)
                    if line.startswith("data: "):
                        data = line[6:]
                        if data.strip() != "[DONE]":
                            try:
                                chunk = json.loads(data)
                                for choice in chunk.get("choices") or []:
                                    delta = choice.get("delta") or {}
                                    # DeepSeek 等 thinking 模型可能只推 reasoning_content
                                    if content := delta.get("content"):
                                        text_parts.append(content)
                                    if reasoning := delta.get("reasoning_content"):
                                        text_parts.append(reasoning)
                            except json.JSONDecodeError:
                                pass
                    yield (line + "\n").encode("utf-8")
            recorder.end_stream(
                prefix,
                text="".join(text_parts),
                raw_events=raw_events,
                status_code=status_code,
            )
            finished = True
        except httpx.HTTPError as exc:
            recorder.end_error(prefix, exc, status_code=status_code)
            finished = True
            raise
        finally:
            # 客户端提前断开时也要落盘，避免只有 ask 没有 answer
            if not finished:
                recorder.end_stream(
                    prefix,
                    text="".join(text_parts),
                    raw_events=raw_events,
                    status_code=status_code,
                )

    return StreamingResponse(event_gen(), media_type="text/event-stream")


async def proxy_models(
    *,
    client: httpx.AsyncClient,
    settings: Settings,
    request: Request,
) -> Response:
    url = f"{settings.normalized_upstream_base()}/models"
    headers = upstream_headers(settings, request)
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
