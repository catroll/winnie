"""共享：上游 JSON / SSE 转发与日志兜底。"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import Response
from fastapi.responses import JSONResponse, StreamingResponse

from winnie_core.usage.tokens import TokenCounts, merge_tokens
from winnie_gateway.record import InteractionRecorder

logger = logging.getLogger(__name__)

TextExtractor = Callable[[dict[str, Any]], str | None]
TokenExtractor = Callable[[dict[str, Any]], TokenCounts]
OutcomeObserver = Callable[["UpstreamOutcome"], None]


@dataclass(frozen=True)
class UpstreamOutcome:
    """What one upstream call did, for the usage ledger."""

    status_code: int | None
    latency_ms: int
    tokens: TokenCounts | None = None
    error: str = ""

    @property
    def status(self) -> str:
        if self.error:
            return "error"
        if self.status_code is None or not 200 <= self.status_code < 300:
            return "error"
        return "ok"


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _notify(observer: OutcomeObserver | None, outcome: UpstreamOutcome) -> None:
    if observer is None:
        return
    try:
        observer(outcome)
    except Exception:  # noqa: BLE001 - telemetry must not break the proxy
        logger.exception("usage observer failed")


def _error_summary(payload: Any) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(payload)
    return text[:300]


async def forward_json_post(
    *,
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    recorder: InteractionRecorder,
    prefix: str,
    error_envelope: Callable[[str], dict[str, Any]],
    extract_tokens: TokenExtractor | None = None,
    on_outcome: OutcomeObserver | None = None,
) -> Response:
    started = time.perf_counter()
    try:
        upstream = await client.post(url, headers=headers, json=body)
    except httpx.HTTPError as exc:
        recorder.end_error(prefix, exc)
        _notify(
            on_outcome,
            UpstreamOutcome(None, _elapsed_ms(started), None, f"upstream unreachable: {exc}"),
        )
        return JSONResponse(status_code=502, content=error_envelope(f"upstream unreachable: {exc}"))

    try:
        data = upstream.json()
    except json.JSONDecodeError:
        text = upstream.text
        recorder.end_error(
            prefix, f"non-json response: {text[:2000]}", status_code=upstream.status_code
        )
        _notify(
            on_outcome,
            UpstreamOutcome(
                upstream.status_code,
                _elapsed_ms(started),
                None,
                f"non-json response: {text[:200]}",
            ),
        )
        return Response(content=text, status_code=upstream.status_code, media_type="text/plain")

    if upstream.is_success:
        recorder.end_json(prefix, data, status_code=upstream.status_code)
        tokens = extract_tokens(data) if extract_tokens is not None else None
        error = ""
    else:
        recorder.end_error(prefix, data, status_code=upstream.status_code)
        tokens = None
        error = f"upstream returned HTTP {upstream.status_code}: {_error_summary(data)}"
    _notify(
        on_outcome,
        UpstreamOutcome(
            upstream.status_code, _elapsed_ms(started), tokens, error
        ),
    )
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
    extract_tokens: TokenExtractor | None = None,
    on_outcome: OutcomeObserver | None = None,
) -> StreamingResponse:
    payload = dict(body)
    payload["stream"] = True

    async def event_gen() -> AsyncIterator[bytes]:
        started = time.perf_counter()
        raw_events: list[str] = []
        text_parts: list[str] = []
        status_code = 502
        finished = False
        recorded = False
        tokens = TokenCounts()

        def finish(*, error: str = "") -> None:
            nonlocal recorded
            if recorded:
                return
            recorded = True
            _notify(
                on_outcome,
                UpstreamOutcome(
                    status_code,
                    _elapsed_ms(started),
                    tokens if tokens.reported else None,
                    error,
                ),
            )

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
                    finish(error=f"upstream returned HTTP {status_code}: {_error_summary(err_json)}")
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
                                if extract_tokens is not None:
                                    tokens = merge_tokens(tokens, extract_tokens(chunk))
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
            finish()
        except httpx.HTTPError as exc:
            recorder.end_error(prefix, exc, status_code=status_code)
            finished = True
            finish(error=f"upstream error: {exc}")
            raise
        finally:
            if not finished:
                recorder.end_stream(
                    prefix,
                    text="".join(text_parts),
                    raw_events=raw_events,
                    status_code=status_code,
                )
                finish(error="stream ended before completion")

    return StreamingResponse(event_gen(), media_type="text/event-stream")
