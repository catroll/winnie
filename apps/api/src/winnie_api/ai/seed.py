"""Seed the admin registry from the YAML config and resolve config fallbacks.

The YAML file remains the bootstrap: on the first start the currently
configured routes and providers are imported into ``data/ai.yaml``. From then
on the console is authoritative, and a feature that has no binding keeps using
the YAML value so an existing deployment never breaks on upgrade.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from winnie_meetings.integrations.asr.volcengine import API_KEY_ENV

from winnie_api.ai.errors import AIProviderError
from winnie_api.ai.schema import (
    AIState,
    BindingRecord,
    FEATURE_BY_KEY,
    IntegrationRecord,
    ResolvedProvider,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle is avoided at runtime
    from winnie_api.settings import Settings


def _normalized_base(url: str) -> str:
    value = url.strip().rstrip("/")
    if value.endswith("/v1"):
        value = value[:-3]
    return value.rstrip("/")


def _route_label(name: str) -> str:
    known = {"openai": "OpenAI", "anthropic": "Anthropic"}
    return known.get(name, name.replace("-", " ").title())


def _unique_name(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    index = 2
    while f"{name}-{index}" in taken:
        index += 1
    return f"{name}-{index}"


def _has_llm_model(settings: Settings) -> bool:
    """An OpenAI-compatible feature needs a real model, not the fake marker."""
    return bool(settings.llm.model and settings.llm.model != "fake")


def _llm_provider(settings: Settings, feature: str, model: str) -> ResolvedProvider:
    if not settings.llm.base_url:
        raise AIProviderError(
            "llm.base_url is not configured; set it in apps/api/config.yaml "
            "or choose an integration for this feature in Admin -> AI providers"
        )
    if not model or model == "fake":
        raise AIProviderError(
            "llm.model is not configured for an OpenAI-compatible provider"
        )
    return ResolvedProvider(
        feature=feature,
        source="config",
        integration_id=None,
        integration_name="config:llm",
        kind="openai",
        base_url=settings.llm.base_url,
        api_key_env=settings.llm.api_key_env,
        model=model,
        options={},
    )


def _fake_provider(feature: str) -> ResolvedProvider:
    return ResolvedProvider(
        feature=feature,
        source="config",
        integration_id=None,
        integration_name="fake",
        kind="fake",
        base_url="",
        api_key_env="",
        model="",
        options={},
    )


def _volcengine_provider(settings: Settings, feature: str) -> ResolvedProvider:
    config = settings.meetings.volcengine
    options = config.model_dump()
    options.pop("base_url", None)
    return ResolvedProvider(
        feature=feature,
        source="config",
        integration_id=None,
        integration_name="volcengine-asr",
        kind="volcengine_asr",
        base_url=config.base_url,
        api_key_env=API_KEY_ENV,
        model=config.model_name,
        options=options,
    )


def config_provider(settings: Settings, feature: str) -> ResolvedProvider:
    """The provider implied by ``config.yaml`` for one feature."""
    if feature == "assistant.chat":
        model = (settings.assistant.model or settings.llm.model or "fake").strip()
        if model == "fake" or not model:
            return _fake_provider(feature)
        return _llm_provider(settings, feature, model)

    if feature == "meetings.transcribe":
        name = settings.meetings.asr_provider
        if name == "fake":
            return _fake_provider(feature)
        if name == "volcengine":
            return _volcengine_provider(settings, feature)
        raise AIProviderError(
            f"meetings.asr_provider={name!r} has no adapter; choose fake or "
            "volcengine, or bind the feature to an integration"
        )

    if feature in {"meetings.translate", "meetings.summarize"}:
        attribute = (
            "translator" if feature == "meetings.translate" else "summarizer"
        )
        name = getattr(settings.meetings, attribute)
        if name == "fake":
            return _fake_provider(feature)
        if name == "openai_compatible":
            return _llm_provider(settings, feature, settings.llm.model)
        raise AIProviderError(
            f"meetings.{attribute}={name!r} has no adapter; choose fake or "
            "openai_compatible, or bind the feature to an integration"
        )

    raise AIProviderError(f"unknown AI feature: {feature}")


def seed_state(settings: Settings) -> AIState:
    """Build the first registry from the YAML configuration."""
    integrations: list[IntegrationRecord] = []
    taken: set[str] = set()

    def add(**values: object) -> IntegrationRecord:
        name = _unique_name(str(values.pop("name")), taken)
        taken.add(name)
        record = IntegrationRecord(name=name, **values)  # type: ignore[arg-type]
        integrations.append(record)
        return record

    routes: dict[str, IntegrationRecord] = {}
    for name, route in settings.routes.items():
        routes[name] = add(
            name=name,
            label=_route_label(name),
            kind=route.protocol,
            base_url=route.base_url,
        )

    llm: IntegrationRecord | None = None
    if settings.llm.base_url:
        requested = _normalized_base(settings.llm.base_url)
        for route in routes.values():
            if _normalized_base(route.base_url) == requested:
                llm = route
                break
        if llm is None:
            llm = add(
                name="internal-llm",
                label="Internal LLM",
                kind="openai",
                base_url=settings.llm.base_url,
            )
        llm.api_key_env = settings.llm.api_key_env
        if not llm.default_model and settings.llm.model != "fake":
            llm.default_model = settings.llm.model

    asr: IntegrationRecord | None = None
    if settings.meetings.asr_provider == "volcengine":
        config = settings.meetings.volcengine
        options = config.model_dump()
        options.pop("base_url", None)
        asr = add(
            name="volcengine-asr",
            label="Volcengine Doubao ASR",
            kind="volcengine_asr",
            base_url=config.base_url,
            api_key_env=API_KEY_ENV,
            default_model=config.model_name,
            options=options,
        )

    fake = add(
        name="fake",
        label="Deterministic fake (offline)",
        kind="fake",
    )

    bindings: dict[str, BindingRecord] = {}

    def bind(feature: str, integration: IntegrationRecord | None, model: str = "") -> None:
        if integration is None:
            return
        bindings[feature] = BindingRecord(
            integration_id=integration.id,
            model=model,
        )

    chat_model = (settings.assistant.model or settings.llm.model or "fake").strip()
    if chat_model == "fake" or not chat_model:
        bind("assistant.chat", fake)
    else:
        bind("assistant.chat", llm, chat_model)

    asr_name = settings.meetings.asr_provider
    if asr_name == "fake":
        bind("meetings.transcribe", fake)
    elif asr_name == "volcengine":
        bind("meetings.transcribe", asr)

    if settings.meetings.translator == "fake":
        bind("meetings.translate", fake)
    elif settings.meetings.translator == "openai_compatible" and _has_llm_model(
        settings
    ):
        bind("meetings.translate", llm, settings.llm.model)

    if settings.meetings.summarizer == "fake":
        bind("meetings.summarize", fake)
    elif settings.meetings.summarizer == "openai_compatible" and _has_llm_model(
        settings
    ):
        bind("meetings.summarize", llm, settings.llm.model)

    assert set(bindings) <= set(FEATURE_BY_KEY)
    return AIState(integrations=integrations, bindings=bindings)


def describe_provider(provider: ResolvedProvider) -> str:
    """A short human label for the config fallback shown in the console."""
    if provider.kind == "fake":
        return "fake (offline, from config.yaml)"
    detail = provider.model or provider.integration_name
    if provider.kind == "volcengine_asr":
        detail = str(provider.options.get("resource_id") or provider.model)
    return f"{provider.kind} · {detail} (from config.yaml)"
