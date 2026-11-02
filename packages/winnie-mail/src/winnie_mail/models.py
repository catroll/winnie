"""The local mail cache: folders, messages and the sync cursors.

Mail is remote, slow and rate-limited, so Winnie does not read it on every
question. The cache holds what was fetched last time — headers for every
selectable folder, bodies only when someone opened a message — and the cursor
that makes the next fetch incremental:

* IMAP keeps ``uid_validity`` and ``last_uid`` per folder. A changed
  ``UIDVALIDITY`` invalidates every cached uid in that folder, so the folder is
  refetched instead of silently missing new mail.
* POP3 has no folders and no uids that are stable across sessions except the
  ``UIDL``, so the cursor is the set of cached uids and the server's own UIDL
  list decides what is new and what was deleted.

Timestamps are naive UTC, as elsewhere in the project, so SQLite and PostgreSQL
agree.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from winnie_core.db import Base


def iso_utc(value: datetime | None) -> str:
    """Serialise a stored naive-UTC datetime with an explicit zone.

    The tables store naive UTC, and a bare ``isoformat()`` leaves every reader to
    guess: the console guesses "UTC", but a language model shown
    ``2026-11-02T14:00`` says "14:00 your time" and is eight hours wrong. The
    trailing ``Z`` makes the instant unambiguous on the wire.
    """
    if value is None:
        return ""
    return value.isoformat(timespec="seconds") + "Z"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MailFolder(Base):
    """One mailbox folder, with the cursor for its next incremental fetch."""

    __tablename__ = "mail_folders"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "protocol", "name", name="uq_mail_folders_account_folder"
        ),
        Index("ix_mail_folders_user_synced", "user_id", "last_synced_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    # imap | pop3 - the two protocols can name a folder the same way.
    protocol: Mapped[str] = mapped_column(String(8))
    # The name as the server spells it (IMAP uses modified UTF-7 for non-ASCII).
    name: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(255), default="")
    delimiter: Mapped[str] = mapped_column(String(8), default="")
    selectable: Mapped[bool] = mapped_column(Boolean, default=True)
    # IMAP only: 0 when the server did not report one.
    uid_validity: Mapped[int] = mapped_column(Integer, default=0)
    # IMAP only: the highest UID fetched, so the next pass asks for UID > this.
    last_uid: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    unread: Mapped[int] = mapped_column(Integer, default=0)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str] = mapped_column(String(500), default="")

    def view(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name or self.name,
            "delimiter": self.delimiter,
            "selectable": self.selectable,
            "protocol": self.protocol,
            "uid_validity": self.uid_validity,
            "last_uid": self.last_uid,
            "total": self.total,
            "unread": self.unread,
            "last_synced_at": iso_utc(self.last_synced_at),
            "last_error": self.last_error,
        }


class MailMessage(Base):
    """One cached message.

    ``body``, ``body_html`` and ``body_raw`` are NULL until someone opens it:
    the sweep stores headers, and the reading pane's three tabs are filled by the
    one download that an open performs.
    """

    __tablename__ = "mail_messages"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "protocol", "folder", "uid",
            name="uq_mail_messages_account_message",
        ),
        Index("ix_mail_messages_user_date", "user_id", "date"),
        Index("ix_mail_messages_user_folder", "user_id", "protocol", "folder"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    protocol: Mapped[str] = mapped_column(String(8))
    folder: Mapped[str] = mapped_column(String(255))
    # IMAP UID or POP3 UIDL, as a string: POP3 has no numeric uid.
    uid: Mapped[str] = mapped_column(String(128))
    message_id: Mapped[str] = mapped_column(String(255), default="")
    subject: Mapped[str] = mapped_column(String(500), default="")
    sender: Mapped[str] = mapped_column(String(500), default="")
    recipients: Mapped[str] = mapped_column(String(1000), default="")
    date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    snippet: Mapped[str] = mapped_column(String(500), default="")
    unread: Mapped[bool] = mapped_column(Boolean, default=False)
    size: Mapped[int] = mapped_column(Integer, default=0)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The sender's markup, rendered in a sandbox, and the RFC 822 source for the
    # "Raw" tab. Both are capped by the parser, not by the column.
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def view(self, *, with_body: bool = False) -> dict:
        payload = {
            "id": str(self.id),
            "folder": self.folder,
            "protocol": self.protocol,
            "uid": self.uid,
            "subject": self.subject,
            "sender": self.sender,
            "recipients": self.recipients,
            "date": iso_utc(self.date),
            "snippet": self.snippet,
            "unread": self.unread,
            "size": self.size,
            "has_body": self.body_fetched_at is not None,
            "has_html": bool(self.body_html),
            "has_raw": bool(self.body_raw),
            "message_id": self.message_id,
        }
        if with_body:
            payload["body"] = self.body or ""
            payload["body_html"] = self.body_html or ""
            payload["body_raw"] = self.body_raw or ""
        return payload


__all__ = ["MailFolder", "MailMessage", "iso_utc", "utcnow"]
