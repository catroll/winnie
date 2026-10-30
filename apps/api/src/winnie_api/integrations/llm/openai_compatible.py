"""Minimal OpenAI-compatible chat client for internal jobs."""

from __future__ import annotations

from typing import Any

import httpx

from winnie_api.integrations.llm.base import LLMCompletion
from winnie_api.integrations.types import Usage


class OpenAICompatibleLLM:
    """Call a `/chat/completions` endpoint directly, never through the gateway.

    This keeps internal meeting jobs independent from the transparent proxy's
    request logging and from client-supplied vendor keys.
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
        self.http = http

    async def complete(self, messages: list[dict[str, str]]) -> LLMCompletion:
        client = self.http or httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_seconds, connect=30.0)
        )
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
            if response.status_code >= 400:
                raise RuntimeError(
                    "LLM request failed: "
                    f"HTTP {response.status_code}: {response.text[:300]}"
                )
            body = response.json()
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
