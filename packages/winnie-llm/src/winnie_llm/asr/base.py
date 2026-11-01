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


class ASRError(RuntimeError):
    """The provider could not transcribe the audio.

    A caller that maps failures onto HTTP status codes has to tell a provider
    failure apart from a request the caller got wrong and from audio that simply
    held no speech; a bare ``RuntimeError`` collapses all three into one 500.
    Both this class and :class:`ASRNoSpeech` stay ``RuntimeError`` subclasses, so
    every existing ``except RuntimeError`` handler keeps working.
    """


class ASRNoSpeech(ASRError):
    """The provider decoded the audio and found no speech in it.

    This is a property of the recording, not a failed call: the vendor answered
    successfully with "no valid speech in audio" (Volcengine code 20000003) or
    returned a result with no text. A caller should answer with a 4xx.
    """


class ASRProvider(Protocol):
    async def transcribe(
        self,
        audio: AudioRef,
        *,
        language_hint: str | None = None,
    ) -> ASRResult: ...
