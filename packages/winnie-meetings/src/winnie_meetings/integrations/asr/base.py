"""ASR provider port and result types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from winnie_core.types import AudioRef, Usage


@dataclass(frozen=True)
class ASRSegment:
    text: str
    language: str | None = None
    start_ms: int = 0
    end_ms: int = 0
    speaker: str | None = None
    language_confidence: float | None = None


@dataclass(frozen=True)
class ASRResult:
    segments: list[ASRSegment]
    usage: Usage | None = None


class ASRProvider(Protocol):
    async def transcribe(
        self,
        audio: AudioRef,
        *,
        language_hint: str | None = None,
    ) -> ASRResult: ...
