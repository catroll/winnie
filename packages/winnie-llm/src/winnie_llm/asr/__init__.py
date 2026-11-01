"""Speech-to-text: ports, vendor adapters and deterministic fakes.

One package owns both the recording-file and the streaming adapter, so a
capability never imports a vendor module or reads a speech credential.
"""

from winnie_llm.asr.base import (
    ASRError,
    ASRNoSpeech,
    ASRProvider,
    ASRResult,
    ASRSegment,
)
from winnie_llm.asr.config import VolcengineASRConfig
from winnie_llm.asr.fake import FakeASRProvider, FakeRealtimeASR
from winnie_llm.asr.realtime import (
    RealtimeASRError,
    RealtimeASRProvider,
    RealtimeEvent,
    StreamStarted,
    TranscriptUpdate,
)
from winnie_llm.asr.volcengine import VolcengineASRProvider
from winnie_llm.asr.volcengine_stream import VolcengineRealtimeASR

__all__ = [
    "ASRError",
    "ASRNoSpeech",
    "ASRProvider",
    "ASRResult",
    "ASRSegment",
    "FakeASRProvider",
    "FakeRealtimeASR",
    "RealtimeASRError",
    "RealtimeASRProvider",
    "RealtimeEvent",
    "StreamStarted",
    "TranscriptUpdate",
    "VolcengineASRConfig",
    "VolcengineASRProvider",
    "VolcengineRealtimeASR",
]
