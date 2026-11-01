from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml

from winnie_llm import (
    AIProviderError,
    ApiKeys,
    BindingDraft,
    IntegrationDraft,
    ModelRecord,
    ProviderRegistry,
    VendorDraft,
)

V5 = """\
version: 5
vendors:
- id: deepseek
  label: DeepSeek
  models:
  - id: deepseek-chat
- id: volcengine
  label: Volcengine
  models:
  - id: bigmodel
integrations:
- name: deepseek-internal
  vendor: deepseek
  kind: openai
  base_url: https://api.deepseek.com
  api_key_env: DEEPSEEK_API_KEY
  default_model: deepseek-chat
  enabled: true
  gateway: false
- name: volcengine-asr
  vendor: volcengine
  kind: volcengine_asr
  base_url: https://openspeech.bytedance.com
  api_key_env: VOLCENGINE_ASR_API_KEY
  default_model: bigmodel
  enabled: true
  gateway: false
  options: {}
bindings:
  assistant.chat:
    vendor: deepseek
    model: deepseek-chat
  meetings.transcribe:
    vendor: volcengine
    model: bigmodel
"""


def _write_v5(tmp_path: Path) -> Path:
    path = tmp_path / "ai.yaml"
    path.write_text(V5, encoding="utf-8")
    return path


def _keys(tmp_path: Path, **values: str) -> ApiKeys:
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(f"{name}={value}" for name, value in values.items()) + "\n",
        encoding="utf-8",
    )
    return ApiKeys.load(env, environ={})


def test_v5_file_is_migrated_and_the_key_variable_is_removed(
    tmp_path: Path,
) -> None:
    path = _write_v5(tmp_path)
    registry = ProviderRegistry(
        path, keys=_keys(tmp_path, DEEPSEEK_API_KEY="secret")
    )
    registry.load()

    stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert stored["version"] == 6
    assert all("api_key_env" not in item for item in stored["integrations"])

    provider = registry.resolve("assistant.chat")
    assert provider.vendor == "deepseek"
    assert provider.model == "deepseek-chat"
    # The name is derived from the supplier, not read from the file.
    assert provider.key_env == "DEEPSEEK_API_KEY"


@pytest.mark.asyncio
async def test_chat_client_sends_the_key_from_the_package_env(
    tmp_path: Path,
) -> None:
    path = _write_v5(tmp_path)
    registry = ProviderRegistry(
        path, keys=_keys(tmp_path, DEEPSEEK_API_KEY="secret-key")
    )
    registry.load()

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "model": "deepseek-chat",
                "choices": [
                    {"message": {"role": "assistant", "content": "pong"}}
                ],
                "usage": {"total_tokens": 3},
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await registry.chat_for("assistant.chat", http=http).complete(
        [{"role": "user", "content": "ping"}]
    )
    await http.aclose()

    assert result.text == "pong"
    assert str(seen[0].url) == "https://api.deepseek.com/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer secret-key"
    assert json.loads(seen[0].content)["model"] == "deepseek-chat"


@pytest.mark.asyncio
async def test_missing_key_is_reported_before_a_request(tmp_path: Path) -> None:
    path = _write_v5(tmp_path)
    registry = ProviderRegistry(path, keys=_keys(tmp_path))
    registry.load()

    with pytest.raises(AIProviderError) as raised:
        await registry.chat_for("assistant.chat").complete(
            [{"role": "user", "content": "ping"}]
        )
    message = str(raised.value)
    assert "DEEPSEEK_API_KEY" in message
    assert str(tmp_path / ".env") in message


def test_asr_client_uses_the_supplier_key(tmp_path: Path) -> None:
    path = _write_v5(tmp_path)
    registry = ProviderRegistry(
        path, keys=_keys(tmp_path, VOLCENGINE_ASR_API_KEY="speech-key")
    )
    registry.load()

    provider = registry.asr_for("meetings.transcribe")
    assert provider.api_key == "speech-key"
    assert provider.model_name == "bigmodel"

    realtime = registry.realtime_asr_for("meetings.transcribe")
    assert realtime.api_key == "speech-key"


def test_seed_uses_the_host_routes_and_asr_defaults(tmp_path: Path) -> None:
    from winnie_llm import SeedInput, SeedRoute

    path = tmp_path / "ai.yaml"
    registry = ProviderRegistry(
        path,
        keys=_keys(tmp_path),
        seed=SeedInput(
            routes=(
                SeedRoute(
                    name="deepseek-openai",
                    protocol="openai",
                    base_url="https://api.deepseek.com",
                ),
            ),
            bind_asr=True,
        ),
    )
    registry.load()

    config = registry.view()
    names = {item["name"] for item in config["integrations"]}
    assert {"deepseek-openai", "volcengine-asr"} <= names
    features = {item["key"]: item for item in config["features"]}
    assert features["meetings.transcribe"]["vendor"] == "volcengine"
    assert features["assistant.chat"]["source"] == "none"
    assert config["env_path"].endswith(".env")


def _zhipu_registry(tmp_path: Path) -> ProviderRegistry:
    """A registry with an OpenAI-compatible supplier rooted at /v4."""
    path = _write_v5(tmp_path)
    registry = ProviderRegistry(
        path,
        keys=_keys(tmp_path, DEEPSEEK_API_KEY="secret", ZHIPU_API_KEY="zhipu-key"),
    )
    registry.load()
    registry.create_vendor(
        VendorDraft(
            id="zhipu",
            label="Zhipu",
            models=[ModelRecord(id="glm-4.6")],
        )
    )
    registry.create_integration(
        IntegrationDraft(
            name="zhipu-internal",
            label="Zhipu API",
            vendor="zhipu",
            kind="openai",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            gateway=False,
            enabled=True,
        )
    )
    registry.set_binding(
        "assistant.chat", BindingDraft(vendor="zhipu", model="glm-4.6")
    )
    return registry


@pytest.mark.asyncio
async def test_model_sync_treats_a_versioned_base_as_the_root(
    tmp_path: Path,
) -> None:
    registry = _zhipu_registry(tmp_path)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "glm-4.6", "owned_by": "z-ai", "created": 1759276800}
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http:
        vendor = await registry.sync_models("zhipu", http)

    # /v4 is the API root, so /v1 is not appended to it.
    assert seen == ["https://open.bigmodel.cn/api/paas/v4/models"]
    assert [(model.id, model.owned_by) for model in vendor.models] == [
        ("glm-4.6", "z-ai")
    ]


def test_chat_client_keeps_a_versioned_base_as_the_root(tmp_path: Path) -> None:
    registry = _zhipu_registry(tmp_path)
    client = registry.chat_for("assistant.chat")
    assert client.base_url == "https://open.bigmodel.cn/api/paas/v4"


def test_chat_client_appends_v1_to_an_unversioned_base(tmp_path: Path) -> None:
    path = _write_v5(tmp_path)
    registry = ProviderRegistry(
        path, keys=_keys(tmp_path, DEEPSEEK_API_KEY="secret")
    )
    registry.load()
    client = registry.chat_for("assistant.chat")
    assert client.base_url == "https://api.deepseek.com/v1"
