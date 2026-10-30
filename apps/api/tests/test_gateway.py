from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from winnie_api.main import create_app
from winnie_api.settings import RouteConfig, Settings

OPENAI_URL = "/fake-openai/v1/chat/completions"
ANTHROPIC_URL = "/fake-anthropic/v1/messages"


async def test_healthz(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body


async def test_routes_lists_configured_routes(client: httpx.AsyncClient) -> None:
    response = await client.get("/routes")
    assert response.status_code == 200
    routes = response.json()["routes"]
    assert [r["name"] for r in routes] == ["fake-anthropic", "fake-openai"]
    assert routes[0]["protocol"] == "anthropic"
    assert routes[1]["paths"] == [
        "/fake-openai/v1/chat/completions",
        "/fake-openai/v1/models",
    ]


async def test_unknown_route_returns_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/missing/v1/models")
    assert response.status_code == 404
    assert "unknown route" in response.json()["detail"]


async def test_wrong_protocol_returns_404(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/fake-openai/v1/messages",
        json={"model": "m", "max_tokens": 8, "messages": []},
    )
    assert response.status_code == 404
    assert "not 'anthropic'" in response.json()["detail"]


async def test_openai_rejects_invalid_json(client: httpx.AsyncClient) -> None:
    response = await client.post(
        OPENAI_URL,
        headers={"content-type": "application/json"},
        content=b"{not json",
    )
    assert response.status_code == 400
    assert "invalid json" in response.json()["error"]["message"]


async def test_openai_requires_messages(client: httpx.AsyncClient) -> None:
    response = await client.post(OPENAI_URL, json={"model": "m"})
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "messages is required"


async def test_openai_forwards_request_and_records_pair(
    client: httpx.AsyncClient,
    upstream,
    settings: Settings,
) -> None:
    response = await client.post(
        OPENAI_URL,
        headers={"Authorization": "Bearer client-key"},
        json={"model": "fake-model", "messages": [{"role": "user", "content": "ping"}]},
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "pong"

    forwarded = upstream.requests[-1]
    assert str(forwarded.url) == "https://fake.openai/v1/chat/completions"
    assert forwarded.headers["authorization"] == "Bearer client-key"

    route_dir = settings.resolve_log_root() / "fake-openai"
    ask_files = sorted(route_dir.glob("*-ask.txt"))
    answer_files = sorted(route_dir.glob("*-answer.txt"))
    assert len(ask_files) == 1
    assert len(answer_files) == 1

    ask = ask_files[0].read_text(encoding="utf-8")
    assert "client_url:" in ask
    assert "upstream_url: https://fake.openai/v1/chat/completions" in ask
    assert "has_authorization" in ask
    assert "client-key" not in ask

    answer = answer_files[0].read_text(encoding="utf-8")
    assert '"status_code": 200' in answer
    assert '"content": "pong"' in answer


async def test_openai_stream_passthrough(client: httpx.AsyncClient, settings: Settings) -> None:
    response = await client.post(
        OPENAI_URL,
        json={
            "model": "fake-model",
            "stream": True,
            "messages": [{"role": "user", "content": "ping"}],
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "pong" in response.text
    assert "data: [DONE]" in response.text

    answer_files = sorted((settings.resolve_log_root() / "fake-openai").glob("*-answer.txt"))
    answer = answer_files[0].read_text(encoding="utf-8")
    assert '"stream": true' in answer
    assert '"text": "pong"' in answer


async def test_anthropic_forwards_headers_and_version(
    client: httpx.AsyncClient,
    upstream,
) -> None:
    response = await client.post(
        ANTHROPIC_URL,
        headers={"x-api-key": "anthropic-key"},
        json={
            "model": "fake-model",
            "max_tokens": 8,
            "messages": [{"role": "user", "content": "ping"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["content"][0]["text"] == "pong"

    forwarded = upstream.requests[-1]
    assert str(forwarded.url) == "https://fake.anthropic/v1/messages"
    assert forwarded.headers["x-api-key"] == "anthropic-key"
    assert forwarded.headers["anthropic-version"] == "2023-06-01"


async def test_anthropic_requires_max_tokens(client: httpx.AsyncClient) -> None:
    response = await client.post(
        ANTHROPIC_URL,
        json={"model": "m", "messages": [{"role": "user", "content": "ping"}]},
    )
    assert response.status_code == 400
    assert "max_tokens" in response.json()["error"]["message"]


async def test_anthropic_stream_passthrough(client: httpx.AsyncClient) -> None:
    response = await client.post(
        ANTHROPIC_URL,
        json={
            "model": "fake-model",
            "max_tokens": 8,
            "stream": True,
            "messages": [{"role": "user", "content": "ping"}],
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "pong" in response.text


async def test_models_endpoints(client: httpx.AsyncClient) -> None:
    openai = await client.get("/fake-openai/v1/models")
    assert openai.status_code == 200
    assert openai.json()["data"][0]["id"] == "fake-openai-model"

    anthropic = await client.get("/fake-anthropic/v1/models")
    assert anthropic.status_code == 200
    assert anthropic.json()["data"][0]["id"] == "fake-anthropic-model"


async def test_upstream_error_is_passed_through(
    client: httpx.AsyncClient,
    upstream,
    settings: Settings,
) -> None:
    upstream.error_response = httpx.Response(
        429,
        json={"error": {"message": "rate limited", "type": "rate_limit_error"}},
    )
    response = await client.post(
        OPENAI_URL,
        json={"model": "m", "messages": [{"role": "user", "content": "ping"}]},
    )
    assert response.status_code == 429
    assert response.json()["error"]["message"] == "rate limited"

    answer_files = sorted((settings.resolve_log_root() / "fake-openai").glob("*-answer.txt"))
    assert '"status_code": 429' in answer_files[0].read_text(encoding="utf-8")


async def test_proxy_auth_is_enforced_when_configured(upstream) -> None:
    settings = Settings(
        log_root="../../data/logs",
        proxy_api_key="secret",
        routes={
            "fake-openai": RouteConfig(
                protocol="openai", base_url="https://fake.openai"
            )
        },
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream), timeout=5.0)
    app = create_app(settings=settings, http=http)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as auth_client:
            missing = await auth_client.get("/fake-openai/v1/models")
            assert missing.status_code == 401

            allowed = await auth_client.get(
                "/fake-openai/v1/models",
                headers={"Authorization": "Bearer secret"},
            )
            assert allowed.status_code == 200
    await http.aclose()


def test_settings_reject_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"unknown_key": True})

    with pytest.raises(ValidationError):
        Settings.model_validate(
            {
                "routes": {
                    "x": {
                        "protocol": "openai",
                        "base_url": "https://example.com",
                        "typo": True,
                    }
                }
            }
        )
