"""Deterministic chat model for tests and offline demos.

Selected explicitly with ``assistant.model: fake``; it is never a silent
fallback for a missing API key.
"""

from __future__ import annotations

import json
from typing import Any

from winnie_core.llm.base import ChatResult, ToolCall
from winnie_core.types import Usage

DEFAULT_FACTS = [
    {
        "category": "preferences",
        "text": "Fake fact extracted from the conversation.",
    }
]


class FakeChatModel:
    name = "fake"

    def __init__(
        self,
        *,
        reply_prefix: str = "Fake reply",
        facts: list[dict[str, str]] | None = None,
    ) -> None:
        self.reply_prefix = reply_prefix
        self.facts = DEFAULT_FACTS if facts is None else facts

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        system = str(messages[0].get("content") or "")
        last_user = next(
            (
                str(message.get("content") or "")
                for message in reversed(messages)
                if message.get("role") == "user"
            ),
            "",
        )

        if "long-term memory" in system and "Return JSON only" in system:
            payload = json.dumps(
                {
                    "facts": self.facts,
                    "nothing_new": not self.facts,
                },
                ensure_ascii=False,
            )
            return _text_result(payload)

        already_used_tool = any(message.get("role") == "tool" for message in messages)
        if tools and not already_used_tool:
            lowered = last_user.lower()
            if "remember" in lowered or "记住" in last_user:
                text = last_user.split("记住", 1)[-1].strip() or last_user
                return _tool_result(
                    ToolCall(
                        id="call_fake_remember",
                        name="memory_remember",
                        arguments=json.dumps(
                            {"category": "preferences", "text": text},
                            ensure_ascii=False,
                        ),
                    )
                )
            if any(
                marker in lowered
                for marker in ("search", "搜索", "查一下", "weather", "天气", "news", "新闻")
            ):
                return _tool_result(
                    ToolCall(
                        id="call_fake_search",
                        name="web_search",
                        arguments=json.dumps({"query": last_user}, ensure_ascii=False),
                    )
                )

        reply = f"{self.reply_prefix}: {last_user}".strip()
        return _text_result(reply)


def _text_result(text: str) -> ChatResult:
    return ChatResult(
        text=text,
        tool_calls=(),
        model="fake",
        usage=Usage(provider="fake", unit="token", quantity=0),
        message={"role": "assistant", "content": text},
    )


def _tool_result(call: ToolCall) -> ChatResult:
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
        ],
    }
    return ChatResult(
        text="",
        tool_calls=(call,),
        model="fake",
        usage=Usage(provider="fake", unit="token", quantity=0),
        message=message,
    )
