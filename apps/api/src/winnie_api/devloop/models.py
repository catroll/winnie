"""Persisted error incidents, one row per distinct failure shape.

The row is an incident, not a log line: ``occurrences`` and ``last_seen`` move
on every repeat while ``first_seen`` and the traceback of the first occurrence
stay put. That is what lets the console show "this has happened 214 times since
08:12" instead of 214 rows.

Timestamps are naive UTC, as in every other model in this repository, so SQLite
and PostgreSQL agree.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from winnie_core.db import Base

from .events import utcnow


class ErrorIncident(Base):
    __tablename__ = "error_incidents"
    __table_args__ = (
        Index("ix_error_incidents_status_last_seen", "status", "last_seen"),
        Index("ix_error_incidents_service_last_seen", "service", "last_seen"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    # The dedupe key; see `ErrorEvent.fingerprint`. Unique, because one shape is
    # one incident: a second INSERT for the same key is the race this column
    # exists to lose.
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(32), default="log", index=True)
    kind: Mapped[str] = mapped_column(String(48), default="logged_error")
    service: Mapped[str] = mapped_column(String(32), default="api", index=True)
    level: Mapped[str] = mapped_column(String(16), default="error")
    message: Mapped[str] = mapped_column(String(1000), default="")
    exception_type: Mapped[str] = mapped_column(String(200), default="")
    # "file.py:123" — the innermost frame inside this checkout, when known.
    location: Mapped[str] = mapped_column(String(400), default="")
    traceback: Mapped[str] = mapped_column(Text, default="")
    # Route, method, status, pid, probe URL — whatever the source could supply.
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ignored_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # A read-only diagnosis: the structured answer, the raw model text, the
    # model that produced it and when. Null until the owner asks for one.
    diagnosis: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    diagnosis_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
