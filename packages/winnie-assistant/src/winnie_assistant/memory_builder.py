"""Distil durable facts from conversation files into memory files.

The builder reads the Markdown transcripts, not the in-process message list,
and keeps a per-conversation watermark so a restart or a repeated run does not
duplicate facts.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from winnie_llm import parse_json_value

from winnie_assistant.agent import ChatModel
from winnie_assistant.config import AssistantConfig
from winnie_assistant.conversations import ConversationStore
from winnie_assistant.files import atomic_write
from winnie_assistant.memory import CATEGORIES, MemoryStore

logger = logging.getLogger(__name__)

MAX_TRANSCRIPT_CHARS = 8000
DIGEST_CONTEXT_CHARS = 1200

SYSTEM_PROMPT = """\
You maintain the long-term memory of a personal assistant.
Read the conversation excerpt and extract only durable facts about its owner
that will still be useful in a future conversation.

Categories: identity, preferences, people, projects, domain, procedures,
constraints, resources, episodes.

Rules:
- One fact per entry: one self-contained sentence, under 300 characters.
- Prefer stable facts (names, preferences, relationships, ongoing projects,
  decisions with a date) over transient details.
- Never store greetings, small talk, one-off commands, or the assistant's own
  guesses.
- Never store a failed lookup or a missing record ("could not find X's
  birthday", "no contact named Y"). A miss is a property of one query, not a
  fact about the owner, and recall treats stored text as true. Store only what
  the owner stated or a tool returned.
- Write each fact in the language the user used in the conversation.
- Do not restate facts already present in the existing memory digest.
- At most {max_facts} facts. If nothing durable was said, return no facts.

Return JSON only: {{"facts": [{{"category": "...", "text": "..."}}],
"nothing_new": true|false}}
"""


@dataclass(frozen=True)
class DistillResult:
    conversations: int
    facts: int
    empty: int
    # Memory files this run appended to, so the caller can index them now
    # instead of waiting for the daemon's five-minute sweep.
    paths: tuple[str, ...] = ()


class MemoryBuilder:
    def __init__(
        self,
        *,
        store: ConversationStore,
        memory: MemoryStore,
        model: ChatModel,
        config: AssistantConfig,
        state_path,
        timezone: ZoneInfo,
        clock=None,
    ) -> None:
        self.store = store
        self.memory = memory
        self.model = model
        self.config = config
        self.state_path = state_path
        self.timezone = timezone
        self.clock = clock or (lambda: datetime.now(timezone))
        self._lock = asyncio.Lock()

    async def run(self) -> DistillResult:
        async with self._lock:
            state = self._load_state()
            processed = 0
            added = 0
            empty = 0
            touched: list[str] = []
            for summary in self.store.list(limit=None):
                if added >= self.config.memory.max_facts_per_run:
                    break
                conversation = self.store.get(summary.id)
                done = int(state.get(conversation.id, 0))
                messages = conversation.messages
                if done >= len(messages):
                    continue
                if messages and messages[-1].role != "assistant":
                    # The turn is still being written (or failed before the
                    # answer); distilling half of it would store a fragment.
                    continue
                fresh = messages[done:]
                transcript = _render(fresh)
                if not transcript.strip():
                    state[conversation.id] = len(messages)
                    self._save_state(state)
                    continue
                remaining = self.config.memory.max_facts_per_run - added
                facts = await self._extract(transcript, conversation.id, remaining)
                for category, text in facts:
                    if self.memory.append(
                        category,
                        text,
                        source=f"conversation:{conversation.id}",
                        now=self.clock(),
                    ):
                        added += 1
                        if category not in touched:
                            touched.append(category)
                state[conversation.id] = len(messages)
                self._save_state(state)
                processed += 1
                if not facts:
                    empty += 1
            if processed == 0:
                logger.info("memory distillation found no new messages")
            return DistillResult(
                conversations=processed,
                facts=added,
                empty=empty,
                paths=tuple(str(self.memory.root / f"{name}.md") for name in touched),
            )

    async def _extract(
        self, transcript: str, conversation_id: str, remaining: int
    ) -> list[tuple[str, str]]:
        system = SYSTEM_PROMPT.format(max_facts=remaining)
        user = (
            "Existing memory (excerpt):\n"
            f"{self.memory.digest(limit=DIGEST_CONTEXT_CHARS) or '(empty)'}\n\n"
            "Conversation:\n"
            f"{transcript[:MAX_TRANSCRIPT_CHARS]}"
        )
        try:
            result = await self.model.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]
            )
        except Exception:  # noqa: BLE001 - caller keeps the watermark and retries
            logger.exception("memory extraction failed for %s", conversation_id)
            raise
        try:
            return _parse_facts(result.text, self.config, remaining)
        except ValueError as exc:
            logger.warning("memory extraction returned unusable JSON: %s", exc)
            return []

    # -- state -----------------------------------------------------------

    def _load_state(self) -> dict[str, int]:
        if not self.state_path.is_file():
            return {}
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("memory state is unreadable; starting from zero")
            return {}
        conversations = raw.get("conversations") if isinstance(raw, dict) else None
        if not isinstance(conversations, dict):
            return {}
        return {
            str(key): int(value)
            for key, value in conversations.items()
            if isinstance(value, int) or str(value).isdigit()
        }

    def _save_state(self, state: dict[str, int]) -> None:
        payload = {
            "conversations": state,
            "updated_at": self.clock().isoformat(timespec="seconds"),
        }
        atomic_write(
            self.state_path,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )


def _render(messages) -> str:
    parts: list[str] = []
    for message in messages:
        if message.role == "tool":
            continue
        label = "USER" if message.role == "user" else "ASSISTANT"
        parts.append(f"{label}: {message.text.strip()}")
    return "\n\n".join(parts)


def _parse_facts(
    text: str, config: AssistantConfig, limit: int
) -> list[tuple[str, str]]:
    value = parse_json_value(text)
    if isinstance(value, dict):
        raw_facts = value.get("facts") or []
    elif isinstance(value, list):
        raw_facts = value
    else:
        raise ValueError("expected an object with a facts list")
    if not isinstance(raw_facts, list):
        raise ValueError("facts must be a list")

    facts: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in raw_facts:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "").strip()
        fact = str(item.get("text") or "").strip()
        if category not in CATEGORIES or not fact:
            continue
        fact = fact[: config.memory.max_fact_chars].strip()
        key = fact.lower()
        if key in seen:
            continue
        seen.add(key)
        facts.append((category, fact))
        if len(facts) >= limit:
            break
    return facts


def unresolved_conversations(
    store: ConversationStore, state_path, limit: int | None = None
) -> int:
    """Number of conversations with messages the builder has not seen yet."""
    if not state_path.is_file():
        state: dict[str, int] = {}
    else:
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
            state = raw.get("conversations", {}) if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError):
            state = {}
    pending = 0
    for summary in store.list(limit=limit):
        done = int(state.get(summary.id, 0))
        if done < summary.message_count:
            pending += 1
    return pending
