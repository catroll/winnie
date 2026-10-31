"""HTTP request and response models for the assistant capability."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str
    tools_used: list[str] = Field(default_factory=list)
    usage_tokens: float = 0.0


class MessageRead(BaseModel):
    role: Literal["user", "assistant", "tool"]
    name: str = ""
    text: str
    at: datetime


class ConversationSummaryRead(BaseModel):
    id: str
    title: str
    created_at: datetime
    source: str
    message_count: int
    preview: str


class ConversationListRead(BaseModel):
    items: list[ConversationSummaryRead]
    total: int


class ConversationRead(BaseModel):
    id: str
    title: str
    created_at: datetime
    source: str
    messages: list[MessageRead]


class MemoryFileRead(BaseModel):
    path: str
    category: str
    title: str
    entries: int
    pinned: bool
    updated: str


class MemoryListRead(BaseModel):
    files: list[MemoryFileRead]
    entries: int
    digest: str


class MemoryHitRead(BaseModel):
    path: str
    category: str
    text: str
    line: int
    snippet: str
    score: float


class MemorySearchRead(BaseModel):
    query: str
    hits: list[MemoryHitRead]


class DistillRead(BaseModel):
    conversations: int
    facts: int
    empty: int


class TaskRead(BaseModel):
    id: str
    title: str
    schedule: str
    prompt: str
    enabled: bool
    webhook: str
    next_run: str | None = None
    last_run: str | None = None
    last_status: str | None = None
    last_error: str | None = None


class TaskListRead(BaseModel):
    tasks: list[TaskRead]
    errors: list[str]


class TaskRunRead(BaseModel):
    task_id: str
    status: str
    started_at: datetime
    finished_at: datetime
    conversation_id: str = ""
    error: str = ""
    delivery_error: str = ""


class StatusRead(BaseModel):
    model: str
    search: str
    timezone: str
    conversations: int
    memory: dict
    scheduler: dict
