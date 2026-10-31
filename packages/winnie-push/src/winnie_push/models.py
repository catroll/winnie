"""SQLAlchemy model for durable push notifications.

`seq` is a monotonic integer primary key and the delivery cursor: a client
reconnects with the last seq it saw and asks for everything newer. `id` is the
public identifier used by the API. Timestamps are naive UTC, as in the phone
capability, so SQLite and PostgreSQL agree.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from winnie_core.db import Base

KINDS = ("notification", "sync", "phone_command")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class PushNotification(Base):
    __tablename__ = "push_notifications"
    __table_args__ = (
        Index("ix_push_notifications_user_seq", "user_id", "seq"),
        {"sqlite_autoincrement": True},
    )

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, default=uuid.uuid4, unique=True, index=True
    )
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    # Null means "every device of this user"; a control message targets one.
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, nullable=True, index=True
    )
    # notification | sync | phone_command
    kind: Mapped[str] = mapped_column(String(32), default="notification")
    title: Mapped[str] = mapped_column(String(200), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = ["KINDS", "PushNotification", "as_utc", "utcnow"]
