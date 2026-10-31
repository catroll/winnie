"""Small shared value types.

``ChatReply`` lives here rather than in ``agent`` so the scheduler can name it
without importing the agent, which imports the tools, which import the
scheduler.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChatReply:
    text: str
    conversation_id: str
    tools_used: list[str] = field(default_factory=list)
    usage_tokens: float = 0.0
