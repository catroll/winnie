from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx

from winnie_api.main import create_app
from winnie_api.settings import AIConfig, DatabaseConfig, Settings
from winnie_assistant.config import (
    AssistantConfig,
    IndexConfig,
    MemoryConfig,
    SearchConfig,
    TasksConfig,
)
from winnie_core.config import JobsConfig
from winnie_gateway.config import RouteConfig
from winnie_meetings.config import MeetingsConfig


@asynccontextmanager
async def _open(settings: Settings, handler):
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        timeout=httpx.Timeout(5.0),
    )
    app = create_app(settings=settings, http=http)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    await http.aclose()


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(
        log_root=str(tmp_path / "logs"),
        routes={
            "openai": RouteConfig(
                protocol="openai", base_url="https://api.openai.com"
            ),
            "anthropic": RouteConfig(
                protocol="anthropic", base_url="https://api.anthropic.com"
            ),
        },
        ai=AIConfig(enabled=True, path=str(tmp_path / "ai.yaml")),
        **overrides,
    )


async def test_config_seeds_routes_features_and_the_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    async with _open(settings, lambda request: httpx.Response(500)) as client:
        response = await client.get("/api/v1/ai/config")
        assert response.status_code == 200, response.text
        body = response.json()

    names = {item["name"] for item in body["integrations"]}
    assert {"openai", "anthropic", "fake"} <= names
    assert body["path"].endswith("ai.yaml")
    assert (tmp_path / "ai.yaml").is_file()

    features = {item["key"]: item for item in body["features"]}
    assert set(features) == {
        "assistant.chat",
        "meetings.transcribe",
        "meetings.translate",
        "meetings.summarize",
    }
    # The default assistant model is fake, so the seed binds the fake provider.
    assert features["assistant.chat"]["integration_name"] == "fake"
    assert features["assistant.chat"]["source"] == "admin"

    kinds = {item["kind"] for item in body["kinds"]}
    assert kinds == {"openai", "anthropic", "volcengine_asr", "fake"}


async def test_integration_crud_drives_the_gateway_route_table(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    async with _open(settings, lambda request: httpx.Response(500)) as client:
        payload = {
            "name": "my-llm",
            "label": "My LLM",
            "kind": "openai",
            "base_url": "https://llm.example.com",
            "api_key_env": "MY_LLM_KEY",
            "default_model": "model-1",
            "enabled": True,
        }
        created = await client.post("/api/v1/ai/integrations", json=payload)
        assert created.status_code == 201, created.text
        integration_id = created.json()["integration"]["id"]

        routes = await client.get("/routes")
        assert "my-llm" in [route["name"] for route in routes.json()["routes"]]

        payload["enabled"] = False
        updated = await client.put(
            f"/api/v1/ai/integrations/{integration_id}", json=payload
        )
        assert updated.status_code == 200, updated.text
        routes = await client.get("/routes")
        assert "my-llm" not in [route["name"] for route in routes.json()["routes"]]

        payload["enabled"] = True
        payload["name"] = "renamed"
        await client.put(f"/api/v1/ai/integrations/{integration_id}", json=payload)
        routes = await client.get("/routes")
        names = [route["name"] for route in routes.json()["routes"]]
        assert "renamed" in names and "my-llm" not in names

        duplicate = await client.post(
            "/api/v1/ai/integrations", json={**payload, "name": "openai"}
        )
        assert duplicate.status_code == 409
        assert "already exists" in duplicate.json()["detail"]

        deleted = await client.delete(f"/api/v1/ai/integrations/{integration_id}")
        assert deleted.status_code == 200, deleted.text
        routes = await client.get("/routes")
        assert "renamed" not in [route["name"] for route in routes.json()["routes"]]

        missing = await client.delete(f"/api/v1/ai/integrations/{integration_id}")
        assert missing.status_code == 404


async def test_feature_binding_validates_kind_model_and_delete(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    async with _open(settings, lambda request: httpx.Response(500)) as client:
        created = await client.post(
            "/api/v1/ai/integrations",
            json={
                "name": "work-llm",
                "kind": "openai",
                "base_url": "https://llm.example.com",
                "api_key_env": "WORK_LLM_KEY",
                "default_model": "model-1",
            },
        )
        assert created.status_code == 201, created.text
        integration_id = created.json()["integration"]["id"]

        bound = await client.put(
            "/api/v1/ai/features/assistant.chat",
            json={"integration_id": integration_id, "model": ""},
        )
        assert bound.status_code == 200, bound.text
        assert bound.json()["binding"]["model"] == "model-1"

        config = (await client.get("/api/v1/ai/config")).json()
        feature = next(
            item for item in config["features"] if item["key"] == "assistant.chat"
        )
        assert feature["source"] == "admin"
        assert feature["integration_name"] == "work-llm"
        assert feature["model"] == "model-1"

        wrong_kind = await client.put(
            "/api/v1/ai/features/meetings.transcribe",
            json={"integration_id": integration_id},
        )
        assert wrong_kind.status_code == 400
        assert "cannot use" in wrong_kind.json()["detail"]

        no_model = await client.post(
            "/api/v1/ai/integrations",
            json={
                "name": "no-model",
                "kind": "openai",
                "base_url": "https://llm.example.com",
                "api_key_env": "NO_MODEL_KEY",
            },
        )
        no_model_id = no_model.json()["integration"]["id"]
        rejected = await client.put(
            "/api/v1/ai/features/assistant.chat",
            json={"integration_id": no_model_id},
        )
        assert rejected.status_code == 400
        assert "model" in rejected.json()["detail"]

        unknown = await client.put(
            "/api/v1/ai/features/does.not.exist", json={}
        )
        assert unknown.status_code == 404

        deleted = await client.delete(
            f"/api/v1/ai/integrations/{integration_id}"
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["cleared_features"] == ["assistant.chat"]

        config = (await client.get("/api/v1/ai/config")).json()
        feature = next(
            item for item in config["features"] if item["key"] == "assistant.chat"
        )
        assert feature["source"] == "none"
        assert feature["integration_id"] is None


async def test_invalid_integration_is_rejected(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    async with _open(settings, lambda request: httpx.Response(500)) as client:
        reserved = await client.post(
            "/api/v1/ai/integrations",
            json={
                "name": "api",
                "kind": "openai",
                "base_url": "https://llm.example.com",
            },
        )
        assert reserved.status_code == 422

        no_url = await client.post(
            "/api/v1/ai/integrations",
            json={"name": "no-url", "kind": "openai"},
        )
        assert no_url.status_code == 422

        pasted_key = await client.post(
            "/api/v1/ai/integrations",
            json={
                "name": "pasted-key",
                "kind": "openai",
                "base_url": "https://llm.example.com",
                "api_key_env": "sk-pasted-secret",
            },
        )
        assert pasted_key.status_code == 422

        unknown_option = await client.post(
            "/api/v1/ai/integrations",
            json={
                "name": "bad-asr",
                "kind": "volcengine_asr",
                "base_url": "https://openspeech.bytedance.com",
                "options": {"resource_id": "x", "typo": 1},
            },
        )
        assert unknown_option.status_code == 422


# -- provider resolution -------------------------------------------------


def _assistant_settings(tmp_path: Path, ai_path: Path) -> Settings:
    return Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
        ai=AIConfig(enabled=True, path=str(ai_path)),
        assistant=AssistantConfig(
            enabled=True,
            root=str(tmp_path / "assistant"),
            search=SearchConfig(provider="fake"),
            index=IndexConfig(enabled=False),
            memory=MemoryConfig(auto_distill=False),
            tasks=TasksConfig(enabled=False),
        ),
    )


async def test_assistant_chat_uses_the_bound_integration(
    tmp_path: Path,
    monkeypatch,
    upstream,
) -> None:
    monkeypatch.setenv("WINNIE_TEST_LLM_KEY", "secret-key")
    settings = _assistant_settings(tmp_path, tmp_path / "ai.yaml")
    async with _open(settings, upstream) as client:
        seeded = await client.get("/api/v1/ai/config")
        assert seeded.status_code == 200

        created = await client.post(
            "/api/v1/ai/integrations",
            json={
                "name": "mock-llm",
                "kind": "openai",
                "base_url": "https://fake.openai",
                "api_key_env": "WINNIE_TEST_LLM_KEY",
                "default_model": "fake-openai-model",
            },
        )
        assert created.status_code == 201, created.text
        integration_id = created.json()["integration"]["id"]
        bound = await client.put(
            "/api/v1/ai/features/assistant.chat",
            json={"integration_id": integration_id},
        )
        assert bound.status_code == 200, bound.text

        reply = await client.post(
            "/api/v1/assistant/chat", json={"message": "ping"}
        )
        assert reply.status_code == 200, reply.text
        assert reply.json()["reply"] == "pong"

        # Deleting the provider leaves the feature unbound; there is no
        # config.yaml model fallback anymore.
        deleted = await client.delete(
            f"/api/v1/ai/integrations/{integration_id}"
        )
        assert deleted.status_code == 200, deleted.text
        unbound = await client.post(
            "/api/v1/assistant/chat", json={"message": "again"}
        )
        assert unbound.status_code == 502, unbound.text
        assert "no provider" in unbound.json()["detail"]

    assert len(upstream.requests) == 1
    forwarded = upstream.requests[-1]
    assert str(forwarded.url) == "https://fake.openai/v1/chat/completions"
    assert forwarded.headers["authorization"] == "Bearer secret-key"
    body = json.loads(forwarded.content)
    assert body["model"] == "fake-openai-model"


async def test_assistant_reports_a_missing_api_key_environment(
    tmp_path: Path,
    monkeypatch,
    upstream,
) -> None:
    monkeypatch.delenv("WINNIE_TEST_MISSING_KEY", raising=False)
    settings = _assistant_settings(tmp_path, tmp_path / "ai.yaml")
    async with _open(settings, upstream) as client:
        created = await client.post(
            "/api/v1/ai/integrations",
            json={
                "name": "missing-key",
                "kind": "openai",
                "base_url": "https://fake.openai",
                "api_key_env": "WINNIE_TEST_MISSING_KEY",
                "default_model": "fake-openai-model",
            },
        )
        integration_id = created.json()["integration"]["id"]
        await client.put(
            "/api/v1/ai/features/assistant.chat",
            json={"integration_id": integration_id},
        )

        reply = await client.post(
            "/api/v1/assistant/chat", json={"message": "ping"}
        )
        assert reply.status_code == 502, reply.text
        assert "WINNIE_TEST_MISSING_KEY" in reply.json()["detail"]


async def test_meeting_translation_uses_the_bound_integration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            seen.append(request)
            body = json.loads(request.content)
            system = body["messages"][0]["content"]
            if "interpreter" in system:
                return httpx.Response(
                    200,
                    json={
                        "model": body["model"],
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(
                                        ["Translated by admin integration"],
                                        ensure_ascii=False,
                                    )
                                }
                            }
                        ],
                        "usage": {"total_tokens": 12},
                    },
                )
        return httpx.Response(500, json={"error": "unexpected request"})

    monkeypatch.setenv("WINNIE_TEST_LLM_KEY", "secret-key")
    settings = Settings(
        log_root=str(tmp_path / "logs"),
        routes={},
        ai=AIConfig(enabled=True, path=str(tmp_path / "ai.yaml")),
        meetings=MeetingsConfig(
            enabled=True,
            local_root=str(tmp_path / "meetings"),
            notes_root=str(tmp_path / "notes"),
            max_upload_mb=1,
            asr_provider="fake",
        ),
        database=DatabaseConfig(
            url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
            auto_create_tables=True,
        ),
        jobs=JobsConfig(backend="inline"),
    )
    async with _open(settings, handler) as client:
        created = await client.post(
            "/api/v1/ai/integrations",
            json={
                "name": "translate-llm",
                "kind": "openai",
                "base_url": "https://fake.openai",
                "api_key_env": "WINNIE_TEST_LLM_KEY",
                "default_model": "fake-openai-model",
            },
        )
        integration_id = created.json()["integration"]["id"]
        await client.put(
            "/api/v1/ai/features/meetings.translate",
            json={"integration_id": integration_id},
        )

        meeting = await client.post(
            "/api/v1/meetings", json={"title": "Bound provider"}
        )
        meeting_id = meeting.json()["id"]
        upload = await client.post(
            f"/api/v1/meetings/{meeting_id}/audio",
            files={"file": ("audio.wav", b"fake audio bytes", "audio/wav")},
        )
        assert upload.status_code == 200, upload.text
        processed = await client.post(
            f"/api/v1/meetings/{meeting_id}/transcribe"
        )
        assert processed.status_code == 202, processed.text

        transcript = await client.get(
            f"/api/v1/meetings/{meeting_id}/transcript"
        )
        segments = transcript.json()["segments"]
        translated = [item for item in segments if item["translated_text"]]
        assert translated[0]["translated_text"] == "Translated by admin integration"

    assert len(seen) == 1
    body = json.loads(seen[0].content)
    assert body["model"] == "fake-openai-model"


async def test_the_registry_survives_a_new_service_instance(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    async with _open(settings, lambda request: httpx.Response(500)) as client:
        created = await client.post(
            "/api/v1/ai/integrations",
            json={
                "name": "persisted",
                "kind": "openai",
                "base_url": "https://llm.example.com",
                "default_model": "model-1",
            },
        )
        assert created.status_code == 201

    # A second application instance reads the same file and sees the record.
    async with _open(settings, lambda request: httpx.Response(500)) as client:
        config = (await client.get("/api/v1/ai/config")).json()
        assert "persisted" in {item["name"] for item in config["integrations"]}


async def test_editing_a_bound_integration_cannot_break_the_binding(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    async with _open(settings, lambda request: httpx.Response(500)) as client:
        created = await client.post(
            "/api/v1/ai/integrations",
            json={
                "name": "bound-llm",
                "kind": "openai",
                "base_url": "https://llm.example.com",
                "api_key_env": "BOUND_LLM_KEY",
                "default_model": "model-1",
            },
        )
        integration_id = created.json()["integration"]["id"]
        await client.put(
            "/api/v1/ai/features/assistant.chat",
            json={"integration_id": integration_id, "model": ""},
        )

        wrong_kind = await client.put(
            f"/api/v1/ai/integrations/{integration_id}",
            json={
                "name": "bound-llm",
                "kind": "volcengine_asr",
                "base_url": "https://openspeech.bytedance.com",
                "options": {},
            },
        )
        assert wrong_kind.status_code == 400, wrong_kind.text
        assert "cannot change" in wrong_kind.json()["detail"]

        # The binding stored the integration's default model when it was
        # saved, so removing the default later does not break it.
        no_default = await client.put(
            f"/api/v1/ai/integrations/{integration_id}",
            json={
                "name": "bound-llm",
                "kind": "openai",
                "base_url": "https://llm.example.com",
                "api_key_env": "BOUND_LLM_KEY",
                "default_model": "",
            },
        )
        assert no_default.status_code == 200, no_default.text
        config = (await client.get("/api/v1/ai/config")).json()
        feature = next(
            item for item in config["features"] if item["key"] == "assistant.chat"
        )
        assert feature["model"] == "model-1"
        assert feature["source"] == "admin"


async def test_fresh_seed_leaves_model_choice_to_the_console(
    tmp_path: Path,
) -> None:
    settings = Settings(
        log_root=str(tmp_path / "logs"),
        routes={
            "deepseek-openai": RouteConfig(
                protocol="openai", base_url="https://api.deepseek.com"
            )
        },
        ai=AIConfig(enabled=True, path=str(tmp_path / "ai.yaml")),
    )
    async with _open(settings, lambda request: httpx.Response(500)) as client:
        config = (await client.get("/api/v1/ai/config")).json()
        routes = (await client.get("/routes")).json()["routes"]

    by_name = {item["name"]: item for item in config["integrations"]}
    # No llm block exists, so the seed cannot and must not invent an internal
    # provider. The console owns that choice.
    assert "internal-llm" not in by_name
    assert by_name["deepseek-openai"]["api_key_env"] == ""
    assert by_name["deepseek-openai"]["gateway"] is True
    assert by_name["deepseek-openai"]["label"] == "DeepSeek (OpenAI-compatible)"

    features = {item["key"]: item for item in config["features"]}
    for key in ("assistant.chat", "meetings.translate", "meetings.summarize"):
        assert features[key]["integration_name"] == "fake"
        assert features[key]["source"] == "admin"

    # Runtime limits live in the registry, not in config.yaml.
    assert config["runtime"]["timeout_seconds"] == 120
    assert config["runtime"]["max_output_tokens"] == 4096
    assert [route["name"] for route in routes] == ["deepseek-openai"]


async def test_v1_registry_is_split_and_rewritten(tmp_path: Path) -> None:
    import yaml

    path = tmp_path / "ai.yaml"
    path.write_text(
        """version: 1
integrations:
- name: deepseek-openai
  label: Deepseek Openai
  kind: openai
  base_url: https://api.deepseek.com
  api_key_env: WINNIE_LLM_API_KEY
  default_model: deepseek-chat
  enabled: true
  options: {}
  id: 11111111-1111-4111-8111-111111111111
  created_at: '2026-10-31T19:20:35+00:00'
  updated_at: '2026-10-31T19:20:35+00:00'
- name: fake
  label: Deterministic fake (offline)
  kind: fake
  base_url: ''
  api_key_env: ''
  default_model: ''
  enabled: true
  options: {}
  id: 22222222-2222-4222-8222-222222222222
  created_at: '2026-10-31T19:20:35+00:00'
  updated_at: '2026-10-31T19:20:35+00:00'
bindings:
  assistant.chat:
    integration_id: 11111111-1111-4111-8111-111111111111
    model: deepseek-chat
    updated_at: '2026-10-31T19:20:35+00:00'
""",
        encoding="utf-8",
    )
    settings = Settings(
        log_root=str(tmp_path / "logs"),
        routes={
            "deepseek-openai": RouteConfig(
                protocol="openai", base_url="https://api.deepseek.com"
            )
        },
        ai=AIConfig(enabled=True, path=str(path)),
    )
    async with _open(settings, lambda request: httpx.Response(500)) as client:
        config = (await client.get("/api/v1/ai/config")).json()
        routes = (await client.get("/routes")).json()["routes"]

    stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert stored["version"] == 2
    by_name = {item["name"]: item for item in config["integrations"]}
    assert by_name["deepseek-openai"]["api_key_env"] == ""
    assert by_name["deepseek-openai"]["default_model"] == ""
    assert by_name["deepseek-openai"]["label"] == "DeepSeek (OpenAI-compatible)"
    assert by_name["deepseek-openai"]["gateway"] is True
    assert by_name["internal-llm"]["api_key_env"] == "WINNIE_LLM_API_KEY"
    assert by_name["internal-llm"]["gateway"] is False
    assert by_name["internal-llm"]["default_model"] == "deepseek-chat"

    features = {item["key"]: item for item in config["features"]}
    assert features["assistant.chat"]["integration_name"] == "internal-llm"
    assert [route["name"] for route in routes] == ["deepseek-openai"]


async def test_a_gateway_only_provider_cannot_serve_an_internal_feature(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    async with _open(settings, lambda request: httpx.Response(500)) as client:
        created = await client.post(
            "/api/v1/ai/integrations",
            json={
                "name": "gateway-only",
                "kind": "openai",
                "base_url": "https://llm.example.com",
                "default_model": "model-1",
                "gateway": True,
            },
        )
        assert created.status_code == 201, created.text
        integration_id = created.json()["integration"]["id"]

        rejected = await client.put(
            "/api/v1/ai/features/assistant.chat",
            json={"integration_id": integration_id},
        )
        assert rejected.status_code == 400, rejected.text
        assert "gateway-only" in rejected.json()["detail"]

        updated = await client.put(
            f"/api/v1/ai/integrations/{integration_id}",
            json={
                "name": "internal-only",
                "kind": "openai",
                "base_url": "https://llm.example.com",
                "api_key_env": "INTERNAL_KEY",
                "default_model": "model-1",
                "gateway": False,
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["integration"]["gateway_paths"] == []

        bound = await client.put(
            "/api/v1/ai/features/assistant.chat",
            json={"integration_id": integration_id},
        )
        assert bound.status_code == 200, bound.text
        routes = (await client.get("/routes")).json()["routes"]
        assert "internal-only" not in [route["name"] for route in routes]


async def test_model_runtime_is_admin_managed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    async with _open(settings, lambda request: httpx.Response(500)) as client:
        config = (await client.get("/api/v1/ai/config")).json()
        assert config["runtime"]["timeout_seconds"] == 120
        assert config["runtime"]["max_output_tokens"] == 4096

        updated = await client.put(
            "/api/v1/ai/runtime",
            json={
                "timeout_seconds": 45,
                "max_output_tokens": 2048,
                "temperature": 0.7,
                "max_retries": 1,
                "retry_backoff_seconds": 0.5,
                "translation_batch_chars": 4000,
                "summary_chunk_chars": 12000,
            },
        )
        assert updated.status_code == 200, updated.text

        config = (await client.get("/api/v1/ai/config")).json()
        assert config["runtime"]["timeout_seconds"] == 45
        assert config["runtime"]["max_output_tokens"] == 2048
        assert config["runtime"]["translation_batch_chars"] == 4000

        invalid = await client.put(
            "/api/v1/ai/runtime",
            json={**config["runtime"], "timeout_seconds": 0},
        )
        assert invalid.status_code == 422
