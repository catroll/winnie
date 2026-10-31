"""Adapters that resolve their provider from the console on every call.

The capability packages are built once at startup. These thin wrappers keep
that lifecycle but delegate each operation to the integration currently bound
to the feature, so changing a provider in the console takes effect without a
restart. The config.yaml fallback is resolved by :class:`AISettingsService`.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from winnie_assistant.agent import ChatModel
from winnie_assistant.fake import FakeChatModel
from winnie_core.llm.base import ChatResult
from winnie_core.llm.openai_compatible import OpenAICompatibleLLM
from winnie_core.types import AudioRef
from winnie_core.usage.ledger import UsageSink
from winnie_meetings.config import VolcengineASRConfig
from winnie_meetings.integrations.asr.base import ASRProvider, ASRResult
from winnie_meetings.integrations.asr.fake import FakeASRProvider, FakeRealtimeASR
from winnie_meetings.integrations.asr.realtime import (
    RealtimeASRError,
    RealtimeASRProvider,
    RealtimeEvent,
)
from winnie_meetings.integrations.asr.volcengine import (
    API_KEY_ENV,
    VolcengineASRProvider,
)
from winnie_meetings.integrations.asr.volcengine_stream import VolcengineRealtimeASR
from winnie_meetings.integrations.summarize.base import (
    MinutesDraft,
    Summarizer,
    TranscriptView,
)
from winnie_meetings.integrations.summarize.fake import FakeSummarizer
from winnie_meetings.integrations.summarize.llm import LLMSummarizer
from winnie_meetings.integrations.translate.base import TranslationResult, Translator
from winnie_meetings.integrations.translate.fake import FakeTranslator
from winnie_meetings.integrations.translate.llm import LLMTranslator

from winnie_api.ai.errors import AIProviderError
from winnie_api.ai.schema import ResolvedProvider
from winnie_api.ai.store import AISettingsService


def _openai_base(value: str) -> str:
    base = value.strip().rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return base


def _api_key(provider: ResolvedProvider, *, default_env: str = "") -> str:
    env = provider.api_key_env.strip() or default_env
    if not env:
        raise AIProviderError(
            f"integration {provider.integration_name!r} has no api_key_env; set it "
            "in Admin -> AI providers"
        )
    value = os.environ.get(env, "").strip()
    if not value:
        raise AIProviderError(
            f"environment variable {env} is empty; export it and restart the API or worker "
            f"(integration {provider.integration_name!r})"
        )
    return value


def _require_model(provider: ResolvedProvider) -> str:
    if not provider.model:
        raise AIProviderError(
            f"integration {provider.integration_name!r} has no model for "
            f"{provider.feature}; choose one in Admin -> AI providers"
        )
    return provider.model


def _require_base_url(provider: ResolvedProvider) -> str:
    if not provider.base_url:
        raise AIProviderError(
            f"integration {provider.integration_name!r} has no base_url"
        )
    return provider.base_url


def _build_openai(
    provider: ResolvedProvider,
    settings: Any,
    http: httpx.AsyncClient | None,
    usage: UsageSink | None = None,
) -> OpenAICompatibleLLM:
    llm = settings.llm
    return OpenAICompatibleLLM(
        base_url=_openai_base(_require_base_url(provider)),
        api_key=_api_key(provider),
        model=_require_model(provider),
        timeout_seconds=llm.timeout_seconds,
        max_output_tokens=llm.max_output_tokens,
        temperature=llm.temperature,
        max_retries=llm.max_retries,
        retry_backoff_seconds=llm.retry_backoff_seconds,
        http=http,
        usage=usage,
        capability=provider.feature,
        provider=provider.integration_name,
    )


class DynamicChatModel:
    """One ``ChatModel`` whose concrete model is resolved per turn."""

    name = "configured"

    def __init__(
        self,
        settings: Any,
        ai: AISettingsService,
        http: httpx.AsyncClient | None,
        usage: UsageSink | None = None,
    ) -> None:
        self.settings = settings
        self.ai = ai
        self.http = http
        self.usage = usage

    @property
    def name(self) -> str:  # type: ignore[override]
        try:
            provider = self.ai.resolve("assistant.chat")
        except AIProviderError:
            return "unavailable"
        return provider.model or provider.integration_name

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        provider = self.ai.resolve("assistant.chat")
        if provider.kind == "fake":
            model: ChatModel = FakeChatModel()
        elif provider.kind == "openai":
            model = _build_openai(
                provider, self.settings, self.http, self.usage
            )
        else:
            raise AIProviderError(
                f"assistant.chat cannot use a {provider.kind} integration; "
                "choose an OpenAI-compatible one"
            )
        return await model.chat(messages, tools)


class DynamicTranslator:
    def __init__(
        self,
        settings: Any,
        ai: AISettingsService,
        http: httpx.AsyncClient | None,
        usage: UsageSink | None = None,
    ) -> None:
        self.settings = settings
        self.ai = ai
        self.http = http
        self.usage = usage

    async def translate(
        self,
        texts: list[str],
        *,
        source: str | None,
        target: str,
    ) -> TranslationResult:
        provider = self.ai.resolve("meetings.translate")
        if provider.kind == "fake":
            implementation: Translator = FakeTranslator()
        elif provider.kind == "openai":
            implementation = LLMTranslator(
                _build_openai(provider, self.settings, self.http, self.usage),
                max_batch_chars=self.settings.llm.translation_batch_chars,
            )
        else:
            raise AIProviderError(
                f"meetings.translate cannot use a {provider.kind} integration; "
                "choose an OpenAI-compatible one"
            )
        return await implementation.translate(texts, source=source, target=target)


class DynamicSummarizer:
    def __init__(
        self,
        settings: Any,
        ai: AISettingsService,
        http: httpx.AsyncClient | None,
        usage: UsageSink | None = None,
    ) -> None:
        self.settings = settings
        self.ai = ai
        self.http = http
        self.usage = usage

    async def summarize(self, transcript: TranscriptView) -> MinutesDraft:
        provider = self.ai.resolve("meetings.summarize")
        if provider.kind == "fake":
            implementation: Summarizer = FakeSummarizer()
        elif provider.kind == "openai":
            implementation = LLMSummarizer(
                _build_openai(provider, self.settings, self.http, self.usage),
                max_chunk_chars=self.settings.llm.summary_chunk_chars,
            )
        else:
            raise AIProviderError(
                f"meetings.summarize cannot use a {provider.kind} integration; "
                "choose an OpenAI-compatible one"
            )
        return await implementation.summarize(transcript)


def _volcengine_config(
    provider: ResolvedProvider, settings: Any
) -> VolcengineASRConfig:
    options = dict(provider.options)
    if "base_url" in options:
        # Older seed files may carry it here; the integration field wins.
        options.pop("base_url")
    merged = {
        "base_url": provider.base_url or settings.meetings.volcengine.base_url,
        **options,
    }
    try:
        return VolcengineASRConfig.model_validate(merged)
    except Exception as exc:  # noqa: BLE001 - pydantic's message is the detail
        raise AIProviderError(
            f"invalid Volcengine ASR options on {provider.integration_name!r}: {exc}"
        ) from exc


def _build_asr(
    provider: ResolvedProvider, settings: Any, http: httpx.AsyncClient | None
) -> ASRProvider:
    if provider.kind == "fake":
        return FakeASRProvider()
    if provider.kind != "volcengine_asr":
        raise AIProviderError(
            f"meetings.transcribe cannot use a {provider.kind} integration; "
            "choose Volcengine ASR or fake"
        )
    config = _volcengine_config(provider, settings)
    return VolcengineASRProvider(
        api_key=_api_key(provider, default_env=API_KEY_ENV),
        base_url=config.base_url,
        resource_id=config.resource_id,
        model_name=config.model_name,
        max_inline_bytes=config.max_inline_mb * 1024 * 1024,
        poll_interval_seconds=config.poll_interval_seconds,
        poll_timeout_seconds=config.poll_timeout_seconds,
        http=http,
    )


def _build_realtime_asr(
    provider: ResolvedProvider, settings: Any
) -> RealtimeASRProvider:
    if provider.kind == "fake":
        return FakeRealtimeASR()
    if provider.kind != "volcengine_asr":
        raise AIProviderError(
            f"live captions cannot use a {provider.kind} integration; "
            "choose Volcengine ASR or fake"
        )
    config = _volcengine_config(provider, settings)
    return VolcengineRealtimeASR(
        api_key=_api_key(provider, default_env=API_KEY_ENV),
        endpoint=config.stream_endpoint,
        resource_id=config.stream_resource_id,
        model_name=config.model_name,
    )


class DynamicASRProvider:
    def __init__(
        self,
        settings: Any,
        ai: AISettingsService,
        http: httpx.AsyncClient | None,
    ) -> None:
        self.settings = settings
        self.ai = ai
        self.http = http

    async def transcribe(
        self,
        audio: AudioRef,
        *,
        language_hint: str | None = None,
    ) -> ASRResult:
        provider = self.ai.resolve("meetings.transcribe")
        implementation = _build_asr(provider, self.settings, self.http)
        return await implementation.transcribe(audio, language_hint=language_hint)


class DynamicRealtimeASR:
    def __init__(self, settings: Any, ai: AISettingsService) -> None:
        self.settings = settings
        self.ai = ai

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        *,
        language_hint: str | None = None,
    ) -> AsyncIterator[RealtimeEvent]:
        try:
            provider = self.ai.resolve("meetings.transcribe")
            implementation = _build_realtime_asr(provider, self.settings)
            async for event in implementation.stream(
                audio, language_hint=language_hint
            ):
                yield event
        except AIProviderError as exc:
            # The WebSocket router only turns RealtimeASRError into a clean
            # {"type":"error"} frame; anything else tears down the socket.
            raise RealtimeASRError(str(exc)) from exc
