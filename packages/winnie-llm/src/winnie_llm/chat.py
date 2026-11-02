"""OpenAI-compatible chat access.

This is the only chat client in the repository. Capabilities (assistant,
meetings, news) receive a resolved client or a small protocol and never build an
HTTP request to a model vendor themselves.

The same client serves single-shot completions and tool-calling turns; tool
support is opt-in, so a request without tools is byte-for-byte what it always
was.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from winnie_core.types import Usage
from winnie_core.usage.event import UsageEvent
from winnie_core.usage.ledger import UsageSink
from winnie_core.usage.tokens import parse_usage

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class LLMCompletion:
    text: str
    model: str
    usage: Usage


@dataclass(frozen=True)
class ToolCall:
    """One function call requested by the model.

    ``arguments`` is the raw JSON string the provider returned; parsing it is
    the caller's job so a malformed payload becomes a visible tool error rather
    than a crash inside the client.
    """

    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ChatResult:
    """A full assistant turn, including any tool calls it requested.

    ``message`` is the provider's assistant message, replayed verbatim when the
    tool results are sent back. ``text`` is empty when the turn is tool-only.
    """

    text: str
    tool_calls: tuple[ToolCall, ...]
    model: str
    usage: Usage
    message: dict[str, Any]


@dataclass(frozen=True)
class StreamPart:
    """One step of a streamed turn.

    Exactly one of the two fields is set. ``text`` is a fragment as the provider
    produced it; ``result`` is the assembled turn and always comes last, so the
    caller ends up with the same object ``chat`` would have returned.
    """

    text: str = ""
    result: ChatResult | None = None


class ChatClient:
    """Call a ``/chat/completions`` endpoint.

    Transient transport errors and retryable HTTP statuses are retried with
    exponential backoff. Usage recording never changes the call's result.

    ``stream`` is the same call with ``stream: true``: it yields text as the
    provider produces it and ends with the same ``ChatResult`` that ``chat``
    returns, so a caller can switch without a second code path for tool calls,
    usage or the replayed assistant message.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 120.0,
        max_output_tokens: int = 4096,
        temperature: float = 0.2,
        max_retries: int = 2,
        retry_backoff_seconds: float = 1.0,
        http: httpx.AsyncClient | None = None,
        usage: UsageSink | None = None,
        capability: str = "",
        provider: str = "",
    ) -> None:
        if not base_url or not api_key or not model or model == "fake":
            raise ValueError(
                "base_url, model and the API key environment variable must be "
                "set for an openai-compatible provider"
            )
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.http = http
        self.usage = usage
        self.capability = capability
        self.provider = provider

    async def complete(self, messages: list[dict[str, Any]]) -> LLMCompletion:
        result = await self.chat(messages)
        return LLMCompletion(
            text=result.text,
            model=result.model,
            usage=result.usage,
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        """Return one assistant turn, including any tool calls it requested."""
        started = time.perf_counter()
        client = self.http or httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_seconds, connect=30.0)
        )
        try:
            body = await self._post_with_retries(client, messages, tools)
        except Exception as exc:
            self._record(
                started,
                provider_model=self.model,
                error=exc,
                tools=tools,
            )
            raise
        finally:
            if self.http is None:
                await client.aclose()

        text = _message_text_or_none(body)
        tool_calls = _tool_calls(body)
        if not text and not tool_calls:
            self._record(
                started,
                provider_model=str(body.get("model") or self.model),
                error=RuntimeError("LLM response contained no text content"),
                tools=tools,
            )
            raise RuntimeError("LLM response contained no text content")
        self._record(
            started,
            provider_model=str(body.get("model") or self.model),
            usage=body.get("usage"),
            tools=tools,
            tool_calls=len(tool_calls),
        )
        return ChatResult(
            text=text,
            tool_calls=tool_calls,
            model=str(body.get("model") or self.model),
            usage=_usage(body),
            message=_assistant_message(body),
        )

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamPart]:
        """Yield the assistant turn as it is produced, then the whole result.

        The wire format is server-sent events: ``data: {json}`` per chunk and a
        final ``data: [DONE]``. Text arrives in ``choices[0].delta.content``;
        tool calls arrive as fragments keyed by ``index`` and have to be joined
        before they are JSON. ``stream_options.include_usage`` asks for the usage
        chunk, which some providers omit — the ledger records what came back and
        treats a missing count as unknown rather than as zero.

        There is deliberately no retry here. A retry after the first delta would
        replay text the owner has already read, so a transport failure mid-stream
        is reported and the turn fails; the caller starts a new one.
        """
        started = time.perf_counter()
        client = self.http or httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_seconds, connect=30.0)
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools
        pieces: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        usage: Any = None
        provider_model = self.model
        try:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread())[:300]
                    raise RuntimeError(
                        "LLM request failed: "
                        f"HTTP {response.status_code}: {body.decode('utf-8', 'replace')}"
                    )
                async for line in response.aiter_lines():
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        logger.warning("ignoring a malformed stream chunk: %r", data[:120])
                        continue
                    provider_model = str(chunk.get("model") or provider_model)
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            pieces.append(str(piece))
                            yield StreamPart(text=str(piece))
                        for call in delta.get("tool_calls") or []:
                            index = int(call.get("index") or 0)
                            slot = calls.setdefault(
                                index, {"id": "", "name": "", "arguments": ""}
                            )
                            if call.get("id"):
                                slot["id"] = str(call["id"])
                            function = call.get("function") or {}
                            # A name is sent whole; opinions differ on whether it
                            # can be split, so append only the first one and let
                            # arguments accumulate fragment by fragment.
                            if function.get("name") and not slot["name"]:
                                slot["name"] = str(function["name"])
                            if function.get("arguments"):
                                slot["arguments"] += str(function["arguments"])
        except Exception as exc:
            self._record(
                started,
                provider_model=provider_model,
                error=exc,
                tools=tools,
            )
            raise
        finally:
            if self.http is None:
                await client.aclose()

        text = "".join(pieces)
        tool_calls = tuple(
            ToolCall(
                id=slot["id"] or f"call_{index}",
                name=slot["name"],
                arguments=slot["arguments"] or "{}",
            )
            for index, slot in sorted(calls.items())
            if slot["name"]
        )
        if not text and not tool_calls:
            self._record(
                started,
                provider_model=provider_model,
                error=RuntimeError("LLM stream contained no text content"),
                tools=tools,
            )
            raise RuntimeError("LLM stream contained no text content")
        message: dict[str, Any] = {"role": "assistant", "content": text}
        if tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in tool_calls
            ]
        self._record(
            started,
            provider_model=provider_model,
            usage=usage,
            tools=tools,
            tool_calls=len(tool_calls),
        )
        yield StreamPart(
            result=ChatResult(
                text=text,
                tool_calls=tool_calls,
                model=provider_model,
                usage=_usage({"usage": usage} if usage else {}),
                message=message,
            )
        )

    def _record(
        self,
        started: float,
        *,
        provider_model: str,
        usage: Any = None,
        error: BaseException | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_calls: int | None = None,
    ) -> None:
        """Append one ledger event. Recording never changes the call's result."""
        if self.usage is None:
            return
        detail: dict[str, Any] = {"tools_offered": bool(tools)}
        if tool_calls is not None:
            detail["tool_calls"] = tool_calls
        error_text = ""
        if error is not None:
            error_text = f"{type(error).__name__}: {error}"[:500]
        event = UsageEvent.create(
            tokens=parse_usage(usage) if usage is not None else None,
            at=datetime.now(timezone.utc),
            capability=self.capability or "internal.llm",
            source="internal",
            provider=self.provider,
            model=provider_model,
            status="error" if error is not None else "ok",
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=error_text,
            detail=detail,
        )
        try:
            self.usage.record(event)
        except Exception:  # noqa: BLE001 - telemetry must not break a call
            logger.exception("failed to record LLM usage")

    async def _post_with_retries(
        self,
        client: httpx.AsyncClient,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
        }
        if tools:
            payload["tools"] = tools
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            retry_after: float | None = None
            try:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                await self._backoff(attempt, reason=type(exc).__name__)
                continue

            if response.status_code in RETRYABLE_STATUS:
                last_error = RuntimeError(
                    "LLM request failed: "
                    f"HTTP {response.status_code}: {response.text[:300]}"
                )
                if attempt >= self.max_retries:
                    break
                retry_after = _retry_after_seconds(response)
                await self._backoff(
                    attempt,
                    reason=f"HTTP {response.status_code}",
                    retry_after=retry_after,
                )
                continue

            if response.status_code >= 400:
                raise RuntimeError(
                    "LLM request failed: "
                    f"HTTP {response.status_code}: {response.text[:300]}"
                )
            return response.json()

        raise RuntimeError(
            f"LLM request failed after {self.max_retries + 1} attempts"
        ) from last_error

    async def _backoff(
        self,
        attempt: int,
        *,
        reason: str,
        retry_after: float | None = None,
    ) -> None:
        delay = self.retry_backoff_seconds * (2**attempt)
        if retry_after is not None:
            delay = max(delay, retry_after)
        delay = min(delay, 30.0)
        logger.warning(
            "LLM request failed (%s); retry %s/%s in %.1fs",
            reason,
            attempt + 1,
            self.max_retries,
            delay,
        )
        if delay:
            await asyncio.sleep(delay)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return min(max(float(value), 0.0), 30.0)
    except ValueError:
        return None


def _first_message(body: dict[str, Any]) -> dict[str, Any]:
    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError("LLM response contained no choices")
    return choices[0].get("message") or {}


def _message_text_or_none(body: dict[str, Any]) -> str:
    message = _first_message(body)
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        parts = [
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") in (None, "text")
        ]
        text = "".join(parts).strip()
        if text:
            return text
    return ""


def _tool_calls(body: dict[str, Any]) -> tuple[ToolCall, ...]:
    message = _first_message(body)
    calls: list[ToolCall] = []
    for index, raw in enumerate(message.get("tool_calls") or []):
        if not isinstance(raw, dict):
            continue
        function = raw.get("function") or {}
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        arguments = function.get("arguments")
        if not isinstance(arguments, str):
            arguments = "{}" if arguments is None else str(arguments)
        calls.append(
            ToolCall(
                id=str(raw.get("id") or f"call_{index}"),
                name=name,
                arguments=arguments,
            )
        )
    return tuple(calls)


def _assistant_message(body: dict[str, Any]) -> dict[str, Any]:
    message = _first_message(body)
    replay: dict[str, Any] = {
        "role": "assistant",
        "content": message.get("content"),
    }
    if message.get("tool_calls"):
        replay["tool_calls"] = message["tool_calls"]
    return replay


def _usage(body: dict[str, Any]) -> Usage:
    usage = body.get("usage") or {}
    quantity = usage.get("total_tokens")
    if quantity is None:
        quantity = int(usage.get("prompt_tokens") or 0) + int(
            usage.get("completion_tokens") or 0
        )
    return Usage(
        provider="openai-compatible",
        unit="token",
        quantity=float(quantity or 0),
    )
