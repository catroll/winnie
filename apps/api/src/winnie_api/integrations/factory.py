"""Build provider adapters from configuration.

Only the fake providers are implemented in the framework scaffold. Real
adapters (Volcengine/Aliyun ASR, LLM translation and summarization) implement
the same ports and are selected here.
"""

from __future__ import annotations

from winnie_api.integrations.asr.base import ASRProvider
from winnie_api.integrations.asr.fake import FakeASRProvider
from winnie_api.integrations.summarize.base import Summarizer
from winnie_api.integrations.summarize.fake import FakeSummarizer
from winnie_api.integrations.translate.base import Translator
from winnie_api.integrations.translate.fake import FakeTranslator
from winnie_api.settings import Settings


def build_asr_provider(settings: Settings) -> ASRProvider:
    name = settings.meetings.asr_provider
    if name == "fake":
        return FakeASRProvider()
    raise ValueError(
        f"unsupported ASR provider {name!r}; implement it under "
        "winnie_api/integrations/asr/ and register it here"
    )


def build_translator(settings: Settings) -> Translator:
    name = settings.meetings.translator
    if name == "fake":
        return FakeTranslator()
    raise ValueError(
        f"unsupported translator {name!r}; implement it under "
        "winnie_api/integrations/translate/ and register it here"
    )


def build_summarizer(settings: Settings) -> Summarizer:
    name = settings.meetings.summarizer
    if name == "fake":
        return FakeSummarizer()
    raise ValueError(
        f"unsupported summarizer {name!r}; implement it under "
        "winnie_api/integrations/summarize/ and register it here"
    )
