"""共享：上游 JSON / SSE 转发与日志兜底。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
from fastapi import Response
from fastapi.responses import JSONResponse, StreamingResponse

from winnie_api.record import InteractionRecorder

TextExtractor = Callable[[dict[str, Any]], str | None]


async def forward_json_post(
    *,
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    recorder: InteractionRecorder,
    prefix: str,
    error_envelope: Callable[[str], dict[str, Any]],
) -> Response:
    try:
        upstream = await client.post(url, headers=headers, json=body)
    except httpx.HTTPError as exc:
        recorder.end_error(prefix, exc)
        return JSONResponse(status_code=502, content=error_envelope(f"upstream unreachable: {exc}"))

    try:
        data = upstream.json()
    except json.JSONDecodeError:
        text = upstream.text
        recorder.end_error(
            prefix, f"non-json response: {text[:2000]}", status_code=upstream.status_code
        )
        return Response(content=text, status_code=upstream.status_code, media_type="text/plain")

    if upstream.is_success:
        recorder.end_json(prefix, data, status_code=upstream.status_code)
    else:
        recorder.end_error(prefix, data, status_code=upstream.status_code)
    return JSONResponse(content=data, status_code=upstream.status_code)


def streaming_sse_response(
    *,
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    recorder: InteractionRecorder,
    prefix: str,
    extract_text: TextExtractor,
) -> StreamingResponse:
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
                        if data.strip() not in ("", "[DONE]"):
                            try:
                                chunk = json.loads(data)
                                if piece := extract_text(chunk):
                                    text_parts.append(piece)
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
            if not finished:
                recorder.end_stream(
                    prefix,
                    text="".join(text_parts),
                    raw_events=raw_events,
                    status_code=status_code,
                )

    return StreamingResponse(event_gen(), media_type="text/event-stream")
