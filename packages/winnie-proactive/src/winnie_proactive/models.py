"""The durable unit of proactivity: one item per (subject, occurrence, stage).

An item is the record that something was noticed, what was decided about it,
what was said, on which channels, and whether the owner acknowledged it. Push
delivery is an acceleration; the row is the truth, so a restart never forgets an
intervention that was due.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from winnie_core.db import Base

# planned -> sent -> acked, or one of the terminal/held states.
STATUSES = (
    "planned",
    "sent",
    "acked",
    "snoozed",
    "muted",
    "digested",
    "superseded",
    "expired",
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class ProactiveItem(Base):
    __tablename__ = "proactive_items"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "subject_key",
            "occurrence_key",
            "stage",
            name="uq_proactive_subject_occurrence_stage",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    # birthday | leave_by | overdue | digest | conflict
    kind: Mapped[str] = mapped_column(String(32))
    # Stable across occurrences: person:圆圆, schedule:<id>, digest:<date>:<slot>
    subject_key: Mapped[str] = mapped_column(String(256))
    # The date this stage is about, ISO: 2026-12-20.
    occurrence_key: Mapped[str] = mapped_column(String(32))
    # heads_up | prepare | confirm | day | follow_up | leave_by | overdue | digest
    stage: Mapped[str] = mapped_column(String(32))
    # 0 interrupt now, 1 notify today, 2 digest, 3 record only.
    priority: Mapped[int] = mapped_column(Integer, default=2)
    title: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    actions: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    planned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="planned", index=True)
    # When the first and the most recent send reached a channel. The budget and
    # the escalation deadlines read these rather than walking `channels`.
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # [{name, at, receipt, error}] — one entry per send attempt that reached a
    # channel, so the escalation sees what has already been tried.
    channels: Mapped[list] = mapped_column(JSON, default=list)
    conversation_id: Mapped[str] = mapped_column(String(64), default="")
    ack_token: Mapped[str] = mapped_column(String(64), default="", index=True)
    acked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acked_via: Mapped[str] = mapped_column(String(32), default="")
    feedback: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


__all__ = ["ProactiveItem", "STATUSES", "as_utc", "utcnow"]
