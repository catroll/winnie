"""Assistant adapter: persistence around the dedicated chat engine.

The model/tool loop, skill selection and prompt assembly live in
``winnie-chat``. This module only creates the conversation, records the user
message and tool traces, and stores the final assistant reply.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from winnie_chat import ChatEngine, ChatEvent, ChatModel, ChatModelError, ChatTurn

from winnie_assistant.conversations import Conversation, ConversationStore
from winnie_assistant.memory import MemoryStore
from winnie_assistant.tools import ToolBox
from winnie_assistant.types import ChatReply

logger = logging.getLogger(__name__)


class AssistantModelError(RuntimeError):
    """The configured model could not produce a turn."""


@dataclass(frozen=True)
class PreparedTurn:
    """A conversation that has accepted the user message, ready to stream."""

    conversation_id: str
    turn: ChatTurn


class AssistantAgent:
    def __init__(
        self,
        *,
        store: ConversationStore,
        memory: MemoryStore | None,
        toolbox: ToolBox,
        engine: ChatEngine,
        max_history_messages: int = 30,
        timezone: ZoneInfo,
        clock: Any = None,
        recall_limit: int = 6,
        recall_chars: int = 1200,
        recall_context_messages: int = 6,
    ) -> None:
        self.store = store
        self.memory = memory
        self.toolbox = toolbox
        self.engine = engine
        self.max_history_messages = max_history_messages
        self.timezone = timezone
        self.clock = clock or (lambda: datetime.now(timezone))
        # Per-turn retrieval. `recall_limit = 0` disables it; the pinned digest
        # is unaffected either way.
        self.recall_limit = recall_limit
        self.recall_chars = recall_chars
        self.recall_context_messages = recall_context_messages

    async def chat(
        self,
        text: str,
        *,
        conversation_id: str | None = None,
        source: str = "chat",
        channel: str = "api",
        task_id: str = "",
        title: str | None = None,
        owner: str = "local",
        modality: str = "text",
    ) -> ChatReply:
        conversation, turn = await self._prepare(
            text,
            conversation_id=conversation_id,
            source=source,
            channel=channel,
            task_id=task_id,
            title=title,
            owner=owner,
            modality=modality,
        )

        async def on_tool(name: str, output: str) -> None:
            await self.store.append(conversation.id, "tool", output, name=name)

        try:
            result = await self.engine.run(turn, on_tool=on_tool)
        except ChatModelError as exc:
            raise AssistantModelError(str(exc)) from exc

        await self.store.append(conversation.id, "assistant", result.text)
        return ChatReply(
            text=result.text,
            conversation_id=conversation.id,
            tools_used=list(result.tools_used),
            usage_tokens=result.usage_tokens,
        )

    async def _prepare(
        self,
        text: str,
        *,
        conversation_id: str | None,
        source: str,
        channel: str,
        task_id: str,
        title: str | None,
        owner: str,
        modality: str,
    ) -> tuple[Conversation, ChatTurn]:
        """Create the conversation and assemble one turn's prompt context."""
        conversation = await self.store.get_or_create(
            first_message=text,
            conversation_id=conversation_id,
            source=source,
            channel=channel,
            task_id=task_id,
            title=title,
            owner=owner,
        )
        # One turn counter per user message. A pending local action can only
        # execute in a later turn, after an explicit confirmation.
        self.toolbox.begin_turn(conversation.id, text)
        phone_digest = ""
        if self.toolbox.phone is not None:
            try:
                phone_digest = await self.toolbox.phone_digest(self.clock())
            except Exception:  # noqa: BLE001 - a digest must not fail a turn
                logger.warning("phone digest failed", exc_info=True)
        history = [
            {"role": message.role, "content": message.text}
            for message in conversation.messages
            if message.role in {"user", "assistant"} and message.text.strip()
        ][-self.max_history_messages :]
        # The digest is the pinned truth; recall adds the unpinned notes that
        # share wording with this message *and with the exchange so far*, because
        # a follow-up ("那明天呢？") carries no subject words of its own. The
        # current message is the last history entry, so the context is everything
        # before it. This runs in-process, so a note written a second ago is
        # already visible — the index would still be minutes behind its sweep.
        recalled = ""
        if self.memory is not None and self.recall_limit > 0:
            context = [
                str(item["content"])
                for item in history[:-1]
                if str(item.get("content") or "").strip()
            ][-self.recall_context_messages :] if self.recall_context_messages > 0 else []
            try:
                recalled = self.memory.recall(
                    text,
                    context=context,
                    limit=self.recall_limit,
                    chars=self.recall_chars,
                )
            except Exception:  # noqa: BLE001 - recall must not fail a turn
                logger.warning("memory recall failed", exc_info=True)
        turn = ChatTurn(
            conversation_id=conversation.id,
            user_message=text,
            history=history,
            now=self.clock(),
            timezone=self.timezone.key,
            memory_digest=self.memory.digest() if self.memory is not None else "",
            recalled_memory=recalled,
            phone_digest=phone_digest,
            pending=self.toolbox.pending_summary(conversation.id),
            scheduled=source == "task",
            channel=channel,
            modality=modality,
        )

        return conversation, turn

    async def open(
        self,
        text: str,
        *,
        conversation_id: str | None = None,
        source: str = "chat",
        channel: str = "api",
        task_id: str = "",
        title: str | None = None,
        owner: str = "local",
        modality: str = "text",
    ) -> PreparedTurn:
        """Create the conversation and record the user message, before any I/O.

        Streaming needs this split: a route cannot answer "404 conversation not
        found" once it has started sending 200 with an event stream, so the
        checks that can fail must run while a status code is still available.
        """
        conversation, turn = await self._prepare(
            text,
            conversation_id=conversation_id,
            source=source,
            channel=channel,
            task_id=task_id,
            title=title,
            owner=owner,
            modality=modality,
        )
        return PreparedTurn(conversation_id=conversation.id, turn=turn)

    async def stream(self, prepared: PreparedTurn) -> AsyncIterator[ChatEvent]:
        """The same turn as ``chat``, emitted while the model writes it.

        The stored conversation is identical: the assembled text is appended
        only when the stream finishes, so a turn that dies mid-stream leaves the
        user message and any tool outputs recorded but never a half reply.
        """
        conversation_id, turn = prepared.conversation_id, prepared.turn
        yield ChatEvent(kind="start", conversation_id=conversation_id)

        async def on_tool(name: str, output: str) -> None:
            await self.store.append(conversation_id, "tool", output, name=name)

        done: ChatEvent | None = None
        try:
            async for event in self.engine.run_stream(turn, on_tool=on_tool):
                if event.kind == "done":
                    done = event
                    continue
                yield event
        except ChatModelError as exc:
            raise AssistantModelError(str(exc)) from exc

        if done is None:  # pragma: no cover - run_stream always ends with done
            raise AssistantModelError("the model stream ended without a reply")
        await self.store.append(conversation_id, "assistant", done.text)
        yield ChatEvent(
            kind="done",
            text=done.text,
            conversation_id=conversation_id,
            tools_used=done.tools_used,
            usage_tokens=done.usage_tokens,
        )
