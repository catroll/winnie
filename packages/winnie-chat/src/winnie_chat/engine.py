"""The bounded model/tool loop. It knows skills, not storage."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

from winnie_llm import ChatResult as ModelResult
from winnie_llm import StreamPart

from winnie_chat.models import ChatEvent, ChatResult, ChatTurn, SkillContext
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

    async def run_stream(
        self,
        turn: ChatTurn,
        *,
        on_tool: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> AsyncIterator[ChatEvent]:
        """The same loop as ``run``, emitting text as the model produces it.

        The only behavioural difference is what the owner sees while waiting:
        text arrives in fragments and the tools announce themselves. Everything
        that decides the outcome — the tool budget, the arguments parsing, the
        error mapping, recording each tool output — is the same code path.

        A model without a ``stream`` method (a scripted fake in a test) is
        adapted by calling ``chat`` once and emitting its text as one fragment,
        so a streaming route can be tested without a vendor.
        """
        skills = self.registry.enabled_skills(scheduled=turn.scheduled)
        schemas = self.registry.schemas(scheduled=turn.scheduled)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": build_system_prompt(turn, skills)}
        ]
        messages.extend(turn.history)

        tools_used: list[str] = []
        usage = 0.0
        rounds = 0
        emitted: list[str] = []
        while True:
            result: ModelResult | None = None
            streamed = 0
            async for part in self._stream_round(messages, schemas):
                if part.result is not None:
                    result = part.result
                elif part.text:
                    streamed += len(part.text)
                    emitted.append(part.text)
                    yield ChatEvent(kind="token", text=part.text)
            if result is None:  # pragma: no cover - a stream without a result
                raise ChatModelError("the model stream ended without a reply")
            # A model without ``stream`` carries its text inside the result, so
            # the round emitted nothing. Forward it as one fragment instead of
            # letting the turn look empty.
            if not streamed and result.text:
                emitted.append(result.text)
                yield ChatEvent(kind="token", text=result.text)
            usage += float(result.usage.quantity)
            if not result.tool_calls:
                if not "".join(emitted).strip():
                    fallback = "(the model returned an empty reply)"
                    emitted.append(fallback)
                    yield ChatEvent(kind="token", text=fallback)
                break
            if rounds >= self.max_tool_rounds:
                logger.warning(
                    "tool-call budget exhausted after %s rounds in %s",
                    rounds,
                    turn.conversation_id,
                )
                stop = (
                    result.text.strip()
                    or "I stopped because the tool-call budget for one turn ran out."
                )
                emitted.append(stop)
                yield ChatEvent(kind="token", text=stop)
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
                yield ChatEvent(kind="tool", name=call.name)
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": output}
                )
        yield ChatEvent(
            kind="done",
            text="".join(emitted).strip(),
            tools_used=tuple(tools_used),
            usage_tokens=usage,
        )

    async def _stream_round(
        self, messages: list[dict[str, Any]], schemas: list[dict[str, Any]]
    ) -> AsyncIterator[Any]:
        """One model round, streamed when the model supports it."""
        stream = getattr(self.model, "stream", None)
        if stream is None:
            try:
                result = await self.model.chat(messages, tools=schemas)
            except Exception as exc:  # noqa: BLE001 - the host maps this to 502
                raise ChatModelError(str(exc)) from exc
            yield _SinglePart(result)
            return
        try:
            async for part in stream(messages, tools=schemas):
                yield part
        except ChatModelError:
            raise
        except Exception as exc:  # noqa: BLE001 - the host maps this to 502
            raise ChatModelError(str(exc)) from exc


def _SinglePart(result: ModelResult) -> StreamPart:
    """Adapt a non-streaming model to the streamed-round shape."""
    return StreamPart(result=result)


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
