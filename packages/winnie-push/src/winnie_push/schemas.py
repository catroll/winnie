"""HTTP and WebSocket models for the push capability."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PushEventRead(BaseModel):
    seq: int
    id: UUID
    kind: str
    title: str
    body: str
    data: dict[str, Any] = Field(default_factory=dict)
    device_id: UUID | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None


class PushList(BaseModel):
    events: list[PushEventRead] = Field(default_factory=list)
    last_seq: int = 0


class PushStatusRead(BaseModel):
    enabled: bool
    connected: int = 0
    pending: int = 0
    last_event_at: datetime | None = None
    # Newest cursor, so a fresh client can start near the present instead of
    # replaying a month of old notifications.
    last_seq: int = 0


class PushTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="Test notification", max_length=200)
    body: str = Field(
        default="If you can see this, the background connection works.",
        max_length=4000,
    )
