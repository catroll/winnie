"""The bounded model/tool loop. It knows skills, not storage."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from winnie_llm import ChatResult as ModelResult

from winnie_chat.models import ChatResult, ChatTurn, SkillContext
from winnie_chat.prompt import build_system_prompt
from winnie_chat.skill import SkillRegistry

logger = logging.getLogger(__name__)


class ChatModelError(RuntimeError):
    """The configured model could not produce a turn."""


class ChatModel(Protocol):
    name: str

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResult: ...


class ChatEngine:
    def __init__(
        self,
        *,
        registry: SkillRegistry,
        model: ChatModel,
        max_tool_rounds: int = 4,
        max_tool_output_chars: int = 4_000,
    ) -> None:
        self.registry = registry
        self.model = model
        self.max_tool_rounds = max_tool_rounds
        self.max_tool_output_chars = max_tool_output_chars

    async def run(
        self,
        turn: ChatTurn,
        *,
        on_tool: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> ChatResult:
        skills = self.registry.enabled_skills(scheduled=turn.scheduled)
        schemas = self.registry.schemas(scheduled=turn.scheduled)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": build_system_prompt(turn, skills)}
        ]
        messages.extend(turn.history)

        tools_used: list[str] = []
        usage = 0.0
        rounds = 0
        while True:
            try:
                result = await self.model.chat(messages, tools=schemas)
            except Exception as exc:  # noqa: BLE001 - the host maps this to 502
                raise ChatModelError(str(exc)) from exc
            usage += float(result.usage.quantity)
            if not result.tool_calls:
                text = result.text.strip() or "(the model returned an empty reply)"
                break
            if rounds >= self.max_tool_rounds:
                logger.warning(
                    "tool-call budget exhausted after %s rounds in %s",
                    rounds,
                    turn.conversation_id,
                )
                text = (
                    result.text.strip()
                    or "I stopped because the tool-call budget for one turn ran out."
                )
                break
            rounds += 1
            messages.append(result.message)
            for call in result.tool_calls:
                skill = self.registry.skill_for_tool(
                    call.name, scheduled=turn.scheduled
                )
                arguments, error = parse_arguments(call.arguments)
                if skill is None:
                    output = (
                        f"ERROR: tool {call.name!r} is not enabled for this turn"
                    )
                elif error:
                    output = f"ERROR: {error}"
                else:
                    try:
                        output = await skill.execute(
                            call.name,
                            arguments,
                            SkillContext(
                                conversation_id=turn.conversation_id,
                                user_message=turn.user_message,
                                scheduled=turn.scheduled,
                            ),
                        )
                    except Exception as exc:  # noqa: BLE001 - a skill must not break the turn
                        logger.exception("chat skill %s failed", call.name)
                        output = f"ERROR: {type(exc).__name__}: {exc}"
                output = _truncate(output, self.max_tool_output_chars)
                tools_used.append(call.name)
                if on_tool is not None:
                    await on_tool(call.name, output)
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": output}
                )
        return ChatResult(
            text=text, tools_used=tuple(tools_used), usage_tokens=usage
        )


def parse_arguments(raw: str) -> tuple[dict[str, Any], str]:
    if not raw.strip():
        return {}, ""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"arguments are not valid JSON: {exc.msg}"
    if not isinstance(value, dict):
        return {}, "arguments must be a JSON object"
    return value, ""


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n… (truncated)"
