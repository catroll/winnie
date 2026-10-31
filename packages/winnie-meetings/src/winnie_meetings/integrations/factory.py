"""Build provider adapters from configuration.

The fake providers keep tests and local demos deterministic. Volcengine
Doubao is the first real ASR adapter, and the LLM adapters use one internal
OpenAI-compatible client for translation and structured minutes.
"""

from __future__ import annotations

import os

from winnie_core.config import LLMConfig
from winnie_meetings.config import MeetingsConfig
from winnie_meetings.integrations.asr.base import ASRProvider
from winnie_meetings.integrations.asr.fake import FakeASRProvider, FakeRealtimeASR
from winnie_meetings.integrations.asr.realtime import RealtimeASRProvider
from winnie_meetings.integrations.asr.volcengine import (
    API_KEY_ENV,
    VolcengineASRProvider,
)
from winnie_meetings.integrations.asr.volcengine_stream import (
    VolcengineRealtimeASR,
)
from winnie_core.llm.factory import build_llm
from winnie_meetings.integrations.summarize.base import Summarizer
from winnie_meetings.integrations.summarize.fake import FakeSummarizer
from winnie_meetings.integrations.summarize.llm import LLMSummarizer
from winnie_meetings.integrations.translate.base import Translator
from winnie_meetings.integrations.translate.fake import FakeTranslator
from winnie_meetings.integrations.translate.llm import LLMTranslator


def build_asr_provider(config: MeetingsConfig) -> ASRProvider:
    name = config.asr_provider
    if name == "fake":
        return FakeASRProvider()
    if name == "volcengine":
        asr = config.volcengine
        return VolcengineASRProvider(
            api_key=os.environ.get(API_KEY_ENV, ""),
            base_url=asr.base_url,
            resource_id=asr.resource_id,
            model_name=asr.model_name,
            max_inline_bytes=asr.max_inline_mb * 1024 * 1024,
            poll_interval_seconds=asr.poll_interval_seconds,
            poll_timeout_seconds=asr.poll_timeout_seconds,
        )
    raise ValueError(
        f"unsupported ASR provider {name!r}; implement it under "
        "winnie_meetings/integrations/asr/ and register it here"
    )


def build_realtime_asr_provider(config: MeetingsConfig) -> RealtimeASRProvider:
    name = config.asr_provider
    if name == "fake":
        return FakeRealtimeASR()
    if name == "volcengine":
        asr = config.volcengine
        return VolcengineRealtimeASR(
            api_key=os.environ.get(API_KEY_ENV, ""),
            endpoint=asr.stream_endpoint,
            resource_id=asr.stream_resource_id,
            model_name=asr.model_name,
        )
    raise ValueError(
        f"unsupported realtime ASR provider {name!r}; implement it under "
        "winnie_meetings/integrations/asr/ and register it here"
    )


def build_translator(
    config: MeetingsConfig,
    llm: LLMConfig,
) -> Translator:
    name = config.translator
    if name == "fake":
        return FakeTranslator()
    if name == "openai_compatible":
        return LLMTranslator(
            build_llm(llm),
            max_batch_chars=llm.translation_batch_chars,
        )
    raise ValueError(
        f"unsupported translator {name!r}; implement it under "
        "winnie_meetings/integrations/translate/ and register it here"
    )


def build_summarizer(
    config: MeetingsConfig,
    llm: LLMConfig,
) -> Summarizer:
    name = config.summarizer
    if name == "fake":
        return FakeSummarizer()
    if name == "openai_compatible":
        return LLMSummarizer(
            build_llm(llm),
            max_chunk_chars=llm.summary_chunk_chars,
        )
    raise ValueError(
        f"unsupported summarizer {name!r}; implement it under "
        "winnie_meetings/integrations/summarize/ and register it here"
    )
