"""The thinking switch, the 402 signal, and the usage a failed call still owes.

Written after a bill of 4.23 CNY for two hours of a news batch: 22 calls came
back HTTP 200 with an empty content because the supplier's default thinking mode
had spent the whole output budget, and the ledger stored them as free calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from winnie_core.usage.event import UsageEvent
from winnie_llm import (
    ApiKeys,
    ChatClient,
    LLMInsufficientBalance,
    ProviderRegistry,
)

V5 = """\
version: 5
vendors:
- id: deepseek
  label: DeepSeek
  models:
  - id: deepseek-flash
integrations:
- name: deepseek-internal
  vendor: deepseek
  kind: openai
  base_url: https://api.deepseek.com
  api_key_env: DEEPSEEK_API_KEY
  default_model: deepseek-flash
  enabled: true
  gateway: false
bindings:
  assistant.chat:
    vendor: deepseek
    model: deepseek-flash
  news.extract:
    vendor: deepseek
    model: deepseek-flash
  news.write:
    vendor: deepseek
    model: deepseek-flash
  news.review:
    vendor: deepseek
    model: deepseek-flash
"""


class _CollectingSink:
    def __init__(self) -> None:
        self.events: list[UsageEvent] = []

    def record(self, event: UsageEvent) -> None:
        self.events.append(event)


def _registry(tmp_path: Path) -> ProviderRegistry:
    path = tmp_path / "ai.yaml"
    path.write_text(V5, encoding="utf-8")
    keys = tmp_path / ".env"
    keys.write_text("DEEPSEEK_API_KEY=secret\n", encoding="utf-8")
    registry = ProviderRegistry(path, keys=ApiKeys.load(keys, environ={}))
    registry.load()
    return registry


def _client(handler: Any, **kwargs: Any) -> ChatClient:
    kwargs.setdefault("max_retries", 0)
    return ChatClient(
        base_url="https://api.deepseek.com/v1",
        api_key="secret",
        model="deepseek-flash",
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


async def test_news_features_turn_thinking_off_and_the_assistant_stays_silent(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)

    for feature in ("news.extract", "news.write", "news.review"):
        assert registry.chat_for(feature).thinking is False, feature

    # None means "send nothing": an undeclared capability must keep the request
    # body it always had, because a supplier that is not DeepSeek may reject a
    # field it does not recognise.
    assert registry.chat_for("assistant.chat").thinking is None


@pytest.mark.asyncio
async def test_a_disabled_thinking_switch_is_sent_explicitly() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
            },
        )

    client = _client(handler, thinking=False)
    await client.complete([{"role": "user", "content": "ping"}])

    assert seen[0]["thinking"] == {"type": "disabled"}

    # The switch is stated, never inferred, and an undeclared capability sends
    # nothing at all: DeepSeek thinks unless told not to, while a supplier that
    # is not DeepSeek may reject a field it does not know.
    other: list[dict[str, Any]] = []

    def handler2(request: httpx.Request) -> httpx.Response:
        other.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
            },
        )

    await _client(handler2).complete([{"role": "user", "content": "ping"}])
    assert "thinking" not in other[0]


@pytest.mark.asyncio
async def test_an_empty_200_still_records_the_tokens_it_was_billed_for() -> None:
    """The bug that hid 0.39 CNY: a 200 with no text is not a free call."""
    sink = _CollectingSink()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                # A thinking model that never left its chain of thought: the
                # content is empty and every token went to reasoning.
                "choices": [
                    {"message": {"role": "assistant", "content": ""},
                     "finish_reason": "length"}
                ],
                "usage": {
                    "prompt_tokens": 2400,
                    "completion_tokens": 4096,
                    "total_tokens": 6496,
                },
            },
        )

    client = _client(handler, usage=sink, capability="news.write")
    with pytest.raises(RuntimeError, match="no text content"):
        await client.complete([{"role": "user", "content": "write"}])

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.status == "error"
    assert event.output_tokens == 4096
    assert event.input_tokens == 2400
    assert event.capability == "news.write"


@pytest.mark.asyncio
async def test_insufficient_balance_is_its_own_error_and_is_not_retried() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(
            402,
            json={"error": {"message": "Insufficient Balance"}},
        )

    client = _client(handler, max_retries=2)
    with pytest.raises(LLMInsufficientBalance, match="balance is exhausted"):
        await client.complete([{"role": "user", "content": "write"}])

    # Retrying a 402 would only spend the same refusal again.
    assert len(attempts) == 1
