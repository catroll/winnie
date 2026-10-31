"""Streaming ASR port and events for live meeting captions.

The stream consumes raw PCM chunks from an async iterator and yields cumulative
text updates. It is deliberately separate from the file adapter: the final
transcript still comes from the uploaded WAV, so a dropped stream or an
unconfigured streaming key never loses a recording.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class StreamStarted:
    """The provider accepted the audio format and is ready for chunks."""


@dataclass(frozen=True)
class TranscriptUpdate:
    """Cumulative text recognized so far; ``final`` marks the last update."""

    text: str
    final: bool = False


RealtimeEvent = StreamStarted | TranscriptUpdate


class RealtimeASRError(RuntimeError):
    """The streaming provider could not open, send, or decode the session."""


class RealtimeASRProvider(Protocol):
    async def stream(
        self,
        audio: AsyncIterator[bytes],
        *,
        language_hint: str | None = None,
    ) -> AsyncIterator[RealtimeEvent]: ...
