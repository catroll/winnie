"""Provider-neutral LLM access for internal capability jobs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from winnie_core.types import Usage


@dataclass(frozen=True)
class LLMCompletion:
    text: str
    model: str
    usage: Usage


@dataclass(frozen=True)
class ToolCall:
    """One function call requested by the model.

    ``arguments`` is the raw JSON string the provider returned; parsing it is
    the caller's job so a malformed payload becomes a visible tool error rather
    than a crash inside the client.
    """

    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ChatResult:
    """A full assistant turn, including any tool calls it requested.

    ``message`` is the provider's assistant message, replayed verbatim when the
    tool results are sent back. ``text`` is empty when the turn is tool-only.
    """

    text: str
    tool_calls: tuple[ToolCall, ...]
    model: str
    usage: Usage
    message: dict[str, Any]
