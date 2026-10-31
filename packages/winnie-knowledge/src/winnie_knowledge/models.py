"""Inputs the knowledge sink needs, independent of the meetings package."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class MeetingNote:
    id: UUID
    title: str
    local_language: str
    created_at: datetime


@dataclass(frozen=True)
class NoteTopic:
    title: str
    points: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NoteDecision:
    text: str
    context: str = ""


@dataclass(frozen=True)
class NoteActionItem:
    text: str
    owner: str | None = None
    due_date: str | None = None


@dataclass(frozen=True)
class NoteHighlight:
    segment_seq: int
    text: str


@dataclass(frozen=True)
class NoteContent:
    summary: str
    topics: list[NoteTopic] = field(default_factory=list)
    decisions: list[NoteDecision] = field(default_factory=list)
    action_items: list[NoteActionItem] = field(default_factory=list)
    highlights: list[NoteHighlight] = field(default_factory=list)
