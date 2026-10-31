"""Deterministic ASR provider for tests and local demos.

It never touches the network. Replace it with a real adapter before relying on
the output for actual meetings.
"""

from __future__ import annotations

from winnie_meetings.integrations.asr.base import ASRProvider, ASRResult, ASRSegment
from winnie_core.types import AudioRef, Usage


class FakeASRProvider(ASRProvider):
    async def transcribe(
        self,
        audio: AudioRef,
        *,
        language_hint: str | None = None,
    ) -> ASRResult:
        _ = audio, language_hint
        return ASRResult(
            segments=[
                ASRSegment(
                    text="大家好，今天讨论会议速记的 MVP 范围。",
                    language="zh",
                    start_ms=0,
                    end_ms=3000,
                ),
                ASRSegment(
                    text="We should validate the file-based workflow before realtime.",
                    language="en",
                    start_ms=3000,
                    end_ms=7000,
                ),
                ASRSegment(
                    text="先以录音文件转写为主路径。",
                    language="zh",
                    start_ms=7000,
                    end_ms=10000,
                ),
            ],
            usage=Usage(provider="fake-asr", unit="audio_second", quantity=10.0),
        )
