"""Summarization provider port and minutes types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from winnie_core.types import Usage


@dataclass(frozen=True)
class Topic:
    title: str
    points: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Decision:
    text: str
    context: str = ""


@dataclass(frozen=True)
class ActionItem:
    text: str
    owner: str | None = None
    due_date: str | None = None
    status: str = "open"


@dataclass(frozen=True)
class Highlight:
    segment_seq: int
    text: str


@dataclass(frozen=True)
class MinutesDraft:
    summary: str
    topics: list[Topic] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    action_items: list[ActionItem] = field(default_factory=list)
    highlights: list[Highlight] = field(default_factory=list)
    usage: Usage | None = None


@dataclass(frozen=True)
class TranscriptLine:
    seq: int
    text: str
    translated_text: str | None
    detected_language: str | None


@dataclass(frozen=True)
class TranscriptView:
    meeting_title: str
    local_language: str
    lines: list[TranscriptLine]


class Summarizer(Protocol):
    async def summarize(self, transcript: TranscriptView) -> MinutesDraft: ...
