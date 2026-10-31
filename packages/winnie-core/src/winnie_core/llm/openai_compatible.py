"""Minimal OpenAI-compatible chat client for internal jobs.

The same client serves two callers: single-shot completions (translation,
minutes) and tool-calling turns (the assistant). Tool support is opt-in: when
no tools are passed, the request body is identical to what it always was.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from winnie_core.llm.base import ChatResult, LLMCompletion, ToolCall
from winnie_core.types import Usage
from winnie_core.usage.event import UsageEvent
from winnie_core.usage.ledger import UsageSink
from winnie_core.usage.tokens import parse_usage

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class OpenAICompatibleLLM:
    """Call a `/chat/completions` endpoint directly, never through the gateway.

    This keeps internal jobs independent from the transparent proxy's request
    logging and from client-supplied vendor keys. Transient transport errors and
    retryable HTTP statuses are retried with exponential backoff.
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
                "llm.base_url, llm.model and the API key environment variable "
                "must be set for openai_compatible providers"
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
