"""Append-only Markdown conversation files.

One file per conversation, named by its UTC creation time so lexical order is
chronological order. The file is the record: the HTTP layer, the scheduled task
runner and the memory builder all read it back instead of trusting in-process
state.
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from winnie_assistant.files import (
    atomic_write,
    collapse_whitespace,
    format_time,
    parse_time,
    render_front_matter,
    split_front_matter,
)

Role = Literal["user", "assistant", "tool"]

_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}$")
_HEADER = re.compile(
    r"^## (?P<role>user|assistant|tool:(?P<name>[A-Za-z0-9_.-]{1,40}))"
    r" · (?P<at>.+?)\s*$"
)


class ConversationNotFound(Exception):
    """The id is well-formed but no conversation file exists."""


class InvalidConversationId(Exception):
    """The id is malformed or would escape the conversation directory."""


@dataclass(frozen=True)
class Message:
    role: Role
    text: str
    at: datetime
    name: str = ""


@dataclass(frozen=True)
class Conversation:
    id: str
    title: str
    created_at: datetime
    source: str
    owner: str
    path: Path
    messages: tuple[Message, ...]


@dataclass(frozen=True)
class ConversationSummary:
    id: str
    title: str
    created_at: datetime
    source: str
    message_count: int
    preview: str


def title_from(text: str, limit: int = 60) -> str:
    """A short single-line title from the first user message."""
    line = next((item for item in text.splitlines() if item.strip()), "")
    line = collapse_whitespace(line.lstrip("#-* ").strip())
    if not line:
        return "Untitled"
    return line if len(line) <= limit else line[: limit - 1] + "…"


class ConversationStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._locks: dict[str, asyncio.Lock] = {}

    # -- paths -----------------------------------------------------------

    def path_for(self, conversation_id: str) -> Path:
        if not _ID.match(conversation_id):
            raise InvalidConversationId(conversation_id)
        day = f"{conversation_id[0:4]}-{conversation_id[4:6]}-{conversation_id[6:8]}"
        return self.root / day / f"{conversation_id}.md"

    def _lock(self, conversation_id: str) -> asyncio.Lock:
        lock = self._locks.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[conversation_id] = lock
        return lock

    # -- writes ----------------------------------------------------------

    async def create(
        self,
        *,
        first_message: str,
        source: str = "chat",
        task_id: str = "",
        owner: str = "local",
        title: str | None = None,
        now: datetime | None = None,
    ) -> Conversation:
        created = now or datetime.now(timezone.utc)
        stamp = created.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        conversation_id = f"{stamp}-{secrets.token_hex(3)}"
        resolved_title = title or title_from(first_message)
        message = Message(
            role="user",
            text=first_message.strip(),
            at=created,
        )
        header = {
            "id": conversation_id,
            "title": resolved_title,
            "created_at": format_time(created),
            "source": source,
            "owner": owner,
        }
        if task_id:
            header["task"] = task_id
        body = (
            render_front_matter(header)
            + f"\n# {resolved_title}\n"
            + _render_block(message)
        )
        path = self.path_for(conversation_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(atomic_write, path, body)
        return Conversation(
            id=conversation_id,
            title=resolved_title,
            created_at=created,
            source=source,
            owner=owner,
            path=path,
            messages=(message,),
        )

    async def append(
        self,
        conversation_id: str,
        role: Role,
        text: str,
        *,
        name: str = "",
        now: datetime | None = None,
    ) -> Message:
        if role == "tool" and not name:
            raise ValueError("tool messages need a tool name")
        message = Message(
            role=role,
            text=text.rstrip(),
            at=now or datetime.now(timezone.utc),
            name=name,
        )
        path = self.path_for(conversation_id)
        async with self._lock(conversation_id):
            if not path.is_file():
                raise ConversationNotFound(conversation_id)
            block = _render_block(message)
            await asyncio.to_thread(_append_text, path, block)
        return message

    # -- reads -----------------------------------------------------------

    def get(self, conversation_id: str) -> Conversation:
        path = self.path_for(conversation_id)
        if not path.is_file():
            raise ConversationNotFound(conversation_id)
        return _parse(path, path.read_text(encoding="utf-8"))

    def list(self, *, limit: int | None = 20) -> list[ConversationSummary]:
        paths = sorted(self.root.glob("*/*.md"), reverse=True)
        summaries: list[ConversationSummary] = []
        for path in paths:
            if not _ID.match(path.stem):
                continue
            conversation = _parse(path, path.read_text(encoding="utf-8"))
            preview = next(
                (
                    collapse_whitespace(message.text)
                    for message in conversation.messages
                    if message.role == "user"
                ),
                "",
            )
            summaries.append(
                ConversationSummary(
                    id=conversation.id,
                    title=conversation.title,
                    created_at=conversation.created_at,
                    source=conversation.source,
                    message_count=len(conversation.messages),
                    preview=preview[:160],
                )
            )
            if limit is not None and len(summaries) >= limit:
                break
        return summaries

    def count(self) -> int:
        return sum(1 for path in self.root.glob("*/*.md") if _ID.match(path.stem))

    async def get_or_create(
        self,
        *,
        first_message: str,
        conversation_id: str | None,
        source: str,
        task_id: str = "",
        owner: str = "local",
        title: str | None = None,
    ) -> Conversation:
        if conversation_id:
            conversation = self.get(conversation_id)
            await self.append(conversation_id, "user", first_message)
            return self.get(conversation_id)
        return await self.create(
            first_message=first_message,
            source=source,
            task_id=task_id,
            owner=owner,
            title=title,
        )


def _render_block(message: Message) -> str:
    if message.role == "tool":
        header = f"## tool:{message.name} · {format_time(message.at)}"
    else:
        header = f"## {message.role} · {format_time(message.at)}"
    body = message.text.strip()
    return f"\n{header}\n\n{body}\n"


def _append_text(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        # A conversation is the only copy of what was said; pay for the fsync.
        os.fsync(stream.fileno())


def _parse(path: Path, text: str) -> Conversation:
    header, body = split_front_matter(text)
    conversation_id = path.stem
    title = str(header.get("title") or "").strip()
    created_at = _header_time(header.get("created_at"))
    source = str(header.get("source") or "chat")
    owner = str(header.get("owner") or "local")

    messages: list[Message] = []
    role: Role | None = None
    name = ""
    at = created_at
    buffer: list[str] = []

    def flush() -> None:
        if role is None:
            return
        text = "\n".join(buffer).strip()
        if text:
            messages.append(Message(role=role, text=text, at=at, name=name))

    for line in body.splitlines():
        match = _HEADER.match(line)
        if match is not None:
            try:
                header_time = parse_time(match.group("at"))
            except ValueError:
                # A user can type something that looks like a block header.
                # Only a parseable timestamp makes it one.
                match = None
        if match is not None:
            flush()
            raw_role = match.group("role")
            role = "tool" if raw_role.startswith("tool:") else raw_role  # type: ignore[assignment]
            name = match.group("name") or ""
            at = header_time
            buffer = []
            continue
        if role is None and line.startswith("# "):
            title = title or line[2:].strip()
            continue
        buffer.append(line)
    flush()

    return Conversation(
        id=conversation_id,
        title=title or "Untitled",
        created_at=created_at,
        source=source,
        owner=owner,
        path=path,
        messages=tuple(messages),
    )


def _header_time(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = parse_time(value)
        except ValueError:
            return datetime.now(timezone.utc)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)
