from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from winnie_api.main import create_app
from winnie_api.settings import DatabaseConfig, Settings
from winnie_assistant.config import (
    AssistantConfig,
    IndexConfig,
    MemoryConfig,
    SearchConfig,
    GoldConfig,
    TasksConfig,
    WeatherConfig,
)
from winnie_core.config import AuthConfig, JobsConfig
from winnie_gateway.config import RouteConfig
from winnie_mail.config import MailConfig
from winnie_meetings.config import MeetingsConfig
from winnie_phone.config import PhoneConfig, PhoneRetentionConfig
from winnie_push.config import PushConfig


@pytest.fixture(autouse=True)
def isolate_llm_environment(monkeypatch, tmp_path: Path) -> None:
    """Never let this checkout's real ``winnie-llm/.env`` reach a test.

    A test that needs a key sets ``<VENDOR>_API_KEY`` through ``monkeypatch``.
    """
    monkeypatch.setenv("WINNIE_LLM_ENV_FILE", str(tmp_path / "winnie-llm.env"))


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
                headers={"x-upstream-request-id": "req-fake-1"},
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

        if path.endswith("/embeddings"):
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [{"object": "embedding", "embedding": [0.1, 0.2]}],
                    "usage": {"prompt_tokens": 4, "total_tokens": 4},
                },
            )

        return httpx.Response(404, json={"error": "unexpected upstream path"})


@pytest.fixture
def upstream() -> FakeUpstream:
    return FakeUpstream()


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    # The gateway injects a server credential, so a route needs a vendor and
    # that vendor needs a key in the winnie-llm environment.
    monkeypatch.setenv("FAKE_API_KEY", "fake-vendor-key")
    return Settings(
        log_root=str(tmp_path / "logs"),
        gateway_keys_path=str(tmp_path / "gateway-keys.yaml"),
        anthropic_version="2023-06-01",
        routes={
            "fake-openai": RouteConfig(
                protocol="openai", base_url="https://fake.openai", vendor="fake"
            ),
            "fake-anthropic": RouteConfig(
                protocol="anthropic",
                base_url="https://fake.anthropic",
                vendor="fake",
            ),
        },
    )


@pytest_asyncio.fixture
async def gateway_app(settings: Settings, upstream: FakeUpstream):
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(upstream),
        timeout=httpx.Timeout(5.0),
    )
    app = create_app(settings=settings, http=http)
    async with app.router.lifespan_context(app):
        # A key that may call every route, plus its secret for tests that need
        # to present it in a non-default header.
        _, secret = app.state.gateway_keys.create(label="pytest")
        app.state.pytest_gateway_secret = secret
        yield app
    await http.aclose()


@pytest_asyncio.fixture
async def client(gateway_app) -> AsyncIterator[httpx.AsyncClient]:
    """Unauthenticated client: exercises the 401 path of the data plane."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway_app), base_url="http://test"
    ) as test_client:
        yield test_client


@pytest_asyncio.fixture
async def gateway_client(gateway_app) -> AsyncIterator[httpx.AsyncClient]:
    """Client carrying a valid Winnie key for every route."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway_app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {gateway_app.state.pytest_gateway_secret}"},
    ) as test_client:
        yield test_client


@pytest.fixture
def meeting_settings(tmp_path: Path) -> Settings:
    return Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
        # These fixtures describe a host with only the capability under test, so
        # every other default-enabled capability is switched off explicitly.
        mail=MailConfig(enabled=False),
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


@pytest.fixture
def auth_settings(tmp_path: Path) -> Settings:
    return Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
        auth=AuthConfig(enabled=True, allow_registration=False),
        database=DatabaseConfig(
            url=f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}",
            auto_create_tables=True,
        ),
        jobs=JobsConfig(backend="inline"),
    )


@pytest.fixture
def account_settings(tmp_path: Path) -> Settings:
    return Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
        auth=AuthConfig(enabled=True, allow_registration=True),
        meetings=MeetingsConfig(
            enabled=True,
            local_root=str(tmp_path / "meetings"),
            notes_root=str(tmp_path / "notes"),
            max_upload_mb=1,
        ),
        database=DatabaseConfig(
            url=f"sqlite+aiosqlite:///{tmp_path / 'account.db'}",
            auto_create_tables=True,
        ),
        jobs=JobsConfig(backend="inline"),
    )


@pytest_asyncio.fixture
async def auth_client(
    auth_settings: Settings,
) -> AsyncIterator[httpx.AsyncClient]:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        timeout=httpx.Timeout(5.0),
    )
    app = create_app(settings=auth_settings, http=http)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as test_client:
            yield test_client
    await http.aclose()


@pytest_asyncio.fixture
async def account_client(
    account_settings: Settings,
) -> AsyncIterator[httpx.AsyncClient]:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        timeout=httpx.Timeout(5.0),
    )
    app = create_app(settings=account_settings, http=http)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as test_client:
            yield test_client
    await http.aclose()


@pytest.fixture
def assistant_settings(tmp_path: Path) -> Settings:
    return Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
        mail=MailConfig(enabled=False),
        assistant=AssistantConfig(
            enabled=True,
            root=str(tmp_path / "assistant"),
            timezone="Asia/Shanghai",
            search=SearchConfig(provider="fake"),
            index=IndexConfig(enabled=False),
            memory=MemoryConfig(auto_distill=False),
            tasks=TasksConfig(enabled=True, tick_seconds=60),
            weather=WeatherConfig(enabled=False),
            gold=GoldConfig(enabled=False),
        ),
    )


@pytest_asyncio.fixture
async def assistant_client(
    assistant_settings: Settings,
) -> AsyncIterator[httpx.AsyncClient]:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        timeout=httpx.Timeout(5.0),
    )
    app = create_app(settings=assistant_settings, http=http)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as test_client:
            yield test_client
    await http.aclose()


@pytest.fixture
def phone_settings(tmp_path: Path) -> Settings:
    """Phone sync with accounts off, so every request is the local user."""
    return Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
        phone=PhoneConfig(
            enabled=True,
            sync_interval_minutes=30,
            max_batch_items=5,
            max_text_chars=200,
            retention=PhoneRetentionConfig(message_days=365),
        ),
        database=DatabaseConfig(
            url=f"sqlite+aiosqlite:///{tmp_path / 'phone.db'}",
            auto_create_tables=True,
        ),
        jobs=JobsConfig(backend="inline"),
    )


@pytest_asyncio.fixture
async def phone_client(phone_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        timeout=httpx.Timeout(5.0),
    )
    app = create_app(settings=phone_settings, http=http)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as test_client:
            yield test_client
    await http.aclose()


@pytest.fixture
def push_settings(tmp_path: Path) -> Settings:
    """Push enabled with accounts off, so the local user owns everything."""
    return Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
        push=PushConfig(
            enabled=True,
            heartbeat_seconds=5,
            retention_days=30,
        ),
        database=DatabaseConfig(
            url=f"sqlite+aiosqlite:///{tmp_path / 'push.db'}",
            auto_create_tables=True,
        ),
        jobs=JobsConfig(backend="inline"),
    )


@pytest_asyncio.fixture
async def push_client(push_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        timeout=httpx.Timeout(5.0),
    )
    app = create_app(settings=push_settings, http=http)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as test_client:
            yield test_client
    await http.aclose()
