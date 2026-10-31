"""Build static ASR adapters from configuration.

Model providers (translation, minutes, assistant) are chosen in the AI
registry; only ASR still has a YAML default for deployments that do not enable
the registry. The fake providers keep tests and local demos deterministic.
"""

from __future__ import annotations

import os

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
