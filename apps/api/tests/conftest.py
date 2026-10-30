from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from winnie_api.main import create_app
from winnie_api.settings import (
    DatabaseConfig,
    JobsConfig,
    MeetingsConfig,
    RouteConfig,
    Settings,
)


class FakeUpstream:
    """Record requests and return deterministic OpenAI/Anthropic responses."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.error_response: httpx.Response | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error_response is not None:
            return self.error_response
        path = request.url.path
        is_anthropic = request.url.host == "fake.anthropic"

        if path.endswith("/models"):
            if is_anthropic:
                return httpx.Response(
                    200,
                    json={
                        "data": [{"id": "fake-anthropic-model", "type": "model"}],
                        "has_more": False,
                    },
                )
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [{"id": "fake-openai-model", "object": "model"}],
                },
            )

        if path.endswith("/chat/completions"):
            payload = json.loads(request.content)
            if payload.get("stream"):
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=(
                        b'data: {"choices":[{"delta":{"content":"pong"}}]}\n\n'
                        b"data: [DONE]\n\n"
                    ),
                )
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-fake",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "pong"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )

        if path.endswith("/messages"):
            payload = json.loads(request.content)
            if payload.get("stream"):
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=(
                        b"event: content_block_delta\n"
                        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"pong"}}\n\n'
                        b"event: message_stop\n"
                        b'data: {"type":"message_stop"}\n\n'
                    ),
                )
            return httpx.Response(
                200,
                json={
                    "id": "msg_fake",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "pong"}],
                },
            )

        return httpx.Response(404, json={"error": "unexpected upstream path"})


@pytest.fixture
def upstream() -> FakeUpstream:
    return FakeUpstream()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        log_root=str(tmp_path / "logs"),
        anthropic_version="2023-06-01",
        routes={
            "fake-openai": RouteConfig(
                protocol="openai", base_url="https://fake.openai"
            ),
            "fake-anthropic": RouteConfig(
                protocol="anthropic", base_url="https://fake.anthropic"
            ),
        },
    )


@pytest_asyncio.fixture
async def client(settings: Settings, upstream: FakeUpstream) -> AsyncIterator[httpx.AsyncClient]:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(upstream),
        timeout=httpx.Timeout(5.0),
    )
    app = create_app(settings=settings, http=http)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as test_client:
            yield test_client
    await http.aclose()


@pytest.fixture
def meeting_settings(tmp_path: Path) -> Settings:
    return Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
        meetings=MeetingsConfig(
            enabled=True,
            default_user_id="local",
            local_root=str(tmp_path / "meetings"),
            notes_root=str(tmp_path / "notes"),
            max_upload_mb=1,
        ),
        database=DatabaseConfig(
            url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
            auto_create_tables=True,
        ),
        jobs=JobsConfig(backend="inline"),
    )


@pytest_asyncio.fixture
async def meeting_client(
    meeting_settings: Settings,
) -> AsyncIterator[httpx.AsyncClient]:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        timeout=httpx.Timeout(5.0),
    )
    app = create_app(settings=meeting_settings, http=http)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as test_client:
            yield test_client
    await http.aclose()
