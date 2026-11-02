"""Byte-level forwarding for the model gateway.

The previous implementation parsed the request, dispatched on the path to a
handler, and rebuilt the response. It therefore supported exactly the three
paths it had handlers for, dropped query strings and unknown headers, and lost
upstream response headers. This module forwards every path and method as-is.

Only three things are taken over:

* the credential — the caller's Winnie key is removed and the route's vendor key
  is injected;
* the model name — the caller asks for a platform model, and the body is handed
  to this module already rewritten to the supplier's own model id (the
  resolution itself lives in :mod:`winnie_gateway.router` and
  :mod:`winnie_gateway.catalog`);
* observability — the request and the response are copied into the ask/answer
  log and the usage ledger, without altering the bytes that are forwarded;
* framing headers — hop-by-hop headers, ``content-length`` and
  ``content-encoding`` are recomputed by this side. The client's
  ``accept-encoding`` is dropped and the body is re-sent decoded, so a body can
  always be parsed for usage and a compressed body is never mislabelled.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from winnie_core.usage.ledger import UsageSink
from winnie_core.usage.tokens import TokenCounts, merge_tokens
from winnie_gateway.keystore import GatewayKey
from winnie_gateway.record import InteractionRecorder, client_request_url
from winnie_gateway.routes import Route
from winnie_gateway.usage import (
    UpstreamOutcome,
    extract_json_usage,
    extract_stream_usage,
    usage_observer,
)

logger = logging.getLogger(__name__)

TextExtractor = Callable[[dict[str, Any]], str | None]
OutcomeObserver = Callable[["UpstreamOutcome"], None]

# Headers that only describe one connection. Forwarding them would make this
# proxy assert something about a socket it does not own.
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)

# Credentials stay at this side: the caller's key authenticates the gateway and
# the vendor key is injected, so neither may travel on. ``accept-encoding`` is
# dropped because the body is re-sent decoded (see the module docstring).
_DROPPED_REQUEST_HEADERS = _HOP_BY_HOP | frozenset(
    {
        "accept-encoding",
        "authorization",
        "content-length",
        "cookie",
        "host",
        "openai-organization",
        "openai-project",
        "x-api-key",
        "x-winnie-key",
    }
)

_DROPPED_RESPONSE_HEADERS = _HOP_BY_HOP | frozenset(
    {"content-encoding", "content-length"}
)

_MAX_LOGGED_BODY = 50_000


def error_response(
    protocol: str,
    status_code: int,
    message: str,
    *,
    error_type: str = "api_error",
) -> JSONResponse:
    """An error body in the shape the route's clients already parse."""
    if protocol == "anthropic":
        content: dict[str, Any] = {
            "type": "error",
            "error": {"type": error_type, "message": message},
        }
    else:
        content = {"error": {"message": message, "type": error_type}}
    headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
    return JSONResponse(content=content, status_code=status_code, headers=headers)


def _extract_openai_text(chunk: dict[str, Any]) -> str | None:
    parts: list[str] = []
    for choice in chunk.get("choices") or []:
        delta = choice.get("delta") or {}
        if content := delta.get("content"):
            parts.append(content)
        if reasoning := delta.get("reasoning_content"):
            parts.append(reasoning)
    return "".join(parts) or None


def _extract_anthropic_text(chunk: dict[str, Any]) -> str | None:
    if chunk.get("type") != "content_block_delta":
        return None
    delta = chunk.get("delta") or {}
    kind = delta.get("type")
    if kind == "text_delta" and (text := delta.get("text")):
        return text
    if kind == "thinking_delta" and (thinking := delta.get("thinking")):
        return thinking
    if kind == "input_json_delta" and (partial := delta.get("partial_json")):
        return partial
    return None


def text_extractor(protocol: str) -> TextExtractor:
    return _extract_anthropic_text if protocol == "anthropic" else _extract_openai_text


def _parse_json(payload: bytes) -> Any | None:
    try:
        return json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _text(payload: bytes) -> str:
    return payload[:_MAX_LOGGED_BODY].decode("utf-8", errors="replace")


def _request_headers(
    request: Request, route: Route, default_anthropic_version: str
) -> dict[str, str]:
    headers = {
        name.lower(): value
        for name, value in request.headers.items()
        if name.lower() not in _DROPPED_REQUEST_HEADERS
    }
    if route.protocol == "anthropic":
        headers["x-api-key"] = route.api_key
        headers.setdefault("anthropic-version", default_anthropic_version)
    else:
        headers["authorization"] = f"Bearer {route.api_key}"
    return headers


def _response_headers(upstream: httpx.Response) -> dict[str, str]:
    return {
        name.lower(): value
        for name, value in upstream.headers.multi_items()
        if name.lower() not in _DROPPED_RESPONSE_HEADERS
    }


def _upstream_url(request: Request, route: Route, path: str) -> str:
    url = route.upstream_url(path)
    query = request.url.query
    return f"{url}?{query}" if query else url


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _notify(observer: OutcomeObserver | None, outcome: UpstreamOutcome) -> None:
    if observer is None:
        return
    try:
        observer(outcome)
    except Exception:  # noqa: BLE001 - telemetry must not break the proxy
        logger.exception("usage observer failed")


def _summary(payload: Any) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(payload)
    return text[:300]


class _SseTee:
    """Split forwarded bytes into SSE lines without changing them."""

    def __init__(self, protocol: str) -> None:
        self._extract = text_extractor(protocol)
        self._buffer = b""
        self._text: list[str] = []
        self._events: list[str] = []
        self.tokens = TokenCounts()

    def feed(self, chunk: bytes) -> None:
        self._buffer += chunk
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            self._line(line.rstrip(b"\r"))

    def flush(self) -> None:
        if self._buffer:
            self._line(self._buffer.rstrip(b"\r"))
            self._buffer = b""

    def _line(self, line: bytes) -> None:
        if not line:
            return
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError:
            return
        self._events.append(text)
        if text.startswith("data: "):
            data = text[6:]
        elif text.startswith("data:"):
            data = text[5:]
        else:
            return
        data = data.strip()
        if not data or data == "[DONE]":
            return
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            return
        if not isinstance(chunk, dict):
            return
        if piece := self._extract(chunk):
            self._text.append(piece)
        self.tokens = merge_tokens(self.tokens, extract_stream_usage(chunk))

    @property
    def text(self) -> str:
        return "".join(self._text)

    @property
    def events(self) -> list[str]:
        return self._events


async def forward(
    *,
    request: Request,
    route: Route,
    path: str,
    body: bytes,
    default_anthropic_version: str,
    client: httpx.AsyncClient,
    recorder: InteractionRecorder,
    key: GatewayKey,
    credential_header: str,
    usage: UsageSink | None,
    requested_model: str = "",
) -> Response:
    """Forward one call to ``route`` and return the upstream's own answer.

    ``body`` is handed in already read, because the caller may have had to
    resolve and rewrite the model name in it before any byte was sent upstream.
    Everything else about the call still travels unchanged.
    """
    parsed = _parse_json(body)
    url = _upstream_url(request, route, path)
    model = str(parsed.get("model") or "") if isinstance(parsed, dict) else ""
    stream_requested = bool(
        isinstance(parsed, dict) and parsed.get("stream")
    )
    prefix = recorder.begin(
        client_url=client_request_url(request),
        upstream_url=url,
        method=request.method,
        body=(
            parsed
            if parsed is not None
            else {"raw": _text(body)}
        ),
        route=route.name,
        protocol=route.protocol,
        key_id=key.id,
        key_label=key.label,
        credential_header=credential_header,
        model=model,
        requested_model=requested_model,
        stream=stream_requested,
        client_host=request.client.host if request.client else "",
        headers=request.headers.items(),
        body_bytes=len(body),
    )
    observer = usage_observer(
        usage=usage,
        route=route,
        model=model,
        protocol=route.protocol,
        stream=stream_requested,
        endpoint=f"/{path.lstrip('/')}",
        key=key,
        requested_model=requested_model,
    )
    started = time.perf_counter()
    try:
        upstream_request = client.build_request(
            request.method,
            url,
            headers=_request_headers(request, route, default_anthropic_version),
            content=body,
        )
        upstream = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        recorder.end_error(prefix, exc, duration_ms=_elapsed_ms(started))
        _notify(
            observer,
            UpstreamOutcome(None, _elapsed_ms(started), None, f"upstream unreachable: {exc}"),
        )
        return error_response(
            route.protocol,
            502,
            f"upstream unreachable: {exc}",
        )

    content_type = upstream.headers.get("content-type", "")
    streaming = content_type.startswith("text/event-stream") and upstream.status_code < 400
    if streaming:
        return _streaming_response(
            upstream=upstream,
            route=route,
            recorder=recorder,
            prefix=prefix,
            observer=observer,
            started=started,
        )
    return await _buffered_response(
        upstream=upstream,
        route=route,
        recorder=recorder,
        prefix=prefix,
        observer=observer,
        started=started,
    )


async def _buffered_response(
    *,
    upstream: httpx.Response,
    route: Route,
    recorder: InteractionRecorder,
    prefix: str,
    observer: OutcomeObserver | None,
    started: float,
) -> Response:
    try:
        payload = await upstream.aread()
    except httpx.HTTPError as exc:
        recorder.end_error(prefix, exc, status_code=upstream.status_code)
        _notify(
            observer,
            UpstreamOutcome(upstream.status_code, _elapsed_ms(started), None, f"upstream error: {exc}"),
        )
        return error_response(route.protocol, 502, f"upstream error: {exc}")
    finally:
        await upstream.aclose()

    status = upstream.status_code
    document = _parse_json(payload)
    tokens: TokenCounts | None = None
    error = ""
    headers = _response_headers(upstream)
    elapsed = _elapsed_ms(started)
    if document is not None:
        if status >= 400:
            error = f"upstream returned HTTP {status}: {_summary(document)}"
            recorder.end_error(
                prefix,
                f"upstream returned HTTP {status}",
                status_code=status,
                duration_ms=elapsed,
                upstream_headers=headers,
                upstream_body=document,
            )
        else:
            tokens = extract_json_usage(document)
            recorder.end_json(
                prefix,
                document,
                status_code=status,
                duration_ms=elapsed,
                upstream_headers=headers,
                tokens=tokens,
            )
    else:
        text = _text(payload)
        if status >= 400:
            error = f"upstream returned HTTP {status}: {text[:300]}"
            recorder.end_error(
                prefix,
                text[:2000] or f"upstream returned HTTP {status} with an empty body",
                status_code=status,
                duration_ms=elapsed,
                upstream_headers=headers,
                upstream_body={"raw": text[:2000]},
            )
        else:
            recorder.end_json(
                prefix,
                {"raw": text},
                status_code=status,
                duration_ms=elapsed,
                upstream_headers=headers,
            )
    _notify(
        observer,
        UpstreamOutcome(status, _elapsed_ms(started), tokens if status < 400 else None, error),
    )
    return Response(
        content=payload,
        status_code=status,
        headers=headers,
    )


def _streaming_response(
    *,
    upstream: httpx.Response,
    route: Route,
    recorder: InteractionRecorder,
    prefix: str,
    observer: OutcomeObserver | None,
    started: float,
) -> StreamingResponse:
    status = upstream.status_code
    tee = _SseTee(route.protocol)
    headers = _response_headers(upstream)

    async def event_gen():
        finished = False
        recorded = False

        def finish(*, error: str = "") -> None:
            nonlocal recorded
            if recorded:
                return
            recorded = True
            _notify(
                observer,
                UpstreamOutcome(
                    status,
                    _elapsed_ms(started),
                    tee.tokens if tee.tokens.reported else None,
                    error,
                ),
            )

        try:
            async for chunk in upstream.aiter_bytes():
                tee.feed(chunk)
                yield chunk
            tee.flush()
            recorder.end_stream(
                prefix,
                text=tee.text,
                raw_events=tee.events,
                status_code=status,
                duration_ms=_elapsed_ms(started),
                upstream_headers=headers,
                tokens=tee.tokens if tee.tokens.reported else None,
            )
            finished = True
            finish()
        except httpx.HTTPError as exc:
            recorder.end_error(
                prefix,
                exc,
                status_code=status,
                duration_ms=_elapsed_ms(started),
                upstream_headers=headers,
            )
            finished = True
            finish(error=f"upstream error: {exc}")
            raise
        finally:
            if not finished:
                recorder.end_stream(
                    prefix,
                    text=tee.text,
                    raw_events=tee.events,
                    status_code=status,
                    duration_ms=_elapsed_ms(started),
                    upstream_headers=headers,
                    tokens=tee.tokens if tee.tokens.reported else None,
                )
                finish(error="stream ended before completion")
            await upstream.aclose()

    return StreamingResponse(
        event_gen(),
        status_code=status,
        headers=headers,
    )
