"""Build provider adapters from configuration.

The fake providers keep tests and local demos deterministic. Volcengine
Doubao is the first real ASR adapter, and the LLM adapters use one internal
OpenAI-compatible client for translation and structured minutes.
"""

from __future__ import annotations

import os

from winnie_api.integrations.asr.base import ASRProvider
from winnie_api.integrations.asr.fake import FakeASRProvider
from winnie_api.integrations.asr.volcengine import (
    API_KEY_ENV,
    VolcengineASRProvider,
)
from winnie_api.integrations.llm.factory import build_llm
from winnie_api.integrations.summarize.base import Summarizer
from winnie_api.integrations.summarize.fake import FakeSummarizer
from winnie_api.integrations.summarize.llm import LLMSummarizer
from winnie_api.integrations.translate.base import Translator
from winnie_api.integrations.translate.fake import FakeTranslator
from winnie_api.integrations.translate.llm import LLMTranslator
from winnie_api.settings import Settings


def build_asr_provider(settings: Settings) -> ASRProvider:
    name = settings.meetings.asr_provider
    if name == "fake":
        return FakeASRProvider()
    if name == "volcengine":
        config = settings.meetings.volcengine
        return VolcengineASRProvider(
            api_key=os.environ.get(API_KEY_ENV, ""),
            base_url=config.base_url,
            resource_id=config.resource_id,
            model_name=config.model_name,
            max_inline_bytes=config.max_inline_mb * 1024 * 1024,
            poll_interval_seconds=config.poll_interval_seconds,
            poll_timeout_seconds=config.poll_timeout_seconds,
        )
    raise ValueError(
        f"unsupported ASR provider {name!r}; implement it under "
        "winnie_api/integrations/asr/ and register it here"
    )


def build_translator(settings: Settings) -> Translator:
    name = settings.meetings.translator
    if name == "fake":
        return FakeTranslator()
    if name == "openai_compatible":
        return LLMTranslator(
            build_llm(settings),
            max_batch_chars=settings.llm.translation_batch_chars,
        )
    raise ValueError(
        f"unsupported translator {name!r}; implement it under "
        "winnie_api/integrations/translate/ and register it here"
    )


def build_summarizer(settings: Settings) -> Summarizer:
    name = settings.meetings.summarizer
    if name == "fake":
        return FakeSummarizer()
    if name == "openai_compatible":
        return LLMSummarizer(build_llm(settings))
    raise ValueError(
        f"unsupported summarizer {name!r}; implement it under "
        "winnie_api/integrations/summarize/ and register it here"
    )
