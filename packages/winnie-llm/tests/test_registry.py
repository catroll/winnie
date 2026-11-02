from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml
from pydantic import ValidationError

from winnie_llm import (
    AIConflict,
    AINotFound,
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


def test_v5_file_is_migrated_and_the_env_backup_key_moves_to_the_supplier(
    tmp_path: Path,
) -> None:
    path = _write_v5(tmp_path)
    registry = ProviderRegistry(
        path, keys=_keys(tmp_path, DEEPSEEK_API_KEY="secret")
    )
    registry.load()

    stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert stored["version"] == 8
    assert all("api_key_env" not in item for item in stored["integrations"])
    # Every direct binding became a virtual model with the same target, so the
    # upgrade does not change which model a feature calls.
    by_virtual = {item["id"]: item for item in stored["virtual_models"]}
    assert by_virtual["deepseek-chat"]["vendor"] == "deepseek"
    assert by_virtual["deepseek-chat"]["model"] == "deepseek-chat"
    assert by_virtual["bigmodel"]["vendor"] == "volcengine"
    assert stored["bindings"]["assistant.chat"]["virtual"] == "deepseek-chat"
    assert stored["bindings"]["meetings.transcribe"]["virtual"] == "bigmodel"
    # The one-time import moves the old env value onto the supplier record; the
    # backup file itself is left as it was.
    by_id = {vendor["id"]: vendor for vendor in stored["vendors"]}
    assert by_id["deepseek"]["api_key"] == "secret"
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "DEEPSEEK_API_KEY=secret\n"

    provider = registry.resolve("assistant.chat")
    assert provider.vendor == "deepseek"
    assert provider.model == "deepseek-chat"
    assert provider.virtual == "deepseek-chat"
    # The name the console mirrors the key under is derived from the supplier.
    assert provider.key_env == "DEEPSEEK_API_KEY"


@pytest.mark.asyncio
async def test_chat_client_sends_the_key_from_the_supplier_record(
    tmp_path: Path,
) -> None:
    path = _write_v5(tmp_path)
    registry = ProviderRegistry(
        path, keys=_keys(tmp_path, DEEPSEEK_API_KEY="secret-key")
    )
    registry.load()
    # The key now lives on the supplier, so a change to the backup file alone
    # must not affect the next call.
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=stale-backup\n", encoding="utf-8")

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
    assert "supplier 'deepseek' has no API key" in message
    assert "Admin -> AI suppliers" in message


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
    # A supplier added after the migration gets its key from the console, not
    # from an env file that happened to already hold it.
    registry.set_api_key("zhipu", "zhipu-key")
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


def test_set_api_key_stores_it_and_mirrors_the_env_backup(tmp_path: Path) -> None:
    path = _write_v5(tmp_path)
    env = tmp_path / ".env"
    env.write_text(
        "# vendor keys\n"
        "DEEPSEEK_API_KEY=\n"
        "UNRELATED=keep-me # a comment stays\n",
        encoding="utf-8",
    )
    keys = ApiKeys.load(env, environ={})
    registry = ProviderRegistry(path, keys=keys)
    registry.load()

    vendor = registry.set_api_key("deepseek", "  sk-new-value  ")

    assert vendor.api_key == "sk-new-value"
    assert registry.vendor_key("deepseek") == "sk-new-value"
    assert registry.key_present("deepseek") is True
    stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert {item["id"]: item["api_key"] for item in stored["vendors"]}[
        "deepseek"
    ] == "sk-new-value"
    # The backup is updated in place and unrelated lines survive.
    assert env.read_text(encoding="utf-8") == (
        "# vendor keys\n"
        "DEEPSEEK_API_KEY=sk-new-value\n"
        "UNRELATED=keep-me # a comment stays\n"
    )
    # The in-memory snapshot keeps up, so a second save does not need a reload.
    assert keys.get("deepseek") == "sk-new-value"


def test_views_never_return_the_key(tmp_path: Path) -> None:
    path = _write_v5(tmp_path)
    registry = ProviderRegistry(path, keys=_keys(tmp_path))
    registry.load()
    registry.set_api_key("deepseek", "sk-0123456789abcd")

    config = registry.view()
    vendor = next(item for item in config["vendors"] if item["id"] == "deepseek")
    assert "api_key" not in vendor
    assert vendor["api_key_present"] is True
    assert vendor["api_key_hint"] == "…abcd"
    assert vendor["api_key_env"] == "DEEPSEEK_API_KEY"
    assert vendor["api_key_backup_path"].endswith(".env")
    integration = next(
        item for item in config["integrations"] if item["vendor"] == "deepseek"
    )
    assert integration["api_key_present"] is True
    assert "api_key" not in integration
    assert "sk-0123456789abcd" not in json.dumps(config)


def test_clearing_a_key_updates_the_backup(tmp_path: Path) -> None:
    path = _write_v5(tmp_path)
    env = tmp_path / ".env"
    env.write_text("DEEPSEEK_API_KEY=secret\n", encoding="utf-8")
    registry = ProviderRegistry(path, keys=ApiKeys.load(env, environ={}))
    registry.load()

    registry.set_api_key("deepseek", "")

    assert registry.key_present("deepseek") is False
    assert env.read_text(encoding="utf-8") == "DEEPSEEK_API_KEY=\n"


@pytest.mark.asyncio
async def test_check_vendor_reports_the_catalog_and_a_missing_binding(
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
        # The supplier retired the model the assistant is bound to; the key
        # works, which is exactly the case a check has to distinguish.
        return httpx.Response(200, json={"data": [{"id": "deepseek-reasoner"}]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    check = await registry.check_vendor("deepseek", http=http)
    await http.aclose()

    assert check.state == "ok"
    assert check.status == 200
    assert check.models == 1
    assert str(seen[0].url) == "https://api.deepseek.com/v1/models"
    assert seen[0].headers["authorization"] == "Bearer secret-key"
    assert check.missing_models == ("assistant.chat · deepseek-chat",)


@pytest.mark.asyncio
async def test_check_vendor_separates_a_rejected_key_from_a_failure(
    tmp_path: Path,
) -> None:
    path = _write_v5(tmp_path)
    registry = ProviderRegistry(
        path, keys=_keys(tmp_path, DEEPSEEK_API_KEY="wrong-key")
    )
    registry.load()

    async def probe(response: httpx.Response):
        http = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: response)
        )
        try:
            return await registry.check_vendor("deepseek", http=http)
        finally:
            await http.aclose()

    rejected = await probe(
        httpx.Response(401, json={"error": {"message": "Authentication Fails"}})
    )
    assert rejected.state == "unauthorized"
    assert rejected.status == 401
    assert "Authentication Fails" in rejected.detail

    broken = await probe(httpx.Response(503, text="upstream\nunavailable"))
    assert broken.state == "error"
    assert broken.status == 503
    assert broken.detail == "upstream unavailable"

    html = await probe(httpx.Response(200, text="<html>not json</html>"))
    assert html.state == "error"
    assert "non-JSON" in html.detail


@pytest.mark.asyncio
async def test_check_vendor_requires_a_key_and_says_when_it_cannot_probe(
    tmp_path: Path,
) -> None:
    path = _write_v5(tmp_path)
    registry = ProviderRegistry(path, keys=_keys(tmp_path))
    registry.load()

    with pytest.raises(AIConflict) as raised:
        await registry.check_vendor("deepseek")
    assert "has no API key" in str(raised.value)

    registry.set_api_key("volcengine", "speech-key")
    unsupported = await registry.check_vendor("volcengine")
    assert unsupported.state == "unsupported"
    assert "no enabled OpenAI or Anthropic API" in unsupported.detail

    with pytest.raises(AINotFound):
        await registry.check_vendor("nope")


@pytest.mark.asyncio
async def test_check_vendor_uses_the_supplier_protocol_and_a_gateway_route(
    tmp_path: Path,
) -> None:
    path = _write_v5(tmp_path)
    registry = ProviderRegistry(path, keys=_keys(tmp_path))
    registry.load()
    registry.create_vendor(
        VendorDraft(id="claude", label="Claude", models=[ModelRecord(id="claude-x")])
    )
    registry.set_api_key("claude", "anthropic-key")
    registry.create_integration(
        IntegrationDraft(
            name="claude-internal",
            label="Claude API",
            vendor="claude",
            kind="anthropic",
            base_url="https://api.anthropic.com",
            gateway=False,
            enabled=True,
        )
    )

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": "claude-x"}]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    check = await registry.check_vendor("claude", http=http)
    await http.aclose()

    # Anthropic does not read a bearer token, so a check that sent one would
    # report every working key as rejected.
    assert str(seen[0].url) == "https://api.anthropic.com/v1/models"
    assert seen[0].headers["x-api-key"] == "anthropic-key"
    assert seen[0].headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in seen[0].headers
    assert check.state == "ok"
    assert check.missing_models == ()


@pytest.mark.asyncio
async def test_check_vendor_can_probe_a_gateway_only_supplier(tmp_path: Path) -> None:
    path = _write_v5(tmp_path)
    registry = ProviderRegistry(path, keys=_keys(tmp_path))
    registry.load()
    registry.create_vendor(
        VendorDraft(id="mirror", label="Mirror", models=[ModelRecord(id="m1")])
    )
    registry.set_api_key("mirror", "mirror-key")
    # No internal endpoint: the supplier only serves traffic through the
    # gateway, and the key is still the one the registry stores.
    registry.create_integration(
        IntegrationDraft(
            name="mirror-gateway",
            label="Mirror gateway",
            vendor="mirror",
            kind="openai",
            base_url="https://mirror.example.com/openai",
            gateway=True,
            enabled=True,
        )
    )

    seen: list[httpx.Request] = []
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: (
                seen.append(request)
                or httpx.Response(200, json={"data": [{"id": "m1"}]})
            )
        )
    )
    check = await registry.check_vendor("mirror", http=http)
    await http.aclose()

    assert str(seen[0].url) == "https://mirror.example.com/openai/v1/models"
    assert check.state == "ok"


V7 = """\
version: 7
vendors:
- id: deepseek
  label: DeepSeek
  models:
  - id: deepseek-chat
  - id: deepseek-reasoner
integrations:
- name: deepseek-internal
  vendor: deepseek
  kind: openai
  base_url: https://api.deepseek.com
  default_model: deepseek-chat
  enabled: true
  gateway: false
bindings:
  assistant.chat:
    vendor: deepseek
    model: deepseek-chat
  news.extract:
    vendor: deepseek
    model: deepseek-chat
"""


def _v7_registry(tmp_path: Path) -> tuple[ProviderRegistry, Path]:
    path = tmp_path / "ai.yaml"
    path.write_text(V7, encoding="utf-8")
    registry = ProviderRegistry(
        path, keys=_keys(tmp_path, DEEPSEEK_API_KEY="secret")
    )
    registry.load()
    return registry, path


def test_one_alias_serves_every_feature_that_shared_a_model(
    tmp_path: Path,
) -> None:
    registry, path = _v7_registry(tmp_path)
    stored = yaml.safe_load(path.read_text(encoding="utf-8"))

    # Two features used the same supplier model before the upgrade, so they
    # already share one alias rather than getting one alias each.
    assert [item["id"] for item in stored["virtual_models"]] == ["deepseek-chat"]
    assert stored["bindings"]["assistant.chat"] == {
        "virtual": "deepseek-chat",
        "vendor": "",
        "model": "",
        "integration_id": None,
        "updated_at": stored["bindings"]["assistant.chat"]["updated_at"],
    }

    config = registry.view()
    virtual = next(
        item
        for item in config["virtual_models"]
        if item["id"] == "deepseek-chat"
    )
    assert virtual["vendor"] == "deepseek"
    assert virtual["model"] == "deepseek-chat"
    assert virtual["used_by"] == ["assistant.chat", "news.extract"]
    features = {item["key"]: item for item in config["features"]}
    assert features["assistant.chat"]["virtual"] == "deepseek-chat"
    assert features["assistant.chat"]["model"] == "deepseek-chat"


def test_retargeting_an_alias_moves_every_bound_feature(tmp_path: Path) -> None:
    from winnie_llm import VirtualModelDraft

    registry, _ = _v7_registry(tmp_path)

    registry.update_virtual_model(
        "deepseek-chat",
        VirtualModelDraft(
            id="deepseek-chat",
            label="Chat",
            vendor="deepseek",
            model="deepseek-reasoner",
        ),
    )

    # One edit, both features follow on their next call.
    assert registry.resolve("assistant.chat").model == "deepseek-reasoner"
    assert registry.resolve("news.extract").model == "deepseek-reasoner"
    assert registry.resolve("assistant.chat").virtual == "deepseek-chat"


def test_an_alias_cannot_point_at_a_model_outside_the_catalog(
    tmp_path: Path,
) -> None:
    from winnie_llm import VirtualModelDraft

    registry, _ = _v7_registry(tmp_path)
    with pytest.raises(AIProviderError) as raised:
        registry.update_virtual_model(
            "deepseek-chat",
            VirtualModelDraft(
                id="deepseek-chat",
                vendor="deepseek",
                model="retired-model",
            ),
        )
    assert "not in the 'deepseek' model catalog" in str(raised.value)
    # The rejected change did not move the features.
    assert registry.resolve("assistant.chat").model == "deepseek-chat"


def test_batch_binding_is_atomic_and_aliases_share_one_target(
    tmp_path: Path,
) -> None:
    from winnie_llm import VirtualModelDraft

    registry, _ = _v7_registry(tmp_path)
    registry.create_virtual_model(
        VirtualModelDraft(
            id="fast", label="Fast", vendor="deepseek", model="deepseek-reasoner"
        )
    )

    saved = registry.set_bindings(
        {
            "assistant.chat": BindingDraft(virtual="fast"),
            "news.extract": BindingDraft(virtual="fast"),
        }
    )
    assert set(saved) == {"assistant.chat", "news.extract"}
    assert registry.resolve("assistant.chat").model == "deepseek-reasoner"
    assert registry.resolve("news.extract").model == "deepseek-reasoner"

    # One bad row leaves the whole batch unapplied.
    with pytest.raises(AINotFound):
        registry.set_bindings(
            {
                "assistant.chat": BindingDraft(virtual="deepseek-chat"),
                "news.review": BindingDraft(virtual="missing"),
            }
        )
    assert registry.resolve("assistant.chat").model == "deepseek-reasoner"


def test_a_used_alias_cannot_be_deleted_and_a_spare_one_can(
    tmp_path: Path,
) -> None:
    from winnie_llm import VirtualModelDraft

    registry, _ = _v7_registry(tmp_path)
    with pytest.raises(AIConflict) as raised:
        registry.delete_virtual_model("deepseek-chat")
    assert "assistant.chat" in str(raised.value)
    assert "news.extract" in str(raised.value)

    registry.create_virtual_model(
        VirtualModelDraft(id="spare", vendor="deepseek", model="deepseek-chat")
    )
    assert registry.delete_virtual_model("spare") == ("spare", [])


def test_a_model_an_alias_maps_to_cannot_be_removed(tmp_path: Path) -> None:
    from winnie_llm import VirtualModelDraft

    registry, _ = _v7_registry(tmp_path)
    registry.create_virtual_model(
        VirtualModelDraft(
            id="deep-think",
            vendor="deepseek",
            model="deepseek-reasoner",
        )
    )
    # deepseek-reasoner is not the endpoint default, so the alias is what
    # blocks its removal.
    with pytest.raises(AIProviderError) as raised:
        registry.update_vendor(
            "deepseek",
            VendorDraft(
                id="deepseek",
                label="DeepSeek",
                models=[ModelRecord(id="deepseek-chat")],
            ),
        )
    assert "virtual model 'deep-think' maps to it" in str(raised.value)


def test_an_empty_binding_unbinds_a_feature_and_direct_bindings_still_work(
    tmp_path: Path,
) -> None:
    registry, _ = _v7_registry(tmp_path)

    # A body with no fields is a caller mistake, not a silent unbind.
    with pytest.raises(AIProviderError) as missing:
        registry.set_binding("assistant.chat", BindingDraft())
    assert "choose a model" in str(missing.value)

    unbound = registry.set_binding("assistant.chat", BindingDraft(virtual=""))
    assert unbound.virtual == "" and unbound.vendor == ""
    with pytest.raises(AIProviderError) as raised:
        registry.resolve("assistant.chat")
    assert "has no model" in str(raised.value)

    # A caller written against the v7 API keeps working; the view reports the
    # resolved target but no alias.
    direct = registry.set_binding(
        "news.write", BindingDraft(vendor="deepseek", model="deepseek-chat")
    )
    assert direct.virtual == ""
    resolved = registry.resolve("news.write")
    assert resolved.vendor == "deepseek"
    assert resolved.model == "deepseek-chat"
    assert resolved.virtual == ""


@pytest.mark.asyncio
async def test_model_sync_keeps_a_model_an_unused_alias_maps_to(
    tmp_path: Path,
) -> None:
    from winnie_llm import VirtualModelDraft

    registry, _ = _v7_registry(tmp_path)
    registry.set_api_key("deepseek", "secret")
    registry.create_virtual_model(
        VirtualModelDraft(
            id="spare-think", vendor="deepseek", model="deepseek-reasoner"
        )
    )

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"data": [{"id": "deepseek-chat"}]}
            )
        )
    )
    vendor = await registry.sync_models("deepseek", http)
    await http.aclose()

    # The supplier temporarily omitted it, but an alias still names it, so the
    # sync must not remove the catalog entry the alias depends on.
    assert [model.id for model in vendor.models] == [
        "deepseek-chat",
        "deepseek-reasoner",
    ]


def test_an_alias_is_another_spelling_of_one_platform_model(
    tmp_path: Path,
) -> None:
    """``deepseek-flash`` answers to the two names clients still send."""
    from winnie_llm import VirtualModelDraft

    registry, path = _v7_registry(tmp_path)
    registry.create_virtual_model(
        VirtualModelDraft(
            id="deepseek-flash",
            label="DeepSeek Flash",
            aliases=["deepseek-v4-flash", "deepseek-v4.1-flash"],
            vendor="deepseek",
            model="deepseek-reasoner",
        )
    )

    stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    row = next(
        item for item in stored["virtual_models"] if item["id"] == "deepseek-flash"
    )
    assert row["aliases"] == ["deepseek-v4-flash", "deepseek-v4.1-flash"]

    view = registry.view()["virtual_models"]
    shown = next(item for item in view if item["id"] == "deepseek-flash")
    assert shown["aliases"] == ["deepseek-v4-flash", "deepseek-v4.1-flash"]


def test_two_platform_models_cannot_share_a_call_name(tmp_path: Path) -> None:
    from winnie_llm import VirtualModelDraft

    registry, _ = _v7_registry(tmp_path)
    with pytest.raises(AIProviderError) as raised:
        # ``deepseek-chat`` is already the id of another platform model.
        registry.create_virtual_model(
            VirtualModelDraft(
                id="fast",
                vendor="deepseek",
                model="deepseek-reasoner",
                aliases=["deepseek-chat"],
            )
        )
    assert "claimed by both" in str(raised.value)


def test_an_alias_must_be_a_model_shaped_name(tmp_path: Path) -> None:
    from winnie_llm import VirtualModelDraft

    # The draft rejects it before the store is reached, which is what makes the
    # console's save button ask for a fix instead of writing a broken file.
    with pytest.raises(ValidationError) as raised:
        VirtualModelDraft(
            id="fast",
            vendor="deepseek",
            model="deepseek-reasoner",
            aliases=["not a model name"],
        )
    assert "model alias" in str(raised.value)


def test_a_platform_model_cannot_list_its_own_id_as_an_alias(
    tmp_path: Path,
) -> None:
    from winnie_llm import VirtualModelDraft

    with pytest.raises(ValidationError) as raised:
        VirtualModelDraft(
            id="fast",
            vendor="deepseek",
            model="deepseek-reasoner",
            aliases=["fast"],
        )
    assert "its own id" in str(raised.value)
