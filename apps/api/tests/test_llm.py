from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from winnie_api.integrations.factory import build_summarizer, build_translator
from winnie_api.integrations.llm.openai_compatible import OpenAICompatibleLLM
from winnie_api.integrations.summarize.base import TranscriptLine, TranscriptView
from winnie_api.integrations.summarize.llm import LLMSummarizer
from winnie_api.integrations.translate.llm import LLMTranslator
from winnie_api.settings import LLMConfig, MeetingsConfig, Settings


def _llm(
    handler,
    *,
    model: str = "test-model",
    **kwargs,
) -> OpenAICompatibleLLM:
    return OpenAICompatibleLLM(
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

    llm = OpenAICompatibleLLM(
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


def test_factory_selects_llm_adapters_and_requires_key(monkeypatch) -> None:
    settings = Settings(
        meetings=MeetingsConfig(
            translator="openai_compatible",
            summarizer="openai_compatible",
        ),
        llm=LLMConfig(
            base_url="https://llm.example/v1",
            api_key_env="WINNIE_TEST_LLM_KEY",
            model="test-model",
        ),
    )
    monkeypatch.setenv("WINNIE_TEST_LLM_KEY", "secret")
    assert isinstance(build_translator(settings), LLMTranslator)
    assert isinstance(build_summarizer(settings), LLMSummarizer)

    monkeypatch.delenv("WINNIE_TEST_LLM_KEY")
    with pytest.raises(RuntimeError, match="WINNIE_TEST_LLM_KEY"):
        build_translator(settings)


async def test_pipeline_uses_llm_adapters(meeting_settings, monkeypatch) -> None:
    settings = meeting_settings.model_copy(
        update={
            "meetings": meeting_settings.meetings.model_copy(
                update={
                    "translator": "openai_compatible",
                    "summarizer": "openai_compatible",
                }
            ),
            "llm": LLMConfig(
                base_url="https://llm.example/v1",
                api_key_env="WINNIE_TEST_LLM_KEY",
                model="test-model",
            ),
        }
    )
    monkeypatch.setenv("WINNIE_TEST_LLM_KEY", "secret")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        system = payload["messages"][0]["content"]
        if "interpreter" in system:
            lines = json.loads(payload["messages"][1]["content"])["lines"]
            content = json.dumps([f"EN:{line}" for line in lines])
        else:
            content = json.dumps(
                {
                    "summary": "LLM summary",
                    "topics": [{"title": "Scope", "points": ["file first"]}],
                    "decisions": [],
                    "action_items": [],
                    "highlights": [{"segment_seq": 1, "text": "大家好"}],
                }
            )
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": content}}],
                "usage": {"total_tokens": 30},
            },
        )

    llm_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def fake_build_llm(_settings):
        return OpenAICompatibleLLM(
            base_url="https://llm.example/v1",
            api_key="secret",
            model="test-model",
            http=llm_http,
        )

    monkeypatch.setattr(
        "winnie_api.integrations.factory.build_llm", fake_build_llm
    )

    gateway_http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500))
    )
    from winnie_api.main import create_app

    app = create_app(settings=settings, http=gateway_http)
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                created = await client.post(
                    "/api/v1/meetings", json={"title": "Pipeline"}
                )
                assert created.status_code == 201, created.text
                meeting_id = created.json()["id"]

                upload = await client.post(
                    f"/api/v1/meetings/{meeting_id}/audio",
                    files={"file": ("a.m4a", b"fake audio", "audio/mp4")},
                )
                assert upload.status_code == 200, upload.text

                processed = await client.post(
                    f"/api/v1/meetings/{meeting_id}/transcribe"
                )
                assert processed.status_code == 202, processed.text
                assert processed.json()["status"] == "ready", processed.text

                transcript = await client.get(
                    f"/api/v1/meetings/{meeting_id}/transcript"
                )
                assert transcript.status_code == 200, transcript.text
                translated = [
                    segment["translated_text"]
                    for segment in transcript.json()["segments"]
                    if segment["translated_text"]
                ]
                assert translated and all(item.startswith("EN:") for item in translated)

                minutes = await client.get(
                    f"/api/v1/meetings/{meeting_id}/minutes"
                )
                assert minutes.status_code == 200, minutes.text
                assert minutes.json()["summary"] == "LLM summary"
                assert minutes.json()["model"] == "test-model"
    finally:
        await llm_http.aclose()
        await gateway_http.aclose()


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
