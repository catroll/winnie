"""Shared chat-engine values: skills, turns and replies."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class SkillManifest:
    name: str
    title: str
    description: str
    instructions: str = ""
    tools: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    scheduled_safe: bool = True
    toggleable: bool = True


@dataclass(frozen=True)
class SkillView:
    name: str
    title: str
    description: str
    instructions: str
    tools: tuple[str, ...]
    permissions: tuple[str, ...]
    enabled: bool
    available: bool
    unavailable_reason: str
    toggleable: bool
    scheduled_safe: bool


@dataclass(frozen=True)
class SkillContext:
    conversation_id: str
    user_message: str
    scheduled: bool = False


class Skill(Protocol):
    manifest: SkillManifest
    available: bool
    unavailable_reason: str

    def schemas(self) -> list[dict[str, Any]]: ...

    async def execute(
        self, tool: str, arguments: dict[str, Any], context: SkillContext
    ) -> str: ...


@dataclass(frozen=True)
class ChatTurn:
    conversation_id: str
    user_message: str
    history: list[dict[str, str]]
    now: datetime
    timezone: str
    memory_digest: str = ""
    # Notes the host matched to this message. Separate from the digest because
    # the digest is the always-present truth and this is per-turn relevance.
    recalled_memory: str = ""
    phone_digest: str = ""
    pending: str = ""
    scheduled: bool = False
    channel: str = "api"


@dataclass(frozen=True)
class ChatResult:
    text: str
    tools_used: tuple[str, ...] = ()
    usage_tokens: float = 0.0


@dataclass(frozen=True)
class ChatReply:
    """The public reply used by the assistant API."""

    text: str
    conversation_id: str
    tools_used: list[str] = field(default_factory=list)
    usage_tokens: float = 0.0
