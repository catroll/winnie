"""HTTP request and response models for the assistant capability."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    conversation_id: str | None = None
    # Where the turn arrived. `task` is reserved for the scheduler; a chat
    # client cannot claim to be one. The default keeps older clients working.
    channel: Literal["admin", "web", "api"] = "api"
    # How the message was produced. A dictated turn is answered for the ear:
    # the host adds a concision instruction to the system prompt, because a
    # Markdown answer that is pleasant to read is noise to listen to. Older
    # clients omit it and mean text.
    input: Literal["text", "voice"] = "text"


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
    channel: str
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
    channel: str
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


class DocumentHitRead(BaseModel):
    path: str
    root: str
    heading: str
    start_line: int
    end_line: int
    snippet: str
    mtime: str
    also_at: list[str] = Field(default_factory=list)


class DocumentSearchRead(BaseModel):
    query: str
    hits: list[DocumentHitRead]


class DistillRead(BaseModel):
    conversations: int
    facts: int
    empty: int

class MemoryTidyStatusRead(BaseModel):
    enabled: bool
    schedule: str
    next_run: str | None = None
    last_run: str | None = None
    last_status: str | None = None
    last_summary: str = ""
    last_conversation_id: str | None = None
    last_error: str = ""
    similarity: float = 0.55
    retire_min_age_days: int = 7
    notify: bool = False


class MemoryTidyRunRead(BaseModel):
    status: str
    merges: int = 0
    retired: int = 0
    files: list[str] = Field(default_factory=list)
    message: str = ""
    conversation_id: str = ""
    checked_at: datetime
    error: str = ""
    rejected: int = 0



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


class TaskRunListRead(BaseModel):
    runs: list[TaskRunRead]
    total: int
    offset: int
    limit: int
    # Lines the log reader could not parse (for example a partial last line).
    skipped: int = 0


class SkillViewRead(BaseModel):
    name: str
    title: str
    description: str
    instructions: str
    tools: list[str]
    permissions: list[str]
    enabled: bool
    available: bool
    unavailable_reason: str
    toggleable: bool
    scheduled_safe: bool


class SkillListRead(BaseModel):
    skills: list[SkillViewRead]


class SkillToggleRequest(BaseModel):
    enabled: bool


class StatusRead(BaseModel):
    model: str
    search: str
    timezone: str
    root: str
    conversations: int
    skills: int = 0
    memory: dict
    index: dict
    scheduler: dict
    weather: dict = Field(default_factory=dict)
    gold: dict = Field(default_factory=dict)


class WeatherStatusRead(BaseModel):
    enabled: bool
    location: str
    schedule: str
    next_run: str | None = None
    last_run: str | None = None
    last_status: str | None = None
    last_conversation_id: str | None = None
    webhook: bool = False


class WeatherCheckRead(BaseModel):
    status: str
    message: str = ""
    conversation_id: str = ""
    advisories: list[str] = Field(default_factory=list)
    checked_at: datetime
    error: str = ""


class GoldStatusRead(BaseModel):
    enabled: bool
    symbol: str
    schedule: str
    window_minutes: int
    threshold_percent: float
    daily_threshold_percent: float
    cooldown_minutes: int
    next_poll: str | None = None
    last_poll: str | None = None
    last_status: str | None = None
    last_price: float | None = None
    last_alert_at: str | None = None
    push: bool = False


class GoldMoveRead(BaseModel):
    direction: str
    percent: float
    from_price: float
    to_price: float
    window_minutes: int
    daily_percent: float | None = None


class GoldCheckRead(BaseModel):
    status: str
    message: str = ""
    price: float | None = None
    move: GoldMoveRead | None = None
    checked_at: datetime
    error: str = ""
