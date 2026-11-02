from __future__ import annotations

import asyncio
import json
import re

import httpx
import pytest

from winnie_llm import ChatClient
from winnie_meetings.integrations.summarize.base import TranscriptLine, TranscriptView
from winnie_meetings.integrations.summarize.llm import LLMSummarizer
from winnie_meetings.integrations.translate.llm import LLMTranslator


def _llm(
    handler,
    *,
    model: str = "test-model",
    **kwargs,
) -> ChatClient:
    return ChatClient(
        base_url="https://llm.example/v1",
        api_key="secret",
        model=model,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


async def test_translator_preserves_order_and_records_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer secret"
        payload = json.loads(request.content)
        assert payload["model"] == "test-model"
        assert payload["max_tokens"] == 4096
        lines = json.loads(payload["messages"][1]["content"])["lines"]
        assert lines == ["Hello", "World"]
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [
                    {
                        "message": {
                            "content": "```json\n[\"你好\", \"世界\"]\n```"
                        }
                    }
                ],
                "usage": {"total_tokens": 42},
            },
        )

    translator = LLMTranslator(_llm(handler))
    result = await translator.translate(["Hello", "World"], source="en", target="zh")

    assert result.texts == ["你好", "世界"]
    assert result.usage is not None
    assert result.usage.provider == "openai-compatible"
    assert result.usage.unit == "token"
    assert result.usage.quantity == 42


async def test_translator_batches_without_reordering() -> None:
    calls: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        lines = json.loads(payload["messages"][1]["content"])["lines"]
        calls.append(lines)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps([f"T:{x}" for x in lines])}}
                ],
                "usage": {"total_tokens": 10},
            },
        )

    translator = LLMTranslator(_llm(handler), max_batch_chars=10)
    result = await translator.translate(
        ["aaaa", "bbbb", "cccc"], source=None, target="zh"
    )

    assert calls == [["aaaa", "bbbb"], ["cccc"]]
    assert result.texts == ["T:aaaa", "T:bbbb", "T:cccc"]
    assert result.usage is not None
    assert result.usage.quantity == 20


async def test_translator_rejects_wrong_count() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '["only one"]'}}],
                "usage": {"total_tokens": 1},
            },
        )

    translator = LLMTranslator(_llm(handler))
    with pytest.raises(RuntimeError, match="items for 2 input lines"):
        await translator.translate(["a", "b"], source=None, target="zh")


async def test_summarizer_parses_structured_minutes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert "Meeting title: Weekly sync" in payload["messages"][1]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                "```json\n"
                                + json.dumps(
                                    {
                                        "summary": "团队确认了发布范围。",
                                        "topics": [
                                            {
                                                "title": "发布范围",
                                                "points": ["先做文件转写"],
                                            }
                                        ],
                                        "decisions": [
                                            {
                                                "text": "本周只做文件转写。",
                                                "context": "范围讨论",
                                            }
                                        ],
                                        "action_items": [
                                            {
                                                "text": "完成联调",
                                                "owner": "alice",
                                                "due_date": None,
                                            }
                                        ],
                                        "highlights": [
                                            {"segment_seq": 1, "text": "先做文件转写"},
                                            {"segment_seq": 99, "text": "不存在"},
                                        ],
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n```"
                            )
                        }
                    }
                ],
                "usage": {"total_tokens": 88},
            },
        )

    llm = ChatClient(
        base_url="https://llm.example/v1",
        api_key="secret",
        model="test-model",
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    summarizer = LLMSummarizer(llm)
    draft = await summarizer.summarize(
        TranscriptView(
            meeting_title="Weekly sync",
            local_language="zh",
            lines=[
                TranscriptLine(
                    seq=1,
                    text="先做文件转写",
                    translated_text=None,
                    detected_language="zh",
                )
            ],
        )
    )

    assert draft.summary == "团队确认了发布范围。"
    assert draft.topics[0].title == "发布范围"
    assert draft.decisions[0].text == "本周只做文件转写。"
    assert draft.action_items[0].owner == "alice"
    assert [item.segment_seq for item in draft.highlights] == [1]
    assert draft.model == "test-model"
    assert draft.usage is not None
    assert draft.usage.quantity == 88


async def test_summarizer_rejects_empty_summary() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"summary": ""}'}}],
                "usage": {"total_tokens": 1},
            },
        )

    summarizer = LLMSummarizer(_llm(handler))
    with pytest.raises(RuntimeError, match="empty summary"):
        await summarizer.summarize(
            TranscriptView(
                meeting_title="Empty",
                local_language="zh",
                lines=[
                    TranscriptLine(
                        seq=1,
                        text="hello",
                        translated_text=None,
                        detected_language="en",
                    )
                ],
            )
        )


async def test_transient_http_error_is_retried(monkeypatch) -> None:
    calls = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, text="busy")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '["ok"]'}}],
                "usage": {"total_tokens": 2},
            },
        )

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    translator = LLMTranslator(
        _llm(handler, max_retries=1, retry_backoff_seconds=0.5)
    )
    result = await translator.translate(["a"], source=None, target="zh")

    assert calls == 2
    assert delays == [0.5]
    assert result.texts == ["ok"]


async def test_client_error_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, text="bad request")

    translator = LLMTranslator(_llm(handler, max_retries=2))
    with pytest.raises(RuntimeError, match="HTTP 400"):
        await translator.translate(["a"], source=None, target="zh")
    assert calls == 1


async def test_transport_error_exhausts_retries() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("boom", request=request)

    translator = LLMTranslator(
        _llm(handler, max_retries=1, retry_backoff_seconds=0)
    )
    with pytest.raises(RuntimeError, match="after 2 attempts"):
        await translator.translate(["a"], source=None, target="zh")
    assert calls == 2


async def test_summarizer_chunks_long_transcripts_and_merges_usage() -> None:
    calls: list[tuple[str, list[int]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        system = payload["messages"][0]["content"]
        user = payload["messages"][1]["content"]
        if "merge partial" in system:
            calls.append(("reduce", []))
            content = json.dumps(
                {
                    "summary": "merged summary",
                    "topics": [{"title": "Merged", "points": ["p"]}],
                    "decisions": [],
                    "action_items": [],
                    "highlights": [{"segment_seq": 1, "text": "line 1"}],
                }
            )
        else:
            seqs = [int(value) for value in re.findall(r"^\[(\d+)\]", user, re.M)]
            calls.append(("map", seqs))
            content = json.dumps(
                {
                    "summary": f"partial {seqs[0]}",
                    "topics": [{"title": f"topic {seqs[0]}", "points": []}],
                    "decisions": [],
                    "action_items": [],
                    "highlights": [
                        {"segment_seq": seqs[0], "text": f"line {seqs[0]}"}
                    ],
                }
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"total_tokens": 10},
            },
        )

    lines = [
        TranscriptLine(
            seq=seq,
            text="x" * 60,
            translated_text=None,
            detected_language="en",
        )
        for seq in range(1, 5)
    ]
    summarizer = LLMSummarizer(_llm(handler), max_chunk_chars=150)
    draft = await summarizer.summarize(
        TranscriptView(meeting_title="Long", local_language="zh", lines=lines)
    )

    assert calls == [
        ("map", [1]),
        ("map", [2]),
        ("map", [3]),
        ("map", [4]),
        ("reduce", []),
    ]
    assert draft.summary == "merged summary"
    assert draft.usage is not None
    assert draft.usage.quantity == 50


def _sse(events: list[dict]) -> bytes:
    lines = [f"data: {json.dumps(event)}\n\n" for event in events]
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


async def test_stream_assembles_text_and_reports_usage() -> None:
    """Provider deltas become fragments, and the turn still ends in a result."""
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(
                [
                    {"choices": [{"delta": {"content": "你"}}]},
                    {"choices": [{"delta": {"content": "好"}}]},
                    {
                        "choices": [{"delta": {}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 7, "completion_tokens": 2},
                    },
                ]
            ),
        )

    llm = _llm(handler)
    parts = [part async for part in llm.stream([{"role": "user", "content": "hi"}])]

    assert requests[0]["stream"] is True
    assert [part.text for part in parts if part.result is None] == ["你", "好"]
    result = parts[-1].result
    assert result is not None
    assert result.text == "你好"
    assert result.usage.quantity == 9
    assert result.message == {"role": "assistant", "content": "你好"}
    await llm.http.aclose()


async def test_stream_joins_tool_call_fragments() -> None:
    """Arguments arrive in pieces and the name only once; the result is valid."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(
                [
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call-1",
                                            "function": {"name": "web_search"},
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "function": {"arguments": '{"query":'},
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "function": {"arguments": ' "winnie"}'},
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                ]
            ),
        )

    llm = _llm(handler)
    parts = [part async for part in llm.stream([{"role": "user", "content": "hi"}])]

    result = parts[-1].result
    assert result is not None
    assert result.text == ""
    assert result.tool_calls[0].name == "web_search"
    assert json.loads(result.tool_calls[0].arguments) == {"query": "winnie"}
    assert result.message["tool_calls"][0]["function"]["name"] == "web_search"
    await llm.http.aclose()


async def test_a_stream_that_returns_an_http_error_is_not_a_silent_reply() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    llm = _llm(handler)
    with pytest.raises(RuntimeError, match="HTTP 401"):
        async for _ in llm.stream([{"role": "user", "content": "hi"}]):
            pass
    await llm.http.aclose()
