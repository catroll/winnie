"""Assistant adapter: persistence around the dedicated chat engine.

The model/tool loop, skill selection and prompt assembly live in
``winnie-chat``. This module only creates the conversation, records the user
message and tool traces, and stores the final assistant reply.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from winnie_chat import ChatEngine, ChatModel, ChatModelError, ChatTurn

from winnie_assistant.conversations import ConversationStore
from winnie_assistant.memory import MemoryStore
from winnie_assistant.tools import ToolBox
from winnie_assistant.types import ChatReply

logger = logging.getLogger(__name__)


class AssistantModelError(RuntimeError):
    """The configured model could not produce a turn."""


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
    ) -> ChatReply:
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
        )

        async def on_tool(name: str, output: str) -> None:
            await self.store.append(
                conversation.id, "tool", output, name=name
            )

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
