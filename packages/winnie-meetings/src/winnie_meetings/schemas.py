"""API request and response models for the meetings context."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MeetingCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    local_language: str | None = Field(default=None, max_length=16)


class MeetingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    status: str
    stage: str | None
    source: str
    local_language: str
    audio_duration_ms: int | None
    audio_deleted_at: datetime | None = None
    error: str | None
    created_at: datetime
    updated_at: datetime


class MeetingList(BaseModel):
    items: list[MeetingRead]
    total: int


class SegmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    start_ms: int | None
    end_ms: int | None
    speaker: str | None
    text: str
    detected_language: str | None
    translated_text: str | None
    translation_status: str


class TranscriptRead(BaseModel):
    meeting_id: UUID
    segments: list[SegmentRead]


class TopicRead(BaseModel):
    title: str
    points: list[str] = Field(default_factory=list)


class DecisionRead(BaseModel):
    text: str
    context: str = ""


class ActionItemRead(BaseModel):
    text: str
    owner: str | None = None
    due_date: str | None = None
    status: str = "open"


class HighlightRead(BaseModel):
    segment_seq: int
    text: str


class MinutesRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    meeting_id: UUID
    summary: str
    topics: list[TopicRead]
    decisions: list[DecisionRead]
    action_items: list[ActionItemRead]
    highlights: list[HighlightRead]
    model: str
    prompt_version: str
    created_at: datetime
