"""Feature-facing adapters used by the host.

Each wrapper resolves the current binding on every call, so switching a model in
the console takes effect on the next turn without a restart. Everything that is
vendor-specific — endpoint, credential, request shape — belongs to
``winnie-llm``; this module only chooses between a real client and an offline
fake for a feature that supports one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from winnie_assistant.agent import ChatModel
from winnie_assistant.fake import FakeChatModel
from winnie_core.types import AudioRef
from winnie_core.usage.ledger import UsageSink
from winnie_llm import (
    AIProviderError,
    ASRProvider,
    ASRResult,
    ChatResult,
    ProviderRegistry,
    RealtimeASRError,
    RealtimeASRProvider,
    RealtimeEvent,
    StreamPart,
)
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


class DynamicChatModel:
    """One ``ChatModel`` whose concrete model is resolved per turn."""

    name = "configured"

    def __init__(
        self,
        registry: ProviderRegistry,
        http: httpx.AsyncClient | None,
        usage: UsageSink | None = None,
    ) -> None:
        self.registry = registry
        self.http = http
        self.usage = usage

    @property
    def name(self) -> str:  # type: ignore[override]
        try:
            provider = self.registry.resolve("assistant.chat")
        except AIProviderError:
            return "unavailable"
        return provider.model or provider.integration_name

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        provider = self.registry.resolve("assistant.chat")
        if provider.kind == "fake":
            model: ChatModel = FakeChatModel()
        elif provider.kind == "openai":
            model = self.registry.chat_for(
                "assistant.chat", http=self.http, usage=self.usage
            )
        else:
            raise AIProviderError(
                f"assistant.chat cannot use a {provider.kind} supplier; "
                "choose an OpenAI-compatible one"
            )
        return await model.chat(messages, tools)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ):
        """The same turn, streamed when the supplier supports it.

        A scripted fake has no stream, so it is adapted: the engine and every
        caller above it see one fragment and a result.
        """
        provider = self.registry.resolve("assistant.chat")
        if provider.kind == "fake":
            model: Any = FakeChatModel()
        elif provider.kind == "openai":
            model = self.registry.chat_for(
                "assistant.chat", http=self.http, usage=self.usage
            )
        else:
            raise AIProviderError(
                f"assistant.chat cannot use a {provider.kind} supplier; "
                "choose an OpenAI-compatible one"
            )
        stream = getattr(model, "stream", None)
        if stream is None:
            yield StreamPart(result=await model.chat(messages, tools=tools))
            return
        async for part in stream(messages, tools=tools):
            yield part


class DynamicTranslator:
    def __init__(
        self,
        registry: ProviderRegistry,
        http: httpx.AsyncClient | None,
        usage: UsageSink | None = None,
    ) -> None:
        self.registry = registry
        self.http = http
        self.usage = usage

    async def translate(
        self,
        texts: list[str],
        *,
        source: str | None,
        target: str,
    ) -> TranslationResult:
        provider = self.registry.resolve("meetings.translate")
        if provider.kind == "fake":
            implementation: Translator = FakeTranslator()
        elif provider.kind == "openai":
            runtime = self.registry.runtime()
            implementation = LLMTranslator(
                self.registry.chat_for(
                    "meetings.translate", http=self.http, usage=self.usage
                ),
                max_batch_chars=runtime.translation_batch_chars,
            )
        else:
            raise AIProviderError(
                f"meetings.translate cannot use a {provider.kind} supplier; "
                "choose an OpenAI-compatible one"
            )
        return await implementation.translate(texts, source=source, target=target)


class DynamicSummarizer:
    def __init__(
        self,
        registry: ProviderRegistry,
        http: httpx.AsyncClient | None,
        usage: UsageSink | None = None,
    ) -> None:
        self.registry = registry
        self.http = http
        self.usage = usage

    async def summarize(self, transcript: TranscriptView) -> MinutesDraft:
        provider = self.registry.resolve("meetings.summarize")
        if provider.kind == "fake":
            implementation: Summarizer = FakeSummarizer()
        elif provider.kind == "openai":
            runtime = self.registry.runtime()
            implementation = LLMSummarizer(
                self.registry.chat_for(
                    "meetings.summarize", http=self.http, usage=self.usage
                ),
                max_chunk_chars=runtime.summary_chunk_chars,
            )
        else:
            raise AIProviderError(
                f"meetings.summarize cannot use a {provider.kind} supplier; "
                "choose an OpenAI-compatible one"
            )
        return await implementation.summarize(transcript)


class DynamicASRProvider:
    def __init__(
        self, registry: ProviderRegistry, http: httpx.AsyncClient | None
    ) -> None:
        self.registry = registry
        self.http = http

    async def transcribe(
        self,
        audio: AudioRef,
        *,
        language_hint: str | None = None,
    ) -> ASRResult:
        implementation: ASRProvider = self.registry.asr_for(
            "meetings.transcribe", http=self.http
        )
        return await implementation.transcribe(audio, language_hint=language_hint)


class DynamicRealtimeASR:
    def __init__(self, registry: ProviderRegistry) -> None:
        self.registry = registry

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        *,
        language_hint: str | None = None,
    ) -> AsyncIterator[RealtimeEvent]:
        try:
            implementation: RealtimeASRProvider = self.registry.realtime_asr_for(
                "meetings.transcribe"
            )
            async for event in implementation.stream(
                audio, language_hint=language_hint
            ):
                yield event
        except AIProviderError as exc:
            # The WebSocket router only turns RealtimeASRError into a clean
            # {"type":"error"} frame; anything else tears down the socket.
            raise RealtimeASRError(str(exc)) from exc
