"""The gateway data plane: Winnie keys in, vendor credential injected, bytes out."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from winnie_api.main import create_app
from winnie_api.settings import RouteConfig, Settings
from winnie_gateway.catalog import ModelCatalog
from winnie_gateway.routes import Route

OPENAI_URL = "/fake-openai/v1/chat/completions"
ANTHROPIC_URL = "/fake-anthropic/v1/messages"

# The platform publishes one name for the model the fake upstream serves, plus
# the two spellings clients still send for it.
PUBLISHED = [
    {
        "id": "deepseek-flash",
        "label": "DeepSeek Flash",
        "aliases": ["deepseek-v4-flash", "deepseek-v4.1-flash"],
        "vendor": "fake",
        "model": "fake-model",
    },
    {
        "id": "slow-chat",
        "vendor": "other",
        "model": "claude-x",
    },
]

CHAT_BODY = {
    "model": "fake-model",
    "messages": [{"role": "user", "content": "ping"}],
}


def _answer_files(root: Path, route: str) -> list[Path]:
    return sorted((root / route).glob("*-answer.txt"))


def _ask_files(root: Path, route: str) -> list[Path]:
    return sorted((root / route).glob("*-ask.txt"))


def _rejected_files(root: Path, route: str) -> list[Path]:
    return sorted((root / route).glob("*-rejected.txt"))


def _record_payload(path: Path) -> dict:
    """The JSON body of a record file, past its ``#`` header lines."""
    _, _, body = path.read_text(encoding="utf-8").partition("\n\n")
    return json.loads(body)


async def test_healthz(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_readyz_reports_disabled_database(client: httpx.AsyncClient) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "disabled",
        "queue": "disabled",
    }


async def test_routes_lists_routes_with_credentials(client: httpx.AsyncClient) -> None:
    response = await client.get("/routes")
    assert response.status_code == 200
    routes = response.json()["routes"]
    assert [route["name"] for route in routes] == ["fake-anthropic", "fake-openai"]
    openai = routes[1]
    assert openai["protocol"] == "openai"
    assert openai["path_prefix"] == "/fake-openai/"
    assert openai["vendor"] == "fake"
    assert openai["api_key_env"] == "FAKE_API_KEY"
    assert openai["credential_present"] is True
    assert "api_key" not in openai


async def test_unknown_route_returns_404(client: httpx.AsyncClient) -> None:
    response = await client.post("/missing/v1/models", json={})
    assert response.status_code == 404
    assert "unknown route" in response.json()["detail"]


async def test_missing_key_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post(OPENAI_URL, json=CHAT_BODY)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    message = response.json()["error"]["message"]
    assert "gateway API key" in message


async def test_missing_key_uses_the_anthropic_error_shape(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(ANTHROPIC_URL, json={"model": "m", "messages": []})
    assert response.status_code == 401
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "authentication_error"


async def test_invalid_and_vendor_keys_are_rejected(
    gateway_client: httpx.AsyncClient,
) -> None:
    invalid = await gateway_client.post(
        OPENAI_URL,
        headers={"Authorization": "Bearer not-a-winnie-key"},
        json=CHAT_BODY,
    )
    assert invalid.status_code == 401
    assert "invalid or revoked" in invalid.json()["error"]["message"]

    vendor = await gateway_client.post(
        OPENAI_URL,
        headers={"Authorization": "Bearer fake-vendor-key"},
        json=CHAT_BODY,
    )
    assert vendor.status_code == 401


async def test_forwards_request_and_injects_the_vendor_key(
    gateway_client: httpx.AsyncClient,
    gateway_app,
    upstream,
    settings: Settings,
) -> None:
    response = await gateway_client.post(
        f"{OPENAI_URL}?trace=1",
        headers={
            "X-Client-Trace": "trace-abc",
            "Idempotency-Key": "idem-1",
            "User-Agent": "probe/1.0",
        },
        json=CHAT_BODY,
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "pong"
    assert response.headers["x-upstream-request-id"] == "req-fake-1"

    forwarded = upstream.requests[-1]
    assert str(forwarded.url) == "https://fake.openai/v1/chat/completions?trace=1"
    assert forwarded.headers["authorization"] == "Bearer fake-vendor-key"
    assert "x-api-key" not in forwarded.headers
    assert forwarded.headers["x-client-trace"] == "trace-abc"
    assert forwarded.headers["idempotency-key"] == "idem-1"
    assert forwarded.headers["user-agent"] == "probe/1.0"
    assert json.loads(forwarded.content) == CHAT_BODY

    ask = _ask_files(settings.resolve_log_root(), "fake-openai")[0].read_text(
        encoding="utf-8"
    )
    assert "trace=1" in ask
    assert "key_id" in ask
    assert "fake-vendor-key" not in ask
    assert gateway_app.state.pytest_gateway_secret not in ask

    answer = _answer_files(settings.resolve_log_root(), "fake-openai")[0].read_text(
        encoding="utf-8"
    )
    assert '"status_code": 200' in answer
    assert '"content": "pong"' in answer


async def test_anthropic_key_goes_in_x_api_key(
    gateway_app,
    upstream,
) -> None:
    secret = gateway_app.state.pytest_gateway_secret
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway_app), base_url="http://test"
    ) as client:
        response = await client.post(
            ANTHROPIC_URL,
            headers={"x-api-key": secret},
            json={"model": "m", "max_tokens": 8, "messages": []},
        )

    assert response.status_code == 200
    forwarded = upstream.requests[-1]
    assert forwarded.headers["x-api-key"] == "fake-vendor-key"
    assert "authorization" not in forwarded.headers
    assert forwarded.headers["anthropic-version"] == "2023-06-01"


async def test_a_key_can_be_limited_to_routes(gateway_app, upstream) -> None:
    _, secret = gateway_app.state.gateway_keys.create(
        label="openai only", routes=["fake-openai"]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway_app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {secret}"},
    ) as client:
        allowed = await client.post(OPENAI_URL, json=CHAT_BODY)
        denied = await client.post(ANTHROPIC_URL, json={"model": "m", "max_tokens": 8})

    assert allowed.status_code == 200
    assert denied.status_code == 403
    assert denied.json()["type"] == "error"
    assert "allowed: fake-openai" in denied.json()["error"]["message"]
    assert len(upstream.requests) == 1


@asynccontextmanager
async def _app_with(settings: Settings) -> AsyncIterator:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"ok": True})
        )
    )
    app = create_app(settings=settings, http=http)
    async with app.router.lifespan_context(app):
        yield app
    await http.aclose()


@pytest.mark.asyncio
async def test_a_route_without_a_vendor_key_answers_503(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("MISSING_API_KEY", raising=False)
    settings = Settings(
        log_root=str(tmp_path / "logs"),
        gateway_keys_path=str(tmp_path / "gateway-keys.yaml"),
        routes={
            "ghost": RouteConfig(
                protocol="openai", base_url="https://ghost.example", vendor="missing"
            )
        },
    )
    async with _app_with(settings) as app:
        _, secret = app.state.gateway_keys.create(label="test")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {secret}"},
        ) as client:
            response = await client.post("/ghost/v1/chat/completions", json=CHAT_BODY)

    assert response.status_code == 503
    assert "no server credential" in response.json()["error"]["message"]
    assert "Admin -> AI suppliers" in response.json()["error"]["message"]


async def test_upstream_errors_keep_their_status_body_and_headers(
    gateway_client: httpx.AsyncClient,
    upstream,
    settings: Settings,
) -> None:
    upstream.error_response = httpx.Response(
        429,
        json={"error": {"message": "rate limited", "type": "rate_limit_error"}},
        headers={"retry-after": "7", "x-upstream-request-id": "err-1"},
    )
    response = await gateway_client.post(OPENAI_URL, json=CHAT_BODY)

    assert response.status_code == 429
    assert response.json()["error"]["message"] == "rate limited"
    assert response.headers["retry-after"] == "7"
    assert response.headers["x-upstream-request-id"] == "err-1"

    answer = _answer_files(settings.resolve_log_root(), "fake-openai")[0].read_text(
        encoding="utf-8"
    )
    assert '"status_code": 429' in answer


async def test_streaming_bytes_pass_through_unchanged(
    gateway_client: httpx.AsyncClient,
    settings: Settings,
) -> None:
    expected = (
        b'data: {"choices":[{"delta":{"content":"pong"}}]}\n\n'
        b"data: [DONE]\n\n"
    )
    response = await gateway_client.post(
        OPENAI_URL, json={**CHAT_BODY, "stream": True}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.content == expected

    answer = _answer_files(settings.resolve_log_root(), "fake-openai")[0].read_text(
        encoding="utf-8"
    )
    assert '"stream": true' in answer
    assert '"text": "pong"' in answer


async def test_every_path_and_method_is_forwarded(
    gateway_client: httpx.AsyncClient,
    upstream,
) -> None:
    embeddings = await gateway_client.post(
        "/fake-openai/v1/embeddings",
        json={"model": "text-embedding-3-small", "input": "ping"},
    )
    assert embeddings.status_code == 200
    assert embeddings.json()["data"][0]["embedding"] == [0.1, 0.2]
    assert upstream.requests[-1].url.path == "/v1/embeddings"

    deleted = await gateway_client.delete("/fake-openai/v1/files/file-1")
    assert deleted.status_code == 404  # the fake upstream has no such path
    assert upstream.requests[-1].method == "DELETE"
    assert upstream.requests[-1].url.path == "/v1/files/file-1"

    models = await gateway_client.get("/fake-openai/v1/models")
    assert models.status_code == 200
    assert models.json()["data"][0]["id"] == "fake-openai-model"


async def test_the_record_explains_the_whole_call(
    gateway_client: httpx.AsyncClient,
    settings: Settings,
) -> None:
    """Who called, with what, and what came back — all of it on disk."""
    response = await gateway_client.post(
        f"{OPENAI_URL}?trace=1",
        headers={"X-Client-Trace": "trace-abc", "User-Agent": "probe/1.0"},
        json=CHAT_BODY,
    )
    assert response.status_code == 200

    ask = _record_payload(_ask_files(settings.resolve_log_root(), "fake-openai")[0])
    assert ask["client"]["method"] == "POST"
    assert "trace=1" in ask["client"]["url"]
    assert ask["route"] == {
        "name": "fake-openai",
        "protocol": "openai",
        "upstream_url": "https://fake.openai/v1/chat/completions?trace=1",
    }
    assert ask["caller"]["key_id"]
    assert ask["caller"]["credential_header"] == "authorization"
    # Every forwarded header is kept, and the credential is present only as a
    # marker: the record must never become a second copy of the key.
    assert ask["request"]["headers"]["x-client-trace"] == "trace-abc"
    assert ask["request"]["headers"]["user-agent"] == "probe/1.0"
    assert ask["request"]["headers"]["authorization"] == "<redacted>"
    assert ask["request"]["model"] == "fake-model"
    assert ask["request"]["body"] == CHAT_BODY
    assert ask["request"]["body_bytes"] == len(
        json.dumps(CHAT_BODY, separators=(",", ":"))
    )

    answer = _record_payload(
        _answer_files(settings.resolve_log_root(), "fake-openai")[0]
    )
    assert answer["status_code"] == 200
    assert answer["duration_ms"] >= 0
    assert answer["upstream_headers"]["x-upstream-request-id"] == "req-fake-1"
    assert answer["body"]["choices"][0]["message"]["content"] == "pong"


async def test_the_record_keeps_the_token_counts(
    gateway_client: httpx.AsyncClient,
    settings: Settings,
) -> None:
    response = await gateway_client.post(
        "/fake-openai/v1/embeddings",
        json={"model": "text-embedding-3-small", "input": "ping"},
    )
    assert response.status_code == 200

    answer = _record_payload(
        _answer_files(settings.resolve_log_root(), "fake-openai")[0]
    )
    assert answer["tokens"]["input_tokens"] == 4
    assert answer["tokens"]["total_tokens"] == 4


async def test_a_refused_call_is_recorded_without_its_key(
    gateway_client: httpx.AsyncClient,
    settings: Settings,
) -> None:
    """A 401 with nothing on disk is the one failure nobody can debug."""
    presented = "sk-not-a-winnie-key"
    response = await gateway_client.post(
        OPENAI_URL,
        headers={"Authorization": f"Bearer {presented}", "X-Client-Trace": "probe-7"},
        json=CHAT_BODY,
    )
    assert response.status_code == 401

    files = _rejected_files(settings.resolve_log_root(), "fake-openai")
    assert len(files) == 1
    record = _record_payload(files[0])
    assert record["rejection"]["status_code"] == 401
    assert "invalid or revoked" in record["rejection"]["reason"]
    assert record["rejection"]["body"] == CHAT_BODY
    assert record["client"]["headers"]["x-client-trace"] == "probe-7"
    assert record["client"]["headers"]["authorization"] == "<redacted>"
    # The digest identifies the key the client keeps sending; the key itself is
    # never written down.
    assert record["caller"]["presented_key_digest"]
    assert presented not in files[0].read_text(encoding="utf-8")


async def test_an_unknown_route_is_recorded_under_one_bucket(
    client: httpx.AsyncClient,
    settings: Settings,
) -> None:
    response = await client.post("/missing/v1/models", json={})
    assert response.status_code == 404

    root = settings.resolve_log_root()
    files = _rejected_files(root, "_unmapped")
    assert len(files) == 1
    record = _record_payload(files[0])
    assert record["rejection"]["status_code"] == 404
    assert "unknown route" in record["rejection"]["reason"]


async def test_a_callers_route_name_can_not_choose_a_log_directory(
    client: httpx.AsyncClient,
    settings: Settings,
) -> None:
    """The bucket is fixed; a name from the URL never becomes a path."""
    response = await client.post("/%2E%2E/v1/models", json={})
    assert response.status_code in (404, 405)

    root = settings.resolve_log_root().resolve()
    assert not (root.parent / "v1").exists()
    assert _rejected_files(root, "_unmapped")


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

    # The shared-key design is gone; a stale config must not be accepted.
    with pytest.raises(ValidationError):
        Settings.model_validate({"proxy_api_key": "secret"})


# -- published model names -------------------------------------------------


def _publish(app, entries: list[dict] | None = None) -> None:
    """Give the app a platform catalog and a second supplier to reach."""
    other = Route(
        name="fake-other",
        protocol="openai",
        base_url="https://fake.openai",
        vendor="other",
        api_key="other-vendor-key",
    )
    app.state.routes_table["fake-other"] = other
    app.state.vendor_routes["other"] = [other]
    app.state.model_catalog = ModelCatalog.from_entries(entries or PUBLISHED)


async def test_a_model_the_platform_does_not_publish_is_refused(
    gateway_app,
    gateway_client: httpx.AsyncClient,
    upstream,
    settings: Settings,
) -> None:
    """The key is valid; the model is not one the platform offers."""
    _publish(gateway_app)
    response = await gateway_client.post(
        OPENAI_URL, json={"model": "gpt-5.5", "messages": []}
    )

    assert response.status_code == 400
    message = response.json()["error"]["message"]
    assert "not published" in message
    # The message names what is available, so the caller can fix the request.
    assert "deepseek-flash" in message
    assert upstream.requests == []

    record = _record_payload(
        _rejected_files(settings.resolve_log_root(), "fake-openai")[-1]
    )
    assert record["rejection"]["status_code"] == 400
    assert "gpt-5.5" in record["rejection"]["reason"]


async def test_an_alias_is_forwarded_as_the_model_it_names(
    gateway_app,
    gateway_client: httpx.AsyncClient,
    upstream,
    settings: Settings,
) -> None:
    """Two old spellings and one new name all reach the same upstream model."""
    _publish(gateway_app)
    response = await gateway_client.post(
        OPENAI_URL, json={"model": "deepseek-v4.1-flash", "messages": []}
    )

    assert response.status_code == 200
    forwarded = json.loads(upstream.requests[-1].content)
    assert forwarded["model"] == "fake-model"
    assert forwarded["messages"] == []

    ask = _record_payload(
        _ask_files(settings.resolve_log_root(), "fake-openai")[-1]
    )
    assert ask["request"]["requested_model"] == "deepseek-v4.1-flash"
    assert ask["request"]["model"] == "fake-model"


async def test_a_keys_override_sends_a_model_to_another_supplier(
    gateway_app,
    upstream,
) -> None:
    """The same call name, followed by the key's policy, not the platform's."""
    _publish(gateway_app)
    _, secret = gateway_app.state.gateway_keys.create(
        label="pinned", overrides={"deepseek-flash": "other/claude-x"}
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway_app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {secret}"},
    ) as client:
        response = await client.post(
            OPENAI_URL, json={"model": "deepseek-v4-flash", "messages": []}
        )

    assert response.status_code == 200
    forwarded = upstream.requests[-1]
    assert forwarded.headers["authorization"] == "Bearer other-vendor-key"
    assert json.loads(forwarded.content)["model"] == "claude-x"


async def test_without_a_catalog_the_model_travels_unchanged(
    gateway_client: httpx.AsyncClient,
    upstream,
) -> None:
    """A gateway-only deployment has no registry; it must keep working."""
    response = await gateway_client.post(
        OPENAI_URL, json={"model": "anything-goes", "messages": []}
    )

    assert response.status_code == 200
    assert json.loads(upstream.requests[-1].content)["model"] == "anything-goes"



async def test_a_long_prompt_does_not_hide_an_unpublished_model(
    gateway_app,
    gateway_client: httpx.AsyncClient,
    upstream,
) -> None:
    """The gate reads the whole body; the record's 8 KB snapshot is not the gate.

    The recorder truncates what it stores, and a truncated body has no ``model``
    field at all. Resolving from it would let an unpublished model through
    whenever the prompt happened to be long enough.
    """
    _publish(gateway_app)
    response = await gateway_client.post(
        OPENAI_URL,
        json={
            "model": "gpt-5.5",
            "messages": [{"role": "user", "content": "x" * 20_000}],
        },
    )

    assert response.status_code == 400
    assert "not published" in response.json()["error"]["message"]
    assert upstream.requests == []
