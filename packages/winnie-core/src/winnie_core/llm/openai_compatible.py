"""Minimal OpenAI-compatible chat client for internal jobs."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from winnie_core.llm.base import LLMCompletion
from winnie_core.types import Usage

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class OpenAICompatibleLLM:
    """Call a `/chat/completions` endpoint directly, never through the gateway.

    This keeps internal meeting jobs independent from the transparent proxy's
    request logging and from client-supplied vendor keys. Transient transport
    errors and retryable HTTP statuses are retried with exponential backoff.
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

    async def complete(self, messages: list[dict[str, str]]) -> LLMCompletion:
        client = self.http or httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_seconds, connect=30.0)
        )
        try:
            body = await self._post_with_retries(client, messages)
        finally:
            if self.http is None:
                await client.aclose()

        text = _message_text(body)
        usage = body.get("usage") or {}
        quantity = usage.get("total_tokens")
        if quantity is None:
            quantity = int(usage.get("prompt_tokens") or 0) + int(
                usage.get("completion_tokens") or 0
            )
        return LLMCompletion(
            text=text,
            model=str(body.get("model") or self.model),
            usage=Usage(
                provider="openai-compatible",
                unit="token",
                quantity=float(quantity or 0),
            ),
        )

    async def _post_with_retries(
        self,
        client: httpx.AsyncClient,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
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
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": self.temperature,
                        "max_tokens": self.max_output_tokens,
                    },
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


def _message_text(body: dict[str, Any]) -> str:
    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError("LLM response contained no choices")
    message = choices[0].get("message") or {}
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
    raise RuntimeError("LLM response contained no text content")
